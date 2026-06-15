"""Offline chargeback + catalogue rollups over exported records (feat-022).

Pure aggregation, off the hot path. Because the ownership dimensions were stamped *at
source* from one declaration, the group-by is clean — no missing / misspelled ``team``. An
absent dimension groups under ``"<unattributed>"`` so cost never silently vanishes. The
catalogue joins the *declared* registry (owner / lifecycle / SLA) with *observed* telemetry
(last-seen / run count / windowed cost), surfacing declared-but-silent and undeclared agents.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from forgesight_api import Kind, Record, RunStatus

from .model import Lifecycle
from .registry import Registry

UNATTRIBUTED = "<unattributed>"
_NANOS_PER_DAY = 86_400 * 1_000_000_000
_OK = frozenset({RunStatus.OK, RunStatus.RUNNING})


@dataclass(frozen=True, slots=True)
class ChargebackRow:
    dimensions: Mapping[str, str]
    cost_usd: float
    run_count: int
    token_total: int
    failure_count: int


class ChargebackReport:
    """Cost / tokens / runs / failures grouped by ownership dimensions."""

    def __init__(self, rows: Sequence[ChargebackRow]) -> None:
        self._rows = list(rows)

    @classmethod
    def from_records(
        cls,
        records: Sequence[Record],
        *,
        dimensions: Sequence[str],
        registry: Registry | None = None,
    ) -> ChargebackReport:
        groups: dict[tuple[str, ...], list[float]] = {}
        for record in records:
            key = tuple(str(record.attributes.get(dim, UNATTRIBUTED)) for dim in dimensions)
            acc = groups.setdefault(key, [0.0, 0.0, 0.0, 0.0])  # cost, tokens, runs, failures
            if record.llm is not None:
                acc[0] += record.llm.cost_usd or 0.0
                acc[1] += record.llm.usage.total
            if record.kind is Kind.AGENT:
                acc[2] += 1
                if record.status not in _OK:
                    acc[3] += 1
        rows = [
            ChargebackRow(
                dimensions=dict(zip(dimensions, key, strict=True)),
                cost_usd=acc[0],
                token_total=int(acc[1]),
                run_count=int(acc[2]),
                failure_count=int(acc[3]),
            )
            for key, acc in groups.items()
        ]
        return cls(rows)

    def rows(self) -> list[ChargebackRow]:
        return list(self._rows)

    def total_usd(self) -> float:
        return sum(row.cost_usd for row in self._rows)


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    name: str
    version: str
    owner: str | None
    team: str | None
    lifecycle: Lifecycle | None
    sla_tier: str | None
    last_seen: int | None
    run_count: int
    cost_30d: float
    declared: bool
    active: bool


@dataclass
class _Observed:
    run_count: int = 0
    last_seen: int | None = None
    cost_window: float = 0.0
    run_ids: set[str] = field(default_factory=set)


class AgentCatalogue:
    """Declared+observed union: agents with owner, lifecycle, last-seen, and windowed cost."""

    def __init__(self, entries: Sequence[CatalogueEntry]) -> None:
        self._entries = list(entries)

    @classmethod
    def from_records(
        cls,
        records: Sequence[Record],
        *,
        registry: Registry,
        now_unix_nanos: int,
        window_days: int = 30,
    ) -> AgentCatalogue:
        cutoff = now_unix_nanos - window_days * _NANOS_PER_DAY
        observed: dict[str, _Observed] = {}
        run_to_name: dict[str, str] = {}
        for record in records:
            if record.kind is Kind.AGENT:
                obs = observed.setdefault(record.name, _Observed())
                obs.run_count += 1
                obs.run_ids.add(record.run_id)
                run_to_name[record.run_id] = record.name
                end = record.end_unix_nanos
                if end is not None and (obs.last_seen is None or end > obs.last_seen):
                    obs.last_seen = end
        for record in records:  # attribute LLM cost to the owning agent run, within the window
            if record.llm is None:
                continue
            name = run_to_name.get(record.run_id)
            if name is None:
                continue
            end = record.end_unix_nanos or 0
            if end >= cutoff and record.llm.cost_usd:
                observed[name].cost_window += record.llm.cost_usd

        entries: list[CatalogueEntry] = []
        seen_names: set[str] = set()
        for declared in registry.entries:  # declared-and-active + declared-but-silent
            declared_obs = observed.get(declared.name)
            seen_names.add(declared.name)
            entries.append(
                CatalogueEntry(
                    name=declared.name,
                    version=declared.version,
                    owner=declared.owner,
                    team=declared.team,
                    lifecycle=declared.lifecycle,
                    sla_tier=declared.sla_tier,
                    last_seen=declared_obs.last_seen if declared_obs else None,
                    run_count=declared_obs.run_count if declared_obs else 0,
                    cost_30d=declared_obs.cost_window if declared_obs else 0.0,
                    declared=True,
                    active=declared_obs is not None,
                )
            )
        for name, obs in observed.items():  # active-but-undeclared (a governance gap)
            if name in seen_names:
                continue
            entries.append(
                CatalogueEntry(
                    name=name,
                    version="*",
                    owner=None,
                    team=None,
                    lifecycle=None,
                    sla_tier=None,
                    last_seen=obs.last_seen,
                    run_count=obs.run_count,
                    cost_30d=obs.cost_window,
                    declared=False,
                    active=True,
                )
            )
        return cls(entries)

    def entries(self) -> list[CatalogueEntry]:
        return list(self._entries)
