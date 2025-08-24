import logging
import discord
from discord.ext import commands, tasks

from galactia.config import DISCORD_TOKEN, GUILD_ID, intents
from core.feature_flags import is_feature_enabled, refresh_feature_flags

bot = commands.Bot(command_prefix="!", intents=intents)


@tasks.loop(minutes=5)
async def refresh_feature_flags_task() -> None:
    """Periodically refresh feature flags from the database."""
    refresh_feature_flags()


@bot.event
async def on_ready():
    logging.info(
        f"✅ Galactia ready! Logged in as {bot.user} (ID: {bot.user.id})"
    )


async def _setup_hook():
    guild_id = GUILD_ID

    # Prime the feature-flag cache and schedule periodic refreshes
    refresh_feature_flags()
    refresh_feature_flags_task.start()

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
        if is_feature_enabled(None, "twitch") or (
            guild_id and is_feature_enabled(guild_id, "twitch")
        ):
            await bot.load_extension("galactia.cogs.twitch")
            logging.info("Loaded extension: galactia.cogs.twitch")
        else:
            logging.info("Skipped extension: galactia.cogs.twitch (feature disabled)")
    except Exception as e:
        logging.exception("Failed loading galactia.cogs.twitch: %s", e)

    try:
        if is_feature_enabled(None, "youtube") or (
            guild_id and is_feature_enabled(guild_id, "youtube")
        ):
            await bot.load_extension("galactia.cogs.youtube")
            logging.info("Loaded extension: galactia.cogs.youtube")
        else:
            logging.info("Skipped extension: galactia.cogs.youtube (feature disabled)")
    except Exception as e:
        logging.exception("Failed loading galactia.cogs.youtube: %s", e)

    try:
        if is_feature_enabled(None, "ai") or (
            guild_id and is_feature_enabled(guild_id, "ai")
        ):
            await bot.load_extension("galactia.cogs.ai")
            logging.info("Loaded extension: galactia.cogs.ai")
        else:
            logging.info("Skipped extension: galactia.cogs.ai (feature disabled)")
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

