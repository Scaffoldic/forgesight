"""MCP **server** instrumentation — extract ``traceparent``, span per handled request.

``instrument_mcp_server`` wraps the handlers registered on a ``Server`` instance so each
handled request extracts the caller's W3C context from the request ``_meta`` and opens a
:class:`~forgesight_core.MCPScope` as a **child** of the client's span — stitching one trace
across the transport. A ``tools/call`` whose result ``isError`` is marked ``error.type =
tool_error``.

It wraps the public ``request_handlers`` registry (not transport internals), so a server
owner sees their ``tools/call`` latency in the same trace the caller sees.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from forgesight_core import MCPScope, get_runtime
from forgesight_core.context import (
    TelemetryContext,
    new_run_id,
    reset_current_context,
    set_current_context,
)

from .client import tool_error
from .mapping import REQUEST_TYPE_TO_METHOD, TOOLS_CALL, resolve_methods, unknown_methods
from .propagation import extract_context

_log = logging.getLogger("forgesight.mcp")
_MARKER = "_forgesight_mcp_server"


def instrument_mcp_server(
    server: Any,
    *,
    capture_content: bool | None = None,
    methods: Sequence[str] | None = None,
    server_name: str | None = None,
) -> Any:
    """Wrap an MCP server: extract incoming ``traceparent``, span per handled request.

    Idempotent. Returns the same server.
    """
    if getattr(server, _MARKER, None) is not None:
        return server
    for name in unknown_methods(methods):
        _log.warning("forgesight-mcp: ignoring unknown MCP method %r", name)
    instrumentation = _ServerInstrumentation(
        server, capture_content=capture_content, methods=methods, server_name=server_name
    )
    instrumentation.apply()
    setattr(server, _MARKER, instrumentation)
    return server


def uninstrument_mcp_server(server: Any) -> None:
    """Restore a server's original handlers. No-op if it was never instrumented."""
    instrumentation = getattr(server, _MARKER, None)
    if instrumentation is None:
        return
    instrumentation.restore()
    delattr(server, _MARKER)


class _ServerInstrumentation:
    def __init__(
        self,
        server: Any,
        *,
        capture_content: bool | None,
        methods: Sequence[str] | None,
        server_name: str | None,
    ) -> None:
        self._server = server
        self._capture_opt = capture_content
        self._methods = resolve_methods(methods)
        self._server_name = server_name or str(getattr(server, "name", None) or "mcp")
        self._originals: dict[Any, Any] = {}

    def apply(self) -> None:
        handlers = getattr(self._server, "request_handlers", None)
        if not isinstance(handlers, dict):
            _log.warning("forgesight-mcp: server has no request_handlers; nothing to instrument")
            return
        for request_type, handler in list(handlers.items()):
            method = REQUEST_TYPE_TO_METHOD.get(getattr(request_type, "__name__", ""))
            if method is None or method not in self._methods or not callable(handler):
                continue
            self._originals[request_type] = handler
            handlers[request_type] = self._make_wrapper(method, handler)

    def restore(self) -> None:
        handlers = getattr(self._server, "request_handlers", None)
        if isinstance(handlers, dict):
            for request_type, handler in self._originals.items():
                handlers[request_type] = handler
        self._originals.clear()

    def _make_wrapper(self, method: str, original: Any) -> Any:
        async def wrapper(req: Any) -> Any:
            params = getattr(req, "params", None)
            parent = extract_context(_meta_mapping(params))
            token = None
            if parent is not None:
                trace_id, span_id = parent
                token = set_current_context(
                    TelemetryContext(
                        run_id=new_run_id(), trace_id=trace_id, current_span_id=span_id
                    )
                )
            try:
                tool = getattr(params, "name", None) if method == TOOLS_CALL else None
                scope = MCPScope(
                    get_runtime(),
                    server=self._server_name,
                    method=method,
                    tool=str(tool) if tool is not None else None,
                    session_id=None,
                )
                async with scope:
                    result = await original(req)
                    if method == TOOLS_CALL and _result_is_error(result):
                        scope.record_error(tool_error("MCP tools/call handler returned isError"))
                    return result
            finally:
                if token is not None:
                    reset_current_context(token)

        return wrapper


def _meta_mapping(params: Any) -> Mapping[str, object] | None:
    """Coerce an MCP request params' ``_meta`` (pydantic model or dict) to a plain mapping."""
    meta = getattr(params, "meta", None)
    if meta is None:
        return None
    if isinstance(meta, Mapping):
        return meta
    dump = getattr(meta, "model_dump", None)
    if callable(dump):
        dumped = dump()
        return dumped if isinstance(dumped, Mapping) else None
    return None


def _result_is_error(result: Any) -> bool:
    if getattr(result, "isError", False):
        return True
    root = getattr(result, "root", None)
    return bool(getattr(root, "isError", False))
