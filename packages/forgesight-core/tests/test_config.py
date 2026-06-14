"""Tests for configuration: zero-config, precedence, ${ENV}, name resolution, fail-fast."""

from __future__ import annotations

import pytest

from forgesight_api import ExporterNotRegisteredError
from forgesight_core import (
    ConsoleExporter,
    InMemoryExporter,
    TablePricingProvider,
    configure,
    register,
    reset_runtime,
)
from forgesight_core.config import interpolate, load_settings, resolve


def test_zero_config_defaults() -> None:
    rt = configure()
    try:
        assert isinstance(rt.exporters[0], ConsoleExporter)
        assert isinstance(rt.pricing, TablePricingProvider)
        assert rt.config.capture_content is False
    finally:
        reset_runtime()


def test_resolve_builtin_exporter_by_name() -> None:
    rt = configure(exporters=["in-memory"])
    try:
        assert isinstance(rt.exporters[0], InMemoryExporter)
    finally:
        reset_runtime()


def test_unknown_exporter_fails_fast() -> None:
    with pytest.raises(ExporterNotRegisteredError, match=r"forgesight\.exporters"):
        configure(exporters=["no-such-exporter-xyz"])  # not registered anywhere
    reset_runtime()


def test_register_in_process_then_resolve() -> None:
    @register("exporters", "my-test-sink")
    class _Sink(InMemoryExporter):
        pass

    rt = configure(exporters=["my-test-sink"])
    try:
        assert isinstance(rt.exporters[0], _Sink)
    finally:
        reset_runtime()


def test_interpolation() -> None:
    env = {"WEBHOOK": "https://hooks/x"}
    assert interpolate("${WEBHOOK}", env) == "https://hooks/x"
    assert interpolate("${MISSING:-fallback}", env) == "fallback"
    assert interpolate({"k": ["${WEBHOOK}"]}, env) == {"k": ["https://hooks/x"]}
    with pytest.raises(ValueError, match="not set"):
        interpolate("${REQUIRED}", env)


def test_yaml_file_layer(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OTLP", "http://collector:4317")
    cfg = tmp_path / "forgesight.yaml"
    cfg.write_text(
        "service_name: from-file\n"
        "exporters: [in-memory]\n"
        "sample_rate: 0.5\n"
        "exporter_config:\n"
        "  in-memory: {}\n"
    )
    settings = load_settings(str(cfg))
    assert settings["service_name"] == "from-file"
    assert settings["exporters"] == ["in-memory"]
    assert settings["sample_rate"] == 0.5


def test_env_overrides_file(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cfg = tmp_path / "fs.yaml"
    cfg.write_text("service_name: file-name\nsample_rate: 1.0\n")
    monkeypatch.setenv("FORGESIGHT_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("FORGESIGHT_SERVICE_NAME", "env-name")
    settings = load_settings(str(cfg))
    assert settings["service_name"] == "env-name"  # env overrides file
    assert settings["sample_rate"] == 0.25


def test_kwargs_win_over_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = tmp_path / "fs.yaml"
    cfg.write_text("service_name: file-name\nsample_rate: 0.5\n")
    rt = configure(config_file=str(cfg), service_name="kwarg-name", sample_rate=0.9)
    try:
        assert rt.config.service_name == "kwarg-name"
        assert rt.config.sample_rate == 0.9
    finally:
        reset_runtime()


def test_file_drives_exporters_and_pricing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = tmp_path / "fs.yaml"
    cfg.write_text("exporters: [in-memory]\npricing: default\n")
    rt = configure(config_file=str(cfg))
    try:
        assert isinstance(rt.exporters[0], InMemoryExporter)
        assert isinstance(rt.pricing, TablePricingProvider)
    finally:
        reset_runtime()


def test_resolve_helper_raises_for_unknown_group_name() -> None:
    with pytest.raises(ExporterNotRegisteredError):
        resolve("exporters", "definitely-not-installed")
