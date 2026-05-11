# SiphonBot

A Discord bot that grabs media from Reddit and YouTube and posts it directly into your server.

## What it does

- **Reddit scraping** - Pull images, videos, and GIFs from subreddits by filter (hot, new, top, rising)
- **Reddit links** - Paste any Reddit post URL and SiphonBot downloads and posts the media, including share links (`/s/` short URLs)
- **YouTube downloads** - Paste a YouTube URL and SiphonBot downloads the video and uploads it to Discord
- **Oversize handling** - Videos that exceed Discord's 50MB upload limit can be compressed (480p re-encode) or split into timestamped parts
- **NSFW tagging** - Reddit posts marked NSFW are automatically prefixed

## Commands

| Command                                                                  | Description                          |
| ------------------------------------------------------------------------ | ------------------------------------ |
| `/yt <url>`                                                              | Download a YouTube video and post it |
| `/reddit <url>`                                                          | Fetch media from a Reddit post URL   |
| `/scrape <subreddit_number> [num_posts] [filter_type] [time_range]`      | Scrape posts from a preset subreddit |
| `/scrape_custom <subreddit_name> [num_posts] [filter_type] [time_range]` | Scrape posts from any subreddit      |
| `/list_subreddits`                                                       | List preset subreddits               |

## YouTube size limits

Discord's upload limit is 50MB (Level 2 boost). When a video exceeds this, SiphonBot presents two options via buttons:

- **Compress** - Re-encodes at 480p with a calculated bitrate to fit under the limit. Only offered when the video is short enough for compression to produce a watchable result (≥500kbps video bitrate).
- **Split into parts** - Splits the video into chunks using stream copy (no quality loss). Each part is labeled with timestamps, e.g. `Video Title (Part 2/4 - 2:30–5:00)`.

## Setup

### Environment variables

Create a `.env` file in the project root:

```
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=your_user_agent
REDDIT_USERNAME=your_username
REDDIT_PASSWORD=your_password
DISCORD_TOKEN=your_discord_bot_token
WEBHOOK=your_discord_webhook_url
SERVICE_BUS_CONNECTION_STRING=Endpoint=sb://...;SharedAccessKeyName=...;SharedAccessKey=...
SERVICE_BUS_QUEUE_NAME=siphon-queue
MEDIA_TMP_DIR=/tmp/siphon
```

If SERVICE_BUS_CONNECTION_STRING is set, scrape commands run in hybrid mode:
- The Container App bot enqueues a job to Service Bus.
- The Azure Function worker processes the job and posts results to Discord via webhook.

If SERVICE_BUS_CONNECTION_STRING is not set, the bot falls back to inline processing.

For Azure Container Apps deployment, configure secret values in GitHub repository secrets.
The CI/CD workflow writes these values into the Container App secrets section and maps
runtime environment variables using `secretref:` entries.

Container media downloads use ephemeral mounted storage (`EmptyDir`) at `/tmp/siphon`.

### Run with Docker

```bash
docker compose up -d --build
```

### View logs

```bash
docker logs -f siphon_bot
```

### Rebuild after changes

```bash
docker compose down
docker compose up -d --build
```


### All in One Line
```bash
docker compose down && docker compose up -d --build && docker logs -f siphon_bot
```

## Logging

Log verbosity is controlled by the `LOG_LEVEL` environment variable (default: `INFO`).

| Level   | Output                                                                                                          |
| ------- | --------------------------------------------------------------------------------------------------------------- |
| `INFO`  | Startup phases, secret source (Container App API vs env vars), HTTP status, present/missing key summary         |
| `DEBUG` | All of the above plus each secret name → config key mapping (values masked as `abcd****`), per-key resolved status |

### Azure Container Apps — change log level without redeploying

```bash
# Enable verbose debug logging
az containerapp update \
  -g siphon_bot \
  -n siphonbot-app \
  --set-env-vars LOG_LEVEL=DEBUG

# Revert to normal
az containerapp update \
  -g siphon_bot \
  -n siphonbot-app \
  --set-env-vars LOG_LEVEL=INFO
```

Then tail logs:

```bash
az containerapp logs show -g siphon_bot -n siphonbot-app --follow
```

You can also set `LOG_LEVEL` in the Azure Portal under **Container Apps → siphonbot-app → Containers → Environment variables**.

### Local / Docker

Add `LOG_LEVEL=DEBUG` to your `.env` file, or pass it inline:

```bash
LOG_LEVEL=DEBUG docker compose up
```

## Project structure

```
├── Dockerfile
├── docker-compose.yml
├── python_files/
│   ├── main.py                  # Entry point - loads env, authenticates Reddit, starts bot
│   ├── discord_bot.py           # Discord client, slash commands, command tree
│   ├── env_config.py            # Environment variable loader
│   ├── utils.py                 # Filename sanitization
│   ├── apis/
│   │   └── reddit_api.py        # Reddit OAuth token + subreddit validation
│   └── media/
│       ├── common.py            # Shared helpers - file send, cleanup, temp dirs
│       ├── reddit_handler.py    # Reddit media pipeline - images, videos, GIFs, galleries
│       └── youtube_handler.py   # YouTube download, compress, split pipeline
└── text_files/
    └── requirements.txt
```

## Backlog

- **Offload `/download` to the Function App** — `/scrape` commands already enqueue work to the Azure Service Bus queue for async processing by the Function App, but `/download` (YouTube / yt-dlp) still runs in-process inside the Container App. Offloading it would keep the bot responsive when multiple downloads are queued simultaneously. Blockers to address:
  - The Function App (Consumption plan Linux) has no `ffmpeg` binary — it would need to be bundled as a self-contained executable or the plan switched to Premium/Dedicated.
  - A new `download_video` job type must be added to `azure_functions/shared/media_processor.py`.
  - The bot's `/download` handler must be wired to `queue_publisher.enqueue_download_job(...)` (analogous to the existing `enqueue_scrape_job`).

## Dependencies

- [discord.py](https://discordpy.readthedocs.io/) - Discord bot framework
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - YouTube downloader
- [ffmpeg](https://ffmpeg.org/) - Video compression and splitting
- [aiohttp](https://docs.aiohttp.org/) - Async HTTP for Reddit API
- [python-dotenv](https://github.com/theskumar/python-dotenv) - Environment variable loading