# nebius-tf-cost

Estimate **Nebius AI Cloud** costs from Terraform **plan** or **state** JSON — no Nebius account credentials required.

Uses a vendored public price catalog (`data/prices.json`) derived from [Nebius docs](https://docs.nebius.com/llms.txt). Supports **min/max** total estimates and plan deltas, budget warn/fail gates on the **max** (pessimistic) figure, hour/month/year periods, and planned usage for usage-based resources (Object Storage, MK8s node counts, Serverless AI duty cycle, local SSD size).

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Demo

```bash
# From a terraform plan JSON:
terraform plan -out=tfplan
terraform show -json tfplan > plan.json

nebius-tf-cost estimate \
  --plan examples/sample-plan.json \
  --usage-file examples/usage.yaml \
  --period month \
  --budget 5000 \
  --on-exceed warn
```

Expected: prints before/after/delta **min and max** totals, line items, warnings for unpriced or zero-usage resources, and a budget verdict gated on **max**.

## CI-friendly test command

```bash
pip install -e ".[dev]"
python -m pytest
ruff check src tests scripts
ruff format --check src tests scripts
```

Or as a single gate:

```bash
python -m pytest && ruff check src tests scripts && ruff format --check src tests scripts
```

## Flags (summary)

| Flag | Description |
|------|-------------|
| `--plan` / `--state` / `--input` | Terraform JSON source (mutually exclusive) |
| `--period hour\|month\|year` | Totals period (`month` = 730h per Nebius docs) |
| `--currency USD\|ILS` | Catalog currency |
| `--budget AMOUNT` | Budget in `--currency` for the selected period |
| `--on-exceed warn\|fail` | Gate on **max** estimate: `warn` → exit 0; `fail` → exit 2 |
| `--budget-compare after\|delta\|abs_delta` | Which **max** figure to gate (`after_total_max`, `delta_max`, or worst `abs` delta) |
| `--usage-file PATH` | YAML/JSON planned usage |
| `--usage-metric KEY=VALUE` | Default usage overrides (repeatable) |
| `--catalog PATH` | Override vendored `prices.json` |
| `--preemptible-policy on_demand\|floor\|reject` | Spot: `on_demand` → min floor / max on-demand; `floor` → both at published floor; `reject` → unknown |
| `--json` | Machine-readable report |
| `--include-zero` | Show free/zero line items |

```bash
nebius-tf-cost estimate --help
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (budget OK, or exceed with `warn`) |
| 2 | Budget exceeded with `--on-exceed fail` |
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

## Min vs max

- **min** — optimistic (published spot floors / fixed preemptible rates; autoscaling `min_node_count`)
- **max** — pessimistic (on-demand ceiling for dynamic spot under `--preemptible-policy on_demand`; autoscaling `max_node_count`); budget uses this
- Fixed preemptible platforms (L40S) use published preemptible list rates for both bounds

See [ASSUMPTIONS.md](ASSUMPTIONS.md) for full semantics.
