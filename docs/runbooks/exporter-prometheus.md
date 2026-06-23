# Prometheus exporter runbook

> Fold ForgeSight records into a Prometheus registry served on a pull `/metrics` endpoint (with optional Pushgateway for short-lived runs). **Extra:** `pip install "forgesight[prometheus]"` · **Selects with:** `exporters=["prometheus"]` · **Spec:** [feat-012](../features/feat-012-prometheus-exporter.md)

## What it does

`PrometheusExporter` derives product metrics and GenAI histograms from records into a `prometheus_client` `CollectorRegistry`, served on a pull `/metrics` HTTP endpoint (default `:9464`) and optionally pushed to a Pushgateway on flush/shutdown. Labels are cardinality-bounded by construction — fixed, low-cardinality label sets — and `run_id` / `trace_id` are never labels (that is what traces are for). Like every exporter it implements the `TelemetryExporter` Protocol, runs on the pipeline worker, and `export()` never raises (P6).

## When to use it

- You already run Prometheus / Grafana and want agent KPIs as time series (runs, failures, cost, durations, token usage).
- You want a scrapeable `/metrics` endpoint with no external dependency.
- Short-lived batch / CI runs: push to a Pushgateway on shutdown instead of being scraped.
- **Not** for traces or per-call spans — use `forgesight-otel` for that. Don't expect per-`run_id` series; cardinality is bounded on purpose.

## Install

```bash
pip install "forgesight[prometheus]"   # facade extra
pip install forgesight-prometheus       # standalone package
```

No sub-extras; pulls in `prometheus-client>=0.20`.

## Configure

Constructor (`forgesight_prometheus.PrometheusExporter`):

```python
PrometheusExporter(
    *,
    host: str = "0.0.0.0",
    port: int = 9464,                      # 0 disables the /metrics server
    prefix: str = "agentforge",            # metric-name prefix
    push_gateway: str | None = None,       # opt-in; push on flush/shutdown
    push_job: str = "forgesight",
    registry: CollectorRegistry | None = None,
)
```

Relevant env vars (resolved by the config layer, feat-010):

| Key (`exporters[].config`) | Env | Default |
| --- | --- | --- |
| `host` | `FORGESIGHT_PROMETHEUS_HOST` | `0.0.0.0` |
| `port` | `FORGESIGHT_PROMETHEUS_PORT` | `9464` |
| `prefix` | `FORGESIGHT_PROMETHEUS_PREFIX` | `agentforge` |
| `push_gateway` | `FORGESIGHT_PROMETHEUS_PUSH_GATEWAY` | `null` |
| `push_job` | `FORGESIGHT_PROMETHEUS_PUSH_JOB` | `forgesight` |

Minimal selection by name:

```python
import forgesight

forgesight.configure(
    exporters=["prometheus"],
    exporter_config={
        "prometheus": {
            "host": "0.0.0.0",
            "port": 9464,
            "prefix": "agentforge",
            # "push_gateway": "http://pushgateway:9091",  # short-lived runs
        }
    },
)
```

Equivalent `forgesight.yaml`:

```yaml
# forgesight.yaml
exporters: [prometheus]
exporter_config:
  prometheus:
    host: "0.0.0.0"
    port: 9464
    prefix: "agentforge"
    # push_gateway: "http://pushgateway:9091"   # opt-in, for short-lived runs
```

## What it emits

All series are prefixed with `prefix` (default `forgesight_`). Counters and histograms folded from records:

| Metric | Type | Labels |
| --- | --- | --- |
| `forgesight_agent_runs` | Counter | `agent_name`, `status` |
| `forgesight_agent_failures` | Counter | `agent_name`, `error_type` |
| `forgesight_agent_cost_usd` | Counter | `gen_ai_provider_name` |
| `forgesight_agent_duration_milliseconds` | Histogram | `agent_name`, `status` |
| `forgesight_tool_invocations` | Counter | `tool_name`, `status` |
| `forgesight_mcp_invocations` | Counter | `mcp_method_name`, `status` |
| `forgesight_gen_ai_client_token_usage` | Histogram (`TOKEN_BUCKETS`) | `gen_ai_provider_name`, `gen_ai_operation_name`, `gen_ai_token_type` |
| `forgesight_gen_ai_client_operation_duration_seconds` | Histogram (`DURATION_BUCKETS`) | `gen_ai_provider_name`, `gen_ai_operation_name` |

**Tokens** are observed per type (`input`, `output`, `cache_read`, `cache_creation`, `reasoning`) on the token-usage histogram with `gen_ai_operation_name="chat"`. **Cost** (`llm.cost_usd`) increments `forgesight_agent_cost_usd` keyed by provider. Agent duration is observed in milliseconds; the GenAI operation-duration histogram is in seconds.

## Operate it

The exporter starts its `/metrics` server lazily on first successful export. Bring up Prometheus with the repo's root `docker-compose.yml` `prometheus` service, which scrapes the agent's `:9464` and serves its UI on `:9090`:

```bash
docker compose up -d prometheus   # UI :9090, scrapes the agent on :9464
```

The bundled scrape config targets the host (`host.docker.internal:9464`), so run the agent on the host. **Verify** two ways: hit the agent's endpoint directly at <http://localhost:9464/metrics> and grep for `forgesight_agent_runs`, then in the Prometheus UI at <http://localhost:9090> query `forgesight_agent_runs` (and check **Status → Targets** shows the agent UP).

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `/metrics` not reachable | Server starts lazily on first export; or port couldn't bind (logged WARN) | Run at least one agent; check `:9464` free; set a different `port` |
| Prometheus target DOWN | Scraper can't reach the host | Ensure agent listens on `0.0.0.0:9464`; compose uses `host.docker.internal` |
| No metrics after a short CI run | Process exited before a scrape | Set `push_gateway` so flush/shutdown pushes |
| `Duplicated timeseries` on re-`configure()` | Two exporters sharing the default registry | Pass distinct `registry=` instances or configure once |
| Fewer series than expected | Bounded labels by design; `run_id`/`trace_id` are never labels | Use traces (`forgesight-otel`) for per-run detail |
| Export failures but agent keeps running | By design — fold errors are caught, counted, and logged; the run is unaffected | Inspect `sdk_export_failures_total` / logs |

## Reference

- Feature spec: [feat-012](../features/feat-012-prometheus-exporter.md)
- Package: [`packages/forgesight-prometheus`](../../packages/forgesight-prometheus)
- Playbooks: [install](../playbooks/01-install.md) · [run locally with Docker](../playbooks/03-run-locally-with-docker.md) · [ship to a backend](../playbooks/04-ship-to-a-backend.md)
