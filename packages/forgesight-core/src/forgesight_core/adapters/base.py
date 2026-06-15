"""``BaseAdapter`` — the idempotent ``instrument`` / ``uninstrument`` bookkeeping.

A concrete adapter subclasses this, sets ``name``, and implements ``_subscribe`` /
``_unsubscribe`` (the framework-specific hook wiring). The guard here makes double
``instrument()`` a no-op and ``uninstrument()`` safe to call any time — the lifecycle
invariants the conformance suite (feat-011) checks. Satisfies the
:class:`~forgesight_api.FrameworkAdapter` Protocol structurally.
"""

from __future__ import annotations


class BaseAdapter:
    """Lifecycle bookkeeping for a framework adapter; subclasses do the hook wiring."""

    name: str = "base"

    def __init__(self) -> None:
        self._instrumented = False

    def instrument(self) -> None:
        if self._instrumented:
            return
        self._subscribe()
        self._instrumented = True

    def uninstrument(self) -> None:
        if not self._instrumented:
            return
        self._unsubscribe()
        self._instrumented = False

    def is_instrumented(self) -> bool:
        return self._instrumented

    # --- subclass hooks ---------------------------------------------------
    def _subscribe(self) -> None:
        """Register the framework's native listeners. Override in a concrete adapter."""
        raise NotImplementedError

    def _unsubscribe(self) -> None:
        """Unregister the framework's native listeners. Override in a concrete adapter."""
        raise NotImplementedError
