"""ForgeSight registry — declared agent ownership auto-stamped onto runs; chargeback rollups.

Wire the registry's stamping at bootstrap, then chargeback and the catalogue are group-bys
over the clean dimensions the SDK stamped:

```python
import forgesight
from forgesight_registry import Registry

reg = Registry.from_file("agents.yaml")
forgesight.configure(run_metadata_provider=reg.ownership_metadata)
```
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .model import AgentEntry, Lifecycle
from .registry import Registry, RegistryUnmatched
from .rollup import AgentCatalogue, CatalogueEntry, ChargebackReport, ChargebackRow
from .source import FileSource, HttpSource, RegistrySource

__version__ = "0.1.0"

_installed: Registry | None = None


def install(config: Mapping[str, Any] | None = None) -> Registry:
    """The ``forgesight.modules`` entry point: build the registry from config and stash it.

    Wire it as the run-start provider with
    ``configure(run_metadata_provider=installed_registry().ownership_metadata)``.
    """
    global _installed
    _installed = Registry.from_config(config)
    return _installed


def installed_registry() -> Registry | None:
    """The registry built by :func:`install`, or ``None`` if not installed."""
    return _installed


def reset_for_tests() -> None:
    global _installed
    _installed = None


__all__ = [
    "AgentCatalogue",
    "AgentEntry",
    "CatalogueEntry",
    "ChargebackReport",
    "ChargebackRow",
    "FileSource",
    "HttpSource",
    "Lifecycle",
    "Registry",
    "RegistrySource",
    "RegistryUnmatched",
    "__version__",
    "install",
    "installed_registry",
    "reset_for_tests",
]
