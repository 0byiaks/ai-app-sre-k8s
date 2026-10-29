"""Run an investigation from your laptop, as if an alert had just fired.

python -m sre_agent.investigate RelayCrashLooping
python -m sre_agent.investigate RelayAuthFailing --severity critical
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from .agent import MODEL, investigate

# The same summaries as the alert rules in helm/relay/templates/alerts.yaml.
SUMMARIES = {
    "RelayBackendDown": "Relay backend has no ready pods",
    "RelayCrashLooping": "A Relay pod keeps restarting",
    "RelayAIErrors": "Relay is failing to get answers from Claude",
    "RelayAuthFailing": "Claude is rejecting Relay's API key",
    "RelayHighMemory": "A Relay container is close to its memory limit",
    "RelaySlowResponses": "Relay answers are slow to start",
    "RelaySpendHigh": "Relay is spending too much on Claude",
    "RelayFallbackInUse": "Relay is answering with the backup model",
    "RelayMetricsMissing": "Prometheus cannot scrape Relay",
    "RelayRateLimited": "Many Relay requests are being rate limited",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Investigate an alert with the AI SRE agent.")
    parser.add_argument("alertname", help="e.g. RelayCrashLooping")
    parser.add_argument("--namespace", default="relay")
    parser.add_argument("--severity", default="warning")
    args = parser.parse_args()

    alert = {
        "labels": {"alertname": args.alertname, "severity": args.severity, "namespace": args.namespace},
        "annotations": {"summary": SUMMARIES.get(args.alertname, args.alertname)},
        "startsAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    print(f"Investigating {args.alertname} in namespace {args.namespace} with {MODEL}...\n")

    result = investigate(alert)

    for i, step in enumerate(result.steps, 1):
        inputs = ", ".join(f"{k}={v!r}" for k, v in step["input"].items())
        print(f"  step {i}: {step['tool']}({inputs})")
    print("\n" + "=" * 70 + "\n" + result.report + "\n" + "=" * 70)
    print(
        f"\n{len(result.steps)} tool calls, {result.input_tokens:,} input + {result.output_tokens:,} "
        f"output tokens, about ${result.cost_usd:.4f}, {result.seconds}s"
    )


if __name__ == "__main__":
    main()
