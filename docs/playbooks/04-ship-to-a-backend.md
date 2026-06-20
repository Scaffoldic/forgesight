# Playbook 04 — Ship to a backend

> Goal: send telemetry to your real observability platform — and switch or fan out to several
> without touching agent code.

## The mental model

- **Install** a package → *enables* a backend.
- **Config** (`exporters=[…]`) → *selects* which enabled backends receive data.
- Agent code is unaware of either. Swap `otel` → `datadog` and nothing else moves.

## Choose your backend

| You use… | Install | `exporters=[…]` | Runbook |
|---|---|---|---|
| Honeycomb / Jaeger / Tempo / New Relic / Phoenix / any OTLP | `forgesight[otel]` | `["otel"]` | [otel](../runbooks/exporter-otel.md) |
| Langfuse | `forgesight[langfuse]` | `["langfuse"]` | [langfuse](../runbooks/exporter-langfuse.md) |
| Datadog | `forgesight[datadog]` | `["datadog"]` | [datadog](../runbooks/exporter-datadog.md) |
| ClickHouse | `forgesight[clickhouse]` | `["clickhouse"]` | [clickhouse](../runbooks/exporter-clickhouse.md) |
| Prometheus | `forgesight[prometheus]` | `["prometheus"]` | [prometheus](../runbooks/exporter-prometheus.md) |

> Already on an OTLP-native platform? You only ever need `otel` — point its `endpoint` at your
> collector. No dedicated package per vendor.

## Minimal config per backend

```python
# OTLP (any OTel platform)
forgesight.configure(exporters=["otel"], exporter_config={
    "otel": {"endpoint": "https://collector.example.com:4317", "headers": {"x-api-key": "…"}},
})

# Langfuse
forgesight.configure(exporters=["langfuse"], exporter_config={
    "langfuse": {"public_key": "pk-…", "secret_key": "sk-…", "region": "us"},
})

# Datadog (via the DD Agent's OTLP intake)
forgesight.configure(exporters=["datadog"], exporter_config={
    "datadog": {"transport": "agent", "agent_endpoint": "http://localhost:4317", "service": "my-agent"},
})
```

Secrets belong in env, not source — every backend reads its own (`DD_API_KEY`,
`LANGFUSE_*`, `FORGESIGHT_CLICKHOUSE_DSN`, …). See each runbook's **Configure** section.

## Fan out to several backends

Just list them. Each export is independent and fault-isolated — one backend being down can't
affect the others or your agent (P6).

```python
forgesight.configure(exporters=["otel", "langfuse", "datadog"])
```

## Production: config without code

The same binary reads `forgesight.yaml` and `FORGESIGHT_*` env, so prod selection is a config
change, not a deploy of new code:

```yaml
# forgesight.yaml
service_name: my-agent
exporters: [otel, langfuse]
exporter_config:
  otel:     { endpoint: "${OTEL_COLLECTOR}" }
  langfuse: { public_key: "${LANGFUSE_PUBLIC_KEY}", secret_key: "${LANGFUSE_SECRET_KEY}", region: us }
```

```bash
export FORGESIGHT_EXPORTERS=otel,datadog      # env overrides file; kwargs override env
```

## Don't lose data on exit

Export is asynchronous. Make sure the process flushes:

- Scripts / serverless: `forgesight.get_runtime().shutdown()` before exit.
- FastAPI: use `sdk_lifespan` ([playbook 05](./05-instrument-a-fastapi-service.md)).
- Tune queue/batch/timeout in the [export pipeline runbook](../runbooks/export-pipeline.md).

## Next

→ [05 — Instrument a FastAPI service](./05-instrument-a-fastapi-service.md) ·
[06 — GitHub Actions](./06-instrument-github-actions.md) ·
[07 — Governance & budgets](./07-governance-and-budgets.md)
