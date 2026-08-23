#!/usr/bin/env python3
"""Audit cumulative inverse-model gains across real-EMX checkpoints.

The audit is deliberately advisory. It measures whether fixed-contract OOD
metrics still improve as accepted real EMX rows grow, but it never stops the
million-sample campaign and never turns surrogate performance into EM proof.
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

import numpy as np


COMMON_TARGET_COLUMNS = (
    "lp_nh_center",
    "ls_nh_center",
    "q_center",
    "k_abs_center",
)
COMMON_FEATURE_SPANS = np.asarray([2.5, 2.5, 20.0, 0.8], dtype=float)
COMMON_IDENTITY_FIELD = "source_geometry_identity_sha256"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    checkpoint_root = Path(args.checkpoint_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, rejects = _checkpoint_rows(checkpoint_root)
    rows.sort(key=lambda item: (item["accepted_checkpoint_count"], item["checkpoint_index"]))
    common_panel_path = out_dir / "fixed_common_test_panel_geometry_ids.csv"
    common_panel = _fixed_common_test_panel(rows, args, common_panel_path)
    comparable, comparison_checks = _comparison_contract(rows, rejects, common_panel, args)
    decision, trend = _trend_decision(rows, comparable, args)
    fit = _fit_power_law(rows)

    csv_path = out_dir / "physical_feature_model_learning_curve.csv"
    summary_path = out_dir / "physical_feature_model_learning_curve_summary.json"
    report_path = out_dir / "physical_feature_model_learning_curve_report.md"
    plot_path = out_dir / "physical_feature_model_learning_curve.png"
    _write_csv(csv_path, rows)
    plot_status = _write_plot(plot_path, rows)

    if not rows:
        overall_status = "FAIL" if rejects else "WAITING_FOR_CHECKPOINTS"
    elif not comparable:
        overall_status = "FAIL"
    else:
        overall_status = "PASS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "checkpoint_root": str(checkpoint_root),
        "checkpoint_count": len(rows),
        "checkpoints": rows,
        "rejected_checkpoint_records": rejects,
        "comparison_contract": {
            "comparable": comparable,
            "checks": comparison_checks,
            "primary_metric": (
                "tandem response RMSE on the fixed cross-checkpoint common geometry panel, "
                "normalized by fixed [Lp,Ls,Q,|K|] physical ranges"
            ),
            "boundary": (
                "The primary trend uses only geometry identities present in every completed checkpoint test prediction "
                "file, with unchanged physical targets. Expanding-cell OOD metrics remain diagnostic and cannot replace "
                "the frozen common-panel trend."
            ),
        },
        "checkpoint_schedule": _checkpoint_schedule_summary(rows, rejects, args),
        "fixed_common_test_panel": common_panel,
        "trend": trend,
        "power_law_extrapolation": fit,
        "artifacts": {
            "csv": str(csv_path),
            "plot": str(plot_path) if plot_status == "PASS" else "",
            "plot_status": plot_status,
            "report": str(report_path),
        },
        "scientific_boundary": (
            "PLATEAU_REVIEW is an evidence prompt, not an automatic stop and not a model PASS. "
            "Campaign completion still requires one million real accepted EMX samples, ten model tests, "
            "final strict uniformity PASS, and separate EMX/HFSS validation."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"checkpoint_count={len(rows)}")
    print(f"summary={summary_path}")
    return 0 if overall_status in {"PASS", "WAITING_FOR_CHECKPOINTS"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--minimum-checkpoints", type=int, default=3)
    parser.add_argument("--plateau-window", type=int, default=3)
    parser.add_argument("--max-marginal-relative-improvement", type=float, default=0.02)
    parser.add_argument("--regression-relative-tolerance", type=float, default=0.05)
    parser.add_argument("--minimum-common-test-rows", type=int, default=1000)
    parser.add_argument("--minimum-first-panel-retention", type=float, default=0.99)
    parser.add_argument("--expected-checkpoint-size", type=int, default=100_000)
    parser.add_argument("--expected-total-checkpoints", type=int, default=10)
    parser.add_argument("--require-complete-schedule", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.minimum_checkpoints < 2:
        parser.error("--minimum-checkpoints must be at least 2")
    if args.plateau_window < 2:
        parser.error("--plateau-window must be at least 2")
    if not 0.0 <= args.max_marginal_relative_improvement < 1.0:
        parser.error("--max-marginal-relative-improvement must be in [0,1)")
    if not 0.0 <= args.regression_relative_tolerance < 1.0:
        parser.error("--regression-relative-tolerance must be in [0,1)")
    if args.minimum_common_test_rows < 1:
        parser.error("--minimum-common-test-rows must be positive")
    if not 0.0 < args.minimum_first_panel_retention <= 1.0:
        parser.error("--minimum-first-panel-retention must be in (0,1]")
    if args.expected_checkpoint_size < 1:
        parser.error("--expected-checkpoint-size must be positive")
    if args.expected_total_checkpoints < 1:
        parser.error("--expected-total-checkpoints must be positive")
    return args


def _checkpoint_rows(checkpoint_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, str]] = []
    for record_path in sorted(checkpoint_root.glob("checkpoint_*/checkpoint_record.json")):
        try:
            record = _read_json(record_path)
            manifest_path = Path(str(record.get("model_manifest") or "")).expanduser()
            manifest = _read_json(manifest_path)
            record_index = int(record.get("checkpoint_index") or 0)
            record_count = int(record.get("target_accepted_count") or 0)
            manifest_index = int(manifest.get("checkpoint_index") or 0)
            manifest_count = int(manifest.get("accepted_checkpoint_count") or 0)
            manifest_sha = _sha256_file(manifest_path)
            recorded_manifest_sha = str(record.get("model_manifest_sha256") or "").strip().lower()
            if record_index != manifest_index or record_count != manifest_count:
                raise ValueError("checkpoint record and model manifest index/count mismatch")
            if not _is_sha256(recorded_manifest_sha) or recorded_manifest_sha != manifest_sha:
                raise ValueError("model manifest SHA is missing or does not match checkpoint record")
            tandem = manifest.get("tandem_ood_metrics") or {}
            forward = tandem.get("forward_proxy") or {}
            inverse = tandem.get("tandem_inverse") or {}
            range_meta = tandem.get("range_normalization") or {}
            direct = _direct_summary(manifest)
            direct_selected = direct.get("selected_candidate") or {}
            split = manifest.get("tandem_ood_split_audit") or {}
            seed_contract = manifest.get("seed_contract") or {}
            tandem_summary_path, tandem_summary = _tandem_summary(manifest)
            tandem_artifact = ((manifest.get("artifacts") or {}).get("tandem_physical_cell_ood") or {})
            tandem_summary_sha = _sha256_file(tandem_summary_path)
            recorded_tandem_summary_sha = str(tandem_artifact.get("sha256") or "").strip().lower()
            if not _is_sha256(recorded_tandem_summary_sha) or recorded_tandem_summary_sha != tandem_summary_sha:
                raise ValueError("tandem summary SHA is missing or does not match model manifest")
            model_contract = tandem_summary.get("model_comparison_contract") or {}
            isolation = tandem_summary.get("evaluation_isolation") or {}
            predictions_path = Path(str(tandem_summary.get("test_predictions_csv") or "")).expanduser()
            predictions_sha = _sha256_file(predictions_path)
            recorded_predictions_sha = str(
                tandem_summary.get("test_predictions_csv_sha256") or ""
            ).strip().lower()
            if not _is_sha256(recorded_predictions_sha) or recorded_predictions_sha != predictions_sha:
                raise ValueError("tandem test-prediction CSV SHA is missing or does not match summary")
            tandem_range = _finite(inverse.get("test_response_range_normalized_rmse"))
            forward_range = _finite(forward.get("test_range_normalized_rmse"))
            row = {
                "checkpoint_index": record_index,
                "accepted_checkpoint_count": record_count,
                "overall_status": str(record.get("overall_status") or manifest.get("overall_status") or "MISSING"),
                "uniformity_status": str(record.get("uniformity_status") or manifest.get("uniformity_status") or "MISSING"),
                "formal_checkpoint_pass": bool(record.get("formal_checkpoint_pass")),
                "tandem_response_range_normalized_rmse": tandem_range,
                "forward_proxy_range_normalized_rmse": forward_range,
                "direct_geometry_test_normalized_rmse_diagnostic": _finite(
                    direct_selected.get("test_normalized_rmse")
                ),
                "range_normalization_source": str(range_meta.get("source") or "MISSING"),
                "selection_seed": _integer(seed_contract.get("selection_seed")),
                "model_initialization_seed": _integer(seed_contract.get("model_initialization_seed")),
                "split_seed": _integer(seed_contract.get("cross_checkpoint_split_seed")),
                "partition_method": str(split.get("physical_cell_partition_method") or "MISSING"),
                "partition_stable_for_existing_cells": bool(
                    split.get("physical_cell_partition_stable_for_existing_cells")
                ),
                "physical_cell_bins": _integer(split.get("physical_cell_bins_per_dimension")),
                "physical_cell_lower": split.get("physical_cell_lower"),
                "physical_cell_upper": split.get("physical_cell_upper"),
                "checkpoint_record": str(record_path),
                "record_directory_name": record_path.parent.name,
                "model_manifest_sha256": manifest_sha,
                "model_manifest_sha_matches_record": recorded_manifest_sha == manifest_sha,
                "model_comparison_fingerprint_sha256": model_contract.get("fingerprint_sha256"),
                "trainer_implementation_sha256": model_contract.get("trainer_implementation_sha256"),
                "model_input_columns": model_contract.get("input_columns"),
                "model_geometry_columns": model_contract.get("geometry_columns"),
                "evaluation_isolation_status": isolation.get("overall_status"),
                "evaluation_isolation_checks": isolation.get("checks"),
                "test_set_used_for_gradient_updates": isolation.get("test_set_used_for_gradient_updates"),
                "test_set_used_for_early_stopping": isolation.get("test_set_used_for_early_stopping"),
                "test_set_used_for_model_or_hyperparameter_selection": isolation.get(
                    "test_set_used_for_model_or_hyperparameter_selection"
                ),
                "test_set_used_for_acceptance_threshold_tuning": isolation.get(
                    "test_set_used_for_acceptance_threshold_tuning"
                ),
                "test_set_used_only_for_post_training_evaluation": isolation.get(
                    "test_set_used_only_for_post_training_evaluation"
                ),
                "declared_test_geometry_identity_set_sha256": (
                    isolation.get("geometry_identity_set_sha256") or {}
                ).get("test"),
                "model_manifest": str(manifest_path),
                "tandem_summary": str(tandem_summary_path),
                "tandem_summary_sha256": tandem_summary_sha,
                "tandem_test_predictions_csv": str(predictions_path),
                "tandem_test_predictions_csv_sha256": predictions_sha,
                "tandem_reported_test_row_count": _integer(tandem.get("test_row_count")),
            }
            if row["checkpoint_index"] < 1 or row["accepted_checkpoint_count"] < 1 or tandem_range is None:
                raise ValueError("checkpoint index/count or fixed-range tandem metric is missing")
            rows.append(row)
        except Exception as exc:  # noqa: BLE001 - malformed evidence is recorded, never hidden.
            rejects.append({"path": str(record_path), "reason": f"{type(exc).__name__}: {exc}"})
    return rows, rejects


def _direct_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    raw = ((manifest.get("artifacts") or {}).get("nn") or {}).get("path")
    if not raw:
        return {}
    path = Path(str(raw)).expanduser()
    return _read_json(path) if path.is_file() else {}


def _tandem_summary(manifest: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    raw = ((manifest.get("artifacts") or {}).get("tandem_physical_cell_ood") or {}).get("path")
    path = Path(str(raw or "")).expanduser()
    return path, _read_json(path) if path.is_file() else {}


def _fixed_common_test_panel(
    rows: list[dict[str, Any]],
    args: argparse.Namespace,
    artifact_path: Path,
) -> dict[str, Any]:
    fieldnames = ["geometry_identity_sha256", *[f"target__{name}" for name in COMMON_TARGET_COLUMNS]]
    if not rows:
        _write_dict_rows(artifact_path, fieldnames, [])
        return {
            "status": "WAITING_FOR_CHECKPOINTS",
            "identity_schema": "ordered_inverse_geometry_float64_v1",
            "common_geometry_count": 0,
            "checks": {"checkpoints_present": False},
            "artifact": {"path": str(artifact_path), "sha256": _sha256_file(artifact_path)},
        }

    checkpoint_maps: list[dict[str, tuple[np.ndarray, np.ndarray]]] = []
    evidence: list[dict[str, Any]] = []
    target_fields = [f"target__{name}" for name in COMMON_TARGET_COLUMNS]
    reconstructed_fields = [f"reconstructed__{name}" for name in COMMON_TARGET_COLUMNS]
    for checkpoint in rows:
        predictions_path = Path(str(checkpoint.get("tandem_test_predictions_csv") or "")).expanduser()
        raw_rows = _read_csv_rows(predictions_path) if predictions_path.is_file() else []
        parsed: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        invalid_row_count = 0
        duplicate_identity_count = 0
        for prediction in raw_rows:
            identity = str(prediction.get(COMMON_IDENTITY_FIELD) or "").strip().lower()
            target = np.asarray([_finite(prediction.get(field)) for field in target_fields], dtype=object)
            reconstructed = np.asarray(
                [_finite(prediction.get(field)) for field in reconstructed_fields], dtype=object
            )
            if not _is_sha256(identity) or any(value is None for value in target) or any(
                value is None for value in reconstructed
            ):
                invalid_row_count += 1
                continue
            if identity in parsed:
                duplicate_identity_count += 1
                continue
            parsed[identity] = (
                np.asarray(target, dtype=float),
                np.asarray(reconstructed, dtype=float),
            )
        reported_count = _integer(checkpoint.get("tandem_reported_test_row_count"))
        prediction_identity_fingerprint = _identity_set_fingerprint(parsed)
        declared_identity_fingerprint = str(
            checkpoint.get("declared_test_geometry_identity_set_sha256") or ""
        ).strip().lower()
        evidence.append(
            {
                "checkpoint_index": int(checkpoint["checkpoint_index"]),
                "predictions_csv": str(predictions_path),
                "predictions_csv_exists": predictions_path.is_file(),
                "prediction_row_count": len(raw_rows),
                "reported_test_row_count": reported_count,
                "unique_valid_geometry_identity_count": len(parsed),
                "invalid_row_count": invalid_row_count,
                "duplicate_geometry_identity_count": duplicate_identity_count,
                "prediction_geometry_identity_set_sha256": prediction_identity_fingerprint,
                "declared_test_geometry_identity_set_sha256": declared_identity_fingerprint,
                "test_geometry_identity_fingerprint_matches": _is_sha256(declared_identity_fingerprint)
                and prediction_identity_fingerprint == declared_identity_fingerprint,
                "complete_test_prediction_coverage": reported_count is not None
                and len(raw_rows) == reported_count
                and len(parsed) == reported_count,
            }
        )
        checkpoint_maps.append(parsed)

    fixed_panel_ids = sorted(checkpoint_maps[0])
    common_ids = set(fixed_panel_ids)
    for mapping in checkpoint_maps[1:]:
        common_ids.intersection_update(mapping)
    ordered_common_ids = sorted(common_ids)
    first_count = len(fixed_panel_ids)
    retention = len(ordered_common_ids) / first_count if first_count else 0.0

    for item, mapping in zip(evidence, checkpoint_maps):
        covered = sum(identity in mapping for identity in fixed_panel_ids)
        item["fixed_panel_geometry_count"] = first_count
        item["fixed_panel_covered_count"] = int(covered)
        item["fixed_panel_missing_count"] = int(first_count - covered)
        item["fixed_panel_coverage_fraction"] = float(covered / first_count) if first_count else 0.0
        item["complete_fixed_panel_coverage"] = bool(first_count and covered == first_count)

    target_mismatch_count = 0
    for identity in fixed_panel_ids:
        reference = checkpoint_maps[0][identity][0]
        if any(
            identity in mapping
            and not np.allclose(reference, mapping[identity][0], rtol=1.0e-12, atol=1.0e-12)
            for mapping in checkpoint_maps[1:]
        ):
            target_mismatch_count += 1

    metric_values = []
    per_checkpoint_metrics = []
    for checkpoint, mapping in zip(rows, checkpoint_maps):
        complete_fixed_panel = bool(fixed_panel_ids) and all(identity in mapping for identity in fixed_panel_ids)
        if complete_fixed_panel:
            targets = np.stack([mapping[identity][0] for identity in fixed_panel_ids], axis=0)
            reconstructed = np.stack([mapping[identity][1] for identity in fixed_panel_ids], axis=0)
            normalized_error = (reconstructed - targets) / COMMON_FEATURE_SPANS[None, :]
            rmse = float(np.sqrt(np.mean(normalized_error**2)))
            per_feature_mae = np.mean(np.abs(reconstructed - targets), axis=0)
        else:
            rmse = math.nan
            per_feature_mae = np.full(len(COMMON_TARGET_COLUMNS), math.nan, dtype=float)
        checkpoint["common_panel_response_range_normalized_rmse"] = rmse
        checkpoint["common_panel_test_row_count"] = len(fixed_panel_ids) if complete_fixed_panel else 0
        metric_values.append(rmse)
        per_checkpoint_metrics.append(
            {
                "checkpoint_index": int(checkpoint["checkpoint_index"]),
                "accepted_checkpoint_count": int(checkpoint["accepted_checkpoint_count"]),
                "common_panel_response_range_normalized_rmse": rmse,
                "per_feature_physical_mae": {
                    name: float(value) for name, value in zip(COMMON_TARGET_COLUMNS, per_feature_mae)
                },
            }
        )

    artifact_rows = []
    for identity in fixed_panel_ids:
        target = checkpoint_maps[0][identity][0]
        artifact_rows.append(
            {
                "geometry_identity_sha256": identity,
                **{f"target__{name}": float(value) for name, value in zip(COMMON_TARGET_COLUMNS, target)},
            }
        )
    _write_dict_rows(artifact_path, fieldnames, artifact_rows)
    panel_fingerprint = hashlib.sha256(
        "".join(
            f"{row['geometry_identity_sha256']}|"
            + "|".join(format(float(row[f'target__{name}']), ".17g") for name in COMMON_TARGET_COLUMNS)
            + "\n"
            for row in artifact_rows
        ).encode("ascii")
    ).hexdigest()
    checks = {
        "checkpoints_present": True,
        "all_prediction_files_exist": all(item["predictions_csv_exists"] for item in evidence),
        "all_prediction_rows_valid": all(item["invalid_row_count"] == 0 for item in evidence),
        "all_geometry_identities_unique": all(
            item["duplicate_geometry_identity_count"] == 0 for item in evidence
        ),
        "complete_test_prediction_coverage": all(
            item["complete_test_prediction_coverage"] is True for item in evidence
        ),
        "test_geometry_identity_fingerprint_matches": all(
            item["test_geometry_identity_fingerprint_matches"] is True for item in evidence
        ),
        "minimum_common_test_rows": len(fixed_panel_ids) >= int(args.minimum_common_test_rows),
        "minimum_first_panel_retention": retention >= float(args.minimum_first_panel_retention),
        "exact_fixed_panel_coverage_all_checkpoints": all(
            item["complete_fixed_panel_coverage"] is True for item in evidence
        ),
        "targets_stable_across_checkpoints": target_mismatch_count == 0,
        "common_panel_metric_finite_all_checkpoints": all(math.isfinite(value) for value in metric_values),
        "artifact_written": artifact_path.is_file() and artifact_path.stat().st_size > 0,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "identity_schema": "ordered_inverse_geometry_float64_v1",
        "identity_field": COMMON_IDENTITY_FIELD,
        "fixed_panel_policy": "all valid test geometries from the first completed checkpoint, sorted by identity SHA",
        "fixed_panel_source_checkpoint_index": int(rows[0]["checkpoint_index"]),
        "target_columns": list(COMMON_TARGET_COLUMNS),
        "fixed_feature_spans": COMMON_FEATURE_SPANS.tolist(),
        "first_checkpoint_test_geometry_count": first_count,
        "fixed_panel_geometry_count": len(fixed_panel_ids),
        "common_geometry_count": len(fixed_panel_ids),
        "intersection_geometry_count": len(ordered_common_ids),
        "first_panel_retention_fraction": retention,
        "target_mismatch_count": target_mismatch_count,
        "common_panel_fingerprint_sha256": panel_fingerprint,
        "fixed_panel_fingerprint_sha256": panel_fingerprint,
        "checks": checks,
        "checkpoint_evidence": evidence,
        "checkpoint_metrics": per_checkpoint_metrics,
        "artifact": {
            "path": str(artifact_path),
            "sha256": _sha256_file(artifact_path),
        },
        "scientific_boundary": (
            "The fixed panel is anchored to the first completed checkpoint and is never replaced by a later "
            "cross-checkpoint intersection. Its identities and targets never enter model fitting, "
            "temperature selection, early stopping, or candidate acquisition."
        ),
    }


def _comparison_contract(
    rows: list[dict[str, Any]],
    rejects: list[dict[str, str]],
    common_panel: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[bool, dict[str, bool]]:
    if not rows:
        checks = {
            "checkpoints_present": False,
            "no_rejected_checkpoint_records": not rejects,
        }
        return not rejects, checks

    def same_non_null(key: str) -> bool:
        values = [json.dumps(row.get(key), sort_keys=True) for row in rows]
        return all(value not in {"null", '"MISSING"'} for value in values) and len(set(values)) == 1

    expected_indices = list(range(1, len(rows) + 1))
    actual_indices = [int(row["checkpoint_index"]) for row in rows]
    expected_counts = [index * int(args.expected_checkpoint_size) for index in expected_indices]
    actual_counts = [int(row["accepted_checkpoint_count"]) for row in rows]
    expected_directory_names = [
        f"checkpoint_{index:02d}_n{count}" for index, count in zip(expected_indices, expected_counts)
    ]
    checks = {
        "checkpoints_present": True,
        "no_rejected_checkpoint_records": not rejects,
        "checkpoint_indices_form_contiguous_prefix": actual_indices == expected_indices,
        "checkpoint_counts_match_index_times_expected_size": actual_counts == expected_counts,
        "checkpoint_count_within_expected_total": len(rows) <= int(args.expected_total_checkpoints),
        "complete_schedule_when_required": not args.require_complete_schedule
        or len(rows) == int(args.expected_total_checkpoints),
        "record_directories_match_schedule": [row["record_directory_name"] for row in rows]
        == expected_directory_names,
        "model_manifest_sha_matches_records": all(
            row.get("model_manifest_sha_matches_record") is True for row in rows
        ),
        "fixed_range_metric_present": all(
            _finite(row.get("tandem_response_range_normalized_rmse")) is not None for row in rows
        ),
        "declared_range_normalization": all(
            row.get("range_normalization_source") == "declared_physical_cell_range" for row in rows
        ),
        "same_model_initialization_seed": same_non_null("model_initialization_seed"),
        "same_split_seed": same_non_null("split_seed"),
        "same_partition_method": same_non_null("partition_method"),
        "stable_partition_method": all(row.get("partition_stable_for_existing_cells") is True for row in rows),
        "same_physical_cell_bins": same_non_null("physical_cell_bins"),
        "same_physical_lower": same_non_null("physical_cell_lower"),
        "same_physical_upper": same_non_null("physical_cell_upper"),
        "same_model_comparison_fingerprint": same_non_null("model_comparison_fingerprint_sha256"),
        "same_trainer_implementation_sha256": same_non_null("trainer_implementation_sha256"),
        "same_model_input_columns": same_non_null("model_input_columns"),
        "same_model_geometry_columns": same_non_null("model_geometry_columns"),
        "evaluation_isolation_pass_all_checkpoints": all(
            row.get("evaluation_isolation_status") == "PASS"
            and bool(row.get("evaluation_isolation_checks"))
            and all(value is True for value in row["evaluation_isolation_checks"].values())
            for row in rows
        ),
        "test_rows_excluded_from_training_and_selection": all(
            row.get("test_set_used_for_gradient_updates") is False
            and row.get("test_set_used_for_early_stopping") is False
            and row.get("test_set_used_for_model_or_hyperparameter_selection") is False
            and row.get("test_set_used_for_acceptance_threshold_tuning") is False
            and row.get("test_set_used_only_for_post_training_evaluation") is True
            for row in rows
        ),
        "strictly_increasing_counts": all(
            rows[index]["accepted_checkpoint_count"] > rows[index - 1]["accepted_checkpoint_count"]
            for index in range(1, len(rows))
        ),
        "fixed_common_test_panel_pass": common_panel.get("status") == "PASS",
    }
    return all(checks.values()), checks


def _checkpoint_schedule_summary(
    rows: list[dict[str, Any]],
    rejects: list[dict[str, str]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    actual_indices = [int(row["checkpoint_index"]) for row in rows]
    actual_counts = [int(row["accepted_checkpoint_count"]) for row in rows]
    expected_prefix_indices = list(range(1, len(rows) + 1))
    expected_prefix_counts = [
        index * int(args.expected_checkpoint_size) for index in expected_prefix_indices
    ]
    return {
        "expected_checkpoint_size": int(args.expected_checkpoint_size),
        "expected_total_checkpoints": int(args.expected_total_checkpoints),
        "require_complete_schedule": bool(args.require_complete_schedule),
        "actual_indices": actual_indices,
        "actual_counts": actual_counts,
        "expected_prefix_indices": expected_prefix_indices,
        "expected_prefix_counts": expected_prefix_counts,
        "completed_checkpoint_count": len(rows),
        "remaining_checkpoint_count": max(0, int(args.expected_total_checkpoints) - len(rows)),
        "rejected_checkpoint_record_count": len(rejects),
        "uniformity_status_by_checkpoint": [str(row.get("uniformity_status")) for row in rows],
        "final_uniformity_pass_if_schedule_complete": (
            rows[-1].get("uniformity_status") == "PASS"
            if len(rows) == int(args.expected_total_checkpoints)
            else None
        ),
    }


def _trend_decision(
    rows: list[dict[str, Any]],
    comparable: bool,
    args: argparse.Namespace,
) -> tuple[str, dict[str, Any]]:
    errors = [float(row["common_panel_response_range_normalized_rmse"]) for row in rows]
    improvements = [
        (errors[index - 1] - errors[index]) / max(errors[index - 1], 1.0e-12)
        for index in range(1, len(errors))
    ]
    for index, value in enumerate(improvements, start=1):
        rows[index]["marginal_relative_improvement"] = float(value)
    if rows:
        rows[0]["marginal_relative_improvement"] = None

    if not comparable:
        decision = "FIX_CROSS_CHECKPOINT_COMPARISON_CONTRACT"
    elif not rows:
        decision = "WAIT_FOR_FIRST_MODEL_CHECKPOINT"
    elif len(rows) < int(args.minimum_checkpoints):
        decision = "CONTINUE_UNTIL_MINIMUM_LEARNING_CURVE_HISTORY"
    else:
        recent = improvements[-(int(args.plateau_window) - 1) :]
        if any(value < -float(args.regression_relative_tolerance) for value in recent):
            decision = "REVIEW_MODEL_REGRESSION_BEFORE_INTERPRETING_MORE_DATA"
        elif len(recent) == int(args.plateau_window) - 1 and all(
            value <= float(args.max_marginal_relative_improvement) for value in recent
        ):
            decision = "PLATEAU_REVIEW_DO_NOT_AUTOMATICALLY_STOP_CAMPAIGN"
        else:
            decision = "CONTINUE_DATA_ACQUISITION_AND_REEVALUATE_NEXT_CHECKPOINT"
    return decision, {
        "metric_values": errors,
        "marginal_relative_improvements": improvements,
        "minimum_checkpoints": int(args.minimum_checkpoints),
        "plateau_window": int(args.plateau_window),
        "max_marginal_relative_improvement": float(args.max_marginal_relative_improvement),
        "regression_relative_tolerance": float(args.regression_relative_tolerance),
    }


def _fit_power_law(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 4:
        return {"status": "INSUFFICIENT_POINTS", "minimum_points": 4}
    counts = np.asarray([row["accepted_checkpoint_count"] for row in rows], dtype=float)
    errors = np.asarray([row["common_panel_response_range_normalized_rmse"] for row in rows], dtype=float)
    best: dict[str, float] | None = None
    for floor in np.linspace(0.0, float(np.min(errors)) * 0.95, 200):
        residual = errors - floor
        if np.any(residual <= 0.0):
            continue
        slope, intercept = np.polyfit(np.log(counts), np.log(residual), 1)
        exponent = -float(slope)
        if exponent <= 0.0:
            continue
        prediction = floor + math.exp(float(intercept)) * counts ** (-exponent)
        sse = float(np.sum((prediction - errors) ** 2))
        if best is None or sse < best["sse"]:
            best = {
                "floor": float(floor),
                "coefficient": float(math.exp(float(intercept))),
                "exponent": exponent,
                "sse": sse,
            }
    if best is None:
        return {"status": "FIT_FAILED"}
    predicted = best["floor"] + best["coefficient"] * counts ** (-best["exponent"])
    total = float(np.sum((errors - np.mean(errors)) ** 2))
    r2 = None if total <= 1.0e-18 else 1.0 - float(np.sum((predicted - errors) ** 2)) / total
    next_count = float(counts[-1] + 100000.0)
    million_count = 1000000.0
    return {
        "status": "ADVISORY_ONLY",
        **best,
        "r2": r2,
        "predicted_next_100k_error": float(
            best["floor"] + best["coefficient"] * next_count ** (-best["exponent"])
        ),
        "predicted_1m_error": float(
            best["floor"] + best["coefficient"] * million_count ** (-best["exponent"])
        ),
        "boundary": "The fit is descriptive extrapolation, not a stopping certificate or a replacement for later real checkpoints.",
    }


def _write_plot(path: Path, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "WAITING_FOR_CHECKPOINTS"
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE:{type(exc).__name__}"

    counts = np.asarray([row["accepted_checkpoint_count"] for row in rows], dtype=float) / 1000.0
    tandem = np.asarray([row["common_panel_response_range_normalized_rmse"] for row in rows], dtype=float)
    expanding = np.asarray([row["tandem_response_range_normalized_rmse"] for row in rows], dtype=float)
    forward = np.asarray(
        [np.nan if row["forward_proxy_range_normalized_rmse"] is None else row["forward_proxy_range_normalized_rmse"] for row in rows],
        dtype=float,
    )
    figure, axis = plt.subplots(figsize=(8.0, 4.8), dpi=180)
    axis.plot(counts, tandem, "o-", color="#c43c35", linewidth=2.0, label="Fixed common-panel tandem RMSE")
    axis.plot(
        counts,
        expanding,
        "o--",
        color="#8b8f97",
        linewidth=1.4,
        label="Expanding OOD tandem RMSE (diagnostic)",
    )
    if np.any(np.isfinite(forward)):
        axis.plot(counts, forward, "s-", color="#2268b2", linewidth=2.0, label="Forward proxy OOD RMSE")
    axis.set_xlabel("Accepted real EMX samples (thousands)")
    axis.set_ylabel("Fixed-range normalized RMSE")
    axis.set_title("Physical-feature model learning curve")
    axis.grid(True, alpha=0.25)
    axis.legend(frameon=False)
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    return "PASS"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    scalar_rows = []
    for row in rows:
        scalar_rows.append(
            {
                key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (list, dict))
                else value
                for key, value in row.items()
            }
        )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(scalar_rows[0]))
        writer.writeheader()
        writer.writerows(scalar_rows)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Physical-feature model learning-curve audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Completed comparable checkpoints: `{summary['checkpoint_count']}`",
        "",
        "## Checkpoints",
        "",
        "| Checkpoint | Real EMX rows | Fixed common-panel RMSE | Expanding OOD RMSE | Marginal improvement | Uniformity |",
        "| ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in summary["checkpoints"]:
        gain = row.get("marginal_relative_improvement")
        gain_text = "-" if gain is None else f"{100.0 * float(gain):.2f}%"
        lines.append(
            f"| {row['checkpoint_index']} | {row['accepted_checkpoint_count']} | "
            f"{row['common_panel_response_range_normalized_rmse']:.6g} | "
            f"{row['tandem_response_range_normalized_rmse']:.6g} | {gain_text} | {row['uniformity_status']} |"
        )
    panel = summary["fixed_common_test_panel"]
    lines.extend(
        [
            "",
            "## Fixed common test panel",
            "",
            f"- Status: **{panel['status']}**",
            f"- Common geometry rows: `{panel['common_geometry_count']}`",
            f"- First-panel retention: `{100.0 * float(panel.get('first_panel_retention_fraction') or 0.0):.2f}%`",
            f"- Target mismatches: `{panel.get('target_mismatch_count')}`",
            "",
            "## Interpretation boundary",
            "",
            summary["scientific_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_dict_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _identity_set_fingerprint(rows: dict[str, Any]) -> str:
    payload = "".join(f"{identity}\n" for identity in sorted(rows))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
