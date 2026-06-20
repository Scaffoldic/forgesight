"""ForgeSight in 30 seconds — instrument an agent, see the trace + cost + a verified,
tamper-evident audit trail. Fully offline (no backend, no network): the same telemetry
ships to Jaeger / Datadog / Langfuse / Prometheus / ClickHouse by changing one config line.

    uv run python examples/demo.py
"""

from __future__ import annotations

import pathlib

import forgesight
from forgesight import telemetry
from forgesight_audit import AuditListener, AuditQuery, JsonlAuditSink, verify
from forgesight_core import AttributionMetricsConfig, InMemoryExporter, MetricConfig, get_runtime
from forgesight_governance import BudgetCap, BudgetInterceptor, BudgetScope

B, DIM, Y, G, C, R = "\033[1m", "\033[2m", "\033[33m", "\033[32m", "\033[36m", "\033[0m"
AUDIT = "/tmp/forgesight-demo-audit.jsonl"


def main() -> None:
    pathlib.Path(AUDIT).unlink(missing_ok=True)
    spans = InMemoryExporter()
    audit = JsonlAuditSink(AUDIT)

    # 1) configure once — pick backends by name; here we capture in-memory + audit.
    forgesight.configure(
        service_name="pr-reviewer",
        sync_export=True,
        exporters=[spans],  # ← swap for ["otel"], ["datadog"], ["langfuse"], … no code change
        metrics=MetricConfig(
            attribution=AttributionMetricsConfig(enabled=True, dimensions=("team",))
        ),
        interceptors=[BudgetInterceptor(caps=[BudgetCap(BudgetScope.TEAM, "platform", usd=1.0)])],
        listeners=[AuditListener(audit)],
    )

    # 2) wrap your agent — everything nests automatically (sync or async).
    print(f"{C}running agent 'pr-reviewer'…{R}")
    with (
        telemetry.agent_run("pr-reviewer", version="2.1.0", metadata={"team": "platform"}) as run,
        run.step("review"),
    ):
        with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
            call.record_usage(input=1240, output=340)  # tokens → USD, derived for you
            call.set_cost(0.0123)
        with run.tool_call("github_get_diff"):
            pass
    get_runtime().force_flush()

    # 3) what ForgeSight captured (and would ship to any backend) ───────────────
    records = sorted(spans.records, key=lambda r: r.start_unix_nanos)
    by_id = {r.span_id: r for r in records}

    def depth(rec: object) -> int:
        d, p = 0, getattr(rec, "parent_span_id", None)
        while p and p in by_id:
            d, p = d + 1, by_id[p].parent_span_id
        return d

    print(f"\n{B}trace{R}  {DIM}(OpenTelemetry GenAI semantic conventions){R}")
    for r in records:
        cost = f"   {Y}${r.llm.cost_usd:.4f}{R}" if r.llm and r.llm.cost_usd else ""
        tok = f" {DIM}{r.llm.usage.total} tok{R}" if r.llm else ""
        print(f"   {'  ' * depth(r)}{r.kind.value:<6} {r.name}{tok}{cost}")

    total = sum(r.llm.cost_usd or 0 for r in records if r.llm)
    print(f"\n{B}cost{R}   {Y}${total:.4f}{R} this run  {DIM}· live metric, attributed by team{R}")

    result = verify(audit)
    n = audit.query(AuditQuery()).event_count
    mark = f"{G}✓ verified{R}" if result.intact else "✗ broken"
    print(f"{B}audit{R}  {mark}  {DIM}· {n}-event hash chain, tamper-evident{R}")

    print(
        f"\n{DIM}instrument once → ship to Jaeger · Datadog · Langfuse · Prometheus · "
        f"ClickHouse — one config line.{R}"
    )
    get_runtime().shutdown()


if __name__ == "__main__":
    main()
