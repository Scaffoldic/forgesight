# ForgeSight — Requirements

## Metadata

| Field | Value |
|---|---|
| **Title** | ForgeSight — product & engineering requirements |
| **Status** | draft |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Last updated** | 2026-06-14 |
| **Applies to version** | 0.x (pre-alpha) |

> This is the canonical requirements document for ForgeSight. It states
> *what* the SDK must do and the constraints it must hold to. The *how* lives in
> [`design/architecture.md`](./design/architecture.md) and the per-feature specs
> under [`features/`](./features/README.md). Where a requirement maps onto a
> feature, the feature id is named in the **Traceability** table (§12).

---

## 1. Overview

The ForgeSight is a vendor-neutral, extensible **observability and execution-
telemetry SDK for AI agents**. It gives any agent — regardless of framework, model
provider, or backend — a single, standard way to record what it did and how much it
cost, and to ship that telemetry anywhere.

The SDK records:

- **Agent runs** — one top-level execution, with identity, status, timing.
- **LLM calls** — provider, model, tokens (input / output / cached / reasoning),
  latency, cost.
- **Tool calls** — name, type, duration, status, error.
- **MCP calls** — server, method, tool, duration, success.
- **Workflow runs** — multi-step orchestration over agents / tools.
- **Metrics** — counters and histograms (runs, failures, cost, duration,
  invocations).
- **Traces** — a span tree per run, propagated across process and agent boundaries.
- **Cost** — derived from tokens × a pluggable pricing table.
- **Business events** — lifecycle events (`RUN_STARTED`, `LLM_EXECUTED`, …) plus
  arbitrary metadata.

…and **exports** all of it to one or more backends concurrently (OpenTelemetry
collectors, Langfuse, Prometheus, ClickHouse, Datadog, Honeycomb, Arize Phoenix, …),
with no backend able to break the agent.

The SDK is usable **standalone**, and also inside AgentForge, GitHub Actions,
FastAPI apps, Spring Boot apps, MCP servers, and multi-agent systems.

### 1.1 The problem it solves

Every team building agents re-invents the same observability glue: a bespoke logger,
a hand-rolled span tree, a copy-pasted token-to-cost calculation, a one-off Langfuse
or Datadog integration that always lags the product. The result is that **no two
agents are comparable**, swapping observability backends means a code rewrite, and
the integration is the first thing to rot. The landscape today (OpenLLMetry,
OpenInference, Langfuse SDK, Logfire) proves the appetite — but each is coupled to a
vendor's model or a particular framing. ForgeSight is the **neutral core**: it
speaks OpenTelemetry as its canonical wire format, owns the agent-specific concepts
the GenAI conventions don't yet stabilise (cost, run identity, governance), and lets
the vendor be a dependency, not a decision baked into your agent code.

### 1.2 Goals

- Instrument any agent in **< 10 lines of code**.
- Add or swap a backend with a **`pip install` + one config line** — never an agent-
  code change.
- Telemetry overhead **< 5 ms per operation** and **never blocking** the agent.
- Backend failure is **invisible** to the agent.
- **Stable contracts** that other code (notably AgentForge) can depend on across
  releases.

### 1.3 Non-goals

See §11. In short: the SDK is not a dashboard, not an APM vendor, not a
framework-of-frameworks, and does not host or store telemetry itself.

---

## 2. Design principles

These are load-bearing constraints. Every feature is checked against them; a feature
that violates one needs an ADR to justify the exception. The full treatment is in
[`design/design-principles.md`](./design/design-principles.md).

| # | Principle | What it means in practice |
|---|---|---|
| **P1** | **Vendor neutral** | The core (`forgesight-api` + `forgesight-core`) has **zero** dependency on OpenAI, Anthropic, Langfuse, Datadog, Grafana, or any backend SDK. Core defines contracts; vendors live behind them in separate packages. |
| **P2** | **Plug and play** | Capabilities are enabled by *installing a package*, not by editing core. `pip install forgesight-langfuse` is the entire integration step. |
| **P3** | **Framework agnostic** | Works with AgentForge, LangGraph, CrewAI, PydanticAI, OpenAI Agents, Spring AI, and hand-written agents. No framework is privileged in core. |
| **P4** | **OpenTelemetry first** | OTel is the canonical telemetry model. The SDK's domain model maps deterministically onto OTel traces / metrics / events using the **GenAI semantic conventions**. Every other backend derives from that mapping. |
| **P5** | **Stable contracts** | The SPI (exporter / interceptor / event-listener / domain model) is backward compatible across minor releases. Breaking it is a major version bump with an ADR. |
| **P6** | **Non-blocking & fault tolerant** | Telemetry export is asynchronous and isolated. The agent never blocks on, and never fails because of, telemetry. |
| **P7** | **Secure by default** | Prompt / completion / argument **content is not captured unless explicitly opted in**. PII redaction is a first-class interceptor. |

---

## 3. Functional requirements

> Requirement IDs (`FR-n`) are stable. The "Detail / acceptance" column is the
> testable statement. "Feature" links to the spec that delivers it (§12).

### FR-1 — Agent-run tracking

The SDK shall track, for each agent execution: **agent name, agent version, run id,
context id, trace id, parent run id, status, start time, end time, duration**.

- **Acceptance**: starting and finishing a run produces a record carrying all of the
  above; `run_id` is a stable, sortable, unique identifier (ULID); `trace_id` is a
  valid W3C trace id; `parent_run_id` links nested / spawned runs.
- **Status** is one of `running | ok | error | cancelled | budget_exceeded |
  guardrail`.

### FR-2 — Tool tracking

The SDK shall track each tool invocation: **tool name, tool type, duration, status,
error**. Tool type ∈ `{mcp, rest, database, function/internal, …}` (open set).

- **Acceptance**: a tool call records name + type + duration + terminal status; on
  failure it records the exception (see FR-7); tool calls nest under the run / step
  that issued them.

### FR-3 — LLM tracking

The SDK shall track each LLM interaction: **provider, model (requested + responded),
input tokens, output tokens, cached tokens, reasoning tokens, total tokens, cost,
latency**, plus request parameters (temperature, max tokens, top-p, …) and
**finish reasons**.

- **Acceptance**: token counts match the provider's billed counts when available;
  cost is computed from tokens × pricing (FR-9) or accepted pre-computed; latency and
  time-to-first-token (when streaming) are recorded.

### FR-4 — MCP tracking

The SDK shall capture MCP interactions: **server, method, tool, request count,
duration, success rate**, following the OTel MCP conventions (`mcp.method.name`,
`mcp.session.id`, …) and setting `gen_ai.operation.name = execute_tool` on
`tools/call` so MCP tool calls are uniform with native tool calls.

- **Acceptance**: an MCP `tools/call` produces one span carrying server, method,
  tool, and status; duration and success rate are derivable in metrics.

### FR-5 — Business metadata

The SDK shall let callers attach arbitrary key/value metadata (e.g.
`repository=agentforge`, `workflow=github-issue`, `environment=prod`,
`team=platform`) at run, step, or call scope, and propagate it onto spans / events.

- **Acceptance**: metadata set at run scope appears on every child span; metadata set
  at call scope appears only on that call.

### FR-6 — Metrics

The SDK shall expose, at minimum: `agent_runs_total`, `agent_failures_total`,
`agent_cost_total`, `agent_duration_ms`, `tool_invocations_total`,
`mcp_invocations_total`, plus the OTel GenAI histograms `gen_ai.client.token.usage`
and `gen_ai.client.operation.duration`.

- **Acceptance**: each metric is emitted with the documented unit and attribute set;
  the GenAI histograms use the spec's exact bucket boundaries.

### FR-7 — Error tracking

The SDK shall capture, on any failed operation: **exception type, message, stack
trace, error code** (when present), and shall set span status + `error.type`.

- **Acceptance**: a raised exception inside an instrumented operation records the
  type / message / stack and marks the span errored, without swallowing the exception
  from the caller (unless the caller is using a context manager that re-raises).

### FR-8 — Event publishing

The SDK shall publish lifecycle events: `RUN_STARTED`, `RUN_COMPLETED`,
`RUN_FAILED`, `STEP_STARTED`, `STEP_COMPLETED`, `LLM_EXECUTED`, `TOOL_EXECUTED`,
`MCP_EXECUTED` (open set), to any registered event listener.

- **Acceptance**: registering a listener receives every lifecycle event in order; a
  raising listener does not affect other listeners or the run (P6).

### FR-9 — Cost model

The SDK shall compute cost from token usage and a **pluggable, versioned pricing
table** keyed on `(provider, model)`, supporting input / output / cached / reasoning
token rates and tiered (context-dependent) pricing; and shall accept a provider-
supplied cost when present (which takes precedence).

- **Acceptance**: given known model + token counts, cost matches the pricing table;
  an unknown model degrades gracefully (records tokens, cost `null`, emits a warning);
  pricing tables are overridable by the caller.

### FR-10 — Interception / policy

The SDK shall provide an interceptor hook on the telemetry path for **PII detection /
redaction, content-capture gating, cost control, and custom auditing / policy
enforcement**, able to mutate or drop a record before export.

- **Acceptance**: an interceptor can redact a field, block content capture, or veto a
  record; interceptors run in registration order; a raising interceptor is isolated.

### FR-11 — Multi-backend export

The SDK shall export the same telemetry to **multiple backends concurrently**, each
configured independently, with per-backend isolation.

- **Acceptance**: configuring OTel + Langfuse + a custom exporter sends every record
  to all three; killing one backend's endpoint does not affect the others or the
  agent.

### FR-12 — Zero-config & declarative config

The SDK shall work with **zero configuration** (sensible defaults, in-process no-op /
console export) and shall accept declarative configuration via environment variables
and a config file, with constructor overrides taking precedence.

- **Acceptance**: `import forgesight; forgesight.configure()` works with no
  args; `FORGESIGHT_*` env vars and a YAML file configure exporters without code
  changes.

---

## 4. Non-functional requirements

### NFR-1 — High performance

Telemetry overhead **< 5 ms per operation** (p99, excluding the wrapped operation's
own work), measured by a benchmark in CI.

### NFR-2 — Non-blocking

All export is asynchronous. The hot path (start/finish a run, record a call) enqueues
and returns; it **never** performs network I/O inline. Agent execution must never
block on telemetry.

### NFR-3 — Fault tolerant

Failure of any backend (Langfuse, Prometheus, ClickHouse, …) — unreachable, slow,
erroring, or misconfigured — **must not** fail or stall agent execution. Bounded
queues drop under sustained backpressure rather than growing unbounded; drops are
counted and logged.

### NFR-4 — Scalable

Must sustain **100,000+ runs/day** and **millions of spans/day** on a single process
without unbounded memory growth, via batching + bounded queues + sampling.

### NFR-5 — Multi-language

Python first (this repo). The contracts are defined language-neutrally so TypeScript
(next), then Java and Go, can reach parity. Semantics are identical across languages;
idiomatic surface differs.

### NFR-6 — Footprint

The core install (`forgesight`) pulls **only** the OTel API + a handful of small
pure-Python deps. Backend SDKs are never transitive dependencies of the core.

### NFR-7 — Quality bar

Type-checked (`mypy --strict`), linted, **≥ 90 % test coverage**, every SPI ships a
**conformance suite** every implementation must pass.

---

## 5. Personas & primary use cases

| Persona | Need | Use case |
|---|---|---|
| **Agent developer** | "See what my agent did and what it cost" with minimal effort | Wrap an agent; get a span tree + cost + metrics on a local Phoenix / OTel collector. |
| **Platform / SRE** | Fleet-wide, comparable telemetry; swap backends centrally | Standardise every team's agents on the SDK; route to the org's collector; change backends without touching agents. |
| **Framework author (AgentForge)** | A stable telemetry contract to depend on | Depend on `forgesight-api`; emit through it; let the deploying team pick the backend. |
| **FinOps / governance** | Cost attribution, budgets, chargeback | Per-team / per-repo cost via business metadata; budgets and policies via interceptors. |
| **CI / automation** | Telemetry for agentic GitHub Actions | One-line bootstrap in a workflow; runs correlated to commit / PR / job. |

---

## 6. Canonical telemetry model (summary)

The domain model is small and OTel-shaped. Full definition in
[`design/architecture.md`](./design/architecture.md) §4 and feat-001.

- **AgentRun** → an OTel span (`invoke_agent`), the root of a run's trace.
- **WorkflowRun** → a span (`invoke_workflow`) that parents agent runs / steps.
- **Step** → an INTERNAL span representing one iteration / phase of a run.
- **LLMCall** → a child span (`chat` / `text_completion` / `embeddings`).
- **ToolCall** → a child span (`execute_tool`).
- **MCPCall** → a child span (`mcp.<method>` / `execute_tool` for `tools/call`).
- **Business metadata** → span attributes.
- **Metrics** → OTel metric instruments.
- **Events** → lifecycle events delivered to listeners and (optionally) OTel events.

---

## 7. Constraints

- **OpenTelemetry GenAI semantic conventions** are the source of truth for attribute,
  span, and metric identifiers. They currently live in the dedicated
  `open-telemetry/semantic-conventions-genai` repo and are all at **Development**
  stability with no tagged release — so the SDK **pins to a specific commit** and
  isolates the mapping behind one module (feat-004) it can re-pin without touching
  callers. See [`design/otel-semantic-conventions.md`](./design/otel-semantic-conventions.md).
- **`gen_ai.provider.name`** is the canonical provider discriminator; legacy
  `gen_ai.system` is emitted only as opt-in back-compat.
- **Cost is not standardised by OTel** — the SDK owns it (FR-9) and emits it as a
  clearly namespaced extension attribute, never claiming a `gen_ai.*` identifier the
  spec hasn't defined.
- **Python ≥ 3.11**, async-first core, `asyncio` (no threads for I/O except the export
  worker, mirroring OTel's `BatchSpanProcessor`).
- **License**: Apache 2.0.

---

## 8. Assumptions

- Callers either use a shipped framework adapter (feat-019) or call the SDK's
  instrumentation API directly; the SDK does not require monkey-patching to function.
- An OTLP-capable collector or backend is reachable in production; in dev the SDK
  degrades to console / in-memory export.
- Token counts come from the provider response where possible; where absent, the SDK
  may estimate (clearly flagged) but never fabricates billed counts.

---

## 9. Dependencies (external)

- `opentelemetry-api` / `opentelemetry-sdk` (core trace/metric primitives + OTLP).
- A pricing dataset (LiteLLM-style JSON, vendored + refreshable) for cost.
- Backend SDKs (Langfuse, prometheus-client, clickhouse-connect, datadog, …) — each
  **only** in its own integration package, never in core.

---

## 10. Acceptance / success criteria

The SDK is successful when:

1. Any agent is instrumented in **< 10 lines**.
2. A new exporter is added **without modifying core** (SPI + entry point only).
3. AgentForge consumes the SDK **without vendor lock-in** (depends on `-api` only).
4. Observability backends are **swapped without code changes** (config only).
5. Telemetry overhead stays **negligible** (NFR-1) and **non-blocking** (NFR-2).
6. A backend outage is **invisible** to the agent (NFR-3).

---

## 11. Out of scope

- **Building dashboards / a UI.** The SDK emits; visualisation lives in the backend
  (Phoenix, Langfuse, Grafana, …).
- **Hosting / storing telemetry.** The SDK is a client; it is not a backend.
- **Being an APM agent** for general application tracing — it focuses on the agent
  domain (though it composes with OTel auto-instrumentation).
- **Wrapping other agent frameworks' execution.** It *observes* them via adapters; it
  does not replace or orchestrate them.
- **Maintaining a global, authoritative LLM price list.** It ships a refreshable
  pricing table and makes it overridable; it is not the canonical pricing registry.
- **Alerting / anomaly detection.** Metrics are emitted; alerts are configured in the
  user's existing stack.

---

## 12. Traceability — requirements → features

| Requirement | Delivered by |
|---|---|
| FR-1 Agent-run tracking | feat-001, feat-002 |
| FR-2 Tool tracking | feat-001, feat-002 |
| FR-3 LLM tracking | feat-001, feat-002, feat-006 |
| FR-4 MCP tracking | feat-001, feat-016 |
| FR-5 Business metadata | feat-002 |
| FR-6 Metrics | feat-005 |
| FR-7 Error tracking | feat-009 |
| FR-8 Event publishing | feat-007 |
| FR-9 Cost model | feat-006 |
| FR-10 Interception / policy | feat-008, feat-020 |
| FR-11 Multi-backend export | feat-003, feat-004, feat-012…015 |
| FR-12 Zero-config & declarative config | feat-010 |
| NFR-1/2/3/4 Perf / non-block / fault / scale | feat-003 |
| NFR-5 Multi-language | architecture §10 (parity policy) |
| NFR-7 Conformance | feat-011 |

---

## 13. References

- [`design/architecture.md`](./design/architecture.md) — system architecture
- [`design/design-principles.md`](./design/design-principles.md) — the rules
- [`design/otel-semantic-conventions.md`](./design/otel-semantic-conventions.md) — the OTel mapping
- [`design/exporter-pipeline.md`](./design/exporter-pipeline.md) — async export
- [`design/cost-model.md`](./design/cost-model.md) — token → cost
- [`features/README.md`](./features/README.md) — feature catalogue
- OpenTelemetry GenAI semconv: <https://github.com/open-telemetry/semantic-conventions-genai>
- Prior art: OpenLLMetry (Traceloop), OpenInference (Arize Phoenix), Langfuse, Pydantic Logfire
