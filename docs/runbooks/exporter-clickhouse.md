# ClickHouse exporter runbook

> Columnar batch insert of immutable ForgeSight records into a denormalized single-table MergeTree, so you can run analytical SQL over your agent telemetry. **Extra:** `pip install "forgesight[clickhouse]"` · **Selects with:** `exporters=["clickhouse"]` · **Spec:** [feat-014](../features/feat-014-clickhouse-exporter.md)

## What it does

`ClickHouseExporter` writes each immutable `Record` as one row in a denormalized MergeTree table (`forgesight_records` by default). Because a record is written once and never updated, trace-level business metadata is denormalized onto every row — no join table, so analytical queries (`sum(cost_usd) GROUP BY team`) never pay a join. It runs on the export worker (never the hot path) and issues one columnar `INSERT` per `batch_size` chunk of the batch the pipeline hands it.

## When to use it

- You want to **query** telemetry in SQL: spend by team, p99 latency by model, tokens per workflow over 90 days, every errored run on repo X.
- You run agents at scale and need a columnar, high-cardinality store over hundreds of millions of rows.
- You want one shared schema so "cost by model" means the same thing across teams.
- **Not** when you only want a trace UI or a dashboard — that is Grafana/Metabase/ClickHouse tooling on top of these rows, or use the Langfuse/Datadog/OTLP exporters instead. The exporter writes rows; it does not read them back.

## Install

```bash
pip install "forgesight[clickhouse]"      # extra on the umbrella package
# or the standalone integration package:
pip install forgesight-clickhouse
```

Installing makes the name `clickhouse` resolvable from config via the `forgesight.exporters` entry point. The package wraps exactly one vendor SDK, `clickhouse-connect` (>=0.7); that dependency lives only here, never on the core.

## Configure

Constructor (all keyword-only, all optional; env fills the gaps):

```python
ClickHouseExporter(
    dsn=None,                    # FORGESIGHT_CLICKHOUSE_DSN — required (or pass a client)
    table="forgesight_records",  # FORGESIGHT_CLICKHOUSE_TABLE
    batch_size=512,              # FORGESIGHT_CLICKHOUSE_BATCH_SIZE — clamped to pipeline max
    async_insert=True,           # FORGESIGHT_CLICKHOUSE_ASYNC_INSERT
    wait_for_async_insert=False, # FORGESIGHT_CLICKHOUSE_WAIT_ASYNC
    create_table=False,          # FORGESIGHT_CLICKHOUSE_CREATE_TABLE — dev convenience
)
```

| Key | Env | Default | Notes |
|---|---|---|---|
| `dsn` | `FORGESIGHT_CLICKHOUSE_DSN` | — (required) | `clickhouse://…` URL; secret, never logged |
| `table` | `FORGESIGHT_CLICKHOUSE_TABLE` | `forgesight_records` | validated identifier (optionally `db.table`) |
| `batch_size` | `FORGESIGHT_CLICKHOUSE_BATCH_SIZE` | `512` | rows per `INSERT`; clamped to pipeline `max_export_batch_size` with a WARN |
| `async_insert` | `FORGESIGHT_CLICKHOUSE_ASYNC_INSERT` | `true` | sets ClickHouse `async_insert` (server-side buffering) |
| `wait_for_async_insert` | `FORGESIGHT_CLICKHOUSE_WAIT_ASYNC` | `false` | `true` = stronger durability, higher latency |
| `create_table` | `FORGESIGHT_CLICKHOUSE_CREATE_TABLE` | `false` | runs the shipped DDL on first export (dev only) |

**DSN format:** `clickhouse://user:pass@host:port/db` — e.g. `clickhouse://forgesight:forgesight@localhost:8123/forgesight`, or `clickhouse://user:pass@ch-host:8443/agents?secure=true` for a TLS-fronted cluster.

Missing `dsn` (and no injected client) fails fast at `configure()`. The vendor driver is imported lazily and built from the DSN on first export, so construction never touches the network.

Select it by name with `exporter_config`:

```python
import forgesight

forgesight.configure(
    exporters=["clickhouse"],
    exporter_config={
        "clickhouse": {
            "dsn": "${FORGESIGHT_CLICKHOUSE_DSN}",   # clickhouse://user:pass@host:8123/db
            "table": "forgesight_records",
            "batch_size": 512,
            "async_insert": True,
            "create_table": True,                     # dev: emit DDL on first export
        },
    },
)
```

Equivalent `forgesight.yaml`:

```yaml
exporters:
  - name: clickhouse
    config:
      dsn: "${FORGESIGHT_CLICKHOUSE_DSN}"   # clickhouse://user:pass@host:8123/db
      table: "forgesight_records"
      batch_size: 512
      async_insert: true
      create_table: true                    # dev convenience; prod runs migrations
```

## What it emits

One row per record (run / step / LLM / tool / MCP call) into one MergeTree table. Column order is fixed and must match `migrations/0001_init.sql`:

| Column | Type | Source |
|---|---|---|
| `run_id` | `String` | record ULID |
| `trace_id` | `String` | W3C trace id |
| `parent_run_id` | `Nullable(String)` | `parent.run_id` attribute |
| `context_id` | `Nullable(String)` | `context.id` attribute |
| `kind` | `LowCardinality(String)` | `workflow\|agent\|step\|llm\|tool\|mcp` |
| `op` | `LowCardinality(String)` | `gen_ai.operation.name` (`chat`, `execute_tool`, `invoke_agent`, …) |
| `agent_name` | `LowCardinality(String)` | record name (agent kind only) |
| `agent_version` | `Nullable(String)` | `agent.version` attribute |
| `provider` | `LowCardinality(Nullable(String))` | LLM provider |
| `model` | `LowCardinality(Nullable(String))` | response model, else request model |
| `tool_name` | `Nullable(String)` | tool name, or MCP tool |
| `mcp_server` | `Nullable(String)` | MCP server |
| `mcp_method` | `LowCardinality(Nullable(String))` | MCP method |
| `input_tokens` … `total_tokens` | `Nullable(UInt32)` | LLM usage (`input`, `output`, `cache_read`, `cache_creation`, `reasoning`, `total`) |
| `cost_usd` | `Nullable(Float64)` | the SDK's computed `forgesight.usage.cost_usd` |
| `status` | `LowCardinality(String)` | `running\|ok\|error\|cancelled\|budget_exceeded\|guardrail` |
| `error_type` | `Nullable(String)` | error type, else non-ok status |
| `start_time` | `DateTime64(9)` | `start_unix_nanos` |
| `end_time` | `Nullable(DateTime64(9))` | `end_unix_nanos` |
| `duration_ms` | `Nullable(Float64)` | duration |
| `metadata` | `JSON` | denormalized business metadata (structured keys lifted to columns are excluded) |

Engine: `MergeTree`, `PARTITION BY toYYYYMM(start_time)`, `ORDER BY (trace_id, start_time, run_id)`. Each `export()` chunks the batch by `batch_size` and issues one columnar `INSERT` per chunk with `async_insert`/`wait_for_async_insert` passed as ClickHouse settings; with `async_insert` on, the server owns the buffer (so `force_flush` has nothing to flush and always returns `True`). Content (prompts/completions) is never stored unless `capture_content` is on SDK-wide and only after redaction.

## Operate it

Backend: a reachable ClickHouse, HTTP `:8123` or native `:9000`. The repo root `docker-compose.yml` ships a `clickhouse` service (`clickhouse/clickhouse-server:24.8`, HTTP `:8123`, native `:9000`, db/user/password all `forgesight`):

```bash
docker compose up -d clickhouse
```

Then point the exporter at `clickhouse://forgesight:forgesight@localhost:8123/forgesight`.

Apply the shipped DDL out-of-band for production (the package ships `migrations/0001_init.sql`); in dev, set `create_table: true` and the exporter runs `CREATE TABLE IF NOT EXISTS` on first export:

```bash
# apply the migration over HTTP
curl -s 'http://localhost:8123/?user=forgesight&password=forgesight&database=forgesight' \
  --data-binary @packages/forgesight-clickhouse/src/forgesight_clickhouse/migrations/0001_init.sql
```

Verify rows arrived after a run:

```sql
SELECT kind, count() AS rows, round(sum(cost_usd), 4) AS cost
FROM forgesight.forgesight_records
GROUP BY kind ORDER BY rows DESC;
```

New domain fields arrive as new nullable columns via numbered migration files — never a destructive change; old queries keep working.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `configure()` raises `requires a dsn` | no `dsn` and no `FORGESIGHT_CLICKHOUSE_DSN` | set the DSN; this is intentional fail-fast |
| `invalid ClickHouse table identifier` | bad `table` value | use a valid identifier, optionally `db.table` |
| No table / `UNKNOWN_TABLE` on export | DDL not applied | apply `0001_init.sql`, or set `create_table: true` in dev |
| `batch_size … clamping` WARN | `batch_size` > pipeline `max_export_batch_size` | lower `batch_size` or raise `max_export_batch_size` |
| Rows missing after a crash | `async_insert` buffer not yet flushed server-side | set `wait_for_async_insert: true` for stronger durability (higher latency) |
| ClickHouse unreachable; agent keeps running | the exporter is fault-isolated | expected — see the guarantee below |

**Non-blocking guarantee:** `export()` never raises (P6). A ClickHouse outage is caught, returns `ExportResult.FAILURE`, is counted by the pipeline (`sdk_export_failures_total`) and logged at WARN; the agent run is unaffected. Under sustained backpressure the pipeline drops newest, not this exporter.

## Reference

- Spec: [feat-014](../features/feat-014-clickhouse-exporter.md)
- Package: [`../../packages/forgesight-clickhouse`](../../packages/forgesight-clickhouse)
- Playbook: [install](../playbooks/01-install.md)
- Playbook: [run locally with Docker](../playbooks/03-run-locally-with-docker.md)
- Playbook: [ship to a backend](../playbooks/04-ship-to-a-backend.md)
