from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from nebius_tf_cost.config import EstimateConfig, PlannedUsage
from nebius_tf_cost.estimate import parse_preset, run_estimate
from nebius_tf_cost.terraform import detect_and_load
from nebius_tf_cost.usage import load_usage


def test_parse_preset() -> None:
    assert parse_preset("2vcpu-8gb") == (0, 2, 8)
    assert parse_preset("1gpu-16vcpu-200gb") == (1, 16, 200)
    assert parse_preset("8gpu-128vcpu-1600gb") == (8, 128, 1600)


def test_parse_preset_invalid() -> None:
    with pytest.raises(ValueError):
        parse_preset("huge")


def test_cpu_instance_monthly(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_create.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_create.json",
        period="month",
        currency="USD",
        mode="plan",
    )
    usage = load_usage(fixtures_dir / "usage.yaml")
    result = run_estimate(doc, catalog, cfg, usage)

    # cpu-e2: 2*0.012 + 8*0.0045 = 0.060 $/hour * 730 = 43.8
    # boot 100 GiB * 0.071 = 7.1
    # disk 500 * 0.071 = 35.5
    # bucket: 1024 * 0.0147 = 15.0528 storage
    #   + 10 * 0.0034 = 0.034 class A
    #   + 50 * 0.00028 = 0.014 class B
    #   + 100 * 0.015 = 1.5 egress
    # vpc free
    assert result.before_total == 0.0
    instance = next(i for i in result.after_items if i.address.endswith(".training"))
    assert instance.amount == pytest.approx(43.8 + 7.1, rel=1e-6)
    disk = next(i for i in result.after_items if i.address.endswith(".data"))
    assert disk.amount == pytest.approx(35.5, rel=1e-6)
    bucket = next(i for i in result.after_items if "bucket" in i.address)
    assert bucket.amount == pytest.approx(15.0528 + 0.034 + 0.014 + 1.5, rel=1e-6)
    assert result.after_total == pytest.approx(
        (43.8 + 7.1) + 35.5 + (15.0528 + 0.034 + 0.014 + 1.5), rel=1e-6
    )


def test_plan_delta(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_delta.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_delta.json",
        period="hour",
        currency="USD",
        mode="plan",
    )
    result = run_estimate(doc, catalog, cfg, PlannedUsage())
    # before: 2*0.012+8*0.0045=0.06 + boot 64*0.071/730
    # after:  4*0.012+16*0.0045=0.12 + same boot
    before_compute = 0.06
    after_compute = 0.12
    boot_hour = (64 * 0.071) / 730
    disk_hour = (200 * 0.071) / 730
    assert result.before_total == pytest.approx(before_compute + boot_hour + disk_hour, rel=1e-6)
    assert result.after_total == pytest.approx(after_compute + boot_hour + disk_hour, rel=1e-6)
    assert result.delta == pytest.approx(0.06, rel=1e-6)


def test_gpu_state_hourly(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "state.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "state.json",
        period="hour",
        currency="USD",
        mode="total",
    )
    result = run_estimate(doc, catalog, cfg, PlannedUsage())
    # H100 gpu-only: 1 * 4.5 + boot 100*0.071/730
    boot = (100 * 0.071) / 730
    assert result.after_total == pytest.approx(4.5 + boot, rel=1e-6)
    assert result.delta == result.after_total  # before empty


def test_period_scales_linearly(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "state.json")
    usage = PlannedUsage()
    hour_cfg = EstimateConfig(input_path=fixtures_dir / "state.json", period="hour", mode="total")
    month_cfg = replace(hour_cfg, period="month")
    hour = run_estimate(doc, catalog, hour_cfg, usage)
    month = run_estimate(doc, catalog, month_cfg, usage)
    assert month.after_total == pytest.approx(hour.after_total * 730, rel=1e-6)


def test_mk8s_node_group_and_serverless(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_coverage.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_coverage.json",
        period="month",
        currency="USD",
        mode="plan",
    )
    result = run_estimate(doc, catalog, cfg, PlannedUsage())

    workers = next(i for i in result.after_items if i.address.endswith(".workers"))
    # 3 nodes × (cpu-e2 2vcpu-8gb = 0.06 $/h × 730 = 43.8) + boot 64×0.071
    per_node = 43.8 + (64 * 0.071)
    assert workers.amount == pytest.approx(3 * per_node, rel=1e-6)
    assert workers.unknown is False

    endpoint = next(i for i in result.after_items if i.address.endswith(".api"))
    # H100 gpu-only: 4.5 $/h × 730 + disk 100×0.071
    assert endpoint.amount == pytest.approx(4.5 * 730 + 100 * 0.071, rel=1e-6)

    hdd = next(i for i in result.after_items if i.address.endswith(".hdd"))
    assert hdd.unknown is True
    assert hdd.amount == 0.0
    assert any("NETWORK_HDD" in w for w in hdd.warnings)

    weka = next(i for i in result.after_items if i.address.endswith(".weka"))
    assert weka.unknown is True
    assert any("WEKA" in w for w in weka.warnings)

    nvl = next(i for i in result.after_items if i.address.endswith(".gb"))
    assert nvl.amount == 0.0
    assert nvl.unknown is False  # free control-plane handle

    cluster = next(i for i in result.after_items if i.address.endswith(".main"))
    assert cluster.amount == 0.0


def test_node_group_usage_override(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_coverage.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_coverage.json",
        period="hour",
        currency="USD",
        mode="plan",
    )
    usage = PlannedUsage(by_address={"nebius_mk8s_v1_node_group.workers": {"node_count": 1}})
    result = run_estimate(doc, catalog, cfg, usage)
    workers = next(i for i in result.after_items if i.address.endswith(".workers"))
    assert workers.amount == pytest.approx(0.06 + (64 * 0.071) / 730, rel=1e-6)


def test_serverless_running_fraction(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_coverage.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_coverage.json",
        period="hour",
        currency="USD",
        mode="plan",
    )
    usage = PlannedUsage(by_address={"nebius_ai_v1_endpoint.api": {"running_fraction": 0.0}})
    result = run_estimate(doc, catalog, cfg, usage)
    endpoint = next(i for i in result.after_items if i.address.endswith(".api"))
    assert endpoint.amount == pytest.approx(0.0, abs=1e-12)


def test_ai_gpu_selector_mapping(catalog, fixtures_dir: Path) -> None:
    from nebius_tf_cost.estimate import estimate_ai_endpoint
    from nebius_tf_cost.terraform import ResourceSnapshot

    snap = ResourceSnapshot(
        address="nebius_ai_v1_endpoint.sel",
        type="nebius_ai_v1_endpoint",
        name="sel",
        provider_name="registry.terraform.io/nebius/nebius",
        side="after",
        values={
            "gpu": ["H100:2"],
            "disk": {"type": "NETWORK_SSD", "size_gibibytes": 50},
        },
    )
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_coverage.json",
        period="hour",
        currency="USD",
        mode="plan",
    )
    item = estimate_ai_endpoint(snap, catalog, cfg, PlannedUsage())
    assert item.unknown is False
    assert item.amount == pytest.approx(2 * 4.5 + (50 * 0.071) / 730, rel=1e-6)
    assert item.amount_min == item.amount_max


def test_minmax_spot_and_autoscaling(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_minmax.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_minmax.json",
        period="hour",
        currency="USD",
        mode="plan",
    )
    result = run_estimate(doc, catalog, cfg, PlannedUsage())

    spot = next(i for i in result.after_items if i.address.endswith(".spot_gpu"))
    boot = (100 * 0.071) / 730
    # H100 floor 0.79, on-demand 4.5
    assert spot.amount_min == pytest.approx(0.79 + boot, rel=1e-6)
    assert spot.amount_max == pytest.approx(4.5 + boot, rel=1e-6)
    assert spot.amount == spot.amount_max

    nodes = next(i for i in result.after_items if i.address.endswith(".autoscale"))
    per_node = 0.06 + (64 * 0.071) / 730
    assert nodes.amount_min == pytest.approx(1 * per_node, rel=1e-6)
    assert nodes.amount_max == pytest.approx(4 * per_node, rel=1e-6)

    assert result.after_total_min == pytest.approx(spot.amount_min + nodes.amount_min, rel=1e-6)
    assert result.after_total_max == pytest.approx(spot.amount_max + nodes.amount_max, rel=1e-6)
    assert result.after_total == result.after_total_max
    assert result.delta_min == result.after_total_min  # before empty
    assert result.delta_max == result.after_total_max


def test_minmax_equal_for_on_demand(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_create.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_create.json",
        period="month",
        currency="USD",
        mode="plan",
    )
    usage = load_usage(fixtures_dir / "usage.yaml")
    result = run_estimate(doc, catalog, cfg, usage)
    assert result.after_total_min == pytest.approx(result.after_total_max, rel=1e-9)
    assert result.delta_min == pytest.approx(result.delta_max, rel=1e-9)


def test_preemptible_policy_floor_collapses_bounds(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_minmax.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_minmax.json",
        period="hour",
        currency="USD",
        mode="plan",
        preemptible_policy="floor",
    )
    result = run_estimate(doc, catalog, cfg, PlannedUsage())
    spot = next(i for i in result.after_items if i.address.endswith(".spot_gpu"))
    boot = (100 * 0.071) / 730
    assert spot.amount_min == pytest.approx(0.79 + boot, rel=1e-6)
    assert spot.amount_max == pytest.approx(0.79 + boot, rel=1e-6)
    assert spot.amount == spot.amount_max


def test_fixed_l40s_preemptible_uses_list_rates(catalog, fixtures_dir: Path) -> None:
    """L40S has fixed published preemptible rates — min == max at those rates."""
    doc = detect_and_load(fixtures_dir / "plan_spot_l40s.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_spot_l40s.json",
        period="hour",
        currency="USD",
        mode="plan",
    )
    result = run_estimate(doc, catalog, cfg, PlannedUsage())
    spot = next(i for i in result.after_items if i.address.endswith(".spot_l40s"))
    # 1 GPU * 0.65 + 16 CPU * 0.006 + 64 GiB * 0.0016 = 0.65 + 0.096 + 0.1024 = 0.8484
    compute = 0.65 + 16 * 0.006 + 64 * 0.0016
    boot = (100 * 0.071) / 730
    assert spot.amount_min == pytest.approx(compute + boot, rel=1e-6)
    assert spot.amount_max == pytest.approx(compute + boot, rel=1e-6)
    # Must not use on-demand component rates as the ceiling.
    on_demand = 1.35 + 16 * 0.012 + 64 * 0.0032
    assert spot.amount_max < on_demand + boot


def test_catalog_spot_floors_match_published_docs(catalog) -> None:
    """Floors/fixed rates must match compute pricing 'From $X' / L40S rows."""
    platforms = catalog.raw["compute_platforms"]
    expected_floors = {
        "gpu-b300-sxm": 0.99,
        "gpu-b200-sxm": 0.99,
        "gpu-b200-sxm-a": 0.99,
        "gpu-h200-sxm": 0.79,
        "gpu-h100-sxm": 0.79,
        "gpu-rtx6000": 0.79,
        "gpu-rtx6000-a": 0.79,
    }
    for plat, floor in expected_floors.items():
        assert platforms[plat]["preemptible_floor_usd"] == pytest.approx(floor)
        assert platforms[plat]["spot_mode"] == "dynamic_floor"
        assert platforms[plat]["preemptible_floor_ils"] == pytest.approx(round(floor * 3.1, 3))

    for plat in ("gpu-l40s-a", "gpu-l40s-d"):
        assert platforms[plat]["spot_mode"] == "fixed"
        assert platforms[plat]["preemptible"]["gpu_hour_usd"] == pytest.approx(0.65)

    # CPU platforms must not invent spot floors.
    for plat in ("cpu-d3", "cpu-e2"):
        assert "preemptible_floor_usd" not in platforms[plat]
        assert "preemptible" not in platforms[plat]

    sources = catalog.raw["meta"]["sources"]
    assert "https://docs.nebius.com/signup-billing/spot-prices.md" in sources
    assert "https://docs.nebius.com/signup-billing/pricing-policy.md" in sources
