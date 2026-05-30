import json
from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient


class JobQueuePublisher:
    def __init__(self, connection_string: str, queue_name: str):
        self.connection_string = connection_string
        self.queue_name = queue_name

    async def enqueue_scrape_job(
        self,
        subreddit: str,
        filter_type: str,
        num_posts: int,
        time_range: str,
        webhook_url: str,
        requested_by: str,
    ):
        payload = {
            "job_type": "scrape_subreddit",
            "subreddit": subreddit,
            "filter_type": filter_type,
            "num_posts": num_posts,
            "time_range": time_range,
            "webhook_url": webhook_url,
            "requested_by": requested_by,
        }

        body = json.dumps(payload)
        async with ServiceBusClient.from_connection_string(self.connection_string) as client:
            sender = client.get_queue_sender(queue_name=self.queue_name)
            async with sender:
                await sender.send_messages(ServiceBusMessage(body))
