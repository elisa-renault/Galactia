import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from galactia.cogs.ai import AI_ALLOWED_MENTIONS, load_summary_settings
from galactia.permissions import can_manage_galactia
from galactia.repositories import AIRequestRepository, GuildSettingsRepository
from galactia.settings import settings


SUMMARY_LANGUAGES = ("fr", "en")
SUMMARY_ACCESS_MODES = ("admins_only", "allowed_roles", "everyone")
REQUIRED_SETUP_PERMISSIONS = {
    "view_channel": "voir le salon",
    "read_message_history": "lire l'historique",
    "send_messages": "envoyer des messages",
    "embed_links": "integrer des liens",
}
SETUP_INTRO = (
    "Galactia est ajoutee a ce serveur. "
    "Un administrateur peut lancer `/galactia setup start` pour configurer le bot."
)


def _choice_value(value, default=None):
    if value is None:
        return default
    return getattr(value, "value", value)


def _format_enabled(value: bool) -> str:
    return "active" if value else "desactive"


def _remaining(limit, used) -> str:
    if limit is None:
        return "?"
    return str(max(int(limit or 0) - int(used or 0), 0))


def _configured_channel_ids(cfg: dict) -> list[int]:
    ids = []
    ids.extend(int(item) for item in cfg.get("summary_allowed_channel_ids") or [])
    for key in ["setup_channel_id", "twitch_announce_channel_id", "youtube_announce_channel_id"]:
        if cfg.get(key):
            ids.append(int(cfg[key]))
    return sorted(set(ids))


def _mention_channel(guild: discord.Guild | None, channel_id: int) -> str:
    channel = guild.get_channel(int(channel_id)) if guild else None
    return channel.mention if isinstance(channel, discord.TextChannel) else f"`{channel_id}`"


def _mention_role(guild: discord.Guild | None, role_id: int) -> str:
    role = guild.get_role(int(role_id)) if guild else None
    return role.mention if role else f"`{role_id}`"


def _format_channel_scope(cfg: dict, guild: discord.Guild | None = None) -> str:
    ids = [int(channel_id) for channel_id in cfg.get("summary_allowed_channel_ids") or []]
    if not ids:
        return "Tous les salons accessibles"
    mentions = ", ".join(_mention_channel(guild, channel_id) for channel_id in ids[:8])
    if len(ids) > 8:
        mentions += f", +{len(ids) - 8}"
    return f"{len(ids)} salon(s): {mentions}"


def _format_role_scope(cfg: dict, guild: discord.Guild | None, key: str) -> str:
    ids = [int(role_id) for role_id in cfg.get(key) or []]
    if not ids:
        return "aucun"
    mentions = ", ".join(_mention_role(guild, role_id) for role_id in ids[:8])
    if len(ids) > 8:
        mentions += f", +{len(ids) - 8}"
    return mentions


def _setup_panel_content(
    cfg: dict,
    guild: discord.Guild | None,
    *,
    note: str | None = None,
) -> str:
    setup_state = "termine" if cfg.get("setup_completed_at") else "a terminer"
    permission_gaps = collect_setup_permission_gaps(guild, cfg)
    content = (
        "**Setup Galactia**\n"
        f"- Setup: `{setup_state}`\n"
        f"- Resume IA: `{_format_enabled(bool(cfg.get('summary_enabled')))}` / `{cfg.get('summary_access_mode')}`\n"
        f"- Salons resumables: {_format_channel_scope(cfg, guild)}\n"
        f"- Twitch: `{_format_enabled(bool(cfg.get('twitch_enabled')))}`\n"
        f"- YouTube: `{_format_enabled(bool(cfg.get('youtube_enabled')))}`\n"
        f"- Managers Galactia: {_format_role_scope(cfg, guild, 'galactia_manager_role_ids')}\n"
        f"- Permissions: `{'; '.join(permission_gaps) if permission_gaps else 'ok'}`"
    )
    if note:
        content += f"\n\n{note}"
    return content


def missing_permissions_for_channel(channel: discord.TextChannel, member) -> list[str]:
    perms = channel.permissions_for(member)
    missing = []
    for attr, label in REQUIRED_SETUP_PERMISSIONS.items():
        if not getattr(perms, attr, False):
            missing.append(label)
    return missing


def collect_setup_permission_gaps(guild: discord.Guild | None, cfg: dict) -> list[str]:
    if guild is None or getattr(guild, "me", None) is None:
        return ["serveur indisponible"]
    gaps = []
    for channel_id in _configured_channel_ids(cfg):
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            gaps.append(f"salon introuvable `{channel_id}`")
            continue
        missing = missing_permissions_for_channel(channel, guild.me)
        if missing:
            gaps.append(f"{channel.mention}: {', '.join(missing)}")
    return gaps


class SummaryChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Restreindre les resumes IA a des salons",
            min_values=1,
            max_values=10,
            channel_types=[discord.ChannelType.text],
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.set_summary_channels(interaction, self.values)


class ManagerRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="Definir les roles Galactia Manager",
            min_values=1,
            max_values=10,
            row=4,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.set_manager_roles(interaction, self.values)


class GalactiaSetupView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = int(guild_id)
        self.add_item(SummaryChannelSelect())
        self.add_item(ManagerRoleSelect())

    async def _guard(self, interaction: discord.Interaction) -> bool:
        return await can_manage_galactia(interaction)

    async def _settings(self) -> dict:
        return await load_summary_settings(self.guild_id)

    async def _refresh(self, interaction: discord.Interaction, *, note: str | None = None) -> None:
        cfg = await self._settings()
        content = _setup_panel_content(cfg, interaction.guild, note=note)
        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, view=self)
        else:
            await interaction.response.edit_message(content=content, view=self)

    async def set_summary_channels(self, interaction: discord.Interaction, channels) -> None:
        if not await self._guard(interaction):
            return
        ids = sorted({int(channel.id) for channel in channels if getattr(channel, "id", None) is not None})
        repo = GuildSettingsRepository()
        await repo.mutate_summary_id_list(self.guild_id, "summary_allowed_channel_ids", "clear")
        for channel_id in ids:
            await repo.mutate_summary_id_list(
                self.guild_id,
                "summary_allowed_channel_ids",
                "add",
                channel_id,
            )
        await self._refresh(interaction, note="Salons resumables mis a jour.")

    async def set_manager_roles(self, interaction: discord.Interaction, roles) -> None:
        if not await self._guard(interaction):
            return
        ids = sorted({int(role.id) for role in roles if getattr(role, "id", None) is not None})
        repo = GuildSettingsRepository()
        await repo.mutate_summary_id_list(self.guild_id, "galactia_manager_role_ids", "clear")
        for role_id in ids:
            await repo.mutate_summary_id_list(
                self.guild_id,
                "galactia_manager_role_ids",
                "add",
                role_id,
            )
        await self._refresh(interaction, note="Roles Galactia Manager mis a jour.")

    @discord.ui.button(label="Resume IA", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_summary(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        cfg = await self._settings()
        next_enabled = not bool(cfg.get("summary_enabled"))
        await GuildSettingsRepository().update_summary_field(self.guild_id, "summary_enabled", next_enabled)
        await self._refresh(interaction, note=f"Resume IA {_format_enabled(next_enabled)}.")

    @discord.ui.button(label="Twitch", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_twitch(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        cfg = await self._settings()
        next_enabled = not bool(cfg.get("twitch_enabled"))
        channel_id = cfg.get("twitch_announce_channel_id")
        if next_enabled and channel_id is None and isinstance(interaction.channel, discord.TextChannel):
            channel_id = interaction.channel.id
        cfg = await GuildSettingsRepository().update_twitch_setup(
            self.guild_id,
            enabled=next_enabled,
            channel_id=channel_id if next_enabled else None,
            seconds=int(cfg.get("twitch_check_interval") or settings.twitch_check_interval),
        )
        twitch_cog = self.bot.get_cog("TwitchNotifier")
        if twitch_cog and hasattr(twitch_cog, "refresh_poll_interval"):
            await twitch_cog.refresh_poll_interval()
        await self._refresh(interaction, note=f"Twitch {_format_enabled(cfg['twitch_enabled'])}.")

    @discord.ui.button(label="YouTube", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_youtube(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        cfg = await self._settings()
        next_enabled = not bool(cfg.get("youtube_enabled"))
        channel_id = cfg.get("youtube_announce_channel_id")
        if next_enabled and channel_id is None and isinstance(interaction.channel, discord.TextChannel):
            channel_id = interaction.channel.id
        cfg = await GuildSettingsRepository().update_youtube_setup(
            self.guild_id,
            enabled=next_enabled,
            channel_id=channel_id if next_enabled else None,
            seconds=int(cfg.get("youtube_check_interval") or settings.youtube_check_interval),
        )
        youtube_cog = self.bot.get_cog("YouTubeNotifier")
        if youtube_cog and hasattr(youtube_cog, "refresh_poll_interval"):
            await youtube_cog.refresh_poll_interval()
        await self._refresh(interaction, note=f"YouTube {_format_enabled(cfg['youtube_enabled'])}.")

    @discord.ui.button(label="Tous les salons", style=discord.ButtonStyle.primary, row=1)
    async def all_channels(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await GuildSettingsRepository().mutate_summary_id_list(
            self.guild_id,
            "summary_allowed_channel_ids",
            "clear",
        )
        await self._refresh(interaction, note="Les resumes IA sont autorises dans tous les salons accessibles.")

    @discord.ui.button(label="Verifier permissions", style=discord.ButtonStyle.secondary, row=1)
    async def check_permissions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        cfg = await self._settings()
        gaps = collect_setup_permission_gaps(interaction.guild, cfg)
        note = "Permissions ok." if not gaps else "Permissions manquantes:\n- " + "\n- ".join(gaps)
        await self._refresh(interaction, note=note)

    @discord.ui.button(label="Terminer setup", style=discord.ButtonStyle.success, row=1)
    async def finish_setup(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        cfg = await self._settings()
        gaps = collect_setup_permission_gaps(interaction.guild, cfg)
        if gaps:
            await self._refresh(interaction, note="Setup incomplet:\n- " + "\n- ".join(gaps))
            return
        await GuildSettingsRepository().mark_setup_finished(
            self.guild_id,
            user_id=getattr(interaction.user, "id", None),
            channel_id=getattr(getattr(interaction, "channel", None), "id", None),
        )
        await self._refresh(interaction, note="Setup Galactia termine.")


class GalactiaAdminCog(commands.GroupCog, name="galactia"):
    """Guild-level administration commands for Galactia."""

    config = app_commands.Group(name="config", description="Configuration Galactia")
    setup = app_commands.Group(name="setup", description="Assistant de configuration Galactia")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool = False,
        view: discord.ui.View | None = None,
    ):
        if interaction.response.is_done():
            await interaction.followup.send(
                content,
                allowed_mentions=AI_ALLOWED_MENTIONS,
                ephemeral=ephemeral,
                view=view,
            )
        else:
            await interaction.response.send_message(
                content,
                allowed_mentions=AI_ALLOWED_MENTIONS,
                ephemeral=ephemeral,
                view=view,
            )

    async def _ensure_settings(self, guild_id: int | None) -> dict:
        if guild_id is None:
            raise RuntimeError("Guild-only command used without guild_id")
        return await load_summary_settings(guild_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await can_manage_galactia(interaction)

    @app_commands.command(name="status", description="Afficher le statut IA et la configuration resume.")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        cfg = await self._ensure_settings(guild_id)
        usage = None
        try:
            usage = await AIRequestRepository().summary_usage_today(
                guild_id,
                user_id=getattr(interaction.user, "id", None),
                channel_id=getattr(getattr(interaction, "channel", None), "id", None),
            )
        except Exception as exc:
            logging.info("Admin status usage unavailable: %s.", type(exc).__name__)

        guild_usage = (usage or {}).get("guild", {"requests": 0, "tokens": 0})
        user_usage = (usage or {}).get("user", {"requests": 0, "tokens": 0})
        channel_usage = (usage or {}).get("channel", {"requests": 0, "tokens": 0})
        permission_gaps = collect_setup_permission_gaps(interaction.guild, cfg)
        setup_state = "termine" if cfg.get("setup_completed_at") else "a terminer"
        content = (
            "**Galactia status**\n"
            f"- Setup: `{setup_state}`\n"
            f"- Resume IA: `{_format_enabled(bool(cfg.get('summary_enabled')))} "
            f"/ {cfg.get('summary_access_mode')}`\n"
            f"- Twitch: `{_format_enabled(bool(cfg.get('twitch_enabled')))}`\n"
            f"- YouTube: `{_format_enabled(bool(cfg.get('youtube_enabled')))}`\n"
            f"- Timezone: `{cfg.get('timezone')}` / langue: `{cfg.get('language')}`\n"
            f"- Max messages: `{cfg.get('summary_max_messages')}` / max scan: `{cfg.get('summary_max_scan_messages')}`\n"
            f"- Salons resumables: {_format_channel_scope(cfg, interaction.guild)}\n"
            f"- Roles autorises resume: {_format_role_scope(cfg, interaction.guild, 'summary_allowed_role_ids')}\n"
            f"- Managers Galactia: {_format_role_scope(cfg, interaction.guild, 'galactia_manager_role_ids')}\n"
            f"- Quotas restants: guilde `{_remaining(cfg.get('summary_quota_guild_daily'), guild_usage['requests'])}`, "
            f"user `{_remaining(cfg.get('summary_quota_user_daily'), user_usage['requests'])}`, "
            f"salon `{_remaining(cfg.get('summary_quota_channel_daily'), channel_usage['requests'])}`, "
            f"tokens `{_remaining(cfg.get('summary_quota_tokens_daily'), guild_usage['tokens'])}`\n"
            f"- Permissions: `{'; '.join(permission_gaps) if permission_gaps else 'ok'}`"
        )
        await self._send(interaction, content)

    @setup.command(name="start", description="Initialiser la configuration de ce serveur.")
    @app_commands.guild_only()
    async def setup_start(self, interaction: discord.Interaction):
        cfg = await self._ensure_settings(interaction.guild_id)
        channel_id = getattr(getattr(interaction, "channel", None), "id", None)
        cfg = await GuildSettingsRepository().mark_setup_started(interaction.guild_id, channel_id)
        view = GalactiaSetupView(self.bot, int(interaction.guild_id))
        await self._send(
            interaction,
            _setup_panel_content(cfg, interaction.guild),
            ephemeral=True,
            view=view,
        )

    @setup.command(name="summary", description="Configurer le module de resume IA.")
    @app_commands.guild_only()
    @app_commands.choices(
        access_mode=[
            app_commands.Choice(name="admins_only", value="admins_only"),
            app_commands.Choice(name="allowed_roles", value="allowed_roles"),
            app_commands.Choice(name="everyone", value="everyone"),
        ],
        language=[
            app_commands.Choice(name="fr", value="fr"),
            app_commands.Choice(name="en", value="en"),
        ],
    )
    async def setup_summary(
        self,
        interaction: discord.Interaction,
        enabled: bool = True,
        channel: discord.TextChannel | None = None,
        access_mode: app_commands.Choice[str] | None = None,
        role: discord.Role | None = None,
        timezone: str = "Europe/Paris",
        language: app_commands.Choice[str] | None = None,
        max_messages: int = 500,
    ):
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            await self._send(interaction, "Timezone invalide. Exemple: `Europe/Paris`.", ephemeral=True)
            return
        mode = _choice_value(access_mode, "admins_only")
        if mode == "allowed_roles" and role is None:
            await self._send(interaction, "Indique un role quand `access_mode=allowed_roles`.", ephemeral=True)
            return
        if max_messages < 1 or max_messages > 2000:
            await self._send(interaction, "Le maximum doit etre compris entre 1 et 2000.", ephemeral=True)
            return
        target_channel = channel or interaction.channel
        if enabled and not isinstance(target_channel, discord.TextChannel):
            await self._send(interaction, "Indique un salon texte pour activer le resume IA.", ephemeral=True)
            return

        await self._ensure_settings(interaction.guild_id)
        cfg = await GuildSettingsRepository().update_summary_setup(
            interaction.guild_id,
            enabled=enabled,
            timezone=timezone,
            language=_choice_value(language, "fr"),
            channel_id=getattr(target_channel, "id", None) if enabled else None,
            access_mode=mode,
            role_id=getattr(role, "id", None),
            max_messages=max_messages,
        )
        await self._send(
            interaction,
            "Resume IA configure: "
            f"`{_format_enabled(cfg['summary_enabled'])}`, mode `{cfg['summary_access_mode']}`, "
            f"salons {_format_channel_scope(cfg, interaction.guild)}.",
            ephemeral=True,
        )

    @setup.command(name="twitch", description="Configurer le module Twitch.")
    @app_commands.guild_only()
    async def setup_twitch(
        self,
        interaction: discord.Interaction,
        enabled: bool = True,
        channel: discord.TextChannel | None = None,
        interval_seconds: int = 60,
    ):
        target_channel = channel or interaction.channel
        if enabled and not isinstance(target_channel, discord.TextChannel):
            await self._send(interaction, "Indique un salon texte pour activer Twitch.", ephemeral=True)
            return
        if interval_seconds < 10:
            await self._send(interaction, "L'intervalle Twitch minimum est 10 secondes.", ephemeral=True)
            return
        await self._ensure_settings(interaction.guild_id)
        cfg = await GuildSettingsRepository().update_twitch_setup(
            interaction.guild_id,
            enabled=enabled,
            channel_id=getattr(target_channel, "id", None) if enabled else None,
            seconds=interval_seconds,
        )
        twitch_cog = self.bot.get_cog("TwitchNotifier")
        if twitch_cog and hasattr(twitch_cog, "refresh_poll_interval"):
            await twitch_cog.refresh_poll_interval()
        await self._send(
            interaction,
            f"Twitch configure: `{_format_enabled(cfg['twitch_enabled'])}`, intervalle `{cfg['twitch_check_interval']}s`.",
            ephemeral=True,
        )

    @setup.command(name="youtube", description="Configurer le module YouTube.")
    @app_commands.guild_only()
    async def setup_youtube(
        self,
        interaction: discord.Interaction,
        enabled: bool = True,
        channel: discord.TextChannel | None = None,
        interval_seconds: int = 300,
    ):
        target_channel = channel or interaction.channel
        if enabled and not isinstance(target_channel, discord.TextChannel):
            await self._send(interaction, "Indique un salon texte pour activer YouTube.", ephemeral=True)
            return
        if interval_seconds < 60:
            await self._send(interaction, "L'intervalle YouTube minimum est 60 secondes.", ephemeral=True)
            return
        await self._ensure_settings(interaction.guild_id)
        cfg = await GuildSettingsRepository().update_youtube_setup(
            interaction.guild_id,
            enabled=enabled,
            channel_id=getattr(target_channel, "id", None) if enabled else None,
            seconds=interval_seconds,
        )
        youtube_cog = self.bot.get_cog("YouTubeNotifier")
        if youtube_cog and getattr(youtube_cog, "poller", None):
            youtube_cog.poll_interval = cfg["youtube_check_interval"]
            if youtube_cog.poller.is_running():
                youtube_cog.poller.change_interval(seconds=cfg["youtube_check_interval"])
        await self._send(
            interaction,
            f"YouTube configure: `{_format_enabled(cfg['youtube_enabled'])}`, intervalle `{cfg['youtube_check_interval']}s`.",
            ephemeral=True,
        )

    @setup.command(name="finish", description="Valider les permissions et terminer le setup.")
    @app_commands.guild_only()
    async def setup_finish(self, interaction: discord.Interaction):
        cfg = await self._ensure_settings(interaction.guild_id)
        gaps = collect_setup_permission_gaps(interaction.guild, cfg)
        if gaps:
            await self._send(
                interaction,
                "Setup incomplet: permissions manquantes:\n- " + "\n- ".join(gaps),
                ephemeral=True,
            )
            return
        cfg = await GuildSettingsRepository().mark_setup_finished(
            interaction.guild_id,
            user_id=getattr(interaction.user, "id", None),
            channel_id=getattr(getattr(interaction, "channel", None), "id", None),
        )
        await self._send(interaction, "Setup Galactia termine. Utilise `/galactia status` pour verifier.", ephemeral=True)

    @config.command(name="timezone", description="Definir la timezone des resumes.")
    @app_commands.guild_only()
    async def config_timezone(self, interaction: discord.Interaction, timezone: str):
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            await self._send(interaction, "Timezone invalide. Exemple: `Europe/Paris`.")
            return
        await self._ensure_settings(interaction.guild_id)
        await GuildSettingsRepository().update_timezone(interaction.guild_id, timezone)
        await self._send(interaction, f"Timezone resume mise a jour: `{timezone}`.")

    @config.command(name="language", description="Definir la langue des resumes.")
    @app_commands.guild_only()
    @app_commands.choices(
        language=[
            app_commands.Choice(name="fr", value="fr"),
            app_commands.Choice(name="en", value="en"),
        ]
    )
    async def config_language(
        self,
        interaction: discord.Interaction,
        language: app_commands.Choice[str],
    ):
        await self._ensure_settings(interaction.guild_id)
        await GuildSettingsRepository().update_language(interaction.guild_id, language.value)
        await self._send(interaction, f"Langue resume mise a jour: `{language.value}`.")

    @config.command(name="max_messages", description="Definir le maximum de messages resumables.")
    @app_commands.guild_only()
    async def config_max_messages(self, interaction: discord.Interaction, max_messages: int):
        if max_messages < 1 or max_messages > 2000:
            await self._send(interaction, "Le maximum doit etre compris entre 1 et 2000.")
            return
        await self._ensure_settings(interaction.guild_id)
        await GuildSettingsRepository().update_summary_max_messages(interaction.guild_id, max_messages)
        await self._send(interaction, f"Maximum resume mis a jour: `{max_messages}` messages.")

    @config.command(name="allowed_channel", description="Gerer les salons resumables par /summary.")
    @app_commands.guild_only()
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="all", value="all"),
            app_commands.Choice(name="list", value="list"),
        ]
    )
    async def config_allowed_channel(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ):
        await self._ensure_settings(interaction.guild_id)
        if action.value in {"add", "remove"} and channel is None:
            await self._send(interaction, "Indique un salon pour `add` ou `remove`.")
            return
        list_action = "clear" if action.value == "all" else action.value
        cfg = await GuildSettingsRepository().mutate_summary_id_list(
            interaction.guild_id,
            "summary_allowed_channel_ids",
            list_action,
            getattr(channel, "id", None),
        )
        await self._send(interaction, f"Salons resumables: {_format_channel_scope(cfg, interaction.guild)}.")

    @config.command(name="allowed_role", description="Gerer les roles autorises pour /summary.")
    @app_commands.guild_only()
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="clear", value="clear"),
            app_commands.Choice(name="list", value="list"),
        ]
    )
    async def config_allowed_role(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        role: discord.Role | None = None,
    ):
        await self._ensure_settings(interaction.guild_id)
        if action.value in {"add", "remove"} and role is None:
            await self._send(interaction, "Indique un role pour `add` ou `remove`.")
            return
        cfg = await GuildSettingsRepository().mutate_summary_id_list(
            interaction.guild_id,
            "summary_allowed_role_ids",
            action.value,
            getattr(role, "id", None),
        )
        if action.value in {"add", "remove", "clear"}:
            next_mode = "allowed_roles" if cfg.get("summary_allowed_role_ids") else "everyone"
            cfg = await GuildSettingsRepository().update_summary_field(
                interaction.guild_id,
                "summary_access_mode",
                next_mode,
            )
        ids = cfg.get("summary_allowed_role_ids") or []
        await self._send(interaction, f"Roles autorises: `{ids or 'tous'}`.")

    @config.command(name="manager_role", description="Gerer les roles Galactia Manager.")
    @app_commands.guild_only()
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="clear", value="clear"),
            app_commands.Choice(name="list", value="list"),
        ]
    )
    async def config_manager_role(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        role: discord.Role | None = None,
    ):
        await self._ensure_settings(interaction.guild_id)
        if action.value in {"add", "remove"} and role is None:
            await self._send(interaction, "Indique un role pour `add` ou `remove`.")
            return
        cfg = await GuildSettingsRepository().mutate_summary_id_list(
            interaction.guild_id,
            "galactia_manager_role_ids",
            action.value,
            getattr(role, "id", None),
        )
        await self._send(
            interaction,
            f"Managers Galactia: {_format_role_scope(cfg, interaction.guild, 'galactia_manager_role_ids')}.",
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        try:
            await GuildSettingsRepository().get_or_create(
                guild.id,
                twitch_check_interval=settings.twitch_check_interval,
                twitch_announce_channel_id=None,
                youtube_check_interval=settings.youtube_check_interval,
                youtube_announce_channel_id=None,
            )
        except Exception as exc:
            logging.info("Guild join settings initialization failed: %s.", type(exc).__name__)

        sent = False
        owner = getattr(guild, "owner", None)
        if owner is None and getattr(guild, "owner_id", None):
            try:
                owner = await self.bot.fetch_user(guild.owner_id)
            except Exception:
                owner = None
        if owner is not None:
            try:
                await owner.send(SETUP_INTRO, allowed_mentions=AI_ALLOWED_MENTIONS)
                sent = True
            except Exception:
                sent = False
        if not sent:
            for channel in getattr(guild, "text_channels", []) or []:
                if not isinstance(channel, discord.TextChannel):
                    continue
                try:
                    perms = channel.permissions_for(guild.me)
                    if getattr(perms, "view_channel", False) and getattr(perms, "send_messages", False):
                        await channel.send(SETUP_INTRO, allowed_mentions=AI_ALLOWED_MENTIONS)
                        sent = True
                        break
                except Exception:
                    continue
        logging.info("Joined guild %s (%s), setup notice sent=%s.", guild.id, guild.name, sent)


async def setup(bot: commands.Bot):
    await bot.add_cog(GalactiaAdminCog(bot))
