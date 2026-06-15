# Configuration Standards

How ForgeSight is configured, and how to add new config without breaking the contract.

## Layering

Config resolves **file → env → kwargs** (last wins), per feat-010:

1. **File** — `forgesight.yaml` (or `FORGESIGHT_CONFIG=path`). `${VAR}` /
   `${VAR:-default}` are interpolated from the environment.
2. **Env** — `FORGESIGHT_*` scalars overlay the file.
3. **kwargs** — explicit `configure(...)` arguments win over both.

Validation is **fail-fast at `configure()`** (architecture §8) — an unknown named
integration raises the matching `*NotRegisteredError`; a bad value raises there, never a
silent mid-run drop.

## Named integrations & entry points

Integrations resolve **by name** through the in-process registry plus entry points:

- `forgesight.exporters` — `name = "module:Class"` (or `:Class.from_config`).
- `forgesight.interceptors` — redaction, content-gate, governance.
- `forgesight.adapters` — framework adapters (config-driven auto-load via the `adapters:`
  block; never enumerate-and-instrument-all).
- `forgesight.modules` — opt-in modules (eval, registry) wired via `install()`.

`configure(exporters=["otlp", "langfuse"])` resolves names; `{name, config}` dicts or live
instances also work.

## Adding a config key (the rules)

- **Named + defaulted** (P8): every option has a sensible default; the package does nothing
  until configured (P2 — install ≠ active for opt-in modules; `enabled` defaults false).
- **Per-package env** uses the `FORGESIGHT_<AREA>_*` convention; document each key in the
  package README's config table with its env var and default.
- **Secrets** (DSNs, API keys, tokens) are read from env/config and **never logged**.
- **kwargs win over env over YAML** (FR-12) — implement env as a fallback inside the
  constructor / `from_config`, not as an override of an explicit kwarg.
- Adding a key is a minor bump behind a default; removing/renaming is major.

## References

[`AGENTS.md`](../../AGENTS.md) ·
[`forgesight_core.config`](../../packages/forgesight-core/src/forgesight_core/config.py) ·
[`docs/design/architecture.md`](../../docs/design/architecture.md)
