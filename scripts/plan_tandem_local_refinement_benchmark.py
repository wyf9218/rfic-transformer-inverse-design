#!/usr/bin/env python3
"""Plan an equal-budget inverse-only versus local-refinement EMX benchmark.

The saved tandem inverse network supplies a one-shot geometry. A frozen
geometry-to-physical-feature proxy then refines that geometry with bounded
L-BFGS-B. Proxy improvements are candidate-priority evidence only; both arms
remain unlabeled until real EMX S4P files return.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.optimize import minimize  # noqa: E402


ARMS = ("inverse_only", "inverse_lbfgsb")
EXPECTED_INPUT_COLUMNS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
EXPECTED_GEOMETRY_COUNT = 10
DEFAULT_RESPONSE_SPANS = np.asarray((2.5, 2.5, 20.0, 0.8), dtype=float)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary_path = Path(args.tandem_summary).expanduser().resolve()
    weights_path = Path(args.weights_npz).expanduser().resolve()
    predictions_path = Path(args.predictions_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tandem = _read_json(summary_path)
    model = _load_model(weights_path)
    input_columns = tuple(tandem.get("input_columns") or ())
    geometry_columns = tuple(tandem.get("geometry_columns") or ())
    targets = _load_targets(predictions_path, input_columns)
    analysis = _plan(targets, model, input_columns, geometry_columns, args)
    checks = _checks(tandem, model, targets, input_columns, geometry_columns, analysis, args)
    status = "PASS" if all(checks.values()) else "FAIL"

    combined_path = out_dir / "tandem_local_refinement_candidates.csv"
    arm_paths = {arm: out_dir / f"arm_{arm}_candidates.csv" for arm in ARMS}
    figure_path = out_dir / "tandem_local_refinement_proxy_comparison.png"
    report_path = out_dir / "tandem_local_refinement_plan_report.md"
    output_summary = out_dir / "tandem_local_refinement_plan_summary.json"
    rows = analysis.get("candidate_rows") or []
    _write_csv(combined_path, rows)
    for arm, path in arm_paths.items():
        _write_csv(path, [row for row in rows if row.get("benchmark_arm") == arm])
    if analysis.get("available") is True:
        _plot(figure_path, analysis)

    public_analysis = {key: value for key, value in analysis.items() if key != "candidate_rows"}
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "RUN_EQUAL_BUDGET_REAL_EMX_REFINEMENT_BENCHMARK"
            if status == "PASS"
            else "DO_NOT_RUN_REFINEMENT_BENCHMARK_FIX_PLAN"
        ),
        "outcome_status": "AWAITING_REAL_EMX",
        "tandem_summary": str(summary_path),
        "tandem_summary_sha256": _sha256(summary_path),
        "weights_npz": str(weights_path),
        "weights_npz_sha256": _sha256(weights_path),
        "predictions_csv": str(predictions_path),
        "predictions_csv_sha256": _sha256(predictions_path),
        "arms": list(ARMS),
        "arm_budget": int(args.candidate_count),
        "input_columns": list(input_columns),
        "geometry_columns": list(geometry_columns),
        "optimizer_contract": {
            "method": "L-BFGS-B",
            "bounds": "observed_training_envelope_in_normalized_geometry_space",
            "objective": "declared-range response MSE plus trust-region penalty to inverse proposal",
            "trust_weight": float(args.trust_weight),
            "max_iterations": int(args.max_iterations),
            "gradient": "analytic_frozen_forward_proxy_jacobian",
        },
        "checks": checks,
        "analysis": public_analysis,
        "artifacts": {
            "combined_candidates_csv": str(combined_path),
            "arm_candidate_csvs": {arm: str(path) for arm, path in arm_paths.items()},
            "proxy_comparison_figure": str(figure_path),
            "report_md": str(report_path),
        },
        "local_literature_basis": [
            {
                "source": "PulseRF.pdf, Sec. 3.3",
                "adaptation": "Use a fast frozen surrogate and constrained optimization to refine a design proposal.",
            },
            {
                "source": "Tandem_Neural_Network_Based_Design_of_Multiband_Antennas.pdf",
                "adaptation": "Keep response consistency and explicit design/fabrication constraints separate.",
            },
            {
                "source": "TMTT-2026-02-0420_Proof_hi.pdf",
                "adaptation": "Retain the frozen forward-consistency contract and physical-range-balanced response error.",
            },
        ],
        "scientific_boundary": (
            "PASS proves only that an equal-budget, pair-matched, geometry-disjoint candidate plan was built. "
            "The proxy objective is not an EM label and cannot establish improvement. Every candidate remains "
            "AWAITING_REAL_EMX until a nonempty S4P return is evaluated by the companion real-EMX script."
        ),
        "arguments": vars(args),
    }
    output_summary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"outcome_status={payload['outcome_status']}")
    print(f"selected_pairs={public_analysis.get('selected_pair_count', 0)}")
    print(f"summary={output_summary}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tandem-summary", required=True)
    parser.add_argument("--weights-npz", required=True)
    parser.add_argument("--predictions-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--candidate-count", type=int, default=256)
    parser.add_argument("--min-source-rows", type=int, default=800_000)
    parser.add_argument("--min-target-rows", type=int, default=512)
    parser.add_argument("--max-target-scan", type=int, default=10_000)
    parser.add_argument("--trust-weight", type=float, default=0.01)
    parser.add_argument("--max-iterations", type=int, default=100)
    parser.add_argument("--gradient-tolerance", type=float, default=1.0e-8)
    parser.add_argument("--min-proxy-response-improvement", type=float, default=1.0e-10)
    parser.add_argument("--min-geometry-separation", type=float, default=1.0e-8)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.candidate_count < 1 or args.min_source_rows < 1 or args.min_target_rows < 1:
        parser.error("candidate and row counts must be positive")
    if args.max_target_scan < args.candidate_count:
        parser.error("--max-target-scan must be at least --candidate-count")
    if args.trust_weight < 0.0 or args.max_iterations < 1 or args.gradient_tolerance <= 0.0:
        parser.error("optimizer settings are invalid")
    if args.min_proxy_response_improvement < 0.0 or args.min_geometry_separation < 0.0:
        parser.error("minimum improvements and separations must be nonnegative")
    return args


def _load_model(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"available": False, "error": "", "path": str(path)}
    if not path.is_file():
        result["error"] = "weights NPZ is missing"
        return result
    try:
        with np.load(path, allow_pickle=False) as archive:
            forward_weights = _numbered_arrays(archive, "forward_weight_")
            forward_biases = _numbered_arrays(archive, "forward_bias_")
            inverse_weights = _numbered_arrays(archive, "inverse_weight_")
            inverse_biases = _numbered_arrays(archive, "inverse_bias_")
            normalization = {
                key: np.asarray(archive[f"normalization__{key}"], dtype=float)
                for key in (
                    "x_mean",
                    "x_scale",
                    "y_mean",
                    "y_scale",
                    "geometry_lower",
                    "geometry_upper",
                )
                if f"normalization__{key}" in archive
            }
            normalization["response_loss_physical_spans"] = np.asarray(
                archive["normalization__response_loss_physical_spans"]
                if "normalization__response_loss_physical_spans" in archive
                else DEFAULT_RESPONSE_SPANS,
                dtype=float,
            )
        _validate_layers(forward_weights, forward_biases, "forward")
        _validate_layers(inverse_weights, inverse_biases, "inverse")
        result.update(
            {
                "available": True,
                "forward_weights": forward_weights,
                "forward_biases": forward_biases,
                "inverse_weights": inverse_weights,
                "inverse_biases": inverse_biases,
                "normalization": normalization,
            }
        )
    except Exception as exc:  # noqa: BLE001 - exact evidence error is recorded.
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _validate_layers(weights: list[np.ndarray], biases: list[np.ndarray], name: str) -> None:
    if not weights or len(weights) != len(biases):
        raise ValueError(f"{name} weights and biases are incomplete")
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        if weight.ndim != 2 or bias.shape != (weight.shape[1],):
            raise ValueError(f"invalid {name} layer {index} shape")
        if index and weights[index - 1].shape[1] != weight.shape[0]:
            raise ValueError(f"{name} layer {index} width mismatch")
        if not np.isfinite(weight).all() or not np.isfinite(bias).all():
            raise ValueError(f"non-finite {name} model value")


def _numbered_arrays(archive: Any, prefix: str) -> list[np.ndarray]:
    values = []
    index = 0
    while f"{prefix}{index}" in archive:
        values.append(np.asarray(archive[f"{prefix}{index}"], dtype=float))
        index += 1
    return values


def _load_targets(path: Path, input_columns: tuple[str, ...]) -> dict[str, Any]:
    result = {"path_exists": path.is_file(), "columns_present": False, "row_count": 0, "targets": []}
    if not path.is_file() or not input_columns:
        return result
    target_columns = tuple(f"target__{column.removeprefix('input__')}" for column in input_columns)
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = set(target_columns) | {"source_row_index"}
        result["columns_present"] = required.issubset(set(reader.fieldnames or []))
        if not result["columns_present"]:
            return result
        for row_index, row in enumerate(reader):
            result["row_count"] += 1
            values = [_finite(row.get(column)) for column in target_columns]
            source_index = _integer(row.get("source_row_index"))
            if source_index is None or any(value is None for value in values):
                continue
            rows.append(
                {
                    "prediction_row_index": row_index,
                    "source_row_index": int(source_index),
                    "target_physical": np.asarray(values, dtype=float),
                }
            )
    result["targets"] = rows
    result["usable_count"] = len(rows)
    return result


def _plan(
    targets: dict[str, Any],
    model: dict[str, Any],
    input_columns: tuple[str, ...],
    geometry_columns: tuple[str, ...],
    args: argparse.Namespace,
) -> dict[str, Any]:
    if model.get("available") is not True or not targets.get("targets"):
        return {"available": False, "candidate_rows": [], "selected_pair_count": 0}
    normalization = model["normalization"]
    required_shapes = {
        "x_mean": (len(input_columns),),
        "x_scale": (len(input_columns),),
        "y_mean": (len(geometry_columns),),
        "y_scale": (len(geometry_columns),),
        "geometry_lower": (len(geometry_columns),),
        "geometry_upper": (len(geometry_columns),),
        "response_loss_physical_spans": (len(input_columns),),
    }
    if any(np.asarray(normalization.get(key, [])).shape != shape for key, shape in required_shapes.items()):
        return {"available": False, "candidate_rows": [], "selected_pair_count": 0, "error": "normalization shape mismatch"}
    x_scale = normalization["x_scale"]
    y_scale = normalization["y_scale"]
    lower = normalization["geometry_lower"]
    upper = normalization["geometry_upper"]
    response_spans = normalization["response_loss_physical_spans"]
    if (
        np.any(x_scale <= 0.0)
        or np.any(y_scale <= 0.0)
        or np.any(response_spans <= 0.0)
        or np.any(upper <= lower)
    ):
        return {"available": False, "candidate_rows": [], "selected_pair_count": 0, "error": "invalid normalization scale or bounds"}

    rng = np.random.default_rng(int(args.seed))
    source = targets["targets"]
    order = rng.permutation(len(source))[: min(len(source), int(args.max_target_scan))]
    candidate_rows: list[dict[str, Any]] = []
    pair_metrics = []
    geometry_digests: set[str] = set()
    optimizer_success_count = 0
    for source_position in order:
        target = source[int(source_position)]
        target_physical = np.asarray(target["target_physical"], dtype=float)
        target_normalized = (target_physical - normalization["x_mean"]) / x_scale
        baseline_geometry = _predict_inverse(
            target_normalized[None, :],
            model["inverse_weights"],
            model["inverse_biases"],
            lower,
            upper,
        )[0]
        baseline_prediction = _predict(
            baseline_geometry[None, :], model["forward_weights"], model["forward_biases"]
        )[0]
        baseline_response_mse = _response_mse(baseline_prediction, target_normalized, x_scale, response_spans)
        refined = _refine_geometry(
            baseline_geometry,
            target_normalized,
            model["forward_weights"],
            model["forward_biases"],
            lower,
            upper,
            x_scale,
            response_spans,
            args,
        )
        if refined["optimizer_success"]:
            optimizer_success_count += 1
        refined_geometry = refined["geometry"]
        refined_response_mse = float(refined["response_mse"])
        separation = float(np.sqrt(np.mean(((refined_geometry - baseline_geometry) / (upper - lower)) ** 2)))
        improvement = baseline_response_mse - refined_response_mse
        if not refined["optimizer_success"] or improvement < float(args.min_proxy_response_improvement):
            continue
        if separation < float(args.min_geometry_separation):
            continue
        baseline_physical = baseline_geometry * y_scale + normalization["y_mean"]
        refined_physical = refined_geometry * y_scale + normalization["y_mean"]
        baseline_digest = _vector_digest(baseline_physical)
        refined_digest = _vector_digest(refined_physical)
        if baseline_digest == refined_digest or baseline_digest in geometry_digests or refined_digest in geometry_digests:
            continue
        geometry_digests.update((baseline_digest, refined_digest))

        pair_number = len(pair_metrics)
        pair_id = f"target_{int(target['source_row_index']):09d}_{pair_number:04d}"
        refined_prediction = np.asarray(refined["prediction"], dtype=float)
        common = {
            "pair_id": pair_id,
            "target_source_row_index": int(target["source_row_index"]),
            "target_prediction_row_index": int(target["prediction_row_index"]),
            "label_status": "AWAITING_REAL_EMX",
            "drc_status": "NOT_EVALUATED_REQUIRED_BEFORE_REAL_EMX",
        }
        for index, column in enumerate(input_columns):
            name = column.removeprefix("input__")
            common[f"target__{name}"] = float(target_physical[index])
        arm_data = (
            ("inverse_only", baseline_physical, baseline_prediction, baseline_response_mse, 0.0),
            ("inverse_lbfgsb", refined_physical, refined_prediction, refined_response_mse, float(refined["trust_penalty"])),
        )
        for arm, physical_geometry, proxy_prediction, response_mse, trust_penalty in arm_data:
            row = {
                **common,
                "candidate_id": f"{pair_id}__{arm}",
                "benchmark_arm": arm,
                "proxy_response_range_mse": float(response_mse),
                "proxy_response_range_rmse": float(math.sqrt(max(0.0, response_mse))),
                "proxy_trust_penalty": float(trust_penalty),
                "optimizer_method": "NONE" if arm == "inverse_only" else "L-BFGS-B",
                "optimizer_success": "true" if arm == "inverse_only" else str(bool(refined["optimizer_success"])).lower(),
                "optimizer_iterations": 0 if arm == "inverse_only" else int(refined["iterations"]),
                "optimizer_function_evaluations": 1 if arm == "inverse_only" else int(refined["function_evaluations"]),
            }
            for index, column in enumerate(input_columns):
                name = column.removeprefix("input__")
                proxy_physical = proxy_prediction[index] * x_scale[index] + normalization["x_mean"][index]
                row[f"proxy__{name}"] = float(proxy_physical)
            for index, column in enumerate(geometry_columns):
                row[column] = float(physical_geometry[index])
            candidate_rows.append(row)
        pair_metrics.append(
            {
                "pair_id": pair_id,
                "baseline_response_rmse": float(math.sqrt(max(0.0, baseline_response_mse))),
                "refined_response_rmse": float(math.sqrt(max(0.0, refined_response_mse))),
                "relative_response_improvement": float(improvement / max(baseline_response_mse, 1.0e-18)),
                "geometry_separation": separation,
                "optimizer_iterations": int(refined["iterations"]),
            }
        )
        if len(pair_metrics) >= int(args.candidate_count):
            break

    baseline_rmse = np.asarray([item["baseline_response_rmse"] for item in pair_metrics], dtype=float)
    refined_rmse = np.asarray([item["refined_response_rmse"] for item in pair_metrics], dtype=float)
    return {
        "available": bool(pair_metrics),
        "candidate_rows": candidate_rows,
        "selected_pair_count": len(pair_metrics),
        "selected_candidate_count": len(candidate_rows),
        "target_scan_count": int(len(order)),
        "optimizer_success_count_during_scan": int(optimizer_success_count),
        "geometry_digest_unique_count": len(geometry_digests),
        "arm_selected_counts": {
            arm: sum(row.get("benchmark_arm") == arm for row in candidate_rows) for arm in ARMS
        },
        "proxy_metrics": {
            "baseline_mean_range_rmse": float(np.mean(baseline_rmse)) if len(baseline_rmse) else None,
            "refined_mean_range_rmse": float(np.mean(refined_rmse)) if len(refined_rmse) else None,
            "mean_relative_response_mse_improvement": float(
                np.mean([item["relative_response_improvement"] for item in pair_metrics])
            )
            if pair_metrics
            else None,
            "median_normalized_geometry_separation": float(
                np.median([item["geometry_separation"] for item in pair_metrics])
            )
            if pair_metrics
            else None,
        },
        "pair_metrics": pair_metrics,
    }


def _refine_geometry(
    baseline: np.ndarray,
    target: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    lower: np.ndarray,
    upper: np.ndarray,
    x_scale: np.ndarray,
    response_spans: np.ndarray,
    args: argparse.Namespace,
) -> dict[str, Any]:
    geometry_span = upper - lower
    trust_weight = float(args.trust_weight)

    def objective(value: np.ndarray) -> tuple[float, np.ndarray]:
        prediction, jacobian = _forward_and_jacobian(value[None, :], weights, biases)
        prediction = prediction[0]
        jacobian = jacobian[0]
        scale = x_scale / response_spans
        delta = prediction - target
        response_mse = float(np.mean((delta * scale) ** 2))
        response_gradient = jacobian @ (2.0 * delta * scale**2 / len(target))
        trust_vector = (value - baseline) / geometry_span
        trust_penalty = trust_weight * float(np.mean(trust_vector**2))
        trust_gradient = 2.0 * trust_weight * (value - baseline) / (len(value) * geometry_span**2)
        return response_mse + trust_penalty, response_gradient + trust_gradient

    result = minimize(
        objective,
        np.asarray(baseline, dtype=float),
        method="L-BFGS-B",
        jac=True,
        bounds=list(zip(lower, upper)),
        options={"maxiter": int(args.max_iterations), "gtol": float(args.gradient_tolerance), "ftol": 1.0e-15},
    )
    geometry = np.clip(np.asarray(result.x, dtype=float), lower, upper)
    prediction = _predict(geometry[None, :], weights, biases)[0]
    response_mse = _response_mse(prediction, target, x_scale, response_spans)
    trust_penalty = trust_weight * float(np.mean(((geometry - baseline) / geometry_span) ** 2))
    return {
        "geometry": geometry,
        "prediction": prediction,
        "response_mse": response_mse,
        "trust_penalty": trust_penalty,
        "optimizer_success": bool(result.success) and np.isfinite(result.fun),
        "optimizer_message": str(result.message),
        "iterations": int(result.nit),
        "function_evaluations": int(result.nfev),
    }


def _response_mse(
    prediction: np.ndarray,
    target: np.ndarray,
    x_scale: np.ndarray,
    response_spans: np.ndarray,
) -> float:
    return float(np.mean(((prediction - target) * x_scale / response_spans) ** 2))


def _predict_inverse(
    values: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    lower: np.ndarray,
    upper: np.ndarray,
) -> np.ndarray:
    raw = _predict(values, weights, biases)
    clipped = np.clip(raw, -40.0, 40.0)
    sigmoid = 1.0 / (1.0 + np.exp(-clipped))
    return lower[None, :] + (upper - lower)[None, :] * sigmoid


def _predict(values: np.ndarray, weights: list[np.ndarray], biases: list[np.ndarray]) -> np.ndarray:
    activation = np.asarray(values, dtype=float)
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        activation = activation @ weight + bias[None, :]
        if index < len(weights) - 1:
            activation = _gelu(activation)
    return activation


def _forward_and_jacobian(
    values: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    activation = np.asarray(values, dtype=float)
    input_count = activation.shape[1]
    jacobian = np.broadcast_to(np.eye(input_count), (len(activation), input_count, input_count)).copy()
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        preactivation = activation @ weight + bias[None, :]
        jacobian = np.einsum("nix,xo->nio", jacobian, weight, optimize=True)
        if index < len(weights) - 1:
            jacobian *= _gelu_derivative(preactivation)[:, None, :]
            activation = _gelu(preactivation)
        else:
            activation = preactivation
    return activation, jacobian


def _gelu(value: np.ndarray) -> np.ndarray:
    return 0.5 * value * (1.0 + np.tanh(math.sqrt(2.0 / math.pi) * (value + 0.044715 * value**3)))


def _gelu_derivative(value: np.ndarray) -> np.ndarray:
    constant = math.sqrt(2.0 / math.pi)
    inner = constant * (value + 0.044715 * value**3)
    tanh_inner = np.tanh(inner)
    return 0.5 * (1.0 + tanh_inner) + 0.5 * value * (1.0 - tanh_inner**2) * constant * (
        1.0 + 3.0 * 0.044715 * value**2
    )


def _checks(
    tandem: dict[str, Any],
    model: dict[str, Any],
    targets: dict[str, Any],
    input_columns: tuple[str, ...],
    geometry_columns: tuple[str, ...],
    analysis: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, bool]:
    rows = analysis.get("candidate_rows") or []
    candidate_ids = [str(row.get("candidate_id") or "") for row in rows]
    geometry_digests = [
        _vector_digest(np.asarray([float(row[column]) for column in geometry_columns], dtype=float))
        for row in rows
        if all(column in row for column in geometry_columns)
    ]
    pair_ids = {arm: {str(row.get("pair_id")) for row in rows if row.get("benchmark_arm") == arm} for arm in ARMS}
    return {
        "tandem_artifact_complete": tandem.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"},
        "source_rows_meet_800k_gate": int(tandem.get("training_count") or 0) >= int(args.min_source_rows),
        "input_contract_is_lp_ls_q_absk": input_columns == EXPECTED_INPUT_COLUMNS,
        "geometry_contract_has_10_independent_variables": len(geometry_columns) == EXPECTED_GEOMETRY_COUNT,
        "weights_model_available": model.get("available") is True,
        "prediction_columns_present": targets.get("columns_present") is True,
        "prediction_targets_meet_minimum": int(targets.get("usable_count") or 0) >= int(args.min_target_rows),
        "analysis_available": analysis.get("available") is True,
        "selected_pair_budget_exact": int(analysis.get("selected_pair_count") or 0) == int(args.candidate_count),
        "equal_arm_budgets": all(
            int((analysis.get("arm_selected_counts") or {}).get(arm) or 0) == int(args.candidate_count) for arm in ARMS
        ),
        "candidate_ids_unique": len(candidate_ids) == 2 * int(args.candidate_count) == len(set(candidate_ids)),
        "pair_targets_match_across_arms": bool(pair_ids[ARMS[0]]) and pair_ids[ARMS[0]] == pair_ids[ARMS[1]],
        "geometry_disjoint_within_and_across_arms": len(geometry_digests) == 2 * int(args.candidate_count) == len(set(geometry_digests)),
        "all_candidates_unlabeled": bool(rows) and {row.get("label_status") for row in rows} == {"AWAITING_REAL_EMX"},
        "all_candidates_require_drc_before_emx": bool(rows)
        and {row.get("drc_status") for row in rows} == {"NOT_EVALUATED_REQUIRED_BEFORE_REAL_EMX"},
        "all_refined_proxy_responses_improve": bool(analysis.get("pair_metrics"))
        and all(
            float(item["refined_response_rmse"]) < float(item["baseline_response_rmse"])
            for item in analysis["pair_metrics"]
        ),
    }


def _plot(path: Path, analysis: dict[str, Any]) -> None:
    pairs = analysis.get("pair_metrics") or []
    baseline = np.asarray([item["baseline_response_rmse"] for item in pairs], dtype=float)
    refined = np.asarray([item["refined_response_rmse"] for item in pairs], dtype=float)
    improvement = 100.0 * (baseline - refined) / np.maximum(baseline, 1.0e-18)
    figure, axes = plt.subplots(1, 2, figsize=(11.0, 4.1), constrained_layout=True)
    axes[0].scatter(baseline, refined, s=22, alpha=0.75, color="#1769aa")
    limit = max(float(np.max(baseline)), float(np.max(refined)), 1.0e-12)
    axes[0].plot([0.0, limit], [0.0, limit], linestyle="--", color="#666666", linewidth=1.1)
    axes[0].set_xlabel("Inverse-only proxy range RMSE")
    axes[0].set_ylabel("Refined proxy range RMSE")
    axes[0].set_title("Paired targets")
    axes[0].grid(alpha=0.22)
    bin_count = 1 if float(np.ptp(improvement)) <= 1.0e-10 else min(24, max(6, int(math.sqrt(len(improvement)))))
    axes[1].hist(improvement, bins=bin_count, color="#cf5c36", edgecolor="white")
    axes[1].axvline(float(np.median(improvement)), color="#222222", linestyle="--", linewidth=1.2)
    axes[1].set_xlabel("Proxy response-MSE improvement (%)")
    axes[1].set_ylabel("Target count")
    axes[1].set_title("Candidate-priority evidence only")
    axes[1].grid(axis="y", alpha=0.22)
    figure.suptitle("Tandem inverse proposal + bounded L-BFGS-B refinement", fontsize=14, fontweight="bold")
    figure.patch.set_facecolor("white")
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)


def _render_report(payload: dict[str, Any]) -> str:
    analysis = payload.get("analysis") or {}
    metrics = analysis.get("proxy_metrics") or {}
    return "\n".join(
        [
            "# Tandem local-refinement candidate plan",
            "",
            f"- Overall status: `{payload['overall_status']}`",
            f"- Outcome: `{payload['outcome_status']}`",
            f"- Pair-matched budget per arm: {payload['arm_budget']}",
            f"- Inverse-only mean proxy range RMSE: {metrics.get('baseline_mean_range_rmse')}",
            f"- Refined mean proxy range RMSE: {metrics.get('refined_mean_range_rmse')}",
            "",
            "The proxy comparison is used only to prioritize candidates. No improvement claim is valid until both arms return real EMX S4P files and pass the paired evaluator.",
            "",
        ]
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _vector_digest(values: np.ndarray) -> str:
    rounded = np.round(np.asarray(values, dtype=np.float64), decimals=12)
    return hashlib.sha256(rounded.tobytes()).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    number = _finite(value)
    return int(number) if number is not None and float(number).is_integer() else None


if __name__ == "__main__":
    raise SystemExit(main())
