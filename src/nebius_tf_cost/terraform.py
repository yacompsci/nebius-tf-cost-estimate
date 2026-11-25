"""Parse Terraform plan JSON (`terraform show -json`) and state JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from nebius_tf_cost.errors import DataError

ChangeKind = Literal["create", "update", "delete", "replace", "no-op", "read", "unknown"]
Side = Literal["before", "after"]


@dataclass(frozen=True)
class ResourceSnapshot:
    """One resource configuration (before or after a change, or from state)."""

    address: str
    type: str
    name: str
    provider_name: str
    values: dict[str, Any] = field(default_factory=dict)
    change_kind: ChangeKind = "no-op"
    side: Side = "after"


@dataclass(frozen=True)
class TerraformDocument:
    kind: Literal["plan", "state", "unknown"]
    path: Path
    resources_before: list[ResourceSnapshot]
    resources_after: list[ResourceSnapshot]
    raw: dict[str, Any]


def _provider_is_nebius(provider_name: str) -> bool:
    # e.g. provider["registry.terraform.io/nebius/nebius"]
    return "nebius/nebius" in provider_name or provider_name.endswith("nebius")


def _actions_to_kind(actions: list[str] | None) -> ChangeKind:
    if not actions:
        return "no-op"
    acts = set(actions)
    if acts == {"create"}:
        return "create"
    if acts == {"delete"}:
        return "delete"
    if acts == {"update"}:
        return "update"
    if acts == {"delete", "create"} or acts == {"create", "delete"}:
        return "replace"
    if acts == {"read"}:
        return "read"
    if acts == {"no-op"}:
        return "no-op"
    return "unknown"


def _snap(
    *,
    address: str,
    rtype: str,
    name: str,
    provider_name: str,
    values: Any,
    change_kind: ChangeKind,
    side: Side,
) -> ResourceSnapshot | None:
    if not isinstance(values, dict):
        values = {}
    if not _provider_is_nebius(provider_name) and not rtype.startswith("nebius_"):
        return None
    return ResourceSnapshot(
        address=address,
        type=rtype,
        name=name,
        provider_name=provider_name,
        values=values,
        change_kind=change_kind,
        side=side,
    )


_BEFORE_KINDS = frozenset({"delete", "update", "replace", "no-op"})
_AFTER_KINDS = frozenset({"create", "update", "replace", "no-op"})


def _append_side(
    out: list[ResourceSnapshot],
    *,
    address: str,
    rtype: str,
    name: str,
    provider_name: str,
    values: Any,
    change_kind: ChangeKind,
    side: Side,
) -> None:
    """Append a snapshot when values are present and the provider/type matches."""
    if values is None:
        return
    snap = _snap(
        address=address,
        rtype=rtype,
        name=name,
        provider_name=provider_name,
        values=values or {},
        change_kind=change_kind,
        side=side,
    )
    if snap is not None:
        out.append(snap)


def _from_plan(raw: dict[str, Any], path: Path) -> TerraformDocument:
    before: list[ResourceSnapshot] = []
    after: list[ResourceSnapshot] = []
    for change in raw.get("resource_changes") or []:
        if not isinstance(change, dict):
            continue
        address = str(change.get("address") or "")
        rtype = str(change.get("type") or "")
        name = str(change.get("name") or "")
        provider_name = str(change.get("provider_name") or "")
        change_block = change.get("change") or {}
        actions = change_block.get("actions") or []
        kind = _actions_to_kind(list(actions) if isinstance(actions, list) else None)
        before_vals = change_block.get("before")
        after_vals = change_block.get("after")
        # For deletes, after is null; for creates, before is null.
        if before_vals is not None or kind in _BEFORE_KINDS:
            _append_side(
                before,
                address=address,
                rtype=rtype,
                name=name,
                provider_name=provider_name,
                values=before_vals,
                change_kind=kind,
                side="before",
            )
        if after_vals is not None or kind in _AFTER_KINDS:
            _append_side(
                after,
                address=address,
                rtype=rtype,
                name=name,
                provider_name=provider_name,
                values=after_vals,
                change_kind=kind,
                side="after",
            )
    return TerraformDocument(
        kind="plan",
        path=path,
        resources_before=before,
        resources_after=after,
        raw=raw,
    )


def _resource_instances(res: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize state ``instances`` and planned_values ``values`` shapes."""
    instances = res.get("instances")
    if isinstance(instances, list) and instances:
        return [inst for inst in instances if isinstance(inst, dict)]
    # planned_values resources expose a single ``values`` map (no instances).
    values = res.get("values")
    if isinstance(values, dict):
        return [{"attributes": values}]
    return []


def _instance_address(base: str, index_key: Any, idx: int, instance_count: int) -> str:
    if index_key is None and instance_count == 1:
        return base
    if isinstance(index_key, str):
        return f'{base}["{index_key}"]'
    return f"{base}[{index_key if index_key is not None else idx}]"


def _flatten_state_module(module: dict[str, Any], prefix: str = "") -> list[ResourceSnapshot]:
    out: list[ResourceSnapshot] = []
    for res in module.get("resources") or []:
        if not isinstance(res, dict):
            continue
        mode = res.get("mode")
        if mode and mode != "managed":
            continue
        rtype = str(res.get("type") or "")
        name = str(res.get("name") or "")
        provider_name = str(res.get("provider") or res.get("provider_name") or "")
        instances = _resource_instances(res)
        base = f"{prefix}.{rtype}.{name}" if prefix else f"{rtype}.{name}"
        for idx, instance in enumerate(instances):
            address = _instance_address(base, instance.get("index_key"), idx, len(instances))
            values = instance.get("attributes") or {}
            snap = _snap(
                address=address,
                rtype=rtype,
                name=name,
                provider_name=provider_name,
                values=values,
                change_kind="no-op",
                side="after",
            )
            if snap is not None:
                out.append(snap)
    for child in module.get("child_modules") or []:
        if not isinstance(child, dict):
            continue
        child_addr = str(child.get("address") or "")
        out.extend(_flatten_state_module(child, prefix=child_addr))
    return out


def _from_state(raw: dict[str, Any], path: Path) -> TerraformDocument:
    values = raw.get("values") or {}
    root = values.get("root_module") or {}
    resources = _flatten_state_module(root)
    # State total: before empty, after = current
    return TerraformDocument(
        kind="state",
        path=path,
        resources_before=[],
        resources_after=resources,
        raw=raw,
    )


def detect_and_load(path: Path) -> TerraformDocument:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DataError(f"cannot read terraform JSON {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DataError(f"invalid terraform JSON {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise DataError(f"terraform JSON root must be an object: {path}")

    has_plan_markers = (
        "resource_changes" in raw
        or "planned_values" in raw
        or (raw.get("format_version") and "configuration" in raw)
    )
    if has_plan_markers:
        # Plan files typically have resource_changes; some have planned_values.
        if "resource_changes" in raw:
            return _from_plan(raw, path)
        # Fall through: treat planned_values like state values
        if "planned_values" in raw:
            synthetic = {"values": raw["planned_values"]}
            doc = _from_state(synthetic, path)
            return TerraformDocument(
                kind="plan",
                path=path,
                resources_before=[],
                resources_after=doc.resources_after,
                raw=raw,
            )

    if "values" in raw and isinstance(raw.get("values"), dict):
        return _from_state(raw, path)

    # Older state format: root resources array
    if "resources" in raw and isinstance(raw["resources"], list) and "terraform_version" in raw:
        # Convert lightly
        root = {"resources": []}
        for res in raw["resources"]:
            if not isinstance(res, dict):
                continue
            if res.get("mode") and res.get("mode") != "managed":
                continue
            root["resources"].append(res)
        return _from_state({"values": {"root_module": root}}, path)

    raise DataError(
        f"unrecognized terraform JSON (expected plan with resource_changes "
        f"or state with values.root_module): {path}"
    )
