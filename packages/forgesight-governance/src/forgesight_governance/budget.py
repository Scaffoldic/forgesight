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

from forgesight_api import GovernanceSignal, Record, RunStatus

from ._settings import governance_settings

BUDGET_EXCEEDED_ATTR = "forgesight.budget.exceeded"
_SCOPE_METADATA = {"team": "team", "repo": "repo", "environment": "environment"}


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
    ) -> None:
        if on_breach not in ("raise", "drop", "mark"):
            raise ValueError(f"on_breach must be raise|drop|mark, got {on_breach!r}")
        for cap in caps:
            if cap.usd is None and cap.tokens is None:
                raise ValueError(f"BudgetCap for {cap.scope}={cap.key} sets neither usd nor tokens")
        self._caps = list(caps)
        self._on_breach = on_breach
        self._totals: dict[tuple[BudgetScope, str], dict[str, float]] = {}

    @classmethod
    def from_config(cls, settings: Mapping[str, Any] | None = None) -> BudgetInterceptor:
        budgets = governance_settings(settings).get("budgets")
        budgets = budgets if isinstance(budgets, Mapping) else {}
        caps = _parse_caps(budgets)
        on_breach = str(budgets.get("on_breach", "raise"))
        return cls(caps=caps, on_breach=on_breach)  # type: ignore[arg-type]

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
        return record

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


def _f(value: Any) -> float | None:
    return float(value) if value is not None else None


def _i(value: Any) -> int | None:
    return int(value) if value is not None else None
