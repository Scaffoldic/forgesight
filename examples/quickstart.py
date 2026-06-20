"""Quick start — instrument any agent with ForgeSight. Run: python quickstart.py"""

import forgesight
from forgesight import telemetry
from forgesight_core import InMemoryExporter, MetricConfig, get_runtime

sink = InMemoryExporter()  # swap exporters for ["otel"], ["datadog"], ["langfuse"] — no code change
forgesight.configure(exporters=[sink], sync_export=True, metrics=MetricConfig(enabled=False))

with telemetry.agent_run("pr-reviewer", metadata={"team": "platform"}) as run:  # wrap your agent
    with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
        call.record_usage(input=1240, output=340)  # tokens → cost, derived for you
        call.set_cost(0.0123)
    with run.tool_call("github_get_diff"):
        ...

get_runtime().force_flush()
for r in sorted(sink.records, key=lambda x: x.start_unix_nanos):
    cost = f"  ${r.llm.cost_usd:.4f}" if r.llm and r.llm.cost_usd else ""
    print(f"  captured: {r.kind.value:<6} {r.name}{cost}")
print("\n  -> same code ships to Jaeger / Datadog / Langfuse / Prometheus - one config line.")
