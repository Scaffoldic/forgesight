# Contributing to ForgeSight

Thanks for your interest in ForgeSight — the vendor-neutral, OpenTelemetry-first
telemetry SDK for AI agents. Contributions (issues, docs, fixes, new integration
packages) are very welcome.

This project is **self-contained**: every requirement, design doc, ADR, feature spec,
standard, and checklist lives inside this repo. You don't need anything outside it to
contribute. By participating you agree to the [Code of Conduct](./CODE_OF_CONDUCT.md).

---

## TL;DR

```bash
# 1. Fork + clone, then set up the uv workspace
uv sync --all-packages

# 2. (optional) install the local gate so commits run it automatically
uv run pre-commit install

# 3. Branch from main
git switch -c feat-NNN main          # or fix-… / docs-… (see "Branches" below)

# 4. Make your change with tests, then run the full gate
uv run ruff format . && uv run ruff check . && uv run mypy packages/*/src && uv run pytest

# 5. Push and open one PR (Conventional Commits title)
gh pr create
```

`main` is always green. Everything lands through a PR with passing CI.

---

## Project layout

ForgeSight is a [uv](https://docs.astral.sh/uv/) workspace with a three-tier model
(ADR-0002):

- `packages/forgesight-api/` — **locked contracts**: the domain model
  (`AgentRun` / `WorkflowRun` / `Step` / `LLMCall` / `ToolCall` / `MCPCall`) and the four
  `Protocol` SPIs (`TelemetryExporter` / `Interceptor` / `EventListener` /
  `PricingProvider`). **No I/O, no third-party SDKs.**
- `packages/forgesight-core/` — the runtime: context propagation, span tree, export
  pipeline, metrics, cost, events, interceptors, config, adapters, governance hooks.
  Depends on `-api` + the OTel **API** only; never a vendor SDK.
- `packages/forgesight/` — the batteries-included facade most users install.
- `packages/forgesight-*` — every backend / integration is its own package wrapping
  exactly **one** target. Never added to core.

Read **[`AGENTS.md`](./AGENTS.md)** first — it's the canonical conventions doc (hard rules,
anti-patterns, reading order, branch/PR rules). Deeper material:

- `docs/requirements.md`, `docs/design/`, `docs/adr/` — the why and the how.
- `docs/features/` — the `feat-NNN` specs (catalogue: `docs/features/README.md`).
- `.claude/standards/` and `.claude/checklists/` — the coding/testing/git/docs standards
  and the per-milestone gates (also used by AI assistants).

---

## The non-negotiables

These are hard rules (full list in `AGENTS.md`); a PR that breaks one will be sent back:

1. **Vendor-neutral core.** No backend or model-provider SDK in `forgesight-api` or
   `forgesight-core`. Vendor deps live only in their own integration package (P1).
2. **OpenTelemetry first.** Map onto the GenAI semantic conventions; don't invent
   attributes where a convention exists.
3. **Export never raises.** `export()` returns `ExportResult.FAILURE`, never throws — a
   backend outage must be invisible to the agent (P6).
4. **Secure by default.** Content capture is opt-in; the redaction interceptor runs before
   export (P7).
5. **Stable SPIs.** Adding an optional field with a safe default is a minor bump; removing
   or renaming a field is major. Every SPI has a conformance suite.
6. **Quality gate.** `mypy --strict` clean, coverage ≥ 90%, ruff format + check clean —
   the same gate CI runs.

---

## Development workflow

ForgeSight follows a per-feature pipeline (see `.claude/development-pipeline.md`):

1. **Branch** from green `main` per change.
2. **Implement** with unit + (where relevant) integration + conformance tests.
3. **Gate**: `ruff format` → `ruff check` → `mypy --strict packages/*/src` → `pytest`
   (coverage ≥ 90% on Python 3.11–3.13). `pre-commit` mirrors this exactly.
4. **PR** with the body filled in (summary, the feature/spec it touches, design principles
   cited, test counts, coverage).
5. **CI green**, then **squash-merge**, delete the branch, pull main.

Run the **[`pre-pr` checklist](./.claude/checklists/pre-pr.md)** before opening a PR.

### Branches

| Type | Pattern | When |
|---|---|---|
| Feature | `feat-NNN` | A feature from `docs/features/` |
| Fix | `fix-<slug>` | A bug fix |
| Docs | `docs-<slug>` | Docs-only change |
| Chore | `chore-<slug>` | Tooling, CI, deps |

### Commits

[**Conventional Commits**](https://www.conventionalcommits.org/):
`feat(<scope>): <subject>`, e.g. `feat(langfuse): add observation mapping (feat-013)`.
Subject is imperative and ≤ 72 chars; the body explains *why*. AI-assisted commits add a
`Co-Authored-By:` trailer — never claim sole authorship for AI-assisted work.

---

## Adding a new integration package

The most common contribution. To add `forgesight-<backend>`:

1. Create `packages/forgesight-<backend>/` with `src/forgesight_<backend>/`,
   `pyproject.toml` (dep on `forgesight-core` + the one vendor SDK), `py.typed`, a README,
   and `tests/`.
2. Register the entry point — exporters under `forgesight.exporters`, interceptors under
   `forgesight.interceptors`, adapters under `forgesight.adapters`, modules under
   `forgesight.modules`.
3. Implement the relevant `Protocol` and **pass its conformance suite**
   (`forgesight_core.testing.conformance`).
4. Add the package's import root to the root `pyproject.toml`
   `[tool.coverage.run] source_pkgs`.
5. Keep the vendor SDK **out of core** — import it lazily inside the package; if it's a
   heavy tree, prefer a peer/optional dependency and test the mapping with a double.

See any existing integration (`forgesight-langfuse`, `-clickhouse`, `-mcp`) as a template.

---

## Reporting bugs & requesting features

- **Bugs / features:** open a GitHub issue with a minimal repro and your environment
  (Python version, OS, which packages).
- **Security vulnerabilities:** do **not** open a public issue — use GitHub private
  advisories ([SECURITY.md](./SECURITY.md)).

---

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](./LICENSE).
