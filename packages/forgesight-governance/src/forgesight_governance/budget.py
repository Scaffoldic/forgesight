"""``BudgetInterceptor`` — turn the cost signal into a control (feat-020).

Runs on each completed LLM record: adds the call's cost / tokens to the per-scope running
totals (keyed on the business metadata the SDK already attaches — FR-5) and, if a cap would
be breached, enforces ``on_breach``. ``raise`` halts the run with ``BudgetExceeded`` (a
:class:`~forgesight_api.GovernanceSignal`, the one sanctioned interceptor-raises case) →
``RunStatus.BUDGET_EXCEEDED``; the run record still flushes. Process-local totals (a shared
store is a follow-up). It rides the locked ``Interceptor`` SPI — no new core surface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from forgesight_api import GovernanceSignal, PricingProvider, Record, RunStatus, TokenUsage
from forgesight_core import get_runtime

from ._settings import governance_settings

BUDGET_EXCEEDED_ATTR = "forgesight.budget.exceeded"
_SCOPE_METADATA = {"team": "team", "repo": "repo", "environment": "environment"}
_ESTIMATES = ("max_tokens", "input_ratio", "fixed")
_ON_UNPRICED = ("allow", "deny")


@dataclass(frozen=True, slots=True)
class ProjectionConfig:
    """Pre-call projection settings (feat-026). Off ⇒ budgets stay post-hoc (feat-020)."""

    enabled: bool = False
    output_token_estimate: Literal["max_tokens", "input_ratio", "fixed"] = "max_tokens"
    fixed_output_tokens: int = 0  # used when estimate == "fixed"
    input_ratio: float = 1.0  # output ~= input_ratio * declared input
    on_unpriced: Literal["allow", "deny"] = "allow"  # cost=None → can't guarantee under cap

    def __post_init__(self) -> None:
        if self.output_token_estimate not in _ESTIMATES:
            raise ValueError(f"output_token_estimate must be one of {_ESTIMATES}")
        if self.on_unpriced not in _ON_UNPRICED:
            raise ValueError(f"on_unpriced must be one of {_ON_UNPRICED}")
        if self.output_token_estimate == "fixed" and self.fixed_output_tokens <= 0:
            raise ValueError("fixed_output_tokens must be > 0 when estimate == 'fixed'")
        if self.output_token_estimate == "input_ratio" and self.input_ratio <= 0:
            raise ValueError("input_ratio must be > 0 when estimate == 'input_ratio'")


class BudgetScope(StrEnum):
    RUN = "run"
    TEAM = "team"
    REPO = "repo"
    ENVIRONMENT = "environment"


@dataclass(frozen=True, slots=True)
class BudgetCap:
    scope: BudgetScope
    key: str | None = None  # e.g. "growth" for scope=team; None = the per-run cap / every value
    usd: float | None = None
    tokens: int | None = None


class BudgetExceeded(GovernanceSignal):
    """Raised when a cap would be breached. Carries the trip context."""

    def __init__(
        self,
        *,
        scope: BudgetScope,
        key: str | None,
        cap_usd: float | None,
        cap_tokens: int | None,
        accumulated_usd: float,
        projected_usd: float,
    ) -> None:
        super().__init__(
            f"budget exceeded for {scope}={key}: ${projected_usd:.4f} > cap ${cap_usd}",
            run_status=RunStatus.BUDGET_EXCEEDED,
        )
        self.scope = scope
        self.key = key
        self.cap_usd = cap_usd
        self.cap_tokens = cap_tokens
        self.accumulated_usd = accumulated_usd
        self.projected_usd = projected_usd


class BudgetInterceptor:
    """Accumulate per-scope spend and enforce caps on the LLM-call path."""

    def __init__(
        self,
        *,
        caps: Sequence[BudgetCap],
        on_breach: Literal["raise", "drop", "mark"] = "raise",
        pricing: PricingProvider | None = None,
        projection: ProjectionConfig | None = None,
    ) -> None:
        if on_breach not in ("raise", "drop", "mark"):
            raise ValueError(f"on_breach must be raise|drop|mark, got {on_breach!r}")
        for cap in caps:
            if cap.usd is None and cap.tokens is None:
                raise ValueError(f"BudgetCap for {cap.scope}={cap.key} sets neither usd nor tokens")
        self._caps = list(caps)
        self._on_breach = on_breach
        self._pricing = pricing  # None ⇒ resolve the runtime's configured PricingProvider
        self._projection = projection  # None ⇒ post-hoc only (feat-020 behaviour)
        self._totals: dict[tuple[BudgetScope, str], dict[str, float]] = {}

    @classmethod
    def from_config(cls, settings: Mapping[str, Any] | None = None) -> BudgetInterceptor:
        budgets = governance_settings(settings).get("budgets")
        budgets = budgets if isinstance(budgets, Mapping) else {}
        caps = _parse_caps(budgets)
        on_breach = str(budgets.get("on_breach", "raise"))
        projection = _parse_projection(budgets.get("projection"))
        return cls(caps=caps, on_breach=on_breach, projection=projection)  # type: ignore[arg-type]

    # --- pre-call projection (feat-026) -----------------------------------
    def precall(self, record: Record) -> None:
        """Estimate this LLM call's cost *before* it is made and deny if a cap would be
        breached. A **guard only** — it never commits to the running totals; the realized
        cost on the completed record (``intercept``) stays the sole writer, so a
        conservative over-estimate can't permanently inflate the accumulator."""
        if self._projection is None or not self._projection.enabled or record.llm is None:
            return
        projected = self._project_cost(record)
        if projected is None:
            if self._projection.on_unpriced == "deny":
                self._raise_for(record, projected_usd=float("inf"))
            return
        self._raise_for(record, projected_usd=projected, additive=True)

    def _raise_for(self, record: Record, *, projected_usd: float, additive: bool = False) -> None:
        for cap in self._caps:
            if cap.usd is None:
                continue
            acc_key = _accumulator_key(cap, record)
            if acc_key is None:
                continue
            accumulated = self._totals.get((cap.scope, acc_key), {"usd": 0.0})["usd"]
            total = accumulated + projected_usd if additive else projected_usd
            if total > cap.usd:
                raise BudgetExceeded(
                    scope=cap.scope,
                    key=acc_key if cap.scope is not BudgetScope.RUN else None,
                    cap_usd=cap.usd,
                    cap_tokens=cap.tokens,
                    accumulated_usd=accumulated,
                    projected_usd=total,
                )

    def _project_cost(self, record: Record) -> float | None:
        assert record.llm is not None
        assert self._projection is not None
        usage = record.llm.usage
        estimate = self._projection.output_token_estimate
        if estimate == "input_ratio":
            output = int(usage.input * self._projection.input_ratio)
        elif estimate == "fixed":
            output = self._projection.fixed_output_tokens
        else:  # max_tokens — the caller-declared worst case (stuffed into usage.output)
            output = usage.output
        projected_usage = TokenUsage(input=usage.input, output=output)
        pricing = self._pricing or get_runtime().pricing
        if pricing is None:
            return None
        return pricing.price(record.llm.provider, record.llm.request_model, projected_usage)

    # --- Interceptor SPI --------------------------------------------------
    def intercept(self, record: Record) -> Record | None:
        if record.llm is None:
            return record  # governance acts only on LLM calls
        cost = record.llm.cost_usd or 0.0
        tokens = record.llm.usage.total
        for cap in self._caps:
            acc_key = _accumulator_key(cap, record)
            if acc_key is None:
                continue
            total = self._totals.setdefault((cap.scope, acc_key), {"usd": 0.0, "tokens": 0.0})
            projected_usd = total["usd"] + cost
            projected_tokens = total["tokens"] + tokens
            if _breaches(cap, projected_usd, projected_tokens):
                breached = self._enforce(cap, acc_key, record, total["usd"], projected_usd)
                if breached is not record:
                    return breached
            total["usd"] = projected_usd
            total["tokens"] = projected_tokens
            self._emit_utilization(cap, acc_key, projected_usd)
        return record

    def _emit_utilization(self, cap: BudgetCap, acc_key: str, accumulated_usd: float) -> None:
        """Record forgesight.cost.budget_utilization (spend/cap) through the runtime's
        metrics subsystem (feat-026). Core never depends on governance — governance records
        through core's public surface. No-op when metrics are off or the cap has no usd."""
        if cap.usd is None or cap.usd <= 0:
            return
        metrics = get_runtime().metrics
        if metrics is None:
            return
        metrics.set_budget_utilization(
            accumulated_usd / cap.usd,
            {"budget.scope": cap.scope.value, "budget.key": acc_key},
        )

    def _enforce(
        self,
        cap: BudgetCap,
        acc_key: str,
        record: Record,
        accumulated_usd: float,
        projected_usd: float,
    ) -> Record | None:
        if self._on_breach == "raise":
            raise BudgetExceeded(
                scope=cap.scope,
                key=acc_key if cap.scope is not BudgetScope.RUN else None,
                cap_usd=cap.usd,
                cap_tokens=cap.tokens,
                accumulated_usd=accumulated_usd,
                projected_usd=projected_usd,
            )
        if self._on_breach == "drop":
            return None
        # mark: flag the record but let the run continue
        from types import MappingProxyType

        attrs = dict(record.attributes)
        attrs[BUDGET_EXCEEDED_ATTR] = True
        from dataclasses import replace

        return replace(record, attributes=MappingProxyType(attrs))


def _accumulator_key(cap: BudgetCap, record: Record) -> str | None:
    if cap.scope is BudgetScope.RUN:
        return record.run_id  # the per-run cap applies to every run, keyed by run id
    value = record.attributes.get(_SCOPE_METADATA[cap.scope.value])
    if value is None:
        return None
    if cap.key is not None and str(value) != cap.key:
        return None  # this cap targets a different key
    return str(value)


def _breaches(cap: BudgetCap, usd: float, tokens: float) -> bool:
    if cap.usd is not None and usd > cap.usd:
        return True
    return cap.tokens is not None and tokens > cap.tokens


def _parse_caps(budgets: Mapping[str, Any]) -> list[BudgetCap]:
    caps: list[BudgetCap] = []
    per_run = budgets.get("per_run")
    if isinstance(per_run, Mapping):
        caps.append(
            BudgetCap(BudgetScope.RUN, None, _f(per_run.get("usd")), _i(per_run.get("tokens")))
        )
    for block_key, scope in (
        ("per_team", BudgetScope.TEAM),
        ("per_repo", BudgetScope.REPO),
        ("per_environment", BudgetScope.ENVIRONMENT),
    ):
        block = budgets.get(block_key)
        if not isinstance(block, Mapping):
            continue
        for key, caps_for_key in block.items():
            if isinstance(caps_for_key, Mapping):
                caps.append(
                    BudgetCap(
                        scope, str(key), _f(caps_for_key.get("usd")), _i(caps_for_key.get("tokens"))
                    )
                )
    return caps


def _parse_projection(raw: Any) -> ProjectionConfig | None:
    if not isinstance(raw, Mapping):
        return None
    return ProjectionConfig(
        enabled=bool(raw.get("enabled", False)),
        output_token_estimate=str(raw.get("output_token_estimate", "max_tokens")),  # type: ignore[arg-type]
        fixed_output_tokens=int(raw.get("fixed_output_tokens", 0)),
        input_ratio=float(raw.get("input_ratio", 1.0)),
        on_unpriced=str(raw.get("on_unpriced", "allow")),  # type: ignore[arg-type]
    )


def _f(value: Any) -> float | None:
    return float(value) if value is not None else None


def _i(value: Any) -> int | None:
    return int(value) if value is not None else None
