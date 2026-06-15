"""``GitHubMetadataInterceptor`` — attach CI correlation metadata to every record.

``bootstrap()`` runs once at process start, before any run opens, so there is no live run
to ``set_metadata`` on. Instead this interceptor merges the ``GITHUB_*`` metadata onto every
record's attributes (FR-5) — so each span carries the repo / sha / PR / workflow / job and
"spend on PR #1234" is a one-line backend query. Per-call metadata wins (``setdefault``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from forgesight_api import Record


class GitHubMetadataInterceptor:
    """Merge fixed CI metadata into each record's attributes without overwriting per-call keys."""

    def __init__(self, metadata: Mapping[str, str]) -> None:
        self._metadata = dict(metadata)

    def intercept(self, record: Record) -> Record | None:
        if not self._metadata:
            return record
        attrs = dict(record.attributes)
        for key, value in self._metadata.items():
            attrs.setdefault(key, value)  # don't clobber metadata the caller set explicitly
        return replace(record, attributes=MappingProxyType(attrs))
