"""Shared fixtures: a runtime wired to an in-memory exporter, reset per test."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from forgesight_core import InMemoryExporter, configure, reset_runtime


@pytest.fixture
def mem() -> Iterator[InMemoryExporter]:
    exporter = InMemoryExporter()
    configure(exporters=[exporter])
    yield exporter
    reset_runtime()
