"""Budget gate: compare estimate totals against a user budget."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nebius_tf_cost.errors import ExitCode
from nebius_tf_cost.estimate import EstimateResult

CompareMode = Literal["after", "delta", "abs_delta"]


@dataclass(frozen=True)
class BudgetVerdict:
    exceeded: bool
    compared_amount: float
    budget: float
    strict: bool
    compare_mode: CompareMode
    message: str

    @property
    def exit_code(self) -> ExitCode:
        if self.exceeded and self.strict:
            return ExitCode.BUDGET_EXCEEDED
        return ExitCode.OK


def select_amount(result: EstimateResult, compare_mode: CompareMode) -> float:
    """Select the **max** (pessimistic) figure for budget gating."""
    if compare_mode == "after":
        return result.after_total_max
    if compare_mode == "delta":
        return result.delta_max
    if compare_mode == "abs_delta":
        # Worst-case absolute change across the delta interval.
        return max(abs(result.delta_min), abs(result.delta_max))
    raise ValueError(f"unknown compare mode: {compare_mode}")


def evaluate_budget(
    result: EstimateResult,
    budget: float | None,
    *,
    strict: bool = False,
    compare_mode: CompareMode = "after",
) -> BudgetVerdict | None:
    """Return a verdict when a budget is set; otherwise None.

    Gate always uses the pessimistic (max) estimate so CI fails when the
    upper bound exceeds the budget under ``--strict-check``.
    """
    if budget is None:
        return None
    amount = select_amount(result, compare_mode)
    exceeded = amount > budget
    if exceeded:
        message = (
            f"budget exceeded (max): {amount:.4f} {result.currency}/{result.period} "
            f"> {budget:.4f} (compare={compare_mode})"
        )
    else:
        message = (
            f"within budget (max): {amount:.4f} {result.currency}/{result.period} "
            f"<= {budget:.4f} (compare={compare_mode})"
        )
    return BudgetVerdict(
        exceeded=exceeded,
        compared_amount=amount,
        budget=budget,
        strict=strict,
        compare_mode=compare_mode,
        message=message,
    )


def strict_check_reasons(
    result: EstimateResult,
    verdict: BudgetVerdict | None,
) -> list[str]:
    """Human-readable reasons ``--strict-check`` should fail (empty = pass)."""
    reasons: list[str] = []
    if result.unknown_resources:
        joined = ", ".join(result.unknown_resources)
        reasons.append(f"unknown or unpriced Nebius resources present: {joined}")
    if result.missing_usage_resources:
        joined = ", ".join(result.missing_usage_resources)
        reasons.append(
            f"resources require planned usage to estimate (pass --usage-file / "
            f"--usage-metric): {joined}"
        )
    if verdict is not None and verdict.exceeded:
        reasons.append(verdict.message)
    return reasons
