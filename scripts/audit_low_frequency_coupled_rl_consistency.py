#!/usr/bin/env python3
"""Audit low-frequency coupled-RL physics on real transformer Touchstone data.

The audit measures reciprocity, passivity, coupled-inductance positive
semidefiniteness, SRF/2 applicability, and the residual of a local coupled-RL
fit. It is an advisory prerequisite for an auxiliary physics loss. It never
replaces the broadband S-parameter proxy, DRC, real EMX, or HFSS validation.
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

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_physical_feature_extraction_frequency_stability import (  # noqa: E402
    _candidate_paths,
    _distributed_sample,
    _read_csv,
    _sha256,
    _validate_grid,
)
from compare_emx_hfss_ads import four_port_z_to_differential_z, parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.analysis import multiport_s_to_grounded_differential_z  # noqa: E402
from rfic_transformer_inverse_design.network_analysis import s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    source_csv = dataset_dir / "dataset_rows.csv"
    source_rows = _read_csv(source_csv)
    candidates = _candidate_paths(dataset_dir, source_rows)
    sampled = _distributed_sample(candidates, int(args.max_files))
    records = [_audit_one(item, args) for item in sampled]
    analysis = _summarize(records, args)
    checks = _checks(source_csv, candidates, records, analysis, args)
    status = "PASS" if all(checks.values()) else "FAIL"
    recommendation = _recommendation(analysis, args) if status == "PASS" else {
        "status": "UNAVAILABLE",
        "decision": "FIX_LOW_FREQUENCY_PHYSICS_AUDIT_INPUTS",
        "reason": "One or more evidence checks failed.",
    }

    rows_path = out_dir / "low_frequency_coupled_rl_consistency_rows.csv"
    summary_path = out_dir / "low_frequency_coupled_rl_consistency_summary.json"
    report_path = out_dir / "low_frequency_coupled_rl_consistency_report.md"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "LOW_FREQUENCY_EQUIVALENT_CIRCUIT_AUDIT_COMPLETE"
            if status == "PASS"
            else "DO_NOT_USE_LOW_FREQUENCY_EQUIVALENT_CIRCUIT_RESULT"
        ),
        "dataset_dir": str(dataset_dir),
        "source_dataset_csv": str(source_csv),
        "candidate_touchstone_count": len(candidates),
        "sampled_touchstone_count": len(sampled),
        "successful_touchstone_count": sum(item.get("ok") is True for item in records),
        "analysis": analysis,
        "recommendation": recommendation,
        "checks": checks,
        "arguments": vars(args),
        "artifacts": {"rows_csv": str(rows_path), "report_md": str(report_path)},
        "literature_basis": (
            "Integrated Transformers: Basic Concepts, Design Intuition, and Practical Considerations states that "
            "Lp/Ls/M/k can be extracted from Z parameters sufficiently below SRF and that Q definitions diverge "
            "near resonance; it recommends operation around or below SRF/2. The second-order equivalent-circuit "
            "paper explains why coupled-tank resonances, k, and Q jointly determine matching behavior."
        ),
        "scientific_boundary": (
            "PASS means real Touchstone evidence was audited and was physically plausible in the declared low-"
            "frequency band. A small coupled-RL residual only supports an auxiliary-loss ablation in that band. "
            "It cannot replace the 5-60 GHz complex-S PICC, EMX closure, HFSS correlation, or measurement."
        ),
    }
    _write_csv(rows_path, records)
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={payload['decision']}")
    print(f"recommendation={recommendation.get('decision')}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--fit-start-ghz", type=float, default=5.0)
    parser.add_argument("--fit-stop-ghz", type=float, default=10.0)
    parser.add_argument("--reference-frequency-ghz", type=float, default=5.0)
    parser.add_argument("--comparison-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--ground-unused-ports", action="store_true")
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-files", type=int, default=512)
    parser.add_argument("--min-files", type=int, default=128)
    parser.add_argument("--min-success-fraction", type=float, default=0.98)
    parser.add_argument("--min-physical-fraction", type=float, default=0.95)
    parser.add_argument("--max-abs-k", type=float, default=1.02)
    parser.add_argument("--max-reciprocity-relative-error", type=float, default=0.05)
    parser.add_argument("--passivity-eigen-tolerance-ohm", type=float, default=0.05)
    parser.add_argument("--inductance-eigen-tolerance-nh", type=float, default=1.0e-3)
    parser.add_argument("--max-advisory-p95-rl-residual", type=float, default=0.15)
    parser.add_argument("--min-reference-below-half-srf-fraction", type=float, default=0.95)
    parser.add_argument("--material-srf-applicability-gap", type=float, default=0.10)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not (args.expected_start_ghz <= args.fit_start_ghz < args.fit_stop_ghz <= args.expected_stop_ghz):
        parser.error("fit band must be inside the expected frequency band")
    if not (args.fit_start_ghz <= args.reference_frequency_ghz <= args.fit_stop_ghz):
        parser.error("reference frequency must be inside the fit band")
    if args.max_files < 1 or args.min_files < 1 or args.min_files > args.max_files:
        parser.error("file counts must satisfy 1 <= min-files <= max-files")
    for name in ("min_success_fraction", "min_physical_fraction", "min_reference_below_half_srf_fraction"):
        if not 0.0 <= float(getattr(args, name)) <= 1.0:
            parser.error(f"--{name.replace('_', '-')} must be in [0, 1]")
    return args


def _audit_one(item: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    path = Path(item["path"])
    record: dict[str, Any] = {
        "source_row_index": item["source_row_index"],
        "evaluation": item["evaluation"],
        "touchstone_path": str(path),
        "touchstone_sha256": _sha256(path) if path.is_file() else "",
        "ok": False,
        "error": "",
    }
    try:
        touchstone = load_touchstone(path)
        frequencies = np.asarray(touchstone.freqs_hz, dtype=float)
        s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
        _validate_grid(frequencies, s_matrix, args)
        z_single = s_to_z(s_matrix, z0=touchstone.reference_impedance_ohm)
        if bool(args.ground_unused_ports):
            z_diff = multiport_s_to_grounded_differential_z(
                s_matrix,
                touchstone.reference_impedance_ohm,
                parse_port_pairs(args.port_pairs),
            )
        elif z_single.shape[1:] == (4, 4):
            z_diff = four_port_z_to_differential_z(z_single, parse_port_pairs(args.port_pairs))
        elif z_single.shape[1:] == (2, 2):
            z_diff = z_single
        else:
            raise ValueError("non-grounded extraction supports only differential S2P or single-ended S4P")
        record.update(_analyze_curves(frequencies, z_diff, args))
        record["ok"] = True
    except Exception as exc:  # noqa: BLE001 - exact evidence failure is required.
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def _analyze_curves(frequencies: np.ndarray, z_diff: np.ndarray, args: argparse.Namespace) -> dict[str, Any]:
    fit_mask = (frequencies >= float(args.fit_start_ghz) * 1.0e9) & (
        frequencies <= float(args.fit_stop_ghz) * 1.0e9
    )
    if int(np.sum(fit_mask)) < 3:
        raise ValueError("at least three frequency points are required in the coupled-RL fit band")
    z_band = np.asarray(z_diff[fit_mask], dtype=np.complex128)
    freq_band = np.asarray(frequencies[fit_mask], dtype=float)
    omega = 2.0 * math.pi * freq_band
    z_transpose = np.swapaxes(z_band, 1, 2)
    z_symmetric = 0.5 * (z_band + z_transpose)
    reciprocity_error = float(
        np.linalg.norm(z_band - z_transpose) / max(float(np.linalg.norm(z_band)), 1.0e-30)
    )
    r_fit = np.mean(np.real(z_symmetric), axis=0)
    l_fit = np.sum(omega[:, None, None] * np.imag(z_symmetric), axis=0) / float(np.sum(omega**2))
    l_fit = 0.5 * (l_fit + l_fit.T)
    z_fit = r_fit[None, :, :] + 1j * omega[:, None, None] * l_fit[None, :, :]
    rl_residual = float(
        np.linalg.norm(z_symmetric - z_fit) / max(float(np.linalg.norm(z_symmetric)), 1.0e-30)
    )
    passivity_eigenvalues = []
    for matrix in z_band:
        hermitian = 0.5 * (matrix + matrix.conj().T)
        passivity_eigenvalues.extend(float(item) for item in np.linalg.eigvalsh(hermitian))
    min_passivity_eigen = float(np.min(passivity_eigenvalues))
    l_eigen_nh = np.linalg.eigvalsh(l_fit) * 1.0e9
    min_l_eigen_nh = float(np.min(l_eigen_nh))
    lp_nh = float(l_fit[0, 0] * 1.0e9)
    ls_nh = float(l_fit[1, 1] * 1.0e9)
    m_nh = float(0.5 * (l_fit[0, 1] + l_fit[1, 0]) * 1.0e9)
    k_fit = m_nh / math.sqrt(max(lp_nh * ls_nh, 1.0e-30))

    reference_index = _exact_index(
        frequencies,
        float(args.reference_frequency_ghz) * 1.0e9,
        float(args.frequency_tolerance_hz),
    )
    reference = np.asarray(z_diff[reference_index], dtype=np.complex128)
    qp = _safe_ratio(float(np.imag(reference[0, 0])), float(np.real(reference[0, 0])))
    qs = _safe_ratio(float(np.imag(reference[1, 1])), float(np.real(reference[1, 1])))
    primary_srf = _first_srf_ghz(frequencies, np.imag(z_diff[:, 0, 0]), reference_index)
    secondary_srf = _first_srf_ghz(frequencies, np.imag(z_diff[:, 1, 1]), reference_index)
    ref_below_half = _below_half_srf(
        float(args.reference_frequency_ghz), primary_srf, secondary_srf, frequencies[-1] / 1.0e9
    )
    comparison_below_half = _below_half_srf(
        float(args.comparison_frequency_ghz), primary_srf, secondary_srf, frequencies[-1] / 1.0e9
    )
    physical = (
        all(math.isfinite(value) for value in (reciprocity_error, rl_residual, min_passivity_eigen, min_l_eigen_nh, lp_nh, ls_nh, m_nh, k_fit, qp, qs))
        and lp_nh > 0.0
        and ls_nh > 0.0
        and qp > 0.0
        and qs > 0.0
        and abs(k_fit) <= float(args.max_abs_k)
        and reciprocity_error <= float(args.max_reciprocity_relative_error)
        and min_passivity_eigen >= -float(args.passivity_eigen_tolerance_ohm)
        and min_l_eigen_nh >= -float(args.inductance_eigen_tolerance_nh)
    )
    return {
        "fit_frequency_start_ghz": float(freq_band[0] / 1.0e9),
        "fit_frequency_stop_ghz": float(freq_band[-1] / 1.0e9),
        "fit_frequency_points": int(len(freq_band)),
        "coupled_rl_relative_residual": rl_residual,
        "reciprocity_relative_error": reciprocity_error,
        "minimum_passivity_eigenvalue_ohm": min_passivity_eigen,
        "minimum_inductance_eigenvalue_nh": min_l_eigen_nh,
        "fitted_lp_nh": lp_nh,
        "fitted_ls_nh": ls_nh,
        "fitted_m_nh": m_nh,
        "fitted_k": k_fit,
        "reference_qp": qp,
        "reference_qs": qs,
        "primary_srf_ghz": primary_srf,
        "secondary_srf_ghz": secondary_srf,
        "reference_below_half_srf": ref_below_half,
        "comparison_below_half_srf": comparison_below_half,
        "physically_plausible": bool(physical),
    }


def _exact_index(frequencies: np.ndarray, target_hz: float, tolerance_hz: float) -> int:
    index = int(np.argmin(np.abs(frequencies - target_hz)))
    if abs(float(frequencies[index]) - target_hz) > tolerance_hz:
        raise ValueError(f"required frequency {target_hz} Hz is absent")
    return index


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if abs(denominator) > 1.0e-18 else math.nan


def _first_srf_ghz(frequencies: np.ndarray, reactance: np.ndarray, start_index: int) -> float | None:
    values = np.asarray(reactance, dtype=float)
    if not math.isfinite(float(values[start_index])) or float(values[start_index]) <= 0.0:
        return float(frequencies[start_index] / 1.0e9)
    for index in range(start_index + 1, len(values)):
        left = float(values[index - 1])
        right = float(values[index])
        if not (math.isfinite(left) and math.isfinite(right)):
            continue
        if left > 0.0 and right <= 0.0:
            fraction = left / max(left - right, 1.0e-30)
            crossing = float(frequencies[index - 1]) + fraction * float(
                frequencies[index] - frequencies[index - 1]
            )
            return crossing / 1.0e9
    return None


def _below_half_srf(
    frequency_ghz: float,
    primary_srf_ghz: float | None,
    secondary_srf_ghz: float | None,
    observed_stop_ghz: float,
) -> bool:
    required = 2.0 * frequency_ghz
    limits = [value for value in (primary_srf_ghz, secondary_srf_ghz) if value is not None]
    limiting_srf = min(limits) if limits else observed_stop_ghz
    return required <= float(limiting_srf) + 1.0e-12


def _summarize(records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    valid = [item for item in records if item.get("ok") is True]
    physical = [item for item in valid if item.get("physically_plausible") is True]
    return {
        "valid_count": len(valid),
        "physically_plausible_count": len(physical),
        "physically_plausible_fraction": len(physical) / len(valid) if valid else 0.0,
        "reference_below_half_srf_fraction": _boolean_fraction(valid, "reference_below_half_srf"),
        "comparison_below_half_srf_fraction": _boolean_fraction(valid, "comparison_below_half_srf"),
        "coupled_rl_relative_residual": _stats(valid, "coupled_rl_relative_residual"),
        "reciprocity_relative_error": _stats(valid, "reciprocity_relative_error"),
        "minimum_passivity_eigenvalue_ohm": _stats(valid, "minimum_passivity_eigenvalue_ohm"),
        "minimum_inductance_eigenvalue_nh": _stats(valid, "minimum_inductance_eigenvalue_nh"),
        "fitted_k_abs": _stats_abs(valid, "fitted_k"),
        "primary_srf_ghz_observed": _stats(valid, "primary_srf_ghz"),
        "secondary_srf_ghz_observed": _stats(valid, "secondary_srf_ghz"),
        "frequency_contract": {
            "fit_band_ghz": [float(args.fit_start_ghz), float(args.fit_stop_ghz)],
            "reference_frequency_ghz": float(args.reference_frequency_ghz),
            "comparison_frequency_ghz": float(args.comparison_frequency_ghz),
        },
    }


def _stats(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = []
    for row in rows:
        value = row.get(field)
        if value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                values.append(number)
    array = np.asarray(values, dtype=float)
    return {
        "count": int(array.size),
        "median": float(np.median(array)) if array.size else None,
        "p95": float(np.quantile(array, 0.95)) if array.size else None,
        "minimum": float(np.min(array)) if array.size else None,
        "maximum": float(np.max(array)) if array.size else None,
    }


def _stats_abs(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    copied = [{field: abs(float(row[field]))} for row in rows if row.get(field) is not None]
    return _stats(copied, field)


def _boolean_fraction(rows: list[dict[str, Any]], field: str) -> float:
    return sum(row.get(field) is True for row in rows) / len(rows) if rows else 0.0


def _checks(
    source_csv: Path,
    candidates: list[dict[str, Any]],
    records: list[dict[str, Any]],
    analysis: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, bool]:
    successes = sum(item.get("ok") is True for item in records)
    success_fraction = successes / len(records) if records else 0.0
    residual = analysis.get("coupled_rl_relative_residual") or {}
    return {
        "dataset_rows_csv_exists": source_csv.is_file(),
        "candidate_count_meets_minimum": len(candidates) >= int(args.min_files),
        "sample_count_meets_minimum": len(records) >= int(args.min_files),
        "touchstone_success_fraction": success_fraction >= float(args.min_success_fraction),
        "physical_plausibility_fraction": float(analysis.get("physically_plausible_fraction") or 0.0)
        >= float(args.min_physical_fraction),
        "coupled_rl_residual_is_measured": int(residual.get("count") or 0) >= int(args.min_files)
        and residual.get("p95") is not None,
        "srf_applicability_is_measured": analysis.get("reference_below_half_srf_fraction") is not None
        and analysis.get("comparison_below_half_srf_fraction") is not None,
    }


def _recommendation(analysis: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    residual_p95 = float((analysis.get("coupled_rl_relative_residual") or {}).get("p95") or math.inf)
    reference_fraction = float(analysis.get("reference_below_half_srf_fraction") or 0.0)
    comparison_fraction = float(analysis.get("comparison_below_half_srf_fraction") or 0.0)
    rl_ready = residual_p95 <= float(args.max_advisory_p95_rl_residual) and reference_fraction >= float(
        args.min_reference_below_half_srf_fraction
    )
    prefer_reference = reference_fraction - comparison_fraction >= float(args.material_srf_applicability_gap)
    if rl_ready:
        decision = "COUPLED_RL_AUXILIARY_PHYSICS_LOSS_ABLATION_READY"
    else:
        decision = "USE_FULL_BROADBAND_PROXY_NOT_LOW_ORDER_AUXILIARY"
    return {
        "status": "AUDIT_ONLY_NO_AUTOMATIC_MODEL_CHANGE",
        "decision": decision,
        "prefer_reference_frequency_over_comparison": prefer_reference,
        "p95_coupled_rl_relative_residual": residual_p95,
        "reference_below_half_srf_fraction": reference_fraction,
        "comparison_below_half_srf_fraction": comparison_fraction,
        "boundary": (
            "A ready decision permits only a fixed-split auxiliary-loss ablation. The broadband complex-S proxy "
            "and real EM validation remain mandatory."
        ),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({field for row in rows for field in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _render_report(payload: dict[str, Any]) -> str:
    analysis = payload.get("analysis") or {}
    residual = analysis.get("coupled_rl_relative_residual") or {}
    recommendation = payload.get("recommendation") or {}
    checks = payload.get("checks") or {}
    lines = [
        "# Low-frequency coupled-RL consistency audit",
        "",
        f"- Overall status: `{payload.get('overall_status')}`",
        f"- Decision: `{payload.get('decision')}`",
        f"- Recommendation: `{recommendation.get('decision')}`",
        f"- Sampled / successful: `{payload.get('sampled_touchstone_count')}` / `{payload.get('successful_touchstone_count')}`",
        f"- Physically plausible fraction: `{analysis.get('physically_plausible_fraction')}`",
        f"- Coupled-RL relative residual median / p95: `{residual.get('median')}` / `{residual.get('p95')}`",
        f"- Reference below SRF/2 fraction: `{analysis.get('reference_below_half_srf_fraction')}`",
        f"- Comparison below SRF/2 fraction: `{analysis.get('comparison_below_half_srf_fraction')}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- `{name}`: `{value}`" for name, value in checks.items())
    lines.extend(["", "## Boundary", "", str(payload.get("scientific_boundary") or ""), ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
