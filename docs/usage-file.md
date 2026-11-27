# Planned usage file (`--usage-file`)

Terraform does not encode how much Object Storage you will store, how many API requests you will make, egress volume, autoscaled node count, Serverless duty cycle, or local SSD size. The usage file supplies those **planned** metrics so the estimator can price usage-based costs for your chosen period (`hour` / `month` / `year`).

Pass it as:

```bash
nebius-tf-cost estimate --plan plan.json --usage-file usage.yaml --period month
```

YAML or JSON. Same shape. Overrides from the CLI (`--usage-metric KEY=VALUE`, repeatable) merge into **`defaults`** (CLI wins on key clash).

## File shape

```yaml
defaults:          # applied to every resource if that metric is missing on the resource
  stored_gib: 0

resources:         # keyed by full Terraform address (preferred)
  nebius_storage_v1_bucket.datasets:
    stored_gib: 1024
    class_a_requests: 10000
    class_b_requests: 50000
    egress_gib: 100

by_name:           # optional; match by resource name only (e.g. "datasets")
  datasets:
    stored_gib: 512
```

Lookup order for a resource (first hit wins per metric): **`resources.<address>`** → **`by_name.<name>`** → **`defaults`** → estimator built-in default (usually `0`).

Addresses must match Terraform addresses in plan/state JSON (e.g. `nebius_storage_v1_bucket.datasets`, `module.foo.nebius_mk8s_v1_node_group.workers`).

## Metrics

| Metric | Used for | Meaning | Unit / range |
|--------|----------|---------|--------------|
| `stored_gib` | Object Storage buckets | Average stored size over the **estimate window** | GiB |
| `class_a_requests` | Object Storage | PUT/POST/DELETE count in the window | count |
| `class_b_requests` | Object Storage | GET/HEAD count in the window | count |
| `egress_gib` | Object Storage | Egress to internet / other region in the window | GiB |
| `intelligent_tier` | Object Storage | If ≥ 1, use Intelligent **cold** storage rate | ≥ 1 selects cold |
| `node_count` | MK8s node groups | Override node count (esp. autoscaling) | nodes |
| `running_fraction` | Serverless AI endpoints | Fraction of the period the endpoint is running | `0`–`1` (default `1`) |
| `local_ssd_gib` | Compute instances with local disks | Local SSD size (not in Terraform) | GiB |

Request and egress counts are for the **same window** as `--period` (they are not auto-scaled from hour→month). Storage GiB-month rates are scaled by period via the catalog’s month/year hour conventions.

## Behavior without a usage file

- Object Storage **storage / requests / egress** cost **0**, with a warning to pass `--usage-file` or `--usage-metric`.
- Local SSD with `local_disks` but no `local_ssd_gib`: warned / unpriced for that component.
- MK8s without `node_count`: uses Terraform `fixed_node_count` / autoscaling min–max for min/max estimates.
- Serverless without `running_fraction`: assumes fully running (`1`).
- With `--strict-check`, missing planned usage for resources that cannot be estimated without it (Object Storage metrics, local SSD size, node count assumed 0) causes a non-zero exit.

## CLI-only defaults

```bash
nebius-tf-cost estimate --plan plan.json \
  --usage-metric stored_gib=100 \
  --usage-metric egress_gib=10
```

These set **defaults** for all resources. Prefer `--usage-file` when different buckets or groups need different numbers.

## Example

See [`examples/usage.yaml`](../examples/usage.yaml) in the repo.

## Tips

1. Align request/egress totals with the period you pass (`--period month` → monthly planned counts).
2. Prefer full addresses under `resources:` when names collide across modules.
3. For autoscaling node groups, set `node_count` for a point estimate, or rely on min/max node counts in Terraform for the estimate’s min/max band.
4. Refresh usage as your capacity plan changes; the catalog prices the metrics, it does not infer them.

For **quote-only catalog rates** (VAST, GB200/GB300, `NETWORK_HDD`), see [catalog-price.md](catalog-price.md) (`--catalog-price`) — that flag fills missing **prices**, not planned usage volumes.
