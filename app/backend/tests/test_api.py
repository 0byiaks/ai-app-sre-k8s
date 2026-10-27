from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

import pytest
from fastapi.testclient import TestClient

from relay.config import Settings
from relay.llm import ChatMessage, LLMError, LLMService, MockProvider, Usage
from relay.main import create_app
from relay.ratelimit import SlidingWindowLimiter


def make_client(**overrides) -> TestClient:
    settings = Settings(_env_file=None, **{"mock_llm": True, **overrides})
    llm = LLMService(settings, provider=MockProvider(delay=0)) if settings.mock_llm else None
    return TestClient(create_app(settings, llm))


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def ask(client: TestClient, text: str = "Why is my pod in CrashLoopBackOff?"):
    return client.post("/api/chat", json={"messages": [{"role": "user", "content": text}]})


# --- probes and info -------------------------------------------------------


def test_healthz():
    assert make_client().get("/healthz").json() == {"status": "ok"}


def test_ready_in_mock_mode():
    assert make_client().get("/readyz").status_code == 200


def test_not_ready_without_key():
    client = make_client(mock_llm=False, anthropic_api_key=None)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.json()["reason"]


def test_info_reports_models():
    data = make_client().get("/api/info").json()
    assert data["mock_llm"] is True and data["models"] == ["mock"]


def test_live_model_chain():
    settings = Settings(_env_file=None, anthropic_api_key="sk-test", primary_model="a", fallback_model="b")
    assert LLMService(settings, provider=MockProvider()).models() == ["a", "b"]


# --- chat ------------------------------------------------------------------


def test_chat_streams_deltas_then_done():
    resp = ask(make_client())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds[-1] == "done" and set(kinds[:-1]) == {"delta"}
    text = "".join(d["text"] for e, d in events if e == "delta")
    assert text == MockProvider.REPLY
    done = events[-1][1]
    assert done["model"] == "mock" and done["fallback_used"] is False
    assert done["output_tokens"] > 0


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": []},
        {"messages": [{"role": "system", "content": "hi"}]},
        {"messages": [{"role": "user", "content": ""}]},
        {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]},
    ],
)
def test_chat_rejects_bad_input(payload):
    assert make_client().post("/api/chat", json=payload).status_code == 422


def test_chat_rejects_long_message():
    assert ask(make_client(max_message_chars=10), "x" * 11).status_code == 422


def test_rate_limit_returns_429():
    client = make_client(rate_limit_per_minute=2)
    assert ask(client).status_code == 200
    assert ask(client).status_code == 200
    resp = ask(client)
    assert resp.status_code == 429 and "Retry-After" in resp.headers


def test_not_configured_returns_error_event():
    client = make_client(mock_llm=False, anthropic_api_key=None)
    events = parse_sse(ask(client).text)
    assert events == [("error", {"reason": "not_configured", "message": events[0][1]["message"]})]


# --- fallback --------------------------------------------------------------


class ScriptedProvider:
    """Fails for the models listed in `failures`, answers for the rest."""

    def __init__(self, failures: dict[str, LLMError]) -> None:
        self.failures = failures
        self.calls: list[str] = []

    async def stream(
        self, model: str, system: str, messages: Sequence[ChatMessage], max_tokens: int
    ) -> AsyncIterator[str | Usage]:
        self.calls.append(model)
        if model in self.failures:
            raise self.failures[model]
        yield f"answer from {model}"
        yield Usage(10, 5)


def live_client(provider: ScriptedProvider) -> TestClient:
    settings = Settings(
        _env_file=None,
        anthropic_api_key="sk-test",
        primary_model="claude-sonnet-5-5",
        fallback_model="claude-haiku-5-5",
    )
    return TestClient(create_app(settings, LLMService(settings, provider=provider)))


def test_fallback_on_retryable_error():
    provider = ScriptedProvider({"claude-sonnet-5-5": LLMError("overloaded", "529", True)})
    events = parse_sse(ask(live_client(provider)).text)
    assert provider.calls == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    done = events[-1][1]
    assert done["model"] == "claude-haiku-5-5" and done["fallback_used"] is True
    assert done["cost_usd"] == pytest.approx((10 * 0.10 + 5 * 0.50) / 1e6)


def test_no_fallback_on_auth_error():
    provider = ScriptedProvider({"claude-sonnet-5-5": LLMError("auth", "401", False)})
    events = parse_sse(ask(live_client(provider)).text)
    assert provider.calls == ["claude-sonnet-5-5"]
    assert events[-1][0] == "error" and events[-1][1]["reason"] == "auth"


def test_error_when_all_models_fail():
    err = LLMError("connection", "down", True)
    provider = ScriptedProvider({"claude-sonnet-5-5": err, "claude-haiku-5-5": err})
    events = parse_sse(ask(live_client(provider)).text)
    assert events[-1][0] == "error" and events[-1][1]["reason"] == "connection"


# --- metrics and limiter ---------------------------------------------------


def test_metrics_expose_llm_series():
    client = make_client()
    ask(client)
    body = client.get("/metrics").text
    for name in (
        "relay_http_requests_total",
        "relay_llm_tokens_total",
        "relay_llm_cost_usd_total",
        "relay_llm_time_to_first_token_seconds",
    ):
        assert name in body
    assert 'route="/api/chat"' in body


def test_limiter_window_expires():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60)
    assert limiter.check("ip", now=0)[0] is True
    allowed, retry = limiter.check("ip", now=10)
    assert allowed is False and retry == 50
    assert limiter.check("ip", now=61)[0] is True
