#!/usr/bin/env python3
"""Benchmark a paired EMX-HFSS residual correction without hiding raw error.

The benchmark uses leave-one-geometry-out evaluation. A real scalar ``rho``
and a shrinkage residual template are fitted from the remaining paired
Touchstones. Raw EMX remains the baseline and the publication gate always uses
the uncorrected EMX-HFSS comparison.
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

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


PRIMARY_METRICS = ("lp_nh", "ls_nh", "q", "k")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    record_paths = [Path(raw).expanduser().resolve() for raw in args.hfss_validation_record]
    dataset = _load_dataset(record_paths, args)
    checks = _checks(dataset, args)
    summary_path = out_dir / "emx_hfss_cross_solver_residual_summary.json"
    fold_csv = out_dir / "emx_hfss_cross_solver_residual_folds.csv"
    frequency_csv = out_dir / "emx_hfss_cross_solver_residual_frequency_errors.csv"
    plot_path = out_dir / "emx_hfss_cross_solver_residual_frequency_errors.png"
    report_path = out_dir / "emx_hfss_cross_solver_residual_report.md"

    if not all(checks.values()):
        insufficient = int(dataset.get("valid_count") or 0) < int(args.min_samples)
        payload = _base_payload(dataset, checks, record_paths, args)
        payload.update(
            {
                "overall_status": "WAITING_FOR_PAIRED_EMX_HFSS" if insufficient else "FAIL",
                "decision": (
                    "WAIT_FOR_MINIMUM_INDEPENDENT_REAL_S4P_PAIRS"
                    if insufficient
                    else "FIX_PAIRED_CROSS_SOLVER_CONTRACT"
                ),
                "artifacts": {"summary": str(summary_path), "folds": "", "frequency_errors": "", "plot": ""},
            }
        )
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report_path.write_text(_render_report(payload), encoding="utf-8")
        print(f"overall_status={payload['overall_status']}")
        print(f"summary={summary_path}")
        return 0 if args.no_fail_exit else 2

    result = _leave_one_geometry_out(dataset, args)
    _write_csv(fold_csv, result["fold_rows"])
    _write_csv(frequency_csv, result["frequency_rows"])
    plot_status = _write_plot(plot_path, result["frequency_rows"], args)
    decision_checks = result["decision_checks"]
    decision = (
        "REVIEW_CROSS_SOLVER_RESIDUAL_FOR_CANDIDATE_RANKING_ONLY"
        if all(decision_checks.values())
        else "KEEP_RAW_EMX_BASELINE_NO_RESIDUAL_MODEL"
    )
    payload = _base_payload(dataset, checks, record_paths, args)
    payload.update(
        {
            "overall_status": "COMPLETE_REVIEW_REQUIRED",
            "decision": decision,
            "model_contract": {
                "baseline": "HFSS prediction equals the paired raw EMX reciprocal complex S4P",
                "residual_arm": "HFSS_hat = rho * EMX + shrinkage mean residual template",
                "evaluation": "leave one independent geometry out",
                "rho_constraint": [float(args.min_rho), float(args.max_rho)],
                "raw_publication_gate_unchanged": True,
            },
            "metrics": result["metrics"],
            "decision_checks": decision_checks,
            "artifacts": {
                "summary": str(summary_path),
                "folds": str(fold_csv),
                "frequency_errors": str(frequency_csv),
                "plot": str(plot_path),
                "plot_status": plot_status,
                "report": str(report_path),
            },
        }
    )
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print("overall_status=COMPLETE_REVIEW_REQUIRED")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hfss-validation-record", action="append", default=[])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--expected-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-points", type=int, default=111)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--min-rho", type=float, default=0.5)
    parser.add_argument("--max-rho", type=float, default=1.5)
    parser.add_argument("--min-fullband-improvement", type=float, default=0.10)
    parser.add_argument("--min-target-improvement", type=float, default=0.10)
    parser.add_argument("--max-frequency-regression-fraction", type=float, default=0.20)
    parser.add_argument("--max-fold-regression-fraction", type=float, default=0.20)
    parser.add_argument("--max-passivity-violation-increase", type=float, default=0.05)
    parser.add_argument("--regression-tolerance", type=float, default=0.05)
    parser.add_argument("--passivity-tolerance", type=float, default=1.0e-6)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.min_samples < 3 or args.expected_points < 2:
        parser.error("minimum samples must be >=3 and expected points >=2")
    if not 0.0 < args.min_rho <= args.max_rho:
        parser.error("rho bounds must satisfy 0 < min <= max")
    for value in (
        args.min_fullband_improvement,
        args.min_target_improvement,
        args.max_frequency_regression_fraction,
        args.max_fold_regression_fraction,
        args.max_passivity_violation_increase,
        args.regression_tolerance,
    ):
        if not 0.0 <= value <= 1.0:
            parser.error("fraction thresholds must be in [0,1]")
    return args


def _load_dataset(paths: list[Path], args: argparse.Namespace) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    rejects: list[dict[str, str]] = []
    content_digest = hashlib.sha256()
    expected_frequency = _expected_frequency(args)
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            contract = record.get("contract") if isinstance(record.get("contract"), dict) else {}
            full_path = _resolve_child(path, record.get("full_grid_comparison_summary"))
            full = json.loads(full_path.read_text(encoding="utf-8"))
            emx_path = Path(str(full.get("emx_source") or "")).expanduser().resolve()
            hfss_path = Path(str(full.get("hfss_ads_source") or "")).expanduser().resolve()
            _validate_record_contract(record, contract, full, emx_path, hfss_path)
            emx = load_touchstone(emx_path)
            hfss = load_touchstone(hfss_path)
            emx_frequency = np.asarray(emx.freqs_hz, dtype=float)
            hfss_frequency = np.asarray(hfss.freqs_hz, dtype=float)
            _validate_touchstone(np.asarray(emx.s_matrix), emx_frequency, expected_frequency, args)
            _validate_touchstone(np.asarray(hfss.s_matrix), hfss_frequency, expected_frequency, args)
            if np.max(np.abs(emx_frequency - hfss_frequency)) > float(args.frequency_tolerance_hz):
                raise ValueError("paired frequency grids differ")
            emx_matrix = _reciprocal(np.asarray(emx.s_matrix, dtype=np.complex128))
            hfss_matrix = _reciprocal(np.asarray(hfss.s_matrix, dtype=np.complex128))
            sample = {
                "sample_id": str(record.get("sample_id") or ""),
                "geometry_sha256": str(contract.get("geometry_contract_sha256") or ""),
                "record_path": str(path),
                "record_sha256": _sha256(path),
                "emx_path": str(emx_path),
                "hfss_path": str(hfss_path),
                "emx_sha256": _sha256(emx_path),
                "hfss_sha256": _sha256(hfss_path),
                "emx": emx_matrix,
                "hfss": hfss_matrix,
            }
            samples.append(sample)
            for key in ("sample_id", "geometry_sha256", "record_sha256", "emx_sha256", "hfss_sha256"):
                content_digest.update(str(sample[key]).encode("ascii", errors="ignore"))
                content_digest.update(b"\0")
            content_digest.update(np.asarray(emx_matrix, dtype=np.complex64).tobytes())
            content_digest.update(np.asarray(hfss_matrix, dtype=np.complex64).tobytes())
        except Exception as exc:  # noqa: BLE001
            rejects.append({"record": str(path), "reason": f"{type(exc).__name__}: {exc}"})
    return {
        "provided_count": len(paths),
        "valid_count": len(samples),
        "samples": samples,
        "rejects": rejects,
        "frequency_hz": expected_frequency,
        "content_sha256": content_digest.hexdigest() if samples else "",
    }


def _validate_record_contract(
    record: dict[str, Any],
    contract: dict[str, Any],
    full: dict[str, Any],
    emx_path: Path,
    hfss_path: Path,
) -> None:
    if record.get("overall_status") != "PASS" or full.get("overall_status") != "PASS":
        raise ValueError("raw cross-solver comparison is not PASS")
    if not str(record.get("sample_id") or "").strip():
        raise ValueError("sample id missing")
    for key in ("same_geometry_verified", "same_process_stack_verified", "same_port_mapping_verified", "independent_geometry"):
        if contract.get(key) is not True:
            raise ValueError(f"contract {key} is not true")
    if str(contract.get("expected_touchstone_suffix") or "").lower() != ".s4p":
        raise ValueError("contract is not S4P")
    if int(contract.get("expected_port_count") or 0) != 4:
        raise ValueError("contract is not four-port")
    if str(contract.get("port_pairs") or "").replace(" ", "") != "1,2:3,4":
        raise ValueError("port-pair contract mismatch")
    geometry_sha = str(contract.get("geometry_contract_sha256") or "")
    if len(geometry_sha) != 64:
        raise ValueError("geometry SHA256 missing")
    if not _matches_sha(emx_path, contract.get("emx_touchstone_sha256")):
        raise ValueError("EMX Touchstone SHA256 mismatch")
    if not _matches_sha(hfss_path, contract.get("hfss_touchstone_sha256")):
        raise ValueError("HFSS Touchstone SHA256 mismatch")
    target = full.get("target_marker") if isinstance(full.get("target_marker"), dict) else {}
    metrics = target.get("metrics") if isinstance(target.get("metrics"), dict) else {}
    if target.get("frequency_status") != "PASS":
        raise ValueError("target frequency is not PASS")
    if any((metrics.get(name) or {}).get("status") != "PASS" for name in PRIMARY_METRICS):
        raise ValueError("raw target physical metrics are not PASS")


def _validate_touchstone(
    matrix: np.ndarray,
    frequency: np.ndarray,
    expected_frequency: np.ndarray,
    args: argparse.Namespace,
) -> None:
    expected_shape = (int(args.expected_points), 4, 4)
    if matrix.shape != expected_shape:
        raise ValueError(f"S shape {matrix.shape}, expected {expected_shape}")
    if not np.isfinite(matrix.real).all() or not np.isfinite(matrix.imag).all():
        raise ValueError("non-finite S matrix")
    if frequency.shape != expected_frequency.shape:
        raise ValueError("frequency point count mismatch")
    if np.max(np.abs(frequency - expected_frequency)) > float(args.frequency_tolerance_hz):
        raise ValueError("frequency grid mismatch")


def _checks(dataset: dict[str, Any], args: argparse.Namespace) -> dict[str, bool]:
    samples = dataset.get("samples") or []
    sample_ids = [sample["sample_id"] for sample in samples]
    geometry_ids = [sample["geometry_sha256"] for sample in samples]
    source_pairs = [(sample["emx_sha256"], sample["hfss_sha256"]) for sample in samples]
    return {
        "records_provided": int(dataset.get("provided_count") or 0) > 0,
        "all_records_valid": int(dataset.get("valid_count") or 0) == int(dataset.get("provided_count") or 0),
        "minimum_independent_samples": int(dataset.get("valid_count") or 0) >= int(args.min_samples),
        "sample_ids_unique": len(sample_ids) == len(set(sample_ids)),
        "geometry_contracts_unique": len(geometry_ids) == len(set(geometry_ids)),
        "touchstone_pairs_unique": len(source_pairs) == len(set(source_pairs)),
        "content_hash_present": len(str(dataset.get("content_sha256") or "")) == 64,
    }


def _leave_one_geometry_out(dataset: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    samples = dataset["samples"]
    emx_matrix = np.asarray([sample["emx"] for sample in samples], dtype=np.complex128)
    hfss_matrix = np.asarray([sample["hfss"] for sample in samples], dtype=np.complex128)
    emx_upper = _upper(emx_matrix)
    hfss_upper = _upper(hfss_matrix)
    calibrated = np.empty_like(hfss_upper)
    fold_rows: list[dict[str, Any]] = []
    for holdout in range(len(samples)):
        train = np.asarray([index for index in range(len(samples)) if index != holdout], dtype=int)
        rho, delta, reliability = _fit_residual(emx_upper[train], hfss_upper[train], args)
        prediction = rho * emx_upper[holdout] + delta
        calibrated[holdout] = prediction
        raw_rmse = _complex_rmse(emx_upper[holdout], hfss_upper[holdout])
        calibrated_rmse = _complex_rmse(prediction, hfss_upper[holdout])
        target_index = _target_index(dataset["frequency_hz"], args)
        raw_target = _complex_rmse(emx_upper[holdout, target_index], hfss_upper[holdout, target_index])
        calibrated_target = _complex_rmse(prediction[target_index], hfss_upper[holdout, target_index])
        fold_rows.append(
            {
                "sample_id": samples[holdout]["sample_id"],
                "geometry_sha256": samples[holdout]["geometry_sha256"],
                "rho": rho,
                "mean_reliability": float(np.mean(reliability)),
                "raw_fullband_complex_rmse": raw_rmse,
                "calibrated_fullband_complex_rmse": calibrated_rmse,
                "fullband_relative_improvement": _relative_improvement(raw_rmse, calibrated_rmse),
                "raw_target_complex_rmse": raw_target,
                "calibrated_target_complex_rmse": calibrated_target,
                "target_relative_improvement": _relative_improvement(raw_target, calibrated_target),
            }
        )

    raw_full = _complex_rmse(emx_upper, hfss_upper)
    calibrated_full = _complex_rmse(calibrated, hfss_upper)
    target_index = _target_index(dataset["frequency_hz"], args)
    raw_target = _complex_rmse(emx_upper[:, target_index], hfss_upper[:, target_index])
    calibrated_target = _complex_rmse(calibrated[:, target_index], hfss_upper[:, target_index])
    raw_frequency = np.sqrt(np.mean(np.abs(emx_upper - hfss_upper) ** 2, axis=(0, 2)))
    calibrated_frequency = np.sqrt(np.mean(np.abs(calibrated - hfss_upper) ** 2, axis=(0, 2)))
    frequency_regression = calibrated_frequency > raw_frequency * (1.0 + float(args.regression_tolerance))
    fold_regression = np.asarray(
        [row["calibrated_fullband_complex_rmse"] > row["raw_fullband_complex_rmse"] * (1.0 + float(args.regression_tolerance)) for row in fold_rows],
        dtype=bool,
    )
    calibrated_matrix = _from_upper(calibrated)
    raw_passivity = _passivity_violation_fraction(emx_matrix, args)
    calibrated_passivity = _passivity_violation_fraction(calibrated_matrix, args)
    hfss_passivity = _passivity_violation_fraction(hfss_matrix, args)
    metrics = {
        "raw_fullband_complex_rmse": raw_full,
        "calibrated_fullband_complex_rmse": calibrated_full,
        "fullband_relative_improvement": _relative_improvement(raw_full, calibrated_full),
        "raw_target_complex_rmse": raw_target,
        "calibrated_target_complex_rmse": calibrated_target,
        "target_relative_improvement": _relative_improvement(raw_target, calibrated_target),
        "frequency_regression_fraction": float(np.mean(frequency_regression)),
        "fold_regression_fraction": float(np.mean(fold_regression)),
        "raw_emx_passivity_violation_fraction": raw_passivity,
        "calibrated_passivity_violation_fraction": calibrated_passivity,
        "hfss_passivity_violation_fraction": hfss_passivity,
        "calibrated_passivity_violation_increase": calibrated_passivity - raw_passivity,
        "target_frequency_ghz": float(dataset["frequency_hz"][target_index] / 1.0e9),
    }
    decision_checks = {
        "fullband_improvement_meets_threshold": metrics["fullband_relative_improvement"] >= float(args.min_fullband_improvement),
        "target_improvement_meets_threshold": metrics["target_relative_improvement"] >= float(args.min_target_improvement),
        "frequency_regression_controlled": metrics["frequency_regression_fraction"] <= float(args.max_frequency_regression_fraction),
        "fold_regression_controlled": metrics["fold_regression_fraction"] <= float(args.max_fold_regression_fraction),
        "passivity_not_materially_worse": metrics["calibrated_passivity_violation_increase"] <= float(args.max_passivity_violation_increase),
    }
    frequency_rows = [
        {
            "frequency_hz": float(frequency),
            "frequency_ghz": float(frequency / 1.0e9),
            "raw_complex_rmse": float(raw_frequency[index]),
            "calibrated_complex_rmse": float(calibrated_frequency[index]),
            "relative_improvement": _relative_improvement(raw_frequency[index], calibrated_frequency[index]),
            "regression_over_tolerance": bool(frequency_regression[index]),
        }
        for index, frequency in enumerate(dataset["frequency_hz"])
    ]
    return {
        "metrics": metrics,
        "decision_checks": {key: bool(value) for key, value in decision_checks.items()},
        "fold_rows": fold_rows,
        "frequency_rows": frequency_rows,
    }


def _fit_residual(
    emx: np.ndarray,
    hfss: np.ndarray,
    args: argparse.Namespace,
) -> tuple[float, np.ndarray, np.ndarray]:
    denominator = float(np.sum(np.abs(emx) ** 2))
    numerator = float(np.real(np.sum(np.conjugate(emx) * hfss)))
    rho = 1.0 if denominator <= np.finfo(float).eps else numerator / denominator
    rho = float(np.clip(rho, float(args.min_rho), float(args.max_rho)))
    residual = hfss - rho * emx
    mean_residual = np.mean(residual, axis=0)
    residual_variance = np.mean(np.abs(residual - mean_residual[None, ...]) ** 2, axis=0)
    mean_signal = np.abs(mean_residual) ** 2
    reliability = mean_signal / (mean_signal + residual_variance / max(1, len(residual)) + 1.0e-15)
    delta = reliability * mean_residual
    return rho, delta, reliability


def _upper(matrix: np.ndarray) -> np.ndarray:
    rows, columns = np.triu_indices(4)
    return matrix[:, :, rows, columns]


def _from_upper(upper: np.ndarray) -> np.ndarray:
    rows, columns = np.triu_indices(4)
    matrix = np.zeros((len(upper), upper.shape[1], 4, 4), dtype=np.complex128)
    matrix[:, :, rows, columns] = upper
    matrix[:, :, columns, rows] = upper
    return matrix


def _reciprocal(matrix: np.ndarray) -> np.ndarray:
    return 0.5 * (matrix + np.swapaxes(matrix, 1, 2))


def _passivity_violation_fraction(matrix: np.ndarray, args: argparse.Namespace) -> float:
    singular = np.linalg.svd(matrix, compute_uv=False)
    maximum = np.max(singular, axis=-1)
    return float(np.mean(maximum > 1.0 + float(args.passivity_tolerance)))


def _complex_rmse(predicted: np.ndarray, reference: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.abs(np.asarray(predicted) - np.asarray(reference)) ** 2)))


def _relative_improvement(raw: float, corrected: float) -> float:
    raw_value = float(raw)
    if raw_value <= np.finfo(float).eps:
        return 0.0 if corrected <= np.finfo(float).eps else -math.inf
    return float((raw_value - float(corrected)) / raw_value)


def _target_index(frequency: np.ndarray, args: argparse.Namespace) -> int:
    return int(np.argmin(np.abs(np.asarray(frequency) - float(args.target_ghz) * 1.0e9)))


def _expected_frequency(args: argparse.Namespace) -> np.ndarray:
    expected = float(args.expected_start_ghz) * 1.0e9 + np.arange(int(args.expected_points)) * float(args.expected_step_ghz) * 1.0e9
    if abs(float(expected[-1]) - float(args.expected_stop_ghz) * 1.0e9) > float(args.frequency_tolerance_hz):
        raise ValueError("expected frequency contract is internally inconsistent")
    return expected


def _base_payload(
    dataset: dict[str, Any],
    checks: dict[str, bool],
    record_paths: list[Path],
    args: argparse.Namespace,
) -> dict[str, Any]:
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks": checks,
        "provided_record_count": len(record_paths),
        "valid_record_count": int(dataset.get("valid_count") or 0),
        "record_paths": [str(path) for path in record_paths],
        "dataset_content_sha256": str(dataset.get("content_sha256") or ""),
        "rejects": dataset.get("rejects") or [],
        "frequency_contract": {
            "start_ghz": float(args.expected_start_ghz),
            "stop_ghz": float(args.expected_stop_ghz),
            "step_ghz": float(args.expected_step_ghz),
            "points": int(args.expected_points),
            "ports": 4,
        },
        "literature_basis": {
            "source": "Machine-learning-based global optimization of microwave passives with variable-fidelity EM models and response features, Scientific Reports 2024",
            "url": "https://www.nature.com/articles/s41598-024-56823-7",
            "adaptation": "Paired-solver rho plus residual modeling with independent-geometry validation.",
        },
        "scientific_boundary": (
            "The residual arm is an advisory model ablation. It never changes the raw EMX-HFSS <=10% gate, "
            "never repairs failed geometry/process/port evidence, and cannot replace real Touchstones."
        ),
        "arguments": vars(args),
    }


def _write_plot(path: Path, rows: list[dict[str, Any]], args: argparse.Namespace) -> str:
    frequency = np.asarray([row["frequency_ghz"] for row in rows], dtype=float)
    raw = np.asarray([row["raw_complex_rmse"] for row in rows], dtype=float)
    calibrated = np.asarray([row["calibrated_complex_rmse"] for row in rows], dtype=float)
    improvement = np.asarray([row["relative_improvement"] for row in rows], dtype=float) * 100.0
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5), constrained_layout=True)
    fig.patch.set_facecolor("white")
    for axis in axes:
        axis.set_facecolor("white")
        axis.grid(True, alpha=0.25)
        axis.axvline(float(args.target_ghz), color="#303030", linestyle=":", linewidth=1.4)
    axes[0].plot(frequency, raw, label="Raw EMX baseline", color="#1f5f99", linewidth=2.0)
    axes[0].plot(frequency, calibrated, label="LOO rho + residual", color="#c44e31", linewidth=2.0)
    axes[0].set_title("Paired EMX-HFSS reciprocal complex-S RMSE")
    axes[0].set_xlabel("Frequency (GHz)")
    axes[0].set_ylabel("Complex-S RMSE")
    axes[0].legend(facecolor="white", framealpha=1.0)
    axes[1].plot(frequency, improvement, color="#397a4a", linewidth=2.0)
    axes[1].axhline(float(args.min_fullband_improvement) * 100.0, color="#777777", linestyle="--")
    axes[1].axhline(-float(args.regression_tolerance) * 100.0, color="#b23a32", linestyle="--")
    axes[1].set_title("Frequency-wise residual-model improvement")
    axes[1].set_xlabel("Frequency (GHz)")
    axes[1].set_ylabel("Relative improvement (%)")
    fig.suptitle("Leave-one-geometry-out EMX-HFSS residual ablation", fontsize=15)
    fig.savefig(path, dpi=200, facecolor="white", transparent=False)
    plt.close(fig)
    return "PASS" if path.is_file() and path.stat().st_size > 0 else "FAIL"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _render_report(data: dict[str, Any]) -> str:
    metrics = data.get("metrics") or {}
    lines = [
        "# EMX-HFSS cross-solver residual benchmark",
        "",
        f"- Overall status: **{data.get('overall_status')}**",
        f"- Decision: **{data.get('decision')}**",
        f"- Valid independent records: `{data.get('valid_record_count')}`",
        f"- Raw full-band complex-S RMSE: `{metrics.get('raw_fullband_complex_rmse')}`",
        f"- Residual-arm full-band complex-S RMSE: `{metrics.get('calibrated_fullband_complex_rmse')}`",
        f"- Full-band relative improvement: `{metrics.get('fullband_relative_improvement')}`",
        f"- 15 GHz relative improvement: `{metrics.get('target_relative_improvement')}`",
        "",
        str(data.get("scientific_boundary") or ""),
        "",
    ]
    return "\n".join(lines)


def _resolve_child(parent: Path, raw: Any) -> Path:
    path = Path(str(raw or "")).expanduser()
    return (path if path.is_absolute() else parent.parent / path).resolve()


def _matches_sha(path: Path, expected: Any) -> bool:
    expected_text = str(expected or "").lower()
    return len(expected_text) == 64 and path.is_file() and _sha256(path) == expected_text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
