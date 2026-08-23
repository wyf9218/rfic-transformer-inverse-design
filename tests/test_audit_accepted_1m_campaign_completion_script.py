from tests.rfic_transformer_inverse_design.shared import *

import csv
import hashlib
import importlib.util
import sys

import pytest


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
BNI_DECISION_RULE = "row_equal_cell_and_p90_tail_cluster_bootstrap_ci_lower_ge_material_improvement"


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_accepted_1m_campaign_completion.py"
    spec = importlib.util.spec_from_file_location("audit_accepted_1m_campaign_completion_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_campaign(
    tmp_path: Path,
    module,
    *,
    duplicate_geometry: bool = False,
    near_duplicate_geometry: bool = False,
    missing_s4p: bool = False,
    tampered_fingerprint: bool = False,
) -> tuple[Path, Path]:
    campaign = tmp_path / "campaign"
    pool = tmp_path / "pool"
    pool.mkdir(parents=True)
    rows = []
    for index in range(10):
        touchstone = pool / f"sample_{index}.s4p"
        if not (missing_s4p and index == 5):
            touchstone.write_text("! synthetic nonempty S4P evidence\n", encoding="ascii")
        geometry_index = 4 if (duplicate_geometry or near_duplicate_geometry) and index == 5 else index
        geometry_delta = 0.4e-6 if near_duplicate_geometry and index == 5 else 0.0
        qp = 8.0 + index
        qs = 8.5 + index
        row = {
            "lp_nh_center": 0.7 + 0.2 * index,
            "ls_nh_center": 0.8 + 0.2 * index,
            "q_center": min(qp, qs),
            "qp_center": qp,
            "qs_center": qs,
            "k_abs_center": 0.1 + 0.07 * index,
            "touchstone_path": str(touchstone),
            "sparam_freq_start_hz": 5.0e9,
            "sparam_freq_stop_hz": 60.0e9,
            "sparam_freq_step_hz": 0.5e9,
            "sparam_freq_points": 111,
        }
        for column_index, column in enumerate(GEOMETRY_COLUMNS):
            row[column] = 10.0 * (column_index + 1) + geometry_index + geometry_delta
        row["geom__primary_width_um"] = row["geom__line_width_um"]
        row["geom__secondary_width_um"] = row["geom__line_width_um"]
        row["canonical_geometry_fingerprint_sha256"] = module._canonical_geometry_fingerprint(row)
        row["canonical_geometry_fingerprint_schema"] = module.GEOMETRY_FINGERPRINT_SCHEMA
        row["canonical_geometry_fingerprint_quantization_um"] = module.GEOMETRY_FINGERPRINT_QUANTIZATION_UM
        if tampered_fingerprint and index == 5:
            row["canonical_geometry_fingerprint_sha256"] = "0" * 64
        rows.append(row)
    with (pool / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    _write_json(
        pool / "accepted_pool_merge_summary.json",
        {
            "overall_status": "PASS",
            "row_count": 10,
            "reject_summary": {"geometry_identity_mismatch": 0},
            "dedupe_policy": {
                "geometry_columns": list(module.GEOMETRY_COLUMNS),
                "canonical_fields": list(module.CANONICAL_GEOMETRY_FIELDS),
                "fingerprint_schema": module.GEOMETRY_FINGERPRINT_SCHEMA,
                "fingerprint_quantization_um": module.GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
            },
        },
    )

    uniformity = {
        "overall_status": "PASS",
        "valid_feature_count": 10,
        "ranges": {
            "lp": {"min": 0.5, "max": 3.0, "explicit": True},
            "ls": {"min": 0.5, "max": 3.0, "explicit": True},
            "q": {"min": 5.0, "max": 25.0, "explicit": True},
            "k": {"min": 0.0, "max": 0.8, "explicit": True},
        },
        "one_dimensional_uniformity": {
            name: {"occupied_fraction": 1.0, "normalized_entropy": 1.0, "max_to_min_nonzero_ratio": 1.0}
            for name in ("lp", "ls", "q", "k")
        },
        "pairwise_uniformity": {
            name: {"occupied_fraction": 0.75, "normalized_entropy": 0.9}
            for name in ("lp_ls", "lp_q", "lp_k", "ls_q", "ls_k", "q_k")
        },
        "four_dimensional_uniformity": {
            "occupied_fraction": 0.6,
            "normalized_entropy": 0.9,
            "max_to_min_nonzero_ratio": 2.0,
        },
        "distribution_thresholds": {
            "require_four_d_gate": True,
            "min_four_d_occupied_fraction": 0.5,
            "min_four_d_normalized_entropy": 0.8,
            "max_four_d_nonzero_bin_imbalance": 4.0,
        },
    }
    uniformity_path = campaign / "final_uniformity" / "physical_feature_uniformity_summary.json"
    _write_json(uniformity_path, uniformity)

    for index, target in enumerate((1, 2, 3, 4, 5, 6, 7, 8, 9, 10), start=1):
        checkpoint = campaign / "checkpoints" / f"checkpoint_{index:02d}_n{target}"
        bni_summary_path = checkpoint / "balanced_mse_bni_ablation_summary.json"
        bni_status = "WAITING_FOR_200K" if index == 1 else ("PASS" if index == 2 else "NOT_REPEATED_AFTER_200K")
        bni_summary = {
            "overall_status": bni_status,
            "decision_rule": BNI_DECISION_RULE if index == 2 else None,
        }
        if index == 2:
            bni_summary["paired_cluster_bootstrap"] = {
                "status": "PASS",
                "relative_improvement_ci_lower": -0.04,
                "cell_balanced_relative_improvement_ci_lower": -0.03,
                "p90_tail_relative_improvement_ci_lower": -0.08,
            }
        _write_json(bni_summary_path, bni_summary)
        mondrian_summary_path = checkpoint / "physical_feature_mondrian_conformal_comparison_summary.json"
        mondrian_status = (
            "WAITING_FOR_600K"
            if index < 6
            else ("PASS" if index == 6 else "NOT_REPEATED_AFTER_600K")
        )
        mondrian_decision = (
            "RETAIN_GLOBAL_INTERVALS_AND_REPORT_GROUP_DIAGNOSTICS"
            if index == 6
            else (
                "RUN_GLOBAL_VS_MONDRIAN_COMPARISON_AT_600K"
                if index < 6
                else "USE_RECORDED_600K_MONDRIAN_COMPARISON_EVIDENCE"
            )
        )
        mondrian_summary = {
            "overall_status": mondrian_status,
            "decision": mondrian_decision,
            "recommendation": {"decision": mondrian_decision} if index == 6 else {},
            "checks": {"same_split": True, "support": True} if index == 6 else {},
            "analysis": {
                "support": {
                    "supported_evaluation_cell_fraction": 0.9,
                    "supported_evaluation_row_fraction": 0.95,
                }
            }
            if index == 6
            else {},
        }
        _write_json(mondrian_summary_path, mondrian_summary)
        manifest = checkpoint / "model_manifest.json"
        _write_json(
            manifest,
            {
                "overall_status": "PASS",
                "model_test_status": "PASS",
                "checkpoint_index": index,
                "accepted_checkpoint_count": target,
                "broadband_sparameter_readiness_status": "PASS",
                "physical_cell_tail_error_status": "PASS",
                "physical_feature_frequency_stability_status": (
                    "WAITING_FOR_200K" if index == 1 else ("PASS" if index == 2 else "NOT_REPEATED_AFTER_200K")
                ),
                "geometry_response_effective_dimension_status": (
                    "WAITING_FOR_300K" if index < 3 else ("PASS" if index == 3 else "NOT_REPEATED_AFTER_300K")
                ),
                "frequency_self_transfer_status": (
                    "WAITING_FOR_300K"
                    if index < 3
                    else ("COMPLETE_REVIEW_REQUIRED" if index == 3 else "NOT_REPEATED_AFTER_300K")
                ),
                "frequency_sequence_architecture_status": (
                    "WAITING_FOR_300K"
                    if index < 3
                    else ("COMPLETE_REVIEW_REQUIRED" if index == 3 else "NOT_REPEATED_AFTER_300K")
                ),
                "inverse_geometry_multiplicity_status": (
                    "PASS"
                    if index == 1
                    else (
                        "COARSE_100K_COMPLETE_FINE_WAITING_FOR_500K"
                        if index < 5
                        else ("PASS" if index == 5 else "NOT_REPEATED_AFTER_500K")
                    )
                ),
                "inverse_geometry_multiplicity_evidence_stage": (
                    "exploratory_coarse" if index == 1 else ("confirmatory_fine" if index == 5 else None)
                ),
                "inverse_geometry_multiplicity_top_k_eligible": False if index == 1 else None,
                "physical_feature_conformal_calibration_status": (
                    "WAITING_FOR_600K" if index < 6 else ("PASS" if index == 6 else "NOT_REPEATED_AFTER_600K")
                ),
                "physical_feature_mondrian_conformal_status": mondrian_status,
                "physical_feature_mondrian_conformal_decision": mondrian_decision,
                "physical_feature_mondrian_conformal_recommendation": (
                    mondrian_decision if index == 6 else "MISSING"
                ),
                "physical_feature_mondrian_supported_cell_fraction": 0.9 if index == 6 else None,
                "physical_feature_mondrian_supported_row_fraction": 0.95 if index == 6 else None,
                "low_frequency_coupled_rl_consistency_status": (
                    "WAITING_FOR_700K" if index < 7 else ("PASS" if index == 7 else "NOT_REPEATED_AFTER_700K")
                ),
                "tandem_local_refinement_plan_status": (
                    "WAITING_FOR_800K" if index < 8 else ("PASS" if index == 8 else "NOT_REPEATED_AFTER_800K")
                ),
                "physical_feature_boundary_ood_stress_status": (
                    "WAITING_FOR_900K" if index < 9 else ("PASS" if index == 9 else "NOT_REPEATED_AFTER_900K")
                ),
                "physical_spec_spectral_expander_status": "COMPLETE_REVIEW_REQUIRED",
                "balanced_mse_bni_ablation_status": bni_status,
                "balanced_mse_bni_ablation_decision_rule": BNI_DECISION_RULE if index == 2 else None,
                "balanced_mse_bni_row_improvement_ci_lower": -0.04 if index == 2 else None,
                "balanced_mse_bni_equal_cell_improvement_ci_lower": -0.03 if index == 2 else None,
                "balanced_mse_bni_p90_tail_improvement_ci_lower": -0.08 if index == 2 else None,
                "artifacts": {
                    "uniformity": {"path": str(uniformity_path)},
                    "balanced_mse_bni_ablation": {
                        "path": str(bni_summary_path),
                        "exists": True,
                        "sha256": _sha256(bni_summary_path),
                    },
                    "physical_feature_mondrian_conformal_comparison": {
                        "path": str(mondrian_summary_path),
                        "exists": True,
                        "sha256": _sha256(mondrian_summary_path),
                    },
                },
            },
        )
        _write_json(
            checkpoint / "checkpoint_record.json",
            {
                "overall_status": "PASS",
                "checkpoint_index": index,
                "target_accepted_count": target,
                "model_test_status": "PASS",
                "formal_checkpoint_pass": index == 10,
                "model_manifest": str(manifest),
                "model_manifest_sha256": _sha256(manifest),
            },
        )

    learning_curve_dir = campaign / "model_learning_curve"
    panel_path = learning_curve_dir / "fixed_common_test_panel_geometry_ids.csv"
    panel_rows = []
    for index in range(1000):
        panel_rows.append(
            {
                "geometry_identity_sha256": hashlib.sha256(f"panel-{index}".encode("ascii")).hexdigest(),
                "target__lp_nh_center": 0.5 + (index % 10) * 0.1,
                "target__ls_nh_center": 0.6 + (index % 10) * 0.1,
                "target__q_center": 5.0 + (index % 20),
                "target__k_abs_center": (index % 8) * 0.1,
            }
        )
    panel_path.parent.mkdir(parents=True, exist_ok=True)
    with panel_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(panel_rows[0]))
        writer.writeheader()
        writer.writerows(panel_rows)
    panel_fingerprint = hashlib.sha256(
        "".join(
            f"{row['geometry_identity_sha256']}|"
            + "|".join(
                format(float(row[f"target__{name}"]), ".17g")
                for name in ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
            )
            + "\n"
            for row in panel_rows
        ).encode("ascii")
    ).hexdigest()
    _write_json(
        learning_curve_dir / "physical_feature_model_learning_curve_summary.json",
        {
            "overall_status": "PASS",
            "checkpoint_count": 10,
            "comparison_contract": {"comparable": True},
            "fixed_common_test_panel": {
                "status": "PASS",
                "fixed_panel_policy": "all valid test geometries from the first completed checkpoint, sorted by identity SHA",
                "fixed_panel_source_checkpoint_index": 1,
                "fixed_panel_geometry_count": 1000,
                "common_geometry_count": 1000,
                "intersection_geometry_count": 1000,
                "first_panel_retention_fraction": 1.0,
                "target_mismatch_count": 0,
                "common_panel_fingerprint_sha256": panel_fingerprint,
                "checks": {
                    "all_prediction_files_exist": True,
                    "all_prediction_rows_valid": True,
                    "all_geometry_identities_unique": True,
                    "complete_test_prediction_coverage": True,
                    "minimum_common_test_rows": True,
                    "minimum_first_panel_retention": True,
                    "exact_fixed_panel_coverage_all_checkpoints": True,
                    "targets_stable_across_checkpoints": True,
                    "common_panel_metric_finite_all_checkpoints": True,
                    "artifact_written": True,
                },
                "checkpoint_metrics": [
                    {
                        "checkpoint_index": index,
                        "common_panel_response_range_normalized_rmse": 0.1 / index,
                    }
                    for index in range(1, 11)
                ],
                "artifact": {"path": str(panel_path), "sha256": _sha256(panel_path)},
            },
            "arguments": {
                "minimum_common_test_rows": 1000,
                "minimum_first_panel_retention": 0.99,
            },
        },
    )
    return campaign, pool


def _run(module, campaign: Path, pool: Path, out_dir: Path) -> int:
    return module.main(
        [
            "--campaign-root",
            str(campaign),
            "--final-pool-dir",
            str(pool),
            "--out-dir",
            str(out_dir),
            "--expected-total",
            "10",
            "--checkpoint-count",
            "10",
            "--checkpoint-size",
            "1",
            "--check-touchstone-exists",
        ]
    )


def test_strict_completion_audit_passes_complete_synthetic_evidence(tmp_path):
    module = _load_module()
    campaign, pool = _make_campaign(tmp_path, module)
    out_dir = tmp_path / "audit"

    assert _run(module, campaign, pool, out_dir) == 0
    summary = json.loads((out_dir / "accepted_1m_campaign_completion_audit_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["dataset_audit"]["row_count"] == 10
    assert summary["dataset_audit"]["unique_geometry_digest_count"] == 10
    assert summary["dataset_audit"]["unique_geometry_fingerprint_count"] == 10
    assert summary["pool_geometry_identity_contract"]["status"] == "PASS"
    assert summary["checkpoint_audit"]["overall_status"] == "PASS"
    assert (out_dir / "accepted_1m_campaign_completion.pass").is_file()


def test_strict_completion_audit_refuses_checkpoint_without_physical_cell_tail_evidence(tmp_path):
    module = _load_module()
    campaign, pool = _make_campaign(tmp_path, module)
    checkpoint = campaign / "checkpoints" / "checkpoint_04_n4"
    manifest_path = checkpoint / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("physical_cell_tail_error_status")
    _write_json(manifest_path, manifest)
    record_path = checkpoint / "checkpoint_record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["model_manifest_sha256"] = _sha256(manifest_path)
    _write_json(record_path, record)
    out_dir = tmp_path / "audit"

    assert _run(module, campaign, pool, out_dir) == 2
    summary = json.loads((out_dir / "accepted_1m_campaign_completion_audit_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checkpoint_audit"]["overall_status"] == "FAIL"
    assert any("physical_cell_tail_error" in reason for reason in summary["checkpoint_audit"]["reasons"])
    assert not (out_dir / "accepted_1m_campaign_completion.pass").exists()


def test_strict_completion_audit_refuses_missing_300k_frequency_sequence_benchmark(tmp_path):
    module = _load_module()
    campaign, pool = _make_campaign(tmp_path, module)
    checkpoint = campaign / "checkpoints" / "checkpoint_03_n3"
    manifest_path = checkpoint / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("frequency_sequence_architecture_status")
    _write_json(manifest_path, manifest)
    record_path = checkpoint / "checkpoint_record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["model_manifest_sha256"] = _sha256(manifest_path)
    _write_json(record_path, record)
    out_dir = tmp_path / "audit"

    assert _run(module, campaign, pool, out_dir) == 2
    summary = json.loads((out_dir / "accepted_1m_campaign_completion_audit_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert any("frequency_sequence_architecture" in reason for reason in summary["checkpoint_audit"]["reasons"])
    assert not (out_dir / "accepted_1m_campaign_completion.pass").exists()


def test_strict_completion_audit_refuses_learning_curve_without_fixed_common_test_panel(tmp_path):
    module = _load_module()
    campaign, pool = _make_campaign(tmp_path, module)
    summary_path = campaign / "model_learning_curve" / "physical_feature_model_learning_curve_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.pop("fixed_common_test_panel")
    _write_json(summary_path, summary)
    out_dir = tmp_path / "audit"

    assert _run(module, campaign, pool, out_dir) == 2
    audit = json.loads((out_dir / "accepted_1m_campaign_completion_audit_summary.json").read_text())
    assert audit["overall_status"] == "FAIL"
    assert audit["checks"]["fixed_common_test_panel_contract_pass"] is False
    assert not (out_dir / "accepted_1m_campaign_completion.pass").exists()


def test_strict_completion_audit_refuses_dynamic_intersection_panel(tmp_path):
    module = _load_module()
    campaign, pool = _make_campaign(tmp_path, module)
    summary_path = campaign / "model_learning_curve" / "physical_feature_model_learning_curve_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    panel = summary["fixed_common_test_panel"]
    panel["fixed_panel_policy"] = "dynamic intersection across all completed checkpoints"
    panel["checks"]["exact_fixed_panel_coverage_all_checkpoints"] = False
    _write_json(summary_path, summary)
    out_dir = tmp_path / "audit"

    assert _run(module, campaign, pool, out_dir) == 2
    audit = json.loads((out_dir / "accepted_1m_campaign_completion_audit_summary.json").read_text())
    panel_checks = audit["fixed_common_test_panel_checks"]
    assert audit["overall_status"] == "FAIL"
    assert panel_checks["fixed_panel_is_first_checkpoint_anchored"] is False
    assert panel_checks["exact_fixed_panel_coverage_all_checkpoints"] is False
    assert audit["checks"]["fixed_common_test_panel_contract_pass"] is False
    assert not (out_dir / "accepted_1m_campaign_completion.pass").exists()


def test_strict_completion_audit_refuses_concentrated_4d_distribution(tmp_path):
    module = _load_module()
    campaign, pool = _make_campaign(tmp_path, module)
    uniformity_path = campaign / "final_uniformity" / "physical_feature_uniformity_summary.json"
    uniformity = json.loads(uniformity_path.read_text(encoding="utf-8"))
    uniformity["four_dimensional_uniformity"]["normalized_entropy"] = 0.79
    uniformity["four_dimensional_uniformity"]["max_to_min_nonzero_ratio"] = 5.0
    _write_json(uniformity_path, uniformity)
    out_dir = tmp_path / "audit"

    assert _run(module, campaign, pool, out_dir) == 2
    audit = json.loads((out_dir / "accepted_1m_campaign_completion_audit_summary.json").read_text())
    assert audit["overall_status"] == "FAIL"
    assert audit["final_uniformity_checks"]["four_d_occupied"] is True
    assert audit["final_uniformity_checks"]["four_d_entropy"] is False
    assert audit["final_uniformity_checks"]["four_d_nonzero_bin_imbalance"] is False


@pytest.mark.parametrize(
    "failure",
    ["missing_manifest_status", "missing_decision_rule", "nonfinite_ci", "missing_artifact", "bad_artifact_sha"],
)
def test_strict_completion_audit_refuses_missing_or_invalid_200k_bni_evidence(tmp_path, failure):
    module = _load_module()
    campaign, pool = _make_campaign(tmp_path, module)
    checkpoint = campaign / "checkpoints" / "checkpoint_02_n2"
    manifest_path = checkpoint / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = manifest["artifacts"]["balanced_mse_bni_ablation"]

    if failure == "missing_manifest_status":
        manifest.pop("balanced_mse_bni_ablation_status")
    elif failure == "missing_decision_rule":
        manifest.pop("balanced_mse_bni_ablation_decision_rule")
    elif failure == "nonfinite_ci":
        manifest["balanced_mse_bni_p90_tail_improvement_ci_lower"] = None
    elif failure == "missing_artifact":
        Path(artifact["path"]).unlink()
    elif failure == "bad_artifact_sha":
        artifact["sha256"] = "0" * 64

    _write_json(manifest_path, manifest)
    record_path = checkpoint / "checkpoint_record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["model_manifest_sha256"] = _sha256(manifest_path)
    _write_json(record_path, record)
    out_dir = tmp_path / "audit"

    assert _run(module, campaign, pool, out_dir) == 2
    summary = json.loads((out_dir / "accepted_1m_campaign_completion_audit_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checkpoint_audit"]["overall_status"] == "FAIL"
    assert any("balanced_mse_bni" in reason for reason in summary["checkpoint_audit"]["reasons"])
    assert not (out_dir / "accepted_1m_campaign_completion.pass").exists()


@pytest.mark.parametrize(
    "failure",
    ["missing_manifest_status", "missing_artifact", "bad_artifact_sha", "insufficient_support", "invalid_decision"],
)
def test_strict_completion_audit_refuses_invalid_600k_mondrian_evidence(tmp_path, failure):
    module = _load_module()
    campaign, pool = _make_campaign(tmp_path, module)
    checkpoint = campaign / "checkpoints" / "checkpoint_06_n6"
    manifest_path = checkpoint / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = manifest["artifacts"]["physical_feature_mondrian_conformal_comparison"]
    artifact_path = Path(artifact["path"])

    if failure == "missing_manifest_status":
        manifest.pop("physical_feature_mondrian_conformal_status")
    elif failure == "missing_artifact":
        artifact_path.unlink()
    elif failure == "bad_artifact_sha":
        artifact["sha256"] = "0" * 64
    elif failure == "insufficient_support":
        summary = json.loads(artifact_path.read_text(encoding="utf-8"))
        summary["analysis"]["support"]["supported_evaluation_cell_fraction"] = 0.79
        _write_json(artifact_path, summary)
        artifact["sha256"] = _sha256(artifact_path)
    elif failure == "invalid_decision":
        summary = json.loads(artifact_path.read_text(encoding="utf-8"))
        summary["decision"] = "ADOPT_WITHOUT_EVIDENCE"
        summary["recommendation"]["decision"] = "ADOPT_WITHOUT_EVIDENCE"
        _write_json(artifact_path, summary)
        artifact["sha256"] = _sha256(artifact_path)
        manifest["physical_feature_mondrian_conformal_decision"] = "ADOPT_WITHOUT_EVIDENCE"
        manifest["physical_feature_mondrian_conformal_recommendation"] = "ADOPT_WITHOUT_EVIDENCE"

    _write_json(manifest_path, manifest)
    record_path = checkpoint / "checkpoint_record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["model_manifest_sha256"] = _sha256(manifest_path)
    _write_json(record_path, record)
    out_dir = tmp_path / "audit"

    assert _run(module, campaign, pool, out_dir) == 2
    summary = json.loads((out_dir / "accepted_1m_campaign_completion_audit_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checkpoint_audit"]["overall_status"] == "FAIL"
    assert any("mondrian" in reason for reason in summary["checkpoint_audit"]["reasons"])
    assert not (out_dir / "accepted_1m_campaign_completion.pass").exists()


@pytest.mark.parametrize(
    "failure",
    ["duplicate_geometry", "near_duplicate_geometry", "missing_s4p", "tampered_fingerprint"],
)
def test_strict_completion_audit_refuses_incomplete_or_duplicate_evidence(tmp_path, failure):
    module = _load_module()
    campaign, pool = _make_campaign(
        tmp_path,
        module,
        duplicate_geometry=failure == "duplicate_geometry",
        near_duplicate_geometry=failure == "near_duplicate_geometry",
        missing_s4p=failure == "missing_s4p",
        tampered_fingerprint=failure == "tampered_fingerprint",
    )
    out_dir = tmp_path / "audit"

    assert _run(module, campaign, pool, out_dir) == 2
    summary = json.loads((out_dir / "accepted_1m_campaign_completion_audit_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "DO_NOT_CLAIM_ONE_MILLION_COMPLETE"
    assert not (out_dir / "accepted_1m_campaign_completion.pass").exists()
    if failure in {"duplicate_geometry", "near_duplicate_geometry"}:
        assert summary["dataset_audit"]["duplicate_geometry_count"] == 1
    if failure == "tampered_fingerprint":
        assert summary["dataset_audit"]["geometry_identity_fingerprint_mismatch_count"] == 1
