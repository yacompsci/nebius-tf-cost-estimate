from __future__ import annotations

from nebius_tf_cost.budget import evaluate_budget, select_amount, strict_check_reasons
from nebius_tf_cost.errors import ExitCode
from nebius_tf_cost.estimate import EstimateResult


def _result(
    after_max: float,
    *,
    after_min: float | None = None,
    before_max: float = 0.0,
    before_min: float | None = None,
    delta_max: float | None = None,
    delta_min: float | None = None,
    unknown_resources: list[str] | None = None,
    missing_usage_resources: list[str] | None = None,
    warnings: list[str] | None = None,
) -> EstimateResult:
    amin = after_max if after_min is None else after_min
    bmin = before_max if before_min is None else before_min
    dmax = after_max - bmin if delta_max is None else delta_max
    dmin = amin - before_max if delta_min is None else delta_min
    return EstimateResult(
        period="month",
        currency="USD",
        before_total_min=bmin,
        before_total_max=before_max,
        after_total_min=amin,
        after_total_max=after_max,
        delta_min=dmin,
        delta_max=dmax,
        before_items=[],
        after_items=[],
        unknown_resources=list(unknown_resources or []),
        missing_usage_resources=list(missing_usage_resources or []),
        warnings=list(warnings or []),
        catalog_path="data/prices.json",
        document_kind="plan",
    )


def test_no_budget() -> None:
    assert evaluate_budget(_result(10), None, strict=True) is None


def test_within_budget() -> None:
    verdict = evaluate_budget(_result(10), 20, strict=True)
    assert verdict is not None
    assert not verdict.exceeded
    assert verdict.exit_code == ExitCode.OK


def test_exceed_without_strict() -> None:
    verdict = evaluate_budget(_result(30), 20, strict=False)
    assert verdict is not None
    assert verdict.exceeded
    assert verdict.exit_code == ExitCode.OK
    assert "strict" not in verdict.message


def test_exceed_with_strict() -> None:
    verdict = evaluate_budget(_result(30), 20, strict=True)
    assert verdict is not None
    assert verdict.exceeded
    assert verdict.exit_code == ExitCode.BUDGET_EXCEEDED
    assert verdict.strict is True


def test_compare_delta() -> None:
    verdict = evaluate_budget(
        _result(100, delta_max=5, delta_min=5), 10, strict=True, compare_mode="delta"
    )
    assert verdict is not None
    assert not verdict.exceeded
    verdict2 = evaluate_budget(
        _result(100, delta_max=15, delta_min=15), 10, strict=True, compare_mode="delta"
    )
    assert verdict2 is not None
    assert verdict2.exceeded


def test_compare_abs_delta() -> None:
    verdict = evaluate_budget(
        _result(50, delta_max=-12, delta_min=-12), 10, strict=True, compare_mode="abs_delta"
    )
    assert verdict is not None
    assert verdict.exceeded


def test_gate_on_max_not_min() -> None:
    """Budget between min and max: max exceeds → fail; gating must not use min."""
    result = _result(30, after_min=10)
    assert select_amount(result, "after") == 30
    verdict = evaluate_budget(result, 20, strict=True)
    assert verdict is not None
    assert verdict.exceeded
    assert verdict.compared_amount == 30
    assert "max" in verdict.message

    # If we wrongly gated on min (10), budget 20 would pass — prove max gates.
    assert result.after_total_min < 20 < result.after_total_max


def test_gate_delta_uses_delta_max() -> None:
    result = _result(
        after_max=100,
        after_min=40,
        before_max=30,
        before_min=10,
    )
    # delta_min = 40-30 = 10; delta_max = 100-10 = 90
    assert result.delta_min == 10
    assert result.delta_max == 90
    verdict = evaluate_budget(result, 50, strict=True, compare_mode="delta")
    assert verdict is not None
    assert verdict.exceeded
    assert verdict.compared_amount == 90


def test_strict_check_reasons_combines_conditions() -> None:
    result = _result(
        30,
        unknown_resources=["nebius_foo.x"],
        missing_usage_resources=["nebius_storage_v1_bucket.b"],
    )
    verdict = evaluate_budget(result, 20, strict=True)
    reasons = strict_check_reasons(result, verdict)
    assert len(reasons) == 3
    assert any("unknown" in r for r in reasons)
    assert any("planned usage" in r for r in reasons)
    assert any("budget exceeded" in r for r in reasons)

    clean = _result(10)
    assert strict_check_reasons(clean, evaluate_budget(clean, 20, strict=True)) == []
