"""Default resolution for the middleware: explicit kwarg → env → installed config → default.

``install`` (the ``forgesight.integrations`` entry point) stashes the ``integrations.fastapi``
config block so the middleware can read defaults from ``forgesight.yaml`` even though ASGI
middleware must be added explicitly in app code (it can't be auto-injected).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Any

_log = logging.getLogger("forgesight.fastapi")

SPAN_KINDS = ("agent_run", "workflow_run")
DEFAULT_SPAN_KIND = "agent_run"
DEFAULT_RUN_ID_HEADER = "x-forgesight-run-id"
DEFAULT_EXCLUDE_PATHS: tuple[str, ...] = (
    "/health",
    "/healthz",
    "/metrics",
    "/docs",
    "/openapi.json",
)

_INSTALLED: dict[str, Any] = {}
_CONTENT_LOGGED = False


def install(config: dict[str, Any] | None = None) -> bool:
    """Stash the ``integrations.fastapi`` config block as middleware defaults. Idempotent."""
    cfg = dict(config or {})
    if not cfg.get("enabled", True):
        _INSTALLED.clear()
        return False
    _INSTALLED.clear()
    _INSTALLED.update(cfg)
    if cfg.get("capture_content"):
        log_content_capture()
    return True


def log_content_capture() -> None:
    """INFO-once so request/response body capture is never silently on (P7)."""
    global _CONTENT_LOGGED
    if not _CONTENT_LOGGED:
        _log.info("forgesight-fastapi: HTTP body capture is ON (request/response bodies)")
        _CONTENT_LOGGED = True


def resolve_span_kind(value: str | None) -> str:
    resolved = (
        value or os.environ.get("FORGESIGHT_FASTAPI_SPAN_KIND") or _INSTALLED.get("span_kind")
    )
    resolved = str(resolved) if resolved else DEFAULT_SPAN_KIND
    if resolved not in SPAN_KINDS:
        raise ValueError(f"span_kind must be one of {SPAN_KINDS}, got {resolved!r}")
    return resolved


def resolve_exclude_paths(value: Sequence[str] | None) -> tuple[str, ...]:
    if value is not None:
        return tuple(str(p) for p in value)
    env = os.environ.get("FORGESIGHT_FASTAPI_EXCLUDE")
    if env:
        return tuple(p.strip() for p in env.split(",") if p.strip())
    installed = _INSTALLED.get("exclude_paths")
    if installed:
        return tuple(str(p) for p in installed)
    return DEFAULT_EXCLUDE_PATHS


def resolve_include_routes(value: Sequence[str] | None) -> tuple[str, ...] | None:
    if value is not None:
        return tuple(str(p) for p in value)
    installed = _INSTALLED.get("include_routes")
    return tuple(str(p) for p in installed) if installed else None


def resolve_capture_content(value: bool | None) -> bool | None:
    if value is not None:
        return value
    env = os.environ.get("FORGESIGHT_FASTAPI_CAPTURE_CONTENT")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    if "capture_content" in _INSTALLED:
        return bool(_INSTALLED["capture_content"])
    return None  # inherit the global gate


def resolve_run_id_header(value: str | None) -> str:
    return (
        value
        or os.environ.get("FORGESIGHT_FASTAPI_RUN_ID_HEADER")
        or str(_INSTALLED.get("run_id_header") or DEFAULT_RUN_ID_HEADER)
    )
