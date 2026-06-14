"""The four service-provider interfaces (SPIs) — the entire extension surface.

Each is a ``runtime_checkable`` structural :class:`~typing.Protocol` (ADR-0006), so
a backend or framework author writes a plain class with the right methods and it
*is* an implementation — no base-class import, no inheritance. ``isinstance`` still
works for registration-time validation (feat-010).

There is no fifth extension point: no monkey-patching, class-swapping, or import
hooks (architecture §6).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .model import TokenUsage
from .record import ExportResult, LifecycleEvent, Record


@runtime_checkable
class TelemetryExporter(Protocol):
    """Ships records to ONE backend. Called by the pipeline worker, never the hot path."""

    def export(self, records: Sequence[Record]) -> ExportResult:
        """Export a batch. MUST return ``ExportResult.FAILURE`` on error, never raise (P6)."""
        ...

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Flush pending data; return False on timeout. Idempotent, non-terminal."""
        ...

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        """Release resources. Idempotent, terminal."""
        ...


@runtime_checkable
class Interceptor(Protocol):
    """Mutate / redact / veto a record before export. Runs in registration order."""

    def intercept(self, record: Record) -> Record | None:
        """Return the record (pass), a new record (replace), or ``None`` (drop, counted)."""
        ...


@runtime_checkable
class EventListener(Protocol):
    """Side-effect subscriber to lifecycle events. Isolated from the run (FR-8, P6)."""

    def on_event(self, event: LifecycleEvent) -> None:
        """Handle one lifecycle event. A raising listener is logged and isolated."""
        ...


@runtime_checkable
class PricingProvider(Protocol):
    """Resolve cost from token usage. Returns ``None`` for unknown models (FR-9)."""

    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
        """USD cost, or ``None`` if the model is unknown. Never raises, never fabricates."""
        ...
