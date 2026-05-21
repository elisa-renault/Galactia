import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

import tiktoken

from galactia.prompts import render_prompt


MAX_DISCORD = 2000
MAX_FETCH_MESSAGES = 2000
MAX_SCAN_MESSAGES = 5000
DEFAULT_FETCH_MESSAGES = 100
SUMMARY_OPENAI_TIMEOUT_SECONDS = 30
SUMMARY_MAP_TIMEOUT_SECONDS = 35
SINGLE_PASS_TOKEN_LIMIT = 7000
MAP_CHUNK_TOKEN_LIMIT = 4500
SUMMARY_MESSAGE_CHAR_LIMIT = 350
SUMMARY_FAST_MESSAGE_CHAR_LIMIT = 80
MAP_REDUCE_MESSAGE_THRESHOLD = 300
MAP_REDUCE_MAX_CHUNKS = 6
MAP_REDUCE_PARALLELISM = 3


@dataclass
class FetchMessagesResult:
    messages: list
    messages_scanned: int
    messages_selected: int
    messages_ignored: int


@dataclass
class SummaryGenerationResult:
    text: str
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 0
    chunks_processed: int = 1
    prompt_version: str = "summary_single.v3"


# Reuse tokenizer instance to avoid repeated initialization.
ENCODING = tiktoken.get_encoding("cl100k_base")


def fit_for_discord(s: str, hard_limit: int = MAX_DISCORD, target: int = 1900) -> str:
    """Trim ``s`` to safely fit under Discord limits as an emergency fallback."""
    if s is None:
        return ""
    if len(s) <= hard_limit:
        return s

    cut = s[:target]
    nl = cut.rfind("\n")

    if nl != -1 and nl >= target - 300:
        cut = cut[:nl]

    cut = cut.rstrip()
    suffix = "\n… (résumé tronqué)"
    if len(cut) + len(suffix) > hard_limit:
        cut = cut[: hard_limit - len(suffix)]
    return cut + suffix


def chunk_text(s: str, size: int = 1900) -> Iterable[str]:
    """Split text into chunks under Discord's 2000-character message limit."""
    if not s:
        return [""]
    return [s[i : i + size] for i in range(0, len(s), size)]


def estimate_token_count(text: str) -> int:
    """Return the number of tokens in ``text`` using OpenAI's tokenizer."""
    return len(ENCODING.encode(text))


def _response_stats(response) -> dict:
    meta = getattr(response, "_galactia_ai_response", None)
    usage = getattr(response, "usage", None)
    if meta is not None:
        return {
            "model": meta.model,
            "prompt_tokens": meta.usage.prompt_tokens,
            "completion_tokens": meta.usage.completion_tokens,
            "total_tokens": meta.usage.total_tokens,
            "latency_ms": meta.latency_ms,
            "attempts": meta.attempts,
        }
    return {
        "model": getattr(response, "model", None),
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "latency_ms": 0,
        "attempts": 1,
    }


def _merge_stats(
    *stats: dict,
    chunks_processed: int = 1,
    prompt_version: str = "summary_single.v3",
) -> SummaryGenerationResult:
    merged = {
        "model": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0,
        "attempts": 0,
    }
    for item in stats:
        if not item:
            continue
        merged["model"] = item.get("model") or merged["model"]
        merged["prompt_tokens"] += int(item.get("prompt_tokens") or 0)
        merged["completion_tokens"] += int(item.get("completion_tokens") or 0)
        merged["total_tokens"] += int(item.get("total_tokens") or 0)
        merged["latency_ms"] += int(item.get("latency_ms") or 0)
        merged["attempts"] += int(item.get("attempts") or 0)
    return SummaryGenerationResult(
        text="",
        model=merged["model"],
        prompt_tokens=merged["prompt_tokens"],
        completion_tokens=merged["completion_tokens"],
        total_tokens=merged["total_tokens"],
        latency_ms=merged["latency_ms"],
        attempts=merged["attempts"],
        chunks_processed=chunks_processed,
        prompt_version=prompt_version,
    )


def _compact_message_content(content: str, limit: int) -> tuple[str, bool]:
    compacted = re.sub(r"\s+", " ", str(content or "")).strip()
    if len(compacted) <= limit:
        return compacted, False
    return compacted[:limit].rstrip() + "…", True


def _format_message_line(msg, *, content_limit: int = SUMMARY_MESSAGE_CHAR_LIMIT) -> tuple[str, bool]:
    timestamp = msg.created_at.strftime("%d/%m/%Y %H:%M")
    author = getattr(getattr(msg, "author", None), "display_name", "Utilisateur")
    content, truncated = _compact_message_content(
        str(getattr(msg, "content", "") or ""),
        content_limit,
    )
    return f"[{timestamp}] {author} : {content}", truncated


def _prepare_message_lines(messages, *, content_limit: int) -> tuple[list[str], int]:
    lines = []
    truncated_messages = 0
    for msg in messages:
        line, truncated = _format_message_line(msg, content_limit=content_limit)
        lines.append(line)
        if truncated:
            truncated_messages += 1
    return lines, truncated_messages


def _prompt_token_count(instructions: str, base_prompt: str, lines: list[str]) -> int:
    return (
        estimate_token_count(instructions)
        + estimate_token_count(base_prompt)
        + estimate_token_count("\n".join(lines))
    )


def _chunk_lines_by_tokens(lines: list[str], token_budget: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for line in lines:
        line_tokens = estimate_token_count(line + "\n")
        if current and current_tokens + line_tokens > token_budget:
            chunks.append("\n".join(current))
            current = []
            current_tokens = 0
        current.append(line)
        current_tokens += line_tokens
    if current:
        chunks.append("\n".join(current))
    return chunks


def _strip_source_artifacts(summary: str) -> str:
    if not summary:
        return summary
    lines = []
    in_sources_section = False
    for line in summary.splitlines():
        stripped = line.strip().lower()
        if stripped in {"**sources**", "sources", "### sources", "## sources"}:
            in_sources_section = True
            continue
        if in_sources_section:
            if line.strip().startswith("**") and "source" not in stripped:
                in_sources_section = False
            else:
                continue
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\s*\[S\d+\]", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _message_mentions_bot(msg, bot_user) -> bool:
    bot_id = getattr(bot_user, "id", None)
    if bot_id is None:
        return False
    bot_id_str = str(bot_id)
    for mentioned_user in getattr(msg, "mentions", []) or []:
        if str(getattr(mentioned_user, "id", "")) == bot_id_str:
            return True
    content = str(getattr(msg, "content", "") or "")
    return f"<@{bot_id_str}>" in content or f"<@!{bot_id_str}>" in content


def _looks_like_summary_invocation(content: str) -> bool:
    normalized = (content or "").strip().lower()
    return normalized.startswith("/summary") or normalized.startswith("/galactia summary")


def normalize_fetch_limit(limit: Optional[int]) -> int:
    if limit is None:
        return DEFAULT_FETCH_MESSAGES
    if isinstance(limit, bool) or int(limit) < 1:
        raise ValueError("limit must be between 1 and 2000")
    return min(int(limit), MAX_FETCH_MESSAGES)


def normalize_scan_limit(result_limit: int, scan_limit: Optional[int]) -> int:
    if scan_limit is None:
        return min(result_limit * 10, MAX_SCAN_MESSAGES)
    if isinstance(scan_limit, bool) or int(scan_limit) < 1:
        raise ValueError("scan_limit must be between 1 and 5000")
    return min(max(int(scan_limit), result_limit), MAX_SCAN_MESSAGES)


async def fetch_valid_messages(
    bot,
    channel,
    start=None,
    end=None,
    limit: Optional[int] = None,
    authors=None,
    selection_mode: Literal["latest", "earliest"] = "latest",
    scan_limit: Optional[int] = None,
    include_stats: bool = False,
):
    def is_author_allowed(author_display_name, author_id, authors_list):
        if not authors_list:
            return True
        normalized_list = [str(a).strip() for a in authors_list]
        return (
            author_display_name.strip() in normalized_list
            or str(author_id) in normalized_list
        )

    if selection_mode not in {"latest", "earliest"}:
        raise ValueError("selection_mode must be 'latest' or 'earliest'")

    result_limit = normalize_fetch_limit(limit)
    max_scanned = normalize_scan_limit(result_limit, scan_limit)
    oldest_first = selection_mode == "earliest"
    history = channel.history(
        limit=max_scanned,
        after=start,
        before=end,
        oldest_first=oldest_first,
    )
    messages = []
    messages_scanned = 0
    async for msg in history:
        messages_scanned += 1
        if not msg.content:
            continue
        if msg.author.bot:
            continue
        if _message_mentions_bot(msg, bot.user):
            continue
        if _looks_like_summary_invocation(msg.content):
            continue
        if authors and not is_author_allowed(
            msg.author.display_name, str(msg.author.id), authors
        ):
            continue
        messages.append(msg)
        if len(messages) >= result_limit:
            break

    logging.info(
        "Valid messages kept: messages_scanned=%d messages_selected=%d result_limit=%d scan_limit=%d mode=%s.",
        messages_scanned,
        len(messages),
        result_limit,
        max_scanned,
        selection_mode,
    )

    messages.sort(key=lambda m: m.created_at)
    if include_stats:
        return FetchMessagesResult(
            messages=messages,
            messages_scanned=messages_scanned,
            messages_selected=len(messages),
            messages_ignored=max(messages_scanned - len(messages), 0),
        )
    return messages


async def generate_summary(
    messages,
    create_chat_completion,
    focus: Optional[str] = None,
    *,
    preset: str = "catchup",
    selection_mode: Literal["latest", "earliest"] = "latest",
    return_result: bool = False,
):
    def finish(result: SummaryGenerationResult):
        return result if return_result else result.text

    try:
        if not messages:
            return finish(SummaryGenerationResult(text="Aucun message pertinent à résumer."))

        messages.sort(key=lambda m: m.created_at)

        instructions = render_prompt(
            "summary_single.v3.md",
            focus=focus or "aucun focus spécifique",
            preset=preset or "catchup",
        )

        base_prompt = "Résume ces messages Discord chronologiques :\n"
        lines, truncated_messages = _prepare_message_lines(
            messages,
            content_limit=SUMMARY_MESSAGE_CHAR_LIMIT,
        )
        total_tokens = _prompt_token_count(instructions, base_prompt, lines)

        if len(messages) <= MAP_REDUCE_MESSAGE_THRESHOLD and total_tokens > SINGLE_PASS_TOKEN_LIMIT:
            for fast_limit in (SUMMARY_FAST_MESSAGE_CHAR_LIMIT, 60, 40, 24):
                fast_lines, fast_truncated = _prepare_message_lines(
                    messages,
                    content_limit=fast_limit,
                )
                fast_tokens = _prompt_token_count(instructions, base_prompt, fast_lines)
                if fast_tokens < total_tokens:
                    lines = fast_lines
                    total_tokens = fast_tokens
                    truncated_messages = fast_truncated
                if total_tokens <= SINGLE_PASS_TOKEN_LIMIT:
                    break

        messages_text = "\n".join(lines)
        should_map_reduce = len(messages) > MAP_REDUCE_MESSAGE_THRESHOLD or total_tokens > SINGLE_PASS_TOKEN_LIMIT
        logging.info(
            "Summary prompt prepared: generation_strategy=%s lines_total=%d lines_used=%d "
            "input_tokens=%d input_chars=%d truncated_messages=%d preset=%s.",
            "map_reduce" if should_map_reduce else "single",
            len(messages),
            len(lines),
            total_tokens,
            len(messages_text),
            truncated_messages,
            preset,
        )
        if not lines:
            logging.info("No lines kept for summary.")

        if should_map_reduce:
            map_instructions = render_prompt(
                "summary_map.v2.md",
                focus=focus or "aucun focus spécifique",
                preset=preset or "catchup",
            )
            reduce_instructions = render_prompt(
                "summary_reduce.v2.md",
                focus=focus or "aucun focus spécifique",
                preset=preset or "catchup",
            )
            chunks = _chunk_lines_by_tokens(lines, MAP_CHUNK_TOKEN_LIMIT)
            if len(chunks) > MAP_REDUCE_MAX_CHUNKS:
                if selection_mode == "earliest":
                    chunks = chunks[:MAP_REDUCE_MAX_CHUNKS]
                else:
                    chunks = chunks[-MAP_REDUCE_MAX_CHUNKS:]
            logging.info(
                "Summary map-reduce enabled: messages=%d chunks=%d parallelism=%d "
                "truncated_messages=%d preset=%s.",
                len(messages),
                len(chunks),
                MAP_REDUCE_PARALLELISM,
                truncated_messages,
                preset,
            )

            semaphore = asyncio.Semaphore(MAP_REDUCE_PARALLELISM)

            async def summarize_chunk(chunk_number: int, chunk: str):
                async with semaphore:
                    resp = await create_chat_completion(
                        model="gpt-5-mini",
                        messages=[
                            {"role": "system", "content": map_instructions},
                            {
                                "role": "user",
                                "content": (
                                    f"Lot {chunk_number}/{len(chunks)} de messages "
                                    f"Discord chronologiques :\n{chunk}"
                                ),
                            },
                        ],
                        timeout=SUMMARY_MAP_TIMEOUT_SECONDS,
                        _overall_timeout=SUMMARY_MAP_TIMEOUT_SECONDS + 5,
                    )
                    partial = _strip_source_artifacts((resp.choices[0].message.content or "").strip())
                    return partial, _response_stats(resp)

            chunk_results = await asyncio.gather(
                *[
                    summarize_chunk(chunk_number, chunk)
                    for chunk_number, chunk in enumerate(chunks, start=1)
                ]
            )
            partials = [partial for partial, _stats in chunk_results]
            stats = [_stats for _partial, _stats in chunk_results]

            reduce_input = "\n\n".join(
                f"Lot {index}:\n{partial}"
                for index, partial in enumerate(partials, start=1)
                if partial
            )
            resp = await create_chat_completion(
                model="gpt-5-mini",
                messages=[
                    {"role": "system", "content": reduce_instructions},
                    {"role": "user", "content": f"Résumés partiels :\n{reduce_input}"},
                ],
                timeout=SUMMARY_MAP_TIMEOUT_SECONDS,
                _overall_timeout=SUMMARY_MAP_TIMEOUT_SECONDS + 5,
            )
            raw_summary = (resp.choices[0].message.content or "").strip()
            raw_summary = _strip_source_artifacts(raw_summary or "Aucun résumé généré.")
            result = _merge_stats(
                *stats,
                _response_stats(resp),
                chunks_processed=len(chunks),
                prompt_version="summary_map.v2+summary_reduce.v2",
            )
            result.text = raw_summary
            return finish(result)

        resp = await create_chat_completion(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": f"Résume ces messages Discord chronologiques :\n{messages_text}"},
            ],
            timeout=SUMMARY_OPENAI_TIMEOUT_SECONDS,
            _overall_timeout=SUMMARY_OPENAI_TIMEOUT_SECONDS + 5,
        )
        raw_summary = (resp.choices[0].message.content or "").strip()
        raw_summary = _strip_source_artifacts(raw_summary or "Aucun résumé généré.")
        result = _merge_stats(
            _response_stats(resp),
            chunks_processed=1,
            prompt_version="summary_single.v3",
        )
        result.text = raw_summary
        return finish(result)

    except asyncio.TimeoutError:
        logging.info("Summary generation timed out.")
        return finish(SummaryGenerationResult(text=(
            "❌ Résumé échoué : la génération a dépassé le délai autorisé. "
            "Réessaie avec une période plus courte ou un nombre de messages."
        )))
    except Exception as e:
        logging.exception("Summary generation failed.")
        detail = str(e).strip() or type(e).__name__
        return finish(SummaryGenerationResult(text=f"❌ Résumé échoué : {detail}"))
