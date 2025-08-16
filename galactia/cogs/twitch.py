import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional

import aiohttp
import discord
from discord import app_commands, Permissions
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Local JSON DB path for Twitch follow entries and cached metadata
TWITCH_STREAMS_DB_PATH = os.path.join("data", "twitch.json")


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


def _fmt_relative(iso_ts: Optional[str]) -> str:
    """
    Render a human-friendly relative time in French (e.g., 'à l’instant', 'il y a 3 heures').
    Always computed relative to Europe/Paris timezone. Returns '—' on error/None.
    """
    if not iso_ts:
        return "—"
    try:
        start_utc = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        now_paris = datetime.now(ZoneInfo("Europe/Paris"))
        start_paris = start_utc.astimezone(ZoneInfo("Europe/Paris"))
        delta = now_paris - start_paris

        sec = int(delta.total_seconds())
        if sec < 0:
            sec = 0  # safety in case of clock skew

        if sec < 10:
            return "à l’instant"
        if sec < 60:
            return f"il y a {sec} s"

        minutes = sec // 60
        if minutes == 1:
            return "il y a 1 minute"
        if minutes < 60:
            return f"il y a {minutes} minutes"

        hours = minutes // 60
        if hours == 1:
            return "il y a 1 heure"
        if hours < 24:
            return f"il y a {hours} heures"

        days = hours // 24
        if days == 1:
            return "hier"
        if days < 7:
            return f"il y a {days} jours"

        weeks = days // 7
        if weeks == 1:
            return "la semaine dernière"
        if days < 31:
            return f"il y a {weeks} semaines"

        months = days // 31
        if months == 1:
            return "le mois dernier"
        return f"il y a {months} mois"
    except Exception:
        return "—"


# ---------- Local JSON DB helpers ----------

def ensure_db():
    """Ensure the data directory and JSON file exist."""
    os.makedirs(os.path.dirname(TWITCH_STREAMS_DB_PATH), exist_ok=True)
    if not os.path.exists(TWITCH_STREAMS_DB_PATH):
        with open(TWITCH_STREAMS_DB_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)


def load_streams() -> List[Dict]:
    """Load the entire Twitch follow list (and cached state) from disk."""
    ensure_db()
    with open(TWITCH_STREAMS_DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_streams(data: List[Dict]):
    """Persist the Twitch follow list to disk."""
    with open(TWITCH_STREAMS_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------- Cog ----------

class TwitchNotifier(commands.Cog):
    """
    Twitch live notifier (polling Twitch Helix).
    - Stores follows/state in data/twitch.json
    - Slash commands (scoped under /twitch): add | remove | list | test_online | test_offline
    - Announces when a followed channel goes live, edits when it ends
    """

    def __init__(self, bot: commands.Bot, session: aiohttp.ClientSession, exit_stack: AsyncExitStack):
        self.bot = bot
        self.twitch_client_id = os.getenv("TWITCH_CLIENT_ID")
        self.twitch_client_secret = os.getenv("TWITCH_CLIENT_SECRET")
        self.check_interval = int(os.getenv("TWITCH_CHECK_INTERVAL", "60"))
        self.fallback_channel_id = int(os.getenv("TWITCH_ANNOUNCE_CHANNEL_ID", "0"))
        self._oauth_token: Optional[str] = None
        self._oauth_expire_ts: float = 0
        self.session = session
        self._exit_stack = exit_stack

        if not self.twitch_client_id or not self.twitch_client_secret:
            logger.warning("Twitch credentials are missing; Twitch notifier will not start.")
        else:
            self.poller.change_interval(seconds=self.check_interval)
            self.poller.start()

    def cog_unload(self):
        """Stop the background poller and close HTTP session when the cog is unloaded."""
        self.bot.loop.create_task(self._shutdown())

    async def _shutdown(self):
        if self.poller.is_running():
            self.poller.cancel()
        await self._exit_stack.aclose()

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
            return await interaction.response.send_message("Please pick a text channel.", ephemeral=True)

        data = load_streams()
        login = twitch_login.strip().lower()
        if any(s for s in data if s["login"] == login and s["channel_id"] == channel.id):
            return await interaction.response.send_message(
                f"**{login}** is already followed in {channel.mention}.", ephemeral=True
            )

        data.append({
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
            "last_stream_title": None
        })
        save_streams(data)
        await interaction.response.send_message(
            f"✅ I will follow **{login}** and announce lives in {channel.mention} "
            + (f"(ping {role.mention})" if role else ""),
            ephemeral=False
        )

    @twitch_group.command(name="list", description="List followed Twitch channels")
    async def list_streams(self, interaction: discord.Interaction):
        data = load_streams()
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
        data = load_streams()
        login = twitch_login.strip().lower()
        before = len(data)
        data = [s for s in data if s["login"] != login]
        save_streams(data)
        removed = before - len(data)
        await interaction.response.send_message(
            f"🗑️ Removed **{removed}** follow(s) for **{login}**." if removed else f"No follow found for **{login}**.",
            ephemeral=False
        )

    @twitch_group.command(name="test_online", description="Simulate a live (sends LIVE announcement)")
    @app_commands.describe(twitch_login="A previously followed Twitch login")
    async def twitch_test_online(self, interaction: discord.Interaction, twitch_login: str):
        """Simulate a LIVE stream announcement for testing."""
        data = load_streams()
        login = twitch_login.strip().lower()
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

        # Initialize on-state
        item["live"] = True
        item["last_started_at"] = fake["started_at"]
        item["last_stream_title"] = fake["title"]
        item["last_display_name"] = fake["user_name"]
        item["peak_viewers"] = int(fake.get("viewer_count") or 0)

        await self._announce_live(fake, item, interaction.guild)
        save_streams(data)
        await interaction.response.send_message("✅ Test LIVE sent.", ephemeral=True)

    @twitch_group.command(name="test_offline", description="Simulate end of live (edits to OFFLINE)")
    @app_commands.describe(twitch_login="A previously followed Twitch login")
    async def twitch_test_offline(self, interaction: discord.Interaction, twitch_login: str):
        """Simulate editing a previous LIVE message into an OFFLINE summary."""
        data = load_streams()
        login = twitch_login.strip().lower()
        item = next((s for s in data if s["login"] == login), None)
        if not item:
            return await interaction.response.send_message("Channel not followed yet.", ephemeral=True)

        if not item.get("last_started_at"):
            item["last_started_at"] = (datetime.utcnow()).isoformat() + "Z"

        item["live"] = False
        save_streams(data)

        await self._edit_to_stream_ended(item, interaction.guild)
        await interaction.response.send_message("✅ Test OFFLINE edited/sent.", ephemeral=True)

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

        data = load_streams()
        if not data:
            return

        logins = list({s["login"].lower() for s in data})
        try:
            live_streams = await self._get_streams_by_logins(logins)
        except Exception as e:
            logger.error("Twitch poll failed: %s", e)
            return

        live_map = {s["user_login"].lower(): s for s in live_streams}
        changed = False

        for item in data:
            login = item["login"].lower()
            live_obj = live_map.get(login)

            if live_obj and not item.get("live"):
                # OFF -> ON : send announcement and cache metadata
                try:
                    guild = self.bot.get_guild(self._first_guild_id())
                    await self._announce_live(live_obj, item, guild)
                    item["live"] = True
                    item["last_display_name"] = live_obj.get("user_name") or login
                    item["last_stream_title"] = live_obj.get("title") or "—"
                    item["last_started_at"] = live_obj.get("started_at")
                    item["peak_viewers"] = int(live_obj.get("viewer_count") or 0)
                    item["last_game_id"] = live_obj.get("game_id") or None
                    item["last_game_name"] = live_obj.get("game_name") or "—"

                    # Cache box art
                    try:
                        box = await self._get_box_art_url_by_game_id(item["last_game_id"])
                        if box:
                            item["last_box_art_url"] = box  # keep {width}x{height} placeholders
                    except Exception as e:
                        logger.warning("Box art fetch failed for %s: %s", login, e)

                    # Cache user id + avatar (for VOD button / author icon)
                    try:
                        user = await self._get_user_by_login(login)
                        if user:
                            if user.get("profile_image_url"):
                                item["profile_image_url"] = user["profile_image_url"]
                            if user.get("id"):
                                item["last_user_id"] = user["id"]
                    except Exception as e:
                        logger.warning("Profile/user fetch failed for %s: %s", login, e)

                    changed = True
                except Exception as e:
                    logger.error("Announce error for %s: %s", login, e)

            elif live_obj and item.get("live"):
                # ON -> ON : update cached peak and game changes
                current = int(live_obj.get("viewer_count") or 0)
                prev_peak = int(item.get("peak_viewers") or 0)
                if current > prev_peak:
                    item["peak_viewers"] = current
                    changed = True

                current_game_id = live_obj.get("game_id") or None
                if current_game_id != item.get("last_game_id"):
                    item["last_game_id"] = current_game_id
                    item["last_game_name"] = live_obj.get("game_name") or "—"
                    try:
                        box = await self._get_box_art_url_by_game_id(current_game_id)
                        if box:
                            item["last_box_art_url"] = box
                            changed = True
                    except Exception as e:
                        logger.warning("Box art refresh failed for %s: %s", login, e)

            elif not live_obj and item.get("live"):
                # ON -> OFF : edit message to "stream ended"
                item["live"] = False
                changed = True
                try:
                    guild = self.bot.get_guild(self._first_guild_id())
                    await self._edit_to_stream_ended(item, guild)
                except Exception as e:
                    logger.error("Edit to 'ended' failed for %s: %s", login, e)

        if changed:
            save_streams(data)

    def _first_guild_id(self) -> int:
        """Return the first guild id the bot is in; used to resolve a Guild for message edits."""
        return self.bot.guilds[0].id if self.bot.guilds else 0

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
        channel_id = item.get("channel_id") or self.fallback_channel_id
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

        content = f"🟣 **{display_name}** est en direct sur Twitch : {url}"
        if item.get("role_id"):
            content = f"<@&{item['role_id']}> " + content

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

        # Fields: game + relative start time
        embed.add_field(name="👾 Jeu", value=stream.get("game_name") or "—", inline=True)
        start_rel = _fmt_relative(stream.get("started_at")) if stream.get("started_at") else "—"
        embed.add_field(name="🕒 Début", value=f"{start_rel}", inline=True)

        # Live preview image
        thumb = stream.get("thumbnail_url")
        if thumb:
            thumb = thumb.replace("{width}", "1280").replace("{height}", "720")
            embed.set_image(url=thumb)

        # Footer CTA
        embed.set_footer(text="✨ Venez soutenir !")

        # LIVE link button
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="▶️ Rejoindre le live",
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
        game_name = item.get("last_game_name") or "—"
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

        ended_embed.add_field(name="👾 Jeu", value=game_name, inline=True)
        ended_embed.add_field(name="⏱️ Durée", value=duration, inline=True)

        if box_art:
            ended_embed.set_thumbnail(url=box_art.replace("{width}", "285").replace("{height}", "380"))

        # Footer with absolute local start/end times
        start_fmt = _fmt_datetime(started_at) if started_at else "?"
        end_fmt = datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y %H:%M")
        ended_embed.set_footer(text=f"Début : {start_fmt} • Fin : {end_fmt}")

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
                    # best-effort persist (avoid overwriting by reloading current file then saving)
                    current = load_streams()
                    for s in current:
                        if s.get("login") == login:
                            s["last_user_id"] = user_id
                            break
                    save_streams(current)

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
    Add the TwitchNotifier cog and (re)register the /twitch command group.
    If DISCORD_GUILD_ID is set, register commands for that guild only (faster updates).
    Otherwise register globally.
    """
    # 1) Add the cog with a managed HTTP session
    exit_stack = AsyncExitStack()
    session = await exit_stack.enter_async_context(aiohttp.ClientSession())
    cog = TwitchNotifier(bot, session, exit_stack)
    await bot.add_cog(cog)

    # 2) (Re)register the /twitch group explicitly
    try:
        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            # Remove any previous definition under the same name for that guild
            try:
                bot.tree.remove_command(cog.twitch_group.name, type=cog.twitch_group.type, guild=guild)
            except Exception:
                pass
            bot.tree.add_command(cog.twitch_group, guild=guild)
            logger.info("Registered /%s group for guild %s", cog.twitch_group.name, guild_id)
        else:
            # Global registration
            try:
                bot.tree.remove_command(cog.twitch_group.name, type=cog.twitch_group.type)
            except Exception:
                pass
            bot.tree.add_command(cog.twitch_group)
            logger.info("Registered /%s group (global)", cog.twitch_group.name)
    except Exception as e:
        logger.exception("Failed to register /twitch group: %s", e)