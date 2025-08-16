import logging
import discord
from discord.ext import commands

from galactia.config import DISCORD_TOKEN, GUILD_ID, intents

bot = commands.Bot(command_prefix="!", intents=intents)


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
        await bot.load_extension("galactia.cogs.ai")
        logging.info("Loaded extension: galactia.cogs.ai")
    except Exception as e:
        logging.exception("Failed loading galactia.cogs.ai: %s", e)

    try:
        if guild_id:
            await bot.tree.sync(guild=discord.Object(id=int(guild_id)))
            logging.info("Slash commands synced (guild=%s).", guild_id)
        else:
            await bot.tree.sync()
            logging.info("Slash commands synced (global).")
    except Exception as e:
        logging.exception("Failed to sync commands: %s", e)


bot.setup_hook = _setup_hook


def run():
    bot.run(DISCORD_TOKEN)

