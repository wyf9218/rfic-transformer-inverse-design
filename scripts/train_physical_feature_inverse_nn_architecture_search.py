#!/usr/bin/env python3
"""Run a traceable NN architecture search for physical-feature inverse design.

Inputs are physical features (Lp/Ls/Q/K), outputs are geometry variables.  The
implementation uses NumPy MLPs with standardized inputs/outputs, Adam, and early
stopping so it can run in the project environment without requiring PyTorch.
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

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.model_splitting import (  # noqa: E402
    parse_optional_feature_bounds,
    split_physical_feature_indices,
)


DEFAULT_FEATURE_COLUMNS = "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_center"


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    @property
    def pass_bool(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    candidate_csv = Path(args.candidate_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(training_csv)
    candidates = _read_rows(candidate_csv)
    input_columns = _resolve_columns(rows, args.input_columns, args.input_prefix, DEFAULT_FEATURE_COLUMNS)
    geometry_columns = _resolve_columns(rows, args.geometry_columns, args.geom_prefix, "")
    matrix = _training_matrix(rows, input_columns, geometry_columns)
    checks = [
        _check("training CSV exists", training_csv.is_file(), str(training_csv)),
        _check("candidate CSV exists", candidate_csv.is_file(), str(candidate_csv)),
        _check("candidate rows present", bool(candidates), f"candidates={len(candidates)}"),
        _check("candidate IDs unique", _candidate_ids_unique(candidates), f"candidates={len(candidates)}"),
        _check("usable training rows present", matrix["count"] > 0, f"usable_rows={matrix['count']}"),
        _check("usable training rows meet minimum", matrix["count"] >= int(args.min_training_rows), f"usable_rows={matrix['count']}, minimum={args.min_training_rows}"),
        _check("input columns present", bool(input_columns), ",".join(input_columns)),
        _check("geometry columns present", bool(geometry_columns), ",".join(geometry_columns[:8])),
        *_physical_feature_checks(input_columns, args),
    ]
    status = _pretrain_status(checks, training_csv, matrix["count"], args)
    results: list[dict[str, Any]] = []
    best_model: dict[str, Any] = {}
    best_history_rows: list[dict[str, Any]] = []
    best_prediction_rows: list[dict[str, Any]] = []
    best_geometry_error_rows: list[dict[str, Any]] = []
    best_test_evidence: dict[str, Any] = {}
    split_audit: dict[str, Any] = {}
    best_weights_path = out_dir / "physical_feature_inverse_nn_best_model_weights.npz"
    model_json_path = out_dir / "physical_feature_inverse_nn_best_model.json"
    history_csv = out_dir / "physical_feature_inverse_nn_best_model_history.csv"
    predictions_csv = out_dir / "physical_feature_inverse_nn_best_model_test_predictions.csv"
    geometry_errors_csv = out_dir / "physical_feature_inverse_nn_best_model_geometry_errors.csv"
    candidate_checkpoint_root = out_dir / "candidate_checkpoints"
    resumed_candidate_count = 0
    trained_this_run_count = 0

    if status == "READY":
        split, split_audit = _split_indices(matrix, args)
        normalized = _normalize_splits(matrix, split, args)
        normalized["split_audit"] = split_audit
        candidates_to_run = candidates[: int(args.max_candidates)]
        candidate_checkpoint_root.mkdir(parents=True, exist_ok=True)
        for candidate in candidates_to_run:
            contract_fingerprint = _candidate_contract_fingerprint(
                training_csv=training_csv,
                candidate=candidate,
                input_columns=input_columns,
                geometry_columns=geometry_columns,
                split_audit=split_audit,
                normalization=normalized["normalization"],
                args=args,
            )
            loaded = (
                _load_candidate_checkpoint(candidate_checkpoint_root, candidate, contract_fingerprint)
                if args.resume_completed_candidates
                else None
            )
            if loaded is not None:
                result, model = loaded
                result["resumed_from_candidate_checkpoint"] = True
                resumed_candidate_count += 1
            else:
                result, model = _train_candidate(candidate, normalized, args)
                result["resumed_from_candidate_checkpoint"] = False
                _save_candidate_checkpoint(
                    candidate_checkpoint_root,
                    candidate,
                    contract_fingerprint,
                    result,
                    model,
                )
                trained_this_run_count += 1
            results.append(result)
            if result["status"] == "PASS" and _is_better_result(result, best_model.get("result")):
                best_model = model
                best_model["result"] = result
            _write_progress_snapshot(
                out_dir,
                results,
                candidate_checkpoint_root,
                candidates_to_run,
                resumed_candidate_count,
                trained_this_run_count,
            )
        checks.extend(
            [
                _check("NN candidates trained", bool(results), f"trained={len(results)}"),
                _check("best NN model selected", bool(best_model), str(best_model.get("candidate_id", ""))),
                _check(
                    "candidate checkpoints persisted",
                    _candidate_checkpoint_count(candidate_checkpoint_root, candidates_to_run) == len(results),
                    (
                        f"persisted={_candidate_checkpoint_count(candidate_checkpoint_root, candidates_to_run)}, "
                        f"expected={len(results)}"
                    ),
                ),
            ]
        )
        if best_model:
            _save_weights(best_weights_path, best_model)
            best_history_rows = list(best_model.get("history") or [])
            best_prediction_rows, best_geometry_error_rows, best_test_evidence = _prediction_evidence(
                normalized,
                best_model,
                input_columns,
                geometry_columns,
                int(args.max_prediction_rows),
            )
            best_model["weights_npz"] = str(best_weights_path)
            best_model["training_csv"] = str(training_csv)
            best_model["candidate_csv"] = str(candidate_csv)
            best_model["input_columns"] = input_columns
            best_model["geometry_columns"] = geometry_columns
            best_model["normalization"] = normalized["normalization"]
            best_model["history_csv"] = str(history_csv)
            best_model["test_predictions_csv"] = str(predictions_csv)
            best_model["geometry_errors_csv"] = str(geometry_errors_csv)
            best_model["test_evidence"] = best_test_evidence
            best_model["split_audit"] = split_audit
            best_model["model_json"] = str(model_json_path)
            model_json_path.write_text(json.dumps(_json_safe_model(best_model), indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        model_json_path.write_text("", encoding="utf-8")

    overall_status = "PASS" if status == "READY" and all(check.pass_bool for check in checks) else status
    if overall_status == "READY":
        overall_status = "FAIL"
    decision = _decision(overall_status)

    results_csv = out_dir / "physical_feature_inverse_nn_architecture_search_results.csv"
    summary_path = out_dir / "physical_feature_inverse_nn_architecture_search_training_summary.json"
    report_path = out_dir / "physical_feature_inverse_nn_architecture_search_training_report.md"
    _write_csv(results_csv, results)
    _write_csv(history_csv, best_history_rows)
    _write_csv(predictions_csv, best_prediction_rows)
    _write_csv(geometry_errors_csv, best_geometry_error_rows)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "training_csv": str(training_csv),
        "candidate_csv": str(candidate_csv),
        "out_dir": str(out_dir),
        "results_csv": str(results_csv),
        "best_model_json": str(model_json_path),
        "best_weights_npz": str(best_weights_path) if best_model else "",
        "best_history_csv": str(history_csv) if best_model else "",
        "best_test_predictions_csv": str(predictions_csv) if best_model else "",
        "best_geometry_errors_csv": str(geometry_errors_csv) if best_model else "",
        "best_test_evidence": best_test_evidence,
        "training_count": matrix["count"],
        "min_training_rows": int(args.min_training_rows),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "candidate_count": len(candidates),
        "trained_candidate_count": len(results),
        "resumed_candidate_count": resumed_candidate_count,
        "trained_this_run_count": trained_this_run_count,
        "candidate_checkpoint_root": str(candidate_checkpoint_root),
        "selected_candidate": best_model.get("result", {}),
        "split_audit": split_audit,
        "checks": [check.as_dict() for check in checks],
        "arguments": vars(args),
        "limitations": [
            "This is a NumPy MLP architecture-search implementation for traceable per-100k optimization.",
            "A selected NN architecture is not final proof: predicted geometries still require layout checks, real EMX Touchstone generation, and EMX/HFSS correlation samples.",
            "The script does not run unless real physical-feature training rows are present and meet the configured row-count gate.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"results_csv={results_csv}")
    print(f"best_model={model_json_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-columns", default=DEFAULT_FEATURE_COLUMNS)
    parser.add_argument("--geometry-columns")
    parser.add_argument("--input-prefix", default="input__")
    parser.add_argument("--geom-prefix", default="geom__")
    parser.add_argument("--min-training-rows", type=int, default=100_000)
    parser.add_argument("--max-candidates", type=int, default=48)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--test-fraction", type=float, default=0.10)
    parser.add_argument("--split-seed", type=int, default=20260626)
    parser.add_argument(
        "--split-mode",
        choices=("random", "physical_cell_grouped"),
        default="random",
        help="Random rows or complete held-out joint physical-feature cells.",
    )
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument("--physical-cell-lower")
    parser.add_argument("--physical-cell-upper")
    parser.add_argument("--max-epochs-cap", type=int, default=300)
    parser.add_argument("--patience-cap", type=int, default=25)
    parser.add_argument("--max-prediction-rows", type=int, default=2000)
    parser.add_argument("--resume-completed-candidates", action="store_true")
    parser.add_argument("--normalization-floor", type=float, default=1.0e-12)
    parser.add_argument("--max-validation-normalized-rmse", type=float, default=math.inf)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_columns(rows: list[dict[str, str]], explicit: str | None, prefix: str, default: str) -> list[str]:
    if explicit:
        return [item.strip() for item in explicit.split(",") if item.strip()]
    if rows:
        return sorted(column for column in rows[0] if column.startswith(prefix))
    return [item.strip() for item in default.split(",") if item.strip()]


def _training_matrix(rows: list[dict[str, str]], input_columns: list[str], geometry_columns: list[str]) -> dict[str, Any]:
    x_rows: list[list[float]] = []
    y_rows: list[list[float]] = []
    source_indices: list[int] = []
    for source_index, row in enumerate(rows):
        x = [_as_float(row.get(column)) for column in input_columns]
        y = [_as_float(row.get(column)) for column in geometry_columns]
        if any(value is None for value in x) or any(value is None for value in y):
            continue
        x_rows.append([float(value) for value in x if value is not None])
        y_rows.append([float(value) for value in y if value is not None])
        source_indices.append(source_index)
    if not x_rows:
        return {
            "count": 0,
            "x": np.empty((0, len(input_columns))),
            "y": np.empty((0, len(geometry_columns))),
            "source_indices": [],
        }
    return {
        "count": len(x_rows),
        "x": np.asarray(x_rows, dtype=float),
        "y": np.asarray(y_rows, dtype=float),
        "source_indices": source_indices,
    }


def _physical_feature_checks(input_columns: list[str], args: argparse.Namespace) -> list[Check]:
    normalized = {column.removeprefix(str(args.input_prefix)).lower() for column in input_columns}
    zin_columns = [column for column in input_columns if "zin" in column.lower()]
    required = {
        "lp": any("lp" in column for column in normalized),
        "ls": any("ls" in column for column in normalized),
        "q": any(column.startswith("q") or "_q" in column for column in normalized),
        "k_or_kw": any("k" in column for column in normalized),
    }
    return [
        _check("inverse NN train inputs do not use Zin", not zin_columns, f"zin_columns={zin_columns}"),
        _check("inverse NN train inputs include Lp/Ls/Q/K", all(required.values()), str(required)),
    ]


def _pretrain_status(checks: list[Check], training_csv: Path, usable_rows: int, args: argparse.Namespace) -> str:
    if not training_csv.is_file():
        return "WAITING_FOR_TRAINING_CSV"
    if usable_rows < int(args.min_training_rows):
        return "WAITING_FOR_COMPLETE_100K_CHUNK"
    if any(not check.pass_bool for check in checks):
        return "FAIL"
    return "READY"


def _split_indices(matrix: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    x = np.asarray(matrix["x"], dtype=float)
    lower, upper = parse_optional_feature_bounds(args.physical_cell_lower, args.physical_cell_upper, x.shape[1])
    return split_physical_feature_indices(
        x,
        mode=str(args.split_mode),
        seed=int(args.split_seed),
        validation_fraction=float(args.validation_fraction),
        test_fraction=float(args.test_fraction),
        physical_cell_bins=int(args.physical_cell_bins),
        physical_cell_lower=lower,
        physical_cell_upper=upper,
    )


def _normalize_splits(matrix: dict[str, Any], split: dict[str, np.ndarray], args: argparse.Namespace) -> dict[str, Any]:
    x = np.asarray(matrix["x"], dtype=float)
    y = np.asarray(matrix["y"], dtype=float)
    train_idx = split["train"]
    x_mean = np.mean(x[train_idx], axis=0)
    x_scale = np.maximum(np.std(x[train_idx], axis=0), float(args.normalization_floor))
    y_mean = np.mean(y[train_idx], axis=0)
    y_scale = np.maximum(np.std(y[train_idx], axis=0), float(args.normalization_floor))
    return {
        "x": (x - x_mean[None, :]) / x_scale[None, :],
        "y": (y - y_mean[None, :]) / y_scale[None, :],
        "split": split,
        "source_indices": list(matrix.get("source_indices") or []),
        "normalization": {
            "x_mean": [float(value) for value in x_mean],
            "x_scale": [float(value) for value in x_scale],
            "y_mean": [float(value) for value in y_mean],
            "y_scale": [float(value) for value in y_scale],
        },
    }


def _train_candidate(candidate: dict[str, str], data: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate_id = str(candidate.get("candidate_id") or "candidate")
    depth = _int_value(candidate.get("hidden_depth"), 2)
    width = _int_value(candidate.get("hidden_width"), 64)
    dropout = _float_value(candidate.get("dropout"), 0.0)
    learning_rate = _float_value(candidate.get("learning_rate"), 1.0e-3)
    weight_decay = _float_value(candidate.get("weight_decay"), 0.0)
    batch_size = max(1, _int_value(candidate.get("batch_size"), 512))
    seed = _int_value(candidate.get("seed"), int(args.split_seed))
    epochs = min(max(1, _int_value(candidate.get("max_epochs"), int(args.max_epochs_cap))), int(args.max_epochs_cap))
    patience = min(max(1, _int_value(candidate.get("early_stopping_patience"), int(args.patience_cap))), int(args.patience_cap))
    rng = np.random.default_rng(seed)

    x = np.asarray(data["x"], dtype=float)
    y = np.asarray(data["y"], dtype=float)
    split = data["split"]
    weights, biases = _init_mlp(x.shape[1], y.shape[1], depth, width, rng)
    adam = _init_adam(weights, biases)
    best_val = math.inf
    best_weights = [item.copy() for item in weights]
    best_biases = [item.copy() for item in biases]
    best_epoch = 0
    stale = 0
    train_idx = np.asarray(split["train"], dtype=int)
    val_idx = np.asarray(split["validation"], dtype=int)
    train_probe_idx = train_idx[: min(4096, len(train_idx))]
    history: list[dict[str, Any]] = []

    for epoch in range(1, epochs + 1):
        shuffled = rng.permutation(train_idx)
        for start in range(0, len(shuffled), batch_size):
            batch = shuffled[start : start + batch_size]
            grads_w, grads_b = _mlp_gradients(x[batch], y[batch], weights, biases, dropout, weight_decay, rng)
            _adam_step(weights, biases, grads_w, grads_b, adam, learning_rate)
        val_rmse = _rmse(_mlp_predict(x[val_idx], weights, biases), y[val_idx])
        train_probe_rmse = _rmse(_mlp_predict(x[train_probe_idx], weights, biases), y[train_probe_idx])
        improved = val_rmse + 1.0e-12 < best_val
        if val_rmse + 1.0e-12 < best_val:
            best_val = val_rmse
            best_epoch = epoch
            best_weights = [item.copy() for item in weights]
            best_biases = [item.copy() for item in biases]
            stale = 0
        else:
            stale += 1
        history.append(
            {
                "candidate_id": candidate_id,
                "epoch": epoch,
                "train_probe_normalized_rmse": float(train_probe_rmse),
                "validation_normalized_rmse": float(val_rmse),
                "best_validation_normalized_rmse_so_far": float(min(best_val, val_rmse)),
                "is_new_best": bool(improved),
            }
        )
        if stale >= patience:
            break

    metrics = _metrics(x, y, split, best_weights, best_biases)
    envelope_metrics = _geometry_envelope_violation_metrics(
        y,
        split,
        best_weights,
        best_biases,
        x,
    )
    status = "PASS" if metrics["validation_normalized_rmse"] <= float(args.max_validation_normalized_rmse) else "FAIL"
    result = {
        "candidate_id": candidate_id,
        "status": status,
        "hidden_depth": depth,
        "hidden_width": width,
        "dropout": dropout,
        "learning_rate": learning_rate,
        "weight_decay": weight_decay,
        "batch_size": batch_size,
        "seed": seed,
        "epochs_run": epoch,
        "best_epoch": best_epoch,
        "history_points": len(history),
        **metrics,
        **envelope_metrics,
    }
    model = {
        "candidate_id": candidate_id,
        "architecture": {
            "hidden_depth": depth,
            "hidden_width": width,
            "activation": "gelu",
            "dropout": dropout,
        },
        "weights": best_weights,
        "biases": best_biases,
        "history": history,
    }
    return result, model


def _prediction_evidence(
    data: dict[str, Any],
    model: dict[str, Any],
    input_columns: list[str],
    geometry_columns: list[str],
    max_prediction_rows: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    x_norm = np.asarray(data["x"], dtype=float)
    y_norm = np.asarray(data["y"], dtype=float)
    test_indices = np.asarray(data["split"]["test"], dtype=int)
    normalization = data["normalization"]
    x_mean = np.asarray(normalization["x_mean"], dtype=float)
    x_scale = np.asarray(normalization["x_scale"], dtype=float)
    y_mean = np.asarray(normalization["y_mean"], dtype=float)
    y_scale = np.asarray(normalization["y_scale"], dtype=float)

    pred_norm = _mlp_predict(x_norm[test_indices], model["weights"], model["biases"])
    truth = y_norm[test_indices] * y_scale[None, :] + y_mean[None, :]
    pred = pred_norm * y_scale[None, :] + y_mean[None, :]
    all_geometry = y_norm * y_scale[None, :] + y_mean[None, :]
    spans = np.maximum(np.max(all_geometry, axis=0) - np.min(all_geometry, axis=0), 1.0e-12)
    errors = pred - truth
    abs_errors = np.abs(errors)
    normalized_abs_errors = abs_errors / spans[None, :]

    geometry_error_rows: list[dict[str, Any]] = []
    for column_index, column in enumerate(geometry_columns):
        squared = errors[:, column_index] ** 2
        truth_column = truth[:, column_index]
        total_variance = float(np.sum((truth_column - np.mean(truth_column)) ** 2))
        r2 = None if total_variance <= 1.0e-18 else 1.0 - float(np.sum(squared)) / total_variance
        geometry_error_rows.append(
            {
                "geometry_column": column,
                "span": float(spans[column_index]),
                "mae": float(np.mean(abs_errors[:, column_index])),
                "rmse": float(np.sqrt(np.mean(squared))),
                "normalized_mae": float(np.mean(normalized_abs_errors[:, column_index])),
                "normalized_rmse": float(np.sqrt(np.mean(normalized_abs_errors[:, column_index] ** 2))),
                "r2": r2,
            }
        )

    source_indices = list(data.get("source_indices") or [])
    prediction_rows: list[dict[str, Any]] = []
    limit = min(max(0, int(max_prediction_rows)), len(test_indices))
    x_physical = x_norm[test_indices[:limit]] * x_scale[None, :] + x_mean[None, :]
    for local_index, matrix_index in enumerate(test_indices[:limit]):
        row: dict[str, Any] = {
            "test_index": local_index,
            "matrix_index": int(matrix_index),
            "source_row_index": int(source_indices[int(matrix_index)]) if source_indices else int(matrix_index),
        }
        for column_index, column in enumerate(input_columns):
            row[column] = float(x_physical[local_index, column_index])
        for column_index, column in enumerate(geometry_columns):
            suffix = column.removeprefix("geom__")
            row[f"true__{suffix}"] = float(truth[local_index, column_index])
            row[f"pred__{suffix}"] = float(pred[local_index, column_index])
            row[f"abs_error__{suffix}"] = float(abs_errors[local_index, column_index])
            row[f"normalized_abs_error__{suffix}"] = float(normalized_abs_errors[local_index, column_index])
        prediction_rows.append(row)

    test_evidence = {
        "test_row_count": int(len(test_indices)),
        "saved_prediction_row_count": int(len(prediction_rows)),
        "mean_normalized_mae": float(np.mean([row["normalized_mae"] for row in geometry_error_rows])),
        "max_normalized_mae": float(max(row["normalized_mae"] for row in geometry_error_rows)),
        "mean_normalized_rmse": float(np.mean([row["normalized_rmse"] for row in geometry_error_rows])),
        "max_normalized_rmse": float(max(row["normalized_rmse"] for row in geometry_error_rows)),
    }
    return prediction_rows, geometry_error_rows, test_evidence


def _init_mlp(input_dim: int, output_dim: int, depth: int, width: int, rng: np.random.Generator) -> tuple[list[np.ndarray], list[np.ndarray]]:
    sizes = [input_dim] + [width] * depth + [output_dim]
    weights = []
    biases = []
    for fan_in, fan_out in zip(sizes[:-1], sizes[1:]):
        scale = math.sqrt(2.0 / max(1, fan_in))
        weights.append(rng.normal(0.0, scale, size=(fan_in, fan_out)))
        biases.append(np.zeros(fan_out, dtype=float))
    return weights, biases


def _init_adam(weights: list[np.ndarray], biases: list[np.ndarray]) -> dict[str, Any]:
    return {
        "t": 0,
        "mw": [np.zeros_like(item) for item in weights],
        "vw": [np.zeros_like(item) for item in weights],
        "mb": [np.zeros_like(item) for item in biases],
        "vb": [np.zeros_like(item) for item in biases],
    }


def _mlp_gradients(
    x: np.ndarray,
    y: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    dropout: float,
    weight_decay: float,
    rng: np.random.Generator,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    activations = [x]
    preacts: list[np.ndarray] = []
    dropout_masks: list[np.ndarray | None] = []
    out = x
    for layer, (weight, bias) in enumerate(zip(weights, biases)):
        z = out @ weight + bias
        preacts.append(z)
        if layer == len(weights) - 1:
            out = z
            dropout_masks.append(None)
        else:
            out = _gelu(z)
            if dropout > 0.0:
                keep = max(1.0e-6, 1.0 - float(dropout))
                mask = (rng.random(out.shape) < keep).astype(float) / keep
                out = out * mask
                dropout_masks.append(mask)
            else:
                dropout_masks.append(None)
        activations.append(out)
    delta = (2.0 / max(1, x.shape[0])) * (activations[-1] - y)
    grads_w = [np.zeros_like(item) for item in weights]
    grads_b = [np.zeros_like(item) for item in biases]
    for layer in reversed(range(len(weights))):
        grads_w[layer] = activations[layer].T @ delta + float(weight_decay) * weights[layer]
        grads_b[layer] = np.sum(delta, axis=0)
        if layer > 0:
            delta = delta @ weights[layer].T
            mask = dropout_masks[layer - 1]
            if mask is not None:
                delta = delta * mask
            delta = delta * _gelu_derivative(preacts[layer - 1])
    return grads_w, grads_b


def _adam_step(
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    grads_w: list[np.ndarray],
    grads_b: list[np.ndarray],
    adam: dict[str, Any],
    learning_rate: float,
) -> None:
    beta1 = 0.9
    beta2 = 0.999
    eps = 1.0e-8
    adam["t"] += 1
    t = int(adam["t"])
    for idx in range(len(weights)):
        adam["mw"][idx] = beta1 * adam["mw"][idx] + (1.0 - beta1) * grads_w[idx]
        adam["vw"][idx] = beta2 * adam["vw"][idx] + (1.0 - beta2) * (grads_w[idx] * grads_w[idx])
        adam["mb"][idx] = beta1 * adam["mb"][idx] + (1.0 - beta1) * grads_b[idx]
        adam["vb"][idx] = beta2 * adam["vb"][idx] + (1.0 - beta2) * (grads_b[idx] * grads_b[idx])
        mw_hat = adam["mw"][idx] / (1.0 - beta1**t)
        vw_hat = adam["vw"][idx] / (1.0 - beta2**t)
        mb_hat = adam["mb"][idx] / (1.0 - beta1**t)
        vb_hat = adam["vb"][idx] / (1.0 - beta2**t)
        weights[idx] -= float(learning_rate) * mw_hat / (np.sqrt(vw_hat) + eps)
        biases[idx] -= float(learning_rate) * mb_hat / (np.sqrt(vb_hat) + eps)


def _mlp_predict(x: np.ndarray, weights: list[np.ndarray], biases: list[np.ndarray]) -> np.ndarray:
    out = x
    for idx, (weight, bias) in enumerate(zip(weights, biases)):
        out = out @ weight + bias
        if idx < len(weights) - 1:
            out = _gelu(out)
    return out


def _metrics(x: np.ndarray, y: np.ndarray, split: dict[str, np.ndarray], weights: list[np.ndarray], biases: list[np.ndarray]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, indices in split.items():
        pred = _mlp_predict(x[indices], weights, biases)
        out[f"{name}_normalized_rmse"] = _rmse(pred, y[indices])
        out[f"{name}_normalized_mae"] = float(np.mean(np.abs(pred - y[indices])))
        per_geom = np.sqrt(np.mean((pred - y[indices]) ** 2, axis=0))
        out[f"{name}_max_per_geometry_normalized_rmse"] = float(np.max(per_geom))
    return out


def _geometry_envelope_violation_metrics(
    y: np.ndarray,
    split: dict[str, np.ndarray],
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    x: np.ndarray,
) -> dict[str, Any]:
    """Measure test predictions outside the observed training envelope.

    This is an empirical extrapolation diagnostic, not a DRC check. Values are
    normalized with training statistics, so per-dimension min/max comparisons
    are equivalent to comparisons in physical units.
    """

    train_indices = np.asarray(split["train"], dtype=int)
    test_indices = np.asarray(split["test"], dtype=int)
    train_y = np.asarray(y[train_indices], dtype=float)
    pred_y = _mlp_predict(np.asarray(x[test_indices], dtype=float), weights, biases)
    lower = np.min(train_y, axis=0)
    upper = np.max(train_y, axis=0)
    violations = (pred_y < lower[None, :]) | (pred_y > upper[None, :])
    return {
        "geometry_bound_violation_rate": float(np.mean(np.any(violations, axis=1))),
        "geometry_bound_violation_element_rate": float(np.mean(violations)),
        "geometry_bound_definition": "observed_training_min_max_per_geometry_dimension_not_drc",
    }


def _rmse(pred: np.ndarray, truth: np.ndarray) -> float:
    return float(np.sqrt(np.mean((pred - truth) ** 2)))


def _gelu(x: np.ndarray) -> np.ndarray:
    c = math.sqrt(2.0 / math.pi)
    return 0.5 * x * (1.0 + np.tanh(c * (x + 0.044715 * x**3)))


def _gelu_derivative(x: np.ndarray) -> np.ndarray:
    c = math.sqrt(2.0 / math.pi)
    u = c * (x + 0.044715 * x**3)
    tanh_u = np.tanh(u)
    sech2 = 1.0 - tanh_u * tanh_u
    return 0.5 * (1.0 + tanh_u) + 0.5 * x * sech2 * c * (1.0 + 3.0 * 0.044715 * x**2)


def _is_better_result(candidate: dict[str, Any], current: Any) -> bool:
    if not isinstance(current, dict):
        return True
    keys = ("validation_normalized_rmse", "geometry_bound_violation_rate", "test_normalized_rmse")
    return tuple(float(candidate.get(key, math.inf)) for key in keys) < tuple(float(current.get(key, math.inf)) for key in keys)


def _candidate_ids_unique(candidates: list[dict[str, str]]) -> bool:
    identifiers = [str(candidate.get("candidate_id") or "").strip() for candidate in candidates]
    return bool(identifiers) and all(identifiers) and len(set(identifiers)) == len(identifiers)


def _candidate_contract_fingerprint(
    *,
    training_csv: Path,
    candidate: dict[str, str],
    input_columns: list[str],
    geometry_columns: list[str],
    split_audit: dict[str, Any],
    normalization: dict[str, Any],
    args: argparse.Namespace,
) -> str:
    payload = {
        "schema_version": 1,
        "script_sha256": _sha256(Path(__file__).resolve()),
        "numpy_version": np.__version__,
        "training_csv_sha256": _sha256(training_csv),
        "candidate": candidate,
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "split_audit": split_audit,
        "normalization": normalization,
        "training_limits": {
            "max_epochs_cap": int(args.max_epochs_cap),
            "patience_cap": int(args.patience_cap),
            "max_validation_normalized_rmse": _finite_float_or_text(args.max_validation_normalized_rmse),
            "normalization_floor": float(args.normalization_floor),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_checkpoint_dir(root: Path, candidate: dict[str, str]) -> Path:
    identifier = str(candidate.get("candidate_id") or "candidate").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", identifier).strip("._") or "candidate"
    suffix = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:12]
    return root / f"{safe[:64]}__{suffix}"


def _save_candidate_checkpoint(
    root: Path,
    candidate: dict[str, str],
    contract_fingerprint: str,
    result: dict[str, Any],
    model: dict[str, Any],
) -> None:
    checkpoint = _candidate_checkpoint_dir(root, candidate)
    checkpoint.mkdir(parents=True, exist_ok=True)
    weights_path = checkpoint / "candidate_model_weights.npz"
    weights_tmp = checkpoint / "candidate_model_weights.tmp.npz"
    model_path = checkpoint / "candidate_model.json"
    history_path = checkpoint / "candidate_history.csv"
    marker_path = checkpoint / "candidate_checkpoint.complete.json"

    _save_weights(weights_tmp, model)
    weights_tmp.replace(weights_path)
    model_payload = {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "contract_fingerprint_sha256": contract_fingerprint,
        "candidate": candidate,
        "result": result,
        "model": _json_safe_model(model),
    }
    _atomic_write_json(model_path, model_payload)
    _write_csv_atomic(history_path, list(model.get("history") or []))
    marker = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "contract_fingerprint_sha256": contract_fingerprint,
        "weights_path": str(weights_path),
        "weights_sha256": _sha256(weights_path),
        "model_path": str(model_path),
        "model_sha256": _sha256(model_path),
        "history_path": str(history_path),
        "history_sha256": _sha256(history_path),
        "status": "PASS",
    }
    _atomic_write_json(marker_path, marker)


def _load_candidate_checkpoint(
    root: Path,
    candidate: dict[str, str],
    contract_fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    checkpoint = _candidate_checkpoint_dir(root, candidate)
    marker_path = checkpoint / "candidate_checkpoint.complete.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if marker.get("status") != "PASS" or marker.get("contract_fingerprint_sha256") != contract_fingerprint:
            return None
        weights_path = Path(str(marker["weights_path"]))
        model_path = Path(str(marker["model_path"]))
        history_path = Path(str(marker["history_path"]))
        if not all(path.is_file() for path in (weights_path, model_path, history_path)):
            return None
        if _sha256(weights_path) != marker.get("weights_sha256"):
            return None
        if _sha256(model_path) != marker.get("model_sha256"):
            return None
        if _sha256(history_path) != marker.get("history_sha256"):
            return None
        payload = json.loads(model_path.read_text(encoding="utf-8"))
        if payload.get("contract_fingerprint_sha256") != contract_fingerprint:
            return None
        candidate_id = str(candidate.get("candidate_id") or "")
        if payload.get("candidate_id") != candidate_id:
            return None
        with np.load(weights_path, allow_pickle=False) as arrays:
            weight_keys = sorted(
                (key for key in arrays.files if key.startswith("weight_")),
                key=lambda key: int(key.removeprefix("weight_")),
            )
            bias_keys = sorted(
                (key for key in arrays.files if key.startswith("bias_")),
                key=lambda key: int(key.removeprefix("bias_")),
            )
            weights = [np.asarray(arrays[key], dtype=float).copy() for key in weight_keys]
            biases = [np.asarray(arrays[key], dtype=float).copy() for key in bias_keys]
        if not weights or len(weights) != len(biases):
            return None
        result = dict(payload["result"])
        model = dict(payload["model"])
        model["weights"] = weights
        model["biases"] = biases
        return result, model
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_progress_snapshot(
    out_dir: Path,
    results: list[dict[str, Any]],
    checkpoint_root: Path,
    candidates: list[dict[str, str]],
    resumed_candidate_count: int,
    trained_this_run_count: int,
) -> None:
    partial_results = out_dir / "physical_feature_inverse_nn_architecture_search_partial_results.csv"
    progress_path = out_dir / "physical_feature_inverse_nn_architecture_search_progress.json"
    _write_csv_atomic(partial_results, results)
    expected_candidate_count = len(candidates)
    progress = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "COMPLETE" if len(results) == expected_candidate_count else "IN_PROGRESS",
        "completed_candidate_count": len(results),
        "expected_candidate_count": expected_candidate_count,
        "resumed_candidate_count": int(resumed_candidate_count),
        "trained_this_run_count": int(trained_this_run_count),
        "last_completed_candidate_id": str(results[-1].get("candidate_id") or "") if results else "",
        "candidate_checkpoint_root": str(checkpoint_root),
        "persisted_candidate_checkpoint_count": _candidate_checkpoint_count(checkpoint_root, candidates),
        "partial_results_csv": str(partial_results),
        "partial_results_sha256": _sha256(partial_results),
        "scientific_boundary": (
            "This is an atomic progress record, not a completed architecture-search result. Candidate reuse is allowed "
            "only when the full data/code/split/normalization contract fingerprint matches."
        ),
    }
    _atomic_write_json(progress_path, progress)


def _candidate_checkpoint_count(root: Path, candidates: list[dict[str, str]]) -> int:
    return sum(
        (_candidate_checkpoint_dir(root, candidate) / "candidate_checkpoint.complete.json").is_file()
        for candidate in candidates
    )


def _finite_float_or_text(value: Any) -> float | str:
    parsed = float(value)
    return parsed if math.isfinite(parsed) else str(parsed)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    _write_csv(temporary, rows)
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _save_weights(path: Path, model: dict[str, Any]) -> None:
    arrays = {}
    for idx, weight in enumerate(model["weights"]):
        arrays[f"weight_{idx}"] = weight
    for idx, bias in enumerate(model["biases"]):
        arrays[f"bias_{idx}"] = bias
    np.savez_compressed(path, **arrays)


def _json_safe_model(model: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in model.items()
        if key not in {"weights", "biases"}
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _render_report(summary: dict[str, Any]) -> str:
    selected = summary.get("selected_candidate") if isinstance(summary.get("selected_candidate"), dict) else {}
    lines = [
        "# Physical-Feature Inverse NN Architecture Search Training",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Training rows: `{summary['training_count']}`",
        f"- Candidates trained: `{summary['trained_candidate_count']}`",
        f"- Selected candidate: `{selected.get('candidate_id', '')}`",
        f"- Validation normalized RMSE: `{selected.get('validation_normalized_rmse', '')}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- {check['status']}: {check['name']} - {check['detail']}" for check in summary["checks"])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _decision(status: str) -> str:
    return {
        "PASS": "SELECT_NN_ARCHITECTURE_FOR_THIS_100K_CHUNK",
        "WAITING_FOR_TRAINING_CSV": "WAIT_FOR_100K_CHUNK_PHYSICAL_FEATURE_TRAINING_TABLE",
        "WAITING_FOR_COMPLETE_100K_CHUNK": "WAIT_FOR_COMPLETE_100K_CHUNK_BEFORE_NN_TRAINING",
        "FAIL": "FIX_NN_ARCHITECTURE_SEARCH_INPUTS",
    }.get(status, "FIX_NN_ARCHITECTURE_SEARCH_INPUTS")


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _float_value(value: Any, default: float) -> float:
    out = _as_float(value)
    return float(default) if out is None else out


def _int_value(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _check(name: str, passed: bool, detail: Any) -> Check:
    return Check("PASS" if passed else "FAIL", name, str(detail))


if __name__ == "__main__":
    raise SystemExit(main())
