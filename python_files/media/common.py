# media/common.py
import os
import tempfile
import uuid
import aiohttp
import discord
import shutil
import logging
import traceback
from asyncio import sleep

logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = int(os.getenv("DISCORD_MAX_UPLOAD_MB", "25")) * 1024 * 1024


def make_workdir():
    base_tmp = os.getenv("MEDIA_TMP_DIR") or tempfile.gettempdir()
    path = os.path.join(base_tmp, f"media_{uuid.uuid4().hex}")
    os.makedirs(path, exist_ok=True)
    return path


def cleanup(workdir, filepath=None):
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
        if workdir and os.path.isdir(workdir):
            shutil.rmtree(workdir, ignore_errors=True)
    except Exception as e:
        logger.exception(f"Cleanup error: {e}")


async def safe_followup(interaction, message):
    """Send a followup with retry logic for transient Discord errors (issue 30)."""
    if interaction is None or not hasattr(interaction, "followup"):
        return
    max_retries = 2
    for attempt in range(max_retries):
        try:
            await interaction.followup.send(message)
            return
        except Exception as e:
            if attempt < max_retries - 1:
                # Transient error; retry after brief delay
                logger.warning(f"Followup failed (attempt {attempt + 1}), retrying: {e}")
                await sleep(0.5)
            else:
                # Final failure
                logger.exception(f"Failed to send followup after {max_retries} attempts: {e}\n{traceback.format_exc()}")


async def send_content(interaction, content):
    if interaction is None:
        return
    try:
        await interaction.channel.send(content=content)
    except Exception as e:
        logger.exception(f"Failed to send content: {e}\n{traceback.format_exc()}")


async def send_file(interaction, content, filepath):
    if interaction is None:
        return
    try:
        channel = interaction.channel
        if content:
            await channel.send(content=content)
        await channel.send(file=discord.File(filepath))
    except Exception as e:
        logger.exception(f"Failed to send file: {e}\n{traceback.format_exc()}")
