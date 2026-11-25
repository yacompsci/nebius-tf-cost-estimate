from __future__ import annotations

import pytest

from nebius_tf_cost.periods import hours_for_period, scale_hourly, scale_monthly


def test_hours_for_period_defaults() -> None:
    assert hours_for_period("hour") == 1.0
    assert hours_for_period("month") == 730.0
    assert hours_for_period("year") == 8760.0


def test_scale_hourly_month() -> None:
    assert scale_hourly(1.0, "month") == 730.0
    assert scale_hourly(2.0, "year") == 17520.0


def test_scale_monthly_to_hour() -> None:
    # 730 USD/month -> 1 USD/hour
    assert scale_monthly(730.0, "hour") == pytest.approx(1.0)
    assert scale_monthly(730.0, "year") == pytest.approx(730.0 * (8760 / 730))


def test_custom_month_hours() -> None:
    assert hours_for_period("month", month_hours=720) == 720.0
    assert scale_monthly(720.0, "hour", month_hours=720) == pytest.approx(1.0)
