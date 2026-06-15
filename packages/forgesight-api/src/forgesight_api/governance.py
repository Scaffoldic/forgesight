"""The one sanctioned way an interceptor halts a run (feat-020).

Telemetry must never break a run (P6), so the pipeline swallows a normal interceptor
exception. A *governance* trip — an exceeded budget, a denied policy, a tripped kill-switch —
is the deliberate exception: it is a control decision, not a telemetry failure, so it
propagates to the caller and maps the run to a terminal status. The base lives in ``-api``
(the leaf) so the runtime can recognise it without importing the governance package; concrete
signals (``BudgetExceeded`` / ``PolicyDenied`` / ``KillSwitchEngaged``) subclass it there.
"""

from __future__ import annotations

from .model import RunStatus


class GovernanceSignal(Exception):
    """A deliberate interceptor halt. Carries the terminal :class:`RunStatus` for the run."""

    def __init__(self, message: str = "", *, run_status: RunStatus) -> None:
        super().__init__(message)
        self.run_status = run_status
