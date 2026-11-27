"""Validate shipped examples/usage/*.yaml parse with the real loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from nebius_tf_cost.usage import KNOWN_METRICS, load_usage

ROOT = Path(__file__).resolve().parents[1]
USAGE_DIR = ROOT / "examples" / "usage"
ROOT_USAGE = ROOT / "examples" / "usage.yaml"


def _example_usage_paths() -> list[Path]:
    paths = sorted(USAGE_DIR.glob("*.yaml")) + sorted(USAGE_DIR.glob("*.yml"))
    assert paths, f"expected usage examples under {USAGE_DIR}"
    return paths


@pytest.mark.parametrize("path", _example_usage_paths(), ids=lambda p: p.name)
def test_example_usage_files_load(path: Path) -> None:
    usage = load_usage(path)
    # At least one metric somewhere (defaults / resources / by_name).
    metrics: set[str] = set(usage.defaults)
    for block in (*usage.by_address.values(), *usage.by_name.values()):
        metrics.update(block)
    assert metrics, f"{path} has no metrics"
    unknown = metrics - KNOWN_METRICS
    assert not unknown, f"{path} uses undocumented metrics: {sorted(unknown)}"


def test_root_examples_usage_yaml_loads() -> None:
    usage = load_usage(ROOT_USAGE)
    assert usage.for_resource("nebius_storage_v1_bucket.datasets", "datasets")["egress_gib"] == 100
