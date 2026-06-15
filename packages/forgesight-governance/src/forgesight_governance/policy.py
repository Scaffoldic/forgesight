"""``PolicyInterceptor`` — declarative allow / deny / redact on business metadata (feat-020).

First matching rule wins (the ``match`` predicate over the run's metadata). ``deny`` a model
set (e.g. unpriced/preview models in ``environment=prod``) → ``PolicyDenied`` → the run is
``GUARDRAIL``. ``allow`` with a model set is an allow-list (a non-listed model is denied).
``redact`` strips captured content from the record (force content-off for PII-tagged runs).
Rides the locked ``Interceptor`` SPI, composing with the budget + kill-switch chain.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from fnmatch import fnmatch
from types import MappingProxyType
from typing import Any

from forgesight_api import GovernanceSignal, Record, RunStatus

from ._settings import governance_settings

_CONTENT_ATTRS = (
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
)


class PolicyAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REDACT = "redact"


@dataclass(frozen=True, slots=True)
class PolicyRule:
    match: Mapping[str, str]
    action: PolicyAction
    models: tuple[str, ...] = ()
    reason: str = ""


class PolicyDenied(GovernanceSignal):
    """Raised when a policy rule denies a call. Maps the run to ``GUARDRAIL``."""

    def __init__(self, reason: str, *, model: str, rule: PolicyRule) -> None:
        super().__init__(reason or f"policy denied model {model}", run_status=RunStatus.GUARDRAIL)
        self.model = model
        self.rule = rule


class PolicyInterceptor:
    """Apply the first matching metadata-predicate rule to each LLM call."""

    def __init__(self, *, rules: Sequence[PolicyRule]) -> None:
        for rule in rules:
            if rule.action in (PolicyAction.ALLOW, PolicyAction.DENY) and not rule.models:
                raise ValueError(f"policy {rule.action} rule must set models: {rule.match}")
        self._rules = list(rules)

    @classmethod
    def from_config(cls, settings: Mapping[str, Any] | None = None) -> PolicyInterceptor:
        policies = governance_settings(settings).get("policies")
        policies = policies if isinstance(policies, Mapping) else {}
        return cls(rules=_parse_rules(policies.get("rules")))

    # --- Interceptor SPI --------------------------------------------------
    def intercept(self, record: Record) -> Record | None:
        if record.llm is None:
            return record
        model = record.llm.request_model
        attrs = record.attributes
        for rule in self._rules:
            if not _matches(rule.match, attrs):
                continue
            return self._apply(rule, record, model)
        return record

    def _apply(self, rule: PolicyRule, record: Record, model: str) -> Record:
        if rule.action is PolicyAction.REDACT:
            return _redact(record)
        in_set = any(fnmatch(model, pattern) for pattern in rule.models)
        if rule.action is PolicyAction.DENY and in_set:
            raise PolicyDenied(rule.reason, model=model, rule=rule)
        if rule.action is PolicyAction.ALLOW and not in_set:
            raise PolicyDenied(
                rule.reason or f"model {model} not in allow-list", model=model, rule=rule
            )
        return record  # matched rule does not forbid this model ⇒ pass


def _matches(match: Mapping[str, str], attrs: Mapping[str, object]) -> bool:
    return all(str(attrs.get(key)) == value for key, value in match.items())


def _redact(record: Record) -> Record:
    attrs = {k: v for k, v in record.attributes.items() if k not in _CONTENT_ATTRS}
    llm = record.llm
    if llm is not None and llm.content is not None:
        llm = replace(llm, content=None)
    return replace(record, attributes=MappingProxyType(attrs), llm=llm)


def _parse_rules(raw: Any) -> list[PolicyRule]:
    if not isinstance(raw, Sequence):
        return []
    rules: list[PolicyRule] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        match = entry.get("match")
        action = entry.get("action")
        if not isinstance(match, Mapping) or action is None:
            continue
        models = entry.get("models") or ()
        rules.append(
            PolicyRule(
                match={str(k): str(v) for k, v in match.items()},
                action=PolicyAction(str(action)),
                models=tuple(str(m) for m in models),
                reason=str(entry.get("reason", "")),
            )
        )
    return rules
