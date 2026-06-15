"""A client double for testing the ClickHouse exporter without a live server.

:class:`InMemoryClickHouseClient` satisfies the ``ClickHouseClient`` protocol and records
every INSERT / command so a test (or a consuming team's pipeline test) can assert the rows,
the column order, the per-insert settings, and that a batch became **one** columnar INSERT —
never row-at-a-time.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class InsertCall:
    """One captured ``insert()`` — a whole pipeline batch as a single columnar INSERT."""

    table: str
    rows: list[list[object]]
    column_names: list[str]
    settings: dict[str, object]


class InMemoryClickHouseClient:
    """In-memory stand-in for a ``clickhouse-connect`` client. For tests/local inspection."""

    def __init__(self) -> None:
        self.inserts: list[InsertCall] = []
        self.commands: list[str] = []
        self.closed = False

    def insert(
        self,
        table: str,
        data: Sequence[Sequence[object]],
        *,
        column_names: Sequence[str],
        settings: Mapping[str, object],
    ) -> object:
        self.inserts.append(
            InsertCall(
                table=table,
                rows=[list(row) for row in data],
                column_names=list(column_names),
                settings=dict(settings),
            )
        )
        return None

    def command(self, statement: str) -> object:
        self.commands.append(statement)
        return None

    def close(self) -> None:
        self.closed = True

    # --- convenience accessors -------------------------------------------
    @property
    def rows(self) -> list[list[object]]:
        """Every inserted row across all INSERTs, in arrival order."""
        return [row for call in self.inserts for row in call.rows]

    def rows_as_dicts(self) -> list[dict[str, object]]:
        """Rows zipped with their column names — for readable assertions."""
        out: list[dict[str, object]] = []
        for call in self.inserts:
            for row in call.rows:
                out.append(dict(zip(call.column_names, row, strict=True)))
        return out


__all__ = ["InMemoryClickHouseClient", "InsertCall"]
