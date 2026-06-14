"""Agent-author test surface: in-memory sink, span-tree assertions, factories.

Deterministic, synchronous tooling so a test asserts *what the agent recorded*
without a real backend. The conformance suites for integration authors live in
:mod:`forgesight_core.testing.conformance`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from forgesight_api import Kind, LLMCall, Record, RunStatus, TokenUsage, ToolCall

from ..exporters import InMemoryExporter

_OP_BY_KIND = {
    Kind.AGENT: "invoke_agent",
    Kind.WORKFLOW: "invoke_workflow",
    Kind.STEP: "step",
    Kind.LLM: "chat",
    Kind.TOOL: "execute_tool",
    Kind.MCP: "execute_tool",
}


@dataclass(slots=True)
class SpanData:
    """A record rendered into a tree node — the shape a backend would see."""

    record: Record
    children: list[SpanData] = field(default_factory=list)

    @property
    def kind(self) -> Kind:
        return self.record.kind

    @property
    def op(self) -> str:
        return _OP_BY_KIND[self.record.kind]

    @property
    def name(self) -> str:
        return self.record.name

    @property
    def status(self) -> RunStatus:
        return self.record.status

    @property
    def attributes(self) -> Mapping[str, object]:
        return self.record.attributes


def build_spans(records: Sequence[Record]) -> list[SpanData]:
    """Render records into root span trees by ``span_id`` / ``parent_span_id``."""
    nodes = {r.span_id: SpanData(record=r) for r in records}
    roots: list[SpanData] = []
    for record in records:
        node = nodes[record.span_id]
        parent = nodes.get(record.parent_span_id) if record.parent_span_id else None
        if parent is not None:
            parent.children.append(node)
        else:
            roots.append(node)
    return roots


def _all_spans(sink: InMemoryExporter) -> list[SpanData]:
    out: list[SpanData] = []

    def walk(node: SpanData) -> None:
        out.append(node)
        for child in node.children:
            walk(child)

    for root in build_spans(sink.records):
        walk(root)
    return out


def find_spans(
    sink: InMemoryExporter, *, op: str | None = None, name: str | None = None
) -> list[SpanData]:
    """All spans matching ``op`` and/or ``name``."""
    return [
        s
        for s in _all_spans(sink)
        if (op is None or s.op == op) and (name is None or s.name == name)
    ]


def find_span(
    sink: InMemoryExporter, *, op: str | None = None, name: str | None = None
) -> SpanData:
    """Exactly one span matching ``op``/``name``; raises if zero or many."""
    matches = find_spans(sink, op=op, name=name)
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one span (op={op!r}, name={name!r}), got {len(matches)}"
        )
    return matches[0]


def assert_span_tree(sink: InMemoryExporter, expected: Mapping[str, object]) -> None:
    """Assert the recorded tree contains a root matching ``expected``.

    Keys: ``op`` / ``name`` (exact), ``attrs`` (subset of business attributes),
    ``children`` (each must match some child; sibling order-insensitive).
    """
    roots = build_spans(sink.records)
    if not any(_matches(root, expected) for root in roots):
        raise AssertionError(f"no root span matched {expected!r}; got {[r.op for r in roots]}")


def _matches(span: SpanData, expected: Mapping[str, object]) -> bool:
    if "op" in expected and span.op != expected["op"]:
        return False
    if "name" in expected and span.name != expected["name"]:
        return False
    attrs = expected.get("attrs")
    if isinstance(attrs, Mapping):
        for key, value in attrs.items():
            if span.attributes.get(key) != value:
                return False
    children = expected.get("children")
    if isinstance(children, list):
        for expected_child in children:
            if not any(_matches(child, expected_child) for child in span.children):
                return False
    return True


# --- factories -------------------------------------------------------------
_USAGE_FIELDS = ("input", "output", "cache_read", "cache_creation", "reasoning")


def token_usage_factory(**overrides: int) -> TokenUsage:
    return TokenUsage(**overrides)


def llm_call_factory(**overrides: object) -> LLMCall:
    usage_kwargs = {k: overrides.pop(k) for k in _USAGE_FIELDS if k in overrides}
    usage = TokenUsage(**usage_kwargs)  # type: ignore[arg-type]
    params: dict[str, object] = {
        "provider": overrides.pop("provider", "test-provider"),
        "request_model": overrides.pop("request_model", "test-model"),
        "usage": usage,
    }
    params.update(overrides)
    return LLMCall(**params)  # type: ignore[arg-type]


def tool_call_factory(**overrides: object) -> ToolCall:
    name = overrides.pop("name", "test-tool")
    return ToolCall(name=str(name), **overrides)  # type: ignore[arg-type]


__all__ = [
    "InMemoryExporter",
    "SpanData",
    "assert_span_tree",
    "build_spans",
    "find_span",
    "find_spans",
    "llm_call_factory",
    "token_usage_factory",
    "tool_call_factory",
]
