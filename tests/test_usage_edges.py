from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebius_tf_cost.errors import DataError, UsageError
from nebius_tf_cost.usage import KNOWN_METRICS, load_usage, parse_usage_cli_overrides


def test_known_metrics_includes_core_keys() -> None:
    assert "stored_gib" in KNOWN_METRICS
    assert "node_count" in KNOWN_METRICS
    assert "running_fraction" in KNOWN_METRICS
    assert "local_ssd_gib" in KNOWN_METRICS


def test_load_usage_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    usage = load_usage(path)
    assert usage.defaults == {}
    assert usage.by_address == {}
    assert usage.by_name == {}


def test_load_usage_unreadable(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="cannot read usage"):
        load_usage(tmp_path / "missing.yaml")


def test_load_usage_extensionless_yaml(tmp_path: Path) -> None:
    path = tmp_path / "usage"
    path.write_text("defaults:\n  stored_gib: 9\n", encoding="utf-8")
    usage = load_usage(path)
    assert usage.defaults["stored_gib"] == 9.0


def test_load_usage_extensionless_json_fallback(tmp_path: Path) -> None:
    path = tmp_path / "usage"
    # YAML can parse JSON objects; ensure plain JSON still loads.
    path.write_text(json.dumps({"defaults": {"stored_gib": 4}}), encoding="utf-8")
    usage = load_usage(path)
    assert usage.defaults["stored_gib"] == 4.0


def test_load_usage_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(DataError, match="invalid usage"):
        load_usage(path)


def test_resources_must_be_mapping(tmp_path: Path) -> None:
    path = tmp_path / "u.yaml"
    path.write_text("resources: [a, b]\n", encoding="utf-8")
    with pytest.raises(DataError, match="resources must be a mapping"):
        load_usage(path)


def test_by_name_must_be_mapping(tmp_path: Path) -> None:
    path = tmp_path / "u.yaml"
    path.write_text("by_name: [a]\n", encoding="utf-8")
    with pytest.raises(DataError, match="by_name must be a mapping"):
        load_usage(path)


def test_metric_value_must_be_numeric(tmp_path: Path) -> None:
    path = tmp_path / "u.yaml"
    path.write_text("defaults:\n  stored_gib: nope\n", encoding="utf-8")
    with pytest.raises(DataError, match="must be numeric"):
        load_usage(path)


def test_defaults_must_be_object(tmp_path: Path) -> None:
    path = tmp_path / "u.json"
    path.write_text(json.dumps({"defaults": ["x"]}), encoding="utf-8")
    with pytest.raises(DataError, match="expected object/map"):
        load_usage(path)


def test_parse_usage_cli_empty_key() -> None:
    with pytest.raises(UsageError, match="empty usage metric key"):
        parse_usage_cli_overrides(["=1"])


def test_parse_usage_cli_non_numeric() -> None:
    with pytest.raises(UsageError, match="must be numeric"):
        parse_usage_cli_overrides(["stored_gib=abc"])


def test_parse_usage_cli_none_and_empty() -> None:
    assert parse_usage_cli_overrides(None) == {}
    assert parse_usage_cli_overrides([]) == {}
