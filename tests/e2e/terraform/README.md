# Plan-only Terraform e2e fixtures

HCL for `nebius/nebius` that supports **`terraform plan` without Nebius credentials**.

## Approach

| Setting | Why |
|---------|-----|
| `provider.no_credentials = true` | Documented provider flag: do not authenticate |
| `provider.disable_preflight_checks = true` | Skip server-side API validation during plan |
| Placeholder `parent_id` / subnet / allocation IDs | Required when no profile/credentials supply a real project; never applied |

Resources: Compute disk + instance (with nested boot disk), Object Storage bucket, VPC network.

**Do not `terraform apply`** — placeholders are not live Nebius IDs.

## Local commands

```bash
cd tests/e2e/terraform
terraform init -backend=false
terraform plan -out=tfplan
terraform show -json tfplan > /tmp/e2e-plan.json
nebius-tf-cost estimate --plan /tmp/e2e-plan.json --usage-file usage.yaml --json
```

Or via pytest (skips if `terraform` is not on `PATH`):

```bash
python -m pytest -m e2e -o addopts=-q
```
