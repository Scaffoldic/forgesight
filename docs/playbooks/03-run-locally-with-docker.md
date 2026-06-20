# Playbook 03 — Run locally with Docker

> Goal: bring up real backends on your laptop and watch your agent's telemetry land in them.

The repo ships a [`docker-compose.yml`](../../docker-compose.yml) with the local-friendly
backends plus **Grafana** for dashboards. Start only what you need.

```bash
docker compose up -d jaeger        # OTLP traces  -> forgesight[otel]
docker compose up -d prometheus    # metrics      -> forgesight[prometheus]
docker compose up -d clickhouse    # analytics    -> forgesight[clickhouse]
docker compose up -d               # the whole stack (incl. Grafana)
```

| Service | Image | Ports | UI |
|---|---|---|---|
| `jaeger` | jaegertracing/all-in-one | 4317 (OTLP gRPC), 4318 (OTLP HTTP), 16686 | http://localhost:16686 |
| `prometheus` | prom/prometheus | 9090 | http://localhost:9090 |
| `clickhouse` | clickhouse/clickhouse-server | 8123 (HTTP), 9000 (native) | — (HTTP/CLI) |
| `grafana` | grafana/grafana | 3000 | http://localhost:3000 (anonymous, no login) |

> **OTLP/HTTP endpoint:** use the base URL `http://localhost:4318` (the exporter appends
> `/v1/traces`). For gRPC use `endpoint: "localhost:4317"` + `protocol: "grpc"` (needs the
> `forgesight-otel[grpc]` extra).

## A. Traces in Jaeger (OTLP)

```bash
docker compose up -d jaeger
pip install "forgesight[otel]"
```

```python
import forgesight
from forgesight import telemetry

forgesight.configure(
    service_name="order-agent",
    exporters=["otel"],
    exporter_config={"otel": {"endpoint": "http://localhost:4318", "protocol": "http/protobuf"}},
)

with telemetry.agent_run("order-agent", version="1.0.0") as run:
    with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
        call.record_usage(input=1200, output=300)
    with run.tool_call("lookup_order"):
        ...

forgesight.get_runtime().shutdown()        # flush before the script exits
```

**Verify:** open http://localhost:16686, pick service `order-agent`, **Find Traces**. You'll
see `invoke_agent order-agent` with child `chat …` and `execute_tool lookup_order` spans
(OTel GenAI semantic-convention names). Or hit the API:

```bash
curl -s "http://localhost:16686/api/traces?service=order-agent" | jq '.data | length'
```

## B. Metrics in Prometheus

```bash
docker compose up -d prometheus
pip install "forgesight[prometheus]"
```

```python
forgesight.configure(
    service_name="order-agent",
    exporters=["prometheus"],
    exporter_config={"prometheus": {"port": 9464}},   # serves /metrics on :9464
)
```

The bundled [`docker/prometheus.yml`](../../docker/prometheus.yml) scrapes
`host.docker.internal:9464` every 5s. Keep your agent process running, then open
http://localhost:9090 and query e.g. `agentforge_agent_runs_total`. See the
[Prometheus runbook](../runbooks/exporter-prometheus.md) for the full metric list.

## C. Analytics in ClickHouse

```bash
docker compose up -d clickhouse
pip install "forgesight[clickhouse]"
```

```python
forgesight.configure(
    exporters=["clickhouse"],
    exporter_config={"clickhouse": {
        "dsn": "clickhouse://forgesight:forgesight@localhost:9000/forgesight",
        "create_table": True,           # auto-create on first run for local dev
    }},
)
```

**Verify:**

```bash
docker exec -it forgesight-clickhouse clickhouse-client \
  --user forgesight --password forgesight \
  --query "SELECT count() FROM forgesight.agentforge_records"
```

See the [ClickHouse runbook](../runbooks/exporter-clickhouse.md) for the schema and the
production migration (don't rely on `create_table` in prod).

## D. Dashboards in Grafana

`docker compose up -d grafana` brings up Grafana at <http://localhost:3000> (anonymous
admin, no login) with **Prometheus** + **Jaeger** datasources and a starter *ForgeSight —
agent telemetry* dashboard (runs, cost, failures, p95 latency, cost-by-provider, tokens)
pre-provisioned. Point your agent at `exporters=["prometheus"]` (and `["otel"]` for traces),
run it, and the panels populate.

> ⚠️ **Short-lived agents under-count in Grafana.** Prometheus *pulls* `:9464` every 5s, but
> a one-shot agent process lives only a few seconds and its counters reset each run — so a
> scrape rarely lands. Traces (pushed per run) are always complete in Jaeger; the *pull
> metrics* need a long-lived target. Use the combined runner that keeps `:9464` up while
> Prometheus scrapes the accumulated totals:
> `uv run --no-sync python -m examples.agents.demo_all` (then refresh Grafana). In production,
> use the Prometheus **push-gateway** (`prometheus` exporter `push_gateway=…`) for short jobs.

## A complete, runnable example

[`examples/agents/`](../../examples/agents/) has three real **AWS Bedrock** agents (ReAct,
RAG, multi-agent) instrumented end-to-end — traces → Jaeger, metrics → Prometheus/Grafana,
hash-chained audit, attributed cost. [`examples/bedrock-e2e/`](../../examples/bedrock-e2e/) is
a single-call e2e, and [`examples/agentforge-agent/`](../../examples/agentforge-agent/) is the
framework-adapter showcase (validated offline **and** against a real OTLP→Jaeger backend).

## Tear down

```bash
docker compose down            # stop & remove containers
docker compose down -v         # also drop volumes (ClickHouse data)
```

## Next

→ [04 — Ship to a backend](./04-ship-to-a-backend.md) (your real platform, not localhost).
