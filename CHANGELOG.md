# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Version source of truth: `src/nebius_tf_cost/__version__` (exposed as `nebius-tf-cost --version`
and as the installed package version via setuptools dynamic metadata).

## [Unreleased]

### Fixed

- PyPI publish workflow triggers on `v*` tag push and `workflow_dispatch` (not on
  `release: published` from `GITHUB_TOKEN`), so Trusted Publishing can run for
  already-cut tags such as `v1.0.0`.

## [1.0.0] - 2026-11-27

First declared SemVer release. The package previously carried an undeclared `0.1.0`
placeholder in packaging metadata; **1.0.0** marks the current CLI surface as stable for
CI adoption (see README). Pre-1.0 development history is summarized below from merged
work on `main` (through PR [#23](https://github.com/yacompsci/nebius-tf-cost-estimate/pull/23)).

### Added

- `nebius-tf-cost` CLI: estimate Nebius AI Cloud costs from Terraform plan/state JSON
  using a vendored public price catalog (no cloud credentials) ([#1](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/1)).
- Budget gating, planned usage (`--usage-file` / `--usage-metric`), periods
  (`hour` / `month` / `year`), currency selection, and JSON output ([#1](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/1)).
- GitHub Actions CI for lint and unit tests ([#2](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/2)).
- Estimators for MK8s node groups and Serverless AI endpoints; expanded free/control-plane
  types; catalog sources and unpriced-gap tracking ([#3](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/3)).
- Min/max (optimistic/pessimistic) totals and deltas; budget compares the **max** bound
  ([#4](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/4)).
- Published spot floors / fixed preemptible rates in the catalog; `--preemptible-policy`
  (`on_demand` / `floor` / `reject`) ([#5](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/5)).
- Planned usage file guide (`docs/usage-file.md`) ([#7](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/7)).
- Scheduled workflow to open coverage issues for uncovered Nebius provider resource types
  ([#8](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/8)).
- Plan-only Terraform e2e suite (no apply, no Nebius credentials)
  ([#9](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/9)).
- Open-source readiness: LICENSE (Apache-2.0), CONTRIBUTING, CODE_OF_CONDUCT, SECURITY,
  AGENTS.md, issue/PR templates, Dependabot ([#10](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/10)).
- `--strict-check` CI gate: fail on budget exceed, missing planned usage, or
  unknown/unpriced resources ([#15](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/15)).
- `--catalog-price KEY=VALUE` overrides for quote-only / missing catalog rates; WEKA USD
  rate ($0.10/GiB·month) vendored from nebius.com/prices
  ([#16](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/16)).
- Copy-paste usage-file examples under `examples/usage/` (GPU nodes, Object Storage egress,
  preemptible mix) with companion plan JSON ([#17](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/17)).
- SemVer + Keep a Changelog process documented in CONTRIBUTING; tag → GitHub Release
  workflow ([#18](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/18)).
- PyPI Trusted Publishing workflow (`.github/workflows/publish-pypi.yml`) for package
  `nebius-tf-cost` (OIDC; no long-lived API token in CI).

### Changed

- **Breaking (pre-1.0):** `--on-exceed fail|warn` replaced by boolean `--strict-check`
  (default remains warn + exit 0) ([#15](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/15)).
- Module polish and helper deduplication; Makefile `check` / `cov` / `format` / `e2e`
  targets ([#6](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/6)).
- Package version is read from `nebius_tf_cost.__version__` only (setuptools dynamic
  version); CLI `--version` uses the same value.
- Document `--mode` clearly in README and argparse help: frames totals as delta
  (Before/After/Delta) vs after-only (Before=0); input still comes from
  `--plan` / `--state` / `--input`
  ([#22](https://github.com/yacompsci/nebius-tf-cost-estimate/pull/22)).
- Canonical repository is now
  [yacompsci/nebius-tf-cost-estimate](https://github.com/yacompsci/nebius-tf-cost-estimate)
  (moved from `alon-sage/nebius-tf-cost-estimate`). Historical PR links below still
  point at the previous owner; GitHub redirects when available
  ([#20](https://github.com/yacompsci/nebius-tf-cost-estimate/pull/20)).

### Fixed

- Correct flattening of Terraform `planned_values` resources that use a `values` map
  ([#6](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/6)).
- Code of Conduct enforcement contact wording ([#10](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/10)).
- README Demo uses `examples/sample-plan.json` (matched to committed sample usage)
  ([#19](https://github.com/alon-sage/nebius-tf-cost-estimate/pull/19)).
- Instance boot disks attached by `existing_disk` (id-only) or without inline
  `managed_disk.spec` size are ignored on the instance line — no “missing size_*”
  / “boot disk price unknown” warnings and no double-count when the disk is already
  a `nebius_compute_v1_disk` resource. Inline sized managed boot disks still fold
  into the instance estimate
  ([#21](https://github.com/yacompsci/nebius-tf-cost-estimate/pull/21)).
- Instance `secondary_disks[]` with inline sized `managed_disk.spec` are folded into
  the instance estimate (previously silent underprice). `existing_disk` / size-less
  stubs stay ignored on the instance line — same attach-by-ref rules as boot disks
  ([#23](https://github.com/yacompsci/nebius-tf-cost-estimate/pull/23)).

[Unreleased]: https://github.com/yacompsci/nebius-tf-cost-estimate/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/yacompsci/nebius-tf-cost-estimate/releases/tag/v1.0.0
