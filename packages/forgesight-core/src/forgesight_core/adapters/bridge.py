"""``ScopeBridge`` — translate a framework's start/end callbacks onto SDK scopes.

Frameworks signal work as *start* then *end* callbacks, not ``with`` blocks. This bridge
opens the matching SDK scope on start and closes it on end, manually driving the scope's
context-manager protocol so nesting rides the SDK's ``TelemetryContext`` (contextvars) —
exactly as the runtime intends. Two addressing modes:

* **keyed** — frameworks that give each unit a run id (LangChain's ``run_id``): look the
  open scope up by key on the end callback.
* **stacked** — frameworks without ids (the CrewAI event bus): per-kind LIFO, matching the
  strictly-nested sequential execution order.

The bridge holds no framework types; the adapter constructs the scope and hands it over.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, Protocol


class _ManagedScope(Protocol):
    def __enter__(self) -> Any: ...
    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any: ...
    def record_error(self, exc: BaseException, *, code: str | None = None) -> None: ...


class ScopeBridge:
    """Open/close SDK scopes from start/end callbacks; nests via the runtime's contextvars."""

    def __init__(self) -> None:
        self._by_key: dict[Hashable, _ManagedScope] = {}
        self._stacks: dict[str, list[_ManagedScope]] = {}

    # --- keyed (frameworks with run ids) ---------------------------------
    def enter_keyed(self, key: Hashable, scope: _ManagedScope) -> _ManagedScope:
        scope.__enter__()
        self._by_key[key] = scope
        return scope

    def get_keyed(self, key: Hashable) -> _ManagedScope | None:
        return self._by_key.get(key)

    def exit_keyed(
        self, key: Hashable, *, error: BaseException | None = None
    ) -> _ManagedScope | None:
        return self._close(self._by_key.pop(key, None), error)

    # --- stacked (frameworks without ids — per-kind LIFO) ----------------
    def enter_stacked(self, kind: str, scope: _ManagedScope) -> _ManagedScope:
        scope.__enter__()
        self._stacks.setdefault(kind, []).append(scope)
        return scope

    def peek_stacked(self, kind: str) -> _ManagedScope | None:
        stack = self._stacks.get(kind)
        return stack[-1] if stack else None

    def exit_stacked(
        self, kind: str, *, error: BaseException | None = None
    ) -> _ManagedScope | None:
        stack = self._stacks.get(kind)
        scope = stack.pop() if stack else None
        return self._close(scope, error)

    # --- cleanup ----------------------------------------------------------
    def close_all(self) -> None:
        """Close every still-open scope (innermost first) — used on uninstrument."""
        leftovers = list(self._by_key.values())
        for stack in self._stacks.values():
            leftovers.extend(stack)
        for scope in reversed(leftovers):
            self._close(scope, None)
        self._by_key.clear()
        self._stacks.clear()

    @staticmethod
    def _close(scope: _ManagedScope | None, error: BaseException | None) -> _ManagedScope | None:
        if scope is None:
            return None
        if error is not None:
            scope.record_error(error)
        scope.__exit__(None, None, None)
        return scope
