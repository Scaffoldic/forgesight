"""Unit tests for ULID + W3C trace-id generation and validation."""

from __future__ import annotations

import pytest

from forgesight_api import is_valid_trace_id, is_valid_ulid, new_trace_id, new_ulid


def test_new_ulid_is_26_char_crockford() -> None:
    ulid = new_ulid()
    assert len(ulid) == 26
    assert is_valid_ulid(ulid)


def test_ulids_sort_by_timestamp() -> None:
    early = new_ulid(timestamp_ms=1)
    late = new_ulid(timestamp_ms=2)
    assert early < late


def test_ulids_are_unique_in_a_tight_loop() -> None:
    ulids = {new_ulid() for _ in range(2000)}
    assert len(ulids) == 2000


def test_new_ulid_rejects_out_of_range_timestamp() -> None:
    with pytest.raises(ValueError, match="48-bit"):
        new_ulid(timestamp_ms=1 << 48)
    with pytest.raises(ValueError, match="48-bit"):
        new_ulid(timestamp_ms=-1)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("01J9Z3K7P8QF2R5V6W7X8Y9Z0A", True),
        ("01j9z3k7p8qf2r5v6w7x8y9z0a", False),  # lowercase not in canonical set
        ("01J9Z3K7P8QF2R5V6W7X8Y9Z0", False),  # 25 chars
        ("81J9Z3K7P8QF2R5V6W7X8Y9Z0A", False),  # first char > 7 overflows 128 bits
        ("01J9Z3K7P8QF2R5V6W7X8Y9Z0I", False),  # 'I' excluded from Crockford
        ("", False),
    ],
)
def test_is_valid_ulid(value: str, valid: bool) -> None:
    assert is_valid_ulid(value) is valid


def test_new_trace_id_is_32_hex() -> None:
    tid = new_trace_id()
    assert len(tid) == 32
    assert is_valid_trace_id(tid)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("4bf92f3577b34da6a3ce929d0e0e4736", True),
        ("00000000000000000000000000000000", False),  # all-zero is invalid
        ("4bf92f3577b34da6a3ce929d0e0e47", False),  # 30 chars
        ("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", False),  # not hex
    ],
)
def test_is_valid_trace_id(value: str, valid: bool) -> None:
    assert is_valid_trace_id(value) is valid
