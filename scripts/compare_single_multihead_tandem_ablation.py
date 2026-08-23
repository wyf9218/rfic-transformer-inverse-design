#!/usr/bin/env python3
"""Audit a same-data single-head versus multi-head tandem ablation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


SHARED_ARGUMENTS = (
    "validation_fraction",
    "test_fraction",
    "split_mode",
    "physical_cell_bins",
    "physical_cell_lower",
    "physical_cell_upper",
    "seed",
    "split_seed",
    "forward_depth",
    "forward_width",
    "inverse_depth",
    "inverse_width",
    "batch_size",
    "forward_epochs",
    "inverse_epochs",
    "patience",
    "learning_rate",
    "weight_decay",
    "geometry_anchor_weight",
    "topology_feasibility_weight",
    "response_loss_scaling",
    "response_loss_family",
    "normalization_floor",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    single_path = Path(args.single_summary).expanduser().resolve()
    multi_path = Path(args.multihead_summary).expanduser().resolve()
    single_predictions_path = Path(args.single_predictions).expanduser().resolve()
    multi_candidates_path = Path(args.multihead_candidates).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "single_multihead_tandem_ablation_summary.json"
    report_path = out_dir / "single_multihead_tandem_ablation_report.md"

    single = _read_json(single_path)
    multi = _read_json(multi_path)
    single_rows = _read_csv(single_predictions_path)
    multi_rows = _read_csv(multi_candidates_path)
    single_weights_path = _artifact_path(single, "weights_npz")
    multi_weights_path = _artifact_path(multi, "weights_npz")
    input_columns = list(single.get("input_columns") or [])
    geometry_columns = list(single.get("geometry_columns") or [])
    single_split = single.get("split_audit") or {}
    multi_split = multi.get("split_audit") or {}
    single_args = single.get("arguments") or {}
    multi_args = multi.get("arguments") or {}

    paired, pairing_errors = _pair_predictions(
        single_rows,
        multi_rows,
        input_columns,
        int((multi.get("method") or {}).get("head_count") or 0),
    )
    explicit_range = _explicit_range(single_split, multi_split, len(input_columns))
    metrics = _comparison_metrics(paired, input_columns, explicit_range, args)
    weights = _weight_contract(single_weights_path, multi_weights_path)
    actual_training_sha = _actual_shared_training_sha(single, multi)
    shared_argument_mismatches = [
        name for name in SHARED_ARGUMENTS if single_args.get(name) != multi_args.get(name)
    ]

    checks = {
        "single_summary_exists": single_path.is_file(),
        "multihead_summary_exists": multi_path.is_file(),
        "single_predictions_exist": single_predictions_path.is_file(),
        "multihead_candidates_exist": multi_candidates_path.is_file(),
        "both_runs_complete_reviewable": single.get("overall_status")
        in {"PASS", "COMPLETE_REVIEW_REQUIRED"}
        and multi.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"},
        "same_nonzero_training_count": int(single.get("training_count") or 0) > 0
        and int(single.get("training_count") or 0) == int(multi.get("training_count") or -1),
        "same_training_csv_sha256": _valid_sha256(single.get("training_csv_sha256"))
        and single.get("training_csv_sha256") == multi.get("training_csv_sha256"),
        "actual_training_csv_matches_summary_sha256": actual_training_sha.get("status") == "PASS",
        "same_input_columns": len(input_columns) == 4
        and input_columns == list(multi.get("input_columns") or []),
        "same_geometry_columns": len(geometry_columns) > 0
        and geometry_columns == list(multi.get("geometry_columns") or []),
        "same_split_fingerprint": _valid_sha256(single_split.get("split_fingerprint_sha256"))
        and single_split.get("split_fingerprint_sha256")
        == multi_split.get("split_fingerprint_sha256"),
        "same_physical_cell_partition": _valid_sha256(
            single_split.get("physical_cell_partition_fingerprint_sha256")
        )
        and single_split.get("physical_cell_partition_fingerprint_sha256")
        == multi_split.get("physical_cell_partition_fingerprint_sha256"),
        "same_explicit_physical_range": explicit_range is not None,
        "same_shared_training_arguments": not shared_argument_mismatches,
        "both_evaluation_isolation_pass": _isolation_pass(single) and _isolation_pass(multi),
        "forward_weights_exist_and_match_exactly": weights.get("forward_exact_match") is True,
        "prediction_artifact_hashes_match_summaries": _prediction_hashes_match(
            single, multi, single_predictions_path, multi_candidates_path
        ),
        "paired_test_rows_complete_and_identical": not pairing_errors
        and len(paired) == _summary_test_count(single) == _summary_test_count(multi),
        "recomputed_metrics_finite": metrics.get("status") == "PASS",
        "recomputed_point_metrics_match_summaries": _summary_point_metrics_match(
            single, multi, metrics, float(args.metric_consistency_tolerance)
        ),
    }
    contract_pass = all(checks.values())
    training_count = int(single.get("training_count") or 0)
    formal_evidence = (
        contract_pass
        and training_count >= int(args.minimum_training_rows)
        and int(metrics.get("paired_test_row_count") or 0) >= int(args.minimum_paired_test_rows)
        and int(metrics.get("paired_physical_cell_count") or 0)
        >= int(args.minimum_paired_test_cells)
    )
    head_use = _head_utilization(multi)
    gates = _promotion_review_gates(metrics, head_use, args) if formal_evidence else {}

    if not contract_pass:
        overall_status = "FAIL"
        decision = "FIX_SINGLE_MULTIHEAD_ABLATION_CONTRACT"
    elif not formal_evidence:
        overall_status = "PASS"
        decision = "INTERFACE_ONLY_NO_MODEL_PROMOTION"
    elif all(gates.values()):
        overall_status = "PASS"
        decision = "REVIEW_MULTIHEAD_FOR_FIXED_BUDGET_REAL_EMX_CLOSURE"
    else:
        overall_status = "PASS"
        decision = "RETAIN_SINGLEHEAD_BASELINE_MULTIHEAD_GATES_NOT_MET"

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "checks": checks,
        "contract_errors": pairing_errors,
        "shared_argument_mismatches": shared_argument_mismatches,
        "training_count": training_count,
        "formal_evidence": formal_evidence,
        "formal_evidence_minimums": {
            "training_rows": int(args.minimum_training_rows),
            "paired_test_rows": int(args.minimum_paired_test_rows),
            "paired_physical_cells": int(args.minimum_paired_test_cells),
        },
        "metrics": metrics,
        "head_utilization": head_use,
        "review_gates": gates,
        "weight_contract": weights,
        "actual_training_sha256": actual_training_sha,
        "comparison_contract": {
            "training_csv_sha256": single.get("training_csv_sha256"),
            "split_fingerprint_sha256": single_split.get("split_fingerprint_sha256"),
            "physical_cell_partition_fingerprint_sha256": single_split.get(
                "physical_cell_partition_fingerprint_sha256"
            ),
            "input_columns": input_columns,
            "geometry_columns": geometry_columns,
            "shared_arguments": {name: single_args.get(name) for name in SHARED_ARGUMENTS},
            "model_seed": single_args.get("seed"),
            "split_seed": single_args.get("split_seed"),
            "head_count": int((multi.get("method") or {}).get("head_count") or 0),
        },
        "artifacts": {
            "single_summary": str(single_path),
            "multihead_summary": str(multi_path),
            "single_predictions": str(single_predictions_path),
            "multihead_candidates": str(multi_candidates_path),
            "single_weights": str(single_weights_path) if single_weights_path else "",
            "multihead_weights": str(multi_weights_path) if multi_weights_path else "",
            "report": str(report_path),
        },
        "scientific_boundary": (
            "This audit compares frozen-forward proxy consistency on one shared physical-cell OOD test set. "
            "It never promotes a model automatically. Formal review also requires at least five training seeds, "
            "production geometry audit, foundry DRC, fixed-budget new real EMX closure, and sampled HFSS correlation. "
            "Proxy-consistent candidates are not new labels and do not count toward dataset uniformity."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single-summary", required=True)
    parser.add_argument("--multihead-summary", required=True)
    parser.add_argument("--single-predictions", required=True)
    parser.add_argument("--multihead-candidates", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--minimum-training-rows", type=int, default=100_000)
    parser.add_argument("--minimum-paired-test-rows", type=int, default=1_000)
    parser.add_argument("--minimum-paired-test-cells", type=int, default=8)
    parser.add_argument("--minimum-material-improvement", type=float, default=0.05)
    parser.add_argument("--maximum-per-feature-regression", type=float, default=0.05)
    parser.add_argument("--minimum-head-utilization-entropy", type=float, default=0.80)
    parser.add_argument("--bootstrap-replicates", type=int, default=2_000)
    parser.add_argument("--bootstrap-confidence", type=float, default=0.95)
    parser.add_argument("--bootstrap-seed", type=int, default=20260712)
    parser.add_argument("--metric-consistency-tolerance", type=float, default=1.0e-10)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if min(
        int(args.minimum_training_rows),
        int(args.minimum_paired_test_rows),
        int(args.minimum_paired_test_cells),
    ) < 1:
        parser.error("minimum evidence counts must be positive")
    if int(args.bootstrap_replicates) < 100:
        parser.error("--bootstrap-replicates must be at least 100")
    if not 0.0 < float(args.bootstrap_confidence) < 1.0:
        parser.error("--bootstrap-confidence must be in (0, 1)")
    if not 0.0 <= float(args.minimum_material_improvement) < 1.0:
        parser.error("--minimum-material-improvement must be in [0, 1)")
    if not 0.0 <= float(args.maximum_per_feature_regression) < 1.0:
        parser.error("--maximum-per-feature-regression must be in [0, 1)")
    if not 0.0 <= float(args.minimum_head_utilization_entropy) <= 1.0:
        parser.error("--minimum-head-utilization-entropy must be in [0, 1]")
    return args


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _artifact_path(summary: dict[str, Any], key: str) -> Path | None:
    value = str(summary.get(key) or "").strip()
    return Path(value).expanduser().resolve() if value else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text.lower())


def _actual_shared_training_sha(single: dict[str, Any], multi: dict[str, Any]) -> dict[str, Any]:
    paths = [Path(str(item.get("training_csv") or "")).expanduser().resolve() for item in (single, multi)]
    if not all(path.is_file() for path in paths):
        return {"status": "FAIL", "reason": "training CSV missing", "paths": [str(path) for path in paths]}
    hashes = [_sha256_file(path) for path in paths]
    declared = [str(item.get("training_csv_sha256") or "") for item in (single, multi)]
    status = "PASS" if hashes[0] == hashes[1] == declared[0] == declared[1] else "FAIL"
    return {"status": status, "paths": [str(path) for path in paths], "sha256": hashes}


def _isolation_pass(summary: dict[str, Any]) -> bool:
    isolation = summary.get("evaluation_isolation") or {}
    checks = isolation.get("checks") or {}
    return isolation.get("overall_status") == "PASS" and bool(checks) and all(checks.values())


def _prediction_hashes_match(
    single: dict[str, Any], multi: dict[str, Any], single_path: Path, multi_path: Path
) -> bool:
    if not single_path.is_file() or not multi_path.is_file():
        return False
    return (
        _sha256_file(single_path) == single.get("test_predictions_csv_sha256")
        and _sha256_file(multi_path) == multi.get("test_candidates_csv_sha256")
    )


def _summary_test_count(summary: dict[str, Any]) -> int:
    try:
        return int(((summary.get("metrics") or {}).get("test_row_count")))
    except (TypeError, ValueError):
        return -1


def _explicit_range(
    single_split: dict[str, Any], multi_split: dict[str, Any], dimensions: int
) -> tuple[np.ndarray, np.ndarray, int] | None:
    keys = ("physical_cell_lower", "physical_cell_upper")
    try:
        lower_a, upper_a = (np.asarray(single_split[key], dtype=float) for key in keys)
        lower_b, upper_b = (np.asarray(multi_split[key], dtype=float) for key in keys)
        bins_a = int(single_split.get("physical_cell_bins_per_dimension"))
        bins_b = int(multi_split.get("physical_cell_bins_per_dimension"))
    except (KeyError, TypeError, ValueError):
        return None
    if (
        lower_a.shape != (dimensions,)
        or upper_a.shape != (dimensions,)
        or not np.array_equal(lower_a, lower_b)
        or not np.array_equal(upper_a, upper_b)
        or bins_a != bins_b
        or bins_a < 2
        or np.any(upper_a <= lower_a)
    ):
        return None
    return lower_a, upper_a, bins_a


def _pair_predictions(
    single_rows: list[dict[str, str]],
    multi_rows: list[dict[str, str]],
    input_columns: list[str],
    head_count: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    single_by_id: dict[str, dict[str, str]] = {}
    for row in single_rows:
        identity = str(row.get("source_geometry_identity_sha256") or "")
        if not _valid_sha256(identity) or identity in single_by_id:
            errors.append("single predictions contain invalid or duplicate geometry identity")
            continue
        single_by_id[identity] = row
    multi_by_id: dict[str, list[dict[str, str]]] = {}
    for row in multi_rows:
        identity = str(row.get("source_geometry_identity_sha256") or "")
        if not _valid_sha256(identity):
            errors.append("multihead candidates contain invalid geometry identity")
            continue
        multi_by_id.setdefault(identity, []).append(row)
    if set(single_by_id) != set(multi_by_id):
        errors.append("single and multihead test geometry identity sets differ")
    paired: list[dict[str, Any]] = []
    feature_names = [str(column).removeprefix("input__") for column in input_columns]
    for identity in sorted(set(single_by_id) & set(multi_by_id)):
        single = single_by_id[identity]
        candidates = multi_by_id[identity]
        if len(candidates) != head_count or {int(row.get("head_index", -1)) for row in candidates} != set(
            range(head_count)
        ):
            errors.append(f"multihead candidate contract differs for {identity}")
            continue
        selected = [row for row in candidates if str(row.get("selected_best_of_k", "")).lower() == "true"]
        if len(selected) != 1:
            errors.append(f"multihead selected-best contract differs for {identity}")
            continue
        multi = selected[0]
        try:
            single_target = np.asarray([float(single[f"target__{name}"]) for name in feature_names])
            multi_target = np.asarray([float(multi[f"target__{name}"]) for name in feature_names])
            single_reconstructed = np.asarray(
                [float(single[f"reconstructed__{name}"]) for name in feature_names]
            )
            multi_reconstructed = np.asarray(
                [float(multi[f"reconstructed__{name}"]) for name in feature_names]
            )
        except (KeyError, TypeError, ValueError):
            errors.append(f"prediction value parse failed for {identity}")
            continue
        if not np.array_equal(single_target, multi_target):
            errors.append(f"target values differ for {identity}")
            continue
        if single.get("matrix_index") != multi.get("matrix_index"):
            errors.append(f"matrix index differs for {identity}")
            continue
        paired.append(
            {
                "identity": identity,
                "target": single_target,
                "single": single_reconstructed,
                "multi": multi_reconstructed,
            }
        )
    return paired, sorted(set(errors))


def _comparison_metrics(
    paired: list[dict[str, Any]],
    input_columns: list[str],
    explicit_range: tuple[np.ndarray, np.ndarray, int] | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if not paired or explicit_range is None:
        return {"status": "FAIL"}
    lower, upper, bins = explicit_range
    targets = np.vstack([row["target"] for row in paired])
    single = np.vstack([row["single"] for row in paired])
    multi = np.vstack([row["multi"] for row in paired])
    spans = upper - lower
    single_error = (single - targets) / spans
    multi_error = (multi - targets) / spans
    single_sq = np.mean(single_error**2, axis=1)
    multi_sq = np.mean(multi_error**2, axis=1)
    cell_matrix = np.floor((targets - lower) / spans * bins).astype(int)
    cell_matrix = np.clip(cell_matrix, 0, bins - 1)
    cell_ids = [":".join(str(int(value)) for value in row) for row in cell_matrix]
    bootstrap = _paired_cell_bootstrap(single_sq, multi_sq, cell_ids, args)
    single_rmse = float(np.sqrt(np.mean(single_sq)))
    multi_rmse = float(np.sqrt(np.mean(multi_sq)))
    relative = float((single_rmse - multi_rmse) / max(single_rmse, 1.0e-12))
    single_mae = np.mean(np.abs(single - targets), axis=0)
    multi_mae = np.mean(np.abs(multi - targets), axis=0)
    per_feature = {}
    for index, column in enumerate(input_columns):
        regression = float((multi_mae[index] - single_mae[index]) / max(single_mae[index], 1.0e-12))
        per_feature[column] = {
            "single_physical_mae": float(single_mae[index]),
            "multihead_best_of_k_physical_mae": float(multi_mae[index]),
            "multihead_relative_regression": regression,
        }
    return {
        "status": "PASS"
        if all(math.isfinite(value) for value in (single_rmse, multi_rmse, relative))
        else "FAIL",
        "paired_test_row_count": len(paired),
        "paired_physical_cell_count": len(set(cell_ids)),
        "single_range_normalized_rmse": single_rmse,
        "multihead_best_of_k_range_normalized_rmse": multi_rmse,
        "multihead_relative_improvement": relative,
        "per_feature": per_feature,
        "paired_cell_bootstrap": bootstrap,
    }


def _summary_point_metrics_match(
    single: dict[str, Any], multi: dict[str, Any], metrics: dict[str, Any], tolerance: float
) -> bool:
    single_declared = _finite(
        (((single.get("metrics") or {}).get("tandem_inverse") or {}).get(
            "test_response_range_normalized_rmse"
        ))
    )
    multi_declared = _finite(
        (((multi.get("metrics") or {}).get("best_of_k") or {}).get(
            "response_range_normalized_rmse"
        ))
    )
    single_recomputed = _finite(metrics.get("single_range_normalized_rmse"))
    multi_recomputed = _finite(metrics.get("multihead_best_of_k_range_normalized_rmse"))
    return (
        None not in (single_declared, multi_declared, single_recomputed, multi_recomputed)
        and abs(float(single_declared) - float(single_recomputed)) <= tolerance
        and abs(float(multi_declared) - float(multi_recomputed)) <= tolerance
    )


def _paired_cell_bootstrap(
    single_sq: np.ndarray, multi_sq: np.ndarray, cell_ids: list[str], args: argparse.Namespace
) -> dict[str, Any]:
    cells = sorted(set(cell_ids))
    by_cell = {cell: np.flatnonzero(np.asarray(cell_ids) == cell) for cell in cells}
    if len(single_sq) < int(args.minimum_paired_test_rows) or len(cells) < int(
        args.minimum_paired_test_cells
    ):
        return {
            "status": "INSUFFICIENT_FORMAL_EVIDENCE",
            "paired_test_rows": len(single_sq),
            "paired_physical_cells": len(cells),
        }
    rng = np.random.default_rng(int(args.bootstrap_seed))
    row_gain: list[float] = []
    cell_gain: list[float] = []
    tail_gain: list[float] = []
    for _ in range(int(args.bootstrap_replicates)):
        sampled = rng.choice(cells, size=len(cells), replace=True)
        indices = np.concatenate([by_cell[str(cell)] for cell in sampled])
        single_row = math.sqrt(float(np.mean(single_sq[indices])))
        multi_row = math.sqrt(float(np.mean(multi_sq[indices])))
        single_cells = np.asarray([math.sqrt(float(np.mean(single_sq[by_cell[str(cell)]]))) for cell in sampled])
        multi_cells = np.asarray([math.sqrt(float(np.mean(multi_sq[by_cell[str(cell)]]))) for cell in sampled])
        single_cell = math.sqrt(float(np.mean(single_cells**2)))
        multi_cell = math.sqrt(float(np.mean(multi_cells**2)))
        single_tail = float(np.quantile(single_cells, 0.90))
        multi_tail = float(np.quantile(multi_cells, 0.90))
        row_gain.append((single_row - multi_row) / max(single_row, 1.0e-12))
        cell_gain.append((single_cell - multi_cell) / max(single_cell, 1.0e-12))
        tail_gain.append((single_tail - multi_tail) / max(single_tail, 1.0e-12))
    alpha = (1.0 - float(args.bootstrap_confidence)) / 2.0
    return {
        "status": "PASS",
        "replicates": int(args.bootstrap_replicates),
        "confidence": float(args.bootstrap_confidence),
        "row_weighted_relative_improvement_ci": _interval(row_gain, alpha),
        "cell_balanced_relative_improvement_ci": _interval(cell_gain, alpha),
        "p90_cell_tail_relative_improvement_ci": _interval(tail_gain, alpha),
    }


def _interval(values: list[float], alpha: float) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    return {
        "lower": float(np.quantile(array, alpha)),
        "median": float(np.quantile(array, 0.5)),
        "upper": float(np.quantile(array, 1.0 - alpha)),
    }


def _weight_contract(single_path: Path | None, multi_path: Path | None) -> dict[str, Any]:
    if single_path is None or multi_path is None or not single_path.is_file() or not multi_path.is_file():
        return {"status": "FAIL", "forward_exact_match": False}
    try:
        single = np.load(single_path)
        multi = np.load(multi_path)
        forward_keys = sorted(
            key for key in single.files if key.startswith(("forward_weight_", "forward_bias_"))
        )
        exact = bool(forward_keys) and all(
            key in multi.files and np.array_equal(single[key], multi[key]) for key in forward_keys
        )
        single_parameters = sum(
            single[key].size
            for key in single.files
            if key.startswith(("forward_weight_", "forward_bias_", "inverse_weight_", "inverse_bias_"))
        )
        multi_parameters = sum(
            multi[key].size
            for key in multi.files
            if key.startswith(("forward_weight_", "forward_bias_", "inverse_weight_", "inverse_bias_"))
        )
    except (OSError, ValueError):
        return {"status": "FAIL", "forward_exact_match": False}
    return {
        "status": "PASS" if exact else "FAIL",
        "forward_exact_match": exact,
        "forward_array_keys": forward_keys,
        "single_parameter_count": int(single_parameters),
        "multihead_parameter_count": int(multi_parameters),
        "multihead_parameter_overhead_fraction": float(
            (multi_parameters - single_parameters) / max(single_parameters, 1)
        ),
    }


def _head_utilization(multi: dict[str, Any]) -> dict[str, Any]:
    diversity = ((multi.get("metrics") or {}).get("diversity") or {})
    counts = [int(value) for value in (diversity.get("head_utilization_counts") or [])]
    entropy = _finite(diversity.get("head_utilization_entropy"))
    return {
        "counts": counts,
        "entropy": entropy,
        "all_heads_selected_at_least_once": bool(counts) and all(value > 0 for value in counts),
    }


def _promotion_review_gates(
    metrics: dict[str, Any], head_use: dict[str, Any], args: argparse.Namespace
) -> dict[str, bool]:
    bootstrap = metrics.get("paired_cell_bootstrap") or {}
    threshold = float(args.minimum_material_improvement)
    intervals = [
        bootstrap.get("row_weighted_relative_improvement_ci") or {},
        bootstrap.get("cell_balanced_relative_improvement_ci") or {},
        bootstrap.get("p90_cell_tail_relative_improvement_ci") or {},
    ]
    feature_regressions = [
        float(item.get("multihead_relative_regression"))
        for item in (metrics.get("per_feature") or {}).values()
    ]
    return {
        "all_bootstrap_improvement_lower_bounds_meet_threshold": bootstrap.get("status") == "PASS"
        and all(_finite(interval.get("lower")) is not None and float(interval["lower"]) >= threshold for interval in intervals),
        "no_per_feature_mae_regression_above_limit": bool(feature_regressions)
        and max(feature_regressions) <= float(args.maximum_per_feature_regression),
        "all_heads_selected_at_least_once": head_use.get("all_heads_selected_at_least_once") is True,
        "head_utilization_entropy_meets_threshold": _finite(head_use.get("entropy")) is not None
        and float(head_use["entropy"]) >= float(args.minimum_head_utilization_entropy),
    }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _render_report(payload: dict[str, Any]) -> str:
    metrics = payload.get("metrics") or {}
    lines = [
        "# Single-head versus multi-head tandem ablation audit",
        "",
        f"- Overall status: `{payload.get('overall_status')}`",
        f"- Decision: `{payload.get('decision')}`",
        f"- Training rows: {payload.get('training_count')}",
        f"- Formal evidence: {payload.get('formal_evidence')}",
        "",
        "## Paired metrics",
        "",
        f"- Test rows / physical cells: {metrics.get('paired_test_row_count')} / {metrics.get('paired_physical_cell_count')}",
        f"- Single-head range-normalized RMSE: {metrics.get('single_range_normalized_rmse')}",
        f"- Multi-head best-of-K range-normalized RMSE: {metrics.get('multihead_best_of_k_range_normalized_rmse')}",
        f"- Multi-head relative improvement: {metrics.get('multihead_relative_improvement')}",
        "",
        "## Contract",
        "",
    ]
    lines.extend(f"- {name}: {value}" for name, value in (payload.get("checks") or {}).items())
    lines.extend(["", "## Scientific boundary", "", str(payload.get("scientific_boundary") or ""), ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
