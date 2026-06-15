# Testing Standards

The gate is non-negotiable and mirrors CI exactly. `main` stays green.

## The gate (run before every commit / PR)

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy packages/*/src
uv run pytest                       # coverage ≥ 90% on the configured source_pkgs
```

`pre-commit` runs all four; `uv run pre-commit install` wires it to `git commit`. Never
bypass with `--no-verify` without explicit, logged approval.

## Coverage

- **≥ 90%** overall (enforced via `--cov-fail-under=90`). New packages add their import
  root to the root `pyproject.toml` `[tool.coverage.run] source_pkgs`.
- Aim for a new package to be ~100% on its own files; the only acceptable misses are
  vendor-edge lines marked `# pragma: no cover` (live-backend-only) and trivial defensive
  branches.

## What to test

- **Unit** — the pure logic: record→attribute/column/span mapping per `Kind`, config
  parsing/validation, math (cost, budgets, rollups), error paths.
- **Conformance** — every SPI implementation runs its suite from
  `forgesight_core.testing.conformance` (`run_exporter_conformance`,
  `run_interceptor_conformance`, `run_event_listener_conformance`,
  `run_pricing_conformance`, `run_adapter_conformance`). This is how cross-implementation
  comparability is *enforced*, not assumed.
- **Integration** — drive through the runtime: `configure(exporters=[InMemoryExporter()],
  sync_export=True)`, run a scope, assert the exported records / span tree.
- **End-to-end** where a feature spans the runtime (e.g. governance trips halting a run and
  still flushing the run record; FastAPI via Starlette `TestClient`).

## Patterns & gotchas

- Use `sync_export=True` for deterministic record assertions; otherwise `force_flush()`
  before asserting.
- `InMemoryExporter.shutdown()` **clears** its records (and `reset_runtime()` shuts it
  down) — assert records *before* the teardown, or use a non-clearing exporter double.
- For a backend that needs a live target, inject a test double (e.g.
  `InMemoryClickHouseClient`, a fake event bus / span writer) and keep the real vendor call
  on a `pragma: no cover` edge. Prefer the OTLP transport where a feature can ride it.
- Tests must not depend on ambient env — scrub provider env vars (`GITHUB_*`, etc.) in a
  fixture so they pass identically locally and in CI.
- `pytest` runs with `--import-mode=importlib` (duplicate `test_exporter.py` basenames are
  fine).

## References

[`forgesight_core.testing.conformance`](../../packages/forgesight-core/src/forgesight_core/testing/conformance.py)
· [`.claude/standards/coding.md`](./coding.md) · [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
