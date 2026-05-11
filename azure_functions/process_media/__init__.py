import azure.functions as func
import asyncio
import json
import traceback
from datetime import datetime

from shared.media_processor import process_media_job


def main(msg: func.ServiceBusQueueMessage) -> None:
    try:
        job = json.loads(msg.get_body().decode("utf-8"))
        job_type = job.get("job_type", "scrape_subreddit")
        print(
            f"[{datetime.utcnow().isoformat()}] Processing job type={job_type} subreddit={job.get('subreddit')} filter={job.get('filter_type', 'hot')}"
        )

        result = asyncio.run(process_media_job(job))
        if result["success"]:
            print(f"Job completed: {result['message']}")
        else:
            print(f"Job failed: {result['error']}")
            raise RuntimeError(result["error"])

    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in message: {str(e)}")
        raise RuntimeError(f"Invalid message format: {str(e)}")
    except Exception as e:
        print(f"ERROR: Unexpected error processing job: {str(e)}")
        print(traceback.format_exc())
        raise RuntimeError(f"Job processing failed: {str(e)}")
