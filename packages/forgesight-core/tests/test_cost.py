"""Tests for the cost model: table pricing, tiers, resolution, refresh, overrides."""

from __future__ import annotations

import json

import pytest

from forgesight_api import Kind, TokenUsage
from forgesight_core import (
    PricingTable,
    TablePricingProvider,
    configure,
    reset_runtime,
    telemetry,
)


def _vendored() -> TablePricingProvider:
    return TablePricingProvider.from_vendored()


def test_basic_input_output_cost() -> None:
    cost = _vendored().price("anthropic", "claude-sonnet-4-5", TokenUsage(input=1000, output=500))
    assert cost == pytest.approx(1000 * 3e-06 + 500 * 1.5e-05)


def test_cache_token_rates() -> None:
    usage = TokenUsage(input=1000, output=0, cache_read=2000, cache_creation=500)
    cost = _vendored().price("anthropic", "claude-sonnet-4-5", usage)
    expected = 1000 * 3e-06 + 2000 * 3e-07 + 500 * 3.75e-06
    assert cost == pytest.approx(expected)


def test_tiered_pricing_above_threshold() -> None:
    usage = TokenUsage(input=300_000, output=100)
    cost = _vendored().price("anthropic", "claude-sonnet-4-5", usage)
    assert cost == pytest.approx(300_000 * 6e-06 + 100 * 2.25e-05)


def test_alias_resolution() -> None:
    # alias 'claude-sonnet-4-5-latest' → anthropic/claude-sonnet-4-5 regardless of provider arg
    cost = _vendored().price("whatever", "claude-sonnet-4-5-latest", TokenUsage(input=1000))
    assert cost == pytest.approx(1000 * 3e-06)


def test_unknown_model_returns_none() -> None:
    assert _vendored().price("acme", "made-up-model", TokenUsage(input=10)) is None


def test_reasoning_bills_at_output_rate_by_default() -> None:
    cost = _vendored().price("openai", "gpt-4o", TokenUsage(reasoning=100))
    assert cost == pytest.approx(100 * 1e-05)  # gpt-4o output rate


def test_updated_at_is_parsed() -> None:
    assert _vendored().updated_at is not None


def test_overrides_rate_and_alias() -> None:
    provider = TablePricingProvider.from_vendored(
        overrides={
            "anthropic/claude-sonnet-4-5": {"output_cost_per_token": 1.4e-05},
            "azure/my-deployment": {"alias": "openai/gpt-4o"},
        }
    )
    cost = provider.price("anthropic", "claude-sonnet-4-5", TokenUsage(output=1000))
    assert cost == pytest.approx(1000 * 1.4e-05)
    aliased = provider.price("azure", "my-deployment", TokenUsage(input=1000))
    assert aliased == pytest.approx(1000 * 2.5e-06)  # gpt-4o input rate


def test_from_url_and_refresh_forgesight_schema(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "prices.json"
    path.write_text(
        json.dumps(
            {"models": {"acme/m": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06}}}
        )
    )
    provider = TablePricingProvider.from_url(path.as_uri())
    assert provider.price("acme", "m", TokenUsage(input=10, output=5)) == pytest.approx(
        10 * 1e-06 + 5 * 2e-06
    )
    assert provider.refresh() is True


def test_from_url_parses_flat_litellm_schema(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "litellm.json"
    path.write_text(
        json.dumps(
            {
                "updated_at": "not-a-date",
                "gpt-x": {
                    "litellm_provider": "openai",
                    "input_cost_per_token": 5e-07,
                    "output_cost_per_token": 1e-06,
                },
            }
        )
    )
    provider = TablePricingProvider.from_url(path.as_uri())
    assert provider.price("openai", "gpt-x", TokenUsage(input=1000)) == pytest.approx(1000 * 5e-07)


def test_refresh_without_source_returns_false() -> None:
    assert TablePricingProvider(PricingTable()).refresh() is False


def test_refresh_bad_url_keeps_table() -> None:
    provider = TablePricingProvider.from_vendored(
        source_url="file:///nonexistent/forgesight-x.json"
    )
    assert provider.refresh() is False
    assert provider.price("anthropic", "claude-sonnet-4-5", TokenUsage(input=1000)) is not None


def test_runtime_prices_llm_from_default_table() -> None:
    exporter_records = []

    class _Sink:
        def export(self, records: object) -> object:
            from forgesight_api import ExportResult

            exporter_records.extend(records)  # type: ignore[arg-type]
            return ExportResult.SUCCESS

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

        def shutdown(self, timeout_millis: int = 30_000) -> None:
            return None

    configure(exporters=[_Sink()], sync_export=True)  # pricing defaults to the vendored table
    try:
        with (
            telemetry.agent_run("c") as run,
            run.llm_call("anthropic", "claude-sonnet-4-5") as call,
        ):
            call.record_usage(input=1000, output=500)
        llm = next(r for r in exporter_records if r.kind is Kind.LLM)
        assert llm.llm.cost_usd == pytest.approx(1000 * 3e-06 + 500 * 1.5e-05)
    finally:
        reset_runtime()
