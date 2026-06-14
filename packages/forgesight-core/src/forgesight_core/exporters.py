"""The two exporters that ship in core (architecture §5).

``InMemoryExporter`` is the testing workhorse — it keeps every record it receives so
tests can assert the span tree (feat-011 builds its helpers on it). ``ConsoleExporter``
is the zero-config default sink so a fresh ``configure()`` shows something in dev.

Both satisfy the ``TelemetryExporter`` Protocol structurally and **never raise** from
``export`` (P6) — they return ``ExportResult.FAILURE`` on error instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TextIO

from forgesight_api import ExportResult, Record


class InMemoryExporter:
    """Collects exported records in memory. For tests and local inspection."""

    def __init__(self) -> None:
        self._records: list[Record] = []

    @property
    def records(self) -> list[Record]:
        """All records exported so far, in arrival order."""
        return list(self._records)

    def clear(self) -> None:
        """Drop all collected records."""
        self._records.clear()

    def export(self, records: Sequence[Record]) -> ExportResult:
        self._records.extend(records)
        return ExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        self._records.clear()


class ConsoleExporter:
    """Prints a one-line summary of each record. The zero-config default sink."""

    def __init__(self, *, stream: TextIO | None = None) -> None:
        # Defaults to stdout via print(); pass any text stream to redirect.
        self._stream = stream

    def export(self, records: Sequence[Record]) -> ExportResult:
        try:
            for r in records:
                line = self._format(r)
                if self._stream is not None:
                    self._stream.write(line + "\n")
                else:
                    print(line)
            return ExportResult.SUCCESS
        except Exception:  # pragma: no cover - defensive; export must never raise (P6)
            return ExportResult.FAILURE

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        return None

    @staticmethod
    def _format(r: Record) -> str:
        dur = "" if r.duration_ms is None else f" {r.duration_ms:.1f}ms"
        cost = ""
        if r.llm is not None and r.llm.cost_usd is not None:
            cost = f" ${r.llm.cost_usd:.6f}"
        return f"[forgesight] {r.kind} {r.name} run={r.run_id} {r.status}{dur}{cost}"
