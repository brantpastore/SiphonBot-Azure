# media/common.py
import os
import tempfile
import uuid
import aiohttp
import discord
import shutil

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
        print(f"Cleanup error: {e}")


async def safe_followup(interaction, message):
    if interaction is None or not hasattr(interaction, "followup"):
        return
    try:
        await interaction.followup.send(message)
    except Exception as e:
        print(f"Failed to send followup: {e}")


async def send_content(interaction, content):
    if interaction is None:
        return
    try:
        await interaction.channel.send(content=content)
    except Exception as e:
        print(f"Failed to send content: {e}")


async def send_file(interaction, content, filepath):
    if interaction is None:
        return
    try:
        channel = interaction.channel
        if content:
            await channel.send(content=content)
        await channel.send(file=discord.File(filepath))
    except Exception as e:
        print(f"Failed to send file: {e}")
