#!/usr/bin/env python3
"""Summarize V66 EMX/HFSS postrun gate evidence for reporting."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_PLAN = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current" / "hfss_v66_calibration_plan_summary.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "v66_postrun_gate_evidence_summary_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan_path = Path(args.plan_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = _read_json(plan_path)
    variants = [_variant_record(item, args) for item in plan.get("variants") or [] if isinstance(item, dict)]
    selected = _select_best_passing_variant(variants)
    status_counts = _status_counts(variants)
    checks = _checks(plan_path, plan, variants, selected, args)
    overall_status, decision = _decision(plan, variants, selected, checks)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "plan_summary": str(plan_path),
        "out_dir": str(out_dir),
        "variant_count": len(variants),
        "variant_status_counts": status_counts,
        "selected_variant": selected or {},
        "variants": variants,
        "checks": checks,
        "acceptance_gate": {
            "required_touchstone": ".s8p",
            "expected_ports": 8,
            "frequency_grid": "5-60 GHz, 1.0 GHz step, 56 points",
            "target_marker_ghz": float(args.target_ghz),
            "max_percent_error": float(args.max_percent_error),
            "metrics": ["lp_nh", "ls_nh", "q", "k", "kw"],
        },
        "method_notes": [
            "This is a read-only evidence summary; it does not run HFSS, EMX, ADS, or the million-sample executor.",
            "PASS requires a V66 postrun PASS summary plus report artifacts for the selected variant.",
            "WAITING_FOR_HFSS means the EMX/payload side is ready but matching HFSS .s8p evidence is absent.",
            "Million-sample production remains locked unless the separate watcher/planner gates pass.",
        ],
    }

    summary_path = out_dir / "v66_postrun_gate_evidence_summary.json"
    report_path = out_dir / "V66_POSTRUN_GATE_EVIDENCE_SUMMARY.md"
    variants_csv = out_dir / "v66_postrun_gate_variant_summary.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary, variants_csv), encoding="utf-8")
    _write_variants_csv(variants_csv, variants)

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"variants_csv={variants_csv}")
    print(f"report={report_path}")
    return 0 if overall_status in {"PASS", "WAITING_FOR_HFSS"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-summary", default=str(DEFAULT_PLAN))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _variant_record(variant: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    name = str(variant.get("name") or "")
    postrun_out = Path(str(variant.get("postrun_out_dir") or "")).expanduser()
    summary_path = postrun_out / "s8p_hfss_postrun_validation_summary.json"
    summary = _read_json(summary_path)
    records = [record for record in summary.get("records") or [] if isinstance(record, dict)]
    first_record = records[0] if records else {}
    artifacts = _artifacts(first_record)
    marker_errors = _target_marker_errors(first_record)
    worst_metric = str(first_record.get("worst_metric") or "")
    worst_percent_error = _number(first_record.get("worst_percent_error"))
    return {
        "name": name,
        "postrun_out_dir": str(postrun_out),
        "summary_path": str(summary_path),
        "summary_exists": summary_path.is_file(),
        "overall_status": str(summary.get("overall_status") or ("MISSING" if not summary_path.is_file() else "")),
        "decision": str(summary.get("decision") or ""),
        "frequency_grid_mode": str(summary.get("frequency_grid_mode") or ""),
        "final_acceptance_candidate": bool(summary.get("final_acceptance_candidate")),
        "sample_count": int(summary.get("sample_count") or len(records)),
        "record_status": str(first_record.get("status") or ""),
        "evaluation": str(first_record.get("evaluation") or ""),
        "emx_s8p": str(first_record.get("emx_s8p") or ""),
        "hfss_s8p": str(first_record.get("hfss_s8p") or ""),
        "port_pairs": str(first_record.get("port_pairs") or ""),
        "target_marker_csv": str(first_record.get("target_marker_csv") or ""),
        "compare_summary": str(first_record.get("compare_summary") or ""),
        "ads_style_plot_summary": str(first_record.get("ads_style_plot_summary") or ""),
        "worst_metric": worst_metric,
        "worst_percent_error": worst_percent_error,
        "target_marker_errors": marker_errors,
        "artifacts": artifacts,
        "artifact_status": _artifact_status(artifacts),
        "passes_acceptance_gate": _passes_acceptance_gate(summary, first_record, marker_errors, artifacts, args),
    }


def _artifacts(record: dict[str, Any]) -> dict[str, str]:
    plot_summary_path = Path(str(record.get("ads_style_plot_summary") or "")).expanduser()
    plot_summary = _read_json(plot_summary_path)
    artifacts = plot_summary.get("artifacts") if isinstance(plot_summary.get("artifacts"), dict) else {}
    window_artifacts = plot_summary.get("window_named_artifacts") if isinstance(plot_summary.get("window_named_artifacts"), dict) else {}
    return {
        "emx_s8p": str(record.get("emx_s8p") or ""),
        "hfss_s8p": str(record.get("hfss_s8p") or ""),
        "target_marker_csv": str(record.get("target_marker_csv") or ""),
        "compare_summary": str(record.get("compare_summary") or ""),
        "ads_style_plot_summary": str(plot_summary_path) if str(record.get("ads_style_plot_summary") or "") else "",
        "emx_plot": str(artifacts.get("emx_common_plot") or window_artifacts.get("emx_common_plot") or ""),
        "hfss_plot": str(artifacts.get("hfss_common_plot") or window_artifacts.get("hfss_common_plot") or ""),
        "overlay_plot": str(artifacts.get("overlay_common_plot") or window_artifacts.get("overlay_common_plot") or ""),
        "percent_error_plot": str(artifacts.get("percent_error_common_plot") or window_artifacts.get("percent_error_common_plot") or ""),
        "metric_csv": str(artifacts.get("metric_csv") or window_artifacts.get("metric_csv") or ""),
    }


def _target_marker_errors(record: dict[str, Any]) -> dict[str, float | None]:
    marker_csv = Path(str(record.get("target_marker_csv") or "")).expanduser()
    if not marker_csv.is_file():
        return {}
    rows = _read_csv(marker_csv)
    out: dict[str, float | None] = {}
    for row in rows:
        metric = str(row.get("metric") or row.get("label") or "").strip()
        if metric:
            out[metric] = _number(row.get("percent_error"))
    return out


def _artifact_status(artifacts: dict[str, str]) -> dict[str, str]:
    return {
        key: ("PASS" if value and Path(value).expanduser().is_file() else "MISSING")
        for key, value in artifacts.items()
        if key not in {"percent_error_plot"}
    }


def _passes_acceptance_gate(
    summary: dict[str, Any],
    record: dict[str, Any],
    marker_errors: dict[str, float | None],
    artifacts: dict[str, str],
    args: argparse.Namespace,
) -> bool:
    if summary.get("overall_status") != "PASS":
        return False
    if summary.get("frequency_grid_mode") != "final_5_60_0p5_111":
        return False
    if not summary.get("final_acceptance_candidate"):
        return False
    if record.get("status") != "PASS":
        return False
    worst = _number(record.get("worst_percent_error"))
    if worst is None or worst > float(args.max_percent_error):
        return False
    required_metrics = {"lp_nh", "ls_nh", "q", "k", "kw"}
    if required_metrics - set(marker_errors):
        return False
    if any(value is None or float(value) > float(args.max_percent_error) for value in marker_errors.values()):
        return False
    required_artifacts = ["emx_s8p", "hfss_s8p", "target_marker_csv", "compare_summary", "ads_style_plot_summary", "emx_plot", "hfss_plot", "overlay_plot", "metric_csv"]
    return all(artifacts.get(key) and Path(artifacts[key]).expanduser().is_file() for key in required_artifacts)


def _select_best_passing_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    passing = [item for item in variants if item.get("passes_acceptance_gate")]
    if not passing:
        return None
    return sorted(passing, key=lambda item: (_sort_error(item.get("worst_percent_error")), str(item.get("name"))))[0]


def _checks(
    plan_path: Path,
    plan: dict[str, Any],
    variants: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    checks = [
        _check("V66 plan summary exists", plan_path.is_file(), str(plan_path)),
        _check("V66 plan status PASS", plan.get("overall_status") == "PASS", str(plan.get("overall_status"))),
        _check("V66 variants present", bool(variants), f"variants={len(variants)}"),
    ]
    for item in variants:
        name = item.get("name")
        status = item.get("overall_status")
        checks.append(_check(f"{name} postrun summary exists", bool(item.get("summary_exists")), item.get("summary_path")))
        if status == "PASS":
            checks.extend(
                [
                    _check(f"{name} final frequency grid", item.get("frequency_grid_mode") == "final_5_60_0p5_111", item.get("frequency_grid_mode")),
                    _check(f"{name} final acceptance candidate", bool(item.get("final_acceptance_candidate")), str(item.get("final_acceptance_candidate"))),
                    _check(f"{name} worst error <= {args.max_percent_error:g}%", _number(item.get("worst_percent_error")) is not None and float(item["worst_percent_error"]) <= float(args.max_percent_error), str(item.get("worst_percent_error"))),
                    _check(f"{name} report artifacts present", item.get("artifact_status") and all(value == "PASS" for value in item["artifact_status"].values()), str(item.get("artifact_status"))),
                ]
            )
    statuses = {str(item.get("overall_status") or "") for item in variants}
    if selected is not None:
        checks.append(_check("selected passing V66 variant exists", True, selected.get("name")))
    elif "WAITING_FOR_HFSS" in statuses or "MISSING" in statuses:
        checks.append(_check_status("WAITING", "selected passing V66 variant exists", "waiting for exported HFSS .s8p"))
    else:
        checks.append(_check("selected passing V66 variant exists", False, "no passing variant"))
    return checks


def _decision(
    plan: dict[str, Any],
    variants: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    checks: list[dict[str, str]],
) -> tuple[str, str]:
    if any(item["status"] == "FAIL" for item in checks[:3]):
        return "FAIL", "FIX_V66_POSTRUN_EVIDENCE_INPUTS"
    if selected is not None:
        return "PASS", "V66_EMX_HFSS_GATE_EVIDENCE_READY"
    statuses = {str(item.get("overall_status") or "") for item in variants}
    if "WAITING_FOR_HFSS" in statuses or "MISSING" in statuses:
        return "WAITING_FOR_HFSS", "WAIT_FOR_V66_EXPORTED_HFSS_S8P"
    if statuses and statuses <= {"FAIL"}:
        return "FAIL", "ALL_V66_VARIANTS_FAILED_EMX_HFSS_GATE"
    return "WAITING_FOR_HFSS", "WAIT_FOR_V66_POSTRUN_GATE"


def _status_counts(variants: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in variants:
        status = str(item.get("overall_status") or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _write_variants_csv(path: Path, variants: list[dict[str, Any]]) -> None:
    fields = [
        "name",
        "overall_status",
        "decision",
        "evaluation",
        "frequency_grid_mode",
        "passes_acceptance_gate",
        "worst_metric",
        "worst_percent_error",
        "emx_s8p",
        "hfss_s8p",
        "target_marker_csv",
        "compare_summary",
        "ads_style_plot_summary",
        "port_pairs",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in variants:
            writer.writerow({field: item.get(field, "") for field in fields})


def _render_report(summary: dict[str, Any], variants_csv: Path) -> str:
    selected = summary.get("selected_variant") or {}
    lines = [
        "# V66 Postrun Gate Evidence Summary",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Variant status counts: `{summary['variant_status_counts']}`",
        f"- Variants CSV: `{variants_csv}`",
        "",
        "## Gate",
        "",
        f"- Touchstone: `{summary['acceptance_gate']['required_touchstone']}`",
        f"- Ports: `{summary['acceptance_gate']['expected_ports']}`",
        f"- Frequency: `{summary['acceptance_gate']['frequency_grid']}`",
        f"- Target marker: `{summary['acceptance_gate']['target_marker_ghz']}` GHz",
        f"- Max error: `{summary['acceptance_gate']['max_percent_error']}` %",
        f"- Metrics: `{', '.join(summary['acceptance_gate']['metrics'])}`",
        "",
        "## Selected Variant",
        "",
    ]
    if selected:
        lines.extend(
            [
                f"- Name: `{selected.get('name', '')}`",
                f"- Worst error: `{selected.get('worst_percent_error')}` %",
                f"- EMX S8P: `{selected.get('emx_s8p', '')}`",
                f"- HFSS S8P: `{selected.get('hfss_s8p', '')}`",
                f"- Target marker CSV: `{selected.get('target_marker_csv', '')}`",
                f"- EMX plot: `{(selected.get('artifacts') or {}).get('emx_plot', '')}`",
                f"- HFSS plot: `{(selected.get('artifacts') or {}).get('hfss_plot', '')}`",
                f"- Overlay plot: `{(selected.get('artifacts') or {}).get('overlay_plot', '')}`",
            ]
        )
    else:
        lines.append("- No V66 variant has passed the complete EMX/HFSS evidence gate yet.")
    lines.extend(["", "## Variants", ""])
    for item in summary["variants"]:
        worst = "" if item.get("worst_percent_error") is None else f"{float(item['worst_percent_error']):.4g}%"
        lines.append(
            f"- `{item['name']}`: `{item['overall_status']}` / `{item['decision']}` / worst `{worst}` / HFSS `{item.get('hfss_s8p', '')}`"
        )
    lines.extend(["", "## Checks", ""])
    lines.extend(f"- {item['status']}: {item['name']} - {item['detail']}" for item in summary["checks"])
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {item}" for item in summary["method_notes"])
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sort_error(value: Any) -> float:
    number = _number(value)
    return float("inf") if number is None else number


def _check(name: str, passed: bool, detail: Any) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": str(detail)}


def _check_status(status: str, name: str, detail: Any) -> dict[str, str]:
    return {"status": status, "name": name, "detail": str(detail)}


if __name__ == "__main__":
    raise SystemExit(main())
