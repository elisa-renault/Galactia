# galactia/cogs/youtube.py
import logging
import os
import re
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from galactia.permissions import can_manage_galactia
from galactia.repositories import GuildSettingsRepository, YouTubeFollowRepository
from galactia.settings import settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# -----------------------
# Small time helpers
# -----------------------
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

# -----------------------
# YouTube Notifier Cog
# -----------------------
class YouTubeNotifier(commands.Cog):
    """
    YouTube new‑video notifier for Galactia.

    Data model per follow row:
      - channel_id: str (UCxxxx)
      - channel_title: str
      - channel_handle: str (e.g. @LimitMaximum)
      - uploads_playlist_id: str (UUxxxx)
      - announce_channel_id: int (Discord text channel)
      - role_id: Optional[int] (role to mention, appended at the end)
      - last_video_id: Optional[str]
      - last_video_published_at: Optional[str]  (ISO8601 Z)
      - last_message_id: Optional[int]  (last announcement message)
      - channel_thumb_url: Optional[str]
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.youtube_key = settings.youtube_api_key
        self.poll_interval = int(os.getenv("YOUTUBE_POLL_INTERVAL", settings.youtube_check_interval))
        self.follow_repo = YouTubeFollowRepository()
        self.guild_settings_repo = GuildSettingsRepository()

        if not self.youtube_key:
            logger.warning("YOUTUBE_API_KEY missing; youtube notifier disabled.")
        else:
            self.poller.change_interval(seconds=self.poll_interval)
            self.poller.start()

    async def get_or_create_guild_settings(self, guild_id: int) -> Dict:
        return await self.guild_settings_repo.get_or_create(
            guild_id,
            twitch_check_interval=settings.twitch_check_interval,
            twitch_announce_channel_id=settings.twitch_announce_channel_id,
            youtube_check_interval=settings.youtube_check_interval,
            youtube_announce_channel_id=settings.youtube_announce_channel_id,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await can_manage_galactia(interaction)

    async def _module_enabled(self, guild_id: int) -> bool:
        cfg = await self.get_or_create_guild_settings(guild_id)
        return bool(cfg.get("youtube_enabled", False))

    async def refresh_poll_interval(self):
        configs = await self.guild_settings_repo.list_all()
        intervals = [
            int(cfg["youtube_check_interval"])
            for cfg in configs
            if cfg.get("youtube_check_interval") and cfg.get("youtube_enabled", False)
        ]
        self.poll_interval = min(intervals) if intervals else int(settings.youtube_check_interval)
        if self.poller.is_running():
            self.poller.change_interval(seconds=self.poll_interval)

    def cog_unload(self):
        if self.poller.is_running():
            self.poller.cancel()

    # =============
    # Slash commands
    # =============
    youtube_group = app_commands.Group(
        name="youtube",
        description="Manage YouTube new-video notifications",
    )

    # -------- add --------
    @youtube_group.command(name="add", description="Follow a YouTube channel and announce its new videos.")
    @app_commands.describe(
        youtube_channel="YouTube channel URL or handle (e.g. https://youtube.com/@LimitMaximum or @LimitMaximum)",
        discord_channel="Discord channel for announcements",
        role="Optional role to mention at the end"
    )
    async def youtube_add(
        self,
        interaction: discord.Interaction,
        youtube_channel: str,
        discord_channel: discord.abc.GuildChannel,
        role: Optional[discord.Role] = None
    ):
        if not isinstance(discord_channel, discord.TextChannel):
            return await interaction.response.send_message("Please pick a text channel.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        handle_or_url = youtube_channel.strip()
        try:
            meta = await self._resolve_channel_meta(handle_or_url)
        except Exception as e:
            logger.exception("Channel resolve failed: %s", e)
            return await interaction.followup.send("Could not resolve channel. Check the handle/URL.", ephemeral=True)

        if not meta or not meta.get("channel_id"):
            return await interaction.followup.send("Channel not found.", ephemeral=True)

        cid = meta["channel_id"]
        uploads = meta.get("uploads_playlist_id")
        title = meta.get("title") or cid
        handle = meta.get("handle") or ""

        if interaction.guild_id is None:
            return await interaction.followup.send(
                "This command must be used in a server.",
                ephemeral=True,
            )
        guild_id = int(interaction.guild_id)
        if not await self._module_enabled(guild_id):
            return await interaction.followup.send(
                "YouTube is not enabled yet. Run `/galactia setup youtube` first.",
                ephemeral=True,
            )
        if await self.follow_repo.exists(guild_id, cid, discord_channel.id):
            return await interaction.followup.send(
                f"Already following **{title}** in {discord_channel.mention}.",
                ephemeral=True
            )

        row = {
            "guild_id": guild_id,
            "channel_id": cid,
            "channel_title": title,
            "channel_handle": handle,
            "uploads_playlist_id": uploads,
            "announce_channel_id": discord_channel.id,
            "role_id": role.id if role else None,
            "last_video_id": None,
            "last_video_published_at": None,
            "last_message_id": None,
            "channel_thumb_url": meta.get("thumb_url"),
        }
        await self.follow_repo.upsert(row)

        await interaction.followup.send(
            f"Now following **{title}** in {discord_channel.mention}"
            + (f" (mention {role.mention})" if role else ""),
            ephemeral=True
        )

    # -------- list --------
    @youtube_group.command(name="list", description="List followed YouTube channels.")
    async def youtube_list(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            return await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
        if not await self._module_enabled(int(interaction.guild_id)):
            return await interaction.response.send_message(
                "YouTube is not enabled yet. Run `/galactia setup youtube` first.",
                ephemeral=True,
            )
        data = await self.follow_repo.list_by_guild(int(interaction.guild_id))
        if not data:
            return await interaction.response.send_message("No YouTube follows yet.", ephemeral=True)

        lines = []
        for s in data:
            ch = interaction.guild.get_channel(s.get("announce_channel_id") or 0)
            rid = s.get("role_id")
            title = s.get("channel_title") or s.get("channel_id")
            dest = ch.mention if ch else f"#deleted({s.get('announce_channel_id')})"
            lines.append(
                f"• **{title}** → {dest}"
                + (f" (mention <@&{rid}>)" if rid else "")
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # -------- remove --------
    @youtube_group.command(name="remove", description="Stop following a YouTube channel (all destinations).")
    @app_commands.describe(youtube_channel="The channel URL or handle previously followed")
    async def youtube_remove(self, interaction: discord.Interaction, youtube_channel: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild_id is None:
            return await interaction.followup.send(
                "This command must be used in a server.",
                ephemeral=True,
            )
        if not await self._module_enabled(int(interaction.guild_id)):
            return await interaction.followup.send(
                "YouTube is not enabled yet. Run `/galactia setup youtube` first.",
                ephemeral=True,
            )

        meta = await self._resolve_channel_meta(youtube_channel.strip())
        if not meta or not meta.get("channel_id"):
            return await interaction.followup.send("Channel not found.", ephemeral=True)

        cid = meta["channel_id"]
        removed = await self.follow_repo.remove_by_channel_id(int(interaction.guild_id), cid)
        await interaction.followup.send(
            f"Removed **{removed}** follow(s) for **{meta.get('title') or cid}**." if removed else "No follow found.",
            ephemeral=True
        )

    # -------- test_new --------
    @youtube_group.command(name="test_new", description="Simulate a new video announcement for a followed channel.")
    @app_commands.describe(youtube_channel="A followed channel URL/handle")
    async def youtube_test_new(self, interaction: discord.Interaction, youtube_channel: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild_id is None:
            return await interaction.followup.send(
                "This command must be used in a server.",
                ephemeral=True,
            )
        if not await self._module_enabled(int(interaction.guild_id)):
            return await interaction.followup.send(
                "YouTube is not enabled yet. Run `/galactia setup youtube` first.",
                ephemeral=True,
            )
        data = await self.follow_repo.list_by_guild(int(interaction.guild_id))
        if not data:
            return await interaction.followup.send("No YouTube follows yet.", ephemeral=True)

        meta = await self._resolve_channel_meta(youtube_channel.strip())
        if not meta or not meta.get("channel_id"):
            return await interaction.followup.send("Channel not found.", ephemeral=True)

        cid = meta["channel_id"]
        item = next((s for s in data if s.get("channel_id") == cid), None)
        if not item:
            return await interaction.followup.send("Channel not followed yet.", ephemeral=True)

        fake_video = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Test video title",
            "description": "This is a test video description.",
            "published_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "thumb_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }

        await self._announce_new_video(fake_video, item)
        await self.follow_repo.upsert(item)
        await interaction.followup.send("New video test sent.", ephemeral=True)

    # -------- test_update --------
    @youtube_group.command(
        name="test_update",
        description="Simulate an embed update for the last announcement (title/description change)."
    )
    @app_commands.describe(youtube_channel="A followed channel URL/handle")
    async def youtube_test_update(self, interaction: discord.Interaction, youtube_channel: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if interaction.guild_id is None:
            return await interaction.followup.send(
                "This command must be used in a server.",
                ephemeral=True,
            )
        if not await self._module_enabled(int(interaction.guild_id)):
            return await interaction.followup.send(
                "YouTube is not enabled yet. Run `/galactia setup youtube` first.",
                ephemeral=True,
            )
        data = await self.follow_repo.list_by_guild(int(interaction.guild_id))
        if not data:
            return await interaction.followup.send("No YouTube follows yet.", ephemeral=True)

        meta = await self._resolve_channel_meta(youtube_channel.strip())
        if not meta or not meta.get("channel_id"):
            return await interaction.followup.send("Channel not found.", ephemeral=True)

        cid = meta["channel_id"]
        item = next((s for s in data if s.get("channel_id") == cid), None)
        if not item:
            return await interaction.followup.send("Channel not followed yet.", ephemeral=True)

        video_id = item.get("last_video_id") or "dQw4w9WgXcQ"
        url = f"https://www.youtube.com/watch?v={video_id}"
        fake_update = {
            "video_id": video_id,
            "title": "Updated title (test)",
            "description": "Updated description (test).",
            "published_at": item.get("last_video_published_at")
                            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "thumb_url": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            "url": url,
        }

        ok = await self._edit_last_announcement(fake_update, item)
        if ok:
            await interaction.followup.send("Test UPDATE sent.", ephemeral=True)
        else:
            await interaction.followup.send("No previous announcement to update.", ephemeral=True)

    # =========
    # Poll loop
    # =========
    @tasks.loop(seconds=300)
    async def poller(self):
        if not self.bot.is_ready():
            return
        if not self.youtube_key:
            return

        data = await self.follow_repo.list_all()
        if not data:
            return
        configs = {int(cfg["guild_id"]): cfg for cfg in await self.guild_settings_repo.list_all()}
        data = [
            row
            for row in data
            if configs.get(int(row["guild_id"]), {}).get("youtube_enabled", False)
        ]
        if not data:
            return

        changed_rows = {}
        for row in data:
            try:
                row_key = (
                    row.get("guild_id"),
                    row.get("channel_id"),
                    row.get("announce_channel_id"),
                )
                uploads = row.get("uploads_playlist_id")
                if not uploads:
                    meta = await self._resolve_channel_meta(row.get("channel_handle") or row.get("channel_id") or "")
                    if meta and meta.get("uploads_playlist_id"):
                        uploads = meta["uploads_playlist_id"]
                        row["uploads_playlist_id"] = uploads
                        changed_rows[row_key] = row
                    else:
                        continue

                latest = await self._fetch_latest_from_uploads(uploads, first=1)
                if not latest:
                    continue
                v = latest[0]
                last_id = row.get("last_video_id")
                if v["video_id"] != last_id:
                    await self._announce_new_video(v, row)
                    row["last_video_id"] = v["video_id"]
                    row["last_video_published_at"] = v.get("published_at")
                    changed_rows[row_key] = row
            except Exception as e:
                logger.warning("YouTube poll error for %s: %s", row.get("channel_id"), e)

        if changed_rows:
            await self.follow_repo.upsert_many(list(changed_rows.values()))
        return

    # =========
    # API helpers
    # =========
    async def _resolve_channel_meta(self, handle_or_url: str) -> Optional[Dict]:
        """
        Resolve a channel handle/URL to:
          - channel_id, title, handle, uploads_playlist_id, thumb_url
        """
        handle = None
        channel_id = None

        s = handle_or_url.strip()
        if s.startswith("@"):
            handle = s[1:]
        else:
            # Parse URL forms:
            try:
                u = urlparse(s)
                path = (u.path or "").strip("/")
                if "youtube.com" in (u.netloc or ""):
                    if path.startswith("@"):
                        handle = path[1:]
                    elif path.startswith("channel/"):
                        channel_id = path.split("/", 1)[1]
                    elif path.startswith("c/") or path.startswith("user/"):
                        # Custom URL forms -> need search fallback (handled by 'forHandle' using '@name')
                        if "/" in path:
                            handle = path.split("/", 1)[1]
                        else:
                            handle = path
            except Exception:
                pass

        # If we have a handle, try channels?forHandle=
        async with aiohttp.ClientSession() as session:
            headers = {}
            if handle:
                url = "https://www.googleapis.com/youtube/v3/channels"
                params = {"part": "id,snippet,contentDetails", "forHandle": f"@{handle}", "key": self.youtube_key}
                async with session.get(url, params=params, headers=headers, timeout=15) as r:
                    data = await r.json()
                    items = data.get("items") or []
                    if items:
                        it = items[0]
                        cid = it["id"]
                        title = it.get("snippet", {}).get("title") or cid
                        thumb = it.get("snippet", {}).get("thumbnails", {}).get("default", {}).get("url")
                        uploads = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                        return {
                            "channel_id": cid,
                            "title": title,
                            "handle": f"@{handle}",
                            "uploads_playlist_id": uploads,
                            "thumb_url": thumb,
                        }

            # If we have a channel_id, use channels?id=
            if channel_id:
                url = "https://www.googleapis.com/youtube/v3/channels"
                params = {"part": "snippet,contentDetails", "id": channel_id, "key": self.youtube_key}
                async with session.get(url, params=params, timeout=15) as r:
                    data = await r.json()
                    items = data.get("items") or []
                    if items:
                        it = items[0]
                        cid = it["id"]
                        title = it.get("snippet", {}).get("title") or cid
                        thumb = it.get("snippet", {}).get("thumbnails", {}).get("default", {}).get("url")
                        uploads = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                        # Try to fetch a handle display via "customUrl" if present
                        custom = it.get("snippet", {}).get("customUrl")  # often like @name
                        handle_val = custom if custom and custom.startswith("@") else ""
                        return {
                            "channel_id": cid,
                            "title": title,
                            "handle": handle_val,
                            "uploads_playlist_id": uploads,
                            "thumb_url": thumb,
                        }

        return None

    async def _fetch_latest_from_uploads(self, uploads_playlist_id: str, first: int = 1) -> List[Dict]:
        """
        Fetch newest videos from uploads playlist.
        Returns list of dicts with video_id, title, description, published_at, thumb_url, url
        """
        if not uploads_playlist_id:
            return []
        url = "https://www.googleapis.com/youtube/v3/playlistItems"
        params = {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": min(max(first, 1), 5),
            "key": self.youtube_key,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=15) as r:
                data = await r.json()
                items = data.get("items") or []
                out: List[Dict] = []
                for it in items:
                    sn = it.get("snippet", {}) or {}
                    cd = it.get("contentDetails", {}) or {}
                    vid = cd.get("videoId") or sn.get("resourceId", {}).get("videoId")
                    if not vid:
                        continue
                    title = sn.get("title") or "(no title)"
                    desc = sn.get("description") or ""
                    published_at = cd.get("videoPublishedAt") or sn.get("publishedAt")
                    # Try maxres, fallback to high
                    thumb = sn.get("thumbnails", {}).get("maxres", {}).get("url") or \
                            sn.get("thumbnails", {}).get("high", {}).get("url") or \
                            sn.get("thumbnails", {}).get("default", {}).get("url")
                    out.append({
                        "video_id": vid,
                        "title": title,
                        "description": desc,
                        "published_at": published_at,
                        "thumb_url": thumb,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                    })
                return out

    # =========
    # Announce / Edit
    # =========
    async def _announce_new_video(self, video: Dict, row: Dict):
        channel = self.bot.get_channel(row.get("announce_channel_id"))
        if not isinstance(channel, discord.TextChannel):
            logger.error("Announce channel %s not found.", row.get("announce_channel_id"))
            return

        title = row.get("channel_title") or row.get("channel_id")
        ch_url = f"https://www.youtube.com/channel/{row.get('channel_id')}"
        content = f"🔴 **{title}** a publié une nouvelle vidéo !"

        # Mention appended at the end
        rid = row.get("role_id")
        if rid:
            content = f"{content} <@&{rid}>"

        embed = discord.Embed(
            title=video.get("title") or "New video",
            url=video.get("url"),
            description=video.get("description") or "",
            color=0xFF0000
        )
        # Author (clickable) with channel avatar
        embed.set_author(
            name=f"{title}".strip(),
            url=ch_url,
            icon_url=row.get("channel_thumb_url") or ""
        )

        # Big thumbnail (preview)
        if video.get("thumb_url"):
            embed.set_image(url=video["thumb_url"])
        
        # Footer with platform + published date (Europe/Paris)
        published = video.get("published_at")
        if published:
            try:
                dt_utc = datetime.fromisoformat(published.replace("Z", "+00:00"))
                dt_paris = dt_utc.astimezone(ZoneInfo("Europe/Paris"))
                embed.set_footer(text=f"YouTube • {dt_paris.strftime('%d/%m/%Y %H:%M')}")
            except Exception:
                embed.set_footer(text="YouTube")
        else:
            embed.set_footer(text="YouTube")

        # Button to watch
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="▶️ Visionner sur YouTube",
            url=video.get("url"),
            style=discord.ButtonStyle.link
        ))

        msg = await channel.send(content=content, embed=embed, view=view)
        row["last_message_id"] = msg.id

    async def _edit_last_announcement(self, video: Dict, row: Dict) -> bool:
        channel = self.bot.get_channel(row.get("announce_channel_id"))
        if not isinstance(channel, discord.TextChannel):
            return False
        msg_id = row.get("last_message_id")
        if not msg_id:
            return False

        try:
            msg = await channel.fetch_message(msg_id)
        except Exception:
            return False

        title = row.get("channel_title") or row.get("channel_id")
        ch_url = f"https://www.youtube.com/channel/{row.get('channel_id')}"

        embed = discord.Embed(
            title=video.get("title") or "Nouvelle vidéo",
            url=video.get("url"),
            color=0xFF0000
        )
        embed.set_author(
            name=f"{title}".strip(),
            url=ch_url,
            icon_url=row.get("channel_thumb_url") or ""
        )
        rel = _fmt_relative(video.get("published_at"))
        embed.add_field(name="Published", value=rel, inline=True)
        if video.get("thumb_url"):
            embed.set_image(url=video["thumb_url"])


        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="▶️ Visionner sur YouTube",
            url=video.get("url"),
            style=discord.ButtonStyle.link
        ))

        try:
            await msg.edit(embed=embed, view=view)
            return True
        except Exception as e:
            logger.warning("Failed to edit last announcement: %s", e)
            return False

    # =========
    # Setup group
    # =========
async def setup(bot: commands.Bot):
    cog = YouTubeNotifier(bot)
    await bot.add_cog(cog)
    if cog.youtube_key:
        await cog.refresh_poll_interval()
