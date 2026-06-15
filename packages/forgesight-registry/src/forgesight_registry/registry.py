"""``Registry`` — resolve ``(name, version)`` → ownership and stamp it onto every run.

Wired at bootstrap as the runtime's run-start metadata provider (feat-022): at run start the
SDK looks the agent up and merges its ownership fields into the run's metadata (on the root
span and every child, FR-5) — caller-set keys win. Resolution is exact ``(name, version)`` →
``(name, "*")`` wildcard → unmatched (counted; ``on_unmatched`` decides warn/ignore/error).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from .model import AgentEntry
from .source import FileSource, HttpSource, RegistrySource, parse_entries

_log = logging.getLogger("forgesight.registry")
_ON_UNMATCHED = ("warn", "ignore", "error")


class RegistryUnmatched(LookupError):
    """Raised at run start when ``on_unmatched='error'`` and an agent isn't declared."""


class Registry:
    """The declared agent registry: resolve ownership and produce run-start metadata."""

    def __init__(
        self,
        entries: Sequence[AgentEntry],
        *,
        stamp_fields: Sequence[str] | None = None,
        prefix: str = "",
        on_unmatched: str = "warn",
    ) -> None:
        if on_unmatched not in _ON_UNMATCHED:
            raise ValueError(f"on_unmatched must be one of {_ON_UNMATCHED}, got {on_unmatched!r}")
        self._entries = list(entries)
        self._exact: dict[tuple[str, str], AgentEntry] = {}
        self._wildcard: dict[str, AgentEntry] = {}
        for entry in entries:
            if entry.version == "*":
                self._wildcard[entry.name] = entry
            else:
                self._exact[(entry.name, entry.version)] = entry
        self._fields = tuple(stamp_fields) if stamp_fields is not None else None
        self._prefix = prefix
        self._on_unmatched = on_unmatched
        self.unmatched_count = 0

    @property
    def entries(self) -> list[AgentEntry]:
        return list(self._entries)

    def resolve(self, name: str, version: str | None) -> AgentEntry | None:
        if version is not None:
            exact = self._exact.get((name, version))
            if exact is not None:
                return exact
        return self._wildcard.get(name)

    def ownership_metadata(self, name: str, version: str | None = None) -> dict[str, str]:
        """The metadata to stamp on a run for ``(name, version)``. The run-start provider."""
        entry = self.resolve(name, version)
        if entry is None:
            self.unmatched_count += 1
            if self._on_unmatched == "error":
                raise RegistryUnmatched(
                    f"agent {name!r} v{version} is not declared in the registry"
                )
            if self._on_unmatched == "warn":
                _log.warning("forgesight-registry: undeclared agent %r v%s", name, version)
            return {}
        fields = entry.fields()
        if self._fields is not None:
            fields = {k: v for k, v in fields.items() if k in self._fields}
        if self._prefix:
            fields = {f"{self._prefix}{k}": v for k, v in fields.items()}
        return fields

    # --- construction -----------------------------------------------------
    @classmethod
    def from_source(cls, source: RegistrySource, **kwargs: Any) -> Registry:
        return cls(source.load(), **kwargs)

    @classmethod
    def from_file(cls, path: str, **kwargs: Any) -> Registry:
        return cls.from_source(FileSource(path), **kwargs)

    @classmethod
    def from_entries(cls, entries: Sequence[Mapping[str, Any]], **kwargs: Any) -> Registry:
        return cls(parse_entries(list(entries)), **kwargs)

    @classmethod
    def from_config(cls, settings: Mapping[str, Any] | None = None) -> Registry:
        from forgesight_core.config import load_settings

        resolved = settings if settings is not None else load_settings()
        block = resolved.get("registry")
        block = block if isinstance(block, Mapping) else {}
        stamp = block.get("stamp")
        stamp = stamp if isinstance(stamp, Mapping) else {}
        kwargs: dict[str, Any] = {
            "on_unmatched": str(block.get("on_unmatched", "warn")),
            "prefix": str(stamp.get("prefix", "")),
            "stamp_fields": list(stamp["fields"])
            if isinstance(stamp.get("fields"), Sequence)
            else None,
        }
        if not block.get("enabled", False):
            return cls([], **kwargs)  # installed but not switched on (P2) ⇒ stamps nothing
        source = str(block.get("source", "file"))
        if source == "file":
            path = block.get("path")
            if not path:
                raise ValueError("registry.source 'file' requires path")
            return cls.from_file(str(path), **kwargs)
        if source == "http":
            url = block.get("url")
            if not url:
                raise ValueError("registry.source 'http' requires url")
            return cls.from_source(HttpSource(str(url)), **kwargs)
        raise ValueError(f"unknown registry source {source!r}")
