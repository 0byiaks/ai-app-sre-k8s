# Relay: system overview

## What runs here

- **Relay** is an AI chat app in the `relay` namespace, with two Deployments:
  - `frontend`: nginx serving the web page and forwarding `/api/` to the backend.
  - `backend`: a Python FastAPI service that calls the Claude API and streams answers back.
- **Monitoring** runs in the `monitoring` namespace: Prometheus, Grafana, Alertmanager, Loki and Alloy.
- The cluster is a local kind cluster with one node, used for development.

## How it is deployed

- Everything in `relay` is managed by **Helm** (release `relay`, chart `helm/relay`, laptop settings in
  `helm/relay/values-kind.yaml`), and the chart lives in Git.
- Changes are meant to go through the chart and `helm upgrade`. Changes made directly with kubectl
  are drift: the cluster no longer matches Git.
- `helm upgrade` does NOT remove env vars that were added with `kubectl set env` and were never in the
  chart (verified in testing). Remove those with `kubectl set env deploy/<name> -n relay NAME-`, then
  check with `kubectl get deploy <name> -n relay -o yaml`.
- The deploy command is: `helm upgrade relay helm/relay -n relay -f helm/relay/values-kind.yaml`
  (run from the repo root).

## What it depends on

- The backend calls the Claude API with a key from the Secret `relay-llm`.
- If the main model fails with a temporary error, the backend retries once on a cheaper backup model.
- Secrets (`relay-llm`, `relay-db`) are created by hand with kubectl and are not part of the chart.

## Guardrails already in place

- Rate limit: 20 chat requests per minute per client (blocked requests get HTTP 429 and count in
  `relay_rate_limited_total`).
- Max answer length: 1024 tokens (`RELAY_MAX_TOKENS`). The app refuses to start if it is below 64.
- Backup model: `claude-haiku-5-5`, used once when the main model fails with a temporary error.
- Main model: `claude-sonnet-5-5` (`RELAY_PRIMARY_MODEL`).
- Settings come from the Helm chart. Any env var that is not in the chart (for example one added with
  `kubectl set env`) is drift.

## Known sources of traffic

- Load tests use k6 (user agent "Grafana k6") through the frontend. They should normally run with the
  backend in mock mode; against the real model they cost real money.

## What normal looks like

- One `backend` pod and one `frontend` pod, both ready, with no restarts.
- Backend: 128Mi memory request, 256Mi limit. Frontend: 64Mi limit.
- Time to first word: the alert threshold is 5 seconds at the 95th percentile. Long answers (up to 1024
  tokens) are normal and are not by themselves a sign of a problem; slowness under heavy traffic
  usually comes from load, not from the normal settings.
- Most chats succeed. Failed Claude calls still return HTTP 200 (the error is inside the stream),
  so check `relay_llm_errors_total` and the backend logs, not HTTP status codes.