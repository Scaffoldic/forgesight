"""Adapter base + scope-bridge shared by every framework adapter (feat-019)."""

from __future__ import annotations

from .base import BaseAdapter
from .bridge import ScopeBridge
from .guard import in_tool_call, tool_call_active

__all__ = ["BaseAdapter", "ScopeBridge", "in_tool_call", "tool_call_active"]
