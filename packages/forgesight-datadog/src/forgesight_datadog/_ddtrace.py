"""Vendor-backed defaults for the ``agent`` transport — ``ddtrace`` + dogstatsd.

These touch the live Datadog SDK / Agent, so they are exercised only against a real DD
Agent (every line is ``pragma: no cover``). The pure record→span mapping lives in
:mod:`forgesight_datadog.exporter` and is fully unit-tested via injected doubles; this
module is the thin edge that pushes a mapped :class:`DatadogSpan` onto a ``ddtrace`` span
and a DD metric onto dogstatsd.

Vendor access goes through an ``Any``-typed dynamic boundary on purpose: the package
supports ``ddtrace>=2`` and the exact span/writer API drifts across major versions, so we
resolve it at runtime (against whatever ``ddtrace`` is installed) rather than pinning to
one version's symbols at type-check time.
"""

from __future__ import annotations

import contextlib
import importlib
from collections.abc import Sequence
from typing import Any

from .exporter import DatadogSpan

_DD_64BIT_MASK = (1 << 64) - 1
_NANOS_PER_S = 1_000_000_000


class DDTraceSpanWriter:  # pragma: no cover - requires a live DD Agent
    """Writes mapped spans to a DD Agent via ``ddtrace``'s public tracer."""

    def __init__(
        self,
        *,
        service: str,
        api_key: str | None,
        site: str,
        agent_endpoint: str | None,
    ) -> None:
        trace_mod: Any = importlib.import_module("ddtrace.trace")
        self._tracer: Any = trace_mod.tracer
        if agent_endpoint:
            # best-effort; the tracer falls back to its default agent url
            with contextlib.suppress(Exception):
                self._tracer.configure(hostname=None, port=None, url=agent_endpoint)
        self._service = service

    def write(self, span: DatadogSpan) -> None:
        dd = self._tracer.start_span(
            span.name, service=span.service, resource=span.resource, activate=False
        )
        dd.trace_id = int(span.trace_id, 16)
        dd.span_id = int(span.span_id, 16) & _DD_64BIT_MASK
        if span.parent_id:
            dd.parent_id = int(span.parent_id, 16) & _DD_64BIT_MASK
        dd.start_ns = span.start_ns
        dd.error = span.error
        for key, tag in span.meta.items():
            dd.set_tag(key, tag)
        for key, metric in span.metrics.items():
            dd.set_metric(key, metric)
        dd.finish(finish_time=(span.start_ns + span.duration_ns) / _NANOS_PER_S)

    def flush(self) -> bool:
        flush = getattr(self._tracer, "flush", None)
        if not callable(flush):
            return True
        try:
            flush()
        except Exception:
            return False
        return True

    def stop(self) -> None:
        shutdown = getattr(self._tracer, "shutdown", None)
        if callable(shutdown):
            shutdown()


class DogStatsdMetricSink:  # pragma: no cover - requires a live DD Agent / dogstatsd
    """Emits DD metrics (cost / tokens) to dogstatsd via the DD Agent."""

    def __init__(self, *, agent_endpoint: str | None) -> None:
        dogstatsd_mod: Any = importlib.import_module("ddtrace.internal.dogstatsd")
        host = "localhost"
        if agent_endpoint:
            host = agent_endpoint.split("://", 1)[-1].split(":", 1)[0]
        self._client: Any = dogstatsd_mod.get_dogstatsd_client(f"udp://{host}:8125")

    def emit(self, name: str, value: float, tags: Sequence[str]) -> None:
        self._client.distribution(name, value, tags=list(tags))

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
