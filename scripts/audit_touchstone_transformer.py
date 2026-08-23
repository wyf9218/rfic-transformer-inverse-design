#!/usr/bin/env python3
"""Audit one transformer Touchstone file before ADS/HFSS-vs-EMX validation.

This script is a preflight gate. It does not prove that HFSS matches EMX, but it
does catch common causes of misleading ADS plots: empty or truncated files,
wrong frequency coverage, non-finite values, non-reciprocal/non-passive S
matrices, and target-frequency L/Q/K values that are not transformer-like.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
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

from compare_emx_hfss_ads import multiport_z_to_differential_z, parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.analysis import multiport_s_to_grounded_differential_z  # noqa: E402
from rfic_transformer_inverse_design.network_analysis import s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


@dataclass(frozen=True)
class MetricCurves:
    freq_hz: np.ndarray
    lp_nh: np.ndarray
    ls_nh: np.ndarray
    m_nh: np.ndarray
    k: np.ndarray
    qp: np.ndarray
    qs: np.ndarray


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    touchstone_path = Path(args.touchstone).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[Check] = []
    metrics: MetricCurves | None = None
    matrix_quality: dict[str, Any] = {}
    differential_z_quality: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    touchstone = None

    try:
        provenance = _touchstone_provenance(touchstone_path)
        checks.extend(_source_checks(provenance, args))
        touchstone = load_touchstone(touchstone_path)
        s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
        freqs_hz = np.asarray(touchstone.freqs_hz, dtype=float)
        checks.extend(_matrix_checks(s_matrix, freqs_hz, args))
        checks.extend(_frequency_checks(freqs_hz, args))
        matrix_quality = _matrix_quality(freqs_hz, s_matrix)
        differential_z = _differential_z_from_s(
            s_matrix,
            touchstone.reference_impedance_ohm,
            args.port_pairs,
            ground_unused_ports=bool(args.ground_unused_ports),
        )
        differential_z_quality = _differential_z_quality(differential_z)
        checks.extend(_differential_z_checks(differential_z_quality, args))
        metrics = _extract_metric_curves(s_matrix, freqs_hz, args, z0=touchstone.reference_impedance_ohm)
        checks.extend(_metric_checks(metrics, args))
    except Exception as exc:
        checks.append(Check("FAIL", "Touchstone parse/extract", f"{type(exc).__name__}: {exc}"))

    overall_status = "FAIL" if any(item.status == "FAIL" for item in checks) else "PASS"
    summary = _build_summary(
        touchstone_path=touchstone_path,
        out_dir=out_dir,
        checks=checks,
        overall_status=overall_status,
        args=args,
        touchstone=touchstone,
        matrix_quality=matrix_quality,
        differential_z_quality=differential_z_quality,
        provenance=provenance,
        metrics=metrics,
    )
    summary_path = out_dir / "touchstone_transformer_audit_summary.json"
    report_path = out_dir / "touchstone_transformer_audit_report.md"
    csv_path = out_dir / "touchstone_transformer_metrics.csv"
    manifest_path = out_dir / "touchstone_transformer_audit_manifest.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    if metrics is not None:
        _write_metrics_csv(csv_path, metrics)
        if args.plot:
            _write_plots(out_dir, matrix_quality, metrics, args)
    manifest_path.write_text(
        json.dumps(
            {
                "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "inputs": [_file_record(touchstone_path)],
                "outputs": [
                    _file_record(path)
                    for path in (
                        summary_path,
                        report_path,
                        csv_path,
                        out_dir / "touchstone_matrix_quality.png",
                        out_dir / "touchstone_ads_equivalent_metrics.png",
                    )
                    if path.exists()
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"overall_status={overall_status}")
    print(f"report={report_path}")
    print(f"summary={summary_path}")
    if csv_path.exists():
        print(f"metrics_csv={csv_path}")
    print(f"manifest={manifest_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("touchstone", help="Input .sNp Touchstone file")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-ports", type=int, default=4, help="Expected number of Touchstone ports")
    parser.add_argument(
        "--port-pairs",
        help="Differential port pairs, e.g. 1,4:5,6. If omitted, 4-port files default to 1,2:3,4; >4-port files require explicit pairs.",
    )
    parser.add_argument(
        "--ground-unused-ports",
        action="store_true",
        help="Short all ports outside the selected differential pairs to ground before extracting Lp/Ls/Q/K. Use for S8P power-line ports grounded in ADS.",
    )
    parser.add_argument("--expected-frequency-start-ghz", type=float)
    parser.add_argument("--expected-frequency-stop-ghz", type=float)
    parser.add_argument("--expected-frequency-step-ghz", type=float)
    parser.add_argument("--expected-frequency-points", type=int)
    parser.add_argument(
        "--expected-source-kind",
        choices=("ANY", "EMX", "HFSS", "ADS", "UNKNOWN"),
        default="ANY",
        help="Optional provenance gate inferred from Touchstone comments/header, with path as fallback",
    )
    parser.add_argument(
        "--required-sweep-start-ghz",
        type=float,
        help="ADS/HFSS post-processing sweep start that must be covered by this file",
    )
    parser.add_argument(
        "--required-sweep-stop-ghz",
        type=float,
        help="ADS/HFSS post-processing sweep stop that must be covered by this file",
    )
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-reciprocity-error", type=float, default=1.0e-6)
    parser.add_argument("--max-passivity-sigma", type=float, default=1.001)
    parser.add_argument("--max-differential-z-reciprocity-error-ohm", type=float, default=1.0e-6)
    parser.add_argument("--max-differential-z-reciprocity-relative-error", type=float, default=1.0e-6)
    parser.add_argument("--min-differential-z-real-eigenvalue-ohm", type=float, default=-1.0e-9)
    parser.add_argument("--min-differential-self-resistance-ohm", type=float, default=0.0)
    parser.add_argument("--target-frequency-ghz", type=float)
    parser.add_argument("--target-frequency-tolerance-ghz", type=float)
    parser.add_argument("--min-target-inductance-nh", type=float, default=0.0)
    parser.add_argument("--min-target-q", type=float, default=0.0)
    parser.add_argument("--min-target-abs-k", type=float, default=0.0)
    parser.add_argument("--max-target-abs-k", type=float, default=1.05)
    parser.add_argument("--positive-window-start-ghz", type=float)
    parser.add_argument("--positive-window-stop-ghz", type=float)
    parser.add_argument(
        "--min-window-abs-k",
        type=float,
        help="Minimum abs(K) required throughout the positive metric window; defaults to --min-target-abs-k",
    )
    parser.add_argument("--shape-window-start-ghz", type=float)
    parser.add_argument("--shape-window-stop-ghz", type=float)
    parser.add_argument(
        "--max-shape-spike-ratio",
        type=float,
        default=8.0,
        help="Maximum p99(abs(metric))/p50(abs(metric)) inside the shape window",
    )
    parser.add_argument(
        "--max-shape-relative-step",
        type=float,
        default=0.5,
        help="Maximum adjacent metric jump divided by median abs(metric) inside the shape window",
    )
    parser.add_argument("--plot", action="store_true", help="Write PNG evidence plots")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _touchstone_provenance(path: Path) -> dict[str, Any]:
    comments: list[str] = []
    option_line = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("!"):
                comments.append(line[1:].strip())
                continue
            if line.startswith("#"):
                option_line = line
                continue
            break
    header_text = "\n".join(comments[:200])
    path_kind = _source_kind_from_text(" ".join(path.parts))
    header_kind = _source_kind_from_text(header_text)
    inferred = header_kind if header_kind != "UNKNOWN" else path_kind
    return {
        "path": str(path),
        "path_source_kind": path_kind,
        "header_source_kind": header_kind,
        "inferred_source_kind": inferred,
        "option_line": option_line,
        "comment_line_count_scanned": len(comments),
        "header_excerpt": "\n".join(comments[:12]),
    }


def _source_kind_from_text(text: str) -> str:
    lowered = text.lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    if "hfss" in lowered or ".aedt" in lowered or "ansys" in lowered:
        return "HFSS"
    if any(token == "emx" or token.startswith("emx") for token in tokens):
        return "EMX"
    if "advanced design system" in lowered or "keysight" in lowered or any(token == "ads" for token in tokens):
        return "ADS"
    return "UNKNOWN"


def _source_checks(provenance: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    expected = str(args.expected_source_kind)
    inferred = str(provenance.get("inferred_source_kind", "UNKNOWN"))
    header_kind = str(provenance.get("header_source_kind", "UNKNOWN"))
    path_kind = str(provenance.get("path_source_kind", "UNKNOWN"))
    if expected == "ANY":
        return [
            Check(
                "WARN",
                "source identity",
                f"no expected source-kind supplied; inferred={inferred}, header={header_kind}, path={path_kind}",
            )
        ]
    if inferred == expected and (header_kind == expected or header_kind == "UNKNOWN"):
        return [
            Check(
                "PASS",
                "source identity",
                f"expected={expected}, inferred={inferred}, header={header_kind}, path={path_kind}",
            )
        ]
    return [
        Check(
            "FAIL",
            "source identity",
            f"expected={expected}, inferred={inferred}, header={header_kind}, path={path_kind}",
        )
    ]


def _matrix_checks(s_matrix: np.ndarray, freqs_hz: np.ndarray, args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    if s_matrix.ndim != 3 or s_matrix.shape[1] != s_matrix.shape[2]:
        return [Check("FAIL", "S-matrix shape", f"Expected (N,P,P), got {s_matrix.shape}")]
    ports = int(s_matrix.shape[1])
    if args.expected_ports and ports != int(args.expected_ports):
        checks.append(Check("FAIL", "port count", f"expected={args.expected_ports}, actual={ports}"))
    else:
        checks.append(Check("PASS", "port count", f"ports={ports}"))
    if len(freqs_hz) != s_matrix.shape[0]:
        checks.append(Check("FAIL", "frequency row count", f"freqs={len(freqs_hz)}, s_rows={s_matrix.shape[0]}"))
    else:
        checks.append(Check("PASS", "frequency row count", f"rows={len(freqs_hz)}"))
    if np.isfinite(freqs_hz).all() and np.isfinite(s_matrix.real).all() and np.isfinite(s_matrix.imag).all():
        checks.append(Check("PASS", "finite numeric values", "all frequency and S-parameter values are finite"))
    else:
        checks.append(Check("FAIL", "finite numeric values", "NaN or Inf found in frequency/S-parameter data"))
    if len(freqs_hz) >= 2 and bool(np.all(np.diff(freqs_hz) > 0.0)):
        checks.append(Check("PASS", "frequency monotonicity", "strictly increasing"))
    else:
        checks.append(Check("FAIL", "frequency monotonicity", "frequency grid is not strictly increasing"))
    rec_max = float(np.max(np.abs(s_matrix - np.swapaxes(s_matrix, 1, 2)))) if s_matrix.size else math.nan
    if rec_max <= float(args.max_reciprocity_error):
        checks.append(Check("PASS", "reciprocity", f"max_abs_error={rec_max:.6g}"))
    else:
        checks.append(
            Check(
                "FAIL",
                "reciprocity",
                f"max_abs_error={rec_max:.6g} exceeds {float(args.max_reciprocity_error):.6g}",
            )
        )
    sigma_max = float(np.max(np.linalg.svd(s_matrix, compute_uv=False))) if s_matrix.size else math.nan
    if sigma_max <= float(args.max_passivity_sigma):
        checks.append(Check("PASS", "passivity", f"sigma_max={sigma_max:.6g}"))
    else:
        checks.append(
            Check(
                "FAIL",
                "passivity",
                f"sigma_max={sigma_max:.6g} exceeds {float(args.max_passivity_sigma):.6g}",
            )
        )
    return checks


def _frequency_checks(freqs_hz: np.ndarray, args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    if len(freqs_hz) == 0:
        return [Check("FAIL", "frequency grid", "no frequency points")]

    expected = {
        "start": _ghz_to_hz(args.expected_frequency_start_ghz),
        "stop": _ghz_to_hz(args.expected_frequency_stop_ghz),
        "step": _ghz_to_hz(args.expected_frequency_step_ghz),
        "points": args.expected_frequency_points,
    }
    actual_step = float(np.median(np.diff(freqs_hz))) if len(freqs_hz) >= 2 else None
    expected_parts = [value is not None for value in expected.values()]
    if any(expected_parts):
        mismatches: list[str] = []
        tol = float(args.frequency_tolerance_hz)
        if expected["start"] is not None and abs(float(freqs_hz[0]) - expected["start"]) > tol:
            mismatches.append(f"start expected={expected['start']} actual={float(freqs_hz[0])}")
        if expected["stop"] is not None and abs(float(freqs_hz[-1]) - expected["stop"]) > tol:
            mismatches.append(f"stop expected={expected['stop']} actual={float(freqs_hz[-1])}")
        if expected["step"] is not None and actual_step is not None and abs(actual_step - expected["step"]) > tol:
            mismatches.append(f"step expected={expected['step']} actual={actual_step}")
        if expected["points"] is not None and int(len(freqs_hz)) != int(expected["points"]):
            mismatches.append(f"points expected={expected['points']} actual={len(freqs_hz)}")
        if expected["step"] is not None and len(freqs_hz) >= 2:
            step_errors = np.abs(np.diff(freqs_hz) - float(expected["step"]))
            max_step_error = float(np.max(step_errors))
            bad_step_count = int(np.sum(step_errors > tol))
            if bad_step_count:
                mismatches.append(
                    f"per-step grid expected={expected['step']} bad_step_count={bad_step_count} max_error_hz={max_step_error:g}"
                )
        if mismatches:
            checks.append(Check("FAIL", "expected frequency grid", "; ".join(mismatches)))
        else:
            checks.append(Check("PASS", "expected frequency grid", _frequency_detail(freqs_hz, actual_step)))
    else:
        checks.append(Check("WARN", "expected frequency grid", "no expected grid supplied"))

    sweep_start = _ghz_to_hz(args.required_sweep_start_ghz)
    sweep_stop = _ghz_to_hz(args.required_sweep_stop_ghz)
    if sweep_start is None and sweep_stop is None:
        checks.append(Check("WARN", "required ADS sweep coverage", "no required sweep supplied"))
    else:
        tol = float(args.frequency_tolerance_hz)
        misses: list[str] = []
        if sweep_start is not None and float(freqs_hz[0]) > sweep_start + tol:
            misses.append(f"file starts at {float(freqs_hz[0])} Hz, later than required {sweep_start} Hz")
        if sweep_stop is not None and float(freqs_hz[-1]) < sweep_stop - tol:
            misses.append(f"file stops at {float(freqs_hz[-1])} Hz, earlier than required {sweep_stop} Hz")
        if misses:
            checks.append(Check("FAIL", "required ADS sweep coverage", "; ".join(misses)))
        else:
            checks.append(Check("PASS", "required ADS sweep coverage", _frequency_detail(freqs_hz, actual_step)))
    return checks


def _matrix_quality(freqs_hz: np.ndarray, s_matrix: np.ndarray) -> dict[str, Any]:
    rec = np.max(np.abs(s_matrix - np.swapaxes(s_matrix, 1, 2)), axis=(1, 2))
    sigma = np.max(np.linalg.svd(s_matrix, compute_uv=False), axis=1)
    return {
        "freq_hz": freqs_hz.tolist(),
        "reciprocity_error_abs": rec.tolist(),
        "passivity_sigma_max_by_freq": sigma.tolist(),
        "reciprocity_error_abs_max": float(np.max(rec)),
        "passivity_sigma_max": float(np.max(sigma)),
        "passivity_excess_max": float(max(0.0, np.max(sigma) - 1.0)),
    }


def _extract_metric_curves(
    s_matrix: np.ndarray,
    freqs_hz: np.ndarray,
    args: argparse.Namespace,
    *,
    z0: float | np.ndarray = 50.0,
) -> MetricCurves:
    z_diff = _differential_z_from_s(
        s_matrix,
        z0,
        args.port_pairs,
        ground_unused_ports=bool(args.ground_unused_ports),
    )
    omega = 2.0 * math.pi * np.asarray(freqs_hz, dtype=float)
    z11 = z_diff[:, 0, 0]
    z22 = z_diff[:, 1, 1]
    z21 = z_diff[:, 1, 0]
    lp_nh = np.imag(z11) / omega * 1.0e9
    ls_nh = np.imag(z22) / omega * 1.0e9
    # Match the ADS worksheet convention used for this project:
    # M = imag(Z31 - Z32 + Z42 - Z41) / omega for port pairs 1,2:3,4.
    m_nh = np.imag(z21) / omega * 1.0e9
    denom = np.sqrt(np.maximum(np.abs(lp_nh * ls_nh), 1.0e-30))
    k = m_nh / denom
    qp = _safe_div(np.imag(z11), np.real(z11))
    qs = _safe_div(np.imag(z22), np.real(z22))
    return MetricCurves(freq_hz=freqs_hz, lp_nh=lp_nh, ls_nh=ls_nh, m_nh=m_nh, k=k, qp=qp, qs=qs)


def _differential_z_from_s(
    s_matrix: np.ndarray,
    z0: float | np.ndarray,
    port_pairs: str | None,
    *,
    ground_unused_ports: bool = False,
) -> np.ndarray:
    s = np.asarray(s_matrix, dtype=np.complex128)
    if s.shape[1:] == (2, 2):
        return s_to_z(s, z0=z0)
    n_ports = int(s.shape[1])
    pair_text = _resolve_port_pairs_for_touchstone(n_ports, port_pairs)
    parsed_pairs = parse_port_pairs(pair_text)
    if ground_unused_ports:
        return multiport_s_to_grounded_differential_z(s, z0, parsed_pairs)
    z_single = s_to_z(s, z0=z0)
    return multiport_z_to_differential_z(z_single, parsed_pairs)


def _resolve_port_pairs_for_touchstone(n_ports: int, port_pairs: str | None) -> str:
    if port_pairs:
        parsed = parse_port_pairs(port_pairs)
        flat = [port for pair in parsed for port in pair]
        if min(flat) < 0 or max(flat) >= int(n_ports):
            raise ValueError(f"Port pair spec {port_pairs!r} is outside S{n_ports}P port range")
        return port_pairs
    if int(n_ports) == 4:
        return "1,2:3,4"
    raise ValueError(
        f"S{n_ports}P transformer audit requires explicit differential port pairs; "
        "pass --port-pairs and record the physical port map."
    )


def _differential_z_quality(z_diff: np.ndarray) -> dict[str, Any]:
    z = np.asarray(z_diff, dtype=np.complex128)
    if z.ndim != 3 or z.shape[1:] != (2, 2):
        raise ValueError(f"Expected differential Z with shape (N,2,2), got {z.shape}")
    rec_abs = np.abs(z[:, 0, 1] - z[:, 1, 0])
    rec_scale = np.maximum(np.maximum(np.abs(z[:, 0, 1]), np.abs(z[:, 1, 0])), 1.0e-30)
    rec_rel = rec_abs / rec_scale
    self_real = np.column_stack((np.real(z[:, 0, 0]), np.real(z[:, 1, 1])))
    hermitian_real_min = []
    for matrix in z:
        hermitian_real = (matrix + matrix.conj().T) / 2.0
        hermitian_real_min.append(float(np.min(np.linalg.eigvalsh(hermitian_real)).real))
    return {
        "finite": bool(np.isfinite(z.real).all() and np.isfinite(z.imag).all()),
        "reciprocity_error_abs_ohm_max": float(np.max(rec_abs)) if rec_abs.size else math.nan,
        "reciprocity_error_relative_max": float(np.max(rec_rel)) if rec_rel.size else math.nan,
        "self_resistance_ohm_min": float(np.min(self_real)) if self_real.size else math.nan,
        "self_resistance_ohm_max": float(np.max(self_real)) if self_real.size else math.nan,
        "positive_real_eigenvalue_ohm_min": float(np.min(hermitian_real_min)) if hermitian_real_min else math.nan,
    }


def _differential_z_checks(quality: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    if quality.get("finite") is True:
        checks.append(Check("PASS", "differential Z finiteness", "Zdiff real/imag values are finite"))
    else:
        checks.append(Check("FAIL", "differential Z finiteness", "Zdiff contains NaN or Inf values"))

    rec_abs = float(quality.get("reciprocity_error_abs_ohm_max", math.nan))
    rec_rel = float(quality.get("reciprocity_error_relative_max", math.nan))
    if (
        math.isfinite(rec_abs)
        and math.isfinite(rec_rel)
        and rec_abs <= float(args.max_differential_z_reciprocity_error_ohm)
        and rec_rel <= float(args.max_differential_z_reciprocity_relative_error)
    ):
        checks.append(
            Check(
                "PASS",
                "differential Z reciprocity",
                f"max_abs_ohm={rec_abs:.6g}, max_relative={rec_rel:.6g}",
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                "differential Z reciprocity",
                (
                    f"max_abs_ohm={rec_abs:.6g} limit={float(args.max_differential_z_reciprocity_error_ohm):.6g}, "
                    f"max_relative={rec_rel:.6g} limit={float(args.max_differential_z_reciprocity_relative_error):.6g}"
                ),
            )
        )

    self_min = float(quality.get("self_resistance_ohm_min", math.nan))
    eig_min = float(quality.get("positive_real_eigenvalue_ohm_min", math.nan))
    if (
        math.isfinite(self_min)
        and math.isfinite(eig_min)
        and self_min > float(args.min_differential_self_resistance_ohm)
        and eig_min >= float(args.min_differential_z_real_eigenvalue_ohm)
    ):
        checks.append(
            Check(
                "PASS",
                "differential Z positive-realness",
                f"self_resistance_min_ohm={self_min:.6g}, hermitian_real_eig_min_ohm={eig_min:.6g}",
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                "differential Z positive-realness",
                (
                    f"self_resistance_min_ohm={self_min:.6g} limit>{float(args.min_differential_self_resistance_ohm):.6g}, "
                    f"hermitian_real_eig_min_ohm={eig_min:.6g} limit>={float(args.min_differential_z_real_eigenvalue_ohm):.6g}"
                ),
            )
        )
    return checks


def _metric_checks(metrics: MetricCurves, args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    arrays = {
        "lp_nh": metrics.lp_nh,
        "ls_nh": metrics.ls_nh,
        "m_nh": metrics.m_nh,
        "k": metrics.k,
        "qp": metrics.qp,
        "qs": metrics.qs,
    }
    nonfinite = [name for name, arr in arrays.items() if not np.isfinite(arr).all()]
    if nonfinite:
        checks.append(Check("FAIL", "ADS-equivalent metric finiteness", f"non-finite arrays: {nonfinite}"))
    else:
        checks.append(Check("PASS", "ADS-equivalent metric finiteness", "Lp/Ls/M/K/Qp/Qs are finite"))

    if args.target_frequency_ghz is not None:
        checks.extend(_target_checks(metrics, args))
    else:
        checks.append(Check("WARN", "target-frequency transformer metrics", "no target frequency supplied"))

    if args.positive_window_start_ghz is not None or args.positive_window_stop_ghz is not None:
        checks.extend(_positive_window_checks(metrics, args))
    else:
        checks.append(Check("WARN", "positive metric window", "no positive-L/Q window supplied"))
    if args.shape_window_start_ghz is not None or args.shape_window_stop_ghz is not None:
        checks.extend(_shape_window_checks(metrics, args))
    return checks


def _target_checks(metrics: MetricCurves, args: argparse.Namespace) -> list[Check]:
    target_hz = float(args.target_frequency_ghz) * 1.0e9
    idx = int(np.argmin(np.abs(metrics.freq_hz - target_hz)))
    freq_error_hz = abs(float(metrics.freq_hz[idx]) - target_hz)
    step_hz = float(np.median(np.diff(metrics.freq_hz))) if len(metrics.freq_hz) >= 2 else 0.0
    allowed_error_hz = (
        float(args.target_frequency_tolerance_ghz) * 1.0e9
        if args.target_frequency_tolerance_ghz is not None
        else max(float(args.frequency_tolerance_hz), 0.5 * step_hz + float(args.frequency_tolerance_hz))
    )
    checks: list[Check] = []
    if freq_error_hz <= allowed_error_hz:
        checks.append(Check("PASS", "target frequency point", f"target_hz={target_hz}, used_hz={float(metrics.freq_hz[idx])}"))
    else:
        checks.append(
            Check(
                "FAIL",
                "target frequency point",
                f"target_hz={target_hz}, nearest_hz={float(metrics.freq_hz[idx])}, error_hz={freq_error_hz}",
            )
        )
    failures = _metric_threshold_failures(
        lp=float(metrics.lp_nh[idx]),
        ls=float(metrics.ls_nh[idx]),
        qp=float(metrics.qp[idx]),
        qs=float(metrics.qs[idx]),
        k=float(metrics.k[idx]),
        min_inductance_nh=float(args.min_target_inductance_nh),
        min_q=float(args.min_target_q),
        min_abs_k=float(args.min_target_abs_k),
        max_abs_k=float(args.max_target_abs_k),
    )
    detail = (
        f"freq_ghz={float(metrics.freq_hz[idx]) / 1.0e9:.6g}, "
        f"Lp_nH={float(metrics.lp_nh[idx]):.6g}, Ls_nH={float(metrics.ls_nh[idx]):.6g}, "
        f"K={float(metrics.k[idx]):.6g}, Qp={float(metrics.qp[idx]):.6g}, Qs={float(metrics.qs[idx]):.6g}"
    )
    if failures:
        checks.append(Check("FAIL", "target-frequency transformer metrics", detail + "; " + "; ".join(failures)))
    else:
        checks.append(Check("PASS", "target-frequency transformer metrics", detail))
    return checks


def _positive_window_checks(metrics: MetricCurves, args: argparse.Namespace) -> list[Check]:
    start_hz = _ghz_to_hz(args.positive_window_start_ghz)
    stop_hz = _ghz_to_hz(args.positive_window_stop_ghz)
    mask = np.ones(len(metrics.freq_hz), dtype=bool)
    if start_hz is not None:
        mask &= metrics.freq_hz >= start_hz - float(args.frequency_tolerance_hz)
    if stop_hz is not None:
        mask &= metrics.freq_hz <= stop_hz + float(args.frequency_tolerance_hz)
    if int(np.sum(mask)) == 0:
        return [Check("FAIL", "positive metric window", "no frequency points inside requested window")]
    failures: list[str] = []
    if float(np.min(metrics.lp_nh[mask])) <= float(args.min_target_inductance_nh):
        failures.append(f"Lp_min_nH={float(np.min(metrics.lp_nh[mask])):.6g}")
    if float(np.min(metrics.ls_nh[mask])) <= float(args.min_target_inductance_nh):
        failures.append(f"Ls_min_nH={float(np.min(metrics.ls_nh[mask])):.6g}")
    if float(np.min(metrics.qp[mask])) <= float(args.min_target_q):
        failures.append(f"Qp_min={float(np.min(metrics.qp[mask])):.6g}")
    if float(np.min(metrics.qs[mask])) <= float(args.min_target_q):
        failures.append(f"Qs_min={float(np.min(metrics.qs[mask])):.6g}")
    min_window_abs_k = (
        float(args.min_window_abs_k)
        if args.min_window_abs_k is not None
        else float(args.min_target_abs_k)
    )
    if min_window_abs_k > 0.0 and float(np.min(np.abs(metrics.k[mask]))) < min_window_abs_k:
        failures.append(f"abs_K_min={float(np.min(np.abs(metrics.k[mask]))):.6g}")
    if float(np.max(np.abs(metrics.k[mask]))) > float(args.max_target_abs_k):
        failures.append(f"abs_K_max={float(np.max(np.abs(metrics.k[mask]))):.6g}")
    detail = (
        f"points={int(np.sum(mask))}, "
        f"freq_range_ghz={float(metrics.freq_hz[mask][0]) / 1.0e9:.6g}-"
        f"{float(metrics.freq_hz[mask][-1]) / 1.0e9:.6g}"
    )
    if failures:
        return [Check("FAIL", "positive metric window", detail + "; " + "; ".join(failures))]
    return [Check("PASS", "positive metric window", detail)]


def _shape_window_checks(metrics: MetricCurves, args: argparse.Namespace) -> list[Check]:
    start_hz = _ghz_to_hz(args.shape_window_start_ghz)
    stop_hz = _ghz_to_hz(args.shape_window_stop_ghz)
    mask = np.ones(len(metrics.freq_hz), dtype=bool)
    if start_hz is not None:
        mask &= metrics.freq_hz >= start_hz - float(args.frequency_tolerance_hz)
    if stop_hz is not None:
        mask &= metrics.freq_hz <= stop_hz + float(args.frequency_tolerance_hz)
    if int(np.sum(mask)) < 3:
        return [Check("FAIL", "smooth transformer metric window", "fewer than 3 points inside requested shape window")]

    data = {
        "Lp": metrics.lp_nh[mask],
        "Ls": metrics.ls_nh[mask],
        "K": metrics.k[mask],
        "Qp": metrics.qp[mask],
        "Qs": metrics.qs[mask],
    }
    failures: list[str] = []
    summaries: list[str] = []
    for name, values in data.items():
        abs_values = np.abs(np.asarray(values, dtype=float))
        floor = 0.05 if name == "K" else 1.0e-12
        scale = max(float(np.nanpercentile(abs_values, 50.0)), floor)
        spike_ratio = float(np.nanpercentile(abs_values, 99.0) / scale)
        relative_step = float(np.max(np.abs(np.diff(values))) / scale)
        summaries.append(f"{name}:p99/p50={spike_ratio:.3g},max_step/p50={relative_step:.3g}")
        if spike_ratio > float(args.max_shape_spike_ratio):
            failures.append(f"{name} spike_ratio={spike_ratio:.3g}")
        if relative_step > float(args.max_shape_relative_step):
            failures.append(f"{name} relative_step={relative_step:.3g}")

    detail = (
        f"points={int(np.sum(mask))}, "
        f"freq_range_ghz={float(metrics.freq_hz[mask][0]) / 1.0e9:.6g}-"
        f"{float(metrics.freq_hz[mask][-1]) / 1.0e9:.6g}; "
        + "; ".join(summaries)
    )
    if failures:
        return [Check("FAIL", "smooth transformer metric window", detail + "; failures=" + ", ".join(failures))]
    return [Check("PASS", "smooth transformer metric window", detail)]


def _metric_threshold_failures(
    *,
    lp: float,
    ls: float,
    qp: float,
    qs: float,
    k: float,
    min_inductance_nh: float,
    min_q: float,
    min_abs_k: float,
    max_abs_k: float,
) -> list[str]:
    failures: list[str] = []
    if lp <= min_inductance_nh:
        failures.append(f"Lp_nH <= {min_inductance_nh:g}")
    if ls <= min_inductance_nh:
        failures.append(f"Ls_nH <= {min_inductance_nh:g}")
    if qp <= min_q:
        failures.append(f"Qp <= {min_q:g}")
    if qs <= min_q:
        failures.append(f"Qs <= {min_q:g}")
    if abs(k) < min_abs_k:
        failures.append(f"abs(K) < {min_abs_k:g}")
    if abs(k) > max_abs_k:
        failures.append(f"abs(K) > {max_abs_k:g}")
    return failures


def _build_summary(
    *,
    touchstone_path: Path,
    out_dir: Path,
    checks: list[Check],
    overall_status: str,
    args: argparse.Namespace,
    touchstone: Any,
    matrix_quality: dict[str, Any],
    differential_z_quality: dict[str, Any],
    provenance: dict[str, Any],
    metrics: MetricCurves | None,
) -> dict[str, Any]:
    freq_summary: dict[str, Any] = {}
    port_count = None
    if touchstone is not None:
        freqs = np.asarray(touchstone.freqs_hz, dtype=float)
        port_count = int(touchstone.s_matrix.shape[1])
        step = float(np.median(np.diff(freqs))) if len(freqs) >= 2 else None
        freq_summary = {
            "start_hz": float(freqs[0]),
            "stop_hz": float(freqs[-1]),
            "step_hz": step,
            "min_step_hz": float(np.min(np.diff(freqs))) if len(freqs) >= 2 else None,
            "max_step_hz": float(np.max(np.diff(freqs))) if len(freqs) >= 2 else None,
            "max_expected_step_error_hz": _max_expected_step_error_hz(freqs, args),
            "points": int(len(freqs)),
        }
    metric_summary = _metric_summary(metrics, args) if metrics is not None else {}
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "touchstone": _file_record(touchstone_path),
        "out_dir": str(out_dir),
        "port_count": port_count,
        "reference_impedance_ohm": _reference_summary(touchstone.reference_impedance_ohm) if touchstone is not None else None,
        "frequency": freq_summary,
        "arguments": {
            "expected_ports": args.expected_ports,
            "port_pairs": args.port_pairs,
            "ground_unused_ports": bool(args.ground_unused_ports),
            "expected_frequency_start_ghz": args.expected_frequency_start_ghz,
            "expected_frequency_stop_ghz": args.expected_frequency_stop_ghz,
            "expected_frequency_step_ghz": args.expected_frequency_step_ghz,
            "expected_frequency_points": args.expected_frequency_points,
            "expected_source_kind": args.expected_source_kind,
            "required_sweep_start_ghz": args.required_sweep_start_ghz,
            "required_sweep_stop_ghz": args.required_sweep_stop_ghz,
            "target_frequency_ghz": args.target_frequency_ghz,
            "min_target_inductance_nh": args.min_target_inductance_nh,
            "min_target_q": args.min_target_q,
            "min_target_abs_k": args.min_target_abs_k,
            "max_target_abs_k": args.max_target_abs_k,
            "positive_window_start_ghz": args.positive_window_start_ghz,
            "positive_window_stop_ghz": args.positive_window_stop_ghz,
            "min_window_abs_k": args.min_window_abs_k,
            "shape_window_start_ghz": args.shape_window_start_ghz,
            "shape_window_stop_ghz": args.shape_window_stop_ghz,
            "max_shape_spike_ratio": args.max_shape_spike_ratio,
            "max_shape_relative_step": args.max_shape_relative_step,
            "max_differential_z_reciprocity_error_ohm": args.max_differential_z_reciprocity_error_ohm,
            "max_differential_z_reciprocity_relative_error": args.max_differential_z_reciprocity_relative_error,
            "min_differential_z_real_eigenvalue_ohm": args.min_differential_z_real_eigenvalue_ohm,
            "min_differential_self_resistance_ohm": args.min_differential_self_resistance_ohm,
        },
        "provenance": provenance,
        "checks": [check.__dict__ for check in checks],
        "matrix_quality": {
            key: value
            for key, value in matrix_quality.items()
            if key not in {"freq_hz", "reciprocity_error_abs", "passivity_sigma_max_by_freq"}
        },
        "differential_z_quality": differential_z_quality,
        "metric_summary": metric_summary,
        "limitations": [
            "This preflight does not compare HFSS against EMX.",
            "A PASS means the Touchstone file is internally usable for ADS-style extraction under the supplied sweep and target/window assumptions.",
            "For S8P power-line ports, ground_unused_ports=true is required when the ADS validation schematic grounds all non-selected supply-line ports.",
            "Final production acceptance still requires HFSS-vs-EMX or ADS-vs-EMX curve error within the project threshold.",
        ],
    }


def _metric_summary(metrics: MetricCurves | None, args: argparse.Namespace) -> dict[str, Any]:
    if metrics is None:
        return {}
    data = {
        "lp_nh": metrics.lp_nh,
        "ls_nh": metrics.ls_nh,
        "m_nh": metrics.m_nh,
        "k": metrics.k,
        "qp": metrics.qp,
        "qs": metrics.qs,
    }
    summary = {name: _array_summary(values) for name, values in data.items()}
    if args.target_frequency_ghz is not None:
        target_hz = float(args.target_frequency_ghz) * 1.0e9
        idx = int(np.argmin(np.abs(metrics.freq_hz - target_hz)))
        summary["target_point"] = {
            "freq_hz": float(metrics.freq_hz[idx]),
            "lp_nh": float(metrics.lp_nh[idx]),
            "ls_nh": float(metrics.ls_nh[idx]),
            "m_nh": float(metrics.m_nh[idx]),
            "k": float(metrics.k[idx]),
            "qp": float(metrics.qp[idx]),
            "qs": float(metrics.qs[idx]),
        }
    return summary


def _array_summary(values: np.ndarray) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    return {
        "min": float(np.nanmin(arr)),
        "max": float(np.nanmax(arr)),
        "mean": float(np.nanmean(arr)),
        "p01": float(np.nanpercentile(arr, 1.0)),
        "p50": float(np.nanpercentile(arr, 50.0)),
        "p99": float(np.nanpercentile(arr, 99.0)),
    }


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Touchstone Transformer Preflight Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Touchstone: `{summary['touchstone']['path']}`",
        f"- SHA256: `{summary['touchstone']['sha256']}`",
        f"- Ports: `{summary.get('port_count')}`",
        f"- Reference impedance: `{summary.get('reference_impedance_ohm')}`",
        f"- Frequency: `{summary.get('frequency')}`",
        f"- Provenance: `{summary.get('provenance')}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## Differential Z Quality", ""])
    for key, value in summary.get("differential_z_quality", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Metric Summary", ""])
    for key, value in summary.get("metric_summary", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Limits",
            "",
            "- This is a preflight gate only; it does not replace EMX-vs-HFSS comparison.",
            "- It is intended to prevent ADS extrapolation and obviously nonphysical S4P extraction before a human/report-level validation step.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_metrics_csv(path: Path, metrics: MetricCurves) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["freq_hz", "freq_ghz", "lp_nh", "ls_nh", "m_nh", "k", "qp", "qs"])
        writer.writeheader()
        for idx, freq_hz in enumerate(metrics.freq_hz):
            writer.writerow(
                {
                    "freq_hz": float(freq_hz),
                    "freq_ghz": float(freq_hz / 1.0e9),
                    "lp_nh": float(metrics.lp_nh[idx]),
                    "ls_nh": float(metrics.ls_nh[idx]),
                    "m_nh": float(metrics.m_nh[idx]),
                    "k": float(metrics.k[idx]),
                    "qp": float(metrics.qp[idx]),
                    "qs": float(metrics.qs[idx]),
                }
            )


def _write_plots(out_dir: Path, matrix_quality: dict[str, Any], metrics: MetricCurves, args: argparse.Namespace) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    freq_ghz = metrics.freq_hz / 1.0e9

    if matrix_quality:
        fig, axes = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
        axes[0].plot(
            np.asarray(matrix_quality["freq_hz"]) / 1.0e9,
            matrix_quality["passivity_sigma_max_by_freq"],
            linewidth=1.8,
        )
        axes[0].axhline(args.max_passivity_sigma, color="#C43B3B", linestyle="--", linewidth=1.2)
        axes[0].set_ylabel("sigma max")
        axes[0].grid(True, alpha=0.3)
        axes[1].semilogy(
            np.asarray(matrix_quality["freq_hz"]) / 1.0e9,
            np.maximum(matrix_quality["reciprocity_error_abs"], 1.0e-18),
            linewidth=1.8,
        )
        axes[1].axhline(args.max_reciprocity_error, color="#C43B3B", linestyle="--", linewidth=1.2)
        axes[1].set_xlabel("Frequency (GHz)")
        axes[1].set_ylabel("reciprocity abs error")
        axes[1].grid(True, which="both", alpha=0.3)
        fig.suptitle("Touchstone matrix quality")
        fig.tight_layout()
        fig.savefig(out_dir / "touchstone_matrix_quality.png", dpi=180)
        plt.close(fig)

    fig, axes = plt.subplots(3, 1, figsize=(8, 8), sharex=True)
    axes[0].plot(freq_ghz, metrics.lp_nh, label="Lp", linewidth=1.8)
    axes[0].plot(freq_ghz, metrics.ls_nh, label="Ls", linewidth=1.8)
    axes[0].set_ylabel("L (nH)")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)
    axes[1].plot(freq_ghz, metrics.k, label="K", color="#6F4AA8", linewidth=1.8)
    axes[1].axhline(args.max_target_abs_k, color="#C43B3B", linestyle="--", linewidth=1.0)
    axes[1].axhline(-args.max_target_abs_k, color="#C43B3B", linestyle="--", linewidth=1.0)
    axes[1].set_ylabel("K")
    axes[1].grid(True, alpha=0.3)
    axes[2].plot(freq_ghz, metrics.qp, label="Qp", linewidth=1.8)
    axes[2].plot(freq_ghz, metrics.qs, label="Qs", linewidth=1.8)
    axes[2].set_xlabel("Frequency (GHz)")
    axes[2].set_ylabel("Q")
    axes[2].legend()
    axes[2].grid(True, alpha=0.3)
    if args.target_frequency_ghz is not None:
        for ax in axes:
            ax.axvline(args.target_frequency_ghz, color="#202020", linestyle=":", linewidth=1.0)
    fig.suptitle("ADS-equivalent transformer metrics from Touchstone")
    fig.tight_layout()
    fig.savefig(out_dir / "touchstone_ads_equivalent_metrics.png", dpi=180)
    plt.close(fig)


def _frequency_detail(freqs_hz: np.ndarray, step_hz: float | None) -> str:
    return (
        f"start_hz={float(freqs_hz[0])}, stop_hz={float(freqs_hz[-1])}, "
        f"step_hz={step_hz}, points={len(freqs_hz)}"
    )


def _max_expected_step_error_hz(freqs_hz: np.ndarray, args: argparse.Namespace) -> float | None:
    expected_step = _ghz_to_hz(args.expected_frequency_step_ghz)
    if expected_step is None or len(freqs_hz) < 2:
        return None
    return float(np.max(np.abs(np.diff(freqs_hz) - expected_step)))


def _ghz_to_hz(value: float | None) -> float | None:
    return None if value is None else float(value) * 1.0e9


def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    den_arr = np.asarray(den, dtype=float)
    return np.divide(num, den_arr, out=np.full_like(np.asarray(num, dtype=float), np.nan), where=np.abs(den_arr) > 1.0e-30)


def _reference_summary(reference: Any) -> float | list[float]:
    values = np.asarray(reference, dtype=float)
    if values.ndim == 0:
        return float(values)
    return [float(value) for value in values]


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


if __name__ == "__main__":
    raise SystemExit(main())
