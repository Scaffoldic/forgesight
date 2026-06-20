"""Run every agent under ONE long-lived process so Prometheus can scrape real totals.

Each agent example, run on its own, is a short-lived process: it binds :9464, runs for a few
seconds, and exits — so Prometheus (which scrapes every 5s) rarely catches it, and its counter
resets each run. This runner configures ForgeSight **once**, runs all the agents under that one
runtime (so the metrics accumulate), and keeps :9464 up long enough to be scraped — so the
Grafana dashboard shows the true totals (5 agent runs: react + rag + supervisor/researcher/writer).

Run: ``uv run --no-sync python -m examples.agents.demo_all``  (traces land under service
``forgesight-demo``; open Grafana http://localhost:3000 while it's alive).
"""

from __future__ import annotations

import time

from forgesight_audit import AuditQuery, verify
from forgesight_core import get_runtime

from . import _demo, multi_agent, rag_agent, react_agent

KEEP_ALIVE_S = 30


def main() -> None:
    sink = _demo.configure("forgesight-demo", "/tmp/forgesight-demo-audit.jsonl")
    client = _demo.bedrock_client()

    react_agent.run(client)
    rag_agent.run(client)
    multi_agent.run(client)
    get_runtime().force_flush()

    rollup = sink.query(AuditQuery())
    print(
        f"\n  ✅ 5 agent runs · audit chain intact: {verify(sink).intact} · "
        f"{rollup.event_count} events · total cost ${rollup.cost_usd_total:.6f}"
    )
    print(f"  keeping :9464 up {KEEP_ALIVE_S}s so Prometheus scrapes the accumulated metrics…")
    print("  → Grafana:  http://localhost:3000  (refresh; runs/cost/tokens now reflect all 5)")
    print("  → Jaeger:   http://localhost:16686/search?service=forgesight-demo")
    time.sleep(KEEP_ALIVE_S)
    get_runtime().shutdown()


if __name__ == "__main__":
    main()
