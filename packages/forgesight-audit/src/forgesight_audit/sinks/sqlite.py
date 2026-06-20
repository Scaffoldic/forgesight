"""An append-only, hash-chained SQLite driver with indexed compliance queries."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..model import AuditEvent, AuditKind
from .base import _ChainedSink

_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    timestamp_unix_nanos INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    principal TEXT NOT NULL,
    version TEXT,
    owner TEXT,
    team TEXT,
    cost_usd REAL,
    status TEXT,
    attributes TEXT NOT NULL,
    prev_hash TEXT,
    hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_principal ON audit_events (principal);
CREATE INDEX IF NOT EXISTS ix_audit_team ON audit_events (team);
CREATE INDEX IF NOT EXISTS ix_audit_kind ON audit_events (kind);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_events (timestamp_unix_nanos);
"""

_COLUMNS = (
    "seq, kind, timestamp_unix_nanos, run_id, trace_id, principal, version, owner, team, "
    "cost_usd, status, attributes, prev_hash, hash, schema_version"
)


class SqliteAuditSink(_ChainedSink):
    """Hash-chained rows in SQLite. ``path`` may be a file or ``":memory:"``."""

    def __init__(self, path: str, *, algorithm: str = "sha256") -> None:
        if path != ":memory:":
            parent = Path(path).parent
            if parent != Path(""):
                parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_DDL)
        self._conn.commit()
        super().__init__(algorithm=algorithm)
        self._bootstrap()

    def _append(self, event: AuditEvent) -> None:
        self._conn.execute(
            f"INSERT INTO audit_events ({_COLUMNS}) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.seq,
                str(event.kind),
                event.timestamp_unix_nanos,
                event.run_id,
                event.trace_id,
                event.principal,
                event.version,
                event.owner,
                event.team,
                event.cost_usd,
                event.status,
                json.dumps(dict(event.attributes), ensure_ascii=False),
                event.prev_hash,
                event.hash,
                event.schema_version,
            ),
        )
        self._conn.commit()

    def _read_all(self) -> Sequence[AuditEvent]:
        cursor = self._conn.execute(f"SELECT {_COLUMNS} FROM audit_events ORDER BY seq")
        return tuple(self._row_to_event(row) for row in cursor.fetchall())

    @staticmethod
    def _row_to_event(row: tuple[Any, ...]) -> AuditEvent:
        attrs = json.loads(str(row[11]))
        return AuditEvent(
            kind=AuditKind(str(row[1])),
            timestamp_unix_nanos=int(row[2]),
            run_id=str(row[3]),
            trace_id=str(row[4]),
            principal=str(row[5]),
            version=None if row[6] is None else str(row[6]),
            owner=None if row[7] is None else str(row[7]),
            team=None if row[8] is None else str(row[8]),
            cost_usd=None if row[9] is None else float(row[9]),
            status=None if row[10] is None else str(row[10]),
            attributes={str(k): str(v) for k, v in attrs.items()},
            seq=int(row[0]),
            prev_hash=None if row[12] is None else str(row[12]),
            hash=str(row[13]),
            schema_version=int(row[14]),
        )

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        if self._closed:
            return True
        self._conn.commit()
        return True

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        if self._closed:
            return  # idempotent — the connection is already committed and closed
        super().shutdown(timeout_millis)
        self._conn.commit()
        self._conn.close()
