from __future__ import annotations

from nebius_tf_cost.budget import evaluate_budget
from nebius_tf_cost.errors import ExitCode
from nebius_tf_cost.estimate import EstimateResult


def _result(after: float, delta: float = 0.0) -> EstimateResult:
    return EstimateResult(
        period="month",
        currency="USD",
        before_total=after - delta,
        after_total=after,
        delta=delta,
        before_items=[],
        after_items=[],
        unknown_resources=[],
        warnings=[],
        catalog_path="data/prices.json",
        document_kind="plan",
    )


def test_no_budget() -> None:
    assert evaluate_budget(_result(10), None, "fail") is None


def test_within_budget() -> None:
    verdict = evaluate_budget(_result(10), 20, "fail")
    assert verdict is not None
    assert not verdict.exceeded
    assert verdict.exit_code == ExitCode.OK


def test_exceed_warn() -> None:
    verdict = evaluate_budget(_result(30), 20, "warn")
    assert verdict is not None
    assert verdict.exceeded
    assert verdict.exit_code == ExitCode.OK


def test_exceed_fail() -> None:
    verdict = evaluate_budget(_result(30), 20, "fail")
    assert verdict is not None
    assert verdict.exceeded
    assert verdict.exit_code == ExitCode.BUDGET_EXCEEDED


def test_compare_delta() -> None:
    verdict = evaluate_budget(_result(100, delta=5), 10, "fail", compare_mode="delta")
    assert verdict is not None
    assert not verdict.exceeded
    verdict2 = evaluate_budget(_result(100, delta=15), 10, "fail", compare_mode="delta")
    assert verdict2 is not None
    assert verdict2.exceeded


def test_compare_abs_delta() -> None:
    verdict = evaluate_budget(_result(50, delta=-12), 10, "fail", compare_mode="abs_delta")
    assert verdict is not None
    assert verdict.exceeded
