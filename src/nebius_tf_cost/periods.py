"""Billing period helpers.

Nebius publishes monthly storage rates as GiB per 730 hours (~1 month).
Hourly compute rates scale linearly with the selected period.
"""

from __future__ import annotations

from typing import Literal

Period = Literal["hour", "month", "year"]

HOURS_PER_MONTH = 730
HOURS_PER_YEAR = 8760

VALID_PERIODS: frozenset[str] = frozenset({"hour", "month", "year"})


def hours_for_period(period: Period, *, month_hours: int = HOURS_PER_MONTH) -> float:
    """Return the number of hours represented by *period*."""
    if period == "hour":
        return 1.0
    if period == "month":
        return float(month_hours)
    if period == "year":
        return float(HOURS_PER_YEAR)
    raise ValueError(f"unsupported period: {period!r}")


def scale_hourly(
    amount_per_hour: float,
    period: Period,
    *,
    month_hours: int = HOURS_PER_MONTH,
) -> float:
    """Scale an hourly rate to the selected period."""
    return amount_per_hour * hours_for_period(period, month_hours=month_hours)


def scale_monthly(
    amount_per_month: float, period: Period, *, month_hours: int = HOURS_PER_MONTH
) -> float:
    """Scale a monthly (730h) rate to the selected period."""
    if period == "month":
        return amount_per_month
    hour_rate = amount_per_month / float(month_hours)
    return scale_hourly(hour_rate, period, month_hours=month_hours)
