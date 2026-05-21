import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import discord
import pytz
from discord import app_commands
from discord.ext import commands
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from galactia.ai_service import AIResponse, AIService
from galactia.ai_helpers import (
    intent_prompt,
)
from galactia.handlers.summary import (
    FetchMessagesResult,
    MAX_DISCORD,
    SummaryGenerationResult,
    chunk_text,
    fetch_valid_messages,
    fit_for_discord,
    generate_summary,
)
from galactia.prompts import render_prompt
from galactia.repositories import AIRequestRepository, GuildSettingsRepository
from galactia.repositories.guild_settings import DEFAULT_SUMMARY_SETTINGS
from galactia.settings import settings
from galactia.time_parser import TimeRangeResult, parse_time_limit_deterministic


OPENAI_TIMEOUT_SECONDS = 25
OPENAI_TIMEOUT_BUFFER_SECONDS = 5
USER_COOLDOWN_SECONDS = 30
CHANNEL_COOLDOWN_SECONDS = 10
SUMMARY_CACHE_TTL_SECONDS = 120
MAX_SUMMARY_MESSAGES = 500
ABSOLUTE_MAX_SUMMARY_MESSAGES = 2000
ABSOLUTE_MAX_SCAN_MESSAGES = 5000
DEFAULT_SUMMARY_MESSAGES = 100
DEFAULT_TIME_RANGE_SUMMARY_MESSAGES = 150
AI_ALLOWED_MENTIONS = discord.AllowedMentions.none()
SUMMARY_PRESETS = ("catchup", "decisions", "actions", "raid", "drama", "funny")
SUMMARY_PROMPT_VERSION = "summary_single.v3"
INTENT_PROMPT_VERSION = "intent.v2"
CONFIG_PERMISSION_MESSAGE = (
    "Le résumé IA n’est pas autorisé dans ce salon ou pour ton rôle sur cette guilde."
)

INTENT_FAILURE_MESSAGE = (
    "Je n’ai pas compris la demande de résumé. Reformule avec une période, "
    "un nombre de messages ou des @mentions."
)
AUTHOR_FAILURE_MESSAGE = (
    "Je n’ai pas pu identifier l’auteur demandé. Utilise une @mention Discord "
    "pour limiter le résumé à une personne précise."
)
INVALID_COUNT_MESSAGE = "Le nombre de messages doit être compris entre 1 et 2000."
EXTERNAL_CHANNEL_MESSAGE = (
    "Je ne peux résumer que le salon où la commande est utilisée. "
    "Lance la demande directement dans le salon à résumer."
)
AMBIGUOUS_TARGET_CHANNEL_MESSAGE = "Indique un seul salon à résumer."
CROSS_GUILD_CHANNEL_MESSAGE = "Je ne peux résumer que des salons de ce serveur."
TARGET_CHANNEL_NOT_ALLOWED_MESSAGE = "Le résumé IA n’est pas autorisé pour ce salon."
ROLE_NOT_ALLOWED_MESSAGE = "Le résumé IA n’est pas autorisé pour ton rôle sur cette guilde."
ADMIN_ONLY_MESSAGE = "Le resume IA est reserve aux administrateurs sur cette guilde."
SETUP_REQUIRED_MESSAGE = "Un administrateur doit terminer `/galactia setup` avant d'utiliser le resume IA."
QUOTA_EXCEEDED_MESSAGE = "Le quota de resume IA est atteint pour aujourd'hui. Reessaie demain."
USER_TARGET_ACCESS_MESSAGE = "Tu n’as pas accès au salon à résumer."
BOT_TARGET_ACCESS_MESSAGE = "Je n’ai pas accès à l’historique de ce salon."
TIME_RANGE_FAILURE_MESSAGE = (
    "Je n’ai pas compris la période demandée. Reformule avec une date plus précise."
)
TIME_RANGE_BEFORE_MIN_MESSAGE = (
    "La période demandée est avant le 15/10/2024, je ne peux pas la résumer."
)
TIME_RANGE_INVALID_MESSAGE = "La période demandée est invalide : la fin est avant le début."


def _timeout_to_seconds(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


_ai_service: AIService | None = None
_last_ai_response: AIResponse | None = None


def get_ai_service() -> AIService:
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service


class SummaryRequestError(Exception):
    """Exception whose message can be shown safely to Discord users."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class IntentDetectionError(SummaryRequestError):
    pass


class SummaryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: bool = False
    wrong_channel: bool = False
    authors: list[str] | None = None
    time_limit: str | None = None
    count_limit: int | None = None
    selection_mode: Literal["latest", "earliest"] = "latest"
    preset: Literal["catchup", "decisions", "actions", "raid", "drama", "funny"] | None = None
    focus: str | None = None

    @field_validator("authors", mode="before")
    @classmethod
    def normalize_authors(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            cleaned = [str(author).strip() for author in value if str(author).strip()]
            return cleaned or None
        return value

    @field_validator("time_limit", "focus", "preset", mode="before")
    @classmethod
    def blank_string_to_none(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("count_limit", mode="before")
    @classmethod
    def normalize_count_limit(cls, value):
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            raise ValueError("count_limit must be an integer")
        return value


SUMMARY_INTENT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "summary_intent",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "summary",
                "wrong_channel",
                "authors",
                "time_limit",
                "count_limit",
                "selection_mode",
                "preset",
                "focus",
            ],
            "properties": {
                "summary": {"type": "boolean"},
                "wrong_channel": {"type": "boolean"},
                "authors": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "time_limit": {"type": ["string", "null"]},
                "count_limit": {"type": ["integer", "null"]},
                "selection_mode": {
                    "type": "string",
                    "enum": ["latest", "earliest"],
                },
                "preset": {
                    "type": ["string", "null"],
                    "enum": ["catchup", "decisions", "actions", "raid", "drama", "funny", None],
                },
                "focus": {"type": ["string", "null"]},
            },
        },
    },
}
SUMMARY_INTENT_REQUIRED_KEYS = {
    "summary",
    "wrong_channel",
    "authors",
    "time_limit",
    "count_limit",
    "selection_mode",
    "preset",
    "focus",
}


@dataclass
class AuthorResolution:
    resolved_ids: list[str]
    failed_names: list[str]


@dataclass
class SummaryRequest:
    source: Literal["mention", "slash"]
    content: str
    guild_id: int | None
    channel_id: int | None
    user_id: int | None
    channel_name: str
    channel: object
    author: object
    bot_user: object
    mentions: list
    channel_mentions: list
    guild: object | None = None
    invocation_channel_id: int | None = None
    invocation_channel_name: str = ""
    invocation_channel: object | None = None
    preset_override: Literal["catchup", "decisions", "actions", "raid", "drama", "funny"] | None = None

    @classmethod
    def from_message(cls, message, bot_user):
        channel = getattr(message, "channel", None)
        guild = getattr(message, "guild", None)
        return cls(
            source="mention",
            content=getattr(message, "content", "") or "",
            guild_id=getattr(guild, "id", None),
            channel_id=getattr(channel, "id", None),
            user_id=getattr(getattr(message, "author", None), "id", None),
            channel_name=getattr(channel, "name", "") or "",
            channel=channel,
            author=getattr(message, "author", None),
            bot_user=bot_user,
            mentions=list(getattr(message, "mentions", []) or []),
            channel_mentions=list(getattr(message, "channel_mentions", []) or []),
            guild=guild,
            invocation_channel_id=getattr(channel, "id", None),
            invocation_channel_name=getattr(channel, "name", "") or "",
            invocation_channel=channel,
        )

    @classmethod
    def from_interaction(
        cls,
        interaction,
        demande: str,
        bot_user,
        preset: str | None = None,
        target_channel=None,
    ):
        invocation_channel = getattr(interaction, "channel", None)
        channel = target_channel or invocation_channel
        guild = getattr(interaction, "guild", None)
        return cls(
            source="slash",
            content=demande or "",
            guild_id=getattr(interaction, "guild_id", None),
            channel_id=getattr(channel, "id", None),
            user_id=getattr(getattr(interaction, "user", None), "id", None),
            channel_name=getattr(channel, "name", "") or "",
            channel=channel,
            author=getattr(interaction, "user", None),
            bot_user=bot_user,
            mentions=[],
            channel_mentions=[],
            guild=guild,
            invocation_channel_id=getattr(invocation_channel, "id", None),
            invocation_channel_name=getattr(invocation_channel, "name", "") or "",
            invocation_channel=invocation_channel,
            preset_override=preset if preset in SUMMARY_PRESETS else None,
        )

    @property
    def is_cross_channel(self) -> bool:
        return (
            self.invocation_channel_id is not None
            and self.channel_id is not None
            and self.invocation_channel_id != self.channel_id
        )


@dataclass
class SummaryResult:
    status: Literal[
        "sent",
        "empty",
        "cache_hit",
        "cooldown",
        "quota_exceeded",
        "setup_required",
        "not_summary",
        "wrong_channel",
        "error",
    ]
    response_text: str | None = None
    summary_text: str | None = None
    messages_scanned: int = 0
    messages_selected: int = 0
    messages_ignored: int = 0
    cache_hit: bool = False
    cooldown_seconds: int | None = None


class MessageSummaryResponder:
    def __init__(self, thinking, channel):
        self.thinking = thinking
        self.channel = channel

    async def edit_initial(self, content: str):
        await self.thinking.edit(
            content=content,
            allowed_mentions=AI_ALLOWED_MENTIONS,
        )

    async def send_followup(self, content: str):
        await self.channel.send(
            content,
            allowed_mentions=AI_ALLOWED_MENTIONS,
        )


class InteractionSummaryResponder:
    def __init__(self, interaction):
        self.interaction = interaction

    async def edit_initial(self, content: str):
        await self.interaction.edit_original_response(
            content=content,
            allowed_mentions=AI_ALLOWED_MENTIONS,
        )

    async def send_followup(self, content: str):
        await self.interaction.followup.send(
            content,
            ephemeral=False,
            allowed_mentions=AI_ALLOWED_MENTIONS,
        )


def is_direct_bot_mention(message, bot_user) -> bool:
    """Return True only when the bot user is explicitly mentioned."""
    bot_id = getattr(bot_user, "id", None)
    if bot_id is None:
        return False
    return any(
        getattr(mentioned_user, "id", None) == bot_id
        for mentioned_user in getattr(message, "mentions", []) or []
    )


async def create_chat_completion(**params):
    """Compatibility wrapper around the async-native AI service."""
    global _last_ai_response
    request_timeout = _timeout_to_seconds(params.get("timeout"), OPENAI_TIMEOUT_SECONDS)
    params.setdefault("timeout", request_timeout)
    params.setdefault(
        "_overall_timeout",
        request_timeout + OPENAI_TIMEOUT_BUFFER_SECONDS,
    )
    response = await get_ai_service().chat_completion(**params)
    _last_ai_response = response
    try:
        setattr(response.raw_response, "_galactia_ai_response", response)
    except Exception:
        pass
    return response.raw_response


def consume_last_ai_response() -> AIResponse | None:
    global _last_ai_response
    response = _last_ai_response
    _last_ai_response = None
    return response


async def sanitize_user_prompt_with_llm(text):
    suspicious_pattern = re.compile(
        r"(?i)\b("
        r"ignore\s+(?:les\s+)?(?:instructions|règles|précédentes)|"
        r"disregard|override|bypass|jailbreak|DAN|act\s+as|"
        r"system\s*prompt|developer\s*message|tool\s*call|function\s*call"
        r")\b"
    )

    def suspicious(t: str) -> bool:
        return bool(suspicious_pattern.search(t))

    try:
        resp = await create_chat_completion(
            model="gpt-5-mini",
            messages=[
                {
                    "role": "system",
                    "content": render_prompt(
                        "sanitize.v1.md",
                        user_message=text,
                    ),
                }
            ],
        )
        cleaned = (resp.choices[0].message.content or "").strip()

        if not cleaned and suspicious(text):
            logging.info("Sanitize blocked fully suspicious input.")
            return ""

        if not cleaned:
            logging.info("Sanitize fallback to original: empty output.")
            return text

        if len(cleaned) < 0.7 * len(text) and not suspicious(text):
            logging.info("Sanitize fallback to original: aggressive removal.")
            return text

        logging.info(
            "Sanitize completed: changed=%s len_in=%d len_out=%d",
            cleaned != text,
            len(text),
            len(cleaned),
        )
        return cleaned

    except Exception as e:
        logging.info("Sanitize fallback to original after %s.", type(e).__name__)
        return text


def parse_summary_intent_content(content: str | None) -> SummaryIntent:
    if not content or not content.strip():
        raise IntentDetectionError(INTENT_FAILURE_MESSAGE)
    try:
        data = json.loads(content)
        if not isinstance(data, dict) or not SUMMARY_INTENT_REQUIRED_KEYS.issubset(data):
            raise ValueError("missing required intent keys")
        return SummaryIntent.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError) as e:
        raise IntentDetectionError(INTENT_FAILURE_MESSAGE) from e


async def detect_intent(user_message, channel_name) -> SummaryIntent:
    try:
        user_message_clean = await sanitize_user_prompt_with_llm(user_message)
        messages = intent_prompt(user_message_clean, channel_name)
        response = await create_chat_completion(
            model="gpt-5-mini",
            messages=messages,
            response_format=SUMMARY_INTENT_RESPONSE_FORMAT,
        )
        if not response.choices:
            raise IntentDetectionError(INTENT_FAILURE_MESSAGE)

        intent_result = response.choices[0].message.content
        intent = parse_summary_intent_content(intent_result)
        logging.info(
            "Intent detected: summary=%s wrong_channel=%s authors=%d mode=%s "
            "preset=%s has_time=%s has_count=%s has_focus=%s",
            intent.summary,
            intent.wrong_channel,
            len(intent.authors or []),
            intent.selection_mode,
            intent.preset,
            bool(intent.time_limit),
            intent.count_limit is not None,
            bool(intent.focus),
        )
        return intent
    except IntentDetectionError:
        logging.info("Intent detection failed: invalid structured output.")
        raise
    except Exception as e:
        logging.info("Intent detection failed after %s.", type(e).__name__)
        raise IntentDetectionError(INTENT_FAILURE_MESSAGE) from e


def get_local_now():
    tz = pytz.timezone("Europe/Paris")
    return datetime.now(tz)


async def parse_time_limit_to_datetime_range(
    time_limit_str,
    timezone_name: str = "Europe/Paris",
) -> TimeRangeResult:
    result = parse_time_limit_deterministic(
        time_limit_str,
        timezone=timezone_name,
    )
    if not result:
        logging.info("Time parsing failed: no deterministic match.")
        raise SummaryRequestError(TIME_RANGE_FAILURE_MESSAGE)
    logging.info(
        "Time range parsed deterministically: rule=%s confidence=%s start=%s end=%s timezone=%s.",
        result.matched_rule,
        result.confidence,
        result.start,
        result.end,
        timezone_name or "Europe/Paris",
    )
    return result


def _norm_person_name(s: str) -> str:
    """Normalize a free-text name ('d’Elsia', quotes, @...) for matching."""
    s = s.strip()
    s = re.sub(r"^(d['’]|l['’])", "", s, flags=re.IGNORECASE)
    s = s.lstrip("@#<>'\"` ").rstrip(">'\"` ")
    return s.strip()


def is_bot_author_reference(raw_name, message, bot_user) -> bool:
    """Return True when an LLM author value points to the bot itself."""
    normalized = _norm_person_name(str(raw_name)).lower()
    if not normalized:
        return False

    bot_id = str(getattr(bot_user, "id", ""))
    if bot_id and normalized == bot_id:
        return True

    bot_aliases = set()
    for user in [bot_user, *(getattr(message, "mentions", []) or [])]:
        if str(getattr(user, "id", "")) != bot_id:
            continue
        for alias in filter(
            None,
            [
                getattr(user, "display_name", None),
                getattr(user, "global_name", None),
                getattr(user, "name", None),
                bot_id,
            ],
        ):
            alias = _norm_person_name(str(alias)).lower()
            if alias:
                bot_aliases.add(alias)

    for alias in bot_aliases:
        if normalized == alias:
            return True
        if len(normalized) >= 3 and alias.startswith(normalized):
            return True
        if len(alias) >= 3 and normalized.startswith(alias):
            return True
    return False


def resolve_llm_authors_to_ids(names, channel, bot_id) -> AuthorResolution:
    if not names:
        return AuthorResolution([], [])

    candidates: dict[str, set[str]] = {}
    for member in getattr(channel, "members", []):
        if member.bot:
            continue
        member_id = str(member.id)
        if member_id == str(bot_id):
            continue
        for key in filter(
            None,
            [
                getattr(member, "display_name", None),
                getattr(member, "global_name", None),
                getattr(member, "name", None),
            ],
        ):
            candidates.setdefault(key.lower(), set()).add(member_id)

    resolved = []
    failed = []
    for raw in names:
        normalized = _norm_person_name(str(raw))
        key = normalized.lower()
        if not key:
            continue

        exact_ids = candidates.get(key, set())
        if len(exact_ids) == 1:
            resolved.extend(exact_ids)
            continue
        if len(exact_ids) > 1:
            failed.append(str(raw))
            continue

        prefix_ids = {
            member_id
            for candidate_name, member_ids in candidates.items()
            if candidate_name.startswith(key)
            for member_id in member_ids
        }
        if len(prefix_ids) == 1:
            resolved.extend(prefix_ids)
        else:
            failed.append(str(raw))

    return AuthorResolution(list(dict.fromkeys(resolved)), failed)


def extract_authors_from_request(request: SummaryRequest) -> list[str] | None:
    bot_id = getattr(request.bot_user, "id", None)
    explicit_mentions = [
        str(member.id)
        for member in request.mentions
        if getattr(member, "id", None) != bot_id
    ]
    if explicit_mentions:
        return list(dict.fromkeys(explicit_mentions))

    raw_ids = re.findall(r"<@!?(\d+)>", request.content or "")
    raw_ids = [member_id for member_id in raw_ids if member_id != str(bot_id)]
    return list(dict.fromkeys(raw_ids)) or None


async def parse_summary_request_intent_and_authors(request: SummaryRequest):
    intent = await detect_intent(request.content, request.channel_name)

    requested_authors = extract_authors_from_request(request)
    if requested_authors:
        logging.info("Authors from explicit mentions: count=%d.", len(requested_authors))
        return intent, requested_authors

    if not intent.authors:
        return intent, None

    llm_authors = [
        author
        for author in intent.authors
        if not is_bot_author_reference(author, request, request.bot_user)
    ]
    ignored_bot_authors = len(intent.authors) - len(llm_authors)
    if ignored_bot_authors:
        logging.info("Ignored bot self-reference in LLM authors: count=%d.", ignored_bot_authors)

    if not llm_authors:
        return intent, None

    resolution = resolve_llm_authors_to_ids(
        llm_authors,
        request.channel,
        getattr(request.bot_user, "id", None),
    )
    if resolution.failed_names or not resolution.resolved_ids:
        logging.info(
            "LLM author resolution failed: requested=%d failed=%d.",
            len(llm_authors),
            len(resolution.failed_names),
        )
        raise SummaryRequestError(AUTHOR_FAILURE_MESSAGE)

    logging.info("Authors resolved from LLM: count=%d.", len(resolution.resolved_ids))
    return intent, resolution.resolved_ids


async def parse_intent_and_authors(bot, message):
    request = SummaryRequest.from_message(message, bot.user)
    return await parse_summary_request_intent_and_authors(request)


CHANNEL_MENTION_RE = re.compile(r"<#(\d+)>")
CHANNEL_LINK_RE = re.compile(
    r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/channels/"
    r"(?P<guild_id>@me|\d+)/(?P<channel_id>\d+)(?:/\d+)?"
)


@dataclass(frozen=True)
class ChannelReference:
    channel_id: int
    guild_id: int | None = None


def _object_guild_id(obj) -> int | None:
    guild = getattr(obj, "guild", None)
    if guild is not None:
        return getattr(guild, "id", None)
    return getattr(obj, "guild_id", None)


def channel_label(channel) -> str:
    name = getattr(channel, "name", None)
    if name:
        return f"#{name}"
    channel_id = getattr(channel, "id", None)
    return f"salon {channel_id}" if channel_id is not None else "ce salon"


def strip_resolved_channel_references(content: str) -> str:
    cleaned = CHANNEL_MENTION_RE.sub("", content or "")
    cleaned = CHANNEL_LINK_RE.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def channel_references(request: SummaryRequest) -> list[ChannelReference]:
    refs = [
        ChannelReference(
            int(getattr(channel, "id")),
            _object_guild_id(channel),
        )
        for channel in request.channel_mentions
        if getattr(channel, "id", None) is not None
    ]
    refs.extend(
        ChannelReference(int(channel_id), None)
        for channel_id in CHANNEL_MENTION_RE.findall(request.content or "")
    )
    for match in CHANNEL_LINK_RE.finditer(request.content or ""):
        raw_guild_id = match.group("guild_id")
        refs.append(
            ChannelReference(
                int(match.group("channel_id")),
                None if raw_guild_id == "@me" else int(raw_guild_id),
            )
        )
    return refs


def external_channel_references(request: SummaryRequest) -> list[int]:
    current_channel_id = str(request.channel_id)
    referenced_ids = {str(reference.channel_id) for reference in channel_references(request)}
    return sorted(
        int(channel_id)
        for channel_id in referenced_ids
        if channel_id and channel_id != current_channel_id
    )


def _find_referenced_channel_object(request: SummaryRequest, channel_id: int):
    for channel in request.channel_mentions:
        if getattr(channel, "id", None) == channel_id:
            return channel
    if getattr(request.channel, "id", None) == channel_id:
        return request.channel
    if getattr(request.invocation_channel, "id", None) == channel_id:
        return request.invocation_channel
    return None


async def _resolve_channel_object(bot, request: SummaryRequest, channel_id: int):
    channel = _find_referenced_channel_object(request, channel_id)
    if channel is not None:
        return channel
    if hasattr(bot, "get_channel"):
        channel = bot.get_channel(channel_id)
        if channel is not None:
            return channel
    if hasattr(bot, "fetch_channel"):
        try:
            return await bot.fetch_channel(channel_id)
        except Exception:
            return None
    return None


def _permissions_have_history(perms) -> bool:
    return bool(
        getattr(perms, "view_channel", False)
        and getattr(perms, "read_message_history", False)
    )


def has_channel_history_access(channel, subject) -> bool:
    if not hasattr(channel, "permissions_for"):
        return True
    if subject is None:
        return False
    try:
        return _permissions_have_history(channel.permissions_for(subject))
    except Exception:
        return False


def bot_member_for_channel(request: SummaryRequest, channel):
    guild = getattr(channel, "guild", None) or request.guild
    return getattr(guild, "me", None) or request.bot_user


def set_summary_target_channel(request: SummaryRequest, channel) -> None:
    request.channel = channel
    request.channel_id = getattr(channel, "id", None)
    request.channel_name = getattr(channel, "name", "") or ""


async def resolve_summary_target_channel(request: SummaryRequest, bot) -> None:
    refs = channel_references(request)
    requested_guild_id = request.guild_id
    if requested_guild_id is None:
        raise SummaryRequestError(CROSS_GUILD_CHANNEL_MESSAGE)

    for reference in refs:
        if reference.guild_id is not None and reference.guild_id != requested_guild_id:
            raise SummaryRequestError(CROSS_GUILD_CHANNEL_MESSAGE)
        if reference.guild_id is None:
            # Discord DM links use @me and are not valid guild channel targets.
            for match in CHANNEL_LINK_RE.finditer(request.content or ""):
                if (
                    int(match.group("channel_id")) == reference.channel_id
                    and match.group("guild_id") == "@me"
                ):
                    raise SummaryRequestError(CROSS_GUILD_CHANNEL_MESSAGE)

    referenced_ids = {reference.channel_id for reference in refs}
    option_target_id = (
        request.channel_id
        if request.invocation_channel_id is not None
        and request.channel_id != request.invocation_channel_id
        else None
    )
    candidate_ids = set(referenced_ids)
    if option_target_id is not None:
        candidate_ids.add(option_target_id)

    if len(candidate_ids) > 1:
        raise SummaryRequestError(AMBIGUOUS_TARGET_CHANNEL_MESSAGE)

    if candidate_ids:
        target_id = next(iter(candidate_ids))
        target_channel = await _resolve_channel_object(bot, request, target_id)
        if target_channel is None:
            raise SummaryRequestError(BOT_TARGET_ACCESS_MESSAGE)
        target_guild_id = _object_guild_id(target_channel)
        if target_guild_id is not None and target_guild_id != requested_guild_id:
            raise SummaryRequestError(CROSS_GUILD_CHANNEL_MESSAGE)
        if not hasattr(target_channel, "history"):
            raise SummaryRequestError(BOT_TARGET_ACCESS_MESSAGE)
        set_summary_target_channel(request, target_channel)
        request.content = strip_resolved_channel_references(request.content)

    if request.channel_id is None or request.invocation_channel_id is None:
        raise SummaryRequestError(CROSS_GUILD_CHANNEL_MESSAGE)

    if not has_channel_history_access(request.channel, request.author):
        raise SummaryRequestError(USER_TARGET_ACCESS_MESSAGE)

    bot_member = bot_member_for_channel(request, request.channel)
    if not has_channel_history_access(request.channel, bot_member):
        raise SummaryRequestError(BOT_TARGET_ACCESS_MESSAGE)


def clamp_time_range_to_allowed_bounds(
    result: TimeRangeResult,
    minimum_date: datetime,
) -> tuple[datetime, datetime, list[str]]:
    if result.end < result.start:
        logging.info(
            "Summary period invalid: rule=%s status=end_before_start.",
            result.matched_rule,
        )
        raise SummaryRequestError(TIME_RANGE_INVALID_MESSAGE)

    if result.end < minimum_date:
        logging.info(
            "Summary period rejected: rule=%s status=before_minimum.",
            result.matched_rule,
        )
        raise SummaryRequestError(TIME_RANGE_BEFORE_MIN_MESSAGE)

    notices = []
    start = result.start
    end = result.end
    if start < minimum_date:
        logging.info(
            "Summary start adjusted to minimum date: rule=%s.",
            result.matched_rule,
        )
        notices.append(
            "La date de début a été ajustée au 15/10/2024 (limite minimale)."
        )
        start = minimum_date

    if end < start:
        logging.info(
            "Summary period invalid after clamp: rule=%s status=end_before_start.",
            result.matched_rule,
        )
        raise SummaryRequestError(TIME_RANGE_INVALID_MESSAGE)

    if result.notice:
        notices.append(result.notice)
    return start, end, notices


async def handle_time_range(
    intent: SummaryIntent,
    *,
    timezone_name: str = "Europe/Paris",
    max_messages: int = MAX_SUMMARY_MESSAGES,
):
    max_messages = min(max(int(max_messages or MAX_SUMMARY_MESSAGES), 1), ABSOLUTE_MAX_SUMMARY_MESSAGES)
    tz = pytz.timezone(timezone_name or "Europe/Paris")
    now = datetime.now(tz)
    min_date = tz.localize(datetime(2024, 10, 15))
    fallback_notices = []

    if intent.count_limit is not None:
        if intent.count_limit < 1:
            raise SummaryRequestError(INVALID_COUNT_MESSAGE)
        if intent.count_limit > max_messages:
            logging.info("count_limit reduced to maximum.")
            fallback_notices.append(
                f"Le nombre de messages demandé a été réduit à {max_messages} (maximum autorisé)."
            )
        limit = min(intent.count_limit, max_messages)
        logging.info("count_limit normalized: %d.", limit)
    elif intent.time_limit:
        limit = min(DEFAULT_TIME_RANGE_SUMMARY_MESSAGES, max_messages)
        logging.info("No count_limit with time_limit; fallback to fast default messages.")
        fallback_notices.append(f"Aucun nombre précisé → résumé sur {limit} messages max.")
    else:
        limit = DEFAULT_SUMMARY_MESSAGES
        logging.info("No count_limit nor time_limit; fallback to 100 messages.")
        fallback_notices.append(
            "Aucun nombre de messages ni plage de temps précisé → résumé sur les 100 derniers messages."
        )

    if intent.time_limit:
        time_result = await parse_time_limit_to_datetime_range(intent.time_limit, timezone_name)
        logging.info("time_limit parsed.")
        start, end, time_notices = clamp_time_range_to_allowed_bounds(time_result, min_date)
        fallback_notices.extend(time_notices)
    elif intent.count_limit is not None:
        end = now
        start = None
        logging.info("No time_limit with count_limit; fetching latest messages without lower date bound.")
    else:
        end = now
        start = now - timedelta(days=1)
        logging.info("No time_limit; fallback to last 24h.")
        fallback_notices.append(
            "Aucun intervalle de temps précisé → résumé sur les dernières 24h."
        )

    return start, end, limit, fallback_notices


async def retrieve_messages(
    bot, channel, start, end, limit, authors, selection_mode: Literal["latest", "earliest"], scan_limit=None
):
    return await fetch_valid_messages(
        bot,
        channel,
        start=start,
        end=end,
        limit=limit,
        authors=authors,
        selection_mode=selection_mode,
        scan_limit=scan_limit,
        include_stats=True,
    )


def default_summary_settings(guild_id: int | None = None) -> dict:
    # Unit/local fallback keeps the historical summary behavior when the DB is
    # unavailable. Persisted new guilds still use safe disabled defaults.
    return {
        "guild_id": guild_id,
        **DEFAULT_SUMMARY_SETTINGS,
        "summary_enabled": True,
        "summary_access_mode": "everyone",
    }


async def load_summary_settings(guild_id: int | None) -> dict:
    if guild_id is None:
        return default_summary_settings(guild_id)
    try:
        return await GuildSettingsRepository().get_or_create(
            guild_id,
            twitch_check_interval=settings.twitch_check_interval,
            twitch_announce_channel_id=settings.twitch_announce_channel_id,
            youtube_check_interval=settings.youtube_check_interval,
            youtube_announce_channel_id=settings.youtube_announce_channel_id,
        )
    except Exception as exc:
        logging.info("Summary settings fallback: %s.", type(exc).__name__)
        return default_summary_settings(guild_id)


def author_is_admin(author) -> bool:
    permissions = getattr(author, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False))


def author_role_ids(author) -> set[int]:
    return {
        int(getattr(role, "id"))
        for role in getattr(author, "roles", []) or []
        if getattr(role, "id", None) is not None
    }


def validate_summary_config_access(request: SummaryRequest, cfg: dict) -> str | None:
    if not cfg.get("summary_enabled", True):
        return SETUP_REQUIRED_MESSAGE

    allowed_channels = {int(channel_id) for channel_id in cfg.get("summary_allowed_channel_ids") or []}
    if allowed_channels and request.channel_id not in allowed_channels:
        return TARGET_CHANNEL_NOT_ALLOWED_MESSAGE

    allowed_roles = {int(role_id) for role_id in cfg.get("summary_allowed_role_ids") or []}
    access_mode = cfg.get("summary_access_mode")
    if not access_mode:
        access_mode = "allowed_roles" if allowed_roles else "everyone"

    if access_mode == "admins_only" and not author_is_admin(request.author):
        return ADMIN_ONLY_MESSAGE

    if access_mode == "allowed_roles" and not author_is_admin(request.author):
        if not (allowed_roles & author_role_ids(request.author)):
            return ROLE_NOT_ALLOWED_MESSAGE
    return None


def summary_max_messages_from_settings(cfg: dict) -> int:
    configured = cfg.get("summary_max_messages", MAX_SUMMARY_MESSAGES)
    return min(max(int(configured or MAX_SUMMARY_MESSAGES), 1), ABSOLUTE_MAX_SUMMARY_MESSAGES)


def summary_scan_limit_from_settings(cfg: dict, result_limit: int) -> int:
    configured_cap = cfg.get("summary_max_scan_messages") or ABSOLUTE_MAX_SCAN_MESSAGES
    configured_cap = min(max(int(configured_cap), result_limit), ABSOLUTE_MAX_SCAN_MESSAGES)
    return min(max(result_limit, result_limit * 10), configured_cap, ABSOLUTE_MAX_SCAN_MESSAGES)


def log_soft_quota_state(cfg: dict, usage: dict | None) -> None:
    if not usage:
        return
    guild_usage = usage.get("guild", {})
    user_usage = usage.get("user", {})
    channel_usage = usage.get("channel", {})
    exceeded = []
    if guild_usage.get("requests", 0) >= int(cfg.get("summary_quota_guild_daily") or 0):
        exceeded.append("guild_requests")
    if user_usage.get("requests", 0) >= int(cfg.get("summary_quota_user_daily") or 0):
        exceeded.append("user_requests")
    if channel_usage.get("requests", 0) >= int(cfg.get("summary_quota_channel_daily") or 0):
        exceeded.append("channel_requests")
    if guild_usage.get("tokens", 0) >= int(cfg.get("summary_quota_tokens_daily") or 0):
        exceeded.append("guild_tokens")
    if exceeded:
        logging.info("Summary soft quota exceeded: %s.", ",".join(exceeded))


def summary_quota_exceeded_reasons(cfg: dict, usage: dict | None) -> list[str]:
    if not usage:
        return []
    guild_usage = usage.get("guild", {})
    user_usage = usage.get("user", {})
    channel_usage = usage.get("channel", {})
    exceeded = []
    if int(cfg.get("summary_quota_guild_daily") or 0) and guild_usage.get("requests", 0) >= int(
        cfg.get("summary_quota_guild_daily") or 0
    ):
        exceeded.append("guild_requests")
    if int(cfg.get("summary_quota_user_daily") or 0) and user_usage.get("requests", 0) >= int(
        cfg.get("summary_quota_user_daily") or 0
    ):
        exceeded.append("user_requests")
    if int(cfg.get("summary_quota_channel_daily") or 0) and channel_usage.get("requests", 0) >= int(
        cfg.get("summary_quota_channel_daily") or 0
    ):
        exceeded.append("channel_requests")
    if int(cfg.get("summary_quota_tokens_daily") or 0) and guild_usage.get("tokens", 0) >= int(
        cfg.get("summary_quota_tokens_daily") or 0
    ):
        exceeded.append("guild_tokens")
    return exceeded


async def load_summary_usage_today(request: SummaryRequest) -> dict | None:
    try:
        return await AIRequestRepository().summary_usage_today(
            request.guild_id,
            user_id=request.user_id,
            channel_id=request.channel_id,
        )
    except Exception as exc:
        logging.info("Summary usage unavailable: %s.", type(exc).__name__)
        return None


async def record_ai_request_event(
    request: SummaryRequest,
    *,
    status: str,
    preset: str | None = None,
    generation: SummaryGenerationResult | None = None,
    messages_scanned: int = 0,
    messages_selected: int = 0,
    messages_ignored: int = 0,
    error_type: str | None = None,
) -> None:
    try:
        await AIRequestRepository().insert(
            {
                "guild_id": request.guild_id,
                "channel_id": request.channel_id,
                "user_id": request.user_id,
                "source": request.source,
                "request_type": "summary",
                "status": status,
                "model": getattr(generation, "model", None),
                "preset": preset,
                "prompt_version": getattr(generation, "prompt_version", SUMMARY_PROMPT_VERSION),
                "messages_scanned": messages_scanned,
                "messages_selected": messages_selected,
                "messages_ignored": messages_ignored,
                "prompt_tokens": getattr(generation, "prompt_tokens", 0),
                "completion_tokens": getattr(generation, "completion_tokens", 0),
                "total_tokens": getattr(generation, "total_tokens", 0),
                "latency_ms": getattr(generation, "latency_ms", 0),
                "attempts": getattr(generation, "attempts", 0),
                "error_type": error_type,
            }
        )
    except Exception as exc:
        logging.info("AI request event not recorded: %s.", type(exc).__name__)


def format_summary_window(start, end) -> str:
    if start is None and end is None:
        return ""
    if start is None:
        return f" avant {end.strftime('%d/%m/%Y %H:%M')}"
    if end is None:
        return f" depuis {start.strftime('%d/%m/%Y %H:%M')}"
    return (
        f" entre {start.strftime('%d/%m/%Y %H:%M')} "
        f"et {end.strftime('%d/%m/%Y %H:%M')}"
    )


def summary_feedback_line(request: SummaryRequest, fetch_result: FetchMessagesResult) -> str:
    if request.is_cross_channel:
        return (
            f"Résumé de {fetch_result.messages_selected} messages de "
            f"{channel_label(request.channel)}."
        )
    return f"Résumé de {fetch_result.messages_selected} messages."


def merge_summary_intro(*parts: str) -> str:
    cleaned = []
    for part in parts:
        text = (part or "").strip()
        if not text:
            continue
        text = re.sub(r"^[ℹ️⚠️\s]+", "", text).strip()
        if text:
            cleaned.append(text)
    return " ".join(cleaned)


async def send_summary_content(responder, content: str):
    content = content or "Aucun contenu à envoyer."
    try:
        if len(content) <= MAX_DISCORD:
            await responder.edit_initial(content)
            return

        chunks = list(chunk_text(content, size=1900))
        await responder.edit_initial(chunks[0])
        for chunk in chunks[1:]:
            await responder.send_followup(chunk)
    except Exception:
        fallback = fit_for_discord(content, hard_limit=MAX_DISCORD, target=1900)
        try:
            await responder.edit_initial(fallback)
        except Exception:
            await responder.send_followup(fallback)


async def send_summary_text(thinking, channel, content: str):
    await send_summary_content(MessageSummaryResponder(thinking, channel), content)


async def send_summary_response(
    thinking, channel, messages, start, end, focus, fallback_notices, fetch_stats=None
):
    stats = fetch_stats or FetchMessagesResult(
        messages=messages,
        messages_scanned=len(messages),
        messages_selected=len(messages),
        messages_ignored=0,
    )
    if not messages:
        stats_suffix = (
            f" ({stats.messages_ignored} ignorés sur {stats.messages_scanned} scannés)."
            if stats.messages_scanned
            else ""
        )
        await thinking.edit(
            content=f"Aucun message trouvé{format_summary_window(start, end)}.{stats_suffix}",
            allowed_mentions=AI_ALLOWED_MENTIONS,
        )
        return None

    summary = await generate_summary(messages, create_chat_completion, focus=focus)

    if not summary.startswith("❌"):
        intro = merge_summary_intro(
            f"Résumé de {stats.messages_selected} messages.",
            *fallback_notices,
        )
        summary = f"{intro}\n\n{summary}" if intro else summary

    await send_summary_text(thinking, channel, summary)
    return summary


async def generate_summary_result(
    messages,
    focus: str | None,
    preset: str,
    selection_mode: str = "latest",
) -> SummaryGenerationResult:
    try:
        result = await generate_summary(
            messages,
            create_chat_completion,
            focus=focus,
            preset=preset,
            selection_mode=selection_mode,
            return_result=True,
        )
    except TypeError:
        try:
            result = await generate_summary(
                messages,
                create_chat_completion,
                focus=focus,
                preset=preset,
                return_result=True,
            )
        except TypeError:
            summary_text = await generate_summary(messages, create_chat_completion, focus=focus)
            return SummaryGenerationResult(text=summary_text)

    if isinstance(result, SummaryGenerationResult):
        return result
    return SummaryGenerationResult(text=str(result or ""))


class AICog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._user_cooldowns: dict[tuple[int | None, int | None], float] = {}
        self._channel_cooldowns: dict[tuple[int | None, int | None], float] = {}
        self._summary_cache: dict[tuple, tuple[float, str]] = {}

    @staticmethod
    def _guild_id(message):
        if hasattr(message, "guild_id"):
            return getattr(message, "guild_id", None)
        return getattr(getattr(message, "guild", None), "id", None)

    @staticmethod
    def _channel_id(message):
        if hasattr(message, "channel_id"):
            return getattr(message, "channel_id", None)
        return getattr(getattr(message, "channel", None), "id", None)

    @staticmethod
    def _author_id(message):
        if hasattr(message, "user_id"):
            return getattr(message, "user_id", None)
        return getattr(getattr(message, "author", None), "id", None)

    def check_and_mark_summary_cooldown(self, message, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        guild_id = self._guild_id(message)
        user_key = (guild_id, self._author_id(message))
        channel_key = (guild_id, self._channel_id(message))

        user_remaining = (
            self._user_cooldowns.get(user_key, 0) + USER_COOLDOWN_SECONDS - now
        )
        channel_remaining = (
            self._channel_cooldowns.get(channel_key, 0) + CHANNEL_COOLDOWN_SECONDS - now
        )
        remaining = max(user_remaining, channel_remaining, 0)
        if remaining > 0:
            return math.ceil(remaining)

        self._user_cooldowns[user_key] = now
        self._channel_cooldowns[channel_key] = now
        return 0

    def build_summary_cache_key(
        self,
        message,
        start,
        end,
        limit,
        authors,
        selection_mode,
        focus,
        preset: str | None = None,
    ):
        return (
            self._guild_id(message),
            self._channel_id(message),
            start.isoformat() if start else None,
            None if start is None else end.isoformat() if end else None,
            int(limit),
            tuple(sorted(str(author) for author in (authors or []))),
            selection_mode,
            focus or None,
            preset or "catchup",
        )

    def get_cached_summary(self, key, now: float | None = None):
        now = time.monotonic() if now is None else now
        cached = self._summary_cache.get(key)
        if not cached:
            return None
        expires_at, content = cached
        if expires_at <= now:
            self._summary_cache.pop(key, None)
            return None
        return content

    def set_cached_summary(self, key, content: str, now: float | None = None):
        now = time.monotonic() if now is None else now
        self._summary_cache[key] = (now + SUMMARY_CACHE_TTL_SECONDS, content)

    async def handle_summary_request(self, request: SummaryRequest, responder) -> SummaryResult:
        try:
            await resolve_summary_target_channel(request, self.bot)
        except SummaryRequestError as e:
            logging.info(
                "Summary target blocked: guild_id=%s invocation_channel_id=%s target_channel_id=%s reason=%s.",
                request.guild_id,
                request.invocation_channel_id,
                request.channel_id,
                type(e).__name__,
            )
            await responder.edit_initial(e.user_message)
            return SummaryResult(status="wrong_channel", response_text=e.user_message)

        summary_settings = await load_summary_settings(request.guild_id)
        permission_error = validate_summary_config_access(request, summary_settings)
        if permission_error:
            logging.info(
                "Summary access blocked: guild_id=%s channel_id=%s user_id=%s.",
                request.guild_id,
                request.channel_id,
                request.user_id,
            )
            await responder.edit_initial(permission_error)
            status = "setup_required" if permission_error == SETUP_REQUIRED_MESSAGE else "wrong_channel"
            if status == "setup_required":
                await record_ai_request_event(request, status=status)
            return SummaryResult(status=status, response_text=permission_error)

        try:
            intent, authors = await parse_summary_request_intent_and_authors(request)
        except SummaryRequestError as e:
            await responder.edit_initial(e.user_message)
            return SummaryResult(status="error", response_text=e.user_message)
        except Exception as e:
            logging.info("Intent flow error: %s.", type(e).__name__)
            await responder.edit_initial(INTENT_FAILURE_MESSAGE)
            return SummaryResult(status="error", response_text=INTENT_FAILURE_MESSAGE)

        if intent.wrong_channel:
            response = "Je ne peux résumer que les discussions du salon sur lequel je suis appelée."
            await responder.edit_initial(response)
            await record_ai_request_event(request, status="wrong_channel")
            return SummaryResult(status="wrong_channel", response_text=response)

        if not intent.summary:
            response = "Cette fonctionnalité d'IA n'est pas encore disponible."
            await responder.edit_initial(response)
            return SummaryResult(status="not_summary", response_text=response)

        effective_preset = request.preset_override or intent.preset or "catchup"
        usage_today = await load_summary_usage_today(request)
        log_soft_quota_state(summary_settings, usage_today)
        quota_reasons = summary_quota_exceeded_reasons(summary_settings, usage_today)
        if quota_reasons:
            logging.info("Summary hard quota exceeded: %s.", ",".join(quota_reasons))
            await responder.edit_initial(QUOTA_EXCEEDED_MESSAGE)
            await record_ai_request_event(
                request,
                status="quota_exceeded",
                preset=effective_preset,
                error_type=",".join(quota_reasons),
            )
            return SummaryResult(
                status="quota_exceeded",
                response_text=QUOTA_EXCEEDED_MESSAGE,
            )

        cooldown = self.check_and_mark_summary_cooldown(request)
        if cooldown:
            response = f"⏳ Galactia est en cooldown. Réessaie dans {cooldown}s."
            await responder.edit_initial(response)
            await record_ai_request_event(request, status="cooldown", preset=effective_preset)
            return SummaryResult(
                status="cooldown",
                response_text=response,
                cooldown_seconds=cooldown,
            )

        try:
            max_messages = summary_max_messages_from_settings(summary_settings)
            start, end, limit, fallback_notices = await handle_time_range(
                intent,
                timezone_name=summary_settings.get("timezone") or "Europe/Paris",
                max_messages=max_messages,
            )
            scan_limit = summary_scan_limit_from_settings(summary_settings, limit)
            logging.info(
                "Summary config: source=%s guild_id=%s channel_id=%s start=%s end=%s "
                "limit=%d scan_limit=%d authors=%d mode=%s preset=%s focus=%s.",
                request.source,
                request.guild_id,
                request.channel_id,
                start,
                end,
                limit,
                scan_limit,
                len(authors or []),
                intent.selection_mode,
                effective_preset,
                bool(intent.focus),
            )
            cache_key = self.build_summary_cache_key(
                request,
                start,
                end,
                limit,
                authors,
                intent.selection_mode,
                intent.focus,
                effective_preset,
            )
            cached_summary = self.get_cached_summary(cache_key)
            if cached_summary:
                logging.info("Summary cache hit.")
                await send_summary_content(responder, cached_summary)
                await record_ai_request_event(request, status="cache_hit", preset=effective_preset)
                return SummaryResult(
                    status="cache_hit",
                    response_text=cached_summary,
                    summary_text=cached_summary,
                    cache_hit=True,
                )

            fetch_result = await retrieve_messages(
                self.bot,
                request.channel,
                start,
                end,
                limit,
                authors,
                intent.selection_mode,
                scan_limit=scan_limit,
            )
            if not isinstance(fetch_result, FetchMessagesResult):
                fetch_result = FetchMessagesResult(
                    messages=fetch_result,
                    messages_scanned=len(fetch_result),
                    messages_selected=len(fetch_result),
                    messages_ignored=0,
                )

            if not fetch_result.messages:
                source_context = (
                    f" dans {channel_label(request.channel)}"
                    if request.is_cross_channel
                    else ""
                )
                response = (
                    f"Aucun message trouvé{source_context}{format_summary_window(start, end)}. "
                    f"({fetch_result.messages_ignored} ignorés sur "
                    f"{fetch_result.messages_scanned} scannés)."
                )
                await responder.edit_initial(response)
                await record_ai_request_event(
                    request,
                    status="empty",
                    preset=effective_preset,
                    messages_scanned=fetch_result.messages_scanned,
                    messages_selected=fetch_result.messages_selected,
                    messages_ignored=fetch_result.messages_ignored,
                )
                return SummaryResult(
                    status="empty",
                    response_text=response,
                    messages_scanned=fetch_result.messages_scanned,
                    messages_selected=fetch_result.messages_selected,
                    messages_ignored=fetch_result.messages_ignored,
                )

            generation = await generate_summary_result(
                fetch_result.messages,
                intent.focus,
                effective_preset,
                intent.selection_mode,
            )
            summary = generation.text

            response_text = summary
            if not summary.startswith("❌"):
                intro = merge_summary_intro(
                    summary_feedback_line(request, fetch_result),
                    *fallback_notices,
                )
                response_text = f"{intro}\n\n{summary}" if intro else summary

            await send_summary_content(responder, response_text)
            if response_text and not response_text.startswith("❌"):
                self.set_cached_summary(cache_key, response_text)

            await record_ai_request_event(
                request,
                status="sent" if response_text and not response_text.startswith("❌") else "error",
                preset=effective_preset,
                generation=generation,
                messages_scanned=fetch_result.messages_scanned,
                messages_selected=fetch_result.messages_selected,
                messages_ignored=fetch_result.messages_ignored,
                error_type=None
                if response_text and not response_text.startswith("❌")
                else "summary_generation",
            )

            return SummaryResult(
                status="sent" if response_text and not response_text.startswith("❌") else "error",
                response_text=response_text,
                summary_text=summary,
                messages_scanned=fetch_result.messages_scanned,
                messages_selected=fetch_result.messages_selected,
                messages_ignored=fetch_result.messages_ignored,
            )
        except SummaryRequestError as e:
            await responder.edit_initial(e.user_message)
            await record_ai_request_event(
                request,
                status="error",
                preset=effective_preset,
                error_type=type(e).__name__,
            )
            return SummaryResult(status="error", response_text=e.user_message)
        except Exception as e:
            logging.info("Summary flow error: %s.", type(e).__name__)
            response = "Je n’ai pas pu résumer la conversation. Une erreur est survenue."
            await responder.edit_initial(response)
            await record_ai_request_event(
                request,
                status="error",
                preset=effective_preset,
                error_type=type(e).__name__,
            )
            return SummaryResult(status="error", response_text=response)

    async def handle_summary_interaction(
        self,
        interaction: discord.Interaction,
        demande: str,
        preset: str | None = None,
        target_channel: discord.TextChannel | None = None,
    ):
        await interaction.response.defer(thinking=True, ephemeral=False)
        request = SummaryRequest.from_interaction(
            interaction,
            demande,
            self.bot.user,
            preset,
            target_channel,
        )
        return await self.handle_summary_request(
            request,
            InteractionSummaryResponder(interaction),
        )

    @app_commands.command(name="summary", description="Résumer le salon courant ou un salon cible avec Galactia.")
    @app_commands.guild_only()
    @app_commands.describe(
        demande="Ex: les 20 derniers messages, les dramas d'hier, les décisions importantes",
        preset="Style de résumé à appliquer",
        channel="Salon à résumer. Par défaut, le salon courant.",
    )
    @app_commands.choices(
        preset=[
            app_commands.Choice(name="catchup", value="catchup"),
            app_commands.Choice(name="decisions", value="decisions"),
            app_commands.Choice(name="actions", value="actions"),
            app_commands.Choice(name="raid", value="raid"),
            app_commands.Choice(name="drama", value="drama"),
            app_commands.Choice(name="funny", value="funny"),
        ]
    )
    async def summary(
        self,
        interaction: discord.Interaction,
        demande: str,
        preset: app_commands.Choice[str] | None = None,
        channel: discord.TextChannel | None = None,
    ):
        await self.handle_summary_interaction(
            interaction,
            demande,
            preset.value if preset else None,
            channel,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user:
            return

        if is_direct_bot_mention(message, self.bot.user):
            logging.info(
                "Mention received: guild_id=%s channel_id=%s author_id=%s len=%d.",
                self._guild_id(message),
                self._channel_id(message),
                self._author_id(message),
                len(message.content or ""),
            )

            thinking = await message.channel.send(
                "⏳ Galactia réfléchit...",
                allowed_mentions=AI_ALLOWED_MENTIONS,
            )
            await self.handle_summary_request(
                SummaryRequest.from_message(message, self.bot.user),
                MessageSummaryResponder(thinking, message.channel),
            )
            return

        await self.bot.process_commands(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(AICog(bot))
