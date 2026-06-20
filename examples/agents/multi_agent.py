"""Multi-agent pipeline — a supervisor delegating to sub-agents, on Bedrock.

Shows nested *runs*: a ``supervisor`` agent_run that opens child ``researcher`` and
``writer`` agent_runs (each a real Bedrock call), so the trace is a tree of agents and the
cost rolls up across all three. Run: ``uv run --no-sync python -m examples.agents.multi_agent``.
"""

from __future__ import annotations

from typing import Any

from forgesight import telemetry

from . import _demo


def run(client: Any) -> None:
    """The agent body — reused by ``main`` and by ``demo_all`` under a shared runtime."""
    topic = "why vendor-neutral agent telemetry matters"
    print("→ Multi-agent pipeline")
    with telemetry.agent_run("supervisor", version="1.0.0", metadata=_demo.run_metadata()) as sup:
        # delegate to the researcher sub-agent (a nested run)
        with (
            sup.step("delegate-research"),
            telemetry.agent_run("researcher", version="1.0.0") as researcher,
            researcher.llm_call("aws.bedrock", _demo.MODEL) as call,
        ):
            notes, usage = _demo.chat(
                client, f"List 3 concise bullet points about: {topic}", max_tokens=160
            )
            _demo.record(call, usage)

        # delegate to the writer sub-agent (another nested run), using the research
        with (
            sup.step("delegate-writing"),
            telemetry.agent_run("writer", version="1.0.0") as writer,
            writer.llm_call("aws.bedrock", _demo.MODEL) as call,
        ):
            draft, usage = _demo.chat(
                client,
                f"Research notes:\n{notes}\n\nWrite a one-sentence summary.",
                system="You are a crisp technical writer.",
                max_tokens=120,
            )
            _demo.record(call, usage)
    print("  summary:", draft)


def main() -> None:
    sink = _demo.configure("multi-agent", "/tmp/forgesight-multi-audit.jsonl")
    run(_demo.bedrock_client())
    _demo.report("multi-agent", sink)


if __name__ == "__main__":
    main()
