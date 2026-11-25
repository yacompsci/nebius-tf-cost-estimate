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
