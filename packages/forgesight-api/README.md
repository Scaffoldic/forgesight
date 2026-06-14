# forgesight-api

The locked contract layer for [ForgeSight](https://github.com/Scaffoldic/forgesight) —
the vendor-neutral, OpenTelemetry-first telemetry SDK for AI agents.

This package contains **only** the stable surface every other package implements:

- the **domain model** — `AgentRun`, `WorkflowRun`, `Step`, `LLMCall`, `ToolCall`,
  `MCPCall`, `TokenUsage`, the `Record`/`LifecycleEvent` value types, and the
  `RunStatus`/`Kind` enums;
- the four **SPIs** — `TelemetryExporter`, `Interceptor`, `EventListener`,
  `PricingProvider`.

It has **no I/O and no backend or model-provider dependencies** (stdlib +
`typing-extensions` only). AgentForge and third-party agents depend on this package
to stay free of vendor lock-in.

See the [feature spec](https://github.com/Scaffoldic/forgesight/blob/main/docs/features/feat-001-core-domain-model-and-contracts.md)
and [ADR-0002](https://github.com/Scaffoldic/forgesight/blob/main/docs/adr/0002-three-tier-vendor-neutral-packaging.md).

## License

Apache-2.0
