# Playbook 03 — Run locally with Docker

> Goal: bring up real backends on your laptop and watch your agent's telemetry land in them.

The repo ships a [`docker-compose.yml`](../../docker-compose.yml) with the three
local-friendly backends. Start only what you need.

```bash
docker compose up -d jaeger        # OTLP traces  -> forgesight[otel]
docker compose up -d prometheus    # metrics      -> forgesight[prometheus]
docker compose up -d clickhouse    # analytics    -> forgesight[clickhouse]
docker compose up -d               # the whole stack
```

| Service | Image | Ports | UI |
|---|---|---|---|
| `jaeger` | jaegertracing/all-in-one | 4317 (OTLP gRPC), 4318 (OTLP HTTP), 16686 | http://localhost:16686 |
| `prometheus` | prom/prometheus | 9090 | http://localhost:9090 |
| `clickhouse` | clickhouse/clickhouse-server | 8123 (HTTP), 9000 (native) | — (HTTP/CLI) |

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

## A complete, runnable example

[`examples/agentforge-agent/`](../../examples/agentforge-agent/) is a full AgentForge agent
instrumented with ForgeSight, validated end-to-end **offline** (in-memory + console) and
against a **real OTLP→Jaeger** backend (`agent_otlp.py` + its own `docker-compose.yml`). Read
its README for the cross-workspace run recipe.

## Tear down

```bash
docker compose down            # stop & remove containers
docker compose down -v         # also drop volumes (ClickHouse data)
```

## Next

→ [04 — Ship to a backend](./04-ship-to-a-backend.md) (your real platform, not localhost).
