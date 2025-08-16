import discord
import openai

from galactia.settings import settings

# Discord intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# External service tokens
openai.api_key = settings.openai_api_key
DISCORD_TOKEN = settings.discord_token
GUILD_ID = settings.discord_guild_id

__all__ = ["intents", "DISCORD_TOKEN", "GUILD_ID"]
