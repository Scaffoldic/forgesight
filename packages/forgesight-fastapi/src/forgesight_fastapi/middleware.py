"""``AgentForgeMiddleware`` — pure-ASGI middleware that correlates a request with an agent run.

Per request it: continues an incoming W3C trace (or starts a root), opens an
``agent_run`` / ``workflow_run`` span via the feat-002 runtime, binds it so the handler's
llm/tool/mcp calls nest under it, attaches ``http.route`` / ``http.method`` / status as
business metadata (FR-5), sets the ``run_id`` response header for correlation, and closes
the span with the response status (5xx ⇒ ERROR; 4xx ⇒ recorded; unhandled exception ⇒
ERROR + re-raise, FR-7).

Implemented as **pure ASGI** (not ``BaseHTTPMiddleware``) to avoid its streaming / lifespan
pitfalls (risk table §8). Request/response bodies are captured only when ``capture_content``
resolves true (P7).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from forgesight_core import RunScope, WorkflowScope, get_runtime
from forgesight_core.context import (
    TelemetryContext,
    new_run_id,
    reset_current_context,
    set_current_context,
)

from ._config import (
    log_content_capture,
    resolve_capture_content,
    resolve_exclude_paths,
    resolve_include_routes,
    resolve_run_id_header,
    resolve_span_kind,
)
from ._w3c import extract_parent

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class HTTPServerError(Exception):
    """Synthesised for a 5xx response so the run span records ``error.type`` (FR-7)."""


class AgentForgeMiddleware:
    """ASGI middleware: one agent-run span per request, correlated and flushed cleanly."""

    def __init__(
        self,
        app: Any,
        *,
        span_kind: str | None = None,
        agent_name: str | Callable[[Any], str] = "fastapi-app",
        exclude_paths: Sequence[str] | None = None,
        include_routes: Sequence[str] | None = None,
        capture_content: bool | None = None,
        run_id_header: str | None = None,
    ) -> None:
        self._app = app
        self._span_kind = resolve_span_kind(span_kind)
        self._agent_name = agent_name
        self._exclude_paths = resolve_exclude_paths(exclude_paths)
        self._include_routes = resolve_include_routes(include_routes)
        self._capture_opt = resolve_capture_content(capture_content)
        if self._capture_opt:
            log_content_capture()
        self._run_id_header = resolve_run_id_header(run_id_header).lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not self._should_instrument(scope.get("path", "")):
            await self._app(scope, receive, send)
            return

        token = self._bind_incoming_trace(scope)
        try:
            await self._handle(scope, receive, send)
        finally:
            if token is not None:
                reset_current_context(token)

    # --- internals --------------------------------------------------------
    async def _handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        run_scope = self._open_scope(scope)
        capture = self._resolve_capture()
        status: dict[str, int] = {}
        body_chunks: list[bytes] = []

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((self._run_id_header, run_scope.run_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        async def receive_wrapper() -> dict[str, Any]:
            message = await receive()
            if message.get("type") == "http.request":
                body_chunks.append(message.get("body", b""))
            return message

        downstream_receive = receive_wrapper if capture else receive
        async with run_scope:
            self._set_request_metadata(run_scope, scope)
            try:
                await self._app(scope, downstream_receive, send_wrapper)
            finally:
                self._finalize(run_scope, scope, status, capture, body_chunks)

    def _bind_incoming_trace(self, scope: Scope) -> Any:
        parent = extract_parent(scope.get("headers") or [])
        if parent is None:
            return None
        trace_id, span_id = parent
        return set_current_context(
            TelemetryContext(run_id=new_run_id(), trace_id=trace_id, current_span_id=span_id)
        )

    def _open_scope(self, scope: Scope) -> RunScope | WorkflowScope:
        name = self._resolve_agent_name(scope)
        runtime = get_runtime()
        if self._span_kind == "workflow_run":
            return WorkflowScope(runtime, name=name)
        return RunScope(runtime, name=name)

    def _resolve_agent_name(self, scope: Scope) -> str:
        if callable(self._agent_name):
            from starlette.requests import Request

            return str(self._agent_name(Request(scope)))
        return self._agent_name

    @staticmethod
    def _set_request_metadata(run_scope: RunScope | WorkflowScope, scope: Scope) -> None:
        run_scope.set_metadata(
            **{"http.method": scope.get("method", ""), "http.target": scope.get("path", "")}
        )

    def _finalize(
        self,
        run_scope: RunScope | WorkflowScope,
        scope: Scope,
        status: dict[str, int],
        capture: bool,
        body_chunks: list[bytes],
    ) -> None:
        run_scope.set_metadata(**{"http.route": _route_template(scope)})
        code = status.get("code")
        if code is not None:
            run_scope.set_metadata(**{"http.status_code": code})
            if code >= 500:
                run_scope.record_error(HTTPServerError(f"HTTP {code} server error"), code=str(code))
        if capture and body_chunks:
            body = b"".join(body_chunks).decode("utf-8", "replace")
            if body:
                run_scope.set_metadata(**{"http.request.body": body})

    def _should_instrument(self, path: str) -> bool:
        if any(path.startswith(prefix) for prefix in self._exclude_paths):
            return False
        if self._include_routes is not None:
            return any(path.startswith(prefix) for prefix in self._include_routes)
        return True

    def _resolve_capture(self) -> bool:
        if self._capture_opt is not None:
            return self._capture_opt
        try:
            return bool(get_runtime().config.capture_content)
        except Exception:  # pragma: no cover - runtime always present in practice
            return False


def _route_template(scope: Scope) -> str:
    """Reconstruct the matched route template (``/agents/{id}/run``) for bounded cardinality.

    Built from the raw path + ``path_params`` the router fills in after matching, so it
    works regardless of whether the ASGI server exposes ``scope['route']``.
    """
    path = str(scope.get("path", ""))
    params = scope.get("path_params") or {}
    template = path
    for name, value in params.items():
        template = template.replace(str(value), "{" + name + "}", 1)
    return template
