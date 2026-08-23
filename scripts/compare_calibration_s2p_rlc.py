#!/usr/bin/env python3
"""Compare EMX and HFSS two-port calibration-line Touchstone files.

This script is intentionally separate from transformer Lp/Ls/Q/K extraction.
For a straight calibration line, the main evidence is the two-terminal series
impedance and the shunt capacitance to the local ground/reference environment.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.network_analysis import s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402

METRICS = ("series_r_ohm", "series_l_nh", "series_q", "shunt_c_ff")


@dataclass(frozen=True)
class CalibrationCurves:
    source: str
    freq_hz: np.ndarray
    metrics: dict[str, np.ndarray]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    emx = load_calibration_curves(Path(args.emx))
    hfss = load_calibration_curves(Path(args.hfss))
    result = compare_curves(
        emx,
        hfss,
        target_hz=args.target_ghz * 1.0e9,
        max_percent_error=args.max_percent_error,
        target_tolerance_hz=args.target_frequency_tolerance_ghz * 1.0e9,
        require_matching_frequency_grid=args.require_matching_frequency_grid,
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "calibration_s2p_rlc_comparison_summary.json"
    report_path = out_dir / "calibration_s2p_rlc_comparison_report.md"
    curves_csv_path = out_dir / "calibration_s2p_curve_metrics.csv"
    marker_csv_path = out_dir / "calibration_s2p_marker_metrics.csv"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    report_path.write_text(render_report(result), encoding="utf-8")
    write_curve_csv(curves_csv_path, emx, hfss)
    write_marker_csv(marker_csv_path, result)
    print(f"overall_status={result['overall_status']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"marker_csv={marker_csv_path}")
    return 2 if result["overall_status"] == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx", required=True, help="Reference EMX .s2p calibration line")
    parser.add_argument("--hfss", required=True, help="HFSS .s2p calibration line")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--target-frequency-tolerance-ghz", type=float, default=0.05)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--require-matching-frequency-grid", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def load_calibration_curves(path: Path) -> CalibrationCurves:
    touchstone = load_touchstone(path)
    s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
    if s_matrix.shape[1:] != (2, 2):
        raise ValueError(f"Stage-1 calibration expects .s2p data, got shape {s_matrix.shape} from {path}")
    freq_hz = np.asarray(touchstone.freqs_hz, dtype=float)
    z = s_to_z(s_matrix, z0=touchstone.reference_impedance_ohm)
    y = np.linalg.inv(z)
    omega = 2.0 * math.pi * freq_hz

    # Differential/equal-and-opposite current through the two line terminals.
    z_series = z[:, 0, 0] - z[:, 0, 1] - z[:, 1, 0] + z[:, 1, 1]
    y_common = y[:, 0, 0] + y[:, 0, 1] + y[:, 1, 0] + y[:, 1, 1]

    return CalibrationCurves(
        source=str(path),
        freq_hz=freq_hz,
        metrics={
            "series_r_ohm": np.real(z_series),
            "series_l_nh": np.imag(z_series) / omega * 1.0e9,
            "series_q": _safe_div(np.imag(z_series), np.real(z_series)),
            "shunt_c_ff": np.imag(y_common) / omega * 1.0e15,
        },
    )


def compare_curves(
    emx: CalibrationCurves,
    hfss: CalibrationCurves,
    *,
    target_hz: float,
    max_percent_error: float,
    target_tolerance_hz: float,
    require_matching_frequency_grid: bool = False,
) -> dict[str, Any]:
    if require_matching_frequency_grid and not _same_grid(emx.freq_hz, hfss.freq_hz):
        frequency_status = "FAIL"
        frequency_detail = "EMX and HFSS frequency grids do not match"
    else:
        frequency_status = "PASS"
        frequency_detail = "frequency grid accepted"
    emx_idx = _nearest_index(emx.freq_hz, target_hz)
    hfss_idx = _nearest_index(hfss.freq_hz, target_hz)
    target_frequency_error_hz = max(abs(float(emx.freq_hz[emx_idx] - target_hz)), abs(float(hfss.freq_hz[hfss_idx] - target_hz)))
    target_status = "PASS" if target_frequency_error_hz <= target_tolerance_hz else "FAIL"
    metrics: dict[str, Any] = {}
    overall_status = "PASS" if frequency_status == "PASS" and target_status == "PASS" else "FAIL"
    for metric in METRICS:
        ref = float(emx.metrics[metric][emx_idx])
        got = float(hfss.metrics[metric][hfss_idx])
        abs_error = abs(got - ref)
        percent_error = _percent_error(got, ref)
        status = "PASS" if percent_error <= max_percent_error else "FAIL"
        if status == "FAIL":
            overall_status = "FAIL"
        metrics[metric] = {
            "emx": ref,
            "hfss": got,
            "abs_error": abs_error,
            "percent_error": percent_error,
            "status": status,
        }
    return {
        "overall_status": overall_status,
        "gate_percent_error": max_percent_error,
        "emx_source": emx.source,
        "hfss_source": hfss.source,
        "target_frequency_hz": target_hz,
        "emx_nearest_frequency_hz": float(emx.freq_hz[emx_idx]),
        "hfss_nearest_frequency_hz": float(hfss.freq_hz[hfss_idx]),
        "target_frequency_error_hz": target_frequency_error_hz,
        "target_frequency_status": target_status,
        "frequency_grid_status": frequency_status,
        "frequency_grid_detail": frequency_detail,
        "metrics": metrics,
    }


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "# Stage 1 calibration S2P R/L/C comparison",
        "",
        f"- Overall status: **{result['overall_status']}**",
        f"- Gate: <= {result['gate_percent_error']:.3g}% at {result['target_frequency_hz'] / 1.0e9:.3g} GHz",
        f"- Frequency grid: {result['frequency_grid_status']} ({result['frequency_grid_detail']})",
        "",
        "| Metric | EMX | HFSS | Percent error | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for metric, item in result["metrics"].items():
        lines.append(
            f"| `{metric}` | {item['emx']:.6g} | {item['hfss']:.6g} | {item['percent_error']:.3f}% | {item['status']} |"
        )
    lines.extend(
        [
            "",
            "Definitions:",
            "",
            "- `series_r_ohm`, `series_l_nh`, `series_q`: extracted from `Z11-Z12-Z21+Z22`.",
            "- `shunt_c_ff`: extracted from `(Y11+Y12+Y21+Y22)/(j*omega)`.",
            "- This gate calibrates line/ground/dielectric equivalence; it is not the final transformer `Lp/Ls/Q/Kw` gate.",
            "",
        ]
    )
    return "\n".join(lines)


def write_curve_csv(path: Path, emx: CalibrationCurves, hfss: CalibrationCurves) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "freq_ghz", *METRICS])
        writer.writeheader()
        for curves in [emx, hfss]:
            for idx, freq_hz in enumerate(curves.freq_hz):
                row = {"source": curves.source, "freq_ghz": float(freq_hz) / 1.0e9}
                row.update({metric: float(curves.metrics[metric][idx]) for metric in METRICS})
                writer.writerow(row)


def write_marker_csv(path: Path, result: dict[str, Any]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["metric", "emx", "hfss", "abs_error", "percent_error", "status"],
        )
        writer.writeheader()
        for metric, item in result["metrics"].items():
            writer.writerow({"metric": metric, **item})


def _same_grid(a: np.ndarray, b: np.ndarray, atol_hz: float = 1.0e5) -> bool:
    return a.shape == b.shape and bool(np.allclose(a, b, rtol=0.0, atol=atol_hz))


def _nearest_index(freq_hz: np.ndarray, target_hz: float) -> int:
    return int(np.argmin(np.abs(np.asarray(freq_hz, dtype=float) - target_hz)))


def _percent_error(value: float, reference: float) -> float:
    denom = max(abs(reference), 1.0e-30)
    return abs(value - reference) / denom * 100.0


def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    return np.divide(num, den, out=np.full_like(num, np.nan, dtype=float), where=np.abs(den) > 1.0e-30)


if __name__ == "__main__":
    raise SystemExit(main())
