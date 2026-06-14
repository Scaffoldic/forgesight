"""Per-run ambient state, propagated via ``contextvars``.

A :class:`TelemetryContext` carries the ids and accumulated run/step-scope metadata
for the active run. It rides on a :class:`~contextvars.ContextVar`, so it survives
``await`` boundaries and is *copied* into ``asyncio.gather`` / ``create_task``
children — which is what makes concurrent leaf calls attach to the right parent
without racing each other's ``current_span_id`` (P9, feat-002 §4.3).
"""

from __future__ import annotations

import contextvars
import os
from dataclasses import dataclass, field

from forgesight_api import new_ulid

_SPAN_ID_BYTES = 8  # OTel span id is 8 bytes / 16 hex chars


@dataclass(slots=True)
class TelemetryContext:
    """Ambient state for the active run. Read by every scope on enter."""

    run_id: str
    trace_id: str
    parent_run_id: str | None = None
    current_span_id: str | None = None  # the parent span for the next child opened
    context_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def child(self, *, current_span_id: str) -> TelemetryContext:
        """A copy with a new ``current_span_id`` and a *copied* metadata dict.

        Used when entering a nested scope so a sibling scope's metadata writes never
        leak across, while inherited run-scope metadata is preserved.
        """
        return TelemetryContext(
            run_id=self.run_id,
            trace_id=self.trace_id,
            parent_run_id=self.parent_run_id,
            current_span_id=current_span_id,
            context_id=self.context_id,
            metadata=dict(self.metadata),
        )


_CURRENT: contextvars.ContextVar[TelemetryContext | None] = contextvars.ContextVar(
    "forgesight_current_context", default=None
)


def current_context() -> TelemetryContext | None:
    """Return the active :class:`TelemetryContext`, or ``None`` outside any run."""
    return _CURRENT.get()


def set_current_context(ctx: TelemetryContext | None) -> contextvars.Token[TelemetryContext | None]:
    """Bind ``ctx`` as the active context; returns a token to restore the previous one."""
    return _CURRENT.set(ctx)


def reset_current_context(token: contextvars.Token[TelemetryContext | None]) -> None:
    """Restore the context that was active before the matching :func:`set_current_context`."""
    _CURRENT.reset(token)


def new_run_id() -> str:
    """Mint a ULID run id — the single place run ids are created (feat-001 format)."""
    return new_ulid()


def new_span_id() -> str:
    """Mint a 16-hex-char (8-byte) span id."""
    return os.urandom(_SPAN_ID_BYTES).hex()
