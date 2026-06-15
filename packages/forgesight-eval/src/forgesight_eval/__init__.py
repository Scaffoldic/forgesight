"""ForgeSight eval — run-correlated eval scores + human feedback on the same pipeline."""

from __future__ import annotations

from ._config import EvalConfig, install
from .api import record_evaluation, record_feedback
from .model import EvaluationResult

__version__ = "0.1.0"

__all__ = [
    "EvalConfig",
    "EvaluationResult",
    "__version__",
    "install",
    "record_evaluation",
    "record_feedback",
]
