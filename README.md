# Relay

A general-purpose AI chat app powered by Claude. Ask questions, draft writing, plan things or get help with code, and Relay streams the answer back as it is written.

It is built to be run and observed like a production service, not just a chat demo:

- **Streaming answers** from Claude over Server-Sent Events, with a Stop button.
- **Fallback model**: if the primary model is overloaded, rate limited or unreachable, the request is retried once on a cheaper model.
- **Cost and latency on every answer**: model, tokens in and out, cost in USD, time to first token and total time.
- **Prometheus metrics** at `/metrics` for HTTP traffic and LLM usage (tokens, spend, errors by reason, fallbacks, time to first token).
- **Guardrails**: per-client rate limit, max tokens per answer, message and conversation size limits.
- **Kubernetes-ready probes**: `/healthz` (process up) and `/readyz` (fails if no API key is set).
- **JSON logs** with a request ID, ready for Loki.
- **Mock mode** for running without an API key.

## How it fits together

```
Browser ──► frontend (nginx :8080) ──/api/──► backend (FastAPI :8000) ──► Claude API
             static UI, security headers         streaming, fallback,      primary + fallback
                                                  rate limit, /metrics
```

| Path | What it is |
|---|---|
| `app/backend/relay/main.py` | API routes, request logging and metrics middleware |
| `app/backend/relay/llm.py` | Claude streaming, error classification, fallback, cost accounting |
| `app/backend/relay/metrics.py` | Prometheus metric definitions |
| `app/backend/relay/config.py` | Settings from environment variables, model prices |
| `app/backend/tests/` | API, fallback, rate limit and metrics tests |
| `app/frontend/public/` | Web UI (plain HTML, CSS and JS, no build step) |
| `app/frontend/nginx/` | nginx config: serves the UI, proxies `/api/` to the backend |

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Liveness |
| `GET` | `/readyz` | Readiness: 503 if no API key and mock mode is off |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/api/info` | Models, limits and version (used by the UI) |
| `POST` | `/api/chat` | `{"messages":[{"role":"user","content":"..."}]}`, streamed as `delta`, then `done` or `error` events |

## Run it with Docker

```bash
cp .env.example .env          # add your ANTHROPIC_API_KEY, or set RELAY_MOCK_LLM=true

docker build -t relay-backend:dev app/backend
docker build -t relay-frontend:dev app/frontend

docker network create relay
docker run -d --name backend  --network relay --env-file .env relay-backend:dev
docker run -d --name frontend --network relay -p 8080:8080 relay-frontend:dev
```

Open http://localhost:8080. Clean up with `docker rm -f frontend backend && docker network rm relay`.

## Run the backend tests

```bash
cd app/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest          # 18 tests
ruff check .
```

## Configuration

All settings are environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Claude API key |
| `RELAY_MOCK_LLM` | `false` | Canned answers, no key needed |
| `RELAY_PRIMARY_MODEL` | `claude-sonnet-5-5` | First model tried |
| `RELAY_FALLBACK_MODEL` | `claude-haiku-5-5` | Used once on a retryable error; empty to disable |
| `RELAY_MAX_TOKENS` | `1024` | Max answer length |
| `RELAY_REQUEST_TIMEOUT_SECONDS` | `60` | Timeout per model call |
| `RELAY_RATE_LIMIT_PER_MINUTE` | `20` | Requests per client per minute |
| `RELAY_ENVIRONMENT` | `local` | Shown in the UI and logs |
| `RELAY_LOG_LEVEL` | `INFO` | Log level |
| `RELAY_PRICES` | built in | JSON of USD prices per million tokens, per model |

Built-in prices follow Anthropic's published list prices; update `RELAY_PRICES` if they change.

## Roadmap

This is Stage 1 of a larger project: deploy it on Kubernetes, add CI/CD, monitoring and an AI cost dashboard, RAG over runbooks, evals in CI, and an AI SRE agent that investigates alerts.
