from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebius_tf_cost.errors import DataError
from nebius_tf_cost.terraform import (
    _actions_to_kind,
    _provider_is_nebius,
    detect_and_load,
)


def test_actions_to_kind_variants() -> None:
    assert _actions_to_kind(None) == "no-op"
    assert _actions_to_kind([]) == "no-op"
    assert _actions_to_kind(["create"]) == "create"
    assert _actions_to_kind(["delete"]) == "delete"
    assert _actions_to_kind(["update"]) == "update"
    assert _actions_to_kind(["create", "delete"]) == "replace"
    assert _actions_to_kind(["delete", "create"]) == "replace"
    assert _actions_to_kind(["read"]) == "read"
    assert _actions_to_kind(["no-op"]) == "no-op"
    assert _actions_to_kind(["create", "update"]) == "unknown"


def test_provider_is_nebius() -> None:
    assert _provider_is_nebius('provider["registry.terraform.io/nebius/nebius"]')
    assert _provider_is_nebius("registry.terraform.io/nebius/nebius")
    assert _provider_is_nebius("nebius")
    assert not _provider_is_nebius("registry.terraform.io/hashicorp/aws")


def test_detect_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(DataError, match="invalid terraform JSON"):
        detect_and_load(path)


def test_detect_unreadable(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="cannot read terraform"):
        detect_and_load(tmp_path / "missing.json")


def test_detect_root_must_be_object(tmp_path: Path) -> None:
    path = tmp_path / "arr.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(DataError, match="root must be an object"):
        detect_and_load(path)


def test_detect_unrecognized(tmp_path: Path) -> None:
    path = tmp_path / "weird.json"
    path.write_text(json.dumps({"foo": 1}), encoding="utf-8")
    with pytest.raises(DataError, match="unrecognized terraform JSON"):
        detect_and_load(path)


def test_plan_skips_non_nebius_and_non_dict_changes(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "format_version": "1.2",
                "resource_changes": [
                    "not-a-dict",
                    {
                        "address": "aws_instance.x",
                        "type": "aws_instance",
                        "name": "x",
                        "provider_name": "registry.terraform.io/hashicorp/aws",
                        "change": {
                            "actions": ["create"],
                            "before": None,
                            "after": {"ami": "ami-1"},
                        },
                    },
                    {
                        "address": "nebius_vpc_v1_allocation.a",
                        "type": "nebius_vpc_v1_allocation",
                        "name": "a",
                        "provider_name": "registry.terraform.io/nebius/nebius",
                        "change": {
                            "actions": ["delete"],
                            "before": {"id": "1"},
                            "after": None,
                        },
                    },
                    {
                        "address": "nebius_compute_v1_instance.i",
                        "type": "nebius_compute_v1_instance",
                        "name": "i",
                        "provider_name": "registry.terraform.io/nebius/nebius",
                        "change": {
                            "actions": ["create", "delete"],
                            "before": {"resources": {"platform": "cpu-e2", "preset": "2vcpu-8gb"}},
                            "after": {"resources": {"platform": "cpu-e2", "preset": "4vcpu-16gb"}},
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    doc = detect_and_load(path)
    assert doc.kind == "plan"
    types = {r.type for r in doc.resources_before + doc.resources_after}
    assert "aws_instance" not in types
    assert "nebius_vpc_v1_allocation" in types
    assert "nebius_compute_v1_instance" in types
    replace = next(r for r in doc.resources_after if r.name == "i")
    assert replace.change_kind == "replace"


def test_planned_values_only_plan(tmp_path: Path) -> None:
    path = tmp_path / "planned.json"
    path.write_text(
        json.dumps(
            {
                "format_version": "1.2",
                "planned_values": {
                    "root_module": {
                        "resources": [
                            {
                                "address": "nebius_storage_v1_bucket.b",
                                "mode": "managed",
                                "type": "nebius_storage_v1_bucket",
                                "name": "b",
                                "provider_name": "registry.terraform.io/nebius/nebius",
                                "values": {"name": "b"},
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    doc = detect_and_load(path)
    assert doc.kind == "plan"
    assert len(doc.resources_after) == 1
    assert doc.resources_after[0].type == "nebius_storage_v1_bucket"


def test_legacy_state_resources_array(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "terraform_version": "0.12.0",
                "resources": [
                    "skip-me",
                    {
                        "mode": "data",
                        "type": "nebius_compute_v1_instance",
                        "name": "data_only",
                        "provider": "registry.terraform.io/nebius/nebius",
                        "instances": [{"attributes": {}}],
                    },
                    {
                        "mode": "managed",
                        "type": "nebius_vpc_v1_allocation",
                        "name": "ip",
                        "provider": "registry.terraform.io/nebius/nebius",
                        "instances": [{"attributes": {"ipv4_public": {}}}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    doc = detect_and_load(path)
    assert doc.kind == "state"
    assert len(doc.resources_after) == 1
    assert doc.resources_after[0].name == "ip"


def test_state_child_modules_and_indexed_instances(tmp_path: Path) -> None:
    path = tmp_path / "state_mod.json"
    path.write_text(
        json.dumps(
            {
                "format_version": "1.0",
                "values": {
                    "root_module": {
                        "resources": [],
                        "child_modules": [
                            {
                                "address": "module.workers",
                                "resources": [
                                    {
                                        "mode": "managed",
                                        "type": "nebius_compute_v1_instance",
                                        "name": "node",
                                        "provider_name": "registry.terraform.io/nebius/nebius",
                                        "instances": [
                                            {
                                                "index_key": 0,
                                                "attributes": {
                                                    "resources": {
                                                        "platform": "cpu-e2",
                                                        "preset": "2vcpu-8gb",
                                                    }
                                                },
                                            },
                                            {
                                                "index_key": "blue",
                                                "attributes": {
                                                    "resources": {
                                                        "platform": "cpu-e2",
                                                        "preset": "2vcpu-8gb",
                                                    }
                                                },
                                            },
                                            "skip",
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    doc = detect_and_load(path)
    addrs = {r.address for r in doc.resources_after}
    assert 'module.workers.nebius_compute_v1_instance.node[0]' in addrs
    assert 'module.workers.nebius_compute_v1_instance.node["blue"]' in addrs
