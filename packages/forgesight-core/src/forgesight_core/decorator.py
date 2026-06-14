"""The ``@instrument`` decorator — turn a function into an instrumented scope.

Works on sync and async functions (detected via ``inspect.iscoroutinefunction``).
Supports ``kind`` ∈ {agent, step, tool}; ``llm``/``mcp``/``workflow`` need per-call
arguments (provider/model, server/method) and so are opened via the scope API, not
the decorator. ``capture_args`` is the opt-in content gate (P7) — accepted here and
honoured once content capture lands in feat-008; off by default.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import TypeVar, cast

from forgesight_api import Kind

from .processor import get_runtime
from .scope import RunScope, StepScope, ToolScope, _Scope

F = TypeVar("F", bound=Callable[..., object])

_DECORATABLE = {Kind.AGENT, Kind.STEP, Kind.TOOL}


def instrument(
    *,
    kind: Kind | str = Kind.TOOL,
    name: str | None = None,
    tool_type: str = "function",
    version: str | None = None,
    capture_args: bool = False,
) -> Callable[[F], F]:
    """Decorate a function so each call opens the matching telemetry scope."""
    resolved_kind = Kind(kind) if isinstance(kind, str) else kind
    if resolved_kind not in _DECORATABLE:
        raise ValueError(
            f"@instrument supports kind in {{agent, step, tool}}, not {resolved_kind!r}; "
            "open llm/mcp/workflow scopes via the telemetry API (they need call arguments)."
        )

    def decorator(fn: F) -> F:
        scope_name = name or fn.__qualname__

        def _open() -> _Scope:
            rt = get_runtime()
            if resolved_kind is Kind.AGENT:
                return RunScope(rt, name=scope_name, version=version)
            if resolved_kind is Kind.STEP:
                return StepScope(rt, name=scope_name)
            return ToolScope(rt, name=scope_name, tool_type=tool_type)

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: object, **kwargs: object) -> object:
                async with _open():
                    return await fn(*args, **kwargs)

            return cast(F, async_wrapper)

        @functools.wraps(fn)
        def sync_wrapper(*args: object, **kwargs: object) -> object:
            with _open():
                return fn(*args, **kwargs)

        return cast(F, sync_wrapper)

    return decorator
