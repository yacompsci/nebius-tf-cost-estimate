"""CLI ``--catalog-price KEY=VALUE`` overrides for quote-only / missing catalog rates."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from nebius_tf_cost.catalog import PriceCatalog
from nebius_tf_cost.errors import UsageError

# Bare aliases that map unambiguously to a catalog section (no NETWORK_HDD —
# that type exists under both disk and filesystem and requires a prefix).
KNOWN_BARE_KEYS: dict[str, tuple[str, str]] = {
    "VAST": ("filesystem", "VAST"),
    "WEKA": ("filesystem", "WEKA"),
    "GB200": ("platform", "GB200"),
    "GB300": ("platform", "GB300"),
}

_SECTION_TO_CATALOG: dict[str, str] = {
    "disk": "disks_gib_month",
    "filesystem": "filesystem_gib_month",
    "platform": "compute_platforms",
}

_SECTION_UNITS = {
    "disk": "$/GiB·month (or ILS equivalent for --currency)",
    "filesystem": "$/GiB·month (or ILS equivalent for --currency)",
    "platform": "$/GPU·hour on-demand (gpu_only billing; or ILS equivalent)",
}


@dataclass(frozen=True)
class CatalogPriceOverride:
    """One parsed ``--catalog-price`` entry."""

    section: str  # disk | filesystem | platform
    name: str
    rate: float
    raw_key: str


def _cur_key(currency: str) -> str:
    return "usd" if currency.upper() == "USD" else "ils"


def parse_catalog_price_cli_overrides(pairs: list[str] | None) -> list[CatalogPriceOverride]:
    """Parse repeated ``--catalog-price KEY=VALUE`` overrides.

    Keys (case-sensitive names)::

        disk.<TYPE>          e.g. disk.NETWORK_HDD
        filesystem.<TYPE>    e.g. filesystem.VAST, filesystem.WEKA
        platform.<ID>        e.g. platform.GB200, platform.gpu-h100-sxm

    Bare aliases (unambiguous only)::

        VAST, WEKA           → filesystem
        GB200, GB300         → platform

    ``NETWORK_HDD`` alone is rejected — use ``disk.NETWORK_HDD`` or
    ``filesystem.NETWORK_HDD``. Units match the catalog section (GiB·month for
    disk/filesystem; GPU·hour for platform). Value applies to ``--currency``.
    """
    if not pairs:
        return []
    out: list[CatalogPriceOverride] = []
    for item in pairs:
        if "=" not in item:
            raise UsageError(f"catalog price must be KEY=VALUE, got {item!r}")
        key, raw = item.split("=", 1)
        key = key.strip()
        if not key:
            raise UsageError(f"empty catalog price key in {item!r}")
        try:
            rate = float(raw.strip())
        except ValueError as exc:
            raise UsageError(f"catalog price value must be numeric: {item!r}") from exc
        if rate < 0:
            raise UsageError(f"catalog price must be non-negative: {item!r}")

        section, name = _resolve_key(key)
        out.append(CatalogPriceOverride(section=section, name=name, rate=rate, raw_key=key))
    return out


def _resolve_key(key: str) -> tuple[str, str]:
    if key in KNOWN_BARE_KEYS:
        return KNOWN_BARE_KEYS[key]
    if key == "NETWORK_HDD":
        raise UsageError(
            "catalog price key NETWORK_HDD is ambiguous (disk and filesystem); "
            "use disk.NETWORK_HDD or filesystem.NETWORK_HDD"
        )
    if "." not in key:
        raise UsageError(
            f"unknown catalog price key {key!r}; use disk.<TYPE>, filesystem.<TYPE>, "
            f"platform.<ID>, or bare aliases {', '.join(sorted(KNOWN_BARE_KEYS))}"
        )
    section, name = key.split(".", 1)
    section = section.strip()
    name = name.strip()
    if section not in _SECTION_TO_CATALOG or not name:
        raise UsageError(
            f"unknown catalog price key {key!r}; use disk.<TYPE>, filesystem.<TYPE>, "
            f"or platform.<ID>"
        )
    return section, name


def apply_catalog_price_overrides(
    catalog: PriceCatalog,
    overrides: list[CatalogPriceOverride],
    *,
    currency: str,
) -> tuple[PriceCatalog, list[str]]:
    """Merge overrides into a deep copy of the catalog.

    Missing / null rates are filled. Existing non-null rates for the selected
    currency are **overridden with a warning** (quote customers may need a
    private rate even when a public list price exists).
    """
    if not overrides:
        return catalog, []

    raw: dict[str, Any] = copy.deepcopy(catalog.raw)
    warnings: list[str] = []
    ck = _cur_key(currency)

    for ov in overrides:
        if ov.section == "platform":
            _apply_platform(raw, ov, ck=ck, warnings=warnings)
        else:
            _apply_storage_rate(raw, ov, ck=ck, warnings=warnings)

    return PriceCatalog(raw=raw, path=catalog.path), warnings


def _apply_storage_rate(
    raw: dict[str, Any],
    ov: CatalogPriceOverride,
    *,
    ck: str,
    warnings: list[str],
) -> None:
    catalog_key = _SECTION_TO_CATALOG[ov.section]
    section = raw.setdefault(catalog_key, {})
    entry = section.get(ov.name)
    if not isinstance(entry, dict):
        entry = {}
        section[ov.name] = entry

    existing = entry.get(ck)
    if existing is not None:
        warnings.append(
            f"--catalog-price {ov.raw_key}: overriding existing {currency_label(ck)} "
            f"catalog rate {existing} → {ov.rate} "
            f"({_SECTION_UNITS[ov.section]})"
        )
    entry[ck] = ov.rate
    # Drop stale "no public price" note once a rate is supplied.
    if entry.get("note") and "no public" in str(entry["note"]).lower():
        entry.pop("note", None)


def _apply_platform(
    raw: dict[str, Any],
    ov: CatalogPriceOverride,
    *,
    ck: str,
    warnings: list[str],
) -> None:
    platforms = raw.setdefault("compute_platforms", {})
    entry = platforms.get(ov.name)
    rate_key = f"gpu_hour_{ck}"
    if not isinstance(entry, dict):
        platforms[ov.name] = {
            "billing": "gpu_only",
            rate_key: ov.rate,
            "note": "from --catalog-price override (quote / private rate)",
        }
        return

    existing = entry.get(rate_key)
    if existing is not None:
        warnings.append(
            f"--catalog-price {ov.raw_key}: overriding existing {currency_label(ck)} "
            f"catalog rate {existing} → {ov.rate} "
            f"({_SECTION_UNITS['platform']})"
        )
    entry[rate_key] = ov.rate
    # Ensure estimators can bill GPU-only when the platform was previously absent
    # of billing metadata.
    if not entry.get("billing"):
        entry["billing"] = "gpu_only"


def currency_label(ck: str) -> str:
    return "USD" if ck == "usd" else "ILS"
