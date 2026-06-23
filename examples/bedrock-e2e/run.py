"""End-to-end: a real AWS Bedrock call instrumented with ForgeSight, exporting traces to
Jaeger (OTLP), metrics to Prometheus, with the feat-023 audit trail and feat-026
attributed-cost metrics — then verifies everything landed.

Run (from the repo root, with Colima/Docker up and the stack started):
    docker compose up -d
    uv run --no-sync python examples/bedrock-e2e/run.py
"""

from __future__ import annotations

import json
import time
import urllib.request

import boto3

import forgesight
from forgesight import telemetry
from forgesight_audit import AuditListener, AuditQuery, JsonlAuditSink, verify
from forgesight_core import AttributionMetricsConfig, MetricConfig, get_runtime
from forgesight_governance import BudgetCap, BudgetInterceptor, BudgetScope, ProjectionConfig

REGION = "us-east-1"
MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
JAEGER = "http://localhost:16686"
PROM = "http://localhost:9090"
AUDIT_LOG = "/tmp/forgesight-bedrock-audit.jsonl"

# Bedrock Claude Haiku 4.5 list price (USD / token).
PRICE_IN = 1.0 / 1_000_000
PRICE_OUT = 5.0 / 1_000_000


def _http_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.load(resp)


def main() -> None:
    audit = JsonlAuditSink(AUDIT_LOG)
    forgesight.configure(
        service_name="bedrock-demo",
        sync_export=True,
        exporters=["otel", "prometheus"],
        exporter_config={
            "otel": {
                # base URL — the exporter appends /v1/traces for OTLP/HTTP automatically.
                "endpoint": "http://localhost:4318",
                "protocol": "http/protobuf",
                "service_name": "bedrock-demo",
            },
            "prometheus": {"port": 9464},
        },
        metrics=MetricConfig(
            attribution=AttributionMetricsConfig(enabled=True, dimensions=("team", "owner"))
        ),
        interceptors=[
            BudgetInterceptor(
                caps=[BudgetCap(BudgetScope.TEAM, "platform", usd=1.0)],
                projection=ProjectionConfig(enabled=True),
            )
        ],
        listeners=[AuditListener(audit)],
    )

    client = boto3.client("bedrock-runtime", region_name=REGION)
    prompt = "In one sentence, what is OpenTelemetry?"

    print("→ calling Bedrock:", MODEL)
    with (
        telemetry.agent_run(
            "bedrock-demo",
            version="1.0.0",
            metadata={"team": "platform", "owner": "owner@example.com", "environment": "demo"},
        ) as run,
        run.step("answer-question"),
    ):
        with run.llm_call(
            "aws.bedrock", MODEL, projected_tokens={"input": 40, "max_tokens": 200}
        ) as call:
            resp = client.converse(
                modelId=MODEL,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 200, "temperature": 0},
            )
            usage = resp["usage"]
            answer = resp["output"]["message"]["content"][0]["text"].strip()
            call.record_usage(input=usage["inputTokens"], output=usage["outputTokens"])
            call.record_response(finish_reasons=(resp.get("stopReason", "end_turn"),))
            call.set_cost(usage["inputTokens"] * PRICE_IN + usage["outputTokens"] * PRICE_OUT)
        with run.tool_call("format_answer"):
            pass

    print(f"  model said: {answer!r}")
    print(f"  tokens: in={usage['inputTokens']} out={usage['outputTokens']}")

    get_runtime().force_flush()
    time.sleep(2)  # let Jaeger ingest

    # 1) Traces in Jaeger -------------------------------------------------------
    traces = _http_json(f"{JAEGER}/api/traces?service=bedrock-demo&lookback=1h&limit=1")["data"]
    print("\n=== JAEGER (traces) ===")
    if traces:
        spans = traces[0]["spans"]
        cost_tag = next(
            (
                t["value"]
                for s in spans
                for t in s["tags"]
                if t["key"] == "forgesight.usage.cost_usd"
            ),
            None,
        )
        print(f"  ✅ trace {traces[0]['traceID'][:16]}… — {len(spans)} spans:")
        for s in sorted(spans, key=lambda x: x["startTime"]):
            print("     ", s["operationName"])
        print(f"  cost on the chat span: ${cost_tag}")
    else:
        print("  ❌ no trace found")

    # 2) feat-026 cost matrices (read from the in-process metrics subsystem) -----
    print("\n=== feat-026 COST METRICS ===")
    data = get_runtime().metrics.collect()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name in (
                    "forgesight.cost.attributed_usd",
                    "forgesight.cost.budget_utilization",
                    "forgesight.agent.cost_total",
                ):
                    for pt in metric.data.data_points:
                        print(f"  {metric.name} = {pt.value:.6f}  {dict(pt.attributes)}")

    # 3) feat-023 audit trail ---------------------------------------------------
    print("\n=== feat-023 AUDIT TRAIL ===")
    result = verify(audit)
    report = audit.query(AuditQuery())
    print(f"  chain intact: {result.intact} ({report.event_count} events)")
    for ev in report.events():
        cost = f" ${ev.cost_usd:.6f}" if ev.cost_usd else ""
        print(f"     {ev.kind:<14} principal={ev.principal} team={ev.team}{cost}")

    # 4) Prometheus scrape ------------------------------------------------------
    print("\n=== PROMETHEUS (scraping the agent's :9464) ===")
    print("  waiting 8s for a scrape…")
    time.sleep(8)
    try:
        q = _http_json(f"{PROM}/api/v1/query?query=%7B__name__%3D~%22forgesight_.%2B%22%7D")
        names = sorted({r["metric"]["__name__"] for r in q["data"]["result"]})
        print(f"  ✅ Prometheus has {len(names)} forgesight metrics scraped:")
        for n in names[:10]:
            print("     ", n)
    except Exception as e:
        print("  (prometheus query failed:", e, ")")

    get_runtime().shutdown()
    print("\nDone. Open Jaeger: http://localhost:16686  ·  Prometheus: http://localhost:9090")


if __name__ == "__main__":
    main()
