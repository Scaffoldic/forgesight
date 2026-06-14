# Changelog

All notable changes to ForgeSight are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/) once it reaches 1.0.

## [Unreleased]

### Added

- Project bootstrap: documentation set (requirements, architecture, design docs,
  9 ADRs, 22 feature specs), `uv` workspace skeleton, Apache-2.0 license, CI
  (ruff + mypy + pytest, coverage ≥ 90% on Python 3.11–3.13), and the
  `forgesight-api` package skeleton.
- **feat-001 — `forgesight-api` contracts.** The locked telemetry domain model
  (`AgentRun`, `WorkflowRun`, `Step`, `LLMCall`, `ToolCall`, `MCPCall`,
  `TokenUsage`, `Content`), the immutable exporter-facing `Record` +
  `LifecycleEvent` + `ExportResult` + `EventType`, the `RunStatus`/`Kind` enums,
  ULID/W3C id helpers, and the four `runtime_checkable` SPIs (`TelemetryExporter`,
  `Interceptor`, `EventListener`, `PricingProvider`). 100% test coverage.
- **feat-002 — `forgesight-core` runtime + `forgesight` facade.** Context
  propagation (`TelemetryContext` over `contextvars`); the instrumentation scopes
  (`agent_run`/`workflow_run`/`step`/`llm_call`/`tool_call`/`mcp_call`, sync +
  async) and the `@instrument` decorator; the fault-isolated dispatch runtime;
  `InMemoryExporter` + `ConsoleExporter`; and a minimal zero-config `configure()`.
  Instrument an agent in under 10 lines. 96.5% coverage.
- **feat-003 — async export pipeline.** Replaced the synchronous dispatch with a
  bounded queue + background worker + batching, fault-isolated per exporter, with
  head-based sampling and graceful `force_flush`/`shutdown` (`atexit`-registered) —
  behind the same `emit_record`/`emit_event` surface. Adds a `sync_export` mode for
  deterministic tests. 97.7% coverage.
- **feat-004 — `forgesight-otel` OpenTelemetry exporter.** Maps records onto OTLP
  spans via the GenAI semantic conventions (`gen_ai.provider.name` canonical; cost as
  the `forgesight.usage.cost_usd` extension; content opt-in; `error.type` on failure),
  pinned + version-stamped. W3C TraceContext inject/extract helpers. One package
  unlocks any OTLP backend (Datadog, Honeycomb, Jaeger, Tempo, Phoenix, …). Metrics
  follow in feat-005.
- **feat-005 — metrics & instruments.** FR-6 product metrics under `forgesight.*`
  (runs / failures / cost / duration / tool & mcp invocations) plus the OTel GenAI
  histograms (`gen_ai.client.token.usage` by token type, operation/workflow/mcp
  durations) with the spec's exact bucket boundaries — all derived automatically from
  the runtime's record stream. `opentelemetry-sdk` added to core.
- **feat-006 — cost model & pricing registry.** `TablePricingProvider` over a
  vendored, refreshable LiteLLM-style table (input/output/cache/reasoning rates +
  tiered context pricing); model-name resolution with aliases + overrides; default in
  `configure()`. Cost emitted as `forgesight.usage.cost_usd`, rolled into
  `forgesight.agent.cost_total`; unknown models degrade to `cost=None`.
- **feat-007 — event bus & lifecycle events.** Ordered, fault-isolated delivery of
  `RUN_STARTED`…`MCP_EXECUTED` to registered `EventListener`s; `LifecycleEvent`
  enriched with `trace_id`/`span_id`; `deliver_step_events` toggle; listener-error
  counter. A raising listener never affects the run or siblings.
- **feat-008 — interceptors.** Built-in `ContentCaptureGate` (secure-by-default:
  strips content unless `capture_content` is on; always prepended) and
  `PIIRedactionInterceptor` (key + regex redaction, recursive, runs once before
  fan-out so every backend is scrubbed). Custom interceptors mutate/redact/veto via
  the SPI; a raising interceptor is isolated.
- **feat-009 — error & exception tracking.** Captures exception type/message/stack/
  code into an `ErrorInfo` on the record, sets `RunStatus.ERROR` + the stable
  `error.type` span attribute, emits `RUN_FAILED`, and **re-raises** (never swallows,
  FR-7). `record_error()` opt-out for handled paths; `stack_capture_depth` /
  `capture_stacktrace` config.
- **feat-010 — configuration & zero-config bootstrap.** `configure()` works with zero
  args; layered file (`forgesight.yaml` + `${ENV}`) → env (`FORGESIGHT_*`) → kwargs;
  named exporters/interceptors/listeners/pricing resolved via `forgesight.<group>`
  entry points + in-process `register()`; fail-fast `*NotRegisteredError` on unknown
  names. (Dataclass-based config; `pyyaml` added to core.)
- **feat-011 — testing & conformance harness.** `forgesight.testing`: `InMemoryExporter`,
  `assert_span_tree` / `find_span` / `find_spans`, record factories, and the four
  per-SPI conformance suites (`run_*_conformance`) — the executable contract every
  exporter/interceptor/listener/pricing-provider must pass (P10). **Completes the 0.1
  core (feat-001 … feat-011).**
