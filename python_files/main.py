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

if __name__ == "__main__":
    env_vars = load_env_variables()

    print("Environment variables loaded successfully.")
    track_event('startup', {"stage": "env_loaded"})

    reddit_auth = RedditAuth(
        env_vars["REDDIT_CLIENT_ID"],
        env_vars["REDDIT_CLIENT_SECRET"],
        env_vars["REDDIT_USERNAME"],
        env_vars["REDDIT_PASSWORD"],
        env_vars["REDDIT_USER_AGENT"],
    )

    bot = SiphonBot(
        env_vars["DISCORD_TOKEN"],
        env_vars["WEBHOOK"],
        reddit_auth,
        service_bus_connection=env_vars["SERVICE_BUS_CONNECTION_STRING"],
        service_bus_queue=env_vars["SERVICE_BUS_QUEUE_NAME"],
    )

    bot.run()
