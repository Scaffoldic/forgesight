# Architecture: ForgeSight

## Metadata

| Field | Value |
|---|---|
| **Title** | ForgeSight — system architecture |
| **Status** | draft |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Last updated** | 2026-06-14 |
| **Applies to version** | 0.x (pre-alpha) |

---

## 1. Purpose

The ForgeSight is a vendor-neutral, OpenTelemetry-first telemetry SDK for AI
agents. This document is the canonical reference for how the system fits together —
what is locked, what is open, and what ships where. It describes the steady state;
proposals to change it are design docs and ADRs.

It does **not** restate the *why* (see [`../requirements.md`](../requirements.md))
or the per-feature *what* (see [`../features/README.md`](../features/README.md)).

## 2. Context within the agent stack

The SDK sits *beside* the agent runtime, not inside it. The agent (or a framework
adapter) calls the instrumentation API; the SDK turns those calls into a span tree,
metrics, cost, and events, runs them through interceptors, and fans them out to
exporters — each of which talks to one backend.

```
        ┌──────────────────────────────────────────────────────────────┐
        │                         your agent                           │
        │   (AgentForge / LangGraph / CrewAI / PydanticAI / custom)    │
        └───────────────┬──────────────────────────┬───────────────────┘
                        │ direct API                │ via adapter (feat-019)
                        ▼                            ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                       forgesight (facade)                        │
   │      configure() · telemetry · @instrument · context managers        │
   └───────────────────────────────┬──────────────────────────────────────┘
                                    │ builds records against
                                    ▼
   ┌────────────────────────┐                ┌────────────────────────────┐
   │   forgesight-api    │                │     forgesight-core     │
   │      (contracts)        │◄──implements───┤        (runtime)            │
   │                         │                │  TelemetryContext / span    │
   │  AgentRun · LLMCall     │                │  tree · interceptor chain   │
   │  ToolCall · MCPCall     │                │  metrics · cost · events    │
   │  WorkflowRun · Step     │                │  ┌───────────────────────┐  │
   │  TelemetryExporter SPI  │                │  │   export pipeline      │  │
   │  Interceptor SPI        │                │  │ bounded queue + worker │  │
   │  EventListener SPI      │                │  │ batch · fault-isolate  │  │
   │  PricingProvider SPI    │                │  └───────────┬───────────┘  │
   └────────────────────────┘                └──────────────┼──────────────┘
                                                            │ fan-out (P6)
            ┌──────────────────────┬──────────────────────┬─┴────────────────┐
            ▼                      ▼                      ▼                  ▼
   ┌────────────────┐   ┌────────────────┐   ┌────────────────┐   ┌────────────────┐
   │ forgesight │   │ forgesight │   │ forgesight │   │  custom        │
   │     -otel      │   │   -langfuse    │   │  -prometheus   │   │  exporter      │
   │ (OTLP → any    │   │ (Langfuse OTLP │   │ (/metrics)     │   │ (entry point)  │
   │  collector)    │   │  ingest)       │   │                │   │                │
   └───────┬────────┘   └───────┬────────┘   └───────┬────────┘   └────────────────┘
           ▼                    ▼                    ▼
   Datadog / Honeycomb /   Langfuse UI          Prometheus /
   Jaeger / Tempo / …                            Grafana
```

The **OTel exporter is the keystone**: because the domain model maps cleanly onto
the OTel GenAI conventions, anything that ingests OTLP (Datadog, Honeycomb, Jaeger,
Tempo, SigNoz, New Relic, Phoenix, Langfuse) works through `forgesight-otel`
with *no* dedicated package. First-party packages (`-langfuse`, `-prometheus`,
`-clickhouse`, `-datadog`) exist only to add value the raw OTLP path can't — native
cost models, pull-based metrics, columnar schemas, vendor APIs.

## 3. Key concepts

| Concept | Definition |
|---|---|
| **AgentRun** | One agent execution. The root of a run's trace. Carries identity (`agent.name`, `agent.version`), correlation ids (`run_id`, `context_id`, `trace_id`, `parent_run_id`), status, timing. |
| **WorkflowRun** | A multi-step orchestration that parents one or more agent runs / steps. Maps to `invoke_workflow`. |
| **Step** | One iteration / phase within a run (e.g. a ReAct turn). An INTERNAL span; parent of the LLM / tool / MCP calls it makes. |
| **LLMCall / ToolCall / MCPCall** | The leaf operations. Each is a child span with domain-specific attributes (tokens & cost; tool name & type; MCP server & method). |
| **Record** | The immutable, exporter-facing value produced when any of the above starts/ends. Exporters consume records, not live objects. |
| **TelemetryContext** | The per-run ambient state (current span, ids, business metadata) propagated via `contextvars`, surviving nested `async` tasks and (via context propagation) process / agent hops. |
| **Exporter** | A `TelemetryExporter` implementation that ships records to one backend. Discovered via entry points; never depended on by core. |
| **Interceptor** | A hook that can mutate, redact, or veto a record before export (PII, content gating, policy, budgets). |
| **EventListener** | A subscriber to lifecycle events (`RUN_STARTED`, …) for side-effects (Slack, Kafka, audit). |
| **PricingProvider** | Resolves `(provider, model, token-usage)` → cost. Pluggable; ships with a refreshable default table. |
| **Pipeline** | The async, bounded-queue, batched, fault-isolated machinery between "record produced" and "exporter called". |

## 4. The contract

The contract lives in `forgesight-api` and is what every exporter / interceptor /
adapter implements. These types are **locked** (P5) — changing them is a major bump
with an ADR. Full definitions in feat-001; signatures below are the stable surface.

### 4.1 Domain model (Python)

```python
# forgesight_api/model.py — locked
from dataclasses import dataclass, field
from enum import Enum

class RunStatus(str, Enum):
    RUNNING = "running"; OK = "ok"; ERROR = "error"
    CANCELLED = "cancelled"; BUDGET_EXCEEDED = "budget_exceeded"; GUARDRAIL = "guardrail"

class Kind(str, Enum):
    WORKFLOW = "workflow"; AGENT = "agent"; STEP = "step"
    LLM = "llm"; TOOL = "tool"; MCP = "mcp"

@dataclass(frozen=True, slots=True)
class TokenUsage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    reasoning: int = 0
    @property
    def total(self) -> int: ...

@dataclass(slots=True)
class LLMCall:
    provider: str                      # → gen_ai.provider.name
    request_model: str                 # → gen_ai.request.model
    response_model: str | None = None  # → gen_ai.response.model
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float | None = None      # extension attr; None until priced
    finish_reasons: tuple[str, ...] = ()
    latency_ms: float | None = None
    params: dict[str, object] = field(default_factory=dict)   # temperature, max_tokens…
    # content (messages) is OPT-IN and lives on a separate, gated field

@dataclass(slots=True)
class ToolCall:
    name: str                          # → gen_ai.tool.name
    tool_type: str = "function"        # → gen_ai.tool.type (open set)
    call_id: str | None = None
    status: RunStatus = RunStatus.RUNNING
    duration_ms: float | None = None

@dataclass(slots=True)
class MCPCall:
    server: str
    method: str                        # → mcp.method.name (e.g. tools/call)
    tool: str | None = None            # → gen_ai.tool.name when tools/call
    session_id: str | None = None
    duration_ms: float | None = None
    status: RunStatus = RunStatus.RUNNING

@dataclass(slots=True)
class AgentRun:
    agent_name: str
    agent_version: str | None
    run_id: str                        # ULID
    context_id: str | None
    trace_id: str
    parent_run_id: str | None
    status: RunStatus
    start_unix_nanos: int
    end_unix_nanos: int | None
    metadata: dict[str, object] = field(default_factory=dict)
    @property
    def duration_ms(self) -> float | None: ...
```

### 4.2 SPIs (Python)

```python
# forgesight_api/spi.py — locked
from typing import Protocol, Sequence, runtime_checkable

@runtime_checkable
class TelemetryExporter(Protocol):
    """One backend. Called by the pipeline worker, never on the hot path."""
    def export(self, records: Sequence["Record"]) -> "ExportResult": ...
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...

@runtime_checkable
class Interceptor(Protocol):
    """Mutate / redact / veto a record before export. Runs in registration order."""
    def intercept(self, record: "Record") -> "Record | None": ...   # None drops it

@runtime_checkable
class EventListener(Protocol):
    """Side-effect subscriber to lifecycle events. Isolated from the run."""
    def on_event(self, event: "LifecycleEvent") -> None: ...

@runtime_checkable
class PricingProvider(Protocol):
    """Resolve cost. Returns None for unknown models (degrade gracefully)."""
    def price(self, provider: str, model: str, usage: "TokenUsage") -> float | None: ...
```

`ExportResult` is `SUCCESS | FAILURE` (mirrors OTel `SpanExportResult`); `export`
**returns** failure, never raises (P6). The four SPIs are the *entire* extension
surface — there is no fifth way (see §6).

### 4.3 TypeScript (parity sketch)

```typescript
// @agentforge/sdk-api
export interface TelemetryExporter {
  export(records: Record[]): ExportResult | Promise<ExportResult>;
  forceFlush(timeoutMillis?: number): Promise<boolean>;
  shutdown(timeoutMillis?: number): Promise<void>;
}
export interface Interceptor { intercept(record: Record): Record | null; }
export interface EventListener { onEvent(event: LifecycleEvent): void; }
export interface PricingProvider {
  price(provider: string, model: string, usage: TokenUsage): number | null;
}
```

Both languages declare the same contract; idiom (Protocol vs interface,
`contextvars` vs `AsyncLocalStorage`) differs, semantics do not.

## 5. Package model (three tiers + integrations)

Mirrors AgentForge's three-tier model (ADR-0003) adapted to telemetry. Tiers:

| Distribution | Import root | Contains | Deps |
|---|---|---|---|
| `forgesight-api` | `forgesight_api` | The locked domain model + 4 SPIs + value types. No I/O. | stdlib + `typing-extensions` only |
| `forgesight-core` | `forgesight_core` | The runtime: context, span tree, pipeline, metrics, cost, events, config, in-memory + console exporters. | `-api`, `opentelemetry-api`, small pure-Python |
| `forgesight` | `forgesight` | Batteries-included facade: `configure()`, `telemetry`, decorators, entry-point auto-load. The thing most users `pip install`. | `-core` |

Integration packages (one backend each; installed to enable — P2):

| Package | Provides |
|---|---|
| `forgesight-otel` | OTLP span/metric exporter + the canonical GenAI semconv mapping (feat-004). Works with any OTLP backend. |
| `forgesight-prometheus` | Pull-based `/metrics` (MetricReader) + push-gateway (feat-012). |
| `forgesight-langfuse` | Langfuse OTLP ingest + native cost/observation mapping (feat-013). |
| `forgesight-clickhouse` | Columnar batch insert of records (feat-014). |
| `forgesight-datadog` | Datadog APM / DD-trace export (feat-015). |
| `forgesight-mcp` | MCP client/server instrumentation (feat-016). |
| `forgesight-fastapi` | FastAPI middleware + lifespan wiring (feat-017). |
| `forgesight-github` | GitHub Actions bootstrap + run↔commit/PR correlation (feat-018). |
| **custom** | Implement an SPI; register via `@register(...)` or entry point `forgesight.exporters`. |

**Dependency rule (locked):** `-api` imports nothing from `-core` or any integration;
it is the leaf. `-core` imports only `-api` + the OTel *API* (not vendor SDKs).
Integrations import `-core` + their one vendor SDK. AgentForge depends on `-api` only.

## 6. Extension points

A developer extends the SDK in one of four ways, in order of preference:

1. **Install a shipped integration.** `pip install forgesight-langfuse` + one
   config line.
2. **Implement an SPI and register it.** Write a `TelemetryExporter` /
   `Interceptor` / `EventListener` / `PricingProvider`; register via
   `@forgesight.register("exporters", "my-sink")` or a `pyproject.toml` entry
   point. Now resolvable by name from config exactly like shipped integrations.
3. **Use a framework adapter** (feat-019) to auto-instrument an existing framework.
4. **Call the instrumentation API directly** for full control.

There is no fifth way. The SDK does **not** support monkey-patching its own
internals, runtime class swapping, or import hooks for extension.

## 7. Lifecycle

```
import forgesight; forgesight.configure()          # 1. bootstrap
   │  load config (env → file → kwargs, last wins); resolve exporters via
   │  entry points; build the pipeline (queue + worker); register interceptors
   │  + listeners + pricing provider; install atexit flush.
   ▼
with telemetry.agent_run("issue-classifier", version="1.2.0") as run:   # 2. run
   │  generate run_id (ULID) + trace_id; bind TelemetryContext; open root span;
   │  emit RUN_STARTED.
   │
   ├── with run.step("react-iter-1") as step:               # 3. step (optional)
   │       ├── run.llm_call(provider=…, model=…) ...         # 4. leaf calls
   │       ├── run.tool_call(name=…) ...                     #    each → record →
   │       └── run.mcp_call(server=…, method=…) ...          #    interceptors → queue
   │
   ▼  on exit: set status + timing; price LLM calls (cost); emit RUN_COMPLETED /
      RUN_FAILED; enqueue the run record. The worker batches + fans out to exporters.
```

Each produced record is: **built → run through the interceptor chain → enqueued**.
The worker thread/task **dequeues in batches → calls each exporter (isolated) →
records drops/failures as metrics**. `force_flush()` drains; `shutdown()` drains +
closes exporters. Steps below the hot path never block (NFR-2).

## 8. Failure modes

| Mode | Surface | Behaviour |
|---|---|---|
| Exporter raises / times out | logged via `forgesight.pipeline`; counted in `sdk_export_failures_total` | Isolated; other exporters + the agent are unaffected (P6, NFR-3). |
| Queue full (sustained backpressure) | `sdk_records_dropped_total` incremented; WARN (throttled) | Newest record dropped; memory stays bounded (NFR-4). Never blocks the agent. |
| Unknown model for cost | `cost_usd = None`; DEBUG once per model | Tokens still recorded; cost degrades to null, not an error (FR-9). |
| Interceptor raises | logged; counted | That interceptor is skipped for the record; chain continues. |
| Event listener raises | logged | Other listeners + the run continue (FR-8). |
| Misconfigured / missing integration | `ExporterNotRegisteredError` at `configure()` | Fail fast at bootstrap with the expected entry-point name, never mid-run. |
| Content-capture opt-in off | content fields absent from records | Default-safe (P7); not an error. |

## 9. Cost & performance characteristics

- **Hot path** (start/finish run, record a call): build an immutable record + run
  interceptors + enqueue — O(#interceptors), no I/O. Target **< 5 ms p99** (NFR-1).
- **Worker**: batches up to `max_export_batch_size` (default 512) every
  `schedule_delay` (default 5 s) or when a batch fills; one `export()` per exporter
  per batch.
- **Memory**: bounded by `max_queue_size` (default 2048 records) × record size;
  excess dropped (NFR-4).
- **Cost lookup**: O(1) dict hit on `(provider, model)` after one regex-normalise;
  pricing table loaded once.
- **Sampling**: head-based `TraceIdRatioBased` (config `sample_rate`) so a sampled
  run keeps its whole tree and an unsampled run emits nothing.

## 10. Cross-language parity

**Identical across Python / TypeScript (and future Java / Go):**

- The domain model + the four SPIs (§4).
- The OTel GenAI semconv mapping (feat-004) — same span names, attributes, metrics.
- The cost model + pricing-table schema (feat-006).
- Config keys (`FORGESIGHT_*` env, the YAML schema).
- The pipeline semantics (bounded queue, batch, fault isolation, flush/shutdown).

**Allowed to differ:** async primitives (`contextvars`/`asyncio` vs
`AsyncLocalStorage`/Promises), packaging (`uv` vs `pnpm`), the vendor SDK each
integration wraps, idiomatic naming.

**Staging:** Python lands first during 0.x; TypeScript targets parity by 0.4; Java
(Spring Boot starter) and Go follow. Tracked per feature via the `Languages` field.

## 11. Relationship to AgentForge

AgentForge's own observability feature (`agentforge-py` feat-009) and this SDK are
complementary, not competing. AgentForge depends only on `forgesight-api` and
emits through it; the deploying team chooses the backend by installing an integration
package. The SDK is the **standalone, framework-neutral telemetry layer**; AgentForge
is one (privileged-by-adapter, not privileged-in-core) consumer. This keeps
AgentForge free of vendor lock-in (its own requirement) and lets non-AgentForge
agents get the same telemetry.

## 12. Where to learn more

- [`../requirements.md`](../requirements.md) — requirements + traceability
- [`design-principles.md`](./design-principles.md) — the rules every feature follows
- [`otel-semantic-conventions.md`](./otel-semantic-conventions.md) — the OTel mapping
- [`exporter-pipeline.md`](./exporter-pipeline.md) — async, bounded, fault-isolated export
- [`cost-model.md`](./cost-model.md) — token → cost
- [`../adr/README.md`](../adr/README.md) — architectural decisions
- [`../features/README.md`](../features/README.md) — feature catalogue
