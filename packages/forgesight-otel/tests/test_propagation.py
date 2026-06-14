"""Tests for W3C TraceContext inject/extract roundtrip."""

from __future__ import annotations

from forgesight_otel import extract, inject

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN = "00f067aa0ba902b7"


def test_inject_then_extract_roundtrips() -> None:
    carrier = inject(TRACE, SPAN)
    assert "traceparent" in carrier
    assert TRACE in carrier["traceparent"]
    result = extract(carrier)
    assert result == (TRACE, SPAN)


def test_extract_empty_carrier_is_none() -> None:
    assert extract({}) is None


def test_inject_into_existing_carrier() -> None:
    carrier = {"x-custom": "keep"}
    out = inject(TRACE, SPAN, carrier)
    assert out is carrier
    assert out["x-custom"] == "keep"
    assert "traceparent" in out
