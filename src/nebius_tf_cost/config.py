"""Typed CLI / runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from nebius_tf_cost.errors import UsageError
from nebius_tf_cost.periods import VALID_PERIODS, Period

Currency = Literal["USD", "ILS"]
OnExceed = Literal["warn", "fail"]
Mode = Literal["auto", "plan", "state", "total"]

VALID_CURRENCIES: frozenset[str] = frozenset({"USD", "ILS"})
VALID_ON_EXCEED: frozenset[str] = frozenset({"warn", "fail"})
VALID_MODES: frozenset[str] = frozenset({"auto", "plan", "state", "total"})


@dataclass(frozen=True)
class EstimateConfig:
    """Immutable runtime config for one estimate run."""

    input_path: Path
    period: Period = "month"
    currency: Currency = "USD"
    budget: float | None = None
    on_exceed: OnExceed = "warn"
    usage_path: Path | None = None
    catalog_path: Path | None = None
    mode: Mode = "auto"
    json_output: bool = False
    include_zero: bool = False
    preemptible_policy: Literal["on_demand", "floor", "reject"] = "on_demand"

    def validate(self) -> None:
        def _one_of(name: str, value: str, allowed: frozenset[str]) -> None:
            if value not in allowed:
                raise UsageError(f"{name} must be one of {sorted(allowed)}, got {value!r}")

        _one_of("period", self.period, VALID_PERIODS)
        _one_of("currency", self.currency, VALID_CURRENCIES)
        _one_of("on-exceed", self.on_exceed, VALID_ON_EXCEED)
        _one_of("mode", self.mode, VALID_MODES)
        if self.budget is not None and self.budget < 0:
            raise UsageError("budget must be >= 0")
        if not self.input_path.exists():
            raise UsageError(f"input file not found: {self.input_path}")
        if self.usage_path is not None and not self.usage_path.exists():
            raise UsageError(f"usage file not found: {self.usage_path}")
        if self.catalog_path is not None and not self.catalog_path.exists():
            raise UsageError(f"catalog file not found: {self.catalog_path}")


@dataclass
class PlannedUsage:
    """User-provided usage for resources whose cost is not size/time alone."""

    # address or name -> metrics
    by_address: dict[str, dict[str, float]] = field(default_factory=dict)
    by_name: dict[str, dict[str, float]] = field(default_factory=dict)
    defaults: dict[str, float] = field(default_factory=dict)

    def for_resource(self, address: str, name: str) -> dict[str, float]:
        merged = dict(self.defaults)
        if name in self.by_name:
            merged.update(self.by_name[name])
        if address in self.by_address:
            merged.update(self.by_address[address])
        return merged
