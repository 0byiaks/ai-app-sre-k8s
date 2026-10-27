"""Prometheus metrics.

Two groups:
* HTTP metrics: classic service health (rate, errors, duration).
* LLM metrics: what makes an AI app different (tokens, cost, time to first
  token, fallbacks). These are what you watch to keep an AI app fast and affordable.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

from . import __version__

BUILD_INFO = Info("relay_build", "Build information")
BUILD_INFO.info({"version": __version__})

HTTP_REQUESTS = Counter(
    "relay_http_requests_total",
    "HTTP requests handled",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "relay_http_request_duration_seconds",
    "HTTP request duration (for streams: until headers are sent)",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

LLM_REQUESTS = Counter(
    "relay_llm_requests_total",
    "LLM calls by model and outcome",
    ["model", "outcome"],  # outcome: success | error
)
LLM_ERRORS = Counter(
    "relay_llm_errors_total",
    "LLM errors by model and reason",
    # reason: auth, rate_limited, overloaded, timeout, connection, bad_request, server_error, unknown
    ["model", "reason"],
)
LLM_FALLBACKS = Counter(
    "relay_llm_fallbacks_total",
    "Times the fallback model was used",
    ["from_model", "to_model"],
)
LLM_TOKENS = Counter(
    "relay_llm_tokens_total",
    "Tokens consumed",
    ["model", "type"],  # type: input | output
)
LLM_COST = Counter(
    "relay_llm_cost_usd_total",
    "Estimated LLM spend in USD",
    ["model"],
)
LLM_LATENCY = Histogram(
    "relay_llm_request_duration_seconds",
    "Full LLM response time",
    ["model"],
    buckets=(0.5, 1, 2, 4, 8, 15, 30, 60, 120),
)
LLM_TTFT = Histogram(
    "relay_llm_time_to_first_token_seconds",
    "Time until the first streamed token",
    ["model"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 15),
)
ACTIVE_STREAMS = Gauge("relay_active_streams", "Chat responses currently streaming")
RATE_LIMITED = Counter("relay_rate_limited_total", "Requests rejected by the rate limiter")
