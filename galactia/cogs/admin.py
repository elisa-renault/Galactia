import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from galactia.cogs.ai import AI_ALLOWED_MENTIONS, load_summary_settings
from galactia.repositories import AIRequestRepository, GuildSettingsRepository


SUMMARY_LANGUAGES = ("fr", "en")


class GalactiaAdminCog(commands.GroupCog, name="galactia"):
    """Guild-level administration commands for Galactia."""

    config = app_commands.Group(name="config", description="Configuration Galactia")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send(self, interaction: discord.Interaction, content: str):
        if interaction.response.is_done():
            await interaction.followup.send(content, allowed_mentions=AI_ALLOWED_MENTIONS)
        else:
            await interaction.response.send_message(content, allowed_mentions=AI_ALLOWED_MENTIONS)

    @app_commands.command(name="status", description="Afficher le statut IA et la configuration résumé.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        cfg = await load_summary_settings(guild_id)
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
        content = (
            "**Galactia status**\n"
            f"- IA: configurée\n"
            f"- Timezone: `{cfg.get('timezone')}`\n"
            f"- Langue: `{cfg.get('language')}`\n"
            f"- Max messages: `{cfg.get('summary_max_messages')}`\n"
            f"- Max scan: `{cfg.get('summary_max_scan_messages')}`\n"
            f"- Salons résumables: `{len(cfg.get('summary_allowed_channel_ids') or [])}`\n"
            f"- Rôles autorisés: `{len(cfg.get('summary_allowed_role_ids') or [])}`\n"
            f"- Usage guilde: `{guild_usage['requests']}/{cfg.get('summary_quota_guild_daily')}` "
            f"résumés, `{guild_usage['tokens']}/{cfg.get('summary_quota_tokens_daily')}` tokens\n"
            f"- Usage user: `{user_usage['requests']}/{cfg.get('summary_quota_user_daily')}` résumés\n"
            f"- Usage salon: `{channel_usage['requests']}/{cfg.get('summary_quota_channel_daily')}` résumés"
        )
        await self._send(interaction, content)

    @config.command(name="timezone", description="Définir la timezone des résumés.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def config_timezone(self, interaction: discord.Interaction, timezone: str):
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            await self._send(interaction, "Timezone invalide. Exemple: `Europe/Paris`.")
            return
        await load_summary_settings(interaction.guild_id)
        await GuildSettingsRepository().update_timezone(interaction.guild_id, timezone)
        await self._send(interaction, f"Timezone résumé mise à jour: `{timezone}`.")

    @config.command(name="language", description="Définir la langue des résumés.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
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
        await load_summary_settings(interaction.guild_id)
        await GuildSettingsRepository().update_language(interaction.guild_id, language.value)
        await self._send(interaction, f"Langue résumé mise à jour: `{language.value}`.")

    @config.command(name="max_messages", description="Définir le maximum de messages résumables.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def config_max_messages(self, interaction: discord.Interaction, max_messages: int):
        if max_messages < 1 or max_messages > 2000:
            await self._send(interaction, "Le maximum doit être compris entre 1 et 2000.")
            return
        await load_summary_settings(interaction.guild_id)
        await GuildSettingsRepository().update_summary_max_messages(interaction.guild_id, max_messages)
        await self._send(interaction, f"Maximum résumé mis à jour: `{max_messages}` messages.")

    @config.command(name="allowed_channel", description="Gérer les salons résumables par /summary.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="clear", value="clear"),
            app_commands.Choice(name="list", value="list"),
        ]
    )
    async def config_allowed_channel(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ):
        await load_summary_settings(interaction.guild_id)
        if action.value in {"add", "remove"} and channel is None:
            await self._send(interaction, "Indique un salon pour `add` ou `remove`.")
            return
        cfg = await GuildSettingsRepository().mutate_summary_id_list(
            interaction.guild_id,
            "summary_allowed_channel_ids",
            action.value,
            getattr(channel, "id", None),
        )
        ids = cfg.get("summary_allowed_channel_ids") or []
        await self._send(interaction, f"Salons résumables: `{ids or 'tous'}`.")

    @config.command(name="allowed_role", description="Gérer les rôles autorisés pour /summary.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
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
        await load_summary_settings(interaction.guild_id)
        if action.value in {"add", "remove"} and role is None:
            await self._send(interaction, "Indique un rôle pour `add` ou `remove`.")
            return
        cfg = await GuildSettingsRepository().mutate_summary_id_list(
            interaction.guild_id,
            "summary_allowed_role_ids",
            action.value,
            getattr(role, "id", None),
        )
        ids = cfg.get("summary_allowed_role_ids") or []
        await self._send(interaction, f"Rôles autorisés: `{ids or 'tous'}`.")


async def setup(bot: commands.Bot):
    await bot.add_cog(GalactiaAdminCog(bot))
