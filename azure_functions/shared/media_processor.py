import os
import sys
from typing import Dict, Any

# Import existing media handlers from the bot code bundled with the Function App package.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'python_files'))

from apis.reddit_api import RedditAuth  # type: ignore[reportMissingImports]
from media.reddit_handler import RedditMediaHandler  # type: ignore[reportMissingImports]
from media.media_handler import MediaHandler  # type: ignore[reportMissingImports]
from media.common import safe_followup  # type: ignore[reportMissingImports]
from shared.webhook_interaction import WebhookInteraction


def get_reddit_auth() -> RedditAuth:
    """Initialize Reddit OAuth from environment variables."""
    return RedditAuth(
        client_id=os.getenv('REDDIT_CLIENT_ID'),
        client_secret=os.getenv('REDDIT_CLIENT_SECRET'),
        username=os.getenv('REDDIT_USERNAME'),
        password=os.getenv('REDDIT_PASSWORD'),
        user_agent=os.getenv('REDDIT_USER_AGENT', 'SiphonBot/1.0')
    )


async def process_media_job(job: Dict[str, Any]) -> Dict[str, Any]:
    try:
        job_type = job.get("job_type", "scrape_subreddit")
        if job_type != "scrape_subreddit":
            return {
                "success": False,
                "error": f"Unsupported job type: {job_type}",
                "message": "Unsupported job type",
            }

        subreddit = job.get("subreddit")
        filter_type = job.get("filter_type", "hot")
        num_posts = int(job.get("num_posts", 1))
        time_range = job.get("time_range", "")
        webhook_url = job.get("webhook_url")

        if not subreddit:
            return {
                "success": False,
                "error": "Missing subreddit in job payload",
                "message": "Invalid payload",
            }
        if not webhook_url:
            return {
                "success": False,
                "error": "Missing webhook_url in job payload",
                "message": "Invalid payload",
            }

        reddit_auth = get_reddit_auth()
        media_handler = MediaHandler()
        reddit_handler = RedditMediaHandler(reddit_auth, media_handler)

        interaction = WebhookInteraction(webhook_url)
        await safe_followup(
            interaction,
            f"Starting queued scrape for r/{subreddit} ({num_posts} post(s), {filter_type})"
        )
        await reddit_handler.scrape_subreddit(
            interaction=interaction,
            subreddit_url=subreddit,
            num_posts=num_posts,
            filter_type=filter_type,
            time_range=time_range,
        )
        return {
            "success": True,
            "message": f"Successfully processed {num_posts} post(s) from r/{subreddit}",
        }

    except Exception as e:
        error_msg = f'Failed to process media job: {str(e)}'
        print(f'ERROR: {error_msg}')
        return {
            'success': False,
            'error': error_msg,
            'message': 'Media processing failed'
        }
