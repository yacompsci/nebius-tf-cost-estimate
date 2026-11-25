"""Command-line entrypoint for nebius-tf-cost."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TextIO

from nebius_tf_cost import __version__
from nebius_tf_cost.budget import evaluate_budget
from nebius_tf_cost.catalog import load_catalog
from nebius_tf_cost.config import EstimateConfig, PlannedUsage
from nebius_tf_cost.errors import EstimateError, ExitCode
from nebius_tf_cost.estimate import EstimateResult, run_estimate
from nebius_tf_cost.terraform import detect_and_load
from nebius_tf_cost.usage import load_usage, parse_usage_cli_overrides


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nebius-tf-cost",
        description=(
            "Estimate Nebius AI Cloud costs from Terraform plan or state JSON. "
            "Uses a vendored public price catalog (no Nebius API credentials required)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  nebius-tf-cost estimate --plan plan.json --period month
  nebius-tf-cost estimate --plan plan.json --budget 500 --on-exceed fail
  nebius-tf-cost estimate --state state.json --usage-file usage.yaml --period hour
  nebius-tf-cost estimate --plan plan.json --usage-metric stored_gib=100 --currency USD

exit codes:
  0  success (and budget OK, or warn-only exceed)
  2  budget exceeded with --on-exceed fail
  64 usage / flag error
  65 bad plan/state/catalog/usage data
  70 internal error
""".rstrip(),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    est = sub.add_parser("estimate", help="Estimate cost from plan or state JSON")
    src = est.add_mutually_exclusive_group(required=True)
    src.add_argument("--plan", type=Path, help="Path to terraform show -json plan output")
    src.add_argument("--state", type=Path, help="Path to terraform show -json state output")
    src.add_argument("--input", type=Path, help="Auto-detect plan or state JSON")

    est.add_argument(
        "--period",
        choices=["hour", "month", "year"],
        default="month",
        help="Billing period for totals (default: month; month=730h per Nebius docs)",
    )
    est.add_argument(
        "--currency",
        choices=["USD", "ILS"],
        default="USD",
        help="Currency for catalog rates (default: USD)",
    )
    est.add_argument("--budget", type=float, default=None, help="Budget amount in --currency")
    est.add_argument(
        "--on-exceed",
        choices=["warn", "fail"],
        default="warn",
        help="When estimate exceeds budget: warn (exit 0) or fail (exit 2)",
    )
    est.add_argument(
        "--budget-compare",
        choices=["after", "delta", "abs_delta"],
        default="after",
        help="What to compare to budget: after total, plan delta, or abs(delta)",
    )
    est.add_argument(
        "--usage-file",
        type=Path,
        help="YAML/JSON planned usage for usage-based resources",
    )
    est.add_argument(
        "--usage-metric",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Default usage metric override (repeatable), e.g. stored_gib=100",
    )
    est.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="Path to prices.json (default: bundled data/prices.json)",
    )
    est.add_argument(
        "--mode",
        choices=["auto", "plan", "state", "total"],
        default="auto",
        help="auto: plan=>delta+after, state=>total; total forces after-only",
    )
    est.add_argument(
        "--preemptible-policy",
        choices=["on_demand", "floor", "reject"],
        default="on_demand",
        help="How to price preemptible/spot VMs (default: on_demand)",
    )
    est.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON report")
    est.add_argument(
        "--include-zero",
        action="store_true",
        help="Include zero-cost line items in text output",
    )
    return parser


def _merge_usage(file_usage: PlannedUsage, overrides: dict[str, float]) -> PlannedUsage:
    defaults = dict(file_usage.defaults)
    defaults.update(overrides)
    return PlannedUsage(
        by_address=file_usage.by_address,
        by_name=file_usage.by_name,
        defaults=defaults,
    )


def _resolve_mode(cfg_mode: str, doc_kind: str) -> str:
    if cfg_mode != "auto":
        return cfg_mode
    if doc_kind == "plan":
        return "plan"
    return "total"


def format_text(result: EstimateResult, include_zero: bool = False) -> str:
    lines: list[str] = []
    lines.append(f"Nebius TF cost estimate ({result.document_kind})")
    lines.append(f"Catalog: {result.catalog_path}")
    lines.append(f"Period:  {result.period}   Currency: {result.currency}")
    lines.append("")
    lines.append(f"Before:  {result.before_total:.6f} {result.currency}")
    lines.append(f"After:   {result.after_total:.6f} {result.currency}")
    lines.append(f"Delta:   {result.delta:.6f} {result.currency}")
    lines.append("")
    lines.append("Line items (after):")
    for item in result.after_items:
        if not include_zero and item.amount == 0 and not item.unknown:
            continue
        flag = " ?" if item.unknown else ""
        lines.append(f"  {item.amount:12.6f}  {item.address}{flag}  ({item.details})")
    if result.before_items:
        lines.append("")
        lines.append("Line items (before):")
        for item in result.before_items:
            if not include_zero and item.amount == 0 and not item.unknown:
                continue
            flag = " ?" if item.unknown else ""
            lines.append(f"  {item.amount:12.6f}  {item.address}{flag}  ({item.details})")
    if result.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in result.warnings:
            lines.append(f"  - {w}")
    if result.unknown_resources:
        lines.append("")
        lines.append("Unknown / unpriced resources:")
        for u in result.unknown_resources:
            lines.append(f"  - {u}")
    return "\n".join(lines) + "\n"


def result_to_dict(result: EstimateResult) -> dict:
    return {
        "period": result.period,
        "currency": result.currency,
        "before_total": result.before_total,
        "after_total": result.after_total,
        "delta": result.delta,
        "catalog_path": result.catalog_path,
        "document_kind": result.document_kind,
        "unknown_resources": result.unknown_resources,
        "warnings": result.warnings,
        "before_items": [asdict(i) for i in result.before_items],
        "after_items": [asdict(i) for i in result.after_items],
    }


def cmd_estimate(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    input_path: Path
    if args.plan is not None:
        input_path = args.plan
    elif args.state is not None:
        input_path = args.state
    else:
        input_path = args.input

    cfg = EstimateConfig(
        input_path=input_path,
        period=args.period,
        currency=args.currency,
        budget=args.budget,
        on_exceed=args.on_exceed,
        usage_path=args.usage_file,
        catalog_path=args.catalog,
        mode=args.mode,
        json_output=args.json_output,
        include_zero=args.include_zero,
        preemptible_policy=args.preemptible_policy,
    )
    cfg.validate()

    catalog = load_catalog(cfg.catalog_path)
    doc = detect_and_load(cfg.input_path)
    usage = _merge_usage(load_usage(cfg.usage_path), parse_usage_cli_overrides(args.usage_metric))

    # Apply resolved mode onto a new config
    resolved_mode = _resolve_mode(cfg.mode, doc.kind)
    if args.plan is not None and resolved_mode == "auto":
        resolved_mode = "plan"
    if args.state is not None and cfg.mode == "auto":
        resolved_mode = "total"

    from dataclasses import replace

    cfg = replace(cfg, mode=resolved_mode)  # type: ignore[arg-type]

    result = run_estimate(doc, catalog, cfg, usage)
    verdict = evaluate_budget(result, cfg.budget, cfg.on_exceed, compare_mode=args.budget_compare)

    if cfg.json_output:
        payload = result_to_dict(result)
        payload["budget"] = None if verdict is None else asdict(verdict)
        # exit_code is IntEnum — make JSON-friendly
        if payload["budget"] is not None:
            payload["budget"]["exit_code"] = int(verdict.exit_code) if verdict else 0
        json.dump(payload, out, indent=2, sort_keys=True)
        out.write("\n")
    else:
        out.write(format_text(result, include_zero=cfg.include_zero))
        if verdict is not None:
            out.write("\n" + verdict.message + "\n")

    if verdict is not None and verdict.exceeded and cfg.on_exceed == "warn" and not cfg.json_output:
        err.write(f"warning: {verdict.message}\n")

    if verdict is not None:
        return int(verdict.exit_code)
    return int(ExitCode.OK)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "estimate":
            return cmd_estimate(args, sys.stdout, sys.stderr)
        parser.error(f"unknown command {args.command}")
        return int(ExitCode.USAGE_ERROR)
    except EstimateError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return int(exc.exit_code)
    except BrokenPipeError:
        return int(ExitCode.OK)
    except Exception as exc:  # noqa: BLE001 — top-level boundary
        print(f"internal error: {exc}", file=sys.stderr)
        return int(ExitCode.INTERNAL)


if __name__ == "__main__":
    sys.exit(main())
