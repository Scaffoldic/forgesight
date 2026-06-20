"""The default driver: an append-only, hash-chained JSONL file."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from ..model import AuditEvent
from .base import _ChainedSink


class JsonlAuditSink(_ChainedSink):
    """One JSON object per line, appended in chain order. Durable as the file is."""

    def __init__(self, path: str, *, algorithm: str = "sha256") -> None:
        self._path = Path(path)
        super().__init__(algorithm=algorithm)
        if self._path.parent != Path(""):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._bootstrap()

    def _append(self, event: AuditEvent) -> None:
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def _read_all(self) -> Sequence[AuditEvent]:
        if not self._path.exists():
            return ()
        events: list[AuditEvent] = []
        with open(self._path, encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    events.append(AuditEvent.from_dict(json.loads(stripped)))
        return tuple(events)
