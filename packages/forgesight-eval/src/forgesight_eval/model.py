"""``EvaluationResult`` — the run-correlated quality signal (feat-021).

The same value type for an automated eval (LLM-as-judge / Ragas / DeepEval) and a post-hoc
human thumbs-up/down. ``realtime`` distinguishes "attached during the run" from "arrived
later by ``run_id``"; ``source`` distinguishes ``auto`` from ``human``. At least one of
``score`` / ``label`` must be set (enforced at the call site). Experimental within 0.x.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

_EMPTY: Mapping[str, object] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    name: str
    run_id: str
    score: float | None = None
    label: str | None = None
    explanation: str | None = None
    evaluator: str | None = None
    source: str = "auto"  # "auto" (eval) | "human" (feedback)
    realtime: bool = True  # True if attached during the run, else post-hoc
    metadata: Mapping[str, object] = field(default_factory=lambda: _EMPTY)
