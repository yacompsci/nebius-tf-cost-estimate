from __future__ import annotations

import json
from pathlib import Path

from nebius_tf_cost.cli import main
from nebius_tf_cost.errors import ExitCode

ROOT = Path(__file__).resolve().parents[1]
CATALOG = str(ROOT / "data" / "prices.json")


def test_cli_help() -> None:
    # argparse help exits via SystemExit
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0


def test_cli_estimate_ok(fixtures_dir: Path, capsys) -> None:
    code = main(
        [
            "estimate",
            "--plan",
            str(fixtures_dir / "plan_create.json"),
            "--catalog",
            CATALOG,
            "--period",
            "month",
            "--usage-file",
            str(fixtures_dir / "usage.yaml"),
        ]
    )
    assert code == int(ExitCode.OK)
    out = capsys.readouterr().out
    assert "After:" in out
    assert "Delta:" in out


def test_cli_budget_fail(fixtures_dir: Path) -> None:
    code = main(
        [
            "estimate",
            "--plan",
            str(fixtures_dir / "plan_create.json"),
            "--catalog",
            CATALOG,
            "--budget",
            "1",
            "--on-exceed",
            "fail",
            "--usage-file",
            str(fixtures_dir / "usage.yaml"),
        ]
    )
    assert code == int(ExitCode.BUDGET_EXCEEDED)


def test_cli_budget_warn(fixtures_dir: Path) -> None:
    code = main(
        [
            "estimate",
            "--plan",
            str(fixtures_dir / "plan_create.json"),
            "--catalog",
            CATALOG,
            "--budget",
            "1",
            "--on-exceed",
            "warn",
            "--usage-file",
            str(fixtures_dir / "usage.yaml"),
        ]
    )
    assert code == int(ExitCode.OK)


def test_cli_json(fixtures_dir: Path, capsys) -> None:
    code = main(
        [
            "estimate",
            "--state",
            str(fixtures_dir / "state.json"),
            "--catalog",
            CATALOG,
            "--period",
            "hour",
            "--json",
        ]
    )
    assert code == int(ExitCode.OK)
    payload = json.loads(capsys.readouterr().out)
    assert payload["currency"] == "USD"
    assert payload["after_total"] > 0


def test_cli_missing_file() -> None:
    code = main(
        [
            "estimate",
            "--plan",
            "/nonexistent/plan.json",
            "--catalog",
            CATALOG,
        ]
    )
    assert code == int(ExitCode.USAGE_ERROR)
