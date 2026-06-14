# AGENTS.md — forgesight

> Repository conventions for any AI assistant editing this repo. Tool-agnostic —
> `CLAUDE.md` and any future tool-specific rules defer to this one.

## What this repo is

`forgesight` is a vendor-neutral, **OpenTelemetry-first** observability and
execution-telemetry SDK for AI agents, shipped as an open-source Python pip package
(TypeScript next; Java/Go on the roadmap). It tracks agent runs, LLM / tool / MCP
calls, workflows, metrics, cost, and events, and exports to any backend (OTLP
collectors, Langfuse, Prometheus, ClickHouse, Datadog, Honeycomb, Phoenix) with no
vendor lock-in.

It is the standalone telemetry layer AgentForge (the framework) and third-party
agents consume via the stable `forgesight-api` contracts.

The repo is a **uv workspace** whose member packages map to a three-tier model
(ADR-0002):

- `packages/forgesight-api/` — locked contracts: the domain model
  (`AgentRun`/`LLMCall`/`ToolCall`/`MCPCall`/`WorkflowRun`/`Step`) and the four SPIs
  (`TelemetryExporter`/`Interceptor`/`EventListener`/`PricingProvider`). **No I/O, no
  third-party SDKs.**
- `packages/forgesight-core/` — the runtime: context propagation, span tree,
  export pipeline, metrics, cost, events, interceptors, config, in-memory + console
  exporters. Depends on `-api` + the OTel **API** only; never a vendor SDK.
- `packages/forgesight/` — batteries-included facade (`configure()`, `telemetry`,
  decorators, entry-point auto-load). The package most users install.

Each integration (OTel, Prometheus, Langfuse, ClickHouse, Datadog, MCP, FastAPI,
GitHub) lands as its own `packages/forgesight-<x>/` directory.

## Hard rules

| # | Rule | Reference |
|---|---|---|
| 1 | `forgesight-api` imports nothing from `-core` or any integration. It is the leaf of the dependency graph. | P1, ADR-0002 |
| 2 | `forgesight-core` depends on `-api` + `opentelemetry-api` only — **no backend/model-provider SDK**. Vendor SDKs live only in their own integration package. | P1 (vendor-neutral core) |
| 3 | The domain model + the four SPIs are **stable surface**. Adding an optional field with a safe default is a minor bump; changing a signature or removing a field is a major bump + an ADR. | P5, ADR-0006 |
| 4 | **OpenTelemetry first.** New telemetry maps onto the GenAI semconv per `design/otel-semantic-conventions.md`. Don't invent attribute names; don't squat on `gen_ai.*`. Cost is the one sanctioned extension (`forgesight.usage.cost_usd`). | P4, ADR-0001/0005 |
| 5 | **Non-blocking & fault-tolerant.** The hot path never does I/O; `export()` returns failure, never raises; queues are bounded; one backend failing never affects the agent or other backends. | P6, ADR-0003 |
| 6 | **Secure by default.** Prompt/completion/argument content is captured only when `capture_content` is on. PII redaction is an interceptor. | P7, ADR-0007 |
| 7 | No magic numbers. Every threshold/timeout/queue-size/batch-size/sample-rate is a named config field with a documented default. | P8 |
| 8 | Type hints everywhere; `mypy --strict` is the gate. Test coverage ≥ 90% on every commit. Every SPI ships a conformance suite implementations must pass. | NFR-7, P10 |
| 9 | Async-first. The only background thread is the export worker (mirrors OTel `BatchSpanProcessor`). No threads for I/O elsewhere. | P9 |

## Anti-patterns reviewers will reject

- **A vendor SDK in `-core`'s dependencies** — wrong package; it belongs in an
  integration.
- **Inventing a `gen_ai.*` attribute** the spec hasn't shipped, or duplicating
  OpenInference `llm.*` conventions — map onto the real GenAI semconv (P4).
- **Inline network I/O on the hot path** — enqueue and return; export on the worker.
- **`export()` raising** — return `ExportResult.FAILURE`; the pipeline guards anyway.
- **Capturing prompt/response content by default** — opt-in only (P7).
- **An unbounded queue or "just retry forever"** — bounded queue, drop+count under
  backpressure (NFR-4).
- **Hard-coded model prices** — use the pricing table + `PricingProvider` (ADR-0005).

## Workflow

This project follows the workspace per-feature pipeline
([`/.claude/development-pipeline.md`](../../.claude/development-pipeline.md)) and is
otherwise self-contained.

### Reading order on session resume

1. This file (`AGENTS.md`)
2. `.claude/state/current.md` — live snapshot (create when you start tracking work)
3. `.claude/state/log.md` — append-only milestone history
4. [`docs/requirements.md`](./docs/requirements.md)
5. [`docs/design/architecture.md`](./docs/design/architecture.md)
6. [`docs/features/README.md`](./docs/features/README.md) — catalogue
7. The active `docs/features/feat-NNN-*.md` spec

### Branch + PR rules

- Branch from `main`: `feat/<NNN>-<slug>`, `fix/<slug>`, `docs/<slug>`,
  `chore/<slug>`. **`<NNN>` must match an existing `docs/features/feat-NNN-*.md`.** No
  invented numbers; non-feature work uses `chore/`/`docs/`/`fix/`.
- Every feature PR updates the spec's **Implementation status** section and adds/updates
  its **Runbook** section before merge.
- One feature = one branch = one PR. Conventional Commits. Squash-merge.
- Pre-commit (format, lint, `mypy --strict`, tests, coverage ≥ 90%) must pass; never
  `--no-verify` without explicit authorisation.

## How to add a new integration package

1. Create `packages/forgesight-<x>/` mirroring an existing member
   (`pyproject.toml`, `src/forgesight_<x>/`, `tests/unit/`).
2. Implement the relevant SPI; register via
   `[project.entry-points."forgesight.<category>"]`.
3. Pin against `forgesight-api ~= <current major>`; depend on `-core` + the one
   vendor SDK only.
4. Pass the SPI conformance suite (feat-011); add at least one unit test.

<!-- agentforge:custom -->
<!-- Project-specific instructions go below this line. Survives upgrades. -->
<!-- agentforge:end-custom -->
