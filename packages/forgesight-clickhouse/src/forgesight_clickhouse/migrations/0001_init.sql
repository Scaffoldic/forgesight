-- forgesight-clickhouse — initial schema (feat-014).
--
-- One denormalized, immutable, MergeTree table: a Record is written once and never
-- updated, so trace-level business metadata is denormalized onto every row (no join
-- table, so analytical queries never pay a join). `${TABLE}` is substituted by the
-- exporter with the configured table name (default `agentforge_records`).
CREATE TABLE IF NOT EXISTS agentforge_records (
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
