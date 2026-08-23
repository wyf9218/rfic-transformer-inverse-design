#!/usr/bin/env python3
"""Strict final audit for the accepted one-million real-EMX campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
CANONICAL_GEOMETRY_FIELDS = tuple(column.removeprefix("geom__") for column in GEOMETRY_COLUMNS)
GEOMETRY_FINGERPRINT_SCHEMA = "mars56_grounded_s4p_geometry_v1"
GEOMETRY_FINGERPRINT_QUANTIZATION_UM = 1.0e-6
FEATURE_RANGES = {
    "lp_nh_center": (0.5, 3.0),
    "ls_nh_center": (0.5, 3.0),
    "q_center": (5.0, 25.0),
    "k_abs_center": (0.0, 0.8),
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    campaign_root = Path(args.campaign_root).expanduser().resolve()
    final_pool = Path(args.final_pool_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_csv = final_pool / "dataset_rows.csv"
    pool_summary_path = final_pool / "accepted_pool_merge_summary.json"
    pool_summary = _read_json(pool_summary_path)
    dataset_audit = _scan_dataset(dataset_csv, final_pool, args)
    checkpoint_audit = _audit_checkpoints(campaign_root / "checkpoints", args)
    learning_curve_path = campaign_root / "model_learning_curve" / "physical_feature_model_learning_curve_summary.json"
    learning_curve = _read_json(learning_curve_path)
    fixed_common_panel_checks = _fixed_common_panel_checks(learning_curve, int(args.checkpoint_count))
    final_record = checkpoint_audit.get("records", [])[-1] if checkpoint_audit.get("records") else {}
    final_manifest = _read_json(Path(str(final_record.get("model_manifest") or "")))
    uniformity_path = Path(
        str((((final_manifest.get("artifacts") or {}).get("uniformity") or {}).get("path") or ""))
    )
    final_uniformity = _read_json(uniformity_path)
    uniformity_checks = _uniformity_checks(final_uniformity, int(args.expected_total))
    pool_identity_contract = _pool_geometry_identity_contract(pool_summary)

    checks = {
        "pool_summary_pass": pool_summary.get("overall_status") == "PASS",
        "pool_summary_count_matches_scan": int(pool_summary.get("row_count") or -1) == dataset_audit["row_count"],
        "pool_summary_geometry_identity_contract": pool_identity_contract["status"] == "PASS",
        "pool_summary_no_geometry_identity_mismatch": int(
            (pool_summary.get("reject_summary") or {}).get("geometry_identity_mismatch", -1)
        )
        == 0,
        "accepted_count_at_least_expected": dataset_audit["row_count"] >= int(args.expected_total),
        "all_rows_finite_and_in_range": dataset_audit["invalid_feature_or_range_count"] == 0,
        "all_rows_have_complete_geometry": dataset_audit["invalid_geometry_count"] == 0,
        "all_rows_have_saved_canonical_geometry_identity": dataset_audit["missing_geometry_identity_count"] == 0,
        "all_saved_geometry_identity_schemas_match": dataset_audit["geometry_identity_schema_mismatch_count"] == 0,
        "all_saved_geometry_identity_quantizations_match": dataset_audit[
            "geometry_identity_quantization_mismatch_count"
        ]
        == 0,
        "all_saved_geometry_fingerprints_match_recomputed": dataset_audit[
            "geometry_identity_fingerprint_mismatch_count"
        ]
        == 0,
        "shared_width_aliases_match_line_width": dataset_audit["shared_width_alias_mismatch_count"] == 0,
        "independent_geometry_unique": dataset_audit["duplicate_geometry_count"] == 0,
        "touchstone_paths_unique": dataset_audit["duplicate_touchstone_path_count"] == 0,
        "all_touchstone_paths_are_s4p": dataset_audit["invalid_touchstone_suffix_count"] == 0,
        "all_touchstone_files_nonempty": not bool(args.check_touchstone_exists)
        or dataset_audit["missing_or_empty_touchstone_count"] == 0,
        "all_frequency_metadata_match": dataset_audit["frequency_contract_failure_count"] == 0,
        "q_equals_min_qp_qs": dataset_audit["missing_qp_qs_count"] == 0
        and dataset_audit["q_min_consistency_failure_count"] == 0,
        "checkpoint_contract_pass": checkpoint_audit.get("overall_status") == "PASS",
        "learning_curve_has_ten_comparable_checkpoints": learning_curve.get("overall_status") == "PASS"
        and int(learning_curve.get("checkpoint_count") or 0) == int(args.checkpoint_count)
        and ((learning_curve.get("comparison_contract") or {}).get("comparable") is True),
        "fixed_common_test_panel_contract_pass": all(fixed_common_panel_checks.values()),
        "final_uniformity_contract_pass": all(uniformity_checks.values()),
        "final_model_manifest_pass": final_manifest.get("overall_status") == "PASS"
        and final_manifest.get("model_test_status") == "PASS"
        and int(final_manifest.get("accepted_checkpoint_count") or 0) == int(args.expected_total),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    summary_path = out_dir / "accepted_1m_campaign_completion_audit_summary.json"
    report_path = out_dir / "accepted_1m_campaign_completion_audit_report.md"
    marker_path = out_dir / "accepted_1m_campaign_completion.pass"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "ACCEPTED_1M_REAL_EMX_CAMPAIGN_COMPLETE" if status == "PASS" else "DO_NOT_CLAIM_ONE_MILLION_COMPLETE",
        "checks": checks,
        "pool_geometry_identity_contract": pool_identity_contract,
        "dataset_audit": dataset_audit,
        "checkpoint_audit": checkpoint_audit,
        "fixed_common_test_panel_checks": fixed_common_panel_checks,
        "final_uniformity_checks": uniformity_checks,
        "artifacts": {
            "dataset_csv": str(dataset_csv),
            "pool_summary": str(pool_summary_path),
            "learning_curve": str(learning_curve_path),
            "final_model_manifest": str(final_record.get("model_manifest") or ""),
            "final_uniformity": str(uniformity_path),
            "report": str(report_path),
            "pass_marker": str(marker_path),
        },
        "scientific_boundary": (
            "PASS proves the declared campaign data/model/uniformity evidence chain. It does not replace the separate "
            "sampled EMX-HFSS correlation or fabricated measurement evidence required for publication."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    if status == "PASS":
        marker_path.touch()
    elif marker_path.exists():
        marker_path.unlink()
    print(f"overall_status={status}")
    print(f"decision={payload['decision']}")
    print(f"row_count={dataset_audit['row_count']}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--final-pool-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-total", type=int, default=1_000_000)
    parser.add_argument("--checkpoint-count", type=int, default=10)
    parser.add_argument("--checkpoint-size", type=int, default=100_000)
    parser.add_argument("--check-touchstone-exists", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.expected_total < 1 or args.checkpoint_count < 1 or args.checkpoint_size < 1:
        parser.error("campaign counts must be positive")
    if args.expected_total != args.checkpoint_count * args.checkpoint_size:
        parser.error("expected total must equal checkpoint count times checkpoint size")
    return args


def _scan_dataset(dataset_csv: Path, pool_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    counts = {
        "row_count": 0,
        "invalid_feature_or_range_count": 0,
        "invalid_geometry_count": 0,
        "missing_geometry_identity_count": 0,
        "geometry_identity_schema_mismatch_count": 0,
        "geometry_identity_quantization_mismatch_count": 0,
        "geometry_identity_fingerprint_mismatch_count": 0,
        "shared_width_alias_mismatch_count": 0,
        "duplicate_geometry_count": 0,
        "duplicate_touchstone_path_count": 0,
        "invalid_touchstone_suffix_count": 0,
        "missing_or_empty_touchstone_count": 0,
        "frequency_contract_failure_count": 0,
        "missing_qp_qs_count": 0,
        "q_min_consistency_failure_count": 0,
    }
    examples: dict[str, list[Any]] = {key: [] for key in counts if key != "row_count"}
    geometry_digests: set[str] = set()
    touchstone_digests: set[bytes] = set()
    if not dataset_csv.is_file():
        counts["dataset_csv_missing"] = 1
        return {**counts, "examples": examples}
    with dataset_csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row_index, row in enumerate(reader):
            counts["row_count"] += 1
            features = {column: _finite(row.get(column)) for column in FEATURE_RANGES}
            if any(value is None for value in features.values()) or any(
                not (FEATURE_RANGES[column][0] <= float(value) <= FEATURE_RANGES[column][1])
                for column, value in features.items()
                if value is not None
            ):
                _failure(counts, examples, "invalid_feature_or_range_count", row_index)
            geometry = [_finite(row.get(column)) for column in GEOMETRY_COLUMNS]
            if any(value is None for value in geometry):
                _failure(counts, examples, "invalid_geometry_count", row_index)
            else:
                fingerprint = _canonical_geometry_fingerprint(row)
                if fingerprint is None:
                    _failure(counts, examples, "invalid_geometry_count", row_index)
                    continue
                if fingerprint in geometry_digests:
                    _failure(counts, examples, "duplicate_geometry_count", row_index)
                geometry_digests.add(fingerprint)
                saved_fingerprint = str(row.get("canonical_geometry_fingerprint_sha256") or "").strip()
                saved_schema = str(row.get("canonical_geometry_fingerprint_schema") or "").strip()
                saved_quantization = _finite(row.get("canonical_geometry_fingerprint_quantization_um"))
                if not saved_fingerprint or not saved_schema or saved_quantization is None:
                    _failure(counts, examples, "missing_geometry_identity_count", row_index)
                if saved_schema and saved_schema != GEOMETRY_FINGERPRINT_SCHEMA:
                    _failure(counts, examples, "geometry_identity_schema_mismatch_count", row_index)
                if saved_quantization is not None and not math.isclose(
                    float(saved_quantization),
                    GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
                    rel_tol=0.0,
                    abs_tol=0.0,
                ):
                    _failure(counts, examples, "geometry_identity_quantization_mismatch_count", row_index)
                if saved_fingerprint and saved_fingerprint != fingerprint:
                    _failure(counts, examples, "geometry_identity_fingerprint_mismatch_count", row_index)
                if not _shared_width_aliases_valid(row):
                    _failure(counts, examples, "shared_width_alias_mismatch_count", row_index)
            raw_path = str(row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
            path = Path(raw_path).expanduser()
            if raw_path and not path.is_absolute():
                path = (pool_dir / path).resolve()
            path_key = hashlib.blake2b(str(path).encode("utf-8"), digest_size=16).digest()
            if path_key in touchstone_digests:
                _failure(counts, examples, "duplicate_touchstone_path_count", row_index)
            touchstone_digests.add(path_key)
            if path.suffix.lower() != ".s4p":
                _failure(counts, examples, "invalid_touchstone_suffix_count", row_index)
            if args.check_touchstone_exists:
                try:
                    exists_nonempty = path.is_file() and path.stat().st_size > 0
                except OSError:
                    exists_nonempty = False
                if not exists_nonempty:
                    _failure(counts, examples, "missing_or_empty_touchstone_count", row_index)
            if not _frequency_matches(row, args):
                _failure(counts, examples, "frequency_contract_failure_count", row_index)
            qp = _finite(row.get("qp_center"))
            qs = _finite(row.get("qs_center"))
            q = features.get("q_center")
            if qp is None or qs is None:
                _failure(counts, examples, "missing_qp_qs_count", row_index)
            elif q is None or not math.isclose(float(q), min(float(qp), float(qs)), rel_tol=1.0e-8, abs_tol=1.0e-8):
                _failure(counts, examples, "q_min_consistency_failure_count", row_index)
    return {
        **counts,
        "geometry_fingerprint_schema": GEOMETRY_FINGERPRINT_SCHEMA,
        "geometry_fingerprint_quantization_um": GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
        "canonical_geometry_fields": list(CANONICAL_GEOMETRY_FIELDS),
        "unique_geometry_fingerprint_count": len(geometry_digests),
        "unique_geometry_digest_count": len(geometry_digests),
        "unique_touchstone_path_digest_count": len(touchstone_digests),
        "examples": examples,
    }


def _audit_checkpoints(checkpoint_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    records = []
    reasons = []
    for path in sorted(checkpoint_root.glob("checkpoint_*/checkpoint_record.json")):
        record = _read_json(path)
        manifest_path = Path(str(record.get("model_manifest") or ""))
        manifest = _read_json(manifest_path)
        manifest_sha = _sha256(manifest_path) if manifest_path.is_file() else ""
        bni_artifact = ((manifest.get("artifacts") or {}).get("balanced_mse_bni_ablation") or {})
        bni_artifact_path = Path(str(bni_artifact.get("path") or ""))
        bni_artifact_sha = _sha256(bni_artifact_path) if bni_artifact_path.is_file() else ""
        bni_summary = _read_json(bni_artifact_path)
        bni_bootstrap = bni_summary.get("paired_cluster_bootstrap") or {}
        mondrian_artifact = (
            (manifest.get("artifacts") or {}).get("physical_feature_mondrian_conformal_comparison") or {}
        )
        mondrian_artifact_path = Path(str(mondrian_artifact.get("path") or ""))
        mondrian_artifact_sha = _sha256(mondrian_artifact_path) if mondrian_artifact_path.is_file() else ""
        mondrian_summary = _read_json(mondrian_artifact_path)
        mondrian_support = (mondrian_summary.get("analysis") or {}).get("support") or {}
        item = {
            **record,
            "record_path": str(path),
            "manifest_exists": manifest_path.is_file(),
            "manifest_sha_matches_record": bool(manifest_sha)
            and manifest_sha == str(record.get("model_manifest_sha256") or ""),
            "manifest_checkpoint_index": manifest.get("checkpoint_index"),
            "manifest_accepted_count": manifest.get("accepted_checkpoint_count"),
            "manifest_model_test_status": manifest.get("model_test_status"),
            "manifest_broadband_readiness": manifest.get("broadband_sparameter_readiness_status"),
            "manifest_physical_cell_tail_error": manifest.get("physical_cell_tail_error_status"),
            "manifest_frequency_stability": manifest.get("physical_feature_frequency_stability_status"),
            "manifest_geometry_sensitivity": manifest.get("geometry_response_effective_dimension_status"),
            "manifest_frequency_self_transfer": manifest.get("frequency_self_transfer_status"),
            "manifest_frequency_sequence_architecture": manifest.get(
                "frequency_sequence_architecture_status"
            ),
            "manifest_geometry_multiplicity": manifest.get("inverse_geometry_multiplicity_status"),
            "manifest_geometry_multiplicity_stage": manifest.get("inverse_geometry_multiplicity_evidence_stage"),
            "manifest_geometry_multiplicity_top_k_eligible": manifest.get(
                "inverse_geometry_multiplicity_top_k_eligible"
            ),
            "manifest_conformal_calibration": manifest.get("physical_feature_conformal_calibration_status"),
            "manifest_mondrian_conformal": manifest.get("physical_feature_mondrian_conformal_status"),
            "manifest_mondrian_conformal_decision": manifest.get("physical_feature_mondrian_conformal_decision"),
            "manifest_mondrian_conformal_recommendation": manifest.get(
                "physical_feature_mondrian_conformal_recommendation"
            ),
            "manifest_mondrian_supported_cell_fraction": manifest.get(
                "physical_feature_mondrian_supported_cell_fraction"
            ),
            "manifest_mondrian_supported_row_fraction": manifest.get(
                "physical_feature_mondrian_supported_row_fraction"
            ),
            "manifest_low_frequency_physics": manifest.get("low_frequency_coupled_rl_consistency_status"),
            "manifest_local_refinement_plan": manifest.get("tandem_local_refinement_plan_status"),
            "manifest_boundary_ood_stress": manifest.get("physical_feature_boundary_ood_stress_status"),
            "manifest_physical_spec_spectral_expander": manifest.get("physical_spec_spectral_expander_status"),
            "manifest_balanced_mse_bni_status": manifest.get("balanced_mse_bni_ablation_status"),
            "manifest_balanced_mse_bni_decision_rule": manifest.get("balanced_mse_bni_ablation_decision_rule"),
            "manifest_balanced_mse_bni_row_ci_lower": manifest.get("balanced_mse_bni_row_improvement_ci_lower"),
            "manifest_balanced_mse_bni_equal_cell_ci_lower": manifest.get(
                "balanced_mse_bni_equal_cell_improvement_ci_lower"
            ),
            "manifest_balanced_mse_bni_p90_tail_ci_lower": manifest.get(
                "balanced_mse_bni_p90_tail_improvement_ci_lower"
            ),
            "balanced_mse_bni_artifact_exists_flag": bni_artifact.get("exists"),
            "balanced_mse_bni_artifact_path": str(bni_artifact_path),
            "balanced_mse_bni_artifact_exists": bni_artifact_path.is_file(),
            "balanced_mse_bni_artifact_sha256_recorded": bni_artifact.get("sha256"),
            "balanced_mse_bni_artifact_sha256_matches": bool(bni_artifact_sha)
            and bni_artifact_sha == str(bni_artifact.get("sha256") or ""),
            "balanced_mse_bni_artifact_status": bni_summary.get("overall_status"),
            "balanced_mse_bni_artifact_decision_rule": bni_summary.get("decision_rule"),
            "balanced_mse_bni_artifact_bootstrap_status": bni_bootstrap.get("status"),
            "balanced_mse_bni_artifact_row_ci_lower": bni_bootstrap.get("relative_improvement_ci_lower"),
            "balanced_mse_bni_artifact_equal_cell_ci_lower": bni_bootstrap.get(
                "cell_balanced_relative_improvement_ci_lower"
            ),
            "balanced_mse_bni_artifact_p90_tail_ci_lower": bni_bootstrap.get(
                "p90_tail_relative_improvement_ci_lower"
            ),
            "mondrian_conformal_artifact_exists_flag": mondrian_artifact.get("exists"),
            "mondrian_conformal_artifact_path": str(mondrian_artifact_path),
            "mondrian_conformal_artifact_exists": mondrian_artifact_path.is_file(),
            "mondrian_conformal_artifact_sha256_recorded": mondrian_artifact.get("sha256"),
            "mondrian_conformal_artifact_sha256_matches": bool(mondrian_artifact_sha)
            and mondrian_artifact_sha == str(mondrian_artifact.get("sha256") or ""),
            "mondrian_conformal_artifact_status": mondrian_summary.get("overall_status"),
            "mondrian_conformal_artifact_decision": mondrian_summary.get("decision"),
            "mondrian_conformal_artifact_recommendation": (
                mondrian_summary.get("recommendation") or {}
            ).get("decision"),
            "mondrian_conformal_artifact_checks_all_pass": bool(mondrian_summary.get("checks"))
            and all(value is True for value in (mondrian_summary.get("checks") or {}).values()),
            "mondrian_conformal_artifact_supported_cell_fraction": mondrian_support.get(
                "supported_evaluation_cell_fraction"
            ),
            "mondrian_conformal_artifact_supported_row_fraction": mondrian_support.get(
                "supported_evaluation_row_fraction"
            ),
        }
        records.append(item)
    expected_indices = list(range(1, int(args.checkpoint_count) + 1))
    expected_targets = [index * int(args.checkpoint_size) for index in expected_indices]
    actual_indices = [int(item.get("checkpoint_index") or -1) for item in records]
    actual_targets = [int(item.get("target_accepted_count") or -1) for item in records]
    if actual_indices != expected_indices:
        reasons.append(f"checkpoint indices={actual_indices}, expected={expected_indices}")
    if actual_targets != expected_targets:
        reasons.append(f"checkpoint targets={actual_targets}, expected={expected_targets}")
    for item in records:
        index = int(item.get("checkpoint_index") or -1)
        target = int(item.get("target_accepted_count") or -1)
        checks = {
            "model_test_status": item.get("model_test_status") == "PASS",
            "manifest_exists": item.get("manifest_exists") is True,
            "manifest_sha": item.get("manifest_sha_matches_record") is True,
            "manifest_index": int(item.get("manifest_checkpoint_index") or -1) == index,
            "manifest_count": int(item.get("manifest_accepted_count") or -1) == target,
            "broadband_readiness": item.get("manifest_broadband_readiness") == "PASS",
            "physical_cell_tail_error": item.get("manifest_physical_cell_tail_error") == "PASS",
            "frequency_stability": item.get("manifest_frequency_stability")
            == (
                "WAITING_FOR_200K"
                if index == 1
                else ("PASS" if index == 2 else "NOT_REPEATED_AFTER_200K")
            ),
            "geometry_sensitivity": item.get("manifest_geometry_sensitivity")
            == (
                "WAITING_FOR_300K"
                if index < 3
                else ("PASS" if index == 3 else "NOT_REPEATED_AFTER_300K")
            ),
            "frequency_self_transfer": item.get("manifest_frequency_self_transfer")
            == (
                "WAITING_FOR_300K"
                if index < 3
                else ("COMPLETE_REVIEW_REQUIRED" if index == 3 else "NOT_REPEATED_AFTER_300K")
            ),
            "frequency_sequence_architecture": item.get("manifest_frequency_sequence_architecture")
            == (
                "WAITING_FOR_300K"
                if index < 3
                else ("COMPLETE_REVIEW_REQUIRED" if index == 3 else "NOT_REPEATED_AFTER_300K")
            ),
            "geometry_multiplicity": item.get("manifest_geometry_multiplicity")
            == (
                "PASS"
                if index == 1
                else (
                    "COARSE_100K_COMPLETE_FINE_WAITING_FOR_500K"
                    if index < 5
                    else ("PASS" if index == 5 else "NOT_REPEATED_AFTER_500K")
                )
            ),
            "geometry_multiplicity_stage": item.get("manifest_geometry_multiplicity_stage")
            == (
                "exploratory_coarse"
                if index == 1
                else (
                    "confirmatory_fine"
                    if index == 5
                    else None
                )
            ),
            "coarse_geometry_multiplicity_cannot_authorize_top_k": (
                item.get("manifest_geometry_multiplicity_top_k_eligible") is False
                if index == 1
                else True
            ),
            "conformal_calibration": item.get("manifest_conformal_calibration")
            == (
                "WAITING_FOR_600K"
                if index < 6
                else ("PASS" if index == 6 else "NOT_REPEATED_AFTER_600K")
            ),
            "mondrian_conformal": item.get("manifest_mondrian_conformal")
            == (
                "WAITING_FOR_600K"
                if index < 6
                else ("PASS" if index == 6 else "NOT_REPEATED_AFTER_600K")
            ),
            "low_frequency_physics": item.get("manifest_low_frequency_physics")
            == (
                "WAITING_FOR_700K"
                if index < 7
                else ("PASS" if index == 7 else "NOT_REPEATED_AFTER_700K")
            ),
            "local_refinement_plan": item.get("manifest_local_refinement_plan")
            == (
                "WAITING_FOR_800K"
                if index < 8
                else ("PASS" if index == 8 else "NOT_REPEATED_AFTER_800K")
            ),
            "boundary_ood_stress": item.get("manifest_boundary_ood_stress")
            == (
                "WAITING_FOR_900K"
                if index < 9
                else ("PASS" if index == 9 else "NOT_REPEATED_AFTER_900K")
            ),
            "physical_spec_spectral_expander": item.get("manifest_physical_spec_spectral_expander")
            == "COMPLETE_REVIEW_REQUIRED",
            "balanced_mse_bni_status": item.get("manifest_balanced_mse_bni_status")
            == (
                "WAITING_FOR_200K"
                if index == 1
                else ("PASS" if index == 2 else "NOT_REPEATED_AFTER_200K")
            ),
        }
        if index == 2:
            expected_bni_rule = (
                "row_equal_cell_and_p90_tail_cluster_bootstrap_ci_lower_ge_material_improvement"
            )
            manifest_bni_cis = (
                _finite(item.get("manifest_balanced_mse_bni_row_ci_lower")),
                _finite(item.get("manifest_balanced_mse_bni_equal_cell_ci_lower")),
                _finite(item.get("manifest_balanced_mse_bni_p90_tail_ci_lower")),
            )
            artifact_bni_cis = (
                _finite(item.get("balanced_mse_bni_artifact_row_ci_lower")),
                _finite(item.get("balanced_mse_bni_artifact_equal_cell_ci_lower")),
                _finite(item.get("balanced_mse_bni_artifact_p90_tail_ci_lower")),
            )
            checks.update(
                {
                    "balanced_mse_bni_decision_rule": item.get("manifest_balanced_mse_bni_decision_rule")
                    == expected_bni_rule,
                    "balanced_mse_bni_ci_values_finite": all(value is not None for value in manifest_bni_cis),
                    "balanced_mse_bni_artifact_exists_flag": item.get(
                        "balanced_mse_bni_artifact_exists_flag"
                    )
                    is True,
                    "balanced_mse_bni_artifact_exists": item.get("balanced_mse_bni_artifact_exists") is True,
                    "balanced_mse_bni_artifact_sha256_recorded": len(
                        str(item.get("balanced_mse_bni_artifact_sha256_recorded") or "")
                    )
                    == 64,
                    "balanced_mse_bni_artifact_sha256_matches": item.get(
                        "balanced_mse_bni_artifact_sha256_matches"
                    )
                    is True,
                    "balanced_mse_bni_artifact_status": item.get("balanced_mse_bni_artifact_status") == "PASS",
                    "balanced_mse_bni_artifact_decision_rule": item.get(
                        "balanced_mse_bni_artifact_decision_rule"
                    )
                    == expected_bni_rule,
                    "balanced_mse_bni_artifact_bootstrap_status": item.get(
                        "balanced_mse_bni_artifact_bootstrap_status"
                    )
                    == "PASS",
                    "balanced_mse_bni_artifact_ci_values_finite": all(
                        value is not None for value in artifact_bni_cis
                    ),
                    "balanced_mse_bni_manifest_artifact_ci_match": all(
                        manifest_value is not None
                        and artifact_value is not None
                        and math.isclose(manifest_value, artifact_value, rel_tol=1.0e-12, abs_tol=1.0e-12)
                        for manifest_value, artifact_value in zip(manifest_bni_cis, artifact_bni_cis)
                    ),
                }
            )
        if index == 6:
            allowed_decisions = {
                "ADOPT_MONDRIAN_FOR_GROUP_REPORTED_INTERVALS",
                "RETAIN_GLOBAL_INTERVALS_AND_REPORT_GROUP_DIAGNOSTICS",
            }
            manifest_cell_fraction = _finite(item.get("manifest_mondrian_supported_cell_fraction"))
            manifest_row_fraction = _finite(item.get("manifest_mondrian_supported_row_fraction"))
            artifact_cell_fraction = _finite(
                item.get("mondrian_conformal_artifact_supported_cell_fraction")
            )
            artifact_row_fraction = _finite(
                item.get("mondrian_conformal_artifact_supported_row_fraction")
            )
            checks.update(
                {
                    "mondrian_manifest_decision_allowed": item.get("manifest_mondrian_conformal_decision")
                    in allowed_decisions,
                    "mondrian_manifest_recommendation_matches_decision": item.get(
                        "manifest_mondrian_conformal_recommendation"
                    )
                    == item.get("manifest_mondrian_conformal_decision"),
                    "mondrian_manifest_support_finite": manifest_cell_fraction is not None
                    and manifest_row_fraction is not None,
                    "mondrian_artifact_exists_flag": item.get("mondrian_conformal_artifact_exists_flag")
                    is True,
                    "mondrian_artifact_exists": item.get("mondrian_conformal_artifact_exists") is True,
                    "mondrian_artifact_sha256_recorded": len(
                        str(item.get("mondrian_conformal_artifact_sha256_recorded") or "")
                    )
                    == 64,
                    "mondrian_artifact_sha256_matches": item.get(
                        "mondrian_conformal_artifact_sha256_matches"
                    )
                    is True,
                    "mondrian_artifact_status": item.get("mondrian_conformal_artifact_status") == "PASS",
                    "mondrian_artifact_decision_allowed": item.get(
                        "mondrian_conformal_artifact_decision"
                    )
                    in allowed_decisions,
                    "mondrian_artifact_recommendation_matches_decision": item.get(
                        "mondrian_conformal_artifact_recommendation"
                    )
                    == item.get("mondrian_conformal_artifact_decision"),
                    "mondrian_artifact_checks_all_pass": item.get(
                        "mondrian_conformal_artifact_checks_all_pass"
                    )
                    is True,
                    "mondrian_artifact_support_thresholds": artifact_cell_fraction is not None
                    and artifact_row_fraction is not None
                    and artifact_cell_fraction >= 0.80
                    and artifact_row_fraction >= 0.80,
                    "mondrian_manifest_artifact_support_match": manifest_cell_fraction is not None
                    and manifest_row_fraction is not None
                    and artifact_cell_fraction is not None
                    and artifact_row_fraction is not None
                    and math.isclose(
                        manifest_cell_fraction, artifact_cell_fraction, rel_tol=1.0e-12, abs_tol=1.0e-12
                    )
                    and math.isclose(
                        manifest_row_fraction, artifact_row_fraction, rel_tol=1.0e-12, abs_tol=1.0e-12
                    ),
                }
            )
        if not all(checks.values()):
            reasons.append(f"checkpoint {index} failed {checks}")
    if len(records) != int(args.checkpoint_count):
        reasons.append(f"record count={len(records)}, expected={args.checkpoint_count}")
    if records and records[-1].get("formal_checkpoint_pass") is not True:
        reasons.append("final formal checkpoint marker is not PASS")
    return {
        "overall_status": "PASS" if not reasons else "FAIL",
        "records": records,
        "reasons": reasons,
        "expected_indices": expected_indices,
        "expected_targets": expected_targets,
    }


def _fixed_common_panel_checks(learning_curve: dict[str, Any], checkpoint_count: int) -> dict[str, bool]:
    panel = learning_curve.get("fixed_common_test_panel") or {}
    checks = panel.get("checks") or {}
    artifact = panel.get("artifact") or {}
    artifact_path = Path(str(artifact.get("path") or ""))
    actual_sha = _sha256(artifact_path) if artifact_path.is_file() else ""
    artifact_rows = []
    if artifact_path.is_file():
        try:
            with artifact_path.open(newline="", encoding="utf-8-sig") as handle:
                artifact_rows = [dict(row) for row in csv.DictReader(handle)]
        except Exception:
            artifact_rows = []
    target_names = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
    artifact_identities = [str(row.get("geometry_identity_sha256") or "").strip().lower() for row in artifact_rows]
    artifact_rows_valid = bool(artifact_rows) and all(
        len(identity) == 64
        and all(character in "0123456789abcdef" for character in identity)
        and all(_finite(row.get(f"target__{name}")) is not None for name in target_names)
        for identity, row in zip(artifact_identities, artifact_rows)
    )
    artifact_fingerprint = ""
    if artifact_rows_valid:
        artifact_fingerprint = hashlib.sha256(
            "".join(
                f"{identity}|"
                + "|".join(format(float(row[f"target__{name}"]), ".17g") for name in target_names)
                + "\n"
                for identity, row in zip(artifact_identities, artifact_rows)
            ).encode("ascii")
        ).hexdigest()
    metrics = panel.get("checkpoint_metrics") or []
    arguments = learning_curve.get("arguments") or {}
    fixed_panel_count = int(
        panel.get("fixed_panel_geometry_count") or panel.get("common_geometry_count") or 0
    )
    return {
        "status_pass": panel.get("status") == "PASS",
        "all_internal_checks_pass": bool(checks) and all(value is True for value in checks.values()),
        "fixed_panel_is_first_checkpoint_anchored": str(panel.get("fixed_panel_policy") or "").startswith(
            "all valid test geometries from the first completed checkpoint"
        )
        and int(panel.get("fixed_panel_source_checkpoint_index") or -1) == 1,
        "exact_fixed_panel_coverage_all_checkpoints": checks.get(
            "exact_fixed_panel_coverage_all_checkpoints"
        )
        is True,
        "minimum_common_geometry_count": fixed_panel_count >= 1000,
        "minimum_first_panel_retention": float(panel.get("first_panel_retention_fraction") or 0.0) >= 0.99,
        "targets_stable": _finite(panel.get("target_mismatch_count")) == 0.0,
        "identity_fingerprint_recorded": len(str(panel.get("common_panel_fingerprint_sha256") or "")) == 64,
        "artifact_row_count_matches_summary": len(artifact_rows) == fixed_panel_count,
        "artifact_rows_valid_and_unique": artifact_rows_valid
        and len(set(artifact_identities)) == len(artifact_identities),
        "artifact_fingerprint_matches": bool(artifact_fingerprint)
        and artifact_fingerprint == str(panel.get("common_panel_fingerprint_sha256") or ""),
        "checkpoint_metric_count": len(metrics) == checkpoint_count,
        "all_checkpoint_metrics_finite": len(metrics) == checkpoint_count
        and all(_finite(item.get("common_panel_response_range_normalized_rmse")) is not None for item in metrics),
        "artifact_exists_nonempty": artifact_path.is_file() and artifact_path.stat().st_size > 0,
        "artifact_sha256_matches": bool(actual_sha) and actual_sha == str(artifact.get("sha256") or ""),
        "learning_curve_minimum_common_rows_not_weakened": int(
            arguments.get("minimum_common_test_rows") or 0
        )
        >= 1000,
        "learning_curve_minimum_retention_not_weakened": float(
            arguments.get("minimum_first_panel_retention") or 0.0
        )
        >= 0.99,
    }


def _uniformity_checks(data: dict[str, Any], minimum: int) -> dict[str, bool]:
    one_d = data.get("one_dimensional_uniformity") or {}
    pairwise = data.get("pairwise_uniformity") or {}
    four_d = data.get("four_dimensional_uniformity") or {}
    thresholds = data.get("distribution_thresholds") or {}
    ranges = data.get("ranges") or {}
    return {
        "overall_status": data.get("overall_status") == "PASS",
        "valid_count": int(data.get("valid_feature_count") or data.get("valid_count") or 0) >= minimum,
        "one_d_all_features": all(
            float((one_d.get(name) or {}).get("occupied_fraction") or 0.0) >= 0.90
            and float((one_d.get(name) or {}).get("normalized_entropy") or 0.0) >= 0.90
            and float((one_d.get(name) or {}).get("max_to_min_nonzero_ratio") or math.inf) <= 2.50
            for name in ("lp", "ls", "q", "k")
        ),
        "all_six_pairs": len(pairwise) >= 6
        and all(
            float((item or {}).get("occupied_fraction") or 0.0) >= 0.65
            and float((item or {}).get("normalized_entropy") or 0.0) >= 0.80
            for item in pairwise.values()
        ),
        "four_d_occupied": float(four_d.get("occupied_fraction") or 0.0) >= 0.50,
        "four_d_entropy": float(four_d.get("normalized_entropy") or 0.0) >= 0.80,
        "four_d_nonzero_bin_imbalance": float(
            four_d.get("max_to_min_nonzero_ratio") or math.inf
        )
        <= 4.0,
        "four_d_gate_required": thresholds.get("require_four_d_gate") is True,
        "four_d_occupancy_threshold_not_weakened": float(
            thresholds.get("min_four_d_occupied_fraction") or 0.0
        )
        >= 0.50,
        "four_d_entropy_threshold_not_weakened": float(
            thresholds.get("min_four_d_normalized_entropy") or 0.0
        )
        >= 0.80,
        "four_d_imbalance_threshold_not_weakened": _finite(
            thresholds.get("max_four_d_nonzero_bin_imbalance")
        )
        is not None
        and float(thresholds.get("max_four_d_nonzero_bin_imbalance")) <= 4.0,
        "explicit_ranges": all(
            (ranges.get(name) or {}).get("explicit") is True
            and math.isclose(float((ranges.get(name) or {}).get("min")), bounds[0], abs_tol=1.0e-12)
            and math.isclose(float((ranges.get(name) or {}).get("max")), bounds[1], abs_tol=1.0e-12)
            for name, bounds in {"lp": (0.5, 3.0), "ls": (0.5, 3.0), "q": (5.0, 25.0), "k": (0.0, 0.8)}.items()
        ),
    }


def _frequency_matches(row: dict[str, str], args: argparse.Namespace) -> bool:
    expected = (5.0e9, 60.0e9, 0.5e9)
    actual = tuple(_finite(row.get(column)) for column in ("sparam_freq_start_hz", "sparam_freq_stop_hz", "sparam_freq_step_hz"))
    points = _finite(row.get("sparam_freq_points"))
    return all(
        value is not None and abs(float(value) - target) <= float(args.frequency_tolerance_hz)
        for value, target in zip(actual, expected)
    ) and points is not None and int(points) == 111


def _canonical_geometry_fingerprint(row: dict[str, Any]) -> str | None:
    quantum = Decimal(str(GEOMETRY_FINGERPRINT_QUANTIZATION_UM))
    quantized = []
    for column in GEOMETRY_COLUMNS:
        value = _finite(row.get(column))
        if value is None:
            return None
        integer = (Decimal(str(value)) / quantum).to_integral_value(rounding=ROUND_HALF_UP)
        quantized.append(int(integer))
    payload = {
        "schema": GEOMETRY_FINGERPRINT_SCHEMA,
        "quantization_um": format(quantum, "f"),
        "fields": list(CANONICAL_GEOMETRY_FIELDS),
        "quantized_values": quantized,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _shared_width_aliases_valid(row: dict[str, Any]) -> bool:
    line_width = _finite(row.get("geom__line_width_um"))
    if line_width is None:
        return False
    for alias in ("geom__primary_width_um", "geom__secondary_width_um"):
        raw = row.get(alias)
        if raw is None or str(raw).strip() == "":
            continue
        value = _finite(raw)
        if value is None or not math.isclose(value, line_width, rel_tol=0.0, abs_tol=1.0e-12):
            return False
    return True


def _pool_geometry_identity_contract(pool_summary: dict[str, Any]) -> dict[str, Any]:
    policy = pool_summary.get("dedupe_policy") if isinstance(pool_summary.get("dedupe_policy"), dict) else {}
    quantization = _finite(policy.get("fingerprint_quantization_um"))
    checks = {
        "fingerprint_schema": policy.get("fingerprint_schema") == GEOMETRY_FINGERPRINT_SCHEMA,
        "fingerprint_quantization_um": quantization is not None
        and math.isclose(quantization, GEOMETRY_FINGERPRINT_QUANTIZATION_UM, rel_tol=0.0, abs_tol=0.0),
        "canonical_fields": policy.get("canonical_fields") == list(CANONICAL_GEOMETRY_FIELDS),
        "geometry_columns": policy.get("geometry_columns") == list(GEOMETRY_COLUMNS),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "expected_schema": GEOMETRY_FINGERPRINT_SCHEMA,
        "expected_quantization_um": GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
    }


def _failure(counts: dict[str, int], examples: dict[str, list[Any]], key: str, row_index: int) -> None:
    counts[key] += 1
    if len(examples[key]) < 10:
        examples[key].append(row_index)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_report(data: dict[str, Any]) -> str:
    dataset = data["dataset_audit"]
    return "\n".join(
        [
            "# Accepted one-million real-EMX campaign completion audit",
            "",
            f"- Overall status: **{data['overall_status']}**",
            f"- Decision: **{data['decision']}**",
            f"- Accepted rows scanned: `{dataset.get('row_count')}`",
            f"- Unique geometries: `{dataset.get('unique_geometry_digest_count')}`",
            f"- Unique S4P paths: `{dataset.get('unique_touchstone_path_digest_count')}`",
            f"- Checkpoint audit: `{data['checkpoint_audit'].get('overall_status')}`",
            "",
            data["scientific_boundary"],
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
