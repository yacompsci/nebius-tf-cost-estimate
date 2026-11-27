"""Tests for ``--catalog-price`` parsing and catalog merge."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from nebius_tf_cost.catalog import PriceCatalog, load_catalog
from nebius_tf_cost.catalog_price import (
    KNOWN_BARE_KEYS,
    apply_catalog_price_overrides,
    parse_catalog_price_cli_overrides,
)
from nebius_tf_cost.cli import main
from nebius_tf_cost.config import EstimateConfig, PlannedUsage
from nebius_tf_cost.errors import ExitCode, UsageError
from nebius_tf_cost.estimate import estimate_filesystem, run_estimate
from nebius_tf_cost.terraform import ResourceSnapshot, detect_and_load

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "prices.json"


def test_parse_catalog_price_none_and_empty() -> None:
    assert parse_catalog_price_cli_overrides(None) == []
    assert parse_catalog_price_cli_overrides([]) == []


def test_parse_catalog_price_requires_equals() -> None:
    with pytest.raises(UsageError, match="must be KEY=VALUE"):
        parse_catalog_price_cli_overrides(["filesystem.VAST"])


def test_parse_catalog_price_empty_key() -> None:
    with pytest.raises(UsageError, match="empty catalog price key"):
        parse_catalog_price_cli_overrides(["=0.1"])


def test_parse_catalog_price_non_numeric() -> None:
    with pytest.raises(UsageError, match="must be numeric"):
        parse_catalog_price_cli_overrides(["filesystem.VAST=abc"])


def test_parse_catalog_price_negative_rejected() -> None:
    with pytest.raises(UsageError, match="non-negative"):
        parse_catalog_price_cli_overrides(["filesystem.VAST=-1"])


def test_parse_catalog_price_unknown_key() -> None:
    with pytest.raises(UsageError, match="unknown catalog price key"):
        parse_catalog_price_cli_overrides(["bogus.thing=1"])


def test_parse_catalog_price_network_hdd_bare_requires_prefix() -> None:
    with pytest.raises(UsageError, match="disk\\.NETWORK_HDD|filesystem\\.NETWORK_HDD"):
        parse_catalog_price_cli_overrides(["NETWORK_HDD=0.05"])


def test_parse_known_bare_keys() -> None:
    assert "VAST" in KNOWN_BARE_KEYS
    assert "WEKA" in KNOWN_BARE_KEYS
    assert "GB200" in KNOWN_BARE_KEYS
    assert "GB300" in KNOWN_BARE_KEYS
    pairs = parse_catalog_price_cli_overrides(
        ["VAST=0.12", "GB200=25", "filesystem.WEKA=0.1", "disk.NETWORK_HDD=0.04"]
    )
    assert [(p.section, p.name, p.rate) for p in pairs] == [
        ("filesystem", "VAST", 0.12),
        ("platform", "GB200", 25.0),
        ("filesystem", "WEKA", 0.1),
        ("disk", "NETWORK_HDD", 0.04),
    ]


def test_apply_fills_null_filesystem_and_disk() -> None:
    cat = load_catalog(CATALOG)
    # VAST is null in catalog; NETWORK_HDD disk is null.
    overrides = parse_catalog_price_cli_overrides(["filesystem.VAST=0.12", "disk.NETWORK_HDD=0.05"])
    merged, warnings = apply_catalog_price_overrides(cat, overrides, currency="USD")
    assert warnings == []
    assert merged.money(merged.raw["filesystem_gib_month"]["VAST"], "USD") == pytest.approx(0.12)
    assert merged.money(merged.raw["disks_gib_month"]["NETWORK_HDD"], "USD") == pytest.approx(0.05)
    # ILS left untouched / still null for VAST
    assert merged.money(merged.raw["filesystem_gib_month"]["VAST"], "ILS") is None


def test_apply_creates_missing_platform() -> None:
    cat = load_catalog(CATALOG)
    assert cat.platform("GB200") is None
    overrides = parse_catalog_price_cli_overrides(["platform.GB200=42.5"])
    merged, warnings = apply_catalog_price_overrides(cat, overrides, currency="USD")
    assert warnings == []
    plat = merged.platform("GB200")
    assert plat is not None
    assert plat["billing"] == "gpu_only"
    assert plat["gpu_hour_usd"] == pytest.approx(42.5)


def test_apply_override_existing_warns() -> None:
    cat = load_catalog(CATALOG)
    # NETWORK_SSD already priced publicly
    overrides = parse_catalog_price_cli_overrides(["disk.NETWORK_SSD=0.99"])
    merged, warnings = apply_catalog_price_overrides(cat, overrides, currency="USD")
    assert len(warnings) == 1
    assert "overriding" in warnings[0].lower()
    assert "NETWORK_SSD" in warnings[0]
    assert merged.money(merged.raw["disks_gib_month"]["NETWORK_SSD"], "USD") == pytest.approx(0.99)


def test_apply_does_not_mutate_original() -> None:
    cat = load_catalog(CATALOG)
    before = copy.deepcopy(cat.raw)
    overrides = parse_catalog_price_cli_overrides(["VAST=0.2"])
    apply_catalog_price_overrides(cat, overrides, currency="USD")
    assert cat.raw == before


def test_weka_vendored_in_catalog() -> None:
    cat = load_catalog(CATALOG)
    rate = cat.money(cat.raw["filesystem_gib_month"]["WEKA"], "USD")
    assert rate == pytest.approx(0.10)
    sources = cat.raw["meta"]["sources"]
    assert "https://nebius.com/prices" in sources
    assert "WEKA" not in cat.raw.get("unpriced_gaps", {})


def test_estimate_vast_with_catalog_price(catalog: PriceCatalog) -> None:
    cfg = EstimateConfig(
        input_path=Path("."),
        period="month",
        currency="USD",
        mode="total",
    )
    overrides = parse_catalog_price_cli_overrides(["VAST=0.15"])
    merged, _ = apply_catalog_price_overrides(catalog, overrides, currency="USD")
    item = estimate_filesystem(
        ResourceSnapshot(
            address="nebius_compute_v1_filesystem.vast",
            type="nebius_compute_v1_filesystem",
            name="vast",
            provider_name="registry.terraform.io/nebius/nebius",
            side="after",
            values={"type": "VAST", "size_gibibytes": 100},
        ),
        merged,
        cfg,
        PlannedUsage(),
    )
    assert item.unknown is False
    assert item.amount == pytest.approx(0.15 * 100, rel=1e-6)


def test_cli_catalog_price_clears_strict_unknown(fixtures_dir: Path, capsys) -> None:
    """NETWORK_HDD unknown under strict-check; --catalog-price fills the hole."""
    # plan_coverage has NETWORK_HDD + (now priced) WEKA; after WEKA vendor only HDD is unknown.
    fail = main(
        [
            "estimate",
            "--plan",
            str(fixtures_dir / "plan_coverage.json"),
            "--catalog",
            str(CATALOG),
            "--period",
            "hour",
            "--strict-check",
        ]
    )
    assert fail == int(ExitCode.STRICT_CHECK_FAILED)
    capsys.readouterr()

    ok = main(
        [
            "estimate",
            "--plan",
            str(fixtures_dir / "plan_coverage.json"),
            "--catalog",
            str(CATALOG),
            "--period",
            "hour",
            "--catalog-price",
            "disk.NETWORK_HDD=0.05",
            "--strict-check",
        ]
    )
    assert ok == int(ExitCode.OK)
    out = capsys.readouterr().out
    assert "hdd" in out.lower() or "After:" in out


def test_cli_catalog_price_override_warns(fixtures_dir: Path, capsys) -> None:
    code = main(
        [
            "estimate",
            "--plan",
            str(fixtures_dir / "plan_coverage.json"),
            "--catalog",
            str(CATALOG),
            "--period",
            "hour",
            "--catalog-price",
            "filesystem.NETWORK_SSD=0.99",
            "--catalog-price",
            "disk.NETWORK_HDD=0.05",
            "--json",
        ]
    )
    assert code == int(ExitCode.OK)
    payload = json.loads(capsys.readouterr().out)
    assert any("overriding" in w.lower() for w in payload["warnings"])


def test_run_estimate_weka_priced(catalog: PriceCatalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_coverage.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_coverage.json",
        period="month",
        currency="USD",
        mode="plan",
    )
    result = run_estimate(doc, catalog, cfg, PlannedUsage())
    weka = next(i for i in result.after_items if i.address.endswith(".weka"))
    assert weka.unknown is False
    assert weka.amount == pytest.approx(0.10 * 10240, rel=1e-6)
