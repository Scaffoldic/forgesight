"""``ClickHouseExporter`` — columnar batch insert of immutable records into ClickHouse.

A :class:`~forgesight_api.TelemetryExporter` (so it resolves via the
``forgesight.exporters`` entry point and passes the conformance suite) that writes each
:class:`~forgesight_api.Record` as one row in a **denormalized single-table MergeTree**.
Because a Record is written once and never updated (OTel's immutable span model),
trace-level business metadata is denormalized onto every row — no join table, so
analytical queries (``sum(cost_usd) GROUP BY team``) never pay a join.

It runs on the export worker (feat-003), never the hot path: ``export()`` receives a
batch already sized by the pipeline and issues **one** columnar INSERT per ``batch_size``
chunk. ``export`` never raises (P6): a ClickHouse outage returns ``ExportResult.FAILURE``,
counted by the pipeline, invisible to the agent.

The vendor driver (``clickhouse-connect``) is imported lazily and built from the DSN on
first export, so construction never touches the network. Tests inject a client double
(:class:`~forgesight_clickhouse.testing.InMemoryClickHouseClient`).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from importlib.resources import files
from typing import Protocol, cast, runtime_checkable

from forgesight_api import ExportResult, Kind, Record, RunStatus

_log = logging.getLogger("forgesight.clickhouse")

DEFAULT_TABLE = "agentforge_records"
DEFAULT_BATCH_SIZE = 512
DEFAULT_MAX_EXPORT_BATCH_SIZE = 512  # pipeline default (exporter-pipeline.md §4.8)

# operation.name values (gen_ai.operation.name) — kept local; no forgesight-otel dep (P1)
_OP_INVOKE_AGENT = "invoke_agent"
_OP_INVOKE_WORKFLOW = "invoke_workflow"
_OP_CHAT = "chat"
_OP_EXECUTE_TOOL = "execute_tool"
_MCP_TOOLS_CALL = "tools/call"

# structured keys feat-002 stashes in Record.attributes — lifted to their own columns,
# so they are not duplicated into the denormalized `metadata` JSON blob.
_AGENT_VERSION_KEY = "agent.version"
_PARENT_RUN_ID_KEY = "parent.run_id"
_CONTEXT_ID_KEY = "context.id"
_STRUCTURED_KEYS = frozenset({_AGENT_VERSION_KEY, _PARENT_RUN_ID_KEY, _CONTEXT_ID_KEY})

_OK_STATUSES = frozenset({RunStatus.OK, RunStatus.RUNNING})
_ENV_PREFIX = "FORGESIGHT_CLICKHOUSE_"
# table name (optionally db-qualified), validated to keep it out of the INSERT verbatim.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

# Column order — must match migrations/0001_init.sql exactly.
COLUMNS: tuple[str, ...] = (
    "run_id",
    "trace_id",
    "parent_run_id",
    "context_id",
    "kind",
    "op",
    "agent_name",
    "agent_version",
    "provider",
    "model",
    "tool_name",
    "mcp_server",
    "mcp_method",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "reasoning_tokens",
    "total_tokens",
    "cost_usd",
    "status",
    "error_type",
    "start_time",
    "end_time",
    "duration_ms",
    "metadata",
)


@runtime_checkable
class ClickHouseClient(Protocol):
    """The slice of the ``clickhouse-connect`` client this exporter uses."""

    def insert(
        self,
        table: str,
        data: Sequence[Sequence[object]],
        *,
        column_names: Sequence[str],
        settings: Mapping[str, object],
    ) -> object: ...

    def command(self, statement: str) -> object: ...

    def close(self) -> None: ...


def _env(key: str) -> str | None:
    return os.environ.get(f"{_ENV_PREFIX}{key}")


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    return int(raw) if raw is not None else default


def _op(record: Record) -> str:
    kind = record.kind
    if kind is Kind.AGENT:
        return _OP_INVOKE_AGENT
    if kind is Kind.WORKFLOW:
        return _OP_INVOKE_WORKFLOW
    if kind is Kind.LLM:
        return _OP_CHAT
    if kind is Kind.TOOL:
        return _OP_EXECUTE_TOOL
    if kind is Kind.MCP and record.mcp is not None and record.mcp.method == _MCP_TOOLS_CALL:
        return _OP_EXECUTE_TOOL
    return ""  # STEP, or a non-tools/call MCP method: no GenAI operation


def _error_type(record: Record) -> str | None:
    if record.error is not None:
        return record.error.error_type
    if record.status not in _OK_STATUSES:
        return record.status.value
    return None


def _chunks(rows: list[list[object]], size: int) -> list[list[list[object]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


class ClickHouseExporter:
    """Columnar batch insert of immutable Records into a denormalized MergeTree table."""

    def __init__(
        self,
        *,
        dsn: str | None = None,
        table: str | None = None,
        batch_size: int | None = None,
        async_insert: bool | None = None,
        wait_for_async_insert: bool | None = None,
        create_table: bool | None = None,
        max_export_batch_size: int = DEFAULT_MAX_EXPORT_BATCH_SIZE,
        client: ClickHouseClient | None = None,
    ) -> None:
        self._dsn = dsn if dsn is not None else _env("DSN")
        self._client = client
        if self._client is None and not self._dsn:
            raise ValueError(
                "ClickHouseExporter requires a dsn (or FORGESIGHT_CLICKHOUSE_DSN), "
                "e.g. clickhouse://user:pass@host:8443/db"
            )

        self._table = table if table is not None else (_env("TABLE") or DEFAULT_TABLE)
        if not _IDENT.match(self._table):
            raise ValueError(f"invalid ClickHouse table identifier {self._table!r}")

        size = batch_size if batch_size is not None else _env_int("BATCH_SIZE", DEFAULT_BATCH_SIZE)
        if size < 1:
            raise ValueError(f"batch_size must be >= 1, got {size}")
        if size > max_export_batch_size:
            _log.warning(
                "batch_size %d exceeds pipeline max_export_batch_size %d; clamping",
                size,
                max_export_batch_size,
            )
            size = max_export_batch_size
        self._batch_size = size

        self._async_insert = (
            async_insert if async_insert is not None else _env_bool("ASYNC_INSERT", True)
        )
        self._wait = (
            wait_for_async_insert
            if wait_for_async_insert is not None
            else _env_bool("WAIT_ASYNC", False)
        )
        self._create_table = (
            create_table if create_table is not None else _env_bool("CREATE_TABLE", False)
        )
        self._table_ready = False

    # --- TelemetryExporter Protocol --------------------------------------
    def export(self, records: Sequence[Record]) -> ExportResult:
        if not records:
            return ExportResult.SUCCESS
        try:
            client = self._get_client()
            self._ensure_table(client)
            rows = [self._to_row(record) for record in records]
            settings: dict[str, object] = {
                "async_insert": 1 if self._async_insert else 0,
                "wait_for_async_insert": 1 if self._wait else 0,
            }
            for chunk in _chunks(rows, self._batch_size):
                client.insert(self._table, chunk, column_names=COLUMNS, settings=settings)
        except Exception:  # export must never raise (P6) — a CH outage is counted, not raised
            _log.warning("clickhouse export failed", exc_info=True)
            return ExportResult.FAILURE
        return ExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        # No client-side buffer: rows are handed to the server per export(); with
        # async_insert the server owns the buffer. Nothing to flush, so this can't fail.
        return True

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.close()
        except Exception:  # pragma: no cover - best-effort close, must never raise
            _log.warning("clickhouse client close failed", exc_info=True)

    # --- internals --------------------------------------------------------
    def _get_client(self) -> ClickHouseClient:
        if self._client is None:
            import clickhouse_connect  # type: ignore[import-untyped]  # pragma: no cover

            self._client = cast(  # pragma: no cover
                ClickHouseClient, clickhouse_connect.get_client(dsn=self._dsn)
            )
        return self._client

    def _ensure_table(self, client: ClickHouseClient) -> None:
        if self._table_ready:
            return
        if self._create_table:
            client.command(_load_ddl(self._table))
        self._table_ready = True

    def _to_row(self, record: Record) -> list[object]:
        attrs = record.attributes
        llm = record.llm
        tool = record.tool
        mcp = record.mcp
        usage = llm.usage if llm is not None else None
        metadata = {key: value for key, value in attrs.items() if key not in _STRUCTURED_KEYS}
        return [
            record.run_id,
            record.trace_id,
            _opt_str(attrs.get(_PARENT_RUN_ID_KEY)),
            _opt_str(attrs.get(_CONTEXT_ID_KEY)),
            record.kind.value,
            _op(record),
            record.name if record.kind is Kind.AGENT else "",
            _opt_str(attrs.get(_AGENT_VERSION_KEY)),
            llm.provider if llm is not None else None,
            (llm.response_model or llm.request_model) if llm is not None else None,
            tool.name if tool is not None else (mcp.tool if mcp is not None else None),
            mcp.server if mcp is not None else None,
            mcp.method if mcp is not None else None,
            usage.input if usage is not None else None,
            usage.output if usage is not None else None,
            usage.cache_read if usage is not None else None,
            usage.cache_creation if usage is not None else None,
            usage.reasoning if usage is not None else None,
            usage.total if usage is not None else None,
            llm.cost_usd if llm is not None else None,
            record.status.value,
            _error_type(record),
            record.start_unix_nanos,
            record.end_unix_nanos,
            record.duration_ms,
            json.dumps(metadata, default=str, sort_keys=True),
        ]


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _load_ddl(table: str) -> str:
    """Read the shipped DDL, retargeted at ``table`` (default ``agentforge_records``)."""
    sql = (files("forgesight_clickhouse") / "migrations" / "0001_init.sql").read_text(
        encoding="utf-8"
    )
    return sql.replace(DEFAULT_TABLE, table) if table != DEFAULT_TABLE else sql
