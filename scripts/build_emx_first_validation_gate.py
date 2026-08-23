#!/usr/bin/env python3
"""Build the first-pass gate for accepting an EMX S4P as the ADS reference.

This is intentionally stricter than a generic Touchstone preflight. A file can
be readable, passive, reciprocal, and still be the wrong transformer case. This
gate therefore combines file/matrix sanity with a target-frequency ADS photo
anchor and a port-pair sensitivity check.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for item in (REPO_ROOT, SCRIPT_DIR):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from audit_ads_photo_reference_alignment import REFERENCE_METRICS, _metric_check  # noqa: E402
from audit_touchstone_transformer import _differential_z_checks, _differential_z_from_s, _differential_z_quality  # noqa: E402
from build_photo_matched_hfss_reference_evidence import parse_touchstone_metadata  # noqa: E402
from plot_emx_hfss_ads_style_metrics import DEFAULT_PACKAGE_DIR, _extract_metric_curves  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402
from scan_s4p_ads_photo_reference_candidates import _source_kind  # noqa: E402


DEFAULT_EMX = DEFAULT_PACKAGE_DIR / "ec6698dfc575950b_EMX_reference_NARROWBAND_13p5_16p5GHz.s4p"
METRIC_ORDER = (
    ("lp_nh", "Lp", "nH", "#2563eb"),
    ("ls_nh", "Ls", "nH", "#dc2626"),
    ("k", "K", "", "#c2410c"),
    ("qp", "Qp", "", "#3348a3"),
    ("qs", "Qs", "", "#be123c"),
    ("cm_single_primary_ff", "Cm", "fF", "#047857"),
)
DIFFERENTIAL_Z_REQUIRED_CHECK_NAMES = (
    "differential Z finiteness",
    "differential Z reciprocity",
    "differential Z positive-realness",
)


@dataclass(frozen=True)
class GateCheck:
    status: str
    name: str
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx-s4p", default=str(DEFAULT_EMX))
    parser.add_argument("--out-dir", default=str(DEFAULT_PACKAGE_DIR / "emx_first_validation_gate_20260613"))
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--photo-max-percent-error", type=float, default=5.0)
    parser.add_argument("--required-sweep-start-ghz", type=float, default=5.0)
    parser.add_argument("--required-sweep-stop-ghz", type=float, default=50.0)
    parser.add_argument("--required-sweep-step-ghz", type=float, default=0.1)
    parser.add_argument("--required-sweep-points", type=int, default=451)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-reciprocity-error", type=float, default=1.0e-6)
    parser.add_argument("--max-passivity-sigma", type=float, default=1.001)
    parser.add_argument("--max-differential-z-reciprocity-error-ohm", type=float, default=1.0e-6)
    parser.add_argument("--max-differential-z-reciprocity-relative-error", type=float, default=1.0e-6)
    parser.add_argument("--min-differential-z-real-eigenvalue-ohm", type=float, default=-1.0e-9)
    parser.add_argument("--min-differential-self-resistance-ohm", type=float, default=0.0)
    parser.add_argument("--min-target-inductance-nh", type=float, default=0.05)
    parser.add_argument("--min-target-q", type=float, default=1.0)
    parser.add_argument("--min-target-abs-k", type=float, default=0.05)
    parser.add_argument("--max-target-abs-k", type=float, default=0.98)
    parser.add_argument("--physical-window-start-ghz", type=float, default=5.0)
    parser.add_argument("--physical-window-stop-ghz", type=float, default=30.0)
    parser.add_argument(
        "--min-window-abs-k",
        type=float,
        help="Minimum abs(K) required throughout the physical metric window; defaults to --min-target-abs-k",
    )
    parser.add_argument("--shape-window-start-ghz", type=float, default=5.0)
    parser.add_argument("--shape-window-stop-ghz", type=float, default=30.0)
    parser.add_argument("--max-shape-spike-ratio", type=float, default=4.0)
    parser.add_argument("--max-shape-relative-step", type=float, default=0.25)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    emx_path = Path(args.emx_s4p).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "emx_first_validation_gate_summary.json"
    report_path = out_dir / "emx_first_validation_gate_report.md"
    metrics_csv_path = out_dir / "emx_first_validation_gate_metrics.csv"
    pair_csv_path = out_dir / "emx_first_validation_gate_port_pair_sensitivity.csv"
    metrics_plot_path = out_dir / "emx_first_validation_gate_ads_style_metrics.png"
    core_metrics_plot_path = out_dir / "emx_first_validation_gate_core_metrics.png"
    pair_plot_path = out_dir / "emx_first_validation_gate_port_pair_sensitivity.png"

    checks: list[GateCheck] = []
    errors: list[str] = []
    metadata: dict[str, Any] = {}
    matrix_quality: dict[str, Any] = {}
    differential_z_quality: dict[str, Any] = {}
    source_kind = _source_kind(emx_path)
    target: dict[str, Any] = {}
    freq_summary: dict[str, Any] = {}
    pair_records: list[dict[str, Any]] = []

    try:
        metadata = parse_touchstone_metadata(emx_path)
        touchstone = load_touchstone(emx_path)
        s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
        freqs_hz = np.asarray(touchstone.freqs_hz, dtype=float)
        freq_summary = _frequency_summary(freqs_hz)
        checks.extend(_source_checks(source_kind, metadata, emx_path))
        checks.extend(_matrix_checks(s_matrix, freqs_hz, args))
        checks.extend(_frequency_checks(freqs_hz, args))
        z_diff = _differential_z_from_s(s_matrix, touchstone.reference_impedance_ohm, args.port_pairs)
        differential_z_quality = _differential_z_quality(z_diff)
        checks.extend(_as_gate_checks(_differential_z_checks(differential_z_quality, args)))

        curves = _extract_metric_curves("EMX candidate", emx_path, args.port_pairs)
        target = _target_record(curves, args)
        checks.extend(_target_photo_checks(target))
        checks.extend(_target_transformer_sanity_checks(target, args))
        checks.extend(_physical_window_checks(curves, args))
        checks.extend(_shape_window_checks(curves, args))
        matrix_quality = _matrix_quality(s_matrix)
        _write_metrics_csv(metrics_csv_path, curves)
        _write_metric_plot(metrics_plot_path, curves, target, args)
        _write_core_metric_plot(core_metrics_plot_path, curves, target, args)

        pair_records = _port_pair_records(emx_path, args)
        _write_pair_csv(pair_csv_path, pair_records)
        _write_pair_plot(pair_plot_path, pair_records, args)
        checks.extend(_port_pair_gate_checks(pair_records, args.port_pairs))
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        checks.append(GateCheck("FAIL", "EMX gate execution", errors[-1]))

    status_counts = _status_counts(checks)
    overall_status = "PASS" if status_counts.get("FAIL", 0) == 0 else "FAIL"
    decision = (
        "ACCEPT_AS_GOLDEN_EMX_REFERENCE"
        if overall_status == "PASS"
        else "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE"
    )
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "emx_touchstone": str(emx_path),
        "source_kind_from_path": source_kind,
        "port_pairs": args.port_pairs,
        "target_ghz": float(args.target_ghz),
        "photo_reference_tolerance_percent": float(args.photo_max_percent_error),
        "required_final_sweep_ghz": {
            "start": float(args.required_sweep_start_ghz),
            "stop": float(args.required_sweep_stop_ghz),
            "step": float(args.required_sweep_step_ghz),
            "points": int(args.required_sweep_points),
        },
        "frequency_ghz": freq_summary,
        "metadata": metadata,
        "matrix_quality": matrix_quality,
        "differential_z_quality": differential_z_quality,
        "target_record": target,
        "physical_curve_gate": {
            "physical_window_start_ghz": float(args.physical_window_start_ghz),
            "physical_window_stop_ghz": float(args.physical_window_stop_ghz),
            "shape_window_start_ghz": float(args.shape_window_start_ghz),
            "shape_window_stop_ghz": float(args.shape_window_stop_ghz),
            "min_target_inductance_nh": float(args.min_target_inductance_nh),
            "min_target_q": float(args.min_target_q),
            "min_target_abs_k": float(args.min_target_abs_k),
            "min_window_abs_k": args.min_window_abs_k,
            "max_target_abs_k": float(args.max_target_abs_k),
            "max_shape_spike_ratio": float(args.max_shape_spike_ratio),
            "max_shape_relative_step": float(args.max_shape_relative_step),
            "max_differential_z_reciprocity_error_ohm": float(args.max_differential_z_reciprocity_error_ohm),
            "max_differential_z_reciprocity_relative_error": float(args.max_differential_z_reciprocity_relative_error),
            "min_differential_z_real_eigenvalue_ohm": float(args.min_differential_z_real_eigenvalue_ohm),
            "min_differential_self_resistance_ohm": float(args.min_differential_self_resistance_ohm),
        },
        "checks": [check.__dict__ for check in checks],
        "status_counts": status_counts,
        "port_pair_sensitivity": {
            "best": pair_records[0] if pair_records else None,
            "default": next((row for row in pair_records if row["port_pairs"] == args.port_pairs), None),
            "pass_count": sum(1 for row in pair_records if row["overall_status"] == "PASS"),
            "rows": pair_records,
        },
        "artifacts": {
            "report": str(report_path),
            "summary": str(summary_path),
            "metrics_csv": str(metrics_csv_path) if metrics_csv_path.exists() else None,
            "port_pair_csv": str(pair_csv_path) if pair_csv_path.exists() else None,
            "metrics_plot": str(metrics_plot_path) if metrics_plot_path.exists() else None,
            "core_metrics_plot": str(core_metrics_plot_path) if core_metrics_plot_path.exists() else None,
            "port_pair_plot": str(pair_plot_path) if pair_plot_path.exists() else None,
        },
        "method_notes": [
            "PASS requires more than a readable S4P: it must be EMX-labeled, passive/reciprocal, cover the requested final sweep, match the ADS photo anchor at 15 GHz, and pass the approved/default port-pair gate.",
            "The ADS-equivalent extraction converts the 4-port single-ended S matrix to Z, then forms the differential two-port with primary=1,2 and secondary=3,4 before extracting Lp/Ls/M/K/Qp/Qs.",
            "The ADS no-extrapolation plot grid check verifies that every requested 5-50 GHz / 0.1 GHz plotting point is present in the Touchstone data, so ADS marker curves cannot be produced by extrapolation outside the EMX sweep.",
            "The differential Z gate separately verifies Zdiff finiteness, reciprocity, and positive-realness before the Lp/Ls/Q/K curves are trusted.",
            "The basic numeric physics sanity check only confirms finite, plausibly bounded L/Q/K values; it is not a golden-reference acceptance by itself.",
            "The physical metric window and smooth transformer metric window check whether the ADS-equivalent Lp/Ls/Qp/Qs/K curves look like a usable coupled transformer over the configured low-to-mid band; they complement, but do not replace, the ADS photo anchor.",
            "Port-pair sensitivity is reported to avoid mistaking a wrong source file for a simple K polarity or port-order issue.",
            "A FAIL here blocks downstream HFSS-vs-EMX comparison for this source; HFSS modeling should continue only after a passing EMX reference is recovered or regenerated.",
        ],
        "errors": errors,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if metrics_plot_path.exists():
        print(f"metrics_plot={metrics_plot_path}")
    if core_metrics_plot_path.exists():
        print(f"core_metrics_plot={core_metrics_plot_path}")
    if pair_plot_path.exists():
        print(f"port_pair_plot={pair_plot_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _source_checks(source_kind: str, metadata: dict[str, Any], path: Path) -> list[GateCheck]:
    checks: list[GateCheck] = []
    if source_kind == "EMX":
        checks.append(GateCheck("PASS", "source identity", f"source_kind={source_kind}"))
    else:
        checks.append(GateCheck("FAIL", "source identity", f"expected EMX source, got {source_kind}; path={path}"))
    haystack = json.dumps(metadata, ensure_ascii=False).lower()
    if "hfss" in haystack:
        checks.append(GateCheck("FAIL", "source provenance header", "header/comment metadata declares HFSS"))
    else:
        checks.append(GateCheck("PASS", "source provenance header", "no HFSS declaration found in comments"))
    return checks


def _as_gate_checks(checks: list[Any]) -> list[GateCheck]:
    converted = [GateCheck(str(check.status), str(check.name), str(check.detail)) for check in checks]
    by_name = {check.name: check for check in converted}
    missing = [name for name in DIFFERENTIAL_Z_REQUIRED_CHECK_NAMES if name not in by_name]
    if missing:
        converted.append(GateCheck("FAIL", "differential Z required check names", f"missing={missing}"))
    return converted


def _matrix_checks(s_matrix: np.ndarray, freqs_hz: np.ndarray, args: argparse.Namespace) -> list[GateCheck]:
    checks: list[GateCheck] = []
    if s_matrix.ndim != 3 or s_matrix.shape[1:] != (4, 4):
        return [GateCheck("FAIL", "S-matrix shape", f"expected (N,4,4), got {s_matrix.shape}")]
    checks.append(GateCheck("PASS", "S-matrix shape", f"shape={s_matrix.shape}"))
    if len(freqs_hz) == s_matrix.shape[0]:
        checks.append(GateCheck("PASS", "frequency row count", f"rows={len(freqs_hz)}"))
    else:
        checks.append(GateCheck("FAIL", "frequency row count", f"freqs={len(freqs_hz)}, s_rows={s_matrix.shape[0]}"))
    if np.isfinite(freqs_hz).all() and np.isfinite(s_matrix.real).all() and np.isfinite(s_matrix.imag).all():
        checks.append(GateCheck("PASS", "finite numeric values", "all frequency and S-parameter values are finite"))
    else:
        checks.append(GateCheck("FAIL", "finite numeric values", "NaN or Inf found"))
    if len(freqs_hz) >= 2 and bool(np.all(np.diff(freqs_hz) > 0.0)):
        checks.append(GateCheck("PASS", "frequency monotonicity", "strictly increasing"))
    else:
        checks.append(GateCheck("FAIL", "frequency monotonicity", "frequency grid is not strictly increasing"))
    rec_max = float(np.max(np.abs(s_matrix - np.swapaxes(s_matrix, 1, 2)))) if s_matrix.size else math.nan
    if rec_max <= float(args.max_reciprocity_error):
        checks.append(GateCheck("PASS", "reciprocity", f"max_abs_error={rec_max:.6g}"))
    else:
        checks.append(GateCheck("FAIL", "reciprocity", f"max_abs_error={rec_max:.6g} exceeds {args.max_reciprocity_error:g}"))
    sigma_max = float(np.max(np.linalg.svd(s_matrix, compute_uv=False))) if s_matrix.size else math.nan
    if sigma_max <= float(args.max_passivity_sigma):
        checks.append(GateCheck("PASS", "passivity", f"sigma_max={sigma_max:.6g}"))
    else:
        checks.append(GateCheck("FAIL", "passivity", f"sigma_max={sigma_max:.6g} exceeds {args.max_passivity_sigma:g}"))
    return checks


def _frequency_checks(freqs_hz: np.ndarray, args: argparse.Namespace) -> list[GateCheck]:
    checks: list[GateCheck] = []
    if len(freqs_hz) == 0:
        return [
            GateCheck("FAIL", "frequency grid", "no frequency points"),
            GateCheck("FAIL", "ADS no-extrapolation plot grid", "no frequency points"),
        ]
    start_hz = float(args.required_sweep_start_ghz) * 1.0e9
    stop_hz = float(args.required_sweep_stop_ghz) * 1.0e9
    step_hz = float(args.required_sweep_step_ghz) * 1.0e9
    actual_step = float(np.median(np.diff(freqs_hz))) if len(freqs_hz) >= 2 else math.nan
    tol = float(args.frequency_tolerance_hz)
    misses: list[str] = []
    if float(freqs_hz[0]) > start_hz + tol:
        misses.append(f"starts {float(freqs_hz[0]) / 1e9:.6g} GHz > required {args.required_sweep_start_ghz:g} GHz")
    if float(freqs_hz[-1]) < stop_hz - tol:
        misses.append(f"stops {float(freqs_hz[-1]) / 1e9:.6g} GHz < required {args.required_sweep_stop_ghz:g} GHz")
    if int(args.required_sweep_points) > 0 and len(freqs_hz) != int(args.required_sweep_points):
        misses.append(f"points {len(freqs_hz)} != required {int(args.required_sweep_points)}")
    if len(freqs_hz) >= 2 and abs(actual_step - step_hz) > tol:
        misses.append(f"step {actual_step / 1e9:.6g} GHz != required {args.required_sweep_step_ghz:g} GHz")
    if len(freqs_hz) >= 2:
        max_step_error = float(np.max(np.abs(np.diff(freqs_hz) - step_hz)))
        if max_step_error > tol:
            misses.append(f"per-step grid max_error={max_step_error / 1e9:.6g} GHz exceeds tolerance")
    if misses:
        checks.append(GateCheck("FAIL", "final ADS sweep coverage", "; ".join(misses)))
    else:
        checks.append(GateCheck("PASS", "final ADS sweep coverage", _frequency_detail(freqs_hz)))
    checks.append(_ads_no_extrapolation_plot_grid_check(freqs_hz, args))
    target_hz = float(args.target_ghz) * 1.0e9
    nearest = float(freqs_hz[int(np.argmin(np.abs(freqs_hz - target_hz)))])
    if abs(nearest - target_hz) <= max(tol, 0.5 * actual_step + tol):
        checks.append(GateCheck("PASS", "target frequency availability", f"target={args.target_ghz:g} GHz, nearest={nearest / 1e9:.6g} GHz"))
    else:
        checks.append(GateCheck("FAIL", "target frequency availability", f"target={args.target_ghz:g} GHz, nearest={nearest / 1e9:.6g} GHz"))
    return checks


def _ads_no_extrapolation_plot_grid_check(freqs_hz: np.ndarray, args: argparse.Namespace) -> GateCheck:
    start_hz = float(args.required_sweep_start_ghz) * 1.0e9
    stop_hz = float(args.required_sweep_stop_ghz) * 1.0e9
    step_hz = float(args.required_sweep_step_ghz) * 1.0e9
    expected_points = int(args.required_sweep_points)
    tol = float(args.frequency_tolerance_hz)
    if expected_points <= 0:
        expected_points = int(round((stop_hz - start_hz) / step_hz)) + 1
    span_points = int(round((stop_hz - start_hz) / step_hz)) + 1 if step_hz > 0 else 0
    expected = start_hz + np.arange(expected_points, dtype=float) * step_hz
    if expected.size:
        expected[-1] = stop_hz
    failures: list[str] = []
    if span_points != expected_points:
        failures.append(f"requested_grid_inconsistent: points={expected_points}, start/stop/step imply {span_points}")
    if len(freqs_hz) != expected_points:
        failures.append(f"file_point_count={len(freqs_hz)} != requested_plot_points={expected_points}")
    if float(freqs_hz[0]) > start_hz + tol:
        failures.append(
            f"ADS would extrapolate below file start: required_start={start_hz / 1e9:.6g} GHz, "
            f"file_start={float(freqs_hz[0]) / 1e9:.6g} GHz"
        )
    if float(freqs_hz[-1]) < stop_hz - tol:
        failures.append(
            f"ADS would extrapolate above file stop: required_stop={stop_hz / 1e9:.6g} GHz, "
            f"file_stop={float(freqs_hz[-1]) / 1e9:.6g} GHz"
        )
    missing = 0
    max_nearest_error_hz = 0.0
    for value in expected:
        nearest_error = float(np.min(np.abs(freqs_hz - value)))
        max_nearest_error_hz = max(max_nearest_error_hz, nearest_error)
        if nearest_error > tol:
            missing += 1
    if missing:
        failures.append(
            f"missing_requested_plot_points={missing}/{expected_points}; "
            f"max_nearest_error={max_nearest_error_hz / 1e9:.6g} GHz"
        )
    detail = (
        f"required_plot_grid={float(args.required_sweep_start_ghz):g}-"
        f"{float(args.required_sweep_stop_ghz):g} GHz/"
        f"{expected_points} points/step={float(args.required_sweep_step_ghz):g} GHz; "
        f"file_grid={_frequency_detail(freqs_hz)}"
    )
    if failures:
        return GateCheck("FAIL", "ADS no-extrapolation plot grid", detail + "; " + "; ".join(failures))
    return GateCheck("PASS", "ADS no-extrapolation plot grid", detail)


def _target_record(curves: Any, args: argparse.Namespace) -> dict[str, Any]:
    freq_ghz = curves.freq_hz / 1.0e9
    idx = int(np.argmin(np.abs(freq_ghz - float(args.target_ghz))))
    actuals = {
        "lp_nh": float(curves.lp_nh[idx]),
        "ls_nh": float(curves.ls_nh[idx]),
        "k": float(curves.k[idx]),
        "qp": float(curves.qp[idx]),
        "qs": float(curves.qs[idx]),
        "cm_single_primary_ff": float(curves.cm_single_primary_ff[idx]),
    }
    checks = [_metric_check(spec, actuals[spec.key], args.photo_max_percent_error) for spec in REFERENCE_METRICS]
    return {"nearest_frequency_ghz": float(freq_ghz[idx]), "actuals": actuals, "checks": checks}


def _target_photo_checks(target: dict[str, Any]) -> list[GateCheck]:
    failures = [check for check in target.get("checks", []) if check.get("status") == "FAIL"]
    if failures:
        worst = max(failures, key=lambda item: float(item["percent_error"]))
        return [
            GateCheck(
                "FAIL",
                "ADS photo anchor",
                f"{len(failures)}/{len(target.get('checks', []))} metrics fail; worst={worst['label']} {worst['percent_error']:.2f}%",
            )
        ]
    return [GateCheck("PASS", "ADS photo anchor", "all target-frequency photo metrics pass")]


def _target_transformer_sanity_checks(target: dict[str, Any], args: argparse.Namespace) -> list[GateCheck]:
    actuals = target.get("actuals", {})
    failures: list[str] = []
    if float(actuals.get("lp_nh", math.nan)) < float(args.min_target_inductance_nh):
        failures.append(f"Lp<{args.min_target_inductance_nh:g} nH")
    if float(actuals.get("ls_nh", math.nan)) < float(args.min_target_inductance_nh):
        failures.append(f"Ls<{args.min_target_inductance_nh:g} nH")
    if float(actuals.get("qp", math.nan)) < float(args.min_target_q):
        failures.append(f"Qp<{args.min_target_q:g}")
    if float(actuals.get("qs", math.nan)) < float(args.min_target_q):
        failures.append(f"Qs<{args.min_target_q:g}")
    abs_k = abs(float(actuals.get("k", math.nan)))
    if abs_k < float(args.min_target_abs_k):
        failures.append(f"|K|<{args.min_target_abs_k:g}")
    if abs_k > float(args.max_target_abs_k):
        failures.append(f"|K|>{args.max_target_abs_k:g}")
    detail = (
        f"Lp={actuals.get('lp_nh'):.6g} nH, Ls={actuals.get('ls_nh'):.6g} nH, "
        f"K={actuals.get('k'):.6g}, Qp={actuals.get('qp'):.6g}, Qs={actuals.get('qs'):.6g}"
    )
    if failures:
        return [GateCheck("FAIL", "basic numeric physics sanity", detail + "; " + "; ".join(failures))]
    return [GateCheck("PASS", "basic numeric physics sanity", detail)]


def _physical_window_checks(curves: Any, args: argparse.Namespace) -> list[GateCheck]:
    arrays = _curve_arrays(curves)
    nonfinite = [name for name, values in arrays.items() if not np.isfinite(values).all()]
    if nonfinite:
        return [GateCheck("FAIL", "physical metric window", f"non-finite metric arrays: {nonfinite}")]
    mask = _window_mask(
        curves.freq_hz,
        float(args.physical_window_start_ghz),
        float(args.physical_window_stop_ghz),
        float(args.frequency_tolerance_hz),
    )
    if int(np.sum(mask)) == 0:
        return [GateCheck("FAIL", "physical metric window", "no frequency points inside requested window")]
    failures: list[str] = _window_coverage_failures(
        curves.freq_hz,
        float(args.physical_window_start_ghz),
        float(args.physical_window_stop_ghz),
        float(args.frequency_tolerance_hz),
    )
    if float(np.min(curves.lp_nh[mask])) <= float(args.min_target_inductance_nh):
        failures.append(f"Lp_min_nH={float(np.min(curves.lp_nh[mask])):.6g}")
    if float(np.min(curves.ls_nh[mask])) <= float(args.min_target_inductance_nh):
        failures.append(f"Ls_min_nH={float(np.min(curves.ls_nh[mask])):.6g}")
    if float(np.min(curves.qp[mask])) <= float(args.min_target_q):
        failures.append(f"Qp_min={float(np.min(curves.qp[mask])):.6g}")
    if float(np.min(curves.qs[mask])) <= float(args.min_target_q):
        failures.append(f"Qs_min={float(np.min(curves.qs[mask])):.6g}")
    min_window_abs_k = (
        float(args.min_window_abs_k)
        if args.min_window_abs_k is not None
        else float(args.min_target_abs_k)
    )
    abs_k = np.abs(curves.k[mask])
    if min_window_abs_k > 0.0 and float(np.min(abs_k)) < min_window_abs_k:
        failures.append(f"abs_K_min={float(np.min(abs_k)):.6g}")
    if float(np.max(abs_k)) > float(args.max_target_abs_k):
        failures.append(f"abs_K_max={float(np.max(abs_k)):.6g}")
    detail = _window_detail(curves.freq_hz, mask)
    if failures:
        return [GateCheck("FAIL", "physical metric window", detail + "; " + "; ".join(failures))]
    return [GateCheck("PASS", "physical metric window", detail)]


def _shape_window_checks(curves: Any, args: argparse.Namespace) -> list[GateCheck]:
    mask = _window_mask(
        curves.freq_hz,
        float(args.shape_window_start_ghz),
        float(args.shape_window_stop_ghz),
        float(args.frequency_tolerance_hz),
    )
    if int(np.sum(mask)) < 3:
        return [GateCheck("FAIL", "smooth transformer metric window", "fewer than 3 points inside requested shape window")]
    coverage_failures = _window_coverage_failures(
        curves.freq_hz,
        float(args.shape_window_start_ghz),
        float(args.shape_window_stop_ghz),
        float(args.frequency_tolerance_hz),
    )
    arrays = {
        "Lp": curves.lp_nh[mask],
        "Ls": curves.ls_nh[mask],
        "K": curves.k[mask],
        "Qp": curves.qp[mask],
        "Qs": curves.qs[mask],
    }
    failures: list[str] = list(coverage_failures)
    summaries: list[str] = []
    for name, values in arrays.items():
        arr = np.asarray(values, dtype=float)
        if not np.isfinite(arr).all():
            failures.append(f"{name} nonfinite")
            continue
        abs_values = np.abs(arr)
        floor = 0.05 if name == "K" else 1.0e-12
        scale = max(float(np.nanpercentile(abs_values, 50.0)), floor)
        spike_ratio = float(np.nanpercentile(abs_values, 99.0) / scale)
        relative_step = float(np.max(np.abs(np.diff(arr))) / scale)
        summaries.append(f"{name}:p99/p50={spike_ratio:.3g},max_step/p50={relative_step:.3g}")
        if spike_ratio > float(args.max_shape_spike_ratio):
            failures.append(f"{name} spike_ratio={spike_ratio:.3g}")
        if relative_step > float(args.max_shape_relative_step):
            failures.append(f"{name} relative_step={relative_step:.3g}")
    detail = _window_detail(curves.freq_hz, mask) + "; " + "; ".join(summaries)
    if failures:
        return [GateCheck("FAIL", "smooth transformer metric window", detail + "; failures=" + ", ".join(failures))]
    return [GateCheck("PASS", "smooth transformer metric window", detail)]


def _curve_arrays(curves: Any) -> dict[str, np.ndarray]:
    return {
        "lp_nh": np.asarray(curves.lp_nh, dtype=float),
        "ls_nh": np.asarray(curves.ls_nh, dtype=float),
        "k": np.asarray(curves.k, dtype=float),
        "qp": np.asarray(curves.qp, dtype=float),
        "qs": np.asarray(curves.qs, dtype=float),
        "cm_single_primary_ff": np.asarray(curves.cm_single_primary_ff, dtype=float),
    }


def _window_mask(freq_hz: np.ndarray, start_ghz: float, stop_ghz: float, tolerance_hz: float) -> np.ndarray:
    freq = np.asarray(freq_hz, dtype=float)
    start_hz = float(start_ghz) * 1.0e9
    stop_hz = float(stop_ghz) * 1.0e9
    return (freq >= start_hz - float(tolerance_hz)) & (freq <= stop_hz + float(tolerance_hz))


def _window_detail(freq_hz: np.ndarray, mask: np.ndarray) -> str:
    freq = np.asarray(freq_hz, dtype=float)
    return (
        f"points={int(np.sum(mask))}, "
        f"freq_range_ghz={float(freq[mask][0]) / 1.0e9:.6g}-{float(freq[mask][-1]) / 1.0e9:.6g}"
    )


def _window_coverage_failures(freq_hz: np.ndarray, start_ghz: float, stop_ghz: float, tolerance_hz: float) -> list[str]:
    freq = np.asarray(freq_hz, dtype=float)
    if freq.size == 0:
        return ["no frequency points"]
    start_hz = float(start_ghz) * 1.0e9
    stop_hz = float(stop_ghz) * 1.0e9
    failures: list[str] = []
    if float(freq[0]) > start_hz + float(tolerance_hz):
        failures.append(f"window_start_missing file_start_ghz={float(freq[0]) / 1.0e9:.6g}")
    if float(freq[-1]) < stop_hz - float(tolerance_hz):
        failures.append(f"window_stop_missing file_stop_ghz={float(freq[-1]) / 1.0e9:.6g}")
    return failures


def _port_pair_records(path: Path, args: argparse.Namespace) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for port_pairs in _all_ordered_port_pairs():
        curves = _extract_metric_curves("EMX candidate", path, port_pairs)
        target = _target_record(curves, argparse.Namespace(**{**vars(args), "port_pairs": port_pairs}))
        checks = target["checks"]
        max_error = max(float(check["percent_error"]) for check in checks)
        mean_error = float(np.mean([float(check["percent_error"]) for check in checks]))
        pass_count = sum(1 for check in checks if check["status"] == "PASS")
        records.append(
            {
                "port_pairs": port_pairs,
                "overall_status": "PASS" if pass_count == len(checks) else "FAIL",
                "pass_count": pass_count,
                "metric_count": len(checks),
                "max_percent_error": float(max_error),
                "mean_percent_error": mean_error,
                **{f"{check['metric']}_percent_error": float(check["percent_error"]) for check in checks},
                **{f"{check['metric']}_actual": float(check["actual"]) for check in checks},
            }
        )
    records.sort(key=lambda row: (row["overall_status"] != "PASS", row["max_percent_error"], row["mean_percent_error"], row["port_pairs"]))
    return records


def _port_pair_gate_checks(records: list[dict[str, Any]], default_port_pairs: str) -> list[GateCheck]:
    default = next((row for row in records if row["port_pairs"] == default_port_pairs), None)
    pass_count = sum(1 for row in records if row["overall_status"] == "PASS")
    checks: list[GateCheck] = []
    if default and default["overall_status"] == "PASS":
        checks.append(GateCheck("PASS", "approved port-pair photo alignment", f"{default_port_pairs} passes photo anchor"))
    else:
        detail = "default port-pair missing" if default is None else f"{default_port_pairs} max_error={default['max_percent_error']:.2f}%"
        checks.append(GateCheck("FAIL", "approved port-pair photo alignment", detail))
    if pass_count:
        best = records[0]
        checks.append(GateCheck("PASS", "any port-pair photo alignment", f"{pass_count} pairings pass; best={best['port_pairs']}"))
    else:
        best = records[0] if records else None
        detail = "no records" if best is None else f"no pairing passes; best={best['port_pairs']} max_error={best['max_percent_error']:.2f}%"
        checks.append(GateCheck("FAIL", "any port-pair photo alignment", detail))
    return checks


def _all_ordered_port_pairs() -> list[str]:
    pairs: list[str] = []
    for a in range(1, 5):
        for b in range(1, 5):
            if b == a:
                continue
            remaining = [port for port in range(1, 5) if port not in (a, b)]
            for c, d in ((remaining[0], remaining[1]), (remaining[1], remaining[0])):
                pairs.append(f"{a},{b}:{c},{d}")
    return pairs


def _write_metrics_csv(path: Path, curves: Any) -> None:
    fields = ["freq_hz", "freq_ghz", *(key for key, *_ in METRIC_ORDER)]
    arrays = {
        "lp_nh": curves.lp_nh,
        "ls_nh": curves.ls_nh,
        "k": curves.k,
        "qp": curves.qp,
        "qs": curves.qs,
        "cm_single_primary_ff": curves.cm_single_primary_ff,
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for idx, freq_hz in enumerate(curves.freq_hz):
            writer.writerow(
                {
                    "freq_hz": float(freq_hz),
                    "freq_ghz": float(freq_hz / 1.0e9),
                    **{key: float(values[idx]) for key, values in arrays.items()},
                }
            )


def _write_pair_csv(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    fields = list(records[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def _write_metric_plot(path: Path, curves: Any, target: dict[str, Any], args: argparse.Namespace) -> None:
    import matplotlib.pyplot as plt

    freq_ghz = curves.freq_hz / 1.0e9
    arrays = {
        "lp_nh": curves.lp_nh,
        "ls_nh": curves.ls_nh,
        "k": curves.k,
        "qp": curves.qp,
        "qs": curves.qs,
        "cm_single_primary_ff": curves.cm_single_primary_ff,
    }
    checks = {check["metric"]: check for check in target.get("checks", [])}
    fig, axes = plt.subplots(2, 3, figsize=(14, 8.2), constrained_layout=True)
    fig.suptitle("EMX-first gate: ADS-equivalent physical metrics", fontsize=15, fontweight="bold")
    for ax, (key, label, unit, color) in zip(axes.ravel(), METRIC_ORDER):
        values = arrays[key]
        check = checks.get(key, {})
        ax.plot(freq_ghz, values, color=color, linewidth=1.9, label=label)
        ax.axvline(float(args.target_ghz), color="#111827", linewidth=1.0, linestyle=":", label="target")
        if target:
            actual = target["actuals"][key]
            ax.scatter([target["nearest_frequency_ghz"]], [actual], color="#111827", s=22, zorder=5)
        unit_text = f" {unit}" if unit else ""
        status = check.get("status", "UNKNOWN")
        err = float(check.get("percent_error", math.nan))
        actual_text = target.get("actuals", {}).get(key)
        ax.text(
            0.02,
            0.94,
            f"{status}\n{target.get('nearest_frequency_ghz', math.nan):.2f} GHz\nactual={actual_text:.5g}{unit_text}\nphoto err={err:.2f}%",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.88, "pad": 3},
        )
        ax.set_title(label if not unit else f"{label} ({unit})")
        ax.set_xlabel("freq (GHz)")
        ax.grid(True, alpha=0.28)
        ax.legend(loc="best", fontsize=7)
    fig.savefig(path, dpi=int(args.dpi))
    plt.close(fig)


def _write_core_metric_plot(path: Path, curves: Any, target: dict[str, Any], args: argparse.Namespace) -> None:
    import matplotlib.pyplot as plt

    freq_ghz = curves.freq_hz / 1.0e9
    fig, axes = plt.subplots(2, 2, figsize=(11.2, 7.8), constrained_layout=True)
    fig.suptitle("EMX-first gate: core transformer metrics", fontsize=15, fontweight="bold")
    _plot_l_pair(axes[0, 0], freq_ghz, curves.lp_nh, curves.ls_nh, target, args)
    _plot_single_core_metric(axes[0, 1], freq_ghz, curves.qp, "Qp", "#3348a3", target, "qp", args)
    _plot_single_core_metric(axes[1, 0], freq_ghz, curves.qs, "Qs", "#be123c", target, "qs", args)
    _plot_single_core_metric(axes[1, 1], freq_ghz, curves.k, "K", "#c2410c", target, "k", args)
    fig.savefig(path, dpi=int(args.dpi))
    plt.close(fig)


def _plot_l_pair(ax: Any, freq_ghz: np.ndarray, lp_nh: np.ndarray, ls_nh: np.ndarray, target: dict[str, Any], args: argparse.Namespace) -> None:
    ax.plot(freq_ghz, lp_nh, color="#2563eb", linewidth=1.9, label="Lp")
    ax.plot(freq_ghz, ls_nh, color="#dc2626", linewidth=1.9, label="Ls")
    ax.axvline(float(args.target_ghz), color="#111827", linewidth=1.0, linestyle=":", label="target")
    if target:
        target_freq = float(target.get("nearest_frequency_ghz", math.nan))
        actuals = target.get("actuals", {})
        ax.scatter([target_freq], [float(actuals.get("lp_nh", math.nan))], color="#1d4ed8", s=24, zorder=5)
        ax.scatter([target_freq], [float(actuals.get("ls_nh", math.nan))], color="#b91c1c", s=24, zorder=5)
        ax.text(
            0.02,
            0.94,
            f"{target_freq:.2f} GHz\nLp={float(actuals.get('lp_nh', math.nan)):.5g} nH\nLs={float(actuals.get('ls_nh', math.nan)):.5g} nH",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.88, "pad": 3},
        )
    ax.set_title("Lp / Ls (nH)")
    ax.set_xlabel("freq (GHz)")
    ax.grid(True, alpha=0.28)
    ax.legend(loc="best", fontsize=8)


def _plot_single_core_metric(ax: Any, freq_ghz: np.ndarray, values: np.ndarray, label: str, color: str, target: dict[str, Any], key: str, args: argparse.Namespace) -> None:
    ax.plot(freq_ghz, values, color=color, linewidth=1.9, label=label)
    ax.axvline(float(args.target_ghz), color="#111827", linewidth=1.0, linestyle=":", label="target")
    if target:
        target_freq = float(target.get("nearest_frequency_ghz", math.nan))
        actual = float((target.get("actuals") or {}).get(key, math.nan))
        ax.scatter([target_freq], [actual], color="#111827", s=24, zorder=5)
        ax.text(
            0.02,
            0.94,
            f"{target_freq:.2f} GHz\n{label}={actual:.5g}",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.88, "pad": 3},
        )
    ax.set_title(label)
    ax.set_xlabel("freq (GHz)")
    ax.grid(True, alpha=0.28)
    ax.legend(loc="best", fontsize=8)


def _write_pair_plot(path: Path, records: list[dict[str, Any]], args: argparse.Namespace) -> None:
    if not records:
        return
    import matplotlib.pyplot as plt

    top = records[: min(12, len(records))]
    labels = [row["port_pairs"] for row in top]
    values = [row["max_percent_error"] for row in top]
    colors = ["#15803d" if row["overall_status"] == "PASS" else "#dc2626" for row in top]
    fig, ax = plt.subplots(figsize=(12, 5.6), constrained_layout=True)
    ax.bar(np.arange(len(top)), values, color=colors, edgecolor="#111827", linewidth=0.45)
    ax.axhline(float(args.photo_max_percent_error), color="#111827", linestyle="--", linewidth=1.0, label="photo gate")
    ax.set_xticks(np.arange(len(top)))
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_ylabel("Worst 15 GHz photo-reference error (%)")
    ax.set_title("EMX port-pair sensitivity: wrong source vs port-order issue", loc="left", fontweight="bold")
    ax.grid(axis="y", alpha=0.28)
    ax.legend(loc="best")
    fig.savefig(path, dpi=int(args.dpi))
    plt.close(fig)


def _frequency_summary(freqs_hz: np.ndarray) -> dict[str, Any]:
    diffs = np.diff(freqs_hz)
    return {
        "start": float(freqs_hz[0] / 1.0e9) if len(freqs_hz) else None,
        "stop": float(freqs_hz[-1] / 1.0e9) if len(freqs_hz) else None,
        "points": int(len(freqs_hz)),
        "step": float(np.median(diffs) / 1.0e9) if len(diffs) else None,
    }


def _frequency_detail(freqs_hz: np.ndarray) -> str:
    summary = _frequency_summary(freqs_hz)
    return (
        f"start={summary['start']:.6g} GHz, stop={summary['stop']:.6g} GHz, "
        f"step={summary['step']:.6g} GHz, points={summary['points']}"
    )


def _matrix_quality(s_matrix: np.ndarray) -> dict[str, Any]:
    rec = np.max(np.abs(s_matrix - np.swapaxes(s_matrix, 1, 2)), axis=(1, 2))
    sigma = np.max(np.linalg.svd(s_matrix, compute_uv=False), axis=1)
    return {
        "reciprocity_error_abs_max": float(np.max(rec)),
        "passivity_sigma_max": float(np.max(sigma)),
        "passivity_excess_max": float(max(0.0, np.max(sigma) - 1.0)),
    }


def _status_counts(checks: list[GateCheck]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def _render_report(summary: dict[str, Any]) -> str:
    target = summary.get("target_record") or {}
    pair = summary.get("port_pair_sensitivity") or {}
    lines = [
        "# EMX-First Validation Gate",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- EMX Touchstone: `{summary['emx_touchstone']}`",
        f"- Source kind from path: `{summary['source_kind_from_path']}`",
        f"- Port pairs: `{summary['port_pairs']}`",
        f"- Target frequency: `{summary['target_ghz']} GHz`",
        f"- Required final sweep: `{summary['required_final_sweep_ghz']}`",
        f"- Actual frequency range: `{summary.get('frequency_ghz')}`",
        "",
        "## Gate Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary.get("checks", []):
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    if target:
        lines.extend(
            [
                "",
                "## 15 GHz ADS Photo Anchor",
                "",
                f"- Nearest frequency: `{target.get('nearest_frequency_ghz')} GHz`",
                "",
                "| Status | Metric | Expected | Actual | Error | Limit |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for check in target.get("checks", []):
            unit = f" {check['unit']}" if check["unit"] else ""
            lines.append(
                f"| {check['status']} | {check['label']} | {check['expected']:.6g}{unit} | "
                f"{check['actual']:.6g}{unit} | {check['percent_error']:.2f}% | "
                f"{check['max_percent_error']:.2f}% |"
            )
    best = pair.get("best")
    default = pair.get("default")
    lines.extend(["", "## Port-Pair Sensitivity", ""])
    if best:
        lines.append(f"- Best pair: `{best['port_pairs']}`, status `{best['overall_status']}`, max error `{best['max_percent_error']:.2f}%`")
    if default:
        lines.append(
            f"- Default/approved pair: `{default['port_pairs']}`, status `{default['overall_status']}`, "
            f"max error `{default['max_percent_error']:.2f}%`"
        )
    lines.append(f"- Passing pair count: `{pair.get('pass_count')}`")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
        ]
    )
    for key, value in summary.get("artifacts", {}).items():
        if value:
            lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Method Boundary", ""])
    lines.extend(f"- {note}" for note in summary.get("method_notes", []))
    if summary.get("errors"):
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {item}" for item in summary["errors"])
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
