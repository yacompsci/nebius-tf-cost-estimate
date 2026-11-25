from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebius_tf_cost.errors import DataError, UsageError
from nebius_tf_cost.usage import load_usage, parse_usage_cli_overrides


def test_load_usage_yaml(fixtures_dir: Path) -> None:
    usage = load_usage(fixtures_dir / "usage.yaml")
    metrics = usage.for_resource("nebius_storage_v1_bucket.datasets", "datasets")
    assert metrics["stored_gib"] == 1024
    assert metrics["class_a_requests"] == 10000
    assert metrics["egress_gib"] == 100


def test_load_usage_json(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    path.write_text(
        json.dumps(
            {
                "defaults": {"stored_gib": 10},
                "by_name": {"artifacts": {"stored_gib": 50}},
            }
        ),
        encoding="utf-8",
    )
    usage = load_usage(path)
    assert usage.for_resource("nebius_storage_v1_bucket.artifacts", "other")["stored_gib"] == 10
    assert usage.for_resource("nebius_storage_v1_bucket.x", "artifacts")["stored_gib"] == 50


def test_address_overrides_name_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "u.yaml"
    path.write_text(
        """
defaults:
  stored_gib: 1
by_name:
  datasets:
    stored_gib: 2
resources:
  nebius_storage_v1_bucket.datasets:
    stored_gib: 3
""",
        encoding="utf-8",
    )
    usage = load_usage(path)
    assert usage.for_resource("nebius_storage_v1_bucket.datasets", "datasets")["stored_gib"] == 3


def test_parse_usage_cli_overrides() -> None:
    assert parse_usage_cli_overrides(["stored_gib=12.5", "egress_gib=1"]) == {
        "stored_gib": 12.5,
        "egress_gib": 1.0,
    }


def test_parse_usage_cli_bad() -> None:
    with pytest.raises(UsageError):
        parse_usage_cli_overrides(["nope"])


def test_load_usage_none() -> None:
    usage = load_usage(None)
    assert usage.defaults == {}


def test_invalid_usage_root(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(DataError):
        load_usage(path)
