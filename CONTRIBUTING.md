# Contributing

Thanks for contributing to **nebius-tf-cost**. This tool estimates Nebius AI Cloud costs from Terraform plan/state JSON using a **vendored public price catalog** — it does not call Nebius billing APIs and needs **no cloud credentials**.

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and [ASSUMPTIONS.md](ASSUMPTIONS.md) before opening a PR that changes pricing or coverage. Coding agents: see [AGENTS.md](AGENTS.md).

## Setup

Requires **Python 3.10+**.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Lint and test

```bash
make check          # pytest (excludes e2e) + ruff check/format
make cov            # pytest with coverage
make format         # auto-fix then re-check
```

Equivalent without Make:

```bash
python -m pytest
ruff check src tests scripts
ruff format --check src tests scripts
```

Default pytest config uses `-m "not e2e"` so the unit suite stays offline and fast. CI runs lint + unit tests on every PR (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

### Terraform plan-only e2e

Marked with `@pytest.mark.e2e` under `tests/e2e/`. Requires the `terraform` CLI on `PATH`; uses provider `no_credentials` / plan-only fixtures — **never apply**, **no cloud secrets**.

```bash
make e2e
# or: python -m pytest -m e2e -o addopts=-q
```

CI job `e2e-terraform` runs the same command. Local runs skip when `terraform` is missing. See [tests/e2e/terraform/README.md](tests/e2e/terraform/README.md).

## Pull request norms

1. **Branch from latest `main`.** Keep focused branches (avoid mixing unrelated concerns).
2. **Tests first for uncovered code.** New estimators, catalog fields, CLI flags, or parsing paths need pytest coverage in the same PR (docs-only changes excepted).
3. **Do not invent prices.** Catalog rates must come from published Nebius docs (or documented floors / fixed preemptible tables). Unpriced types stay unknown / free per [ASSUMPTIONS.md](ASSUMPTIONS.md); record gaps instead of guessing.
4. **Keep both catalog copies in sync** when editing prices: `data/prices.json` and `src/nebius_tf_cost/data/prices.json`. Prefer `python scripts/refresh_catalog.py` when refreshing from docs.
5. **No secrets.** Do not commit credentials, tokens, or real account IDs. Unit tests, e2e, and CI must run without Nebius credentials.
6. **Lint clean.** `make check` (or the CI gate) must pass. Run `make e2e` when changing Terraform parsing, estimators exercised by the e2e fixture, or e2e fixtures themselves.
7. **Describe the change.** Link issues, note ASSUMPTIONS impacts, and mention min/max or budget behavior when relevant.
8. **Small, reviewable PRs** preferred over mixed mega-diffs.

## Pricing and coverage changes

- Document semantic changes in [ASSUMPTIONS.md](ASSUMPTIONS.md).
- Usage-based metrics: update [docs/usage-file.md](docs/usage-file.md) and examples when adding keys.
- New provider types: register estimators in `estimate.ESTIMATORS` (and/or `free_resource_types` in the catalog). The scheduled workflow [`.github/workflows/discover-uncovered-types.yml`](.github/workflows/discover-uncovered-types.yml) opens coverage issues for newly uncovered types — those issues are requests only; they do not invent prices.

## Security reports

See [SECURITY.md](SECURITY.md).
