# Git Standards

One change = one branch = one PR. `main` is always green; everything lands via PR with
passing CI. No direct commits to `main`.

## Branches

Branch from green `main`.

| Type | Pattern | When |
|---|---|---|
| Feature | `feat-NNN` | A feature from `docs/features/` (e.g. `feat-014`) |
| Fix | `fix-<slug>` | A bug fix |
| Docs | `docs-<slug>` | Docs-only change |
| Chore | `chore-<slug>` | Tooling, CI, deps |

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <subject>`.

- **type** ∈ `feat` / `fix` / `docs` / `test` / `refactor` / `chore` / `perf` / `revert`.
- **scope** is the package or feature: `feat(langfuse): …`, `fix(core): …`.
- **subject** imperative, ≤ 72 chars, no trailing period. Reference the feature in the
  subject or body: `feat(clickhouse): columnar batch insert (feat-014)`.
- **body** explains *why* (the *what* is in the diff); wrap at 72.
- One coherent unit of work per commit; never `git commit -am` without reviewing the diff;
  never mix unrelated changes.

## Pull requests

- **Title:** `feat(<scope>): <subject> (feat-NNN)` — the squashed merge commit uses it.
- **Body** (see `.claude/checklists/pre-pr.md`): summary; the feature/spec it implements;
  design principles cited (P#); test counts; coverage; gate output.
- Open with `gh pr create`. **Wait for CI green before asking for review.**

## Merging

- **Squash-merge** to `main` with the PR title; delete the branch.
- Pull `main`, mark the spec **shipped** (in the PR / spec status), update
  `.claude/state/{current,log}.md`, pick the next feature.
- Merging to `main` is a human-authorized action — an AI assistant must have explicit
  approval to squash-merge.

## Forbidden

- Force-push to `main`; editing `main` directly; merging without green CI.
- `--no-verify` on commit (only with explicit, logged user approval).
- Mixing features in one PR — split them.
- Committing secrets — if one slips, rotate immediately.

## Multi-author commits

The user is the primary author. AI assistance is recorded via a trailer; never claim sole
authorship for AI-assisted work:

```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

## Versioning & releases

Strict [SemVer](https://semver.org). During 0.x, every minor may carry breaking changes;
patches are bug fixes only. Adding an optional field with a safe default to a locked SPI is
a minor bump; removing/renaming is major (ADR-0006). Releases follow
`.claude/checklists/pre-release.md`; `CHANGELOG.md` uses
[Keep a Changelog](https://keepachangelog.com/).

## References

- [`AGENTS.md`](../../AGENTS.md) · [`.claude/development-pipeline.md`](../../../../.claude/development-pipeline.md)
- [`.claude/checklists/pre-pr.md`](../checklists/pre-pr.md) ·
  [`.claude/checklists/pre-release.md`](../checklists/pre-release.md)
