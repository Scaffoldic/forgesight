"""``bootstrap()`` — the one-line CI wiring, plus the ``forgesight.integrations`` ``install``.

``bootstrap()`` does three things in order: read ``GITHUB_*`` metadata, ``configure()`` the
SDK and attach that metadata to every record (FR-5), and register an exit hook that flushes
telemetry (so a deploy/ephemeral runner never drops it) and writes a job summary. Outside CI
it falls back to a plain ``configure()`` and warns once, so the same script runs locally.
"""

from __future__ import annotations

import atexit
import logging
import os
from collections.abc import Mapping, Sequence

from forgesight_core import Runtime, configure

from .interceptor import GitHubMetadataInterceptor
from .metadata import github_metadata
from .oidc import fetch_oidc_token
from .summary import DEFAULT_SUMMARY_METRICS, SummaryCollector, write_summary

_log = logging.getLogger("forgesight.github")

OIDC_TOKEN_ENV = "FORGESIGHT_OTLP_TOKEN"  # documented hand-off for the collector/exporter
_warned_not_ci = False
_installed_config: dict[str, object] = {}


def in_github_actions(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get("GITHUB_ACTIONS") == "true"


def bootstrap(
    *,
    write_summary: bool = True,
    summary_metrics: Sequence[str] = DEFAULT_SUMMARY_METRICS,
    oidc: bool = False,
    extra_metadata: Mapping[str, str] | None = None,
    _register_exit: bool = True,
) -> None:
    """One-line CI bootstrap: ``configure()``, attach ``GITHUB_*`` metadata, summary-on-exit."""
    in_ci = in_github_actions()
    if not in_ci:
        _warn_not_ci()

    metadata: dict[str, str] = github_metadata() if in_ci else {}
    if extra_metadata:
        metadata.update(extra_metadata)

    if oidc:
        _apply_oidc()

    runtime = configure()
    if metadata:
        runtime.add_interceptor(GitHubMetadataInterceptor(metadata))

    do_summary = write_summary and in_ci and _summary_enabled()
    collector: SummaryCollector | None = None
    if do_summary:
        collector = SummaryCollector()
        runtime.add_listener(collector)

    fields = tuple(summary_metrics)
    if _register_exit:
        atexit.register(run_exit_hook, runtime, collector, fields)


def run_exit_hook(
    runtime: Runtime, collector: SummaryCollector | None, fields: Sequence[str]
) -> None:
    """Flush telemetry then (best-effort) write the job summary. Safe to call directly."""
    timeout = runtime.config.export_timeout_millis
    try:
        runtime.force_flush(timeout)
    finally:
        runtime.shutdown(timeout)
    if collector is not None:
        write_summary(collector, fields)


def _apply_oidc() -> None:
    token = fetch_oidc_token()
    if token is None:
        _log.warning(
            "forgesight-github: OIDC requested but no runner id-token endpoint; "
            "falling back to configured exporter auth"
        )
        return
    os.environ.setdefault(OIDC_TOKEN_ENV, token)
    _log.info("forgesight-github: obtained runner OIDC token (handed off via %s)", OIDC_TOKEN_ENV)


def _summary_enabled() -> bool:
    env = os.environ.get("FORGESIGHT_GITHUB_SUMMARY")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    if "write_summary" in _installed_config:
        return bool(_installed_config["write_summary"])
    return True


def _warn_not_ci() -> None:
    global _warned_not_ci
    if not _warned_not_ci:
        _log.warning(
            "forgesight-github: GITHUB_ACTIONS unset; bootstrap() falls back to plain configure()"
        )
        _warned_not_ci = True


def install(config: dict[str, object] | None = None) -> bool:
    """The ``forgesight.integrations`` entry point: stash config defaults for ``bootstrap()``."""
    cfg = dict(config or {})
    _installed_config.clear()
    if not cfg.get("enabled", True):
        return False
    _installed_config.update(cfg)
    return True


def _reset_for_tests() -> None:
    """Clear module state so tests don't leak the not-in-CI warning / installed config."""
    global _warned_not_ci
    _warned_not_ci = False
    _installed_config.clear()
