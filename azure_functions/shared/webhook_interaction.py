import json
import os

import aiohttp


def _resolve_path_from_discord_file(file_obj):
    # discord.File exposes fp with a filename path for files created from a local path.
    fp = getattr(file_obj, "fp", None)
    if fp and hasattr(fp, "name"):
        return fp.name

    fallback = getattr(file_obj, "filename", None)
    if fallback and os.path.exists(fallback):
        return fallback

    return None


class _WebhookEndpoint:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, content=None, file=None, view=None):
        # View components are Discord-interaction specific and not supported via plain webhook posts.
        if view is not None:
            print("Ignoring interactive view for webhook send in function worker.")

        if file is None:
            payload = {"content": content or ""}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    response.raise_for_status()
                    return await response.text()

        file_path = _resolve_path_from_discord_file(file)
        if not file_path:
            raise ValueError("Could not resolve file path from discord.File payload")

        payload_json = {"content": content or ""}
        form = aiohttp.FormData()
        form.add_field("payload_json", json.dumps(payload_json))
        with open(file_path, "rb") as fh:
            form.add_field("file", fh, filename=os.path.basename(file_path))
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as response:
                    response.raise_for_status()
                    return await response.text()


class WebhookInteraction:
    def __init__(self, webhook_url: str):
        endpoint = _WebhookEndpoint(webhook_url)
        self.followup = endpoint
        self.channel = endpoint
