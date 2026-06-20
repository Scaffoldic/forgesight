"""The hash chain — canonical serialization + ``prev_hash``/``hash`` rule.

Each event's hash folds in its predecessor's hash, so altering, deleting, or reordering
any event breaks every hash after it (a Merkle-style linked list). The canonical
serialization is specified down to key order / separators / encoding so a log written on
one host verifies on another.
"""

from __future__ import annotations

import hashlib
import json

from .model import AuditEvent

DEFAULT_ALGORITHM = "sha256"
SUPPORTED_ALGORITHMS: frozenset[str] = frozenset({"sha256", "sha512"})


def canonical_bytes(event: AuditEvent) -> bytes:
    """The byte-exact serialization hashed for the chain. Excludes ``prev_hash``/``hash``
    (``prev_hash`` is folded in separately by :func:`compute_hash`)."""
    content = {
        "schema_version": event.schema_version,
        "kind": str(event.kind),
        "seq": event.seq,
        "timestamp_unix_nanos": event.timestamp_unix_nanos,
        "run_id": event.run_id,
        "trace_id": event.trace_id,
        "principal": event.principal,
        "version": event.version,
        "owner": event.owner,
        "team": event.team,
        "cost_usd": event.cost_usd,
        "status": event.status,
        "attributes": dict(sorted(event.attributes.items())),
    }
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def compute_hash(
    event: AuditEvent, prev_hash: str | None, algorithm: str = DEFAULT_ALGORITHM
) -> str:
    """``H( canonical(event) || prev_hash )`` — the chain hash for one event."""
    digest = hashlib.new(algorithm)
    digest.update(canonical_bytes(event))
    if prev_hash is not None:
        digest.update(b"\x00")  # domain separator between content and the prior hash
        digest.update(prev_hash.encode("utf-8"))
    return digest.hexdigest()
