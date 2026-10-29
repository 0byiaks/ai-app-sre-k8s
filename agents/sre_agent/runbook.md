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
- The deploy command is: `helm upgrade relay helm/relay -n relay -f helm/relay/values-kind.yaml`
  (run from the repo root).
- If someone changed a resource with kubectl, `helm upgrade` fails with "conflicts with kubectl-set".
  Add `--force-conflicts` so Helm takes the fields back from the drift.

## What it depends on

- The backend calls the Claude API with a key from the Secret `relay-llm`.
- If the main model fails with a temporary error, the backend retries once on a cheaper backup model.
- Secrets (`relay-llm`, `relay-db`) are created by hand with kubectl and are not part of the chart.

## What normal looks like

- One `backend` pod and one `frontend` pod, both ready, with no restarts.
- Backend: 128Mi memory request, 256Mi limit. Frontend: 64Mi limit.
- Most chats succeed. Failed Claude calls still return HTTP 200 (the error is inside the stream),
  so check `relay_llm_errors_total` and the backend logs, not HTTP status codes.
