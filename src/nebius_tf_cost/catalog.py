"""Vendored Nebius price catalog loader."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nebius_tf_cost.errors import DataError

# Preference order for the default catalog:
# 1) package-bundled copy (pip install / wheel)
# 2) repository root data/prices.json (editable checkout)
# 3) ./data/prices.json relative to CWD
_PACKAGE_DATA = Path(__file__).resolve().parent / "data" / "prices.json"
_REPO_DATA = Path(__file__).resolve().parents[2] / "data" / "prices.json"


@dataclass(frozen=True)
class PriceCatalog:
    raw: dict[str, Any]
    path: Path

    @property
    def month_hours(self) -> int:
        return int(self.raw.get("meta", {}).get("month_hours", 730))

    @property
    def year_hours(self) -> int:
        return int(self.raw.get("meta", {}).get("year_hours", 8760))

    @property
    def free_types(self) -> frozenset[str]:
        return frozenset(self.raw.get("free_resource_types", []))

    def platform(self, platform_id: str) -> dict[str, Any] | None:
        return self.raw.get("compute_platforms", {}).get(platform_id)

    def money(self, mapping: dict[str, Any] | None, currency: str) -> float | None:
        if mapping is None:
            return None
        key = currency.lower()
        value = mapping.get(key)
        if value is None:
            return None
        return float(value)


def default_catalog_path() -> Path:
    """Resolve the bundled catalog path."""
    candidates = [
        _PACKAGE_DATA,
        _REPO_DATA,
        Path.cwd() / "data" / "prices.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise DataError(
        "price catalog not found; pass --catalog path/to/prices.json "
        f"(looked for: {', '.join(str(c) for c in candidates)})"
    )


def load_catalog(path: Path | None = None) -> PriceCatalog:
    catalog_path = path or default_catalog_path()
    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DataError(f"cannot read catalog {catalog_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DataError(f"invalid catalog JSON {catalog_path}: {exc}") from exc
    if not isinstance(raw, dict) or "compute_platforms" not in raw:
        raise DataError(f"catalog missing compute_platforms: {catalog_path}")
    return PriceCatalog(raw=raw, path=catalog_path)
