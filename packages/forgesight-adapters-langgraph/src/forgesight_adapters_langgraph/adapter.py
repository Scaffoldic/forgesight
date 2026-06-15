"""``LangGraphAdapter`` — subscribe ForgeSight to LangChain/LangGraph callbacks globally.

``instrument()`` registers the handler as an *inheritable* LangChain callback (via
``register_configure_hook``), so every graph/chain run picks it up with **no change to the
user's graph**. ``uninstrument()`` clears it. Idempotent via :class:`~forgesight_core.BaseAdapter`.
"""

from __future__ import annotations

import contextvars
from typing import Any

from forgesight_core import BaseAdapter

from .handler import ForgeSightLangChainHandler


class LangGraphAdapter(BaseAdapter):
    """Auto-instrument LangGraph / LangChain by registering a global callback handler."""

    name = "langgraph"

    def __init__(self) -> None:
        super().__init__()
        self._handler = ForgeSightLangChainHandler()
        self._var: contextvars.ContextVar[Any] = contextvars.ContextVar(
            "forgesight_langgraph_handler", default=None
        )
        self._hook_registered = False
        self._token: contextvars.Token[Any] | None = None

    @property
    def handler(self) -> ForgeSightLangChainHandler:
        """The callback handler — also usable explicitly via ``callbacks=[adapter.handler]``."""
        return self._handler

    def _subscribe(self) -> None:
        from langchain_core.tracers.context import register_configure_hook

        if not self._hook_registered:
            # inheritable=True ⇒ LangChain adds the contextvar's handler to every run
            register_configure_hook(self._var, True)
            self._hook_registered = True
        self._token = self._var.set(self._handler)

    def _unsubscribe(self) -> None:
        if self._token is not None:
            self._var.reset(self._token)
            self._token = None
