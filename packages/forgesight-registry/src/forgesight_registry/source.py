"""Where registry entries come from — file and HTTP shipped, custom via the Protocol.

A ``RegistrySource`` loads ``AgentEntry`` records from a declared source. The file source
reads YAML/JSON once at ``configure()``; the HTTP source TTL-refreshes best-effort and keeps
the last-good set on a failed refresh (the cost-table pattern). No vendor SDK — the HTTP
source uses stdlib ``urllib`` (P1).
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

import yaml

from .model import AgentEntry, Lifecycle


@runtime_checkable
class RegistrySource(Protocol):
    """Loads the declared registry entries. Shipped: file + HTTP; custom via this Protocol."""

    def load(self) -> Sequence[AgentEntry]: ...


def parse_entries(raw: Any) -> list[AgentEntry]:
    """Parse the ``agents:`` list (or a bare list) into :class:`AgentEntry` records."""
    items = raw.get("agents") if isinstance(raw, Mapping) else raw
    if not isinstance(items, Sequence):
        return []
    entries: list[AgentEntry] = []
    for item in items:
        if not isinstance(item, Mapping) or not item.get("name"):
            continue
        known = {"name", "version", "owner", "team", "repo", "lifecycle", "sla_tier"}
        extra = {str(k): str(v) for k, v in item.items() if k not in known and k != "extra"}
        extra.update({str(k): str(v) for k, v in (item.get("extra") or {}).items()})
        entries.append(
            AgentEntry(
                name=str(item["name"]),
                version=str(item.get("version", "*")),
                owner=_opt(item.get("owner")),
                team=_opt(item.get("team")),
                repo=_opt(item.get("repo")),
                lifecycle=Lifecycle(str(item.get("lifecycle", "ga"))),
                sla_tier=_opt(item.get("sla_tier")),
                extra=extra,
            )
        )
    return entries


class FileSource:
    """Loads entries from a YAML or JSON file (read once at load)."""

    def __init__(self, path: str) -> None:
        self._path = path

    def load(self) -> Sequence[AgentEntry]:
        with open(self._path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)  # YAML is a JSON superset
        return parse_entries(raw)


class HttpSource:  # pragma: no cover - requires a live endpoint
    """Loads entries from an HTTP(S) URL (stdlib urllib; TTL refresh is the Registry's job)."""

    def __init__(self, url: str, *, timeout: float = 5.0) -> None:
        self._url = url
        self._timeout = timeout

    def load(self) -> Sequence[AgentEntry]:
        request = urllib.request.Request(self._url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
        return parse_entries(raw)


def _opt(value: Any) -> str | None:
    return str(value) if value is not None else None
