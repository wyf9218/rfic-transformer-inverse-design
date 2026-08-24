#!/usr/bin/env python3
"""Reconcile and report target-only panels from the frozen fixed8k proxy CSV.

The script performs no inference and no physical simulation.  It preserves the
legacy one-sided-Q engineering score while reporting the symmetric absolute-Q
fidelity metric under a separate identity and independent reproduction gate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.analysis.fixed8k_application_panels import (  # noqa: E402
    CORE_PANEL,
    EXTENDED_PANEL,
    EXPECTED_ABSOLUTE_Q_COUNTS,
    FEATURE_NAMES,
    FULL_PANEL,
    LEGACY_METRIC_ID,
    NORMALIZATION_SPANS,
    PANELS,
    STRICT_METRIC_ID,
    SYMMETRIC_METRIC_ID,
    SYMMETRIC_JOINT_METRIC_ID,
    create_no_clobber_directory,
    derive_errors,
    figure_sidecar,
    independent_metric_reproduction,
    joint_metrics_by_definition_rows,
    normalized_nearest_neighbor_distance,
    panel_membership,
    panel_summary_rows,
    raw_feature_error_rows,
    reconstruct_exact_training_response_cloud,
    report_headline_binding,
    require_sha256,
    sha256_file,
    support_distance_quintiles,
    tolerance_success_by_definition_rows,
    tolerance_success_rows,
    validate_figure_sidecar_sources,
    validate_target_prediction_matrices,
)


TARGET_SHA256 = "c9d7d8bc7f65a488be0805969389a01ef049534eefdfdea71cbd640ee27d6407"
PREDICTION_SHA256 = "c8e883251101e5c72aa0038c6e8018b167279e0715797176aaf748c183aef1a3"
MODEL_CONTRACT_SHA256 = "c09aabf91071baa08fdbe0a44eb8ef840c7c61ff241937681e40d8844c7f2812"
MODEL_SUMMARY_SHA256 = "90a81532f7342ae7348248ea45889368fbb13ed2b6ee8f89e789f80f6811a3fa"
MODEL_WEIGHTS_SHA256 = "ffea66dfdd0bb252e402e1ade70f5e8768511e26f2978b1d73b1817a0221e42a"
TRAINING_TABLE_SHA256 = "3027290eb1b4c229a23f0676f970ff9d13762677a897a0d7e2aed959075c85c8"
EVIDENCE_CLASS = "frozen_forward_proxy_diagnostic_not_fresh_emx"
EXPECTED_FULL_ROWS = 8000
EXPECTED_MODEL_ID = "real10k_center15ghz_seed20260711"
PREVIOUS_BLOCKED_RECEIPT_SHA256 = "3ea7b045a09fa041b727069ce9d1ec66939fef0ff71f0757c6922b60583ff2a2"
PREVIOUS_BLOCKED_MISMATCH_SHA256 = "673acff482738fa233c65a1ebaa6a8358321fd99b5ee2d2fe23d251aadb90c39"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--output-utc", default="")
    return parser.parse_args(argv)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str] | None = None) -> None:
    if not rows and not fieldnames:
        raise ValueError(f"cannot infer columns for empty CSV: {path}")
    columns = list(fieldnames or rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in columns})


def _utc_token(value: str) -> str:
    if value:
        token = value.strip()
        if not token.endswith("Z") or not token.replace("T", "").replace("Z", "").isdigit():
            raise ValueError("--output-utc must use YYYYMMDDTHHMMSSZ")
        return token
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _range_contract(generated_utc: str) -> dict[str, Any]:
    return {
        "schema": "literature_informed_15ghz_range_contract.v1",
        "frozen_utc": generated_utc,
        "range_classification": (
            "literature-informed project application panels, not an industry standard"
        ),
        "frequency_ghz": 15,
        "scientific_basis": (
            "The numeric limits are project-level evaluation panels informed by the "
            "literature, the present transformer topology, the current model domain, "
            "and the intended 15-GHz matching-network use."
        ),
        "filtering_contract": {
            "source": "target values only",
            "prediction_filtering_allowed": False,
            "error_filtering_allowed": False,
            "post_result_limit_optimization_allowed": False,
            "normalization_spans": {
                "Lp_nH": 2.5,
                "Ls_nH": 2.5,
                "Qmin": 20.0,
                "K_abs": 0.8,
            },
        },
        "panels": [
            {
                "name": panel.name,
                "role": panel.role,
                "lower": dict(zip(FEATURE_NAMES, panel.lower)),
                "upper": dict(zip(FEATURE_NAMES, panel.upper)),
                "Ls_over_Lp_lower": panel.ratio_lower,
                "Ls_over_Lp_upper": panel.ratio_upper,
                "definition": panel.definition(),
            }
            for panel in PANELS
        ],
        "subset_contract": [
            "core_15ghz_application subset_of extended_15ghz_practical",
            "extended_15ghz_practical subset_of full_declared_range_stress",
        ],
        "evidence_boundary": {
            "class": EVIDENCE_CLASS,
            "fresh_emx": False,
            "iid_population_estimate": False,
        },
    }


def _freeze_global_contract(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = _read_json(path)
        comparable_existing = dict(existing)
        comparable_new = dict(payload)
        comparable_existing.pop("frozen_utc", None)
        comparable_new.pop("frozen_utc", None)
        if comparable_existing != comparable_new:
            raise FileExistsError(f"existing range contract differs and will not be overwritten: {path}")
        return
    _write_json(path, payload)


def _load_identity_sources(workspace: Path) -> dict[str, Any]:
    repo = workspace / "github_release" / "rfic-transformer-inverse-design"
    target_path = (
        workspace
        / "reports/mars56_resume_20260806/fixed_physical_target_frame_10000"
        / "fixed_physical_target_frame_10000.json"
    )
    prediction_path = (
        workspace
        / "reports/real10k_fixed8000_proxy_report_20260824_v1"
        / "in_support_proxy_predictions.csv"
    )
    model_contract_path = (
        repo / "rfic_transformer_inverse_design/synthesis/real10k_model_contract.json"
    )
    model_dir = (
        workspace
        / "reports/model_training_monday_20260720/multifrequency_real10k_pilot_20260720"
        / "model_seed_20260711/A_center_15ghz_4d"
    )
    model_summary_path = model_dir / "physical_feature_tandem_inverse_summary.json"
    model_weights_path = model_dir / "physical_feature_tandem_inverse_weights.npz"
    training_table_path = (
        workspace
        / "reports/model_training_monday_20260720/multifrequency_real10k_table_20260720"
        / "multifrequency_physical_feature_training_table.csv"
    )
    previous_blocked_dir = (
        workspace
        / "reports/fixed8k_15ghz_application_panels_v1_20260824T171106Z"
    )
    previous_blocked_receipt_path = previous_blocked_dir / "FINAL_RECEIPT.json"
    previous_blocked_mismatch_path = previous_blocked_dir / "EXISTING_HEADLINE_MISMATCH.json"
    expected = {
        target_path: TARGET_SHA256,
        prediction_path: PREDICTION_SHA256,
        model_contract_path: MODEL_CONTRACT_SHA256,
        model_summary_path: MODEL_SUMMARY_SHA256,
        model_weights_path: MODEL_WEIGHTS_SHA256,
        training_table_path: TRAINING_TABLE_SHA256,
        previous_blocked_receipt_path: PREVIOUS_BLOCKED_RECEIPT_SHA256,
        previous_blocked_mismatch_path: PREVIOUS_BLOCKED_MISMATCH_SHA256,
    }
    actual = {str(path): require_sha256(path, digest) for path, digest in expected.items()}
    return {
        "repo": repo,
        "target_path": target_path,
        "prediction_path": prediction_path,
        "model_contract_path": model_contract_path,
        "model_summary_path": model_summary_path,
        "model_weights_path": model_weights_path,
        "training_table_path": training_table_path,
        "previous_blocked_receipt_path": previous_blocked_receipt_path,
        "previous_blocked_mismatch_path": previous_blocked_mismatch_path,
        "hashes": actual,
    }


def _aligned_fixed8k(sources: Mapping[str, Any]) -> dict[str, Any]:
    target_payload = _read_json(Path(sources["target_path"]))
    prediction_rows = _read_csv(Path(sources["prediction_path"]))
    all_targets = target_payload.get("targets")
    if not isinstance(all_targets, list) or len(all_targets) != 10000:
        raise ValueError("frozen target artifact does not contain exactly 10,000 targets")
    target_by_id: dict[str, Mapping[str, Any]] = {}
    for row in all_targets:
        if not isinstance(row, Mapping):
            raise ValueError("target artifact contains a non-object target row")
        target_id = str(row.get("target_id") or "")
        if not target_id or target_id in target_by_id:
            raise ValueError("target artifact has missing or duplicate target IDs")
        target_by_id[target_id] = row
    ids = [str(row.get("target_id") or "") for row in prediction_rows]
    if len(prediction_rows) != EXPECTED_FULL_ROWS or len(set(ids)) != EXPECTED_FULL_ROWS:
        raise ValueError("prediction artifact must contain exactly 8,000 unique target IDs")
    missing = sorted(set(ids) - set(target_by_id))
    if missing:
        raise ValueError(f"prediction artifact includes IDs absent from target frame: {len(missing)}")
    target_columns_json = ("Lp_nH", "Ls_nH", "Q_min", "K_abs")
    target_columns_csv = ("target_Lp_nH", "target_Ls_nH", "target_Q_min", "target_K_abs")
    prediction_columns_csv = ("proxy_Lp_nH", "proxy_Ls_nH", "proxy_Q_min", "proxy_K_abs")
    targets: list[list[float]] = []
    predictions: list[list[float]] = []
    for index, (target_id, prediction_row) in enumerate(zip(ids, prediction_rows)):
        target_row = target_by_id[target_id]
        expected_values = np.asarray([float(target_row[name]) for name in target_columns_json])
        recorded_values = np.asarray([float(prediction_row[name]) for name in target_columns_csv])
        if not np.array_equal(expected_values, recorded_values):
            raise ValueError(f"prediction target values differ from frozen target row {target_id}")
        targets.append(recorded_values.tolist())
        predictions.append([float(prediction_row[name]) for name in prediction_columns_csv])
    target_matrix = np.asarray(targets, dtype=float)
    prediction_matrix = np.asarray(predictions, dtype=float)
    validate_target_prediction_matrices(ids, target_matrix, ids, prediction_matrix)
    if not np.all(target_matrix[:, 3] <= 0.8):
        raise ValueError("fixed8k target artifact includes |K| above 0.8")
    expected_legacy_ids = {
        str(row["target_id"])
        for row in all_targets
        if float(row["K_abs"]) <= 0.8
    }
    if set(ids) != expected_legacy_ids:
        raise ValueError("fixed8k IDs are not the exact target-only |K|<=0.8 legacy subset")
    return {
        "target_payload": target_payload,
        "prediction_rows": prediction_rows,
        "target_ids": ids,
        "targets": target_matrix,
        "predictions": prediction_matrix,
        "fixed8k_id_set_sha256": hashlib.sha256("\n".join(sorted(ids)).encode("ascii")).hexdigest(),
    }


def _identity_audit(sources: Mapping[str, Any], aligned: Mapping[str, Any]) -> dict[str, Any]:
    model_contract = _read_json(Path(sources["model_contract_path"]))
    model_summary = _read_json(Path(sources["model_summary_path"]))
    previous_blocked_receipt = _read_json(Path(sources["previous_blocked_receipt_path"]))
    previous_blocked_mismatch = _read_json(Path(sources["previous_blocked_mismatch_path"]))
    target_payload = aligned["target_payload"]
    checks = {
        "target_sha256_exact": sources["hashes"][str(sources["target_path"])] == TARGET_SHA256,
        "exactly_8000_unique_legacy_target_ids": len(set(aligned["target_ids"])) == EXPECTED_FULL_ROWS,
        "all_fixed8k_targets_K_abs_le_0p8": bool(np.all(aligned["targets"][:, 3] <= 0.8)),
        "prediction_sha256_exact": sources["hashes"][str(sources["prediction_path"])] == PREDICTION_SHA256,
        "one_prediction_per_target_id": len(aligned["prediction_rows"]) == len(set(aligned["target_ids"])),
        "no_missing_duplicate_nan_or_inf": bool(
            np.isfinite(aligned["targets"]).all() and np.isfinite(aligned["predictions"]).all()
        ),
        "model_id_exact": model_contract.get("model_id") == EXPECTED_MODEL_ID,
        "model_summary_sha256_exact": sources["hashes"][str(sources["model_summary_path"])] == MODEL_SUMMARY_SHA256,
        "model_weights_sha256_exact": sources["hashes"][str(sources["model_weights_path"])] == MODEL_WEIGHTS_SHA256,
        "center_frequency_15ghz": float(model_contract.get("target_frequency_ghz", math.nan)) == 15.0,
        "feature_order_exact": tuple(model_contract.get("input_columns") or ())
        == (
            "input__lp_nh_center",
            "input__ls_nh_center",
            "input__q_center",
            "input__k_abs_center",
        ),
        "qmin_lower_bound_semantics": (
            target_payload.get("q_target_semantics") == "minimum"
            and model_contract.get("q_training_definition") == "min(Qp, Qs)"
        ),
        "declared_lower_exact": model_contract.get("declared_support_lower") == [0.5, 0.5, 5.0, 0.0],
        "declared_upper_exact": model_contract.get("declared_support_upper") == [3.0, 3.0, 25.0, 0.8],
        "model_summary_training_table_sha_exact": model_summary.get("training_csv_sha256") == TRAINING_TABLE_SHA256,
        "model_summary_gradient_train_rows_exact": int(
            ((model_summary.get("evaluation_isolation") or {}).get("row_counts") or {}).get("train", -1)
        )
        == 7871,
        "previous_blocked_receipt_preserved": (
            previous_blocked_receipt.get("status") == "BLOCKED"
            and previous_blocked_receipt.get("report_released") is False
        ),
        "previous_definition_mismatch_preserved": (
            previous_blocked_mismatch.get("status") == "BLOCKED"
            and (previous_blocked_mismatch.get("headline_gate") or {}).get("status")
            == "MISMATCH"
        ),
    }
    if not all(checks.values()):
        failures = sorted(name for name, passed in checks.items() if not passed)
        raise ValueError(f"source identity audit failed: {failures}")
    return {
        "schema": "fixed8k_application_source_identity_audit.v1",
        "status": "PASS",
        "checks": checks,
        "frozen_target_artifact": {
            "path": str(Path(sources["target_path"]).resolve()),
            "sha256": TARGET_SHA256,
            "total_rows": 10000,
            "fixed8k_legacy_subset_rows": EXPECTED_FULL_ROWS,
            "fixed8k_id_set_sha256": aligned["fixed8k_id_set_sha256"],
            "sampler": (target_payload.get("frame_contract") or {}).get("sampler"),
            "finite_deterministic_frame": True,
            "iid_deployment_population_estimate": False,
        },
        "prediction_artifact": {
            "path": str(Path(sources["prediction_path"]).resolve()),
            "sha256": PREDICTION_SHA256,
            "row_count": EXPECTED_FULL_ROWS,
            "evidence_class": EVIDENCE_CLASS,
            "fresh_emx": False,
        },
        "preserved_blocked_evidence": {
            "receipt_path": str(Path(sources["previous_blocked_receipt_path"]).resolve()),
            "receipt_sha256": PREVIOUS_BLOCKED_RECEIPT_SHA256,
            "definition_mismatch_path": str(
                Path(sources["previous_blocked_mismatch_path"]).resolve()
            ),
            "definition_mismatch_sha256": PREVIOUS_BLOCKED_MISMATCH_SHA256,
        },
        "model": {
            "model_id": EXPECTED_MODEL_ID,
            "summary_path": str(Path(sources["model_summary_path"]).resolve()),
            "summary_sha256": MODEL_SUMMARY_SHA256,
            "weights_path": str(Path(sources["model_weights_path"]).resolve()),
            "weights_sha256": MODEL_WEIGHTS_SHA256,
            "model_contract_path": str(Path(sources["model_contract_path"]).resolve()),
            "model_contract_sha256": MODEL_CONTRACT_SHA256,
            "center_frequency_ghz": 15.0,
            "feature_definitions": {
                "Lp": "primary differential inductance scalar at 15 GHz",
                "Ls": "secondary differential inductance scalar at 15 GHz",
                "Qmin": "min(Qp, Qs) at 15 GHz; lower-bound engineering target",
                "K_abs": "absolute coupling coefficient |K| at 15 GHz",
            },
            "declared_target_ranges": {
                "Lp_nH": [0.5, 3.0],
                "Ls_nH": [0.5, 3.0],
                "Qmin": [5.0, 25.0],
                "K_abs": [0.0, 0.8],
            },
        },
    }


def _training_response_identity_audit(
    sources: Mapping[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    summary = _read_json(Path(sources["model_summary_path"]))
    rows = _read_csv(Path(sources["training_table_path"]))
    input_columns = tuple(summary.get("input_columns") or ())
    if input_columns != (
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__k_abs_center",
    ):
        raise ValueError("model summary has an unexpected training-response feature order")
    if len(rows) != int(summary.get("training_count", -1)):
        raise ValueError("training table row count does not match model summary")
    responses = np.asarray(
        [[float(row[column]) for column in input_columns] for row in rows],
        dtype=float,
    )
    split = summary.get("split_audit") or {}
    args = summary.get("arguments") or {}
    expected_train_sha = str((split.get("split_index_sha256") or {}).get("train") or "")
    cloud, indices, reconstructed_audit = reconstruct_exact_training_response_cloud(
        responses,
        seed=int(split.get("physical_cell_partition_seed")),
        validation_fraction=float(args.get("validation_fraction")),
        test_fraction=float(args.get("test_fraction")),
        bins=int(split.get("physical_cell_bins_per_dimension")),
        lower=split.get("physical_cell_lower"),
        upper=split.get("physical_cell_upper"),
        expected_train_count=int((split.get("row_counts") or {}).get("train")),
        expected_train_index_sha256=expected_train_sha,
    )
    recorded_hashes = split.get("split_index_sha256") or {}
    reconstructed_hashes = reconstructed_audit.get("split_index_sha256") or {}
    if reconstructed_hashes != recorded_hashes:
        raise ValueError("reconstructed train/validation/test index hashes differ from model summary")
    if reconstructed_audit.get("split_fingerprint_sha256") != split.get("split_fingerprint_sha256"):
        raise ValueError("reconstructed split fingerprint differs from model summary")
    if reconstructed_audit.get("cell_ids") != split.get("cell_ids"):
        raise ValueError("reconstructed physical-cell assignments differ from model summary")
    cloud_rows: list[dict[str, Any]] = []
    for cloud_index, source_position in enumerate(indices):
        source_row = rows[int(source_position)]
        cloud_rows.append(
            {
                "source_table_position": int(source_position),
                "source_row_index": source_row.get("row_index"),
                "source_evaluation": source_row.get("evaluation"),
                "Lp_nH": float(cloud[cloud_index, 0]),
                "Ls_nH": float(cloud[cloud_index, 1]),
                "Qmin": float(cloud[cloud_index, 2]),
                "K_abs": float(cloud[cloud_index, 3]),
            }
        )
    cloud_path = output_dir / "exact_gradient_training_response_cloud.csv"
    _write_csv(cloud_path, cloud_rows)
    return {
        "schema": "exact_gradient_training_response_cloud_binding.v1",
        "status": "PASS",
        "model_id": EXPECTED_MODEL_ID,
        "source_training_table": {
            "path": str(Path(sources["training_table_path"]).resolve()),
            "sha256": TRAINING_TABLE_SHA256,
            "source_rows": len(rows),
        },
        "gradient_training_response_cloud": {
            "path": str(cloud_path),
            "sha256": sha256_file(cloud_path),
            "row_count": len(indices),
            "feature_order": list(FEATURE_NAMES),
            "train_split_index_sha256": expected_train_sha,
            "split_fingerprint_sha256": split.get("split_fingerprint_sha256"),
        },
        "identity_checks": {
            "source_table_sha_exact": True,
            "gradient_train_row_count_exact": len(indices) == 7871,
            "all_split_index_hashes_exact": reconstructed_hashes == recorded_hashes,
            "split_fingerprint_exact": True,
            "cell_assignments_exact": True,
            "finite_response_values": bool(np.isfinite(cloud).all()),
        },
        "boundary": (
            "This is the exact feature cloud for rows that performed gradient updates. "
            "It is not validation, test, fixed8k, synthetic, or another model's data."
        ),
    }


def _panel_membership_rows(
    ids: Sequence[str],
    masks: Mapping[str, np.ndarray],
    reasons: Mapping[str, Sequence[str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, target_id in enumerate(ids):
        rows.append(
            {
                "target_id": target_id,
                CORE_PANEL.name: int(bool(masks[CORE_PANEL.name][index])),
                EXTENDED_PANEL.name: int(bool(masks[EXTENDED_PANEL.name][index])),
                FULL_PANEL.name: int(bool(masks[FULL_PANEL.name][index])),
                "core_exclusion_reason": reasons[CORE_PANEL.name][index],
                "extended_exclusion_reason": reasons[EXTENDED_PANEL.name][index],
            }
        )
    return rows


def _panel_counts_rows(
    masks: Mapping[str, np.ndarray],
    exclusion_counts: Mapping[str, Mapping[str, int]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    panel_lookup = {panel.name: panel for panel in PANELS}
    for name in (CORE_PANEL.name, EXTENDED_PANEL.name, FULL_PANEL.name):
        count = int(np.sum(masks[name]))
        panel = panel_lookup[name]
        excluded = exclusion_counts[name]
        rows.append(
            {
                "panel": name,
                "role": panel.role,
                "panel_count": count,
                "percentage_of_original_8000": count / EXPECTED_FULL_ROWS,
                "Lp_filter_excluded": int(excluded.get("Lp_outside_range", 0)),
                "Ls_filter_excluded": int(excluded.get("Ls_outside_range", 0)),
                "Qmin_filter_excluded": int(excluded.get("Qmin_outside_range", 0)),
                "K_filter_excluded": int(excluded.get("K_abs_outside_range", 0)),
                "ratio_filter_excluded": int(excluded.get("Ls_over_Lp_outside_range", 0)),
                "multiple_filters_excluded": int(excluded.get("multiple_filters", 0)),
                "core_subset_extended": bool(np.all(~masks[CORE_PANEL.name] | masks[EXTENDED_PANEL.name])),
                "extended_subset_full": bool(np.all(~masks[EXTENDED_PANEL.name] | masks[FULL_PANEL.name])),
                "target_only_filtering": True,
                "definition": panel.definition(),
            }
        )
    return rows


def _per_target_rows(
    aligned: Mapping[str, Any],
    masks: Mapping[str, np.ndarray],
    errors: Mapping[str, np.ndarray],
) -> list[dict[str, Any]]:
    targets = aligned["targets"]
    predictions = aligned["predictions"]
    raw = errors["raw"]
    absolute = errors["absolute"]
    normalized = errors["normalized_absolute"]
    rows: list[dict[str, Any]] = []
    for index, target_id in enumerate(aligned["target_ids"]):
        row: dict[str, Any] = {
            "target_id": target_id,
            CORE_PANEL.name: int(bool(masks[CORE_PANEL.name][index])),
            EXTENDED_PANEL.name: int(bool(masks[EXTENDED_PANEL.name][index])),
            FULL_PANEL.name: int(bool(masks[FULL_PANEL.name][index])),
        }
        for feature_index, feature in enumerate(FEATURE_NAMES):
            row[f"target_{feature}"] = float(targets[index, feature_index])
            row[f"predicted_{feature}"] = float(predictions[index, feature_index])
            row[f"raw_{feature}_error"] = float(raw[index, feature_index])
            row[f"abs_{feature}_error"] = float(absolute[index, feature_index])
            row[f"normalized_abs_{feature}_error"] = float(normalized[index, feature_index])
        row.update(
            {
                "Q_shortfall": float(errors["q_shortfall"][index]),
                "Q_target_met": int(bool(errors["q_target_met"][index])),
                "joint_normalized_rmse": float(errors["joint_normalized_rmse"][index]),
                "strict_max_feature_error": float(errors["strict_max_feature_error"][index]),
                "legacy_engineering_joint_normalized_rmse": float(
                    errors["legacy_engineering_joint_normalized_rmse"][index]
                ),
            }
        )
        rows.append(row)
    return rows


def _ecdf(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ordered = np.sort(np.asarray(values, dtype=float))
    return ordered, np.arange(1, len(ordered) + 1, dtype=float) / len(ordered)


def _save_figure(fig: plt.Figure, stem: Path) -> tuple[Path, Path]:
    png = stem.with_suffix(".png")
    svg = stem.with_suffix(".svg")
    fig.savefig(png, dpi=220, bbox_inches="tight", facecolor="white")
    fig.savefig(svg, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return png, svg


def _figure_metadata(panel_counts: Mapping[str, int]) -> dict[str, Any]:
    return {
        "panel_denominators": dict(panel_counts),
        "panel_definitions": {panel.name: panel.definition() for panel in PANELS},
        "normalization_spans": dict(zip(FEATURE_NAMES, NORMALIZATION_SPANS.tolist())),
        "target_only_filtering": True,
        "model_id": EXPECTED_MODEL_ID,
        "evidence_class": EVIDENCE_CLASS,
        "fresh_emx": False,
        "iid_population_estimate": False,
    }


def _bind_figure(
    png: Path,
    svg: Path,
    source_files: Sequence[Path],
    metadata: Mapping[str, Any],
) -> None:
    for figure in (png, svg):
        payload = figure_sidecar(
            figure_path=figure,
            source_files=source_files,
            metadata=metadata,
        )
        validate_figure_sidecar_sources(payload)
        _write_json(Path(str(figure) + ".json"), payload)


def _plot_normalized_cdf(
    output: Path,
    masks: Mapping[str, np.ndarray],
    errors: Mapping[str, np.ndarray],
    source_csv: Path,
) -> None:
    panel_names = (CORE_PANEL.name, EXTENDED_PANEL.name, FULL_PANEL.name)
    labels = ("Lp", "Ls", "Q absolute", "|K|")
    colors = ("#0B6EDE", "#E4572E", "#7A4DD8", "#008F7A")
    normalized = np.asarray(errors["normalized_absolute"], dtype=float) * 100.0
    xmax = max(5.0, math.ceil(float(np.max(normalized)) / 5.0) * 5.0)
    fig, axes = plt.subplots(1, 3, figsize=(18, 6.6), sharex=True, sharey=True)
    for axis, panel_name in zip(axes, panel_names):
        mask = np.asarray(masks[panel_name], dtype=bool)
        panel = next(item for item in PANELS if item.name == panel_name)
        percentile_lines: list[str] = []
        for index, (label, color) in enumerate(zip(labels, colors)):
            x, y = _ecdf(normalized[mask, index])
            axis.plot(x, y, lw=2.2, color=color, label=label)
            p50, p90, p95 = np.percentile(normalized[mask, index], [50, 90, 95])
            percentile_lines.append(f"{label}: {p50:.1f}/{p90:.1f}/{p95:.1f}%")
        axis.set_title(f"{panel_name}\nn={int(np.sum(mask))}", fontsize=11, fontweight="bold")
        axis.text(
            0.02,
            0.98,
            panel.definition(),
            transform=axis.transAxes,
            va="top",
            fontsize=7.3,
            wrap=True,
            bbox={"facecolor": "white", "edgecolor": "#D5DCE5", "alpha": 0.88},
        )
        axis.text(
            0.98,
            0.05,
            "P50/P90/P95\n" + "\n".join(percentile_lines),
            transform=axis.transAxes,
            ha="right",
            va="bottom",
            fontsize=7.5,
            bbox={"facecolor": "white", "edgecolor": "#D5DCE5", "alpha": 0.9},
        )
        axis.grid(alpha=0.25)
        axis.set_xlim(0.0, xmax)
        axis.set_ylim(0.0, 1.0)
        axis.set_xlabel("Normalized absolute error (% of frozen declared span)")
    axes[0].set_ylabel("Empirical CDF")
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.91),
        ncol=len(legend_labels),
        fontsize=8,
        frameon=False,
    )
    fig.suptitle(
        "Symmetric four-feature fidelity: normalized absolute-error CDF",
        fontsize=17,
        fontweight="bold",
    )
    fig.text(
        0.5,
        0.01,
        "Spans: Lp=2.5 nH, Ls=2.5 nH, Q=20, |K|=0.8. Target-only filters. "
        "Frozen-forward proxy diagnostic; not fresh EMX.",
        ha="center",
        fontsize=9,
        color="#34495E",
    )
    fig.tight_layout(rect=(0, 0.055, 1, 0.84))
    png, svg = _save_figure(fig, output / "normalized_absolute_error_cdf")
    counts = {name: int(np.sum(mask)) for name, mask in masks.items()}
    metadata = _figure_metadata(counts)
    metadata["metric_family"] = SYMMETRIC_METRIC_ID
    metadata["q_error_definition"] = "absolute Q error"
    _bind_figure(png, svg, [source_csv], metadata)


def _plot_q_requirement(
    output: Path,
    masks: Mapping[str, np.ndarray],
    errors: Mapping[str, np.ndarray],
    source_csv: Path,
) -> None:
    panel_names = (CORE_PANEL.name, EXTENDED_PANEL.name, FULL_PANEL.name)
    shortfall = np.asarray(errors["q_shortfall"], dtype=float)
    q_met = np.asarray(errors["q_target_met"], dtype=bool)
    fig, axes = plt.subplots(2, 3, figsize=(18, 9))
    for column, panel_name in enumerate(panel_names):
        mask = np.asarray(masks[panel_name], dtype=bool)
        met_fraction = float(np.mean(q_met[mask]))
        axes[0, column].bar(
            ["Target met", "Unmet"],
            [met_fraction * 100.0, (1.0 - met_fraction) * 100.0],
            color=["#008F7A", "#E4572E"],
        )
        axes[0, column].set_ylim(0, 100)
        axes[0, column].set_ylabel("Fraction of panel (%)")
        axes[0, column].set_title(f"{panel_name}\nn={int(np.sum(mask))}", fontsize=11, fontweight="bold")
        for bar in axes[0, column].patches:
            axes[0, column].text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 2,
                f"{bar.get_height():.1f}%",
                ha="center",
                fontsize=9,
            )
        panel_shortfall = shortfall[mask]
        unmet_shortfall = panel_shortfall[panel_shortfall > 0.0]
        x_all, y_all = _ecdf(panel_shortfall)
        axes[1, column].plot(x_all, y_all, color="#0B6EDE", lw=2.2, label=f"all n={len(panel_shortfall)}")
        if len(unmet_shortfall):
            x_unmet, y_unmet = _ecdf(unmet_shortfall)
            axes[1, column].plot(
                x_unmet,
                y_unmet,
                color="#E4572E",
                lw=2.2,
                label=f"unmet only n={len(unmet_shortfall)}",
            )
        p50, p90, p95 = np.percentile(panel_shortfall, [50, 90, 95])
        unmet_text = "none"
        if len(unmet_shortfall):
            up50, up90, up95 = np.percentile(unmet_shortfall, [50, 90, 95])
            unmet_text = f"{up50:.2f}/{up90:.2f}/{up95:.2f}"
        axes[1, column].text(
            0.98,
            0.05,
            f"all mean={np.mean(panel_shortfall):.2f}\nall P50/90/95={p50:.2f}/{p90:.2f}/{p95:.2f}\n"
            f"unmet P50/90/95={unmet_text}",
            transform=axes[1, column].transAxes,
            ha="right",
            va="bottom",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "#D5DCE5", "alpha": 0.9},
        )
        axes[1, column].set_xlabel("Q one-sided shortfall")
        axes[1, column].set_ylabel("Empirical CDF")
        axes[1, column].grid(alpha=0.25)
        axes[1, column].legend(loc="upper left", fontsize=8)
    fig.suptitle("Qmin engineering lower-bound requirement", fontsize=17, fontweight="bold")
    fig.text(
        0.5,
        0.012,
        "Q_shortfall=max(target Qmin - predicted Qmin, 0). Predictions above target have zero shortfall. "
        "This is not absolute Q prediction error. Frozen-forward proxy diagnostic; not fresh EMX.",
        ha="center",
        fontsize=9,
        color="#34495E",
    )
    fig.tight_layout(rect=(0, 0.055, 1, 0.94))
    png, svg = _save_figure(fig, output / "q_engineering_requirement")
    counts = {name: int(np.sum(mask)) for name, mask in masks.items()}
    metadata = _figure_metadata(counts)
    metadata["q_definition"] = "max(target_Qmin - predicted_Qmin, 0)"
    metadata["unmet_only_has_separate_denominator"] = True
    _bind_figure(png, svg, [source_csv], metadata)


def _plot_tolerance_success(
    output: Path,
    tolerance_rows: Sequence[Mapping[str, Any]],
    source_csv: Path,
) -> None:
    panel_names = (CORE_PANEL.name, EXTENDED_PANEL.name, FULL_PANEL.name)
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.6), sharex=True, sharey=True)
    for axis, panel_name in zip(axes, panel_names):
        rows = [row for row in tolerance_rows if row["panel"] == panel_name]
        series = (
            (LEGACY_METRIC_ID, "Legacy engineering joint score", "#7A4DD8", "^"),
            (SYMMETRIC_JOINT_METRIC_ID, "Symmetric absolute-Q joint RMSE", "#0B6EDE", "o"),
            (STRICT_METRIC_ID, "Strict all-four absolute-Q criterion", "#E4572E", "s"),
        )
        x = np.asarray(sorted({float(row["tolerance"]) * 100.0 for row in rows}))
        for metric_id, label, color, marker in series:
            metric_rows = [row for row in rows if row["metric_definition"] == metric_id]
            metric_rows.sort(key=lambda row: float(row["tolerance"]))
            y = np.asarray(
                [float(row["success_fraction"]) * 100.0 for row in metric_rows]
            )
            axis.plot(x, y, marker=marker, lw=2.2, color=color, label=label)
        axis.set_title(f"{panel_name}\nn={int(rows[0]['denominator'])}", fontsize=11, fontweight="bold")
        axis.set_xlabel("Normalized tolerance (%)")
        axis.grid(alpha=0.25)
        axis.set_xticks(x)
    axes[0].set_ylabel("Descriptive fixed-frame success rate (%)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.91),
        ncol=3,
        fontsize=8,
        frameon=False,
    )
    fig.suptitle("Tolerance rates by explicit metric definition", fontsize=17, fontweight="bold")
    fig.text(
        0.5,
        0.012,
        "Legacy uses one-sided Q shortfall; symmetric and strict use absolute Q error. "
        "Deterministic finite-frame rates, not accuracy, confidence, or deployment probability. Not fresh EMX.",
        ha="center",
        fontsize=9,
        color="#34495E",
    )
    fig.tight_layout(rect=(0, 0.06, 1, 0.84))
    png, svg = _save_figure(fig, output / "tolerance_success_rates")
    counts = {
        panel_name: int(next(row["denominator"] for row in tolerance_rows if row["panel"] == panel_name))
        for panel_name in panel_names
    }
    metadata = _figure_metadata(counts)
    metadata["metric_definitions"] = [
        LEGACY_METRIC_ID,
        SYMMETRIC_JOINT_METRIC_ID,
        STRICT_METRIC_ID,
    ]
    _bind_figure(png, svg, [source_csv], metadata)


def _support_rows(
    aligned: Mapping[str, Any],
    masks: Mapping[str, np.ndarray],
    errors: Mapping[str, np.ndarray],
    distances: np.ndarray,
) -> list[dict[str, Any]]:
    distance_values = np.asarray(distances, dtype=float)
    edges = np.quantile(distance_values, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    quintiles = np.searchsorted(edges[1:-1], distance_values, side="right") + 1
    targets = np.asarray(aligned["targets"], dtype=float)
    absolute = np.asarray(errors["absolute"], dtype=float)
    rows: list[dict[str, Any]] = []
    for index, target_id in enumerate(aligned["target_ids"]):
        rows.append(
            {
                "target_id": target_id,
                CORE_PANEL.name: int(bool(masks[CORE_PANEL.name][index])),
                EXTENDED_PANEL.name: int(bool(masks[EXTENDED_PANEL.name][index])),
                FULL_PANEL.name: int(bool(masks[FULL_PANEL.name][index])),
                "normalized_nearest_neighbor_distance": float(distance_values[index]),
                "support_distance_quintile": int(quintiles[index]),
                "joint_normalized_rmse": float(errors["joint_normalized_rmse"][index]),
                "strict_max_feature_error": float(errors["strict_max_feature_error"][index]),
                "legacy_engineering_joint_score": float(
                    errors["legacy_engineering_joint_normalized_rmse"][index]
                ),
                "Lp_abs_error": float(absolute[index, 0]),
                "Ls_abs_error": float(absolute[index, 1]),
                "Q_abs_error": float(absolute[index, 2]),
                "Q_shortfall": float(errors["q_shortfall"][index]),
                "K_abs_error": float(absolute[index, 3]),
                "target_Lp": float(targets[index, 0]),
                "target_Ls": float(targets[index, 1]),
                "target_Qmin": float(targets[index, 2]),
                "target_K_abs": float(targets[index, 3]),
            }
        )
    return rows


def _plot_support_distance_diagnostics(
    output: Path,
    aligned: Mapping[str, Any],
    errors: Mapping[str, np.ndarray],
    distances: np.ndarray,
    quintile_rows: Sequence[Mapping[str, Any]],
    support_csv: Path,
    quintile_csv: Path,
) -> None:
    targets = np.asarray(aligned["targets"], dtype=float)
    absolute = np.asarray(errors["absolute"], dtype=float)
    joint = np.asarray(errors["joint_normalized_rmse"], dtype=float) * 100.0
    shortfall = np.asarray(errors["q_shortfall"], dtype=float)
    distance_values = np.asarray(distances, dtype=float)
    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    scatter = {
        "s": 8,
        "alpha": 0.24,
        "color": "#0B6EDE",
        "edgecolors": "none",
        "rasterized": True,
    }
    axes[0, 0].scatter(distance_values, joint, **scatter)
    axes[0, 0].set_xlabel("Normalized nearest-neighbor distance")
    axes[0, 0].set_ylabel("Symmetric joint RMSE (%)")
    axes[0, 0].set_title("Symmetric fidelity vs training support distance")

    axes[0, 1].scatter(targets[:, 2], shortfall, **scatter)
    axes[0, 1].set_xlabel("Target Qmin")
    axes[0, 1].set_ylabel("Q one-sided shortfall")
    axes[0, 1].set_title("Q engineering shortfall vs target Qmin")

    axes[0, 2].scatter(targets[:, 3], absolute[:, 3], **scatter)
    axes[0, 2].set_xlabel("Target |K|")
    axes[0, 2].set_ylabel("|K| absolute error")
    axes[0, 2].set_title("Coupling error vs target |K|")

    axes[1, 0].scatter(targets[:, 0], absolute[:, 0], **scatter)
    axes[1, 0].set_xlabel("Target Lp (nH)")
    axes[1, 0].set_ylabel("Lp absolute error (nH)")
    axes[1, 0].set_title("Lp error vs target Lp")

    axes[1, 1].scatter(targets[:, 1], absolute[:, 1], **scatter)
    axes[1, 1].set_xlabel("Target Ls (nH)")
    axes[1, 1].set_ylabel("Ls absolute error (nH)")
    axes[1, 1].set_title("Ls error vs target Ls")

    x = np.arange(1, 6)
    means = np.asarray([float(row["joint_error_mean"]) * 100.0 for row in quintile_rows])
    p95 = np.asarray([float(row["joint_error_p95"]) * 100.0 for row in quintile_rows])
    width = 0.36
    axes[1, 2].bar(x - width / 2, means, width, label="Mean", color="#0B6EDE")
    axes[1, 2].bar(x + width / 2, p95, width, label="P95", color="#E4572E")
    axes[1, 2].set_xticks(x)
    axes[1, 2].set_xlabel("Support-distance quintile (1=nearest)")
    axes[1, 2].set_ylabel("Symmetric joint RMSE (%)")
    axes[1, 2].set_title("Error summaries by support-distance quintile")
    axes[1, 2].legend(fontsize=8)

    for axis in axes.flat:
        axis.grid(alpha=0.22)
    fig.suptitle("Training-support diagnostics for the frozen fixed8k frame", fontsize=17, fontweight="bold")
    fig.text(
        0.5,
        0.012,
        "Rectangular range support does not guarantee joint physical feasibility. Distance is diagnostic only; "
        "it does not prove physical impossibility. Frozen-forward proxy diagnostic; not fresh EMX.",
        ha="center",
        fontsize=9,
        color="#34495E",
    )
    fig.tight_layout(rect=(0, 0.055, 1, 0.94))
    png, svg = _save_figure(fig, output / "support_distance_diagnostics")
    metadata = _figure_metadata({FULL_PANEL.name: EXPECTED_FULL_ROWS})
    metadata["primary_vertical_metric"] = SYMMETRIC_METRIC_ID
    metadata["support_feature_order"] = list(FEATURE_NAMES)
    metadata["support_distance_spans"] = NORMALIZATION_SPANS.tolist()
    _bind_figure(png, svg, [support_csv, quintile_csv], metadata)


def _plot_panel_coverage_and_counts(
    output: Path,
    count_rows: Sequence[Mapping[str, Any]],
    source_csv: Path,
    contract_path: Path,
) -> None:
    labels = ["Core", "Extended", "Full"]
    counts = np.asarray([int(row["panel_count"]) for row in count_rows])
    percentages = np.asarray(
        [float(row["percentage_of_original_8000"]) * 100.0 for row in count_rows]
    )
    fig, axes = plt.subplots(1, 2, figsize=(16, 6.8), gridspec_kw={"width_ratios": [1.05, 1.45]})
    bars = axes[0].bar(labels, counts, color=["#0B6EDE", "#008F7A", "#7A4DD8"])
    axes[0].set_ylabel("Target count")
    axes[0].set_title("Target-only panel coverage")
    axes[0].set_ylim(0, 8800)
    axes[0].grid(axis="y", alpha=0.22)
    for bar, count, percentage in zip(bars, counts, percentages):
        axes[0].text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 150,
            f"n={count}\n{percentage:.2f}%",
            ha="center",
            fontsize=10,
            fontweight="bold",
        )
    axes[1].axis("off")
    definitions = "\n\n".join(
        f"{label} (n={int(row['panel_count'])})\n{row['definition']}"
        for label, row in zip(labels, count_rows)
    )
    axes[1].text(
        0.02,
        0.96,
        definitions
        + "\n\nSubset checks: core subset extended = PASS; extended subset full = PASS.\n"
        "Literature-informed project application ranges; not an industry standard.\n"
        "Core and extended are post-hoc application-aligned target-only strata.",
        va="top",
        fontsize=10,
        linespacing=1.35,
        bbox={"facecolor": "#F7F9FC", "edgecolor": "#D5DCE5", "pad": 12},
    )
    fig.suptitle("Fixed8k application-panel counts and subset hierarchy", fontsize=17, fontweight="bold")
    fig.text(
        0.5,
        0.015,
        "N=8000 deterministic space-filling in-support targets. Frozen-forward proxy diagnostic; not fresh EMX.",
        ha="center",
        fontsize=9,
        color="#34495E",
    )
    fig.tight_layout(rect=(0, 0.055, 1, 0.93))
    png, svg = _save_figure(fig, output / "panel_coverage_and_counts")
    metadata = _figure_metadata(
        {str(row["panel"]): int(row["panel_count"]) for row in count_rows}
    )
    metadata["subset_relations"] = ["core subset extended", "extended subset full"]
    _bind_figure(png, svg, [source_csv, contract_path], metadata)


def _plot_panel_metric_comparison(
    output: Path,
    summary_rows: Sequence[Mapping[str, Any]],
    source_csv: Path,
) -> None:
    labels = ["Core", "Extended", "Full"]
    rows = list(summary_rows)
    x = np.arange(3)
    width = 0.34
    fig, axes = plt.subplots(3, 3, figsize=(18, 13))

    pair_specs = (
        (axes[0, 0], "Lp_mae_nH", "Lp_rmse_nH", "Lp error (nH)"),
        (axes[0, 1], "Ls_mae_nH", "Ls_rmse_nH", "Ls error (nH)"),
        (axes[0, 2], "Q_absolute_mae", "Q_absolute_rmse", "Q absolute error"),
        (axes[1, 0], "K_mae", "K_rmse", "|K| absolute error"),
    )
    for axis, mae_key, rmse_key, title in pair_specs:
        mae = [float(row[mae_key]) for row in rows]
        rmse = [float(row[rmse_key]) for row in rows]
        axis.bar(x - width / 2, mae, width, label="MAE", color="#0B6EDE")
        axis.bar(x + width / 2, rmse, width, label="RMSE", color="#E4572E")
        axis.set_title(title)
        axis.legend(fontsize=8)

    single_specs = (
        (axes[1, 1], "Q_shortfall_mae", "Q shortfall MAE", 1.0),
        (axes[1, 2], "Q_target_met_fraction", "Q target-met fraction (%)", 100.0),
        (axes[2, 0], "median_joint_normalized_rmse", "Median symmetric joint RMSE (%)", 100.0),
        (axes[2, 1], "joint_rmse_le_10_fraction", "Symmetric joint RMSE <=10%", 100.0),
        (axes[2, 2], "strict_all_four_le_10_fraction", "Strict all-four <=10%", 100.0),
    )
    for axis, key, title, scale in single_specs:
        values = [float(row[key]) * scale for row in rows]
        axis.bar(x, values, color=["#0B6EDE", "#008F7A", "#7A4DD8"])
        axis.set_title(title)
    for axis in axes.flat:
        axis.set_xticks(x, labels)
        axis.grid(axis="y", alpha=0.22)
    fig.suptitle("Core vs extended vs full: physical-unit and fidelity diagnostics", fontsize=17, fontweight="bold")
    fig.text(
        0.5,
        0.012,
        "All normalized metrics use frozen spans [2.5 nH, 2.5 nH, 20, 0.8]. "
        "Target-only post-hoc strata; frozen-forward proxy diagnostic; not fresh EMX.",
        ha="center",
        fontsize=9,
        color="#34495E",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.94))
    png, svg = _save_figure(fig, output / "core_vs_extended_vs_full_metrics")
    metadata = _figure_metadata(
        {str(row["panel"]): int(row["count"]) for row in summary_rows}
    )
    metadata["normalized_metric_family"] = SYMMETRIC_METRIC_ID
    _bind_figure(png, svg, [source_csv], metadata)


def _write_advisor_notes(path: Path) -> None:
    path.write_text(
        "# Fixed8k 15-GHz application-panel notes\n\nStatus: **COMPLETE**\n\n"
        "1. The core panel represents the project's principal 15-GHz application region, not an industry standard.\n"
        "2. The extended panel includes broader but still practically relevant inductance, Q, coupling, and turns-ratio combinations.\n"
        "3. The full 8,000-target panel remains an unchanged declared-range stress test.\n"
        "4. Better results on a narrower target-only panel do not mean the model was retrained or improved; the panel answers a different application-specific question.\n"
        "5. Lp, Ls, Q, and |K| are not independent physical variables.\n"
        "6. Marginal range compliance does not establish joint physical feasibility.\n"
        "7. All numerical results are frozen-forward proxy diagnostics.\n"
        "8. None of these results is fresh-EMX accuracy.\n"
        "9. The fixed8k frame is deterministic and finite, not an iid random deployment population.\n"
        "10. The strict-all-feature success rate is more demanding than the joint-RMSE success rate.\n\n"
        "## Metric reconciliation\n\n"
        "Q shortfall is suitable for an engineering lower-bound because values above the requested minimum "
        "have zero requirement shortfall. Absolute Q error is required for symmetric response-target fidelity "
        "because over- and under-prediction both count. The percentages therefore differ by design. The legacy "
        "43.0% and symmetric 38.86% answer different questions and are not contradictory.\n\n"
        "The core panel contains only 150 post-hoc, application-aligned targets. Its result cannot be generalized "
        "to the full 8,000-target stress frame. All evidence remains frozen-forward proxy-only.\n",
        encoding="utf-8",
    )


def _write_advisor_report(
    path: Path,
    headlines: Mapping[str, str],
    output_dir: Path,
) -> None:
    path.write_text(
        "# Frozen fixed8k 15-GHz application-panel report\n\n"
        "**Evidence:** Frozen-forward proxy diagnostic; not fresh EMX. The 8,000-target frame is "
        "deterministic and finite, not an iid deployment-population estimate.\n\n"
        "## Primary: core_15ghz_application (n=150)\n\n"
        f"{headlines[CORE_PANEL.name]}\n\n"
        "Literature-informed project application range; not an industry standard. This is a post-hoc "
        "application-aligned stratification of the same frozen frame.\n\n"
        "## Secondary: extended_15ghz_practical (n=817)\n\n"
        f"{headlines[EXTENDED_PANEL.name]}\n\n"
        "## Appendix/stress: full_declared_range_stress (n=8000)\n\n"
        f"{headlines[FULL_PANEL.name]}\n\n"
        "N=8000 deterministic space-filling in-support targets. Here, in-support means membership in "
        "the declared marginal target ranges and legacy |K|<=0.8 frame; it does not prove joint physical "
        "realizability. Rectangular range support does not guarantee joint physical feasibility.\n\n"
        "## Historical-continuity box\n\n"
        f"{headlines['legacy_continuity']}\n\n"
        "The legacy 43.0% and the symmetric 38.86% answer different questions and are not contradictory.\n\n"
        "## Artifact index\n\n"
        f"All tables, figures, sidecars, receipts, and SHA bindings are in `{output_dir}`.\n",
        encoding="utf-8",
    )


def _sha_index(output_dir: Path) -> None:
    index_path = output_dir / "SHA256SUMS.txt"
    files = sorted(
        path for path in output_dir.rglob("*") if path.is_file() and path != index_path
    )
    lines = [f"{sha256_file(path)}  {path.relative_to(output_dir)}" for path in files]
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    workspace = Path(args.workspace_root).expanduser().resolve()
    utc_token = _utc_token(str(args.output_utc))
    generated_utc = datetime.strptime(utc_token, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    output_dir = create_no_clobber_directory(
        workspace
        / "reports"
        / f"fixed8k_15ghz_application_panels_reconciled_v1_{utc_token}"
    )
    figures_dir = output_dir / "figures"
    figures_dir.mkdir()

    sources = _load_identity_sources(workspace)
    aligned = _aligned_fixed8k(sources)
    audit = _identity_audit(sources, aligned)
    training_response_audit = _training_response_identity_audit(sources, output_dir)
    audit["exact_gradient_training_response_cloud"] = training_response_audit[
        "gradient_training_response_cloud"
    ]
    audit["generated_utc"] = generated_utc
    _write_json(output_dir / "SOURCE_IDENTITY_AUDIT.json", audit)
    _write_json(
        output_dir / "TRAINING_RESPONSE_IDENTITY_AUDIT.json",
        training_response_audit,
    )

    contract = _range_contract(generated_utc)
    global_contract = workspace / "reports/contracts/LITERATURE_INFORMED_15GHZ_RANGE_CONTRACT.json"
    _freeze_global_contract(global_contract, contract)
    shutil.copy2(global_contract, output_dir / "LITERATURE_INFORMED_15GHZ_RANGE_CONTRACT.json")

    masks, reasons, exclusion_counts = panel_membership(aligned["targets"])
    if int(np.sum(masks[FULL_PANEL.name])) != EXPECTED_FULL_ROWS:
        raise ValueError("full declared-range panel is not exactly 8,000 targets")
    membership_rows = _panel_membership_rows(aligned["target_ids"], masks, reasons)
    membership_path = output_dir / "panel_membership.csv"
    _write_csv(membership_path, membership_rows)
    count_rows = _panel_counts_rows(masks, exclusion_counts)
    counts_path = output_dir / "panel_counts_and_coverage.csv"
    _write_csv(counts_path, count_rows)

    errors = derive_errors(aligned["targets"], aligned["predictions"])
    per_target_rows = _per_target_rows(aligned, masks, errors)
    per_target_path = output_dir / "per_target_derived_errors.csv"
    _write_csv(per_target_path, per_target_rows)
    raw_rows = raw_feature_error_rows(
        masks,
        errors,
        aligned["targets"],
        aligned["predictions"],
    )
    raw_path = output_dir / "raw_feature_error_table.csv"
    _write_csv(raw_path, raw_rows)
    tolerance_rows = tolerance_success_rows(masks, errors)
    tolerance_path = output_dir / "tolerance_success_rates.csv"
    _write_csv(tolerance_path, tolerance_rows)
    joint_definition_rows = joint_metrics_by_definition_rows(masks, errors)
    joint_definition_path = output_dir / "joint_metrics_by_definition.csv"
    _write_csv(joint_definition_path, joint_definition_rows)
    tolerance_definition_rows = tolerance_success_by_definition_rows(masks, errors)
    tolerance_definition_path = output_dir / "tolerance_success_rates_by_definition.csv"
    _write_csv(tolerance_definition_path, tolerance_definition_rows)
    summary_rows = panel_summary_rows(masks, errors, aligned["targets"], aligned["predictions"])
    panel_summary_path = output_dir / "panel_summary_metrics.csv"
    _write_csv(panel_summary_path, summary_rows)

    reproduction_gate, independent_vectors = independent_metric_reproduction(
        aligned["targets"], aligned["predictions"], masks
    )
    crosschecks = {
        LEGACY_METRIC_ID: float(
            np.max(
                np.abs(
                    independent_vectors[LEGACY_METRIC_ID]
                    - np.asarray(errors["legacy_engineering_joint_normalized_rmse"])
                )
            )
        ),
        SYMMETRIC_METRIC_ID: float(
            np.max(
                np.abs(
                    independent_vectors[SYMMETRIC_METRIC_ID]
                    - np.asarray(errors["joint_normalized_rmse"])
                )
            )
        ),
        STRICT_METRIC_ID: float(
            np.max(
                np.abs(
                    independent_vectors[STRICT_METRIC_ID]
                    - np.asarray(errors["strict_max_feature_error"])
                )
            )
        ),
    }
    reproduction_gate["implementation_crosscheck_max_abs_difference"] = crosschecks
    reproduction_gate["implementation_crosscheck_status"] = (
        "PASS" if all(value <= 1e-15 for value in crosschecks.values()) else "FAIL"
    )
    if reproduction_gate["implementation_crosscheck_status"] != "PASS":
        reproduction_gate["status"] = "FAIL"

    reconciliation = {
        "schema": "fixed8k_metric_definition_reconciliation.v1",
        "status": reproduction_gate["status"],
        "generated_utc": generated_utc,
        "metric_families": [
            {
                "metric_id": LEGACY_METRIC_ID,
                "definitions": {
                    "e_Lp": "abs(pred_Lp - target_Lp) / 2.5",
                    "e_Ls": "abs(pred_Ls - target_Ls) / 2.5",
                    "e_Q_shortfall": "max(target_Qmin - pred_Qmin, 0) / 20",
                    "e_K": "abs(pred_K_abs - target_K_abs) / 0.8",
                    "joint": "sqrt((e_Lp^2 + e_Ls^2 + e_Q_shortfall^2 + e_K^2) / 4)",
                },
                "purpose": [
                    "continuity with the original fixed8k report",
                    "engineering lower-bound satisfaction",
                    "not symmetric target fidelity",
                ],
            },
            {
                "metric_id": SYMMETRIC_METRIC_ID,
                "definitions": {
                    "e_Q_abs": "abs(pred_Qmin - target_Qmin) / 20",
                    "joint": "sqrt((e_Lp^2 + e_Ls^2 + e_Q_abs^2 + e_K^2) / 4)",
                    "strict_all_feature_error": "max(e_Lp, e_Ls, e_Q_abs, e_K)",
                },
                "purpose": [
                    "symmetric response-target fidelity",
                    "comparison of Lp, Ls, Qmin, and |K|",
                    "primary metric for the revised report",
                ],
            },
        ],
        "non_interchangeability": (
            "The two metric families are not numerically interchangeable. A Q value above the "
            "target has zero engineering shortfall but nonzero absolute-Q fidelity error."
        ),
        "reproduction_gate": reproduction_gate,
        "preserved_blocked_evidence": audit["preserved_blocked_evidence"],
        "frozen_sources": {
            "target_sha256": TARGET_SHA256,
            "prediction_sha256": PREDICTION_SHA256,
            "model_id": EXPECTED_MODEL_ID,
            "model_summary_sha256": MODEL_SUMMARY_SHA256,
            "model_weights_sha256": MODEL_WEIGHTS_SHA256,
        },
    }
    reconciliation_path = output_dir / "METRIC_DEFINITION_RECONCILIATION.json"
    _write_json(reconciliation_path, reconciliation)
    legacy_reproduction = {
        "schema": "legacy_headline_reproduction.v1",
        "status": reproduction_gate["legacy_reproduction"]["status"],
        "generated_utc": generated_utc,
        "metric_id": LEGACY_METRIC_ID,
        "computed_from": "frozen per-target target/prediction rows",
        "expected_display_rounding": {"median_percent": 12.46, "success_percent": 43.0},
        "result": reproduction_gate["legacy_reproduction"],
        "hard_coded_result_values": False,
    }
    _write_json(output_dir / "LEGACY_HEADLINE_REPRODUCTION.json", legacy_reproduction)
    _write_json(
        output_dir / "ABSOLUTE_Q_COUNT_REPRODUCTION.json",
        {
            "schema": "absolute_q_count_reproduction.v1",
            "status": reproduction_gate["absolute_q_count_reproduction"]["status"],
            "generated_utc": generated_utc,
            "metric_family": SYMMETRIC_METRIC_ID,
            "expected_counts": {
                name: {
                    "denominator": values[0],
                    "joint_le_10": values[1],
                    "strict_le_10": values[2],
                }
                for name, values in EXPECTED_ABSOLUTE_Q_COUNTS.items()
            },
            "result": reproduction_gate["absolute_q_count_reproduction"],
        },
    )
    if reproduction_gate["status"] != "PASS":
        failure = {
            "schema": "fixed8k_reconciliation_gate_failure.v1",
            "status": "BLOCKED",
            "generated_utc": generated_utc,
            "reproduction_gate": reproduction_gate,
        }
        _write_json(output_dir / "RECONCILIATION_GATE_FAILURE.json", failure)
        receipt = {
            "schema": "fixed8k_15ghz_reconciled_final_receipt.v1",
            "status": "BLOCKED",
            "generated_utc": generated_utc,
            "output_directory": str(output_dir),
            "report_released": False,
            "blocker": "Independent metric reproduction failed.",
            "model_retrained": False,
            "model_inference_rerun": False,
            "emx_cadence_calibre_drc_run": False,
        }
        _write_json(output_dir / "FINAL_RECEIPT.json", receipt)
        _sha_index(output_dir)
        print(json.dumps(receipt, sort_keys=True))
        return 2

    _plot_normalized_cdf(figures_dir, masks, errors, per_target_path)
    _plot_q_requirement(figures_dir, masks, errors, per_target_path)
    _plot_tolerance_success(
        figures_dir,
        tolerance_definition_rows,
        tolerance_definition_path,
    )

    cloud_rows = _read_csv(output_dir / "exact_gradient_training_response_cloud.csv")
    training_cloud = np.asarray(
        [
            [float(row["Lp_nH"]), float(row["Ls_nH"]), float(row["Qmin"]), float(row["K_abs"])]
            for row in cloud_rows
        ],
        dtype=float,
    )
    if len(training_cloud) != 7871 or not np.isfinite(training_cloud).all():
        raise ValueError("identity-bound gradient-training response cloud is invalid")
    distances = normalized_nearest_neighbor_distance(aligned["targets"], training_cloud)
    support_rows = _support_rows(aligned, masks, errors, distances)
    support_path = output_dir / "support_diagnostic.csv"
    _write_csv(support_path, support_rows)
    quintile_rows = support_distance_quintiles(
        distances,
        errors["joint_normalized_rmse"],
        errors["q_shortfall"],
    )
    quintile_path = output_dir / "support_distance_quintile_summary.csv"
    _write_csv(quintile_path, quintile_rows)

    local_contract_path = output_dir / "LITERATURE_INFORMED_15GHZ_RANGE_CONTRACT.json"
    _plot_support_distance_diagnostics(
        figures_dir,
        aligned,
        errors,
        distances,
        quintile_rows,
        support_path,
        quintile_path,
    )
    _plot_panel_coverage_and_counts(
        figures_dir,
        count_rows,
        counts_path,
        local_contract_path,
    )
    _plot_panel_metric_comparison(figures_dir, summary_rows, panel_summary_path)

    headlines = report_headline_binding(reproduction_gate)
    advisor_notes_path = output_dir / "ADVISOR_REPORT_NOTES.md"
    advisor_report_path = output_dir / "ADVISOR_REPORT.md"
    _write_advisor_notes(advisor_notes_path)
    _write_advisor_report(advisor_report_path, headlines, output_dir)

    panel_lookup = {row["panel"]: row for row in summary_rows}
    report_summary = {
        "schema": "fixed8k_15ghz_application_panel_report_summary.v2",
        "status": "COMPLETE",
        "generated_utc": generated_utc,
        "output_directory": str(output_dir),
        "report": str(advisor_report_path),
        "contract": str(global_contract),
        "evidence_class": EVIDENCE_CLASS,
        "panel_summaries": panel_lookup,
        "joint_metrics_by_definition": joint_definition_rows,
        "reproduction_gate": reproduction_gate,
        "headlines": headlines,
        "support_distance_quintiles": quintile_rows,
        "scientific_boundary": (
            "Frozen-forward proxy diagnostic; not fresh EMX; deterministic finite fixed frame; "
            "not an iid deployment-population estimate."
        ),
    }
    _write_json(output_dir / "REPORT_SUMMARY.json", report_summary)
    receipt = {
        "schema": "fixed8k_15ghz_reconciled_final_receipt.v1",
        "status": "COMPLETE",
        "generated_utc": generated_utc,
        "output_directory": str(output_dir),
        "contract": str(global_contract),
        "report": str(advisor_report_path),
        "report_released": True,
        "reproduction_gate_status": reproduction_gate["status"],
        "previous_blocked_receipt_preserved": True,
        "model_retrained": False,
        "model_inference_rerun": False,
        "model_weights_changed": False,
        "target_rows_changed": False,
        "target_panel_membership_changed": False,
        "normalization_spans_changed": False,
        "emx_cadence_calibre_drc_run": False,
        "evidence_class": EVIDENCE_CLASS,
        "blocker": None,
    }
    _write_json(output_dir / "FINAL_RECEIPT.json", receipt)
    _sha_index(output_dir)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
