"""ForgeSight contracts — the locked telemetry domain model + SPIs.

This is the leaf of the dependency graph: it imports nothing from ``forgesight_core``
or any integration package, and depends on no backend or model-provider SDK.

The concrete contracts land in feat-001; this module currently exposes the version.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
