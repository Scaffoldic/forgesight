"""ForgeSight — the batteries-included facade.

``import forgesight`` gives you ``configure()``, the ``telemetry`` instrumentation
facade, and the ``@instrument`` decorator. Backends are added by installing their
package (``forgesight-otel`` etc.) — never a code change here.
"""

from __future__ import annotations

from forgesight_core import (
    ConsoleExporter,
    InMemoryExporter,
    Telemetry,
    configure,
    current_run_scope,
    get_runtime,
    instrument,
    register,
    telemetry,
)

__version__ = "0.1.0"


def current_run() -> object | None:
    """The active run scope, or ``None`` outside any run (alias of ``telemetry.current_run``)."""
    return current_run_scope()


__all__ = [
    "ConsoleExporter",
    "InMemoryExporter",
    "Telemetry",
    "__version__",
    "configure",
    "current_run",
    "get_runtime",
    "instrument",
    "register",
    "telemetry",
]
