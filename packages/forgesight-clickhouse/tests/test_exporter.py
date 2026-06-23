"""Tests for the ClickHouse exporter: mapping, denormalization, batching, conformance."""

from __future__ import annotations

import json

import pytest

from forgesight_api import (
    ErrorInfo,
    ExportResult,
    Kind,
    LLMCall,
    MCPCall,
    Record,
    RunStatus,
    TokenUsage,
    ToolCall,
)
from forgesight_clickhouse import ClickHouseExporter, InMemoryClickHouseClient
from forgesight_clickhouse.exporter import COLUMNS, _load_ddl
from forgesight_core import configure, reset_runtime, telemetry
from forgesight_core.testing.conformance import run_exporter_conformance

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
DSN = "clickhouse://user:pass@host:8443/agents"


def _exporter(**kw: object) -> tuple[ClickHouseExporter, InMemoryClickHouseClient]:
    client = InMemoryClickHouseClient()
    return ClickHouseExporter(client=client, **kw), client


def _llm_record(span: str = "00f067aa0ba902b7") -> Record:
    return Record(
        kind=Kind.LLM,
        run_id="01J9Z3K7P8QF2R5V6W7X8Y9Z0A",
        trace_id=TRACE,
        span_id=span,
        parent_span_id="aaaaaaaaaaaaaaaa",
        name="claude-sonnet-4-5",
        status=RunStatus.OK,
        start_unix_nanos=1_000_000_000,
        end_unix_nanos=3_000_000_000,
        llm=LLMCall(
            provider="anthropic",
            request_model="claude-sonnet-4-5",
            response_model="claude-sonnet-4-5-20990101",
            usage=TokenUsage(input=100, output=50, cache_read=10, reasoning=5),
            cost_usd=0.01,
        ),
    )


# --- construction / validation ------------------------------------------------
def test_requires_dsn_or_client() -> None:
    with pytest.raises(ValueError, match="requires a dsn"):
        ClickHouseExporter()


def test_invalid_table_rejected() -> None:
    with pytest.raises(ValueError, match="invalid ClickHouse table"):
        ClickHouseExporter(dsn=DSN, table="bad table; DROP")


def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="batch_size must be"):
        ClickHouseExporter(dsn=DSN, batch_size=0)


def test_batch_size_clamped_to_pipeline_max(caplog: pytest.LogCaptureFixture) -> None:
    exporter, _ = _exporter(batch_size=5000, max_export_batch_size=512)
    assert exporter._batch_size == 512
    assert any("clamping" in r.message for r in caplog.records)


# --- conformance --------------------------------------------------------------
def test_conformance() -> None:
    run_exporter_conformance(lambda: ClickHouseExporter(client=InMemoryClickHouseClient()))


# --- record → column mapping --------------------------------------------------
def test_llm_record_maps_to_columns() -> None:
    exporter, client = _exporter()
    assert exporter.export([_llm_record()]) is ExportResult.SUCCESS
    [row] = client.rows_as_dicts()
    assert row["kind"] == "llm"
    assert row["op"] == "chat"
    assert row["provider"] == "anthropic"
    assert row["model"] == "claude-sonnet-4-5-20990101"  # response model wins
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50
    assert row["cache_read_tokens"] == 10
    assert row["reasoning_tokens"] == 5
    assert row["total_tokens"] == 165
    assert row["cost_usd"] == 0.01  # forgesight.usage.cost_usd
    assert row["status"] == "ok"
    assert row["start_time"] == 1_000_000_000
    assert row["end_time"] == 3_000_000_000
    assert row["duration_ms"] == 2000.0


def test_tool_and_mcp_and_step_mapping() -> None:
    exporter, client = _exporter()
    tool = Record(
        kind=Kind.TOOL,
        run_id="r",
        trace_id=TRACE,
        span_id="1111111111111111",
        parent_span_id=None,
        name="search",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        tool=ToolCall(name="search"),
    )
    mcp = Record(
        kind=Kind.MCP,
        run_id="r",
        trace_id=TRACE,
        span_id="2222222222222222",
        parent_span_id=None,
        name="tools/call",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
        mcp=MCPCall(server="files", method="tools/call", tool="read_file"),
    )
    step = Record(
        kind=Kind.STEP,
        run_id="r",
        trace_id=TRACE,
        span_id="3333333333333333",
        parent_span_id=None,
        name="react-1",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
    )
    exporter.export([tool, mcp, step])
    by_kind = {row["kind"]: row for row in client.rows_as_dicts()}
    assert by_kind["tool"]["tool_name"] == "search"
    assert by_kind["tool"]["op"] == "execute_tool"
    assert by_kind["mcp"]["mcp_server"] == "files"
    assert by_kind["mcp"]["mcp_method"] == "tools/call"
    assert by_kind["mcp"]["tool_name"] == "read_file"  # tools/call lifts the tool name
    assert by_kind["mcp"]["op"] == "execute_tool"
    assert by_kind["step"]["op"] == ""  # a step has no GenAI operation
    assert by_kind["step"]["tool_name"] is None


def test_error_record_carries_error_type() -> None:
    exporter, client = _exporter()
    rec = Record(
        kind=Kind.AGENT,
        run_id="r",
        trace_id=TRACE,
        span_id="4444444444444444",
        parent_span_id=None,
        name="classifier",
        status=RunStatus.ERROR,
        start_unix_nanos=1,
        end_unix_nanos=2,
    )
    exporter.export([rec])
    [row] = client.rows_as_dicts()
    assert row["status"] == "error"
    assert row["error_type"] == "error"  # status fallback when no ErrorInfo


def test_error_info_error_type_wins_over_status() -> None:
    exporter, client = _exporter()
    rec = Record(
        kind=Kind.TOOL,
        run_id="r",
        trace_id=TRACE,
        span_id="5555555555555555",
        parent_span_id=None,
        name="search",
        status=RunStatus.ERROR,
        start_unix_nanos=1,
        end_unix_nanos=2,
        tool=ToolCall(name="search", status=RunStatus.ERROR),
        error=ErrorInfo(error_type="TimeoutError", message="boom"),
    )
    exporter.export([rec])
    [row] = client.rows_as_dicts()
    assert row["error_type"] == "TimeoutError"  # ErrorInfo wins over the status fallback


def test_workflow_record_maps_op() -> None:
    exporter, client = _exporter()
    rec = Record(
        kind=Kind.WORKFLOW,
        run_id="r",
        trace_id=TRACE,
        span_id="6666666666666666",
        parent_span_id=None,
        name="nightly",
        status=RunStatus.OK,
        start_unix_nanos=1,
        end_unix_nanos=2,
    )
    exporter.export([rec])
    [row] = client.rows_as_dicts()
    assert row["op"] == "invoke_workflow"
    assert row["agent_name"] == ""  # only AGENT records carry an agent name
    assert client.rows[0][0] == "r"  # raw-row accessor: run_id is the first column


# --- denormalization (the headline) ------------------------------------------
def test_business_metadata_denormalized_onto_child_rows() -> None:
    exporter, client = _exporter()
    configure(exporters=[exporter], sync_export=True)
    try:
        with telemetry.agent_run("classifier", version="1.2.0") as run:
            run.set_metadata(team="payments")
            with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
                call.record_usage(input=1000, output=500)
    finally:
        reset_runtime()

    rows = client.rows_as_dicts()
    assert rows, "expected exported rows"
    # every row — run AND the child llm call — carries the denormalized team
    for row in rows:
        meta = json.loads(str(row["metadata"]))
        assert meta["team"] == "payments"
    agent_row = next(r for r in rows if r["kind"] == "agent")
    assert agent_row["agent_name"] == "classifier"
    assert agent_row["agent_version"] == "1.2.0"
    # the structured fields are lifted to columns, not duplicated into metadata JSON
    assert "agent.version" not in json.loads(str(agent_row["metadata"]))


def test_parent_run_id_and_context_id_lift_to_columns() -> None:
    exporter, client = _exporter()
    configure(exporters=[exporter], sync_export=True)
    try:
        with (
            telemetry.agent_run("outer"),
            telemetry.agent_run("inner", context_id="conv-7"),
        ):
            pass
    finally:
        reset_runtime()
    inner = next(r for r in client.rows_as_dicts() if r["agent_name"] == "inner")
    assert inner["context_id"] == "conv-7"
    assert inner["parent_run_id"] is not None


# --- batching -----------------------------------------------------------------
def test_one_insert_per_batch_not_row_at_a_time() -> None:
    exporter, client = _exporter()
    exporter.export([_llm_record(span=f"{i:016x}") for i in range(50)])
    assert len(client.inserts) == 1  # one columnar INSERT for the whole batch
    assert len(client.inserts[0].rows) == 50
    assert client.inserts[0].column_names == list(COLUMNS)


def test_oversized_batch_chunks_by_batch_size() -> None:
    exporter, client = _exporter(batch_size=10, max_export_batch_size=512)
    exporter.export([_llm_record(span=f"{i:016x}") for i in range(25)])
    assert [len(c.rows) for c in client.inserts] == [10, 10, 5]


def test_async_insert_settings_passed() -> None:
    exporter, client = _exporter(async_insert=True, wait_for_async_insert=False)
    exporter.export([_llm_record()])
    settings = client.inserts[0].settings
    assert settings["async_insert"] == 1
    assert settings["wait_for_async_insert"] == 0


def test_empty_batch_is_a_noop() -> None:
    exporter, client = _exporter()
    assert exporter.export([]) is ExportResult.SUCCESS
    assert client.inserts == []


# --- create_table / migrations ------------------------------------------------
def test_create_table_runs_ddl_once() -> None:
    exporter, client = _exporter(create_table=True)
    exporter.export([_llm_record()])
    exporter.export([_llm_record()])
    assert len(client.commands) == 1  # DDL emitted once, on first export
    assert "CREATE TABLE IF NOT EXISTS forgesight_records" in client.commands[0]


def test_create_table_false_emits_no_ddl() -> None:
    exporter, client = _exporter(create_table=False)
    exporter.export([_llm_record()])
    assert client.commands == []


def test_load_ddl_retargets_table_name() -> None:
    sql = _load_ddl("custom_records")
    assert "CREATE TABLE IF NOT EXISTS custom_records" in sql
    assert "forgesight_records" not in sql
    # column contract intact after retarget
    assert "cost_usd" in sql
    assert "ORDER BY (trace_id, start_time, run_id)" in sql


def test_custom_table_used_in_insert_and_ddl() -> None:
    exporter, client = _exporter(table="my_db.records", create_table=True)
    exporter.export([_llm_record()])
    assert client.inserts[0].table == "my_db.records"
    assert "my_db.records" in client.commands[0]


# --- fault isolation (P6) -----------------------------------------------------
class _FailingClient:
    def __init__(self) -> None:
        self.closed = False

    def insert(self, *args: object, **kwargs: object) -> object:
        raise ConnectionError("clickhouse unreachable")

    def command(self, statement: str) -> object:
        raise ConnectionError("clickhouse unreachable")

    def close(self) -> None:
        self.closed = True


def test_clickhouse_outage_is_isolated() -> None:
    exporter = ClickHouseExporter(client=_FailingClient())
    assert exporter.export([_llm_record()]) is ExportResult.FAILURE  # counted, never raised


# --- lifecycle ----------------------------------------------------------------
def test_shutdown_closes_client_and_is_idempotent() -> None:
    exporter, client = _exporter()
    exporter.shutdown()
    assert client.closed is True
    exporter.shutdown()  # idempotent, must not raise


def test_force_flush_returns_true() -> None:
    exporter, _ = _exporter()
    assert exporter.force_flush() is True


def test_shutdown_without_a_built_client_is_a_noop() -> None:
    exporter = ClickHouseExporter(dsn=DSN)  # never exported ⇒ no client built
    exporter.shutdown()  # must not raise


# --- config: env resolution ---------------------------------------------------
def test_env_resolves_dsn_table_and_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGESIGHT_CLICKHOUSE_DSN", DSN)
    monkeypatch.setenv("FORGESIGHT_CLICKHOUSE_TABLE", "env_records")
    monkeypatch.setenv("FORGESIGHT_CLICKHOUSE_BATCH_SIZE", "64")
    monkeypatch.setenv("FORGESIGHT_CLICKHOUSE_ASYNC_INSERT", "false")
    monkeypatch.setenv("FORGESIGHT_CLICKHOUSE_CREATE_TABLE", "true")
    exporter = ClickHouseExporter()
    assert exporter._dsn == DSN
    assert exporter._table == "env_records"
    assert exporter._batch_size == 64
    assert exporter._async_insert is False
    assert exporter._create_table is True


def test_kwargs_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGESIGHT_CLICKHOUSE_TABLE", "env_records")
    exporter, _ = _exporter(table="explicit_records")
    assert exporter._table == "explicit_records"


# --- resolves by entry-point name ---------------------------------------------
def test_resolves_by_name() -> None:
    from forgesight_core.config import resolve

    exporter = resolve("exporters", "clickhouse", {"dsn": DSN})
    assert isinstance(exporter, ClickHouseExporter)
