"""Token → cost via a LiteLLM-style pricing table (vendored + refreshable).

``TablePricingProvider`` satisfies the locked ``PricingProvider`` Protocol (feat-001).
Resolution order (provider-supplied cost > caller provider > this table > None) is
applied by the runtime (feat-002); this module owns the table + the per-model
computation: model-name resolution (exact → alias → composed), tiered (context-
dependent) pricing, and cache/reasoning token rates. Unknown models return ``None``
(graceful degrade, FR-9); pricing is pure CPU (no I/O except optional refresh).
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources

from forgesight_api import TokenUsage

_log = logging.getLogger("forgesight.cost")

_OPS = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
}
_USAGE_FIELDS = {
    "input_tokens": lambda u: u.input,
    "output_tokens": lambda u: u.output,
    "total_tokens": lambda u: u.total,
}


@dataclass(frozen=True, slots=True)
class _Tier:
    name: str
    priority: int
    when: dict[str, dict[str, float]]
    input_cost_per_token: float | None = None
    output_cost_per_token: float | None = None

    def matches(self, usage: TokenUsage) -> bool:
        for field_name, condition in self.when.items():
            getter = _USAGE_FIELDS.get(field_name)
            if getter is None:
                return False
            actual = getter(usage)
            for op_name, threshold in condition.items():
                op = _OPS.get(op_name)
                if op is None or not op(actual, threshold):
                    return False
        return True


@dataclass(frozen=True, slots=True)
class ModelRates:
    """Per-token rates for one model."""

    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    cache_read_input_token_cost: float = 0.0
    cache_creation_input_token_cost: float = 0.0
    reasoning_cost_per_token: float | None = None
    tiers: tuple[_Tier, ...] = ()

    def cost(self, usage: TokenUsage) -> float:
        input_rate = self.input_cost_per_token
        output_rate = self.output_cost_per_token
        for tier in sorted(self.tiers, key=lambda t: t.priority):
            if tier.matches(usage):
                if tier.input_cost_per_token is not None:
                    input_rate = tier.input_cost_per_token
                if tier.output_cost_per_token is not None:
                    output_rate = tier.output_cost_per_token
                break
        reasoning_rate = self.reasoning_cost_per_token
        if reasoning_rate is None:
            reasoning_rate = output_rate
        return (
            usage.input * input_rate
            + usage.output * output_rate
            + usage.cache_read * self.cache_read_input_token_cost
            + usage.cache_creation * self.cache_creation_input_token_cost
            + usage.reasoning * reasoning_rate
        )


@dataclass(slots=True)
class PricingTable:
    """A set of model rates + alias map, with model-name resolution."""

    models: dict[str, ModelRates] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    updated_at: datetime | None = None

    def resolve(self, provider: str, model: str) -> ModelRates | None:
        for key in (f"{provider}/{model}", model):
            if key in self.models:
                return self.models[key]
            alias = self.aliases.get(key)
            if alias is not None and alias in self.models:
                return self.models[alias]
        return None

    @classmethod
    def parse(cls, data: dict[str, object]) -> PricingTable:
        """Parse either the ForgeSight schema (``{models, aliases}``) or flat LiteLLM."""
        updated = _parse_dt(data.get("updated_at"))
        models_obj = data.get("models")
        aliases: dict[str, str] = {}
        if isinstance(models_obj, dict):  # ForgeSight schema
            raw_models: dict[str, object] = models_obj
            aliases_obj = data.get("aliases")
            if isinstance(aliases_obj, dict):
                aliases = {str(k): str(v) for k, v in aliases_obj.items()}
        else:  # flat LiteLLM map: {model: {input_cost_per_token, ...}}
            raw_models = {k: v for k, v in data.items() if isinstance(v, dict)}
        models = {
            name: _parse_rates(entry)
            for name, entry in raw_models.items()
            if isinstance(entry, dict)
        }
        return cls(models=models, aliases=aliases, updated_at=updated)


class TablePricingProvider:
    """The shipped default ``PricingProvider`` — a vendored, refreshable price table."""

    def __init__(
        self,
        table: PricingTable | None = None,
        *,
        overrides: dict[str, dict[str, object]] | None = None,
        source_url: str | None = None,
    ) -> None:
        self._table = table if table is not None else PricingTable()
        self._source_url = source_url
        if overrides:
            self._apply_overrides(overrides)

    def price(self, provider: str, model: str, usage: TokenUsage) -> float | None:
        rates = self._table.resolve(provider, model)
        if rates is None:
            _log.debug("no pricing for %s/%s; cost=None", provider, model)
            return None
        return rates.cost(usage)

    @property
    def updated_at(self) -> datetime | None:
        return self._table.updated_at

    @classmethod
    def from_vendored(
        cls, *, overrides: dict[str, dict[str, object]] | None = None, source_url: str | None = None
    ) -> TablePricingProvider:
        return cls(_vendored_table(), overrides=overrides, source_url=source_url)

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        timeout_s: float = 5.0,
        overrides: dict[str, dict[str, object]] | None = None,
    ) -> TablePricingProvider:
        return cls(_fetch_table(url, timeout_s), overrides=overrides, source_url=url)

    def refresh(self, *, timeout_s: float = 5.0) -> bool:
        """Best-effort refresh from ``source_url``; keeps the old table on failure."""
        if self._source_url is None:
            return False
        try:
            self._table = _fetch_table(self._source_url, timeout_s)
        except Exception:
            _log.warning("pricing refresh from %s failed; keeping last table", self._source_url)
            return False
        return True

    def _apply_overrides(self, overrides: dict[str, dict[str, object]]) -> None:
        for key, spec in overrides.items():
            alias = spec.get("alias")
            if isinstance(alias, str):
                self._table.aliases[key] = alias
                continue
            base = self._table.models.get(key, ModelRates())
            reasoning = (
                _opt_float(spec["reasoning_cost_per_token"])
                if "reasoning_cost_per_token" in spec
                else base.reasoning_cost_per_token
            )
            self._table.models[key] = ModelRates(
                input_cost_per_token=_flt(
                    spec.get("input_cost_per_token", base.input_cost_per_token)
                ),
                output_cost_per_token=_flt(
                    spec.get("output_cost_per_token", base.output_cost_per_token)
                ),
                cache_read_input_token_cost=_flt(
                    spec.get("cache_read_input_token_cost", base.cache_read_input_token_cost)
                ),
                cache_creation_input_token_cost=_flt(
                    spec.get(
                        "cache_creation_input_token_cost", base.cache_creation_input_token_cost
                    )
                ),
                reasoning_cost_per_token=reasoning,
                tiers=base.tiers,
            )


_vendored_cache: PricingTable | None = None


def _vendored_table() -> PricingTable:
    """Return a fresh copy of the parsed vendored table (parsed once, copied per call).

    A copy so that per-provider ``overrides`` mutate the caller's table, never the
    shared cache (otherwise one provider's overrides leak into the next).
    """
    global _vendored_cache
    if _vendored_cache is None:
        raw = resources.files("forgesight_core.cost").joinpath("data/prices.json").read_text()
        _vendored_cache = PricingTable.parse(json.loads(raw))
    src = _vendored_cache
    return PricingTable(
        models=dict(src.models), aliases=dict(src.aliases), updated_at=src.updated_at
    )


def _fetch_table(url: str, timeout_s: float) -> PricingTable:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        return PricingTable.parse(json.loads(response.read().decode("utf-8")))


def _tier(t: dict[str, object]) -> _Tier:
    when = t.get("when")
    return _Tier(
        name=str(t.get("name", "")),
        priority=_int(t.get("priority")),
        when=when if isinstance(when, dict) else {},
        input_cost_per_token=_opt_float(t.get("input_cost_per_token")),
        output_cost_per_token=_opt_float(t.get("output_cost_per_token")),
    )


def _parse_rates(entry: dict[str, object]) -> ModelRates:
    raw_tiers = entry.get("tiers")
    tiers: tuple[_Tier, ...] = ()
    if isinstance(raw_tiers, list):
        tiers = tuple(_tier(t) for t in raw_tiers if isinstance(t, dict))
    return ModelRates(
        input_cost_per_token=_flt(entry.get("input_cost_per_token")),
        output_cost_per_token=_flt(entry.get("output_cost_per_token")),
        cache_read_input_token_cost=_flt(entry.get("cache_read_input_token_cost")),
        cache_creation_input_token_cost=_flt(entry.get("cache_creation_input_token_cost")),
        reasoning_cost_per_token=_opt_float(entry.get("reasoning_cost_per_token")),
        tiers=tiers,
    )


def _int(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0


def _flt(value: object) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _opt_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
