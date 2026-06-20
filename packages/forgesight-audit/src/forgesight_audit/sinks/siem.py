"""Bridge driver: ship each ``AuditEvent`` as a JSON line to a SIEM/syslog collector.

Generic by design (P1) — it writes JSON lines through a pluggable transport (a file path,
or an injected callable), not a branded vendor client.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ..model import AuditEvent
from .base import _BridgeSink

#: A transport takes one serialized JSON line and ships it.
Transport = Callable[[str], None]


class SiemAuditSink(_BridgeSink):
    """Hash-chains in process and ships each event as a JSON line via ``transport``
    (default: append to the file at ``endpoint``)."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        transport: Transport | None = None,
        algorithm: str = "sha256",
    ) -> None:
        self._transport: Transport = (
            transport if transport is not None else _file_transport(endpoint)
        )
        super().__init__(algorithm=algorithm)

    def _emit(self, event: AuditEvent) -> None:
        self._transport(json.dumps(event.to_dict(), ensure_ascii=False))


def _file_transport(endpoint: str | None) -> Transport:
    if endpoint is None:

        def _unconfigured(line: str) -> None:
            raise RuntimeError("SiemAuditSink requires an endpoint or a transport")

        return _unconfigured

    def _append(line: str) -> None:
        with open(endpoint, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    return _append
