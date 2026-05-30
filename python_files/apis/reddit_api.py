import time
import aiohttp
import requests
import requests.auth

import logging

logger = logging.getLogger(__name__)


class RedditAuth:
    def __init__(self, client_id, client_secret, username, password, user_agent):
        self.client_id = client_id
        self.client_secret = client_secret
        self.username = username
        self.password = password
        self.user_agent = user_agent
        self.token = None
        self.expires_at = 0

    def get_headers(self):
        """Returns fresh headers, refreshing token if needed."""
        if not self.token or time.time() >= self.expires_at:
            self._refresh()
        return {
            "Authorization": f"bearer {self.token}",
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
        }

    def _refresh(self):
        auth = requests.auth.HTTPBasicAuth(self.client_id, self.client_secret)
        data = {
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
        }
        headers = {"User-Agent": self.user_agent}

        response = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=auth,
            data=data,
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()

        self.token = result.get("access_token")
        if not self.token:
            raise KeyError("Access token not found in response.")

        # Reddit tokens expire in 3600s, refresh 5 min early
        self.expires_at = time.time() + result.get("expires_in", 3600) - 300
        logger.info("Reddit token refreshed.")


async def check_subreddit_exists(subreddit_name, reddit_auth):
    async with aiohttp.ClientSession(headers=reddit_auth.get_headers()) as session:
        async with session.get(
            f"https://oauth.reddit.com/r/{subreddit_name}/about",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status == 200:
                return True
            if response.status == 404:
                return False
            response.raise_for_status()
            return False
