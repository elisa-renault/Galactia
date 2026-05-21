import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from galactia.cogs.ai import (
    AICog,
    IntentDetectionError,
    MessageSummaryResponder,
    SummaryIntent,
    SummaryRequest,
    SummaryRequestError,
    SummaryGenerationResult,
    detect_intent,
    external_channel_references,
    handle_time_range,
    is_bot_author_reference,
    is_direct_bot_mention,
    parse_summary_intent_content,
    parse_intent_and_authors,
    resolve_llm_authors_to_ids,
    send_summary_response,
    send_summary_text,
    summary_scan_limit_from_settings,
    validate_summary_config_access,
)
from galactia.ai_service import AIService
from galactia.handlers.summary import (
    FetchMessagesResult,
    SUMMARY_OPENAI_TIMEOUT_SECONDS,
    fetch_valid_messages,
    generate_summary,
    normalize_fetch_limit,
    normalize_scan_limit,
)
from galactia.prompts import render_prompt
from galactia.repositories.ai_requests import normalize_ai_request
from galactia.repositories.guild_settings import normalize_settings_payload
from galactia.time_parser import parse_time_limit_deterministic


class FakeAuthor:
    def __init__(
        self,
        author_id,
        display_name,
        *,
        bot=False,
        name=None,
        global_name=None,
        roles=None,
        guild_permissions=None,
    ):
        self.id = author_id
        self.display_name = display_name
        self.bot = bot
        self.name = name or display_name
        self.global_name = global_name
        self.roles = roles or []
        self.guild_permissions = guild_permissions or SimpleNamespace(administrator=False)


class FakePermissions:
    def __init__(self, *, view_channel=True, read_message_history=True):
        self.view_channel = view_channel
        self.read_message_history = read_message_history


class FakeMessage:
    def __init__(
        self,
        content,
        author,
        created_at,
        *,
        mentions=None,
        mention_everyone=False,
        role_mentions=None,
        channel_mentions=None,
        jump_url=None,
    ):
        self.content = content
        self.author = author
        self.created_at = created_at
        self.mentions = mentions or []
        self.mention_everyone = mention_everyone
        self.role_mentions = role_mentions or []
        self.channel_mentions = channel_mentions or []
        self.jump_url = jump_url


class FakeChannel:
    def __init__(
        self,
        messages,
        *,
        channel_id=123,
        members=None,
        name="general",
        guild=None,
        permissions=None,
    ):
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.guild_id = getattr(guild, "id", None)
        self.messages = messages
        self.members = members or []
        self.history_calls = []
        self.sent = []
        self.send_kwargs = []
        self.sent_objects = []
        self.permissions = permissions or {}

    def permissions_for(self, subject):
        subject_id = getattr(subject, "id", None)
        return self.permissions.get(subject_id, FakePermissions())

    def history(self, **kwargs):
        self.history_calls.append(kwargs)
        after = kwargs.get("after")
        before = kwargs.get("before")
        oldest_first = kwargs.get("oldest_first")
        limit = kwargs.get("limit")

        messages = list(self.messages)
        if after is not None:
            messages = [m for m in messages if m.created_at > after]
        if before is not None:
            messages = [m for m in messages if m.created_at < before]
        messages.sort(key=lambda m: m.created_at, reverse=not oldest_first)
        messages = messages[:limit]

        async def iterator():
            for message in messages:
                yield message

        return iterator()

    async def send(self, content, **kwargs):
        self.sent.append(content)
        self.send_kwargs.append(kwargs)
        sent_object = FakeThinking()
        self.sent_objects.append(sent_object)
        return sent_object


class FakeThinking:
    def __init__(self):
        self.edits = []
        self.edit_kwargs = []

    async def edit(self, *, content, **kwargs):
        self.edits.append(content)
        self.edit_kwargs.append(kwargs)


class FakeInteractionResponse:
    def __init__(self):
        self.deferred = False
        self.defer_kwargs = None

    async def defer(self, **kwargs):
        self.deferred = True
        self.defer_kwargs = kwargs


class FakeFollowup:
    def __init__(self):
        self.sent = []
        self.send_kwargs = []

    async def send(self, content, **kwargs):
        self.sent.append(content)
        self.send_kwargs.append(kwargs)


class FakeInteraction:
    def __init__(self, channel, user, *, guild_id=1, guild=None):
        self.channel = channel
        self.user = user
        self.guild_id = guild_id
        self.guild = guild
        self.response = FakeInteractionResponse()
        self.followup = FakeFollowup()
        self.edits = []
        self.edit_kwargs = []

    async def edit_original_response(self, *, content, **kwargs):
        self.edits.append(content)
        self.edit_kwargs.append(kwargs)


def run(coro):
    return asyncio.run(coro)


def dt(hour, minute=0):
    return datetime(2026, 5, 21, hour, minute, tzinfo=timezone.utc)


def old_dt(day, minute=0):
    return datetime(2026, 1, day, 10, minute, tzinfo=timezone.utc)


def test_direct_bot_mention_only_matches_user_mentions():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    role = SimpleNamespace(id=42)

    direct = FakeMessage("hey", user, dt(10), mentions=[bot_user])
    everyone_only = FakeMessage("hey @everyone", user, dt(10), mention_everyone=True)
    role_only = FakeMessage("hey role", user, dt(10), role_mentions=[role])
    direct_with_everyone = FakeMessage(
        "hey all", user, dt(10), mentions=[bot_user], mention_everyone=True
    )

    assert is_direct_bot_mention(direct, bot_user) is True
    assert is_direct_bot_mention(everyone_only, bot_user) is False
    assert is_direct_bot_mention(role_only, bot_user) is False
    assert is_direct_bot_mention(direct_with_everyone, bot_user) is True


def test_bot_author_reference_matches_direct_bot_mention_aliases():
    bot_user = FakeAuthor(999, "Galactia", bot=True, name="galactia")
    bot_mention = FakeAuthor(999, "Galactia Dev", bot=True, name="galactia-dev")
    user = FakeAuthor(1, "Elsia")
    message = FakeMessage(
        "@Galactia Dev résume",
        user,
        dt(10),
        mentions=[bot_mention],
    )

    assert is_bot_author_reference("Galactia Dev", message, bot_user) is True
    assert is_bot_author_reference("Galactia", message, bot_user) is True
    assert is_bot_author_reference("999", message, bot_user) is True
    assert is_bot_author_reference("Elsia", message, bot_user) is False


def test_parse_intent_and_authors_ignores_bot_self_reference_from_llm(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True, name="galactia")
    bot_mention = FakeAuthor(999, "Galactia Dev", bot=True, name="galactia-dev")
    user = FakeAuthor(1, "Elsia")
    channel = FakeChannel([], members=[user])
    channel.name = "general"
    message = FakeMessage(
        "@Galactia Dev résume",
        user,
        dt(10),
        mentions=[bot_mention],
    )
    message.channel = channel

    async def fake_detect_intent(_content, _channel_name):
        return SummaryIntent(summary=True, authors=["Galactia Dev"])

    monkeypatch.setattr("galactia.cogs.ai.detect_intent", fake_detect_intent)

    intent, authors = run(parse_intent_and_authors(SimpleNamespace(user=bot_user), message))

    assert intent.summary is True
    assert authors is None


def test_summary_request_builders_for_message_and_slash():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    current_channel = FakeChannel([], channel_id=10, name="raid")
    other_channel = SimpleNamespace(id=11)
    message = FakeMessage(
        "@Galactia résume <#10>",
        user,
        dt(10),
        mentions=[bot_user],
        channel_mentions=[current_channel],
    )
    message.guild = SimpleNamespace(id=1)
    message.channel = current_channel

    mention_request = SummaryRequest.from_message(message, bot_user)
    interaction = FakeInteraction(current_channel, user, guild_id=1)
    slash_request = SummaryRequest.from_interaction(interaction, "les 20 derniers", bot_user)

    assert mention_request.source == "mention"
    assert mention_request.content == message.content
    assert mention_request.channel_id == 10
    assert mention_request.invocation_channel_id == 10
    assert mention_request.channel_mentions == [current_channel]
    assert slash_request.source == "slash"
    assert slash_request.content == "les 20 derniers"
    assert slash_request.channel_id == 10
    assert slash_request.invocation_channel_id == 10
    assert other_channel.id not in [c.id for c in slash_request.channel_mentions]


def test_summary_request_slash_can_target_another_channel():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    invocation = FakeChannel([], channel_id=10, name="general")
    target = FakeChannel([], channel_id=20, name="raid")
    interaction = FakeInteraction(invocation, user, guild_id=1)

    request = SummaryRequest.from_interaction(
        interaction,
        "les 20 derniers",
        bot_user,
        target_channel=target,
    )

    assert request.channel is target
    assert request.channel_id == 20
    assert request.channel_name == "raid"
    assert request.invocation_channel is invocation
    assert request.is_cross_channel is True


def test_external_channel_references_block_only_other_channels():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    channel = FakeChannel([], channel_id=10, name="general")
    request = SummaryRequest(
        source="slash",
        content="résume <#10> et https://discord.com/channels/1/11/22",
        guild_id=1,
        channel_id=10,
        user_id=1,
        channel_name="general",
        channel=channel,
        author=user,
        bot_user=bot_user,
        mentions=[],
        channel_mentions=[SimpleNamespace(id=10), SimpleNamespace(id=12)],
    )

    assert external_channel_references(request) == [11, 12]


def test_on_message_ignores_everyone_without_marking_cooldown():
    bot_user = FakeAuthor(999, "Galactia", bot=True)

    class FakeBot:
        def __init__(self):
            self.user = bot_user
            self.processed = []

        async def process_commands(self, message):
            self.processed.append(message)

    bot = FakeBot()
    cog = AICog(bot)
    channel = FakeChannel([], channel_id=10)
    message = FakeMessage(
        "@everyone hello",
        FakeAuthor(1, "Elsia"),
        dt(10),
        mention_everyone=True,
    )
    message.guild = SimpleNamespace(id=1)
    message.channel = channel

    run(cog.on_message(message))

    assert bot.processed == [message]
    assert channel.sent == []
    assert cog._user_cooldowns == {}
    assert cog._channel_cooldowns == {}


def test_on_message_non_summary_does_not_mark_summary_cooldown(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)

    class FakeBot:
        def __init__(self):
            self.user = bot_user
            self.processed = []

        async def process_commands(self, message):
            self.processed.append(message)

    async def fake_parse_intent_and_authors(_request):
        return SummaryIntent(summary=False), None

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fake_parse_intent_and_authors,
    )

    bot = FakeBot()
    cog = AICog(bot)
    channel = FakeChannel([], channel_id=10)
    message = FakeMessage(
        "@Galactia ping",
        FakeAuthor(1, "Elsia"),
        dt(10),
        mentions=[bot_user],
    )
    message.guild = SimpleNamespace(id=1)
    message.channel = channel

    run(cog.on_message(message))

    assert bot.processed == []
    assert channel.sent == ["⏳ Galactia réfléchit..."]
    assert "pas encore disponible" in channel.sent_objects[0].edits[-1]
    assert cog._user_cooldowns == {}
    assert cog._channel_cooldowns == {}


def test_external_channel_block_happens_before_cooldown_and_openai(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    channel = FakeChannel([], channel_id=10)
    request = SummaryRequest(
        source="slash",
        content="résume <#11>",
        guild_id=1,
        channel_id=10,
        user_id=1,
        channel_name="general",
        channel=channel,
        author=user,
        bot_user=bot_user,
        mentions=[],
        channel_mentions=[],
    )
    thinking = FakeThinking()
    cog = AICog(SimpleNamespace(user=bot_user))

    async def fail_parse(_request):
        raise AssertionError("OpenAI intent should not be called")

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fail_parse,
    )

    result = run(
        cog.handle_summary_request(
            request,
            MessageSummaryResponder(thinking, channel),
        )
    )

    assert result.status == "wrong_channel"
    assert "historique de ce salon" in thinking.edits[-1]
    assert cog._user_cooldowns == {}
    assert cog._channel_cooldowns == {}


def test_cross_channel_slash_option_fetches_target_and_responds_in_invocation(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    guild = SimpleNamespace(id=1, me=bot_user)
    user = FakeAuthor(1, "Elsia")
    invocation = FakeChannel([], channel_id=10, name="general", guild=guild)
    target = FakeChannel([], channel_id=20, name="raid", guild=guild)
    interaction = FakeInteraction(invocation, user, guild_id=1, guild=guild)
    cog = AICog(SimpleNamespace(user=bot_user))
    seen = {}

    async def fake_parse(request):
        assert request.channel is target
        assert request.invocation_channel is invocation
        return SummaryIntent(summary=True, count_limit=1), None

    async def fake_retrieve(_bot, channel, *_args, **_kwargs):
        seen["channel"] = channel
        message = FakeMessage("raid msg", user, dt(10, 1))
        return FetchMessagesResult(
            messages=[message],
            messages_scanned=1,
            messages_selected=1,
            messages_ignored=0,
        )

    async def fake_generate_summary(_messages, _create_chat_completion, focus=None, **_kwargs):
        return SummaryGenerationResult(text="ok") if _kwargs.get("return_result") else "ok"

    async def fake_record(request, **_kwargs):
        seen["recorded_channel_id"] = request.channel_id

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fake_parse,
    )
    monkeypatch.setattr("galactia.cogs.ai.retrieve_messages", fake_retrieve)
    monkeypatch.setattr("galactia.cogs.ai.generate_summary", fake_generate_summary)
    monkeypatch.setattr("galactia.cogs.ai.record_ai_request_event", fake_record)

    result = run(
        cog.handle_summary_interaction(
            interaction,
            "les derniers messages",
            target_channel=target,
        )
    )

    assert result.status == "sent"
    assert seen["channel"] is target
    assert seen["recorded_channel_id"] == 20
    assert "Résumé de 1 messages de #raid, 0 ignorés" in interaction.edits[-1]
    assert cog._channel_cooldowns[(1, 20)] > 0


def test_cross_channel_mention_resolves_target_and_strips_reference(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    guild = SimpleNamespace(id=1, me=bot_user)
    user = FakeAuthor(1, "Elsia")
    invocation = FakeChannel([], channel_id=10, name="general", guild=guild)
    target = FakeChannel([], channel_id=20, name="raid", guild=guild)
    message = FakeMessage(
        "@Galactia résume <#20>",
        user,
        dt(10),
        mentions=[bot_user],
        channel_mentions=[target],
    )
    message.guild = guild
    message.channel = invocation
    request = SummaryRequest.from_message(message, bot_user)
    thinking = FakeThinking()
    cog = AICog(SimpleNamespace(user=bot_user))

    async def fake_parse(parsed_request):
        assert parsed_request.channel is target
        assert "<#20>" not in parsed_request.content
        return SummaryIntent(summary=True, count_limit=1), None

    async def fake_retrieve(*_args, **_kwargs):
        return FetchMessagesResult(
            messages=[FakeMessage("raid msg", user, dt(10, 1))],
            messages_scanned=1,
            messages_selected=1,
            messages_ignored=0,
        )

    async def fake_generate_summary(_messages, _create_chat_completion, focus=None, **_kwargs):
        return SummaryGenerationResult(text="ok") if _kwargs.get("return_result") else "ok"

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fake_parse,
    )
    monkeypatch.setattr("galactia.cogs.ai.retrieve_messages", fake_retrieve)
    monkeypatch.setattr("galactia.cogs.ai.generate_summary", fake_generate_summary)

    result = run(cog.handle_summary_request(request, MessageSummaryResponder(thinking, invocation)))

    assert result.status == "sent"
    assert "de #raid" in thinking.edits[-1]


def test_cross_channel_discord_link_same_guild_resolves_via_bot_cache(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    guild = SimpleNamespace(id=1, me=bot_user)
    user = FakeAuthor(1, "Elsia")
    invocation = FakeChannel([], channel_id=10, name="general", guild=guild)
    target = FakeChannel([], channel_id=20, name="raid", guild=guild)
    request = SummaryRequest(
        source="slash",
        content="résume https://discord.com/channels/1/20/30",
        guild_id=1,
        channel_id=10,
        user_id=1,
        channel_name="general",
        channel=invocation,
        author=user,
        bot_user=bot_user,
        mentions=[],
        channel_mentions=[],
        guild=guild,
        invocation_channel_id=10,
        invocation_channel=invocation,
    )
    bot = SimpleNamespace(user=bot_user, get_channel=lambda channel_id: target if channel_id == 20 else None)
    thinking = FakeThinking()
    cog = AICog(bot)

    async def fake_parse(parsed_request):
        assert parsed_request.channel is target
        assert "discord.com/channels" not in parsed_request.content
        return SummaryIntent(summary=True, count_limit=1), None

    async def fake_retrieve(*_args, **_kwargs):
        return FetchMessagesResult(
            messages=[FakeMessage("raid msg", user, dt(10, 1))],
            messages_scanned=1,
            messages_selected=1,
            messages_ignored=0,
        )

    async def fake_generate_summary(_messages, _create_chat_completion, focus=None, **_kwargs):
        return SummaryGenerationResult(text="ok") if _kwargs.get("return_result") else "ok"

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fake_parse,
    )
    monkeypatch.setattr("galactia.cogs.ai.retrieve_messages", fake_retrieve)
    monkeypatch.setattr("galactia.cogs.ai.generate_summary", fake_generate_summary)

    result = run(cog.handle_summary_request(request, MessageSummaryResponder(thinking, invocation)))

    assert result.status == "sent"
    assert "de #raid" in thinking.edits[-1]


def test_cross_channel_rejects_multiple_or_conflicting_targets_before_openai(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    guild = SimpleNamespace(id=1, me=bot_user)
    user = FakeAuthor(1, "Elsia")
    invocation = FakeChannel([], channel_id=10, name="general", guild=guild)
    first = FakeChannel([], channel_id=20, name="raid", guild=guild)
    second = FakeChannel([], channel_id=30, name="staff", guild=guild)
    thinking = FakeThinking()
    cog = AICog(SimpleNamespace(user=bot_user))

    async def fail_parse(_request):
        raise AssertionError("OpenAI intent should not be called")

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fail_parse,
    )

    request = SummaryRequest(
        source="mention",
        content="résume <#20> <#30>",
        guild_id=1,
        channel_id=10,
        user_id=1,
        channel_name="general",
        channel=invocation,
        author=user,
        bot_user=bot_user,
        mentions=[],
        channel_mentions=[first, second],
        guild=guild,
        invocation_channel_id=10,
        invocation_channel_name="general",
        invocation_channel=invocation,
    )

    result = run(cog.handle_summary_request(request, MessageSummaryResponder(thinking, invocation)))

    assert result.status == "wrong_channel"
    assert "un seul salon" in thinking.edits[-1]

    interaction = FakeInteraction(invocation, user, guild_id=1, guild=guild)
    conflicting_request = SummaryRequest.from_interaction(
        interaction,
        "résume <#30>",
        bot_user,
        target_channel=first,
    )
    conflicting_request.channel_mentions = [second]
    thinking = FakeThinking()

    result = run(
        cog.handle_summary_request(
            conflicting_request,
            MessageSummaryResponder(thinking, invocation),
        )
    )

    assert result.status == "wrong_channel"
    assert "un seul salon" in thinking.edits[-1]


def test_cross_channel_rejects_discord_link_to_other_guild_before_openai(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    invocation = FakeChannel([], channel_id=10, name="general")
    request = SummaryRequest(
        source="slash",
        content="résume https://discord.com/channels/2/20/30",
        guild_id=1,
        channel_id=10,
        user_id=1,
        channel_name="general",
        channel=invocation,
        author=user,
        bot_user=bot_user,
        mentions=[],
        channel_mentions=[],
        invocation_channel_id=10,
        invocation_channel=invocation,
    )
    thinking = FakeThinking()
    cog = AICog(SimpleNamespace(user=bot_user))

    async def fail_parse(_request):
        raise AssertionError("OpenAI intent should not be called")

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fail_parse,
    )

    result = run(cog.handle_summary_request(request, MessageSummaryResponder(thinking, invocation)))

    assert result.status == "wrong_channel"
    assert "ce serveur" in thinking.edits[-1]


def test_cross_channel_config_blocks_target_channel_before_openai(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    guild = SimpleNamespace(id=1, me=bot_user)
    user = FakeAuthor(1, "Elsia")
    invocation = FakeChannel([], channel_id=10, name="general", guild=guild)
    target = FakeChannel([], channel_id=20, name="raid", guild=guild)
    request = SummaryRequest(
        source="mention",
        content="résume <#20>",
        guild_id=1,
        channel_id=10,
        user_id=1,
        channel_name="general",
        channel=invocation,
        author=user,
        bot_user=bot_user,
        mentions=[],
        channel_mentions=[target],
        guild=guild,
        invocation_channel_id=10,
        invocation_channel=invocation,
    )
    thinking = FakeThinking()
    cog = AICog(SimpleNamespace(user=bot_user))

    async def fake_settings(_guild_id):
        return {
            "summary_allowed_channel_ids": [30],
            "summary_allowed_role_ids": [],
        }

    async def fail_parse(_request):
        raise AssertionError("OpenAI intent should not be called")

    monkeypatch.setattr("galactia.cogs.ai.load_summary_settings", fake_settings)
    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fail_parse,
    )

    result = run(cog.handle_summary_request(request, MessageSummaryResponder(thinking, invocation)))

    assert result.status == "wrong_channel"
    assert "pas autorisé pour ce salon" in thinking.edits[-1]


def test_cross_channel_requires_user_and_bot_history_access(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    guild = SimpleNamespace(id=1, me=bot_user)
    user = FakeAuthor(1, "Elsia")
    invocation = FakeChannel([], channel_id=10, name="general", guild=guild)
    target = FakeChannel(
        [],
        channel_id=20,
        name="raid",
        guild=guild,
        permissions={1: FakePermissions(read_message_history=False)},
    )
    request = SummaryRequest(
        source="mention",
        content="résume <#20>",
        guild_id=1,
        channel_id=10,
        user_id=1,
        channel_name="general",
        channel=invocation,
        author=user,
        bot_user=bot_user,
        mentions=[],
        channel_mentions=[target],
        guild=guild,
        invocation_channel_id=10,
        invocation_channel=invocation,
    )
    thinking = FakeThinking()
    cog = AICog(SimpleNamespace(user=bot_user))

    async def fail_parse(_request):
        raise AssertionError("OpenAI intent should not be called")

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fail_parse,
    )

    result = run(cog.handle_summary_request(request, MessageSummaryResponder(thinking, invocation)))
    assert result.status == "wrong_channel"
    assert "Tu n’as pas accès" in thinking.edits[-1]

    target.permissions = {999: FakePermissions(read_message_history=False)}
    thinking = FakeThinking()
    request = SummaryRequest(
        source="mention",
        content="résume <#20>",
        guild_id=1,
        channel_id=10,
        user_id=1,
        channel_name="general",
        channel=invocation,
        author=user,
        bot_user=bot_user,
        mentions=[],
        channel_mentions=[target],
        guild=guild,
        invocation_channel_id=10,
        invocation_channel=invocation,
    )

    result = run(cog.handle_summary_request(request, MessageSummaryResponder(thinking, invocation)))
    assert result.status == "wrong_channel"
    assert "Je n’ai pas accès" in thinking.edits[-1]


def test_summary_cache_key_same_for_equivalent_message_and_slash_requests():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    channel = FakeChannel([], channel_id=10)
    message = FakeMessage("@Galactia résume les 20 derniers", user, dt(10), mentions=[bot_user])
    message.guild = SimpleNamespace(id=1)
    message.channel = channel
    interaction = FakeInteraction(channel, user, guild_id=1)
    cog = AICog(SimpleNamespace(user=bot_user))

    message_request = SummaryRequest.from_message(message, bot_user)
    slash_request = SummaryRequest.from_interaction(interaction, "les 20 derniers", bot_user)

    assert cog.build_summary_cache_key(
        message_request, None, dt(10), 20, None, "latest", None
    ) == cog.build_summary_cache_key(
        slash_request, None, dt(10), 20, None, "latest", None
    )


def test_slash_summary_uses_shared_pipeline_and_public_response(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    channel = FakeChannel([], channel_id=10, name="general")
    interaction = FakeInteraction(channel, user, guild_id=1)
    cog = AICog(SimpleNamespace(user=bot_user))

    async def fake_parse(_request):
        return SummaryIntent(summary=True, count_limit=2), None

    async def fake_retrieve(*_args, **_kwargs):
        messages = [
            FakeMessage("m1", user, dt(10, 1)),
            FakeMessage("m2", user, dt(10, 2)),
        ]
        return FetchMessagesResult(
            messages=messages,
            messages_scanned=4,
            messages_selected=2,
            messages_ignored=2,
        )

    async def fake_generate_summary(_messages, _create_chat_completion, focus=None):
        return "**Résumé**\nDeux messages utiles.\n\n**Points importants**\n- Test."

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fake_parse,
    )
    monkeypatch.setattr("galactia.cogs.ai.retrieve_messages", fake_retrieve)
    monkeypatch.setattr("galactia.cogs.ai.generate_summary", fake_generate_summary)

    result = run(cog.handle_summary_interaction(interaction, "les 2 derniers messages"))

    assert result.status == "sent"
    assert interaction.response.defer_kwargs == {"thinking": True, "ephemeral": False}
    assert interaction.edits
    assert "Résumé de 2 messages, 2 ignorés" in interaction.edits[-1]
    assert interaction.edit_kwargs[-1]["allowed_mentions"] is not None


def test_slash_summary_long_response_uses_public_followups(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    channel = FakeChannel([], channel_id=10, name="general")
    interaction = FakeInteraction(channel, user, guild_id=1)
    cog = AICog(SimpleNamespace(user=bot_user))

    async def fake_parse(_request):
        return SummaryIntent(summary=True, count_limit=1), None

    async def fake_retrieve(*_args, **_kwargs):
        message = FakeMessage("m1", user, dt(10, 1))
        return FetchMessagesResult(
            messages=[message],
            messages_scanned=1,
            messages_selected=1,
            messages_ignored=0,
        )

    async def fake_generate_summary(_messages, _create_chat_completion, focus=None):
        return "x" * 4100

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fake_parse,
    )
    monkeypatch.setattr("galactia.cogs.ai.retrieve_messages", fake_retrieve)
    monkeypatch.setattr("galactia.cogs.ai.generate_summary", fake_generate_summary)

    run(cog.handle_summary_interaction(interaction, "un message"))

    assert interaction.edits
    assert interaction.followup.sent
    assert all(kwargs["ephemeral"] is False for kwargs in interaction.followup.send_kwargs)
    assert all(kwargs["allowed_mentions"] is not None for kwargs in interaction.followup.send_kwargs)


def test_fetch_valid_messages_latest_uses_newest_history_and_returns_chronological():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    messages = [
        FakeMessage(f"m{i}", user, dt(10, i))
        for i in range(5)
    ]
    channel = FakeChannel(messages)

    result = run(
        fetch_valid_messages(
            SimpleNamespace(user=bot_user),
            channel,
            start=dt(9),
            end=dt(11),
            limit=2,
            selection_mode="latest",
        )
    )

    assert channel.history_calls[0]["oldest_first"] is False
    assert [m.content for m in result] == ["m3", "m4"]


def test_fetch_valid_messages_latest_without_start_ignores_message_age():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    messages = [
        FakeMessage(f"old-{i}", user, old_dt(1, i))
        for i in range(25)
    ]
    channel = FakeChannel(messages)

    result = run(
        fetch_valid_messages(
            SimpleNamespace(user=bot_user),
            channel,
            start=None,
            end=dt(10),
            limit=20,
            selection_mode="latest",
        )
    )

    assert channel.history_calls[0]["after"] is None
    assert channel.history_calls[0]["oldest_first"] is False
    assert len(result) == 20
    assert [m.content for m in result] == [f"old-{i}" for i in range(5, 25)]


def test_fetch_valid_messages_scans_more_than_requested_to_find_valid_messages():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    bot_author = FakeAuthor(2, "HelperBot", bot=True)
    messages = [
        FakeMessage(f"valid-{i}", user, dt(10, i))
        for i in range(5)
    ] + [
        FakeMessage("", user, dt(10, 10 + i))
        for i in range(5)
    ] + [
        FakeMessage(f"bot-{i}", bot_author, dt(10, 15 + i))
        for i in range(5)
    ]
    channel = FakeChannel(messages)

    result = run(
        fetch_valid_messages(
            SimpleNamespace(user=bot_user),
            channel,
            start=dt(9),
            end=dt(11),
            limit=5,
            selection_mode="latest",
        )
    )

    assert channel.history_calls[0]["limit"] == 50
    assert [m.content for m in result] == [f"valid-{i}" for i in range(5)]


def test_fetch_valid_messages_can_return_scan_stats():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    bot_author = FakeAuthor(2, "Bot", bot=True)
    messages = [
        FakeMessage("valid", user, dt(10, 0)),
        FakeMessage("", user, dt(10, 1)),
        FakeMessage("bot", bot_author, dt(10, 2)),
    ]
    channel = FakeChannel(messages)

    result = run(
        fetch_valid_messages(
            SimpleNamespace(user=bot_user),
            channel,
            start=dt(9),
            end=dt(11),
            limit=3,
            selection_mode="earliest",
            include_stats=True,
        )
    )

    assert result.messages_selected == 1
    assert result.messages_scanned == 3
    assert result.messages_ignored == 2
    assert [m.content for m in result.messages] == ["valid"]


def test_fetch_valid_messages_earliest_uses_oldest_history():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    messages = [
        FakeMessage(f"m{i}", user, dt(10, i))
        for i in range(5)
    ]
    channel = FakeChannel(messages)

    result = run(
        fetch_valid_messages(
            SimpleNamespace(user=bot_user),
            channel,
            start=dt(9),
            end=dt(11),
            limit=2,
            selection_mode="earliest",
        )
    )

    assert channel.history_calls[0]["oldest_first"] is True
    assert [m.content for m in result] == ["m0", "m1"]


def test_fetch_valid_messages_excludes_invalid_messages_and_filters_authors():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    wanted = FakeAuthor(2, "Wanted")
    other = FakeAuthor(3, "Other")
    bot_author = FakeAuthor(4, "Bot", bot=True)
    messages = [
        FakeMessage("", wanted, dt(10, 0)),
        FakeMessage("bot", bot_author, dt(10, 1)),
        FakeMessage("mentions bot", wanted, dt(10, 2), mentions=[bot_user]),
        FakeMessage("wrong author", other, dt(10, 3)),
        FakeMessage("kept", wanted, dt(10, 4)),
    ]
    channel = FakeChannel(messages)

    result = run(
        fetch_valid_messages(
            SimpleNamespace(user=bot_user),
            channel,
            start=dt(9),
            end=dt(11),
            limit=5,
            authors=["2"],
            selection_mode="earliest",
        )
    )

    assert [m.content for m in result] == ["kept"]


def test_fetch_limit_is_bounded_to_one_to_two_thousand():
    assert normalize_fetch_limit(None) == 100
    assert normalize_fetch_limit(999) == 999
    assert normalize_fetch_limit(9999) == 2000
    with pytest.raises(ValueError):
        normalize_fetch_limit(0)


def test_scan_limit_defaults_to_ten_times_result_limit_with_cap():
    assert normalize_scan_limit(20, None) == 200
    assert normalize_scan_limit(100, None) == 1000
    assert normalize_scan_limit(20, 10) == 20
    assert normalize_scan_limit(20, 9999) == 5000


def test_summary_intent_accepts_valid_structured_json():
    intent = parse_summary_intent_content(
        """
        {
          "summary": true,
          "wrong_channel": false,
          "authors": ["Elsia"],
          "time_limit": "hier",
          "count_limit": 20,
          "selection_mode": "earliest",
          "preset": null,
          "focus": "points importants"
        }
        """
    )

    assert intent.summary is True
    assert intent.selection_mode == "earliest"
    assert intent.authors == ["Elsia"]


def test_summary_intent_rejects_invalid_or_empty_json():
    with pytest.raises(IntentDetectionError):
        parse_summary_intent_content("")
    with pytest.raises(IntentDetectionError):
        parse_summary_intent_content('{"summary": true, "selection_mode": "middle"}')


def load_golden_intents():
    path = Path(__file__).with_name("golden_summary_intents.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_versioned_prompt_loader_renders_expected_placeholders():
    rendered = render_prompt(
        "intent.v1.md",
        user_message="résume les 20 derniers",
        current_channel_name="general",
    )

    assert "résume les 20 derniers" in rendered
    assert "general" in rendered
    assert "{user_message}" not in rendered
    assert "{current_channel_name}" not in rendered


def test_golden_summary_intents_fixture_is_valid():
    cases = load_golden_intents()

    assert len(cases) == 42
    for case in cases:
        intent = SummaryIntent.model_validate(case["expected"])
        assert intent.model_dump() == case["expected"]


def test_detect_intent_accepts_golden_structured_outputs(monkeypatch):
    async def fake_sanitize(text):
        return text

    monkeypatch.setattr("galactia.cogs.ai.sanitize_user_prompt_with_llm", fake_sanitize)

    for case in load_golden_intents():
        expected = case["expected"]

        async def fake_create_chat_completion(**params):
            assert params["response_format"] is not None
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(expected))
                    )
                ]
            )

        monkeypatch.setattr(
            "galactia.cogs.ai.create_chat_completion",
            fake_create_chat_completion,
        )
        intent = run(detect_intent(case["prompt"], "general"))
        assert intent.model_dump() == SummaryIntent.model_validate(expected).model_dump()


def test_handle_time_range_caps_high_count_and_rejects_low_count():
    _, _, limit, notices = run(
        handle_time_range(SummaryIntent(summary=True, count_limit=501))
    )

    assert limit == 500
    assert any("500" in notice for notice in notices)
    with pytest.raises(SummaryRequestError):
        run(handle_time_range(SummaryIntent(summary=True, count_limit=0)))


def test_handle_time_range_count_limit_without_time_has_no_lower_date_bound():
    start, end, limit, notices = run(
        handle_time_range(SummaryIntent(summary=True, count_limit=20))
    )

    assert start is None
    assert end is not None
    assert limit == 20
    assert not any("24h" in notice for notice in notices)
    assert not any("dernières 24h" in notice for notice in notices)


def test_handle_time_range_rejects_period_entirely_before_minimum_date():
    with pytest.raises(SummaryRequestError) as exc_info:
        run(handle_time_range(SummaryIntent(summary=True, time_limit="janvier 2024")))

    assert "avant le 15/10/2024" in exc_info.value.user_message


def test_handle_time_range_clamps_period_partially_before_minimum_date():
    start, end, limit, notices = run(
        handle_time_range(SummaryIntent(summary=True, time_limit="2024"))
    )

    assert start.month == 10
    assert start.day == 15
    assert end.month == 12
    assert end.day == 31
    assert limit == 500
    assert any("15/10/2024" in notice for notice in notices)


def test_handle_time_range_rejects_invalid_and_unparsed_explicit_periods():
    with pytest.raises(SummaryRequestError) as invalid_exc:
        run(handle_time_range(SummaryIntent(summary=True, time_limit="entre 23h et 21h")))
    assert "fin est avant le début" in invalid_exc.value.user_message

    with pytest.raises(SummaryRequestError) as unknown_exc:
        run(handle_time_range(SummaryIntent(summary=True, time_limit="période floue inconnue")))
    assert "pas compris la période" in unknown_exc.value.user_message


def test_text_author_resolution_requires_unique_match():
    channel = SimpleNamespace(
        members=[
            FakeAuthor(1, "Elsia"),
            FakeAuthor(2, "Elspeth"),
            FakeAuthor(3, "Nox"),
        ]
    )

    exact = resolve_llm_authors_to_ids(["Nox"], channel, bot_id=999)
    ambiguous = resolve_llm_authors_to_ids(["Els"], channel, bot_id=999)
    missing = resolve_llm_authors_to_ids(["Unknown"], channel, bot_id=999)

    assert exact.resolved_ids == ["3"]
    assert ambiguous.failed_names == ["Els"]
    assert missing.failed_names == ["Unknown"]


def test_send_summary_text_splits_long_text_without_truncating():
    thinking = FakeThinking()
    channel = FakeChannel([])
    long_text = "x" * 4100

    run(send_summary_text(thinking, channel, long_text))

    chunks = thinking.edits + channel.sent
    assert len(chunks) == 3
    assert all(len(chunk) <= 2000 for chunk in chunks)
    assert "résumé tronqué" not in "".join(chunks)
    assert "".join(chunks) == long_text


def test_send_summary_text_disables_allowed_mentions():
    thinking = FakeThinking()
    channel = FakeChannel([])

    run(send_summary_text(thinking, channel, "hello @everyone <@123>"))

    assert thinking.edit_kwargs[0]["allowed_mentions"] is not None


def test_send_summary_response_without_start_has_clear_empty_message():
    thinking = FakeThinking()
    channel = FakeChannel([])

    result = run(
        send_summary_response(
            thinking,
            channel,
            [],
            None,
            dt(10),
            None,
            [],
        )
    )

    assert result is None
    assert "avant" in thinking.edits[-1]
    assert "entre" not in thinking.edits[-1]
    assert "None" not in thinking.edits[-1]


def test_generate_summary_uses_longer_timeout_for_openai_call():
    user = FakeAuthor(1, "Elsia")
    message = FakeMessage("hello", user, dt(10))
    seen_params = {}

    async def fake_create_chat_completion(**params):
        seen_params.update(params)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

    summary = run(generate_summary([message], fake_create_chat_completion))

    assert summary == "ok"
    assert seen_params["timeout"] == SUMMARY_OPENAI_TIMEOUT_SECONDS
    assert seen_params["_overall_timeout"] == SUMMARY_OPENAI_TIMEOUT_SECONDS + 5


def test_generate_summary_timeout_has_readable_error():
    user = FakeAuthor(1, "Elsia")
    message = FakeMessage("hello", user, dt(10))

    async def fake_create_chat_completion(**_params):
        raise asyncio.TimeoutError()

    summary = run(generate_summary([message], fake_create_chat_completion))

    assert summary.startswith("❌ Résumé échoué :")
    assert summary != "❌ Résumé échoué : "
    assert "délai" in summary


def test_cooldown_blocks_user_and_channel_requests():
    cog = AICog(SimpleNamespace())
    first = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=10),
        author=SimpleNamespace(id=20),
    )
    second_user_same_channel = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=10),
        author=SimpleNamespace(id=21),
    )

    assert cog.check_and_mark_summary_cooldown(first, now=100) == 0
    assert cog.check_and_mark_summary_cooldown(first, now=105) == 25
    assert cog.check_and_mark_summary_cooldown(second_user_same_channel, now=105) == 5
    assert cog.check_and_mark_summary_cooldown(first, now=131) == 0


def test_summary_cache_is_exact_and_expires_after_two_minutes():
    cog = AICog(SimpleNamespace())
    message = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=10),
        author=SimpleNamespace(id=20),
    )
    key = cog.build_summary_cache_key(
        message,
        dt(10),
        dt(11),
        100,
        ["2"],
        "latest",
        None,
    )

    cog.set_cached_summary(key, "cached", now=0)

    assert cog.get_cached_summary(key, now=119) == "cached"
    assert cog.get_cached_summary(key, now=121) is None


def test_summary_cache_count_only_key_ignores_precise_end_timestamp():
    cog = AICog(SimpleNamespace())
    message = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=10),
        author=SimpleNamespace(id=20),
    )

    first = cog.build_summary_cache_key(
        message,
        None,
        dt(10),
        20,
        None,
        "latest",
        None,
    )
    second = cog.build_summary_cache_key(
        message,
        None,
        dt(11),
        20,
        None,
        "latest",
        None,
    )

    assert first == second


def test_time_parser_handles_common_french_ranges():
    now = datetime(2026, 5, 21, 15, 30, tzinfo=timezone.utc)

    yesterday = parse_time_limit_deterministic("hier", now=now, timezone_name="Europe/Paris")
    morning = parse_time_limit_deterministic("ce matin", now=now, timezone_name="Europe/Paris")
    since = parse_time_limit_deterministic("depuis 8h", now=now, timezone_name="Europe/Paris")
    explicit = parse_time_limit_deterministic("entre 21h et 23h", now=now, timezone_name="Europe/Paris")
    today = parse_time_limit_deterministic("aujourd’hui", now=now, timezone_name="Europe/Paris")

    assert yesterday.matched_rule == "yesterday"
    assert morning.matched_rule == "this_morning"
    assert today.matched_rule == "today"
    assert since.start.hour == 8
    assert explicit.start.hour == 21
    assert explicit.end.hour == 23
    assert parse_time_limit_deterministic("pas une date", now=now) is None


def test_time_parser_treats_year_as_full_year_not_current_day():
    now = datetime(2026, 5, 21, 15, 30, tzinfo=timezone.utc)

    bare_year = parse_time_limit_deterministic("2025", now=now, timezone_name="Europe/Paris")
    phrased_year = parse_time_limit_deterministic("en 2024", now=now, timezone_name="Europe/Paris")

    assert bare_year.matched_rule == "year"
    assert bare_year.start.month == 1
    assert bare_year.start.day == 1
    assert bare_year.end.month == 12
    assert bare_year.end.day == 31
    assert phrased_year.matched_rule == "year"
    assert phrased_year.start.year == 2024
    assert phrased_year.end.year == 2024


def test_time_parser_handles_advanced_french_weekdays_and_relative_ranges():
    now = datetime(2026, 5, 21, 15, 30, tzinfo=ZoneInfo("Europe/Paris"))

    monday = parse_time_limit_deterministic("lundi", now=now, timezone_name="Europe/Paris")
    previous_tuesday = parse_time_limit_deterministic(
        "mardi dernier",
        now=now,
        timezone_name="Europe/Paris",
    )
    friday_evening = parse_time_limit_deterministic(
        "vendredi soir",
        now=now,
        timezone_name="Europe/Paris",
    )
    last_three_days = parse_time_limit_deterministic(
        "les 3 derniers jours",
        now=now,
        timezone_name="Europe/Paris",
    )
    last_two_weeks = parse_time_limit_deterministic(
        "les 2 dernières semaines",
        now=now,
        timezone_name="Europe/Paris",
    )
    last_thirty_minutes = parse_time_limit_deterministic(
        "depuis 30 minutes",
        now=now,
        timezone_name="Europe/Paris",
    )

    assert monday.matched_rule == "weekday"
    assert monday.start.day == 18
    assert previous_tuesday.start.day == 19
    assert friday_evening.matched_rule == "weekday_part"
    assert friday_evening.start.hour == 18
    assert last_three_days.start.day == 18
    assert last_two_weeks.start.day == 7
    assert last_thirty_minutes.start.hour == 15
    assert last_thirty_minutes.start.minute == 0


def test_time_parser_handles_months_dates_periods_and_seasons():
    now = datetime(2026, 5, 21, 15, 30, tzinfo=ZoneInfo("Europe/Paris"))

    march_2025 = parse_time_limit_deterministic("mars 2025", now=now, timezone_name="Europe/Paris")
    previous_march = parse_time_limit_deterministic(
        "mars dernier",
        now=now,
        timezone_name="Europe/Paris",
    )
    june_without_year = parse_time_limit_deterministic(
        "le 5 juin",
        now=now,
        timezone_name="Europe/Paris",
    )
    q1 = parse_time_limit_deterministic("T1 2025", now=now, timezone_name="Europe/Paris")
    s2 = parse_time_limit_deterministic("S2 2025", now=now, timezone_name="Europe/Paris")
    winter = parse_time_limit_deterministic("hiver 2025", now=now, timezone_name="Europe/Paris")

    assert march_2025.start.month == 3
    assert march_2025.end.day == 31
    assert previous_march.start.year == 2026
    assert previous_march.start.month == 3
    assert june_without_year.start.year == 2025
    assert june_without_year.start.month == 6
    assert june_without_year.start.day == 5
    assert q1.start.month == 1
    assert q1.end.month == 3
    assert s2.start.month == 7
    assert s2.end.month == 12
    assert winter.start.year == 2025
    assert winter.start.month == 12
    assert winter.end.year == 2026
    assert winter.end.month == 2


def test_time_parser_handles_open_and_closed_bounds():
    now = datetime(2026, 5, 21, 15, 30, tzinfo=ZoneInfo("Europe/Paris"))

    before = parse_time_limit_deterministic("avant 18h", now=now, timezone_name="Europe/Paris")
    after = parse_time_limit_deterministic("après 21h", now=now, timezone_name="Europe/Paris")
    date_range = parse_time_limit_deterministic(
        "du 5 juin au 7 juin",
        now=now,
        timezone_name="Europe/Paris",
    )
    weekday_range = parse_time_limit_deterministic(
        "depuis lundi jusqu’à mercredi",
        now=now,
        timezone_name="Europe/Paris",
    )

    assert before.start.hour == 0
    assert before.end.hour == 18
    assert after.start.hour == 21
    assert after.end.hour == 23
    assert date_range.start.year == 2025
    assert date_range.start.day == 5
    assert date_range.end.day == 7
    assert weekday_range.start.day == 18
    assert weekday_range.end.day == 20


def test_time_parser_handles_french_discord_abbreviations():
    now = datetime(2026, 5, 21, 15, 30, tzinfo=ZoneInfo("Europe/Paris"))

    monday = parse_time_limit_deterministic("lun", now=now, timezone_name="Europe/Paris")
    previous_tuesday = parse_time_limit_deterministic("mar der", now=now, timezone_name="Europe/Paris")
    friday_evening = parse_time_limit_deterministic("ven soir", now=now, timezone_name="Europe/Paris")
    today = parse_time_limit_deterministic("auj", now=now, timezone_name="Europe/Paris")
    today_short = parse_time_limit_deterministic("ajd", now=now, timezone_name="Europe/Paris")
    afternoon = parse_time_limit_deterministic("aprem", now=now, timezone_name="Europe/Paris")
    three_days = parse_time_limit_deterministic("les 3 der j", now=now, timezone_name="Europe/Paris")
    thirty_minutes = parse_time_limit_deterministic("dep 30 mn", now=now, timezone_name="Europe/Paris")
    two_weeks = parse_time_limit_deterministic("dep. 2 sem", now=now, timezone_name="Europe/Paris")
    january = parse_time_limit_deterministic("janv 2025", now=now, timezone_name="Europe/Paris")
    previous_september = parse_time_limit_deterministic("sept der", now=now, timezone_name="Europe/Paris")
    july_date = parse_time_limit_deterministic("5 juil", now=now, timezone_name="Europe/Paris")
    before = parse_time_limit_deterministic("avt 18h", now=now, timezone_name="Europe/Paris")
    until = parse_time_limit_deterministic("jusq. 18h", now=now, timezone_name="Europe/Paris")
    range_ = parse_time_limit_deterministic("dep lun jusq mer", now=now, timezone_name="Europe/Paris")
    q1 = parse_time_limit_deterministic("T1 2025", now=now, timezone_name="Europe/Paris")
    q2 = parse_time_limit_deterministic("2e trim 2025", now=now, timezone_name="Europe/Paris")
    previous_quarter = parse_time_limit_deterministic("trim der", now=now, timezone_name="Europe/Paris")

    assert monday.start.day == 18
    assert previous_tuesday.start.day == 19
    assert friday_evening.start.hour == 18
    assert today.matched_rule == "today"
    assert today_short.matched_rule == "today"
    assert afternoon.matched_rule == "this_afternoon"
    assert three_days.start.day == 18
    assert thirty_minutes.start.hour == 15
    assert thirty_minutes.start.minute == 0
    assert two_weeks.start.day == 7
    assert january.start.month == 1
    assert january.start.year == 2025
    assert previous_september.start.month == 9
    assert previous_september.start.year == 2025
    assert july_date.start.month == 7
    assert july_date.start.year == 2025
    assert before.end.hour == 18
    assert until.end.hour == 18
    assert range_.start.day == 18
    assert range_.end.day == 20
    assert q1.start.month == 1
    assert q2.start.month == 4
    assert previous_quarter.start.month == 1


def test_time_parser_abbreviations_do_not_break_existing_month_and_weekday_rules():
    now = datetime(2026, 5, 21, 15, 30, tzinfo=ZoneInfo("Europe/Paris"))

    march = parse_time_limit_deterministic("mars 2025", now=now, timezone_name="Europe/Paris")
    tuesday = parse_time_limit_deterministic("mardi dernier", now=now, timezone_name="Europe/Paris")

    assert march.matched_rule == "month"
    assert march.start.month == 3
    assert tuesday.matched_rule == "weekday"
    assert tuesday.start.day == 19


def test_ai_service_retries_timeout_and_reports_usage():
    class FakeCompletions:
        def __init__(self):
            self.calls = 0

        async def create(self, **_params):
            self.calls += 1
            if self.calls == 1:
                raise asyncio.TimeoutError()
            return SimpleNamespace(
                model="gpt-test",
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                ),
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            )

    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service = AIService(client=client, max_retries=2, backoff_seconds=0)

    response = run(service.chat_completion(model="gpt-test", messages=[], timeout=1))

    assert completions.calls == 2
    assert response.content == "ok"
    assert response.model == "gpt-test"
    assert response.usage.total_tokens == 15
    assert response.attempts == 2


def test_summary_settings_and_ai_request_normalizers_keep_safe_defaults():
    settings_payload = normalize_settings_payload(
        {
            "guild_id": 1,
            "twitch_check_interval": 60,
            "youtube_check_interval": 300,
            "summary_allowed_channel_ids": ["10"],
            "summary_allowed_role_ids": ["20"],
            "summary_max_messages": 9999,
            "summary_max_scan_messages": 1,
        }
    )
    request_payload = normalize_ai_request({"status": "sent", "total_tokens": "42"})

    assert settings_payload["summary_allowed_channel_ids"] == [10]
    assert settings_payload["summary_allowed_role_ids"] == [20]
    assert settings_payload["summary_max_messages"] == 2000
    assert settings_payload["summary_max_scan_messages"] == 2000
    assert request_payload["request_type"] == "summary"
    assert request_payload["total_tokens"] == 42


def test_summary_config_access_checks_allowed_channels_and_roles():
    user = SimpleNamespace(
        id=1,
        roles=[SimpleNamespace(id=20)],
        guild_permissions=SimpleNamespace(administrator=False),
    )
    admin = SimpleNamespace(
        id=2,
        roles=[],
        guild_permissions=SimpleNamespace(administrator=True),
    )
    request = SummaryRequest(
        source="slash",
        content="résume",
        guild_id=1,
        channel_id=10,
        user_id=1,
        channel_name="general",
        channel=SimpleNamespace(id=10),
        author=user,
        bot_user=SimpleNamespace(id=999),
        mentions=[],
        channel_mentions=[],
    )
    cfg = {
        "summary_allowed_channel_ids": [10],
        "summary_allowed_role_ids": [20],
    }

    assert validate_summary_config_access(request, cfg) is None
    request.channel_id = 11
    assert validate_summary_config_access(request, cfg) is not None
    request.channel_id = 10
    request.author = SimpleNamespace(
        id=3,
        roles=[],
        guild_permissions=SimpleNamespace(administrator=False),
    )
    assert validate_summary_config_access(request, cfg) is not None
    request.author = admin
    assert validate_summary_config_access(request, cfg) is None


def test_summary_scan_limit_uses_configured_cap():
    assert summary_scan_limit_from_settings({"summary_max_scan_messages": 5000}, 20) == 200
    assert summary_scan_limit_from_settings({"summary_max_scan_messages": 120}, 20) == 120
    assert summary_scan_limit_from_settings({"summary_max_scan_messages": 9999}, 700) == 5000


def test_slash_preset_overrides_detected_intent(monkeypatch):
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    channel = FakeChannel([], channel_id=10, name="general")
    interaction = FakeInteraction(channel, user, guild_id=1)
    cog = AICog(SimpleNamespace(user=bot_user))
    seen = {}

    async def fake_parse(_request):
        return SummaryIntent(summary=True, count_limit=1, preset="catchup"), None

    async def fake_retrieve(*_args, **_kwargs):
        message = FakeMessage("m1", user, dt(10, 1))
        return FetchMessagesResult(
            messages=[message],
            messages_scanned=1,
            messages_selected=1,
            messages_ignored=0,
        )

    async def fake_generate_summary(_messages, _create_chat_completion, focus=None, *, preset="catchup", return_result=False):
        seen["preset"] = preset
        return SummaryGenerationResult(text="ok") if return_result else "ok"

    monkeypatch.setattr(
        "galactia.cogs.ai.parse_summary_request_intent_and_authors",
        fake_parse,
    )
    monkeypatch.setattr("galactia.cogs.ai.retrieve_messages", fake_retrieve)
    monkeypatch.setattr("galactia.cogs.ai.generate_summary", fake_generate_summary)

    result = run(cog.handle_summary_interaction(interaction, "résume", preset="drama"))

    assert result.status == "sent"
    assert seen["preset"] == "drama"


def test_generate_summary_map_reduce_adds_cited_jump_links():
    user = FakeAuthor(1, "Elsia")
    messages = [
        FakeMessage(
            f"message {i}",
            user,
            dt(10, i % 60),
            jump_url=f"https://discord.com/channels/1/10/{i}",
        )
        for i in range(501)
    ]
    calls = []

    async def fake_create_chat_completion(**params):
        calls.append(params)
        content = (
            "**Résumé**\nPoint important [S1].\n\n"
            "**Points importants**\n- Source utile [S1]."
        )
        return SimpleNamespace(
            model="gpt-test",
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        )

    result = run(
        generate_summary(
            messages,
            fake_create_chat_completion,
            preset="catchup",
            return_result=True,
        )
    )

    assert isinstance(result, SummaryGenerationResult)
    assert len(calls) >= 2
    assert result.prompt_version == "summary_map.v1+summary_reduce.v1"
    assert "**Sources**" in result.text
    assert "https://discord.com/channels/1/10/0" in result.text
