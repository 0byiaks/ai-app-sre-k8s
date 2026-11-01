"""Webhook server: Alertmanager sends alerts here, the agent investigates, the report goes to Slack.

Routes
  POST /alerts    Alertmanager webhook (the standard Alertmanager JSON format)
  GET  /healthz   liveness
  GET  /metrics   Prometheus metrics about the agent itself

Guardrails
  - Only firing alerts whose name matches ALERT_PREFIX are investigated.
  - Each alert episode is investigated once. An episode is one alert (fingerprint) from the moment it
    starts firing: Alertmanager's repeat notifications are skipped, but if the alert resolves and fires
    again later, that is a new episode and is investigated.
  - One incident, one investigation: if another alert in the same namespace was investigated in the
    last INCIDENT_WINDOW_MINUTES, the new alert is linked to it in Slack instead of investigated again.
  - One investigation at a time, and at most MAX_PER_HOUR per hour, to cap cost.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .agent import MODEL, investigate

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sre_agent.server")

ALERT_PREFIX = os.getenv("ALERT_PREFIX", "Relay")
INCIDENT_WINDOW_MINUTES = int(os.getenv("INCIDENT_WINDOW_MINUTES", "5"))
DEFAULT_NAMESPACE = os.getenv("TARGET_NAMESPACE", "relay")
MAX_PER_HOUR = int(os.getenv("MAX_PER_HOUR", "10"))

INVESTIGATIONS = Counter("sre_agent_investigations_total", "Investigations run", ["alertname", "outcome"])
SKIPPED = Counter("sre_agent_alerts_skipped_total", "Alerts not investigated", ["reason"])
COST = Counter("sre_agent_cost_usd_total", "Estimated LLM spend of investigations in USD")
DURATION = Histogram(
    "sre_agent_investigation_seconds", "Time to finish an investigation", buckets=(5, 10, 20, 30, 60, 120)
)

_lock = threading.Lock()  # one investigation at a time
_episodes: dict[str, float] = {}  # "fingerprint|startsAt" -> when we investigated that episode
_namespaces: dict[str, tuple[float, str]] = {}  # namespace -> (when, alertname) of the last investigation
_hour: deque[float] = deque()  # start times of investigations in the last hour


def slack_url() -> str | None:
    """The Slack webhook URL, from a mounted Secret file (in the cluster) or an env var (on a laptop)."""
    path = os.getenv("SLACK_WEBHOOK_FILE")
    if path and Path(path).is_file():
        return Path(path).read_text().strip()
    return os.getenv("SLACK_WEBHOOK_URL")


def post_to_slack(text: str) -> None:
    url = slack_url()
    if not url:
        log.warning("no Slack webhook configured; report only logged:\n%s", text)
        return
    try:
        httpx.post(url, json={"text": text}, timeout=10).raise_for_status()
    except httpx.HTTPError as exc:
        log.error("posting to Slack failed: %s", exc)


def should_investigate(alert: dict, now: float) -> tuple[str | None, str | None]:
    """Return (None, None) to investigate, or (reason to skip, related alertname if any)."""
    labels = alert.get("labels", {})
    name = labels.get("alertname", "")
    if alert.get("status") != "firing":
        return "resolved", None
    if not name.startswith(ALERT_PREFIX):
        return "not_ours", None

    for key, seen in list(_episodes.items()):  # forget episodes older than a day
        if now - seen > 86400:
            del _episodes[key]
    episode = f"{alert.get('fingerprint') or name}|{alert.get('startsAt', '')}"
    if episode in _episodes:
        return "already_investigated", None

    namespace = labels.get("namespace", DEFAULT_NAMESPACE)
    last = _namespaces.get(namespace)
    if last and now - last[0] < INCIDENT_WINDOW_MINUTES * 60:
        _episodes[episode] = now  # count it as handled, so repeats are skipped too
        return "same_incident", last[1]

    while _hour and now - _hour[0] > 3600:
        _hour.popleft()
    if len(_hour) >= MAX_PER_HOUR:
        return "hourly_limit", None

    _episodes[episode] = now
    _namespaces[namespace] = (now, name)
    _hour.append(now)
    return None, None


def run_investigation(alert: dict) -> None:
    name = alert.get("labels", {}).get("alertname", "unknown")
    with _lock:
        log.info("investigating %s", name)
        try:
            result = investigate(alert)
        except Exception as exc:  # report the failure instead of failing silently
            INVESTIGATIONS.labels(name, "error").inc()
            log.exception("investigation of %s failed", name)
            post_to_slack(
                f":warning: *AI SRE agent could not investigate {name}:* {type(exc).__name__}: {exc}"
            )
            return
        INVESTIGATIONS.labels(name, "ok").inc()
        COST.inc(result.cost_usd)
        DURATION.observe(result.seconds)
        tools_used = ", ".join(step["tool"] for step in result.steps) or "none"
        post_to_slack(
            f":mag: *AI investigation: {name}*\n\n{result.report}\n\n"
            f"_{len(result.steps)} tool calls ({tools_used}) · {result.seconds}s · "
            f"about ${result.cost_usd:.3f} · {MODEL} · read-only, a human decides the fix_"
        )
        log.info("investigation of %s done in %ss, about $%.4f", name, result.seconds, result.cost_usd)


app = FastAPI(title="AI SRE agent")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/alerts", status_code=202)
async def alerts(request: Request, background: BackgroundTasks) -> dict:
    payload = await request.json()
    now = time.time()
    accepted = []
    for alert in payload.get("alerts", []):
        reason, related = should_investigate(alert, now)
        if reason:
            SKIPPED.labels(reason).inc()
            if reason == "same_incident":
                name = alert.get("labels", {}).get("alertname")
                post_to_slack(
                    f":link: *{name}* looks like part of the incident already being investigated "
                    f"(*{related}*, in the last {INCIDENT_WINDOW_MINUTES} minutes), so it is not "
                    "investigated separately."
                )
            continue
        accepted.append(alert.get("labels", {}).get("alertname"))
        # Answer Alertmanager straight away; the investigation runs in the background.
        background.add_task(run_investigation, alert)
    log.info("received %d alerts, investigating %s", len(payload.get("alerts", [])), accepted or "none")
    return {"investigating": accepted}