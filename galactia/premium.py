from __future__ import annotations

import discord
from discord import app_commands

from galactia.settings import settings

DEFAULT_PREMIUM_GUILDS = {1372478988882022502, 881871369149759502}


def is_premium_guild(guild_id: int | None) -> bool:
    if guild_id is None:
        return False
    try:
        gid = int(guild_id)
    except (TypeError, ValueError):
        return False
    return gid in DEFAULT_PREMIUM_GUILDS or gid in settings.premium_guild_ids


def premium_guild_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_premium_guild(interaction.guild_id):
            return True
        raise app_commands.CheckFailure("This feature requires the premium plan.")

    return app_commands.check(predicate)
