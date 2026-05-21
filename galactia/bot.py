import logging
import discord
from discord import app_commands
from discord.ext import commands

from galactia.config import DISCORD_TOKEN, GUILD_ID, intents
from galactia.settings import settings

bot = commands.Bot(command_prefix="!", intents=intents)


def _command_names(command_list) -> list[str]:
    return sorted(
        getattr(command, "qualified_name", None) or getattr(command, "name", str(command))
        for command in command_list
    )


def command_sync_target(
    guild_id: int | None = None,
    command_scope: str | None = None,
) -> int | None:
    scope = (command_scope or settings.discord_command_scope or "global").strip().lower()
    if scope == "guild" and guild_id:
        return int(guild_id)
    return None


def register_extension_command_groups(bot_instance: commands.Bot, guild_id: int | None = None) -> None:
    guild = discord.Object(id=int(guild_id)) if guild_id else None
    explicit_groups = ("twitch_group", "youtube_group")
    for cog in bot_instance.cogs.values():
        for attr in explicit_groups:
            group = getattr(cog, attr, None)
            if not isinstance(group, app_commands.Group):
                continue
            existing = bot_instance.tree.get_command(group.name, guild=guild)
            if existing is not None:
                logging.info(
                    "/%s group already registered (%s).",
                    group.name,
                    f"guild={guild_id}" if guild_id else "global",
                )
                continue
            bot_instance.tree.add_command(group, guild=guild)
            logging.info(
                "Registered /%s group (%s).",
                group.name,
                f"guild={guild_id}" if guild_id else "global",
            )


async def sync_slash_commands(
    bot_instance: commands.Bot,
    guild_id: int | None = None,
    command_scope: str | None = None,
):
    target_guild_id = command_sync_target(guild_id, command_scope)
    if target_guild_id:
        guild = discord.Object(id=int(target_guild_id))
        global_commands = _command_names(bot_instance.tree.get_commands())
        bot_instance.tree.copy_global_to(guild=guild)
        guild_commands = _command_names(bot_instance.tree.get_commands(guild=guild))
        logging.info(
            "Preparing slash command sync (guild=%s global=%s guild=%s).",
            target_guild_id,
            global_commands,
            guild_commands,
        )
        synced = await bot_instance.tree.sync(guild=guild)
        logging.info(
            "Slash commands synced (guild=%s count=%d commands=%s).",
            target_guild_id,
            len(synced),
            _command_names(synced),
        )
        return synced

    commands_to_sync = _command_names(bot_instance.tree.get_commands())
    logging.info("Preparing global slash command sync: commands=%s.", commands_to_sync)
    synced = await bot_instance.tree.sync()
    logging.info(
        "Slash commands synced (global count=%d commands=%s).",
        len(synced),
        _command_names(synced),
    )
    return synced


@bot.event
async def on_ready():
    logging.info(
        f"✅ Galactia ready! Logged in as {bot.user} (ID: {bot.user.id})"
    )


async def _setup_hook():
    guild_id = command_sync_target(GUILD_ID)

    try:
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
            logging.info("Purged existing slash commands (guild=%s).", guild_id)
        else:
            logging.info("Skipping global slash command purge.")
    except Exception as e:
        logging.exception("Failed to purge commands: %s", e)

    try:
        await bot.load_extension("galactia.cogs.twitch")
        logging.info("Loaded extension: galactia.cogs.twitch")
    except Exception as e:
        logging.exception("Failed loading galactia.cogs.twitch: %s", e)

    try:
        await bot.load_extension("galactia.cogs.youtube")
        logging.info("Loaded extension: galactia.cogs.youtube")
    except Exception as e:
        logging.exception("Failed loading galactia.cogs.youtube: %s", e)

    try:
        await bot.load_extension("galactia.cogs.ai")
        logging.info("Loaded extension: galactia.cogs.ai")
    except Exception as e:
        logging.exception("Failed loading galactia.cogs.ai: %s", e)

    try:
        await bot.load_extension("galactia.cogs.admin")
        logging.info("Loaded extension: galactia.cogs.admin")
    except Exception as e:
        logging.exception("Failed loading galactia.cogs.admin: %s", e)

    try:
        register_extension_command_groups(bot)
        await sync_slash_commands(bot, guild_id)
    except Exception as e:
        logging.exception("Failed to sync commands: %s", e)


bot.setup_hook = _setup_hook


def run():
    bot.run(DISCORD_TOKEN)

