# forgesight-core

The runtime for [ForgeSight](https://github.com/Scaffoldic/forgesight) — the
vendor-neutral, OpenTelemetry-first telemetry SDK for AI agents.

Contains the instrumentation runtime built on the `forgesight-api` contracts:

- **context propagation** (`TelemetryContext` over `contextvars`),
- the **instrumentation scopes** (`agent_run` / `workflow_run` / `step` /
  `llm_call` / `tool_call` / `mcp_call`) and the `@instrument` decorator,
- the **dispatch** that runs interceptors and fans records out to exporters and
  events out to listeners,
- the shipped **`InMemoryExporter`** and **`ConsoleExporter`**.

Depends on `forgesight-api` and `opentelemetry-api` only — **no backend or
model-provider SDK** (P1).

## License

Apache-2.0
