# forgesight-datadog

The Datadog exporter for [ForgeSight](https://github.com/Scaffoldic/forgesight).
Surfaces agent telemetry in **Datadog APM** with the unified `service` / `env` / `version`
tags, LLM / tool / MCP calls as child spans, and the SDK's **computed cost** as the
monitorable DD metric `forgesight.cost_usd` — the same number every other backend reports.

```bash
pip install forgesight-datadog
```

```python
import forgesight
from forgesight_datadog import DatadogExporter

forgesight.configure(exporters=[
    DatadogExporter(api_key="...", site="datadoghq.com",
                    service="issue-classifier", env="prod"),
])
```

Or by name: `exporters: [{name: datadog, config: {api_key: "${DD_API_KEY}", service: ...}}]`.

## Two transports

- **`agent`** (default) — maps each record to a DD APM span via `ddtrace` and writes it to a
  local DD Agent (`agent_endpoint: http://datadog-agent:8126`), plus emits cost/token DD
  metrics. Direct intake (no `agent_endpoint`) requires `api_key`.
- **`otlp`** — sends OTLP/HTTP to the DD Agent's OTLP port (`agent_endpoint: http://datadog-agent:4318`)
  with the DD unified tags applied as resource attributes. Reuses `forgesight-otel`.

A DD Agent / intake outage makes `export()` return `FAILURE` (counted, never raised — P6);
it never blocks the agent. Prompt/response content is attached only with
`capture_content=True` (off by default, P7).

## OTLP-native backends need **no package**

Because the domain model maps cleanly onto the OTel GenAI conventions, anything that ingests
OTLP works through `forgesight-otel` with **no dedicated package** — point it at the backend
and you're done:

| Backend | How to send |
|---|---|
| Honeycomb | `forgesight-otel` → `api.honeycomb.io:443` + `x-honeycomb-team` header |
| Jaeger / Tempo / SigNoz | `forgesight-otel` → its OTLP collector |
| New Relic | `forgesight-otel` → `otlp.nr-data.net:4317` + `api-key` header |
| AWS X-Ray | `forgesight-otel` → ADOT collector |
| Arize Phoenix | `forgesight-otel` → Phoenix OTLP endpoint |

Datadog earns a package **only** because its richest path (DD-native APM tagging +
cost-as-DD-metric) is DD-specific. A team that only wants generic spans in Datadog can use
the OTLP path and skip this package entirely.

## Configuration

| Key | Env | Default |
|---|---|---|
| `api_key` | `DD_API_KEY` / `FORGESIGHT_DATADOG_API_KEY` | — (required for direct intake) |
| `site` | `DD_SITE` / `FORGESIGHT_DATADOG_SITE` | `datadoghq.com` |
| `service` | `DD_SERVICE` / `FORGESIGHT_DATADOG_SERVICE` | `agentforge` |
| `env` | `DD_ENV` / `FORGESIGHT_DATADOG_ENV` | — |
| `version` | `DD_VERSION` / `FORGESIGHT_DATADOG_VERSION` | — |
| `agent_endpoint` | `FORGESIGHT_DATADOG_AGENT_ENDPOINT` | — |
| `transport` | `FORGESIGHT_DATADOG_TRANSPORT` | `agent` |

Constructor kwargs win over env (FR-12).

## License

Apache-2.0
