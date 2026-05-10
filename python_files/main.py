import logging
import os
import sys
import base64

from env_config import load_env_variables
from apis.reddit_api import RedditAuth
from discord_bot import SiphonBot

# Optional telemetry — import if available, otherwise provide no-op fallbacks
try:
    from Azurify.telemetry.app_insights_snippet import track_event, track_exception
except Exception:
    def track_event(*args, **kwargs):
        return None

    def track_exception(*args, **kwargs):
        return None


def _configure_logging() -> None:
    """
    Configure root logging. Defaults to INFO; set LOG_LEVEL=DEBUG in the
    Container App environment variables (non-secret) for verbose startup output.
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger(__name__).info(
        "[startup] Logging initialised at level %s (LOG_LEVEL=%s)",
        logging.getLevelName(level), level_name,
    )


def _configure_yt_dlp_cookies() -> None:
    """
    Materialize yt-dlp cookies from environment variables into a file.

    Supported env vars:
      - YTDLP_COOKIES: raw cookies.txt content
      - YTDLP_COOKIES_B64: base64-encoded cookies.txt content
      - YTDLP_COOKIES_FILE: optional explicit output path
    """
    logger = logging.getLogger(__name__)

    raw = os.environ.get("YTDLP_COOKIES", "").strip()
    b64 = os.environ.get("YTDLP_COOKIES_B64", "").strip()

    if not raw and not b64:
        logger.info("[startup] yt-dlp cookies not configured (YTDLP_COOKIES/YTDLP_COOKIES_B64 absent).")
        return

    cookie_content = raw
    if not cookie_content and b64:
        try:
            cookie_content = base64.b64decode(b64).decode("utf-8")
        except Exception as exc:
            logger.error("[startup] Failed decoding YTDLP_COOKIES_B64: %s", exc)
            return

    cookie_file = os.environ.get("YTDLP_COOKIES_FILE", "/tmp/siphon/yt_cookies.txt")
    cookie_dir = os.path.dirname(cookie_file) or "/tmp"
    try:
        os.makedirs(cookie_dir, exist_ok=True)
        with open(cookie_file, "w", encoding="utf-8") as f:
            f.write(cookie_content)
        os.chmod(cookie_file, 0o600)
        os.environ["YTDLP_COOKIES_FILE"] = cookie_file
        logger.info("[startup] yt-dlp cookie file ready at %s", cookie_file)
    except Exception as exc:
        logger.error("[startup] Failed writing yt-dlp cookie file: %s", exc)


if __name__ == "__main__":
    _configure_logging()
    logger = logging.getLogger(__name__)

    logger.info("[startup] SiphonBot starting up...")
    logger.info("[startup] Python %s", sys.version)
    logger.info(
        "[startup] Container App context: NAME=%s REVISION=%s HOSTNAME=%s",
        os.environ.get("CONTAINER_APP_NAME", "<not set>"),
        os.environ.get("CONTAINER_APP_REVISION", "<not set>"),
        os.environ.get("CONTAINER_APP_HOSTNAME", "<not set>"),
    )

    _configure_yt_dlp_cookies()

    env_vars = load_env_variables()

    logger.info("[startup] Environment variables loaded successfully.")
    track_event('startup', {"stage": "env_loaded"})

    logger.info("[startup] Initialising Reddit auth...")
    reddit_auth = RedditAuth(
        env_vars["REDDIT_CLIENT_ID"],
        env_vars["REDDIT_CLIENT_SECRET"],
        env_vars["REDDIT_USERNAME"],
        env_vars["REDDIT_PASSWORD"],
        env_vars["REDDIT_USER_AGENT"],
    )

    logger.info("[startup] Building SiphonBot (service_bus_queue=%s)...", env_vars["SERVICE_BUS_QUEUE_NAME"])
    bot = SiphonBot(
        env_vars["DISCORD_TOKEN"],
        env_vars["WEBHOOK"],
        reddit_auth,
        service_bus_connection=env_vars["SERVICE_BUS_CONNECTION_STRING"],
        service_bus_queue=env_vars["SERVICE_BUS_QUEUE_NAME"],
    )

    logger.info("[startup] Starting bot event loop.")
    bot.run()
