"""Shared setup for the ForgeSight agent examples.

Each example is a real AWS Bedrock agent instrumented with ForgeSight, exporting traces to
Jaeger (OTLP) + metrics to Prometheus (→ Grafana), with the audit trail (feat-023) and
attributed-cost metrics (feat-026). Bring the stack up first: ``docker compose up -d``.

Run an example (from the repo root):
    uv run --no-sync python -m examples.agents.react_agent
    uv run --no-sync python -m examples.agents.rag_agent
    uv run --no-sync python -m examples.agents.multi_agent
"""

from __future__ import annotations

import time
from typing import Any

import boto3

import forgesight
from forgesight_audit import AuditListener, AuditQuery, JsonlAuditSink, verify
from forgesight_core import AttributionMetricsConfig, MetricConfig, get_runtime
from forgesight_governance import BudgetCap, BudgetInterceptor, BudgetScope

MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
REGION = "us-east-1"
TEAM = "platform"
OWNER = "engg.kjoshi@gmail.com"

# Bedrock Claude Haiku 4.5 list price (USD / token).
PRICE_IN = 1.0 / 1_000_000
PRICE_OUT = 5.0 / 1_000_000


def configure(service_name: str, audit_path: str) -> JsonlAuditSink:
    """Wire ForgeSight to the local stack and return the audit sink."""
    sink = JsonlAuditSink(audit_path)
    forgesight.configure(
        service_name=service_name,
        sync_export=True,
        exporters=["otel", "prometheus"],
        exporter_config={
            "otel": {
                "endpoint": "http://localhost:4318",  # exporter appends /v1/traces
                "protocol": "http/protobuf",
                "service_name": service_name,
            },
            "prometheus": {"port": 9464},
        },
        metrics=MetricConfig(
            attribution=AttributionMetricsConfig(enabled=True, dimensions=("team", "owner"))
        ),
        interceptors=[BudgetInterceptor(caps=[BudgetCap(BudgetScope.TEAM, TEAM, usd=5.0)])],
        listeners=[AuditListener(sink)],
    )
    return sink


def run_metadata() -> dict[str, object]:
    return {"team": TEAM, "owner": OWNER, "environment": "demo"}


def bedrock_client() -> Any:
    return boto3.client("bedrock-runtime", region_name=REGION)


def chat(
    client: Any, user_text: str, *, system: str | None = None, max_tokens: int = 300
) -> tuple[str, dict[str, int]]:
    """One Bedrock turn. Returns (text, usage)."""
    kwargs: dict[str, Any] = {
        "modelId": MODEL,
        "messages": [{"role": "user", "content": [{"text": user_text}]}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    resp = client.converse(**kwargs)
    return resp["output"]["message"]["content"][0]["text"].strip(), resp["usage"]


def record(call: Any, usage: dict[str, int]) -> None:
    """Stamp real token usage + derived cost onto an llm_call scope."""
    call.record_usage(input=usage["inputTokens"], output=usage["outputTokens"])
    call.set_cost(usage["inputTokens"] * PRICE_IN + usage["outputTokens"] * PRICE_OUT)


def report(service_name: str, sink: JsonlAuditSink) -> None:
    """Flush, then summarise what landed and where to look."""
    get_runtime().force_flush()
    time.sleep(2)  # let Jaeger ingest
    result = verify(sink)
    rollup = sink.query(AuditQuery())
    print(
        f"\n  audit chain intact: {result.intact} · {rollup.event_count} events · "
        f"total cost ${rollup.cost_usd_total:.6f}"
    )
    print(f"  → Jaeger:  http://localhost:16686/search?service={service_name}")
    print("  → Grafana: http://localhost:3000  (dashboard: ForgeSight — agent telemetry)")
    get_runtime().shutdown()
