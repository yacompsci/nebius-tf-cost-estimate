#!/usr/bin/env python3
"""Refresh helper for the vendored price catalog.

This script does **not** rewrite ``data/prices.json`` automatically. It prints
public docs URLs, a checklist for manual updates, and an optional live spot
price snapshot from the public Nebius SpotPriceService API for comparison
against catalog floors. Automated scraping stays out of scope so catalog
changes remain reviewable — never invent floors for platforms not listed in
the published docs.

Usage:
  python scripts/refresh_catalog.py
  python scripts/refresh_catalog.py --spot-snapshot
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections import defaultdict
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
    "https://docs.nebius.com/signup-billing/spot-prices.md",
    "https://docs.nebius.com/signup-billing/pricing-policy.md",
    "https://docs.nebius.com/compute/virtual-machines/preemptible.md",
    "https://nebius.com/spot-prices",
]

SPOT_API = "https://api.nebius.cloud/billing/v1alpha1/spot-price"


def _fetch_spot_snapshot() -> dict:
    """Paginate the public spot-price API (no auth)."""
    items: list[dict] = []
    page_token = ""
    snapshot_time = None
    while True:
        url = f"{SPOT_API}?pageSize=100"
        if page_token:
            url += f"&pageToken={page_token}"
        # Some edges reject the default Python-urllib User-Agent with 403.
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "nebius-tf-cost-refresh/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        items.extend(payload.get("items") or [])
        snapshot_time = payload.get("snapshotTime") or snapshot_time
        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break
    return {"snapshotTime": snapshot_time, "items": items}


def _print_spot_comparison(catalog: dict, snapshot: dict) -> None:
    platforms = catalog.get("compute_platforms") or {}
    by_plat: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for item in snapshot.get("items") or []:
        plat = (item.get("computeInstance") or {}).get("platform")
        price = item.get("price") or {}
        if not plat or price.get("currency", "").lower() != "usd":
            continue
        try:
            value = float(price["value"])
        except (KeyError, TypeError, ValueError):
            continue
        by_plat[plat].append((str(item.get("region") or ""), value))

    print()
    print("Live spot snapshot (USD/GPU-hour) vs catalog floors")
    print("--------------------------------------------------")
    print(f"API snapshotTime: {snapshot.get('snapshotTime')}")
    print(f"SKU count: {len(snapshot.get('items') or [])}")
    print()
    print(f"{'platform':<22} {'floor_usd':>10} {'live_min':>10} {'live_max':>10} {'regions'}")
    all_plats = sorted(set(platforms) | set(by_plat))
    for plat in all_plats:
        pdata = platforms.get(plat) or {}
        floor = pdata.get("preemptible_floor_usd")
        fixed = pdata.get("preemptible")
        if floor is None and isinstance(fixed, dict):
            floor_label = f"fixed:{fixed.get('gpu_hour_usd')}"
        elif floor is None:
            floor_label = "—"
        else:
            floor_label = f"{float(floor):.4g}"
        rows = by_plat.get(plat) or []
        if rows:
            vals = [v for _, v in rows]
            live_min = f"{min(vals):.4g}"
            live_max = f"{max(vals):.4g}"
            regions = ",".join(sorted({r for r, _ in rows}))
        else:
            live_min = live_max = "—"
            regions = "(not in live API)"
        print(f"{plat:<22} {floor_label:>10} {live_min:>10} {live_max:>10} {regions}")
    print()
    print(
        "Note: live quotes are informational only. Catalog floors must come from "
        "published compute pricing 'From $X' / fixed preemptible rows — do not "
        "overwrite floors with a single live observation."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spot-snapshot",
        action="store_true",
        help="Fetch public SpotPriceService list and compare to catalog floors",
    )
    args = parser.parse_args(argv)

    print("Vendored catalog refresh checklist")
    print("=================================")
    print(f"Catalog path: {CATALOG}")
    print()
    print("1. Fetch each public pricing / spot page:")
    for url in URLS:
        print(f"   - {url}")
    print()
    print("2. Update numeric rates in data/prices.json (USD and ILS).")
    print("   - Dynamic GPU spot floors: compute pricing 'From $X' / 'From ₪Y' lines.")
    print("   - Fixed preemptible (L40S): exact GPU/CPU/RAM preemptible rows.")
    print("   - Do not invent floors for platforms absent from those tables.")
    print("3. Mirror the same file to src/nebius_tf_cost/data/prices.json.")
    print("4. Bump meta.fetched_at; keep meta.sources in sync with the URL list.")
    print("5. Run: python -m pytest && ruff check src tests scripts")
    print()
    if not CATALOG.is_file():
        print("WARNING: catalog file missing", file=sys.stderr)
        return 1

    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    print(f"Current meta.fetched_at: {data.get('meta', {}).get('fetched_at')}")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"Suggested fetched_at:    {stamp}")

    if args.spot_snapshot:
        try:
            snapshot = _fetch_spot_snapshot()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"ERROR: spot API fetch failed: {exc}", file=sys.stderr)
            return 1
        _print_spot_comparison(data, snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
