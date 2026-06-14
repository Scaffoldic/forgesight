# Feature catalogue — ForgeSight

> The full list of features that make up ForgeSight. Each row links to a
> `feat-NNN-{slug}.md` spec (template:
> [`/.claude/templates/feature.md`](../../../../.claude/templates/feature.md)).
>
> Numbers are **immutable** once assigned. A `dropped` row stays for history; a
> `deferred` row moves to the bottom of its section.

---

## Status legend

- `proposed` — listed here, spec drafted, not yet approved/implemented
- `accepted` — spec approved
- `in-progress` — implementation underway
- `shipped` — released in a tagged version
- `deferred` — agreed, not in current milestone
- `dropped` — decided against; row kept for history

## Versioning targets

- **0.1 — core + OTel.** The vendor-neutral heart: domain model + SPIs (feat-001),
  instrumentation runtime (feat-002), async export pipeline (feat-003), the
  OpenTelemetry exporter + GenAI semconv mapping (feat-004), metrics (feat-005), cost
  model (feat-006), event bus (feat-007), interceptors incl. redaction + content
  gating (feat-008), error tracking (feat-009), config + zero-config bootstrap
  (feat-010), and the testing/conformance harness (feat-011). At 0.1 an agent is
  instrumented in < 10 lines and ships traces+metrics+cost to any OTLP backend.
- **0.2 — backends + integrations.** First-party exporters (Prometheus, Langfuse,
  ClickHouse, Datadog), MCP instrumentation, FastAPI middleware, GitHub Actions
  bootstrap, and framework adapters (LangGraph / CrewAI / PydanticAI / AgentForge /
  Spring AI).
- **0.3 — governance.** Cost budgets + policy enforcement, agent evaluations + human
  feedback capture.
- **0.4 — registry & TypeScript parity.** Agent registry / catalogue / ownership /
  chargeback analytics; TypeScript reaches the 0.2 surface.
- **1.0 — stability bar.** Contracts frozen, semver enforced, full backend +
  governance stack, multi-language parity policy in force.

## The features

### Core (0.1)

| ID | Title | Status | Target | Languages | Package(s) |
|---|---|---|---|---|---|
| **feat-001** | Core domain model & SPI contracts (`AgentRun`/`LLMCall`/`ToolCall`/`MCPCall`/`WorkflowRun`/`Step` + `TelemetryExporter`/`Interceptor`/`EventListener`/`PricingProvider`) | shipped | 0.1 | both | `forgesight-api` |
| **feat-002** | Telemetry runtime & instrumentation API (context propagation, span tree, `agent_run`/`step`/`llm_call`/`tool_call`/`mcp_call`, decorators) | shipped | 0.1 | both | `forgesight-core`, `forgesight` |
| **feat-003** | Async export pipeline (bounded queue, batching, fault isolation, flush/shutdown) | shipped | 0.1 | both | `forgesight-core` |
| **feat-004** | OpenTelemetry exporter & GenAI semantic-convention mapping (OTLP traces+metrics; W3C propagation) | shipped | 0.1 | both | `forgesight-otel` |
| **feat-005** | Metrics & instruments (FR-6 product metrics + GenAI histograms) | shipped | 0.1 | both | `forgesight-core` |
| **feat-006** | Cost model & pricing registry (`PricingProvider`, tiered/cache pricing, refreshable table) | shipped | 0.1 | both | `forgesight-core` |
| **feat-007** | Event bus & lifecycle events (`EventListener` SPI; `RUN_STARTED`…`MCP_EXECUTED`) | shipped | 0.1 | both | `forgesight-core` |
| **feat-008** | Interceptors — PII redaction, content-capture gating, custom policy/audit | shipped | 0.1 | both | `forgesight-core` |
| **feat-009** | Error & exception tracking (type/message/stack/code; span status + `error.type`) | proposed | 0.1 | both | `forgesight-core` |
| **feat-010** | Configuration & zero-config bootstrap (`configure()`, env + YAML, entry-point auto-load) | proposed | 0.1 | both | `forgesight-core`, `forgesight` |
| **feat-011** | Testing & conformance harness (in-memory exporter, span-tree assertions, per-SPI conformance suites) | proposed | 0.1 | both | `forgesight-core`, `forgesight-testing` |

### Backends & exporters (0.2)

| ID | Title | Status | Target | Languages | Package(s) |
|---|---|---|---|---|---|
| **feat-012** | Prometheus exporter (pull `/metrics` + push-gateway) | proposed | 0.2 | both | `forgesight-prometheus` |
| **feat-013** | Langfuse exporter (OTLP ingest + native observation/cost mapping) | proposed | 0.2 | both | `forgesight-langfuse` |
| **feat-014** | ClickHouse exporter (columnar batch insert, immutable records) | proposed | 0.2 | both | `forgesight-clickhouse` |
| **feat-015** | Datadog exporter (DD APM / OTLP intake) + OTLP-native backend notes | proposed | 0.2 | both | `forgesight-datadog` |

### Protocols & integrations (0.2)

| ID | Title | Status | Target | Languages | Package(s) |
|---|---|---|---|---|---|
| **feat-016** | MCP instrumentation (client + server; `mcp.*` conventions; `tools/call` as `execute_tool`) | proposed | 0.2 | both | `forgesight-mcp` |
| **feat-017** | FastAPI integration (middleware + lifespan flush; request↔run correlation) | proposed | 0.2 | both | `forgesight-fastapi` |
| **feat-018** | GitHub Actions integration (one-line bootstrap; run↔commit/PR/job correlation) | proposed | 0.2 | both | `forgesight-github` |
| **feat-019** | Framework adapters (LangGraph, CrewAI, PydanticAI, OpenAI Agents, AgentForge, Spring AI) | proposed | 0.2 | both | `forgesight-adapters-*` |

### Governance (0.3)

| ID | Title | Status | Target | Languages | Package(s) |
|---|---|---|---|---|---|
| **feat-020** | Cost budgets & governance policies (budget interceptor, policy enforcement, kill-switch) | proposed | 0.3 | both | `forgesight-core`, `forgesight-governance` |
| **feat-021** | Agent evaluations & human feedback (eval result spans, feedback/score capture) | proposed | 0.3 | both | `forgesight-eval` |

### Platform (0.4)

| ID | Title | Status | Target | Languages | Package(s) |
|---|---|---|---|---|---|
| **feat-022** | Agent registry, ownership & chargeback analytics | proposed | 0.4 | both | `forgesight-registry` |

---

## Dependency order (critical path)

```
feat-001 (model + SPIs)
   ├── feat-002 (runtime) ── feat-003 (pipeline) ── feat-004 (OTel exporter)
   │        ├── feat-005 (metrics)
   │        ├── feat-006 (cost) ── feat-020 (budgets/governance)
   │        ├── feat-007 (events)
   │        ├── feat-008 (interceptors)
   │        └── feat-009 (errors)
   ├── feat-010 (config/bootstrap)
   └── feat-011 (testing/conformance)
feat-004 ──► feat-013 (Langfuse), feat-015 (Datadog)   [OTLP-derived]
feat-005 ──► feat-012 (Prometheus)
feat-003 ──► feat-014 (ClickHouse)
feat-002 ──► feat-016 (MCP), feat-017 (FastAPI), feat-018 (GitHub), feat-019 (adapters)
feat-007 ──► feat-021 (eval/feedback)
feat-002+010 ──► feat-022 (registry/chargeback)
```

Implement lowest-numbered `proposed` feature whose dependencies are all `shipped`.
feat-001 is the root; nothing ships before it.

## Cross-cutting (tracked here, not features)

- **Cross-language parity.** `architecture.md` §10. Python first; TS by 0.4.
- **Conformance suites.** Every SPI ships one (feat-011); each integration must pass.
- **Release train.** Coordinated bump across `-api` / `-core` / `-sdk` / integration
  packages (ADR-0008 territory; mirrors AgentForge ADR-0015).

## What's deliberately not here

- **No dashboard / UI** — emit only (requirements §11).
- **No telemetry hosting/storage** — the SDK is a client.
- **No general-purpose APM** — agent-domain focus; composes with OTel auto-instr.
- **No canonical price registry** — refreshable + overridable table, not the source of
  truth (requirements §11).
- **No framework-of-frameworks** — adapters observe, they don't orchestrate.
