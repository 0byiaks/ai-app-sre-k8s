"""Read-only investigation tools for the AI SRE agent.

Each function gathers one kind of evidence, the same things you checked by hand
in the break-it exercises:

    get_pods              kubectl get pods        (status, restarts, last crash reason)
    get_events            kubectl get events      (what Kubernetes noticed)
    get_logs              kubectl logs            (what the app itself said)
    get_rollout_history   kubectl rollout history (what changed recently)
    query_prometheus      PromQL                  (the numbers)
    search_logs           LogQL                   (logs kept in Loki, even from deleted pods)

None of them can change anything in the cluster. Outputs are kept short on purpose:
the agent sends them to Claude, and every character costs tokens.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import httpx
from kubernetes import client, config

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")
MAX_CHARS = 4000  # cap on any single tool result


_clients: dict[str, object] = {}


def _k8s() -> tuple[client.CoreV1Api, client.AppsV1Api]:
    """Connect once. Uses the pod's service account in the cluster, or your kubeconfig on a laptop."""
    if not _clients:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        _clients["core"], _clients["apps"] = client.CoreV1Api(), client.AppsV1Api()
    return _clients["core"], _clients["apps"]  # type: ignore[return-value]


def _ago(ts: datetime | None) -> str:
    if ts is None:
        return "unknown"
    seconds = int((datetime.now(UTC) - ts).total_seconds())
    if seconds < 120:
        return f"{seconds}s ago"
    if seconds < 7200:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def _cap(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n... (truncated)"


def get_pods(namespace: str) -> str:
    """Every pod's status, readiness, restarts and why its container last stopped."""
    core, _ = _k8s()
    lines = []
    for pod in core.list_namespaced_pod(namespace).items:
        for cs in pod.status.container_statuses or []:
            state = "running"
            if cs.state.waiting:
                state = f"waiting: {cs.state.waiting.reason}"
            elif cs.state.terminated:
                state = f"terminated: {cs.state.terminated.reason}"
            last = ""
            if cs.last_state and cs.last_state.terminated:
                t = cs.last_state.terminated
                last = f", last stop: {t.reason} (exit code {t.exit_code}) {_ago(t.finished_at)}"
            lines.append(
                f"{pod.metadata.name} [{cs.name}] phase={pod.status.phase} ready={cs.ready} "
                f"restarts={cs.restart_count} state={state}{last} image={cs.image}"
            )
    return _cap("\n".join(lines) or f"No pods in namespace {namespace}.")


def get_events(namespace: str, name: str | None = None) -> str:
    """Recent Kubernetes events, newest first. Optionally only for one pod or deployment."""
    core, _ = _k8s()
    selector = f"involvedObject.name={name}" if name else None
    events = core.list_namespaced_event(namespace, field_selector=selector).items
    events.sort(key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp, reverse=True)
    lines = [
        f"{_ago(e.last_timestamp or e.event_time)} {e.type} {e.reason} "
        f"{e.involved_object.kind}/{e.involved_object.name} (x{e.count or 1}): {e.message}"
        for e in events[:25]
    ]
    return _cap("\n".join(lines) or "No recent events.")


def get_logs(
    namespace: str, pod: str, container: str | None = None, previous: bool = False, tail: int = 50
) -> str:
    """The last lines a container printed. previous=True reads the crashed container before a restart."""
    core, _ = _k8s()
    try:
        text = core.read_namespaced_pod_log(
            pod, namespace, container=container, previous=previous, tail_lines=min(tail, 200)
        )
    except client.ApiException as exc:
        return f"Could not read logs: {exc.status} {exc.reason}"
    return _cap(text or "(no log output)")


def get_rollout_history(namespace: str, deployment: str) -> str:
    """Each revision of a deployment: when it was created and which image it ran."""
    _, apps = _k8s()
    dep = apps.read_namespaced_deployment(deployment, namespace)
    selector = ",".join(f"{k}={v}" for k, v in dep.spec.selector.match_labels.items())
    sets = apps.list_namespaced_replica_set(namespace, label_selector=selector).items
    rows = []
    for rs in sets:
        revision = int((rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision", 0))
        images = ", ".join(c.image for c in rs.spec.template.spec.containers)
        limits = ", ".join(
            f"{c.name} memory request={(c.resources.requests or {}).get('memory', 'none')} "
            f"limit={(c.resources.limits or {}).get('memory', 'none')}"
            for c in rs.spec.template.spec.containers
        )
        rows.append(
            (
                revision,
                f"revision {revision}: created {_ago(rs.metadata.creation_timestamp)}, "
                f"pods ready {rs.status.ready_replicas or 0}/{rs.spec.replicas}, "
                f"image {images}, {limits}",
            )
        )
    rows.sort(reverse=True)
    note = (
        "\n(Note: 'created' is when that ReplicaSet was first made. Kubernetes reuses an old ReplicaSet "
        "when the same settings come back, so a new revision can show an older time. Use get_events "
        "'Scaled up replica set' messages for when it was rolled out.)"
    )
    return _cap(("\n".join(r for _, r in rows[:6]) + note) if rows else "No revisions found.")


def query_prometheus(promql: str) -> str:
    """Run a PromQL query and return each series with its labels and current value."""
    try:
        resp = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql}, timeout=10)
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return f"Prometheus query failed: {exc}"
    if data.get("status") != "success":
        return f"Prometheus error: {data.get('error', 'unknown')}"
    lines = []
    for series in data["data"]["result"]:
        labels = ", ".join(f'{k}="{v}"' for k, v in series["metric"].items() if k != "__name__")
        value = series.get("value", [None, "n/a"])[1]
        lines.append(f"{{{labels}}} = {value}")
    return _cap("\n".join(lines) or "No data (the query matched nothing).")


def search_logs(logql: str, minutes: int = 30, limit: int = 50) -> str:
    """Search logs stored in Loki over the last N minutes, newest first."""
    end = time.time_ns()
    params = {
        "query": logql,
        "start": end - minutes * 60 * 1_000_000_000,
        "end": end,
        "limit": min(limit, 200),
        "direction": "backward",
    }
    try:
        resp = httpx.get(f"{LOKI_URL}/loki/api/v1/query_range", params=params, timeout=15)
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return f"Loki query failed: {exc}"
    if data.get("status") != "success":
        return f"Loki error: {data.get('error', resp.text[:300])}"
    entries = []
    for stream in data["data"]["result"]:
        pod = stream["stream"].get("pod", "?")
        for ts, line in stream["values"]:
            entries.append((int(ts), f"[{pod}] {line}"))
    entries.sort(reverse=True)
    return _cap("\n".join(line for _, line in entries[:limit]) or "No matching log lines.")


if __name__ == "__main__":
    # A quick look at what the agent can see. Run with your kind cluster and port-forwards up.
    ns = os.getenv("TARGET_NAMESPACE", "relay")
    core, _ = _k8s()
    backend_pods = core.list_namespaced_pod(ns, label_selector="app=backend").items
    backend_pod = backend_pods[0].metadata.name if backend_pods else "backend"

    print("== Pods ==\n" + get_pods(ns))
    print("\n== Events ==\n" + get_events(ns))
    print("\n== Backend rollout history ==\n" + get_rollout_history(ns, "backend"))
    print("\n== Backend logs (last 10 lines) ==\n" + get_logs(ns, backend_pod, tail=10))
    print(
        "\n== Prometheus: AI errors in the last hour ==\n"
        + query_prometheus("sum by (reason) (increase(relay_llm_errors_total[1h]))")
    )
    print(
        "\n== Loki: recent backend warnings ==\n"
        + search_logs('{namespace="relay", app="backend"} | json | level="warning"', minutes=60, limit=5)
    )
