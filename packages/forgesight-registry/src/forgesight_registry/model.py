"""``AgentEntry`` — one declared agent's ownership metadata (feat-022).

The registry's value type: the name→team→owner→repo→lifecycle mapping that, declared once,
is auto-stamped onto every run so chargeback rolls up on clean dimensions and every run is
traceable to a human. ``version`` is an exact string or ``"*"`` (any version). Experimental.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

_EMPTY: Mapping[str, str] = MappingProxyType({})


class Lifecycle(StrEnum):
    EXPERIMENTAL = "experimental"
    BETA = "beta"
    GA = "ga"
    DEPRECATED = "deprecated"


@dataclass(frozen=True, slots=True)
class AgentEntry:
    name: str
    version: str = "*"  # exact version or "*" wildcard
    owner: str | None = None
    team: str | None = None
    repo: str | None = None
    lifecycle: Lifecycle = Lifecycle.GA
    sla_tier: str | None = None
    extra: Mapping[str, str] = field(default_factory=lambda: _EMPTY)

    def fields(self) -> dict[str, str]:
        """The stampable fields as a flat ``key → value`` dict (omitting unset ones)."""
        out: dict[str, str] = {}
        if self.owner is not None:
            out["owner"] = self.owner
        if self.team is not None:
            out["team"] = self.team
        if self.repo is not None:
            out["repo"] = self.repo
        out["lifecycle"] = self.lifecycle.value
        if self.sla_tier is not None:
            out["sla_tier"] = self.sla_tier
        for key, value in self.extra.items():
            out[key] = value
        return out
