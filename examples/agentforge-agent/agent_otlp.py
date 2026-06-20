"""The same offline AgentForge agent, exporting to a *real* OTLP backend (Jaeger).

Identical agent + bridge as `agent.py` — the only change is the exporter: instead of an
in-memory sink, ForgeSight ships the trace over OTLP/HTTP to a local collector. This proves
the export path end to end: an AgentForge run shows up as a distributed trace in Jaeger.

    docker compose up -d                     # start Jaeger (OTLP :4318, UI :16686)
    python agent_otlp.py                     # run the agent → trace lands in Jaeger
    open http://localhost:16686              # service "order-agent-otlp"

No API key, no network to any model provider — the agent loop runs against a scripted fake.
Point FORGESIGHT_OTLP_ENDPOINT at any OTLP collector to swap backends; the code never changes.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request

from agent import lookup_order, scripted_model  # the same agent as agent.py
from agentforge import Agent
from forgesight_bridge import instrument_agentforge_run

import forgesight
from forgesight_core.metrics import MetricConfig
from forgesight_otel import OTelExporter

SERVICE_NAME = "order-agent-otlp"
OTLP_ENDPOINT = os.environ.get("FORGESIGHT_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
JAEGER_API = os.environ.get("JAEGER_API", "http://localhost:16686")


async def main() -> None:
    # ForgeSight → OTLP/HTTP → the collector. Swap the endpoint to change backends.
    forgesight.configure(
        service_name=SERVICE_NAME,
        exporters=[
            OTelExporter(
                endpoint=OTLP_ENDPOINT, protocol="http/protobuf", service_name=SERVICE_NAME
            )
        ],
        sync_export=True,
        metrics=MetricConfig(enabled=False),
    )

    async with Agent(model=scripted_model(), tools=[lookup_order], strategy="react") as agent:
        result = await agent.run("What's the status of order 1042?")
    print(f"AgentForge: {result.output!r}  (cost=${result.cost_usd:.4f}, run_id={result.run_id})")

    instrument_agentforge_run(
        result,
        agent_name=SERVICE_NAME,
        version="1.0.0",
        metadata={"team": "growth", "environment": "demo"},
    )
    forgesight.get_runtime().force_flush()  # ensure the batch reaches the collector
    print(f"→ exported over OTLP to {OTLP_ENDPOINT}")

    _verify_in_jaeger()


def _verify_in_jaeger(attempts: int = 10) -> None:
    """Poll Jaeger's query API to confirm the trace arrived (ingestion has a little lag)."""
    url = f"{JAEGER_API}/api/traces?service={SERVICE_NAME}&limit=1"
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            data = {"data": []}
        traces = data.get("data") or []
        if traces:
            spans = traces[0].get("spans", [])
            names = sorted({s.get("operationName", "") for s in spans})
            print(f"\n✅ trace found in Jaeger — {len(spans)} spans: {names}")
            print(f"   view it: {JAEGER_API}/trace/{traces[0].get('traceID')}")
            return
        _sleep()
    raise SystemExit(
        f"❌ no trace for service {SERVICE_NAME!r} in Jaeger yet — "
        "is `docker compose up -d` running?"
    )


def _sleep() -> None:
    import time

    time.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
