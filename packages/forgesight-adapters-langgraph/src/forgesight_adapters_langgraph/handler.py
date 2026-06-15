"""The LangChain/LangGraph callback handler that translates callbacks → SDK scopes.

LangGraph runs on LangChain's callback system: a graph invocation is a root *chain*, each
node a nested chain, and LLM / tool calls fire their own start/end callbacks — every one
carrying a ``run_id`` (and ``parent_run_id``). This handler opens the matching SDK scope on
start (keyed by ``run_id``) and closes it on end, so the *unchanged* graph emits the SDK's
domain model. Nesting rides the SDK's contextvars via :class:`~forgesight_core.ScopeBridge`.

The translation is the valuable part and is fully unit-tested by driving these methods with
real ``run_id`` / ``LLMResult`` values — no running graph required.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler

from forgesight_core import (
    LLMScope,
    RunScope,
    ScopeBridge,
    StepScope,
    ToolScope,
    get_runtime,
    in_tool_call,
)


class ForgeSightLangChainHandler(BaseCallbackHandler):
    """Maps LangChain/LangGraph callbacks onto SDK instrumentation calls."""

    def __init__(self) -> None:
        self._bridge = ScopeBridge()

    # --- chains: graph (root) + nodes (nested) ---------------------------
    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        name = _chain_name(serialized, metadata)
        runtime = get_runtime()
        # the root chain (no parent) is the agent_run; nested chains are steps (nodes)
        scope: RunScope | StepScope = (
            RunScope(runtime, name=name) if parent_run_id is None else StepScope(runtime, name=name)
        )
        self._bridge.enter_keyed(run_id, scope)

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._bridge.exit_keyed(run_id)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._bridge.exit_keyed(run_id, error=error)

    # --- LLM calls --------------------------------------------------------
    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._open_llm(serialized, run_id, metadata)

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._open_llm(serialized, run_id, metadata)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        scope = self._bridge.get_keyed(run_id)
        if isinstance(scope, LLMScope):
            inp, out = _llm_usage(response)
            if inp or out:
                scope.record_usage(input=inp, output=out)
        self._bridge.exit_keyed(run_id)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._bridge.exit_keyed(run_id, error=error)

    # --- tool calls -------------------------------------------------------
    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if in_tool_call():
            return  # an inner span (MCP tools/call) already covers this — no double-instrument
        name = (serialized or {}).get("name") or "tool"
        self._bridge.enter_keyed(run_id, ToolScope(get_runtime(), name=str(name)))

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._bridge.exit_keyed(run_id)

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._bridge.exit_keyed(run_id, error=error)

    # --- internals --------------------------------------------------------
    def _open_llm(
        self, serialized: dict[str, Any], run_id: UUID, metadata: dict[str, Any] | None
    ) -> None:
        provider, model = _llm_provider_model(serialized, metadata)
        self._bridge.enter_keyed(run_id, LLMScope(get_runtime(), provider=provider, model=model))


def _chain_name(serialized: dict[str, Any] | None, metadata: dict[str, Any] | None) -> str:
    if metadata:
        node = metadata.get("langgraph_node")
        if node:
            return str(node)
    if serialized:
        name = serialized.get("name")
        if name:
            return str(name)
        ident = serialized.get("id")
        if isinstance(ident, list) and ident:
            return str(ident[-1])
    return "chain"


def _llm_provider_model(
    serialized: dict[str, Any] | None, metadata: dict[str, Any] | None
) -> tuple[str, str]:
    meta = metadata or {}
    provider = meta.get("ls_provider")
    model = meta.get("ls_model_name")
    if model is None and serialized:
        kwargs = serialized.get("kwargs") or {}
        model = kwargs.get("model") or kwargs.get("model_name")
    return str(provider or "unknown"), str(model or "unknown")


def _llm_usage(response: Any) -> tuple[int, int]:
    """Pull (input, output) tokens from an ``LLMResult``; (0, 0) if absent (cost stays null)."""
    output = getattr(response, "llm_output", None) or {}
    usage = output.get("token_usage") or output.get("usage") or {}
    inp = usage.get("prompt_tokens", usage.get("input_tokens"))
    out = usage.get("completion_tokens", usage.get("output_tokens"))
    if inp is None and out is None:
        inp, out = _usage_from_generations(response)
    return int(inp or 0), int(out or 0)


def _usage_from_generations(response: Any) -> tuple[int | None, int | None]:
    for batch in getattr(response, "generations", []) or []:
        for generation in batch:
            message = getattr(generation, "message", None)
            meta = getattr(message, "usage_metadata", None)
            if meta:
                return meta.get("input_tokens"), meta.get("output_tokens")
    return None, None
