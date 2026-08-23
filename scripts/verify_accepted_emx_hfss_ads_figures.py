#!/usr/bin/env python3
"""Verify final accepted EMX/HFSS ADS-style figure evidence.

This is a narrow evidence gate for reportable Lp/Ls/Qp/Qs/K figures. It does
not run EMX, HFSS, ADS, or the comparison workflow. It accepts only the output
of `run_accepted_emx_hfss_ads_validation.py` after that workflow has already
returned `ACCEPT_HFSS_VALIDATION_SAMPLE`.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_CORE_METRICS = ("k", "qp", "qs", "lp_nh", "ls_nh")
REQUIRED_FIGURES = (
    "emx_ads_style_core_metrics",
    "hfss_ads_style_core_metrics",
    "emx_vs_hfss_ads_style_core_overlay",
)
REQUIRED_TARGET_MARKER_METRICS = ("lp_nh", "ls_nh", "qp", "qs", "k")
REQUIRED_ACCEPTED_SUMMARY_CHECKS = (
    "accepted EMX import status",
    "accepted EMX import decision",
    "accepted EMX local SHA",
    "accepted EMX import verifier evidence",
    "accepted EMX import core metric artifact",
    "HFSS geometry asset audit evidence",
    "HFSS geometry required asset checks",
    "HFSS geometry artifact paths",
    "HFSS Touchstone physical gate status",
    "HFSS Touchstone required differential/physics checks",
    "HFSS Touchstone physical gate internal checks",
    "EMX-vs-HFSS compare status",
    "EMX-vs-HFSS compare source traceability",
    "EMX-vs-HFSS compare criterion",
    "EMX-vs-HFSS compare frequency window",
    "EMX-vs-HFSS compare frequency-grid checks",
    "EMX-vs-HFSS compare core metric errors",
    "ADS/Python formula note",
    "ADS-style plot_data integrity",
    "ADS-style core metric figures",
    "ADS-style core metric figure manifest",
    "ADS-style target marker table",
    "ADS-style target marker manifest",
)
REQUIRED_GRID_CHECKS = (
    "ADS no-extrapolation coverage",
    "expected frequency points",
    "expected frequency step",
    "matching HFSS/ADS frequency grid",
)
MIN_PNG_WIDTH = 640
MIN_PNG_HEIGHT = 360
MIN_PNG_BYTES = 2048
MIN_PNG_COLOR_DELTA = 2


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    accepted_summary_path = Path(args.accepted_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else accepted_summary_path.parent / "accepted_figure_evidence_audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[Check] = []
    accepted_summary: dict[str, Any] = {}
    compare_summary: dict[str, Any] = {}
    formula_note_path: Path | None = None
    try:
        accepted_summary = _read_json(accepted_summary_path)
        base_dir = accepted_summary_path.parent
        checks.extend(_accepted_summary_checks(accepted_summary))

        compare_summary_path = _resolve_path(accepted_summary.get("compare_summary"), base_dir)
        formula_note_path = _resolve_path(accepted_summary.get("formula_note"), base_dir)
        hfss_audit_summary_path = _resolve_path(accepted_summary.get("hfss_audit_summary"), base_dir)
        hfss_geometry_summary_path = _resolve_path(accepted_summary.get("hfss_geometry_summary"), base_dir)
        checks.extend(
            _required_artifact_checks(
                compare_summary_path,
                formula_note_path,
                hfss_audit_summary_path,
                hfss_geometry_summary_path,
            )
        )

        if compare_summary_path and compare_summary_path.is_file():
            compare_summary = _read_json(compare_summary_path)
            checks.extend(_compare_summary_checks(compare_summary, accepted_summary, args))
        else:
            checks.append(Check("FAIL", "accepted compare summary content", f"missing compare summary: {compare_summary_path}"))

        checks.extend(_formula_note_checks(formula_note_path))
        checks.extend(_figure_checks(accepted_summary.get("figure_paths"), base_dir))
        checks.extend(
            _artifact_record_checks(
                accepted_summary.get("figure_paths"),
                accepted_summary.get("figure_records"),
                base_dir,
                required_keys=REQUIRED_FIGURES,
                check_name="accepted final figure manifest",
            )
        )
        checks.extend(_target_marker_checks(accepted_summary.get("target_marker_paths"), compare_summary, base_dir, args))
        checks.extend(
            _artifact_record_checks(
                accepted_summary.get("target_marker_paths"),
                accepted_summary.get("target_marker_records"),
                base_dir,
                required_keys=("csv", "markdown"),
                check_name="accepted target marker manifest",
            )
        )
    except Exception as exc:  # noqa: BLE001 - exact audit failure belongs in evidence.
        checks.append(Check("FAIL", "accepted figure evidence verifier execution", f"{type(exc).__name__}: {exc}"))

    status_counts = _status_counts(checks)
    overall_status = "FAIL" if status_counts.get("FAIL", 0) else "PASS"
    decision = "ACCEPT_FINAL_LP_LS_Q_K_FIGURES" if overall_status == "PASS" else "DO_NOT_USE_FINAL_LP_LS_Q_K_FIGURES"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "accepted_summary": str(accepted_summary_path),
        "compare_summary": str(_resolve_path(accepted_summary.get("compare_summary"), accepted_summary_path.parent)) if accepted_summary else None,
        "formula_note": str(formula_note_path) if formula_note_path else None,
        "hfss_geometry_summary": str(_resolve_path(accepted_summary.get("hfss_geometry_summary"), accepted_summary_path.parent)) if accepted_summary else None,
        "checks": [check.as_dict() for check in checks],
        "status_counts": status_counts,
        "requirements": {
            "accepted_validation_decision": "ACCEPT_HFSS_VALIDATION_SAMPLE",
            "frequency_ghz": {
                "start": float(args.expected_frequency_start_ghz),
                "stop": float(args.expected_frequency_stop_ghz),
                "step": float(args.expected_frequency_step_ghz),
                "points": int(args.expected_frequency_points),
            },
            "max_percent_error": float(args.max_percent_error),
            "core_metrics": list(REQUIRED_CORE_METRICS),
            "required_figures": list(REQUIRED_FIGURES),
            "target_marker_ghz": float(args.target_ghz),
            "target_marker_metrics": list(REQUIRED_TARGET_MARKER_METRICS),
        },
        "method_notes": [
            "This verifier is a final-figure evidence gate, not a simulator runner.",
            "PASS requires accepted EMX/HFSS validation summary decision ACCEPT_HFSS_VALIDATION_SAMPLE.",
            "PASS requires the accepted validation summary to include PASS HFSS geometry asset audit evidence for the model used to export the HFSS S4P.",
            f"PASS requires K/Qp/Qs/Lp/Ls <= max_percent_error over the exact {float(args.expected_frequency_start_ghz):g}-{float(args.expected_frequency_stop_ghz):g} GHz / {float(args.expected_frequency_step_ghz):g} GHz / {int(args.expected_frequency_points)}-point no-extrapolation grid.",
            "PASS requires EMX, HFSS/ADS, and overlay PNG figures to be real, decodable, large enough, and nonblank.",
            "PASS requires figure and target-marker artifact records to match current file bytes and SHA256 digests.",
            "PASS requires the target-frequency marker CSV/Markdown to exist and match the accepted plot_data values for K/Qp/Qs/Lp/Ls.",
        ],
    }
    summary_path = out_dir / "accepted_figure_evidence_audit_summary.json"
    report_path = out_dir / "accepted_figure_evidence_audit_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accepted-summary", required=True)
    parser.add_argument("--out-dir")
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _accepted_summary_checks(summary: dict[str, Any]) -> list[Check]:
    checks = [
        Check(
            "PASS" if summary.get("overall_status") == "PASS" else "FAIL",
            "accepted validation status",
            f"overall_status={summary.get('overall_status')!r}",
        ),
        Check(
            "PASS" if summary.get("decision") == "ACCEPT_HFSS_VALIDATION_SAMPLE" else "FAIL",
            "accepted validation decision",
            f"decision={summary.get('decision')!r}",
        ),
    ]
    by_name = {item.get("name"): item for item in summary.get("checks", []) if isinstance(item, dict)}
    failures = [
        f"{name}={(by_name.get(name) or {}).get('status')!r}"
        for name in REQUIRED_ACCEPTED_SUMMARY_CHECKS
        if (by_name.get(name) or {}).get("status") != "PASS"
    ]
    checks.append(
        Check(
            "PASS" if not failures else "FAIL",
            "accepted validation required checks",
            f"{len(REQUIRED_ACCEPTED_SUMMARY_CHECKS)} checks PASS" if not failures else "; ".join(failures[:10]),
        )
    )
    return checks


def _required_artifact_checks(
    compare_summary: Path | None,
    formula_note: Path | None,
    hfss_audit_summary: Path | None,
    hfss_geometry_summary: Path | None,
) -> list[Check]:
    required = {
        "compare summary": compare_summary,
        "formula note": formula_note,
        "HFSS audit summary": hfss_audit_summary,
        "HFSS geometry summary": hfss_geometry_summary,
    }
    failures = [f"{name} missing: {path}" for name, path in required.items() if path is None or not path.is_file()]
    return [
        Check(
            "PASS" if not failures else "FAIL",
            "accepted validation required artifacts",
            "compare summary, formula note, HFSS audit summary, and HFSS geometry summary exist" if not failures else "; ".join(failures),
        )
    ]


def _compare_summary_checks(
    compare: dict[str, Any],
    accepted: dict[str, Any],
    args: argparse.Namespace,
) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "PASS" if compare.get("overall_status") == "PASS" else "FAIL",
            "accepted compare summary status",
            f"overall_status={compare.get('overall_status')!r}",
        )
    )
    checks.append(_source_traceability_check(compare, accepted))
    checks.append(_frequency_window_check(compare, args))
    checks.append(_grid_checks(compare, args))
    checks.append(_metric_error_checks(compare, args))
    checks.append(_plot_data_checks(compare, args))
    return checks


def _source_traceability_check(compare: dict[str, Any], accepted: dict[str, Any]) -> Check:
    emx_expected = _normalized_path_text((accepted.get("emx_s4p") or {}).get("path"))
    hfss_expected = _normalized_path_text((accepted.get("hfss_s4p") or {}).get("path"))
    failures: list[str] = []
    if _normalized_path_text(compare.get("emx_source")) != emx_expected:
        failures.append(f"emx_source mismatch expected={emx_expected!r}, actual={compare.get('emx_source')!r}")
    if _normalized_path_text(compare.get("hfss_ads_source")) != hfss_expected:
        failures.append(f"hfss_ads_source mismatch expected={hfss_expected!r}, actual={compare.get('hfss_ads_source')!r}")
    return Check(
        "PASS" if not failures else "FAIL",
        "accepted compare source traceability",
        "compare sources match accepted EMX/HFSS inputs" if not failures else "; ".join(failures),
    )


def _frequency_window_check(compare: dict[str, Any], args: argparse.Namespace) -> Check:
    window = compare.get("frequency_window_hz") or {}
    expected_start = float(args.expected_frequency_start_ghz) * 1.0e9
    expected_stop = float(args.expected_frequency_stop_ghz) * 1.0e9
    tolerance = float(args.frequency_tolerance_hz)
    failures: list[str] = []
    if abs(_float(window.get("min")) - expected_start) > tolerance:
        failures.append(f"min={window.get('min')!r}")
    if abs(_float(window.get("max")) - expected_stop) > tolerance:
        failures.append(f"max={window.get('max')!r}")
    if int(window.get("count", -1)) != int(args.expected_frequency_points):
        failures.append(f"count={window.get('count')!r}")
    return Check(
        "PASS" if not failures else "FAIL",
        "accepted compare frequency window",
        f"{float(args.expected_frequency_start_ghz):g}-{float(args.expected_frequency_stop_ghz):g} GHz / {float(args.expected_frequency_step_ghz):g} GHz / {int(args.expected_frequency_points)} points"
        if not failures
        else "; ".join(failures),
    )


def _grid_checks(compare: dict[str, Any], args: argparse.Namespace) -> Check:
    grid = compare.get("frequency_grid_checks") or {}
    failures = [f"{name}={(grid.get(name) or {}).get('status')!r}" for name in REQUIRED_GRID_CHECKS if (grid.get(name) or {}).get("status") != "PASS"]
    return Check(
        "PASS" if not failures else "FAIL",
        "accepted compare grid/no-extrapolation checks",
        f"ADS no-extrapolation coverage and matching {int(args.expected_frequency_points)}-point grid PASS" if not failures else "; ".join(failures),
    )


def _metric_error_checks(compare: dict[str, Any], args: argparse.Namespace) -> Check:
    metrics = compare.get("metrics") or {}
    failures: list[str] = []
    for name in REQUIRED_CORE_METRICS:
        item = metrics.get(name) or {}
        if item.get("status") != "PASS":
            failures.append(f"{name}_status={item.get('status')!r}")
            continue
        error = _float(item.get("max_percent_error"), default=math.inf)
        if error > float(args.max_percent_error):
            failures.append(f"{name}_max_percent_error={error:g}")
    return Check(
        "PASS" if not failures else "FAIL",
        f"accepted K/Qp/Qs/Lp/Ls <= {float(args.max_percent_error):g}% errors",
        "K/Qp/Qs/Lp/Ls max_percent_error <= gate" if not failures else "; ".join(failures),
    )


def _plot_data_checks(compare: dict[str, Any], args: argparse.Namespace) -> Check:
    plot_data = compare.get("plot_data")
    if not isinstance(plot_data, dict):
        return Check("FAIL", "accepted ADS-style plot_data arrays", "compare summary missing plot_data object")
    failures: list[str] = []
    freq = _numeric_list(plot_data.get("freq_hz"), "plot_data.freq_hz", failures)
    if freq:
        expected_start = float(args.expected_frequency_start_ghz) * 1.0e9
        expected_stop = float(args.expected_frequency_stop_ghz) * 1.0e9
        expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
        tolerance = float(args.frequency_tolerance_hz)
        if len(freq) != int(args.expected_frequency_points):
            failures.append(f"freq point_count={len(freq)}")
        if abs(freq[0] - expected_start) > tolerance:
            failures.append(f"freq start={freq[0]:g}")
        if abs(freq[-1] - expected_stop) > tolerance:
            failures.append(f"freq stop={freq[-1]:g}")
        steps = [right - left for left, right in zip(freq, freq[1:])]
        if any(step <= 0 for step in steps):
            failures.append("freq is not strictly increasing")
        bad_steps = [step for step in steps if abs(step - expected_step) > tolerance]
        if bad_steps:
            failures.append(f"freq step bad_count={len(bad_steps)}")
    for group_name in ("emx", "hfss_ads"):
        group = plot_data.get(group_name)
        if not isinstance(group, dict):
            failures.append(f"plot_data.{group_name} missing object")
            continue
        for metric in REQUIRED_CORE_METRICS:
            arr = _numeric_list(group.get(metric), f"plot_data.{group_name}.{metric}", failures)
            if arr and freq and len(arr) != len(freq):
                failures.append(f"plot_data.{group_name}.{metric} length={len(arr)} freq_length={len(freq)}")
    return Check(
        "PASS" if not failures else "FAIL",
        "accepted ADS-style plot_data arrays",
        f"finite K/Qp/Qs/Lp/Ls arrays for EMX and HFSS/ADS on {int(args.expected_frequency_points)}-point grid" if not failures else "; ".join(failures[:10]),
    )


def _formula_note_checks(path: Path | None) -> list[Check]:
    required = (
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
        "Qp = imag(Zdiff[1,1]) / real(Zdiff[1,1])",
        "Qs = imag(Zdiff[2,2]) / real(Zdiff[2,2])",
        "ADS no-extrapolation coverage",
    )
    if path is None or not path.is_file():
        return [Check("FAIL", "accepted ADS/Python formula note", f"missing: {path}")]
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in required if fragment not in text]
    return [
        Check(
            "PASS" if not missing else "FAIL",
            "accepted ADS/Python formula note",
            str(path) if not missing else f"missing={missing}",
        )
    ]


def _figure_checks(raw_paths: object, base_dir: Path) -> list[Check]:
    if not isinstance(raw_paths, dict):
        return [Check("FAIL", "accepted final figure PNGs", "summary missing figure_paths object")]
    failures: list[str] = []
    for name in REQUIRED_FIGURES:
        path = _resolve_path(raw_paths.get(name), base_dir)
        if path is None:
            failures.append(f"{name}: missing path")
            continue
        failures.extend(_png_failures(path, name))
    return [
        Check(
            "PASS" if not failures else "FAIL",
            "accepted final figure PNGs",
            "EMX, HFSS/ADS, and overlay PNG figures are valid and nonblank" if not failures else "; ".join(failures[:8]),
        )
    ]


def _target_marker_checks(
    raw_paths: object,
    compare: dict[str, Any],
    base_dir: Path,
    args: argparse.Namespace,
) -> list[Check]:
    if not isinstance(raw_paths, dict):
        return [Check("FAIL", "accepted target marker table", "summary missing target_marker_paths object")]
    csv_path = _resolve_path(raw_paths.get("csv"), base_dir)
    md_path = _resolve_path(raw_paths.get("markdown"), base_dir)
    failures: list[str] = []
    for label, path in (("csv", csv_path), ("markdown", md_path)):
        if path is None:
            failures.append(f"{label}: missing path")
        elif not path.is_file():
            failures.append(f"{label}: missing file {path}")
        elif path.stat().st_size <= 0:
            failures.append(f"{label}: empty file {path}")
    rows: list[dict[str, str]] = []
    if csv_path is not None and csv_path.is_file() and csv_path.stat().st_size > 0:
        try:
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"csv: unreadable ({type(exc).__name__}: {exc})")
    marker_failures = _target_marker_row_failures(rows, compare, args)
    failures.extend(marker_failures)
    if md_path is not None and md_path.is_file() and md_path.stat().st_size > 0:
        text = md_path.read_text(encoding="utf-8")
        required_text = (
            "ADS-Style Target Marker Values",
            "EMX",
            "HFSS/ADS",
            "Percent error",
            "Required final statement still depends",
        )
        missing_text = [item for item in required_text if item not in text]
        if missing_text:
            failures.append(f"markdown missing={missing_text}")
        for metric in REQUIRED_TARGET_MARKER_METRICS:
            if metric not in text:
                failures.append(f"markdown missing metric {metric}")
    return [
        Check(
            "PASS" if not failures else "FAIL",
            "accepted target marker table",
            f"K/Qp/Qs/Lp/Ls marker CSV/Markdown match plot_data at {float(args.target_ghz):g} GHz"
            if not failures
            else "; ".join(failures[:10]),
        )
    ]


def _artifact_record_checks(
    raw_paths: object,
    raw_records: object,
    base_dir: Path,
    *,
    required_keys: tuple[str, ...],
    check_name: str,
) -> list[Check]:
    if not isinstance(raw_paths, dict):
        return [Check("FAIL", check_name, "summary missing paths object")]
    if not isinstance(raw_records, dict):
        return [Check("FAIL", check_name, "summary missing records object")]
    failures: list[str] = []
    expected_keys = set(required_keys)
    if set(raw_paths) != expected_keys:
        failures.append(f"path keys expected={sorted(expected_keys)}, actual={sorted(raw_paths)}")
    if set(raw_records) != expected_keys:
        failures.append(f"record keys expected={sorted(expected_keys)}, actual={sorted(raw_records)}")
    for key in required_keys:
        path = _resolve_path(raw_paths.get(key), base_dir)
        record = raw_records.get(key)
        if path is None:
            failures.append(f"{key}: missing path")
            continue
        if not isinstance(record, dict):
            failures.append(f"{key}: missing record")
            continue
        failures.extend(_artifact_record_failures(key, path, record))
    return [
        Check(
            "PASS" if not failures else "FAIL",
            check_name,
            f"{len(required_keys)} artifacts match recorded bytes and SHA256" if not failures else "; ".join(failures[:8]),
        )
    ]


def _artifact_record_failures(key: str, path: Path, record: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not path.is_file():
        return [f"{key}: missing file {path}"]
    actual_bytes = path.stat().st_size
    actual_sha = _sha256(path)
    expected_path = _normalized_path_text(record.get("path"))
    actual_path = _normalized_path_text(path)
    if expected_path != actual_path:
        failures.append(f"{key}: path mismatch expected={expected_path!r}, actual={actual_path!r}")
    if record.get("exists") is not True:
        failures.append(f"{key}: exists record={record.get('exists')!r}")
    if int(record.get("bytes") or -1) != actual_bytes:
        failures.append(f"{key}: bytes expected={record.get('bytes')!r}, actual={actual_bytes}")
    if record.get("sha256") != actual_sha:
        failures.append(f"{key}: sha256 mismatch")
    return failures


def _target_marker_row_failures(rows: list[dict[str, str]], compare: dict[str, Any], args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    if not rows:
        return ["csv: no marker rows"]
    by_metric = {row.get("metric"): row for row in rows}
    expected_metrics = set(REQUIRED_TARGET_MARKER_METRICS)
    actual_metrics = set(by_metric)
    if actual_metrics != expected_metrics:
        failures.append(f"csv metrics expected={sorted(expected_metrics)}, actual={sorted(actual_metrics)}")
    plot_data = compare.get("plot_data") if isinstance(compare, dict) else None
    if not isinstance(plot_data, dict):
        failures.append("compare summary missing plot_data for marker cross-check")
        return failures
    freq = _numeric_list(plot_data.get("freq_hz"), "plot_data.freq_hz", failures)
    if not freq:
        return failures
    target_hz = float(args.target_ghz) * 1.0e9
    nearest_index = min(range(len(freq)), key=lambda index: abs(freq[index] - target_hz))
    nearest_hz = freq[nearest_index]
    if abs(nearest_hz - target_hz) > float(args.frequency_tolerance_hz):
        failures.append(
            f"plot_data target frequency missing: target={float(args.target_ghz):g} GHz nearest={nearest_hz / 1.0e9:.10g} GHz"
        )
    for metric in REQUIRED_TARGET_MARKER_METRICS:
        row = by_metric.get(metric)
        if row is None:
            continue
        failures.extend(_target_marker_single_row_failures(row, metric, plot_data, nearest_index, nearest_hz, compare, args))
    return failures


def _target_marker_single_row_failures(
    row: dict[str, str],
    metric: str,
    plot_data: dict[str, Any],
    nearest_index: int,
    nearest_hz: float,
    compare: dict[str, Any],
    args: argparse.Namespace,
) -> list[str]:
    failures: list[str] = []
    emx_values = _numeric_list((plot_data.get("emx") or {}).get(metric), f"plot_data.emx.{metric}", failures)
    hfss_values = _numeric_list((plot_data.get("hfss_ads") or {}).get(metric), f"plot_data.hfss_ads.{metric}", failures)
    if not emx_values or not hfss_values:
        return failures
    if nearest_index >= len(emx_values) or nearest_index >= len(hfss_values):
        return [f"{metric}: marker index outside metric arrays"]
    expected = {
        "target_ghz": float(args.target_ghz),
        "nearest_freq_ghz": nearest_hz / 1.0e9,
        "emx": float(emx_values[nearest_index]),
        "hfss_ads": float(hfss_values[nearest_index]),
    }
    expected["abs_error"] = abs(expected["hfss_ads"] - expected["emx"])
    expected["percent_error"] = expected["abs_error"] / max(
        abs(expected["emx"]),
        _relative_error_floor(metric, emx_values),
    ) * 100.0
    metric_gate = (compare.get("metrics", {}).get(metric) or {})
    expected["metric_gate_max_percent"] = _float(metric_gate.get("max_percent_error"), default=math.inf)
    numeric_tolerances = {
        "target_ghz": 1.0e-9,
        "nearest_freq_ghz": 1.0e-9,
        "emx": 1.0e-8,
        "hfss_ads": 1.0e-8,
        "abs_error": 1.0e-8,
        "percent_error": 1.0e-6,
        "metric_gate_max_percent": 1.0e-6,
    }
    for key, expected_value in expected.items():
        actual = _float(row.get(key), default=math.nan)
        if not math.isfinite(actual):
            failures.append(f"{metric}: {key} non-finite in marker row")
            continue
        if abs(actual - expected_value) > numeric_tolerances[key]:
            failures.append(f"{metric}: {key} expected={expected_value:.10g}, actual={actual:.10g}")
    if row.get("metric_status") != "PASS":
        failures.append(f"{metric}: marker metric_status={row.get('metric_status')!r}")
    if metric_gate.get("status") != "PASS":
        failures.append(f"{metric}: compare metric status={metric_gate.get('status')!r}")
    if expected["metric_gate_max_percent"] > float(args.max_percent_error):
        failures.append(f"{metric}: gate max_percent_error={expected['metric_gate_max_percent']:g}")
    return failures


def _relative_error_floor(metric: str, reference: list[float]) -> float:
    if metric == "k":
        return 0.02
    if metric in {"qp", "qs"}:
        return 0.2
    finite = [abs(value) for value in reference if math.isfinite(value)]
    if not finite:
        return 1.0e-6
    finite.sort()
    mid = len(finite) // 2
    median = finite[mid] if len(finite) % 2 else (finite[mid - 1] + finite[mid]) / 2.0
    return max(float(median) * 1.0e-3, 1.0e-6)


def _png_failures(path: Path, name: str) -> list[str]:
    if not path.is_file():
        return [f"{name}: missing file {path}"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"{name}: unreadable PNG ({type(exc).__name__}: {exc})"]
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return [f"{name}: missing PNG signature"]
    if len(data) < 33:
        return [f"{name}: missing PNG IHDR chunk"]
    ihdr_length = int.from_bytes(data[8:12], "big")
    ihdr_type = data[12:16]
    if ihdr_length != 13 or ihdr_type != b"IHDR":
        return [f"{name}: invalid PNG IHDR chunk"]
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    failures: list[str] = []
    if width < MIN_PNG_WIDTH or height < MIN_PNG_HEIGHT:
        failures.append(f"{name}: PNG dimensions {width}x{height} below minimum {MIN_PNG_WIDTH}x{MIN_PNG_HEIGHT}")
    if len(data) < MIN_PNG_BYTES:
        failures.append(f"{name}: PNG bytes {len(data)} below minimum {MIN_PNG_BYTES}")
    if failures:
        return failures
    try:
        from PIL import Image, ImageStat

        with Image.open(path) as image:
            extrema = ImageStat.Stat(image.convert("RGB")).extrema
    except Exception as exc:  # noqa: BLE001
        return [f"{name}: unreadable PNG image data ({type(exc).__name__}: {exc})"]
    max_delta = max(high - low for low, high in extrema)
    if max_delta <= MIN_PNG_COLOR_DELTA:
        return [f"{name}: blank or nearly constant PNG (max_channel_delta={max_delta})"]
    return []


def _numeric_list(raw: object, label: str, failures: list[str]) -> list[float]:
    if not isinstance(raw, list) or not raw:
        failures.append(f"{label} missing or empty")
        return []
    values: list[float] = []
    for value in raw:
        try:
            number = float(value)
        except (TypeError, ValueError):
            failures.append(f"{label} contains non-numeric value")
            return []
        if not math.isfinite(number):
            failures.append(f"{label} contains non-finite value")
            return []
        values.append(number)
    return values


def _resolve_path(raw: object, base_dir: Path) -> Path | None:
    if raw is None or str(raw).strip() == "":
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    try:
        return path.resolve()
    except OSError:
        return path


def _normalized_path_text(raw: object) -> str:
    path = _resolve_path(raw, Path.cwd())
    return str(path) if path is not None else ""


def _float(raw: object, *, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return dict(sorted(counts.items()))


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Accepted EMX/HFSS ADS-Style Figure Evidence Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Accepted summary: `{summary['accepted_summary']}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This gate validates already-generated final figure evidence only.",
            "- It does not run ADS, HFSS, EMX, or the accepted comparison workflow.",
            "- A FAIL decision means the Lp/Ls/Qp/Qs/K figures must not be used as final report evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
