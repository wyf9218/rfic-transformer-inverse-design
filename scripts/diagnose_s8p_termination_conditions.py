#!/usr/bin/env python3
"""Diagnose unused-port termination assumptions for S8P transformer extraction.

This script does not replace the ADS-equivalent comparison flow. It is a
diagnostic helper for checking whether the EMX/HFSS disagreement is sensitive to
how the four unused S8P ports are terminated before extracting Lp/Ls/Q/Kw.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.analysis import multiport_single_ended_to_differential_z  # noqa: E402
from rfic_transformer_inverse_design.network_analysis import reduce_s_params_by_shorting, s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


METRICS = ("lp_nh", "ls_nh", "q_min", "qp", "qs", "k_signed", "kw_abs")


@dataclass(frozen=True)
class CurveSet:
    freq_hz: np.ndarray
    metrics: dict[str, np.ndarray]


def main() -> int:
    args = _parse_args()
    pairs = _parse_port_pairs(args.port_pairs)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    for condition in _conditions():
        emx = _load_curves(Path(args.emx), pairs, condition)
        hfss = _load_curves(Path(args.hfss), pairs, condition)
        rows.extend(_compare_at_marker(condition["name"], emx, hfss, args.target_ghz * 1.0e9))

    csv_path = out_dir / "s8p_unused_port_termination_marker_diagnostic.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    report_path = out_dir / "s8p_unused_port_termination_diagnostic.md"
    report_path.write_text(_render_report(rows, args), encoding="utf-8")

    print(f"csv={csv_path}")
    print(f"report={report_path}")
    for row in rows:
        if row["metric"] in {"lp_nh", "ls_nh", "q_min", "kw_abs"}:
            print(
                f"{row['termination']:<22s} {row['metric']:<8s} "
                f"emx={row['emx']:.6g} hfss={row['hfss']:.6g} "
                f"err={row['percent_error']:.3f}%"
            )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx", required=True, help="EMX S8P path")
    parser.add_argument("--hfss", required=True, help="HFSS S8P path")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--port-pairs", required=True, help="Differential pairs, e.g. 1,4:5,6")
    parser.add_argument("--target-ghz", type=float, default=15.0)
    return parser.parse_args()


def _conditions() -> list[dict[str, object]]:
    return [
        {
            "name": "unused_short_gamma_-1",
            "kind": "loaded_s",
            "gamma_load": -1.0,
            "note": "Unused ports shorted to ground in the S-parameter reduction.",
        },
        {
            "name": "unused_matched_gamma_0",
            "kind": "loaded_s",
            "gamma_load": 0.0,
            "note": "Unused ports terminated by the Touchstone reference impedance.",
        },
        {
            "name": "unused_open_gamma_+1",
            "kind": "loaded_s",
            "gamma_load": 1.0,
            "note": "Unused ports open-circuited as an S-parameter load.",
        },
        {
            "name": "unused_open_z_projection",
            "kind": "open_z",
            "gamma_load": None,
            "note": "Full S converted to Z first, then selected differential currents are projected with unused-port currents set to zero.",
        },
    ]


def _parse_port_pairs(text: str) -> tuple[tuple[int, int], tuple[int, int]]:
    first, second = text.split(":", 1)
    a, b = (int(item.strip()) - 1 for item in first.split(",", 1))
    c, d = (int(item.strip()) - 1 for item in second.split(",", 1))
    return (a, b), (c, d)


def _load_curves(path: Path, pairs: tuple[tuple[int, int], tuple[int, int]], condition: dict[str, object]) -> CurveSet:
    result = load_touchstone(path)
    s_matrix = np.asarray(result.s_matrix, dtype=np.complex128)
    freq_hz = np.asarray(result.freqs_hz, dtype=float)
    flat_ports = [port for pair in pairs for port in pair]

    if condition["kind"] == "open_z":
        z_single = s_to_z(s_matrix, z0=result.reference_impedance_ohm)
        z_diff = multiport_single_ended_to_differential_z(z_single, pairs)
    else:
        ports_to_short = [port for port in range(s_matrix.shape[1]) if port not in set(flat_ports)]
        reduced_s = reduce_s_params_by_shorting(
            s_matrix,
            ports_to_short=ports_to_short,
            ports_to_keep=flat_ports,
            gamma_load=complex(condition["gamma_load"]),
        )
        z_reduced = s_to_z(reduced_s, z0=result.reference_impedance_ohm)
        z_diff = multiport_single_ended_to_differential_z(z_reduced, ((0, 1), (2, 3)))

    return CurveSet(freq_hz=freq_hz, metrics=_extract_metrics(freq_hz, z_diff))


def _extract_metrics(freq_hz: np.ndarray, z_diff: np.ndarray) -> dict[str, np.ndarray]:
    omega = 2.0 * math.pi * freq_hz
    z11 = z_diff[:, 0, 0]
    z22 = z_diff[:, 1, 1]
    z21 = z_diff[:, 1, 0]
    lp_h = np.imag(z11) / omega
    ls_h = np.imag(z22) / omega
    mutual_h = np.imag(z21) / omega
    denom = np.sqrt(np.maximum(np.abs(lp_h * ls_h), 1.0e-30))
    k_signed = mutual_h / denom
    qp = _safe_div(np.imag(z11), np.real(z11))
    qs = _safe_div(np.imag(z22), np.real(z22))
    return {
        "lp_nh": lp_h * 1.0e9,
        "ls_nh": ls_h * 1.0e9,
        "q_min": np.minimum(qp, qs),
        "qp": qp,
        "qs": qs,
        "k_signed": k_signed,
        "kw_abs": np.abs(k_signed),
    }


def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    out = np.full_like(num, np.nan, dtype=float)
    mask = np.abs(den) > 1.0e-18
    out[mask] = np.real(num[mask] / den[mask])
    return out


def _compare_at_marker(condition_name: str, emx: CurveSet, hfss: CurveSet, target_hz: float) -> list[dict[str, object]]:
    emx_idx = int(np.argmin(np.abs(emx.freq_hz - target_hz)))
    hfss_idx = int(np.argmin(np.abs(hfss.freq_hz - target_hz)))
    rows: list[dict[str, object]] = []
    for metric in METRICS:
        emx_value = float(emx.metrics[metric][emx_idx])
        hfss_value = float(hfss.metrics[metric][hfss_idx])
        abs_error = abs(hfss_value - emx_value)
        denom = max(abs(emx_value), 1.0e-30)
        rows.append(
            {
                "termination": condition_name,
                "metric": metric,
                "freq_ghz": float(emx.freq_hz[emx_idx] / 1.0e9),
                "emx": emx_value,
                "hfss": hfss_value,
                "abs_error": abs_error,
                "percent_error": abs_error / denom * 100.0,
            }
        )
    return rows


def _render_report(rows: list[dict[str, object]], args: argparse.Namespace) -> str:
    lines = [
        "# S8P unused-port termination diagnostic",
        "",
        f"- EMX: `{args.emx}`",
        f"- HFSS: `{args.hfss}`",
        f"- Differential pairs: `{args.port_pairs}`",
        f"- Marker: {args.target_ghz:g} GHz",
        "",
        "| Termination | Lp err | Ls err | Q err | Kw err | Main interpretation |",
        "|---|---:|---:|---:|---:|---|",
    ]
    by_term: dict[str, dict[str, dict[str, object]]] = {}
    for row in rows:
        by_term.setdefault(str(row["termination"]), {})[str(row["metric"])] = row
    for term, metric_rows in by_term.items():
        lp = metric_rows["lp_nh"]["percent_error"]
        ls = metric_rows["ls_nh"]["percent_error"]
        q = metric_rows["q_min"]["percent_error"]
        kw = metric_rows["kw_abs"]["percent_error"]
        if float(lp) > 20.0 and float(ls) > 20.0 and float(kw) < 5.0:
            interp = "Coupling ratio is stable, but HFSS absolute L scale is lower."
        elif float(lp) < 10.0 and float(ls) < 10.0 and float(kw) < 10.0:
            interp = "This termination is a plausible ADS-equivalent candidate."
        else:
            interp = "Termination change does not by itself close the validation gap."
        lines.append(f"| `{term}` | {lp:.2f}% | {ls:.2f}% | {q:.2f}% | {kw:.2f}% | {interp} |")
    lines.append("")
    lines.append("This file is a diagnostic record only; the project acceptance gate remains the advisor-approved ADS-equivalent extraction.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
