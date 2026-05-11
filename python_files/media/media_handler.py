import asyncio
import math
import os
import subprocess
import discord
from discord.ui import View, Button

import yt_dlp

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
        pass

    async def download_and_send(self, interaction, url, upload_limit=None):
        if not is_safe_url(url):
            await safe_followup(interaction, "URL is not safe to access.")
            return
        
        workdir = make_workdir()
        filepath = None

        try:
            print(f"[VERBOSE] download_and_send called: url={url} user={getattr(interaction,'user',None)} guild={getattr(interaction,'guild',None)} channel={getattr(interaction,'channel',None)}")
            info = await self._extract_info(url)

            # Log key metadata fields so operator can see what will be downloaded
            if info:
                title = info.get('title')
                uploader = info.get('uploader') or info.get('uploader_id')
                duration = info.get('duration')
                filesize = info.get('filesize') or info.get('filesize_approx')
                print(f"[VERBOSE] extracted info: title={title} uploader={uploader} duration={duration} filesize={filesize}")

            if info is None:
                await safe_followup(
                    interaction, "Could not fetch video info. Check the URL."
                )
                return

            title = info.get("title", "video")
            estimated_size = self._estimate_filesize(info)

            if estimated_size and estimated_size > MAX_DOWNLOAD_BYTES:
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

            await self._download(url, filepath, upload_limit=upload_limit)

            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                await safe_followup(interaction, "Download failed - file is empty.")
                return

            file_size = os.path.getsize(filepath)
            if file_size > (upload_limit or MAX_UPLOAD_BYTES):
                compress_viable = can_compress(info)
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
            print(f"YouTube download error: {e}")
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

            # Get source height to avoid upscaling
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

            target_height = min(source_height, 480)

            limit = (upload_limit or MAX_UPLOAD_BYTES) - (1 * 1024 * 1024)
            target_total_kbps = int(
                (limit * 8) // (duration * 1000) * 0.80
            )
            audio_kbps = 96
            video_kbps = max(target_total_kbps - audio_kbps, MIN_VIDEO_KBPS)

            ffmpeg_cmd = ["ffmpeg", "-y", "-i", raw_path]

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
            print(f"Compress error: {e}")
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
            segment_duration = math.floor(duration / num_parts)

            parts = []
            for i in range(num_parts):
                start = i * segment_duration
                end = min(start + segment_duration, int(duration))
                part_path = os.path.join(
                    workdir, sanitize_filename(f"{title}_part{i + 1}.mp4")
                )
                ffmpeg_cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(start),
                    "-i",
                    raw_path,
                    "-t",
                    str(segment_duration),
                    "-c",
                    "copy",
                    "-avoid_negative_ts",
                    "make_zero",
                    part_path,
                ]

                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda cmd=ffmpeg_cmd: subprocess.run(cmd, check=True, timeout=300),
                )

                if os.path.exists(part_path) and os.path.getsize(part_path) > 0:
                    parts.append((part_path, start, end))

            for idx, (part_path, start, end) in enumerate(parts):
                start_ts = self._format_timestamp(start)
                end_ts = self._format_timestamp(end)
                part_title = (
                    f"{title} (Part {idx + 1}/{len(parts)} - {start_ts}-{end_ts})"
                )
                await send_file(interaction, part_title, part_path)

        except Exception as e:
            print(f"Split error: {e}")
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

    def _build_yt_opts(self, skip_download=True, format_str=None, **extra_opts):
        """Build yt-dlp options with PO token/proxy/cookie support from env vars."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": skip_download,
        }
        
        # Add format if specified
        if format_str:
            opts["format"] = format_str
        
        # YouTube-specific extraction args with PO token support
        po_token = os.getenv("YTDLP_YOUTUBE_PO_TOKEN")
        visitor_data = os.getenv("YTDLP_YOUTUBE_VISITOR_DATA")
        cookies_file = os.getenv("YTDLP_COOKIES_FILE")
        
        if po_token or visitor_data or cookies_file:
            opts["extractor_args"] = {"youtube": {}}
            if po_token:
                opts["extractor_args"]["youtube"]["po_token"] = [po_token]
            if visitor_data:
                opts["extractor_args"]["youtube"]["visitor_data"] = [visitor_data]
        
        # Cookies support
        if cookies_file and os.path.exists(cookies_file):
            opts["cookiefile"] = cookies_file
        
        # Proxy support
        proxy = os.getenv("YTDLP_PROXY")
        if proxy:
            opts["proxy"] = proxy
        
        # Merge any extra options
        opts.update(extra_opts)
        return opts

    async def _extract_info(self, url):
        # Try with default client first (mweb), then fallback to web_safari with debug
        for client in ["mweb", "web_safari"]:
            try:
                opts = self._build_yt_opts(skip_download=True)
                
                # Add YouTube client selection and PO token tracing
                if "extractor_args" not in opts:
                    opts["extractor_args"] = {}
                if "youtube" not in opts["extractor_args"]:
                    opts["extractor_args"]["youtube"] = {}
                
                opts["extractor_args"]["youtube"]["player_client"] = [client]
                
                # Enable debug for web_safari to help diagnose failures
                if client == "web_safari":
                    opts["verbose"] = True
                
                print(f"[VERBOSE] Attempting yt-dlp extraction with client={client}")
                loop = asyncio.get_event_loop()
                info = await loop.run_in_executor(None, lambda: self._yt_extract(url, opts))
                return info
                
            except Exception as e:
                error_msg = str(e)
                if "Sign in to confirm you" in error_msg or "LOGIN_REQUIRED" in error_msg:
                    if client == "mweb":
                        print(f"[VERBOSE] Bot check with {client}; retrying with web_safari")
                        continue
                    else:
                        # Both clients failed
                        print(f"[VERBOSE] Both clients exhausted. Error: {error_msg}")
                        raise
                else:
                    # Non-auth error, don't retry
                    raise

    MERGE_DOMAINS = ["youtube.com", "youtu.be"]

    async def _download(self, url, output_path, format_str=None, upload_limit=None):
        limit_mb = (upload_limit or MAX_UPLOAD_BYTES) // (1024 * 1024) - 5
        if format_str:
            fmt = format_str
        elif any(domain in url for domain in self.MERGE_DOMAINS):
            fmt = f"bestvideo[ext=mp4][filesize<{limit_mb}M]+bestaudio[ext=m4a]/best[ext=mp4][filesize<{limit_mb}M]/best[filesize<{limit_mb}M]"
        else:
            fmt = f"best[filesize<{limit_mb}M]/best"
        
        opts = self._build_yt_opts(
            skip_download=False, 
            format_str=fmt,
            merge_output_format="mp4",
            outtmpl=output_path
        )
        print(f"[VERBOSE] yt-dlp download options: format={fmt} outtmpl={output_path} upload_limit_mb={limit_mb}")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: self._yt_download(url, opts))

    @staticmethod
    def _yt_extract(url, opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    @staticmethod
    def _yt_download(url, opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
