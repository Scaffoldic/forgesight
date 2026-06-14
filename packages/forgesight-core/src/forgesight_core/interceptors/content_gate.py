"""``ContentCaptureGate`` — enforces secure-by-default content capture (P7/ADR-0007).

Always first in the chain. When ``capture_content`` is off (the default), it strips
captured message content from every record before any other interceptor or exporter
can see it. The security-critical built-in: it *fails closed*.
"""

from __future__ import annotations

from dataclasses import replace

from forgesight_api import Record


class ContentCaptureGate:
    """Strip content fields unless content capture is explicitly enabled."""

    def __init__(self, *, capture_content: bool = False) -> None:
        self._capture = capture_content

    def intercept(self, record: Record) -> Record | None:
        if self._capture or record.llm is None or record.llm.content is None:
            return record
        return replace(record, llm=replace(record.llm, content=None))
