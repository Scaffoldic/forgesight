# Pre-release checklist

Run end-to-end before tagging `vX.Y.Z`. This is the one place the cross-cutting drift checks
(CHANGELOG ↔ spec status ↔ README ↔ state) are enforced in a single pass.

## Pre-conditions

- [ ] `main` is green (CI passing on 3.11 / 3.12 / 3.13).
- [ ] Working tree clean; on a `chore-release-vX.Y.Z` branch off `main`.

## Versions

- [ ] Every workspace package's `pyproject.toml` `version` bumped to `X.Y.Z` in lockstep.
- [ ] `__version__` in each package matches.
- [ ] SemVer respected: any locked-SPI removal/rename ⇒ major; additive-with-default ⇒ minor.

## The gate

- [ ] `uv sync --all-packages`
- [ ] `uv run ruff format --check . && uv run ruff check .`
- [ ] `uv run mypy packages/*/src`
- [ ] `uv run pytest` — green, coverage ≥ 90%.
- [ ] `uv build` builds every package wheel cleanly (each includes `py.typed`).

## Docs ↔ reality drift checks

- [ ] `CHANGELOG.md`: `[Unreleased]` renamed to `[X.Y.Z] — YYYY-MM-DD`; a fresh empty
      `[Unreleased]` added; entries match what actually shipped.
- [ ] Every feature shipped this train has spec status = shipped.
- [ ] Root `README.md` package list / capability table matches the shipped packages.
- [ ] `docs/features/README.md` catalogue status column is current.
- [ ] No dangling cross-references (`docs/`, package READMEs).

## Publish

- [ ] PR the release branch through the normal gate; squash-merge.
- [ ] Pull `main`; annotated tag: `git tag -a vX.Y.Z -m "vX.Y.Z"`; push the tag.
- [ ] `gh release create vX.Y.Z` with notes (highlights + the per-package version table).
- [ ] Publish packages (PyPI) if applicable.
- [ ] `.claude/state/{current,log}.md` updated with the release.
