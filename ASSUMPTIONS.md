# Assumptions and known gaps

## Min / max cost semantics

Estimates report an **optimistic min** and **pessimistic max** for totals and plan deltas.
Budget gating (and `--strict-check`) always compares against the **max** figure.

| Bound | Meaning |
|-------|---------|
| **min** | Optimistic: known rates only; dynamic spot uses published floor (`From $X`); fixed preemptible (L40S) uses exact published preemptible component rates; autoscaling node groups use `min_node_count` (else `initial_node_count`). |
| **max** | Pessimistic: dynamic spot uses on-demand as a conventional ceiling under `--preemptible-policy on_demand`; fixed preemptible uses the same published preemptible rates as min; autoscaling uses `max_node_count` (else initial/min); budget compare uses this bound. |

- When min and max coincide (fixed sizes, non-spot, fixed preemptible, or `--preemptible-policy floor`), both equal the point estimate.
- **Delta range** uses interval arithmetic: `delta_min = after_min − before_max`, `delta_max = after_max − before_min`.
- Missing planned usage (Object Storage, local SSD size) stays **0 for both** bounds — there is no configured upper bound unless you pass `--usage-file` / `--usage-metric`. Default is zero-with-warning; `--strict-check` fails instead.
- Unknown / unpriced resources contribute **0** to both bounds (and appear under unknown resources). `--strict-check` fails when any are present.
- `--preemptible-policy` for spot/preemptible GPU resources:
  - `on_demand` (default): min = published floor / fixed preemptible; max = on-demand for **dynamic** floors (budget uses max). Fixed preemptible platforms keep min = max at the list preemptible rates.
  - `floor`: min = max = published floor (or fixed preemptible rates).
  - `reject`: mark the resource unknown (0).
- Live spot can **exceed** on-demand when following the market without a pricing-policy max; that uncapped case is not modeled as a finite max. Prefer a Nebius pricing policy for a hard per-GPU ceiling.

JSON includes `*_min` / `*_max` fields; `before_total` / `after_total` / `delta` remain aliases of the **max** values for compatibility.

## Pricing

- Rates come from public Nebius documentation pages (USD/ILS list prices, excluding tax/VAT).
- **Month** means **730 hours**, matching Nebius storage pricing units.
- **Year** means **8760 hours** (linear scale from hourly rates).
- Spot/preemptible GPU pricing sources:
  - [Getting spot prices](https://docs.nebius.com/signup-billing/spot-prices.md) / [Spot pricing policies](https://docs.nebius.com/signup-billing/pricing-policy.md) — dynamic market model + public SpotPriceService API.
  - [Compute pricing](https://docs.nebius.com/compute/resources/pricing.md) — published **floors** (`From $X` / `From ₪Y`) for B300/B200/H200/H100/RTX PRO 6000, and **fixed** preemptible GPU/CPU/RAM rows for L40S.
- Floors are stored as `preemptible_floor_*`; fixed L40S rates as `preemptible{}`. Live API quotes are **not** vendored as floors (they change as often as every 15 minutes). `python scripts/refresh_catalog.py --spot-snapshot` prints a comparison.
- Platforms without a published floor or fixed preemptible table are **not** given invented spot rates (min = max = on-demand with a warning, or unknown if the platform itself is unpriced).
- Catalog timestamp is in `meta.fetched_at`. Treat estimates as approximate until refreshed.
- Catalog also records `meta.sources`, `unpriced_gaps`, and documented `usage_metrics`.

## Terraform inputs

- Designed for `terraform show -json` **plan** (`resource_changes`) and **state** (`values.root_module`).
- Only `nebius/nebius` provider resources are priced; other providers are ignored.
- Plan **delta** = sum(after) − sum(before) for resources present on each side of the change.
- Nested managed boot disks on `nebius_compute_v1_instance` are included when `boot_disk.managed_disk.spec` includes a size (`size_*`).
- Instance `boot_disk.existing_disk` (id-only) or managed stubs without size are **ignored** on the instance line (no price, no “missing size” / “boot disk price unknown” warning). Price those disks only when they appear as `nebius_compute_v1_disk` (or equivalent) in the plan/state input — avoids double-counting and false unknowns.
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
- Copy-paste planned-usage patterns (GPU nodes, Object Storage egress, preemptible mix): [examples/usage/](examples/usage/) — see [docs/usage-file.md](docs/usage-file.md).

## Free / control-plane resources

- VPC networks/subnets/routes/security groups/allocations, Container Registry, KMS (preview), Tunnels (preview), IAM, DNS, MysteryBox, GPU cluster handles, NVL instance **group** handles, Object Storage inventory/transfer resources, Applications k8s releases, utility `nebius_hash`, and Managed Kubernetes **control plane** are treated as **$0** per public docs (or no separate service fee).
- Observability is free while in preview (no Terraform resource types in the provider as of inventory).
- Nodes, Serverless AI running capacity, disks, filesystems, and buckets still bill as their underlying services.

## Known unpriced / partial types

| Type / item | Notes |
|-------------|-------|
| `NETWORK_HDD` disks / filesystems | No public list price — **unknown** unless `--catalog-price disk.NETWORK_HDD=...` / `filesystem.NETWORK_HDD=...` |
| Filesystem `VAST` | Quote/enterprise — **unknown** unless `--catalog-price VAST=...` ($/GiB·month) |
| Filesystem `WEKA` | USD **$0.10/GiB·month** vendored from [nebius.com/prices](https://nebius.com/prices); ILS not published there (override with `--catalog-price WEKA=...` under `--currency ILS`) |
| Local SSD size | Priced when `local_ssd_gib` usage is set; otherwise 0 + warning if `local_disks` requested |
| GB200 / GB300 (NVL groups) | Group handle is free; member platform rates are quote-only — use `--catalog-price platform.GB200=...` / `GB300=...` ($/GPU·hour) |
| Live spot SKU quotes | Dynamic per region; use public API / website — catalog keeps published floors only |
| Serverless CPU selectors | Need `preset` (or platform+preset) for RAM; GPU selectors map to default platforms |
| Object Storage transfers | No separate transfer service fee; destination storage / egress still need bucket usage metrics |

### `--catalog-price` overrides

Repeatable `KEY=VALUE` flags (same style as `--usage-metric`) fill null/missing catalog rates for the selected `--currency`. Existing public rates may be overridden (**warning**). Under `--strict-check`, a hole covered by `--catalog-price` no longer fails. Key vocabulary and units: [docs/catalog-price.md](docs/catalog-price.md).

## Security

- No Nebius API tokens, cloud credentials, or Terraform backend secrets are read or required.
- Do not place secrets in usage files or commit `.env` / credential material (see `.gitignore`).
