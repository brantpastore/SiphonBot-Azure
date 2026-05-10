import aiohttp
import discord
import os
import time
from discord import app_commands
from apis.reddit_api import RedditAuth, check_subreddit_exists
from apis.job_queue import JobQueuePublisher
from media.reddit_handler import RedditMediaHandler
from media.media_handler import MediaHandler

# Constants for dropdown menu options
FILTER_TYPES = ["hot", "new", "top", "rising"]
TIME_RANGES = ["hour", "day", "week", "month", "year", "all"]
NUM_POSTS = [1, 2, 3, 4, 5]


class SiphonBot:
    def __init__(
        self,
        token: str,
        webhook: str,
        reddit_auth: RedditAuth,
        service_bus_connection: str = "",
        service_bus_queue: str = "siphon-queue",
    ):
        self.token = token
        self.webhook = webhook
        self.reddit_auth = reddit_auth
        self.service_bus_connection = service_bus_connection
        self.service_bus_queue = service_bus_queue
        self.bot = discord.Client(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self.bot)
        self.subreddits = {
            1: "memes",
            2: "combatfootage",
            3: "greentext",
            4: "dankmemes",
            5: "pics",
        }
        self.media = MediaHandler()
        self.reddit = RedditMediaHandler(self.reddit_auth, self.media)
        self.cooldowns: dict[int, float] = {}
        self.cooldown_seconds = 5
        self.queue_publisher = self._build_queue_publisher()
        self.commands_synced = False
        self.setup_bot_commands()

    def _build_queue_publisher(self):
        if not self.service_bus_connection:
            print("Hybrid mode disabled: no Service Bus connection string. Using inline processing.")
            return None

        print(f"Hybrid mode enabled: queueing jobs to Service Bus queue '{self.service_bus_queue}'.")
        return JobQueuePublisher(self.service_bus_connection, self.service_bus_queue)

    def check_cooldown(self, user_id: int) -> float:
        """Returns seconds remaining, or 0 if ready."""
        remaining = self.cooldowns.get(user_id, 0) - time.time()
        return max(remaining, 0)

    def set_cooldown(self, user_id: int):
        self.cooldowns[user_id] = time.time() + self.cooldown_seconds

    @staticmethod
    def _env_flag(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def setup_bot_commands(self):
        @self.tree.command(name="scrape", description="Scrape posts from a subreddit")
        async def scrape_command(
            interaction: discord.Interaction,
            subreddit_number: int,
            num_posts: int = 1,
            filter_type: str = "hot",
            time_range: str = "",
        ):
            remaining = self.check_cooldown(interaction.user.id)
            if remaining:
                await interaction.response.send_message(
                    f"Cooldown — try again in {int(remaining)}s.", ephemeral=True
                )
                return

            if subreddit_number in self.subreddits:
                subreddit_url = self.subreddits[subreddit_number]

                if num_posts > 5:
                    num_posts = 5
                elif num_posts < 1:
                    num_posts = 1

                self.set_cooldown(interaction.user.id)
                await interaction.response.defer()
                if self.queue_publisher:
                    await self.queue_publisher.enqueue_scrape_job(
                        subreddit=subreddit_url,
                        filter_type=filter_type,
                        num_posts=num_posts,
                        time_range=time_range,
                        webhook_url=self.webhook,
                        requested_by=str(interaction.user.id),
                    )
                    await interaction.followup.send(
                        f"Queued scrape job for r/{subreddit_url} ({num_posts} post(s), {filter_type})."
                    )
                else:
                    await interaction.followup.send(
                        f"Starting to scrape {num_posts} posts from: r/{subreddit_url}"
                    )
                    upload_limit = interaction.guild.filesize_limit if interaction.guild else None
                    print(f"Guild upload limit: {upload_limit} bytes")
                    await self.reddit.scrape_subreddit(
                        interaction, subreddit_url, num_posts, filter_type, time_range,
                        upload_limit=upload_limit
                    )
            else:
                await interaction.response.send_message(
                    "Invalid subreddit number. Please choose a number between 1 and 5."
                )

        @self.tree.command(
            name="list_subreddits", description="List available subreddits"
        )
        async def list_subreddits(interaction: discord.Interaction):
            subreddit_list = "\n".join(
                [f"{k}. {v}" for k, v in self.subreddits.items()]
            )
            await interaction.response.send_message(
                f"Available subreddits to scrape:\n{subreddit_list}"
            )

        @self.tree.command(
            name="scrape_custom", description="Scrape posts from a custom subreddit"
        )
        async def scrape_custom_command(
            interaction: discord.Interaction,
            subreddit_name: str,
            num_posts: int = 1,
            filter_type: str = "hot",
            time_range: str = "",
        ):
            remaining = self.check_cooldown(interaction.user.id)
            if remaining:
                await interaction.response.send_message(
                    f"Cooldown — try again in {int(remaining)}s.", ephemeral=True
                )
                return

            try:
                subreddit_exists = await check_subreddit_exists(
                    subreddit_name, self.reddit_auth
                )
            except Exception as e:
                print(f"Error checking subreddit existence: {e}")
                await interaction.response.send_message(
                    f"Error checking subreddit: {e}"
                )
                return

            if subreddit_exists:
                if num_posts > 5:
                    num_posts = 5
                elif num_posts < 1:
                    num_posts = 1

                self.set_cooldown(interaction.user.id)
                await interaction.response.defer()
                if self.queue_publisher:
                    await self.queue_publisher.enqueue_scrape_job(
                        subreddit=subreddit_name,
                        filter_type=filter_type,
                        num_posts=num_posts,
                        time_range=time_range,
                        webhook_url=self.webhook,
                        requested_by=str(interaction.user.id),
                    )
                    await interaction.followup.send(
                        f"Queued scrape job for r/{subreddit_name} ({num_posts} post(s), {filter_type})."
                    )
                else:
                    await interaction.followup.send(
                        f"Starting to scrape {num_posts} posts from: r/{subreddit_name}"
                    )
                    await self.reddit.scrape_subreddit(
                        interaction, subreddit_name, num_posts, filter_type, time_range
                    )
            else:
                await interaction.response.send_message(
                    "Invalid subreddit name. Community not found. Please provide a valid subreddit name."
                )

        @self.tree.command(
            name="reddit",
            description="Fetch a Reddit post by URL and post its media to this channel",
        )
        async def reddit_command(
            interaction: discord.Interaction,
            url: str,
        ):
            remaining = self.check_cooldown(interaction.user.id)
            if remaining:
                await interaction.response.send_message(
                    f"Cooldown — try again in {int(remaining)}s.", ephemeral=True
                )
                return

            self.set_cooldown(interaction.user.id)
            await interaction.response.defer()
            await interaction.followup.send(f"Fetching Reddit post: {url}")
            upload_limit = interaction.guild.filesize_limit if interaction.guild else None
            print(f"Guild upload limit: {upload_limit} bytes")
            await self.reddit.fetch_and_send(interaction, url, upload_limit=upload_limit)

        @self.tree.command(
            name="download",
            description="Download a YouTube, Instagram, TikTok, or Reddit video and post it to this channel",
        )
        async def dl_command(
            interaction: discord.Interaction,
            url: str,
        ):
            remaining = self.check_cooldown(interaction.user.id)
            if remaining:
                await interaction.response.send_message(
                    f"Cooldown — try again in {int(remaining)}s.", ephemeral=True
                )
                return

            self.set_cooldown(interaction.user.id)
            await interaction.response.defer()
            upload_limit = interaction.guild.filesize_limit if interaction.guild else None
            print(f"Guild upload limit: {upload_limit} bytes")

            # Reddit URLs (posts and v.redd.it) require authenticated API access;
            # route them through the reddit handler instead of yt-dlp.
            if any(domain in url for domain in ("reddit.com", "redd.it", "v.redd.it")):
                await interaction.followup.send(f"Fetching Reddit media: {url}")
                await self.reddit.fetch_and_send(interaction, url, upload_limit=upload_limit)
            else:
                await interaction.followup.send(f"Downloading: {url}")
                await self.media.download_and_send(interaction, url, upload_limit=upload_limit)

        @scrape_custom_command.autocomplete("filter_type")
        async def filter_type_autocomplete(
            interaction: discord.Interaction, current: str
        ):
            return [
                app_commands.Choice(name=ft, value=ft)
                for ft in FILTER_TYPES
                if current.lower() in ft.lower()
            ]

        @scrape_custom_command.autocomplete("time_range")
        async def time_range_autocomplete_custom(
            interaction: discord.Interaction, current: str
        ):
            return [
                app_commands.Choice(name=tr, value=tr)
                for tr in TIME_RANGES
                if current.lower() in tr.lower()
            ]

        @scrape_custom_command.autocomplete("num_posts")
        async def num_posts_autocomplete_custom(
            interaction: discord.Interaction, current: str
        ):
            return [
                app_commands.Choice(name=str(n), value=n)
                for n in NUM_POSTS
                if current in str(n)
            ]

        @scrape_command.autocomplete("subreddit_number")
        async def subreddit_number_autocomplete(
            interaction: discord.Interaction, current: str
        ):
            return [
                app_commands.Choice(name=str(k), value=k)
                for k in self.subreddits.keys()
                if current in str(k)
            ]

        @scrape_command.autocomplete("filter_type")
        async def filter_type_autocomplete_scrape(
            interaction: discord.Interaction, current: str
        ):
            return [
                app_commands.Choice(name=ft, value=ft)
                for ft in FILTER_TYPES
                if current.lower() in ft.lower()
            ]

        @scrape_command.autocomplete("time_range")
        async def time_range_autocomplete(
            interaction: discord.Interaction, current: str
        ):
            return [
                app_commands.Choice(name=tr, value=tr)
                for tr in TIME_RANGES
                if current.lower() in tr.lower()
            ]

        @scrape_command.autocomplete("num_posts")
        async def num_posts_autocomplete(
            interaction: discord.Interaction, current: str
        ):
            return [
                app_commands.Choice(name=str(n), value=n)
                for n in NUM_POSTS
                if current in str(n)
            ]

    async def sync_commands(self):
        try:
            guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()
            sync_global = self._env_flag("DISCORD_SYNC_GLOBAL", True)

            if guild_id.isdigit():
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"Synced {len(synced)} guild command(s) to guild {guild_id}")
            elif self.bot.guilds:
                # Auto-detect guilds from the active bot session when no guild id is configured.
                for g in self.bot.guilds:
                    guild = discord.Object(id=g.id)
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    print(f"Auto-synced {len(synced)} guild command(s) to guild {g.id} ({g.name})")
            else:
                print("DISCORD_GUILD_ID not set and no guilds available yet; skipping guild sync.")

            if sync_global:
                synced = await self.tree.sync()
                print(f"Synced {len(synced)} global command(s)")
            elif not guild_id.isdigit() and not self.bot.guilds:
                # Fallback so commands still get registered when guild cache is empty.
                synced = await self.tree.sync()
                print(f"Fallback: synced {len(synced)} global command(s)")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

    def run(self):
        @self.bot.event
        async def on_ready():
            if not self.commands_synced:
                await self.sync_commands()
                self.commands_synced = True
            print(f"{self.bot.user} has connected to Discord!")
            print(f"Bot is active in {len(self.bot.guilds)} servers.")
            print("Ready to receive commands!")

            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        self.webhook,
                        json={
                            "content": f"{self.bot.user} is ready to receive commands!"
                        },
                        timeout=aiohttp.ClientTimeout(total=10),
                    )
            except Exception as e:
                print(f"Error sending message to webhook: {e}")

        self.bot.run(self.token)