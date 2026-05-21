import logging
import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional

import aiohttp
import discord
from discord import app_commands, Permissions
from discord.ext import commands, tasks
from galactia.repositories import GuildSettingsRepository, TwitchFollowRepository
from galactia.settings import settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------- Formatting helpers ----------

def _fmt_duration(start_iso: str) -> str:
    """
    Convert an ISO UTC timestamp to a compact elapsed duration (e.g., 01h23m / 12m34s / 45s).
    Falls back to "—" on parsing issues.
    """
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - start
        total_sec = int(delta.total_seconds())
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        if h > 0:
            return f"{h:02d}h{m:02d}m"
        if m > 0:
            return f"{m}m{s:02d}s"
        return f"{s}s"
    except Exception:
        return "—"


def _fmt_datetime(iso_ts: str) -> str:
    """
    Format an ISO UTC timestamp in Europe/Paris local time as 'dd/mm/YYYY HH:MM'.
    Returns '?' if parsing fails.
    """
    try:
        dt_utc = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        dt_paris = dt_utc.astimezone(ZoneInfo("Europe/Paris"))
        return dt_paris.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return "?"


# ---------- Cog ----------

class TwitchNotifier(commands.Cog):
    """
    Twitch live notifier (polling Twitch Helix).
    - Stores follows/state in PostgreSQL
    - Slash commands (scoped under /twitch): add | remove | list | test_online | test_offline
    - Announces when a followed channel goes live, edits when it ends
    """

    def __init__(self, bot: commands.Bot, session: aiohttp.ClientSession, exit_stack: AsyncExitStack):
        self.bot = bot
        self.twitch_client_id = settings.twitch_client_id
        self.twitch_client_secret = settings.twitch_client_secret
        self.check_interval = int(settings.twitch_check_interval)
        self._oauth_token: Optional[str] = None
        self._oauth_expire_ts: float = 0
        self.session = session
        self._exit_stack = exit_stack
        self.stream_repo = TwitchFollowRepository()
        self.guild_settings_repo = GuildSettingsRepository()

    async def initialize(self):
        if not self.twitch_client_id or not self.twitch_client_secret:
            logger.warning("Twitch credentials are missing; Twitch notifier will not start.")
        else:
            await self.refresh_poll_interval()
            self.poller.change_interval(seconds=self.check_interval)
            self.poller.start()

    def cog_unload(self):
        """Stop the background poller and close HTTP session when the cog is unloaded."""
        self.bot.loop.create_task(self._shutdown())

    async def _shutdown(self):
        if self.poller.is_running():
            self.poller.cancel()
        await self._exit_stack.aclose()

    async def get_or_create_guild_settings(self, guild_id: int) -> Dict:
        return await self.guild_settings_repo.get_or_create(
            guild_id,
            twitch_check_interval=settings.twitch_check_interval,
            twitch_announce_channel_id=settings.twitch_announce_channel_id,
            youtube_check_interval=settings.youtube_check_interval,
            youtube_announce_channel_id=settings.youtube_announce_channel_id,
        )

    async def refresh_poll_interval(self):
        configs = await self.guild_settings_repo.list_all()
        intervals = [
            int(cfg["twitch_check_interval"])
            for cfg in configs
            if cfg.get("twitch_check_interval") and cfg.get("twitch_enabled", True)
        ]
        self.check_interval = min(intervals) if intervals else int(settings.twitch_check_interval)
        if self.poller.is_running():
            self.poller.change_interval(seconds=self.check_interval)

    # ==========================
    # Slash command group /twitch (admin-only)
    # ==========================
    twitch_group = app_commands.Group(
        name="twitch",
        description="Manage Twitch live notifications",
        default_permissions=Permissions(administrator=True),
    )

    # ----- Admin commands -----

    @twitch_group.command(name="add", description="Follow a Twitch channel and announce its lives")
    @app_commands.describe(
        twitch_login="Twitch login (e.g. 'shroud')",
        channel="Discord channel for announcements",
        role="Optional role to ping on live"
    )
    async def twitch_add(
        self,
        interaction: discord.Interaction,
        twitch_login: str,
        channel: discord.abc.GuildChannel,
        role: Optional[discord.Role] = None
    ):
        """Add a Twitch login to the follow list for a given announcement channel."""
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Please pick a text channel.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        if interaction.guild_id is None:
            return await interaction.followup.send(
                "This command must be used in a server.",
                ephemeral=True,
            )

        guild_id = int(interaction.guild_id)
        cfg = await self.get_or_create_guild_settings(guild_id)
        if not cfg.get("twitch_enabled", False):
            return await interaction.followup.send(
                "Twitch is not enabled yet. Run `/galactia setup twitch` first.",
                ephemeral=True,
            )
        login = twitch_login.strip().lower()
        exists = await self.stream_repo.exists(guild_id, login, channel.id)

        if exists:
            return await interaction.followup.send(
                f"Already following **{login}** in {channel.mention}.",
                ephemeral=True,
            )

        await self.stream_repo.upsert(
            {
                "guild_id": guild_id,
                "login": login,
                "channel_id": channel.id,
                "role_id": role.id if role else None,
                "live": False,
                "last_started_at": None,
                "last_message_id": None,
                "peak_viewers": 0,
                "last_game_id": None,
                "last_box_art_url": None,
                "last_display_name": None,
                "last_stream_title": None,
                "last_game_name": None,
                "profile_image_url": None,
                "last_user_id": None,
            }
        )
        await interaction.followup.send(
            f"Now following **{login}** in {channel.mention}"
            + (f" (mention {role.mention})" if role else ""),
            ephemeral=True,
        )

    @twitch_group.command(name="list", description="List followed Twitch channels")
    async def list_streams(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            return await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
        cfg = await self.get_or_create_guild_settings(int(interaction.guild_id))
        if not cfg.get("twitch_enabled", False):
            return await interaction.response.send_message(
                "Twitch is not enabled yet. Run `/galactia setup twitch` first.",
                ephemeral=True,
            )
        data = await self.stream_repo.list_by_guild(int(interaction.guild_id))
        if not data:
            return await interaction.response.send_message("No follows yet.", ephemeral=True)

        lines = []
        for s in data:
            ch = interaction.guild.get_channel(s["channel_id"])
            rid = s.get("role_id")

            dest = ch.mention if ch else f"#deleted({s['channel_id']})"
            ping = f" (ping <@&{rid}>)" if rid else ""
            live_flag = " — live" if s.get("live") else ""

            lines.append(f"• **{s['login']}** → {dest}{ping}{live_flag}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @twitch_group.command(name="remove", description="Stop following a Twitch channel (all destinations)")
    @app_commands.describe(twitch_login="Twitch login to remove")
    async def twitch_remove(self, interaction: discord.Interaction, twitch_login: str):
        """Remove any follow entries for the given Twitch login."""
        await interaction.response.defer(ephemeral=True, thinking=True)

        if interaction.guild_id is None:
            return await interaction.followup.send(
                "This command must be used in a server.",
                ephemeral=True,
            )
        cfg = await self.get_or_create_guild_settings(int(interaction.guild_id))
        if not cfg.get("twitch_enabled", False):
            return await interaction.followup.send(
                "Twitch is not enabled yet. Run `/galactia setup twitch` first.",
                ephemeral=True,
            )

        login = twitch_login.strip().lower()
        removed = await self.stream_repo.remove_by_login(int(interaction.guild_id), login)
        await interaction.followup.send(
            f"Removed **{removed}** follow(s) for **{login}**." if removed else f"No follow found for **{login}**.",
            ephemeral=True,
        )

    @twitch_group.command(name="test_online", description="Simulate a live (sends LIVE announcement)")
    @app_commands.describe(twitch_login="A previously followed Twitch login")
    async def twitch_test_online(self, interaction: discord.Interaction, twitch_login: str):
        """Simulate a LIVE stream announcement for testing."""
        login = twitch_login.strip().lower()
        if interaction.guild_id is None:
            return await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
        cfg = await self.get_or_create_guild_settings(int(interaction.guild_id))
        if not cfg.get("twitch_enabled", False):
            return await interaction.response.send_message(
                "Twitch is not enabled yet. Run `/galactia setup twitch` first.",
                ephemeral=True,
            )
        data = await self.stream_repo.list_by_guild(int(interaction.guild_id))
        item = next((s for s in data if s["login"] == login), None)
        if not item:
            return await interaction.response.send_message("Channel not followed yet.", ephemeral=True)

        fake = {
            "user_login": login,
            "user_name": item.get("last_display_name") or login,
            "title": "Test stream",
            "game_name": "Testing",
            "viewer_count": 123,
            "thumbnail_url": f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{login}-{{width}}x{{height}}.jpg",
            "language": "fr",
            "started_at": datetime.utcnow().isoformat() + "Z",
            "game_id": item.get("last_game_id"),
        }

        item["live"] = True
        item["last_started_at"] = fake["started_at"]
        item["last_stream_title"] = fake["title"]
        item["last_display_name"] = fake["user_name"]
        item["peak_viewers"] = int(fake.get("viewer_count") or 0)

        if not item.get("profile_image_url") or not item.get("last_user_id"):
            try:
                user = await self._get_user_by_login(login)
                if user:
                    if user.get("profile_image_url"):
                        item["profile_image_url"] = user["profile_image_url"]
                    if user.get("id"):
                        item["last_user_id"] = user["id"]
            except Exception as e:
                logger.warning("Profile/user fetch (test) failed for %s: %s", login, e)

        await self._announce_live(fake, item, interaction.guild)
        await self.stream_repo.upsert(item)
        await interaction.response.send_message("Test LIVE sent.", ephemeral=True)
        return

    @twitch_group.command(name="test_offline", description="Simulate end of live (edits to OFFLINE)")
    @app_commands.describe(twitch_login="A previously followed Twitch login")
    async def twitch_test_offline(self, interaction: discord.Interaction, twitch_login: str):
        """Simulate editing a previous LIVE message into an OFFLINE summary."""
        login = twitch_login.strip().lower()
        if interaction.guild_id is None:
            return await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
        cfg = await self.get_or_create_guild_settings(int(interaction.guild_id))
        if not cfg.get("twitch_enabled", False):
            return await interaction.response.send_message(
                "Twitch is not enabled yet. Run `/galactia setup twitch` first.",
                ephemeral=True,
            )
        data = await self.stream_repo.list_by_guild(int(interaction.guild_id))
        item = next((s for s in data if s["login"] == login), None)
        if not item:
            return await interaction.response.send_message("Channel not followed yet.", ephemeral=True)

        if not item.get("last_started_at"):
            item["last_started_at"] = (datetime.utcnow()).isoformat() + "Z"

        item["live"] = False
        await self._edit_to_stream_ended(item, interaction.guild)
        await self.stream_repo.upsert(item)
        await interaction.response.send_message("Test OFFLINE edited/sent.", ephemeral=True)
        return

    @twitch_group.command(name="config", description="Show current Twitch notifier settings")
    async def twitch_show_config(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            return await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
        cfg = await self.get_or_create_guild_settings(int(interaction.guild_id))
        fallback_channel_id = cfg.get("twitch_announce_channel_id")
        channel = self.bot.get_channel(fallback_channel_id) if fallback_channel_id else None
        channel_mention = channel.mention if channel else "None"
        msg = (
            f"Active: {cfg.get('twitch_enabled', False)}\n"
            f"Intervalle: {cfg['twitch_check_interval']}s "
            f"(poller effectif: {self.check_interval}s)\n"
            f"Salon par defaut: {channel_mention}"
        )
        await interaction.response.send_message(msg, ephemeral=True)
        return

    @twitch_group.command(name="set_interval", description="Update Twitch poll interval in seconds")
    @app_commands.describe(seconds="Polling interval in seconds (minimum 10)")
    async def twitch_set_interval(self, interaction: discord.Interaction, seconds: int):
        if seconds < 10:
            return await interaction.response.send_message(
                "Interval must be at least 10 seconds.", ephemeral=True
            )
        if interaction.guild_id is None:
            return await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
        guild_id = int(interaction.guild_id)
        cfg = await self.get_or_create_guild_settings(guild_id)
        if not cfg.get("twitch_enabled", False):
            return await interaction.response.send_message(
                "Twitch is not enabled yet. Run `/galactia setup twitch` first.",
                ephemeral=True,
            )
        await self.guild_settings_repo.update_twitch_interval(guild_id, seconds)
        await self.refresh_poll_interval()
        await interaction.response.send_message(
            f"Interval updated to {seconds}s.", ephemeral=True
        )
        return

    @twitch_group.command(name="set_channel", description="Set default announce channel")
    @app_commands.describe(channel="Channel where live notifications will be sent")
    async def twitch_set_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if interaction.guild_id is None:
            return await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
        guild_id = int(interaction.guild_id)
        cfg = await self.get_or_create_guild_settings(guild_id)
        if not cfg.get("twitch_enabled", False):
            return await interaction.response.send_message(
                "Twitch is not enabled yet. Run `/galactia setup twitch` first.",
                ephemeral=True,
            )
        await self.guild_settings_repo.update_twitch_channel(guild_id, channel.id)
        await interaction.response.send_message(
            f"Default channel set to {channel.mention}.", ephemeral=True
        )
        return

    # =========
    # Poll loop
    # =========
    @tasks.loop(seconds=60)
    async def poller(self):
        """
        Periodically poll Twitch Helix for current live streams of followed logins
        and broadcast changes (OFF->ON, ON->ON updates, ON->OFF).
        """
        if not self.bot.is_ready():
            return
        if not self.twitch_client_id or not self.twitch_client_secret:
            return

        streams = await self.stream_repo.list_all()
        if not streams:
            return
        configs = {int(cfg["guild_id"]): cfg for cfg in await self.guild_settings_repo.list_all()}
        streams = [
            item
            for item in streams
            if configs.get(int(item["guild_id"]), {}).get("twitch_enabled", False)
        ]
        if not streams:
            return
        logins = list({s["login"].lower() for s in streams})
        try:
            live_streams = await self._get_streams_by_logins(logins)
        except Exception as e:
            logger.error("Twitch poll failed: %s", e)
            return

        live_map = {s["user_login"].lower(): s for s in live_streams}
        changed_items: list[Dict] = []

        for item in streams:
            login = item["login"].lower()
            live_obj = live_map.get(login)
            guild = self.bot.get_guild(int(item["guild_id"]))

            if live_obj and not item.get("live"):
                try:
                    try:
                        user = await self._get_user_by_login(login)
                        if user:
                            if user.get("profile_image_url"):
                                item["profile_image_url"] = user["profile_image_url"]
                            if user.get("id"):
                                item["last_user_id"] = user["id"]
                    except Exception as e:
                        logger.warning("Profile/user fetch failed for %s: %s", login, e)

                    item["live"] = True
                    item["last_display_name"] = live_obj.get("user_name") or login
                    item["last_stream_title"] = live_obj.get("title") or "-"
                    item["last_started_at"] = live_obj.get("started_at")
                    item["peak_viewers"] = int(live_obj.get("viewer_count") or 0)
                    item["last_game_id"] = live_obj.get("game_id") or None
                    item["last_game_name"] = live_obj.get("game_name") or "-"

                    try:
                        box = await self._get_box_art_url_by_game_id(item["last_game_id"])
                        if box:
                            item["last_box_art_url"] = box
                    except Exception as e:
                        logger.warning("Box art fetch failed for %s: %s", login, e)

                    await self._announce_live(live_obj, item, guild)
                    changed_items.append(item)
                except Exception as e:
                    logger.error("Announce error for %s: %s", login, e)

            elif live_obj and item.get("live"):
                changed = False
                current = int(live_obj.get("viewer_count") or 0)
                prev_peak = int(item.get("peak_viewers") or 0)
                if current > prev_peak:
                    item["peak_viewers"] = current
                    changed = True

                current_game_id = live_obj.get("game_id") or None
                if current_game_id != item.get("last_game_id"):
                    item["last_game_id"] = current_game_id
                    item["last_game_name"] = live_obj.get("game_name") or "-"
                    changed = True
                    try:
                        box = await self._get_box_art_url_by_game_id(current_game_id)
                        if box:
                            item["last_box_art_url"] = box
                    except Exception as e:
                        logger.warning("Box art refresh failed for %s: %s", login, e)

                if changed:
                    changed_items.append(item)

            elif not live_obj and item.get("live"):
                item["live"] = False
                try:
                    await self._edit_to_stream_ended(item, guild)
                except Exception as e:
                    logger.error("Edit to 'ended' failed for %s: %s", login, e)
                changed_items.append(item)

        if changed_items:
            await self.stream_repo.upsert_many(changed_items)
        return

    # =========
    # Twitch Helix helpers
    # =========
    async def _get_oauth_token(self) -> str:
        """
        Retrieve and cache an App Access Token for Twitch Helix.
        Reuses token until ~60 seconds before expiry.
        """
        import time
        if self._oauth_token and time.time() < self._oauth_expire_ts - 60:
            return self._oauth_token
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": self.twitch_client_id,
            "client_secret": self.twitch_client_secret,
            "grant_type": "client_credentials",
        }
        async with self.session.post(url, params=params, timeout=20) as r:
            data = await r.json()
            self._oauth_token = data["access_token"]
            self._oauth_expire_ts = time.time() + data.get("expires_in", 3600)
            return self._oauth_token

    async def _get_streams_by_logins(self, logins: List[str]) -> List[Dict]:
        """
        Fetch current live streams by Twitch logins (batched by 100).
        Returns raw Helix stream objects.
        """
        if not logins:
            return []
        results: List[Dict] = []
        token = await self._get_oauth_token()
        headers = {
            "Client-Id": self.twitch_client_id,
            "Authorization": f"Bearer {token}",
        }
        for i in range(0, len(logins), 100):
            chunk = logins[i:i + 100]
            params = [("user_login", l) for l in chunk]
            url = "https://api.twitch.tv/helix/streams"
            async with self.session.get(url, params=params, headers=headers, timeout=20) as r:
                data = await r.json()
                if data and data.get("data"):
                    results.extend(data["data"])
        return results

    async def _get_user_by_login(self, login: str) -> Optional[Dict]:
        """
        Resolve a Twitch login to basic user info dict: { id, display_name, profile_image_url }.
        Returns None if not found.
        """
        if not login:
            return None
        token = await self._get_oauth_token()
        headers = {
            "Client-Id": self.twitch_client_id,
            "Authorization": f"Bearer {token}",
        }
        url = "https://api.twitch.tv/helix/users"
        params = {"login": login}
        async with self.session.get(url, params=params, headers=headers, timeout=15) as r:
            data = await r.json()
            users = data.get("data") or []
            if not users:
                return None
            u = users[0]
            return {
                "id": u.get("id"),
                "display_name": u.get("display_name") or u.get("login"),
                "profile_image_url": u.get("profile_image_url"),
            }

    async def _get_profile_image_by_login(self, login: str) -> Optional[str]:
        """
        Back-compat helper: returns only the profile image URL.
        Prefer _get_user_by_login in new code.
        """
        user = await self._get_user_by_login(login)
        return user.get("profile_image_url") if user else None

    async def _get_box_art_url_by_game_id(self, game_id: Optional[str]) -> Optional[str]:
        """
        Fetch a game's box art URL (with {width}x{height} placeholders).
        Returns None if unknown.
        """
        if not game_id:
            return None
        token = await self._get_oauth_token()
        headers = {
            "Client-Id": self.twitch_client_id,
            "Authorization": f"Bearer {token}",
        }
        url = "https://api.twitch.tv/helix/games"
        params = {"id": game_id}
        async with self.session.get(url, params=params, headers=headers, timeout=15) as r:
            data = await r.json()
            items = data.get("data") or []
            if not items:
                return None
            return items[0].get("box_art_url")

    async def _get_latest_vod_url(self, user_id: Optional[str], started_at: Optional[str]) -> Optional[str]:
        """
        Heuristically pick the most relevant VOD for the ended stream:
        - Fetch a few recent 'archive' videos.
        - If we know the stream's started_at, pick the first VOD whose created_at >= (started_at - 6h).
        - Otherwise fall back to the latest archive.
        """
        if not user_id:
            return None
        token = await self._get_oauth_token()
        headers = {
            "Client-Id": self.twitch_client_id,
            "Authorization": f"Bearer {token}",
        }
        url = "https://api.twitch.tv/helix/videos"
        params = {"user_id": user_id, "type": "archive", "first": 5, "sort": "time"}
        async with self.session.get(url, params=params, headers=headers, timeout=15) as r:
            data = await r.json()
            vids = data.get("data") or []
            if not vids:
                return None

            if started_at:
                try:
                    start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))

                    best = None
                    for v in vids:
                        ca = v.get("created_at")
                        if not ca:
                            continue
                        v_dt = datetime.fromisoformat(ca.replace("Z", "+00:00"))
                        # allow some scheduling drift / reruns
                        if v_dt >= start_dt - timedelta(hours=6):
                            best = v
                            break
                    if best:
                        return best.get("url")
                except Exception:
                    pass

            # Fallback: most recent archive
            return vids[0].get("url")

    # =========
    # Announce / Edit
    # =========
    async def _announce_live(self, stream: Dict, item: Dict, guild: Optional[discord.Guild]):
        """
        Post a LIVE announcement embed with:
        - Author (profile image clickable)
        - Game name
        - Relative start time
        - Stream thumbnail
        - Footer CTA
        - Link button to join the live
        """
        channel_id = item.get("channel_id")
        if not channel_id and item.get("guild_id"):
            cfg = await self.get_or_create_guild_settings(int(item["guild_id"]))
            channel_id = cfg.get("twitch_announce_channel_id")
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        login = stream.get("user_login", item.get("login", ""))
        url = f"https://twitch.tv/{login}"
        display_name = (
            stream.get("user_name")
            or item.get("last_display_name")
            or login
        )

        # Best-effort: fetch avatar if missing in cache
        if not item.get("profile_image_url"):
            try:
                user = await self._get_user_by_login(login)
                if user and user.get("profile_image_url"):
                    item["profile_image_url"] = user["profile_image_url"]
            except Exception:
                pass

        content = f"🟣 **{display_name}** est en direct sur Twitch !"
        # Mention appended at the end
        rid = item.get("role_id")
        if rid:
            content = f"{content} <@&{rid}>"

        embed = discord.Embed(
            title=stream.get("title") or "En direct sur Twitch !",
            url=url,
            color=0x9146FF
        )

        # Author row with profile picture (clickable)
        embed.set_author(
            name=display_name,
            url=url,
            icon_url=item.get("profile_image_url") or ""
        )

        # Field: current game name
        game_name = stream.get("game_name") or item.get("last_game_name") or "—"
        embed.add_field(name="👾 Jeu", value=game_name, inline=True)

        # Field: relative start time using Discord's dynamic timestamp
        start_rel = "—"
        started_at = stream.get("started_at")
        if started_at:
            try:
                dt_start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                start_rel = f"<t:{int(dt_start.timestamp())}:R>"
            except Exception:
                pass
        embed.add_field(name="🕒 Début", value=start_rel, inline=True)

        # Live preview image
        thumb = stream.get("thumbnail_url")
        if thumb:
            thumb = thumb.replace("{width}", "1280").replace("{height}", "720")
            thumb = f"{thumb}?t={int(time.time())}"
            embed.set_image(url=thumb)

        # Footer with platform + published date (Europe/Paris)
        published = stream.get("started_at")
        if published:
            try:
                dt_utc = datetime.fromisoformat(published.replace("Z", "+00:00"))
                dt_paris = dt_utc.astimezone(ZoneInfo("Europe/Paris"))
                embed.set_footer(text=f"Twitch • {dt_paris.strftime('%d/%m/%Y %H:%M')}")
            except Exception:
                embed.set_footer(text="Twitch")
        else:
            embed.set_footer(text="Twitch")

        # LIVE link button
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="✨ Venez soutenir !",
            url=url,
            style=discord.ButtonStyle.link
        ))

        msg = await channel.send(content=content, embed=embed, view=view)
        item["last_message_id"] = msg.id

    async def _edit_to_stream_ended(self, item: dict, guild: Optional[discord.Guild]):
        """
        Edit the LIVE message (or post a new one) into an OFFLINE summary:
        - Author with avatar
        - Game + total duration
        - Game box art as thumbnail (if available)
        - Footer with start/end absolute times
        - Optional VOD button resolved from Helix videos
        """
        channel_id = item.get("channel_id")
        msg_id = item.get("last_message_id")
        started_at = item.get("last_started_at")
        login = item.get("login", "")
        display_name = item.get("last_display_name") or login
        url = f"https://twitch.tv/{login}"
        title_stream = item.get("last_stream_title") or "Stream terminé."
        duration = _fmt_duration(started_at) if started_at else "—"
        peak = item.get("peak_viewers") or 0  # kept for potential future use
        box_art = item.get("last_box_art_url")

        channel = self.bot.get_channel(channel_id) if channel_id else None
        if not channel:
            logger.error("Channel %s not found for stream end of %s", channel_id, login)
            return

        ended_embed = discord.Embed(
            title=f"**{title_stream}**",
            url=url,
            color=0x2B2D31
        )

        ended_embed.set_author(
            name=display_name,
            url=url,
            icon_url=item.get("profile_image_url") or ""
        )

        ended_embed.add_field(name="👾 Jeu", value=item.get("last_game_name") or "—", inline=True)
        ended_embed.add_field(name="⏱️ Durée", value=duration, inline=True)

        if box_art:
            ended_embed.set_thumbnail(url=box_art.replace("{width}", "285").replace("{height}", "380"))

        # Footer with absolute local start/end times
        start_fmt = _fmt_datetime(started_at) if started_at else "?"
        end_fmt = datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y %H:%M")
        ended_embed.set_footer(text=f"Twitch • Début : {start_fmt} • Fin : {end_fmt}")

        content = f"⏹️ **{display_name}** a terminé son live."

        # Optional VOD button
        view: Optional[discord.ui.View] = None
        try:
            user_id = item.get("last_user_id")
            if not user_id:
                user = await self._get_user_by_login(login)
                user_id = user.get("id") if user else None
                if user_id:
                    item["last_user_id"] = user_id  # lightweight memo

            vod_url = await self._get_latest_vod_url(user_id, started_at)
            if vod_url:
                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="⏮️ Rediffusion",
                    url=vod_url,
                    style=discord.ButtonStyle.link
                ))
        except Exception as e:
            logger.warning("VOD lookup failed for %s: %s", login, e)

        avatar = item.get("profile_image_url")
        if avatar:
            ended_embed.set_author(name=display_name, url=url, icon_url=avatar)
        else:
            ended_embed.set_author(name=display_name, url=url)

        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(content=content, embed=ended_embed, view=view)
                return
            except Exception as e:
                logger.warning("Could not edit old message for %s: %s", login, e)

        await channel.send(content=content, embed=ended_embed, view=view)


# ---------- Cog setup & command registration ----------

async def setup(bot: commands.Bot):
    """
    Add the TwitchNotifier cog.
    Slash command registration is centralized in galactia.bot.
    """
    exit_stack = AsyncExitStack()
    session = await exit_stack.enter_async_context(aiohttp.ClientSession())
    cog = TwitchNotifier(bot, session, exit_stack)
    await bot.add_cog(cog)
    await cog.initialize()
