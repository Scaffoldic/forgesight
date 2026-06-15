"""``KillSwitch`` — cut off one scope's spend in seconds, no redeploy (feat-020).

Checks each LLM call's scope keys (run / team / repo / environment) against a hot-reloadable
:class:`KillSwitchSource`. A tripped key raises ``KillSwitchEngaged`` →
``RunStatus.BUDGET_EXCEEDED`` so that scope's runs halt while every other agent keeps running.
The env source reads ``FORGESIGHT_KILL_<SCOPE>_<KEY>`` per call (instant); the file source
re-reads a trip list on a TTL. No vendor dependency (P1); O(1) membership, no I/O on the env
path.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from forgesight_api import GovernanceSignal, Record, RunStatus

from ._settings import governance_settings

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_TRUE = ("1", "true", "yes", "on")


def _norm(value: str) -> str:
    return _NON_ALNUM.sub("_", value.upper()).strip("_")


@runtime_checkable
class KillSwitchSource(Protocol):
    """A pollable trip source. ``is_tripped`` must be cheap (hot path)."""

    def is_tripped(self, scope: str, key: str) -> bool: ...


class EnvKillSwitchSource:
    """Reads ``FORGESIGHT_KILL_<SCOPE>_<KEY>=true`` per call — instant, no caching."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env

    def is_tripped(self, scope: str, key: str) -> bool:
        env = self._env if self._env is not None else os.environ
        name = f"FORGESIGHT_KILL_{_norm(scope)}_{_norm(key)}"
        return env.get(name, "").strip().lower() in _TRUE


class FileKillSwitchSource:
    """Reads a trip list (``scope:key`` per line) from a file, re-read on a TTL."""

    def __init__(
        self, path: str, *, poll_seconds: float = 5.0, clock: Any = time.monotonic
    ) -> None:
        self._path = path
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._tripped: frozenset[str] = frozenset()
        self._last_read: float | None = None

    def is_tripped(self, scope: str, key: str) -> bool:
        now = self._clock()
        if self._last_read is None or (now - self._last_read) >= self._poll_seconds:
            self._reload()
            self._last_read = now
        return f"{scope}:{key}" in self._tripped

    def _reload(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            self._tripped = frozenset()  # missing file ⇒ nothing tripped (fail-open per scope)
            return
        self._tripped = frozenset(
            line.strip() for line in lines if line.strip() and not line.startswith("#")
        )


class KillSwitchEngaged(GovernanceSignal):
    """Raised when a scope's kill-switch is tripped. Maps the run to ``BUDGET_EXCEEDED``."""

    def __init__(self, scope: str, key: str) -> None:
        super().__init__(
            f"kill-switch engaged: {scope}={key}", run_status=RunStatus.BUDGET_EXCEEDED
        )
        self.scope = scope
        self.key = key


class KillSwitch:
    """Veto an LLM call when its run / team / repo / environment is tripped."""

    def __init__(self, *, source: KillSwitchSource) -> None:
        self._source = source

    @classmethod
    def from_config(cls, settings: Mapping[str, Any] | None = None) -> KillSwitch:
        block = governance_settings(settings).get("kill_switch")
        block = block if isinstance(block, Mapping) else {}
        kind = str(block.get("source", "env"))
        if kind == "file":
            path = block.get("file_path")
            if not path:
                raise ValueError("kill_switch.source 'file' requires file_path")
            return cls(
                source=FileKillSwitchSource(
                    str(path), poll_seconds=float(block.get("poll_seconds", 5))
                )
            )
        return cls(source=EnvKillSwitchSource())

    # --- Interceptor SPI --------------------------------------------------
    def intercept(self, record: Record) -> Record | None:
        if record.llm is None:
            return record
        attrs = record.attributes
        for scope, key in (
            ("run", record.run_id),
            ("team", attrs.get("team")),
            ("repo", attrs.get("repo")),
            ("environment", attrs.get("environment")),
        ):
            if key and self._source.is_tripped(scope, str(key)):
                raise KillSwitchEngaged(scope, str(key))
        return record
