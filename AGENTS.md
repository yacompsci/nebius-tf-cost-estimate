# Agent guide (nebius-tf-cost)

Guidance for coding agents and humans automating changes in this repo.

## Layout

| Path | Role |
|------|------|
| `src/nebius_tf_cost/` | Library + CLI (`cli`, `estimate`, `catalog`, `terraform`, `usage`, `budget`, …) |
| `src/nebius_tf_cost/data/prices.json` | Packaged catalog (shipped with the wheel) |
| `data/prices.json` | Same catalog at repo root — **keep in sync** with the packaged copy |
| `tests/` | Pytest suite (default excludes `@pytest.mark.e2e`) |
| `tests/e2e/` | Plan-only Terraform e2e (optional; needs `terraform` CLI) |
| `examples/` | Sample plan JSON + usage file for demos |
| `scripts/refresh_catalog.py` | Refresh / spot-snapshot helpers from public docs |
| `scripts/discover_uncovered_types.py` | Diff provider docs vs estimators / free types |
| `docs/usage-file.md` | Planned usage file format |
| `ASSUMPTIONS.md` | Pricing semantics, min/max, known gaps — **source of truth** |
| `Makefile` | `check`, `cov`, `format`, `e2e` |

Extension point for new billable types: register in `estimate.ESTIMATORS`. Free/control-plane types go in catalog `free_resource_types`.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

make check          # pytest (non-e2e) + ruff
make cov
make format
make e2e            # pytest -m e2e (requires terraform; no cloud secrets)
```

Default pytest config uses `-m "not e2e"`. E2E is opt-in via `make e2e` or `python -m pytest -m e2e -o addopts=-q`.

## Catalog and assumptions

- Rates come from **published Nebius documentation** (and documented spot floors / fixed preemptible tables). See `ASSUMPTIONS.md` and catalog `meta.sources`.
- **Never invent prices** for platforms or SKUs absent from those tables. Leave resources unknown / unpriced / free as documented; record gaps in `ASSUMPTIONS.md` / catalog `unpriced_gaps`.
- Month = **730h**, year = **8760h**. Budget gates use the **max** (pessimistic) bound.
- After catalog edits, sync **both** JSON paths and re-run tests. Prefer `python scripts/refresh_catalog.py` when refreshing from docs.
- Spot: do not vendor live SpotPriceService quotes as floors; use published floors / fixed preemptible rows. `--spot-snapshot` is for comparison only.

## Tests-first for uncovered code

When adding estimators, CLI flags, parsers, usage metrics, or catalog fields:

1. Add or extend **pytest** coverage in the same change (fixtures under `tests/fixtures/` as needed).
2. Keep default CI/`make check` offline and credential-free.
3. Mark true Terraform plan e2e with `@pytest.mark.e2e` under `tests/e2e/`; never `terraform apply`; use provider `no_credentials` / plan-only flows only.
4. Do not weaken lint (`ruff check` / `ruff format --check` on `src tests scripts`).

## Out of scope for opportunistic edits

- Do not mix large unrelated refactors with pricing/coverage PRs.
- Do not commit credentials, real `parent_id` values that look like production accounts, or private tokens.
- Do not “fix” unknown costs by guessing rates.
