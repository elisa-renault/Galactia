from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands

from galactia.bot import command_sync_target, register_extension_command_groups, sync_slash_commands
from galactia.cogs.admin import GalactiaAdminCog


class FakeCog:
    twitch_group = discord.app_commands.Group(name="twitch", description="Twitch")


class FakeTree:
    def __init__(self):
        self.global_commands = {
            "summary": SimpleNamespace(name="summary"),
            "galactia": SimpleNamespace(name="galactia"),
        }
        self.guild_commands = {
            "stream": SimpleNamespace(name="stream"),
        }
        self.added_commands = []
        self.copied_guild_id = None
        self.synced_guild_id = None

    def get_commands(self, *, guild=None):
        if guild is None:
            return list(self.global_commands.values())
        return list(self.guild_commands.values())

    def get_command(self, name, *, guild=None, type=None):
        commands = self.guild_commands if guild is not None else self.global_commands
        return commands.get(name)

    def add_command(self, command, *, guild=None):
        commands = self.guild_commands if guild is not None else self.global_commands
        if command.name in commands:
            raise RuntimeError(f"Command already registered: {command.name}")
        commands[command.name] = command
        self.added_commands.append(command.name)

    def copy_global_to(self, *, guild):
        self.copied_guild_id = guild.id
        self.guild_commands.update(self.global_commands)

    async def sync(self, *, guild=None):
        self.synced_guild_id = getattr(guild, "id", None)
        return self.get_commands(guild=guild) if guild is not None else self.get_commands()


@pytest.mark.asyncio
async def test_global_sync_is_default_even_when_guild_id_exists():
    tree = FakeTree()
    bot = SimpleNamespace(tree=tree)

    synced = await sync_slash_commands(bot, guild_id=123)

    assert tree.copied_guild_id is None
    assert tree.synced_guild_id is None
    assert {command.name for command in synced} == {"galactia", "summary"}


@pytest.mark.asyncio
async def test_guild_sync_copies_global_commands_before_sync_when_requested():
    tree = FakeTree()
    bot = SimpleNamespace(tree=tree)

    synced = await sync_slash_commands(bot, guild_id=123, command_scope="guild")

    assert tree.copied_guild_id == 123
    assert tree.synced_guild_id == 123
    assert {command.name for command in synced} == {"galactia", "stream", "summary"}


def test_command_sync_target_requires_guild_scope_and_id():
    assert command_sync_target(123, "guild") == 123
    assert command_sync_target(123, "global") is None
    assert command_sync_target(None, "guild") is None


def test_register_extension_command_groups_is_idempotent():
    tree = FakeTree()
    tree.global_commands["twitch"] = FakeCog.twitch_group
    bot = SimpleNamespace(tree=tree, cogs={"fake": FakeCog()})

    register_extension_command_groups(bot)

    assert tree.added_commands == []


def test_register_extension_command_groups_adds_missing_group():
    tree = FakeTree()
    bot = SimpleNamespace(tree=tree, cogs={"fake": FakeCog()})

    register_extension_command_groups(bot)

    assert tree.added_commands == ["twitch"]
    assert tree.global_commands["twitch"].name == "twitch"


@pytest.mark.asyncio
async def test_admin_group_declares_expected_commands_without_status_alias():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    try:
        await bot.add_cog(GalactiaAdminCog(bot))

        group = bot.tree.get_command("galactia")
        assert group is not None
        assert bot.tree.get_command("status") is None

        group_commands = {command.name: command for command in group.commands}
        assert {"status", "config", "setup"}.issubset(group_commands)

        config_group = group_commands["config"]
        config_commands = {command.name for command in config_group.commands}
        assert {
            "timezone",
            "language",
            "max_messages",
            "allowed_channel",
            "allowed_role",
        }.issubset(config_commands)

        setup_group = group_commands["setup"]
        setup_commands = {command.name for command in setup_group.commands}
        assert {"start", "summary", "twitch", "youtube", "finish"}.issubset(setup_commands)
    finally:
        await bot.close()
