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

# Serverless AI `gpu` selector → default catalog platform (region variants may differ).
GPU_SELECTOR_TO_PLATFORM: dict[str, str] = {
    "B300": "gpu-b300-sxm",
    "B200": "gpu-b200-sxm",
    "H200": "gpu-h200-sxm",
    "H100": "gpu-h100-sxm",
    "L40S": "gpu-l40s-a",
    "RTX6000": "gpu-rtx6000",
    "RTXPRO6000": "gpu-rtx6000",
    "RTX": "gpu-rtx6000",
}

# Serverless AI `cpu` selector family → default non-GPU platform.
CPU_FAMILY_TO_PLATFORM: dict[str, str] = {
    "amd": "cpu-d3",
    "intel": "cpu-e2",
}


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


@dataclass
class _ComputeParts:
    hourly: float
    platform: str
    preset: str
    gpus: int
    vcpus: int
    ram_gib: int
    unknown: bool = False
    warnings: list[str] = field(default_factory=list)
    details: str = ""


def _price_compute(
    *,
    address: str,
    platform: str,
    preset: str,
    preemptible: bool,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    gpus: int | None = None,
    vcpus: int | None = None,
    ram_gib: int | None = None,
) -> _ComputeParts:
    """Shared GPU/CPU/RAM hourly pricing used by instances, node groups, and Serverless AI."""
    warnings: list[str] = []
    platform_prices = catalog.platform(platform)
    if not platform_prices:
        return _ComputeParts(
            hourly=0.0,
            platform=platform,
            preset=preset,
            gpus=gpus or 0,
            vcpus=vcpus or 0,
            ram_gib=ram_gib or 0,
            unknown=True,
            warnings=[f"{address}: no catalog entry for platform {platform!r}"],
            details=f"unknown platform {platform!r}",
        )

    if gpus is None or vcpus is None or ram_gib is None:
        try:
            parsed_gpus, parsed_vcpus, parsed_ram = parse_preset(preset) if preset else (0, 0, 0)
        except ValueError as exc:
            return _ComputeParts(
                hourly=0.0,
                platform=platform,
                preset=preset,
                gpus=0,
                vcpus=0,
                ram_gib=0,
                unknown=True,
                warnings=[f"{address}: {exc}"],
                details=str(exc),
            )
        gpus = parsed_gpus if gpus is None else gpus
        vcpus = parsed_vcpus if vcpus is None else vcpus
        ram_gib = parsed_ram if ram_gib is None else ram_gib

    ck = _cur_key(cfg.currency)
    hourly = 0.0
    billing = platform_prices.get("billing")
    use_preemptible = preemptible

    if use_preemptible:
        if cfg.preemptible_policy == "reject":
            return _ComputeParts(
                hourly=0.0,
                platform=platform,
                preset=preset,
                gpus=gpus,
                vcpus=vcpus,
                ram_gib=ram_gib,
                unknown=True,
                warnings=[f"{address}: preemptible/spot instance; set --preemptible-policy"],
                details="preemptible rejected by policy",
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
                warnings.append(f"{address}: using published preemptible rates")
            elif floor is not None and billing == "gpu_only":
                hourly = float(floor) * max(gpus, 1 if gpus else 0)
                warnings.append(
                    f"{address}: spot price is dynamic; using published floor "
                    f"{floor}/{cfg.currency} per GPU-hour"
                )
            else:
                warnings.append(f"{address}: no preemptible rates; falling back to on-demand")
                use_preemptible = False
        else:
            warnings.append(f"{address}: preemptible/spot flagged; estimating at on-demand rates")
            use_preemptible = False

    if not use_preemptible or hourly == 0.0:
        if billing == "gpu_only":
            rate = float(platform_prices.get(f"gpu_hour_{ck}", 0) or 0)
            hourly = rate * max(gpus, 1 if "gpu" in platform else 0)
            if gpus == 0 and "gpu" in platform:
                warnings.append(f"{address}: GPU platform but preset has 0 GPUs")
        else:
            if billing == "components" and (vcpus == 0 and ram_gib == 0) and gpus > 0:
                # GPU selector without preset: cannot bill CPU/RAM components.
                return _ComputeParts(
                    hourly=0.0,
                    platform=platform,
                    preset=preset,
                    gpus=gpus,
                    vcpus=vcpus,
                    ram_gib=ram_gib,
                    unknown=True,
                    warnings=[
                        f"{address}: platform {platform!r} bills CPU/RAM components; "
                        "provide a preset (or platform+preset) to estimate"
                    ],
                    details="missing preset for component-billed platform",
                )
            hourly = (
                float(platform_prices.get(f"gpu_hour_{ck}", 0) or 0) * gpus
                + float(platform_prices.get(f"cpu_hour_{ck}", 0) or 0) * vcpus
                + float(platform_prices.get(f"ram_gib_hour_{ck}", 0) or 0) * ram_gib
            )

    return _ComputeParts(
        hourly=hourly,
        platform=platform,
        preset=preset,
        gpus=gpus,
        vcpus=vcpus,
        ram_gib=ram_gib,
        warnings=warnings,
        details=f"platform={platform} preset={preset}",
    )


def _local_ssd_amount(
    address: str,
    values: dict[str, Any],
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    usage: PlannedUsage,
    *,
    resource_name: str = "",
) -> tuple[float, list[str]]:
    """Price local SSD when requested; size is not in Terraform — needs planned usage."""
    local = values.get("local_disks") or {}
    if not isinstance(local, dict) or not local:
        return 0.0, []
    passthrough = local.get("passthrough_group") or {}
    requested = False
    if isinstance(passthrough, dict) and passthrough.get("requested") is True:
        requested = True
    elif local:
        # Presence of local_disks block is enough to flag potential local SSD charges.
        requested = True
    if not requested:
        return 0.0, []

    metrics = usage.for_resource(address, resource_name)
    size = float(metrics.get("local_ssd_gib", 0.0))
    if size <= 0:
        return 0.0, [
            f"{address}: local disks requested but local_ssd_gib usage not set; "
            "local SSD cost assumed 0"
        ]
    rate = catalog.money(catalog.raw.get("local_ssd_gib_month"), cfg.currency)
    if rate is None:
        return 0.0, [f"{address}: no catalog price for local SSD"]
    amount = scale_monthly(rate * size, cfg.period, month_hours=catalog.month_hours)
    return amount, []


def estimate_instance(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    usage: PlannedUsage,
) -> LineItem:
    values = snap.values
    resources = values.get("resources") or {}
    if not isinstance(resources, dict):
        resources = {}
    platform = str(resources.get("platform") or "")
    preset = str(resources.get("preset") or "")
    parts = _price_compute(
        address=snap.address,
        platform=platform,
        preset=preset,
        preemptible=_is_preemptible(values),
        catalog=catalog,
        cfg=cfg,
    )
    if parts.unknown:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=parts.details,
            unknown=True,
            warnings=parts.warnings,
        )

    warnings = list(parts.warnings)
    compute_amount = scale_hourly(parts.hourly, cfg.period, month_hours=catalog.month_hours)

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

    local_amount, local_warnings = _local_ssd_amount(
        snap.address, values, catalog, cfg, usage, resource_name=snap.name
    )
    warnings.extend(local_warnings)

    total = compute_amount + disk_amount + local_amount
    details = f"{parts.details} compute={compute_amount:.6f}"
    if disk_amount:
        details += f" boot_disk={disk_amount:.6f}"
    if local_amount:
        details += f" local_ssd={local_amount:.6f}"
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
        note = (prices or {}).get("note", "no public list price")
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=f"filesystem type {fs_type}: {note}",
            unknown=True,
            warnings=[f"{snap.address}: no price for filesystem type {fs_type} ({note})"],
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


def _node_group_count(
    values: dict[str, Any],
    usage: PlannedUsage,
    address: str,
    name: str,
) -> tuple[int, list[str]]:
    """Resolve node count: usage override > fixed_node_count > autoscaling initial/min."""
    warnings: list[str] = []
    metrics = usage.for_resource(address, name)
    if "node_count" in metrics:
        count = max(0, int(metrics["node_count"]))
        return count, warnings

    fixed = values.get("fixed_node_count")
    if isinstance(fixed, (int, float)):
        return max(0, int(fixed)), warnings

    autoscaling = values.get("autoscaling") or {}
    if isinstance(autoscaling, dict) and autoscaling:
        initial = autoscaling.get("initial_node_count")
        minimum = autoscaling.get("min_node_count")
        maximum = autoscaling.get("max_node_count")
        if isinstance(initial, (int, float)):
            count = int(initial)
            warnings.append(
                f"{address}: autoscaling node group; using initial_node_count={count} "
                "(override with usage metric node_count)"
            )
            return max(0, count), warnings
        if isinstance(minimum, (int, float)):
            count = int(minimum)
            warnings.append(
                f"{address}: autoscaling node group; using min_node_count={count} "
                f"(max={maximum!r}); override with usage metric node_count"
            )
            return max(0, count), warnings
        return 0, [f"{address}: autoscaling present but min/initial node count missing"]

    return 0, [f"{address}: missing fixed_node_count / autoscaling; node count assumed 0"]


def estimate_node_group(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    usage: PlannedUsage,
) -> LineItem:
    """MK8s nodes bill as Compute VMs (control plane is free)."""
    values = snap.values
    template = values.get("template") or {}
    if not isinstance(template, dict):
        template = {}
    resources = template.get("resources") or {}
    if not isinstance(resources, dict):
        resources = {}
    platform = str(resources.get("platform") or "")
    preset = str(resources.get("preset") or "")
    count, warnings = _node_group_count(values, usage, snap.address, snap.name)

    if not platform:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details="node group missing template.resources.platform",
            unknown=True,
            warnings=[f"{snap.address}: missing template.resources.platform"],
        )

    parts = _price_compute(
        address=snap.address,
        platform=platform,
        preset=preset,
        preemptible=_is_preemptible(template),
        catalog=catalog,
        cfg=cfg,
    )
    warnings.extend(parts.warnings)
    if parts.unknown:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=parts.details,
            unknown=True,
            warnings=warnings,
        )

    per_node_compute = scale_hourly(parts.hourly, cfg.period, month_hours=catalog.month_hours)
    boot = template.get("boot_disk") or {}
    per_node_disk = 0.0
    if isinstance(boot, dict) and boot:
        disk_item = _estimate_disk_values(
            snap.address + ".boot_disk",
            snap.type,
            snap.side,
            boot,
            catalog,
            cfg,
        )
        per_node_disk = disk_item.amount
        warnings.extend(disk_item.warnings)
        if disk_item.unknown:
            warnings.append(f"{snap.address}: boot disk price unknown")

    local_amount, local_warnings = _local_ssd_amount(
        snap.address, template, catalog, cfg, usage, resource_name=snap.name
    )
    warnings.extend(local_warnings)

    amount = (per_node_compute + per_node_disk + local_amount) * count
    details = (
        f"nodes={count} {parts.details} per_node_compute={per_node_compute:.6f}"
        f" per_node_disk={per_node_disk:.6f}"
    )
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=amount,
        currency=cfg.currency,
        period=cfg.period,
        details=details,
        warnings=warnings,
    )


def _parse_gpu_selector(raw: str) -> tuple[str, int] | None:
    """Parse 'H100:8' or 'H100' → (MODEL, count)."""
    text = raw.strip()
    if not text:
        return None
    if ":" in text:
        model, count_s = text.split(":", 1)
        try:
            count = int(count_s.strip())
        except ValueError:
            return None
    else:
        model, count = text, 1
    model_key = re.sub(r"[^A-Za-z0-9]", "", model).upper()
    return model_key, max(1, count)


def _parse_cpu_selector(raw: str) -> tuple[str, int] | None:
    """Parse 'amd:16' → (family, vcpus)."""
    text = raw.strip()
    if not text or ":" not in text:
        return None
    family, count_s = text.split(":", 1)
    try:
        count = int(count_s.strip())
    except ValueError:
        return None
    return family.strip().lower(), max(1, count)


def _resolve_serverless_platform(
    values: dict[str, Any],
    address: str,
) -> tuple[str, str, int | None, int | None, int | None, list[str]]:
    """Return platform, preset, optional gpu/vcpu/ram overrides, warnings."""
    warnings: list[str] = []
    platform = str(values.get("platform") or "")
    preset = str(values.get("preset") or "")
    gpus: int | None = None
    vcpus: int | None = None
    ram_gib: int | None = None

    gpu_list = values.get("gpu") or []
    cpu_list = values.get("cpu") or []
    if isinstance(gpu_list, list) and gpu_list and not platform:
        parsed = _parse_gpu_selector(str(gpu_list[0]))
        if parsed:
            model, gpus = parsed
            mapped = GPU_SELECTOR_TO_PLATFORM.get(model)
            if mapped:
                platform = mapped
                warnings.append(
                    f"{address}: mapped gpu selector {gpu_list[0]!r} → platform {platform}"
                )
            else:
                warnings.append(f"{address}: unrecognized gpu selector model {model!r}")
    if isinstance(cpu_list, list) and cpu_list and not platform:
        parsed = _parse_cpu_selector(str(cpu_list[0]))
        if parsed:
            family, vcpus = parsed
            mapped = CPU_FAMILY_TO_PLATFORM.get(family)
            if mapped:
                platform = mapped
                warnings.append(
                    f"{address}: mapped cpu selector {cpu_list[0]!r} → platform {platform}; "
                    "RAM unknown without preset — set platform+preset for accurate estimate"
                )
                # Without RAM, mark incomplete unless preset later fills it.
                ram_gib = 0
            else:
                warnings.append(f"{address}: unrecognized cpu family {family!r}")

    return platform, preset, gpus, vcpus, ram_gib, warnings


def estimate_ai_endpoint(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    usage: PlannedUsage,
) -> LineItem:
    """Serverless AI endpoints bill as Compute while running (docs)."""
    values = snap.values
    warnings: list[str] = []
    metrics = usage.for_resource(snap.address, snap.name)
    running_fraction = float(metrics.get("running_fraction", 1.0))
    if running_fraction < 0:
        running_fraction = 0.0
    if running_fraction > 1:
        running_fraction = 1.0
    if "running_fraction" not in metrics:
        warnings.append(
            f"{snap.address}: assuming endpoint runs 100% of period "
            "(set usage metric running_fraction 0..1; stopped endpoints bill disk only=0)"
        )

    platform, preset, gpus, vcpus, ram_gib, map_warnings = _resolve_serverless_platform(
        values, snap.address
    )
    warnings.extend(map_warnings)

    if not platform:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details="serverless endpoint missing platform/preset/gpu/cpu",
            unknown=True,
            warnings=[f"{snap.address}: cannot resolve Compute platform for endpoint"],
        )

    # CPU selector without RAM/preset cannot be priced.
    if vcpus is not None and ram_gib == 0 and not preset:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details="cpu selector without preset (RAM unknown)",
            unknown=True,
            warnings=warnings
            + [f"{snap.address}: provide preset or platform+preset for CPU endpoints"],
        )

    preemptible = _is_preemptible(values)
    pricing_model = values.get("pricing_model")
    if isinstance(pricing_model, dict) and (
        pricing_model.get("follows_spot_price") or pricing_model.get("spot_pricing_policy")
    ):
        preemptible = True

    if preset:
        parts = _price_compute(
            address=snap.address,
            platform=platform,
            preset=preset,
            preemptible=preemptible,
            catalog=catalog,
            cfg=cfg,
        )
    elif gpus is not None:
        parts = _price_compute(
            address=snap.address,
            platform=platform,
            preset=preset,
            preemptible=preemptible,
            catalog=catalog,
            cfg=cfg,
            gpus=gpus,
            vcpus=0,
            ram_gib=0,
        )
    else:
        parts = _price_compute(
            address=snap.address,
            platform=platform,
            preset=preset,
            preemptible=preemptible,
            catalog=catalog,
            cfg=cfg,
            gpus=gpus,
            vcpus=vcpus,
            ram_gib=ram_gib,
        )

    warnings.extend(parts.warnings)
    if parts.unknown:
        return LineItem(
            address=snap.address,
            resource_type=snap.type,
            side=snap.side,
            amount=0.0,
            currency=cfg.currency,
            period=cfg.period,
            details=parts.details,
            unknown=True,
            warnings=warnings,
        )

    compute_amount = (
        scale_hourly(parts.hourly, cfg.period, month_hours=catalog.month_hours) * running_fraction
    )

    disk = values.get("disk") or {}
    disk_amount = 0.0
    if isinstance(disk, dict) and disk:
        # Endpoint disk schema often uses size_bytes only.
        disk_item = _estimate_disk_values(
            snap.address + ".disk",
            snap.type,
            snap.side,
            disk,
            catalog,
            cfg,
        )
        # While running, disk is billed; docs: stopped endpoint → no storage charge.
        disk_amount = disk_item.amount * running_fraction
        warnings.extend(disk_item.warnings)
        if disk_item.unknown:
            warnings.append(f"{snap.address}: endpoint disk price unknown")

    amount = compute_amount + disk_amount
    details = (
        f"{parts.details} running_fraction={running_fraction} "
        f"compute={compute_amount:.6f} disk={disk_amount:.6f}"
    )
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=amount,
        currency=cfg.currency,
        period=cfg.period,
        details=details,
        warnings=warnings,
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
    "nebius_mk8s_v1_node_group": estimate_node_group,
    "nebius_mk8s_v1alpha1_node_group": estimate_node_group,
    "nebius_ai_v1_endpoint": estimate_ai_endpoint,
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
