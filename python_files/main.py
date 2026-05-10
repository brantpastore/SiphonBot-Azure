import logging
import os
import sys

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
