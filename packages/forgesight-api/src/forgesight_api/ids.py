"""Identifier contracts: ULID run-ids and W3C trace-ids.

`run_id` is a [ULID](https://github.com/ulid/spec) — 128 bits, lexicographically
sortable by creation time, Crockford base32, 26 characters. Sortability means
"most recent runs" is an index range scan in every backend, not a timestamp join.

`trace_id` is a W3C 16-byte (32-hex-char) trace id, distinct from `run_id`: one
trace can carry nested runs that share a `trace_id` but each have their own ULID
`run_id`.

This module is pure Python (stdlib only) — it generates and validates ids, which
keeps the contract self-contained without pulling a dependency into the leaf
package. It performs no network or disk I/O.
"""

from __future__ import annotations

import os
import time

# Crockford base32 alphabet (excludes I, L, O, U to avoid ambiguity).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_SET = frozenset(_CROCKFORD)

_ULID_LEN = 26
_TRACE_ID_LEN = 32
_MAX_48_BIT = (1 << 48) - 1


def new_ulid(timestamp_ms: int | None = None) -> str:
    """Generate a 26-character Crockford-base32 ULID.

    The high 48 bits are a millisecond timestamp (so ids sort by creation time);
    the low 80 bits are cryptographically random. Pass ``timestamp_ms`` for
    deterministic tests; otherwise the current wall-clock time is used.
    """
    ts = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    if not 0 <= ts <= _MAX_48_BIT:
        raise ValueError(f"timestamp_ms out of 48-bit range: {ts}")
    randomness = int.from_bytes(os.urandom(10), "big")  # 80 bits
    return _encode_u128((ts << 80) | randomness)


def is_valid_ulid(value: str) -> bool:
    """Return True if ``value`` is a syntactically valid 26-char ULID."""
    if len(value) != _ULID_LEN:
        return False
    # The first character encodes only the top bits of the 128-bit value, so it
    # must be 0-7 (a larger value would overflow 128 bits).
    if value[0] not in "01234567":
        return False
    return all(ch in _CROCKFORD_SET for ch in value)


def new_trace_id() -> str:
    """Generate a W3C trace id: 16 random bytes as 32 lowercase hex chars."""
    return os.urandom(16).hex()


def is_valid_trace_id(value: str) -> bool:
    """Return True if ``value`` is a valid, non-zero 32-hex-char W3C trace id."""
    if len(value) != _TRACE_ID_LEN:
        return False
    try:
        return int(value, 16) != 0
    except ValueError:
        return False


def _encode_u128(value: int) -> str:
    """Encode a 128-bit unsigned integer as 26 Crockford-base32 characters."""
    chars = ["0"] * _ULID_LEN
    for i in range(_ULID_LEN - 1, -1, -1):
        chars[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)
