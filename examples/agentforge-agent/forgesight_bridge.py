"""A tiny AgentForge → ForgeSight bridge.

AgentForge runs the agent loop and returns a ``RunResult`` carrying the full step trace
(each step's tokens, cost, and optional tool call). ForgeSight owns the telemetry domain
model and exporters. This module maps one onto the other: it replays a ``RunResult`` into a
ForgeSight ``agent_run`` trace — each ReAct iteration becomes a ``step``, each step's
reasoning turn an ``llm_call`` (with the step's tokens + cost), and any tool the model
invoked a nested ``tool_call``.

It is deliberately small: a hand-rolled stand-in for the first-party
``forgesight-adapters-agentforge`` adapter (feat-019, deferred). It shows the integration
shape — *AgentForge does the work; ForgeSight records it, correlated by run id, and exports
it anywhere* — with no change to the agent's code.
"""

from __future__ import annotations

from itertools import groupby
from typing import Any

from forgesight import telemetry


def instrument_agentforge_run(
    result: Any,
    *,
    agent_name: str,
    version: str | None = None,
    provider: str = "agentforge",
    model: str = "fake",
    metadata: dict[str, str] | None = None,
) -> str:
    """Replay an AgentForge ``RunResult`` into a ForgeSight trace; return the ForgeSight run id.

    The trace shape is ``agent_run → step(iteration-N) → [llm_call, tool_call]`` — the same
    domain model any other ForgeSight-instrumented agent produces, so an AgentForge agent is
    comparable to a LangGraph or hand-written one in the same backend.

    AgentForge's ReAct loop emits one step per phase: ``think`` (an LLM reasoning turn,
    carrying tokens + cost), ``act`` (the tool invocation), and ``observe`` (the tool
    result — folded into the act span). We group those by iteration.
    """
    md = dict(metadata or {})
    md["agentforge.run_id"] = str(result.run_id)  # correlate back to AgentForge's run id
    md["agentforge.finish_reason"] = str(result.finish_reason)

    with telemetry.agent_run(agent_name, version=version, metadata=md) as run:
        for iteration, group in groupby(result.steps, key=lambda s: s.iteration):
            with run.step(f"iteration-{iteration}"):
                for step in group:
                    if step.kind == "think":
                        with run.llm_call(provider, model) as call:
                            call.record_usage(input=step.tokens_in, output=step.tokens_out)
                            if step.cost_usd:
                                call.set_cost(step.cost_usd)  # AgentForge's cost wins (FR-9)
                    elif step.kind == "act" and step.tool_call is not None:
                        with run.tool_call(str(step.tool_call.name)):
                            pass  # the tool ran inside AgentForge; we record the span
                    # 'observe' is the tool's result — folded into the act span above
        return run.run_id
