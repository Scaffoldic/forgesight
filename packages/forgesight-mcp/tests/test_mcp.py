"""Tests for MCP instrumentation: mapping, propagation, content gate, errors, install."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from forgesight_api import Kind, RunStatus
from forgesight_core import InMemoryExporter, configure, reset_runtime
from forgesight_mcp import (
    in_mcp_tool_call,
    install,
    instrument_mcp_client,
    instrument_mcp_server,
    uninstall,
    uninstrument_mcp_client,
    uninstrument_mcp_server,
)
from forgesight_mcp.mapping import resolve_methods, unknown_methods
from forgesight_mcp.propagation import extract_context, inject_traceparent

# --- fakes mirroring the mcp public surface ----------------------------------


class FakeResult:
    def __init__(self, *, is_error: bool = False, content: object = "ok") -> None:
        self.isError = is_error
        self.content = content


class FakeSession:
    """Mimics mcp.ClientSession's public request methods; records the meta it receives."""

    def __init__(self) -> None:
        self.received_meta: dict[str, object] | None = None
        self.calls: list[str] = []

    async def initialize(self) -> Any:
        self.calls.append("initialize")
        return type("Init", (), {"protocolVersion": "2025-06-18"})()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        meta: dict[str, Any] | None = None,
    ) -> FakeResult:
        self.calls.append("call_tool")
        self.received_meta = meta
        return FakeResult()

    async def list_tools(self, cursor: str | None = None) -> str:
        self.calls.append("list_tools")
        return "tools"

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> str:
        self.calls.append("get_prompt")
        return "prompt"

    async def read_resource(self, uri: str) -> str:
        self.calls.append("read_resource")
        return "resource"

    async def list_prompts(self, cursor: str | None = None) -> str:
        return "prompts"

    async def list_resources(self, cursor: str | None = None) -> str:
        return "resources"


class CallToolRequest:  # name matters: REQUEST_TYPE_TO_METHOD keys on it
    def __init__(
        self, *, name: str, meta: dict[str, Any] | None = None, is_error: bool = False
    ) -> None:
        self.params = type("P", (), {"name": name, "meta": meta})()
        self._is_error = is_error


class FakeServer:
    def __init__(self, name: str = "github-mcp") -> None:
        self.name = name
        self.request_handlers: dict[type, Any] = {}


@pytest.fixture
def sink() -> Iterator[InMemoryExporter]:
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True)
    try:
        yield exporter
    finally:
        reset_runtime()


# --- client mapping -----------------------------------------------------------
async def test_tools_call_is_one_mapped_span(sink: InMemoryExporter) -> None:
    session = instrument_mcp_client(FakeSession())
    await session.call_tool("get_diff", {"pr": 42})
    records = [r for r in sink.records if r.kind is Kind.MCP]
    assert len(records) == 1  # exactly one span — no double-instrument
    record = records[0]
    assert record.mcp is not None
    assert record.mcp.method == "tools/call"
    assert record.mcp.tool == "get_diff"
    assert record.status is RunStatus.OK


async def test_other_methods_map(sink: InMemoryExporter) -> None:
    session = instrument_mcp_client(FakeSession())
    await session.list_tools()
    await session.read_resource("file:///x")
    await session.get_prompt("greet")
    by_method = {r.mcp.method: r for r in sink.records if r.mcp is not None}
    assert by_method["tools/list"].mcp.tool is None
    assert by_method["resources/read"].attributes["mcp.resource.uri"] == "file:///x"
    assert by_method["prompts/get"].attributes["mcp.prompt.name"] == "greet"


async def test_protocol_version_captured_from_initialize(sink: InMemoryExporter) -> None:
    session = instrument_mcp_client(FakeSession())
    await session.initialize()
    await session.call_tool("get_diff")
    record = next(r for r in sink.records if r.kind is Kind.MCP)
    assert record.mcp is not None
    assert record.mcp.protocol_version == "2025-06-18"


# --- propagation --------------------------------------------------------------
async def test_client_injects_traceparent(sink: InMemoryExporter) -> None:
    session = instrument_mcp_client(FakeSession())
    await session.call_tool("get_diff", {"pr": 1})
    record = next(r for r in sink.records if r.kind is Kind.MCP)
    assert session.received_meta is not None
    parsed = extract_context(session.received_meta)
    assert parsed is not None
    trace_id, _span_id = parsed
    assert trace_id == record.trace_id  # injected traceparent carries the span's trace


async def test_client_to_server_stitches_one_trace(sink: InMemoryExporter) -> None:
    # client side: capture the injected meta
    session = instrument_mcp_client(FakeSession())
    await session.call_tool("get_diff", {"pr": 1})
    client_record = next(r for r in sink.records if r.kind is Kind.MCP)
    meta = session.received_meta
    assert meta is not None

    # server side: feed that meta into an instrumented handler
    server = FakeServer()

    async def handler(req: CallToolRequest) -> FakeResult:
        return FakeResult(is_error=req._is_error)

    server.request_handlers[CallToolRequest] = handler
    instrument_mcp_server(server)
    await server.request_handlers[CallToolRequest](CallToolRequest(name="get_diff", meta=meta))

    server_record = [r for r in sink.records if r.kind is Kind.MCP][-1]
    assert server_record.trace_id == client_record.trace_id  # one trace across the hop
    assert server_record.parent_span_id is not None


# --- errors -------------------------------------------------------------------
async def test_is_error_result_sets_tool_error(sink: InMemoryExporter) -> None:
    class ErroringSession(FakeSession):
        async def call_tool(self, name, arguments=None, *, meta=None):  # type: ignore[no-untyped-def]
            return FakeResult(is_error=True)

    session = instrument_mcp_client(ErroringSession())
    await session.call_tool("get_diff")
    record = next(r for r in sink.records if r.kind is Kind.MCP)
    assert record.status is RunStatus.ERROR
    assert record.error is not None
    assert record.error.error_type == "tool_error"


async def test_raised_exception_marks_error(sink: InMemoryExporter) -> None:
    class BoomSession(FakeSession):
        async def call_tool(self, name, arguments=None, *, meta=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("transport down")

    session = instrument_mcp_client(BoomSession())
    with pytest.raises(RuntimeError, match="transport down"):
        await session.call_tool("get_diff")
    record = next(r for r in sink.records if r.kind is Kind.MCP)
    assert record.status is RunStatus.ERROR
    assert record.error is not None
    assert record.error.error_type == "RuntimeError"


async def test_server_is_error_marks_tool_error(sink: InMemoryExporter) -> None:
    server = FakeServer()

    async def handler(req: CallToolRequest) -> FakeResult:
        return FakeResult(is_error=True)

    server.request_handlers[CallToolRequest] = handler
    instrument_mcp_server(server)
    await server.request_handlers[CallToolRequest](CallToolRequest(name="get_diff"))
    record = next(r for r in sink.records if r.kind is Kind.MCP)
    assert record.status is RunStatus.ERROR
    assert record.error is not None
    assert record.error.error_type == "tool_error"


# --- content gate (P7) --------------------------------------------------------
async def test_content_absent_by_default(sink: InMemoryExporter) -> None:
    session = instrument_mcp_client(FakeSession())
    await session.call_tool("get_diff", {"pr": 42})
    record = next(r for r in sink.records if r.kind is Kind.MCP)
    assert "gen_ai.tool.call.arguments" not in record.attributes
    assert "gen_ai.tool.call.result" not in record.attributes


async def test_content_captured_when_opted_in(sink: InMemoryExporter) -> None:
    session = instrument_mcp_client(FakeSession(), capture_content=True)
    await session.call_tool("get_diff", {"pr": 42})
    record = next(r for r in sink.records if r.kind is Kind.MCP)
    assert "pr" in str(record.attributes["gen_ai.tool.call.arguments"])
    assert "gen_ai.tool.call.result" in record.attributes


async def test_capture_inherits_global_gate(sink: InMemoryExporter) -> None:
    reset_runtime()
    exporter = InMemoryExporter()
    configure(exporters=[exporter], sync_export=True, capture_content=True)
    try:
        session = instrument_mcp_client(FakeSession())  # no explicit opt ⇒ inherit global
        await session.call_tool("get_diff", {"pr": 7})
        record = next(r for r in exporter.records if r.kind is Kind.MCP)
        assert "gen_ai.tool.call.arguments" in record.attributes
    finally:
        reset_runtime()


# --- re-entrancy guard --------------------------------------------------------
async def test_in_mcp_tool_call_guard(sink: InMemoryExporter) -> None:
    seen: list[bool] = []

    class ProbingSession(FakeSession):
        async def call_tool(self, name, arguments=None, *, meta=None):  # type: ignore[no-untyped-def]
            seen.append(in_mcp_tool_call())
            return FakeResult()

    session = instrument_mcp_client(ProbingSession())
    assert in_mcp_tool_call() is False
    await session.call_tool("get_diff")
    assert seen == [True]  # the guard is set while the underlying call runs
    assert in_mcp_tool_call() is False  # and cleared after


# --- idempotency + uninstrument ----------------------------------------------
async def test_idempotent_and_uninstrument(sink: InMemoryExporter) -> None:
    session = FakeSession()
    instrument_mcp_client(session)
    instrument_mcp_client(session)  # no-op
    await session.call_tool("get_diff")
    assert len([r for r in sink.records if r.kind is Kind.MCP]) == 1  # one span, not two

    uninstrument_mcp_client(session)
    uninstrument_mcp_client(session)  # no-op
    await session.call_tool("get_diff")
    assert (
        len([r for r in sink.records if r.kind is Kind.MCP]) == 1
    )  # original restored: no new span


async def test_server_uninstrument_restores(sink: InMemoryExporter) -> None:
    server = FakeServer()

    async def handler(req: CallToolRequest) -> FakeResult:
        return FakeResult()

    server.request_handlers[CallToolRequest] = handler
    instrument_mcp_server(server)
    uninstrument_mcp_server(server)
    uninstrument_mcp_server(server)  # no-op
    await server.request_handlers[CallToolRequest](CallToolRequest(name="x"))
    assert [r for r in sink.records if r.kind is Kind.MCP] == []  # restored: no span


# --- methods filter -----------------------------------------------------------
async def test_methods_filter_restricts(sink: InMemoryExporter) -> None:
    session = instrument_mcp_client(FakeSession(), methods=["tools/call"])
    await session.call_tool("get_diff")
    await session.list_tools()  # not instrumented
    methods = [r.mcp.method for r in sink.records if r.mcp is not None]
    assert methods == ["tools/call"]


def test_unknown_methods_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        instrument_mcp_client(FakeSession(), methods=["tools/call", "bogus/method"])
    assert any("bogus/method" in r.message for r in caplog.records)


# --- propagation units --------------------------------------------------------
def test_inject_extract_roundtrip() -> None:
    meta = inject_traceparent(
        {"existing": 1}, trace_id="4bf92f3577b34da6a3ce929d0e0e4736", span_id="00f067aa0ba902b7"
    )
    assert meta["existing"] == 1
    assert extract_context(meta) == ("4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7")


@pytest.mark.parametrize(
    "meta",
    [
        None,
        {},
        {"traceparent": "garbage"},
        {"traceparent": "00-short-x-01"},
        {"traceparent": "00-" + "0" * 32 + "-00f067aa0ba902b7-01"},
    ],
)
def test_extract_rejects_bad_headers(meta: dict[str, object] | None) -> None:
    assert extract_context(meta) is None


def test_resolve_and_unknown_methods() -> None:
    assert resolve_methods(None) == resolve_methods(list(resolve_methods(None)))
    assert resolve_methods(["tools/call"]) == frozenset({"tools/call"})
    assert unknown_methods(["tools/call", "x"]) == ["x"]
    assert unknown_methods(None) == []


# --- install / uninstall (auto-instrument seam) ------------------------------
def test_install_auto_instruments_new_instances() -> None:
    class AutoSession(FakeSession):
        pass

    class AutoServer(FakeServer):
        pass

    try:
        assert install(
            {"enabled": True, "auto_instrument": True},
            _client_cls=AutoSession,
            _server_cls=AutoServer,
        )
        assert install({"auto_instrument": True}, _client_cls=AutoSession) is True  # idempotent
        session = AutoSession()
        assert getattr(session, "_forgesight_mcp", None) is not None  # auto-instrumented on init
    finally:
        uninstall()
    fresh = AutoSession()
    assert getattr(fresh, "_forgesight_mcp", None) is None  # uninstalled: back to normal


def test_install_respects_disabled() -> None:
    class S(FakeSession):
        pass

    assert install({"enabled": False}, _client_cls=S) is False
    assert install({"auto_instrument": False}, _client_cls=S) is False
    assert getattr(S(), "_forgesight_mcp", None) is None


def test_install_logs_content_capture(caplog: pytest.LogCaptureFixture) -> None:
    class S(FakeSession):
        pass

    try:
        with caplog.at_level("INFO"):
            install({"capture_content": True}, _client_cls=S, _server_cls=None)
        assert any("content capture is ON" in r.message for r in caplog.records)
    finally:
        uninstall()


# --- edge coverage: client method/initialize absence -------------------------
async def test_minimal_session_skips_missing_methods(sink: InMemoryExporter) -> None:
    class MinimalSession:
        async def call_tool(self, name, arguments=None, *, meta=None):  # type: ignore[no-untyped-def]
            return FakeResult()

    session = instrument_mcp_client(MinimalSession())  # no initialize / list_tools / etc.
    await session.call_tool("get_diff")
    assert len([r for r in sink.records if r.kind is Kind.MCP]) == 1


async def test_initialize_without_protocol_version(sink: InMemoryExporter) -> None:
    class NoVersionSession(FakeSession):
        async def initialize(self):  # type: ignore[no-untyped-def]
            return object()  # no protocolVersion attribute

    session = instrument_mcp_client(NoVersionSession())
    await session.initialize()
    await session.call_tool("get_diff")
    record = next(r for r in sink.records if r.kind is Kind.MCP)
    assert record.mcp is not None
    assert record.mcp.protocol_version is None


# --- edge coverage: server ----------------------------------------------------
def test_server_without_request_handlers_warns(caplog: pytest.LogCaptureFixture) -> None:
    class BadServer:
        request_handlers = None  # not a dict

    with caplog.at_level("WARNING"):
        instrument_mcp_server(BadServer())
    assert any("no request_handlers" in r.message for r in caplog.records)


async def test_server_skips_unknown_request_types(sink: InMemoryExporter) -> None:
    server = FakeServer()

    class MysteryRequest:  # not in REQUEST_TYPE_TO_METHOD
        params = None

    async def handler(req: object) -> FakeResult:
        return FakeResult()

    server.request_handlers[MysteryRequest] = handler
    instrument_mcp_server(server)
    await server.request_handlers[MysteryRequest](MysteryRequest())
    assert [r for r in sink.records if r.kind is Kind.MCP] == []  # unknown type ⇒ not spanned


def test_server_unknown_methods_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        instrument_mcp_server(FakeServer(), methods=["bogus/method"])
    assert any("bogus/method" in r.message for r in caplog.records)


async def test_server_extracts_meta_from_pydantic_like_params(sink: InMemoryExporter) -> None:
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    span_id = "00f067aa0ba902b7"

    class PydanticMeta:
        def model_dump(self) -> dict[str, object]:
            return {"traceparent": f"00-{trace_id}-{span_id}-01"}

    class Req:
        params = type("P", (), {"name": "get_diff", "meta": PydanticMeta()})()

    Req.__name__ = "CallToolRequest"  # map to tools/call
    server = FakeServer()

    async def handler(req: object) -> FakeResult:
        return FakeResult()

    server.request_handlers[Req] = handler
    instrument_mcp_server(server)
    await server.request_handlers[Req](Req())
    record = next(r for r in sink.records if r.kind is Kind.MCP)
    assert record.trace_id == trace_id  # extracted from a model_dump()-able _meta


# --- propagation edge cases ---------------------------------------------------
def test_inject_tracestate_explicit_and_preserved() -> None:
    explicit = inject_traceparent({}, trace_id="a" * 32, span_id="b" * 16, tracestate="vendor=1")
    assert explicit["tracestate"] == "vendor=1"
    preserved = inject_traceparent({"tracestate": "keep=1"}, trace_id="a" * 32, span_id="b" * 16)
    assert preserved["tracestate"] == "keep=1"
    none = inject_traceparent({}, trace_id="a" * 32, span_id="b" * 16)
    assert "tracestate" not in none


def test_extract_non_string_traceparent() -> None:
    assert extract_context({"traceparent": 1234}) is None


def test_extract_non_hex_of_right_length() -> None:
    bad = {"traceparent": "00-" + "g" * 32 + "-" + "0" * 16 + "-01"}
    assert extract_context(bad) is None


def test_get_tracestate() -> None:
    from forgesight_mcp.propagation import get_tracestate

    assert get_tracestate({"tracestate": "vendor=1"}) == "vendor=1"
    assert get_tracestate({}) is None
    assert get_tracestate(None) is None
    assert get_tracestate({"tracestate": 5}) is None


async def test_server_idempotent(sink: InMemoryExporter) -> None:
    server = FakeServer()

    async def handler(req: CallToolRequest) -> FakeResult:
        return FakeResult()

    server.request_handlers[CallToolRequest] = handler
    instrument_mcp_server(server)
    first = server.request_handlers[CallToolRequest]
    instrument_mcp_server(server)  # idempotent — early return, no re-wrap
    assert server.request_handlers[CallToolRequest] is first


async def test_server_meta_object_without_model_dump(sink: InMemoryExporter) -> None:
    class Req:
        params = type(
            "P", (), {"name": "x", "meta": object()}
        )()  # meta: not a Mapping, no model_dump

    Req.__name__ = "CallToolRequest"
    server = FakeServer()

    async def handler(req: object) -> FakeResult:
        return FakeResult()

    server.request_handlers[Req] = handler
    instrument_mcp_server(server)
    await server.request_handlers[Req](Req())  # no parent extracted ⇒ new root span
    record = next(r for r in sink.records if r.kind is Kind.MCP)
    assert record.parent_span_id is None


def test_server_uninstrument_without_handlers_dict() -> None:
    class BadServer:
        request_handlers = None

    instrument_mcp_server(BadServer())  # warns, registers nothing
    uninstrument_mcp_server(BadServer())  # restore path with no dict — must not raise
