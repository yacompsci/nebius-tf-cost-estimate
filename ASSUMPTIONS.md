# Assumptions and known gaps

## Pricing

- Rates come from public Nebius documentation pages (USD/ILS list prices, excluding tax/VAT).
- **Month** means **730 hours**, matching Nebius storage pricing units.
- **Year** means **8760 hours** (linear scale from hourly rates).
- Spot/preemptible GPU prices for many platforms are **dynamic**. Default `--preemptible-policy on_demand` prices them at on-demand rates and emits a warning. `floor` uses published “from $X” floors where available; `reject` marks the resource unknown.
- Catalog timestamp is in `meta.fetched_at`. Treat estimates as approximate until refreshed.

## Terraform inputs

- Designed for `terraform show -json` **plan** (`resource_changes`) and **state** (`values.root_module`).
- Only `nebius/nebius` provider resources are priced; other providers are ignored.
- Plan **delta** = sum(after) − sum(before) for resources present on each side of the change.
- Nested managed boot disks on `nebius_compute_v1_instance` are included when `boot_disk.managed_disk.spec` is present.
- Separately managed disks attached only by ID are priced via the disk resource itself (avoid double-counting boot disks that also appear as standalone resources — Terraform usually nests managed boot disks).

## Usage-based resources

- Object Storage buckets have **no size in Terraform**. Without `--usage-file` / `--usage-metric`, storage/request/egress cost is **0** with a warning.
- Request and egress metrics are interpreted as totals for the estimate window you care about (not auto-scaled by `--period`). Storage GiB is scaled with period via monthly rates.

## Free / control-plane resources

- VPC networks/subnets/routes/security groups, Container Registry, KMS (preview), Tunnels (preview), IAM, DNS, GPU cluster handles, and Managed Kubernetes **control plane** are treated as **$0** per public docs (nodes still bill as Compute).

## Known unpriced / partial types

| Type | Notes |
|------|-------|
| `NETWORK_HDD` disks | No public list price in compute pricing at catalog fetch |
| Filesystem `WEKA` / `VAST` / `NETWORK_HDD` | No public list price |
| `nebius_mk8s_v1_node_group` | Nodes = Compute VMs; template parsing not fully modeled yet |
| Serverless AI endpoints | Billed as Compute while running; not fully modeled |
| Observability / audit logs | Not in v0 estimators |
| Intelligent storage cold/warm | Use `intelligent_tier` usage metric (≥1 → cold) |

## Security

- No Nebius API tokens, cloud credentials, or Terraform backend secrets are read or required.
- Do not place secrets in usage files or commit `.env` / credential material (see `.gitignore`).
