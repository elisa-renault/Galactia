from __future__ import annotations

import logging

import discord

from galactia.repositories import GuildSettingsRepository
from galactia.settings import settings


MANAGE_GALACTIA_DENIED_MESSAGE = (
    "Cette commande est reservee aux administrateurs Discord ou aux roles Galactia Manager."
)


def _author_is_discord_admin(author) -> bool:
    permissions = getattr(author, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False))


def _author_role_ids(author) -> set[int]:
    return {
        int(getattr(role, "id"))
        for role in getattr(author, "roles", []) or []
        if getattr(role, "id", None) is not None
    }


def user_can_manage_galactia(author, cfg: dict | None) -> bool:
    if _author_is_discord_admin(author):
        return True
    manager_role_ids = {
        int(role_id)
        for role_id in (cfg or {}).get("galactia_manager_role_ids", []) or []
        if role_id is not None
    }
    return bool(manager_role_ids & _author_role_ids(author))


async def load_guild_settings_for_permissions(guild_id: int) -> dict:
    return await GuildSettingsRepository().get_or_create(
        guild_id,
        twitch_check_interval=settings.twitch_check_interval,
        twitch_announce_channel_id=settings.twitch_announce_channel_id,
        youtube_check_interval=settings.youtube_check_interval,
        youtube_announce_channel_id=settings.youtube_announce_channel_id,
    )


async def send_manage_galactia_denial(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(MANAGE_GALACTIA_DENIED_MESSAGE, ephemeral=True)
    else:
        await interaction.response.send_message(MANAGE_GALACTIA_DENIED_MESSAGE, ephemeral=True)


async def can_manage_galactia(interaction: discord.Interaction) -> bool:
    if interaction.guild_id is None:
        await send_manage_galactia_denial(interaction)
        return False

    if _author_is_discord_admin(interaction.user):
        return True

    try:
        cfg = await load_guild_settings_for_permissions(int(interaction.guild_id))
    except Exception as exc:
        logging.info("Galactia manager permission settings unavailable: %s.", type(exc).__name__)
        await send_manage_galactia_denial(interaction)
        return False

    if user_can_manage_galactia(interaction.user, cfg):
        return True

    await send_manage_galactia_denial(interaction)
    return False
