#!/usr/bin/env python3
"""Compare EMX and HFSS/ADS transformer curves.

Accepted inputs:

- Touchstone .s2p files: Lp, Ls, scalar Q, K, Qp, and Qs are extracted from the Z matrix.
- Touchstone .s4p files: the 4-port single-ended matrix is converted to a
  2-port differential transformer using terminal pairs such as 1,2:3,4.
- Touchstone files with more than 4 ports, including .s8p files: explicit
  differential terminal pairs are required so the script cannot silently reuse
  an old 4-port port map on a new topology.
- CSV files: frequency plus metric columns exported from ADS or another tool.

The default pass criterion is <=10 percent max relative error for every metric.
Scalar Q is defined as min(Qp, Qs), matching the project-level physical-feature
target Lp/Ls/Q/K while keeping Qp/Qs as diagnostic channels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.analysis import (  # noqa: E402
    multiport_s_to_grounded_differential_z,
    multiport_single_ended_to_differential_z,
)
from rfic_transformer_inverse_design.network_analysis import s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402

METRICS = ("lp_nh", "ls_nh", "q", "k", "kw", "qp", "qs")
FIXED_COMPARE_GENERATED_UTC = datetime(2026, 6, 13, tzinfo=timezone.utc).isoformat(timespec="seconds")

CSV_ALIASES = {
    "freq_hz": ("freq_hz", "frequency_hz", "freq", "frequency"),
    "freq_ghz": ("freq_ghz", "frequency_ghz"),
    "k": ("k", "coupling", "coupling_k"),
    "kw": ("kw", "k_w", "coupling_kw", "coupling_k"),
    "q": ("q", "q_min", "q_scalar", "qfactor", "q_factor"),
    "qp": ("qp", "q_p", "qprimary", "q_primary"),
    "qs": ("qs", "q_s", "qsecondary", "q_secondary"),
    "lp_nh": ("lp_nh", "lp", "l_p_nh", "lprimary_nh", "l_primary_nh"),
    "ls_nh": ("ls_nh", "ls", "l_s_nh", "lsecondary_nh", "l_secondary_nh"),
}


@dataclass(frozen=True)
class CurveSet:
    source: str
    freq_hz: np.ndarray
    metrics: dict[str, np.ndarray]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _assert_input_contract(
        Path(args.emx),
        label="EMX",
        required_touchstone_suffix=args.require_touchstone_suffix,
        expected_port_count=args.expected_port_count,
        expected_reference_ohm=args.expected_reference_ohm,
    )
    _assert_input_contract(
        Path(args.hfss),
        label="HFSS/ADS",
        required_touchstone_suffix=args.require_touchstone_suffix,
        expected_port_count=args.expected_port_count,
        expected_reference_ohm=args.expected_reference_ohm,
    )
    emx = load_curves(
        Path(args.emx),
        port_pairs=parse_port_pairs(args.emx_port_pairs) if args.emx_port_pairs else None,
        ground_unused_ports=bool(args.ground_unused_ports),
    )
    hfss = load_curves(
        Path(args.hfss),
        port_pairs=parse_port_pairs(args.hfss_port_pairs) if args.hfss_port_pairs else None,
        ground_unused_ports=bool(args.ground_unused_ports),
    )
    result = compare_curves(
        emx,
        hfss,
        max_percent_error=args.max_percent_error,
        compare_start_hz=_ghz_to_hz(args.compare_start_ghz),
        compare_stop_hz=_ghz_to_hz(args.compare_stop_ghz),
        min_frequency_points=args.min_frequency_points,
        expected_frequency_step_hz=_ghz_to_hz(args.expected_frequency_step_ghz),
        expected_frequency_points=args.expected_frequency_points,
        frequency_tolerance_hz=args.frequency_tolerance_hz,
        require_matching_frequency_grid=args.require_matching_frequency_grid,
        target_hz=_ghz_to_hz(args.target_ghz),
        target_frequency_tolerance_hz=_ghz_to_hz(args.target_frequency_tolerance_ghz),
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "emx_hfss_ads_comparison_summary.json"
    report_path = out_dir / "emx_hfss_ads_comparison_report.md"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    report_path.write_text(render_report(result, emx, hfss), encoding="utf-8")
    curve_csv_path, error_csv_path = export_comparison_tables(result, out_dir)
    target_marker_csv_path = export_target_marker_table(result, out_dir)
    formula_path = write_formula_note(result, emx, hfss, args, out_dir)
    if args.plot:
        maybe_plot(result, out_dir)
    manifest_path = write_manifest(
        out_dir,
        input_paths=[Path(args.emx), Path(args.hfss)],
        output_paths=[
            summary_path,
            report_path,
            curve_csv_path,
            error_csv_path,
            *( [target_marker_csv_path] if target_marker_csv_path is not None else [] ),
            formula_path,
            *(out_dir / f"{metric}_comparison.png" for metric in METRICS if (out_dir / f"{metric}_comparison.png").exists()),
        ],
    )
    print(f"overall_status={result['overall_status']}")
    print(f"report={report_path}")
    print(f"summary={summary_path}")
    print(f"curves_csv={curve_csv_path}")
    print(f"errors_csv={error_csv_path}")
    if target_marker_csv_path is not None:
        print(f"target_marker_csv={target_marker_csv_path}")
    print(f"formula_note={formula_path}")
    print(f"manifest={manifest_path}")
    for metric, item in result["metrics"].items():
        print(
            f"{item['status']:4s} {metric}: "
            f"max_abs_error={item['max_abs_error']:.6g}, "
            f"max_percent_error={item['max_percent_error']:.6g}%"
        )
    return 2 if result["overall_status"] == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx", required=True, help="EMX .s2p or metric CSV")
    parser.add_argument("--hfss", required=True, help="HFSS/ADS .s2p or metric CSV")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--emx-port-pairs",
        help="Differential port pairs for EMX multiport Touchstone files, e.g. 1,2:3,4. Required for >4-port files.",
    )
    parser.add_argument(
        "--hfss-port-pairs",
        help="Differential port pairs for HFSS/ADS multiport Touchstone files, e.g. 1,2:3,4. Required for >4-port files.",
    )
    parser.add_argument(
        "--ground-unused-ports",
        action="store_true",
        help="Short all Touchstone ports outside the selected differential pairs to ground before extracting Lp/Ls/Q/K. Use this for S8P power-line ports grounded in ADS.",
    )
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--compare-start-ghz", type=float, help="Require and compare from this frequency in GHz")
    parser.add_argument("--compare-stop-ghz", type=float, help="Require and compare through this frequency in GHz")
    parser.add_argument("--min-frequency-points", type=int, default=2, help="Minimum EMX/reference points inside the comparison window")
    parser.add_argument("--expected-frequency-step-ghz", type=float, help="Require this EMX/reference comparison-grid step in GHz")
    parser.add_argument("--expected-frequency-points", type=int, help="Require this exact comparison-grid point count")
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--target-ghz", type=float, help="Write a marker table at this target frequency in GHz, e.g. 15")
    parser.add_argument("--target-frequency-tolerance-ghz", type=float, default=0.05)
    parser.add_argument(
        "--require-touchstone-suffix",
        help="Require both inputs to be Touchstone files with this suffix, e.g. .s8p for final 8-port EMX/HFSS evidence.",
    )
    parser.add_argument(
        "--expected-port-count",
        type=int,
        help="Require both Touchstone inputs to contain this number of ports, e.g. 8 for final S8P validation.",
    )
    parser.add_argument(
        "--expected-reference-ohm",
        type=float,
        help="Require both Touchstone inputs to use this reference impedance, e.g. 50.",
    )
    parser.add_argument(
        "--require-matching-frequency-grid",
        action="store_true",
        help="Require HFSS/ADS frequency points to match the EMX/reference comparison grid before interpolation",
    )
    parser.add_argument("--plot", action="store_true", help="Write comparison PNGs when matplotlib is available")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _assert_input_contract(
    path: Path,
    *,
    label: str,
    required_touchstone_suffix: str | None = None,
    expected_port_count: int | None = None,
    expected_reference_ohm: float | None = None,
) -> None:
    needs_touchstone = required_touchstone_suffix is not None or expected_port_count is not None or expected_reference_ohm is not None
    if not needs_touchstone:
        return
    suffix = path.suffix.lower()
    if not (suffix.endswith("p") and ".s" in path.name.lower()):
        raise ValueError(f"{label} input must be a Touchstone file for this contract: {path}")
    if required_touchstone_suffix is not None:
        required = required_touchstone_suffix.lower()
        if not required.startswith("."):
            required = f".{required}"
        if suffix != required:
            raise ValueError(f"{label} input must be {required}, got {suffix}: {path}")
    sparams = load_touchstone(path)
    n_ports = int(sparams.s_matrix.shape[1])
    if expected_port_count is not None and n_ports != int(expected_port_count):
        raise ValueError(f"{label} input must have {int(expected_port_count)} ports, got {n_ports}: {path}")
    if expected_reference_ohm is not None:
        reference = np.asarray(sparams.reference_impedance_ohm, dtype=float)
        expected = float(expected_reference_ohm)
        if not np.allclose(reference, expected, rtol=0.0, atol=1.0e-9):
            raise ValueError(f"{label} input must use R {expected:g} ohm, got {reference.tolist()}: {path}")


def load_curves(
    path: Path,
    *,
    port_pairs: tuple[tuple[int, int], tuple[int, int]] | None = None,
    ground_unused_ports: bool = False,
) -> CurveSet:
    suffix = path.suffix.lower()
    if suffix.endswith("p") and ".s" in path.name.lower():
        return load_touchstone_curves(path, port_pairs=port_pairs, ground_unused_ports=ground_unused_ports)
    if suffix == ".csv":
        return load_csv_curves(path)
    raise ValueError(f"Unsupported curve file: {path}")


def load_touchstone_curves(
    path: Path,
    *,
    port_pairs: tuple[tuple[int, int], tuple[int, int]] | None = None,
    ground_unused_ports: bool = False,
) -> CurveSet:
    sparams = load_touchstone(path)
    n_ports = int(sparams.s_matrix.shape[1])
    if n_ports < 2:
        raise ValueError(f"Expected at least a 2-port Touchstone file, got shape {sparams.s_matrix.shape}")
    freq = np.asarray(sparams.freqs_hz, dtype=float)
    s_matrix = np.asarray(sparams.s_matrix, dtype=np.complex128)
    if s_matrix.shape[1:] == (2, 2):
        z = s_to_z(s_matrix, z0=sparams.reference_impedance_ohm)
    else:
        resolved_pairs = _resolve_touchstone_port_pairs(n_ports, port_pairs)
        if ground_unused_ports:
            z = multiport_s_to_grounded_differential_z(s_matrix, sparams.reference_impedance_ohm, resolved_pairs)
        else:
            z_single = s_to_z(s_matrix, z0=sparams.reference_impedance_ohm)
            z = multiport_z_to_differential_z(z_single, resolved_pairs)
    omega = 2.0 * math.pi * freq
    z11 = z[:, 0, 0]
    z22 = z[:, 1, 1]
    z21 = z[:, 1, 0]
    lp_h = np.imag(z11) / omega
    ls_h = np.imag(z22) / omega
    # Match the ADS worksheet convention used for this project:
    # M = imag(Z31 - Z32 + Z42 - Z41) / omega for port pairs 1,2:3,4.
    # After the differential transform, that expression is Zdiff[2,1].
    mutual_h = np.imag(z21) / omega
    denom = np.sqrt(np.maximum(np.abs(lp_h * ls_h), 1.0e-30))
    k = mutual_h / denom
    qp = _safe_div(np.imag(z11), np.real(z11))
    qs = _safe_div(np.imag(z22), np.real(z22))
    q = np.minimum(qp, qs)
    return CurveSet(
        source=str(path),
        freq_hz=freq,
        metrics={
            "lp_nh": lp_h * 1.0e9,
            "ls_nh": ls_h * 1.0e9,
            "q": q,
            "k": k,
            "kw": k,
            "qp": qp,
            "qs": qs,
        },
    )


def multiport_z_to_differential_z(
    z_single: np.ndarray,
    port_pairs: tuple[tuple[int, int], tuple[int, int]],
) -> np.ndarray:
    """Convert an n-port single-ended Z matrix into a 2-port differential Z matrix.

    Port pairs are zero-based, for example ((0, 1), (2, 3)) for primary ports
    1/2 and secondary ports 3/4. The first item of each pair is treated as the
    positive terminal. Differential currents are [I, -I] on each pair.

    Ports not present in ``port_pairs`` carry zero current in this Z-domain
    projection. For .s8p transformer validation, the caller must explicitly
    choose the two physical differential pairs before trusting L/Q/K curves.
    """

    return multiport_single_ended_to_differential_z(z_single, port_pairs)


def four_port_z_to_differential_z(
    z_single: np.ndarray,
    port_pairs: tuple[tuple[int, int], tuple[int, int]],
) -> np.ndarray:
    """Backward-compatible 4-port wrapper used by existing extraction code."""

    z = np.asarray(z_single, dtype=np.complex128)
    if z.ndim != 3 or z.shape[1:] != (4, 4):
        raise ValueError(f"Expected 4-port Z with shape (N,4,4), got {z.shape}")
    flat_ports = [port for pair in port_pairs for port in pair]
    if sorted(flat_ports) != [0, 1, 2, 3]:
        raise ValueError(f"Port pairs must use each S4P port exactly once, got {port_pairs}")
    return multiport_z_to_differential_z(z, port_pairs)


def _resolve_touchstone_port_pairs(
    n_ports: int,
    port_pairs: tuple[tuple[int, int], tuple[int, int]] | None,
) -> tuple[tuple[int, int], tuple[int, int]]:
    if port_pairs is not None:
        return port_pairs
    if int(n_ports) == 4:
        return parse_port_pairs("1,2:3,4")
    raise ValueError(
        f"{n_ports}-port Touchstone extraction requires explicit differential port pairs; "
        "pass --emx-port-pairs/--hfss-port-pairs and record the physical port map."
    )


def parse_port_pairs(text: str) -> tuple[tuple[int, int], tuple[int, int]]:
    try:
        first, second = text.split(":", 1)
        a, b = (int(item.strip()) - 1 for item in first.split(",", 1))
        c, d = (int(item.strip()) - 1 for item in second.split(",", 1))
    except Exception as exc:
        raise ValueError(f"Invalid port-pair spec {text!r}; use e.g. 1,2:3,4") from exc
    return (a, b), (c, d)


def load_csv_curves(path: Path) -> CurveSet:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"Empty CSV: {path}")
    lower_to_name = {name.strip().lower(): name for name in rows[0]}
    freq_col = _find_col(lower_to_name, CSV_ALIASES["freq_hz"])
    freq_multiplier = 1.0
    if freq_col is None:
        freq_col = _find_col(lower_to_name, CSV_ALIASES["freq_ghz"])
        freq_multiplier = 1.0e9
    if freq_col is None:
        raise ValueError(f"CSV must contain frequency column: {path}")
    freq = np.asarray([_float(row[freq_col]) * freq_multiplier for row in rows], dtype=float)
    metrics: dict[str, np.ndarray] = {}
    for metric in METRICS:
        col = _find_col(lower_to_name, CSV_ALIASES[metric])
        if col is not None:
            metrics[metric] = np.asarray([_float(row[col]) for row in rows], dtype=float)
    if "q" not in metrics and "qp" in metrics and "qs" in metrics:
        metrics["q"] = np.minimum(metrics["qp"], metrics["qs"])
    if "kw" not in metrics and "k" in metrics:
        metrics["kw"] = np.asarray(metrics["k"], dtype=float)
    missing = [metric for metric in METRICS if metric not in metrics]
    if missing:
        raise ValueError(f"CSV missing metric columns {missing}: {path}")
    return CurveSet(source=str(path), freq_hz=freq, metrics=metrics)


def compare_curves(
    emx: CurveSet,
    hfss: CurveSet,
    *,
    max_percent_error: float,
    compare_start_hz: float | None = None,
    compare_stop_hz: float | None = None,
    min_frequency_points: int = 2,
    expected_frequency_step_hz: float | None = None,
    expected_frequency_points: int | None = None,
    frequency_tolerance_hz: float = 1.0e5,
    require_matching_frequency_grid: bool = False,
    target_hz: float | None = None,
    target_frequency_tolerance_hz: float | None = None,
) -> dict[str, Any]:
    overlap_min = max(float(np.min(emx.freq_hz)), float(np.min(hfss.freq_hz)))
    overlap_max = min(float(np.max(emx.freq_hz)), float(np.max(hfss.freq_hz)))
    if not overlap_min < overlap_max:
        raise ValueError("EMX and HFSS/ADS frequency ranges do not overlap")
    f_min = overlap_min if compare_start_hz is None else float(compare_start_hz)
    f_max = overlap_max if compare_stop_hz is None else float(compare_stop_hz)
    if f_min < overlap_min or f_max > overlap_max:
        raise ValueError(
            "Requested comparison window is not fully covered by both files: "
            f"requested={f_min / 1.0e9:.6g}-{f_max / 1.0e9:.6g} GHz, "
            f"overlap={overlap_min / 1.0e9:.6g}-{overlap_max / 1.0e9:.6g} GHz"
        )
    if not f_min < f_max:
        raise ValueError("Comparison frequency window must have start < stop")
    freq = np.asarray([f for f in emx.freq_hz if f_min <= f <= f_max], dtype=float)
    if len(freq) < int(min_frequency_points):
        raise ValueError(
            f"Only {len(freq)} EMX/reference frequency points are inside the comparison window; "
            f"required at least {int(min_frequency_points)}"
        )
    hfss_window_freq = np.asarray([f for f in hfss.freq_hz if f_min <= f <= f_max], dtype=float)
    frequency_grid_checks = _frequency_grid_checks(
        reference_freq=freq,
        hfss_ads_freq=hfss_window_freq,
        overlap_min_hz=overlap_min,
        overlap_max_hz=overlap_max,
        window_start_hz=f_min,
        window_stop_hz=f_max,
        expected_start_hz=compare_start_hz,
        expected_stop_hz=compare_stop_hz,
        expected_step_hz=expected_frequency_step_hz,
        expected_points=expected_frequency_points,
        tolerance_hz=frequency_tolerance_hz,
        require_matching_frequency_grid=require_matching_frequency_grid,
    )

    metric_results: dict[str, dict[str, Any]] = {}
    for metric in METRICS:
        emx_values = _interp(freq, emx.freq_hz, emx.metrics[metric])
        hfss_values = _interp(freq, hfss.freq_hz, hfss.metrics[metric])
        abs_error = np.abs(hfss_values - emx_values)
        floor = _relative_error_floor(metric, emx_values)
        pct_error = abs_error / np.maximum(np.abs(emx_values), floor) * 100.0
        max_pct = float(np.max(pct_error))
        metric_results[metric] = {
            "status": "PASS" if max_pct <= max_percent_error else "FAIL",
            "max_abs_error": float(np.max(abs_error)),
            "mean_abs_error": float(np.mean(abs_error)),
            "max_percent_error": max_pct,
            "mean_percent_error": float(np.mean(pct_error)),
            "emx_min": float(np.min(emx_values)),
            "emx_max": float(np.max(emx_values)),
            "hfss_min": float(np.min(hfss_values)),
            "hfss_max": float(np.max(hfss_values)),
        }

    overall_pass = all(item["status"] == "PASS" for item in metric_results.values()) and all(
        item["status"] == "PASS" for item in frequency_grid_checks.values()
    )
    plot_data = {
        "freq_hz": freq.tolist(),
        "emx": {metric: _interp(freq, emx.freq_hz, emx.metrics[metric]).tolist() for metric in METRICS},
        "hfss_ads": {metric: _interp(freq, hfss.freq_hz, hfss.metrics[metric]).tolist() for metric in METRICS},
    }
    target_marker = _target_marker_summary(
        freq,
        plot_data,
        target_hz=target_hz,
        tolerance_hz=target_frequency_tolerance_hz,
        max_percent_error=max_percent_error,
    )
    target_marker_pass = target_marker is None or target_marker.get("status") == "PASS"
    overall_pass = overall_pass and target_marker_pass
    return {
        "overall_status": "PASS" if overall_pass else "FAIL",
        "criterion": {"max_percent_error": float(max_percent_error)},
        "emx_source": emx.source,
        "hfss_ads_source": hfss.source,
        "frequency_overlap_hz": {"min": overlap_min, "max": overlap_max},
        "frequency_window_hz": {"min": f_min, "max": f_max, "count": int(len(freq))},
        "frequency_grid_checks": frequency_grid_checks,
        "target_marker": target_marker,
        "metrics": metric_results,
        "plot_data": plot_data,
    }


def render_report(result: dict[str, Any], emx: CurveSet, hfss: CurveSet) -> str:
    lines = [
        "# EMX vs HFSS/ADS Cross-Validation Report",
        "",
        f"- Overall status: **{result['overall_status']}**",
        f"- EMX source: `{emx.source}`",
        f"- HFSS/ADS source: `{hfss.source}`",
        f"- Pass criterion: max relative error <= {result['criterion']['max_percent_error']}%",
        f"- Frequency overlap: `{result['frequency_overlap_hz']}`",
        f"- Comparison window: `{result['frequency_window_hz']}`",
        "",
        "## Frequency Grid Checks",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for name, item in result.get("frequency_grid_checks", {}).items():
        lines.append(f"| {name} | {item['status']} | {item['detail']} |")
    target_marker = result.get("target_marker")
    if target_marker:
        lines.extend(
            [
                "",
                "## Target-Frequency Marker",
                "",
                f"- Requested target: `{target_marker['requested_frequency_ghz']:.6g} GHz`",
                f"- Nearest comparison point: `{target_marker['nearest_frequency_ghz']:.6g} GHz`",
                f"- Frequency error: `{target_marker['frequency_error_ghz']:.6g} GHz`",
                f"- Status: **{target_marker['status']}**",
                "",
                "| Metric | Status | EMX | HFSS/ADS | Abs error | Percent error |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for metric in METRICS:
            item = target_marker["metrics"][metric]
            lines.append(
                f"| {metric} | {item['status']} | {item['emx']:.6g} | {item['hfss_ads']:.6g} | "
                f"{item['abs_error']:.6g} | {item['percent_error']:.6g}% |"
            )
    lines.extend(
        [
            "",
            "## Metric Error Checks",
            "",
        ]
    )
    lines.extend(
        [
        "| Metric | Status | Max abs error | Mean abs error | Max percent error | Mean percent error |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for metric, item in result["metrics"].items():
        lines.append(
            f"| {metric} | {item['status']} | {item['max_abs_error']:.6g} | "
            f"{item['mean_abs_error']:.6g} | {item['max_percent_error']:.6g}% | "
            f"{item['mean_percent_error']:.6g}% |"
        )
    lines.append("")
    lines.append("Use this report as the final cross-simulator validity gate for production data.")
    return "\n".join(lines)


def _frequency_grid_checks(
    *,
    reference_freq: np.ndarray,
    hfss_ads_freq: np.ndarray,
    overlap_min_hz: float,
    overlap_max_hz: float,
    window_start_hz: float,
    window_stop_hz: float,
    expected_start_hz: float | None,
    expected_stop_hz: float | None,
    expected_step_hz: float | None,
    expected_points: int | None,
    tolerance_hz: float,
    require_matching_frequency_grid: bool,
) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    tolerance_hz = float(tolerance_hz)
    checks["comparison point count"] = _grid_check(
        len(reference_freq) > 0,
        f"count={len(reference_freq)}",
    )
    start_margin_hz = float(window_start_hz) - float(overlap_min_hz)
    stop_margin_hz = float(overlap_max_hz) - float(window_stop_hz)
    checks["ADS no-extrapolation coverage"] = _grid_check(
        start_margin_hz >= -tolerance_hz and stop_margin_hz >= -tolerance_hz,
        "requested_window_hz="
        f"{float(window_start_hz)}-{float(window_stop_hz)}, "
        f"covered_overlap_hz={float(overlap_min_hz)}-{float(overlap_max_hz)}, "
        f"start_margin_hz={start_margin_hz}, stop_margin_hz={stop_margin_hz}",
    )
    if expected_points is not None:
        checks["expected frequency points"] = _grid_check(
            len(reference_freq) == int(expected_points),
            f"expected={int(expected_points)}, actual={len(reference_freq)}",
        )
    if expected_step_hz is not None:
        diffs = np.diff(reference_freq)
        if len(diffs) == 0:
            max_step_error = math.inf
        else:
            max_step_error = float(np.max(np.abs(diffs - float(expected_step_hz))))
        checks["expected frequency step"] = _grid_check(
            math.isfinite(max_step_error) and max_step_error <= tolerance_hz,
            f"expected_step_hz={float(expected_step_hz)}, max_step_error_hz={max_step_error}, tolerance_hz={tolerance_hz}",
        )
    if expected_start_hz is not None:
        start_error = abs(float(reference_freq[0]) - float(expected_start_hz)) if len(reference_freq) else math.inf
        checks["expected window start point"] = _grid_check(
            start_error <= tolerance_hz,
            f"expected_start_hz={float(expected_start_hz)}, actual_start_hz={float(reference_freq[0]) if len(reference_freq) else None}, error_hz={start_error}",
        )
    if expected_stop_hz is not None:
        stop_error = abs(float(reference_freq[-1]) - float(expected_stop_hz)) if len(reference_freq) else math.inf
        checks["expected window stop point"] = _grid_check(
            stop_error <= tolerance_hz,
            f"expected_stop_hz={float(expected_stop_hz)}, actual_stop_hz={float(reference_freq[-1]) if len(reference_freq) else None}, error_hz={stop_error}",
        )
    if require_matching_frequency_grid:
        if len(reference_freq) != len(hfss_ads_freq):
            detail = f"emx_reference_count={len(reference_freq)}, hfss_ads_count={len(hfss_ads_freq)}"
            passed = False
        else:
            max_grid_error = float(np.max(np.abs(reference_freq - hfss_ads_freq))) if len(reference_freq) else math.inf
            detail = f"count={len(reference_freq)}, max_grid_error_hz={max_grid_error}, tolerance_hz={tolerance_hz}"
            passed = math.isfinite(max_grid_error) and max_grid_error <= tolerance_hz
        checks["matching HFSS/ADS frequency grid"] = _grid_check(passed, detail)
    return checks


def _grid_check(passed: bool, detail: str) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "detail": detail}


def maybe_plot(result: dict[str, Any], out_dir: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    freq_ghz = np.asarray(result["plot_data"]["freq_hz"], dtype=float) / 1.0e9
    for metric in METRICS:
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot(freq_ghz, result["plot_data"]["emx"][metric], label="EMX", linewidth=2)
        ax.plot(freq_ghz, result["plot_data"]["hfss_ads"][metric], label="HFSS/ADS", linewidth=2, linestyle="--")
        ax.set_xlabel("Frequency (GHz)")
        ax.set_ylabel(metric)
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(out_dir / f"{metric}_comparison.png", dpi=160)
        plt.close(fig)


def _target_marker_summary(
    freq_hz: np.ndarray,
    plot_data: dict[str, Any],
    *,
    target_hz: float | None,
    tolerance_hz: float | None,
    max_percent_error: float,
) -> dict[str, Any] | None:
    if target_hz is None:
        return None
    freq = np.asarray(freq_hz, dtype=float)
    if len(freq) == 0:
        return {
            "status": "FAIL",
            "requested_frequency_hz": float(target_hz),
            "requested_frequency_ghz": float(target_hz) / 1.0e9,
            "nearest_frequency_hz": None,
            "nearest_frequency_ghz": None,
            "frequency_error_hz": None,
            "frequency_error_ghz": None,
            "tolerance_hz": None if tolerance_hz is None else float(tolerance_hz),
            "metrics": {},
        }
    idx = int(np.argmin(np.abs(freq - float(target_hz))))
    nearest = float(freq[idx])
    error_hz = abs(nearest - float(target_hz))
    tol = float(tolerance_hz) if tolerance_hz is not None else math.inf
    marker_metrics: dict[str, dict[str, Any]] = {}
    for metric in METRICS:
        emx_values = np.asarray(plot_data["emx"][metric], dtype=float)
        hfss_values = np.asarray(plot_data["hfss_ads"][metric], dtype=float)
        emx_value = float(emx_values[idx])
        hfss_value = float(hfss_values[idx])
        abs_error = abs(hfss_value - emx_value)
        floor = _relative_error_floor(metric, emx_values)
        percent_error = abs_error / max(abs(emx_value), floor) * 100.0
        marker_metrics[metric] = {
            "status": "PASS" if percent_error <= float(max_percent_error) else "FAIL",
            "emx": emx_value,
            "hfss_ads": hfss_value,
            "abs_error": abs_error,
            "percent_error": percent_error,
        }
    frequency_status = "PASS" if error_hz <= tol else "FAIL"
    metric_status = "PASS" if all(item["status"] == "PASS" for item in marker_metrics.values()) else "FAIL"
    return {
        "status": "PASS" if frequency_status == "PASS" and metric_status == "PASS" else "FAIL",
        "frequency_status": frequency_status,
        "metric_status": metric_status,
        "requested_frequency_hz": float(target_hz),
        "requested_frequency_ghz": float(target_hz) / 1.0e9,
        "nearest_frequency_hz": nearest,
        "nearest_frequency_ghz": nearest / 1.0e9,
        "frequency_error_hz": error_hz,
        "frequency_error_ghz": error_hz / 1.0e9,
        "tolerance_hz": None if tolerance_hz is None else float(tolerance_hz),
        "tolerance_ghz": None if tolerance_hz is None else float(tolerance_hz) / 1.0e9,
        "metrics": marker_metrics,
    }


def export_comparison_tables(result: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    curve_path = out_dir / "emx_hfss_ads_curves.csv"
    error_path = out_dir / "emx_hfss_ads_metric_errors.csv"
    freq = np.asarray(result["plot_data"]["freq_hz"], dtype=float)
    fieldnames = ["freq_hz", "freq_ghz"]
    for metric in METRICS:
        fieldnames.extend(
            [
                f"emx_{metric}",
                f"hfss_ads_{metric}",
                f"abs_error_{metric}",
                f"percent_error_{metric}",
            ]
        )
    with curve_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row_idx, f_hz in enumerate(freq):
            row: dict[str, float] = {"freq_hz": float(f_hz), "freq_ghz": float(f_hz / 1.0e9)}
            for metric in METRICS:
                emx_values = np.asarray(result["plot_data"]["emx"][metric], dtype=float)
                hfss_values = np.asarray(result["plot_data"]["hfss_ads"][metric], dtype=float)
                abs_error = abs(float(hfss_values[row_idx] - emx_values[row_idx]))
                floor = _relative_error_floor(metric, emx_values)
                pct_error = abs_error / max(abs(float(emx_values[row_idx])), floor) * 100.0
                row[f"emx_{metric}"] = float(emx_values[row_idx])
                row[f"hfss_ads_{metric}"] = float(hfss_values[row_idx])
                row[f"abs_error_{metric}"] = abs_error
                row[f"percent_error_{metric}"] = pct_error
            writer.writerow(row)

    metric_fields = [
        "metric",
        "status",
        "max_abs_error",
        "mean_abs_error",
        "max_percent_error",
        "mean_percent_error",
        "emx_min",
        "emx_max",
        "hfss_min",
        "hfss_max",
    ]
    with error_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_fields)
        writer.writeheader()
        for metric in METRICS:
            item = result["metrics"][metric]
            writer.writerow({"metric": metric, **{field: item[field] for field in metric_fields[2:]}, "status": item["status"]})
    return curve_path, error_path


def export_target_marker_table(result: dict[str, Any], out_dir: Path) -> Path | None:
    marker = result.get("target_marker")
    if not marker:
        return None
    path = out_dir / "emx_hfss_ads_target_marker_metrics.csv"
    fields = [
        "requested_frequency_ghz",
        "nearest_frequency_ghz",
        "frequency_error_ghz",
        "status",
        "metric",
        "metric_status",
        "emx",
        "hfss_ads",
        "abs_error",
        "percent_error",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for metric in METRICS:
            item = (marker.get("metrics") or {}).get(metric) or {}
            writer.writerow(
                {
                    "requested_frequency_ghz": marker.get("requested_frequency_ghz"),
                    "nearest_frequency_ghz": marker.get("nearest_frequency_ghz"),
                    "frequency_error_ghz": marker.get("frequency_error_ghz"),
                    "status": marker.get("status"),
                    "metric": metric,
                    "metric_status": item.get("status"),
                    "emx": item.get("emx"),
                    "hfss_ads": item.get("hfss_ads"),
                    "abs_error": item.get("abs_error"),
                    "percent_error": item.get("percent_error"),
                }
            )
    return path


def write_formula_note(result: dict[str, Any], emx: CurveSet, hfss: CurveSet, args: argparse.Namespace, out_dir: Path) -> Path:
    path = out_dir / "ads_python_formula_crosscheck.md"
    lines = [
        "# ADS/Python Formula Cross-Check",
        "",
        f"- Generated UTC: `{FIXED_COMPARE_GENERATED_UTC}`",
        f"- EMX input: `{emx.source}`",
        f"- HFSS/ADS input: `{hfss.source}`",
        f"- EMX differential port pairs: `{args.emx_port_pairs}`",
        f"- HFSS/ADS differential port pairs: `{args.hfss_port_pairs}`",
        f"- Unused selected-network ports grounded before extraction: `{bool(args.ground_unused_ports)}`",
        f"- Pass criterion: max relative error <= `{result['criterion']['max_percent_error']}%` for every metric.",
        f"- Frequency overlap available: `{result['frequency_overlap_hz']}`",
        f"- Frequency window used for the gate: `{result['frequency_window_hz']}`",
        "",
        "## Frequency grid checks",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for name, item in result.get("frequency_grid_checks", {}).items():
        lines.append(f"| {name} | {item['status']} | {item['detail']} |")
    lines.extend(
        [
            "",
            "## Standards and source basis",
            "",
            "- Touchstone is an n-port network-parameter exchange format. This workflow follows the Touchstone 2.1 convention that `.s4p` carries four-port network data and uses the option-line or `[Reference]` impedance when converting S to Z.",
            "- The four physical transformer terminals are interpreted as two differential pairs only after the single-ended four-port matrix is read; this is why port pairing must be recorded with every accepted plot.",
            "- These equations are ADS-equivalent post-processing of the same Touchstone data. They are not a replacement for HFSS geometry construction, EM simulation, or ADS GUI review.",
            "",
            "## Touchstone to differential Z",
            "",
            "The input files can be 4-port single-ended Touchstone files or 2-port differential Touchstone files. The parser uses the Touchstone reference impedance from the option line or [Reference] keyword; files without an explicit reference default to 50 ohm. For .s2p input, the parser honors Touchstone two-port data ordering: default S11,S21,S12,S22 unless [Two-Port Data Order] requests S11,S12,S21,S22.",
            "For S8P files whose power-line ports are grounded in ADS, pass `--ground-unused-ports`; the script first shorts every port outside the selected differential pairs, then converts the resulting four-port network to differential Z.",
            "",
            "```text",
            "Z_single = sqrt(Z0) * (I + S) * inv(I - S) * sqrt(Z0)",
            "```",
            "",
            "For port pairs `1,2:3,4`, the differential-current transform is:",
            "",
            "```text",
            "T = [[ 1,  0],",
            "     [-1,  0],",
            "     [ 0,  1],",
            "     [ 0, -1]]",
            "Z_diff = transpose(T) * Z_single * T",
            "```",
            "",
            "This convention applies +I/-I on each pair and measures V+ - V-. It matches the Python gate used here and is the convention that should be mirrored in ADS when comparing curves from the exported S4P.",
            "",
            "## ADS Data Display equation template",
            "",
            "Use these expressions in ADS Data Display after an S-parameter simulation/import has produced a 4-port Z matrix with ports ordered as primary+ primary- secondary+ secondary-. The expression names deliberately match the Python gate.",
            "",
            "```text",
            "Zp = Z11 - Z12 + Z22 - Z21",
            "Zs = Z33 - Z34 + Z44 - Z43",
            "Zm = Z31 - Z32 + Z42 - Z41",
            "Lp = 1/(2*pi*freq)*imag(Zp)",
            "Ls = 1/(2*pi*freq)*imag(Zs)",
            "M  = 1/(2*pi*freq)*imag(Zm)",
            "K  = M / sqrt(abs(Lp * Ls))",
            "Kw = K",
            "Qp = imag(Zp) / real(Zp)",
            "Qs = imag(Zs) / real(Zs)",
            "Q  = min(Qp, Qs)",
            "```",
            "",
            "For a different port order, first rewrite Zp/Zs/Zm from the selected differential pairs and record that port pairing next to the figure.",
            "",
            "## Extracted metrics",
            "",
            "For each frequency point:",
            "",
            "```text",
            "omega = 2*pi*f",
            "Lp = imag(Zdiff[1,1]) / omega",
            "Ls = imag(Zdiff[2,2]) / omega",
            "M  = imag(Zdiff[2,1]) / omega   # ADS formula: imag(Z31 - Z32 + Z42 - Z41) / omega for port pairs 1,2:3,4",
            "k  = M / sqrt(abs(Lp * Ls))",
            "Kw = k",
            "Qp = imag(Zdiff[1,1]) / real(Zdiff[1,1])",
            "Qs = imag(Zdiff[2,2]) / real(Zdiff[2,2])",
            "Q  = min(Qp, Qs)",
            "```",
            "",
            "Lp and Ls are exported in nH in the CSV. Q is the scalar training/reporting feature min(Qp, Qs). Kw is the same coupling coefficient as k/K. The k sign follows the selected port polarity; if ADS plots positive K by default, compare `abs(k)` for display, but keep a fixed polarity for pass/fail. If K fails while L/Q pass, first audit port polarity and port-pair mapping before interpreting it as a process or meshing issue.",
            "",
            "## Relative-error rule",
            "",
            "```text",
            "percent_error = abs(HFSS_or_ADS - EMX) / max(abs(EMX), floor) * 100",
            "floor(k) = 0.02",
            "floor(Q, Qp, Qs) = 0.2",
            "floor(Lp, Ls) = max(median(abs(EMX_metric))*1e-3, 1e-6)",
            "```",
            "",
            "The small floors prevent unstable percentage errors when the reference approaches zero; for this selected sample, the EMX values are not near zero for the reported metrics.",
            "",
            "## Current gate result",
            "",
            "| Metric | Status | Max percent error | Mean percent error |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for metric in METRICS:
        item = result["metrics"][metric]
        lines.append(
            f"| {metric} | {item['status']} | {item['max_percent_error']:.6g}% | {item['mean_percent_error']:.6g}% |"
        )
    target_marker = result.get("target_marker")
    if target_marker:
        lines.extend(
            [
                "",
                "## Target-frequency marker",
                "",
                f"- Requested target: `{target_marker['requested_frequency_ghz']:.6g} GHz`",
                f"- Nearest comparison point: `{target_marker['nearest_frequency_ghz']:.6g} GHz`",
                f"- Marker status: **{target_marker['status']}**",
                "",
                "| Metric | Status | EMX | HFSS/ADS | Percent error |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
        )
        for metric in METRICS:
            item = target_marker["metrics"][metric]
            lines.append(
                f"| {metric} | {item['status']} | {item['emx']:.6g} | {item['hfss_ads']:.6g} | {item['percent_error']:.6g}% |"
            )
    lines.extend(
        [
            "",
            f"Overall status: **{result['overall_status']}**.",
            "",
            "Use `emx_hfss_ads_curves.csv` for point-by-point ADS/Python cross-checks and `emx_hfss_ads_metric_errors.csv` for the 10% gate summary.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_manifest(out_dir: Path, *, input_paths: list[Path], output_paths: list[Path]) -> Path:
    path = out_dir / "ads_python_crosscheck_manifest.json"
    payload = {
        "generated_utc": FIXED_COMPARE_GENERATED_UTC,
        "inputs": [_file_record(item) for item in input_paths],
        "outputs": [_file_record(item) for item in output_paths],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _file_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    exists = resolved.exists()
    return {
        "path": str(resolved),
        "exists": exists,
        "bytes": resolved.stat().st_size if exists else None,
        "sha256": _sha256(resolved) if exists else None,
    }


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    return num / np.where(np.abs(den) < 1.0e-18, np.nan, den)


def _interp(freq: np.ndarray, source_freq: np.ndarray, values: np.ndarray) -> np.ndarray:
    order = np.argsort(source_freq)
    return np.interp(freq, source_freq[order], np.asarray(values, dtype=float)[order])


def _relative_error_floor(metric: str, reference: np.ndarray) -> float:
    if metric in {"k", "kw"}:
        return 0.02
    if metric in {"q", "qp", "qs"}:
        return 0.2
    return max(float(np.nanmedian(np.abs(reference))) * 1.0e-3, 1.0e-6)


def _find_col(lower_to_name: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in lower_to_name:
            return lower_to_name[alias]
    return None


def _float(value: str) -> float:
    item = float(value)
    if not math.isfinite(item):
        raise ValueError(f"Non-finite value: {value!r}")
    return item


def _ghz_to_hz(value: float | None) -> float | None:
    return None if value is None else float(value) * 1.0e9


if __name__ == "__main__":
    sys.exit(main())
