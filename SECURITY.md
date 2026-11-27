# Security Policy

## Supported versions

Security fixes are accepted against the default branch (`main`) for the current
**1.x** SemVer line (see [CHANGELOG.md](CHANGELOG.md) and
`src/nebius_tf_cost/__version__`).

## Reporting a vulnerability

Please report security issues privately when possible:

1. Prefer **GitHub Security Advisories** for this repository (Security → Advisories → Report a vulnerability), or
2. Contact the repository owner via GitHub if advisories are unavailable.

Do **not** open a public issue with exploit details until maintainers have had a chance to respond.

We aim to acknowledge reports promptly and will coordinate disclosure timing with you.

## Scope notes for this project

**nebius-tf-cost** estimates costs from local Terraform plan/state JSON and a **vendored public price catalog**. It:

- Does **not** require Nebius (or other cloud) account credentials
- Does **not** call private billing APIs
- Should never need secrets in CI, unit tests, or the plan-only Terraform e2e suite

If you find a path that exfiltrates credentials, executes remote code from untrusted plan JSON unsafely, or otherwise elevates trust beyond local estimation, that is in scope. Catalog/price accuracy issues belong in normal issues/PRs, not security reports (see [ASSUMPTIONS.md](ASSUMPTIONS.md)).
