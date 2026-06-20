"""ReAct tool agent — think → act → observe across iterations, on Bedrock.

Shows nested run → step → llm_call/tool_call spans, multiple LLM calls, tool calls, and
per-run cost accumulation. Run: ``uv run --no-sync python -m examples.agents.react_agent``.
"""

from __future__ import annotations

from typing import Any

from forgesight import telemetry

from . import _demo


def calculator(a: int, b: int) -> int:
    return a * b


def search(query: str) -> str:
    # a stand-in "tool": a fixed knowledge snippet (no external call needed for the demo).
    return f"[search:{query}] ForgeSight is a vendor-neutral, OpenTelemetry-first telemetry SDK."


def run(client: Any) -> None:
    """The agent body — reused by ``main`` and by ``demo_all`` under a shared runtime."""
    question = "What is 21 times 2, and in one line, what does ForgeSight do?"
    print("→ ReAct agent")
    with telemetry.agent_run(
        "react-agent", version="1.0.0", metadata=_demo.run_metadata()
    ) as agent:
        with agent.step("iteration-0"):
            with agent.llm_call("aws.bedrock", _demo.MODEL) as call:
                _, usage = _demo.chat(
                    client,
                    f"You can use a calculator and a search tool. Plan how to answer: {question}",
                    max_tokens=120,
                )
                _demo.record(call, usage)
            with agent.tool_call("calculator"):
                calc = calculator(21, 2)
            with agent.tool_call("search"):
                docs = search("ForgeSight")
        with agent.step("iteration-1"), agent.llm_call("aws.bedrock", _demo.MODEL) as call:
            answer, usage = _demo.chat(
                client,
                f"Question: {question}\nCalculator: {calc}\nSearch: {docs}\n"
                "Answer in one sentence.",
                max_tokens=120,
            )
            _demo.record(call, usage)
    print("  answer:", answer)


def main() -> None:
    sink = _demo.configure("react-agent", "/tmp/forgesight-react-audit.jsonl")
    run(_demo.bedrock_client())
    _demo.report("react-agent", sink)


if __name__ == "__main__":
    main()
