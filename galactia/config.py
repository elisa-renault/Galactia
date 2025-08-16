import os
import logging
from datetime import datetime

import discord
import openai
from dotenv import load_dotenv


# Load environment variables
env_file = os.getenv("ENV_FILE", ".env")
load_dotenv(dotenv_path=env_file)

# Configure logging
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

today = datetime.now().strftime("%Y-%m-%d")
log_file_path = os.path.join(log_dir, f"Galactia_{today}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file_path, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

# Discord intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# External service tokens
openai.api_key = os.getenv("OPENAI_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")

__all__ = ["intents", "DISCORD_TOKEN", "GUILD_ID"]

if __name__ == "__main__":
    logging.info("📦 Loading env from %s", env_file)
    logging.info(
        "🚀 Starting Galactia in %s mode...",
        os.getenv("ENV_MODE", "undefined"),
    )
