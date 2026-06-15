"""ForgeSight MCP instrumentation — client + server spans, ``mcp.*`` conventions, W3C propagation.

Public surface (stable for 0.2): :func:`instrument_mcp_client`, :func:`instrument_mcp_server`,
:func:`uninstrument_mcp_client`, :func:`uninstrument_mcp_server`. :func:`install` is the
``forgesight.integrations`` entry point that auto-instruments new sessions/servers when
``auto_instrument`` is on.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from .client import in_mcp_tool_call, instrument_mcp_client, uninstrument_mcp_client
from .mapping import KNOWN_METHODS
from .server import instrument_mcp_server, uninstrument_mcp_server

_log = logging.getLogger("forgesight.mcp")
__version__ = "0.1.0"

# class-patch registry for install()/uninstall(): key → (class, original __init__)
_PATCHED: dict[str, tuple[type, Any]] = {}
_CONTENT_LOGGED = False


def install(
    config: dict[str, object] | None = None,
    *,
    _client_cls: type | None = None,
    _server_cls: type | None = None,
) -> bool:
    """Auto-instrument MCP: patch new ``ClientSession`` / ``Server`` instances at creation.

    The ``forgesight.integrations`` entry point. Honours the ``integrations.mcp`` config
    block (``enabled`` / ``auto_instrument`` / ``methods`` / ``capture_content``). Returns
    True if patching was applied. Idempotent.
    """
    cfg = dict(config or {})
    if not cfg.get("enabled", True) or not cfg.get("auto_instrument", True):
        return False
    if _PATCHED:
        return True  # already installed
    capture = cfg.get("capture_content")
    capture_opt = bool(capture) if capture is not None else None
    methods = cfg.get("methods")
    methods_opt = (
        list(methods) if isinstance(methods, Sequence) and not isinstance(methods, str) else None
    )
    if capture_opt:
        _log_content_capture()

    client_cls = _client_cls or _import("mcp", "ClientSession")
    server_cls = _server_cls or _import("mcp.server", "Server")
    if client_cls is not None:
        _patch_init("client", client_cls, instrument_mcp_client, capture_opt, methods_opt)
    if server_cls is not None:
        _patch_init("server", server_cls, instrument_mcp_server, capture_opt, methods_opt)
    return bool(_PATCHED)


def uninstall() -> None:
    """Undo :func:`install` — restore the original class constructors."""
    for cls, original in _PATCHED.values():
        setattr(cls, "__init__", original)  # noqa: B010 - direct __init__ assign trips mypy
    _PATCHED.clear()


def _patch_init(
    key: str,
    cls: type,
    instrument: Any,
    capture: bool | None,
    methods: list[str] | None,
) -> None:
    original_init = getattr(cls, "__init__")  # noqa: B009 - keep mypy off __init__ specialcasing

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        try:
            instrument(self, capture_content=capture, methods=methods)
        except Exception:  # pragma: no cover - defensive; auto-instrument must never break init
            _log.warning("forgesight-mcp: auto-instrument failed for %s", cls.__name__)

    setattr(cls, "__init__", patched_init)  # noqa: B010 - direct __init__ assign trips mypy
    _PATCHED[key] = (cls, original_init)


def _import(module: str, attr: str) -> type | None:
    import importlib

    try:
        return getattr(importlib.import_module(module), attr)  # type: ignore[no-any-return]
    except Exception:  # pragma: no cover - mcp is a declared dep; absent only in odd installs
        _log.warning(
            "forgesight-mcp: could not import %s.%s; auto-instrument skipped", module, attr
        )
        return None


def _log_content_capture() -> None:
    global _CONTENT_LOGGED
    if not _CONTENT_LOGGED:
        _log.info("forgesight-mcp: MCP content capture is ON (tools/call args + results)")
        _CONTENT_LOGGED = True


__all__ = [
    "KNOWN_METHODS",
    "__version__",
    "in_mcp_tool_call",
    "install",
    "instrument_mcp_client",
    "instrument_mcp_server",
    "uninstall",
    "uninstrument_mcp_client",
    "uninstrument_mcp_server",
]
