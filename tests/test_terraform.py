from __future__ import annotations

from pathlib import Path

from nebius_tf_cost.terraform import detect_and_load


def test_load_plan(fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_create.json")
    assert doc.kind == "plan"
    assert doc.resources_before == []
    types = {r.type for r in doc.resources_after}
    assert "nebius_compute_v1_instance" in types
    assert "nebius_storage_v1_bucket" in types


def test_load_state(fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "state.json")
    assert doc.kind == "state"
    assert len(doc.resources_after) == 2
    assert any(r.type == "nebius_compute_v1_instance" for r in doc.resources_after)


def test_plan_delta_has_before_and_after(fixtures_dir: Path) -> None:
    doc = detect_and_load(fixtures_dir / "plan_delta.json")
    assert len(doc.resources_before) >= 1
    assert len(doc.resources_after) >= 1
    before = next(r for r in doc.resources_before if r.name == "training")
    after = next(r for r in doc.resources_after if r.name == "training")
    assert before.values["resources"]["preset"] == "2vcpu-8gb"
    assert after.values["resources"]["preset"] == "4vcpu-16gb"
