#!/usr/bin/env python3
"""Evaluate two hash-bound, architecture-matched tandem models on legacy8k.

The evaluator is intentionally fail-closed.  It reads the existing frozen
10,000-target frame, selects only the exactly 8,000 rows with ``K_abs <= 0.8``,
and performs one-shot inference with each model's own frozen forward proxy.
It never creates targets, refines candidates, runs EMX, or uses evaluation
targets for checkpoint or threshold selection.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

import numpy as np


INPUT_COLUMNS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
GEOMETRY_COLUMNS = (
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
TARGET_KEYS = ("Lp_nH", "Ls_nH", "Q_min", "K_abs")
FEATURE_LABELS = ("Lp", "Ls", "Qmin", "K_abs")
NORMALIZATION_SPANS = np.asarray((2.5, 2.5, 20.0, 0.8), dtype=float)
EXPECTED_TARGET_FRAME_ROWS = 10_000
EXPECTED_LEGACY_ROWS = 8_000
EXPECTED_INVERSE_ARCHITECTURE = (4, 512, 512, 256, 10)
EXPECTED_FORWARD_ARCHITECTURE = (10, 256, 256, 128, 4)
EXPECTED_PARAMETER_COUNT = 501_134
EXPECTED_PROJECTION_MODE = "hard_feasible_topology_v1"
EXPECTED_REFERENCE_ROLE = (
    "deployed_and_presented_seed20260713_not_final_global_winner"
)
EXPECTED_REFERENCE_SELECTION_STATUS = "MISMATCH_FINAL_GLOBAL_WINNER"
FROZEN_FIXED10K_SHA256 = (
    "c9d7d8bc7f65a488be0805969389a01ef049534eefdfdea71cbd640ee27d6407"
)
POPULATION_SPECIFIC_ARGUMENT_KEYS = frozenset(
    {
        "training_csv",
        "out_dir",
        "min_training_rows",
    }
)
GENERATED_FILENAMES = (
    "per_target_100k_predictions.csv",
    "per_target_200k_predictions.csv",
    "architecture_matched_comparison.csv",
    "evaluation_summary.json",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    for role in ("100k", "200k"):
        parser.add_argument(f"--model-{role}-id", required=True)
        parser.add_argument(f"--model-{role}-summary", required=True)
        parser.add_argument(f"--model-{role}-weights", required=True)
        parser.add_argument(f"--model-{role}-trainer-source", required=True)
        parser.add_argument(
            f"--expected-model-{role}-summary-sha256", required=True
        )
        parser.add_argument(
            f"--expected-model-{role}-weights-sha256", required=True
        )
        parser.add_argument(
            f"--expected-model-{role}-trainer-sha256", required=True
        )
    parser.add_argument("--targets-json", required=True)
    parser.add_argument("--expected-targets-sha256", required=True)
    parser.add_argument("--reference-contract", required=True)
    parser.add_argument("--expected-reference-contract-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("ascii")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _validate_reference_contract(
    payload: dict[str, Any], model_ids: dict[str, str]
) -> dict[str, Any]:
    eligibility = payload.get("comparison_eligibility")
    if not isinstance(eligibility, dict):
        raise ValueError("reference contract lacks comparison_eligibility")
    required_keys = {
        "reference_model_id",
        "candidate_model_id",
        "reference_role",
        "reference_selection_status",
        "advisor_comparison_eligible",
        "engineering_evaluation_allowed",
    }
    if set(eligibility) != required_keys:
        raise ValueError("reference contract comparison_eligibility schema is not exact")
    for key in (
        "reference_model_id",
        "candidate_model_id",
        "reference_role",
        "reference_selection_status",
    ):
        if type(eligibility[key]) is not str:
            raise ValueError(f"reference contract {key} must be an exact string")
    for key in ("advisor_comparison_eligible", "engineering_evaluation_allowed"):
        if type(eligibility[key]) is not bool:
            raise ValueError(f"reference contract {key} must be an exact boolean")
    expected = {
        "reference_model_id": model_ids["100k"],
        "candidate_model_id": model_ids["200k"],
        "reference_role": EXPECTED_REFERENCE_ROLE,
        "reference_selection_status": EXPECTED_REFERENCE_SELECTION_STATUS,
        "advisor_comparison_eligible": False,
        "engineering_evaluation_allowed": True,
    }
    if eligibility != expected:
        raise ValueError(
            "reference contract identity or comparison-eligibility status does not match"
        )
    return dict(eligibility)


def _require_sha(path: Path, expected: str, label: str) -> str:
    normalized = expected.strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{label} expected SHA-256 is malformed")
    actual = _sha256(path)
    if actual != normalized:
        raise ValueError(f"{label} SHA-256 mismatch: {actual} != {normalized}")
    return actual


def _source_record(path: Path, sha256: str) -> dict[str, str]:
    """Return a public-safe record without embedding a private directory."""
    return {"file_name": path.name, "sha256": sha256}


def _load_trainer(path: Path, sha256: str, role: str) -> ModuleType:
    module_name = f"_architecture_matched_{role}_trainer_{sha256[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {role} trainer source")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    for symbol in ("_predict", "_predict_inverse"):
        if not callable(getattr(module, symbol, None)):
            raise AttributeError(f"{role} trainer does not expose callable {symbol}")
    return module


def _numbered_arrays(archive: Any, prefix: str) -> list[np.ndarray]:
    pairs: list[tuple[int, str]] = []
    for key in archive.files:
        if not key.startswith(prefix):
            continue
        suffix = key.removeprefix(prefix)
        if not suffix.isdigit():
            raise ValueError(f"malformed numbered weights key: {key}")
        pairs.append((int(suffix), key))
    pairs.sort()
    if [index for index, _ in pairs] != list(range(len(pairs))) or not pairs:
        raise ValueError(f"{prefix} arrays are missing or non-contiguous")
    return [np.asarray(archive[key], dtype=float) for _, key in pairs]


def _archive_scalar_string(archive: Any, key: str) -> str:
    if key not in archive.files:
        raise KeyError(f"weights archive is missing {key}")
    value = np.asarray(archive[key]).reshape(-1)
    if value.size != 1:
        raise ValueError(f"weights field {key} must contain one string")
    return str(value[0])


def _layer_widths(weights: list[np.ndarray]) -> tuple[int, ...]:
    if not weights or any(weight.ndim != 2 for weight in weights):
        raise ValueError("model weights must be a nonempty list of matrices")
    widths = [int(weights[0].shape[0])]
    for index, weight in enumerate(weights):
        if int(weight.shape[0]) != widths[-1]:
            raise ValueError(f"weight chain is disconnected at layer {index}")
        widths.append(int(weight.shape[1]))
    return tuple(widths)


def _parameter_count(
    weights: list[np.ndarray], biases: list[np.ndarray]
) -> int:
    if len(weights) != len(biases):
        raise ValueError("weight and bias layer counts differ")
    count = 0
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        if bias.ndim != 1 or bias.shape != (weight.shape[1],):
            raise ValueError(f"bias shape mismatch at layer {index}")
        count += int(weight.size + bias.size)
    return count


def _array_digest(value: np.ndarray) -> str:
    array = np.asarray(value, dtype="<f8", order="C")
    digest = hashlib.sha256()
    digest.update(str(array.shape).encode("ascii"))
    digest.update(b"\0")
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def _normalization_digest(model: dict[str, Any]) -> str:
    digest = hashlib.sha256(b"architecture_matched_normalization_v1\0")
    for key in (
        "x_mean",
        "x_scale",
        "y_mean",
        "y_scale",
        "geometry_lower",
        "geometry_upper",
        "dimension_weights",
    ):
        digest.update(key.encode("ascii"))
        digest.update(b"\0")
        digest.update(_array_digest(model[key]).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _load_model(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as archive:
        required = (
            "normalization__x_mean",
            "normalization__x_scale",
            "normalization__y_mean",
            "normalization__y_scale",
            "normalization__geometry_lower",
            "normalization__geometry_upper",
            "normalization__response_loss_dimension_weights",
        )
        missing = [key for key in required if key not in archive.files]
        if missing:
            raise KeyError(f"weights archive lacks normalization arrays: {missing}")
        topology_json = _archive_scalar_string(
            archive, "inverse_geometry_projection__topology_contract_json"
        )
        model = {
            "forward_weights": _numbered_arrays(archive, "forward_weight_"),
            "forward_biases": _numbered_arrays(archive, "forward_bias_"),
            "inverse_weights": _numbered_arrays(archive, "inverse_weight_"),
            "inverse_biases": _numbered_arrays(archive, "inverse_bias_"),
            "x_mean": np.asarray(archive["normalization__x_mean"], dtype=float),
            "x_scale": np.asarray(archive["normalization__x_scale"], dtype=float),
            "y_mean": np.asarray(archive["normalization__y_mean"], dtype=float),
            "y_scale": np.asarray(archive["normalization__y_scale"], dtype=float),
            "geometry_lower": np.asarray(
                archive["normalization__geometry_lower"], dtype=float
            ),
            "geometry_upper": np.asarray(
                archive["normalization__geometry_upper"], dtype=float
            ),
            "dimension_weights": np.asarray(
                archive["normalization__response_loss_dimension_weights"],
                dtype=float,
            ),
            "projection_mode": _archive_scalar_string(
                archive, "inverse_geometry_projection__mode"
            ),
            "topology_contract": json.loads(topology_json),
        }

    forward_architecture = _layer_widths(model["forward_weights"])
    inverse_architecture = _layer_widths(model["inverse_weights"])
    forward_parameters = _parameter_count(
        model["forward_weights"], model["forward_biases"]
    )
    inverse_parameters = _parameter_count(
        model["inverse_weights"], model["inverse_biases"]
    )
    if forward_architecture != EXPECTED_FORWARD_ARCHITECTURE:
        raise ValueError(f"unexpected forward architecture: {forward_architecture}")
    if inverse_architecture != EXPECTED_INVERSE_ARCHITECTURE:
        raise ValueError(f"unexpected inverse architecture: {inverse_architecture}")
    if forward_parameters + inverse_parameters != EXPECTED_PARAMETER_COUNT:
        raise ValueError("architecture parameter count does not match the reference contract")
    if model["projection_mode"] != EXPECTED_PROJECTION_MODE:
        raise ValueError("model does not use the hard-feasible topology projection")
    if not isinstance(model["topology_contract"], dict):
        raise ValueError("topology contract is not a JSON object")
    power_contract = model["topology_contract"].get(
        "power_line_port_ground_overlap"
    ) or {}
    if not model["topology_contract"].get("available") or not power_contract.get(
        "enabled"
    ):
        raise ValueError("hard-feasible topology contract is unavailable or disabled")

    expected_vector_shapes = {
        "x_mean": (4,),
        "x_scale": (4,),
        "y_mean": (10,),
        "y_scale": (10,),
        "geometry_lower": (10,),
        "geometry_upper": (10,),
        "dimension_weights": (4,),
    }
    for key, shape in expected_vector_shapes.items():
        value = model[key]
        if value.shape != shape or not np.all(np.isfinite(value)):
            raise ValueError(f"model {key} is non-finite or has shape {value.shape}")
    for family in (
        "forward_weights",
        "forward_biases",
        "inverse_weights",
        "inverse_biases",
    ):
        if not all(np.all(np.isfinite(value)) for value in model[family]):
            raise ValueError(f"model {family} contains non-finite values")
    if np.any(model["x_scale"] <= 0.0) or np.any(model["y_scale"] <= 0.0):
        raise ValueError("normalization scales must be positive")
    if np.any(model["geometry_upper"] <= model["geometry_lower"]):
        raise ValueError("geometry envelope must have positive width")

    model["forward_architecture"] = forward_architecture
    model["inverse_architecture"] = inverse_architecture
    model["forward_parameter_count"] = forward_parameters
    model["inverse_parameter_count"] = inverse_parameters
    model["parameter_count"] = forward_parameters + inverse_parameters
    model["normalization_sha256"] = _normalization_digest(model)
    return model


def _parse_hidden_widths(value: Any) -> tuple[int, ...]:
    if isinstance(value, str):
        return tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if isinstance(value, list):
        return tuple(int(item) for item in value)
    raise ValueError("hidden-width declaration is absent or malformed")


def _verify_contract_fingerprint(contract: dict[str, Any], role: str) -> str:
    if not isinstance(contract, dict):
        raise ValueError(f"{role} model comparison contract is absent")
    payload = dict(contract)
    recorded = str(payload.pop("fingerprint_sha256", ""))
    computed = _canonical_sha256(payload)
    if recorded != computed:
        raise ValueError(f"{role} model comparison contract fingerprint is invalid")
    return recorded


def _validate_summary(
    summary: dict[str, Any],
    model: dict[str, Any],
    *,
    role: str,
    model_id: str,
    expected_training_count: int,
    weights_sha256: str,
    trainer_sha256: str,
) -> dict[str, Any]:
    arguments = summary.get("arguments") or {}
    method = summary.get("method") or {}
    contract = summary.get("model_comparison_contract") or {}
    contract_fingerprint = _verify_contract_fingerprint(contract, role)
    if summary.get("execution_status") != "PASS":
        raise ValueError(f"{role} summary is not a completed PASS execution")
    if int(summary.get("training_count") or 0) != expected_training_count:
        raise ValueError(f"{role} source-table row count is not {expected_training_count}")
    if str(summary.get("weights_npz_sha256") or "") != weights_sha256:
        raise ValueError(f"{role} summary does not bind the supplied weights")
    summary_model_id = str(summary.get("model_id") or "")
    if summary_model_id and summary_model_id != model_id:
        raise ValueError(f"{role} summary model id differs from the declared model id")
    if tuple(summary.get("input_columns") or ()) != INPUT_COLUMNS:
        raise ValueError(f"{role} input columns differ from the reference contract")
    if tuple(summary.get("geometry_columns") or ()) != GEOMETRY_COLUMNS:
        raise ValueError(f"{role} geometry columns differ from the reference contract")
    if _parse_hidden_widths(arguments.get("forward_hidden_widths")) != tuple(
        EXPECTED_FORWARD_ARCHITECTURE[1:-1]
    ):
        raise ValueError(f"{role} summary forward architecture is not exact")
    if _parse_hidden_widths(arguments.get("inverse_hidden_widths")) != tuple(
        EXPECTED_INVERSE_ARCHITECTURE[1:-1]
    ):
        raise ValueError(f"{role} summary inverse architecture is not exact")
    if arguments.get("inverse_geometry_projection") != EXPECTED_PROJECTION_MODE:
        raise ValueError(f"{role} summary projection mode is not hard-feasible")
    if arguments.get("q_target_semantics") != "minimum":
        raise ValueError(f"{role} summary does not use Q-minimum semantics")
    q_margin = float(arguments.get("q_minimum_margin_physical"))
    if not math.isfinite(q_margin):
        raise ValueError(f"{role} Q-minimum guardband is non-finite")
    if int(arguments.get("local_refinement_steps") or 0) != 0:
        raise ValueError(f"{role} summary enables target-dependent refinement")
    if int(arguments.get("local_refinement_starts") or 0) != 1:
        raise ValueError(f"{role} summary is not one-shot")
    if method.get("geometry_output_constraint") != EXPECTED_PROJECTION_MODE:
        raise ValueError(f"{role} method does not bind the hard-feasible decoder")
    if method.get("geometry_output_constraint_is_single_pass") is not True:
        raise ValueError(f"{role} decoder is not declared single-pass")
    if method.get("geometry_output_constraint_is_posthoc_repair") is not False:
        raise ValueError(f"{role} decoder is declared as post-hoc repair")
    if contract.get("trainer_implementation_sha256") != trainer_sha256:
        raise ValueError(f"{role} summary does not bind the supplied trainer")
    architecture = contract.get("architecture") or {}
    if tuple(architecture.get("forward_hidden_widths") or ()) != tuple(
        EXPECTED_FORWARD_ARCHITECTURE[1:-1]
    ) or tuple(architecture.get("inverse_hidden_widths") or ()) != tuple(
        EXPECTED_INVERSE_ARCHITECTURE[1:-1]
    ):
        raise ValueError(f"{role} comparison contract architecture is not exact")
    if architecture.get("inverse_geometry_projection") != EXPECTED_PROJECTION_MODE:
        raise ValueError(f"{role} comparison contract decoder is not exact")
    if (contract.get("loss") or {}).get("q_target_semantics") != "minimum":
        raise ValueError(f"{role} comparison contract lacks Q-minimum semantics")
    if float((contract.get("loss") or {}).get("q_minimum_margin_physical")) != q_margin:
        raise ValueError(f"{role} Q-minimum guardband is internally inconsistent")

    counts = (summary.get("split_audit") or {}).get("row_counts") or {}
    split_counts = {
        name: int(counts.get(name) or 0) for name in ("train", "validation", "test")
    }
    if any(value <= 0 for value in split_counts.values()):
        raise ValueError(f"{role} split row counts are absent or non-positive")
    if sum(split_counts.values()) != expected_training_count:
        raise ValueError(f"{role} split counts do not close to the source-table count")

    run_directory = str(summary.get("out_dir") or arguments.get("out_dir") or "")
    if not run_directory:
        raise ValueError(f"{role} training run directory identity is absent")
    normalized_arguments = {
        key: value
        for key, value in arguments.items()
        if key not in POPULATION_SPECIFIC_ARGUMENT_KEYS
    }
    return {
        "model_id": model_id,
        "summary_status": {
            "overall_status": summary.get("overall_status"),
            "execution_status": summary.get("execution_status"),
            "quality_status": summary.get("quality_status"),
            "eligible_for_checkpoint_model_acceptance": summary.get(
                "eligible_for_checkpoint_model_acceptance"
            ),
            "eligible_for_model_success_claim": summary.get(
                "eligible_for_model_success_claim"
            ),
        },
        "source_table_rows": expected_training_count,
        "accepted_rows": expected_training_count,
        "gradient_training_rows": split_counts["train"],
        "validation_rows": split_counts["validation"],
        "test_rows": split_counts["test"],
        "run_directory_identity_sha256": hashlib.sha256(
            run_directory.encode("utf-8")
        ).hexdigest(),
        "trainer_sha256": trainer_sha256,
        "weights_sha256": weights_sha256,
        "model_comparison_contract_sha256": contract_fingerprint,
        "contract_arguments_without_population_paths": normalized_arguments,
        "q_minimum_guardband_physical": q_margin,
        "projection_mode": model["projection_mode"],
        "forward_architecture": list(model["forward_architecture"]),
        "inverse_architecture": list(model["inverse_architecture"]),
        "forward_parameter_count": model["forward_parameter_count"],
        "inverse_parameter_count": model["inverse_parameter_count"],
        "parameter_count": model["parameter_count"],
        "normalization_sha256": model["normalization_sha256"],
    }


def _targets(payload: dict[str, Any]) -> tuple[list[str], np.ndarray, np.ndarray]:
    if payload.get("schema") != "direct_mlp_one_shot_targets_v1":
        raise ValueError("target JSON is not the frozen direct-MLP target schema")
    if payload.get("target_role") != "nonadvisor_fixed_proxy_frame":
        raise ValueError("target JSON has the wrong scientific role")
    if payload.get("q_target_semantics") != "minimum":
        raise ValueError("target JSON does not use Q-minimum semantics")
    rows = payload.get("targets")
    if not isinstance(rows, list) or len(rows) != EXPECTED_TARGET_FRAME_ROWS:
        raise ValueError("target JSON does not contain the exact frozen frame row count")
    if int(payload.get("row_count") or 0) != EXPECTED_TARGET_FRAME_ROWS:
        raise ValueError("target JSON row_count metadata is inconsistent")

    ids: list[str] = []
    values: list[list[float]] = []
    original_indices: list[int] = []
    all_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"target row {index} is not an object")
        target_id = str(row.get("target_id") or "")
        if not target_id or target_id in all_ids:
            raise ValueError("target ids are empty or non-unique")
        all_ids.add(target_id)
        k_abs = float(row["K_abs"])
        if not math.isfinite(k_abs):
            raise ValueError(f"target row {index} contains a non-finite K_abs")
        if k_abs <= 0.8:
            vector = [float(row[key]) for key in TARGET_KEYS[:-1]] + [k_abs]
            if not all(math.isfinite(value) for value in vector):
                raise ValueError(f"legacy target row {index} contains non-finite values")
            ids.append(target_id)
            values.append(vector)
            original_indices.append(index)
    if len(ids) != EXPECTED_LEGACY_ROWS:
        raise ValueError("frozen target frame does not select exactly 8000 legacy rows")
    matrix = np.asarray(values, dtype=float)
    indices = np.asarray(original_indices, dtype=int)
    if matrix.shape != (EXPECTED_LEGACY_ROWS, 4):
        raise ValueError(f"unexpected legacy target matrix shape: {matrix.shape}")
    return ids, matrix, indices


def _normalization_for_trainer(model: dict[str, Any]) -> dict[str, np.ndarray]:
    return {
        "x_mean": model["x_mean"],
        "x_scale": model["x_scale"],
        "y_mean": model["y_mean"],
        "y_scale": model["y_scale"],
        "geometry_lower": model["geometry_lower"],
        "geometry_upper": model["geometry_upper"],
        "response_loss_dimension_weights": model["dimension_weights"],
    }


def _infer(
    target: np.ndarray, model: dict[str, Any], trainer: ModuleType, role: str
) -> dict[str, np.ndarray]:
    standardized_target = (target - model["x_mean"][None, :]) / model[
        "x_scale"
    ][None, :]
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        geometry_normalized = trainer._predict_inverse(
            standardized_target,
            model["inverse_weights"],
            model["inverse_biases"],
            model["geometry_lower"],
            model["geometry_upper"],
            projection_mode=model["projection_mode"],
            normalization=_normalization_for_trainer(model),
            topology_contract=model["topology_contract"],
        )
        response_standardized = trainer._predict(
            geometry_normalized,
            model["forward_weights"],
            model["forward_biases"],
        )
    geometry_normalized = np.asarray(geometry_normalized, dtype=float)
    response_standardized = np.asarray(response_standardized, dtype=float)
    expected_rows = target.shape[0]
    if geometry_normalized.shape != (expected_rows, 10):
        raise ValueError(f"{role} inverse output shape is not ({expected_rows}, 10)")
    if response_standardized.shape != (expected_rows, 4):
        raise ValueError(f"{role} forward output shape is not ({expected_rows}, 4)")
    geometry = geometry_normalized * model["y_scale"][None, :] + model[
        "y_mean"
    ][None, :]
    response = response_standardized * model["x_scale"][None, :] + model[
        "x_mean"
    ][None, :]
    arrays = {
        "standardized_target": standardized_target,
        "geometry_normalized": geometry_normalized,
        "geometry": geometry,
        "response_standardized": response_standardized,
        "response": response,
    }
    for name, value in arrays.items():
        if not np.all(np.isfinite(value)):
            raise ValueError(f"{role} inference produced non-finite {name}")
    tolerance = 1.0e-12
    if np.any(geometry_normalized < model["geometry_lower"][None, :] - tolerance) or np.any(
        geometry_normalized > model["geometry_upper"][None, :] + tolerance
    ):
        raise ValueError(f"{role} hard-feasible decoder left its saved envelope")
    return arrays


def _calculate_metrics(target: np.ndarray, prediction: np.ndarray) -> dict[str, Any]:
    if target.shape != prediction.shape or target.ndim != 2 or target.shape[1] != 4:
        raise ValueError("metric inputs must be matching N x 4 matrices")
    if target.shape[0] == 0 or not np.all(np.isfinite(target)) or not np.all(
        np.isfinite(prediction)
    ):
        raise ValueError("metric inputs must be nonempty and finite")
    signed = prediction - target
    absolute = np.abs(signed)
    normalized = signed / NORMALIZATION_SPANS[None, :]
    per_feature: dict[str, dict[str, float]] = {}
    for index, feature in enumerate(FEATURE_LABELS):
        feature_absolute = absolute[:, index]
        per_feature[feature] = {
            "bias": float(np.mean(signed[:, index])),
            "mae": float(np.mean(feature_absolute)),
            "rmse": float(np.sqrt(np.mean(signed[:, index] ** 2))),
            "median_absolute_error": float(np.median(feature_absolute)),
            "p90_absolute_error": float(np.percentile(feature_absolute, 90.0)),
            "p95_absolute_error": float(np.percentile(feature_absolute, 95.0)),
            "normalized_mae": float(np.mean(np.abs(normalized[:, index]))),
            "normalized_rmse": float(
                np.sqrt(np.mean(normalized[:, index] ** 2))
            ),
        }

    q_shortfall = np.maximum(target[:, 2] - prediction[:, 2], 0.0)
    q_shortfall_normalized = q_shortfall / NORMALIZATION_SPANS[2]
    row_joint = np.sqrt(np.mean(normalized**2, axis=1))
    engineering_normalized = normalized.copy()
    engineering_normalized[:, 2] = q_shortfall_normalized
    row_joint_q_shortfall = np.sqrt(np.mean(engineering_normalized**2, axis=1))

    def distribution(values: np.ndarray) -> dict[str, float]:
        return {
            "mean": float(np.mean(values)),
            "rmse": float(np.sqrt(np.mean(values**2))),
            "median": float(np.median(values)),
            "p90": float(np.percentile(values, 90.0)),
            "p95": float(np.percentile(values, 95.0)),
            "maximum": float(np.max(values)),
        }

    return {
        "row_count": int(target.shape[0]),
        "normalization_spans": {
            feature: float(NORMALIZATION_SPANS[index])
            for index, feature in enumerate(FEATURE_LABELS)
        },
        "per_feature": per_feature,
        "q_one_sided_shortfall": {
            **distribution(q_shortfall),
            "normalized_mean": float(np.mean(q_shortfall_normalized)),
            "normalized_rmse": float(
                np.sqrt(np.mean(q_shortfall_normalized**2))
            ),
            "target_met_rate": float(np.mean(prediction[:, 2] >= target[:, 2])),
        },
        "joint_normalized_error": distribution(row_joint),
        "joint_q_shortfall_normalized_error": distribution(
            row_joint_q_shortfall
        ),
    }


def _geometry_sha256(row: np.ndarray) -> str:
    rounded = np.round(np.asarray(row, dtype=np.float64), decimals=12)
    return hashlib.sha256(rounded.tobytes()).hexdigest()


def _prediction_rows(
    *,
    target_ids: list[str],
    original_indices: np.ndarray,
    target: np.ndarray,
    inference: dict[str, np.ndarray],
    model_id: str,
    role: str,
) -> Iterable[dict[str, Any]]:
    prediction = inference["response"]
    geometry = inference["geometry"]
    for index, target_id in enumerate(target_ids):
        signed = prediction[index] - target[index]
        normalized = signed / NORMALIZATION_SPANS
        q_shortfall = max(float(target[index, 2] - prediction[index, 2]), 0.0)
        engineering = normalized.copy()
        engineering[2] = q_shortfall / NORMALIZATION_SPANS[2]
        row: dict[str, Any] = {
            "legacy_row_index": index,
            "fixed10k_original_row_index": int(original_indices[index]),
            "target_id": target_id,
            "panel": "legacy_k_le_0p8",
            "model_role": role,
            "model_id": model_id,
            "inference_mode": "one_shot_hard_feasible_topology_v1",
            "advisor_comparison_eligible": False,
            "reference_selection_status": EXPECTED_REFERENCE_SELECTION_STATUS,
            "engineering_evaluation_allowed": True,
        }
        for feature_index, feature in enumerate(FEATURE_LABELS):
            key = feature.lower()
            row[f"target__{key}"] = float(target[index, feature_index])
            row[f"proxy_prediction__{key}"] = float(
                prediction[index, feature_index]
            )
            row[f"signed_error__{key}"] = float(signed[feature_index])
            row[f"absolute_error__{key}"] = float(abs(signed[feature_index]))
            row[f"normalized_error__{key}"] = float(normalized[feature_index])
        row["q_one_sided_shortfall"] = q_shortfall
        row["q_target_met"] = bool(prediction[index, 2] >= target[index, 2])
        row["joint_normalized_error"] = float(np.sqrt(np.mean(normalized**2)))
        row["joint_q_shortfall_normalized_error"] = float(
            np.sqrt(np.mean(engineering**2))
        )
        for column, value in zip(GEOMETRY_COLUMNS, geometry[index]):
            row[column] = float(value)
        row["geometry_sha256_12decimal_float64"] = _geometry_sha256(
            geometry[index]
        )
        yield row


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    iterator = iter(rows)
    try:
        first = next(iterator)
    except StopIteration as exc:
        raise ValueError(f"refusing to write an empty CSV: {path.name}") from exc
    count = 0
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(first))
        writer.writeheader()
        writer.writerow(first)
        count = 1
        for row in iterator:
            if list(row) != list(first):
                raise ValueError("CSV row schema changed during export")
            writer.writerow(row)
            count += 1
    return count


def _comparison_rows(
    metrics_100k: dict[str, Any], metrics_200k: dict[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def append(
        scope: str,
        feature: str,
        metric: str,
        value_100k: float,
        value_200k: float,
        direction: str,
    ) -> None:
        relative: float | None = None
        if direction != "zero_is_better" and value_100k != 0.0:
            relative = (value_200k - value_100k) / abs(value_100k)
        rows.append(
            {
                "panel": "legacy_k_le_0p8",
                "evidence_class": "architecture_matched_own_forward_proxy",
                "advisor_comparison_eligible": False,
                "reference_selection_status": EXPECTED_REFERENCE_SELECTION_STATUS,
                "engineering_evaluation_allowed": True,
                "scope": scope,
                "feature": feature,
                "metric": metric,
                "direction": direction,
                "model_100k_value": value_100k,
                "model_200k_value": value_200k,
                "delta_200k_minus_100k": value_200k - value_100k,
                "relative_change_fraction": relative,
            }
        )

    feature_metrics = (
        "bias",
        "mae",
        "rmse",
        "median_absolute_error",
        "p90_absolute_error",
        "p95_absolute_error",
        "normalized_mae",
        "normalized_rmse",
    )
    for feature in FEATURE_LABELS:
        for metric in feature_metrics:
            append(
                "per_feature",
                feature,
                metric,
                metrics_100k["per_feature"][feature][metric],
                metrics_200k["per_feature"][feature][metric],
                "zero_is_better" if metric == "bias" else "lower_is_better",
            )
    for metric in (
        "mean",
        "rmse",
        "median",
        "p90",
        "p95",
        "maximum",
        "normalized_mean",
        "normalized_rmse",
        "target_met_rate",
    ):
        append(
            "q_one_sided_shortfall",
            "Qmin",
            metric,
            metrics_100k["q_one_sided_shortfall"][metric],
            metrics_200k["q_one_sided_shortfall"][metric],
            "higher_is_better" if metric == "target_met_rate" else "lower_is_better",
        )
    for scope in (
        "joint_normalized_error",
        "joint_q_shortfall_normalized_error",
    ):
        for metric in ("mean", "rmse", "median", "p90", "p95", "maximum"):
            append(
                scope,
                "all_four",
                metric,
                metrics_100k[scope][metric],
                metrics_200k[scope][metric],
                "lower_is_better",
            )
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    evaluator_path = Path(__file__).resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise FileExistsError(f"no-clobber output already exists: {out_dir}")

    paths: dict[str, Path] = {
        "targets": Path(args.targets_json).expanduser().resolve(),
        "reference_contract": Path(args.reference_contract).expanduser().resolve(),
    }
    expected_hashes: dict[str, str] = {
        "targets": args.expected_targets_sha256,
        "reference_contract": args.expected_reference_contract_sha256,
    }
    model_ids: dict[str, str] = {}
    for role in ("100k", "200k"):
        model_id = str(getattr(args, f"model_{role}_id")).strip()
        if not model_id:
            raise ValueError(f"{role} model id is empty")
        model_ids[role] = model_id
        for kind in ("summary", "weights", "trainer_source"):
            paths[f"{role}_{kind}"] = Path(
                getattr(args, f"model_{role}_{kind}")
            ).expanduser().resolve()
            expected_kind = "trainer" if kind == "trainer_source" else kind
            expected_hashes[f"{role}_{kind}"] = getattr(
                args, f"expected_model_{role}_{expected_kind}_sha256"
            )

    actual_hashes = {
        label: _require_sha(path, expected_hashes[label], label)
        for label, path in paths.items()
    }
    if actual_hashes["targets"] != FROZEN_FIXED10K_SHA256:
        raise ValueError("targets are not the immutable project fixed10k artifact")
    if actual_hashes["100k_trainer_source"] != actual_hashes["200k_trainer_source"]:
        raise ValueError("100k and 200k trainer source hashes differ")

    reference_contract_payload = _read_json(paths["reference_contract"])
    comparison_eligibility = _validate_reference_contract(
        reference_contract_payload, model_ids
    )
    target_payload = _read_json(paths["targets"])
    target_ids, target_matrix, original_indices = _targets(target_payload)
    summaries: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, Any]] = {}
    model_records: dict[str, dict[str, Any]] = {}
    trainers: dict[str, ModuleType] = {}
    for role, expected_training_count in (("100k", 100_000), ("200k", 200_000)):
        summaries[role] = _read_json(paths[f"{role}_summary"])
        models[role] = _load_model(paths[f"{role}_weights"])
        model_records[role] = _validate_summary(
            summaries[role],
            models[role],
            role=role,
            model_id=model_ids[role],
            expected_training_count=expected_training_count,
            weights_sha256=actual_hashes[f"{role}_weights"],
            trainer_sha256=actual_hashes[f"{role}_trainer_source"],
        )
        trainers[role] = _load_trainer(
            paths[f"{role}_trainer_source"],
            actual_hashes[f"{role}_trainer_source"],
            role,
        )

    if (
        model_records["100k"]["model_comparison_contract_sha256"]
        != model_records["200k"]["model_comparison_contract_sha256"]
    ):
        raise ValueError("model-comparison contract fingerprints differ")
    if _canonical_json(
        model_records["100k"]["contract_arguments_without_population_paths"]
    ) != _canonical_json(
        model_records["200k"]["contract_arguments_without_population_paths"]
    ):
        raise ValueError("trainer arguments differ beyond the training-data population")
    if (
        model_records["100k"]["q_minimum_guardband_physical"]
        != model_records["200k"]["q_minimum_guardband_physical"]
    ):
        raise ValueError("Q-minimum guardbands differ")

    inference = {
        role: _infer(target_matrix, models[role], trainers[role], role)
        for role in ("100k", "200k")
    }
    metrics = {
        role: _calculate_metrics(target_matrix, inference[role]["response"])
        for role in ("100k", "200k")
    }

    out_dir.mkdir(parents=True, exist_ok=False)
    prediction_paths = {
        role: out_dir / f"per_target_{role}_predictions.csv"
        for role in ("100k", "200k")
    }
    prediction_counts: dict[str, int] = {}
    for role in ("100k", "200k"):
        prediction_counts[role] = _write_csv(
            prediction_paths[role],
            _prediction_rows(
                target_ids=target_ids,
                original_indices=original_indices,
                target=target_matrix,
                inference=inference[role],
                model_id=model_ids[role],
                role=role,
            ),
        )
        if prediction_counts[role] != EXPECTED_LEGACY_ROWS:
            raise RuntimeError(f"{role} prediction export row count changed")

    comparison_path = out_dir / "architecture_matched_comparison.csv"
    comparison_rows = _comparison_rows(metrics["100k"], metrics["200k"])
    comparison_count = _write_csv(comparison_path, comparison_rows)

    source_records = {
        label: _source_record(paths[label], actual_hashes[label])
        for label in sorted(paths)
    }
    source_records["evaluator_source"] = _source_record(
        evaluator_path, _sha256(evaluator_path)
    )
    summary_path = out_dir / "evaluation_summary.json"
    summary_payload: dict[str, Any] = {
        "schema": "architecture_matched_fixed_legacy8k_proxy_evaluation_v2",
        "evaluation_execution_status": "PASS",
        "evaluation_execution_scope": "FIXED_LEGACY8K_OWN_FORWARD_PROXY_ONLY",
        "advisor_comparison_eligible": False,
        "formal_advisor_comparison_status": (
            "INELIGIBLE_REFERENCE_SELECTION_MISMATCH"
        ),
        "reference_selection_status": EXPECTED_REFERENCE_SELECTION_STATUS,
        "reference_role": EXPECTED_REFERENCE_ROLE,
        "engineering_evaluation_allowed": True,
        "scientific_boundary": (
            "The reference is the deployed and previous-presentation seed20260713 model, "
            "but identity evidence proves it is not the final global winner among the "
            "historical 100k experiments. Therefore advisor_comparison_eligible is false: "
            "this execution is an engineering-only diagnostic and must not be presented as "
            "the formal advisor 100k-versus-200k comparison. Both inverse outputs are scored "
            "by their own frozen forward proxies, not fresh EMX or measured physical "
            "accuracy. The frozen target frame is evaluation-only and is not used for "
            "training, validation, early stopping, checkpoint selection, threshold "
            "selection, refinement, search, or reranking. A single paired seed does not "
            "quantify training-randomness uncertainty."
        ),
        "comparison_eligibility": comparison_eligibility,
        "comparison_design": {
            "panel": "legacy_k_le_0p8",
            "selection_filter": "K_abs <= 0.8",
            "selected_row_count": EXPECTED_LEGACY_ROWS,
            "target_frame_row_count": EXPECTED_TARGET_FRAME_ROWS,
            "high_k_extension_included": False,
            "targets_regenerated": False,
            "emx_run": False,
            "inference_mode": "one_shot_hard_feasible_topology_v1",
            "normalization_spans": {
                feature: float(NORMALIZATION_SPANS[index])
                for index, feature in enumerate(FEATURE_LABELS)
            },
            "normalization_span_source": "historical physical-domain declared ranges",
        },
        "contract_checks": {
            "trainer_source_sha256_exact_and_equal": True,
            "model_comparison_contract_fingerprint_exact_and_equal": True,
            "arguments_equal_except_training_population_paths_and_counts": True,
            "input_and_geometry_columns_exact": True,
            "inverse_architecture_exact": True,
            "forward_architecture_exact": True,
            "parameter_count_exact": True,
            "hard_feasible_decoder_exact": True,
            "q_minimum_guardband_exact_and_equal": True,
            "all_loaded_arrays_and_predictions_finite": True,
            "fixed10k_hash_exact": True,
            "legacy_row_count_exact": True,
            "reference_contract_sha256_exact": True,
            "reference_and_candidate_model_ids_exact": True,
            "advisor_comparison_ineligibility_acknowledged": True,
            "engineering_evaluation_authority_exact": True,
        },
        "models": model_records,
        "metrics": metrics,
        "sources": source_records,
        "outputs": {
            "per_target_100k_predictions.csv": {
                "row_count": prediction_counts["100k"],
                "sha256": _sha256(prediction_paths["100k"]),
            },
            "per_target_200k_predictions.csv": {
                "row_count": prediction_counts["200k"],
                "sha256": _sha256(prediction_paths["200k"]),
            },
            "architecture_matched_comparison.csv": {
                "row_count": comparison_count,
                "sha256": _sha256(comparison_path),
            },
        },
    }
    for model_record in model_records.values():
        model_record.pop("contract_arguments_without_population_paths", None)
    summary_payload["canonical_payload_sha256_without_self"] = _canonical_sha256(
        summary_payload
    )
    _write_json(summary_path, summary_payload)

    sha_path = out_dir / "SHA256SUMS.txt"
    with sha_path.open("x", encoding="ascii", newline="\n") as handle:
        for filename in GENERATED_FILENAMES:
            handle.write(f"{_sha256(out_dir / filename)}  {filename}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
