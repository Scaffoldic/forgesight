# Pre-PR checklist

Before `gh pr create`, verify the branch is PR-ready.

## The gate (must be green)

- [ ] `uv run ruff format --check .`
- [ ] `uv run ruff check .`
- [ ] `uv run mypy packages/*/src`
- [ ] `uv run pytest` — all pass, coverage ≥ 90%.
- [ ] (New package) it's in the root `pyproject.toml` `[tool.coverage.run] source_pkgs`,
      and its wheel builds (`uv build --wheel`) including `py.typed` + any data files.

## Branch state

- [ ] Commits follow Conventional Commits; no unrelated changes; no WIP/noise commits.
- [ ] Branch pushed: `git push -u origin feat-NNN`.

## Cleanliness

- [ ] No stray `print()`, debug code, or commented-out blocks.
- [ ] No owner-less `TODO` / `FIXME`.
- [ ] New dependencies are declared in the package `pyproject.toml` and justified;
      vendor SDKs are **not** on `forgesight-core` / `-api`.

## Conformance & contracts

- [ ] Any new SPI implementation passes its conformance suite.
- [ ] No breaking change to a locked SPI / domain type (or it's a deliberate major bump,
      called out).
- [ ] `export()` never raises; secrets are never logged; content stays opt-in (P6/P7).

## Documentation

- [ ] Feature spec status updated (shipped, pending merge).
- [ ] Package `README.md` reflects the shipped API (no future-tense caveats).
- [ ] `CHANGELOG.md` entry added under `[Unreleased]`.
- [ ] `AGENTS.md` updated if conventions changed.
- [ ] Forward references swept (`git grep -n feat-NNN docs/`).

## State files

- [ ] `.claude/state/current.md` reflects the branch + feature.
- [ ] `.claude/state/log.md` has milestone entries (start, impl done, tests green, PR).

## PR body

- [ ] **Summary** — one paragraph.
- [ ] **Feature reference** — the `feat-NNN` it implements.
- [ ] **Design principles** cited (which P#, which ADRs).
- [ ] **Test counts** + **coverage**.
- [ ] **Gate output** confirmed green.

## After raising

- [ ] **Wait for CI to go green** before asking for review.
- [ ] `.claude/state/log.md` gets the PR URL.
- [ ] Merge (squash) only with explicit human approval; then delete branch, pull main,
      mark shipped, update state, pick next.
