#!/usr/bin/env python3
"""Audit readiness of the new .s8p physical-feature transformer dataset.

This gate checks local artifacts only. It verifies that dataset rows contain
simulator-derived physical features, that Touchstone files are .s8p with the
expected port/frequency grid, and that the run manifest records the 8-port
power-line topology contract.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402
from rfic_transformer_inverse_design.analysis.extraction import (  # noqa: E402
    multiport_s_to_grounded_differential_z,
    multiport_single_ended_to_differential_z,
)

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception as exc:  # pragma: no cover - environment-specific fallback
    plt = None
    MATPLOTLIB_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    MATPLOTLIB_IMPORT_ERROR = ""


Q_DEFINITIONS = ("min", "mean", "geometric_mean", "harmonic_mean", "primary", "secondary")
REQUIRED_FEATURE_COLUMNS = ("lp_nh_center", "ls_nh_center", "q_center", "k_center", "qp_center", "qs_center")
RECOMPUTED_FEATURE_COLUMNS = ("lp_nh_center", "ls_nh_center", "m_nh_center", "q_center", "k_center", "qp_center", "qs_center")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else dataset_dir / "s8p_physical_feature_dataset_audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(dataset_dir / "dataset_rows.csv")
    manifest = _read_json(dataset_dir / "dataset_manifest.json")
    ok_rows = [row for row in rows if _truthy(row.get("ok", "true"))]
    touchstone_records = _audit_touchstones(dataset_dir, ok_rows, args)
    feature_recompute_records = _recompute_feature_labels_from_touchstones(dataset_dir, ok_rows, manifest, args)
    coverage_columns = _coverage_feature_columns(ok_rows, args.coverage_feature_columns, bool(args.require_scalar_q_feature))
    coverage = _feature_coverage(ok_rows, coverage_columns, int(args.coverage_bins))
    coverage_artifacts, coverage_plot_errors = _write_coverage_plots(ok_rows, coverage_columns, coverage, out_dir, args)
    checks = _build_checks(rows, ok_rows, manifest, touchstone_records, args)
    checks.extend(_feature_recompute_checks(feature_recompute_records, args))
    checks.extend(_coverage_checks(coverage, coverage_artifacts, coverage_plot_errors, args))
    overall_status = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "overall_status": overall_status,
        "decision": "S8P_PHYSICAL_FEATURE_DATASET_READY" if overall_status == "PASS" else "DO_NOT_USE_S8P_DATASET",
        "rows": {"row_count": len(rows), "ok_count": len(ok_rows)},
        "manifest": _manifest_summary(manifest),
        "touchstone": _touchstone_summary(touchstone_records),
        "feature_label_recompute": _feature_recompute_summary(feature_recompute_records, args),
        "features": _feature_summary(ok_rows),
        "coverage": coverage,
        "coverage_artifacts": coverage_artifacts,
        "coverage_plot_errors": coverage_plot_errors,
        "checks": checks,
        "limitations": [
            "This audit does not run EMX, HFSS, or ADS.",
            "PASS does not prove EMX/HFSS agreement; it proves local dataset artifacts satisfy the configured .s8p physical-feature readiness contract.",
            "Physical-feature coverage figures are measured from real dataset rows; they do not fabricate missing labels or imply uniformity without the shown bin evidence.",
            "The random-sample EMX/HFSS/ADS comparison remains required before accepting the dataset for training.",
        ],
    }

    summary_path = out_dir / "s8p_physical_feature_dataset_audit_summary.json"
    report_path = out_dir / "s8p_physical_feature_dataset_audit_report.md"
    touchstone_csv = out_dir / "s8p_touchstone_checks.csv"
    feature_recompute_csv = out_dir / "s8p_feature_label_recompute_checks.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_touchstone_csv(touchstone_csv, touchstone_records)
    _write_feature_recompute_csv(feature_recompute_csv, feature_recompute_records)

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"touchstone_csv={touchstone_csv}")
    print(f"feature_recompute_csv={feature_recompute_csv}")
    for check in checks:
        print(f"{check['status']:4s} {check['name']}: {check['detail']}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--expected-count", type=int, default=500)
    parser.add_argument("--expected-ok-count", type=int)
    parser.add_argument("--require-all-ok", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--expected-port-mode", default="single_ended_shield_grounded")
    parser.add_argument("--expected-differential-port-pairs", default="1,4:5,6")
    parser.add_argument("--expected-touchstone-extension", default=".s8p")
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-touchstone-checks", type=int, default=500)
    parser.add_argument("--require-power-line-8port", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-differential-port-pairs", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--expected-power-line-bridge-width-um", type=float, default=10.0)
    parser.add_argument("--power-line-bridge-width-tolerance-um", type=float, default=1.0e-12)
    parser.add_argument("--expected-power-line-vertical-length-ratio", type=float, default=1.5)
    parser.add_argument("--power-line-vertical-length-ratio-tolerance", type=float, default=1.0e-12)
    parser.add_argument("--expected-power-line-bridge-y-policy", default="center")
    parser.add_argument("--expected-power-line-bridge-motion-axis", default="x_only")
    parser.add_argument("--expected-power-line-port-ground-reference", default="shield")
    parser.add_argument("--expected-power-line-port-map", default="P001,P002,P003,P004,P005,P006,P007,P008")
    parser.add_argument(
        "--expected-power-line-role-labels",
        default=(
            "primary_top=P001,left_power_top=P002,left_power_bottom=P003,primary_bottom=P004,"
            "secondary_bottom=P005,secondary_top=P006,right_power_top=P007,right_power_bottom=P008"
        ),
    )
    parser.add_argument("--expected-power-line-ground-frame-width-um", type=float, default=100.0)
    parser.add_argument("--power-line-ground-frame-width-tolerance-um", type=float, default=1.0e-9)
    parser.add_argument(
        "--expected-power-line-ground-frame-policy",
        default="power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
    )
    parser.add_argument("--min-l-nh", type=float, default=0.0)
    parser.add_argument("--min-q", type=float, default=0.0)
    parser.add_argument("--max-abs-k", type=float, default=1.05)
    parser.add_argument("--verify-feature-labels-from-touchstone", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--feature-label-target-ghz", type=float, default=15.0)
    parser.add_argument("--feature-label-frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--feature-label-relative-tolerance", type=float, default=1.0e-3)
    parser.add_argument("--feature-label-absolute-tolerance", type=float, default=1.0e-6)
    parser.add_argument(
        "--ground-unused-ports",
        action="store_true",
        help="Short S8P ports outside the selected differential pairs to ground before recomputing Lp/Ls/Q/K labels.",
    )
    parser.add_argument("--require-scalar-q-feature", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--scalar-q-definition", default="min", choices=Q_DEFINITIONS)
    parser.add_argument("--scalar-q-relative-tolerance", type=float, default=1.0e-9)
    parser.add_argument("--scalar-q-absolute-tolerance", type=float, default=1.0e-9)
    parser.add_argument("--coverage-feature-columns", help="Comma-separated Lp/Ls/Q/K columns for coverage figures; defaults to lp_nh_center,ls_nh_center,q_center,k_center")
    parser.add_argument("--coverage-bins", type=int, default=8)
    parser.add_argument("--min-coverage-finite-count", type=int)
    parser.add_argument("--min-coverage-occupied-1d-bins", type=int, default=1)
    parser.add_argument("--skip-coverage-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_json_error": True}


def _audit_touchstones(dataset_dir: Path, rows: list[dict[str, str]], args: argparse.Namespace) -> list[dict[str, Any]]:
    selected_rows = rows[: max(0, int(args.max_touchstone_checks))]
    records = []
    for row in selected_rows:
        raw_path = (row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
        path = _resolve(dataset_dir, raw_path) if raw_path else None
        record: dict[str, Any] = {
            "evaluation": row.get("evaluation") or row.get("cache_key"),
            "path": "" if path is None else str(path),
            "exists": False,
            "suffix": None,
            "num_ports": None,
            "freq_start_hz": None,
            "freq_stop_hz": None,
            "freq_points": None,
            "freq_step_hz": None,
            "status": "FAIL",
            "reason": None,
        }
        if path is None:
            record["reason"] = "missing touchstone_path"
            records.append(record)
            continue
        record["suffix"] = path.suffix.lower()
        if not path.is_file():
            record["reason"] = "file not found"
            records.append(record)
            continue
        record["exists"] = True
        try:
            result = load_touchstone(path)
        except Exception as exc:
            record["reason"] = f"load failed: {exc}"
            records.append(record)
            continue
        freqs = np.asarray(result.freqs_hz, dtype=float)
        record["num_ports"] = int(result.num_ports)
        record["freq_points"] = int(freqs.size)
        if freqs.size:
            record["freq_start_hz"] = float(freqs[0])
            record["freq_stop_hz"] = float(freqs[-1])
        if freqs.size > 1:
            diffs = np.diff(freqs)
            record["freq_step_hz"] = float(diffs[0])
            record["freq_step_span_hz"] = float(np.max(diffs) - np.min(diffs))
        reasons = []
        if path.suffix.lower() != str(args.expected_touchstone_extension).lower():
            reasons.append(f"suffix expected {args.expected_touchstone_extension}, got {path.suffix}")
        if int(result.num_ports) != int(args.expected_ports):
            reasons.append(f"ports expected {args.expected_ports}, got {result.num_ports}")
        reasons.extend(_frequency_reasons(freqs, args))
        record["status"] = "PASS" if not reasons else "FAIL"
        record["reason"] = "; ".join(reasons) if reasons else ""
        records.append(record)
    return records


def _frequency_reasons(freqs: np.ndarray, args: argparse.Namespace) -> list[str]:
    reasons = []
    tol = float(args.frequency_tolerance_hz)
    if int(freqs.size) != int(args.expected_frequency_points):
        reasons.append(f"frequency points expected {args.expected_frequency_points}, got {freqs.size}")
    if freqs.size:
        if abs(float(freqs[0]) - float(args.expected_frequency_start_ghz) * 1.0e9) > tol:
            reasons.append(f"frequency start expected {args.expected_frequency_start_ghz} GHz, got {float(freqs[0]) / 1e9:.12g} GHz")
        if abs(float(freqs[-1]) - float(args.expected_frequency_stop_ghz) * 1.0e9) > tol:
            reasons.append(f"frequency stop expected {args.expected_frequency_stop_ghz} GHz, got {float(freqs[-1]) / 1e9:.12g} GHz")
    if freqs.size > 1:
        diffs = np.diff(freqs)
        expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
        if abs(float(diffs[0]) - expected_step) > tol or float(np.max(diffs) - np.min(diffs)) > tol:
            reasons.append(
                f"frequency step expected {args.expected_frequency_step_ghz} GHz, "
                f"got first={float(diffs[0]) / 1e9:.12g} GHz span={float(np.max(diffs) - np.min(diffs)) / 1e9:.12g} GHz"
            )
    return reasons


def _recompute_feature_labels_from_touchstones(
    dataset_dir: Path,
    rows: list[dict[str, str]],
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    if not bool(args.verify_feature_labels_from_touchstone):
        return []
    selected_rows = rows[: max(0, int(args.max_touchstone_checks))]
    records: list[dict[str, Any]] = []
    port_pairs, pair_reason = _manifest_port_pairs_zero_based(manifest)
    for row in selected_rows:
        raw_path = (row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
        path = _resolve(dataset_dir, raw_path) if raw_path else None
        base: dict[str, Any] = {
            "evaluation": row.get("evaluation") or row.get("cache_key"),
            "path": "" if path is None else str(path),
            "port_pairs_zero_based": "" if port_pairs is None else json.dumps(port_pairs),
            "target_frequency_hz": None,
            "actual_frequency_hz": None,
            "metric": "",
            "csv_value": None,
            "recomputed_value": None,
            "absolute_error": None,
            "relative_error": None,
            "status": "FAIL",
            "reason": None,
        }
        if port_pairs is None:
            base["reason"] = pair_reason
            records.append(dict(base))
            continue
        if path is None:
            base["reason"] = "missing touchstone_path"
            records.append(dict(base))
            continue
        if not path.is_file():
            base["reason"] = "file not found"
            records.append(dict(base))
            continue
        try:
            result = load_touchstone(path)
            target_hz = _row_target_frequency_hz(row, manifest, args)
            values = _physical_feature_values_from_result(
                result,
                port_pairs,
                target_hz,
                ground_unused_ports=bool(args.ground_unused_ports),
            )
            values["q_center"] = _derive_scalar_q(
                float(values["qp_center"]),
                float(values["qs_center"]),
                str(args.scalar_q_definition),
            )
        except Exception as exc:  # noqa: BLE001
            base["reason"] = f"recompute failed: {type(exc).__name__}: {exc}"
            records.append(dict(base))
            continue

        actual_hz = float(values.pop("actual_frequency_hz"))
        frequency_error = abs(actual_hz - target_hz)
        for metric in RECOMPUTED_FEATURE_COLUMNS:
            record = dict(base)
            record["target_frequency_hz"] = float(target_hz)
            record["actual_frequency_hz"] = actual_hz
            record["metric"] = metric
            csv_value = _as_float(row.get(metric))
            recomputed = _as_float(values.get(metric))
            record["csv_value"] = csv_value
            record["recomputed_value"] = recomputed
            if frequency_error > float(args.feature_label_frequency_tolerance_hz):
                record["reason"] = (
                    f"nearest frequency differs from target by {frequency_error:.6g} Hz "
                    f"> {float(args.feature_label_frequency_tolerance_hz):.6g} Hz"
                )
                records.append(record)
                continue
            if csv_value is None:
                if metric == "m_nh_center":
                    record["status"] = "SKIP"
                    record["reason"] = "optional m_nh_center column absent"
                else:
                    record["reason"] = "missing CSV label"
                records.append(record)
                continue
            if recomputed is None or not math.isfinite(float(recomputed)):
                record["reason"] = "recomputed value is not finite"
                records.append(record)
                continue
            abs_error = abs(float(csv_value) - float(recomputed))
            rel_error = abs_error / max(abs(float(recomputed)), 1.0e-30)
            record["absolute_error"] = float(abs_error)
            record["relative_error"] = float(rel_error)
            passes = (
                abs_error <= float(args.feature_label_absolute_tolerance)
                or rel_error <= float(args.feature_label_relative_tolerance)
            )
            record["status"] = "PASS" if passes else "FAIL"
            record["reason"] = (
                ""
                if passes
                else (
                    f"abs_error={abs_error:.6g}, rel_error={rel_error:.6g}; "
                    f"limits abs<={float(args.feature_label_absolute_tolerance):.6g} "
                    f"or rel<={float(args.feature_label_relative_tolerance):.6g}"
                )
            )
            records.append(record)
    return records


def _manifest_port_pairs_zero_based(manifest: dict[str, Any]) -> tuple[tuple[tuple[int, int], tuple[int, int]] | None, str]:
    raw_pairs = manifest.get("differential_port_pairs")
    if not isinstance(raw_pairs, list) or len(raw_pairs) != 2:
        return None, f"manifest differential_port_pairs must contain two pairs, got {raw_pairs!r}"
    parsed: list[tuple[int, int]] = []
    for pair in raw_pairs:
        if isinstance(pair, dict):
            values = [
                pair.get("positive", pair.get("pos", pair.get("p"))),
                pair.get("negative", pair.get("neg", pair.get("n"))),
            ]
        elif isinstance(pair, (list, tuple)) and len(pair) == 2:
            values = [pair[0], pair[1]]
        else:
            return None, f"invalid differential port pair {pair!r}"
        try:
            parsed.append((int(values[0]), int(values[1])))
        except (TypeError, ValueError):
            return None, f"non-integer differential port pair {pair!r}"
    flat = [port for pair in parsed for port in pair]
    if len(set(flat)) != 4:
        return None, f"differential port pairs must use four distinct ports, got {raw_pairs!r}"
    if min(flat) >= 1:
        parsed = [(a - 1, b - 1) for a, b in parsed]
    if min(port for pair in parsed for port in pair) < 0:
        return None, f"differential port pairs cannot be negative after normalization, got {raw_pairs!r}"
    return (parsed[0], parsed[1]), ""


def _parse_port_pairs_zero_based(text: str) -> tuple[tuple[tuple[int, int], tuple[int, int]] | None, str]:
    try:
        groups = [group.strip() for group in str(text).split(":") if group.strip()]
        if len(groups) != 2:
            return None, f"expected two pairs separated by ':', got {text!r}"
        pairs: list[tuple[int, int]] = []
        for group in groups:
            values = [value.strip() for value in group.split(",") if value.strip()]
            if len(values) != 2:
                return None, f"expected two ports in pair {group!r}"
            pairs.append((int(values[0]), int(values[1])))
    except ValueError as exc:
        return None, f"non-integer port in {text!r}: {exc}"
    flat = [port for pair in pairs for port in pair]
    if len(set(flat)) != 4:
        return None, f"differential port pairs must use four distinct ports, got {text!r}"
    if min(flat) >= 1:
        pairs = [(a - 1, b - 1) for a, b in pairs]
    if min(port for pair in pairs for port in pair) < 0:
        return None, f"differential port pairs cannot be negative after normalization, got {text!r}"
    return (pairs[0], pairs[1]), ""


def _split_csv(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _split_assignments(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in _split_csv(text):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            out[key] = value
    return out


def _row_target_frequency_hz(row: dict[str, str], manifest: dict[str, Any], args: argparse.Namespace) -> float:
    for column in ("physical_feature_center_freq_hz", "target_frequency_used_hz"):
        value = _as_float(row.get(column))
        if value is not None and math.isfinite(value):
            return float(value)
    value_ghz = _as_float(row.get("target_frequency_used_ghz"))
    if value_ghz is not None and math.isfinite(value_ghz):
        return float(value_ghz) * 1.0e9
    target = manifest.get("target_frequency") if isinstance(manifest.get("target_frequency"), dict) else {}
    for key in ("f0_hz", "target_frequency_hz"):
        value = _as_float(target.get(key))
        if value is not None and math.isfinite(value):
            return float(value)
    return float(args.feature_label_target_ghz) * 1.0e9


def _physical_feature_values_from_result(
    result: Any,
    port_pairs: tuple[tuple[int, int], tuple[int, int]],
    target_hz: float,
    *,
    ground_unused_ports: bool = False,
) -> dict[str, float]:
    freqs = np.asarray(result.freqs_hz, dtype=float)
    if freqs.ndim != 1 or freqs.size == 0:
        raise ValueError("Touchstone has no frequency points")
    center_idx = int(np.argmin(np.abs(freqs - float(target_hz))))
    if ground_unused_ports:
        z_diff = multiport_s_to_grounded_differential_z(
            result.s_matrix,
            result.reference_impedance_ohm,
            port_pairs,
        )
    else:
        z_single = result.to_z_parameters()
        z_diff = multiport_single_ended_to_differential_z(z_single, port_pairs)
    omega = 2.0 * math.pi * float(freqs[center_idx])
    z0 = z_diff[center_idx]
    lp_h = float(np.imag(z0[0, 0]) / omega)
    ls_h = float(np.imag(z0[1, 1]) / omega)
    m_h = float(np.imag(z0[1, 0]) / omega)
    denom = math.sqrt(max(abs(lp_h * ls_h), 1.0e-30))
    return {
        "actual_frequency_hz": float(freqs[center_idx]),
        "lp_nh_center": lp_h * 1.0e9,
        "ls_nh_center": ls_h * 1.0e9,
        "m_nh_center": m_h * 1.0e9,
        "k_center": m_h / denom,
        "qp_center": _safe_div(float(np.imag(z0[0, 0])), float(np.real(z0[0, 0]))),
        "qs_center": _safe_div(float(np.imag(z0[1, 1])), float(np.real(z0[1, 1]))),
    }


def _safe_div(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if abs(float(denominator)) > 1.0e-12 else 0.0


def _build_checks(
    rows: list[dict[str, str]],
    ok_rows: list[dict[str, str]],
    manifest: dict[str, Any],
    touchstone_records: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    expected_ok = int(args.expected_ok_count) if args.expected_ok_count is not None else int(args.expected_count)
    arrays = _feature_arrays(ok_rows)
    checks = [
        _check(bool(rows), "dataset_rows.csv present", f"rows={len(rows)}"),
        _check(len(rows) == int(args.expected_count), "dataset row count", f"expected={args.expected_count} actual={len(rows)}"),
        _check(len(ok_rows) >= expected_ok, "ok row count", f"expected>={expected_ok} actual={len(ok_rows)}"),
        _check((not args.require_all_ok) or len(ok_rows) == len(rows), "all rows ok", f"rows={len(rows)} ok={len(ok_rows)}"),
        _check(str(manifest.get("port_mode")) == str(args.expected_port_mode), "manifest port mode", f"expected={args.expected_port_mode} actual={manifest.get('port_mode')}"),
        _check(_has_required_features(ok_rows), "physical feature columns", ",".join(REQUIRED_FEATURE_COLUMNS)),
    ]
    checks.extend(_scalar_q_consistency_checks(ok_rows, args))
    checks.extend(_manifest_differential_port_pair_checks(manifest, args))
    checks.extend(_manifest_power_line_8port_checks(manifest, args))
    if arrays:
        checks.extend(
            [
                _check(float(np.nanmin(arrays["lp_nh_center"])) > float(args.min_l_nh), "positive Lp", f"min={float(np.nanmin(arrays['lp_nh_center'])):.6g} nH"),
                _check(float(np.nanmin(arrays["ls_nh_center"])) > float(args.min_l_nh), "positive Ls", f"min={float(np.nanmin(arrays['ls_nh_center'])):.6g} nH"),
                _check(float(np.nanmin(arrays["q_center"])) > float(args.min_q), "positive scalar Q", f"min={float(np.nanmin(arrays['q_center'])):.6g}"),
                _check(float(np.nanmin(arrays["qp_center"])) > float(args.min_q), "positive Qp", f"min={float(np.nanmin(arrays['qp_center'])):.6g}"),
                _check(float(np.nanmin(arrays["qs_center"])) > float(args.min_q), "positive Qs", f"min={float(np.nanmin(arrays['qs_center'])):.6g}"),
                _check(float(np.nanmax(np.abs(arrays["k_center"]))) <= float(args.max_abs_k), "K magnitude", f"max_abs={float(np.nanmax(np.abs(arrays['k_center']))):.6g}"),
            ]
        )
    else:
        checks.append(_check(False, "finite physical features", "no complete finite feature rows"))

    discovered_count = len([row for row in ok_rows if (row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()])
    pass_touchstone = [item for item in touchstone_records if item["status"] == "PASS"]
    checks.extend(
        [
            _check(discovered_count >= expected_ok, "touchstone path coverage", f"expected>={expected_ok} actual={discovered_count}"),
            _check(bool(touchstone_records), "sampled touchstones checked", f"checked={len(touchstone_records)}"),
            _check(len(pass_touchstone) == len(touchstone_records), "sampled touchstones pass", f"pass={len(pass_touchstone)} checked={len(touchstone_records)}"),
        ]
    )
    return checks


def _manifest_differential_port_pair_checks(manifest: dict[str, Any], args: argparse.Namespace) -> list[dict[str, str]]:
    if not bool(args.require_differential_port_pairs):
        return []
    actual_pairs, actual_reason = _manifest_port_pairs_zero_based(manifest)
    expected_pairs, expected_reason = _parse_port_pairs_zero_based(str(args.expected_differential_port_pairs))
    return [
        _check(actual_pairs is not None, "manifest differential port pairs present and parseable", actual_reason or str(manifest.get("differential_port_pairs"))),
        _check(
            actual_pairs is not None and expected_pairs is not None and actual_pairs == expected_pairs,
            "manifest differential port pairs match approved contract",
            f"expected={args.expected_differential_port_pairs} actual={manifest.get('differential_port_pairs')} reason={expected_reason or actual_reason}",
        ),
    ]


def _manifest_power_line_8port_checks(manifest: dict[str, Any], args: argparse.Namespace) -> list[dict[str, str]]:
    if not bool(args.require_power_line_8port):
        return []
    power = manifest.get("power_line_8port")
    power_dict = power if isinstance(power, dict) else {}
    expected_port_map = _split_csv(str(args.expected_power_line_port_map))
    expected_role_labels = _split_assignments(str(args.expected_power_line_role_labels))
    actual_port_map = [str(item) for item in power_dict.get("port_map", [])] if isinstance(power_dict.get("port_map"), list) else []
    actual_role_raw = power_dict.get("role_labels")
    actual_role_labels = {str(key): str(value) for key, value in actual_role_raw.items()} if isinstance(actual_role_raw, dict) else {}
    bridge_width = _as_float(power_dict.get("bridge_width_um"))
    vertical_ratio = _as_float(power_dict.get("vertical_length_diameter_ratio"))
    ground_frame_width = _as_float(power_dict.get("ground_frame_width_um"))
    return [
        _check(bool(power_dict.get("enabled")), "manifest power_line_8port enabled", str(power)),
        _check(
            bridge_width is not None
            and abs(float(bridge_width) - float(args.expected_power_line_bridge_width_um)) <= float(args.power_line_bridge_width_tolerance_um),
            "manifest power_line_8port bridge width",
            f"expected={float(args.expected_power_line_bridge_width_um):.12g} um actual={power_dict.get('bridge_width_um')}",
        ),
        _check(
            vertical_ratio is not None
            and abs(float(vertical_ratio) - float(args.expected_power_line_vertical_length_ratio)) <= float(args.power_line_vertical_length_ratio_tolerance),
            "manifest power_line_8port vertical length ratio",
            f"expected={float(args.expected_power_line_vertical_length_ratio):.12g} actual={power_dict.get('vertical_length_diameter_ratio')}",
        ),
        _check(
            str(power_dict.get("bridge_y_policy")) == str(args.expected_power_line_bridge_y_policy),
            "manifest power_line_8port bridge y policy",
            f"expected={args.expected_power_line_bridge_y_policy} actual={power_dict.get('bridge_y_policy')}",
        ),
        _check(
            str(power_dict.get("bridge_motion_axis")) == str(args.expected_power_line_bridge_motion_axis),
            "manifest power_line_8port bridge motion axis",
            f"expected={args.expected_power_line_bridge_motion_axis} actual={power_dict.get('bridge_motion_axis')}",
        ),
        _check(
            str(power_dict.get("port_ground_reference")) == str(args.expected_power_line_port_ground_reference),
            "manifest power_line_8port ground reference",
            f"expected={args.expected_power_line_port_ground_reference} actual={power_dict.get('port_ground_reference')}",
        ),
        _check(
            actual_port_map == expected_port_map,
            "manifest power_line_8port port map",
            f"expected={expected_port_map} actual={actual_port_map}",
        ),
        _check(
            actual_role_labels == expected_role_labels,
            "manifest power_line_8port role labels",
            f"expected={expected_role_labels} actual={actual_role_labels}",
        ),
        _check(
            ground_frame_width is not None
            and abs(float(ground_frame_width) - float(args.expected_power_line_ground_frame_width_um)) <= float(args.power_line_ground_frame_width_tolerance_um),
            "manifest power_line_8port ground frame width",
            f"expected={float(args.expected_power_line_ground_frame_width_um):.12g} um actual={power_dict.get('ground_frame_width_um')}",
        ),
        _check(
            str(power_dict.get("ground_frame_policy")) == str(args.expected_power_line_ground_frame_policy),
            "manifest power_line_8port ground frame policy",
            f"expected={args.expected_power_line_ground_frame_policy} actual={power_dict.get('ground_frame_policy')}",
        ),
    ]


def _feature_arrays(rows: list[dict[str, str]]) -> dict[str, np.ndarray]:
    arrays = {}
    for name in REQUIRED_FEATURE_COLUMNS:
        values = [_as_float(row.get(name)) for row in rows]
        finite = [value for value in values if value is not None and math.isfinite(value)]
        if len(finite) != len(rows):
            return {}
        arrays[name] = np.asarray(finite, dtype=float)
    return arrays


def _feature_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    arrays = _feature_arrays(rows)
    return {
        "required_columns": list(REQUIRED_FEATURE_COLUMNS),
        "complete_finite_row_count": 0 if not arrays else len(next(iter(arrays.values()))),
        "metrics": {name: _range_summary(values) for name, values in arrays.items()},
    }


def _scalar_q_consistency_checks(rows: list[dict[str, str]], args: argparse.Namespace) -> list[dict[str, str]]:
    if not bool(args.require_scalar_q_feature):
        return [_check(True, "scalar Q q_center consistency", "disabled by --no-require-scalar-q-feature")]
    missing_q = 0
    invalid_inputs = 0
    mismatches = 0
    compared = 0
    max_abs_error = 0.0
    max_rel_error = 0.0
    examples = []
    for index, row in enumerate(rows):
        label = row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or f"row_{index}"
        q_value = _as_finite_float(row.get("q_center"))
        qp = _as_finite_float(row.get("qp_center"))
        qs = _as_finite_float(row.get("qs_center"))
        if q_value is None:
            missing_q += 1
            if len(examples) < 3:
                examples.append(f"{label}: missing/non-finite q_center")
            continue
        if qp is None or qs is None:
            invalid_inputs += 1
            if len(examples) < 3:
                examples.append(f"{label}: missing/non-finite Qp/Qs")
            continue
        try:
            expected = _derive_scalar_q(float(qp), float(qs), str(args.scalar_q_definition))
        except ValueError as exc:
            invalid_inputs += 1
            if len(examples) < 3:
                examples.append(f"{label}: {exc}")
            continue
        compared += 1
        abs_error = abs(float(q_value) - float(expected))
        rel_error = abs_error / max(abs(float(expected)), 1.0e-30)
        max_abs_error = max(max_abs_error, float(abs_error))
        max_rel_error = max(max_rel_error, float(rel_error))
        passes = (
            abs_error <= float(args.scalar_q_absolute_tolerance)
            or rel_error <= float(args.scalar_q_relative_tolerance)
        )
        if not passes:
            mismatches += 1
            if len(examples) < 3:
                examples.append(
                    f"{label}: q_center={float(q_value):.12g}, expected={float(expected):.12g}, "
                    f"abs={abs_error:.3g}, rel={rel_error:.3g}"
                )
    detail = (
        f"definition={args.scalar_q_definition}; rows={len(rows)} compared={compared}; "
        f"missing_q={missing_q}; invalid_inputs={invalid_inputs}; mismatches={mismatches}; "
        f"max_abs_error={max_abs_error:.6g}; max_rel_error={max_rel_error:.6g}; examples={examples}"
    )
    return [
        _check(missing_q == 0, "scalar Q q_center present and finite", detail),
        _check(invalid_inputs == 0 and mismatches == 0 and compared == len(rows), "scalar Q matches Qp/Qs definition", detail),
    ]


def _derive_scalar_q(qp: float, qs: float, definition: str) -> float:
    if definition == "min":
        return min(float(qp), float(qs))
    if definition == "mean":
        return 0.5 * (float(qp) + float(qs))
    if definition == "geometric_mean":
        if qp <= 0.0 or qs <= 0.0:
            raise ValueError("geometric_mean requires positive Qp/Qs")
        return math.sqrt(float(qp) * float(qs))
    if definition == "harmonic_mean":
        if qp <= 0.0 or qs <= 0.0:
            raise ValueError("harmonic_mean requires positive Qp/Qs")
        return 2.0 / (1.0 / float(qp) + 1.0 / float(qs))
    if definition == "primary":
        return float(qp)
    if definition == "secondary":
        return float(qs)
    raise ValueError(f"unsupported scalar Q definition: {definition}")


def _coverage_feature_columns(rows: list[dict[str, str]], raw: str | None, require_scalar_q: bool) -> list[str]:
    if raw:
        return [item.strip() for item in str(raw).split(",") if item.strip()]
    if require_scalar_q:
        return ["lp_nh_center", "ls_nh_center", "q_center", "k_center"]
    if rows and any(_as_float(row.get("q_center")) is not None for row in rows):
        return ["lp_nh_center", "ls_nh_center", "q_center", "k_center"]
    return ["lp_nh_center", "ls_nh_center", "qp_center", "qs_center", "k_center"]


def _feature_coverage(rows: list[dict[str, str]], columns: list[str], bins: int) -> dict[str, Any]:
    arrays: dict[str, np.ndarray] = {}
    for column in columns:
        values = [_as_float(row.get(column)) for row in rows]
        finite = np.asarray([float(value) for value in values if value is not None and math.isfinite(float(value))], dtype=float)
        arrays[column] = finite
    metrics = {}
    for column, values in arrays.items():
        metrics[column] = _coverage_column_summary(values, bins)
    pair_keys = _coverage_pair_keys(columns)
    pairs = {}
    for x_col, y_col in pair_keys:
        pairs[f"{x_col}__vs__{y_col}"] = _coverage_pair_summary(rows, x_col, y_col, bins)
    finite_counts = [item["finite_count"] for item in metrics.values()]
    return {
        "feature_columns": columns,
        "bins": int(bins),
        "finite_count_min": int(min(finite_counts)) if finite_counts else 0,
        "metrics": metrics,
        "pairs": pairs,
        "interpretation": (
            "Coverage is computed from completed simulator-derived physical-feature labels. "
            "High occupied-bin counts and broad spans are useful for inverse training; this audit does not claim uniformity unless the measured bins support it."
        ),
    }


def _coverage_column_summary(values: np.ndarray, bins: int) -> dict[str, Any]:
    if values.size == 0:
        return {
            "finite_count": 0,
            "min": None,
            "max": None,
            "span": None,
            "mean": None,
            "std": None,
            "occupied_1d_bins": 0,
            "bin_count": int(bins),
            "bin_counts": [],
        }
    lo = float(np.min(values))
    hi = float(np.max(values))
    if hi <= lo:
        counts = np.asarray([int(values.size)] + [0] * max(0, int(bins) - 1), dtype=int)
    else:
        counts, _edges = np.histogram(values, bins=int(bins), range=(lo, hi))
    return {
        "finite_count": int(values.size),
        "min": lo,
        "max": hi,
        "span": float(hi - lo),
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "occupied_1d_bins": int(np.count_nonzero(counts)),
        "bin_count": int(bins),
        "bin_counts": [int(item) for item in counts],
    }


def _coverage_pair_keys(columns: list[str]) -> list[tuple[str, str]]:
    pairs = []
    if "lp_nh_center" in columns and "ls_nh_center" in columns:
        pairs.append(("lp_nh_center", "ls_nh_center"))
    q_col = "q_center" if "q_center" in columns else ("qp_center" if "qp_center" in columns else "")
    if q_col and "k_center" in columns:
        pairs.append((q_col, "k_center"))
    if "lp_nh_center" in columns and "k_center" in columns:
        pairs.append(("lp_nh_center", "k_center"))
    if "ls_nh_center" in columns and q_col:
        pairs.append(("ls_nh_center", q_col))
    return pairs


def _coverage_pair_summary(rows: list[dict[str, str]], x_col: str, y_col: str, bins: int) -> dict[str, Any]:
    points = []
    for row in rows:
        x = _as_float(row.get(x_col))
        y = _as_float(row.get(y_col))
        if x is not None and y is not None:
            points.append((float(x), float(y)))
    if not points:
        return {"finite_pair_count": 0, "occupied_2d_bins": 0, "total_2d_bins": int(bins) ** 2, "occupied_2d_bin_fraction": 0.0}
    x_values = np.asarray([point[0] for point in points], dtype=float)
    y_values = np.asarray([point[1] for point in points], dtype=float)
    x_min, x_max = float(np.min(x_values)), float(np.max(x_values))
    y_min, y_max = float(np.min(y_values)), float(np.max(y_values))
    if x_max <= x_min or y_max <= y_min:
        occupied = 1
        counts = np.zeros((int(bins), int(bins)), dtype=int)
        counts[0, 0] = len(points)
    else:
        counts, _x_edges, _y_edges = np.histogram2d(x_values, y_values, bins=int(bins), range=((x_min, x_max), (y_min, y_max)))
        occupied = int(np.count_nonzero(counts))
    total = int(bins) ** 2
    return {
        "x_column": x_col,
        "y_column": y_col,
        "finite_pair_count": int(len(points)),
        "occupied_2d_bins": int(occupied),
        "total_2d_bins": int(total),
        "occupied_2d_bin_fraction": float(occupied / max(1, total)),
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
    }


def _write_coverage_plots(
    rows: list[dict[str, str]],
    columns: list[str],
    coverage: dict[str, Any],
    out_dir: Path,
    args: argparse.Namespace,
) -> tuple[dict[str, str], list[str]]:
    if args.skip_coverage_plots:
        return {}, []
    if plt is None:
        return {}, [f"matplotlib unavailable: {MATPLOTLIB_IMPORT_ERROR}"]
    errors: list[str] = []
    artifacts: dict[str, str] = {}
    plot_specs = [
        ("marginal_histograms", out_dir / "s8p_physical_feature_marginal_histograms.png", _plot_feature_marginals),
        ("pairwise_scatter", out_dir / "s8p_physical_feature_pairwise_scatter.png", _plot_feature_pairwise_scatter),
        ("pair_heatmaps", out_dir / "s8p_physical_feature_pair_heatmaps.png", _plot_feature_pair_heatmaps),
    ]
    for key, path, plotter in plot_specs:
        try:
            ok = plotter(rows, columns, coverage, path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{key}: {type(exc).__name__}: {exc}")
            continue
        if ok:
            artifacts[key] = str(path)
    return artifacts, errors


def _plot_feature_marginals(rows: list[dict[str, str]], columns: list[str], coverage: dict[str, Any], path: Path) -> bool:
    metrics = coverage.get("metrics") or {}
    present = [column for column in columns if int((metrics.get(column) or {}).get("finite_count") or 0) > 0]
    if not present:
        return False
    ncols = min(3, len(present))
    nrows = int(math.ceil(len(present) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.4 * ncols, 3.2 * nrows), squeeze=False)
    for ax, column in zip(axes.flat, present):
        values = _column_values(rows, column)
        item = metrics[column]
        bins = max(1, int(item["bin_count"]))
        ax.hist(values, bins=bins, color="#2563EB", alpha=0.72, edgecolor="white")
        ax.set_title(_feature_label(column))
        ax.set_xlabel(column)
        ax.set_ylabel("count")
        ax.grid(True, axis="y", linestyle=":", color="#D1D5DB")
        ax.text(
            0.02,
            0.96,
            f"span={float(item['span'] or 0):.3g}\noccupied={item['occupied_1d_bins']}/{item['bin_count']}",
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=8,
            color="#111827",
        )
    for ax in axes.flat[len(present) :]:
        ax.axis("off")
    fig.suptitle("S8P physical-feature marginal coverage", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return True


def _plot_feature_pairwise_scatter(rows: list[dict[str, str]], columns: list[str], coverage: dict[str, Any], path: Path) -> bool:
    present = [column for column in columns if _column_values(rows, column).size > 0]
    if len(present) < 2:
        return False
    n = len(present)
    fig, axes = plt.subplots(n, n, figsize=(2.35 * n, 2.35 * n), squeeze=False)
    for i, y_col in enumerate(present):
        for j, x_col in enumerate(present):
            ax = axes[i, j]
            if i == j:
                ax.hist(_column_values(rows, x_col), bins=int(coverage["bins"]), color="#60A5FA", edgecolor="white")
            else:
                x, y = _paired_values(rows, x_col, y_col)
                ax.scatter(x, y, s=12, alpha=0.62, color="#1D4ED8", linewidths=0)
            if i == n - 1:
                ax.set_xlabel(_short_feature_label(x_col), fontsize=7)
            else:
                ax.set_xticklabels([])
            if j == 0:
                ax.set_ylabel(_short_feature_label(y_col), fontsize=7)
            else:
                ax.set_yticklabels([])
            ax.grid(True, linestyle=":", linewidth=0.5, color="#E5E7EB")
    fig.suptitle("S8P physical-feature pairwise coverage", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return True


def _plot_feature_pair_heatmaps(rows: list[dict[str, str]], columns: list[str], coverage: dict[str, Any], path: Path) -> bool:
    pairs = list((coverage.get("pairs") or {}).values())
    pairs = [item for item in pairs if int(item.get("finite_pair_count") or 0) > 0]
    if not pairs:
        return False
    ncols = min(2, len(pairs))
    nrows = int(math.ceil(len(pairs) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(5.6 * ncols, 4.6 * nrows), squeeze=False)
    for ax, pair in zip(axes.flat, pairs):
        x_col = pair["x_column"]
        y_col = pair["y_column"]
        x, y = _paired_values(rows, x_col, y_col)
        if x.size == 0 or y.size == 0:
            ax.axis("off")
            continue
        bins = int(coverage["bins"])
        hist, x_edges, y_edges = np.histogram2d(x, y, bins=bins)
        im = ax.imshow(hist.T, origin="lower", aspect="auto", cmap="YlGnBu")
        ax.set_title(f"{_feature_label(x_col)} vs {_feature_label(y_col)}\noccupied {pair['occupied_2d_bins']}/{pair['total_2d_bins']}")
        ax.set_xlabel(_short_feature_label(x_col))
        ax.set_ylabel(_short_feature_label(y_col))
        ax.set_xticks([0, bins - 1], [f"{x_edges[0]:.3g}", f"{x_edges[-1]:.3g}"])
        ax.set_yticks([0, bins - 1], [f"{y_edges[0]:.3g}", f"{y_edges[-1]:.3g}"])
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="count")
    for ax in axes.flat[len(pairs) :]:
        ax.axis("off")
    fig.suptitle("S8P physical-feature 2D bin coverage", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return True


def _coverage_checks(
    coverage: dict[str, Any],
    artifacts: dict[str, str],
    plot_errors: list[str],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    required_count = args.min_coverage_finite_count
    if required_count is None:
        required_count = int(args.expected_ok_count) if args.expected_ok_count is not None else 1
    metrics = coverage.get("metrics") or {}
    occupied = [int(item.get("occupied_1d_bins") or 0) for item in metrics.values()]
    return [
        _check(int(coverage.get("finite_count_min") or 0) >= int(required_count), "physical-feature coverage finite rows", f"min={coverage.get('finite_count_min')} required>={required_count}"),
        _check(bool(metrics), "physical-feature coverage metrics present", ",".join(coverage.get("feature_columns") or [])),
        _check(all(value >= int(args.min_coverage_occupied_1d_bins) for value in occupied), "physical-feature occupied 1D bins", f"occupied={occupied} required>={args.min_coverage_occupied_1d_bins}"),
        _check(bool(args.skip_coverage_plots) or len(artifacts) >= 3, "physical-feature coverage figures generated", f"artifacts={artifacts}; errors={plot_errors}"),
    ]


def _column_values(rows: list[dict[str, str]], column: str) -> np.ndarray:
    values = [_as_float(row.get(column)) for row in rows]
    return np.asarray([float(value) for value in values if value is not None and math.isfinite(float(value))], dtype=float)


def _paired_values(rows: list[dict[str, str]], x_col: str, y_col: str) -> tuple[np.ndarray, np.ndarray]:
    xs = []
    ys = []
    for row in rows:
        x = _as_float(row.get(x_col))
        y = _as_float(row.get(y_col))
        if x is not None and y is not None:
            xs.append(float(x))
            ys.append(float(y))
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


def _feature_label(column: str) -> str:
    labels = {
        "lp_nh_center": "Lp at center frequency (nH)",
        "ls_nh_center": "Ls at center frequency (nH)",
        "qp_center": "Qp at center frequency",
        "qs_center": "Qs at center frequency",
        "q_center": "Scalar Q at center frequency",
        "k_center": "K at center frequency",
    }
    return labels.get(column, column)


def _short_feature_label(column: str) -> str:
    labels = {
        "lp_nh_center": "Lp",
        "ls_nh_center": "Ls",
        "qp_center": "Qp",
        "qs_center": "Qs",
        "q_center": "Q",
        "k_center": "K",
    }
    return labels.get(column, column)


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "present": bool(manifest),
        "port_mode": manifest.get("port_mode"),
        "cadence_pin_purpose": manifest.get("cadence_pin_purpose"),
        "differential_port_pairs": manifest.get("differential_port_pairs"),
        "power_line_8port": manifest.get("power_line_8port"),
        "requested_count": manifest.get("requested_count"),
        "ok_count": manifest.get("ok_count"),
        "fail_count": manifest.get("fail_count"),
    }


def _touchstone_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "checked_count": len(records),
        "pass_count": sum(1 for item in records if item["status"] == "PASS"),
        "fail_count": sum(1 for item in records if item["status"] == "FAIL"),
        "failures": [item for item in records if item["status"] == "FAIL"][:20],
    }


def _feature_recompute_checks(records: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, str]]:
    if not bool(args.verify_feature_labels_from_touchstone):
        return [_check(True, "feature labels recomputed from sampled touchstones", "disabled by --no-verify-feature-labels-from-touchstone")]
    pass_records = [item for item in records if item.get("status") == "PASS"]
    fail_records = [item for item in records if item.get("status") == "FAIL"]
    metric_records = [item for item in records if item.get("metric") in REQUIRED_FEATURE_COLUMNS]
    checked_evaluations = {str(item.get("evaluation")) for item in metric_records if item.get("status") in {"PASS", "FAIL"}}
    return [
        _check(bool(records), "feature labels recomputed from sampled touchstones", f"records={len(records)}"),
        _check(bool(metric_records), "required feature labels selected for recompute", f"records={len(metric_records)}"),
        _check(not fail_records, "feature labels match sampled touchstones", f"pass={len(pass_records)} fail={len(fail_records)} checked_samples={len(checked_evaluations)}"),
    ]


def _feature_recompute_summary(records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    enabled = bool(args.verify_feature_labels_from_touchstone)
    metric_records = [item for item in records if item.get("metric")]
    fail_records = [item for item in records if item.get("status") == "FAIL"]
    pass_records = [item for item in records if item.get("status") == "PASS"]
    checked_evaluations = sorted({str(item.get("evaluation")) for item in metric_records if item.get("evaluation") is not None})
    finite_abs_errors = [
        float(item["absolute_error"])
        for item in metric_records
        if item.get("absolute_error") is not None and math.isfinite(float(item["absolute_error"]))
    ]
    finite_rel_errors = [
        float(item["relative_error"])
        for item in metric_records
        if item.get("relative_error") is not None and math.isfinite(float(item["relative_error"]))
    ]
    return {
        "enabled": enabled,
        "record_count": len(records),
        "metric_record_count": len(metric_records),
        "sample_count": len(checked_evaluations),
        "sampled_evaluations": checked_evaluations[:50],
        "pass_count": len(pass_records),
        "fail_count": len(fail_records),
        "skip_count": sum(1 for item in records if item.get("status") == "SKIP"),
        "max_absolute_error": max(finite_abs_errors) if finite_abs_errors else None,
        "max_relative_error": max(finite_rel_errors) if finite_rel_errors else None,
        "relative_tolerance": float(args.feature_label_relative_tolerance),
        "absolute_tolerance": float(args.feature_label_absolute_tolerance),
        "target_frequency_fallback_ghz": float(args.feature_label_target_ghz),
        "failures": fail_records[:20],
    }


def _has_required_features(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    return all(all(row.get(name) not in (None, "") for name in REQUIRED_FEATURE_COLUMNS) for row in rows)


def _write_touchstone_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fieldnames = [
        "evaluation",
        "path",
        "exists",
        "suffix",
        "num_ports",
        "freq_start_hz",
        "freq_stop_hz",
        "freq_points",
        "freq_step_hz",
        "freq_step_span_hz",
        "status",
        "reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def _write_feature_recompute_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fieldnames = [
        "evaluation",
        "path",
        "port_pairs_zero_based",
        "target_frequency_hz",
        "actual_frequency_hz",
        "metric",
        "csv_value",
        "recomputed_value",
        "absolute_error",
        "relative_error",
        "status",
        "reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# S8P Physical Feature Dataset Audit",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Rows: `{summary['rows']['row_count']}` total, `{summary['rows']['ok_count']}` ok",
        f"- Touchstones checked: `{summary['touchstone']['checked_count']}`",
        f"- Feature-label recompute records: `{summary['feature_label_recompute']['record_count']}`",
        f"- Coverage columns: `{', '.join(summary['coverage']['feature_columns'])}`",
        "",
        "## Checks",
    ]
    for check in summary["checks"]:
        lines.append(f"- `{check['status']}` {check['name']}: {check['detail']}")
    lines.extend(["", "## Physical-Feature Coverage"])
    metrics = (summary.get("coverage") or {}).get("metrics") or {}
    lines.extend(["", "| Feature | Count | Min | Max | Span | Occupied bins |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for column, item in metrics.items():
        lines.append(
            f"| `{column}` | {item.get('finite_count', 0)} | {_fmt(item.get('min'))} | {_fmt(item.get('max'))} | "
            f"{_fmt(item.get('span'))} | {item.get('occupied_1d_bins', 0)}/{item.get('bin_count', 0)} |"
        )
    pairs = (summary.get("coverage") or {}).get("pairs") or {}
    if pairs:
        lines.extend(["", "| Pair | Count | Occupied 2D bins | Fraction |", "| --- | ---: | ---: | ---: |"])
        for name, item in pairs.items():
            lines.append(
                f"| `{name}` | {item.get('finite_pair_count', 0)} | {item.get('occupied_2d_bins', 0)}/{item.get('total_2d_bins', 0)} | "
                f"{_fmt(item.get('occupied_2d_bin_fraction'))} |"
            )
    artifacts = summary.get("coverage_artifacts") or {}
    if artifacts:
        lines.extend(["", "Coverage figures:"])
        for name, path in artifacts.items():
            lines.append(f"- `{name}`: `{path}`")
    recompute = summary.get("feature_label_recompute") or {}
    lines.extend(
        [
            "",
            "## Feature-Label Recompute",
            "",
            f"- Enabled: `{recompute.get('enabled')}`",
            f"- Samples: `{recompute.get('sample_count')}`",
            f"- Pass/fail/skip records: `{recompute.get('pass_count')}` / `{recompute.get('fail_count')}` / `{recompute.get('skip_count')}`",
            f"- Max absolute error: `{_fmt(recompute.get('max_absolute_error'))}`",
            f"- Max relative error: `{_fmt(recompute.get('max_relative_error'))}`",
        ]
    )
    if recompute.get("failures"):
        lines.extend(["", "| Evaluation | Metric | CSV | Recomputed | Abs err | Rel err | Reason |", "| --- | --- | ---: | ---: | ---: | ---: | --- |"])
        for item in recompute["failures"][:10]:
            lines.append(
                f"| `{item.get('evaluation')}` | `{item.get('metric')}` | {_fmt(item.get('csv_value'))} | "
                f"{_fmt(item.get('recomputed_value'))} | {_fmt(item.get('absolute_error'))} | "
                f"{_fmt(item.get('relative_error'))} | {item.get('reason') or ''} |"
            )
    if summary.get("coverage_plot_errors"):
        lines.extend(["", "Coverage plot errors:"])
        lines.extend(f"- {item}" for item in summary["coverage_plot_errors"])
    lines.extend(["", "## Limitations"])
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.append("")
    return "\n".join(lines)


def _check(condition: bool, name: str, detail: object) -> dict[str, str]:
    return {"name": name, "status": "PASS" if condition else "FAIL", "detail": str(detail)}


def _range_summary(values: np.ndarray) -> dict[str, float | None]:
    if values.size == 0:
        return {"min": None, "max": None, "mean": None, "std": None}
    return {
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.4g}"
    except (TypeError, ValueError):
        return str(value)


def _resolve(root: Path, path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _as_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_finite_float(value: object) -> float | None:
    out = _as_float(value)
    return out if out is not None and math.isfinite(float(out)) else None


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass", "ok"}


if __name__ == "__main__":
    raise SystemExit(main())
