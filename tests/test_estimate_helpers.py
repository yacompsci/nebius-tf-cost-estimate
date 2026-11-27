from __future__ import annotations

from pathlib import Path

import pytest

from nebius_tf_cost.config import EstimateConfig, PlannedUsage
from nebius_tf_cost.estimate import (
    LineItem,
    _is_preemptible,
    _size_gib,
    _sum_items_max,
    _sum_items_min,
    estimate_ai_endpoint,
    estimate_filesystem,
    estimate_free,
    estimate_resource,
    estimate_snapshot,
    estimate_unknown,
    run_estimate,
)
from nebius_tf_cost.terraform import ResourceSnapshot, TerraformDocument, detect_and_load


def test_size_gib_unit_variants() -> None:
    assert _size_gib({"size_gibibytes": 10}) == 10.0
    assert _size_gib({"size_mebibytes": 2048}) == pytest.approx(2.0)
    assert _size_gib({"size_kibibytes": 1024 * 1024}) == pytest.approx(1.0)
    assert _size_gib({"size_bytes": 1024**3}) == pytest.approx(1.0)
    assert _size_gib({}) is None


def test_is_preemptible_variants() -> None:
    assert _is_preemptible({"preemptible": True})
    assert _is_preemptible({"preemptible": {"on_preemption": "STOP"}})
    assert _is_preemptible({"follows_spot_price": True})
    assert _is_preemptible({"spot_pricing_policy": "FOLLOW"})
    assert _is_preemptible({"follows_spot_price": {"enabled": True}})
    assert not _is_preemptible({})
    assert not _is_preemptible({"on_demand": {"x": 1}})


def test_line_item_normalizes_bounds() -> None:
    item = LineItem(
        address="a",
        resource_type="t",
        side="after",
        amount=5.0,
        currency="USD",
        period="hour",
        details="",
        amount_min=1.0,
        amount_max=9.0,
    )
    assert item.amount == 9.0
    assert item.amount_min == 1.0
    assert item.amount_max == 9.0

    plain = LineItem(
        address="b",
        resource_type="t",
        side="after",
        amount=3.0,
        currency="USD",
        period="hour",
        details="",
    )
    assert plain.amount_min == 3.0
    assert plain.amount_max == 3.0


def test_sum_items_helpers() -> None:
    items = [
        LineItem(
            address="a",
            resource_type="t",
            side="after",
            amount=10.0,
            currency="USD",
            period="hour",
            details="",
            amount_min=2.0,
            amount_max=10.0,
        ),
        LineItem(
            address="b",
            resource_type="t",
            side="after",
            amount=4.0,
            currency="USD",
            period="hour",
            details="",
            amount_min=1.0,
            amount_max=4.0,
        ),
    ]
    assert _sum_items_min(items) == pytest.approx(3.0)
    assert _sum_items_max(items) == pytest.approx(14.0)


def test_estimate_free_and_unknown(catalog, fixtures_dir: Path) -> None:
    cfg = EstimateConfig(input_path=fixtures_dir / "plan_create.json", period="hour")
    free_snap = ResourceSnapshot(
        address="nebius_vpc_v1_subnet.main",
        type="nebius_vpc_v1_subnet",
        name="main",
        provider_name="registry.terraform.io/nebius/nebius",
        side="after",
        values={},
    )
    free = estimate_free(free_snap, catalog, cfg, PlannedUsage())
    assert free.amount == 0.0
    assert free.unknown is False

    unknown_snap = ResourceSnapshot(
        address="nebius_something_v1_widget.x",
        type="nebius_something_v1_widget",
        name="x",
        provider_name="registry.terraform.io/nebius/nebius",
        side="after",
        values={},
    )
    unk = estimate_unknown(unknown_snap, catalog, cfg, PlannedUsage())
    assert unk.unknown is True
    routed = estimate_resource(unknown_snap, catalog, cfg, PlannedUsage())
    assert routed.unknown is True


def test_estimate_snapshot_and_filesystem(catalog, fixtures_dir: Path) -> None:
    cfg = EstimateConfig(input_path=fixtures_dir / "plan_create.json", period="month")
    snap = ResourceSnapshot(
        address="nebius_compute_v1_disk_snapshot.s",
        type="nebius_compute_v1_disk_snapshot",
        name="s",
        provider_name="registry.terraform.io/nebius/nebius",
        side="after",
        values={"size_gibibytes": 50},
    )
    item = estimate_snapshot(snap, catalog, cfg, PlannedUsage())
    rate = catalog.money(catalog.raw.get("disk_snapshot_gib_month"), "USD") or 0.0
    assert item.amount == pytest.approx(rate * 50)

    missing = estimate_snapshot(
        ResourceSnapshot(
            address="nebius_compute_v1_disk_snapshot.m",
            type="nebius_compute_v1_disk_snapshot",
            name="m",
            provider_name="registry.terraform.io/nebius/nebius",
            side="after",
            values={},
        ),
        catalog,
        cfg,
        PlannedUsage(),
    )
    assert missing.unknown is True

    fs = estimate_filesystem(
        ResourceSnapshot(
            address="nebius_compute_v1_filesystem.fs",
            type="nebius_compute_v1_filesystem",
            name="fs",
            provider_name="registry.terraform.io/nebius/nebius",
            side="after",
            values={"type": "NETWORK_SSD", "size_gibibytes": 100},
        ),
        catalog,
        cfg,
        PlannedUsage(),
    )
    assert fs.unknown is False
    assert fs.amount > 0

    weka = estimate_filesystem(
        ResourceSnapshot(
            address="nebius_compute_v1_filesystem.weka",
            type="nebius_compute_v1_filesystem",
            name="weka",
            provider_name="registry.terraform.io/nebius/nebius",
            side="after",
            values={"type": "WEKA", "size_gibibytes": 100},
        ),
        catalog,
        cfg,
        PlannedUsage(),
    )
    assert weka.unknown is False
    assert weka.amount == pytest.approx(0.10 * 100, rel=1e-6)

    vast = estimate_filesystem(
        ResourceSnapshot(
            address="nebius_compute_v1_filesystem.vast",
            type="nebius_compute_v1_filesystem",
            name="vast",
            provider_name="registry.terraform.io/nebius/nebius",
            side="after",
            values={"type": "VAST", "size_gibibytes": 100},
        ),
        catalog,
        cfg,
        PlannedUsage(),
    )
    assert vast.unknown is True


def test_preemptible_reject_policy(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_minmax.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_minmax.json",
        period="hour",
        mode="plan",
        preemptible_policy="reject",
    )
    result = run_estimate(doc, catalog, cfg, PlannedUsage())
    spot = next(i for i in result.after_items if i.address.endswith(".spot_gpu"))
    assert spot.unknown is True
    assert spot.amount == 0.0


def test_local_ssd_usage_priced(catalog, fixtures_dir: Path) -> None:
    snap = ResourceSnapshot(
        address="nebius_compute_v1_instance.local",
        type="nebius_compute_v1_instance",
        name="local",
        provider_name="registry.terraform.io/nebius/nebius",
        side="after",
        values={
            "resources": {"platform": "cpu-e2", "preset": "2vcpu-8gb"},
            "boot_disk": {"managed_disk": {"spec": {"type": "NETWORK_SSD", "size_gibibytes": 64}}},
            "local_disks": {"passthrough_group": {"requested": True}},
        },
    )
    cfg = EstimateConfig(input_path=fixtures_dir / "plan_create.json", period="month")
    usage = PlannedUsage(by_address={snap.address: {"local_ssd_gib": 100}})
    item = estimate_resource(snap, catalog, cfg, usage)
    assert "local_ssd" in item.details
    assert item.amount > 43.8  # compute alone


def test_ai_endpoint_cpu_selector_without_preset_unknown(catalog, fixtures_dir: Path) -> None:
    snap = ResourceSnapshot(
        address="nebius_ai_v1_endpoint.cpu",
        type="nebius_ai_v1_endpoint",
        name="cpu",
        provider_name="registry.terraform.io/nebius/nebius",
        side="after",
        values={"cpu": ["amd:16"]},
    )
    cfg = EstimateConfig(input_path=fixtures_dir / "plan_create.json", period="hour")
    item = estimate_ai_endpoint(snap, catalog, cfg, PlannedUsage())
    assert item.unknown is True


def test_result_total_aliases(catalog, fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_create.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "plan_create.json",
        period="hour",
        mode="total",
    )
    # Minimal empty usage — totals still defined.
    empty = TerraformDocument(
        kind="plan",
        path=fixtures_dir / "plan_create.json",
        resources_before=[],
        resources_after=doc.resources_after[:1],
        raw={},
    )
    result = run_estimate(empty, catalog, cfg, PlannedUsage())
    assert result.total == result.after_total_max
    assert result.total_min == result.after_total_min
    assert result.total_max == result.after_total_max
    assert result.before_total == result.before_total_max
    assert result.delta == result.delta_max
