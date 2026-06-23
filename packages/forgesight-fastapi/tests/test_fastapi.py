"""Tests for the FastAPI integration: correlation, propagation, errors, lifespan flush."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from forgesight_api import Kind, RunStatus
from forgesight_core import InMemoryExporter, configure, get_runtime, reset_runtime, telemetry
from forgesight_fastapi import ForgeSightMiddleware, sdk_lifespan
from forgesight_fastapi._config import install

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN = "00f067aa0ba902b7"


@pytest.fixture
def sink() -> Iterator[InMemoryExporter]:
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True)
    try:
        yield exporter
    finally:
        reset_runtime()


def _build_app(sink: InMemoryExporter, **mw: object) -> Starlette:
    async def run_handler(request: Request) -> JSONResponse:
        await request.body()  # read the body so capture (when on) sees the chunks
        # the request span is open and bound — a child llm call nests under it
        run = telemetry.current_run()
        if run is not None:  # None under workflow_run span_kind
            with run.llm_call("anthropic", "claude-sonnet-4-5") as call:
                call.record_usage(input=10, output=5)
        return JSONResponse({"ok": True})

    async def boom(request: Request) -> JSONResponse:
        raise RuntimeError("handler exploded")

    async def server_error(request: Request) -> PlainTextResponse:
        return PlainTextResponse("nope", status_code=503)

    async def bad_request(request: Request) -> PlainTextResponse:
        return PlainTextResponse("bad", status_code=422)

    async def health(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[
            Route("/agents/{agent_id}/run", run_handler, methods=["POST"]),
            Route("/boom", boom, methods=["GET"]),
            Route("/server-error", server_error, methods=["GET"]),
            Route("/bad", bad_request, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
        ]
    )
    app.add_middleware(ForgeSightMiddleware, **mw)
    return app


# --- correlation + mapping ----------------------------------------------------
def test_request_opens_run_span_with_route_template(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink))
    response = client.post("/agents/pr-reviewer/run")
    assert response.status_code == 200
    assert response.headers["x-forgesight-run-id"]  # run_id correlation header

    runs = [r for r in sink.records if r.kind is Kind.AGENT]
    assert len(runs) == 1
    run = runs[0]
    assert run.attributes["http.route"] == "/agents/{agent_id}/run"  # template, not raw path
    assert run.attributes["http.method"] == "POST"
    assert run.attributes["http.status_code"] == 200
    assert run.status is RunStatus.OK
    # the header run_id matches the run record
    assert response.headers["x-forgesight-run-id"] == run.run_id


def test_child_calls_nest_under_request_run(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink))
    client.post("/agents/x/run")
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    llm = next(r for r in sink.records if r.kind is Kind.LLM)
    assert llm.trace_id == run.trace_id  # same trace
    assert llm.parent_span_id == run.span_id  # nested under the request span


def test_workflow_span_kind(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink, span_kind="workflow_run"))
    client.post("/agents/x/run")
    assert any(r.kind is Kind.WORKFLOW for r in sink.records)


def test_agent_name_callable(sink: InMemoryExporter) -> None:
    app = _build_app(sink, agent_name=lambda req: f"svc:{req.url.path}")
    TestClient(app).post("/agents/x/run")
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert run.name == "svc:/agents/x/run"


# --- exclude / include --------------------------------------------------------
def test_excluded_path_gets_no_span(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink))
    client.get("/health")
    assert [r for r in sink.records if r.kind is Kind.AGENT] == []


def test_include_routes_allow_list(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink, include_routes=["/agents"]))
    client.get("/bad")  # not in include list ⇒ no span
    client.post("/agents/x/run")  # included
    runs = [r for r in sink.records if r.kind is Kind.AGENT]
    assert len(runs) == 1
    assert runs[0].attributes["http.target"] == "/agents/x/run"


# --- propagation --------------------------------------------------------------
def test_incoming_traceparent_continued(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink))
    client.post("/agents/x/run", headers={"traceparent": f"00-{TRACE}-{SPAN}-01"})
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert run.trace_id == TRACE  # request run is a child of the upstream trace
    assert run.parent_span_id == SPAN


def test_malformed_traceparent_starts_new_root(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink))
    client.post("/agents/x/run", headers={"traceparent": "garbage"})
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert run.trace_id != TRACE
    assert run.parent_span_id is None  # new root, not a broken child


# --- error mapping ------------------------------------------------------------
def test_5xx_marks_span_error(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink))
    client.get("/server-error")
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert run.status is RunStatus.ERROR
    assert run.error is not None
    assert run.attributes["http.status_code"] == 503


def test_4xx_recorded_without_error(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink))
    client.get("/bad")
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert run.status is RunStatus.OK  # 4xx is the caller's fault, not a server error
    assert run.attributes["http.status_code"] == 422


def test_unhandled_exception_recorded_and_reraised(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink), raise_server_exceptions=False)
    client.get("/boom")
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert run.status is RunStatus.ERROR
    assert run.error is not None
    assert run.error.error_type == "RuntimeError"


# --- content gate (P7) --------------------------------------------------------
def test_body_absent_by_default(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink))
    client.post("/agents/x/run", content=b'{"task": "secret"}')
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert "http.request.body" not in run.attributes


def test_body_captured_when_opted_in(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink, capture_content=True))
    client.post("/agents/x/run", content=b'{"task": "do it"}')
    run = next(r for r in sink.records if r.kind is Kind.AGENT)
    assert "do it" in str(run.attributes["http.request.body"])


# --- lifespan flush -----------------------------------------------------------
class RetainingExporter:
    """Like InMemoryExporter but does NOT clear on shutdown — so a test can assert the
    flushed batch *after* the lifespan shutdown that drains it."""

    def __init__(self) -> None:
        self.records: list[object] = []

    def export(self, records: object) -> object:
        from forgesight_api import ExportResult

        self.records.extend(records)  # type: ignore[arg-type]
        return ExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        return None  # retain across shutdown


def test_lifespan_configures_and_flushes() -> None:
    exporter = RetainingExporter()

    async def handler(request: Request) -> JSONResponse:
        with telemetry.agent_run("x"):
            pass
        return JSONResponse({"ok": True})

    def lifespan(app: Starlette):  # type: ignore[no-untyped-def]
        return sdk_lifespan(app, exporters=[exporter], sync_export=False)

    app = Starlette(routes=[Route("/agents/x/run", handler, methods=["POST"])], lifespan=lifespan)
    app.add_middleware(ForgeSightMiddleware)

    with TestClient(app) as client:  # __enter__ runs startup, __exit__ runs shutdown
        client.post("/agents/x/run")
    # after shutdown, the buffered batch has been flushed (force_flush + shutdown) to the exporter
    assert any(getattr(r, "kind", None) is Kind.AGENT for r in exporter.records)
    reset_runtime()


def test_lifespan_respects_already_configured() -> None:
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True)
    try:
        runtime_before = get_runtime()

        async def cm() -> None:
            async with sdk_lifespan(configure_sdk=False):
                with telemetry.agent_run("x"):
                    # assert the record landed BEFORE shutdown clears the InMemoryExporter
                    pass
                assert any(r.kind is Kind.AGENT for r in exporter.records)
                assert get_runtime() is runtime_before  # not reconfigured

        import anyio

        anyio.run(cm)
    finally:
        reset_runtime()


# --- config / install ---------------------------------------------------------
def test_install_provides_defaults(sink: InMemoryExporter) -> None:
    try:
        assert install({"enabled": True, "span_kind": "workflow_run"}) is True
        client = TestClient(_build_app(sink))  # no explicit span_kind ⇒ from install()
        client.post("/agents/x/run")
        assert any(r.kind is Kind.WORKFLOW for r in sink.records)
    finally:
        install({"enabled": False})  # clear


def test_install_disabled_returns_false() -> None:
    assert install({"enabled": False}) is False


def test_invalid_span_kind_rejected(sink: InMemoryExporter) -> None:
    with pytest.raises(ValueError, match="span_kind must be"):
        ForgeSightMiddleware(_build_app(sink), span_kind="teleport")


def test_non_http_scope_passes_through() -> None:
    # a lifespan/websocket scope must not be instrumented
    configure(exporters=[InMemoryExporter()], sync_export=True)
    try:
        seen = {}

        async def app(scope, receive, send):  # type: ignore[no-untyped-def]
            seen["type"] = scope["type"]

        mw = ForgeSightMiddleware(app)

        async def run() -> None:
            await mw({"type": "lifespan"}, _noop_receive, _noop_send)

        import anyio

        anyio.run(run)
        assert seen["type"] == "lifespan"
    finally:
        reset_runtime()


async def _noop_receive() -> dict[str, object]:
    return {"type": "noop"}


async def _noop_send(message: dict[str, object]) -> None:
    return None


def test_run_id_header_custom(sink: InMemoryExporter) -> None:
    client = TestClient(_build_app(sink, run_id_header="x-run"))
    response = client.post("/agents/x/run")
    assert "x-run" in response.headers
    assert get_runtime() is not None


# --- _w3c unit coverage -------------------------------------------------------
def test_w3c_extract_valid_and_missing() -> None:
    from forgesight_fastapi._w3c import extract_parent

    headers = [(b"traceparent", f"00-{TRACE}-{SPAN}-01".encode())]
    assert extract_parent(headers) == (TRACE, SPAN)
    assert extract_parent([(b"content-type", b"application/json")]) is None  # no traceparent


@pytest.mark.parametrize(
    "value",
    [
        "garbage",  # wrong part count
        f"00-{TRACE}-tooShort-01",  # wrong span length
        "00-" + "g" * 32 + "-" + "0" * 16 + "-01",  # non-hex
        "00-" + "0" * 32 + "-" + SPAN + "-01",  # all-zero trace
    ],
)
def test_w3c_rejects_bad_traceparent(value: str) -> None:
    from forgesight_fastapi._w3c import extract_parent

    assert extract_parent([(b"traceparent", value.encode())]) is None


# --- _config resolver coverage ------------------------------------------------
def test_config_env_resolvers(monkeypatch: pytest.MonkeyPatch) -> None:
    from forgesight_fastapi._config import (
        resolve_exclude_paths,
        resolve_run_id_header,
        resolve_span_kind,
    )

    monkeypatch.setenv("FORGESIGHT_FASTAPI_EXCLUDE", "/a, /b")
    monkeypatch.setenv("FORGESIGHT_FASTAPI_RUN_ID_HEADER", "x-corr")
    monkeypatch.setenv("FORGESIGHT_FASTAPI_SPAN_KIND", "workflow_run")
    assert resolve_exclude_paths(None) == ("/a", "/b")
    assert resolve_run_id_header(None) == "x-corr"
    assert resolve_span_kind(None) == "workflow_run"


def test_config_install_include_routes_and_run_id() -> None:
    from forgesight_fastapi._config import resolve_include_routes, resolve_run_id_header

    try:
        install({"include_routes": ["/agents"], "run_id_header": "x-from-yaml"})
        assert resolve_include_routes(None) == ("/agents",)
        assert resolve_run_id_header(None) == "x-from-yaml"
    finally:
        install({"enabled": False})


def test_config_capture_content_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    from forgesight_fastapi._config import resolve_capture_content

    monkeypatch.setenv("FORGESIGHT_FASTAPI_CAPTURE_CONTENT", "true")
    assert resolve_capture_content(None) is True  # env path
    monkeypatch.delenv("FORGESIGHT_FASTAPI_CAPTURE_CONTENT")
    try:
        install({"capture_content": True})  # logs once + sets installed default
        install({"capture_content": True})  # already logged ⇒ no second log
        assert resolve_capture_content(None) is True  # installed path
    finally:
        install({"enabled": False})
    assert resolve_capture_content(None) is None  # nothing set ⇒ inherit global gate
