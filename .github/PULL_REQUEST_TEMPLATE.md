## Summary

<!-- What does this change, and why? -->

## Type of change

- [ ] Bug fix
- [ ] New estimator / coverage
- [ ] Catalog / pricing update (from published docs only)
- [ ] Docs / OSS hygiene
- [ ] Tests / CI
- [ ] Other

## Checklist

- [ ] `make check` passes (unit tests + ruff)
- [ ] New/uncovered code paths have pytest coverage
- [ ] No invented prices; ASSUMPTIONS / catalog gaps updated if needed
- [ ] Both `data/prices.json` and `src/nebius_tf_cost/data/prices.json` stay in sync (if catalog touched)
- [ ] User-visible changes noted under `[Unreleased]` in `CHANGELOG.md` (release bumps: `__version__` + dated section)
- [ ] No secrets or live cloud credentials
- [ ] `make e2e` run if this touches Terraform parsing, e2e fixtures, or estimators covered by plan-only e2e

## Notes for reviewers

<!-- ASSUMPTIONS impact, min/max behavior, linked issues, etc. -->
