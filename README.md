# ForgeSight

> Vendor-neutral, **OpenTelemetry-first** observability & execution-telemetry for AI
> agents. Instrument any agent in under 10 lines; ship traces, metrics, cost, and
> events to any backend — without vendor lock-in.

`forgesight` gives every agent — AgentForge, LangGraph, CrewAI, PydanticAI,
OpenAI Agents, Spring AI, or hand-written — one standard way to record **what it did
and what it cost**, and to export that anywhere (OTLP collectors, Langfuse,
Prometheus, ClickHouse, Datadog, Honeycomb, Arize Phoenix). The core depends on **no**
vendor or model-provider SDK; backends are dependencies you install, not decisions
baked into your agent.

> **Status: pre-alpha (0.x).** The docs and feature specs are being written first;
> implementation follows the catalogue in [`docs/features/README.md`](./docs/features/README.md).

## Why

- **Vendor neutral** — swap Langfuse → Datadog → your own sink with a `pip install`
  and one config line; never an agent-code change.
- **OpenTelemetry first** — the canonical wire format is the OTel GenAI semantic
  conventions, so anything that ingests OTLP works for free.
- **Non-blocking & fault tolerant** — telemetry export is async and isolated; a
  backend outage is invisible to your agent.
- **Cost built in** — token → cost via a pluggable, refreshable pricing table
  (input/output/cached/reasoning/tiered).
- **Secure by default** — prompt/response content is never captured unless you opt in.

## What it tracks

Agent runs · workflows · steps · LLM calls (tokens, cost, latency) · tool calls · MCP
calls · metrics · traces · cost · lifecycle events + arbitrary business metadata.

## Quick taste (target API)

```python
import forgesight as sdk

sdk.configure()  # zero-config: console/in-memory in dev; OTLP when configured

with sdk.telemetry.agent_run("issue-classifier", version="1.2.0") as run:
    with run.llm_call(provider="anthropic", model="claude-sonnet-4-5") as call:
        resp = client.messages.create(...)
        call.record_usage(input=resp.usage.input_tokens,
                          output=resp.usage.output_tokens)   # cost computed for you
    with run.tool_call("web_search", tool_type="function"):
        results = web_search(...)
# → one trace (invoke_agent → chat → execute_tool), metrics, cost, RUN_COMPLETED event
```

Add a backend without touching the code above:

```bash
pip install forgesight-otel        # any OTLP backend (Datadog, Honeycomb, Jaeger…)
pip install forgesight-langfuse    # Langfuse dashboard
pip install forgesight-prometheus  # /metrics endpoint
```

```yaml
# forgesight.yaml
exporters:
  - name: otel
    config: { endpoint: "http://otel-collector:4317", service_name: "my-agent" }
  - name: langfuse
    config: { public_key: "${LANGFUSE_PUBLIC_KEY}", secret_key: "${LANGFUSE_SECRET_KEY}" }
```

## Packages (three tiers + integrations)

| Package | Role |
|---|---|
| `forgesight-api` | Locked contracts: domain model + 4 SPIs. Zero vendor deps. |
| `forgesight-core` | Runtime: context, span tree, export pipeline, metrics, cost, events, interceptors, config. |
| `forgesight` | Batteries-included facade (`configure()`, `telemetry`, decorators). |
| `forgesight-otel` / `-prometheus` / `-langfuse` / `-clickhouse` / `-datadog` / `-mcp` / `-fastapi` / `-github` | One backend / integration each — install to enable. |

## Documentation

- [`docs/requirements.md`](./docs/requirements.md) — what it must do
- [`docs/design/architecture.md`](./docs/design/architecture.md) — how it works
- [`docs/design/`](./docs/design/) — principles, OTel mapping, pipeline, cost model
- [`docs/features/README.md`](./docs/features/README.md) — the feature catalogue
- [`docs/adr/README.md`](./docs/adr/README.md) — architectural decisions
- [`AGENTS.md`](./AGENTS.md) — contributor / AI-assistant rules

## License

Apache 2.0 (ADR-0009).
