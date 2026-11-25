from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebius_tf_cost.catalog import PriceCatalog, default_catalog_path, load_catalog
from nebius_tf_cost.errors import DataError

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "prices.json"


def test_load_catalog_valid() -> None:
    cat = load_catalog(CATALOG)
    assert isinstance(cat, PriceCatalog)
    assert cat.path == CATALOG
    assert cat.month_hours == 730
    assert cat.year_hours == 8760
    assert "nebius_vpc_v1_subnet" in cat.free_types or len(cat.free_types) >= 0
    assert cat.platform("cpu-e2") is not None


def test_catalog_money_helpers() -> None:
    cat = load_catalog(CATALOG)
    assert cat.money(None, "USD") is None
    assert cat.money({"usd": 1.5}, "USD") == pytest.approx(1.5)
    assert cat.money({"ils": 3.0}, "ILS") == pytest.approx(3.0)
    assert cat.money({"usd": 1.5}, "ILS") is None


def test_load_catalog_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="cannot read catalog"):
        load_catalog(tmp_path / "nope.json")


def test_load_catalog_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(DataError, match="invalid catalog JSON"):
        load_catalog(path)


def test_load_catalog_missing_platforms(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"meta": {}}), encoding="utf-8")
    with pytest.raises(DataError, match="missing compute_platforms"):
        load_catalog(path)


def test_default_catalog_path_finds_repo_or_package() -> None:
    path = default_catalog_path()
    assert path.is_file()
    assert path.name == "prices.json"


def test_month_year_hours_defaults(tmp_path: Path) -> None:
    path = tmp_path / "prices.json"
    path.write_text(
        json.dumps({"compute_platforms": {}, "meta": {}}),
        encoding="utf-8",
    )
    cat = load_catalog(path)
    assert cat.month_hours == 730
    assert cat.year_hours == 8760
