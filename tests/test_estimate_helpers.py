from __future__ import annotations

from pathlib import Path

import pytest

from nebius_tf_cost.config import EstimateConfig, PlannedUsage
from nebius_tf_cost.estimate import (
    LineItem,
    _inline_managed_boot_disk_spec,
    _inline_managed_disk_spec,
    _is_preemptible,
    _size_gib,
    _sum_items_max,
    _sum_items_min,
    estimate_ai_endpoint,
    estimate_filesystem,
    estimate_free,
    estimate_instance,
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


def test_inline_managed_disk_spec() -> None:
    sized = {
        "attach_mode": "READ_WRITE",
        "managed_disk": {
            "name": "boot",
            "spec": {"type": "NETWORK_SSD", "size_gibibytes": 64},
        },
    }
    assert _inline_managed_disk_spec(sized) == {
        "type": "NETWORK_SSD",
        "size_gibibytes": 64,
    }
    # Alias used by boot-disk tests / callers
    assert _inline_managed_boot_disk_spec(sized) == _inline_managed_disk_spec(sized)
    # existing_disk id-only → ignore
    assert (
        _inline_managed_disk_spec(
            {"attach_mode": "READ_WRITE", "existing_disk": {"id": "computdisk-1"}}
        )
        is None
    )
    # empty / null managed_disk (common alongside existing_disk in state) → ignore
    assert _inline_managed_disk_spec({"managed_disk": None}) is None
    assert _inline_managed_disk_spec({"managed_disk": {}}) is None
    # name-only managed stub without spec/size → ignore
    assert _inline_managed_disk_spec({"managed_disk": {"name": "boot"}}) is None
    assert (
        _inline_managed_disk_spec(
            {"managed_disk": {"name": "boot", "spec": {"type": "NETWORK_SSD"}}}
        )
        is None
    )
    assert _inline_managed_disk_spec(None) is None
    assert _inline_managed_disk_spec({}) is None


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


def test_existing_boot_disk_ignored_on_instance(catalog, fixtures_dir: Path) -> None:
    """existing_disk / size-less refs: compute only, no missing-size warnings."""
    cfg = EstimateConfig(input_path=fixtures_dir / "plan_create.json", period="month")
    compute_only = 43.8  # cpu-e2 2vcpu-8gb monthly
    for boot in (
        {"attach_mode": "READ_WRITE", "existing_disk": {"id": "computdisk-1"}},
        {
            "attach_mode": "READ_WRITE",
            "existing_disk": {"id": "computdisk-1"},
            "managed_disk": None,
        },
        {"attach_mode": "READ_WRITE", "managed_disk": {"name": "boot-only"}},
        {
            "attach_mode": "READ_WRITE",
            "managed_disk": {"name": "boot", "spec": {"type": "NETWORK_SSD"}},
        },
    ):
        snap = ResourceSnapshot(
            address="nebius_compute_v1_instance.ref",
            type="nebius_compute_v1_instance",
            name="ref",
            provider_name="registry.terraform.io/nebius/nebius",
            side="after",
            values={
                "resources": {"platform": "cpu-e2", "preset": "2vcpu-8gb"},
                "boot_disk": boot,
            },
        )
        item = estimate_instance(snap, catalog, cfg, PlannedUsage())
        assert item.amount == pytest.approx(compute_only, rel=1e-6)
        assert "boot_disk=" not in item.details
        assert not any("missing size" in w for w in item.warnings)
        assert not any("boot disk price unknown" in w for w in item.warnings)
        assert item.unknown is False


def test_inline_boot_disk_still_priced_on_instance(catalog, fixtures_dir: Path) -> None:
    snap = ResourceSnapshot(
        address="nebius_compute_v1_instance.inline",
        type="nebius_compute_v1_instance",
        name="inline",
        provider_name="registry.terraform.io/nebius/nebius",
        side="after",
        values={
            "resources": {"platform": "cpu-e2", "preset": "2vcpu-8gb"},
            "boot_disk": {
                "managed_disk": {
                    "name": "inline-boot",
                    "spec": {"type": "NETWORK_SSD", "size_gibibytes": 100},
                }
            },
        },
    )
    cfg = EstimateConfig(input_path=fixtures_dir / "plan_create.json", period="month")
    item = estimate_instance(snap, catalog, cfg, PlannedUsage())
    assert item.amount == pytest.approx(43.8 + 7.1, rel=1e-6)
    assert "boot_disk=7.100000" in item.details


def test_inline_secondary_managed_disk_priced_on_instance(catalog, fixtures_dir: Path) -> None:
    """Sized secondary managed_disk.spec folds into the instance (sibling of boot #21)."""
    snap = ResourceSnapshot(
        address="nebius_compute_v1_instance.with_data",
        type="nebius_compute_v1_instance",
        name="with_data",
        provider_name="registry.terraform.io/nebius/nebius",
        side="after",
        values={
            "resources": {"platform": "cpu-e2", "preset": "2vcpu-8gb"},
            "boot_disk": {
                "managed_disk": {
                    "name": "boot",
                    "spec": {"type": "NETWORK_SSD", "size_gibibytes": 100},
                }
            },
            "secondary_disks": [
                {
                    "attach_mode": "READ_WRITE",
                    "managed_disk": {
                        "name": "data",
                        "spec": {"type": "NETWORK_SSD", "size_gibibytes": 50},
                    },
                },
                {
                    # second sized secondary
                    "managed_disk": {
                        "spec": {
                            "type": "NETWORK_SSD_NON_REPLICATED",
                            "size_gibibytes": 100,
                        }
                    }
                },
            ],
        },
    )
    cfg = EstimateConfig(input_path=fixtures_dir / "plan_create.json", period="month")
    item = estimate_instance(snap, catalog, cfg, PlannedUsage())
    boot = 7.1  # 100 GiB NETWORK_SSD
    sec = 50 * 0.071 + 100 * 0.053  # sized secondaries
    assert item.amount == pytest.approx(43.8 + boot + sec, rel=1e-6)
    assert "boot_disk=7.100000" in item.details
    assert f"secondary_disks={sec:.6f}" in item.details
    assert not any("missing size" in w for w in item.warnings)


def test_existing_secondary_disk_ignored_on_instance(catalog, fixtures_dir: Path) -> None:
    """secondary_disks existing_disk / size-less stubs: silent ignore (same as boot #21)."""
    cfg = EstimateConfig(input_path=fixtures_dir / "plan_create.json", period="month")
    compute_only = 43.8
    for secondary in (
        [{"attach_mode": "READ_WRITE", "existing_disk": {"id": "computdisk-data"}}],
        [
            {
                "existing_disk": {"id": "computdisk-data"},
                "managed_disk": None,
            }
        ],
        [{"managed_disk": {"name": "data-only"}}],
        [{"managed_disk": {"name": "data", "spec": {"type": "NETWORK_SSD"}}}],
        # mix: one attach-by-ref + one size-less — still compute only
        [
            {"existing_disk": {"id": "computdisk-a"}},
            {"managed_disk": {"spec": {"type": "NETWORK_SSD"}}},
        ],
    ):
        snap = ResourceSnapshot(
            address="nebius_compute_v1_instance.sec_ref",
            type="nebius_compute_v1_instance",
            name="sec_ref",
            provider_name="registry.terraform.io/nebius/nebius",
            side="after",
            values={
                "resources": {"platform": "cpu-e2", "preset": "2vcpu-8gb"},
                "boot_disk": {"existing_disk": {"id": "computdisk-boot"}},
                "secondary_disks": secondary,
            },
        )
        item = estimate_instance(snap, catalog, cfg, PlannedUsage())
        assert item.amount == pytest.approx(compute_only, rel=1e-6)
        assert "boot_disk=" not in item.details
        assert "secondary_disks=" not in item.details
        assert not any("missing size" in w for w in item.warnings)
        assert not any("disk price unknown" in w for w in item.warnings)
        assert item.unknown is False


def test_secondary_existing_disk_no_double_count_with_disk_resource(
    catalog, fixtures_dir: Path
) -> None:
    """Disk resource + secondary existing_disk attach: disk once, instance compute only."""
    disk_snap = ResourceSnapshot(
        address="nebius_compute_v1_disk.data",
        type="nebius_compute_v1_disk",
        name="data",
        provider_name="registry.terraform.io/nebius/nebius",
        side="after",
        values={"type": "NETWORK_SSD", "size_gibibytes": 100, "id": "computdisk-data"},
    )
    inst_snap = ResourceSnapshot(
        address="nebius_compute_v1_instance.app",
        type="nebius_compute_v1_instance",
        name="app",
        provider_name="registry.terraform.io/nebius/nebius",
        side="after",
        values={
            "resources": {"platform": "cpu-e2", "preset": "2vcpu-8gb"},
            "boot_disk": {
                "managed_disk": {
                    "spec": {"type": "NETWORK_SSD", "size_gibibytes": 100},
                }
            },
            "secondary_disks": [{"existing_disk": {"id": "computdisk-data"}}],
        },
    )
    cfg = EstimateConfig(input_path=fixtures_dir / "plan_create.json", period="month")
    usage = PlannedUsage()
    disk_item = estimate_resource(disk_snap, catalog, cfg, usage)
    inst_item = estimate_instance(inst_snap, catalog, cfg, usage)
    disk_cost = 7.1
    compute = 43.8
    assert disk_item.amount == pytest.approx(disk_cost, rel=1e-6)
    assert inst_item.amount == pytest.approx(compute + disk_cost, rel=1e-6)
    assert "boot_disk=" in inst_item.details
    assert "secondary_disks=" not in inst_item.details
    # Combined inventory cost = disk resource + instance (boot folded, secondary not)
    assert disk_item.amount + inst_item.amount == pytest.approx(
        disk_cost + compute + disk_cost, rel=1e-6
    )


def test_attached_boot_disks_no_double_count(catalog, fixtures_dir: Path) -> None:
    """Separate disk resources + existing_disk refs: disks once, no instance warnings."""
    doc = detect_and_load(fixtures_dir / "state_attached_boot.json")
    cfg = EstimateConfig(
        input_path=fixtures_dir / "state_attached_boot.json",
        period="month",
        mode="total",
    )
    result = run_estimate(doc, catalog, cfg, PlannedUsage())

    by_addr = {i.address: i for i in result.after_items}
    disk_cost = 7.1  # 100 GiB * 0.071
    compute = 43.8

    assert by_addr["nebius_compute_v1_disk.gw_boot[0]"].amount == pytest.approx(disk_cost, rel=1e-6)
    assert by_addr["nebius_compute_v1_disk.gw_boot[1]"].amount == pytest.approx(disk_cost, rel=1e-6)
    assert by_addr["nebius_compute_v1_disk.sms_boot"].amount == pytest.approx(disk_cost, rel=1e-6)

    bastion = by_addr["nebius_compute_v1_instance.bastion"]
    assert bastion.amount == pytest.approx(compute + disk_cost, rel=1e-6)
    assert "boot_disk=" in bastion.details

    for addr in (
        "nebius_compute_v1_instance.gw[0]",
        "nebius_compute_v1_instance.gw[1]",
        "nebius_compute_v1_instance.sms",
        "nebius_compute_v1_instance.external_boot",
    ):
        item = by_addr[addr]
        assert item.amount == pytest.approx(compute, rel=1e-6)
        assert "boot_disk=" not in item.details
        assert not any("missing size" in w for w in item.warnings)
        assert not any("boot disk price unknown" in w for w in item.warnings)

    assert not any("missing size" in w for w in result.warnings)
    assert not any("boot disk price unknown" in w for w in result.warnings)
    # 3 separate disks + bastion inline boot + 4 compute-only instances
    assert result.after_total == pytest.approx(
        3 * disk_cost + (compute + disk_cost) + 4 * compute, rel=1e-6
    )


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
