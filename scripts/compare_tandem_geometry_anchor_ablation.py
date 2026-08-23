#!/usr/bin/env python3
"""Compare anchored and response-only tandem inverse models on one OOD split."""

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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    anchored_path = Path(args.anchored_summary).expanduser().resolve()
    response_only_path = Path(args.response_only_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "tandem_geometry_anchor_ablation_summary.json"
    report_path = out_dir / "tandem_geometry_anchor_ablation_report.md"

    anchored = _read_json(anchored_path)
    response_only = _read_json(response_only_path)
    anchored_metric = _response_metric(anchored)
    response_only_metric = _response_metric(response_only)
    anchored_split = anchored.get("split_audit") or {}
    response_only_split = response_only.get("split_audit") or {}
    anchored_method = anchored.get("method") or {}
    response_only_method = response_only.get("method") or {}
    anchored_args = anchored.get("arguments") or {}
    response_only_args = response_only.get("arguments") or {}
    paired_bootstrap = _paired_cluster_bootstrap(anchored, response_only, args)

    checks = {
        "anchored_summary_exists": anchored_path.is_file(),
        "response_only_summary_exists": response_only_path.is_file(),
        "both_models_complete": anchored.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"}
        and response_only.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"},
        "same_training_count": int(anchored.get("training_count") or 0)
        == int(response_only.get("training_count") or -1),
        "same_input_columns": list(anchored.get("input_columns") or [])
        == list(response_only.get("input_columns") or []),
        "same_geometry_columns": list(anchored.get("geometry_columns") or [])
        == list(response_only.get("geometry_columns") or []),
        "same_split_fingerprint": bool(anchored_split.get("split_fingerprint_sha256"))
        and anchored_split.get("split_fingerprint_sha256")
        == response_only_split.get("split_fingerprint_sha256"),
        "same_split_cell_partition": bool(
            anchored_split.get("physical_cell_partition_fingerprint_sha256")
        )
        and anchored_split.get("physical_cell_partition_fingerprint_sha256")
        == response_only_split.get("physical_cell_partition_fingerprint_sha256"),
        "anchored_model_uses_geometry_label": anchored_method.get("geometry_label_used_in_inverse_objective")
        is True
        and _finite(anchored_method.get("geometry_anchor_weight"), positive=True) is not None,
        "response_only_model_uses_no_geometry_label": response_only_method.get(
            "geometry_label_used_in_inverse_objective"
        )
        is False
        and _finite(response_only_method.get("geometry_anchor_weight")) == 0.0,
        "response_only_has_no_zero_gradient_warmup": _finite(
            response_only_args.get("response_warmup_fraction")
        )
        == 0.0,
        "same_label_free_topology_feasibility_weight": _finite(
            anchored_method.get("topology_feasibility_weight")
        )
        == _finite(response_only_method.get("topology_feasibility_weight")),
        "same_label_free_topology_feasibility_contract": anchored_method.get(
            "topology_feasibility_is_label_free"
        )
        == response_only_method.get("topology_feasibility_is_label_free"),
        "response_metrics_finite": anchored_metric is not None and response_only_metric is not None,
    }
    if args.require_paired_bootstrap:
        checks["paired_cluster_bootstrap_pass"] = paired_bootstrap.get("status") == "PASS"
    elif paired_bootstrap.get("status") not in {"NOT_REQUESTED", "PASS"}:
        checks["requested_paired_cluster_bootstrap_pass"] = False
    comparable = all(checks.values())
    improvement = None
    if comparable and anchored_metric is not None and response_only_metric is not None:
        improvement = (anchored_metric - response_only_metric) / max(anchored_metric, 1.0e-12)
        threshold = float(args.minimum_material_improvement)
        if args.require_paired_bootstrap:
            lower = _finite(paired_bootstrap.get("relative_improvement_ci_lower"))
            upper = _finite(paired_bootstrap.get("relative_improvement_ci_upper"))
            cell_lower = _finite(paired_bootstrap.get("cell_balanced_relative_improvement_ci_lower"))
            cell_upper = _finite(paired_bootstrap.get("cell_balanced_relative_improvement_ci_upper"))
            if lower is not None and cell_lower is not None and min(lower, cell_lower) >= threshold:
                decision = "REVIEW_RESPONSE_ONLY_FOR_REAL_EMX_CLOSURE"
            elif upper is not None and cell_upper is not None and max(upper, cell_upper) <= -threshold:
                decision = "RETAIN_ANCHORED_BASELINE_RESPONSE_ONLY_IS_MATERIALLY_WORSE"
            elif improvement >= threshold:
                decision = "RETAIN_ANCHORED_BASELINE_UNCERTAIN_RESPONSE_ONLY_GAIN"
            else:
                decision = "RETAIN_ANCHORED_BASELINE_NO_CONFIDENT_MATERIAL_RESPONSE_ONLY_GAIN"
        elif improvement >= threshold:
            decision = "REVIEW_RESPONSE_ONLY_FOR_REAL_EMX_CLOSURE"
        elif improvement <= -threshold:
            decision = "RETAIN_ANCHORED_BASELINE_RESPONSE_ONLY_IS_MATERIALLY_WORSE"
        else:
            decision = "RETAIN_ANCHORED_BASELINE_NO_MATERIAL_RESPONSE_ONLY_GAIN"
        status = "PASS"
    else:
        decision = "FIX_GEOMETRY_ANCHOR_ABLATION_CONTRACT"
        status = "FAIL"

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "checks": checks,
        "training_count": int(anchored.get("training_count") or 0),
        "comparison_metric": "physical-cell OOD declared-range-normalized Lp/Ls/Q/|K| response RMSE",
        "anchored_response_rmse": anchored_metric,
        "response_only_response_rmse": response_only_metric,
        "response_only_relative_improvement": improvement,
        "minimum_material_improvement": float(args.minimum_material_improvement),
        "decision_rule": (
            "paired_cluster_bootstrap_row_and_cell_balanced_ci_lower_ge_material_improvement"
            if args.require_paired_bootstrap
            else "legacy_point_estimate_material_improvement"
        ),
        "paired_cluster_bootstrap": paired_bootstrap,
        "anchored_geometry_anchor_weight": anchored_method.get("geometry_anchor_weight"),
        "response_only_geometry_anchor_weight": response_only_method.get("geometry_anchor_weight"),
        "shared_topology_feasibility_weight": anchored_method.get("topology_feasibility_weight"),
        "anchored_per_feature_range_normalized_mae": _per_feature_error(anchored),
        "response_only_per_feature_range_normalized_mae": _per_feature_error(response_only),
        "artifacts": {
            "anchored_summary": str(anchored_path),
            "response_only_summary": str(response_only_path),
            "anchored_predictions": str(Path(args.anchored_predictions).expanduser().resolve())
            if args.anchored_predictions
            else "",
            "response_only_predictions": str(Path(args.response_only_predictions).expanduser().resolve())
            if args.response_only_predictions
            else "",
            "report": str(report_path),
        },
        "scientific_boundary": (
            "This is a same-data, same-physical-cell-OOD ablation of geometry-label anchoring; both arms keep the "
            "same label-free topology-feasibility objective. "
            "It cannot change the active inverse model by itself. A response-only candidate still requires DRC, "
            "real EMX closed-loop verification, and sampled HFSS correlation. The paired cluster bootstrap "
            "quantifies held-out physical-cell sampling uncertainty for two fixed trained models; it does not "
            "capture training-seed variability, simulator bias, or fabrication uncertainty."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchored-summary", required=True)
    parser.add_argument("--response-only-summary", required=True)
    parser.add_argument("--anchored-predictions")
    parser.add_argument("--response-only-predictions")
    parser.add_argument("--require-paired-bootstrap", action="store_true")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--minimum-material-improvement", type=float, default=0.05)
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    parser.add_argument("--bootstrap-confidence", type=float, default=0.95)
    parser.add_argument("--bootstrap-seed", type=int, default=20260711)
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument("--physical-cell-lower", default="0.5,0.5,5,0")
    parser.add_argument("--physical-cell-upper", default="3,3,25,0.8")
    parser.add_argument("--minimum-paired-test-rows", type=int, default=1000)
    parser.add_argument("--minimum-paired-test-cells", type=int, default=8)
    parser.add_argument("--metric-consistency-tolerance", type=float, default=1.0e-10)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not 0.0 <= float(args.minimum_material_improvement) < 1.0:
        parser.error("--minimum-material-improvement must be in [0, 1)")
    if args.require_paired_bootstrap and not (args.anchored_predictions and args.response_only_predictions):
        parser.error("--require-paired-bootstrap requires both prediction CSV paths")
    if int(args.bootstrap_replicates) < 100:
        parser.error("--bootstrap-replicates must be at least 100")
    if not 0.0 < float(args.bootstrap_confidence) < 1.0:
        parser.error("--bootstrap-confidence must be in (0, 1)")
    if int(args.physical_cell_bins) < 2:
        parser.error("--physical-cell-bins must be at least 2")
    if int(args.minimum_paired_test_rows) < 1 or int(args.minimum_paired_test_cells) < 2:
        parser.error("paired bootstrap minimum rows/cells are invalid")
    if not math.isfinite(float(args.metric_consistency_tolerance)) or float(args.metric_consistency_tolerance) < 0.0:
        parser.error("--metric-consistency-tolerance must be finite and nonnegative")
    return args


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _finite(value: Any, *, positive: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (positive and number <= 0.0):
        return None
    return number


def _response_metric(data: dict[str, Any]) -> float | None:
    tandem = ((data.get("metrics") or {}).get("tandem_inverse") or {})
    return _finite(tandem.get("test_response_range_normalized_rmse"))


def _per_feature_error(data: dict[str, Any]) -> dict[str, Any]:
    value = ((data.get("metrics") or {}).get("per_feature_range_normalized_mae") or {})
    return value if isinstance(value, dict) else {}


def _paired_cluster_bootstrap(
    anchored: dict[str, Any],
    response_only: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    if not args.anchored_predictions and not args.response_only_predictions:
        return {
            "status": "NOT_REQUESTED",
            "decision_use": "LEGACY_POINT_ESTIMATE_ONLY",
            "boundary": "Formal 100k checkpoint execution requires paired physical-cell bootstrap evidence.",
        }
    anchored_path = Path(args.anchored_predictions or "").expanduser().resolve()
    response_path = Path(args.response_only_predictions or "").expanduser().resolve()
    errors: list[str] = []
    anchored_rows = _read_csv(anchored_path)
    response_rows = _read_csv(response_path)
    input_columns = list(anchored.get("input_columns") or [])
    if input_columns != list(response_only.get("input_columns") or []):
        errors.append("input columns differ")
    feature_names = [str(column).removeprefix("input__") for column in input_columns]
    if len(feature_names) != 4:
        errors.append(f"expected four physical features, got {feature_names}")
    anchored_by_id, anchored_index_errors = _prediction_index(anchored_rows)
    response_by_id, response_index_errors = _prediction_index(response_rows)
    errors.extend(anchored_index_errors)
    errors.extend(response_index_errors)
    anchored_ids = set(anchored_by_id)
    response_ids = set(response_by_id)
    if anchored_ids != response_ids:
        errors.append(
            f"paired matrix_index sets differ: anchored={len(anchored_ids)} response_only={len(response_ids)}"
        )
    paired_ids = sorted(anchored_ids & response_ids, key=_sortable_id)
    if len(paired_ids) < int(args.minimum_paired_test_rows):
        errors.append(f"paired rows={len(paired_ids)} minimum={args.minimum_paired_test_rows}")
    anchored_test_count = _summary_test_row_count(anchored)
    response_test_count = _summary_test_row_count(response_only)
    if anchored_test_count is None or response_test_count is None:
        errors.append("summary test_row_count is missing or invalid")
    elif anchored_test_count != response_test_count or len(paired_ids) != anchored_test_count:
        errors.append(
            "prediction CSVs do not cover the complete shared test set: "
            f"paired={len(paired_ids)} anchored_summary={anchored_test_count} "
            f"response_summary={response_test_count}"
        )

    spans_a = _range_spans(anchored, input_columns)
    spans_b = _range_spans(response_only, input_columns)
    if spans_a is None or spans_b is None or not np.array_equal(spans_a, spans_b):
        errors.append("declared range-normalization spans are missing or differ")
    spans = spans_a if spans_a is not None else np.ones(max(1, len(feature_names)), dtype=float)
    lower = _parse_vector(args.physical_cell_lower, len(feature_names))
    upper = _parse_vector(args.physical_cell_upper, len(feature_names))
    if lower is None or upper is None or np.any(upper <= lower):
        errors.append("physical-cell bounds are invalid")

    targets: list[list[float]] = []
    anchored_reconstructed: list[list[float]] = []
    response_reconstructed: list[list[float]] = []
    for row_id in paired_ids:
        row_a = anchored_by_id[row_id]
        row_b = response_by_id[row_id]
        target_a = [_row_float(row_a, f"target__{name}") for name in feature_names]
        target_b = [_row_float(row_b, f"target__{name}") for name in feature_names]
        reconstructed_a = [_row_float(row_a, f"reconstructed__{name}") for name in feature_names]
        reconstructed_b = [_row_float(row_b, f"reconstructed__{name}") for name in feature_names]
        if any(value is None for value in (*target_a, *target_b, *reconstructed_a, *reconstructed_b)):
            errors.append(f"missing/nonfinite target or reconstruction at matrix_index={row_id}")
            continue
        if not np.allclose(target_a, target_b, rtol=0.0, atol=1.0e-12):
            errors.append(f"paired targets differ at matrix_index={row_id}")
            continue
        targets.append([float(value) for value in target_a if value is not None])
        anchored_reconstructed.append([float(value) for value in reconstructed_a if value is not None])
        response_reconstructed.append([float(value) for value in reconstructed_b if value is not None])
    if len(targets) != len(paired_ids):
        errors.append(f"valid aligned rows={len(targets)} paired ids={len(paired_ids)}")

    if errors:
        return _bootstrap_failure(anchored_path, response_path, errors, len(paired_ids))

    target_array = np.asarray(targets, dtype=float)
    anchored_array = np.asarray(anchored_reconstructed, dtype=float)
    response_array = np.asarray(response_reconstructed, dtype=float)
    anchored_row_mse = np.mean(((anchored_array - target_array) / spans[None, :]) ** 2, axis=1)
    response_row_mse = np.mean(((response_array - target_array) / spans[None, :]) ** 2, axis=1)
    anchored_rmse = float(np.sqrt(np.mean(anchored_row_mse)))
    response_rmse = float(np.sqrt(np.mean(response_row_mse)))
    summary_anchored = _response_metric(anchored)
    summary_response = _response_metric(response_only)
    tolerance = float(args.metric_consistency_tolerance)
    if summary_anchored is None or abs(summary_anchored - anchored_rmse) > tolerance:
        errors.append(f"anchored prediction RMSE={anchored_rmse} summary={summary_anchored} tolerance={tolerance}")
    if summary_response is None or abs(summary_response - response_rmse) > tolerance:
        errors.append(f"response-only prediction RMSE={response_rmse} summary={summary_response} tolerance={tolerance}")

    assert lower is not None and upper is not None
    normalized = (target_array - lower[None, :]) / (upper - lower)[None, :]
    out_of_range = np.any((normalized < -1.0e-12) | (normalized > 1.0 + 1.0e-12), axis=1)
    if np.any(out_of_range):
        errors.append(f"targets outside declared physical-cell bounds={int(np.sum(out_of_range))}")
    bins = int(args.physical_cell_bins)
    clipped = np.clip(normalized, 0.0, 1.0)
    cell_indices = np.minimum(np.floor(clipped * bins).astype(int), bins - 1)
    cell_keys = [tuple(int(value) for value in row) for row in cell_indices]
    unique_cells = sorted(set(cell_keys))
    if len(unique_cells) < int(args.minimum_paired_test_cells):
        errors.append(f"paired physical cells={len(unique_cells)} minimum={args.minimum_paired_test_cells}")
    if errors:
        return _bootstrap_failure(anchored_path, response_path, errors, len(paired_ids), len(unique_cells))

    cluster_statistics = []
    for cell in unique_cells:
        mask = np.asarray([key == cell for key in cell_keys], dtype=bool)
        cluster_statistics.append(
            (
                int(np.sum(mask)),
                float(np.sum(anchored_row_mse[mask])),
                float(np.sum(response_row_mse[mask])),
            )
        )
    cluster_counts = np.asarray([item[0] for item in cluster_statistics], dtype=float)
    cluster_anchored_sse = np.asarray([item[1] for item in cluster_statistics], dtype=float)
    cluster_response_sse = np.asarray([item[2] for item in cluster_statistics], dtype=float)
    cluster_anchored_mse = cluster_anchored_sse / cluster_counts
    cluster_response_mse = cluster_response_sse / cluster_counts
    cluster_anchored_rmse = np.sqrt(cluster_anchored_mse)
    cluster_response_rmse = np.sqrt(cluster_response_mse)
    cell_balanced_anchored_rmse = float(np.sqrt(np.mean(cluster_anchored_mse)))
    cell_balanced_response_rmse = float(np.sqrt(np.mean(cluster_response_mse)))
    anchored_tail = _physical_cell_tail_summary(unique_cells, cluster_anchored_rmse)
    response_tail = _physical_cell_tail_summary(unique_cells, cluster_response_rmse)
    rng = np.random.default_rng(int(args.bootstrap_seed))
    replicates = int(args.bootstrap_replicates)
    improvements = np.empty(replicates, dtype=float)
    cell_balanced_improvements = np.empty(replicates, dtype=float)
    p90_tail_improvements = np.empty(replicates, dtype=float)
    cell_count = len(unique_cells)
    for index in range(replicates):
        draw = rng.integers(0, cell_count, size=cell_count)
        multiplicity = np.bincount(draw, minlength=cell_count).astype(float)
        row_count = float(np.dot(multiplicity, cluster_counts))
        rmse_a = math.sqrt(float(np.dot(multiplicity, cluster_anchored_sse)) / row_count)
        rmse_b = math.sqrt(float(np.dot(multiplicity, cluster_response_sse)) / row_count)
        improvements[index] = (rmse_a - rmse_b) / max(rmse_a, 1.0e-12)
        cell_rmse_a = math.sqrt(float(np.mean(cluster_anchored_mse[draw])))
        cell_rmse_b = math.sqrt(float(np.mean(cluster_response_mse[draw])))
        cell_balanced_improvements[index] = (cell_rmse_a - cell_rmse_b) / max(cell_rmse_a, 1.0e-12)
        p90_a = float(np.quantile(cluster_anchored_rmse[draw], 0.90))
        p90_b = float(np.quantile(cluster_response_rmse[draw], 0.90))
        p90_tail_improvements[index] = (p90_a - p90_b) / max(p90_a, 1.0e-12)
    alpha = 1.0 - float(args.bootstrap_confidence)
    lower_ci, upper_ci = np.quantile(improvements, [alpha / 2.0, 1.0 - alpha / 2.0])
    cell_lower_ci, cell_upper_ci = np.quantile(
        cell_balanced_improvements,
        [alpha / 2.0, 1.0 - alpha / 2.0],
    )
    tail_lower_ci, tail_upper_ci = np.quantile(
        p90_tail_improvements,
        [alpha / 2.0, 1.0 - alpha / 2.0],
    )
    point_improvement = (anchored_rmse - response_rmse) / max(anchored_rmse, 1.0e-12)
    cell_point_improvement = (
        cell_balanced_anchored_rmse - cell_balanced_response_rmse
    ) / max(cell_balanced_anchored_rmse, 1.0e-12)
    fingerprint_payload = {
        "schema": "paired_physical_cell_cluster_bootstrap_v1",
        "matrix_indices": paired_ids,
        "cell_keys": [list(key) for key in cell_keys],
        "feature_names": feature_names,
        "spans": spans.tolist(),
        "bounds": {"lower": lower.tolist(), "upper": upper.tolist(), "bins": bins},
        "seed": int(args.bootstrap_seed),
        "replicates": replicates,
        "confidence": float(args.bootstrap_confidence),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "status": "PASS",
        "method": "paired percentile cluster bootstrap over complete held-out 4D physical cells",
        "schema": "paired_physical_cell_cluster_bootstrap_v1",
        "paired_test_row_count": len(paired_ids),
        "paired_physical_cell_count": cell_count,
        "bootstrap_replicates": replicates,
        "confidence_level": float(args.bootstrap_confidence),
        "seed": int(args.bootstrap_seed),
        "anchored_prediction_rmse": anchored_rmse,
        "response_only_prediction_rmse": response_rmse,
        "relative_improvement_point": point_improvement,
        "relative_improvement_ci_lower": float(lower_ci),
        "relative_improvement_ci_upper": float(upper_ci),
        "probability_response_only_better": float(np.mean(improvements > 0.0)),
        "cell_balanced_anchored_prediction_rmse": cell_balanced_anchored_rmse,
        "cell_balanced_response_only_prediction_rmse": cell_balanced_response_rmse,
        "cell_balanced_relative_improvement_point": cell_point_improvement,
        "cell_balanced_relative_improvement_ci_lower": float(cell_lower_ci),
        "cell_balanced_relative_improvement_ci_upper": float(cell_upper_ci),
        "cell_balanced_probability_response_only_better": float(
            np.mean(cell_balanced_improvements > 0.0)
        ),
        "physical_cell_tail_extension_schema": "paired_physical_cell_p90_tail_bootstrap_v1",
        "anchored_physical_cell_tail": anchored_tail,
        "response_only_physical_cell_tail": response_tail,
        "p90_tail_relative_improvement_point": (
            float(anchored_tail["p90_rmse"]) - float(response_tail["p90_rmse"])
        )
        / max(float(anchored_tail["p90_rmse"]), 1.0e-12),
        "p90_tail_relative_improvement_ci_lower": float(tail_lower_ci),
        "p90_tail_relative_improvement_ci_upper": float(tail_upper_ci),
        "p90_tail_probability_response_only_better": float(np.mean(p90_tail_improvements > 0.0)),
        "bootstrap_contract_fingerprint_sha256": fingerprint,
        "physical_cell_contract": {
            "feature_names": feature_names,
            "bins": bins,
            "lower": lower.tolist(),
            "upper": upper.tolist(),
            "range_normalization_spans": spans.tolist(),
        },
        "artifacts": {
            "anchored_predictions": str(anchored_path),
            "response_only_predictions": str(response_path),
        },
        "boundary": (
            "The interval resamples held-out physical cells for two fixed trained models and reports both "
            "row-weighted and equal-cell metrics. A favorable review requires both lower bounds to clear the "
            "material-gain threshold. It does not include training-seed, EMX-HFSS, process, or fabrication uncertainty."
        ),
        "errors": [],
    }


def _physical_cell_tail_summary(
    cells: list[tuple[int, ...]],
    cell_rmse: np.ndarray,
) -> dict[str, Any]:
    values = np.asarray(cell_rmse, dtype=float)
    if values.shape != (len(cells),) or values.size == 0 or np.any(~np.isfinite(values)):
        raise ValueError("physical-cell tail summary received invalid cell errors")
    worst_index = int(np.argmax(values))
    return {
        "cell_count": int(values.size),
        "p50_rmse": float(np.quantile(values, 0.50)),
        "p90_rmse": float(np.quantile(values, 0.90)),
        "p95_rmse": float(np.quantile(values, 0.95)),
        "max_rmse": float(values[worst_index]),
        "worst_cell": ":".join(str(value) for value in cells[worst_index]),
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except OSError:
        return []


def _prediction_index(rows: list[dict[str, str]]) -> tuple[dict[str, dict[str, str]], list[str]]:
    result: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for row_number, row in enumerate(rows):
        key = str(row.get("matrix_index") or "").strip()
        if not key:
            errors.append(f"missing matrix_index at prediction row={row_number}")
            continue
        if key in result:
            errors.append(f"duplicate matrix_index={key}")
            continue
        result[key] = row
    if not rows:
        errors.append("prediction CSV is missing or empty")
    return result, errors


def _range_spans(data: dict[str, Any], input_columns: list[str]) -> np.ndarray | None:
    spans = (((data.get("metrics") or {}).get("range_normalization") or {}).get("feature_span") or {})
    try:
        values = np.asarray([float(spans[column]) for column in input_columns], dtype=float)
    except (KeyError, TypeError, ValueError):
        return None
    if values.shape != (len(input_columns),) or np.any(~np.isfinite(values)) or np.any(values <= 0.0):
        return None
    return values


def _summary_test_row_count(data: dict[str, Any]) -> int | None:
    value = ((data.get("metrics") or {}).get("test_row_count"))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_vector(raw: str, expected: int) -> np.ndarray | None:
    try:
        values = np.asarray([float(item.strip()) for item in str(raw).split(",") if item.strip()], dtype=float)
    except ValueError:
        return None
    if values.shape != (expected,) or np.any(~np.isfinite(values)):
        return None
    return values


def _row_float(row: dict[str, str], field: str) -> float | None:
    return _finite(row.get(field))


def _sortable_id(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


def _bootstrap_failure(
    anchored_path: Path,
    response_path: Path,
    errors: list[str],
    paired_rows: int,
    paired_cells: int = 0,
) -> dict[str, Any]:
    return {
        "status": "FAIL",
        "method": "paired percentile cluster bootstrap over complete held-out 4D physical cells",
        "paired_test_row_count": paired_rows,
        "paired_physical_cell_count": paired_cells,
        "artifacts": {
            "anchored_predictions": str(anchored_path),
            "response_only_predictions": str(response_path),
        },
        "errors": errors[:50],
    }


def _render_report(payload: dict[str, Any]) -> str:
    checks = payload["checks"]
    check_lines = "\n".join(f"- {key}: {'PASS' if value else 'FAIL'}" for key, value in checks.items())
    improvement = payload.get("response_only_relative_improvement")
    improvement_text = "n/a" if improvement is None else f"{100.0 * float(improvement):.3f}%"
    bootstrap = payload.get("paired_cluster_bootstrap") or {}
    lower = bootstrap.get("relative_improvement_ci_lower")
    upper = bootstrap.get("relative_improvement_ci_upper")
    ci_text = "n/a" if lower is None or upper is None else f"[{100.0 * float(lower):.3f}%, {100.0 * float(upper):.3f}%]"
    cell_lower = bootstrap.get("cell_balanced_relative_improvement_ci_lower")
    cell_upper = bootstrap.get("cell_balanced_relative_improvement_ci_upper")
    cell_ci_text = (
        "n/a"
        if cell_lower is None or cell_upper is None
        else f"[{100.0 * float(cell_lower):.3f}%, {100.0 * float(cell_upper):.3f}%]"
    )
    return (
        "# Tandem geometry-anchor ablation\n\n"
        f"- Overall status: `{payload['overall_status']}`\n"
        f"- Decision: `{payload['decision']}`\n"
        f"- Anchored response RMSE: `{payload.get('anchored_response_rmse')}`\n"
        f"- Response-only response RMSE: `{payload.get('response_only_response_rmse')}`\n"
        f"- Response-only relative improvement: `{improvement_text}`\n\n"
        f"- Paired physical-cell bootstrap status: `{bootstrap.get('status')}`\n"
        f"- Row-weighted bootstrap improvement CI: `{ci_text}`\n"
        f"- Equal-cell bootstrap improvement CI: `{cell_ci_text}`\n\n"
        "## Contract checks\n\n"
        f"{check_lines}\n\n"
        "## Scientific boundary\n\n"
        f"{payload['scientific_boundary']}\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
