# Planned-usage examples

Copy-paste `--usage-file` samples for common patterns that otherwise trigger
missing-usage warnings. Schema and metric table: [docs/usage-file.md](../../docs/usage-file.md).

| File | Covers | Pair with |
|------|--------|-----------|
| [gpu-nodes.yaml](gpu-nodes.yaml) | GPU MK8s `node_count`, Serverless `running_fraction`, optional `local_ssd_gib` | [../plans/gpu-nodes.json](../plans/gpu-nodes.json) or `tests/fixtures/plan_coverage.json` |
| [object-storage-egress.yaml](object-storage-egress.yaml) | Bucket `stored_gib` / requests / `egress_gib` | [../sample-plan.json](../sample-plan.json) or e2e `tests/e2e/terraform/` |
| [preemptible-mix.yaml](preemptible-mix.yaml) | Usage overlay for a fleet with on-demand + spot/preemptible compute | [../plans/preemptible-mix.json](../plans/preemptible-mix.json); also see `tests/fixtures/plan_minmax.json` |

Spot vs on-demand is a **Terraform** attribute (`preemptible` / `follows_spot_price`), not a usage metric. Bound that mix with `--preemptible-policy` (see comments in `preemptible-mix.yaml`).

## Quick demos

```bash
# Object Storage (clears zero-usage / egress warnings)
nebius-tf-cost estimate \
  --plan examples/sample-plan.json \
  --usage-file examples/usage/object-storage-egress.yaml \
  --period month

# GPU nodes + Serverless duty cycle
nebius-tf-cost estimate \
  --plan examples/plans/gpu-nodes.json \
  --usage-file examples/usage/gpu-nodes.yaml \
  --period month

# On-demand + preemptible GPU (min/max + spot floors)
nebius-tf-cost estimate \
  --plan examples/plans/preemptible-mix.json \
  --usage-file examples/usage/preemptible-mix.yaml \
  --period month \
  --preemptible-policy on_demand
```

The root [../usage.yaml](../usage.yaml) remains the short demo used in the README.
