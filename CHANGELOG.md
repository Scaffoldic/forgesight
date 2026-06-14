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
