"""ForgeSight governance — budgets, policy, and a kill-switch on the interceptor chain."""

from __future__ import annotations

from forgesight_api import GovernanceSignal

from .budget import (
    BudgetCap,
    BudgetExceeded,
    BudgetInterceptor,
    BudgetScope,
    ProjectionConfig,
)
from .kill_switch import (
    EnvKillSwitchSource,
    FileKillSwitchSource,
    KillSwitch,
    KillSwitchEngaged,
    KillSwitchSource,
)
from .policy import PolicyAction, PolicyDenied, PolicyInterceptor, PolicyRule

__version__ = "0.1.0"

__all__ = [
    "BudgetCap",
    "BudgetExceeded",
    "BudgetInterceptor",
    "BudgetScope",
    "EnvKillSwitchSource",
    "FileKillSwitchSource",
    "GovernanceSignal",
    "KillSwitch",
    "KillSwitchEngaged",
    "KillSwitchSource",
    "PolicyAction",
    "PolicyDenied",
    "PolicyInterceptor",
    "PolicyRule",
    "ProjectionConfig",
    "__version__",
]
