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
    missing_usage: bool = False
    warnings: list[str] = field(default_factory=list)
    # Optimistic / pessimistic bounds. When omitted, both default to ``amount``.
    # ``amount`` is normalized to ``amount_max`` (budget / point estimate).
    amount_min: float | None = None
    amount_max: float | None = None

    def __post_init__(self) -> None:
        amin = self.amount if self.amount_min is None else self.amount_min
        amax = self.amount if self.amount_max is None else self.amount_max
        self.amount_min = float(amin)
        self.amount_max = float(amax)
        self.amount = self.amount_max


@dataclass
class EstimateResult:
    period: Period
    currency: str
    before_total_min: float
    before_total_max: float
    after_total_min: float
    after_total_max: float
    delta_min: float
    delta_max: float
    before_items: list[LineItem]
    after_items: list[LineItem]
    unknown_resources: list[str]
    missing_usage_resources: list[str]
    warnings: list[str]
    catalog_path: str
    document_kind: str
    # Backward-compatible aliases: totals/delta are the **max** (pessimistic) figures.
    before_total: float = 0.0
    after_total: float = 0.0
    delta: float = 0.0

    def __post_init__(self) -> None:
        self.before_total = self.before_total_max
        self.after_total = self.after_total_max
        self.delta = self.delta_max

    @property
    def total(self) -> float:
        """Preferred total for budget gating: after max (pessimistic) cost."""
        return self.after_total_max

    @property
    def total_min(self) -> float:
        return self.after_total_min

    @property
    def total_max(self) -> float:
        return self.after_total_max


Estimator = Callable[
    [ResourceSnapshot, PriceCatalog, EstimateConfig, PlannedUsage],
    LineItem,
]


def _uniq(items: list[str]) -> list[str]:
    """De-duplicate while preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _line(
    snap: ResourceSnapshot,
    cfg: EstimateConfig,
    *,
    amount: float,
    details: str,
    unknown: bool = False,
    missing_usage: bool = False,
    warnings: list[str] | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
) -> LineItem:
    """Build a line item from a resource snapshot + runtime config."""
    return LineItem(
        address=snap.address,
        resource_type=snap.type,
        side=snap.side,
        amount=amount,
        currency=cfg.currency,
        period=cfg.period,
        details=details,
        unknown=unknown,
        missing_usage=missing_usage,
        warnings=list(warnings or []),
        amount_min=amount_min,
        amount_max=amount_max,
    )


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


def _inline_managed_boot_disk_spec(boot: Any) -> dict[str, Any] | None:
    """Return ``boot_disk.managed_disk.spec`` only when it is self-contained.

    Instances often attach a separately managed disk via ``existing_disk.id``
    (or a managed_disk stub without size). Those disks must be priced only when
    they appear as ``nebius_compute_v1_disk`` (or equivalent) resources — not
    folded into the instance with a missing-size warning.
    """
    if not isinstance(boot, dict) or not boot:
        return None
    # ID/name-only attachment: never fold into the instance line.
    existing = boot.get("existing_disk")
    if isinstance(existing, dict) and existing:
        return None
    managed = boot.get("managed_disk")
    if not isinstance(managed, dict) or not managed:
        return None
    spec = managed.get("spec")
    if not isinstance(spec, dict) or not spec:
        return None
    # Size-less managed spec → ignore (priced via disk resource or unknown).
    if _size_gib(spec) is None:
        return None
    return spec


def _is_preemptible(values: dict[str, Any]) -> bool:
    preempt = values.get("preemptible")
    if preempt is True:
        return True
    # Terraform provider uses a nested block: preemptible = { on_preemption = "STOP" }
    if isinstance(preempt, dict):
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
    hourly_min: float
    hourly_max: float
    platform: str
    preset: str
    gpus: int
    vcpus: int
    ram_gib: int
    unknown: bool = False
    warnings: list[str] = field(default_factory=list)
    details: str = ""

    @property
    def hourly(self) -> float:
        """Pessimistic hourly rate (on-demand / ceiling)."""
        return self.hourly_max


def _on_demand_hourly(
    platform_prices: dict[str, Any],
    *,
    billing: str | None,
    gpus: int,
    vcpus: int,
    ram_gib: int,
    platform: str,
    ck: str,
) -> tuple[float, list[str], bool]:
    """Return (hourly, warnings, unknown)."""
    warnings: list[str] = []
    if billing == "gpu_only":
        rate = float(platform_prices.get(f"gpu_hour_{ck}", 0) or 0)
        hourly = rate * max(gpus, 1 if "gpu" in platform else 0)
        if gpus == 0 and "gpu" in platform:
            warnings.append("GPU platform but preset has 0 GPUs")
        return hourly, warnings, False
    if billing == "components" and (vcpus == 0 and ram_gib == 0) and gpus > 0:
        return (
            0.0,
            [
                f"platform {platform!r} bills CPU/RAM components; "
                "provide a preset (or platform+preset) to estimate"
            ],
            True,
        )
    hourly = (
        float(platform_prices.get(f"gpu_hour_{ck}", 0) or 0) * gpus
        + float(platform_prices.get(f"cpu_hour_{ck}", 0) or 0) * vcpus
        + float(platform_prices.get(f"ram_gib_hour_{ck}", 0) or 0) * ram_gib
    )
    return hourly, warnings, False


def _preemptible_rates_hourly(
    platform_prices: dict[str, Any],
    *,
    billing: str | None,
    gpus: int,
    vcpus: int,
    ram_gib: int,
    ck: str,
) -> tuple[float | None, str, str | None]:
    """Return (hourly_or_None, kind, note) for published spot/preemptible rates.

    ``kind`` is ``fixed`` (exact published preemptible component rates), ``floor``
    (dynamic spot minimum from compute pricing "From $X" lines), or ``none``.
    """
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
        return hourly, "fixed", "published preemptible rates"
    floor = platform_prices.get(f"preemptible_floor_{ck}")
    if floor is not None and billing == "gpu_only":
        return (
            float(floor) * max(gpus, 1 if gpus else 0),
            "floor",
            f"published floor {floor}",
        )
    return None, "none", None


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
    """Shared GPU/CPU/RAM hourly pricing used by instances, node groups, and Serverless AI.

    Preemptible/spot semantics (when rates are listed in the catalog):

    * **Fixed** preemptible rates (e.g. L40S): ``min == max`` at the published
      preemptible component rates (not on-demand).
    * **Dynamic** spot floors (e.g. H100 "From $X"): ``min`` uses the published
      floor; ``max`` uses on-demand as a conventional ceiling under
      ``--preemptible-policy on_demand``. With ``floor``, both bounds use the
      floor. Spot can exceed on-demand when following the live market without a
      pricing-policy max — that uncapped case is not modeled as a finite max.

    Platforms without a published floor or fixed preemptible table are not
    invented: bounds fall back to on-demand with a warning.
    """
    warnings: list[str] = []
    platform_prices = catalog.platform(platform)
    if not platform_prices:
        return _ComputeParts(
            hourly_min=0.0,
            hourly_max=0.0,
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
                hourly_min=0.0,
                hourly_max=0.0,
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
    billing = platform_prices.get("billing")

    on_demand, od_warnings, od_unknown = _on_demand_hourly(
        platform_prices,
        billing=billing,
        gpus=gpus,
        vcpus=vcpus,
        ram_gib=ram_gib,
        platform=platform,
        ck=ck,
    )
    for w in od_warnings:
        warnings.append(f"{address}: {w}")

    if od_unknown:
        return _ComputeParts(
            hourly_min=0.0,
            hourly_max=0.0,
            platform=platform,
            preset=preset,
            gpus=gpus,
            vcpus=vcpus,
            ram_gib=ram_gib,
            unknown=True,
            warnings=warnings,
            details="missing preset for component-billed platform",
        )

    if not preemptible:
        return _ComputeParts(
            hourly_min=on_demand,
            hourly_max=on_demand,
            platform=platform,
            preset=preset,
            gpus=gpus,
            vcpus=vcpus,
            ram_gib=ram_gib,
            warnings=warnings,
            details=f"platform={platform} preset={preset}",
        )

    if cfg.preemptible_policy == "reject":
        return _ComputeParts(
            hourly_min=0.0,
            hourly_max=0.0,
            platform=platform,
            preset=preset,
            gpus=gpus,
            vcpus=vcpus,
            ram_gib=ram_gib,
            unknown=True,
            warnings=[f"{address}: preemptible/spot instance; set --preemptible-policy"],
            details="preemptible rejected by policy",
        )

    spot_hourly, spot_kind, spot_note = _preemptible_rates_hourly(
        platform_prices,
        billing=billing,
        gpus=gpus,
        vcpus=vcpus,
        ram_gib=ram_gib,
        ck=ck,
    )
    if spot_hourly is not None and spot_kind == "fixed":
        # Exact published preemptible rates — not a dynamic market.
        warnings.append(f"{address}: preemptible/spot; uses {spot_note} (fixed list rates)")
        return _ComputeParts(
            hourly_min=spot_hourly,
            hourly_max=spot_hourly,
            platform=platform,
            preset=preset,
            gpus=gpus,
            vcpus=vcpus,
            ram_gib=ram_gib,
            warnings=warnings,
            details=f"platform={platform} preset={preset}",
        )

    if spot_hourly is not None and spot_kind == "floor":
        floor_hourly = min(spot_hourly, on_demand)
        if cfg.preemptible_policy == "floor":
            warnings.append(
                f"{address}: spot price is dynamic; "
                f"--preemptible-policy floor uses {spot_note} "
                f"{cfg.currency}/GPU-hour for min and max"
            )
            hourly_min = floor_hourly
            hourly_max = floor_hourly
        else:
            # Default on_demand: optimistic floor / pessimistic on-demand ceiling.
            warnings.append(
                f"{address}: spot price is dynamic; min uses {spot_note} "
                f"{cfg.currency}/GPU-hour, max uses on-demand "
                "(live spot can exceed on-demand without a pricing-policy max)"
            )
            hourly_min = floor_hourly
            hourly_max = on_demand
        return _ComputeParts(
            hourly_min=hourly_min,
            hourly_max=hourly_max,
            platform=platform,
            preset=preset,
            gpus=gpus,
            vcpus=vcpus,
            ram_gib=ram_gib,
            warnings=warnings,
            details=f"platform={platform} preset={preset}",
        )

    warnings.append(
        f"{address}: preemptible/spot flagged; no published floor/preemptible "
        "rates in catalog — min=max=on-demand (not inventing spot prices)"
    )
    return _ComputeParts(
        hourly_min=on_demand,
        hourly_max=on_demand,
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
) -> tuple[float, list[str], bool]:
    """Price local SSD when requested; size is not in Terraform — needs planned usage.

    Returns ``(amount, warnings, missing_usage)``.
    """
    local = values.get("local_disks") or {}
    if not isinstance(local, dict) or not local:
        return 0.0, [], False
    passthrough = local.get("passthrough_group") or {}
    requested = False
    if isinstance(passthrough, dict) and passthrough.get("requested") is True:
        requested = True
    elif local:
        # Presence of local_disks block is enough to flag potential local SSD charges.
        requested = True
    if not requested:
        return 0.0, [], False

    metrics = usage.for_resource(address, resource_name)
    size = float(metrics.get("local_ssd_gib", 0.0))
    if size <= 0:
        return (
            0.0,
            [
                f"{address}: local disks requested but local_ssd_gib usage not set; "
                "local SSD cost assumed 0"
            ],
            True,
        )
    rate = catalog.money(catalog.raw.get("local_ssd_gib_month"), cfg.currency)
    if rate is None:
        return 0.0, [f"{address}: no catalog price for local SSD"], False
    amount = scale_monthly(rate * size, cfg.period, month_hours=catalog.month_hours)
    return amount, [], False


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
        return _line(
            snap,
            cfg,
            amount=0.0,
            details=parts.details,
            unknown=True,
            warnings=parts.warnings,
        )

    warnings = list(parts.warnings)
    compute_min = scale_hourly(parts.hourly_min, cfg.period, month_hours=catalog.month_hours)
    compute_max = scale_hourly(parts.hourly_max, cfg.period, month_hours=catalog.month_hours)

    disk_amount = 0.0
    boot_spec = _inline_managed_boot_disk_spec(values.get("boot_disk"))
    if boot_spec is not None:
        disk_item = _estimate_disk_values(
            snap.address + ".boot_disk",
            snap.type,
            snap.side,
            boot_spec,
            catalog,
            cfg,
        )
        disk_amount = disk_item.amount_max
        warnings.extend(disk_item.warnings)
        if disk_item.unknown:
            warnings.append(f"{snap.address}: boot disk price unknown")

    local_amount, local_warnings, local_missing = _local_ssd_amount(
        snap.address, values, catalog, cfg, usage, resource_name=snap.name
    )
    warnings.extend(local_warnings)

    total_min = compute_min + disk_amount + local_amount
    total_max = compute_max + disk_amount + local_amount
    details = f"{parts.details} compute_min={compute_min:.6f} compute_max={compute_max:.6f}"
    if disk_amount:
        details += f" boot_disk={disk_amount:.6f}"
    if local_amount:
        details += f" local_ssd={local_amount:.6f}"
    return _line(
        snap,
        cfg,
        amount=total_max,
        missing_usage=local_missing,
        details=details,
        warnings=warnings,
        amount_min=total_min,
        amount_max=total_max,
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
        return _line(
            snap,
            cfg,
            amount=0.0,
            details="missing snapshot size",
            unknown=True,
            warnings=[f"{snap.address}: missing size; cannot price snapshot"],
        )
    rate = catalog.money(catalog.raw.get("disk_snapshot_gib_month"), cfg.currency) or 0.0
    amount = scale_monthly(rate * size, cfg.period, month_hours=catalog.month_hours)
    return _line(snap, cfg, amount=amount, details=f"snapshot size_gib={size}")


def estimate_filesystem(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    size = _size_gib(snap.values)
    fs_type = str(snap.values.get("type") or "NETWORK_SSD")
    if size is None:
        return _line(
            snap,
            cfg,
            amount=0.0,
            details="missing filesystem size",
            unknown=True,
            warnings=[f"{snap.address}: missing size_* field"],
        )
    prices = catalog.raw.get("filesystem_gib_month", {}).get(fs_type)
    rate = catalog.money(prices, cfg.currency) if prices else None
    if rate is None:
        note = (prices or {}).get("note", "no public list price")
        return _line(
            snap,
            cfg,
            amount=0.0,
            details=f"filesystem type {fs_type}: {note}",
            unknown=True,
            warnings=[f"{snap.address}: no price for filesystem type {fs_type} ({note})"],
        )
    amount = scale_monthly(rate * size, cfg.period, month_hours=catalog.month_hours)
    return _line(snap, cfg, amount=amount, details=f"type={fs_type} size_gib={size}")


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
    missing_usage = False
    if stored == 0 and class_a == 0 and class_b == 0 and egress == 0:
        missing_usage = True
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
        return _line(
            snap,
            cfg,
            amount=0.0,
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
    return _line(
        snap,
        cfg,
        amount=amount,
        details=(
            f"class={storage_class} stored_gib={stored} "
            f"reqA={class_a} reqB={class_b} egress_gib={egress}"
        ),
        missing_usage=missing_usage,
        warnings=warnings,
    )


def estimate_allocation(
    snap: ResourceSnapshot,
    _catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    # VPC is free of charge per docs; public/private allocations included.
    return _line(
        snap,
        cfg,
        amount=0.0,
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
        return _line(
            snap,
            cfg,
            amount=0.0,
            details="postgresql cluster missing preset; cannot estimate hosts",
            unknown=True,
            warnings=[f"{snap.address}: no preset found for PostgreSQL hosts"],
        )
    try:
        _gpus, vcpus, ram_gib = parse_preset(str(preset))
    except ValueError as exc:
        return _line(
            snap,
            cfg,
            amount=0.0,
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

    return _line(
        snap,
        cfg,
        amount=compute_amount + disk_amount,
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
        return _line(
            snap,
            cfg,
            amount=0.0,
            details=f"unknown mlflow size {size_name}",
            unknown=True,
            warnings=[f"{snap.address}: unknown MLflow size {size_name}"],
        )
    ck = _cur_key(cfg.currency)
    hourly = float(ml["cpu_hour"][ck]) * int(size["vcpu"]) + float(ml["ram_gib_hour"][ck]) * int(
        size["ram_gib"]
    )
    amount = scale_hourly(hourly, cfg.period, month_hours=catalog.month_hours)
    return _line(
        snap,
        cfg,
        amount=amount,
        details=f"size={size_name} vcpu={size['vcpu']} ram_gib={size['ram_gib']}",
    )


def estimate_free(
    snap: ResourceSnapshot,
    _catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    return _line(
        snap,
        cfg,
        amount=0.0,
        details="free / not billed separately per public pricing",
    )


def _node_group_count_range(
    values: dict[str, Any],
    usage: PlannedUsage,
    address: str,
    name: str,
) -> tuple[int, int, list[str], bool]:
    """Resolve node count range: (min, max, warnings, missing_usage).

    - usage ``node_count`` or ``fixed_node_count`` → min == max
    - autoscaling → min uses min_node_count (else initial); max uses max_node_count
      (else initial / min)
    """
    warnings: list[str] = []
    metrics = usage.for_resource(address, name)
    if "node_count" in metrics:
        count = max(0, int(metrics["node_count"]))
        return count, count, warnings, False

    fixed = values.get("fixed_node_count")
    if isinstance(fixed, (int, float)):
        count = max(0, int(fixed))
        return count, count, warnings, False

    autoscaling = values.get("autoscaling") or {}
    if isinstance(autoscaling, dict) and autoscaling:
        initial = autoscaling.get("initial_node_count")
        minimum = autoscaling.get("min_node_count")
        maximum = autoscaling.get("max_node_count")

        count_min: int | None = None
        if isinstance(minimum, (int, float)):
            count_min = max(0, int(minimum))
        elif isinstance(initial, (int, float)):
            count_min = max(0, int(initial))

        count_max: int | None = None
        if isinstance(maximum, (int, float)):
            count_max = max(0, int(maximum))
        elif isinstance(initial, (int, float)):
            count_max = max(0, int(initial))
        elif count_min is not None:
            count_max = count_min

        if count_min is None and count_max is None:
            return (
                0,
                0,
                [f"{address}: autoscaling present but min/initial node count missing"],
                True,
            )

        if count_min is None:
            count_min = count_max or 0
        if count_max is None:
            count_max = count_min
        if count_max < count_min:
            count_max = count_min

        warnings.append(
            f"{address}: autoscaling node group; min_nodes={count_min} max_nodes={count_max} "
            "(override with usage metric node_count)"
        )
        return count_min, count_max, warnings, False

    return (
        0,
        0,
        [f"{address}: missing fixed_node_count / autoscaling; node count assumed 0"],
        True,
    )


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
    count_min, count_max, warnings, count_missing = _node_group_count_range(
        values, usage, snap.address, snap.name
    )

    if not platform:
        return _line(
            snap,
            cfg,
            amount=0.0,
            details="node group missing template.resources.platform",
            unknown=True,
            missing_usage=count_missing,
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
        return _line(
            snap,
            cfg,
            amount=0.0,
            details=parts.details,
            unknown=True,
            missing_usage=count_missing,
            warnings=warnings,
        )

    per_node_compute_min = scale_hourly(
        parts.hourly_min, cfg.period, month_hours=catalog.month_hours
    )
    per_node_compute_max = scale_hourly(
        parts.hourly_max, cfg.period, month_hours=catalog.month_hours
    )
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
        per_node_disk = disk_item.amount_max
        warnings.extend(disk_item.warnings)
        if disk_item.unknown:
            warnings.append(f"{snap.address}: boot disk price unknown")

    local_amount, local_warnings, local_missing = _local_ssd_amount(
        snap.address, template, catalog, cfg, usage, resource_name=snap.name
    )
    warnings.extend(local_warnings)

    per_node_min = per_node_compute_min + per_node_disk + local_amount
    per_node_max = per_node_compute_max + per_node_disk + local_amount
    amount_min = per_node_min * count_min
    amount_max = per_node_max * count_max
    details = (
        f"nodes_min={count_min} nodes_max={count_max} {parts.details} "
        f"per_node_compute_min={per_node_compute_min:.6f} "
        f"per_node_compute_max={per_node_compute_max:.6f} "
        f"per_node_disk={per_node_disk:.6f}"
    )
    return _line(
        snap,
        cfg,
        amount=amount_max,
        details=details,
        missing_usage=count_missing or local_missing,
        warnings=warnings,
        amount_min=amount_min,
        amount_max=amount_max,
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
        return _line(
            snap,
            cfg,
            amount=0.0,
            details="serverless endpoint missing platform/preset/gpu/cpu",
            unknown=True,
            warnings=[f"{snap.address}: cannot resolve Compute platform for endpoint"],
        )

    # CPU selector without RAM/preset cannot be priced.
    if vcpus is not None and ram_gib == 0 and not preset:
        return _line(
            snap,
            cfg,
            amount=0.0,
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

    # GPU selector without preset: bill GPU-only hours. Otherwise pass through
    # optional vcpu/ram overrides (or let _price_compute parse the preset).
    price_kwargs: dict[str, Any] = {}
    if not preset and gpus is not None:
        price_kwargs = {"gpus": gpus, "vcpus": 0, "ram_gib": 0}
    elif not preset:
        price_kwargs = {"gpus": gpus, "vcpus": vcpus, "ram_gib": ram_gib}

    parts = _price_compute(
        address=snap.address,
        platform=platform,
        preset=preset,
        preemptible=preemptible,
        catalog=catalog,
        cfg=cfg,
        **price_kwargs,
    )

    warnings.extend(parts.warnings)
    if parts.unknown:
        return _line(
            snap,
            cfg,
            amount=0.0,
            details=parts.details,
            unknown=True,
            warnings=warnings,
        )

    compute_min = (
        scale_hourly(parts.hourly_min, cfg.period, month_hours=catalog.month_hours)
        * running_fraction
    )
    compute_max = (
        scale_hourly(parts.hourly_max, cfg.period, month_hours=catalog.month_hours)
        * running_fraction
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
        disk_amount = disk_item.amount_max * running_fraction
        warnings.extend(disk_item.warnings)
        if disk_item.unknown:
            warnings.append(f"{snap.address}: endpoint disk price unknown")

    amount_min = compute_min + disk_amount
    amount_max = compute_max + disk_amount
    details = (
        f"{parts.details} running_fraction={running_fraction} "
        f"compute_min={compute_min:.6f} compute_max={compute_max:.6f} disk={disk_amount:.6f}"
    )
    return _line(
        snap,
        cfg,
        amount=amount_max,
        details=details,
        warnings=warnings,
        amount_min=amount_min,
        amount_max=amount_max,
    )


def estimate_unknown(
    snap: ResourceSnapshot,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    _usage: PlannedUsage,
) -> LineItem:
    hint = catalog.raw.get("unsupported_priced_elsewhere", {}).get(snap.type, "no estimator")
    return _line(
        snap,
        cfg,
        amount=0.0,
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


def _sum_items_min(items: list[LineItem]) -> float:
    # LineItem.__post_init__ always materializes amount_min.
    return sum(i.amount_min or 0.0 for i in items)


def _sum_items_max(items: list[LineItem]) -> float:
    # LineItem.__post_init__ always materializes amount_max; amount == amount_max.
    return sum(i.amount_max or 0.0 for i in items)


def run_estimate(
    doc: TerraformDocument,
    catalog: PriceCatalog,
    cfg: EstimateConfig,
    usage: PlannedUsage,
) -> EstimateResult:
    before_items = [estimate_resource(s, catalog, cfg, usage) for s in doc.resources_before]
    after_items = [estimate_resource(s, catalog, cfg, usage) for s in doc.resources_after]

    # State / total: report after-only totals (no plan delta).
    if cfg.mode in {"total", "state"}:
        before_items = []

    before_total_min = _sum_items_min(before_items)
    before_total_max = _sum_items_max(before_items)
    after_total_min = _sum_items_min(after_items)
    after_total_max = _sum_items_max(after_items)
    # Interval arithmetic for plan delta: optimistic change vs pessimistic change.
    delta_min = after_total_min - before_total_max
    delta_max = after_total_max - before_total_min

    warnings: list[str] = []
    unknown: list[str] = []
    missing_usage: list[str] = []
    for item in before_items + after_items:
        warnings.extend(item.warnings)
        if item.unknown:
            unknown.append(item.address)
        if item.missing_usage:
            missing_usage.append(item.address)

    return EstimateResult(
        period=cfg.period,
        currency=cfg.currency,
        before_total_min=before_total_min,
        before_total_max=before_total_max,
        after_total_min=after_total_min,
        after_total_max=after_total_max,
        delta_min=delta_min,
        delta_max=delta_max,
        before_items=before_items,
        after_items=after_items,
        unknown_resources=_uniq(unknown),
        missing_usage_resources=_uniq(missing_usage),
        warnings=_uniq(warnings),
        catalog_path=str(catalog.path),
        document_kind=doc.kind,
    )
