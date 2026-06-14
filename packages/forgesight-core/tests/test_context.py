"""Tests for context propagation primitives."""

from __future__ import annotations

from forgesight_api import is_valid_ulid
from forgesight_core import current_context, new_run_id, new_span_id
from forgesight_core.context import (
    TelemetryContext,
    reset_current_context,
    set_current_context,
)


def test_new_run_id_is_a_ulid() -> None:
    assert is_valid_ulid(new_run_id())


def test_new_span_id_is_16_hex() -> None:
    span = new_span_id()
    assert len(span) == 16
    int(span, 16)  # parses as hex


def test_current_context_default_is_none() -> None:
    assert current_context() is None


def test_set_and_reset_context() -> None:
    ctx = TelemetryContext(run_id="r", trace_id="t")
    token = set_current_context(ctx)
    assert current_context() is ctx
    reset_current_context(token)
    assert current_context() is None


def test_child_copies_metadata_and_sets_span() -> None:
    parent = TelemetryContext(run_id="r", trace_id="t", metadata={"team": "platform"})
    child = parent.child(current_span_id="span-1")
    child.metadata["extra"] = 1
    assert child.current_span_id == "span-1"
    assert child.run_id == "r"
    assert "extra" not in parent.metadata  # copy, not shared
    assert parent.metadata == {"team": "platform"}
