#!/usr/bin/env python3
"""Refresh helper for the vendored price catalog.

This script does **not** call Nebius APIs. It prints the public docs URLs and a
checklist for manually updating ``data/prices.json``. Automated HTML/Markdown
scraping is intentionally out of scope so catalog changes stay reviewable.

Usage:
  python scripts/refresh_catalog.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "prices.json"

URLS = [
    "https://docs.nebius.com/compute/resources/pricing.md",
    "https://docs.nebius.com/object-storage/resources/pricing.md",
    "https://docs.nebius.com/vpc/resources/pricing.md",
    "https://docs.nebius.com/postgresql/resources/pricing.md",
    "https://docs.nebius.com/mlflow/resources/pricing.md",
    "https://docs.nebius.com/kubernetes/resources/pricing.md",
    "https://docs.nebius.com/container-registry/resources/pricing.md",
    "https://docs.nebius.com/kms/resources/pricing.md",
    "https://docs.nebius.com/tunnels/resources/pricing.md",
    "https://docs.nebius.com/serverless/pricing-quotas.md",
    "https://docs.nebius.com/observability/resources/pricing.md",
    "https://docs.nebius.com/slurm-soperator/managed-soperator/resources/pricing.md",
    "https://docs.nebius.com/compute/virtual-machines/types.md",
    "https://docs.nebius.com/compute/storage/types.md",
]


def main() -> int:
    print("Vendored catalog refresh checklist")
    print("=================================")
    print(f"Catalog path: {CATALOG}")
    print()
    print("1. Fetch each public pricing page (append .md to docs.nebius.com URLs):")
    for url in URLS:
        print(f"   - {url}")
    print()
    print("2. Update numeric rates in data/prices.json (USD and ILS).")
    print("3. Bump meta.fetched_at to the current UTC timestamp.")
    print("4. Run: python -m pytest && ruff check src tests scripts")
    print()
    if CATALOG.is_file():
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        print(f"Current meta.fetched_at: {data.get('meta', {}).get('fetched_at')}")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"Suggested fetched_at:    {stamp}")
    else:
        print("WARNING: catalog file missing", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
