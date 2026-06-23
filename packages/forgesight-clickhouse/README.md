# forgesight-clickhouse

The ClickHouse exporter for [ForgeSight](https://github.com/Scaffoldic/forgesight).
Writes each ForgeSight record as one row in a **denormalized single-table MergeTree**,
batched columnar — so a platform team can *query* its agent telemetry in SQL: spend by
team, p99 LLM latency by model, tokens per workflow over 90 days.

```bash
pip install forgesight-clickhouse
```

```python
import forgesight
from forgesight_clickhouse import ClickHouseExporter

forgesight.configure(exporters=[
    ClickHouseExporter(
        dsn="clickhouse://user:pass@ch-host:8443/agents?secure=true",
        table="forgesight_records",
        create_table=True,   # dev convenience; production runs the shipped migration
    ),
])
```

Or by name: `exporters: [{name: clickhouse, config: {dsn: "${FORGESIGHT_CLICKHOUSE_DSN}"}}]`.

## Why a columnar exporter

A Record is written once and never updated (OTel's immutable span model), so trace-level
**business metadata is denormalized onto every row** — no join table, so analytical
queries never pay a join:

```sql
SELECT agent_name, quantile(0.99)(duration_ms)
FROM forgesight_records WHERE kind = 'llm' GROUP BY agent_name;

SELECT metadata.team, sum(cost_usd)
FROM forgesight_records
WHERE kind = 'llm' AND start_time >= now() - INTERVAL 30 DAY
GROUP BY metadata.team;
```

Inserts are **batched** by the SDK's export pipeline (ClickHouse hates row-at-a-time): one
columnar INSERT per batch, on the export worker, fault-isolated. A ClickHouse outage makes
`export()` return `FAILURE` (counted, never raised — P6); it never blocks the agent.

## Schema & migrations

The DDL ships in the package (`migrations/0001_init.sql`). `create_table=True` runs
`CREATE TABLE IF NOT EXISTS` on first export for dev; production applies the migration
out-of-band. New domain fields arrive as new nullable columns via numbered migrations —
old queries keep working.

## Configuration

| Key | Env | Default |
|---|---|---|
| `dsn` | `FORGESIGHT_CLICKHOUSE_DSN` | — (required) |
| `table` | `FORGESIGHT_CLICKHOUSE_TABLE` | `forgesight_records` |
| `batch_size` | `FORGESIGHT_CLICKHOUSE_BATCH_SIZE` | `512` (clamped to the pipeline max) |
| `async_insert` | `FORGESIGHT_CLICKHOUSE_ASYNC_INSERT` | `true` |
| `wait_for_async_insert` | `FORGESIGHT_CLICKHOUSE_WAIT_ASYNC` | `false` |
| `create_table` | `FORGESIGHT_CLICKHOUSE_CREATE_TABLE` | `false` |

Constructor kwargs win over env (FR-12). Prompt/response **content is never stored** in
the base table; it is gated SDK-wide by `capture_content` (off by default, P7).

## License

Apache-2.0
