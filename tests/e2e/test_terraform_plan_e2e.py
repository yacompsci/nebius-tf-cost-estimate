"""Plan-only Terraform e2e: real init/plan/show JSON → nebius-tf-cost estimate.

Requires the Terraform CLI. Skips when `terraform` is not on PATH.
Never runs `terraform apply`. No Nebius credentials are used.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from nebius_tf_cost.cli import main
from nebius_tf_cost.errors import ExitCode

TF_FIXTURE = Path(__file__).resolve().parent / "terraform"
ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "data" / "prices.json"

EXPECTED_ADDRESSES = {
    "nebius_compute_v1_disk.data",
    "nebius_compute_v1_instance.training",
    "nebius_storage_v1_bucket.datasets",
    "nebius_vpc_v1_network.main",
}


def _terraform_available() -> bool:
    return shutil.which("terraform") is not None


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not _terraform_available(), reason="terraform CLI not installed"),
]


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=merged,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


@pytest.fixture
def terraform_workdir(tmp_path: Path) -> Path:
    """Copy HCL/usage into an isolated workdir (avoids dirtying the fixture tree)."""
    work = tmp_path / "tf"
    work.mkdir()
    for name in ("versions.tf", "providers.tf", "main.tf", "usage.yaml"):
        shutil.copy2(TF_FIXTURE / name, work / name)
    # Prefer the committed lockfile when present for reproducible provider selection.
    lock = TF_FIXTURE / ".terraform.lock.hcl"
    if lock.exists():
        shutil.copy2(lock, work / ".terraform.lock.hcl")
    return work


def test_plan_json_estimate_shape(terraform_workdir: Path, tmp_path: Path, capsys) -> None:
    plugin_cache = tmp_path / "plugin-cache"
    plugin_cache.mkdir()
    env = {"TF_PLUGIN_CACHE_DIR": str(plugin_cache)}

    _run(["terraform", "init", "-backend=false", "-input=false"], cwd=terraform_workdir, env=env)

    plan_bin = terraform_workdir / "tfplan"
    _run(
        ["terraform", "plan", "-input=false", f"-out={plan_bin}"],
        cwd=terraform_workdir,
        env=env,
    )

    plan_json_path = tmp_path / "plan.json"
    show = subprocess.run(
        ["terraform", "show", "-json", str(plan_bin)],
        cwd=terraform_workdir,
        env={**os.environ, **env},
        check=False,
        capture_output=True,
        text=True,
    )
    assert show.returncode == 0, show.stderr
    plan_json_path.write_text(show.stdout, encoding="utf-8")

    plan = json.loads(show.stdout)
    assert plan.get("format_version"), "expected terraform show -json format_version"
    changes = plan.get("resource_changes") or []
    addresses = {c["address"] for c in changes}
    assert addresses >= EXPECTED_ADDRESSES
    assert all(c.get("change", {}).get("actions") == ["create"] for c in changes)

    code = main(
        [
            "estimate",
            "--plan",
            str(plan_json_path),
            "--usage-file",
            str(terraform_workdir / "usage.yaml"),
            "--catalog",
            str(CATALOG),
            "--period",
            "month",
            "--json",
            "--include-zero",
        ]
    )
    assert code == int(ExitCode.OK)

    payload = json.loads(capsys.readouterr().out)
    assert payload["document_kind"] == "plan"
    assert payload["currency"] == "USD"
    assert payload["period"] == "month"
    assert isinstance(payload["after_total"], (int, float))
    assert payload["after_total"] > 0
    assert payload["after_total_min"] <= payload["after_total_max"]
    assert payload["after_total_max"] == payload["after_total"]
    assert "delta_min" in payload and "delta_max" in payload
    assert payload["delta_max"] == payload["after_total_max"]  # create-only plan

    after_addresses = {item["address"] for item in payload["after_items"]}
    assert after_addresses >= EXPECTED_ADDRESSES
