from dotenv import load_dotenv
import os

def load_env_variables():
    load_dotenv()
    return {
        "REDDIT_CLIENT_ID": os.getenv("REDDIT_CLIENT_ID"),
        "REDDIT_CLIENT_SECRET": os.getenv("REDDIT_CLIENT_SECRET"),
        "REDDIT_USER_AGENT": os.getenv("REDDIT_USER_AGENT"),
        "REDDIT_USERNAME": os.getenv("REDDIT_USERNAME"),
        "REDDIT_PASSWORD": os.getenv("REDDIT_PASSWORD"),
        "DISCORD_TOKEN": os.getenv("DISCORD_TOKEN"),
        "WEBHOOK": os.getenv("WEBHOOK"),
        "SERVICE_BUS_CONNECTION_STRING": os.getenv("SERVICE_BUS_CONNECTION_STRING"),
        "SERVICE_BUS_QUEUE_NAME": os.getenv("SERVICE_BUS_QUEUE_NAME", "siphon-queue"),
    }
