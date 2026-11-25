"""Discover Nebius Terraform provider resource types vs tool coverage.

Primary inventory source (no secrets required):
  GitHub Contents API for the public provider docs tree
  ``nebius/terraform-provider-nebius`` → ``docs/resources/*.md``

Each docs file ``foo.md`` corresponds to Terraform type ``nebius_foo``.
Covered types are the union of ``estimate.ESTIMATORS`` keys and catalog
``free_resource_types``. Catalog ``unpriced_gaps`` / ``unsupported_priced_elsewhere``
are documentation of pricing gaps, not Terraform type coverage; they do not
mark a resource type as covered.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Public, unauthenticated inventory of provider-managed resource docs.
PROVIDER_DOCS_CONTENTS_URL = (
    "https://api.github.com/repos/nebius/terraform-provider-nebius/contents/docs/resources"
)
PROVIDER_DOCS_SOURCE = (
    "https://github.com/nebius/terraform-provider-nebius/tree/main/docs/resources"
)

ISSUE_TITLE_PREFIX = "feature: cover "
_TITLE_TYPE_RE = re.compile(
    r"^feature:\s+cover\s+`?(nebius_[a-z0-9_]+)`?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CoverageDiff:
    """Result of comparing provider inventory to local coverage sets."""

    provider_types: frozenset[str]
    covered_types: frozenset[str]
    uncovered_types: frozenset[str]
    source: str


def resource_type_from_doc_filename(filename: str) -> str | None:
    """Map ``compute_v1_instance.md`` → ``nebius_compute_v1_instance``."""
    name = filename.strip()
    if not name.endswith(".md"):
        return None
    stem = name[: -len(".md")]
    if not stem or not re.fullmatch(r"[a-z0-9_]+", stem):
        return None
    return f"nebius_{stem}"


def resource_types_from_doc_filenames(filenames: Iterable[str]) -> frozenset[str]:
    types: set[str] = set()
    for filename in filenames:
        rtype = resource_type_from_doc_filename(filename)
        if rtype is not None:
            types.add(rtype)
    return frozenset(types)


def covered_resource_types(
    estimators: Mapping[str, Any] | Iterable[str],
    free_resource_types: Iterable[str],
) -> frozenset[str]:
    """Types the tool already handles (priced estimators or free/control-plane)."""
    estimator_keys = (
        frozenset(estimators.keys()) if isinstance(estimators, Mapping) else frozenset(estimators)
    )
    return frozenset(estimator_keys) | frozenset(free_resource_types)


def uncovered_resource_types(
    provider_types: Iterable[str],
    covered_types: Iterable[str],
) -> frozenset[str]:
    return frozenset(provider_types) - frozenset(covered_types)


def diff_coverage(
    provider_types: Iterable[str],
    estimators: Mapping[str, Any] | Iterable[str],
    free_resource_types: Iterable[str],
    *,
    source: str = PROVIDER_DOCS_SOURCE,
) -> CoverageDiff:
    covered = covered_resource_types(estimators, free_resource_types)
    provider = frozenset(provider_types)
    return CoverageDiff(
        provider_types=provider,
        covered_types=covered,
        uncovered_types=uncovered_resource_types(provider, covered),
        source=source,
    )


def issue_title_for(resource_type: str) -> str:
    """Canonical GitHub issue title used for create + dedupe."""
    if not resource_type.startswith("nebius_"):
        raise ValueError(f"expected nebius_* resource type, got {resource_type!r}")
    return f"{ISSUE_TITLE_PREFIX}{resource_type}"


def resource_type_from_issue_title(title: str) -> str | None:
    match = _TITLE_TYPE_RE.match(title.strip())
    if not match:
        return None
    return match.group(1)


def fetch_provider_resource_types(
    *,
    url: str = PROVIDER_DOCS_CONTENTS_URL,
    timeout: float = 30.0,
    opener: Any = None,
) -> frozenset[str]:
    """Fetch resource type names from the public provider docs Contents API."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "nebius-tf-cost-provider-coverage",
        },
        method="GET",
    )
    open_url = opener.open if opener is not None else urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"failed to fetch provider docs inventory ({exc.code}): {body[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"failed to fetch provider docs inventory: {exc}") from exc

    if not isinstance(payload, list):
        raise RuntimeError(
            f"provider docs Contents API returned non-list JSON; got {type(payload).__name__}"
        )

    filenames: list[str] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in (None, "file"):
            continue
        name = entry.get("name")
        if isinstance(name, str):
            filenames.append(name)

    types = resource_types_from_doc_filenames(filenames)
    if not types:
        raise RuntimeError("provider docs inventory contained no resource markdown files")
    return types


def build_issue_body(resource_type: str, *, source: str = PROVIDER_DOCS_SOURCE) -> str:
    """Feature-request body for an uncovered type (no prices invented)."""
    docs_slug = resource_type.removeprefix("nebius_")
    return "\n".join(
        [
            "<!-- nebius-tf-cost-coverage -->",
            "",
            f"## Coverage request: `{resource_type}`",
            "",
            "The Nebius Terraform provider documents this resource type, but "
            "`nebius-tf-cost` has neither a priced estimator in "
            "`estimate.ESTIMATORS` nor an entry in catalog "
            "`free_resource_types`.",
            "",
            "### Requested work",
            "",
            "- Add a cost estimator **or** classify the type as free/control-plane "
            "when public docs support that.",
            "- Do **not** invent prices. If public list prices are missing, document "
            "the gap (unknown/zero + warning) rather than guessing rates.",
            "",
            "### Inventory source",
            "",
            f"- Provider docs tree: {source}",
            f"- Resource doc: "
            f"https://registry.terraform.io/providers/nebius/nebius/latest/docs/"
            f"resources/{docs_slug}",
            "",
            "Opened by the scheduled `discover-uncovered-types` workflow.",
            "",
        ]
    )


def select_matching_issue(
    issues: Sequence[Mapping[str, Any]],
    resource_type: str,
) -> Mapping[str, Any] | None:
    """Pick an existing issue for ``resource_type`` (prefer open, then newest)."""
    wanted_title = issue_title_for(resource_type)
    matches: list[Mapping[str, Any]] = []
    for issue in issues:
        title = str(issue.get("title") or "")
        title_match = (
            title == wanted_title
            or resource_type_from_issue_title(title) == resource_type
            or (resource_type in title and ISSUE_TITLE_PREFIX.rstrip() in title.lower())
        )
        if title_match:
            matches.append(issue)
    if not matches:
        return None

    def sort_key(issue: Mapping[str, Any]) -> tuple[int, str]:
        state = 0 if issue.get("state") == "open" else 1
        # Prefer higher numbers (newer issues) when dates missing.
        number = int(issue.get("number") or 0)
        return (state, f"{-number:010d}")

    return sorted(matches, key=sort_key)[0]


def github_api_request(
    method: str,
    url: str,
    *,
    token: str,
    body: Mapping[str, Any] | None = None,
    timeout: float = 30.0,
    opener: Any = None,
) -> Any:
    """Minimal GitHub REST helper using stdlib only."""
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "nebius-tf-cost-provider-coverage",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    open_url = opener.open if opener is not None else urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API {method} {url} failed ({exc.code}): {err_body[:800]}"
        ) from exc


def ensure_label(
    owner: str,
    repo: str,
    *,
    token: str,
    name: str = "feature",
    color: str = "0E8A16",
    description: str = "Feature request / coverage expansion",
    opener: Any = None,
) -> None:
    """Create the label when missing (idempotent)."""
    encoded = urllib.parse.quote(name)
    get_url = f"https://api.github.com/repos/{owner}/{repo}/labels/{encoded}"
    try:
        github_api_request("GET", get_url, token=token, opener=opener)
        return
    except RuntimeError as exc:
        if "(404)" not in str(exc):
            raise
    create_url = f"https://api.github.com/repos/{owner}/{repo}/labels"
    github_api_request(
        "POST",
        create_url,
        token=token,
        body={"name": name, "color": color, "description": description},
        opener=opener,
    )


def list_coverage_issues(
    owner: str,
    repo: str,
    *,
    token: str,
    opener: Any = None,
) -> list[dict[str, Any]]:
    """List issues whose titles follow the coverage feature convention."""
    issues: list[dict[str, Any]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {
                "state": "all",
                "per_page": "100",
                "page": str(page),
            }
        )
        url = f"https://api.github.com/repos/{owner}/{repo}/issues?{query}"
        batch = github_api_request("GET", url, token=token, opener=opener)
        if not isinstance(batch, list) or not batch:
            break
        for item in batch:
            if not isinstance(item, dict):
                continue
            # Skip pull requests (they appear in the issues API).
            if "pull_request" in item:
                continue
            title = str(item.get("title") or "")
            if resource_type_from_issue_title(title) or title.startswith(ISSUE_TITLE_PREFIX):
                issues.append(item)
        if len(batch) < 100:
            break
        page += 1
        if page > 20:
            break
    return issues


def open_or_update_coverage_issue(
    owner: str,
    repo: str,
    resource_type: str,
    *,
    token: str,
    existing_issues: Sequence[Mapping[str, Any]],
    source: str = PROVIDER_DOCS_SOURCE,
    dry_run: bool = False,
    opener: Any = None,
) -> dict[str, Any]:
    """Create or refresh a feature issue for ``resource_type`` (idempotent)."""
    title = issue_title_for(resource_type)
    body = build_issue_body(resource_type, source=source)
    match = select_matching_issue(existing_issues, resource_type)

    if match is not None and match.get("state") == "open":
        number = int(match["number"])
        if dry_run:
            return {"action": "exists", "number": number, "title": title, "dry_run": True}
        # Refresh body so the issue stays the canonical coverage tracker.
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
        updated = github_api_request(
            "PATCH",
            url,
            token=token,
            body={"title": title, "body": body, "labels": ["feature"]},
            opener=opener,
        )
        return {
            "action": "updated",
            "number": number,
            "title": title,
            "url": (updated or {}).get("html_url") or match.get("html_url"),
        }

    if match is not None and match.get("state") == "closed":
        number = int(match["number"])
        if dry_run:
            return {"action": "reopen", "number": number, "title": title, "dry_run": True}
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
        updated = github_api_request(
            "PATCH",
            url,
            token=token,
            body={
                "title": title,
                "body": body,
                "state": "open",
                "labels": ["feature"],
            },
            opener=opener,
        )
        return {
            "action": "reopened",
            "number": number,
            "title": title,
            "url": (updated or {}).get("html_url") or match.get("html_url"),
        }

    if dry_run:
        return {"action": "create", "title": title, "dry_run": True}

    created = github_api_request(
        "POST",
        f"https://api.github.com/repos/{owner}/{repo}/issues",
        token=token,
        body={"title": title, "body": body, "labels": ["feature"]},
        opener=opener,
    )
    if not isinstance(created, dict):
        raise RuntimeError(f"unexpected create issue response for {resource_type}")
    return {
        "action": "created",
        "number": created.get("number"),
        "title": title,
        "url": created.get("html_url"),
    }
