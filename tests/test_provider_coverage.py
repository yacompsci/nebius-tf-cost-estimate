"""Tests for provider resource inventory and coverage diff helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nebius_tf_cost.catalog import load_catalog
from nebius_tf_cost.estimate import ESTIMATORS
from nebius_tf_cost.provider_coverage import (
    ISSUE_TITLE_PREFIX,
    build_issue_body,
    covered_resource_types,
    diff_coverage,
    fetch_provider_resource_types,
    issue_title_for,
    open_or_update_coverage_issue,
    resource_type_from_doc_filename,
    resource_type_from_issue_title,
    resource_types_from_doc_filenames,
    select_matching_issue,
    uncovered_resource_types,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "prices.json"


def test_resource_type_from_doc_filename() -> None:
    assert resource_type_from_doc_filename("compute_v1_instance.md") == (
        "nebius_compute_v1_instance"
    )
    assert resource_type_from_doc_filename("hash.md") == "nebius_hash"
    assert resource_type_from_doc_filename("README.txt") is None
    assert resource_type_from_doc_filename("Bad-Name.md") is None


def test_resource_types_from_doc_filenames_filters_noise() -> None:
    names = [
        "compute_v1_instance.md",
        "vpc_v1_network.md",
        "notes.txt",
        "compute_v1_instance.md",
    ]
    assert resource_types_from_doc_filenames(names) == frozenset(
        {"nebius_compute_v1_instance", "nebius_vpc_v1_network"}
    )


def test_covered_and_uncovered_diff() -> None:
    estimators = {"nebius_compute_v1_instance": object()}
    free = {"nebius_vpc_v1_network", "nebius_hash"}
    covered = covered_resource_types(estimators, free)
    provider = {
        "nebius_compute_v1_instance",
        "nebius_vpc_v1_network",
        "nebius_hash",
        "nebius_brand_new_v1_thing",
    }
    assert uncovered_resource_types(provider, covered) == frozenset({"nebius_brand_new_v1_thing"})

    diff = diff_coverage(provider, estimators, free, source="test-source")
    assert diff.uncovered_types == frozenset({"nebius_brand_new_v1_thing"})
    assert diff.source == "test-source"
    assert "nebius_compute_v1_instance" in diff.covered_types


def test_unpriced_gaps_do_not_count_as_type_coverage() -> None:
    """Catalog pricing gaps are not Terraform resource types."""
    catalog = load_catalog(CATALOG)
    gaps = set(catalog.raw.get("unpriced_gaps", {}))
    covered = covered_resource_types(ESTIMATORS, catalog.free_types)
    # None of the disk-type gap keys are nebius_* resource types.
    assert not any(g.startswith("nebius_") for g in gaps)
    assert gaps.isdisjoint(covered)


def test_issue_title_round_trip() -> None:
    rtype = "nebius_observability_v1_dashboard"
    title = issue_title_for(rtype)
    assert title == f"{ISSUE_TITLE_PREFIX}{rtype}"
    assert resource_type_from_issue_title(title) == rtype
    assert resource_type_from_issue_title(f"feature: cover `{rtype}`") == rtype
    assert resource_type_from_issue_title("unrelated issue") is None
    with pytest.raises(ValueError):
        issue_title_for("aws_instance")


def test_build_issue_body_forbids_invented_prices() -> None:
    body = build_issue_body("nebius_foo_v1_bar")
    assert "nebius_foo_v1_bar" in body
    assert "Do **not** invent prices" in body
    assert "nebius-tf-cost-coverage" in body


def test_select_matching_issue_prefers_open() -> None:
    rtype = "nebius_foo_v1_bar"
    title = issue_title_for(rtype)
    issues = [
        {"number": 1, "state": "closed", "title": title},
        {"number": 9, "state": "open", "title": title},
        {"number": 3, "state": "open", "title": "other"},
    ]
    match = select_matching_issue(issues, rtype)
    assert match is not None
    assert match["number"] == 9


def test_open_or_update_dry_run_create_and_exists() -> None:
    rtype = "nebius_foo_v1_bar"
    created = open_or_update_coverage_issue(
        "o",
        "r",
        rtype,
        token="x",
        existing_issues=[],
        dry_run=True,
    )
    assert created["action"] == "create"
    assert created["dry_run"] is True

    existing = [
        {
            "number": 42,
            "state": "open",
            "title": issue_title_for(rtype),
            "html_url": "https://example.test/42",
        }
    ]
    exists = open_or_update_coverage_issue(
        "o",
        "r",
        rtype,
        token="x",
        existing_issues=existing,
        dry_run=True,
    )
    assert exists == {
        "action": "exists",
        "number": 42,
        "title": issue_title_for(rtype),
        "dry_run": True,
    }


def test_open_or_update_dry_run_reopen() -> None:
    rtype = "nebius_foo_v1_bar"
    existing = [
        {
            "number": 7,
            "state": "closed",
            "title": issue_title_for(rtype),
            "html_url": "https://example.test/7",
        }
    ]
    result = open_or_update_coverage_issue(
        "o",
        "r",
        rtype,
        token="x",
        existing_issues=existing,
        dry_run=True,
    )
    assert result["action"] == "reopen"
    assert result["number"] == 7


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakeOpener:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.requests: list[Any] = []

    def open(self, request: Any, timeout: float = 0) -> _FakeResponse:
        self.requests.append(request)
        return _FakeResponse(self.payload)


def test_fetch_provider_resource_types_parses_contents_api() -> None:
    opener = _FakeOpener(
        [
            {"name": "compute_v1_instance.md", "type": "file"},
            {"name": "vpc_v1_network.md", "type": "file"},
            {"name": "ignore_me", "type": "dir"},
        ]
    )
    types = fetch_provider_resource_types(opener=opener)
    assert types == frozenset({"nebius_compute_v1_instance", "nebius_vpc_v1_network"})
    assert opener.requests


def test_fetch_provider_resource_types_rejects_empty() -> None:
    opener = _FakeOpener([{"name": "readme.txt", "type": "file"}])
    with pytest.raises(RuntimeError, match="no resource markdown"):
        fetch_provider_resource_types(opener=opener)


def test_live_catalog_estimators_cover_known_provider_subset() -> None:
    """Sanity: current free+estimator sets are well-formed nebius_* names."""
    catalog = load_catalog(CATALOG)
    covered = covered_resource_types(ESTIMATORS, catalog.free_types)
    assert covered
    assert all(t.startswith("nebius_") for t in covered)
    assert set(ESTIMATORS).issubset(covered)
    assert set(catalog.free_types).issubset(covered)
    # JSON dump remains serializable for CI summaries.
    assert json.dumps(sorted(covered))
