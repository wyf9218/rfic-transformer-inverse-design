#!/usr/bin/env python3
"""Audit whether a layout transform is label-preserving after a port permutation.

The input is a manifest of independently simulated real-EMX Touchstone pairs.
This tool never creates training labels and never counts transformed rows as new
real samples.  It only decides whether a proposed symmetry is eligible for a
later, controlled model-augmentation ablation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.analysis.extraction import single_ended_to_differential_z  # noqa: E402
from rfic_transformer_inverse_design.sim.base import SParameterResult  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


FEATURE_RANGES = {
    "lp_nh_center": (0.5, 3.0),
    "ls_nh_center": (0.5, 3.0),
    "q_center": (5.0, 25.0),
    "k_abs_center": (0.0, 0.8),
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    pairs_csv = Path(args.pairs_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_csv(pairs_csv)
    audits = [_audit_pair(row, pairs_csv.parent, args) for row in rows]
    pass_count = sum(item.get("status") == "PASS" for item in audits)
    checks = {
        "pairs_csv_exists": pairs_csv.is_file(),
        "pair_count_meets_minimum": len(rows) >= int(args.min_pairs),
        "physical_cell_count_meets_minimum": len(
            {str(item.get("physical_cell_id") or "") for item in audits if str(item.get("physical_cell_id") or "")}
        )
        >= int(args.min_physical_cells),
        "all_pairs_audited": len(audits) == len(rows),
        "all_pairs_pass": bool(audits) and pass_count == len(audits),
        "no_self_pairs": bool(audits) and all(item.get("distinct_touchstone_paths") is True for item in audits),
        "independent_geometry_ids": bool(audits) and all(item.get("distinct_geometry_ids") is True for item in audits),
        "real_emx_sources_only": bool(audits) and all(item.get("real_emx_sources") is True for item in audits),
    }
    overall_status = "PASS" if all(checks.values()) else "FAIL"
    decision = (
        "ELIGIBLE_FOR_CONTROLLED_MODEL_AUGMENTATION_ABLATION"
        if overall_status == "PASS"
        else "DO_NOT_USE_LAYOUT_SYMMETRY_FOR_AUGMENTATION"
    )

    pair_csv = out_dir / "real_emx_port_permutation_symmetry_pairs.csv"
    summary_path = out_dir / "real_emx_port_permutation_symmetry_summary.json"
    report_path = out_dir / "real_emx_port_permutation_symmetry_report.md"
    _write_csv(pair_csv, audits)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "pairs_csv": _file_record(pairs_csv),
        "pair_audit_csv": _file_record(pair_csv),
        "pair_count": len(rows),
        "pair_pass_count": pass_count,
        "checks": checks,
        "thresholds": {
            "max_complex_s_rmse": float(args.max_complex_s_rmse),
            "max_complex_s_abs_error": float(args.max_complex_s_abs_error),
            "max_feature_range_normalized_error": float(args.max_feature_range_normalized_error),
            "max_reciprocity_error": float(args.max_reciprocity_error),
            "max_passivity_excess": float(args.max_passivity_excess),
        },
        "frequency_contract": {
            "start_ghz": float(args.expected_frequency_start_ghz),
            "stop_ghz": float(args.expected_frequency_stop_ghz),
            "step_ghz": float(args.expected_frequency_step_ghz),
            "points": int(args.expected_frequency_points),
            "center_ghz": float(args.center_frequency_ghz),
            "ports": int(args.expected_ports),
        },
        "aggregate_metrics": _aggregate(audits),
        "paired_physical_cell_bootstrap": _cluster_bootstrap(audits, args),
        "arguments": vars(args),
        "scientific_boundary": (
            "PASS only makes this exact transform-plus-port-permutation eligible for a controlled model augmentation "
            "ablation. Paired rows remain two real EMX simulations; derived augmentation rows contribute zero to the "
            "real-sample count, geometry-unique count, and all 1D/2D/4D uniformity denominators. A later ablation must "
            "use untouched real-EMX physical-cell OOD validation/test rows and real-EMX closure."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-pairs", type=int, default=128)
    parser.add_argument("--min-physical-cells", type=int, default=8)
    parser.add_argument("--bootstrap-repetitions", type=int, default=1000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260712)
    parser.add_argument("--default-permutation", default="")
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument("--center-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--required-process-token", default="TSMC65_05_12_26")
    parser.add_argument("--required-pin-purpose", type=int, default=51)
    parser.add_argument("--max-complex-s-rmse", type=float, default=1.0e-3)
    parser.add_argument("--max-complex-s-abs-error", type=float, default=1.0e-2)
    parser.add_argument("--max-feature-range-normalized-error", type=float, default=1.0e-2)
    parser.add_argument("--max-reciprocity-error", type=float, default=2.0e-2)
    parser.add_argument("--max-passivity-excess", type=float, default=5.0e-2)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.min_pairs < 1 or args.min_physical_cells < 1 or args.bootstrap_repetitions < 1:
        parser.error("pair, physical-cell, and bootstrap counts must be positive")
    if args.expected_ports != 4:
        parser.error("this audited transformer contract requires --expected-ports 4")
    if args.expected_frequency_points < 2 or args.expected_frequency_step_ghz <= 0:
        parser.error("frequency contract is invalid")
    for name in (
        "max_complex_s_rmse",
        "max_complex_s_abs_error",
        "max_feature_range_normalized_error",
        "max_reciprocity_error",
        "max_passivity_excess",
    ):
        if not math.isfinite(float(getattr(args, name))) or float(getattr(args, name)) < 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and non-negative")
    return args


def _audit_pair(row: dict[str, str], base_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    pair_id = str(row.get("pair_id") or "").strip()
    reference_raw = str(row.get("reference_touchstone") or "").strip()
    transformed_raw = str(row.get("transformed_touchstone") or "").strip()
    reference = _resolve_path(reference_raw, base_dir)
    transformed = _resolve_path(transformed_raw, base_dir)
    reference_geometry_id = str(row.get("reference_geometry_id") or "").strip()
    transformed_geometry_id = str(row.get("transformed_geometry_id") or "").strip()
    physical_cell_id = str(row.get("physical_cell_id") or "").strip()
    reference_source = str(row.get("reference_source_kind") or "").strip().upper()
    transformed_source = str(row.get("transformed_source_kind") or "").strip().upper()
    permutation_raw = str(row.get("transformed_ports_for_reference") or args.default_permutation).strip()
    record: dict[str, Any] = {
        "pair_id": pair_id,
        "reference_touchstone": str(reference) if reference else reference_raw,
        "transformed_touchstone": str(transformed) if transformed else transformed_raw,
        "reference_geometry_id": reference_geometry_id,
        "transformed_geometry_id": transformed_geometry_id,
        "physical_cell_id": physical_cell_id,
        "reference_source_kind": reference_source,
        "transformed_source_kind": transformed_source,
        "transformed_ports_for_reference": permutation_raw,
        "distinct_touchstone_paths": bool(reference and transformed and reference != transformed),
        "distinct_geometry_ids": bool(
            reference_geometry_id and transformed_geometry_id and reference_geometry_id != transformed_geometry_id
        ),
        "declared_emx_sources": reference_source == "EMX" and transformed_source == "EMX",
        "embedded_emx_evidence": False,
        "real_emx_sources": False,
    }
    try:
        if not pair_id:
            raise ValueError("missing pair_id")
        if not physical_cell_id:
            raise ValueError("missing physical_cell_id")
        if reference is None or transformed is None:
            raise ValueError("missing Touchstone path")
        if not record["distinct_touchstone_paths"]:
            raise ValueError("reference and transformed Touchstone paths must be distinct")
        if not record["distinct_geometry_ids"]:
            raise ValueError("reference and transformed geometry IDs must be present and distinct")
        if not record["declared_emx_sources"]:
            raise ValueError("both source_kind fields must be EMX")
        if not reference.is_file() or not transformed.is_file():
            raise ValueError("paired Touchstone file is missing")
        reference_emx_evidence = _emx_header_evidence(reference, args)
        transformed_emx_evidence = _emx_header_evidence(transformed, args)
        record["reference_emx_header_checks"] = reference_emx_evidence
        record["transformed_emx_header_checks"] = transformed_emx_evidence
        record["embedded_emx_evidence"] = bool(
            reference_emx_evidence["overall_pass"] and transformed_emx_evidence["overall_pass"]
        )
        record["real_emx_sources"] = bool(record["declared_emx_sources"] and record["embedded_emx_evidence"])
        if not record["embedded_emx_evidence"]:
            raise ValueError("paired Touchstone comments do not prove the required EMX production contract")
        permutation = _parse_permutation(permutation_raw, int(args.expected_ports))
        reference_result = load_touchstone(reference)
        transformed_result = load_touchstone(transformed)
        _validate_result(reference_result, args, "reference")
        _validate_result(transformed_result, args, "transformed")
        if not np.allclose(
            reference_result.freqs_hz,
            transformed_result.freqs_hz,
            rtol=0.0,
            atol=float(args.frequency_tolerance_hz),
        ):
            raise ValueError("paired frequency grids differ")
        reference_z0 = _reference_impedance_vector(reference_result)
        transformed_z0 = _reference_impedance_vector(transformed_result)[permutation]
        if not np.allclose(reference_z0, transformed_z0, rtol=0.0, atol=1.0e-12):
            raise ValueError("paired reference impedances differ")

        aligned_matrix = transformed_result.s_matrix[:, permutation][:, :, permutation]
        aligned_z0: float | np.ndarray = (
            float(transformed_z0[0]) if np.all(transformed_z0 == transformed_z0[0]) else transformed_z0
        )
        aligned_result = SParameterResult(
            freqs_hz=transformed_result.freqs_hz,
            s_matrix=aligned_matrix,
            reference_impedance_ohm=aligned_z0,
        )
        delta = reference_result.s_matrix - aligned_matrix
        complex_rmse = float(np.sqrt(np.mean(np.abs(delta) ** 2)))
        complex_max = float(np.max(np.abs(delta)))
        reference_features = _physical_features(reference_result, float(args.center_frequency_ghz) * 1.0e9)
        transformed_features = _physical_features(aligned_result, float(args.center_frequency_ghz) * 1.0e9)
        feature_errors = {
            name: abs(float(reference_features[name]) - float(transformed_features[name]))
            for name in FEATURE_RANGES
        }
        feature_range_normalized = {
            name: feature_errors[name] / (FEATURE_RANGES[name][1] - FEATURE_RANGES[name][0])
            for name in FEATURE_RANGES
        }
        quality_reference = _raw_quality(reference_result)
        quality_transformed = _raw_quality(transformed_result)
        record.update(
            {
                "reference_touchstone_sha256": _sha256(reference),
                "transformed_touchstone_sha256": _sha256(transformed),
                "complex_s_rmse": complex_rmse,
                "complex_s_max_abs_error": complex_max,
                "max_feature_range_normalized_error": max(feature_range_normalized.values()),
                "feature_absolute_errors": feature_errors,
                "feature_range_normalized_errors": feature_range_normalized,
                "reference_features": reference_features,
                "transformed_aligned_features": transformed_features,
                "reference_reciprocity_error": quality_reference["reciprocity_error"],
                "transformed_reciprocity_error": quality_transformed["reciprocity_error"],
                "reference_passivity_excess": quality_reference["passivity_excess"],
                "transformed_passivity_excess": quality_transformed["passivity_excess"],
            }
        )
        failures = []
        if complex_rmse > float(args.max_complex_s_rmse):
            failures.append("complex_s_rmse")
        if complex_max > float(args.max_complex_s_abs_error):
            failures.append("complex_s_max_abs_error")
        if max(feature_range_normalized.values()) > float(args.max_feature_range_normalized_error):
            failures.append("feature_range_normalized_error")
        if max(quality_reference["reciprocity_error"], quality_transformed["reciprocity_error"]) > float(
            args.max_reciprocity_error
        ):
            failures.append("raw_reciprocity")
        if max(quality_reference["passivity_excess"], quality_transformed["passivity_excess"]) > float(
            args.max_passivity_excess
        ):
            failures.append("raw_passivity")
        record["status"] = "PASS" if not failures else "FAIL"
        record["failure_reasons"] = failures
    except Exception as exc:
        record["status"] = "FAIL"
        record["failure_reasons"] = [f"{type(exc).__name__}: {exc}"]
    return record


def _validate_result(result: SParameterResult, args: argparse.Namespace, label: str) -> None:
    if result.num_ports != int(args.expected_ports):
        raise ValueError(f"{label} ports={result.num_ports}, expected={args.expected_ports}")
    expected = (
        float(args.expected_frequency_start_ghz) * 1.0e9
        + np.arange(int(args.expected_frequency_points), dtype=float)
        * float(args.expected_frequency_step_ghz)
        * 1.0e9
    )
    if not math.isclose(
        float(expected[-1]),
        float(args.expected_frequency_stop_ghz) * 1.0e9,
        rel_tol=0.0,
        abs_tol=float(args.frequency_tolerance_hz),
    ):
        raise ValueError("declared frequency start/step/points do not reach stop")
    if result.freqs_hz.shape != expected.shape or not np.allclose(
        result.freqs_hz,
        expected,
        rtol=0.0,
        atol=float(args.frequency_tolerance_hz),
    ):
        raise ValueError(f"{label} frequency grid does not match the declared contract")


def _physical_features(result: SParameterResult, center_hz: float) -> dict[str, float]:
    z_diff = single_ended_to_differential_z(result.to_z_parameters())
    index = int(np.argmin(np.abs(result.freqs_hz - float(center_hz))))
    frequency = float(result.freqs_hz[index])
    omega = 2.0 * np.pi * frequency
    z11 = complex(z_diff[index, 0, 0])
    z22 = complex(z_diff[index, 1, 1])
    z21 = complex(z_diff[index, 1, 0])
    lp_h = z11.imag / omega
    ls_h = z22.imag / omega
    mutual_h = z21.imag / omega
    denominator = math.sqrt(max(abs(lp_h * ls_h), 1.0e-30))
    qp = _safe_ratio(z11.imag, z11.real)
    qs = _safe_ratio(z22.imag, z22.real)
    values = {
        "lp_nh_center": lp_h * 1.0e9,
        "ls_nh_center": ls_h * 1.0e9,
        "q_center": min(qp, qs),
        "k_abs_center": abs(mutual_h / denominator),
    }
    if not all(math.isfinite(float(value)) for value in values.values()):
        raise ValueError("non-finite physical feature")
    return {name: float(value) for name, value in values.items()}


def _raw_quality(result: SParameterResult) -> dict[str, float]:
    reciprocity = float(np.max(np.abs(result.s_matrix - np.swapaxes(result.s_matrix, 1, 2))))
    sigma_max = np.max(np.linalg.svd(result.s_matrix, compute_uv=False), axis=1)
    passivity_excess = float(max(float(np.max(sigma_max)) - 1.0, 0.0))
    return {"reciprocity_error": reciprocity, "passivity_excess": passivity_excess}


def _reference_impedance_vector(result: SParameterResult) -> np.ndarray:
    values = np.asarray(result.reference_impedance_ohm, dtype=float)
    if values.ndim == 0:
        return np.full(result.num_ports, float(values), dtype=float)
    return values.copy()


def _emx_header_evidence(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    comment_lines: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle):
            stripped = line.strip()
            if stripped.startswith("!"):
                comment_lines.append(stripped[1:].strip())
            elif stripped and not stripped.startswith("["):
                break
            if line_number >= 127:
                break
    normalized = " ".join(" ".join(comment_lines).lower().split())
    start_hz = int(round(float(args.expected_frequency_start_ghz) * 1.0e9))
    stop_hz = int(round(float(args.expected_frequency_stop_ghz) * 1.0e9))
    step_hz = int(round(float(args.expected_frequency_step_ghz) * 1.0e9))
    checks = {
        "emx_version_banner": "touchstone simulation data from emx version" in normalized,
        "emx_run_command": "emx was run" in normalized and "/emx/bin/64bit/emx" in normalized,
        "included_command_line": "--include-command-line" in normalized,
        "required_process_token": str(args.required_process_token).strip().lower() in normalized,
        "required_pin_purpose": f"--cadence-pins={int(args.required_pin_purpose)}" in normalized,
        "s_impedance_50_ohm": "--s-impedance=50" in normalized,
        "standard_accuracy": "--accuracy=standard" in normalized,
        "emx_parallel_2": "--parallel=2" in normalized,
        "four_grounded_ports": all(
            f"--port=p00{index}=p00{index}:p00{index}_g" in normalized for index in range(1, 5)
        ),
        "frequency_sweep": (
            f"--sweep {start_hz} {stop_hz}" in normalized
            and "--sweep-stepsize" in normalized
            and str(step_hz) in normalized
        ),
    }
    return {
        "overall_pass": all(checks.values()),
        "checks": checks,
        "comment_line_count": len(comment_lines),
        "comment_sha256": hashlib.sha256("\n".join(comment_lines).encode("utf-8")).hexdigest(),
    }


def _parse_permutation(raw: str, ports: int) -> np.ndarray:
    tokens = [token.strip() for token in raw.split(",") if token.strip()]
    if len(tokens) != ports:
        raise ValueError(f"permutation must contain exactly {ports} one-based ports")
    values = [int(token) for token in tokens]
    if sorted(values) != list(range(1, ports + 1)):
        raise ValueError(f"permutation must be a bijection of 1..{ports}")
    return np.asarray([value - 1 for value in values], dtype=int)


def _resolve_path(raw: str, base_dir: Path) -> Path | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _safe_ratio(numerator: float, denominator: float) -> float:
    if abs(float(denominator)) <= 1.0e-30:
        return math.copysign(math.inf, float(numerator))
    return float(numerator) / float(denominator)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or ["status"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
                    for key, value in row.items()
                }
            )


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = (
        "complex_s_rmse",
        "complex_s_max_abs_error",
        "max_feature_range_normalized_error",
        "reference_reciprocity_error",
        "transformed_reciprocity_error",
        "reference_passivity_excess",
        "transformed_passivity_excess",
    )
    result: dict[str, Any] = {}
    for metric in metrics:
        values = [float(row[metric]) for row in rows if _finite(row.get(metric))]
        result[metric] = (
            {"count": len(values), "mean": float(np.mean(values)), "p95": float(np.quantile(values, 0.95)), "max": max(values)}
            if values
            else {"count": 0}
        )
    return result


def _cluster_bootstrap(rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    metrics = (
        "complex_s_rmse",
        "complex_s_max_abs_error",
        "max_feature_range_normalized_error",
    )
    cells: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        cell = str(row.get("physical_cell_id") or "").strip()
        if cell and all(_finite(row.get(metric)) for metric in metrics):
            cells.setdefault(cell, []).append(row)
    cell_ids = sorted(cells)
    if not cell_ids:
        return {
            "status": "NOT_APPLICABLE",
            "physical_cell_count": 0,
            "repetitions": int(args.bootstrap_repetitions),
            "seed": int(args.bootstrap_seed),
            "scientific_boundary": "No finite physical-cell clusters were available.",
        }
    rng = np.random.default_rng(int(args.bootstrap_seed))
    distributions = {metric: [] for metric in metrics}
    for _ in range(int(args.bootstrap_repetitions)):
        sampled_ids = rng.choice(cell_ids, size=len(cell_ids), replace=True)
        sampled_rows = [row for cell_id in sampled_ids for row in cells[str(cell_id)]]
        for metric in metrics:
            values = np.asarray([float(row[metric]) for row in sampled_rows], dtype=float)
            distributions[metric].append(float(np.quantile(values, 0.95)))
    metric_summary = {}
    for metric, values_raw in distributions.items():
        values = np.asarray(values_raw, dtype=float)
        point_values = np.asarray([float(row[metric]) for cell in cell_ids for row in cells[cell]], dtype=float)
        metric_summary[metric] = {
            "point_p95": float(np.quantile(point_values, 0.95)),
            "bootstrap_p95_median": float(np.median(values)),
            "bootstrap_p95_lower_95": float(np.quantile(values, 0.025)),
            "bootstrap_p95_upper_95": float(np.quantile(values, 0.975)),
        }
    return {
        "status": "PASS",
        "physical_cell_count": len(cell_ids),
        "physical_cell_ids_sha256": hashlib.sha256("\n".join(cell_ids).encode("utf-8")).hexdigest(),
        "repetitions": int(args.bootstrap_repetitions),
        "seed": int(args.bootstrap_seed),
        "metrics": metric_summary,
        "scientific_boundary": (
            "This deterministic cluster bootstrap resamples declared physical cells, not individual rows. It quantifies "
            "paired numerical-equivalence evidence only and is not an IID confidence interval for deployment targets."
        ),
    }


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": _sha256(path) if path.is_file() else "",
    }


def _render_report(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Real EMX port-permutation symmetry audit",
            "",
            f"- Overall status: **{summary['overall_status']}**",
            f"- Decision: `{summary['decision']}`",
            f"- Pair PASS: `{summary['pair_pass_count']}` / `{summary['pair_count']}`",
            f"- Pair evidence: `{summary['pair_audit_csv']['path']}`",
            "",
            "## Scientific boundary",
            "",
            str(summary["scientific_boundary"]),
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
