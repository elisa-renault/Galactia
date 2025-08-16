import logging
from typing import Iterable, Optional

import tiktoken


MAX_DISCORD = 2000


# Reuse tokenizer instance to avoid repeated initialization
ENCODING = tiktoken.get_encoding("cl100k_base")


def fit_for_discord(s: str, hard_limit: int = MAX_DISCORD, target: int = 1900) -> str:
    """Trim ``s`` to safely fit under Discord limits."""
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
    """Split text into chunks ≤ size (margin vs 2000)."""
    if not s:
        return [""]
    return [s[i : i + size] for i in range(0, len(s), size)]


def estimate_token_count(text: str) -> int:
    """Return the number of tokens in ``text`` using OpenAI's tokenizer."""
    return len(ENCODING.encode(text))


async def fetch_valid_messages(
    bot,
    channel,
    start=None,
    end=None,
    limit: Optional[int] = None,
    authors=None,
    sort_ascending: bool = False,
):
    def is_author_allowed(author_display_name, author_id, authors_list):
        if not authors_list:
            return True
        normalized_list = [str(a).strip() for a in authors_list]
        match = (
            author_display_name.strip() in normalized_list
            or str(author_id) in normalized_list
        )
        return match

    raw_limit = limit if limit is not None else 1000
    history = channel.history(
        limit=min(limit or raw_limit, raw_limit), after=start, before=end
    )
    messages = []
    async for msg in history:
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

    logging.info(f"✅ Valid messages kept: {len(messages)}")

    messages.sort(key=lambda m: m.created_at, reverse=not sort_ascending)
    return messages


async def generate_summary(messages, create_chat_completion, focus: Optional[str] = None):
    try:
        if not messages:
            return "Aucun message pertinent à résumer."

        messages.sort(key=lambda m: m.created_at)

        lines = [
            f"[{msg.created_at.strftime('%d/%m/%Y %H:%M')}] {msg.author.display_name} : {msg.content}"
            for msg in messages
        ]

        token_limit = 12000

        instructions = [
            "Tu es Galactia, un assistant IA pour la guilde Les Galactiques.",
            "Tu dois générer un résumé clair des messages reçus.",
            "Ton résumé peut être mis en forme avec du markdown pour une meilleure lisibilité.",
            "⚠️ Le texte FINAL doit faire AU MAXIMUM 1200 caractères, mise en forme et espaces compris.",
            "N'invente jamais de contenu. Résume seulement ce qui est présent.",
        ]
        if focus:
            instructions.append(f"Concentre-toi uniquement sur : {focus}.")

        base_prompt = "Résume ces messages :\n"
        total_tokens = estimate_token_count(" ".join(instructions))
        total_tokens += estimate_token_count(base_prompt)

        selected_lines = []

        for line in lines:
            tokens = estimate_token_count(line + "\n")
            if total_tokens + tokens > token_limit:
                break
            selected_lines.append(line)
            total_tokens += tokens

        messages_text = "\n".join(selected_lines)
        logging.info(f"📏 Tokens sent to GPT: {total_tokens}")
        logging.info(f"🧾 Total lines kept: {len(selected_lines)}")
        if selected_lines:
            logging.info(f"🔸 First line: {selected_lines[0][:100]}...")
            logging.info(f"🔸 Last line : {selected_lines[-1][:100]}...")
        else:
            logging.info("⚠️ No lines kept for summary (0 tokens)")

        logging.info("🧠 Full prompt to GPT (system + user).")
        logging.info("---- SYSTEM ----")
        logging.info(instructions)
        logging.info("---- USER ----")
        logging.info(messages_text[:2000])
        if len(messages_text) > 2000:
            logging.info("📎 (user content truncated in logs, >2000 chars)")

        resp = await create_chat_completion(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": " ".join(instructions)},
                {"role": "user", "content": f"Résume ces messages :\n{messages_text}"},
            ],
        )
        raw_summary = (resp.choices[0].message.content or "").strip()

        safe_summary = fit_for_discord(raw_summary, hard_limit=MAX_DISCORD, target=1900)
        return safe_summary

    except Exception as e:
        return f"❌ Résumé échoué : {str(e)}"
