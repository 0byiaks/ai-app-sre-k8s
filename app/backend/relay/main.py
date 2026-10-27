"""HTTP API for Relay.

Routes
  GET  /healthz    liveness: the process is up
  GET  /readyz     readiness: an LLM is configured (API key or mock mode)
  GET  /metrics    Prometheus metrics
  GET  /api/info   models and limits, shown in the UI
  POST /api/chat   chat, streamed back as Server-Sent Events
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from . import __version__, metrics
from .config import Settings, get_settings
from .llm import ChatMessage, LLMService
from .logging_setup import configure_logging, request_id_var
from .ratelimit import SlidingWindowLimiter

log = logging.getLogger("relay.api")

QUIET_PATHS = {"/healthz", "/readyz", "/metrics"}


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)


def client_key(request: Request) -> str:
    # nginx in front sets X-Real-IP to the caller's address.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(settings: Settings | None = None, llm: LLMService | None = None) -> FastAPI:
    settings = settings or get_settings()
    llm = llm or LLMService(settings)
    limiter = SlidingWindowLimiter(settings.rate_limit_per_minute)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        configure_logging(settings.log_level)
        log.info(
            "starting",
            extra={
                "version": __version__,
                "environment": settings.environment,
                "models": llm.models(),
                "mock_llm": settings.mock_llm,
                "llm_configured": settings.llm_configured,
            },
        )
        if not settings.llm_configured:
            log.error("no ANTHROPIC_API_KEY set and mock mode is off; readiness will fail")
        yield
        log.info("shutting down")

    app = FastAPI(title="Relay API", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            route = request.scope.get("route")
            path = route.path if route is not None else "unmatched"
            elapsed = time.perf_counter() - started
            metrics.HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
            metrics.HTTP_LATENCY.labels(request.method, path).observe(elapsed)
            if request.url.path not in QUIET_PATHS:
                log.info(
                    "request",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 1),
                    },
                )
            request_id_var.reset(token)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        if settings.llm_configured:
            return JSONResponse({"status": "ready"})
        return JSONResponse(
            {"status": "not_ready", "reason": "ANTHROPIC_API_KEY is not set and mock mode is off"},
            status_code=503,
        )

    @app.get("/metrics")
    async def prometheus_metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/info")
    async def info() -> dict:
        return {
            "name": settings.app_name,
            "version": __version__,
            "environment": settings.environment,
            "mock_llm": settings.mock_llm,
            "models": llm.models(),
            "max_tokens": settings.max_tokens,
            "rate_limit_per_minute": settings.rate_limit_per_minute,
        }

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        allowed, retry_after = limiter.check(client_key(request))
        if not allowed:
            metrics.RATE_LIMITED.inc()
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit reached ({settings.rate_limit_per_minute} requests per minute).",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        if len(body.messages) > settings.max_messages:
            raise HTTPException(422, f"Conversation too long (max {settings.max_messages} messages).")
        if any(len(m.content) > settings.max_message_chars for m in body.messages):
            raise HTTPException(422, f"Message too long (max {settings.max_message_chars} characters).")
        if body.messages[-1].role != "user":
            raise HTTPException(422, "The last message must come from the user.")

        messages = [ChatMessage(m.role, m.content) for m in body.messages]

        async def events() -> AsyncIterator[str]:
            metrics.ACTIVE_STREAMS.inc()
            try:
                async for event, data in llm.stream_chat(messages):
                    yield sse(event, data)
            finally:
                metrics.ACTIVE_STREAMS.dec()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
