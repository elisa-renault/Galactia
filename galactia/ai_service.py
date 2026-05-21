from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from galactia.settings import settings


@dataclass
class AIUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class AIResponse:
    content: str
    model: str | None
    usage: AIUsage
    latency_ms: int
    attempts: int
    raw_response: Any | None = None


class AIService:
    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
    ):
        self.client = client or AsyncOpenAI(api_key=settings.openai_api_key)
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    async def chat_completion(self, **params) -> AIResponse:
        timeout = params.pop("_overall_timeout", params.get("timeout", 25))
        started = time.perf_counter()
        attempts = 0
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            try:
                raw = await asyncio.wait_for(
                    self.client.chat.completions.create(**params),
                    timeout=float(timeout),
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                content = ""
                if getattr(raw, "choices", None):
                    content = (raw.choices[0].message.content or "").strip()
                usage = getattr(raw, "usage", None)
                return AIResponse(
                    content=content,
                    model=getattr(raw, "model", params.get("model")),
                    usage=AIUsage(
                        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
                    ),
                    latency_ms=latency_ms,
                    attempts=attempts,
                    raw_response=raw,
                )
            except (RateLimitError, APIConnectionError, APITimeoutError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_seconds * (attempt + 1))
            except APIStatusError as exc:
                status_code = int(getattr(exc, "status_code", 0) or 0)
                if status_code != 429 and status_code < 500:
                    raise
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_seconds * (attempt + 1))
            except Exception:
                raise

        logging.info(
            "OpenAI chat completion failed after %d attempts: %s.",
            attempts,
            type(last_exc).__name__ if last_exc else "unknown",
        )
        if last_exc:
            raise last_exc
        raise RuntimeError("OpenAI chat completion failed")

    async def structured_intent(self, **params) -> AIResponse:
        return await self.chat_completion(**params)
