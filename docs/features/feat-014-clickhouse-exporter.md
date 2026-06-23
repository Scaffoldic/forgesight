# feat-014: ClickHouse exporter

## Metadata

| Field | Value |
|---|---|
| **ID** | feat-014 |
| **Title** | ClickHouse exporter — columnar batch insert of immutable records |
| **Status** | `proposed` |
| **Owner** | kjoshi |
| **Created** | 2026-06-14 |
| **Target version** | 0.2 |
| **Languages** | both |
| **Module package(s)** | `forgesight-clickhouse` |
| **Depends on** | feat-003 |
| **Blocks** | none |

---

## 1. Why this feature

A platform team running agents at scale eventually wants to *query* its telemetry,
not just stare at a dashboard: "spend by team last month," "p99 LLM latency by
model," "every errored run on repo X," "tokens per workflow over 90 days." Those
are analytical, columnar, high-cardinality questions over hundreds of millions of
rows — exactly what ClickHouse is built for, and exactly what Langfuse itself
stores agent traces in.

Today a team that wants this writes the boring, error-prone part by hand: a table
schema, a batching insert loop, an async-insert tuning pass, a migration script,
and a mapping from the SDK's records onto columns. They get the batch sizing
wrong (row-at-a-time inserts murder ClickHouse), or they normalise trace metadata
into a join table (and then every analytical query pays a join), or they pick
column types that don't compress.

This package ships the schema and the batched columnar insert so a team gets a
queryable, denormalised, MergeTree-backed record store with a `pip install` and a
DSN — and the insert path reuses the SDK's pipeline so it is non-blocking and
fault-isolated like every other exporter.

## 2. Why this belongs in the SDK ecosystem (vs each team integrating the backend by hand)

- **The record is the SDK's immutable value type, and that is what makes the
  schema clean.** Exporters consume **Records** — the immutable, exporter-facing
  snapshot produced when an operation starts/ends (`architecture.md` §3). That
  immutability is *why* a denormalised single-table schema fits: a row is written
  once and never updated, so trace-level metadata can be copied onto every child
  row (Langfuse-style) without the update anomalies a mutable model would suffer.
  This maps onto OTel's immutable span model exactly. A team deriving its own
  schema would have to re-discover that the record is append-only.
- **One schema makes telemetry comparable and queryable across teams.** If every
  team invents its own ClickHouse table, no two teams' "cost by model" query is
  the same and a platform-wide rollup is impossible — the same fragmentation the
  SDK exists to kill (requirements §1.1). Shipping one DDL means one query shape
  works everywhere.
- **The insert path is the pipeline's, not the team's.** Columnar inserts must be
  *batched* (ClickHouse hates row-at-a-time). The SDK's export pipeline already
  delivers records to `export()` in batches sized by `max_export_batch_size`
  (`exporter-pipeline.md` §4.3), on a worker, fault-isolated. This exporter rides
  that — a ClickHouse outage is caught, counted in `sdk_export_failures_total`,
  and invisible to the agent (P6 / NFR-3). A hand-rolled inserter usually blocks a
  run on a slow insert or loses the isolation.
- **Anti-pattern it prevents:** the per-team migration script + row-at-a-time
  insert loop + bespoke column mapping that rots the moment the domain model gains
  a field — the exact glue the SDK replaces.

This is a textbook first-party package: it adds value the raw OTLP path can't — a
**columnar schema** (`architecture.md` §2). It is not on the core; it wraps
exactly one vendor SDK (P1).

## 3. How consuming agents/teams benefit

- **Before:** a team writes a CREATE TABLE by hand, a batching insert wrapper, an
  async-insert tuning pass, and a column mapping — then maintains a migration
  script as the model evolves. First production load reveals the inserts were
  row-at-a-time.
- **After:** `pip install forgesight-clickhouse`, point it at a DSN, run the
  shipped DDL (or let the exporter create the table). Every run, step, LLM/tool/MCP
  call lands as a row in a denormalised MergeTree, batched, queryable in SQL.
- **Queries that were impossible become one statement:** `SELECT team,
  sum(cost_usd) FROM forgesight_records WHERE kind='llm' AND ts >= now() -
  INTERVAL 30 DAY GROUP BY team` — because `team` (business metadata) is
  denormalised onto every row.
- **Swapping/adding is config.** Run ClickHouse for analytics *and* Langfuse for
  review *and* OTLP to the org collector from the same run (FR-11); drop any one
  with a config edit, no agent-code change (requirements §10.4).
- **The schema evolves safely.** New optional domain fields land as new nullable
  columns via a shipped migration; old queries keep working (P5).

## 4. Feature specifications

### 4.1 User-facing experience

```bash
pip install forgesight-clickhouse
```

```python
# python
import forgesight
forgesight.configure()      # resolves "clickhouse" from the exporters list

# or explicit
from forgesight_clickhouse import ClickHouseExporter
forgesight.configure(exporters=[
    ClickHouseExporter(dsn="clickhouse://user:pass@ch-host:8443/agents?secure=true",
                       table="forgesight_records"),
])
```

```yaml
# forgesight.yaml — preferred
exporters:
  - name: clickhouse
    config:
      dsn: "${FORGESIGHT_CLICKHOUSE_DSN}"   # clickhouse://user:pass@host:8443/db
      table: "forgesight_records"
      batch_size: 512                            # aligns with the pipeline
      async_insert: true
```

```typescript
// typescript
import { configure } from '@agentforge/sdk';
import { ClickHouseExporter } from '@agentforge/sdk-clickhouse';
configure({ exporters: [new ClickHouseExporter({ dsn: process.env.FORGESIGHT_CLICKHOUSE_DSN!, table: 'forgesight_records' })] });
```

### 4.2 Public API / contract

`ClickHouseExporter` implements the locked `TelemetryExporter` Protocol
(`architecture.md` §4.2), registered under the entry point name `clickhouse`, and
must pass the exporter conformance suite (feat-011).

```python
# forgesight_clickhouse/exporter.py
from collections.abc import Sequence
from forgesight_api import Record, ExportResult, TelemetryExporter

class ClickHouseExporter(TelemetryExporter):
    """Columnar batch insert of immutable Records into a denormalized
    single-table MergeTree. Stable from v0.2."""

    def __init__(
        self,
        *,
        dsn: str,
        table: str = "forgesight_records",
        batch_size: int = 512,            # ≤ pipeline max_export_batch_size
        async_insert: bool = True,        # ClickHouse async_insert setting
        wait_for_async_insert: bool = False,
        create_table: bool = False,       # emit the DDL on first export if missing
    ) -> None: ...

    # --- TelemetryExporter Protocol (locked) ---
    def export(self, records: Sequence[Record]) -> ExportResult: ...   # one batched INSERT
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
    def shutdown(self, timeout_millis: int = 30_000) -> None: ...      # close the client
```

`export()` issues **one** batched columnar INSERT for the batch and returns
`SUCCESS`/`FAILURE` (never raises — P6).

**Stability:** class name, constructor keywords, config keys, and the column
contract (§4.3) are stable from v0.2; new domain fields arrive as new nullable
columns + a migration (P5).

### 4.3 Internal mechanics

**One denormalised, immutable, MergeTree table.** Because a Record is written once
and never updated (`architecture.md` §3, OTel's immutable span model), trace-level
metadata is **denormalised onto every row** (Langfuse-style) — no join table, so
analytical queries never pay a join.

```
records (batch from the pipeline worker)
   │  one INSERT per batch (columnar), async_insert on
   ▼
ClickHouse  ──  table: forgesight_records  (MergeTree)
   PARTITION BY toYYYYMM(start_time)
   ORDER BY (trace_id, start_time, run_id)        -- locality for trace-tree scans
   TTL start_time + INTERVAL <retention> DELETE   -- optional, configurable
```

**Schema (shipped DDL).** One row per record (run / step / LLM / tool / MCP call):

```sql
CREATE TABLE IF NOT EXISTS forgesight_records (
    run_id           String,                       -- ULID
    trace_id         String,                       -- W3C trace id
    parent_run_id    Nullable(String),
    context_id       Nullable(String),
    kind             LowCardinality(String),       -- workflow|agent|step|llm|tool|mcp
    op               LowCardinality(String),       -- gen_ai.operation.name (chat|execute_tool|…)
    agent_name       LowCardinality(String),
    agent_version    Nullable(String),
    provider         LowCardinality(Nullable(String)),  -- gen_ai.provider.name
    model            LowCardinality(Nullable(String)),  -- request/response model
    tool_name        Nullable(String),
    mcp_server       Nullable(String),
    mcp_method       LowCardinality(Nullable(String)),
    input_tokens     Nullable(UInt32),
    output_tokens    Nullable(UInt32),
    cache_read_tokens     Nullable(UInt32),
    cache_creation_tokens Nullable(UInt32),
    reasoning_tokens Nullable(UInt32),
    total_tokens     Nullable(UInt32),
    cost_usd         Nullable(Float64),            -- forgesight.usage.cost_usd
    status           LowCardinality(String),       -- running|ok|error|cancelled|budget_exceeded|guardrail
    error_type       Nullable(String),
    start_time       DateTime64(9),                -- start_unix_nanos
    end_time         Nullable(DateTime64(9)),
    duration_ms      Nullable(Float64),
    metadata         JSON                          -- denormalized business metadata (FR-5)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(start_time)
ORDER BY (trace_id, start_time, run_id);
```

**Record → column mapping.** Identity/timing/status columns are populated for
every kind; `provider`/`model`/`*_tokens`/`cost_usd` for `llm`;
`tool_name` for `tool`; `mcp_*` for `mcp`. Business metadata is written as a `JSON`
column (denormalised — set at run scope, copied onto child rows). `cost_usd` is
the SDK's computed `forgesight.usage.cost_usd` (feat-006). Content (prompts/
completions) is **not** stored unless `capture_content` is on (P7) and, even then,
only after the redaction interceptor (feat-008); when stored it is an opt-in
`Nullable(String)` JSON column (added by migration, off by default).

**Batching aligns with the pipeline.** `export()` receives a batch sized by
`max_export_batch_size` (`exporter-pipeline.md` §4.3, §4.8); `batch_size` is
capped at that. `async_insert: true` lets ClickHouse buffer server-side for
throughput; `wait_for_async_insert: false` keeps `export()` from blocking the
worker. A ClickHouse outage → `export()` returns `FAILURE`, counted, never raised
(P6); under sustained backpressure the *pipeline* drops newest (NFR-4), not this
exporter.

**Migrations.** DDL ships in the package (`migrations/0001_init.sql`).
`create_table: true` runs `CREATE TABLE IF NOT EXISTS` on first export for dev;
production runs the migration out-of-band. New domain fields → new nullable
columns via numbered migration files; never a destructive change (P5).

### 4.4 Module packaging

An **integration package — one backend, one vendor SDK** (`architecture.md` §5),
wrapping exactly **one** vendor SDK: `clickhouse-connect`. Per P1 this dependency
is **never** added to `forgesight-core`; it lives only here.

| Package | Provides | Deps |
|---|---|---|
| `forgesight-clickhouse` | `ClickHouseExporter` + shipped DDL / migrations | `forgesight-core`, `clickhouse-connect` |

```toml
# forgesight_clickhouse/pyproject.toml
[project]
dependencies = ["forgesight-core>=0.2", "clickhouse-connect>=0.7"]

[project.entry-points."forgesight.exporters"]
clickhouse = "forgesight_clickhouse.exporter:ClickHouseExporter"
```

Installing makes `clickhouse` resolvable by name from config (`architecture.md`
§6, path 1). No core change.

### 4.5 Configuration

`exporters[].config` + `FORGESIGHT_*` env; constructor kwargs win (FR-12).
Named + defaulted (P8).

| Key | Env | Default | Validation |
|---|---|---|---|
| `dsn` | `FORGESIGHT_CLICKHOUSE_DSN` | — (required) | `clickhouse://…` URL; secret never logged |
| `table` | `FORGESIGHT_CLICKHOUSE_TABLE` | `forgesight_records` | valid ClickHouse identifier |
| `batch_size` | `FORGESIGHT_CLICKHOUSE_BATCH_SIZE` | `512` | 1 ≤ n ≤ pipeline `max_export_batch_size` |
| `async_insert` | `FORGESIGHT_CLICKHOUSE_ASYNC_INSERT` | `true` | bool → ClickHouse `async_insert` |
| `wait_for_async_insert` | `FORGESIGHT_CLICKHOUSE_WAIT_ASYNC` | `false` | bool |
| `create_table` | `FORGESIGHT_CLICKHOUSE_CREATE_TABLE` | `false` | dev convenience; prod uses migrations |

Validation: missing `dsn` → fail-fast at `configure()` (`architecture.md` §8);
`batch_size > max_export_batch_size` → clamped + WARN. Content storage is governed
by the SDK-wide `capture_content` gate (P7), not configured here.

## 5. Plug-and-play & upgrade story

Add later with `pip install forgesight-clickhouse` + the `exporters` block and
the shipped DDL — no agent-code change (P2). Remove by dropping the package +
config. Schema evolution is additive: new domain fields become new nullable
columns via numbered migrations; existing queries keep working (P5). The
`clickhouse-connect` SDK is pinned in this package, so a driver bump never touches
callers. Class name + config keys stable from v0.2.

## 6. Cross-language parity

Identical across Python / TypeScript: the table schema/DDL, the record→column
mapping, the denormalisation rule, batch alignment, and config keys
(`architecture.md` §10). Allowed to differ: the driver (`clickhouse-connect` vs
`@clickhouse/client`), async idioms, naming. TypeScript targets parity by 0.4.

## 7. Test strategy

- **Unit:** record→column mapping for each `Kind`; metadata denormalisation onto
  child rows; `cost_usd` from `forgesight.usage.cost_usd`; content omitted unless
  `capture_content`; `batch_size` clamp to pipeline max.
- **Conformance (feat-011):** exporter conformance suite — non-raising `export`,
  idempotent `force_flush`/`shutdown`, fault isolation (ClickHouse down ⇒ counted,
  not raised).
- **Integration:** against a ClickHouse container (skips if absent) — run the DDL,
  export a batch, `SELECT` it back; assert one columnar INSERT per batch (not
  row-at-a-time); assert a denormalised analytical query (`sum(cost_usd) GROUP BY
  team`) returns expected rows.
- **Migration:** `0001_init.sql` applies cleanly; a forward migration adding a
  nullable column leaves old rows valid.
- **Example agent:** a multi-step run; assert the trace tree is reconstructable
  from rows via `(trace_id, parent_run_id)`.

## 8. Risks & open questions

| Risk / Question | Mitigation / Decision |
|---|---|
| Row-at-a-time inserts crushing ClickHouse | One batched INSERT per pipeline batch; `async_insert` (§4.3). |
| Denormalised metadata bloating rows | `JSON` column + `LowCardinality` on enum-ish columns; partition + TTL; documented trade-off (single-table reads beat joins). |
| Schema drift as the domain model grows | Additive numbered migrations; new fields nullable (P5). |
| Storing prompts/PII | Content gated by `capture_content` (P7) + redaction (feat-008); content column off by default. |
| `async_insert` losing data on crash before flush | Documented; `wait_for_async_insert: true` for stronger durability at a latency cost. |
| Trace-tree reconstruction | `(trace_id, parent_run_id, run_id)` + `ORDER BY (trace_id, start_time, run_id)` give locality. |

## 9. Out of scope

- **A query UI / dashboard.** We write rows; visualisation is Grafana/Metabase/
  ClickHouse's own tooling (requirements §11).
- **Materialised views / rollup tables.** The base table ships; aggregation views
  are the operator's to add.
- **Distributed/replicated engine choice.** Default `MergeTree`; `Replicated*` /
  sharding are deployment decisions, documented but not prescribed.
- **Reading telemetry back through the SDK.** Export only; the SDK is a client.
- **Storing content by default.** Off unless `capture_content` (P7).

## 10. References

- [`../design/architecture.md`](../design/architecture.md) §2 (first-party-package rationale), §3 (Record immutability), §4.2 (SPI), §5 (packages)
- [`../design/design-principles.md`](../design/design-principles.md) P1, P2, P5, P6, P7, P8, P10
- [`../design/exporter-pipeline.md`](../design/exporter-pipeline.md) §4.3 (worker/batch), §4.5 (backpressure), §4.8 (batch knobs)
- [`../design/cost-model.md`](../design/cost-model.md) (the `cost_usd` column source)
- [`../requirements.md`](../requirements.md) FR-1…FR-5, FR-9, FR-11, NFR-3, NFR-4
- feat-003 (export pipeline), feat-001/002 (model + runtime), feat-006 (cost), feat-008 (interceptors), feat-011 (conformance)
- Prior art: ClickHouse MergeTree, Langfuse's ClickHouse-backed trace store, `clickhouse-connect`
