"""Cost estimation engine and resource-type estimators."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nebius_tf_cost.catalog import PriceCatalog
from nebius_tf_cost.config import EstimateConfig, PlannedUsage
from nebius_tf_cost.periods import Period, scale_hourly, scale_monthly
from nebius_tf_cost.terraform import ResourceSnapshot, TerraformDocument

PRESET_RE = re.compile(
    r"^(?:(?P<gpu>\d+)gpu-)?(?P<vcpu>\d+)vcpu-(?P<ram>\d+)(?:gb|gib)?$",
    re.IGNORECASE,
)


@dataclass
class LineItem:
    address: str
    resource_type: str
    side: str
    amount: float
    currency: str
    period: Period
    details: str
    unknown: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class EstimateResult:
    period: Period
    currency: str
    before_total: float
    after_total: float
    delta: float
    before_items: list[LineItem]
    after_items: list[LineItem]
    unknown_resources: list[str]
    warnings: list[str]
    catalog_path: str
    document_kind: str

    @property
    def total(self) -> float:
        """Preferred total for budget gating: after (plan/state) cost."""
        return self.after_total


Estimator = Callable[
    [ResourceSnapshot, PriceCatalog, EstimateConfig, PlannedUsage],
    LineItem,
]


def parse_preset(preset: str) -> tuple[int, int, int]:
    """Return (gpus, vcpus, ram_gib) from a Nebius preset name."""
    m = PRESET_RE.match(preset.strip())
    if not m:
        raise ValueError(f"unrecognized preset: {preset!r}")
    gpu = int(m.group("gpu") or 0)
    vcpu = int(m.group("vcpu"))
    ram = int(m.group("ram"))
    return gpu, vcpu, ram


def _size_gib(values: dict[str, Any]) -> float | None:
    if values.get("size_gibibytes") is not None:
        return float(values["size_gibibytes"])
    if values.get("size_mebibytes") is not None:
        return float(values["size_mebibytes"]) / 1024.0
    if values.get("size_kibibytes") is not None:
        return float(values["size_kibibytes"]) / (1024.0 * 1024.0)
    if values.get("size_bytes") is not None:
        return float(values["size_bytes"]) / (1024.0**3)
    return None


def _is_preemptible(values: dict[str, Any]) -> bool:
    if values.get("preemptible") is True:
        return True
    if values.get("follows_spot_price"):
        return True
    if values.get("spot_pricing_policy"):
        return True
    # Nested forms from provider schemas
    for key in ("follows_spot_price", "spot_pricing_policy", "on_demand"):
        val = values.get(key)
        if isinstance(val, dict) and val and key != "on_demand":
            return True
    return False


def _cur_key(currency: str) -> str:
    return "usd" if currency.upper() == "USD" else "ils"


def estimate_instance(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    values = snap.values
    resources = values.get("resources") or {}
    if not isinstance(resources, dict):
        resources = {}
    platform = str(resources.get("platform") or "")
    preset = str(resources.get("preset") or "")
    warnings: list[str] = []
    platform_prices = catalog.platform(platform)
    if not platform_prices:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=f"unknown platform {platform!r}",
            unknown=True,
            warnings=[f"{snap.address}: no catalog entry for platform {platform!r}"],
        )
    try:
        gpus, vcpus, ram_gib = parse_preset(preset) if preset else (0, 0, 0)
    except ValueError as exc:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=str(exc),
            unknown=True,
            warnings=[f"{snap.address}: {exc}"],
        )

    preemptible = _is_preemptible(values)
    ck = _cur_key(cfg.currency)
    hourly = 0.0
    billing = platform_prices.get("billing")

    if preemptible:
        if cfg.preemptible_policy == "reject":
            return LineItem(
                address=snap.address,
                resource_type=snap.type,
                side=snap.side,
                amount=0.0,
                currency=cfg.currency,
                period=cfg.period,
                details="preemptible rejected by policy",
                unknown=True,
                warnings=[f"{snap.address}: preemptible/spot instance; set --preemptible-policy"],
            )
        if cfg.preemptible_policy == "floor":
            floor = platform_prices.get(f"preemptible_floor_{ck}")
            fixed = platform_prices.get("preemptible")
            if isinstance(fixed, dict):
                if billing == "gpu_only":
                    hourly = float(fixed.get(f"gpu_hour_{ck}", 0)) * max(gpus, 1 if gpus else 0)
                else:
                    hourly = (
                        float(fixed.get(f"gpu_hour_{ck}", 0) or 0) * gpus
                        + float(fixed.get(f"cpu_hour_{ck}", 0) or 0) * vcpus
                        + float(fixed.get(f"ram_gib_hour_{ck}", 0) or 0) * ram_gib
                    )
                warnings.append(f"{snap.address}: using published preemptible rates")
            elif floor is not None and billing == "gpu_only":
                hourly = float(floor) * max(gpus, 1 if gpus else 0)
                warnings.append(
                    f"{snap.address}: spot price is dynamic; using published floor "
                    f"{floor}/{cfg.currency} per GPU-hour"
                )
            else:
                warnings.append(f"{snap.address}: no preemptible rates; falling back to on-demand")
                preemptible = False
        else:
            # on_demand policy: estimate at on-demand even if flagged preemptible
            warnings.append(
                f"{snap.address}: preemptible/spot flagged; estimating at on-demand rates"
            )
            preemptible = False

    if not preemptible or hourly == 0.0:
        if billing == "gpu_only":
            rate = float(platform_prices.get(f"gpu_hour_{ck}", 0) or 0)
            hourly = rate * max(gpus, 1 if "gpu" in platform else 0)
            if gpus == 0 and "gpu" in platform:
                warnings.append(f"{snap.address}: GPU platform but preset has 0 GPUs")
        else:
            hourly = (
                float(platform_prices.get(f"gpu_hour_{ck}", 0) or 0) * gpus
                + float(platform_prices.get(f"cpu_hour_{ck}", 0) or 0) * vcpus
                + float(platform_prices.get(f"ram_gib_hour_{ck}", 0) or 0) * ram_gib
            )

    compute_amount = scale_hourly(hourly, cfg.period, month_hours=catalog.month_hours)

    # Nested managed boot disk
    disk_amount = 0.0
    boot = values.get("boot_disk") or {}
    if isinstance(boot, dict):
        managed = boot.get("managed_disk") or {}
        if isinstance(managed, dict):
            spec = managed.get("spec") or managed
            if isinstance(spec, dict):
                disk_item = _estimate_disk_values(
                    snap.address + ".boot_disk",
                    snap.type,
                    snap.side,
                    spec,
                    catalog,
                    cfg,
                )
                disk_amount = disk_item.amount
                warnings.extend(disk_item.warnings)
                if disk_item.unknown:
                    warnings.append(f"{snap.address}: boot disk price unknown")

    total = compute_amount + disk_amount
    details = f"platform={platform} preset={preset} compute={compute_amount:.6f}"
    if disk_amount:
        details += f" boot_disk={disk_amount:.6f}"
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=total,
        currency=cfg.currency,
        period=cfg.period,
        details=details,
        warnings=warnings,
    )


def _estimate_disk_values(
    address: str,
    rtype: str,
    side: str,
    values: dict[str, Any],
    catalog: PriceCatalog,
    cfg: EstimateConfig,
) -> LineItem:
    size = _size_gib(values)
    disk_type = str(values.get("type") or "NETWORK_SSD")
    warnings: list[str] = []
    if size is None:
        return LineItem(
            address=address,
            resource_type=rtype,
            side=side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details="missing disk size",
            unknown=True,
            warnings=[f"{address}: missing size_* field"],
        )
    prices = catalog.raw.get("disks_gib_month", {}).get(disk_type)
    rate = catalog.money(prices, cfg.currency) if prices else None
    if rate is None:
        note = (prices or {}).get("note", "no public price")
        return LineItem(
            address=address,
            resource_type=rtype,
            side=side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=f"disk type {disk_type}: {note}",
            unknown=True,
            warnings=[f"{address}: no price for disk type {disk_type}"],
        )
    amount = scale_monthly(rate * size, cfg.period, month_hours=catalog.month_hours)
    return LineItem(
        address=address,
        resource_type=rtype,
        side=side,
        amount=amount,
        currency=cfg.currency,
        period=cfg.period,
        details=f"type={disk_type} size_gib={size}",
        warnings=warnings,
    )


def estimate_disk(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    return _estimate_disk_values(snap.address, snap.type, snap.side, snap.values, catalog, cfg)


def estimate_snapshot(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    size = _size_gib(snap.values)
    if size is None:
        # Snapshots may only expose size in status; treat as unknown if missing.
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details="missing snapshot size",
            unknown=True,
            warnings=[f"{snap.address}: missing size; cannot price snapshot"],
        )
    rate = catalog.money(catalog.raw.get("disk_snapshot_gib_month"), cfg.currency) or 0.0
    amount = scale_monthly(rate * size, cfg.period, month_hours=catalog.month_hours)
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=amount,
        currency=cfg.currency,
        period=cfg.period,
        details=f"snapshot size_gib={size}",
    )


def estimate_filesystem(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    size = _size_gib(snap.values)
    fs_type = str(snap.values.get("type") or "NETWORK_SSD")
    if size is None:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details="missing filesystem size",
            unknown=True,
            warnings=[f"{snap.address}: missing size_* field"],
        )
    prices = catalog.raw.get("filesystem_gib_month", {}).get(fs_type)
    rate = catalog.money(prices, cfg.currency) if prices else None
    if rate is None:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=f"filesystem type {fs_type} unpriced",
            unknown=True,
            warnings=[f"{snap.address}: no price for filesystem type {fs_type}"],
        )
    amount = scale_monthly(rate * size, cfg.period, month_hours=catalog.month_hours)
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=amount,
        currency=cfg.currency,
        period=cfg.period,
        details=f"type={fs_type} size_gib={size}",
    )


def estimate_bucket(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    usage: PlannedUsage,
) -> LineItem:
    metrics = usage.for_resource(snap.address, snap.name)
    storage_class = str(snap.values.get("default_storage_class") or "STANDARD")
    if storage_class in {"STORAGE_CLASS_UNSPECIFIED", ""}:
        storage_class = "STANDARD"

    stored = float(metrics.get("stored_gib", 0.0))
    class_a = float(metrics.get("class_a_requests", 0.0))
    class_b = float(metrics.get("class_b_requests", 0.0))
    egress = float(metrics.get("egress_gib", 0.0))
    intelligent_cold = float(metrics.get("intelligent_tier", 0.0)) >= 1.0

    warnings: list[str] = []
    if stored == 0 and class_a == 0 and class_b == 0 and egress == 0:
        warnings.append(
            f"{snap.address}: no planned usage; storage/request/egress cost assumed 0. "
            "Pass --usage-file or --usage-metric."
        )

    obj = catalog.raw.get("object_storage", {})
    storage_key = storage_class
    if storage_class == "INTELLIGENT":
        storage_key = "INTELLIGENT_COLD" if intelligent_cold else "INTELLIGENT_WARM"

    storage_prices = obj.get("storage_gib_month", {}).get(storage_key)
    storage_rate = catalog.money(storage_prices, cfg.currency) if storage_prices else None
    if storage_rate is None and stored > 0:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=f"unpriced storage class {storage_class}",
            unknown=True,
            warnings=[f"{snap.address}: no price for storage class {storage_class}"],
        )

    storage_cost = (
        scale_monthly(storage_rate * stored, cfg.period, month_hours=catalog.month_hours)
        if storage_rate is not None
        else 0.0
    )

    # Request pricing is per 1000 requests (period-independent usage quota for the estimate window).
    req = obj.get("requests_per_1000", {})
    prefix = "STANDARD"
    if storage_class == "ENHANCED_THROUGHPUT":
        prefix = "ENHANCED_THROUGHPUT"
    elif storage_class == "INTELLIGENT":
        prefix = "INTELLIGENT"
    a_rate = catalog.money(req.get(f"{prefix}_CLASS_A"), cfg.currency) or 0.0
    b_rate = catalog.money(req.get(f"{prefix}_CLASS_B"), cfg.currency) or 0.0
    request_cost = (class_a / 1000.0) * a_rate + (class_b / 1000.0) * b_rate

    egress_key = storage_class if storage_class != "INTELLIGENT" else "INTELLIGENT_OVER_3X"
    if storage_class == "INTELLIGENT":
        # Free up to 3x stored; only charge overage if user passes egress above that.
        free_egress = 3.0 * stored
        billable_egress = max(0.0, egress - free_egress)
        over_map = obj.get("egress_gib", {}).get("INTELLIGENT_OVER_3X")
        e_rate = catalog.money(over_map, cfg.currency) or 0.0
        egress_cost = billable_egress * e_rate
    else:
        e_map = obj.get("egress_gib", {}).get(egress_key)
        e_rate = catalog.money(e_map, cfg.currency) or 0.0
        egress_cost = egress * e_rate

    amount = storage_cost + request_cost + egress_cost
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=amount,
        currency=cfg.currency,
        period=cfg.period,
        details=(
            f"class={storage_class} stored_gib={stored} "
            f"reqA={class_a} reqB={class_b} egress_gib={egress}"
        ),
        warnings=warnings,
    )


def estimate_allocation(
    snap: ResourceSnapshot,
    _catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    # VPC is free of charge per docs; public/private allocations included.
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=0.0,
        currency=cfg.currency,
        period=cfg.period,
        details="VPC allocations are free per Nebius VPC pricing",
    )


def estimate_postgresql(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    """Best-effort: look for hosts/resources preset fields when present."""
    values = snap.values
    warnings: list[str] = []
    pg = catalog.raw.get("postgresql", {})
    ck = _cur_key(cfg.currency)
    cpu_rate = float(pg.get("cpu_hour", {}).get(ck, 0))
    ram_rate = float(pg.get("ram_gib_hour", {}).get(ck, 0))
    disk_rate = float(pg.get("disk_gib_month", {}).get(ck, 0))

    # Provider schema may vary; support common shapes.
    hosts = values.get("hosts") or values.get("config") or {}
    preset = None
    platform = None
    disk_gib = None
    if isinstance(hosts, dict):
        resources = hosts.get("resources") or hosts
        if isinstance(resources, dict):
            preset = resources.get("preset") or resources.get("resource_preset")
            platform = resources.get("platform")
        disk_gib = _size_gib(hosts) if "size_gibibytes" in hosts else None
    if preset is None:
        preset = values.get("preset") or values.get("resource_preset")
    if disk_gib is None:
        disk_gib = _size_gib(values)

    if not preset:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details="postgresql cluster missing preset; cannot estimate hosts",
            unknown=True,
            warnings=[f"{snap.address}: no preset found for PostgreSQL hosts"],
        )
    try:
        _gpus, vcpus, ram_gib = parse_preset(str(preset))
    except ValueError as exc:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=str(exc),
            unknown=True,
            warnings=[f"{snap.address}: {exc}"],
        )
    # Assume 1 host unless hosts count present
    host_count = 1
    if isinstance(values.get("hosts"), list):
        host_count = max(1, len(values["hosts"]))
    elif isinstance(values.get("host_count"), (int, float)):
        host_count = int(values["host_count"])

    hourly = (cpu_rate * vcpus + ram_rate * ram_gib) * host_count
    compute_amount = scale_hourly(hourly, cfg.period, month_hours=catalog.month_hours)
    disk_amount = 0.0
    if disk_gib:
        disk_amount = scale_monthly(
            disk_rate * disk_gib * host_count, cfg.period, month_hours=catalog.month_hours
        )
    else:
        warnings.append(f"{snap.address}: no disk size; storage portion omitted")

    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=compute_amount + disk_amount,
        currency=cfg.currency,
        period=cfg.period,
        details=f"preset={preset} hosts={host_count} platform={platform}",
        warnings=warnings,
    )


def estimate_mlflow(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    ml = catalog.raw.get("mlflow", {})
    sizes = ml.get("sizes", {})
    size_name = str(snap.values.get("size") or snap.values.get("cluster_size") or "medium").lower()
    size = sizes.get(size_name)
    if not size:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=f"unknown mlflow size {size_name}",
            unknown=True,
            warnings=[f"{snap.address}: unknown MLflow size {size_name}"],
        )
    ck = _cur_key(cfg.currency)
    hourly = float(ml["cpu_hour"][ck]) * int(size["vcpu"]) + float(ml["ram_gib_hour"][ck]) * int(
        size["ram_gib"]
    )
    amount = scale_hourly(hourly, cfg.period, month_hours=catalog.month_hours)
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=amount,
        currency=cfg.currency,
        period=cfg.period,
        details=f"size={size_name} vcpu={size['vcpu']} ram_gib={size['ram_gib']}",
    )


def estimate_free(
    snap: ResourceSnapshot,
    _catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=0.0,
        currency=cfg.currency,
        period=cfg.period,
        details="free / not billed separately per public pricing",
    )


def estimate_unknown(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    hint = catalog.raw.get("unsupported_priced_elsewhere", {}).get(snap.type, "no estimator")
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=0.0,
        currency=cfg.currency,
        period=cfg.period,
        details=str(hint),
        unknown=True,
        warnings=[f"{snap.address}: {snap.type} not priced ({hint})"],
    )


ESTIMATORS: dict[str, Estimator] = {
    "nebius_compute_v1_instance": estimate_instance,
    "nebius_compute_v1_disk": estimate_disk,
    "nebius_compute_v1_disk_snapshot": estimate_snapshot,
    "nebius_compute_v1_filesystem": estimate_filesystem,
    "nebius_storage_v1_bucket": estimate_bucket,
    "nebius_vpc_v1_allocation": estimate_allocation,
    "nebius_msp_postgresql_v1alpha1_cluster": estimate_postgresql,
    "nebius_msp_mlflow_v1alpha1_cluster": estimate_mlflow,
}


def estimate_resource(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    usage: PlannedUsage,
) -> LineItem:
    if snap.type in catalog.free_types:
        return estimate_free(snap, catalog, cfg, usage)
    estimator = ESTIMATORS.get(snap.type, estimate_unknown)
    return estimator(snap, catalog, cfg, usage)


def _sum_items(items: list[LineItem]) -> float:
    return sum(i.amount for i in items)


def run_estimate(
    doc: TerraformDocument,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    usage: PlannedUsage,
) -> EstimateResult:
    before_items = [estimate_resource(s, catalog, cfg, usage) for s in doc.resources_before]
    after_items = [estimate_resource(s, catalog, cfg, usage) for s in doc.resources_after]

    # Mode handling: for state / total, before is empty unless plan.
    if cfg.mode == "total":
        # Total of after only (or planned_values)
        before_items = []
    elif cfg.mode == "state":
        before_items = []
    elif cfg.mode == "plan":
        pass  # keep both

    before_total = _sum_items(before_items)
    after_total = _sum_items(after_items)
    delta = after_total - before_total

    warnings: list[str] = []
    unknown: list[str] = []
    for item in before_items + after_items:
        warnings.extend(item.warnings)
        if item.unknown:
            unknown.append(item.address)

    # De-dupe while preserving order
    seen_w: set[str] = set()
    uniq_w: list[str] = []
    for w in warnings:
        if w not in seen_w:
            seen_w.add(w)
            uniq_w.append(w)
    seen_u: set[str] = set()
    uniq_u: list[str] = []
    for u in unknown:
        if u not in seen_u:
            seen_u.add(u)
            uniq_u.append(u)

    return EstimateResult(
        period=cfg.period,
        currency=cfg.currency,
        before_total=before_total,
        after_total=after_total,
        delta=delta,
        before_items=before_items,
        after_items=after_items,
        unknown_resources=uniq_u,
        warnings=uniq_w,
        catalog_path=str(catalog.path),
        document_kind=doc.kind,
    )
