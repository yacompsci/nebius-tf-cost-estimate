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
- Usage-based metrics: update [docs/usage-file.md](docs/usage-file.md) and examples when adding keys (keep [examples/usage/](examples/usage/) pattern files in sync when the schema or common patterns change).
- Catalog price overrides (`--catalog-price`): update [docs/catalog-price.md](docs/catalog-price.md) and `KNOWN_BARE_KEYS` / parsers when adding keys; do not invent default catalog rates for quote-only SKUs.
- New provider types: register estimators in `estimate.ESTIMATORS` (and/or `free_resource_types` in the catalog). The scheduled workflow [`.github/workflows/discover-uncovered-types.yml`](.github/workflows/discover-uncovered-types.yml) opens coverage issues for newly uncovered types — those issues are requests only; they do not invent prices.

## Versioning and releases

This project uses [Semantic Versioning](https://semver.org/) and
[Keep a Changelog](https://keepachangelog.com/).

**Version source of truth:** `src/nebius_tf_cost/__version__`.
`pyproject.toml` reads that attribute via setuptools dynamic metadata, so
`pip show nebius-tf-cost`, `importlib.metadata.version("nebius-tf-cost")`, and
`nebius-tf-cost --version` stay aligned. Do **not** add a second hardcoded
version string in `pyproject.toml`.

| Bump | When |
|------|------|
| **MAJOR** | Breaking CLI / public library behavior (post-1.0) |
| **MINOR** | Additive flags, estimators, catalog fields, docs that change expected usage |
| **PATCH** | Bug fixes, catalog refreshes from published docs, non-breaking docs/CI |

While working, append notes under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md).

### Cutting a release

1. Move `[Unreleased]` entries into a new `## [X.Y.Z] - YYYY-MM-DD` section in
   `CHANGELOG.md` (Keep a Changelog categories: Added / Changed / Fixed / …).
2. Set `__version__ = "X.Y.Z"` in `src/nebius_tf_cost/__init__.py`.
3. Open a PR; ensure `make check` is green.
4. After merge to `main`, create and push an annotated tag:
   ```bash
   git tag -a "vX.Y.Z" -m "vX.Y.Z"
   git push origin "vX.Y.Z"
   ```
5. Pushing `v*` tags runs [`.github/workflows/release.yml`](.github/workflows/release.yml),
   which creates a GitHub Release from the matching CHANGELOG section.
6. The same tag push also runs
   [`.github/workflows/publish-pypi.yml`](.github/workflows/publish-pypi.yml), which
   builds the `nebius-tf-cost` sdist/wheel and uploads via **Trusted Publishing**
   (OIDC). No `PYPI_API_TOKEN` secret is used or required.
   (Do not depend on `release: published` from the Release workflow — events created
   with `GITHUB_TOKEN` do not start other workflows.)

Consumers install with:

```bash
pip install nebius-tf-cost
```

Until the first PyPI upload succeeds, install from git:

```bash
pip install "git+https://github.com/yacompsci/nebius-tf-cost-estimate.git"
```

### Maintainer setup: PyPI Trusted Publisher (one-time)

Do this **before** (or right after) cutting `v1.0.0` so the publish workflow can
authenticate. Prefer Trusted Publishing over long-lived API tokens.

**1. GitHub Environment**

1. Open the repo → **Settings** → **Environments** → **New environment**.
2. Name it exactly `pypi` (must match `environment.name` in
   `.github/workflows/publish-pypi.yml`).
3. Optional but recommended: enable **Required reviewers** so a maintainer must
   approve each publish run.
4. No environment secrets are needed for Trusted Publishing.

**2. PyPI Trusted Publisher**

Package name on PyPI: **`nebius-tf-cost`**.

If the project does **not** exist yet on PyPI (first publish):

1. Sign in at [https://pypi.org](https://pypi.org) (account that will own the
   project).
2. Account settings → **Publishing** → **Add a new pending publisher**
   ([docs](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)).
3. Fill in:
   - **PyPI Project Name:** `nebius-tf-cost`
   - **Owner:** `yacompsci`
   - **Repository name:** `nebius-tf-cost-estimate`
   - **Workflow name:** `publish-pypi.yml` (filename only, under `.github/workflows/`)
   - **Environment name:** `pypi`
4. Save. The pending publisher becomes active on the first successful upload.

If the project **already** exists on PyPI:

1. Project → **Manage** → **Publishing** → add a GitHub publisher with the same
   Owner / Repository / Workflow / Environment values as above
   ([docs](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)).

**3. After tagging (or for an already-cut tag such as `v1.0.0`)**

1. Confirm the GitHub Release exists:
   `https://github.com/yacompsci/nebius-tf-cost-estimate/releases/tag/vX.Y.Z`
2. Approve the `pypi` environment deployment if reviewers are required.
3. Check Actions → **Publish to PyPI** for a green run.
4. If the tag was cut before Trusted Publisher / the `pypi` environment existed:
   Actions → **Publish to PyPI** → **Run workflow** → enter the tag (e.g. `v1.0.0`).
5. Verify: `pip index versions nebius-tf-cost` or
   [https://pypi.org/project/nebius-tf-cost/](https://pypi.org/project/nebius-tf-cost/).

Do **not** invent or commit API tokens. If Trusted Publishing is not configured,
the publish job will fail until the steps above are completed; use
**workflow_dispatch** (or re-run) after configuring the publisher — do not
force-move the tag.

## Security reports

See [SECURITY.md](SECURITY.md).
