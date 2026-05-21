"""Local dry run for Galactia summary selection without Discord or OpenAI.

Run from the repository root:
    python scripts/test_summary_flow_local.py
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DISCORD_TOKEN", "local-test-discord-token")
os.environ.setdefault("TWITCH_CLIENT_ID", "local-test-twitch-client-id")
os.environ.setdefault("TWITCH_CLIENT_SECRET", "local-test-twitch-client-secret")
os.environ.setdefault("OPENAI_API_KEY", "local-test-openai-api-key")

from galactia.cogs.ai import SummaryIntent, handle_time_range
from galactia.handlers.summary import fetch_valid_messages, generate_summary


class FakeAuthor:
    def __init__(self, author_id, display_name, *, bot=False):
        self.id = author_id
        self.display_name = display_name
        self.name = display_name
        self.global_name = display_name
        self.bot = bot


class FakeMessage:
    def __init__(self, content, author, created_at, *, mentions=None):
        self.content = content
        self.author = author
        self.created_at = created_at
        self.mentions = mentions or []


class FakeChannel:
    def __init__(self, messages):
        self.messages = messages
        self.history_calls = []

    def history(self, **kwargs):
        self.history_calls.append(kwargs)
        after = kwargs.get("after")
        before = kwargs.get("before")
        oldest_first = kwargs.get("oldest_first")
        limit = kwargs.get("limit")

        messages = list(self.messages)
        if after is not None:
            messages = [msg for msg in messages if msg.created_at > after]
        if before is not None:
            messages = [msg for msg in messages if msg.created_at < before]
        messages.sort(key=lambda msg: msg.created_at, reverse=not oldest_first)
        messages = messages[:limit]

        async def iterator():
            for message in messages:
                yield message

        return iterator()


def fake_completion(content):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


async def fake_create_chat_completion(**_params):
    return fake_completion(
        "**Resume**\n"
        "Simulation locale: les 20 derniers messages valides ont ete selectionnes.\n\n"
        "**Points importants**\n"
        "- Aucun appel Discord ou OpenAI n'a ete effectue.\n"
        "- La selection ignore l'age des messages quand seul count_limit est fourni."
    )


def old_dt(day, minute):
    return datetime(2026, 1, day, 10, minute, tzinfo=timezone.utc)


async def main():
    bot_user = FakeAuthor(999, "Galactia", bot=True)
    user = FakeAuthor(1, "Elsia")
    channel = FakeChannel(
        [
            FakeMessage(f"old message {i:02d}", user, old_dt(1, i))
            for i in range(25)
        ]
    )
    bot = SimpleNamespace(user=bot_user)

    intent = SummaryIntent(
        summary=True,
        count_limit=20,
        time_limit=None,
        selection_mode="latest",
    )
    start, end, limit, notices = await handle_time_range(intent)
    messages = await fetch_valid_messages(
        bot,
        channel,
        start=start,
        end=end,
        limit=limit,
        selection_mode=intent.selection_mode,
    )
    summary = await generate_summary(messages, fake_create_chat_completion)

    print("Summary flow local test")
    print(f"start={start}")
    print(f"end={end}")
    print(f"limit={limit}")
    print(f"selection_mode={intent.selection_mode}")
    print(f"fallback_notices={notices}")
    print(f"history_kwargs={channel.history_calls[0]}")
    print("selected_messages=")
    for message in messages:
        print(f"- {message.created_at.isoformat()} {message.content}")
    print("\nfinal_output=")
    print(summary)


if __name__ == "__main__":
    asyncio.run(main())
