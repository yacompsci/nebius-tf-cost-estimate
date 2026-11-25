# Plan-only e2e: no Nebius cloud credentials.
#
# The Nebius provider documents `no_credentials = true` for unauthenticated
# workflows. When credentials are omitted, `parent_id` cannot be read from a
# CLI profile, so we set a non-live placeholder project ID. Preflight checks
# are disabled so plan does not call Nebius APIs to validate placeholders.
#
# Never run `terraform apply` against this configuration.

provider "nebius" {
  no_credentials           = true
  disable_preflight_checks = true
  parent_id                = "project-e2e0000000000000000000000"
}
