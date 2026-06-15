"""Shared helper to read the ``governance:`` block from the SDK's layered settings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from forgesight_core.config import load_settings


def governance_settings(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the ``governance`` config block (file → env layered), or ``{}`` if absent."""
    resolved = settings if settings is not None else load_settings()
    block = resolved.get("governance")
    return dict(block) if isinstance(block, Mapping) else {}
