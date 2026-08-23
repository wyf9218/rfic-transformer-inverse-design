#!/usr/bin/env python3
"""Verify that a Zin balanced-acquisition plan is not concentrated in one bin."""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan_dir = Path(args.plan_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else plan_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    targets_path = plan_dir / "zin_balanced_acquisition_targets.csv"
    summary_path = plan_dir / "zin_balanced_acquisition_plan_summary.json"
    targets = _read_targets(targets_path)
    plan_summary = _read_json(summary_path)
    metrics = _target_metrics(targets)
    checks = _build_checks(args, plan_dir, targets_path, summary_path, targets, metrics, plan_summary)
    overall_status = "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL"
    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan_dir": str(plan_dir),
        "overall_status": overall_status,
        "decision": "ACCEPT_ZIN_BALANCED_ACQUISITION_PLAN" if overall_status == "PASS" else "REJECT_CONCENTRATED_ZIN_ACQUISITION_PLAN",
        "target_metrics": metrics,
        "dataset_source": {} if not plan_summary else plan_summary.get("dataset_source", {}),
        "checks": checks,
        "arguments": vars(args),
        "plan_summary_path": str(summary_path),
        "targets_csv": str(targets_path),
        "limitations": [
            "This verifier checks the acquisition plan, not the eventual EMX response labels.",
            "A PASS means the next target allocation is spread across enough sparse bins to be worth running.",
            "Final dataset acceptance still requires real regenerated Zin distributions and EMX/HFSS/ADS validation.",
        ],
    }
    out_json = out_dir / "zin_balanced_acquisition_plan_verification_summary.json"
    out_md = out_dir / "zin_balanced_acquisition_plan_verification_report.md"
    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    out_md.write_text(_render_report(result), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={result['decision']}")
    print(f"summary={out_json}")
    for check in checks:
        print(f"{check['status']:5s} {check['name']}: {check['detail']}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--expected-new-sample-count", type=int)
    parser.add_argument("--min-target-bins", type=int, default=1)
    parser.add_argument("--max-single-bin-fraction", type=float, default=1.0)
    parser.add_argument("--min-nonzero-target-fraction", type=float, default=0.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_targets(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _as_int(value: Any) -> int:
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        return 0
    return out if out > 0 else 0


def _target_metrics(targets: list[dict[str, Any]]) -> dict[str, Any]:
    allocations = [_as_int(row.get("recommended_new_samples")) for row in targets]
    total = int(sum(allocations))
    nonzero = [value for value in allocations if value > 0]
    max_value = max(nonzero) if nonzero else 0
    fractions = [value / total for value in nonzero] if total > 0 else []
    return {
        "target_row_count": len(targets),
        "nonzero_target_bin_count": len(nonzero),
        "recommended_new_sample_count": total,
        "max_single_bin_samples": int(max_value),
        "max_single_bin_fraction": float(max(fractions)) if fractions else None,
        "nonzero_target_fraction": float(len(nonzero) / len(targets)) if targets else None,
        "allocation_min": int(min(nonzero)) if nonzero else 0,
        "allocation_max": int(max_value),
        "allocation_mean": float(total / len(nonzero)) if nonzero else None,
    }


def _build_checks(
    args: argparse.Namespace,
    plan_dir: Path,
    targets_path: Path,
    summary_path: Path,
    targets: list[dict[str, Any]],
    metrics: dict[str, Any],
    plan_summary: dict[str, Any] | None,
) -> list[dict[str, str]]:
    checks = [
        _check("PASS" if plan_dir.exists() else "FAIL", "plan directory exists", str(plan_dir)),
        _check("PASS" if summary_path.exists() else "FAIL", "plan summary exists", str(summary_path)),
        _check("PASS" if targets_path.exists() else "FAIL", "targets CSV exists", str(targets_path)),
        _check("PASS" if targets else "FAIL", "targets rows", f"rows={len(targets)}"),
    ]
    if plan_summary:
        checks.append(_check("PASS" if plan_summary.get("overall_status") == "PASS" else "FAIL", "planner overall status", str(plan_summary.get("overall_status"))))
        dataset_source = plan_summary.get("dataset_source") or {}
        sha = str(dataset_source.get("sha256") or "")
        csv_rows = _as_int(dataset_source.get("csv_row_count"))
        ok_rows = _as_int(dataset_source.get("ok_row_count"))
        checks.append(
            _check(
                "PASS" if bool(dataset_source.get("exists")) and len(sha) == 64 and csv_rows > 0 and ok_rows > 0 else "FAIL",
                "dataset source traceability",
                f"exists={dataset_source.get('exists')}, sha256_len={len(sha)}, csv_rows={csv_rows}, ok_rows={ok_rows}",
            )
        )
    total = int(metrics["recommended_new_sample_count"])
    if args.expected_new_sample_count is not None:
        checks.append(
            _check(
                "PASS" if total == int(args.expected_new_sample_count) else "FAIL",
                "recommended sample count",
                f"actual={total}, expected={args.expected_new_sample_count}",
            )
        )
    checks.append(
        _check(
            "PASS" if int(metrics["nonzero_target_bin_count"]) >= int(args.min_target_bins) else "FAIL",
            "minimum nonzero target bins",
            f"actual={metrics['nonzero_target_bin_count']}, required={args.min_target_bins}",
        )
    )
    max_fraction = metrics["max_single_bin_fraction"]
    checks.append(
        _check(
            "PASS" if max_fraction is not None and float(max_fraction) <= float(args.max_single_bin_fraction) + 1e-12 else "FAIL",
            "maximum single-bin allocation fraction",
            f"actual={_fmt(max_fraction)}, allowed={args.max_single_bin_fraction}",
        )
    )
    nonzero_fraction = metrics["nonzero_target_fraction"]
    checks.append(
        _check(
            "PASS" if nonzero_fraction is not None and float(nonzero_fraction) >= float(args.min_nonzero_target_fraction) - 1e-12 else "FAIL",
            "minimum nonzero target row fraction",
            f"actual={_fmt(nonzero_fraction)}, required={args.min_nonzero_target_fraction}",
        )
    )
    return checks


def _check(status: str, name: str, detail: str) -> dict[str, str]:
    return {"status": status, "name": name, "detail": detail}


def _fmt(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, float) and math.isfinite(value):
        return f"{value:.6g}"
    return str(value)


def _render_report(result: dict[str, Any]) -> str:
    lines = [
        "# Zin Balanced Acquisition Plan Verification",
        "",
        f"- Overall status: **{result['overall_status']}**",
        f"- Decision: **{result['decision']}**",
        f"- Plan directory: `{result['plan_dir']}`",
        f"- Dataset CSV SHA256: `{result.get('dataset_source', {}).get('sha256', 'missing')}`",
        "",
        "## Target Metrics",
        "",
    ]
    for key, value in result["target_metrics"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Checks", "", "| Status | Check | Detail |", "| --- | --- | --- |"])
    for check in result["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in result["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
