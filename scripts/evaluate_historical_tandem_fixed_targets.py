#!/usr/bin/env python3
"""Replay a hash-pinned historical tandem model on a frozen target frame.

This is a diagnostic replay utility.  It deliberately keeps legacy-domain
targets (K <= 0.8) separate from K-extension targets and labels all forward
network responses as proxy predictions, never as EMX truth.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np


INPUT_COLUMNS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
TARGET_KEYS = ("Lp_nH", "Ls_nH", "Q_min", "K_abs")
TARGET_OUTPUT_COLUMNS = (
    "target__lp_nh_center",
    "target__ls_nh_center",
    "target__q_center",
    "target__k_abs_center",
)
PROXY_OUTPUT_COLUMNS = (
    "proxy__lp_nh_center",
    "proxy__ls_nh_center",
    "proxy__q_center",
    "proxy__k_abs_center",
)
LEGACY_SPANS = np.asarray([2.5, 2.5, 20.0, 0.8], dtype=float)
FIXED_FRAME_SPANS = np.asarray([2.5, 2.5, 20.0, 1.0], dtype=float)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights-npz", required=True)
    parser.add_argument("--model-summary", required=True)
    parser.add_argument("--model-manifest")
    parser.add_argument("--targets-json", required=True)
    parser.add_argument("--trainer-source", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--expected-weights-sha256", required=True)
    parser.add_argument("--expected-summary-sha256", required=True)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--expected-targets-sha256", required=True)
    parser.add_argument("--expected-trainer-sha256", required=True)
    parser.add_argument("--expected-model-seed", type=int, required=True)
    parser.add_argument("--expected-training-count", type=int, required=True)
    parser.add_argument(
        "--selection-rule",
        required=True,
        help="Artifact-selection rule fixed before this replay.",
    )
    parser.add_argument(
        "--selection-rule-source",
        required=True,
        help="Traceable local record supporting --selection-rule.",
    )
    parser.add_argument("--expected-selection-rule-source-sha256", required=True)
    parser.add_argument(
        "--warning",
        action="append",
        default=[],
        help="Known evidence limitation or historical conflict; repeat as needed.",
    )
    parser.add_argument(
        "--inference-mode",
        choices=("one_shot", "historical_proxy_refinement"),
        required=True,
    )
    return parser.parse_args(argv)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("ascii")).hexdigest()


def _float_csv(value: Any) -> tuple[float, ...]:
    return tuple(float(item.strip()) for item in str(value).split(","))


def _layer_widths(weights: list[np.ndarray]) -> list[int]:
    if not weights or any(weight.ndim != 2 for weight in weights):
        raise ValueError("model weights must be a nonempty list of matrices")
    widths = [int(weights[0].shape[0])]
    for index, weight in enumerate(weights):
        if int(weight.shape[0]) != widths[-1]:
            raise ValueError(f"weight chain is disconnected at layer {index}")
        widths.append(int(weight.shape[1]))
    return widths


def _vector_digest(values: np.ndarray) -> str:
    """Match the project's portable frozen-MLP geometry digest contract."""
    rounded = np.round(np.asarray(values, dtype=np.float64), decimals=12)
    return hashlib.sha256(rounded.tobytes()).hexdigest()


def _load_trainer(path: Path) -> ModuleType:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    module_name = f"_historical_tandem_trainer_{_sha256(path)[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load trainer source: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    for symbol in ("_predict", "_predict_inverse", "_refine_geometry_candidates"):
        if not callable(getattr(module, symbol, None)):
            raise AttributeError(f"historical trainer does not expose {symbol}")
    return module


def _numbered(archive: Any, prefix: str) -> list[np.ndarray]:
    keys = sorted(
        (key for key in archive.files if key.startswith(prefix)),
        key=lambda key: int(key.removeprefix(prefix)),
    )
    if not keys:
        raise KeyError(f"missing arrays with prefix {prefix}")
    return [np.asarray(archive[key], dtype=float) for key in keys]


def _archive_string(archive: Any, key: str, default: str) -> str:
    if key not in archive.files:
        return default
    values = np.asarray(archive[key]).reshape(-1)
    if values.size != 1:
        raise ValueError(f"weights field {key} must contain exactly one string")
    return str(values[0])


def _load_weights(path: Path) -> dict[str, Any]:
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
            raise KeyError(f"weights archive missing normalization arrays: {missing}")
        projection_mode = _archive_string(
            archive,
            "inverse_geometry_projection__mode",
            "independent_sigmoid",
        )
        topology_json = _archive_string(
            archive,
            "inverse_geometry_projection__topology_contract_json",
            "",
        )
        result = {
            "forward_weights": _numbered(archive, "forward_weight_"),
            "forward_biases": _numbered(archive, "forward_bias_"),
            "inverse_weights": _numbered(archive, "inverse_weight_"),
            "inverse_biases": _numbered(archive, "inverse_bias_"),
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
            "projection_mode": projection_mode,
            "topology_contract": json.loads(topology_json) if topology_json else {},
        }
    for key in (
        "forward_weights",
        "forward_biases",
        "inverse_weights",
        "inverse_biases",
        "x_mean",
        "x_scale",
        "y_mean",
        "y_scale",
        "geometry_lower",
        "geometry_upper",
        "dimension_weights",
    ):
        value = result[key]
        arrays = value if isinstance(value, list) else [value]
        if not all(np.all(np.isfinite(array)) for array in arrays):
            raise ValueError(f"non-finite values in weights field {key}")
    if result["projection_mode"] not in (
        "independent_sigmoid",
        "hard_feasible_topology_v1",
    ):
        raise ValueError(
            f"unsupported inverse projection mode: {result['projection_mode']}"
        )
    if not isinstance(result["topology_contract"], dict):
        raise ValueError("weights topology contract must be a JSON object")
    if (
        result["projection_mode"] == "hard_feasible_topology_v1"
        and not result["topology_contract"].get("available")
    ):
        raise ValueError("hard-feasible weights are missing their topology contract")
    return result


def _targets(payload: dict[str, Any]) -> tuple[list[str], np.ndarray]:
    rows = payload.get("targets")
    if not isinstance(rows, list) or not rows:
        raise ValueError("target frame has no target rows")
    ids: list[str] = []
    values: list[list[float]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"target row {index} is not an object")
        target_id = str(row.get("target_id") or "")
        vector = [float(row[key]) for key in TARGET_KEYS]
        if not target_id or not all(math.isfinite(value) for value in vector):
            raise ValueError(f"invalid target row {index}")
        ids.append(target_id)
        values.append(vector)
    if len(ids) != len(set(ids)):
        raise ValueError("target ids are not unique")
    matrix = np.asarray(values, dtype=float)
    if matrix.shape != (len(rows), 4):
        raise ValueError(f"unexpected target matrix shape {matrix.shape}")
    return ids, matrix


def _percentiles(values: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "p90": float(np.percentile(values, 90.0)),
        "p95": float(np.percentile(values, 95.0)),
        "p99": float(np.percentile(values, 99.0)),
        "maximum": float(np.max(values)),
    }


def _panel_metrics(
    target: np.ndarray,
    proxy: np.ndarray,
    mask: np.ndarray,
) -> dict[str, Any]:
    target_panel = target[mask]
    proxy_panel = proxy[mask]
    signed = proxy_panel - target_panel
    absolute = np.abs(signed)
    range_error_legacy = signed / LEGACY_SPANS[None, :]
    range_error_fixed = signed / FIXED_FRAME_SPANS[None, :]
    row_joint_fixed = np.sqrt(np.mean(range_error_fixed**2, axis=1))
    row_joint_legacy = np.sqrt(np.mean(range_error_legacy**2, axis=1))
    per_feature: dict[str, Any] = {}
    for index, key in enumerate(TARGET_KEYS):
        per_feature[key] = {
            "mae_physical": float(np.mean(absolute[:, index])),
            "rmse_physical": float(np.sqrt(np.mean(signed[:, index] ** 2))),
            "bias_physical": float(np.mean(signed[:, index])),
            "absolute_error_distribution": _percentiles(absolute[:, index]),
            "mae_fixed_frame_range_fraction": float(
                np.mean(np.abs(range_error_fixed[:, index]))
            ),
            "rmse_fixed_frame_range_fraction": float(
                np.sqrt(np.mean(range_error_fixed[:, index] ** 2))
            ),
        }
    q_shortfall = np.maximum(target_panel[:, 2] - proxy_panel[:, 2], 0.0)
    q_excess = np.maximum(proxy_panel[:, 2] - target_panel[:, 2], 0.0)
    return {
        "row_count": int(np.sum(mask)),
        "per_feature": per_feature,
        "joint_fixed_frame_range_rmse": float(
            np.sqrt(np.mean(range_error_fixed**2))
        ),
        "joint_legacy_range_rmse": float(
            np.sqrt(np.mean(range_error_legacy**2))
        ),
        "per_row_joint_fixed_frame_range_error": _percentiles(row_joint_fixed),
        "per_row_joint_legacy_range_error": _percentiles(row_joint_legacy),
        "q_minimum_semantics": {
            "shortfall_mae": float(np.mean(q_shortfall)),
            "shortfall_p95": float(np.percentile(q_shortfall, 95.0)),
            "shortfall_maximum": float(np.max(q_shortfall)),
            "target_met_rate": float(np.mean(proxy_panel[:, 2] >= target_panel[:, 2])),
            "excess_mean": float(np.mean(q_excess)),
        },
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write an empty result CSV")
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    evaluator_path = Path(__file__).resolve()
    weights_path = Path(args.weights_npz).expanduser().resolve()
    summary_path = Path(args.model_summary).expanduser().resolve()
    manifest_path = (
        Path(args.model_manifest).expanduser().resolve()
        if args.model_manifest
        else None
    )
    targets_path = Path(args.targets_json).expanduser().resolve()
    trainer_path = Path(args.trainer_source).expanduser().resolve()
    selection_source_path = Path(args.selection_rule_source).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if bool(manifest_path) != bool(args.expected_manifest_sha256):
        raise ValueError(
            "--model-manifest and --expected-manifest-sha256 must be supplied together"
        )
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output directory: {out_dir}")

    sources = {
        "evaluator_source": {
            "path": str(evaluator_path),
            "sha256": _sha256(evaluator_path),
        },
        "weights": {"path": str(weights_path), "sha256": _sha256(weights_path)},
        "model_summary": {"path": str(summary_path), "sha256": _sha256(summary_path)},
        "targets": {"path": str(targets_path), "sha256": _sha256(targets_path)},
        "trainer_source": {"path": str(trainer_path), "sha256": _sha256(trainer_path)},
        "selection_rule_source": {
            "path": str(selection_source_path),
            "sha256": _sha256(selection_source_path),
        },
    }
    if manifest_path is not None:
        sources["model_manifest"] = {
            "path": str(manifest_path),
            "sha256": _sha256(manifest_path),
        }
    expected_hashes = {
        "weights": args.expected_weights_sha256.lower(),
        "model_summary": args.expected_summary_sha256.lower(),
        "targets": args.expected_targets_sha256.lower(),
        "trainer_source": args.expected_trainer_sha256.lower(),
        "selection_rule_source": (
            args.expected_selection_rule_source_sha256.lower()
        ),
    }
    if manifest_path is not None:
        expected_hashes["model_manifest"] = args.expected_manifest_sha256.lower()
    for name, expected in expected_hashes.items():
        if sources[name]["sha256"] != expected:
            raise ValueError(
                f"{name} SHA-256 mismatch: {sources[name]['sha256']} != {expected}"
            )

    summary = _read_json(summary_path)
    manifest = _read_json(manifest_path) if manifest_path is not None else None
    target_payload = _read_json(targets_path)
    summary_arguments = summary.get("arguments") or {}
    training_count = int(summary.get("training_count") or 0)
    expected_training_count = int(args.expected_training_count)
    if expected_training_count <= 0:
        raise ValueError("expected training count must be positive")
    model_id = args.model_id.strip()
    selection_rule = args.selection_rule.strip()
    if not model_id or not selection_rule:
        raise ValueError("model id and selection rule must be nonempty")
    if expected_training_count % 1_000 == 0:
        training_label = f"{expected_training_count // 1_000}k"
    else:
        training_label = str(expected_training_count)
    try:
        declared_lower = _float_csv(summary_arguments.get("physical_cell_lower"))
        declared_upper = _float_csv(summary_arguments.get("physical_cell_upper"))
    except (TypeError, ValueError) as exc:
        raise ValueError("model physical support is absent or malformed") from exc
    checks = {
        "all_source_hashes_match": True,
        "model_training_count_exact": training_count == expected_training_count,
        "model_seed_exact": int(summary_arguments.get("seed") or -1)
        == int(args.expected_model_seed),
        "input_columns_exact": tuple(summary.get("input_columns") or ())
        == INPUT_COLUMNS,
        "target_schema_exact": target_payload.get("schema")
        == "direct_mlp_one_shot_targets_v1",
        "target_role_is_fixed_proxy_frame": target_payload.get("target_role")
        == "nonadvisor_fixed_proxy_frame",
        "target_q_semantics_minimum": target_payload.get("q_target_semantics")
        == "minimum",
        "target_row_count_exact": int(target_payload.get("row_count") or 0)
        == 10_000,
        "legacy_support_exact": declared_lower == (0.5, 0.5, 5.0, 0.0)
        and declared_upper == (3.0, 3.0, 25.0, 0.8),
        "historical_model_not_formally_accepted": summary.get(
            "eligible_for_checkpoint_model_acceptance"
        )
        is False,
    }
    if manifest is not None:
        manifest_artifacts = manifest.get("artifacts") or {}
        manifest_contract = manifest.get("production_contract") or {}
        checks.update(
            {
                "manifest_model_id_exact": manifest.get("model_id") == model_id,
                "manifest_model_seed_exact": int(manifest.get("model_seed") or -1)
                == int(args.expected_model_seed),
                "manifest_training_count_exact": int(
                    manifest_contract.get("training_count") or 0
                )
                == expected_training_count,
                "manifest_input_columns_exact": tuple(
                    manifest.get("input_columns") or ()
                )
                == INPUT_COLUMNS,
                "manifest_geometry_columns_match_summary": tuple(
                    manifest.get("geometry_columns") or ()
                )
                == tuple(summary.get("geometry_columns") or ()),
                "manifest_summary_sha_matches": (
                    (manifest_artifacts.get("model_summary") or {}).get("sha256")
                    == sources["model_summary"]["sha256"]
                ),
                "manifest_weights_sha_matches": (
                    (manifest_artifacts.get("model_weights") or {}).get("sha256")
                    == sources["weights"]["sha256"]
                ),
                "manifest_trainer_sha_matches": (
                    (manifest_artifacts.get("trainer_runtime") or {}).get("sha256")
                    == sources["trainer_source"]["sha256"]
                ),
            }
        )

    target_ids, target_matrix = _targets(target_payload)
    model = _load_weights(weights_path)
    geometry_columns = tuple(summary.get("geometry_columns") or ())
    if len(geometry_columns) != 10:
        raise ValueError(f"expected 10 geometry columns, got {len(geometry_columns)}")
    forward_architecture = _layer_widths(model["forward_weights"])
    inverse_architecture = _layer_widths(model["inverse_weights"])
    summary_projection_mode = str(
        summary_arguments.get("inverse_geometry_projection")
        or "independent_sigmoid"
    )
    checks.update(
        {
            "weights_forward_architecture_is_10_to_4": (
                forward_architecture[0] == 10 and forward_architecture[-1] == 4
            ),
            "weights_inverse_architecture_is_4_to_10": (
                inverse_architecture[0] == 4 and inverse_architecture[-1] == 10
            ),
            "weights_projection_mode_matches_summary": (
                model["projection_mode"] == summary_projection_mode
            ),
        }
    )
    if manifest is not None:
        manifest_architecture = manifest.get("architecture") or {}
        checks.update(
            {
                "manifest_forward_architecture_matches_weights": list(
                    manifest_architecture.get("forward_surrogate") or []
                )
                == forward_architecture,
                "manifest_inverse_architecture_matches_weights": list(
                    manifest_architecture.get("inverse_mlp") or []
                )
                == inverse_architecture,
            }
        )
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise ValueError(f"contract checks failed: {failed}")

    configured_refinement_steps = int(
        summary_arguments.get("local_refinement_steps") or 0
    )
    configured_refinement_starts = int(
        summary_arguments.get("local_refinement_starts") or 1
    )
    if (
        args.inference_mode == "historical_proxy_refinement"
        and configured_refinement_steps <= 0
    ):
        raise ValueError(
            "historical proxy refinement requested but the model contract disables it"
        )
    trainer = _load_trainer(trainer_path)

    standardized_targets = (
        target_matrix - model["x_mean"][None, :]
    ) / model["x_scale"][None, :]
    normalization = {
        "x_mean": model["x_mean"],
        "x_scale": model["x_scale"],
        "y_mean": model["y_mean"],
        "y_scale": model["y_scale"],
        "geometry_lower": model["geometry_lower"],
        "geometry_upper": model["geometry_upper"],
        "response_loss_dimension_weights": model["dimension_weights"],
    }
    inverse_kwargs: dict[str, Any] = {}
    if model["projection_mode"] != "independent_sigmoid":
        inverse_kwargs = {
            "projection_mode": model["projection_mode"],
            "normalization": normalization,
            "topology_contract": model["topology_contract"],
        }
    inference_start = time.perf_counter_ns()
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        direct_geometry_normalized = trainer._predict_inverse(
            standardized_targets,
            model["inverse_weights"],
            model["inverse_biases"],
            model["geometry_lower"],
            model["geometry_upper"],
            **inverse_kwargs,
        )
    direct_end = time.perf_counter_ns()

    refinement_record: dict[str, Any]
    if args.inference_mode == "historical_proxy_refinement":
        refinement_args = SimpleNamespace(
            local_refinement_steps=configured_refinement_steps,
            local_refinement_starts=configured_refinement_starts,
            local_refinement_learning_rate=float(
                summary_arguments.get("local_refinement_learning_rate") or 0.05
            ),
            local_refinement_jitter=float(
                summary_arguments.get("local_refinement_jitter") or 0.0
            ),
            local_refinement_seed=int(
                summary_arguments.get("local_refinement_seed") or args.expected_model_seed
            ),
        )
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            selected_geometry_normalized, refinement_record = (
                trainer._refine_geometry_candidates(
                    standardized_targets,
                    direct_geometry_normalized,
                    model["forward_weights"],
                    model["forward_biases"],
                    model["geometry_lower"],
                    model["geometry_upper"],
                    model["dimension_weights"],
                    refinement_args,
                )
            )
    else:
        selected_geometry_normalized = direct_geometry_normalized.copy()
        refinement_record = {
            "enabled": False,
            "method": "one_shot_frozen_inverse_mlp",
            "steps": 0,
            "starts": 1,
        }
    selected_end = time.perf_counter_ns()

    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        direct_proxy_standardized = trainer._predict(
            direct_geometry_normalized,
            model["forward_weights"],
            model["forward_biases"],
        )
        selected_proxy_standardized = trainer._predict(
            selected_geometry_normalized,
            model["forward_weights"],
            model["forward_biases"],
        )
    proxy_end = time.perf_counter_ns()
    direct_proxy = (
        direct_proxy_standardized * model["x_scale"][None, :]
        + model["x_mean"][None, :]
    )
    selected_proxy = (
        selected_proxy_standardized * model["x_scale"][None, :]
        + model["x_mean"][None, :]
    )
    direct_geometry = (
        direct_geometry_normalized * model["y_scale"][None, :]
        + model["y_mean"][None, :]
    )
    selected_geometry = (
        selected_geometry_normalized * model["y_scale"][None, :]
        + model["y_mean"][None, :]
    )
    numerical_checks = {
        "standardized_targets_all_finite": bool(
            np.all(np.isfinite(standardized_targets))
        ),
        "direct_geometry_normalized_all_finite": bool(
            np.all(np.isfinite(direct_geometry_normalized))
        ),
        "selected_geometry_normalized_all_finite": bool(
            np.all(np.isfinite(selected_geometry_normalized))
        ),
        "direct_proxy_all_finite": bool(np.all(np.isfinite(direct_proxy))),
        "selected_proxy_all_finite": bool(np.all(np.isfinite(selected_proxy))),
        "direct_geometry_physical_all_finite": bool(
            np.all(np.isfinite(direct_geometry))
        ),
        "selected_geometry_physical_all_finite": bool(
            np.all(np.isfinite(selected_geometry))
        ),
        "direct_geometry_inside_saved_envelope": bool(
            np.all(direct_geometry_normalized >= model["geometry_lower"][None, :] - 1e-12)
            and np.all(
                direct_geometry_normalized
                <= model["geometry_upper"][None, :] + 1e-12
            )
        ),
        "selected_geometry_inside_saved_envelope": bool(
            np.all(selected_geometry_normalized >= model["geometry_lower"][None, :] - 1e-12)
            and np.all(
                selected_geometry_normalized
                <= model["geometry_upper"][None, :] + 1e-12
            )
        ),
    }
    checks.update(numerical_checks)
    if not all(numerical_checks.values()):
        failed = [name for name, passed in numerical_checks.items() if not passed]
        raise ValueError(f"numerical inference checks failed: {failed}")

    legacy_mask = target_matrix[:, 3] <= 0.8
    extension_mask = target_matrix[:, 3] > 0.8
    if int(np.sum(legacy_mask)) != 8_000 or int(np.sum(extension_mask)) != 2_000:
        raise ValueError("fixed target frame does not split into 8000 legacy / 2000 extension rows")

    rows: list[dict[str, Any]] = []
    for row_index, target_id in enumerate(target_ids):
        target = target_matrix[row_index]
        direct_prediction = direct_proxy[row_index]
        prediction = selected_proxy[row_index]
        signed = prediction - target
        fixed_range = signed / FIXED_FRAME_SPANS
        legacy_range = signed / LEGACY_SPANS
        row: dict[str, Any] = {
            "row_index": row_index,
            "target_id": target_id,
            "panel": "legacy_k_le_0p8" if legacy_mask[row_index] else "extension_k_gt_0p8",
            "inside_historical_training_contract": bool(legacy_mask[row_index]),
            "model_id": model_id,
            "model_seed": int(args.expected_model_seed),
            "inference_mode": args.inference_mode,
        }
        for column, value in zip(TARGET_OUTPUT_COLUMNS, target):
            row[column] = float(value)
        for column, value in zip(PROXY_OUTPUT_COLUMNS, prediction):
            row[column] = float(value)
        for index, key in enumerate(("lp_nh", "ls_nh", "q", "k_abs")):
            row[f"direct_proxy__{key}"] = float(direct_prediction[index])
            row[f"signed_error__{key}"] = float(signed[index])
            row[f"absolute_error__{key}"] = float(abs(signed[index]))
            row[f"fixed_frame_range_error__{key}"] = float(fixed_range[index])
            row[f"legacy_range_error__{key}"] = float(legacy_range[index])
        row["q_shortfall"] = float(max(target[2] - prediction[2], 0.0))
        row["q_target_met"] = bool(prediction[2] >= target[2])
        row["joint_fixed_frame_range_rmse"] = float(
            np.sqrt(np.mean(fixed_range**2))
        )
        row["joint_legacy_range_rmse"] = float(
            np.sqrt(np.mean(legacy_range**2))
        )
        for column, value in zip(geometry_columns, selected_geometry[row_index]):
            row[column] = float(value)
        for column, value in zip(geometry_columns, direct_geometry[row_index]):
            row[f"direct__{column}"] = float(value)
        row["selected_geometry_sha256"] = _vector_digest(
            selected_geometry[row_index]
        )
        rows.append(row)

    out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = out_dir / f"historical_{training_label}_fixed10k_predictions.csv"
    _write_csv(predictions_path, rows)
    metrics = {
        "all_10000": _panel_metrics(
            target_matrix, selected_proxy, np.ones(target_matrix.shape[0], dtype=bool)
        ),
        "legacy_k_le_0p8": _panel_metrics(target_matrix, selected_proxy, legacy_mask),
        "extension_k_gt_0p8": _panel_metrics(target_matrix, selected_proxy, extension_mask),
        "direct_one_shot_all_10000": _panel_metrics(
            target_matrix, direct_proxy, np.ones(target_matrix.shape[0], dtype=bool)
        ),
        "direct_one_shot_legacy_k_le_0p8": _panel_metrics(
            target_matrix, direct_proxy, legacy_mask
        ),
        "direct_one_shot_extension_k_gt_0p8": _panel_metrics(
            target_matrix, direct_proxy, extension_mask
        ),
    }
    summary_payload = {
        "schema": "historical_tandem_fixed_target_proxy_evaluation_v1",
        "overall_status": "PASS_HISTORICAL_DIAGNOSTIC_NOT_CURRENT_MODEL_ACCEPTANCE",
        "scientific_boundary": (
            "The selected responses are predictions from the frozen forward proxy, not EMX labels. "
            "Only the 8000 rows with K_abs<=0.8 are inside the historical model contract; the "
            "remaining 2000 rows are an explicitly labeled extrapolation stress test. The archived "
            "checkpoint is not a current strict-|K|<1 accepted model."
        ),
        "selection_rule": selection_rule,
        "selection_rule_source": sources["selection_rule_source"],
        "warnings": list(args.warning),
        "sources": sources,
        "runtime_environment": {
            "numpy_version": np.__version__,
            "floating_point_policy": (
                "matmul status warnings suppressed during inference; explicit finite and "
                "saved-envelope checks are fail-closed"
            ),
        },
        "checks": checks,
        "model": {
            "model_id": model_id,
            "seed": int(args.expected_model_seed),
            "training_count": training_count,
            "training_label": training_label,
            "input_columns": list(INPUT_COLUMNS),
            "geometry_columns": list(geometry_columns),
            "inverse_architecture": inverse_architecture,
            "forward_proxy_architecture": forward_architecture,
            "inverse_geometry_projection": model["projection_mode"],
            "historical_support_lower": [0.5, 0.5, 5.0, 0.0],
            "historical_support_upper": [3.0, 3.0, 25.0, 0.8],
            "manifest_bound": manifest is not None,
            "eligible_for_checkpoint_model_acceptance": summary.get(
                "eligible_for_checkpoint_model_acceptance"
            ),
        },
        "target_frame": {
            "row_count": len(rows),
            "legacy_row_count": int(np.sum(legacy_mask)),
            "extension_row_count": int(np.sum(extension_mask)),
        },
        "inference": {
            "mode": args.inference_mode,
            "historical_configured_refinement_steps": configured_refinement_steps,
            "historical_configured_refinement_starts": configured_refinement_starts,
            "historical_config_requires_refinement": configured_refinement_steps > 0,
            "direct_inverse_time_seconds": (direct_end - inference_start) / 1e9,
            "refinement_time_seconds": (selected_end - direct_end) / 1e9,
            "proxy_diagnostic_time_seconds": (proxy_end - selected_end) / 1e9,
            "total_compute_time_seconds": (proxy_end - inference_start) / 1e9,
            "refinement_record": refinement_record,
        },
        "metrics": metrics,
        "outputs": {
            "predictions_csv": str(predictions_path),
            "predictions_csv_sha256": _sha256(predictions_path),
            "predictions_row_count": len(rows),
        },
    }
    summary_payload["canonical_payload_sha256_without_self"] = _canonical_sha256(
        summary_payload
    )
    summary_out = out_dir / f"historical_{training_label}_fixed10k_proxy_summary.json"
    summary_out.write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"overall_status={summary_payload['overall_status']}")
    print(f"predictions={predictions_path}")
    print(f"summary={summary_out}")
    print(f"legacy_joint_fixed_range_rmse={metrics['legacy_k_le_0p8']['joint_fixed_frame_range_rmse']}")
    print(f"extension_joint_fixed_range_rmse={metrics['extension_k_gt_0p8']['joint_fixed_frame_range_rmse']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
