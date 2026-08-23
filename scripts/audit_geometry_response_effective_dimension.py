#!/usr/bin/env python3
"""Audit geometry sensitivity and effective dimension from a trained forward surrogate.

The audit qualifies the frozen geometry-to-Lp/Ls/Q/|K| surrogate on its held-out
metric, then evaluates analytic Jacobians and observed-row permutation
importance.  Results are diagnostic: a low-sensitivity geometry variable is not
removed from the production contract without a controlled retraining ablation.
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


DEFAULT_RESPONSE_SPANS = np.asarray([2.5, 2.5, 20.0, 0.8], dtype=float)
EXPECTED_INPUT_COLUMNS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
EXPECTED_GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary_path = Path(args.tandem_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tandem = _read_json(summary_path)
    input_columns = [str(item) for item in tandem.get("input_columns") or []]
    geometry_columns = [str(item) for item in tandem.get("geometry_columns") or []]
    training_csv = _resolve_path(args.training_csv or tandem.get("training_csv"), summary_path.parent)
    weights_path = _resolve_path(args.weights_npz or tandem.get("weights_npz"), summary_path.parent)

    sampled = _reservoir_sample(
        training_csv,
        input_columns,
        geometry_columns,
        int(args.max_sample_rows),
        int(args.seed),
    )
    model = _load_forward_model(weights_path)
    analysis = _analyze(sampled, model, tandem, input_columns, geometry_columns, args)
    checks = _checks(sampled, model, tandem, input_columns, geometry_columns, analysis, args)
    status = "PASS" if all(checks.values()) else "FAIL"

    sensitivity_csv = out_dir / "geometry_response_sensitivity.csv"
    eigen_csv = out_dir / "geometry_effective_dimension_eigenspectrum.csv"
    figure_path = out_dir / "geometry_response_effective_dimension.png"
    summary_out = out_dir / "geometry_response_effective_dimension_summary.json"
    report_path = out_dir / "geometry_response_effective_dimension_report.md"
    _write_sensitivity_csv(sensitivity_csv, analysis, input_columns, geometry_columns)
    _write_eigen_csv(eigen_csv, analysis)
    if analysis.get("available") is True:
        _plot(figure_path, analysis, input_columns, geometry_columns)

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "GEOMETRY_SENSITIVITY_EVIDENCE_READY" if status == "PASS" else "DO_NOT_INTERPRET_GEOMETRY_SENSITIVITY",
        "source_tandem_summary": str(summary_path),
        "source_tandem_summary_sha256": _sha256(summary_path),
        "source_training_csv": str(training_csv),
        "source_weights_npz": str(weights_path),
        "source_weights_npz_sha256": _sha256(weights_path),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "source_row_count": sampled["source_row_count"],
        "usable_row_count": sampled["usable_row_count"],
        "invalid_row_count": sampled["invalid_row_count"],
        "sampled_row_count": sampled["sampled_row_count"],
        "sample_fingerprint_sha256": sampled["sample_fingerprint_sha256"],
        "checks": checks,
        "analysis": analysis,
        "artifacts": {
            "sensitivity_csv": str(sensitivity_csv),
            "eigenspectrum_csv": str(eigen_csv),
            "figure_png": str(figure_path),
            "report_md": str(report_path),
        },
        "literature_basis": [
            {
                "source": "Scientific Reports 2024, Efficient and accurate reduced-dimensional surrogate modeling of electromagnetic structures",
                "url": "https://www.nature.com/articles/s41598-024-72478-w",
                "adaptation": "Use model-derived active directions and sensitivity ranking before changing the geometry dimension.",
            },
            {
                "source": "Deep Learning Assisted End-to-End Synthesis of mm-Wave Passive Networks with 3D EM Structures",
                "adaptation": "Balance heterogeneous physical responses before interpreting a shared geometry representation.",
            },
        ],
        "scientific_boundary": (
            "PASS proves that a held-out-qualified forward surrogate produced finite, repeatable sensitivity and "
            "active-subspace evidence from real-EMX-labeled rows. Importance is model- and distribution-dependent, "
            "not causal. No geometry variable may be removed until a shared-split retraining ablation preserves "
            "Lp/Ls/Q/|K| accuracy and real-EMX closure."
        ),
        "arguments": vars(args),
    }
    summary_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={payload['decision']}")
    print(f"sampled_row_count={sampled['sampled_row_count']}")
    print(f"summary={summary_out}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tandem-summary", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--training-csv")
    parser.add_argument("--weights-npz")
    parser.add_argument("--min-source-rows", type=int, default=200_000)
    parser.add_argument("--min-sample-rows", type=int, default=2_048)
    parser.add_argument("--max-sample-rows", type=int, default=8_192)
    parser.add_argument("--permutation-rows", type=int, default=2_048)
    parser.add_argument("--permutation-repeats", type=int, default=3)
    parser.add_argument("--jacobian-batch-size", type=int, default=512)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--max-forward-test-normalized-rmse", type=float, default=1.0)
    parser.add_argument("--min-permutation-stability", type=float, default=0.50)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.min_sample_rows <= args.max_sample_rows:
        parser.error("sample counts must satisfy 1 <= min-sample-rows <= max-sample-rows")
    if args.min_source_rows < args.min_sample_rows:
        parser.error("--min-source-rows must be at least --min-sample-rows")
    if args.permutation_rows < 1 or args.permutation_repeats < 1 or args.jacobian_batch_size < 1:
        parser.error("permutation and Jacobian controls must be positive")
    if not 0.0 <= args.min_permutation_stability <= 1.0:
        parser.error("--min-permutation-stability must be in [0, 1]")
    return args


def _resolve_path(raw: Any, base: Path) -> Path:
    if not raw:
        return base / "__missing__"
    path = Path(str(raw)).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _reservoir_sample(
    path: Path,
    input_columns: list[str],
    geometry_columns: list[str],
    maximum: int,
    seed: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source_row_count": 0,
        "usable_row_count": 0,
        "invalid_row_count": 0,
        "sampled_row_count": 0,
        "sample_fingerprint_sha256": "",
        "x": np.empty((0, len(input_columns))),
        "geometry": np.empty((0, len(geometry_columns))),
        "source_indices": np.empty(0, dtype=np.int64),
        "columns_present": False,
    }
    if not path.is_file() or not input_columns or not geometry_columns:
        return result
    rng = np.random.default_rng(seed)
    x_rows: list[np.ndarray] = []
    geometry_rows: list[np.ndarray] = []
    source_indices: list[int] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = set(reader.fieldnames or [])
        required = set(input_columns) | set(geometry_columns)
        result["columns_present"] = required.issubset(header)
        if not result["columns_present"]:
            return result
        for source_index, row in enumerate(reader):
            result["source_row_count"] += 1
            try:
                x = np.asarray([float(row[column]) for column in input_columns], dtype=float)
                geometry = np.asarray([float(row[column]) for column in geometry_columns], dtype=float)
            except (KeyError, TypeError, ValueError):
                result["invalid_row_count"] += 1
                continue
            if not np.isfinite(x).all() or not np.isfinite(geometry).all():
                result["invalid_row_count"] += 1
                continue
            usable_index = int(result["usable_row_count"])
            result["usable_row_count"] = usable_index + 1
            if len(x_rows) < maximum:
                x_rows.append(x)
                geometry_rows.append(geometry)
                source_indices.append(source_index)
                continue
            replacement = int(rng.integers(0, usable_index + 1))
            if replacement < maximum:
                x_rows[replacement] = x
                geometry_rows[replacement] = geometry
                source_indices[replacement] = source_index
    if x_rows:
        order = np.argsort(np.asarray(source_indices, dtype=np.int64), kind="stable")
        result["x"] = np.vstack(x_rows)[order]
        result["geometry"] = np.vstack(geometry_rows)[order]
        result["source_indices"] = np.asarray(source_indices, dtype=np.int64)[order]
    result["sampled_row_count"] = len(x_rows)
    digest = hashlib.sha256()
    digest.update("\0".join(input_columns + geometry_columns).encode("utf-8"))
    digest.update(np.asarray(result["source_indices"], dtype=np.int64).tobytes())
    result["sample_fingerprint_sha256"] = digest.hexdigest() if x_rows else ""
    return result


def _load_forward_model(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"available": False, "path": str(path), "error": ""}
    if not path.is_file():
        result["error"] = "weights NPZ is missing"
        return result
    try:
        with np.load(path, allow_pickle=False) as archive:
            weights = _numbered_arrays(archive, "forward_weight_")
            biases = _numbered_arrays(archive, "forward_bias_")
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
            if "normalization__response_loss_physical_spans" in archive:
                normalization["response_loss_physical_spans"] = np.asarray(
                    archive["normalization__response_loss_physical_spans"], dtype=float
                )
        if not weights or len(weights) != len(biases):
            raise ValueError("forward weights and biases are incomplete")
        for index, (weight, bias) in enumerate(zip(weights, biases)):
            if weight.ndim != 2 or bias.shape != (weight.shape[1],):
                raise ValueError(f"invalid forward layer {index} shapes")
            if index and weights[index - 1].shape[1] != weight.shape[0]:
                raise ValueError(f"forward layer {index} input width mismatch")
            if not np.isfinite(weight).all() or not np.isfinite(bias).all():
                raise ValueError("non-finite forward model values")
        result.update({"available": True, "weights": weights, "biases": biases, "normalization": normalization})
    except Exception as exc:  # noqa: BLE001 - exact evidence error belongs in the audit artifact.
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _numbered_arrays(archive: Any, prefix: str) -> list[np.ndarray]:
    values = []
    index = 0
    while f"{prefix}{index}" in archive:
        values.append(np.asarray(archive[f"{prefix}{index}"], dtype=float))
        index += 1
    return values


def _analyze(
    sampled: dict[str, Any],
    model: dict[str, Any],
    tandem: dict[str, Any],
    input_columns: list[str],
    geometry_columns: list[str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    unavailable = {"available": False, "error": model.get("error") or "insufficient inputs"}
    if model.get("available") is not True or sampled["sampled_row_count"] < 1:
        return unavailable
    normalization = model.get("normalization") or {}
    response_count = len(input_columns)
    geometry_count = len(geometry_columns)
    required_shapes = {
        "x_mean": (response_count,),
        "x_scale": (response_count,),
        "y_mean": (geometry_count,),
        "y_scale": (geometry_count,),
        "geometry_lower": (geometry_count,),
        "geometry_upper": (geometry_count,),
    }
    if any(np.asarray(normalization.get(key, [])).shape != shape for key, shape in required_shapes.items()):
        return {"available": False, "error": "normalization array shape mismatch"}
    weights = model["weights"]
    biases = model["biases"]
    if weights[0].shape[0] != geometry_count or weights[-1].shape[1] != response_count:
        return {"available": False, "error": "forward model input/output dimension mismatch"}
    x_mean = normalization["x_mean"]
    x_scale = normalization["x_scale"]
    y_mean = normalization["y_mean"]
    y_scale = normalization["y_scale"]
    geometry_lower = normalization["geometry_lower"]
    geometry_upper = normalization["geometry_upper"]
    if np.any(x_scale <= 0.0) or np.any(y_scale <= 0.0):
        return {"available": False, "error": "normalization scales must be positive"}
    response_spans = np.asarray(normalization.get("response_loss_physical_spans", DEFAULT_RESPONSE_SPANS), dtype=float)
    if response_spans.shape != (response_count,) or np.any(~np.isfinite(response_spans)) or np.any(response_spans <= 0.0):
        return {"available": False, "error": "physical response spans are unavailable or invalid"}
    geometry_span_normalized = geometry_upper - geometry_lower
    if np.any(~np.isfinite(geometry_span_normalized)) or np.any(geometry_span_normalized <= 0.0):
        return {"available": False, "error": "observed geometry spans are unavailable or invalid"}

    truth_normalized = (sampled["x"] - x_mean[None, :]) / x_scale[None, :]
    geometry_normalized = (sampled["geometry"] - y_mean[None, :]) / y_scale[None, :]
    prediction_normalized = _predict_batched(geometry_normalized, weights, biases, int(args.jacobian_batch_size))
    baseline_mse = np.mean((prediction_normalized - truth_normalized) ** 2, axis=0)
    sample_r2 = _r2(truth_normalized, prediction_normalized)
    sample_rmse = np.sqrt(baseline_mse)

    jacobian_abs_sum = np.zeros((geometry_count, response_count), dtype=float)
    active_covariance = np.zeros((geometry_count, geometry_count), dtype=float)
    jacobian_rows = 0
    response_scale = x_scale / response_spans
    for start in range(0, len(geometry_normalized), int(args.jacobian_batch_size)):
        batch = geometry_normalized[start : start + int(args.jacobian_batch_size)]
        _, jacobian = _forward_and_jacobian(batch, weights, biases)
        jacobian = (
            jacobian
            * geometry_span_normalized[None, :, None]
            * response_scale[None, None, :]
        )
        jacobian_abs_sum += np.sum(np.abs(jacobian), axis=0)
        active_covariance += np.einsum("nir,njr->ij", jacobian, jacobian, optimize=True)
        jacobian_rows += len(batch)
    jacobian_mean_abs = jacobian_abs_sum / max(1, jacobian_rows)
    jacobian_normalized = _normalize_columns(jacobian_mean_abs)
    active_covariance /= max(1, jacobian_rows * response_count)
    eigenvalues, eigenvectors = np.linalg.eigh(active_covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0.0)
    eigenvectors = eigenvectors[:, order]
    eigen_fraction = eigenvalues / np.sum(eigenvalues) if np.sum(eigenvalues) > 0.0 else np.zeros_like(eigenvalues)
    cumulative = np.cumsum(eigen_fraction)

    permutation = _permutation_importance(
        geometry_normalized,
        truth_normalized,
        weights,
        biases,
        baseline_mse,
        geometry_count,
        response_count,
        args,
    )
    permutation_normalized = permutation["mean_normalized_by_response"]
    combined_by_response = _normalize_columns(0.5 * (jacobian_normalized + permutation_normalized))
    aggregate = np.mean(combined_by_response, axis=1)
    aggregate = aggregate / np.sum(aggregate) if np.sum(aggregate) > 0.0 else aggregate
    ranking = np.argsort(aggregate)[::-1]
    participation = float(np.sum(eigenvalues) ** 2 / np.sum(eigenvalues**2)) if np.sum(eigenvalues**2) > 0.0 else None
    forward_metrics = ((tandem.get("metrics") or {}).get("forward_proxy") or {})
    return {
        "available": True,
        "held_out_forward_test_normalized_rmse": _finite_or_none(forward_metrics.get("test_normalized_rmse")),
        "held_out_forward_test_normalized_r2": _finite_or_none(forward_metrics.get("test_normalized_r2")),
        "observed_sample_normalized_rmse_by_response": {
            column: float(sample_rmse[index]) for index, column in enumerate(input_columns)
        },
        "observed_sample_normalized_r2_by_response": {
            column: float(sample_r2[index]) for index, column in enumerate(input_columns)
        },
        "response_physical_spans": {
            column: float(response_spans[index]) for index, column in enumerate(input_columns)
        },
        "geometry_observed_normalized_spans": {
            column: float(geometry_span_normalized[index]) for index, column in enumerate(geometry_columns)
        },
        "jacobian_mean_absolute_dimensionless": jacobian_mean_abs.tolist(),
        "jacobian_normalized_by_response": jacobian_normalized.tolist(),
        "permutation_normalized_by_response": permutation_normalized.tolist(),
        "permutation_raw_mse_increase": permutation["mean_raw_mse_increase"].tolist(),
        "permutation_repeat_stability_min_cosine": permutation["min_repeat_cosine"],
        "combined_normalized_by_response": combined_by_response.tolist(),
        "aggregate_geometry_importance": {
            geometry_columns[index]: float(aggregate[index]) for index in range(geometry_count)
        },
        "ranked_geometry_variables": [geometry_columns[int(index)] for index in ranking],
        "active_subspace": {
            "eigenvalues": eigenvalues.tolist(),
            "eigenvalue_fractions": eigen_fraction.tolist(),
            "cumulative_fractions": cumulative.tolist(),
            "participation_ratio_effective_dimension": participation,
            "dimension_for_90_percent_energy": _dimension_for_fraction(cumulative, 0.90),
            "dimension_for_95_percent_energy": _dimension_for_fraction(cumulative, 0.95),
            "active_direction_loadings": eigenvectors.tolist(),
        },
        "interpretation": (
            "The combined ranking averages response-normalized analytic-Jacobian and permutation evidence. "
            "The active-subspace spectrum estimates effective directions, not a license to delete variables."
        ),
    }


def _forward_and_jacobian(
    values: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    activation = np.asarray(values, dtype=float)
    input_count = activation.shape[1]
    jacobian = np.broadcast_to(np.eye(input_count, dtype=float), (len(activation), input_count, input_count)).copy()
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        preactivation = activation @ weight + bias[None, :]
        jacobian = np.einsum("nix,xo->nio", jacobian, weight, optimize=True)
        if index < len(weights) - 1:
            jacobian *= _gelu_derivative(preactivation)[:, None, :]
            activation = _gelu(preactivation)
        else:
            activation = preactivation
    return activation, jacobian


def _predict(values: np.ndarray, weights: list[np.ndarray], biases: list[np.ndarray]) -> np.ndarray:
    activation = np.asarray(values, dtype=float)
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        activation = activation @ weight + bias[None, :]
        if index < len(weights) - 1:
            activation = _gelu(activation)
    return activation


def _predict_batched(
    values: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    batch_size: int,
) -> np.ndarray:
    parts = [_predict(values[start : start + batch_size], weights, biases) for start in range(0, len(values), batch_size)]
    return np.vstack(parts) if parts else np.empty((0, weights[-1].shape[1]))


def _gelu(value: np.ndarray) -> np.ndarray:
    return 0.5 * value * (1.0 + np.tanh(math.sqrt(2.0 / math.pi) * (value + 0.044715 * value**3)))


def _gelu_derivative(value: np.ndarray) -> np.ndarray:
    constant = math.sqrt(2.0 / math.pi)
    inner = constant * (value + 0.044715 * value**3)
    tanh_inner = np.tanh(inner)
    return 0.5 * (1.0 + tanh_inner) + 0.5 * value * (1.0 - tanh_inner**2) * constant * (1.0 + 3.0 * 0.044715 * value**2)


def _permutation_importance(
    geometry: np.ndarray,
    truth: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    baseline_mse: np.ndarray,
    geometry_count: int,
    response_count: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    row_count = min(len(geometry), int(args.permutation_rows))
    if row_count < len(geometry):
        indices = np.linspace(0, len(geometry) - 1, row_count, dtype=int)
        base_geometry = geometry[indices]
        base_truth = truth[indices]
        base_prediction = _predict_batched(base_geometry, weights, biases, int(args.jacobian_batch_size))
        reference_mse = np.mean((base_prediction - base_truth) ** 2, axis=0)
    else:
        base_geometry = geometry
        base_truth = truth
        reference_mse = baseline_mse
    repeats = []
    normalized_repeats = []
    for repeat in range(int(args.permutation_repeats)):
        rng = np.random.default_rng(int(args.seed) + 1009 * (repeat + 1))
        raw = np.zeros((geometry_count, response_count), dtype=float)
        for feature_index in range(geometry_count):
            permuted = base_geometry.copy()
            permuted[:, feature_index] = permuted[rng.permutation(len(permuted)), feature_index]
            prediction = _predict_batched(permuted, weights, biases, int(args.jacobian_batch_size))
            mse = np.mean((prediction - base_truth) ** 2, axis=0)
            raw[feature_index] = np.maximum(mse - reference_mse, 0.0)
        repeats.append(raw)
        normalized_repeats.append(_normalize_columns(raw))
    raw_stack = np.stack(repeats, axis=0)
    normalized_stack = np.stack(normalized_repeats, axis=0)
    aggregate_vectors = np.mean(normalized_stack, axis=2)
    cosines = []
    for left in range(len(aggregate_vectors)):
        for right in range(left + 1, len(aggregate_vectors)):
            cosines.append(_cosine(aggregate_vectors[left], aggregate_vectors[right]))
    return {
        "mean_raw_mse_increase": np.mean(raw_stack, axis=0),
        "mean_normalized_by_response": np.mean(normalized_stack, axis=0),
        "min_repeat_cosine": float(min(cosines)) if cosines else 1.0,
    }


def _checks(
    sampled: dict[str, Any],
    model: dict[str, Any],
    tandem: dict[str, Any],
    input_columns: list[str],
    geometry_columns: list[str],
    analysis: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, bool]:
    split = tandem.get("split_audit") or {}
    held_out_rmse = _finite_or_none(analysis.get("held_out_forward_test_normalized_rmse"))
    active = analysis.get("active_subspace") or {}
    eigenvalues = np.asarray(active.get("eigenvalues") or [], dtype=float)
    aggregate = np.asarray(list((analysis.get("aggregate_geometry_importance") or {}).values()), dtype=float)
    return {
        "tandem_summary_present": bool(tandem),
        "tandem_status_reviewable": tandem.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"},
        "tandem_training_count_meets_minimum": int(tandem.get("training_count") or 0) >= int(args.min_source_rows),
        "declared_four_physical_inputs": tuple(input_columns) == EXPECTED_INPUT_COLUMNS,
        "declared_ten_independent_geometry_outputs": tuple(geometry_columns) == EXPECTED_GEOMETRY_COLUMNS,
        "source_columns_present": sampled.get("columns_present") is True,
        "source_rows_meet_minimum": int(sampled.get("usable_row_count") or 0) >= int(args.min_source_rows),
        "sample_rows_meet_minimum": int(sampled.get("sampled_row_count") or 0) >= int(args.min_sample_rows),
        "all_source_rows_finite": int(sampled.get("invalid_row_count") or 0) == 0,
        "forward_model_available": model.get("available") is True,
        "held_out_physical_cell_split": split.get("split_mode") == "physical_cell_grouped"
        and int(split.get("physical_cell_overlap_count") or 0) == 0,
        "held_out_forward_rmse_qualified": held_out_rmse is not None
        and held_out_rmse <= float(args.max_forward_test_normalized_rmse),
        "held_out_forward_r2_finite": _finite_or_none(analysis.get("held_out_forward_test_normalized_r2")) is not None,
        "analysis_available": analysis.get("available") is True,
        "permutation_repeats_stable": _finite_or_none(analysis.get("permutation_repeat_stability_min_cosine")) is not None
        and float(analysis.get("permutation_repeat_stability_min_cosine")) >= float(args.min_permutation_stability),
        "finite_nonzero_active_subspace": eigenvalues.shape == (len(geometry_columns),)
        and np.isfinite(eigenvalues).all()
        and float(np.sum(eigenvalues)) > 0.0,
        "finite_normalized_geometry_importance": aggregate.shape == (len(geometry_columns),)
        and np.isfinite(aggregate).all()
        and math.isclose(float(np.sum(aggregate)), 1.0, rel_tol=1.0e-6, abs_tol=1.0e-6),
    }


def _normalize_columns(values: np.ndarray) -> np.ndarray:
    values = np.maximum(np.asarray(values, dtype=float), 0.0)
    totals = np.sum(values, axis=0, keepdims=True)
    return np.divide(values, totals, out=np.zeros_like(values), where=totals > 0.0)


def _r2(truth: np.ndarray, prediction: np.ndarray) -> np.ndarray:
    residual = np.sum((truth - prediction) ** 2, axis=0)
    total = np.sum((truth - np.mean(truth, axis=0, keepdims=True)) ** 2, axis=0)
    return np.divide(total - residual, total, out=np.full_like(total, np.nan), where=total > 0.0)


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator > 0.0 else 0.0


def _dimension_for_fraction(cumulative: np.ndarray, fraction: float) -> int | None:
    indices = np.flatnonzero(cumulative >= fraction)
    return int(indices[0] + 1) if indices.size else None


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_sensitivity_csv(
    path: Path,
    analysis: dict[str, Any],
    input_columns: list[str],
    geometry_columns: list[str],
) -> None:
    if analysis.get("available") is not True:
        path.write_text("", encoding="utf-8")
        return
    jacobian = np.asarray(analysis["jacobian_normalized_by_response"], dtype=float)
    permutation = np.asarray(analysis["permutation_normalized_by_response"], dtype=float)
    combined = np.asarray(analysis["combined_normalized_by_response"], dtype=float)
    aggregate = analysis["aggregate_geometry_importance"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("geometry", "response", "jacobian_importance", "permutation_importance", "combined_importance", "aggregate_importance"),
        )
        writer.writeheader()
        for geometry_index, geometry in enumerate(geometry_columns):
            for response_index, response in enumerate(input_columns):
                writer.writerow(
                    {
                        "geometry": geometry,
                        "response": response,
                        "jacobian_importance": float(jacobian[geometry_index, response_index]),
                        "permutation_importance": float(permutation[geometry_index, response_index]),
                        "combined_importance": float(combined[geometry_index, response_index]),
                        "aggregate_importance": float(aggregate[geometry]),
                    }
                )


def _write_eigen_csv(path: Path, analysis: dict[str, Any]) -> None:
    if analysis.get("available") is not True:
        path.write_text("", encoding="utf-8")
        return
    active = analysis["active_subspace"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("direction", "eigenvalue", "fraction", "cumulative_fraction"))
        writer.writeheader()
        for index, (value, fraction, cumulative) in enumerate(
            zip(active["eigenvalues"], active["eigenvalue_fractions"], active["cumulative_fractions"]), start=1
        ):
            writer.writerow({"direction": index, "eigenvalue": value, "fraction": fraction, "cumulative_fraction": cumulative})


def _plot(path: Path, analysis: dict[str, Any], input_columns: list[str], geometry_columns: list[str]) -> None:
    combined = np.asarray(analysis["combined_normalized_by_response"], dtype=float)
    jacobian = np.asarray(analysis["jacobian_normalized_by_response"], dtype=float)
    permutation = np.asarray(analysis["permutation_normalized_by_response"], dtype=float)
    aggregate = np.asarray([analysis["aggregate_geometry_importance"][name] for name in geometry_columns], dtype=float)
    active = analysis["active_subspace"]
    fractions = np.asarray(active["eigenvalue_fractions"], dtype=float)
    cumulative = np.asarray(active["cumulative_fractions"], dtype=float)
    loadings = np.asarray(active["active_direction_loadings"], dtype=float)
    labels = [name.removeprefix("geom__").replace("_um", "") for name in geometry_columns]
    responses = [name.removeprefix("input__").replace("_center", "") for name in input_columns]

    fig, axes = plt.subplots(2, 2, figsize=(16, 10), constrained_layout=True)
    image = axes[0, 0].imshow(combined, aspect="auto", cmap="viridis", vmin=0.0)
    axes[0, 0].set_title("Response-normalized combined sensitivity")
    axes[0, 0].set_xticks(range(len(responses)), responses, rotation=20, ha="right")
    axes[0, 0].set_yticks(range(len(labels)), labels)
    fig.colorbar(image, ax=axes[0, 0], shrink=0.8)

    order = np.argsort(aggregate)
    axes[0, 1].barh(np.arange(len(order)) - 0.20, np.mean(jacobian, axis=1)[order], height=0.20, label="Jacobian")
    axes[0, 1].barh(np.arange(len(order)), np.mean(permutation, axis=1)[order], height=0.20, label="Permutation")
    axes[0, 1].barh(np.arange(len(order)) + 0.20, aggregate[order], height=0.20, label="Combined")
    axes[0, 1].set_yticks(range(len(order)), [labels[index] for index in order])
    axes[0, 1].set_title("Aggregate geometry importance")
    axes[0, 1].legend()

    directions = np.arange(1, len(fractions) + 1)
    axes[1, 0].bar(directions, fractions, color="#2864b7", label="Energy fraction")
    twin = axes[1, 0].twinx()
    twin.plot(directions, cumulative, color="#c33d32", marker="o", label="Cumulative")
    twin.axhline(0.90, color="#666666", linestyle="--", linewidth=1)
    twin.set_ylim(0.0, 1.05)
    axes[1, 0].set_title("Active-subspace eigenspectrum")
    axes[1, 0].set_xlabel("Active direction")
    axes[1, 0].set_ylabel("Energy fraction")
    twin.set_ylabel("Cumulative fraction")

    width = 0.38
    positions = np.arange(len(labels))
    axes[1, 1].bar(positions - width / 2, np.abs(loadings[:, 0]), width=width, label="Direction 1")
    if loadings.shape[1] > 1:
        axes[1, 1].bar(positions + width / 2, np.abs(loadings[:, 1]), width=width, label="Direction 2")
    axes[1, 1].set_xticks(positions, labels, rotation=35, ha="right")
    axes[1, 1].set_title("Leading active-direction loadings")
    axes[1, 1].legend()

    fig.suptitle("Geometry sensitivity and effective dimension from the frozen forward surrogate", fontsize=15)
    fig.savefig(path, dpi=200)
    plt.close(fig)


def _render_report(data: dict[str, Any]) -> str:
    analysis = data.get("analysis") or {}
    active = analysis.get("active_subspace") or {}
    ranked = analysis.get("ranked_geometry_variables") or []
    lines = [
        "# Geometry-response sensitivity and effective-dimension audit",
        "",
        f"- Overall status: **{data['overall_status']}**",
        f"- Decision: **{data['decision']}**",
        f"- Source usable rows: `{data['usable_row_count']}`",
        f"- Deterministic sampled rows: `{data['sampled_row_count']}`",
        f"- Held-out forward normalized RMSE: `{analysis.get('held_out_forward_test_normalized_rmse')}`",
        f"- Permutation repeat minimum cosine: `{analysis.get('permutation_repeat_stability_min_cosine')}`",
        f"- 90% active dimension: `{active.get('dimension_for_90_percent_energy')}`",
        f"- Participation-ratio dimension: `{active.get('participation_ratio_effective_dimension')}`",
        "",
        "## Ranked geometry variables",
        "",
    ]
    lines.extend(f"{index}. `{name}`" for index, name in enumerate(ranked, start=1))
    lines.extend(["", data["scientific_boundary"], ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
