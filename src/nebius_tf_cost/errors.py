"""Structured errors and process exit codes."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """CLI exit codes (stable for CI/scripts)."""

    OK = 0
    BUDGET_EXCEEDED = 2
    USAGE_ERROR = 64  # bad args / config / input shape
    DATA_ERROR = 65  # unreadable plan/state/catalog
    INTERNAL = 70


class EstimateError(Exception):
    """Base error for the estimator."""

    exit_code: ExitCode = ExitCode.INTERNAL

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UsageError(EstimateError):
    """Invalid user input or flags."""

    exit_code = ExitCode.USAGE_ERROR


class DataError(EstimateError):
    """Missing or invalid plan/state/catalog/usage data."""

    exit_code = ExitCode.DATA_ERROR
