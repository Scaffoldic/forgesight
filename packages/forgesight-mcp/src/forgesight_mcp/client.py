"""MCP **client** instrumentation — a span per outgoing request, W3C inject, ``mcp.*`` attrs.

``instrument_mcp_client`` replaces the public request methods on a ``ClientSession`` instance
with wrappers that open the right :class:`~forgesight_core.MCPScope` via the runtime, inject
``traceparent`` into the request ``_meta`` (so the server continues the trace), and close the
span with status / duration. A ``tools/call`` becomes the single span carrying both the
``mcp.*`` attributes and ``gen_ai.operation.name = execute_tool`` — never a second span.

Wrapping the *public* session API (not transport internals) keeps it resilient to MCP-SDK
churn (risk table §8). Idempotent; ``uninstrument_mcp_client`` restores the originals.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable, Sequence
from typing import Any

from forgesight_core import MCPScope, get_runtime

from .mapping import (
    MCP_PROMPT_NAME,
    MCP_RESOURCE_URI,
    PROMPTS_GET,
    RESOURCES_READ,
    TOOL_CALL_ARGUMENTS,
    TOOL_CALL_RESULT,
    TOOLS_CALL,
    resolve_methods,
    unknown_methods,
)
from .propagation import inject_traceparent

_log = logging.getLogger("forgesight.mcp")

_MARKER = "_forgesight_mcp"
# method string → the ClientSession attribute that issues it
_METHOD_ATTRS: dict[str, str] = {
    TOOLS_CALL: "call_tool",
    "tools/list": "list_tools",
    PROMPTS_GET: "get_prompt",
    "prompts/list": "list_prompts",
    RESOURCES_READ: "read_resource",
    "resources/list": "list_resources",
}

# Set while a tools/call span is in flight so a framework adapter (feat-019) can defer to
# the MCP span instead of opening a second execute_tool span (no double-instrument).
_IN_TOOLS_CALL: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "forgesight_mcp_in_tools_call", default=False
)


def in_mcp_tool_call() -> bool:
    """True while an MCP ``tools/call`` span is open on this context (re-entrancy guard)."""
    return _IN_TOOLS_CALL.get()


class tool_error(Exception):
    """Marks a ``CallToolResult.isError`` so the span records ``error.type = tool_error``.

    The runtime derives ``error.type`` from the exception's class name (feat-009), so the
    name is load-bearing — it is the exact ``error.type`` the MCP semconv mandates for an
    ``isError`` result (otel mapping §4.2).
    """


def instrument_mcp_client(
    session: Any,
    *,
    capture_content: bool | None = None,
    methods: Sequence[str] | None = None,
    server_name: str = "mcp",
) -> Any:
    """Wrap an MCP client session: span per request, W3C inject, ``mcp.*`` attrs.

    Idempotent — instrumenting an already-instrumented session is a no-op. Returns the same
    session for chaining.
    """
    if getattr(session, _MARKER, None) is not None:
        return session
    for name in unknown_methods(methods):
        _log.warning("forgesight-mcp: ignoring unknown MCP method %r", name)
    instrumentation = _ClientInstrumentation(
        session, capture_content=capture_content, methods=methods, server_name=server_name
    )
    instrumentation.apply()
    setattr(session, _MARKER, instrumentation)
    return session


def uninstrument_mcp_client(session: Any) -> None:
    """Restore a session's original methods. No-op if it was never instrumented."""
    instrumentation = getattr(session, _MARKER, None)
    if instrumentation is None:
        return
    instrumentation.restore()
    delattr(session, _MARKER)


class _ClientInstrumentation:
    def __init__(
        self,
        session: Any,
        *,
        capture_content: bool | None,
        methods: Sequence[str] | None,
        server_name: str,
    ) -> None:
        self._session = session
        self._capture_opt = capture_content
        self._methods = resolve_methods(methods)
        self._server_name = server_name
        self._originals: dict[str, Callable[..., Any]] = {}
        self._protocol_version: str | None = None

    # --- (un)patching ----------------------------------------------------
    def apply(self) -> None:
        self._wrap_initialize()
        for method in self._methods:
            attr = _METHOD_ATTRS[method]
            original = getattr(self._session, attr, None)
            if original is None or not callable(original):
                continue
            self._originals[attr] = original
            setattr(self._session, attr, self._make_wrapper(method, attr, original))

    def restore(self) -> None:
        for attr, original in self._originals.items():
            setattr(self._session, attr, original)
        self._originals.clear()

    # --- wrappers --------------------------------------------------------
    def _wrap_initialize(self) -> None:
        original = getattr(self._session, "initialize", None)
        if original is None or not callable(original):
            return
        self._originals["initialize"] = original

        async def initialize(*args: Any, **kwargs: Any) -> Any:
            result = await original(*args, **kwargs)
            version = getattr(result, "protocolVersion", None)
            if version is not None:
                self._protocol_version = str(version)
            return result

        self._session.initialize = initialize

    def _make_wrapper(
        self, method: str, attr: str, original: Callable[..., Any]
    ) -> Callable[..., Any]:
        is_tools_call = method == TOOLS_CALL

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            tool = _first_name(args, kwargs) if is_tools_call else None
            scope = MCPScope(
                get_runtime(), server=self._server_name, method=method, tool=tool, session_id=None
            )
            async with scope:
                if self._protocol_version is not None:
                    scope._call.protocol_version = self._protocol_version
                self._set_request_metadata(scope, method, args, kwargs)
                capture = self._resolve_capture()
                if is_tools_call:
                    kwargs = dict(kwargs)
                    kwargs["meta"] = inject_traceparent(
                        kwargs.get("meta"), trace_id=scope.trace_id, span_id=scope.span_id
                    )
                    if capture:
                        scope.set_metadata(**{TOOL_CALL_ARGUMENTS: _arguments(args, kwargs)})
                token = _IN_TOOLS_CALL.set(True) if is_tools_call else None
                try:
                    result = await original(*args, **kwargs)
                finally:
                    if token is not None:
                        _IN_TOOLS_CALL.reset(token)
                if is_tools_call:
                    self._handle_tool_result(scope, result, capture)
                return result

        return wrapper

    @staticmethod
    def _set_request_metadata(
        scope: MCPScope, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        if method == RESOURCES_READ:
            uri = args[0] if args else kwargs.get("uri")
            if uri is not None:
                scope.set_metadata(**{MCP_RESOURCE_URI: str(uri)})
        elif method == PROMPTS_GET:
            name = _first_name(args, kwargs)
            if name is not None:
                scope.set_metadata(**{MCP_PROMPT_NAME: name})

    def _handle_tool_result(self, scope: MCPScope, result: Any, capture: bool) -> None:
        if getattr(result, "isError", False):
            scope.record_error(tool_error("MCP tools/call returned isError"))
        elif capture:
            scope.set_metadata(**{TOOL_CALL_RESULT: _result_repr(result)})

    def _resolve_capture(self) -> bool:
        if self._capture_opt is not None:
            return self._capture_opt
        try:
            return bool(get_runtime().config.capture_content)
        except Exception:  # pragma: no cover - runtime always configured in practice
            return False


def _first_name(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    if args:
        return str(args[0])
    name = kwargs.get("name")
    return str(name) if name is not None else None


def _arguments(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    value = args[1] if len(args) > 1 else kwargs.get("arguments")
    return repr(value)


def _result_repr(result: Any) -> str:
    content = getattr(result, "content", result)
    return repr(content)
