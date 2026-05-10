import asyncio
import glob
import math
import os
import subprocess
import time
import logging
import traceback
import discord
from discord.ui import View, Button

import yt_dlp
from yt_dlp import version as yt_dlp_version

logger = logging.getLogger(__name__)

from utils import sanitize_filename, is_safe_url
from media.common import (
    MAX_UPLOAD_BYTES,
    make_workdir,
    cleanup,
    safe_followup,
    send_file,
)

MAX_DOWNLOAD_BYTES = MAX_UPLOAD_BYTES - (
    1 * 1024 * 1024
)  # 1MB buffer for metadata and encoding overhead
MIN_VIDEO_KBPS = 500  # minimum for watchable compressed video


def can_compress(info, upload_limit=None):
    duration = info.get("duration") or 0
    if not duration:
        return False
    limit = (upload_limit or MAX_UPLOAD_BYTES) - (1 * 1024 * 1024)
    target_kbps = int((limit * 8) // (duration * 1000) * 0.80)
    return (target_kbps - 96) >= MIN_VIDEO_KBPS


class OversizeView(View):
    """Buttons shown when a video exceeds the upload limit."""

    def __init__(
        self, handler, interaction, url, info, compress_viable=True, timeout=60, upload_limit=None
    ):
        super().__init__(timeout=timeout)
        self.handler = handler
        self.original_interaction = interaction
        self.url = url
        self.info = info
        self.message: discord.Message | None = None
        self.upload_limit = upload_limit

        if not compress_viable:
            self.compress.disabled = True
            self.compress.label = "Compress (too long)"

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(content="Timed out.", view=self)
            except Exception:
                pass

    async def _disable_all(self, interaction, chosen_label):
        for child in self.children:
            if isinstance(child, Button):
                child.disabled = True
        await interaction.response.edit_message(
            content=f"Selected: **{chosen_label}**", view=self
        )
        self.stop()

    @discord.ui.button(label="Compress", style=discord.ButtonStyle.primary)
    async def compress(self, interaction: discord.Interaction, button: Button):
        await self._disable_all(interaction, "Compress")
        await self.handler._compress_and_send(
            self.original_interaction, self.url, self.info, upload_limit=self.upload_limit
        )

    @discord.ui.button(label="Split into parts", style=discord.ButtonStyle.secondary)
    async def split(self, interaction: discord.Interaction, button: Button):
        await self._disable_all(interaction, "Split into parts")
        await self.handler._split_and_send(
            self.original_interaction, self.url, self.info, upload_limit=self.upload_limit
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await self._disable_all(interaction, "Cancelled")


class MediaHandler:
    def __init__(self):
        self._info_cache: dict[str, tuple[float, dict]] = {}
        self._youtube_client_cache: dict[str, str] = {}
        self._cache_ttl_seconds = int(os.getenv("MEDIA_INFO_CACHE_TTL_SECONDS", "600"))

    @staticmethod
    def _domain_kind(url: str) -> str:
        lowered = (url or "").lower()
        if any(domain in lowered for domain in ("youtube.com", "youtu.be")):
            return "youtube"
        if any(domain in lowered for domain in ("twitter.com", "x.com", "t.co")):
            return "twitter"
        return "generic"

    def _build_base_ydl_opts(self, youtube_client: str = "mweb", force_debug: bool = False) -> dict:
        debug_mode = force_debug or os.environ.get("LOG_LEVEL", "INFO").upper() == "DEBUG"
        youtube_args = {
            "player_client": [youtube_client],
        }
        if debug_mode:
            youtube_args["pot_trace"] = ["true"]

        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "verbose": debug_mode,
            # Ensure yt-dlp can execute JavaScript challenge providers with Node.js.
            # In Azure logs we observed JSC "node (unavailable)" unless this is explicit.
            "js_runtimes": {
                "node": {},
            },
            # bgutil PO token provider for YouTube bot-check bypass (script mode)
            "extractor_args": {
                "youtube": youtube_args,
                "youtubepot-bgutilscript": {
                    "server_home": ["/opt/bgutil-ytdlp-pot-provider/server"]
                }
            },
        }
        cookie_file = os.getenv("YTDLP_COOKIES_FILE", "").strip()
        if cookie_file:
            opts["cookiefile"] = cookie_file
        logger.debug(
            "yt-dlp base opts prepared (debug_mode=%s, yt_dlp_version=%s, cookiefile=%s, youtube_client=%s, extractor_args=%s)",
            debug_mode,
            yt_dlp_version.__version__,
            cookie_file or "<unset>",
            youtube_client,
            {
                "youtube": youtube_args,
                "youtubepot-bgutilscript": {
                    "server_home": ["/opt/bgutil-ytdlp-pot-provider/server"],
                },
            },
        )
        return opts

    @staticmethod
    def _is_youtube_bot_check_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "sign in to confirm you" in msg and "not a bot" in msg

    def _get_cached_info(self, url: str):
        cached = self._info_cache.get(url)
        if not cached:
            return None
        ts, info = cached
        if (time.time() - ts) > self._cache_ttl_seconds:
            self._info_cache.pop(url, None)
            return None
        return info

    def _set_cached_info(self, url: str, info: dict):
        self._info_cache[url] = (time.time(), info)

    @staticmethod
    def _pick_hls_manifest(info: dict | None) -> str | None:
        if not isinstance(info, dict):
            return None

        direct_protocol = str(info.get("protocol", ""))
        direct_url = info.get("url")
        if direct_url and "m3u8" in direct_protocol:
            return direct_url

        requested = info.get("requested_formats") or []
        for fmt in requested:
            protocol = str(fmt.get("protocol", ""))
            url = fmt.get("url")
            if url and "m3u8" in protocol:
                return url

        formats = info.get("formats") or []
        hls_candidates = [f for f in formats if f.get("url") and "m3u8" in str(f.get("protocol", ""))]
        if not hls_candidates:
            return None

        # Prefer higher quality candidate when multiple HLS renditions are available.
        best = max(
            hls_candidates,
            key=lambda f: (
                f.get("height") or 0,
                f.get("tbr") or 0,
                f.get("fps") or 0,
            ),
        )
        return best.get("url")

    def _format_selector(self, url: str, limit_mb: int) -> str:
        kind = self._domain_kind(url)
        if kind == "youtube":
            # Try a single-file mp4 first for speed, then fallback to merged formats.
            return (
                f"best[ext=mp4][filesize<{limit_mb}M]"
                f"/best[filesize<{limit_mb}M]"
                f"/bestvideo[ext=mp4][filesize<{limit_mb}M]+bestaudio[ext=m4a]"
                f"/bestvideo+bestaudio/best"
            )
        if kind == "twitter":
            # Prefer direct MP4 variants and avoid m3u8 where possible.
            return (
                f"best[ext=mp4][protocol!=m3u8_native][protocol!=m3u8][filesize<{limit_mb}M]"
                f"/best[ext=mp4][filesize<{limit_mb}M]"
                f"/best[ext=mp4][protocol!=m3u8_native][protocol!=m3u8]"
                f"/best[ext=mp4]/best"
            )
        return f"best[ext=mp4][filesize<{limit_mb}M]/best[filesize<{limit_mb}M]/best"

    async def download_and_send(self, interaction, url, upload_limit=None):
        if not is_safe_url(url):
            await safe_followup(interaction, "URL is not safe to access.")
            return
        
        workdir = make_workdir()
        filepath = None

        try:
            info = await self._extract_info(url)

            if info is None:
                await safe_followup(
                    interaction, "Could not fetch video info. Check the URL."
                )
                return

            title = info.get("title", "video")
            estimated_size = self._estimate_filesize(info)
            effective_limit = (upload_limit or MAX_UPLOAD_BYTES) - (1 * 1024 * 1024)

            # Metadata-based short-circuit: decide oversize before downloading body.
            if estimated_size and estimated_size > effective_limit:
                est_mb = estimated_size // (1024 * 1024)
                limit_mb = (
                    upload_limit // (1024 * 1024)
                    if upload_limit
                    else MAX_UPLOAD_BYTES // (1024 * 1024)
                )
                compress_viable = can_compress(info, upload_limit=upload_limit)
                view = OversizeView(
                    self, interaction, url, info, compress_viable=compress_viable, upload_limit=upload_limit
                )
                msg = await interaction.followup.send(
                    f"Estimated file size is ~{est_mb}MB - exceeds the {limit_mb}MB limit.\nWhat do you want to do?",
                    view=view,
                )
                view.message = msg
                return

            filename = sanitize_filename(f"{title}.mp4")
            filepath = os.path.join(workdir, filename)

            # Fast path: if metadata points to an HLS stream, remux it directly to mp4
            # using stream copy. This avoids a full re-encode and is typically much faster.
            hls_manifest = self._pick_hls_manifest(info)
            if hls_manifest:
                try:
                    await self._download_hls_remux(hls_manifest, filepath)
                except Exception as remux_err:
                    logger.warning(f"HLS remux fast-path failed, falling back to yt-dlp: {remux_err}")
                    await self._download(url, filepath, upload_limit=upload_limit)
            else:
                await self._download(url, filepath, upload_limit=upload_limit)

            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                await safe_followup(interaction, "Download failed - file is empty.")
                return

            file_size = os.path.getsize(filepath)
            if file_size > (upload_limit or MAX_UPLOAD_BYTES):
                compress_viable = can_compress(info, upload_limit=upload_limit)
                view = OversizeView(
                    self, interaction, url, info, compress_viable=compress_viable, upload_limit=upload_limit
                )
                msg = await interaction.followup.send(
                    f"Downloaded file is {file_size // (1024 * 1024)}MB - too large.\nWhat do you want to do?",
                    view=view,
                )
                view.message = msg
                return

            await send_file(interaction, title, filepath)

        except Exception as e:
            logger.exception(f"YouTube download error: {e}\n{traceback.format_exc()}")
            await safe_followup(interaction, f"Error downloading video: {e}")
        finally:
            cleanup(workdir, filepath)

    async def _compress_and_send(self, interaction, url, info, upload_limit=None):
        workdir = make_workdir()
        title = info.get("title", "video")
        raw_path = os.path.join(workdir, sanitize_filename(f"{title}_raw.mp4"))
        out_path = os.path.join(workdir, sanitize_filename(f"{title}.mp4"))

        try:
            await safe_followup(
                interaction, "Compressing video to fit under the limit..."
            )
            # Issue 18: Try HLS remux first to avoid re-encoding; fallback to download
            hls_manifest = self._pick_hls_manifest(info)
            if hls_manifest:
                try:
                    await self._download_hls_remux(hls_manifest, raw_path)
                except Exception as remux_err:
                    logger.warning(
                        "HLS remux in compress failed, falling back to yt-dlp: %s",
                        remux_err,
                    )
                    await self._download(
                        url,
                        raw_path,
                        format_str="best[ext=mp4]/best",
                        upload_limit=upload_limit,
                    )
            else:
                await self._download(
                    url,
                    raw_path,
                    format_str="best[ext=mp4]/best",
                    upload_limit=upload_limit,
                )

            if not os.path.exists(raw_path) or os.path.getsize(raw_path) == 0:
                await safe_followup(interaction, "Download failed.")
                return

            duration = info.get("duration") or await self._probe_duration(raw_path)
            if not duration:
                await safe_followup(interaction, "Could not determine video duration.")
                return

            # Issue 20: Get source height from info metadata when available, avoiding redundant ffprobe
            source_height = info.get("height")
            if not source_height:
                loop = asyncio.get_event_loop()
                probe_cmd = [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=height",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    raw_path,
                ]
                probe_result = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        probe_cmd, capture_output=True, text=True, timeout=30
                    ),
                )
                try:
                    source_height = int(probe_result.stdout.strip())
                except (ValueError, AttributeError):
                    source_height = 720
            else:
                source_height = int(source_height)

            target_height = min(source_height, 480)

            limit = (upload_limit or MAX_UPLOAD_BYTES) - (1 * 1024 * 1024)
            target_total_kbps = int(
                (limit * 8) // (duration * 1000) * 0.80
            )
            audio_kbps = 96
            video_kbps = max(target_total_kbps - audio_kbps, MIN_VIDEO_KBPS)

            # Issue 19: Add ffmpeg flags for faster transcode and better Discord compatibility
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", raw_path, "-threads", "0"]

            if source_height > target_height:
                ffmpeg_cmd += ["-vf", f"scale=-2:{target_height}"]

            ffmpeg_cmd += [
                "-c:v",
                "libx264",
                "-b:v",
                f"{video_kbps}k",
                "-maxrate",
                f"{video_kbps}k",
                "-bufsize",
                f"{video_kbps}k",
                "-preset",
                "veryfast",
                "-tune",
                "fastdecode",
                "-movflags",
                "+faststart",
                "-c:a",
                "aac",
                "-b:a",
                f"{audio_kbps}k",
                out_path,
            ]

            await loop.run_in_executor(
                None, lambda: subprocess.run(ffmpeg_cmd, check=True, timeout=600)
            )

            file_size = os.path.getsize(out_path)
            if file_size > (upload_limit or MAX_UPLOAD_BYTES):
                await safe_followup(
                    interaction,
                    f"Compressed to {file_size // (1024 * 1024)}MB - still too large. Splitting instead...",
                )
                await self._split_and_send(
                    interaction, url, info, upload_limit=upload_limit
                )
                return

            await send_file(interaction, title, out_path)

        except Exception as e:
            logger.exception(f"Compress error: {e}\n{traceback.format_exc()}")
            await safe_followup(interaction, f"Error compressing video: {e}")
        finally:
            cleanup(workdir, raw_path)
            cleanup(workdir, out_path)

    async def _split_and_send(self, interaction, url, info, upload_limit=None):
        """Split video into chunks that each fit under the upload limit."""
        workdir = make_workdir()
        title = info.get("title", "video")
        raw_path = os.path.join(workdir, sanitize_filename(f"{title}_raw.mp4"))

        try:
            await safe_followup(
                interaction, "Downloading and splitting video into parts..."
            )

            await self._download(
                url,
                raw_path,
                format_str="best[ext=mp4]/best",
                upload_limit=upload_limit,
            )

            if not os.path.exists(raw_path) or os.path.getsize(raw_path) == 0:
                await safe_followup(interaction, "Download failed.")
                return

            file_size = os.path.getsize(raw_path)
            duration = info.get("duration") or await self._probe_duration(raw_path)
            if not duration:
                await safe_followup(interaction, "Could not determine video duration.")
                return

            limit = (upload_limit or MAX_UPLOAD_BYTES) - (1 * 1024 * 1024)
            num_parts = math.ceil(file_size / limit)
            segment_duration = max(1, math.floor(duration / num_parts))
            segment_pattern = os.path.join(workdir, "segment_%03d.mp4")

            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                raw_path,
                "-c",
                "copy",
                "-f",
                "segment",
                "-segment_time",
                str(segment_duration),
                "-reset_timestamps",
                "1",
                "-map",
                "0",
                segment_pattern,
            ]

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(ffmpeg_cmd, check=True, timeout=600),
            )

            part_files = sorted(glob.glob(os.path.join(workdir, "segment_*.mp4")))
            parts = []
            for idx, part_path in enumerate(part_files):
                if os.path.exists(part_path) and os.path.getsize(part_path) > 0:
                    start = idx * segment_duration
                    end = min(start + segment_duration, int(duration))
                    parts.append((part_path, start, end))

            for idx, (part_path, start, end) in enumerate(parts):
                start_ts = self._format_timestamp(start)
                end_ts = self._format_timestamp(end)
                part_title = (
                    f"{title} (Part {idx + 1}/{len(parts)} - {start_ts}-{end_ts})"
                )
                await send_file(interaction, part_title, part_path)

        except Exception as e:
            logger.exception(f"Split error: {e}\n{traceback.format_exc()}")
            await safe_followup(interaction, f"Error splitting video: {e}")
        finally:
            for f in os.listdir(workdir):
                cleanup(workdir, os.path.join(workdir, f))
            cleanup(workdir)

    @staticmethod
    def _format_timestamp(seconds):
        """Format seconds into m:ss or h:mm:ss."""
        seconds = int(seconds)
        if seconds >= 3600:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            s = seconds % 60
            return f"{h}:{m:02d}:{s:02d}"
        m = seconds // 60
        s = seconds % 60
        return f"{m}:{s:02d}"

    async def _probe_duration(self, filepath):
        """Get duration from a downloaded file via ffprobe."""
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            filepath,
        ]
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30),
        )
        try:
            return float(result.stdout.strip())
        except (ValueError, AttributeError):
            return None

    def _estimate_filesize(self, info):
        """Estimate file size from metadata. Returns bytes or None if unknown."""
        if info.get("filesize_approx"):
            return info["filesize_approx"]
        if info.get("filesize"):
            return info["filesize"]

        duration = info.get("duration")
        tbr = info.get("tbr")
        if duration and tbr:
            return int((tbr * 1000 / 8) * duration)

        return None

    async def _extract_info(self, url):
        cached = self._get_cached_info(url)
        if cached is not None:
            return cached

        # Try mweb first (PO-token preferred path), then web_safari as fallback.
        candidate_clients = ["mweb", "web_safari"] if self._domain_kind(url) == "youtube" else ["mweb"]

        loop = asyncio.get_event_loop()
        last_exc = None
        info = None
        chosen_client = "mweb"
        for idx, client in enumerate(candidate_clients):
            force_debug = idx > 0
            opts = self._build_base_ydl_opts(youtube_client=client, force_debug=force_debug)
            opts["skip_download"] = True
            logger.info(
                "Starting yt-dlp info extraction for %s (client=%s, debug=%s, yt-dlp=%s, yt_dlp_plugin=%s, cookiefile=%s)",
                url,
                client,
                force_debug or os.environ.get("LOG_LEVEL", "INFO").upper() == "DEBUG",
                yt_dlp_version.__version__,
                "bgutil-ytdlp-pot-provider",
                opts.get("cookiefile", "<unset>"),
            )
            try:
                info = await loop.run_in_executor(None, lambda: self._yt_extract(url, opts))
                chosen_client = client
                break
            except Exception as exc:
                last_exc = exc
                if client != "mweb" or not self._is_youtube_bot_check_error(exc):
                    raise
                logger.warning(
                    "YouTube bot-check encountered with client=%s; retrying with client=web_safari",
                    client,
                )

        if info is None and last_exc is not None:
            raise last_exc

        if info is not None:
            self._set_cached_info(url, info)
            self._youtube_client_cache[url] = chosen_client
        return info

    MERGE_DOMAINS = ["youtube.com", "youtu.be"]

    async def _download(self, url, output_path, format_str=None, upload_limit=None, youtube_client=None):
        # Use consistent 1MB headroom (issue 17: was mixing 1MB vs 5MB)
        limit_bytes = (upload_limit or MAX_UPLOAD_BYTES) - (1 * 1024 * 1024)
        limit_mb = limit_bytes // (1024 * 1024)
        if format_str:
            fmt = format_str
        else:
            fmt = self._format_selector(url, limit_mb)
        default_client = youtube_client or self._youtube_client_cache.get(url, "mweb")
        candidate_clients = [default_client]
        if self._domain_kind(url) == "youtube" and default_client != "web_safari":
            candidate_clients.append("web_safari")

        loop = asyncio.get_event_loop()
        last_exc = None
        for idx, client in enumerate(candidate_clients):
            force_debug = idx > 0
            opts = self._build_base_ydl_opts(youtube_client=client, force_debug=force_debug)
            opts.update(
                {
                    "format": fmt,
                    "merge_output_format": "mp4",
                    "outtmpl": output_path,
                }
            )
            logger.info(
                "Starting yt-dlp download for %s (client=%s, format=%s, debug=%s, yt-dlp=%s, yt_dlp_plugin=%s)",
                url,
                client,
                fmt,
                force_debug or os.environ.get("LOG_LEVEL", "INFO").upper() == "DEBUG",
                yt_dlp_version.__version__,
                "bgutil-ytdlp-pot-provider",
            )
            try:
                await loop.run_in_executor(None, lambda: self._yt_download(url, opts))
                self._youtube_client_cache[url] = client
                return
            except Exception as exc:
                last_exc = exc
                if client != default_client or not self._is_youtube_bot_check_error(exc):
                    raise
                logger.warning(
                    "YouTube bot-check encountered during download with client=%s; retrying with client=web_safari",
                    client,
                )

        if last_exc is not None:
            raise last_exc

    async def _download_hls_remux(self, manifest_url: str, output_path: str):
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            manifest_url,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-bsf:a",
            "aac_adtstoasc",
            output_path,
        ]
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, check=True, timeout=300),
        )

    @staticmethod
    def _yt_extract(url, opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    @staticmethod
    def _yt_download(url, opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
