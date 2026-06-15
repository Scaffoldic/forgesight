"""The :class:`FrameworkAdapter` SPI — the locked lifecycle every adapter implements.

An adapter translates one agent framework's native hooks (LangChain callbacks, the CrewAI
event bus, …) into SDK instrumentation calls, so an *unchanged* agent emits the SAME domain
model (feat-001) regardless of framework — that uniformity is the whole point (requirements
§1.1). It lives in ``-api`` (not ``-core``) so AgentForge and third parties can implement it
without importing the runtime. The three methods are idempotent and stable for 0.2.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class FrameworkAdapter(Protocol):
    """Translates one framework's native hooks into SDK instrumentation calls."""

    name: str  # "langgraph", "crewai", … — the adapter's stable identifier

    def instrument(self) -> None:
        """Subscribe to the framework's native hooks. Idempotent."""
        ...

    def uninstrument(self) -> None:
        """Unsubscribe. Idempotent."""
        ...

    def is_instrumented(self) -> bool:
        """Whether the adapter is currently subscribed."""
        ...
