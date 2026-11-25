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
    assert "min=" in out
    assert "max=" in out
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
    assert payload["after_total_max"] == payload["after_total"]
    assert "after_total_min" in payload
    assert "delta_min" in payload
    assert "delta_max" in payload


def test_cli_budget_gates_on_max(fixtures_dir: Path, capsys) -> None:
    """Budget between min and max of a spot+autoscale plan must fail on max."""
    # First get the range without budget.
    code = main(
        [
            "estimate",
            "--plan",
            str(fixtures_dir / "plan_minmax.json"),
            "--catalog",
            CATALOG,
            "--period",
            "hour",
            "--json",
        ]
    )
    assert code == int(ExitCode.OK)
    payload = json.loads(capsys.readouterr().out)
    amin = payload["after_total_min"]
    amax = payload["after_total_max"]
    assert amin < amax
    mid = (amin + amax) / 2

    code_fail = main(
        [
            "estimate",
            "--plan",
            str(fixtures_dir / "plan_minmax.json"),
            "--catalog",
            CATALOG,
            "--period",
            "hour",
            "--budget",
            str(mid),
            "--on-exceed",
            "fail",
        ]
    )
    assert code_fail == int(ExitCode.BUDGET_EXCEEDED)

    code_ok = main(
        [
            "estimate",
            "--plan",
            str(fixtures_dir / "plan_minmax.json"),
            "--catalog",
            CATALOG,
            "--period",
            "hour",
            "--budget",
            str(amax + 1),
            "--on-exceed",
            "fail",
        ]
    )
    assert code_ok == int(ExitCode.OK)


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
