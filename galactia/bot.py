import logging
import discord
from discord.ext import commands

from galactia.config import DISCORD_TOKEN, GUILD_ID, intents

bot = commands.Bot(command_prefix="!", intents=intents)


def _command_names(command_list) -> list[str]:
    return sorted(
        getattr(command, "qualified_name", None) or getattr(command, "name", str(command))
        for command in command_list
    )


async def sync_slash_commands(bot_instance: commands.Bot, guild_id: int | None):
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        global_commands = _command_names(bot_instance.tree.get_commands())
        bot_instance.tree.copy_global_to(guild=guild)
        guild_commands = _command_names(bot_instance.tree.get_commands(guild=guild))
        logging.info(
            "Preparing slash command sync (guild=%s global=%s guild=%s).",
            guild_id,
            global_commands,
            guild_commands,
        )
        synced = await bot_instance.tree.sync(guild=guild)
        logging.info(
            "Slash commands synced (guild=%s count=%d commands=%s).",
            guild_id,
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
    guild_id = GUILD_ID

    try:
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
            logging.info("Purged existing slash commands (guild=%s).", guild_id)
        else:
            bot.tree.clear_commands()
            await bot.tree.sync()
            logging.info("Purged existing global slash commands.")
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
        await sync_slash_commands(bot, guild_id)
    except Exception as e:
        logging.exception("Failed to sync commands: %s", e)


bot.setup_hook = _setup_hook


def run():
    bot.run(DISCORD_TOKEN)

