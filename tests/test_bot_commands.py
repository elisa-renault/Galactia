from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands

from galactia.bot import sync_slash_commands
from galactia.cogs.admin import GalactiaAdminCog


class FakeTree:
    def __init__(self):
        self.global_commands = {
            "summary": SimpleNamespace(name="summary"),
            "galactia": SimpleNamespace(name="galactia"),
        }
        self.guild_commands = {
            "stream": SimpleNamespace(name="stream"),
        }
        self.copied_guild_id = None
        self.synced_guild_id = None

    def get_commands(self, *, guild=None):
        if guild is None:
            return list(self.global_commands.values())
        return list(self.guild_commands.values())

    def copy_global_to(self, *, guild):
        self.copied_guild_id = guild.id
        self.guild_commands.update(self.global_commands)

    async def sync(self, *, guild=None):
        self.synced_guild_id = getattr(guild, "id", None)
        return self.get_commands(guild=guild) if guild is not None else self.get_commands()


@pytest.mark.asyncio
async def test_guild_sync_copies_global_commands_before_sync():
    tree = FakeTree()
    bot = SimpleNamespace(tree=tree)

    synced = await sync_slash_commands(bot, guild_id=123)

    assert tree.copied_guild_id == 123
    assert tree.synced_guild_id == 123
    assert {command.name for command in synced} == {"galactia", "stream", "summary"}


@pytest.mark.asyncio
async def test_admin_group_declares_expected_commands_without_status_alias():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    try:
        await bot.add_cog(GalactiaAdminCog(bot))

        group = bot.tree.get_command("galactia")
        assert group is not None
        assert bot.tree.get_command("status") is None

        group_commands = {command.name: command for command in group.commands}
        assert {"status", "config"}.issubset(group_commands)

        config_group = group_commands["config"]
        config_commands = {command.name for command in config_group.commands}
        assert {
            "timezone",
            "language",
            "max_messages",
            "allowed_channel",
            "allowed_role",
        }.issubset(config_commands)
    finally:
        await bot.close()
