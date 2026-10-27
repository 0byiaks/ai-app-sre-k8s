"""LLM access: streaming, error classification, fallback and accounting.

``LLMService.stream_chat`` yields events the API turns into Server-Sent Events:

* ``("delta", {"text": ...})``   a chunk of the answer
* ``("done",  {...summary})``    model, tokens, cost, latency, fallback flag
* ``("error", {...})``           a user-safe error message and reason

Fallback rule: if the primary model fails with a *retryable* error before any
text has been sent, the request is retried once on the fallback model. Errors
that a second model cannot fix (bad API key, bad request) are not retried.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from . import metrics
from .config import Settings
from .prompts import SYSTEM_PROMPT

log = logging.getLogger("relay.llm")


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


class LLMError(Exception):
    """Provider-neutral error with a short reason used in metrics and logs."""

    def __init__(self, reason: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


USER_MESSAGES = {
    "auth": "The AI provider rejected the API key. Check the ANTHROPIC_API_KEY secret.",
    "rate_limited": "The AI provider is rate limiting requests. Try again shortly.",
    "overloaded": "The AI provider is overloaded right now. Try again shortly.",
    "timeout": "The AI provider took too long to respond.",
    "connection": "Could not reach the AI provider.",
    "bad_request": "The request was rejected by the AI provider.",
    "not_configured": "No API key is configured and mock mode is off.",
}


class Provider(Protocol):
    def stream(
        self, model: str, system: str, messages: Sequence[ChatMessage], max_tokens: int
    ) -> AsyncIterator[str | Usage]:
        """Yield text chunks, then a final ``Usage``. Raise ``LLMError`` on failure."""
        ...


class AnthropicProvider:
    def __init__(self, api_key: str, timeout: float) -> None:
        import anthropic

        self._anthropic = anthropic
        # max_retries=1: one quick SDK retry, then our own fallback logic takes over.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)

    async def stream(
        self, model: str, system: str, messages: Sequence[ChatMessage], max_tokens: int
    ) -> AsyncIterator[str | Usage]:
        try:
            async with self._client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
                yield Usage(final.usage.input_tokens, final.usage.output_tokens)
        except LLMError:
            raise
        except Exception as exc:  # translate SDK errors into LLMError
            raise self._classify(exc) from exc

    def _classify(self, exc: Exception) -> LLMError:
        a = self._anthropic
        if isinstance(exc, a.AuthenticationError | a.PermissionDeniedError):
            return LLMError("auth", str(exc), retryable=False)
        if isinstance(exc, a.RateLimitError):
            return LLMError("rate_limited", str(exc), retryable=True)
        if isinstance(exc, a.APITimeoutError):
            return LLMError("timeout", str(exc), retryable=True)
        if isinstance(exc, a.APIConnectionError):
            return LLMError("connection", str(exc), retryable=True)
        if isinstance(exc, a.APIStatusError):
            if exc.status_code == 529:
                return LLMError("overloaded", str(exc), retryable=True)
            if exc.status_code >= 500:
                return LLMError("server_error", str(exc), retryable=True)
            if exc.status_code == 404:  # unknown model id: the fallback may still work
                return LLMError("bad_request", str(exc), retryable=True)
            return LLMError("bad_request", str(exc), retryable=False)
        return LLMError("unknown", str(exc), retryable=False)


class MockProvider:
    """Deterministic offline provider for demos, CI and local development."""

    REPLY = (
        "**Mock mode is on**, so this reply comes from Relay itself, not a live model.\n\n"
        "Here is what a real answer can include:\n\n"
        "1. **Plain explanations** in short paragraphs.\n"
        "2. **Lists and steps** when order matters.\n"
        "3. **Code** when you ask for it:\n\n"
        "```python\n"
        "def greet(name: str) -> str:\n"
        '    return f"Hello, {name}!"\n'
        "```\n\n"
        "Set `ANTHROPIC_API_KEY` and turn off mock mode to chat with Claude."
    )

    def __init__(self, delay: float = 0.012) -> None:
        self.delay = delay

    async def stream(
        self, model: str, system: str, messages: Sequence[ChatMessage], max_tokens: int
    ) -> AsyncIterator[str | Usage]:
        words = self.REPLY.split(" ")
        for i, word in enumerate(words):
            await asyncio.sleep(self.delay)
            yield word if i == 0 else " " + word
        prompt_chars = len(system) + sum(len(m.content) for m in messages)
        yield Usage(input_tokens=max(1, prompt_chars // 4), output_tokens=len(self.REPLY) // 4)


class LLMService:
    def __init__(self, settings: Settings, provider: Provider | None = None) -> None:
        self.settings = settings
        if provider is not None:
            self.provider: Provider | None = provider
        elif settings.mock_llm:
            self.provider = MockProvider()
        elif settings.llm_configured:
            self.provider = AnthropicProvider(
                settings.anthropic_api_key.get_secret_value(),  # type: ignore[union-attr]
                settings.request_timeout_seconds,
            )
        else:
            self.provider = None

    def models(self) -> list[str]:
        if self.settings.mock_llm:
            return ["mock"]
        chain = [self.settings.primary_model]
        if self.settings.fallback_model and self.settings.fallback_model != chain[0]:
            chain.append(self.settings.fallback_model)
        return chain

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[tuple[str, dict]]:
        if self.provider is None:
            yield "error", {"reason": "not_configured", "message": USER_MESSAGES["not_configured"]}
            return

        chain = self.models()
        for attempt, model in enumerate(chain):
            started = time.perf_counter()
            first_token_at: float | None = None
            sent_text = False
            usage = Usage()
            try:
                async for item in self.provider.stream(
                    model, SYSTEM_PROMPT, messages, self.settings.max_tokens
                ):
                    if isinstance(item, Usage):
                        usage = item
                        continue
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                        metrics.LLM_TTFT.labels(model).observe(first_token_at - started)
                    sent_text = True
                    yield "delta", {"text": item}
            except LLMError as err:
                metrics.LLM_REQUESTS.labels(model, "error").inc()
                metrics.LLM_ERRORS.labels(model, err.reason).inc()
                has_next = attempt + 1 < len(chain)
                log.warning(
                    "llm call failed",
                    extra={
                        "model": model,
                        "reason": err.reason,
                        "retryable": err.retryable,
                        "will_fallback": err.retryable and has_next and not sent_text,
                    },
                )
                if err.retryable and has_next and not sent_text:
                    metrics.LLM_FALLBACKS.labels(model, chain[attempt + 1]).inc()
                    continue
                yield (
                    "error",
                    {
                        "reason": err.reason,
                        "message": USER_MESSAGES.get(err.reason, "The AI provider returned an error."),
                        "model": model,
                    },
                )
                return

            elapsed = time.perf_counter() - started
            cost = self.settings.cost_usd(model, usage.input_tokens, usage.output_tokens)
            metrics.LLM_REQUESTS.labels(model, "success").inc()
            metrics.LLM_LATENCY.labels(model).observe(elapsed)
            metrics.LLM_TOKENS.labels(model, "input").inc(usage.input_tokens)
            metrics.LLM_TOKENS.labels(model, "output").inc(usage.output_tokens)
            metrics.LLM_COST.labels(model).inc(cost)
            summary = {
                "model": model,
                "fallback_used": attempt > 0,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_usd": round(cost, 8),
                "latency_ms": round(elapsed * 1000),
                "ttft_ms": round((first_token_at - started) * 1000) if first_token_at else None,
            }
            log.info("llm call completed", extra=summary)
            yield "done", summary
            return
