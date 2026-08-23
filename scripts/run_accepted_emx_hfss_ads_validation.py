#!/usr/bin/env python3
"""Run the local accepted-EMX to HFSS/ADS validation workflow.

This script starts only after `verify_target_emx_postrun_package.py` has
accepted a local EMX .s4p. It then audits the HFSS Touchstone file, runs the
strict EMX-vs-HFSS/ADS 5% comparison, and writes ADS-style Lp/Ls/Q/K figures.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_OUT_DIR = Path(
    "/home/researcher/Documents/模拟变压器AI反向建模/hfss_validation/final500_ec6698dfc575950b/accepted_emx_hfss_ads_validation_20260613"
)
MIN_ACCEPTED_FIGURE_WIDTH = 640
MIN_ACCEPTED_FIGURE_HEIGHT = 360
MIN_ACCEPTED_FIGURE_BYTES = 2048
MIN_ACCEPTED_FIGURE_COLOR_DELTA = 2


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx-import-summary", required=True, help="Summary from verify_target_emx_postrun_package.py")
    parser.add_argument("--emx-s4p", help="Accepted local EMX .s4p; kept for old 4-port flows")
    parser.add_argument("--hfss-s4p", help="HFSS/ADS .s4p or metric CSV; kept for old 4-port flows")
    parser.add_argument("--emx-touchstone", help="Accepted local EMX Touchstone file; use this for .s8p flows")
    parser.add_argument("--hfss-touchstone", help="HFSS Touchstone file; use this for .s8p flows")
    parser.add_argument(
        "--hfss-geometry-summary",
        help="Summary from audit_hfss_model_geometry_assets.py for the HFSS model used to export --hfss-s4p",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--emx-port-pairs", default="1,2:3,4")
    parser.add_argument("--hfss-port-pairs", default="1,2:3,4")
    parser.add_argument("--compare-start-ghz", type=float, default=5.0)
    parser.add_argument("--compare-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument(
        "--ground-unused-ports",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Short Touchstone ports outside the selected differential pair to ground before extracting Lp/Ls/Q/K. "
            "Use this only when the ADS validation setup explicitly shorts the unused S8P power-line ports; "
            "the default keeps them open, matching the current EMX physical-feature labels."
        ),
    )
    parser.add_argument("--hfss-expected-ports", type=int, help="Expected HFSS Touchstone port count; inferred from .sNp suffix when omitted")
    parser.add_argument("--min-target-abs-k", type=float, default=0.05)
    parser.add_argument("--min-window-abs-k", type=float, default=0.05)
    parser.add_argument("--positive-window-start-ghz", type=float, default=5.0)
    parser.add_argument("--positive-window-stop-ghz", type=float, default=30.0)
    parser.add_argument("--shape-window-start-ghz", type=float, default=5.0)
    parser.add_argument("--shape-window-stop-ghz", type=float, default=30.0)
    parser.add_argument("--max-shape-spike-ratio", type=float, default=4.0)
    parser.add_argument("--max-shape-relative-step", type=float, default=0.25)
    parser.add_argument("--compare-script", default=str(Path(__file__).resolve().with_name("compare_emx_hfss_ads.py")))
    parser.add_argument("--touchstone-audit-script", default=str(Path(__file__).resolve().with_name("audit_touchstone_transformer.py")))
    parser.add_argument(
        "--skip-hfss-touchstone-audit",
        action="store_true",
        help="For metric-CSV dry runs only. Do not use for final HFSS .s4p acceptance.",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def _required_path_arg(raw: str | None, flag_name: str) -> Path:
    if not raw:
        raise ValueError(f"{flag_name} is required")
    return Path(raw).expanduser().resolve()


def _expected_ports_for_touchstone(path: Path, explicit: int | None) -> int:
    if explicit is not None:
        return int(explicit)
    suffix = path.suffix.lower()
    if suffix.startswith(".s") and suffix.endswith("p"):
        text = suffix[2:-1]
        if text.isdigit():
            return int(text)
    return 4


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    emx_import_summary_path = Path(args.emx_import_summary).expanduser().resolve()
    emx_path = _required_path_arg(args.emx_touchstone or args.emx_s4p, "--emx-touchstone/--emx-s4p")
    hfss_path = _required_path_arg(args.hfss_touchstone or args.hfss_s4p, "--hfss-touchstone/--hfss-s4p")
    hfss_geometry_summary_path = Path(args.hfss_geometry_summary).expanduser().resolve() if args.hfss_geometry_summary else None
    compare_out_dir = out_dir / "emx_hfss_ads_compare"
    hfss_audit_dir = out_dir / "hfss_touchstone_physical_gate"
    figures_dir = out_dir / "ads_style_core_metric_figures"
    target_marker_dir = out_dir / "ads_style_target_marker_values"
    figures_dir.mkdir(parents=True, exist_ok=True)

    checks: list[Check] = []
    errors: list[str] = []
    import_summary: dict[str, Any] = {}
    hfss_audit_summary: dict[str, Any] = {}
    compare_summary: dict[str, Any] = {}
    command_log: list[dict[str, Any]] = []
    figure_paths: dict[str, str] = {}
    target_marker_paths: dict[str, str] = {}
    figure_records: dict[str, dict[str, Any]] = {}
    target_marker_records: dict[str, dict[str, Any]] = {}
    formula_note_path = compare_out_dir / "ads_python_formula_crosscheck.md"

    try:
        import_summary = _read_json(emx_import_summary_path)
        checks.extend(_import_summary_checks(import_summary, emx_path))
        if _has_failed(checks):
            checks.append(
                Check(
                    "FAIL",
                    "EMX-first stop before HFSS comparison",
                    "accepted EMX import/EMX-first checks failed, so HFSS audit, compare, and figures were not generated",
                )
            )
        else:
            checks.extend(_hfss_geometry_summary_checks(hfss_geometry_summary_path))

            if not args.skip_hfss_touchstone_audit:
                completed = _run_hfss_audit(hfss_path, hfss_audit_dir, args)
                command_log.append(_command_record("hfss_touchstone_audit", completed))
                hfss_audit_summary = _read_json(hfss_audit_dir / "touchstone_transformer_audit_summary.json")
                checks.extend(_hfss_audit_checks(hfss_audit_summary))
            else:
                checks.append(
                    Check(
                        "FAIL",
                        "HFSS Touchstone physical gate",
                        "skipped by --skip-hfss-touchstone-audit; generated curves are review-only and cannot be used for final HFSS .s4p acceptance",
                    )
                )

            completed = _run_compare(emx_path, hfss_path, compare_out_dir, args)
            command_log.append(_command_record("emx_hfss_ads_compare", completed))
            compare_summary = _read_json(compare_out_dir / "emx_hfss_ads_comparison_summary.json")
            checks.extend(_compare_checks(compare_summary, args, emx_path, hfss_path))
            checks.extend(_formula_note_checks(formula_note_path))
            plot_data_checks = _plot_data_checks(compare_summary, args)
            checks.extend(plot_data_checks)
            if _has_failed(plot_data_checks):
                checks.append(
                    Check(
                        "FAIL",
                        "ADS-style core metric figures",
                        "not generated because ADS-style plot_data integrity failed",
                    )
                )
                checks.append(
                    Check(
                        "FAIL",
                        "ADS-style target marker table",
                        "not generated because ADS-style plot_data integrity failed",
                    )
                )
            else:
                figure_paths = _write_ads_style_core_figures(compare_summary, figures_dir, args)
                checks.extend(_figure_checks(figure_paths))
                figure_records = _artifact_records(figure_paths)
                checks.extend(
                    _artifact_manifest_checks(
                        "ADS-style core metric figure manifest",
                        figure_paths,
                        figure_records,
                    )
                )
                target_marker_paths = _write_ads_style_target_marker_tables(compare_summary, target_marker_dir, args)
                checks.extend(_target_marker_checks(target_marker_paths, args))
                target_marker_records = _artifact_records(target_marker_paths)
                checks.extend(
                    _artifact_manifest_checks(
                        "ADS-style target marker manifest",
                        target_marker_paths,
                        target_marker_records,
                    )
                )
    except Exception as exc:  # noqa: BLE001 - preserve exact failure.
        errors.append(f"{type(exc).__name__}: {exc}")
        checks.append(Check("FAIL", "accepted EMX/HFSS validation execution", errors[-1]))

    status_counts = _status_counts(checks)
    overall_status = "FAIL" if status_counts.get("FAIL", 0) else "PASS"
    decision = _decision(overall_status, status_counts)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "emx_import_summary": str(emx_import_summary_path),
        "hfss_geometry_summary": str(hfss_geometry_summary_path) if hfss_geometry_summary_path else None,
        "emx_touchstone": _file_record(emx_path),
        "hfss_touchstone": _file_record(hfss_path),
        "emx_s4p": _file_record(emx_path),
        "hfss_s4p": _file_record(hfss_path),
        "out_dir": str(out_dir),
        "compare_out_dir": str(compare_out_dir),
        "hfss_audit_dir": str(hfss_audit_dir) if not args.skip_hfss_touchstone_audit else None,
        "figure_paths": figure_paths,
        "figure_records": figure_records,
        "target_marker_paths": target_marker_paths,
        "target_marker_records": target_marker_records,
        "checks": [check.__dict__ for check in checks],
        "status_counts": status_counts,
        "command_log": command_log,
        "compare_summary": str(compare_out_dir / "emx_hfss_ads_comparison_summary.json"),
        "formula_note": str(formula_note_path),
        "hfss_audit_summary": str(hfss_audit_dir / "touchstone_transformer_audit_summary.json") if hfss_audit_summary else None,
        "arguments": {
            "emx_port_pairs": args.emx_port_pairs,
            "hfss_port_pairs": args.hfss_port_pairs,
            "compare_start_ghz": float(args.compare_start_ghz),
            "compare_stop_ghz": float(args.compare_stop_ghz),
            "expected_frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "expected_frequency_points": int(args.expected_frequency_points),
            "max_percent_error": float(args.max_percent_error),
            "target_ghz": float(args.target_ghz),
            "min_target_abs_k": float(args.min_target_abs_k),
            "min_window_abs_k": float(args.min_window_abs_k),
            "ground_unused_ports": bool(args.ground_unused_ports),
            "skip_hfss_touchstone_audit": bool(args.skip_hfss_touchstone_audit),
            "hfss_expected_ports": _expected_ports_for_touchstone(hfss_path, args.hfss_expected_ports),
        },
        "method_notes": [
            "This workflow does not create HFSS geometry or run EM solvers; it validates already-exported files.",
            "Final accepted HFSS validation also requires a PASS HFSS geometry asset audit for the model used to export the HFSS S4P, so the electrical curves remain traceable to inspectable model-view PNGs and STEP geometry.",
            f"Final accepted HFSS validation requires an accepted local EMX reference, a PASS HFSS Touchstone physical gate with non-near-zero coupling, strict matching {float(args.compare_start_ghz):g}-{float(args.compare_stop_ghz):g} GHz / {float(args.expected_frequency_step_ghz):g} GHz / {int(args.expected_frequency_points)}-point frequency grid, grounded unused S8P ports when applicable, and <={float(args.max_percent_error):g}% max error for K/Qp/Qs/Lp/Ls.",
            "The generated ADS-style figures are Python post-processing evidence from the same formulas used by the compare gate, not ADS GUI screenshots; the formula note is recorded as a required traceability artifact.",
            "The generated figure and target-marker files are recorded with bytes and SHA256 digests so report evidence can be checked for replacement or drift.",
            "The target-marker CSV/Markdown records the exact target-frequency K/Qp/Qs/Lp/Ls values used for report callouts; it is generated only after plot_data integrity passes.",
            "If --skip-hfss-touchstone-audit is used, outputs are review-only and the run is intentionally FAIL/DO_NOT_USE_HFSS_COMPARISON until a real HFSS .s4p passes the physical gate.",
        ],
        "errors": errors,
    }
    summary_path = out_dir / "accepted_emx_hfss_ads_validation_summary.json"
    report_path = out_dir / "accepted_emx_hfss_ads_validation_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary, compare_summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _import_summary_checks(summary: dict[str, Any], emx_path: Path) -> list[Check]:
    checks: list[Check] = []
    checks.append(_status_check("accepted EMX import status", summary.get("overall_status"), "PASS"))
    checks.append(_status_check("accepted EMX import decision", summary.get("decision"), "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS"))
    expected_sha = (
        summary.get("mars_emx_sha256")
        or (summary.get("emx_touchstone") or {}).get("sha256")
        or (summary.get("emx_s8p") or {}).get("sha256")
        or (summary.get("emx_s4p") or {}).get("sha256")
    )
    if not emx_path.is_file():
        checks.append(Check("FAIL", "accepted EMX local file", f"missing: {emx_path}"))
        return checks
    actual_sha = _sha256(emx_path)
    if expected_sha and actual_sha == expected_sha:
        checks.append(Check("PASS", "accepted EMX local SHA", actual_sha))
    else:
        checks.append(Check("FAIL", "accepted EMX local SHA", f"expected={expected_sha!r}, actual={actual_sha}"))
    checks.extend(_required_import_verifier_checks(summary, emx_path))
    return checks


def _required_import_verifier_checks(summary: dict[str, Any], emx_path: Path) -> list[Check]:
    by_name = {item.get("name"): item for item in summary.get("checks", [])}
    required_names = (
        "post-run validation artifacts",
        "post-run validation artifact content",
        "Touchstone physical gate frequency grid",
        "Touchstone physical gate coupling arguments",
        "Touchstone physical gate required physics checks",
        "Touchstone physical gate internal checks",
        "EMX-first gate frequency grid",
        "EMX-first gate internal checks",
        "local EMX S4P SHA",
    )
    failures: list[str] = []
    for name in required_names:
        status = by_name.get(name, {}).get("status")
        if status != "PASS":
            failures.append(f"{name}={status!r}")
    if failures:
        return [Check("FAIL", "accepted EMX import verifier evidence", "; ".join(failures))]
    return [
        Check("PASS", "accepted EMX import verifier evidence", f"{len(required_names)} verifier checks PASS"),
        _accepted_import_artifact_bundle_check(summary, emx_path),
        _accepted_import_core_metric_artifact_check(summary),
    ]


def _accepted_import_artifact_bundle_check(summary: dict[str, Any], emx_path: Path) -> Check:
    bundle = summary.get("accepted_emx_reference_bundle")
    if not isinstance(bundle, dict):
        return Check("FAIL", "accepted EMX import artifact bundle", "missing accepted_emx_reference_bundle")
    if bundle.get("status") != "READY_FOR_HFSS":
        return Check("FAIL", "accepted EMX import artifact bundle", f"status={bundle.get('status')!r}")
    touchstone_record = bundle.get("emx_touchstone") or bundle.get("emx_s8p") or bundle.get("emx_s4p")
    if not isinstance(touchstone_record, dict):
        return Check("FAIL", "accepted EMX import artifact bundle", "missing EMX Touchstone record")
    record_path_text = touchstone_record.get("path")
    if not record_path_text:
        return Check("FAIL", "accepted EMX import artifact bundle", "EMX Touchstone record missing path")
    record_path = Path(str(record_path_text)).expanduser()
    if record_path.resolve() != emx_path.resolve():
        return Check("FAIL", "accepted EMX import artifact bundle", f"bundle EMX path {record_path.resolve()} != CLI EMX path {emx_path.resolve()}")
    actual_sha = _sha256(emx_path)
    if touchstone_record.get("sha256") != actual_sha:
        return Check("FAIL", "accepted EMX import artifact bundle", f"bundle EMX SHA {touchstone_record.get('sha256')!r} != actual {actual_sha}")
    mars_sha = summary.get("mars_emx_sha256")
    if mars_sha and mars_sha != actual_sha:
        return Check("FAIL", "accepted EMX import artifact bundle", f"MARS EMX SHA {mars_sha!r} != actual {actual_sha}")
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, dict):
        return Check("FAIL", "accepted EMX import artifact bundle", "missing artifacts map")
    required_artifacts = (
        "touchstone_summary",
        "touchstone_metrics_csv",
        "touchstone_ads_equivalent_plot",
        "emx_first_summary",
        "emx_first_metrics_csv",
        "emx_first_ads_style_plot",
        "emx_first_core_plot",
        "port_pair_sensitivity_csv",
        "port_pair_sensitivity_plot",
    )
    failures: list[str] = []
    for name in required_artifacts:
        record = artifacts.get(name)
        if not isinstance(record, dict):
            failures.append(f"{name}: missing record")
            continue
        path_text = record.get("path")
        if not path_text:
            failures.append(f"{name}: missing path")
            continue
        artifact_path = Path(str(path_text)).expanduser()
        if not artifact_path.is_file():
            failures.append(f"{name}: missing file {artifact_path}")
        elif artifact_path.stat().st_size <= 0:
            failures.append(f"{name}: empty file {artifact_path}")
    if failures:
        return Check("FAIL", "accepted EMX import artifact bundle", "; ".join(failures[:6]))
    return Check("PASS", "accepted EMX import artifact bundle", f"{len(required_artifacts)} accepted EMX artifact paths exist and are non-empty")


def _accepted_import_core_metric_artifact_check(summary: dict[str, Any]) -> Check:
    validation_root_text = summary.get("validation_root")
    if not validation_root_text:
        return Check("FAIL", "accepted EMX import core metric artifact", "validation_root missing from import summary")
    core_plot = (
        Path(str(validation_root_text)).expanduser()
        / "emx_first_validation_gate_20260613"
        / "emx_first_validation_gate_core_metrics.png"
    )
    if not core_plot.is_file():
        return Check("FAIL", "accepted EMX import core metric artifact", f"missing: {core_plot}")
    failures = _png_figure_failures(core_plot, "emx_first_validation_gate_core_metrics.png")
    if failures:
        return Check("FAIL", "accepted EMX import core metric artifact", "; ".join(failures[:4]))
    return Check("PASS", "accepted EMX import core metric artifact", str(core_plot))


def _hfss_geometry_summary_checks(summary_path: Path | None) -> list[Check]:
    if summary_path is None:
        return [
            Check(
                "FAIL",
                "HFSS geometry asset audit evidence",
                "missing --hfss-geometry-summary from audit_hfss_model_geometry_assets.py",
            )
        ]
    if not summary_path.is_file():
        return [Check("FAIL", "HFSS geometry asset audit evidence", f"missing: {summary_path}")]
    try:
        summary = _read_json(summary_path)
    except Exception as exc:  # noqa: BLE001 - preserve exact evidence failure.
        return [Check("FAIL", "HFSS geometry asset audit evidence", f"{type(exc).__name__}: {exc}")]

    checks: list[Check] = []
    checks.append(_status_check("HFSS geometry asset audit status", summary.get("overall_status"), "PASS"))
    checks.append(
        _status_check(
            "HFSS geometry asset audit decision",
            summary.get("decision"),
            "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS",
        )
    )
    checks.append(_hfss_geometry_required_internal_checks(summary))
    checks.append(_hfss_geometry_artifact_paths_check(summary))
    if _has_failed(checks):
        return [Check("FAIL", "HFSS geometry asset audit evidence", f"summary={summary_path}"), *checks]
    return [Check("PASS", "HFSS geometry asset audit evidence", str(summary_path)), *checks]


def _hfss_geometry_required_internal_checks(summary: dict[str, Any]) -> Check:
    by_name = {item.get("name"): item for item in summary.get("checks", []) if isinstance(item, dict)}
    required = (
        "HFSS top-view PNG",
        "HFSS isometric-view PNG",
        "HFSS geometry-quality PNG",
        "HFSS STEP model",
    )
    failures = [
        f"{name}={(by_name.get(name) or {}).get('status')!r}"
        for name in required
        if (by_name.get(name) or {}).get("status") != "PASS"
    ]
    return Check(
        "PASS" if not failures else "FAIL",
        "HFSS geometry required asset checks",
        f"{len(required)} geometry asset checks PASS" if not failures else "; ".join(failures),
    )


def _hfss_geometry_artifact_paths_check(summary: dict[str, Any]) -> Check:
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, dict):
        return Check("FAIL", "HFSS geometry artifact paths", "missing artifacts map")
    required = ("top_png", "isometric_png", "quality_png", "step")
    failures: list[str] = []
    for name in required:
        raw_path = artifacts.get(name)
        if not raw_path:
            failures.append(f"{name}: missing path")
            continue
        path = Path(str(raw_path)).expanduser()
        if not path.is_file():
            failures.append(f"{name}: missing file {path}")
            continue
        if path.stat().st_size <= 0:
            failures.append(f"{name}: empty file {path}")
            continue
        if name.endswith("_png"):
            png_failures = _png_figure_failures(path, name)
            if png_failures:
                failures.append(f"{name}: {'; '.join(png_failures[:3])}")
    return Check(
        "PASS" if not failures else "FAIL",
        "HFSS geometry artifact paths",
        f"{len(required)} geometry artifact paths exist and are non-empty" if not failures else "; ".join(failures[:6]),
    )


def _run_hfss_audit(hfss_path: Path, out_dir: Path, args: argparse.Namespace) -> subprocess.CompletedProcess[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(Path(args.touchstone_audit_script).expanduser().resolve()),
        str(hfss_path),
        "--out-dir",
        str(out_dir),
        "--expected-ports",
        str(_expected_ports_for_touchstone(hfss_path, args.hfss_expected_ports)),
        "--expected-source-kind",
        "HFSS",
        "--port-pairs",
        args.hfss_port_pairs,
        "--required-sweep-start-ghz",
        f"{args.compare_start_ghz:g}",
        "--required-sweep-stop-ghz",
        f"{args.compare_stop_ghz:g}",
        "--target-frequency-ghz",
        f"{args.target_ghz:g}",
        "--target-frequency-tolerance-ghz",
        "0.05",
        "--min-target-inductance-nh",
        "0.05",
        "--min-target-q",
        "1.0",
        "--min-target-abs-k",
        f"{args.min_target_abs_k:g}",
        "--max-target-abs-k",
        "0.98",
        "--positive-window-start-ghz",
        f"{args.positive_window_start_ghz:g}",
        "--positive-window-stop-ghz",
        f"{args.positive_window_stop_ghz:g}",
        "--shape-window-start-ghz",
        f"{args.shape_window_start_ghz:g}",
        "--shape-window-stop-ghz",
        f"{args.shape_window_stop_ghz:g}",
        "--min-window-abs-k",
        f"{args.min_window_abs_k:g}",
        "--max-shape-spike-ratio",
        f"{args.max_shape_spike_ratio:g}",
        "--max-shape-relative-step",
        f"{args.max_shape_relative_step:g}",
    ]
    if args.ground_unused_ports:
        cmd.append("--ground-unused-ports")
    cmd.extend(["--plot", "--no-fail-exit"])
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _run_compare(emx_path: Path, hfss_path: Path, out_dir: Path, args: argparse.Namespace) -> subprocess.CompletedProcess[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(Path(args.compare_script).expanduser().resolve()),
        "--emx",
        str(emx_path),
        "--hfss",
        str(hfss_path),
        "--out-dir",
        str(out_dir),
        "--emx-port-pairs",
        args.emx_port_pairs,
        "--hfss-port-pairs",
        args.hfss_port_pairs,
        "--compare-start-ghz",
        f"{args.compare_start_ghz:g}",
        "--compare-stop-ghz",
        f"{args.compare_stop_ghz:g}",
        "--min-frequency-points",
        str(args.expected_frequency_points),
        "--expected-frequency-step-ghz",
        f"{args.expected_frequency_step_ghz:g}",
        "--expected-frequency-points",
        str(args.expected_frequency_points),
        "--frequency-tolerance-hz",
        f"{args.frequency_tolerance_hz:g}",
        "--require-matching-frequency-grid",
        "--max-percent-error",
        f"{args.max_percent_error:g}",
    ]
    if args.ground_unused_ports:
        cmd.append("--ground-unused-ports")
    cmd.extend(["--plot", "--no-fail-exit"])
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _hfss_audit_checks(summary: dict[str, Any]) -> list[Check]:
    checks = [_status_check("HFSS Touchstone physical gate status", summary.get("overall_status"), "PASS")]
    checks.extend(_required_hfss_touchstone_checks(summary))
    failed = [item for item in summary.get("checks", []) if item.get("status") == "FAIL"]
    if failed:
        checks.append(Check("FAIL", "HFSS Touchstone physical gate internal checks", f"failed={len(failed)}"))
    else:
        checks.append(Check("PASS", "HFSS Touchstone physical gate internal checks", f"{len(summary.get('checks', []))} checks without FAIL"))
    return checks


def _required_hfss_touchstone_checks(summary: dict[str, Any]) -> list[Check]:
    by_name = {item.get("name"): item for item in summary.get("checks", [])}
    required = (
        "source identity",
        "differential Z finiteness",
        "differential Z reciprocity",
        "differential Z positive-realness",
        "ADS-equivalent metric finiteness",
        "target-frequency transformer metrics",
        "positive metric window",
        "smooth transformer metric window",
    )
    failures = [f"{name}={(by_name.get(name) or {}).get('status')!r}" for name in required if (by_name.get(name) or {}).get("status") != "PASS"]
    return [
        Check(
            "PASS" if not failures else "FAIL",
            "HFSS Touchstone required differential/physics checks",
            f"{len(required)} required checks PASS" if not failures else "; ".join(failures),
        )
    ]


def _compare_checks(summary: dict[str, Any], args: argparse.Namespace, emx_path: Path, hfss_path: Path) -> list[Check]:
    checks = [_status_check("EMX-vs-HFSS compare status", summary.get("overall_status"), "PASS")]
    checks.extend(_compare_source_checks(summary, emx_path, hfss_path))
    checks.extend(_compare_criterion_checks(summary, args))
    freq = summary.get("frequency_window_hz", {})
    failures: list[str] = []
    expected_start = float(args.compare_start_ghz) * 1.0e9
    expected_stop = float(args.compare_stop_ghz) * 1.0e9
    if abs(float(freq.get("min", 0.0)) - expected_start) > float(args.frequency_tolerance_hz):
        failures.append(f"start={freq.get('min')!r}")
    if abs(float(freq.get("max", 0.0)) - expected_stop) > float(args.frequency_tolerance_hz):
        failures.append(f"stop={freq.get('max')!r}")
    if int(freq.get("count", -1)) != int(args.expected_frequency_points):
        failures.append(f"count={freq.get('count')!r}")
    checks.append(
        Check(
            "PASS" if not failures else "FAIL",
            "EMX-vs-HFSS compare frequency window",
            f"{float(args.compare_start_ghz):g}-{float(args.compare_stop_ghz):g} GHz / {int(args.expected_frequency_points)} points"
            if not failures
            else "; ".join(failures),
        )
    )
    checks.extend(_compare_grid_checks(summary))
    checks.extend(_compare_metric_checks(summary, args))
    return checks


def _compare_source_checks(summary: dict[str, Any], emx_path: Path, hfss_path: Path) -> list[Check]:
    expected_sources = {
        "emx_source": _normalized_path_text(emx_path),
        "hfss_ads_source": _normalized_path_text(hfss_path),
    }
    failures: list[str] = []
    for key, expected in expected_sources.items():
        actual = _normalized_path_text(summary.get(key))
        if actual != expected:
            failures.append(f"{key}_mismatch: expected={expected!r}, actual={actual!r}")
    if failures:
        return [Check("FAIL", "EMX-vs-HFSS compare source traceability", "; ".join(failures))]
    return [Check("PASS", "EMX-vs-HFSS compare source traceability", "summary sources match EMX/HFSS inputs")]


def _compare_criterion_checks(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    raw_value = (summary.get("criterion") or {}).get("max_percent_error")
    try:
        criterion_max = float(raw_value)
    except (TypeError, ValueError):
        return [Check("FAIL", "EMX-vs-HFSS compare criterion", f"criterion_max_percent_error={raw_value!r}")]
    if criterion_max > float(args.max_percent_error):
        return [
            Check(
                "FAIL",
                "EMX-vs-HFSS compare criterion",
                f"criterion_max_percent_error={criterion_max:g} exceeds allowed {float(args.max_percent_error):g}",
            )
        ]
    return [Check("PASS", "EMX-vs-HFSS compare criterion", f"criterion_max_percent_error={criterion_max:g}")]


def _compare_grid_checks(summary: dict[str, Any]) -> list[Check]:
    grid_checks = summary.get("frequency_grid_checks") or {}
    required = (
        "ADS no-extrapolation coverage",
        "expected frequency points",
        "expected frequency step",
        "matching HFSS/ADS frequency grid",
    )
    failures = [f"{name}={(grid_checks.get(name) or {}).get('status')!r}" for name in required if (grid_checks.get(name) or {}).get("status") != "PASS"]
    return [
        Check(
            "PASS" if not failures else "FAIL",
            "EMX-vs-HFSS compare frequency-grid checks",
            "required grid/no-extrapolation checks PASS" if not failures else "; ".join(failures),
        )
    ]


def _compare_metric_checks(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    metrics = summary.get("metrics") or {}
    required = ("k", "qp", "qs", "lp_nh", "ls_nh")
    failures: list[str] = []
    for name in required:
        item = metrics.get(name) or {}
        if item.get("status") != "PASS":
            failures.append(f"{name}_status={item.get('status')!r}")
            continue
        raw_error = item.get("max_percent_error")
        try:
            max_percent_error = float(raw_error)
        except (TypeError, ValueError):
            failures.append(f"metric_{name}_max_percent_error={raw_error!r}")
            continue
        if max_percent_error > float(args.max_percent_error):
            failures.append(f"metric_{name}_max_percent_error={max_percent_error:g}")
    return [
        Check(
            "PASS" if not failures else "FAIL",
            "EMX-vs-HFSS compare core metric errors",
            "K/Qp/Qs/Lp/Ls numeric max errors <= gate" if not failures else "; ".join(failures),
        )
    ]


def _normalized_path_text(raw_path: object) -> str:
    if raw_path is None:
        return ""
    try:
        return str(Path(str(raw_path)).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return str(raw_path)


def _formula_note_checks(path: Path) -> list[Check]:
    required_fragments = (
        "# ADS/Python Formula Cross-Check",
        "Touchstone 2.1",
        "port pairing must be recorded",
        "Touchstone reference impedance",
        "Z_diff = transpose(T) * Z_single * T",
        "ADS Data Display equation template",
        "Zp = Z11 - Z12 + Z22 - Z21",
        "Zs = Z33 - Z34 + Z44 - Z43",
        "Zm = Z31 - Z32 + Z42 - Z41",
        "Lp = imag(Zdiff[1,1]) / omega",
        "Ls = imag(Zdiff[2,2]) / omega",
        "M  = imag(Zdiff[2,1]) / omega",
        "K  = M / sqrt(abs(Lp * Ls))",
        "k  = M / sqrt(abs(Lp * Ls))",
        "percent_error = abs(HFSS_or_ADS - EMX)",
        "ADS no-extrapolation coverage",
    )
    if not path.is_file():
        return [Check("FAIL", "ADS/Python formula note", f"missing: {path}")]
    if path.stat().st_size <= 0:
        return [Check("FAIL", "ADS/Python formula note", f"empty: {path}")]
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if missing:
        return [Check("FAIL", "ADS/Python formula note", f"missing fragments={missing[:4]}")]
    return [Check("PASS", "ADS/Python formula note", str(path))]


def _write_ads_style_core_figures(summary: dict[str, Any], out_dir: Path, args: argparse.Namespace) -> dict[str, str]:
    import matplotlib.pyplot as plt

    plot_data = summary.get("plot_data", {})
    freq_ghz = np.asarray(plot_data.get("freq_hz", []), dtype=float) / 1.0e9
    emx = {key: np.asarray(value, dtype=float) for key, value in (plot_data.get("emx") or {}).items()}
    hfss = {key: np.asarray(value, dtype=float) for key, value in (plot_data.get("hfss_ads") or {}).items()}
    if freq_ghz.size == 0 or not emx or not hfss:
        raise ValueError("compare summary does not contain plot_data for ADS-style figures")
    paths = {
        "emx_ads_style_core_metrics": out_dir / "emx_ads_style_core_metrics.png",
        "hfss_ads_style_core_metrics": out_dir / "hfss_ads_style_core_metrics.png",
        "emx_vs_hfss_ads_style_core_overlay": out_dir / "emx_vs_hfss_ads_style_core_overlay.png",
    }
    _write_single_core_panel(paths["emx_ads_style_core_metrics"], "EMX accepted reference", freq_ghz, emx, args)
    _write_single_core_panel(paths["hfss_ads_style_core_metrics"], "HFSS/ADS export", freq_ghz, hfss, args)
    _write_overlay_core_panel(paths["emx_vs_hfss_ads_style_core_overlay"], freq_ghz, emx, hfss, summary, args)
    return {key: str(path) for key, path in paths.items()}


def _write_ads_style_target_marker_tables(summary: dict[str, Any], out_dir: Path, args: argparse.Namespace) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = _target_marker_records(summary, args)
    csv_path = out_dir / f"ads_style_target_marker_values_{float(args.target_ghz):g}ghz.csv"
    md_path = out_dir / f"ADS_STYLE_TARGET_MARKER_VALUES_{float(args.target_ghz):g}GHZ.md"
    fields = [
        "target_ghz",
        "nearest_freq_ghz",
        "metric",
        "emx",
        "hfss_ads",
        "abs_error",
        "percent_error",
        "metric_gate_max_percent",
        "metric_status",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    lines = [
        f"# ADS-Style Target Marker Values at {float(args.target_ghz):g} GHz",
        "",
        "These values are extracted from the same accepted-runner `plot_data` arrays used for the EMX/HFSS overlay figures.",
        "They are report callouts, not a substitute for the full 5-60 GHz gate.",
        "",
        "| Metric | EMX | HFSS/ADS | Abs error | Percent error | Gate | Status |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for record in records:
        lines.append(
            f"| `{record['metric']}` | {record['emx']:.10g} | {record['hfss_ads']:.10g} | "
            f"{record['abs_error']:.10g} | {record['percent_error']:.6g}% | "
            f"{record['metric_gate_max_percent']:.6g}% | {record['metric_status']} |"
        )
    lines.extend(
        [
            "",
            f"- Target frequency requested: `{float(args.target_ghz):g} GHz`",
            f"- Nearest grid frequency used: `{records[0]['nearest_freq_ghz']:.10g} GHz`",
            "- Required final statement still depends on the full-run decision being `ACCEPT_HFSS_VALIDATION_SAMPLE`.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"csv": str(csv_path), "markdown": str(md_path)}


def _target_marker_records(summary: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    plot_data = summary.get("plot_data") or {}
    freq_hz = np.asarray(plot_data.get("freq_hz", []), dtype=float)
    if freq_hz.size == 0 or not np.isfinite(freq_hz).all():
        raise ValueError("target marker cannot be extracted from missing or non-finite plot_data.freq_hz")
    target_hz = float(args.target_ghz) * 1.0e9
    index = int(np.argmin(np.abs(freq_hz - target_hz)))
    nearest_hz = float(freq_hz[index])
    if abs(nearest_hz - target_hz) > float(args.frequency_tolerance_hz):
        raise ValueError(
            f"target marker frequency {float(args.target_ghz):g} GHz is not present on the plot grid; "
            f"nearest={nearest_hz / 1.0e9:.10g} GHz, tolerance_hz={float(args.frequency_tolerance_hz):g}"
        )
    records: list[dict[str, Any]] = []
    for metric in ("lp_nh", "ls_nh", "qp", "qs", "k"):
        emx_values = np.asarray((plot_data.get("emx") or {}).get(metric, []), dtype=float)
        hfss_values = np.asarray((plot_data.get("hfss_ads") or {}).get(metric, []), dtype=float)
        if emx_values.size != freq_hz.size or hfss_values.size != freq_hz.size:
            raise ValueError(f"target marker metric {metric} length does not match plot_data.freq_hz")
        if not np.isfinite(emx_values).all() or not np.isfinite(hfss_values).all():
            raise ValueError(f"target marker metric {metric} contains non-finite values")
        emx_value = float(emx_values[index])
        hfss_value = float(hfss_values[index])
        abs_error = abs(hfss_value - emx_value)
        pct_error = abs_error / max(abs(emx_value), _relative_error_floor_for_target_marker(metric, emx_values)) * 100.0
        records.append(
            {
                "target_ghz": float(args.target_ghz),
                "nearest_freq_ghz": nearest_hz / 1.0e9,
                "metric": metric,
                "emx": emx_value,
                "hfss_ads": hfss_value,
                "abs_error": abs_error,
                "percent_error": pct_error,
                "metric_gate_max_percent": float((summary.get("metrics", {}).get(metric) or {}).get("max_percent_error", float("nan"))),
                "metric_status": str((summary.get("metrics", {}).get(metric) or {}).get("status", "UNKNOWN")),
            }
        )
    return records


def _relative_error_floor_for_target_marker(metric: str, reference: np.ndarray) -> float:
    # Keep this rule in sync with compare_emx_hfss_ads.py::_relative_error_floor.
    if metric == "k":
        return 0.02
    if metric in {"qp", "qs"}:
        return 0.2
    return max(float(np.nanmedian(np.abs(reference))) * 1.0e-3, 1.0e-6)


def _target_marker_checks(paths: dict[str, str], args: argparse.Namespace) -> list[Check]:
    csv_path = Path(paths.get("csv", ""))
    md_path = Path(paths.get("markdown", ""))
    failures: list[str] = []
    for path in (csv_path, md_path):
        if not path.is_file():
            failures.append(f"missing={path}")
        elif path.stat().st_size <= 0:
            failures.append(f"empty={path}")
    rows: list[dict[str, str]] = []
    if csv_path.is_file() and csv_path.stat().st_size > 0:
        try:
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"csv unreadable={type(exc).__name__}: {exc}")
    expected_metrics = {"lp_nh", "ls_nh", "qp", "qs", "k"}
    row_metrics = {row.get("metric") for row in rows}
    if row_metrics != expected_metrics:
        failures.append(f"metrics expected={sorted(expected_metrics)}, actual={sorted(row_metrics)}")
    for row in rows:
        try:
            target = float(row.get("target_ghz", "nan"))
            nearest = float(row.get("nearest_freq_ghz", "nan"))
            percent_error = float(row.get("percent_error", "nan"))
            gate = float(row.get("metric_gate_max_percent", "nan"))
        except ValueError:
            failures.append(f"non-numeric row={row}")
            continue
        if abs(target - float(args.target_ghz)) > 1.0e-9 or abs(nearest - float(args.target_ghz)) > 1.0e-6:
            failures.append(f"target frequency mismatch row={row}")
        if not np.isfinite(percent_error) or not np.isfinite(gate):
            failures.append(f"non-finite marker/gate error row={row}")
    if failures:
        return [Check("FAIL", "ADS-style target marker table", "; ".join(failures[:6]))]
    return [
        Check(
            "PASS",
            "ADS-style target marker table",
            f"K/Qp/Qs/Lp/Ls marker values recorded at {float(args.target_ghz):g} GHz in CSV and Markdown",
        )
    ]


def _artifact_records(paths: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {name: _file_record(Path(path_text)) for name, path_text in sorted(paths.items())}


def _artifact_manifest_checks(
    name: str,
    paths: dict[str, str],
    records: dict[str, dict[str, Any]],
) -> list[Check]:
    failures: list[str] = []
    if set(paths) != set(records):
        failures.append(f"keys mismatch paths={sorted(paths)} records={sorted(records)}")
    for key, path_text in sorted(paths.items()):
        path = Path(path_text)
        record = records.get(key) or {}
        if not path.is_file():
            failures.append(f"{key}: missing {path}")
            continue
        actual = _file_record(path)
        for field in ("path", "exists", "bytes", "sha256"):
            if record.get(field) != actual.get(field):
                failures.append(f"{key}: {field} mismatch")
                break
    return [
        Check(
            "PASS" if not failures else "FAIL",
            name,
            f"{len(records)} artifacts recorded with bytes and SHA256" if not failures else "; ".join(failures[:6]),
        )
    ]


def _plot_data_checks(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    plot_data = summary.get("plot_data")
    if not isinstance(plot_data, dict):
        return [Check("FAIL", "ADS-style plot_data integrity", "compare summary missing plot_data object")]
    failures: list[str] = []
    freq = _numeric_array(plot_data.get("freq_hz"), "plot_data.freq_hz", failures)
    if freq is not None:
        expected_points = int(args.expected_frequency_points)
        expected_start = float(args.compare_start_ghz) * 1.0e9
        expected_stop = float(args.compare_stop_ghz) * 1.0e9
        expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
        tolerance = float(args.frequency_tolerance_hz)
        if freq.size != expected_points:
            failures.append(f"plot_data.freq_hz point_count expected {expected_points}, got {freq.size}")
        if freq.size:
            if abs(float(freq[0]) - expected_start) > tolerance:
                failures.append(f"plot_data.freq_hz start expected {expected_start:g}, got {float(freq[0]):g}")
            if abs(float(freq[-1]) - expected_stop) > tolerance:
                failures.append(f"plot_data.freq_hz stop expected {expected_stop:g}, got {float(freq[-1]):g}")
        steps = np.diff(freq)
        if steps.size and np.any(steps <= 0.0):
            failures.append("plot_data.freq_hz is not strictly increasing")
        bad_steps = steps[np.abs(steps - expected_step) > tolerance] if steps.size else np.asarray([])
        if bad_steps.size:
            failures.append(f"plot_data.freq_hz step expected {expected_step:g}, bad_step_count={bad_steps.size}")
    required_metrics = ("k", "qp", "qs", "lp_nh", "ls_nh")
    for group_name in ("emx", "hfss_ads"):
        group = plot_data.get(group_name)
        if not isinstance(group, dict):
            failures.append(f"plot_data.{group_name} missing object")
            continue
        for metric in required_metrics:
            arr = _numeric_array(group.get(metric), f"plot_data.{group_name}.{metric}", failures)
            if arr is not None and freq is not None and arr.size != freq.size:
                failures.append(f"plot_data.{group_name}.{metric} length {arr.size} does not match freq length {freq.size}")
    if failures:
        return [Check("FAIL", "ADS-style plot_data integrity", "; ".join(failures[:10]))]
    return [
        Check(
            "PASS",
            "ADS-style plot_data integrity",
            f"finite K/Qp/Qs/Lp/Ls arrays on {int(args.expected_frequency_points)}-point {float(args.compare_start_ghz):g}-{float(args.compare_stop_ghz):g} GHz grid for EMX and HFSS/ADS",
        )
    ]


def _numeric_array(raw: object, label: str, failures: list[str]) -> np.ndarray | None:
    if raw is None:
        failures.append(f"{label} missing")
        return None
    try:
        arr = np.asarray(raw, dtype=float)
    except (TypeError, ValueError):
        failures.append(f"{label} cannot be parsed as numeric array")
        return None
    if arr.ndim != 1:
        failures.append(f"{label} is not one-dimensional")
        return None
    if arr.size == 0:
        failures.append(f"{label} is empty")
        return None
    if not np.isfinite(arr).all():
        failures.append(f"{label} contains non-finite values")
    return arr


def _write_single_core_panel(path: Path, title: str, freq_ghz: np.ndarray, values: dict[str, np.ndarray], args: argparse.Namespace) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.4), constrained_layout=True)
    fig.suptitle(f"{title}: ADS-style core transformer metrics", fontsize=14, fontweight="bold")
    _plot_lp_ls(axes[0, 0], freq_ghz, values["lp_nh"], values["ls_nh"], None, None)
    _plot_one(axes[0, 1], freq_ghz, values["qp"], "Qp", "#3348a3")
    _plot_one(axes[1, 0], freq_ghz, values["qs"], "Qs", "#be123c")
    _plot_one(axes[1, 1], freq_ghz, values["k"], "K", "#c2410c")
    for ax in axes.ravel():
        ax.axvline(float(args.target_ghz), color="#111827", linewidth=1.0, linestyle=":")
        ax.grid(True, alpha=0.28)
        ax.set_xlabel("freq (GHz)")
        _annotate_nearest(ax, freq_ghz, values, args.target_ghz)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _write_overlay_core_panel(
    path: Path,
    freq_ghz: np.ndarray,
    emx: dict[str, np.ndarray],
    hfss: dict[str, np.ndarray],
    summary: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.4), constrained_layout=True)
    fig.suptitle("EMX vs HFSS/ADS: ADS-style core metric overlay", fontsize=14, fontweight="bold")
    _plot_lp_ls(axes[0, 0], freq_ghz, emx["lp_nh"], emx["ls_nh"], hfss["lp_nh"], hfss["ls_nh"])
    _plot_overlay(axes[0, 1], freq_ghz, emx["qp"], hfss["qp"], "Qp")
    _plot_overlay(axes[1, 0], freq_ghz, emx["qs"], hfss["qs"], "Qs")
    _plot_overlay(axes[1, 1], freq_ghz, emx["k"], hfss["k"], "K")
    metrics = summary.get("metrics", {})
    for ax, metric in [(axes[0, 1], "qp"), (axes[1, 0], "qs"), (axes[1, 1], "k")]:
        err = (metrics.get(metric) or {}).get("max_percent_error")
        ax.text(0.02, 0.94, f"max err={float(err):.2f}%" if isinstance(err, (int, float)) else "max err=n/a", transform=ax.transAxes, ha="left", va="top", fontsize=8, bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.86, "pad": 3})
    lp_err = (metrics.get("lp_nh") or {}).get("max_percent_error")
    ls_err = (metrics.get("ls_nh") or {}).get("max_percent_error")
    axes[0, 0].text(
        0.02,
        0.94,
        f"Lp max err={float(lp_err):.2f}%\nLs max err={float(ls_err):.2f}%" if isinstance(lp_err, (int, float)) and isinstance(ls_err, (int, float)) else "L max err=n/a",
        transform=axes[0, 0].transAxes,
        ha="left",
        va="top",
        fontsize=8,
        bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.86, "pad": 3},
    )
    for ax in axes.ravel():
        ax.axvline(float(args.target_ghz), color="#111827", linewidth=1.0, linestyle=":")
        ax.grid(True, alpha=0.28)
        ax.set_xlabel("freq (GHz)")
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _plot_lp_ls(ax: Any, freq_ghz: np.ndarray, lp: np.ndarray, ls: np.ndarray, other_lp: np.ndarray | None, other_ls: np.ndarray | None) -> None:
    if other_lp is None or other_ls is None:
        ax.plot(freq_ghz, lp, color="#2563eb", linewidth=1.9, label="Lp")
        ax.plot(freq_ghz, ls, color="#dc2626", linewidth=1.9, label="Ls")
    else:
        ax.plot(freq_ghz, lp, color="#2563eb", linewidth=1.9, label="EMX Lp")
        ax.plot(freq_ghz, other_lp, color="#60a5fa", linewidth=1.7, linestyle="--", label="HFSS Lp")
        ax.plot(freq_ghz, ls, color="#dc2626", linewidth=1.9, label="EMX Ls")
        ax.plot(freq_ghz, other_ls, color="#fca5a5", linewidth=1.7, linestyle="--", label="HFSS Ls")
    ax.set_title("Lp / Ls")
    ax.set_ylabel("nH")
    ax.legend(loc="best", fontsize=8)


def _plot_one(ax: Any, freq_ghz: np.ndarray, values: np.ndarray, title: str, color: str) -> None:
    ax.plot(freq_ghz, values, color=color, linewidth=1.9, label=title)
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)


def _plot_overlay(ax: Any, freq_ghz: np.ndarray, emx: np.ndarray, hfss: np.ndarray, title: str) -> None:
    ax.plot(freq_ghz, emx, color="#111827", linewidth=1.9, label="EMX")
    ax.plot(freq_ghz, hfss, color="#ef4444", linewidth=1.7, linestyle="--", label="HFSS")
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)


def _annotate_nearest(ax: Any, freq_ghz: np.ndarray, values: dict[str, np.ndarray], target_ghz: float) -> None:
    idx = int(np.argmin(np.abs(freq_ghz - float(target_ghz))))
    title = ax.get_title()
    if title.startswith("Lp"):
        text = f"{freq_ghz[idx]:.2f} GHz\nLp={values['lp_nh'][idx]:.3g} nH\nLs={values['ls_nh'][idx]:.3g} nH"
    elif title == "Qp":
        text = f"{freq_ghz[idx]:.2f} GHz\nQp={values['qp'][idx]:.3g}"
    elif title == "Qs":
        text = f"{freq_ghz[idx]:.2f} GHz\nQs={values['qs'][idx]:.3g}"
    else:
        text = f"{freq_ghz[idx]:.2f} GHz\nK={values['k'][idx]:.3g}"
    ax.text(0.02, 0.94, text, transform=ax.transAxes, ha="left", va="top", fontsize=8, bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.86, "pad": 3})


def _figure_checks(paths: dict[str, str]) -> list[Check]:
    missing = [name for name, path in paths.items() if not Path(path).is_file()]
    if missing:
        return [Check("FAIL", "ADS-style core metric figures", f"missing={missing}")]
    failures: list[str] = []
    for name, path_text in paths.items():
        failures.extend(_png_figure_failures(Path(path_text), name))
    if failures:
        return [Check("FAIL", "ADS-style core metric figures", "; ".join(failures[:6]))]
    return [
        Check(
            "PASS",
            "ADS-style core metric figures",
            f"{len(paths)} PNG figures present with valid dimensions and nonblank content",
        )
    ]


def _png_figure_failures(path: Path, name: str) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"{name}: unreadable PNG ({type(exc).__name__}: {exc})"]
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        return [f"{name}: missing PNG signature"]
    if len(data) < 33:
        return [f"{name}: missing PNG IHDR chunk"]
    ihdr_length = int.from_bytes(data[8:12], "big")
    ihdr_type = data[12:16]
    if ihdr_length != 13 or ihdr_type != b"IHDR":
        return [f"{name}: invalid PNG IHDR chunk"]
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return [f"{name}: invalid PNG dimensions {width}x{height}"]
    failures: list[str] = []
    if width < MIN_ACCEPTED_FIGURE_WIDTH or height < MIN_ACCEPTED_FIGURE_HEIGHT:
        failures.append(
            f"{name}: PNG dimensions {width}x{height} below minimum {MIN_ACCEPTED_FIGURE_WIDTH}x{MIN_ACCEPTED_FIGURE_HEIGHT}"
        )
    if len(data) < MIN_ACCEPTED_FIGURE_BYTES:
        failures.append(f"{name}: PNG bytes {len(data)} below minimum {MIN_ACCEPTED_FIGURE_BYTES}")
    if failures:
        return failures
    return _png_figure_content_failures(path, name)


def _png_figure_content_failures(path: Path, name: str) -> list[str]:
    try:
        from PIL import Image, ImageStat
    except Exception as exc:  # noqa: BLE001
        return [f"{name}: Pillow unavailable for nonblank PNG check ({type(exc).__name__}: {exc})"]
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            extrema = ImageStat.Stat(rgb).extrema
    except Exception as exc:  # noqa: BLE001
        return [f"{name}: unreadable PNG image data ({type(exc).__name__}: {exc})"]
    max_delta = max((high - low) for low, high in extrema)
    if max_delta <= MIN_ACCEPTED_FIGURE_COLOR_DELTA:
        return [f"{name}: blank or nearly constant PNG (max_channel_delta={max_delta})"]
    return []


def _status_check(name: str, actual: Any, expected: str) -> Check:
    return Check("PASS" if actual == expected else "FAIL", name, f"expected={expected}, actual={actual!r}")


def _has_failed(checks: list[Check]) -> bool:
    return any(check.status == "FAIL" for check in checks)


def _decision(overall_status: str, counts: dict[str, int]) -> str:
    if overall_status != "PASS":
        return "DO_NOT_USE_HFSS_COMPARISON"
    if counts.get("WARN", 0):
        return "REVIEW_ONLY_HFSS_AUDIT_SKIPPED"
    return "ACCEPT_HFSS_VALIDATION_SAMPLE"


def _command_record(name: str, completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "name": name,
        "args": list(completed.args) if isinstance(completed.args, (list, tuple)) else completed.args,
        "returncode": completed.returncode,
        "stdout_tail": "\n".join((completed.stdout or "").splitlines()[-12:]),
        "stderr_tail": "\n".join((completed.stderr or "").splitlines()[-12:]),
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else None,
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def _render_report(summary: dict[str, Any], compare_summary: dict[str, Any]) -> str:
    lines = [
        "# Accepted EMX to HFSS/ADS Validation",
        "",
        f"- Overall status: **{summary.get('overall_status')}**",
        f"- Decision: `{summary.get('decision')}`",
        f"- EMX: `{summary.get('emx_s4p', {}).get('path')}`",
        f"- HFSS/ADS: `{summary.get('hfss_s4p', {}).get('path')}`",
        f"- Compare summary: `{summary.get('compare_summary')}`",
        f"- ADS/Python formula note: `{summary.get('formula_note')}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary.get("checks", []):
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## Metric Gate", ""])
    for metric, item in (compare_summary.get("metrics") or {}).items():
        lines.append(
            f"- `{metric}`: {item.get('status')} max_error={float(item.get('max_percent_error', 0.0)):.6g}%"
        )
    lines.extend(["", "## Figures", ""])
    for name, path in summary.get("figure_paths", {}).items():
        lines.append(f"- `{name}`: `{path}`")
    lines.extend(["", "## Target Marker Values", ""])
    for name, path in summary.get("target_marker_paths", {}).items():
        lines.append(f"- `{name}`: `{path}`")
    lines.extend(["", "## Formula Evidence", ""])
    lines.append(f"- ADS/Python formula cross-check: `{summary.get('formula_note')}`")
    lines.extend(["", "## Boundary", ""])
    lines.extend(f"- {item}" for item in summary.get("method_notes", []))
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
