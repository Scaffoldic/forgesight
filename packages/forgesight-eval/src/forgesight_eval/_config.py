"""Module config for eval emission: enabled switch, emit_as, explanation gate, score schema.

``enabled`` defaults **false** — installing the package emits nothing until switched on
(P2). ``install`` (the ``forgesight.modules`` entry point) sets it from ``modules.eval.*``;
otherwise it lazy-loads from the SDK's layered settings + ``FORGESIGHT_EVAL_*`` env on first
use. The optional ``score_schema`` validates score/label at the call site (fail-fast).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from forgesight_core.config import load_settings


@dataclass
class EvalConfig:
    enabled: bool = False
    emit_as: str = "span"  # "span" | "event"
    capture_explanation: bool = True
    score_schema: dict[str, dict[str, Any]] = field(default_factory=dict)


_config: EvalConfig | None = None


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _build(block: Mapping[str, Any]) -> EvalConfig:
    emit_as = str(os.environ.get("FORGESIGHT_EVAL_EMIT_AS") or block.get("emit_as") or "span")
    if emit_as not in ("span", "event"):
        raise ValueError(f"modules.eval.emit_as must be span|event, got {emit_as!r}")
    enabled_env = os.environ.get("FORGESIGHT_EVAL_ENABLED")
    enabled = _as_bool(enabled_env, _as_bool(block.get("enabled"), False))
    schema = block.get("score_schema")
    return EvalConfig(
        enabled=enabled,
        emit_as=emit_as,
        capture_explanation=_as_bool(block.get("capture_explanation"), True),
        score_schema=dict(schema) if isinstance(schema, Mapping) else {},
    )


def install(config: Mapping[str, Any] | None = None) -> bool:
    """The ``forgesight.modules`` entry point: set the eval module config. Returns ``enabled``."""
    global _config
    _config = _build(config or {})
    return _config.enabled


def get_config() -> EvalConfig:
    """Return the active config, lazily loading from ``modules.eval`` settings if unset."""
    global _config
    if _config is None:
        block = load_settings().get("modules")
        eval_block = block.get("eval") if isinstance(block, Mapping) else None
        _config = _build(eval_block if isinstance(eval_block, Mapping) else {})
    return _config


def reset_config() -> None:
    """Clear the cached config (tests / re-configure)."""
    global _config
    _config = None
