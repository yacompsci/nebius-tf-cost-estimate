"""Load planned usage for usage-based resources (object storage, etc.)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from nebius_tf_cost.config import PlannedUsage
from nebius_tf_cost.errors import DataError, UsageError

# Known metric keys for documentation / validation hints.
KNOWN_METRICS = frozenset(
    {
        "stored_gib",
        "class_a_requests",
        "class_b_requests",
        "egress_gib",
        "intelligent_tier",  # 0=warm (default), 1=cold — stored as float for uniformity
        "node_count",  # MK8s node group size override (autoscaling)
        "running_fraction",  # Serverless AI endpoint duty cycle 0..1
        "local_ssd_gib",  # Local SSD GiB when local_disks requested
    }
)


def _as_float_map(obj: Any, *, context: str) -> dict[str, float]:
    if not isinstance(obj, dict):
        raise DataError(f"{context}: expected object/map of metrics")
    out: dict[str, float] = {}
    for key, value in obj.items():
        if not isinstance(key, str):
            raise DataError(f"{context}: metric keys must be strings")
        try:
            out[key] = float(value)
        except (TypeError, ValueError) as exc:
            raise DataError(f"{context}: metric {key!r} must be numeric") from exc
    return out


def _parse_usage_text(text: str, *, suffix: str, path: Path) -> Any:
    try:
        if suffix in {".yaml", ".yml"}:
            return yaml.safe_load(text)
        if suffix == ".json":
            return json.loads(text)
        # Extensionless / unknown: try YAML first, then JSON.
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise DataError(f"invalid usage file {path}: {exc}") from exc


def _metrics_by_key(
    section: Any,
    *,
    section_name: str,
    key_label: str,
) -> dict[str, dict[str, float]]:
    data = section or {}
    if not isinstance(data, dict):
        raise DataError(f"{section_name} must be a mapping of {key_label} -> metrics")
    out: dict[str, dict[str, float]] = {}
    for key, metrics in data.items():
        if not isinstance(key, str):
            raise DataError(f"{section_name} keys must be strings")
        out[key] = _as_float_map(metrics, context=f"{section_name}.{key}")
    return out


def load_usage(path: Path | None) -> PlannedUsage:
    """Parse a YAML or JSON planned-usage file.

    Schema::

        defaults:
          stored_gib: 100
        resources:
          nebius_storage_v1_bucket.datasets:
            stored_gib: 500
            class_a_requests: 10000
            class_b_requests: 50000
            egress_gib: 20
        by_name:
          artifacts:
            stored_gib: 200
    """
    if path is None:
        return PlannedUsage()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataError(f"cannot read usage file {path}: {exc}") from exc

    data = _parse_usage_text(text, suffix=path.suffix.lower(), path=path)
    if data is None:
        return PlannedUsage()
    if not isinstance(data, dict):
        raise DataError(f"usage file root must be a mapping: {path}")

    defaults = _as_float_map(data.get("defaults") or {}, context="defaults")
    by_address = _metrics_by_key(
        data.get("resources"),
        section_name="resources",
        key_label="address",
    )
    by_name = _metrics_by_key(
        data.get("by_name"),
        section_name="by_name",
        key_label="name",
    )
    return PlannedUsage(by_address=by_address, by_name=by_name, defaults=defaults)


def parse_usage_cli_overrides(pairs: list[str] | None) -> dict[str, float]:
    """Parse repeated ``--usage-metric KEY=VALUE`` overrides into defaults."""
    if not pairs:
        return {}
    out: dict[str, float] = {}
    for item in pairs:
        if "=" not in item:
            raise UsageError(f"usage metric must be KEY=VALUE, got {item!r}")
        key, raw = item.split("=", 1)
        key = key.strip()
        if not key:
            raise UsageError(f"empty usage metric key in {item!r}")
        try:
            out[key] = float(raw.strip())
        except ValueError as exc:
            raise UsageError(f"usage metric value must be numeric: {item!r}") from exc
    return out
