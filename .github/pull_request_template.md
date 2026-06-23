## Summary

<one-paragraph description of what this PR does and why>

## Tests added

- Unit: <count>
- Integration: <count>
- Conformance (if an SPI is touched): <count>
- Coverage on diff: <pct>% (gate is 90%)

## Local gate output

```
✅ ruff format
✅ ruff check
✅ mypy --strict
✅ pytest (coverage ≥ 90%, py3.11–3.13)
✅ gitleaks (no secrets)
```

## Checklist

- [ ] Branch follows convention: `feat-NNN`, `fix-<slug>`, `docs-<slug>`, or `chore-<slug>`
- [ ] One feature / fix / chore — not mixed
- [ ] Conventional Commits on every commit (`feat:` / `fix:` / `docs:` / `test:` /
      `refactor:` / `chore:` / `perf:` / `revert:`), subject imperative & ≤ 72 chars
- [ ] Vendor-neutral core preserved (no backend/model-provider SDK in `-api` / `-core`)
- [ ] New/changed SPI has a conformance test
- [ ] `AGENTS.md` updated if conventions changed
- [ ] `CHANGELOG.md` entry added under `[Unreleased]`
- [ ] AI-assisted commits carry a `Co-Authored-By:` trailer
