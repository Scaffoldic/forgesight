# Runbooks — backend & integration reference

Operational reference for each ForgeSight backend and integration: how to configure it, what
it emits, how to operate it, and how to troubleshoot. Every runbook is verified against the
shipped package source.

> New here? Start with the [playbooks](../playbooks/) (task-oriented walkthroughs), then come
> back for depth.

## Exporters — ship telemetry to a backend

| Backend | Select with | Extra | Runbook |
|---|---|---|---|
| OTLP (Honeycomb, Jaeger, Tempo, New Relic, Phoenix, …) | `exporters=["otel"]` | `otel` | [exporter-otel](./exporter-otel.md) |
| Prometheus | `exporters=["prometheus"]` | `prometheus` | [exporter-prometheus](./exporter-prometheus.md) |
| Langfuse | `exporters=["langfuse"]` | `langfuse` | [exporter-langfuse](./exporter-langfuse.md) |
| ClickHouse | `exporters=["clickhouse"]` | `clickhouse` | [exporter-clickhouse](./exporter-clickhouse.md) |
| Datadog | `exporters=["datadog"]` | `datadog` | [exporter-datadog](./exporter-datadog.md) |

## Integrations — instrument a runtime/surface

| Integration | Extra | Runbook |
|---|---|---|
| MCP client/server spans + W3C propagation | `mcp` | [mcp-instrumentation](./mcp-instrumentation.md) |
| FastAPI request↔run correlation + flush-on-deploy | `fastapi` | [fastapi-integration](./fastapi-integration.md) |
| GitHub Actions run↔commit/PR/job + cost summary | `github` | [github-actions](./github-actions.md) |

## Frameworks & control plane

| Topic | Extra | Runbook |
|---|---|---|
| LangGraph / CrewAI auto-instrumentation (zero agent change) | `adapters-langgraph`, `adapters-crewai` | [framework-adapters](./framework-adapters.md) |
| Cost budgets, policy & kill-switch (+ pre-call projection) | `governance` | [governance](./governance.md) |
| Tamper-evident audit trail + compliance query/export | `audit` | [audit-trail](./audit-trail.md) |
| Eval scores & human feedback | `eval` | [evaluations](./evaluations.md) |
| Agent registry, ownership & chargeback | `registry` | [registry-chargeback](./registry-chargeback.md) |

## Core

| Topic | Runbook |
|---|---|
| Async export pipeline — queue, batching, backpressure, flushing | [export-pipeline](./export-pipeline.md) |

---

**Cross-cutting guarantee:** every exporter is non-blocking and fault-isolated — `export()`
returns a failure result, it never raises. A backend outage is counted and logged; your agent
run is unaffected (P6). The one deliberate exception is **governance**, whose `GovernanceSignal`
is *meant* to stop a run.

See also: the [design docs](../design/) (architecture, cost model, semconv, exporter pipeline),
the [ADRs](../adr/), and the [feature specs](../features/).
