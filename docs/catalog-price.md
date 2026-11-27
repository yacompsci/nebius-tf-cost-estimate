# Catalog price overrides (`--catalog-price`)

Some Nebius SKUs have **no public list price** in the vendored catalog (quote-only platforms, unpublished disk/FS types). Pass a private / sales quote with repeatable `--catalog-price KEY=VALUE` flags — the same interaction pattern as `--usage-metric`.

```bash
nebius-tf-cost estimate --plan plan.json \
  --catalog-price VAST=0.12 \
  --catalog-price platform.GB200=25 \
  --catalog-price disk.NETWORK_HDD=0.05 \
  --currency USD \
  --period month \
  --strict-check
```

Values apply to the selected `--currency` (USD or ILS). Units match the catalog section for that key.

## Keys

| Key | Catalog target | Unit | Notes |
|-----|----------------|------|-------|
| `disk.<TYPE>` | `disks_gib_month` | /GiB·month | e.g. `disk.NETWORK_HDD` |
| `filesystem.<TYPE>` | `filesystem_gib_month` | /GiB·month | e.g. `filesystem.VAST`, `filesystem.WEKA` |
| `platform.<ID>` | `compute_platforms` | /GPU·hour (on-demand, `gpu_only`) | e.g. `platform.GB200`, `platform.GB300`, or any platform id |

### Bare aliases (unambiguous only)

| Alias | Expands to |
|-------|------------|
| `VAST` | `filesystem.VAST` |
| `WEKA` | `filesystem.WEKA` |
| `GB200` | `platform.GB200` |
| `GB300` | `platform.GB300` |

`NETWORK_HDD` alone is **rejected** (it exists under both disk and filesystem). Use `disk.NETWORK_HDD` or `filesystem.NETWORK_HDD`.

## Merge behavior

1. Overrides are applied to a **copy** of the loaded catalog (bundled or `--catalog`).
2. **Null / missing** rates are filled — the resource is no longer unknown for that hole.
3. If a **public catalog rate already exists** for the selected currency, the override **wins** and a **warning** is emitted (quote customers may need a private rate).
4. Under `--strict-check`, a type that was unknown only because of a missing rate **passes** once `--catalog-price` supplies it.
5. Platform overrides create a `gpu_only` catalog entry when the platform id is absent (typical for GB200/GB300 quotes).

## Quote-only examples

```bash
# VAST filesystem (GiB·month) + GB300 GPU-hour quote
nebius-tf-cost estimate --plan plan.json \
  --catalog-price VAST=0.18 \
  --catalog-price GB300=30

# Private NETWORK_HDD disk rate
nebius-tf-cost estimate --plan plan.json \
  --catalog-price disk.NETWORK_HDD=0.04
```

WEKA USD ($0.10/GiB·month) is already vendored from [nebius.com/prices](https://nebius.com/prices). You may still override it (with a warning) via `--catalog-price WEKA=...` for consistency with other quote workflows. ILS for WEKA is not published on that page — pass `--currency ILS --catalog-price WEKA=...` if you have an ILS quote.

## Related

- Planned usage metrics: [usage-file.md](usage-file.md) (`--usage-file` / `--usage-metric`)
- Pricing gaps / assumptions: [ASSUMPTIONS.md](../ASSUMPTIONS.md)
