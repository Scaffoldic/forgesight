"""Per-job run summary written to ``$GITHUB_STEP_SUMMARY`` on exit.

A :class:`SummaryCollector` (an ``EventListener``) tallies the runs the SDK saw in the
process — status, cost (summed from LLM calls), duration, tool-call count. On exit a markdown
block is appended to the Actions job summary so the UI shows "run: ok · cost $0.12 · 38s · 3
tool calls" with no author effort. The write is best-effort and never fails the job (P6).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence

from forgesight_api import EventType, LifecycleEvent, RunStatus

_log = logging.getLogger("forgesight.github")

DEFAULT_SUMMARY_METRICS: tuple[str, ...] = ("status", "cost_usd", "duration_ms", "n_tool_calls")
_RUN_DONE = frozenset({EventType.RUN_COMPLETED, EventType.RUN_FAILED})
_TOOL_DONE = frozenset({EventType.TOOL_EXECUTED, EventType.MCP_EXECUTED})


class SummaryCollector:
    """Tally runs / cost / tool-calls from lifecycle events (an ``EventListener``)."""

    def __init__(self) -> None:
        self.run_statuses: list[str] = []
        self.total_duration_ms: float = 0.0
        self.total_cost_usd: float = 0.0
        self.n_tool_calls: int = 0

    def on_event(self, event: LifecycleEvent) -> None:
        record = event.record
        if event.type in _RUN_DONE and record is not None:
            self.run_statuses.append(record.status.value)
            if record.duration_ms is not None:
                self.total_duration_ms += record.duration_ms
        elif event.type is EventType.LLM_EXECUTED and record is not None and record.llm is not None:
            if record.llm.cost_usd is not None:
                self.total_cost_usd += record.llm.cost_usd
        elif event.type in _TOOL_DONE:
            self.n_tool_calls += 1

    # --- rendering --------------------------------------------------------
    def status(self) -> str:
        if not self.run_statuses:
            return "no runs"
        if all(s == RunStatus.OK.value for s in self.run_statuses):
            return "ok"
        return "error"

    def values(self) -> dict[str, str]:
        return {
            "status": self.status(),
            "cost_usd": f"${self.total_cost_usd:.4f}",
            "duration_ms": f"{self.total_duration_ms:.0f}",
            "n_tool_calls": str(self.n_tool_calls),
            "runs": str(len(self.run_statuses)),
        }


def format_summary(collector: SummaryCollector, fields: Sequence[str]) -> str:
    """Render the markdown block appended to the job summary."""
    n = len(collector.run_statuses)
    values = collector.values()
    heading = f"### 🤖 ForgeSight agent run{'s' if n != 1 else ''}"
    lines = [heading]
    if n > 1:
        lines.append(f"- **runs**: {n}")
    for field in fields:
        if field in values:
            lines.append(f"- **{field}**: {values[field]}")
    return "\n".join(lines) + "\n"


def write_summary(
    collector: SummaryCollector, fields: Sequence[str], *, path: str | None = None
) -> bool:
    """Append the summary to ``$GITHUB_STEP_SUMMARY`` (or ``path``). Never raises (P6)."""
    target = path or os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return False
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(format_summary(collector, fields))
    except OSError:  # a failed summary write must never fail the job (P6)
        _log.warning("forgesight-github: could not write job summary to %s", target)
        return False
    return True
