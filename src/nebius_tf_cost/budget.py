"""Budget gate: compare estimate totals against a user budget."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nebius_tf_cost.config import OnExceed
from nebius_tf_cost.errors import ExitCode
from nebius_tf_cost.estimate import EstimateResult

CompareMode = Literal["after", "delta", "abs_delta"]


@dataclass(frozen=True)
class BudgetVerdict:
    exceeded: bool
    compared_amount: float
    budget: float
    on_exceed: OnExceed
    compare_mode: CompareMode
    message: str

    @property
    def exit_code(self) -> ExitCode:
        if not self.exceeded:
            return ExitCode.OK
        if self.on_exceed == "fail":
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
    on_exceed: OnExceed,
    *,
    compare_mode: CompareMode = "after",
) -> BudgetVerdict | None:
    """Return a verdict when a budget is set; otherwise None.

    Gate always uses the pessimistic (max) estimate so CI fails when the
    upper bound exceeds the budget.
    """
    if budget is None:
        return None
    amount = select_amount(result, compare_mode)
    exceeded = amount > budget
    if exceeded:
        message = (
            f"budget exceeded (max): {amount:.4f} {result.currency}/{result.period} "
            f"> {budget:.4f} (compare={compare_mode}, on-exceed={on_exceed})"
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
        on_exceed=on_exceed,
        compare_mode=compare_mode,
        message=message,
    )
