"""An AgentForge agent, instrumented with ForgeSight — runnable fully offline.

This follows AgentForge's "Build your own agent" guide (a `@tool` + `Agent(...)` + a
scripted offline `FakeLLMClient`, so it runs with no API key and no network), then records
the run into ForgeSight and validates the telemetry end to end against an in-memory exporter.

Run (from an env that has both packages — see README.md):

    python agent.py
"""

from __future__ import annotations

import asyncio

# --- AgentForge (the framework that runs the agent) -------------------------------------
from agentforge import Agent, tool
from agentforge._testing import FakeLLMClient
from agentforge_core.values.messages import LLMResponse, TokenUsage, ToolCall
from forgesight_bridge import instrument_agentforge_run

# --- ForgeSight (the telemetry SDK we are integrating) ----------------------------------
import forgesight
from forgesight import InMemoryExporter
from forgesight_api import Kind, RunStatus
from forgesight_core.exporters import ConsoleExporter
from forgesight_core.metrics import MetricConfig

ORDERS = {"1042": {"id": "1042", "status": "shipped", "eta": "2026-06-18"}}


@tool
def lookup_order(order_id: str) -> dict:
    """Fetch an order record by id."""
    return ORDERS.get(order_id, {"id": order_id, "status": "unknown"})


def _scripted_model() -> FakeLLMClient:
    """A two-turn offline model: call the tool, then answer — no key, no network."""
    return FakeLLMClient(
        responses=[
            LLMResponse(
                content="I should look up the order.",
                stop_reason="tool_use",
                tool_calls=(
                    ToolCall(id="t1", name="lookup_order", arguments={"order_id": "1042"}),
                ),
                usage=TokenUsage(input_tokens=42, output_tokens=12),
                cost_usd=0.0021,
                model="fake",
                provider="agentforge",
            ),
            LLMResponse(
                content="Order 1042 has shipped; ETA 2026-06-18.",
                stop_reason="end_turn",
                usage=TokenUsage(input_tokens=58, output_tokens=18),
                cost_usd=0.0034,
                model="fake",
                provider="agentforge",
            ),
        ]
    )


async def main() -> None:
    # 1. Configure ForgeSight once. InMemory lets us assert; Console shows the trace.
    exporter = InMemoryExporter()
    forgesight.configure(
        service_name="order-agent",
        exporters=[exporter, ConsoleExporter()],
        sync_export=True,
        metrics=MetricConfig(enabled=False),  # this demo asserts records, not OTel metrics
    )

    # 2. Run the AgentForge agent — unchanged from the framework's guide, offline.
    async with Agent(model=_scripted_model(), tools=[lookup_order], strategy="react") as agent:
        result = await agent.run("What's the status of order 1042?")

    print("\n=== AgentForge result ===")
    print("output:", result.output)
    print(
        f"[agentforge run_id={result.run_id} cost=${result.cost_usd:.4f} "
        f"steps={len(result.steps)} finish={result.finish_reason}]"
    )

    # 3. Bridge the result into ForgeSight (the integration point).
    fs_run_id = instrument_agentforge_run(
        result,
        agent_name="order-agent",
        version="1.0.0",
        metadata={"team": "growth", "environment": "demo"},
    )

    # 4. Validate the telemetry end to end.
    records = exporter.records
    runs = [r for r in records if r.kind is Kind.AGENT]
    llms = [r for r in records if r.kind is Kind.LLM]
    tools = [r for r in records if r.kind is Kind.TOOL]
    steps = [r for r in records if r.kind is Kind.STEP]

    n_iterations = len({s.iteration for s in result.steps})
    n_think = sum(1 for s in result.steps if s.kind == "think")
    n_act = sum(1 for s in result.steps if s.kind == "act")

    assert len(runs) == 1, f"expected 1 agent run, got {len(runs)}"
    run = runs[0]
    assert run.status is RunStatus.OK
    assert run.attributes["team"] == "growth"
    assert run.attributes["agentforge.run_id"] == result.run_id  # correlated to AgentForge
    assert len(steps) == n_iterations, "one ForgeSight step per AgentForge iteration"
    assert len(llms) == n_think, "one llm_call per think turn"
    assert len(tools) == n_act, f"one tool span per act step, got {len(tools)}"
    assert any(t.tool and t.tool.name == "lookup_order" for t in tools)
    # everything shares one trace and the cost adds up
    assert {r.trace_id for r in records} == {run.trace_id}, "single trace tree"
    total_cost = sum(r.llm.cost_usd or 0.0 for r in llms if r.llm)
    assert abs(total_cost - result.cost_usd) < 1e-9, "ForgeSight cost == AgentForge cost"

    print("\n=== ForgeSight telemetry (validated) ===")
    print(f"forgesight run_id : {fs_run_id}")
    print(f"trace_id          : {run.trace_id}")
    print(
        f"records           : {len(records)}  "
        f"(agent={len(runs)} step={len(steps)} llm={len(llms)} tool={len(tools)})"
    )
    print(f"tool spans        : {[t.tool.name for t in tools if t.tool]}")
    print(f"cost (ForgeSight) : ${total_cost:.4f}  == AgentForge ${result.cost_usd:.4f}")
    print(
        "\n✅ end-to-end OK — an AgentForge agent's run, steps, LLM calls, tool call, and "
        "cost were captured by ForgeSight and exported."
    )


if __name__ == "__main__":
    asyncio.run(main())
