#!/usr/bin/env python3
"""Discover uncovered Nebius Terraform provider types and open feature issues.

Inventory source (no secrets): public GitHub Contents API for
``nebius/terraform-provider-nebius`` ``docs/resources/*.md``
(see ``nebius_tf_cost.provider_coverage.PROVIDER_DOCS_SOURCE``).

Coverage = ``estimate.ESTIMATORS`` ∪ catalog ``free_resource_types``.
For each new uncovered type, opens or updates a GitHub issue titled
``feature: cover <type>`` with the ``feature`` label (created if missing).

Usage:
  python scripts/discover_uncovered_types.py --dry-run
  python scripts/discover_uncovered_types.py --repo owner/name
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nebius_tf_cost.catalog import load_catalog  # noqa: E402
from nebius_tf_cost.estimate import ESTIMATORS  # noqa: E402
from nebius_tf_cost.provider_coverage import (  # noqa: E402
    PROVIDER_DOCS_SOURCE,
    diff_coverage,
    ensure_label,
    fetch_provider_resource_types,
    list_coverage_issues,
    open_or_update_coverage_issue,
)


def _parse_repo(value: str | None) -> tuple[str, str]:
    raw = value or os.environ.get("GITHUB_REPOSITORY") or ""
    if "/" not in raw:
        raise SystemExit("repository required as owner/name via --repo or GITHUB_REPOSITORY")
    owner, repo = raw.split("/", 1)
    if not owner or not repo:
        raise SystemExit(f"invalid repository {raw!r}; expected owner/name")
    return owner, repo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Diff Nebius provider resource types against estimators/"
            "free_resource_types and open feature issues for gaps."
        )
    )
    parser.add_argument(
        "--repo",
        help="GitHub repository owner/name (default: GITHUB_REPOSITORY)",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "data" / "prices.json",
        help="Path to prices.json (default: data/prices.json)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "",
        help="GitHub token (default: GITHUB_TOKEN / GH_TOKEN)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions only; do not create/update issues",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary on stdout",
    )
    args = parser.parse_args(argv)

    catalog = load_catalog(args.catalog)
    provider_types = fetch_provider_resource_types()
    diff = diff_coverage(
        provider_types,
        ESTIMATORS,
        catalog.free_types,
        source=PROVIDER_DOCS_SOURCE,
    )

    summary: dict = {
        "source": diff.source,
        "provider_count": len(diff.provider_types),
        "covered_count": len(diff.covered_types & diff.provider_types),
        "uncovered": sorted(diff.uncovered_types),
        "actions": [],
    }

    if not args.json:
        print("Nebius provider coverage discovery")
        print("=================================")
        print(f"Source:   {diff.source}")
        print(f"Provider: {len(diff.provider_types)} resource types")
        print(f"Covered:  {len(diff.covered_types & diff.provider_types)}")
        print(f"Uncovered ({len(diff.uncovered_types)}):")
        if diff.uncovered_types:
            for rtype in sorted(diff.uncovered_types):
                print(f"  - {rtype}")
        else:
            print("  (none)")
        print()

    if not diff.uncovered_types:
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print("No uncovered types; nothing to file.")
        return 0

    owner, repo = _parse_repo(args.repo)
    if not args.dry_run and not args.token:
        raise SystemExit("GITHUB_TOKEN (or --token) required unless --dry-run")

    existing: list = []
    if args.token:
        if not args.dry_run:
            ensure_label(owner, repo, token=args.token)
        existing = list_coverage_issues(owner, repo, token=args.token)
    elif args.dry_run and not args.json:
        print("No token: dry-run will assume no existing coverage issues.")

    for rtype in sorted(diff.uncovered_types):
        result = open_or_update_coverage_issue(
            owner,
            repo,
            rtype,
            token=args.token or "dry-run",
            existing_issues=existing,
            source=diff.source,
            dry_run=args.dry_run,
        )
        summary["actions"].append(result)
        if not args.json:
            action = result["action"]
            number = result.get("number")
            url = result.get("url")
            suffix = f" #{number}" if number else ""
            extra = f" {url}" if url else ""
            dry = " (dry-run)" if result.get("dry_run") else ""
            print(f"{action}{suffix}: {result['title']}{extra}{dry}")

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
