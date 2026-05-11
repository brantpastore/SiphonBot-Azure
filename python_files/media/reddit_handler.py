import aiohttp
import asyncio
import os
import subprocess
import re
import logging
import traceback
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

from utils import sanitize_filename, is_safe_url
from media.common import (
    MAX_UPLOAD_BYTES,
    make_workdir,
    cleanup,
    safe_followup,
    send_content,
    send_file,
)


def should_skip(post_data):
    """Check if post should be skipped based on metadata (issue 32)."""
    if post_data.get("stickied"):
        return True
    if post_data.get("pinned"):
        return True
    if post_data.get("removed_by_category"):
        return True
    # Skip posts where media/author was removed
    if post_data.get("author") == "[deleted]":
        return True
    if post_data.get("is_self") and not post_data.get("selftext"):
        return True
    return False


class RedditMediaHandler:
    EXTERNAL_VIDEO_DOMAINS = ["youtube.com", "youtu.be", "tiktok.com", "instagram.com"]

    def __init__(self, reddit_auth, media_handler=None):
        self.reddit_auth = reddit_auth
        self.media_handler = media_handler

    def _limit(self, upload_limit):
        return upload_limit or MAX_UPLOAD_BYTES

    @staticmethod
    async def _domain_match(url: str, domains: list[str]) -> bool:
        """Check if URL hostname matches any domain (issue 31: proper parsing vs substring match)."""
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            hostname_lower = hostname.lower()
            for domain in domains:
                domain_lower = domain.lower()
                if hostname_lower == domain_lower or hostname_lower.endswith("." + domain_lower):
                    return True
            return False
        except Exception:
            return False

    @staticmethod
    async def _download_to_file(session, url, output_path, timeout_seconds):
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as response:
            response.raise_for_status()
            with open(output_path, "wb") as f:
                async for chunk in response.content.iter_chunked(8192):
                    f.write(chunk)

    @staticmethod
    async def _download_hls_to_file(session, url, output_path, timeout_seconds):
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout_seconds, sock_read=None)
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            is_hls = (
                "application/vnd.apple.mpegurl" in content_type
                or "application/x-mpegurl" in content_type
                or url.endswith(".m3u8")
            )

            if not is_hls:
                extension = os.path.splitext(urlparse(url).path)[1] or ".mp4"
                output_file = os.path.splitext(output_path)[0] + extension
                with open(output_file, "wb") as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)
                return output_file, False

        return output_path, True

    async def scrape_subreddit(
        self, interaction, subreddit_url, num_posts, filter_type, time_range, upload_limit=None
    ):
        logger.info("Scraping %s posts from: %s", num_posts, subreddit_url)

        fetch_limit = num_posts + 5

        if filter_type in ["top", "controversial"]:
            url = f"https://oauth.reddit.com/r/{subreddit_url}/{filter_type}?limit={fetch_limit}&t={time_range}"
        elif filter_type in ["hot", "new", "rising"]:
            url = f"https://oauth.reddit.com/r/{subreddit_url}/{filter_type}?limit={fetch_limit}"
        else:
            url = f"https://oauth.reddit.com/r/{subreddit_url}/hot?limit={fetch_limit}"

        try:
            async with aiohttp.ClientSession(headers=self.reddit_auth.get_headers()) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    response.raise_for_status()
                    data = await response.json()

                posts = data.get("data", {}).get("children", [])
                filtered = [
                    p for p in posts if not should_skip(p.get("data", {}))
                ][:num_posts]

                logger.info("Fetched %s posts, %s after filtering.", len(posts), len(filtered))

                # Issue 23: Parallelize post processing with semaphore to avoid overwhelming single vCPU
                sem = asyncio.Semaphore(2)

                async def process_post_with_sem(post):
                    async with sem:
                        post_data = post.get("data", {})
                        if post_data:
                            logger.debug("Moving to get_post_content for: %s", post_data.get("url"))
                            await self.get_post_content(
                                post_data,
                                interaction,
                                upload_limit=upload_limit,
                                session=session,
                            )

                await asyncio.gather(
                    *[process_post_with_sem(post) for post in filtered],
                    return_exceptions=True
                )

        except aiohttp.ClientResponseError as http_err:
            logger.exception(f"HTTP error occurred: {http_err}\n{traceback.format_exc()}")
            await safe_followup(interaction, f"HTTP error occurred: {http_err}")
        except aiohttp.ClientError as e:
            logger.exception(f"An error occurred: {e}\n{traceback.format_exc()}")
            await safe_followup(interaction, f"An error occurred: {e}")
        except Exception as e:
            logger.exception(f"Error encountered in scrape_subreddit: {e}\n{traceback.format_exc()}")
            await safe_followup(interaction, f"An unexpected error occurred: {e}")

    async def get_post_content(self, post, interaction=None, upload_limit=None, session=None):
        try:
            url = post.get("url", "")
            title = post.get("title", "untitled")
            nsfw = post.get("over_18", False)
            gallery = post.get("is_gallery", False)
            perm_url = post.get("permalink", "")
            reddit_post_url = urljoin("https://www.reddit.com", perm_url)

            logger.info("Getting post content for %s", url)

            if gallery:
                await self.process_gallery(post, title, interaction, nsfw)
                return

            media = post.get("media")
            video = (
                media["reddit_video"]["fallback_url"]
                if media and "reddit_video" in media
                else None
            )
            hls_video = (
                media["reddit_video"]["hls_url"]
                if media and "reddit_video" in media
                else None
            )

            lowered = urlparse(url).path.lower()
            image = (
                url if lowered.endswith((".jpg", ".jpeg", ".png", ".webp")) else None
            )
            gif = url if lowered.endswith(".gif") else None

            if hls_video:
                await self.process_video(
                    hls_video,
                    title,
                    backup_video=video,
                    interaction=interaction,
                    nsfw=nsfw,
                    upload_limit=upload_limit,
                    session=session,
                )
            elif video:
                await self.process_video(
                    video,
                    title,
                    backup_video=None,
                    interaction=interaction,
                    nsfw=nsfw,
                    upload_limit=upload_limit,
                    session=session,
                )
            elif image:
                await self.process_image(
                    image,
                    title,
                    reddit_post_url=reddit_post_url,
                    interaction=interaction,
                    nsfw=nsfw,
                    upload_limit=upload_limit,
                    session=session,
                )
            elif gif:
                await self.process_gif(
                    gif,
                    title,
                    reddit_post_url=reddit_post_url,
                    interaction=interaction,
                    nsfw=nsfw,
                    upload_limit=upload_limit,
                    session=session,
                )
            elif self.media_handler and await self._domain_match(url, self.EXTERNAL_VIDEO_DOMAINS):
                if not is_safe_url(url):
                    await safe_followup(interaction, "URL is not safe to access.")
                    return
                logger.info(f"External video detected, routing to MediaHandler: {url}")
                await self.media_handler.download_and_send(interaction, url, upload_limit=upload_limit)
            else:
                logger.info("No image, video, gif, or gallery found.")
                await safe_followup(
                    interaction,
                    f"No image, video, gif, or gallery found for post: {title} ({url})",
                )

        except Exception as e:
            logger.exception(f"Error getting post content: {e}\n{traceback.format_exc()}")
            await safe_followup(
                interaction,
                f"An unexpected error occurred while processing the post: {e}",
            )

    async def process_gallery(self, post, title, interaction, nsfw):
        try:
            perm_url = post.get("permalink", "")
            reddit_post_url = urljoin("https://www.reddit.com", perm_url)
            prefix = "NSFW: " if nsfw else ""
            message = f"{prefix}{title}\n{reddit_post_url}"
            logger.info("Gallery post detected - sending preview link: %s", reddit_post_url)
            await safe_followup(interaction, message)
        except Exception as e:
            logger.exception(f"Error processing gallery content: {e}\n{traceback.format_exc()}")
            await safe_followup(
                interaction, f"Error processing gallery for {title}: {e}"
            )

    async def process_image(
        self, image_url, title, reddit_post_url=None, interaction=None, nsfw=False, upload_limit=None, session=None
    ):
        logger.debug("Image URL: %s", image_url)
        limit = self._limit(upload_limit)
        workdir = make_workdir()
        image_filename = os.path.join(workdir, sanitize_filename(f"{title}.jpg"))

        try:
            if session is None:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        image_url, timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        response.raise_for_status()
                        content_length = response.headers.get("Content-Length")
                        if content_length and int(content_length) > limit:
                            prefix = "NSFW: " if nsfw else ""
                            await send_content(
                                interaction, f"{prefix}{title}\n{reddit_post_url or image_url}"
                            )
                            return
                        with open(image_filename, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)
            else:
                async with session.get(
                    image_url, timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    response.raise_for_status()
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > limit:
                        prefix = "NSFW: " if nsfw else ""
                        await send_content(
                            interaction, f"{prefix}{title}\n{reddit_post_url or image_url}"
                        )
                        return
                    with open(image_filename, "wb") as f:
                        async for chunk in response.content.iter_chunked(8192):
                            f.write(chunk)

            if os.path.getsize(image_filename) > limit:
                prefix = "NSFW: " if nsfw else ""
                await send_content(
                    interaction, f"{prefix}{title}\n{reddit_post_url or image_url}"
                )
                return

            prefix = "NSFW: " if nsfw else ""
            content = (
                f"{prefix}{title}\n<{reddit_post_url}>"
                if reddit_post_url
                else f"{prefix}{title}"
            )
            await send_file(interaction, content, image_filename)

        finally:
            cleanup(workdir, image_filename)

    async def process_video(
        self, video_url, title, backup_video=None, interaction=None, nsfw=False, upload_limit=None, session=None
    ):
        logger.debug("Video URL: %s", video_url)
        limit = self._limit(upload_limit)
        workdir = make_workdir()
        video_filename = None

        try:
            if session is None:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        video_url, timeout=aiohttp.ClientTimeout(total=30, sock_read=None)
                    ) as response:
                        response.raise_for_status()
                        content_type = response.headers.get("Content-Type", "")
                        is_hls = (
                            "application/vnd.apple.mpegurl" in content_type
                            or "application/x-mpegurl" in content_type
                        )

                        if is_hls:
                            video_filename = os.path.join(
                                workdir, sanitize_filename(f"{title}.mp4")
                            )
                            await self._remux_hls(video_url, video_filename)
                            if os.path.exists(video_filename) and os.path.getsize(video_filename) > limit:
                                compressed_path = os.path.join(
                                    workdir, sanitize_filename(f"{title}_compressed.mp4")
                                )
                                await self._run_ffmpeg(video_filename, compressed_path)
                                video_filename = compressed_path
                        else:
                            content_length = response.headers.get("Content-Length")
                            if content_length and int(content_length) > limit:
                                logger.info(
                                    "Video at %s is larger than the upload limit, sending link.",
                                    video_url,
                                )
                                prefix = "NSFW: " if nsfw else ""
                                await send_content(
                                    interaction, f"{prefix}{title}\n{video_url}"
                                )
                                return

                            extension = (
                                os.path.splitext(urlparse(video_url).path)[1] or ".mp4"
                            )
                            video_filename = os.path.join(
                                workdir, sanitize_filename(f"{title}{extension}")
                            )
                            with open(video_filename, "wb") as f:
                                async for chunk in response.content.iter_chunked(8192):
                                    f.write(chunk)
            else:
                async with session.get(
                    video_url, timeout=aiohttp.ClientTimeout(total=30, sock_read=None)
                ) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "")
                    is_hls = (
                        "application/vnd.apple.mpegurl" in content_type
                        or "application/x-mpegurl" in content_type
                    )

                    if is_hls:
                        video_filename = os.path.join(
                            workdir, sanitize_filename(f"{title}.mp4")
                        )
                        await self._remux_hls(video_url, video_filename)
                        if os.path.exists(video_filename) and os.path.getsize(video_filename) > limit:
                            compressed_path = os.path.join(
                                workdir, sanitize_filename(f"{title}_compressed.mp4")
                            )
                            await self._run_ffmpeg(video_filename, compressed_path)
                            video_filename = compressed_path
                    else:
                        content_length = response.headers.get("Content-Length")
                        if content_length and int(content_length) > limit:
                            logger.info(
                                "Video at %s is larger than the upload limit, sending link.",
                                video_url,
                            )
                            prefix = "NSFW: " if nsfw else ""
                            await send_content(
                                interaction, f"{prefix}{title}\n{video_url}"
                            )
                            return

                        extension = (
                            os.path.splitext(urlparse(video_url).path)[1] or ".mp4"
                        )
                        video_filename = os.path.join(
                            workdir, sanitize_filename(f"{title}{extension}")
                        )
                        with open(video_filename, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)

            if not video_filename or not os.path.exists(video_filename):
                logger.error("Video file was not created.")
                return

            file_size = os.path.getsize(video_filename)
            if file_size == 0:
                logger.error("Downloaded video file is empty.")
                return

            if file_size > limit:
                logger.warning("Downloaded video file is too large to send to Discord.")
                fallback_url = backup_video or video_url
                trimmed_url = (
                    re.sub(r"/DASH.*", "", fallback_url)
                    if isinstance(fallback_url, str)
                    else fallback_url
                )
                prefix = "NSFW: " if nsfw else ""
                await send_content(interaction, f"{prefix}{title}\n{trimmed_url}")
                return

            trimmed_backup = (
                re.sub(r"/DASH.*", "", backup_video)
                if isinstance(backup_video, str)
                else ""
            )
            prefix = "NSFW: " if nsfw else ""
            content = f"{prefix}{title}"
            if trimmed_backup:
                content += f"\n<{trimmed_backup}>"

            await send_file(interaction, content, video_filename)

        except subprocess.TimeoutExpired:
            logger.warning("FFmpeg process timed out.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error processing video with ffmpeg: {e}\n{traceback.format_exc()}")
        except aiohttp.ClientError as e:
            logger.error(f"Error downloading video: {e}\n{traceback.format_exc()}")
        except Exception as e:
            logger.exception(f"Unexpected error in process_video: {e}\n{traceback.format_exc()}")
        finally:
            cleanup(workdir, video_filename)

    async def _run_ffmpeg(self, input_path, output_filename):
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-c:v",
            "libx264",
            "-crf",
            "25",
            "-preset",
            "veryfast",
            "-max_muxing_queue_size",
            "1024",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-bsf:a",
            "aac_adtstoasc",
            output_filename,
        ]
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: subprocess.run(ffmpeg_cmd, check=True, timeout=300)
        )
        logger.info("Successfully processed video: %s", output_filename)

    async def _remux_hls(self, source_url, output_filename):
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            source_url,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-max_muxing_queue_size",
            "1024",
            output_filename,
        ]
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: subprocess.run(ffmpeg_cmd, check=True, timeout=300)
        )
        logger.info("Successfully remuxed HLS video: %s", output_filename)

    async def process_gif(
        self, gif_url, title, reddit_post_url=None, interaction=None, nsfw=False, upload_limit=None, session=None
    ):
        logger.debug("Gif URL: %s", gif_url)
        limit = self._limit(upload_limit)
        workdir = make_workdir()
        gif_filename = os.path.join(workdir, sanitize_filename(f"{title}.gif"))

        try:
            if session is None:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        gif_url, timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        response.raise_for_status()
                        content_length = response.headers.get("Content-Length")
                        if content_length and int(content_length) > limit:
                            logger.info("GIF too large, sending link instead: %s", gif_url)
                            prefix = "NSFW: " if nsfw else ""
                            await send_content(
                                interaction, f"{prefix}{title}\n{reddit_post_url or gif_url}"
                            )
                            return
                        with open(gif_filename, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)
            else:
                async with session.get(
                    gif_url, timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    response.raise_for_status()
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > limit:
                        logger.info("GIF too large, sending link instead: %s", gif_url)
                        prefix = "NSFW: " if nsfw else ""
                        await send_content(
                            interaction, f"{prefix}{title}\n{reddit_post_url or gif_url}"
                        )
                        return
                    with open(gif_filename, "wb") as f:
                        async for chunk in response.content.iter_chunked(8192):
                            f.write(chunk)

            if os.path.getsize(gif_filename) > limit:
                logger.info("GIF too large, sending link instead: %s", gif_url)
                prefix = "NSFW: " if nsfw else ""
                await send_content(
                    interaction, f"{prefix}{title}\n{reddit_post_url or gif_url}"
                )
                return

            prefix = "NSFW: " if nsfw else ""
            content = f"{prefix}{title}\n<{reddit_post_url or gif_url}>"
            await send_file(interaction, content, gif_filename)

        finally:
            cleanup(workdir, gif_filename)

    async def fetch_and_send(self, interaction, reddit_url, upload_limit=None):
        """Fetch a single Reddit post by URL and process its media."""
        if not is_safe_url(reddit_url):
            await safe_followup(interaction, "URL is not safe to access.")
            return
        try:
            async with aiohttp.ClientSession(headers=self.reddit_auth.get_headers()) as session:
                if "/s/" in reddit_url:
                    async with session.get(
                        reddit_url,
                        allow_redirects=True,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        reddit_url = str(response.url)

                path = urlparse(reddit_url).path.rstrip("/")
                api_url = f"https://oauth.reddit.com{path}.json"

                async with session.get(
                    api_url, timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    response.raise_for_status()
                    data = await response.json()

                post_data = data[0]["data"]["children"][0]["data"]
                await self.get_post_content(
                    post_data,
                    interaction,
                    upload_limit=upload_limit,
                    session=session,
                )

        except Exception as e:
            logger.exception(f"Error fetching Reddit post: {e}\n{traceback.format_exc()}")
            await safe_followup(interaction, f"Error fetching post: {e}")