"""Configuration: the named-integration registry + layered file/env loading.

Resolves names (``"console"``, ``"otel"``, ``"langfuse"``, …) to implementations via
an in-process registry plus the ``forgesight.<group>`` entry points (the plug-and-play
seam, P2). A name that resolves to nothing raises the matching ``*NotRegisteredError``
at ``configure()`` — fail-fast, never a silent mid-run drop (architecture §8).

Settings are layered file → env → kwargs (last wins); ``${VAR}`` / ``${VAR:-default}``
in the YAML file are interpolated from the environment. Implemented with dataclasses +
manual validation (no Pydantic) to keep the core dependency-light (P1).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from importlib import metadata
from typing import Any

import yaml

from forgesight_api import (
    EventListenerNotRegisteredError,
    ExporterNotRegisteredError,
    InterceptorNotRegisteredError,
    PricingProviderNotRegisteredError,
)

from .cost import TablePricingProvider
from .exporters import ConsoleExporter, InMemoryExporter
from .interceptors import ContentCaptureGate, PIIRedactionInterceptor

_ERRORS: dict[str, type[LookupError]] = {
    "exporters": ExporterNotRegisteredError,
    "interceptors": InterceptorNotRegisteredError,
    "listeners": EventListenerNotRegisteredError,
    "pricing": PricingProviderNotRegisteredError,
}
_REGISTRY: dict[str, dict[str, Callable[..., Any]]] = {group: {} for group in _ERRORS}

# Built-in implementations resolve by name with no privileged path.
_REGISTRY["exporters"]["console"] = ConsoleExporter
_REGISTRY["exporters"]["in-memory"] = InMemoryExporter
_REGISTRY["interceptors"]["content-gate"] = ContentCaptureGate
_REGISTRY["interceptors"]["pii-redaction"] = PIIRedactionInterceptor
_REGISTRY["pricing"]["default"] = TablePricingProvider.from_vendored

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def register(group: str, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: register an in-process factory under a group, resolvable by name."""
    if group not in _REGISTRY:
        raise ValueError(f"unknown registration group {group!r}; expected one of {list(_ERRORS)}")

    def decorator(factory: Callable[..., Any]) -> Callable[..., Any]:
        _REGISTRY[group][name] = factory
        return factory

    return decorator


def resolve(group: str, name: str, config: Mapping[str, object] | None = None) -> object:
    """Resolve ``name`` in ``group`` to an instance, or raise the group's error."""
    factory = _REGISTRY[group].get(name) or _load_entry_point(group, name)
    if factory is None:
        raise _ERRORS[group](
            f"No {group[:-1]} registered under name {name!r}. Expected an entry point in "
            f"group 'forgesight.{group}' (did you `pip install` the integration package?)."
        )
    return factory(**dict(config)) if config else factory()


def _load_entry_point(group: str, name: str) -> Callable[..., Any] | None:
    try:
        entry_points = metadata.entry_points(group=f"forgesight.{group}")
    except Exception:  # pragma: no cover - importlib metadata edge
        return None
    for entry_point in entry_points:
        if entry_point.name == name:
            loaded: Callable[..., Any] = entry_point.load()
            return loaded
    return None


def interpolate(value: object, env: Mapping[str, str]) -> object:
    """Substitute ``${VAR}`` / ``${VAR:-default}`` in strings, recursing into dicts/lists."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: _sub(m, env), value)
    if isinstance(value, dict):
        return {k: interpolate(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, env) for v in value]
    return value


def _sub(match: re.Match[str], env: Mapping[str, str]) -> str:
    var, default = match.group(1), match.group(2)
    if var in env:
        return env[var]
    if default is not None:
        return default
    raise ValueError(f"config references ${{{var}}} which is not set and has no default")


def load_settings(
    config_file: str | None = None, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Load the file layer (YAML + ``${ENV}``) then overlay ``FORGESIGHT_*`` env scalars."""
    env = os.environ if env is None else env
    settings: dict[str, Any] = {}
    path = config_file or env.get("FORGESIGHT_CONFIG")
    if path is None and os.path.exists("forgesight.yaml"):
        path = "forgesight.yaml"
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        loaded = interpolate(raw, env)
        if isinstance(loaded, dict):
            settings.update(loaded)
    _env_overlay(settings, env)
    return settings


def _env_overlay(settings: dict[str, Any], env: Mapping[str, str]) -> None:
    if "FORGESIGHT_SERVICE_NAME" in env:
        settings["service_name"] = env["FORGESIGHT_SERVICE_NAME"]
    if "FORGESIGHT_EXPORTERS" in env:
        settings["exporters"] = [
            s.strip() for s in env["FORGESIGHT_EXPORTERS"].split(",") if s.strip()
        ]
    if "FORGESIGHT_CAPTURE_CONTENT" in env:
        settings["capture_content"] = _as_bool(env["FORGESIGHT_CAPTURE_CONTENT"])
    if "FORGESIGHT_SAMPLE_RATE" in env:
        settings["sample_rate"] = float(env["FORGESIGHT_SAMPLE_RATE"])


def _as_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")
