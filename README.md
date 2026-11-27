# nebius-tf-cost

[![CI](https://github.com/yacompsci/nebius-tf-cost-estimate/actions/workflows/ci.yml/badge.svg)](https://github.com/yacompsci/nebius-tf-cost-estimate/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Estimate **Nebius AI Cloud** costs from Terraform **plan** or **state** JSON — no Nebius account credentials required.

Uses a vendored public price catalog (`data/prices.json`) derived from [Nebius docs](https://docs.nebius.com/llms.txt). Supports **min/max** total estimates and plan deltas, optional `--strict-check` gating on the **max** (pessimistic) figure / missing usage / unknown types, hour/month/year periods, and planned usage for usage-based resources (Object Storage, MK8s node counts, Serverless AI duty cycle, local SSD size).

**Version:** SemVer **1.0.0** (stable CLI for CI). See [CHANGELOG.md](CHANGELOG.md); bump process in [CONTRIBUTING.md](CONTRIBUTING.md). `nebius-tf-cost --version` reports the installed package version.

## Install

```bash
git clone https://github.com/yacompsci/nebius-tf-cost-estimate.git
cd nebius-tf-cost-estimate
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Demo

Uses the committed sample plan and usage file (no Terraform required):

```bash
nebius-tf-cost estimate \
  --plan examples/sample-plan.json \
  --usage-file examples/usage.yaml \
  --period month \
  --budget 5000
```

Expected: prints before/after/delta **min and max** totals, line items, warnings for unpriced or zero-usage resources, and a budget verdict gated on **max**. Add `--strict-check` in CI to fail on budget exceed, missing planned usage, or unknown/unpriced resources.

More copy-paste pairs: [examples/usage/README.md](examples/usage/README.md).

To estimate **your** stack, export plan JSON and pass that same path:

```bash
terraform plan -out=tfplan
terraform show -json tfplan > plan.json
nebius-tf-cost estimate --plan plan.json --usage-file examples/usage.yaml --period month
```

## CI-friendly test command

```bash
pip install -e ".[dev]"
python -m pytest
ruff check src tests scripts
ruff format --check src tests scripts
```

Or via Make:

```bash
make check          # pytest (excludes e2e) + ruff
make cov            # pytest with coverage (pytest-cov in [dev])
make e2e            # plan-only Terraform e2e (@pytest.mark.e2e)
```

Or as a single gate:

```bash
python -m pytest && ruff check src tests scripts && ruff format --check src tests scripts
```

Default `pytest` excludes `@pytest.mark.e2e` (offline/fast). Plan-only Terraform e2e (real `init`/`plan`/`show -json` → estimate; **no** Nebius credentials, **never** `apply`) lives under `tests/e2e/terraform/`:

```bash
# requires terraform on PATH; skips locally if missing
make e2e
# or: python -m pytest -m e2e -o addopts=-q
```

## Flags (summary)

| Flag | Description |
|------|-------------|
| `--plan` / `--state` / `--input` | Terraform JSON source (mutually exclusive) |
| `--period hour\|month\|year` | Totals period (`month` = 730h per Nebius docs) |
| `--currency USD\|ILS` | Catalog currency |
| `--budget AMOUNT` | Budget in `--currency` for the selected period |
| `--strict-check` | Fail (exit 2) if max exceeds `--budget`, planned usage is missing, or unknown/unpriced resources are present; default is warn + exit 0 |
| `--budget-compare after\|delta\|abs_delta` | Which **max** figure to gate (`after_total_max`, `delta_max`, or worst `abs` delta) |
| `--usage-file PATH` | YAML/JSON planned usage |
| `--usage-metric KEY=VALUE` | Default usage overrides (repeatable) |
| `--catalog-price KEY=VALUE` | Quote / missing catalog rates (repeatable); see [docs/catalog-price.md](docs/catalog-price.md) |
| `--catalog PATH` | Override vendored `prices.json` |
| `--preemptible-policy on_demand\|floor\|reject` | Spot: `on_demand` → min floor / max on-demand; `floor` → both at published floor; `reject` → unknown |
| `--json` | Machine-readable report |
| `--include-zero` | Show free/zero line items |

```bash
nebius-tf-cost estimate --help
```

## Planned usage

Object Storage, MK8s node counts, Serverless AI duty cycle, and local SSD size are not fully encoded in Terraform. Pass planned metrics with `--usage-file` / `--usage-metric`.

See **[docs/usage-file.md](docs/usage-file.md)** for the file shape, metric table, and lookup order.

Copy-paste patterns (clear missing-usage warnings without tribal knowledge):

| Example | Covers |
|---------|--------|
| [examples/usage.yaml](examples/usage.yaml) | Short Object Storage demo for `examples/sample-plan.json` |
| [examples/usage/gpu-nodes.yaml](examples/usage/gpu-nodes.yaml) | GPU node counts / Serverless utilization / local SSD |
| [examples/usage/object-storage-egress.yaml](examples/usage/object-storage-egress.yaml) | Storage + requests + egress |
| [examples/usage/preemptible-mix.yaml](examples/usage/preemptible-mix.yaml) | On-demand + spot fleet overlays + `--preemptible-policy` |

Index: [examples/usage/README.md](examples/usage/README.md).

## Quote-only / missing catalog rates

GB200/GB300 platforms, VAST, and `NETWORK_HDD` have no public list price in the vendored catalog. Supply a private rate with `--catalog-price` (same `KEY=VALUE` repeatable style as `--usage-metric`):

```bash
nebius-tf-cost estimate --plan plan.json \
  --catalog-price VAST=0.12 \
  --catalog-price platform.GB200=25 \
  --catalog-price disk.NETWORK_HDD=0.05
```

See **[docs/catalog-price.md](docs/catalog-price.md)** for key vocabulary, units, and override-vs-public-rate behavior. WEKA USD ($0.10/GiB·month) is vendored from [nebius.com/prices](https://nebius.com/prices).

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (warnings only unless `--strict-check`) |
| 2 | `--strict-check` failed (budget exceeded, missing planned usage, or unknown resources) |
| 64 | Bad flags / missing input paths |
| 65 | Invalid plan/state/catalog/usage data |
| 70 | Unexpected internal error |

## Module layout

| Module | Responsibility |
|--------|----------------|
| `cli.py` | argparse, formatting, exit codes |
| `config.py` | Typed `EstimateConfig` / `PlannedUsage` |
| `catalog.py` | Load vendored prices |
| `terraform.py` | Parse plan/state JSON |
| `estimate.py` | Resource estimators + totals/delta |
| `budget.py` | Budget gate |
| `usage.py` | Planned usage file/flags |
| `periods.py` | hour/month/year scaling |
| `errors.py` | Structured errors + exit codes |

Extension point: register a new estimator in `estimate.ESTIMATORS` for additional `nebius_*` resource types.

Covered estimators include Compute instances/disks/snapshots/filesystems, Object Storage buckets, VPC allocations, Managed PostgreSQL, Managed MLflow, **MK8s node groups**, and **Serverless AI endpoints**. Free/control-plane types are listed in the catalog `free_resource_types`.

## Refreshing prices

```bash
python scripts/refresh_catalog.py
python scripts/refresh_catalog.py --spot-snapshot   # compare live public spot API to catalog floors
```

Edit `data/prices.json` (and mirror `src/nebius_tf_cost/data/prices.json`) from the published docs floors / fixed preemptible rows — do not invent rates for platforms absent from those tables. Re-run tests. Do not commit credentials — this tool never needs them (the spot snapshot API is public and unauthenticated).

## Discovering new provider types

Weekly GitHub Actions workflow [`.github/workflows/discover-uncovered-types.yml`](.github/workflows/discover-uncovered-types.yml) (also `workflow_dispatch`) inventories Nebius Terraform provider resource types from the public [provider docs tree](https://github.com/nebius/terraform-provider-nebius/tree/main/docs/resources) (GitHub Contents API — no secrets), diffs them against `estimate.ESTIMATORS` and catalog `free_resource_types`, and opens or updates GitHub issues for newly uncovered types.

- Issue title convention: `feature: cover nebius_<type>` (deduped by that title / type name among existing issues)
- Label: `feature` (created if missing)
- Issues are coverage feature requests only — they do **not** invent prices

```bash
python scripts/discover_uncovered_types.py --dry-run --repo yacompsci/nebius-tf-cost-estimate
```

## Min vs max

- **min** — optimistic (published spot floors / fixed preemptible rates; autoscaling `min_node_count`)
- **max** — pessimistic (on-demand ceiling for dynamic spot under `--preemptible-policy on_demand`; autoscaling `max_node_count`); budget uses this
- Fixed preemptible platforms (L40S) use published preemptible list rates for both bounds

See [ASSUMPTIONS.md](ASSUMPTIONS.md) for full semantics.

## Contributing

Contributions are welcome. See:

- [CONTRIBUTING.md](CONTRIBUTING.md) — setup, lint/test, PR norms, e2e, **versioning / releases**
- [CHANGELOG.md](CHANGELOG.md) — Keep a Changelog release notes
- [AGENTS.md](AGENTS.md) — layout and rules for coding agents
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md) — vulnerability reporting (no cloud secrets required to use or test this tool)

Licensed under the [Apache License 2.0](LICENSE) (matches `pyproject.toml`).
