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
SUMMARY_OPENAI_TIMEOUT_SECONDS = 90
SINGLE_PASS_TOKEN_LIMIT = 12000
MAP_CHUNK_TOKEN_LIMIT = 9000


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
    prompt_version: str = "summary_single.v2"


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
    prompt_version: str = "summary_single.v2",
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


def _format_message_line(index: int, msg) -> tuple[str, str, str | None]:
    source_id = f"S{index}"
    timestamp = msg.created_at.strftime("%d/%m/%Y %H:%M")
    author = getattr(getattr(msg, "author", None), "display_name", "Utilisateur")
    content = str(getattr(msg, "content", "") or "").replace("\n", " ").strip()
    jump_url = getattr(msg, "jump_url", None)
    return f"[{source_id}] [{timestamp}] {author} : {content}", source_id, jump_url


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


def _append_sources(summary: str, sources: dict[str, str | None]) -> str:
    cited_ids = []
    for match in re.findall(r"\[S(\d+)\]", summary or ""):
        source_id = f"S{match}"
        if source_id in sources and source_id not in cited_ids and sources[source_id]:
            cited_ids.append(source_id)
        if len(cited_ids) >= 8:
            break
    if not cited_ids:
        return summary
    lines = ["", "**Sources**"]
    lines.extend(f"- [{source_id}]({sources[source_id]})" for source_id in cited_ids)
    return summary.rstrip() + "\n" + "\n".join(lines)


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
        if bot.user in msg.mentions:
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
    return_result: bool = False,
):
    def finish(result: SummaryGenerationResult):
        return result if return_result else result.text

    try:
        if not messages:
            return finish(SummaryGenerationResult(text="Aucun message pertinent à résumer."))

        messages.sort(key=lambda m: m.created_at)

        sources: dict[str, str | None] = {}
        lines = []
        for index, msg in enumerate(messages, start=1):
            line, source_id, jump_url = _format_message_line(index, msg)
            lines.append(line)
            sources[source_id] = jump_url

        instructions = render_prompt(
            "summary_single.v2.md",
            focus=focus or "aucun focus spécifique",
            preset=preset or "catchup",
        )

        base_prompt = "Résume ces messages Discord chronologiques :\n"
        total_tokens = estimate_token_count(instructions) + estimate_token_count(base_prompt)
        selected_lines = []

        for line in lines:
            tokens = estimate_token_count(line + "\n")
            if total_tokens + tokens > SINGLE_PASS_TOKEN_LIMIT:
                break
            selected_lines.append(line)
            total_tokens += tokens

        messages_text = "\n".join(selected_lines)
        logging.info(
            "Summary prompt prepared: tokens=%d lines=%d input_chars=%d preset=%s.",
            total_tokens,
            len(selected_lines),
            len(messages_text),
            preset,
        )
        if not selected_lines:
            logging.info("No lines kept for summary.")

        should_map_reduce = len(messages) > 500 or len(selected_lines) < len(lines)
        if should_map_reduce:
            map_instructions = render_prompt(
                "summary_map.v1.md",
                focus=focus or "aucun focus spécifique",
                preset=preset or "catchup",
            )
            reduce_instructions = render_prompt(
                "summary_reduce.v1.md",
                focus=focus or "aucun focus spécifique",
                preset=preset or "catchup",
            )
            chunks = _chunk_lines_by_tokens(lines, MAP_CHUNK_TOKEN_LIMIT)
            partials = []
            stats = []
            logging.info(
                "Summary map-reduce enabled: messages=%d chunks=%d preset=%s.",
                len(messages),
                len(chunks),
                preset,
            )
            for chunk_number, chunk in enumerate(chunks, start=1):
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
                    timeout=SUMMARY_OPENAI_TIMEOUT_SECONDS,
                    _overall_timeout=SUMMARY_OPENAI_TIMEOUT_SECONDS + 5,
                )
                partials.append((resp.choices[0].message.content or "").strip())
                stats.append(_response_stats(resp))

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
                timeout=SUMMARY_OPENAI_TIMEOUT_SECONDS,
                _overall_timeout=SUMMARY_OPENAI_TIMEOUT_SECONDS + 5,
            )
            raw_summary = (resp.choices[0].message.content or "").strip()
            raw_summary = _append_sources(raw_summary or "Aucun résumé généré.", sources)
            result = _merge_stats(
                *stats,
                _response_stats(resp),
                chunks_processed=len(chunks),
                prompt_version="summary_map.v1+summary_reduce.v1",
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
        raw_summary = _append_sources(raw_summary or "Aucun résumé généré.", sources)
        result = _merge_stats(
            _response_stats(resp),
            chunks_processed=1,
            prompt_version="summary_single.v2",
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
