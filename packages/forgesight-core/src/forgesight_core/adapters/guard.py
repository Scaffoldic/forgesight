"""Re-entrancy guard so a framework adapter never double-instruments a tool call.

When an inner span already covers a tool execution — an MCP ``tools/call`` (feat-016) or a
native ``tool_call`` — a framework adapter observing the *same* call must defer to that span
instead of opening a second ``execute_tool`` (otel mapping §4.3). The in-flight span marks
this contextvar; adapters check :func:`in_tool_call` on their tool-start hook and skip.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

_IN_TOOL_CALL: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "forgesight_in_tool_call", default=False
)


def in_tool_call() -> bool:
    """True while a tool span is already open on this context (adapters defer to it)."""
    return _IN_TOOL_CALL.get()


@contextmanager
def tool_call_active() -> Iterator[None]:
    """Mark a tool span in flight so an adapter observing the same call defers (no double-span)."""
    token = _IN_TOOL_CALL.set(True)
    try:
        yield
    finally:
        _IN_TOOL_CALL.reset(token)
