"""``PIIRedactionInterceptor`` — key-based + pattern-based redaction.

Redacts sensitive values in a record's business metadata/attributes and LLM request
params before export, so a secret is scrubbed once and is gone from every backend.
Key matching (case-insensitive substring on the field key) takes precedence over
pattern matching; both replace with a placeholder. Patterns are compiled once.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from forgesight_api import Record

_DEFAULT_KEYS = ("api_key", "password", "secret", "token", "authorization")


class PIIRedactionInterceptor:
    """Replace values whose key matches ``redact_keys`` or whose text matches a pattern."""

    def __init__(
        self,
        *,
        redact_keys: tuple[str, ...] = _DEFAULT_KEYS,
        redact_patterns: tuple[str, ...] = (),
        placeholder: str = "<redacted>",
    ) -> None:
        self._keys = tuple(k.lower() for k in redact_keys)
        self._patterns = [re.compile(p) for p in redact_patterns]  # bad regex → fail fast
        self._placeholder = placeholder

    def intercept(self, record: Record) -> Record | None:
        new_attrs: Mapping[str, object] = MappingProxyType(self._redact_mapping(record.attributes))
        if record.llm is not None and record.llm.params:
            new_llm = replace(record.llm, params=self._redact_mapping(record.llm.params))
            return replace(record, attributes=new_attrs, llm=new_llm)
        return replace(record, attributes=new_attrs)

    def _redact_mapping(self, source: Mapping[str, object]) -> dict[str, object]:
        return {key: self._redact_value(key, value) for key, value in source.items()}

    def _redact_value(self, key: str, value: object) -> object:
        if self._key_matches(key):
            return self._placeholder
        if isinstance(value, Mapping):
            return self._redact_mapping(value)
        if isinstance(value, str):
            return self._apply_patterns(value)
        return value

    def _key_matches(self, key: str) -> bool:
        lowered = key.lower()
        return any(needle in lowered for needle in self._keys)

    def _apply_patterns(self, text: str) -> str:
        for pattern in self._patterns:
            text = pattern.sub(self._placeholder, text)
        return text
