# Assumptions and known gaps

## Min / max cost semantics

Estimates report an **optimistic min** and **pessimistic max** for totals and plan deltas.
Budget `--on-exceed warn|fail` always gates on the **max** figure.

| Bound | Meaning |
|-------|---------|
| **min** | Optimistic: known rates only; spot/preemptible uses published floor or fixed preemptible rates when available; autoscaling node groups use `min_node_count` (else `initial_node_count`). |
| **max** | Pessimistic: spot/preemptible uses on-demand as the ceiling; autoscaling uses `max_node_count` (else initial/min); budget compare uses this bound. |

- When min and max coincide (fixed sizes, non-spot, known rates), both equal the previous single-point estimate.
- **Delta range** uses interval arithmetic: `delta_min = after_min − before_max`, `delta_max = after_max − before_min`.
- Missing planned usage (Object Storage, local SSD size) stays **0 for both** bounds — there is no configured upper bound unless you pass `--usage-file` / `--usage-metric`. Documented zero-with-warning policy is unchanged.
- Unknown / unpriced resources contribute **0** to both bounds (and appear under unknown resources).
- `--preemptible-policy reject` still marks spot resources unknown (0). `on_demand` / `floor` both report the min/max range when a floor exists; max remains on-demand for budget safety.

JSON includes `*_min` / `*_max` fields; `before_total` / `after_total` / `delta` remain aliases of the **max** values for compatibility.

## Pricing

- Rates come from public Nebius documentation pages (USD/ILS list prices, excluding tax/VAT).
- **Month** means **730 hours**, matching Nebius storage pricing units.
- **Year** means **8760 hours** (linear scale from hourly rates).
- Spot/preemptible GPU prices for many platforms are **dynamic**. Estimates use published floors for **min** and on-demand for **max** when a floor exists. `--preemptible-policy reject` marks the resource unknown; otherwise a warning is emitted.
- Catalog timestamp is in `meta.fetched_at`. Treat estimates as approximate until refreshed.
- Catalog also records `meta.sources`, `unpriced_gaps`, and documented `usage_metrics`.

## Terraform inputs

- Designed for `terraform show -json` **plan** (`resource_changes`) and **state** (`values.root_module`).
- Only `nebius/nebius` provider resources are priced; other providers are ignored.
- Plan **delta** = sum(after) − sum(before) for resources present on each side of the change.
- Nested managed boot disks on `nebius_compute_v1_instance` are included when `boot_disk.managed_disk.spec` is present.
- Separately managed disks attached only by ID are priced via the disk resource itself (avoid double-counting boot disks that also appear as standalone resources — Terraform usually nests managed boot disks).
- `nebius_mk8s_v1_node_group` / `v1alpha1` templates are priced as Compute: `template.resources` × node count + per-node boot disk.
- `nebius_ai_v1_endpoint` is priced as Compute while running (Serverless AI public pricing). Prefer `platform`+`preset` in plan/state; `gpu`/`cpu` selectors are best-effort mapped.

## Usage-based resources

| Metric | Applies to | Notes |
|--------|------------|-------|
| `stored_gib` | Object Storage buckets | Average GiB; scaled with `--period` via monthly rates |
| `class_a_requests` / `class_b_requests` | Buckets | Counts for the estimate window (not auto-scaled by period) |
| `egress_gib` | Buckets | Internet / cross-region egress GiB for the window |
| `intelligent_tier` | Buckets (`INTELLIGENT`) | `≥1` → cold tier rates |
| `node_count` | MK8s node groups | Overrides fixed/autoscaling size |
| `running_fraction` | Serverless AI endpoints | `0..1` duty cycle (default `1`); stopped endpoints → use `0` |
| `local_ssd_gib` | Instances / node templates with `local_disks` | Size is not in Terraform |

- Without `--usage-file` / `--usage-metric`, Object Storage storage/request/egress cost is **0** with a warning.
- Autoscaling node groups without `node_count` use **min** = `min_node_count` (else `initial_node_count`) and **max** = `max_node_count` (else initial/min), with a warning.

## Free / control-plane resources

- VPC networks/subnets/routes/security groups/allocations, Container Registry, KMS (preview), Tunnels (preview), IAM, DNS, MysteryBox, GPU cluster handles, NVL instance **group** handles, Object Storage inventory/transfer resources, Applications k8s releases, utility `nebius_hash`, and Managed Kubernetes **control plane** are treated as **$0** per public docs (or no separate service fee).
- Observability is free while in preview (no Terraform resource types in the provider as of inventory).
- Nodes, Serverless AI running capacity, disks, filesystems, and buckets still bill as their underlying services.

## Known unpriced / partial types

| Type / item | Notes |
|-------------|-------|
| `NETWORK_HDD` disks / filesystems | No public list price in compute pricing at catalog fetch — estimated as **unknown** (amount 0 + warning) |
| Filesystem `WEKA` / `VAST` | No public list price — **unknown** |
| Local SSD size | Priced when `local_ssd_gib` usage is set; otherwise 0 + warning if `local_disks` requested |
| GB200 / GB300 (NVL groups) | Group handle is free; no published per-GPU list price for those platforms |
| Serverless CPU selectors | Need `preset` (or platform+preset) for RAM; GPU selectors map to default platforms |
| Object Storage transfers | No separate transfer service fee; destination storage / egress still need bucket usage metrics |

## Security

- No Nebius API tokens, cloud credentials, or Terraform backend secrets are read or required.
- Do not place secrets in usage files or commit `.env` / credential material (see `.gitignore`).
