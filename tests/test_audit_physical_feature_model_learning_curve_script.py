from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import csv
import hashlib
import math
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_feature_model_learning_curve.py"
    spec = importlib.util.spec_from_file_location("audit_physical_feature_model_learning_curve_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_checkpoint(
    root: Path,
    index: int,
    error: float,
    split_seed: int = 20260711,
    *,
    panel_error: float | None = None,
    identity_prefix: str = "common",
    target_shift: float = 0.0,
    omit_sample_index: int | None = None,
) -> None:
    checkpoint = root / f"checkpoint_{index:02d}_n{index * 100000}"
    model = checkpoint / "model_attempt_001"
    model.mkdir(parents=True)
    nn_summary = model / "nn_summary.json"
    nn_summary.write_text(
        json.dumps({"selected_candidate": {"test_normalized_rmse": error * 2.0}}),
        encoding="utf-8",
    )
    predictions = model / "physical_feature_tandem_inverse_test_predictions.csv"
    prediction_rows = []
    spans = (2.5, 2.5, 20.0, 0.8)
    names = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
    panel_error = error if panel_error is None else panel_error
    for sample_index in range(8):
        if omit_sample_index is not None and sample_index == omit_sample_index:
            continue
        identity = hashlib.sha256(f"{identity_prefix}-{sample_index}".encode("ascii")).hexdigest()
        targets = (0.7 + 0.1 * sample_index + target_shift, 0.8 + 0.1 * sample_index, 8.0 + sample_index, 0.1)
        row = {"source_geometry_identity_sha256": identity}
        for name, target, span in zip(names, targets, spans):
            row[f"target__{name}"] = target
            row[f"reconstructed__{name}"] = target + panel_error * span
        prediction_rows.append(row)
    with predictions.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(prediction_rows[0]))
        writer.writeheader()
        writer.writerows(prediction_rows)
    test_identity_fingerprint = hashlib.sha256(
        "".join(
            f"{row['source_geometry_identity_sha256']}\n"
            for row in sorted(prediction_rows, key=lambda item: item["source_geometry_identity_sha256"])
        ).encode("ascii")
    ).hexdigest()
    tandem_summary = model / "tandem_summary.json"
    tandem_summary.write_text(
        json.dumps(
            {
                "test_predictions_csv": str(predictions),
                "test_predictions_csv_sha256": hashlib.sha256(predictions.read_bytes()).hexdigest(),
                "model_comparison_contract": {
                    "fingerprint_sha256": "a" * 64,
                    "trainer_implementation_sha256": "b" * 64,
                    "input_columns": ["Lp", "Ls", "Q", "|K|"],
                    "geometry_columns": [f"g{item}" for item in range(10)],
                },
                "evaluation_isolation": {
                    "overall_status": "PASS",
                    "checks": {
                        "all_geometry_identities_are_sha256": True,
                        "all_geometry_identities_unique": True,
                        "all_rows_assigned_once": True,
                        "no_geometry_identity_overlap_across_splits": True,
                        "all_splits_nonempty": True,
                    },
                    "geometry_identity_set_sha256": {"test": test_identity_fingerprint},
                    "test_set_used_for_gradient_updates": False,
                    "test_set_used_for_early_stopping": False,
                    "test_set_used_for_model_or_hyperparameter_selection": False,
                    "test_set_used_for_acceptance_threshold_tuning": False,
                    "test_set_used_only_for_post_training_evaluation": True,
                },
            }
        ),
        encoding="utf-8",
    )
    tandem_summary_sha = hashlib.sha256(tandem_summary.read_bytes()).hexdigest()
    manifest = model / "accepted_physical_feature_model_checkpoint_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "checkpoint_index": index,
                "accepted_checkpoint_count": index * 100000,
                "overall_status": "PASS",
                "uniformity_status": "PASS",
                "seed_contract": {
                    "selection_seed": 1000 + index,
                    "model_initialization_seed": 20260711,
                    "cross_checkpoint_split_seed": split_seed,
                },
                "tandem_ood_split_audit": {
                    "physical_cell_partition_method": "seeded_sha256_threshold_by_cell_id",
                    "physical_cell_partition_stable_for_existing_cells": True,
                    "physical_cell_bins_per_dimension": 4,
                    "physical_cell_lower": [0.5, 0.5, 5.0, 0.0],
                    "physical_cell_upper": [3.0, 3.0, 25.0, 0.8],
                },
                "tandem_ood_metrics": {
                    "forward_proxy": {"test_range_normalized_rmse": error / 2.0},
                    "tandem_inverse": {"test_response_range_normalized_rmse": error},
                    "range_normalization": {"source": "declared_physical_cell_range"},
                    "test_row_count": len(prediction_rows),
                },
                "artifacts": {
                    "nn": {"path": str(nn_summary), "exists": True},
                    "tandem_physical_cell_ood": {
                        "path": str(tandem_summary),
                        "exists": True,
                        "sha256": tandem_summary_sha,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    (checkpoint / "checkpoint_record.json").write_text(
        json.dumps(
            {
                "checkpoint_index": index,
                "target_accepted_count": index * 100000,
                "model_manifest": str(manifest),
                "overall_status": "PASS",
                "uniformity_status": "PASS",
                "formal_checkpoint_pass": True,
                "model_manifest_sha256": manifest_sha,
            }
        ),
        encoding="utf-8",
    )


def _refresh_evidence_hashes(root: Path, index: int) -> None:
    checkpoint = root / f"checkpoint_{index:02d}_n{index * 100000}"
    record_path = checkpoint / "checkpoint_record.json"
    record = json.loads(record_path.read_text())
    manifest_path = Path(record["model_manifest"])
    manifest = json.loads(manifest_path.read_text())
    tandem_artifact = manifest["artifacts"]["tandem_physical_cell_ood"]
    tandem_path = Path(tandem_artifact["path"])
    tandem_artifact["sha256"] = hashlib.sha256(tandem_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    record["model_manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    record_path.write_text(json.dumps(record), encoding="utf-8")


def test_reports_plateau_only_after_comparable_history(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    for index, error in enumerate((0.1000, 0.0900, 0.0885, 0.0872), start=1):
        _write_checkpoint(root, index, error)

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--minimum-checkpoints",
            "3",
            "--plateau-window",
            "3",
            "--max-marginal-relative-improvement",
            "0.02",
            "--minimum-common-test-rows",
            "8",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "PASS"
    assert summary["comparison_contract"]["comparable"] is True
    assert summary["fixed_common_test_panel"]["status"] == "PASS"
    assert summary["fixed_common_test_panel"]["common_geometry_count"] == 8
    assert summary["fixed_common_test_panel"]["first_panel_retention_fraction"] == 1.0
    assert summary["decision"] == "PLATEAU_REVIEW_DO_NOT_AUTOMATICALLY_STOP_CAMPAIGN"
    assert summary["power_law_extrapolation"]["status"] == "ADVISORY_ONLY"
    assert Path(summary["artifacts"]["csv"]).is_file()


def test_rejects_learning_curve_with_changing_split_seed(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10, split_seed=1)
    _write_checkpoint(root, 2, 0.08, split_seed=2)

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--minimum-common-test-rows",
            "8",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["comparison_contract"]["checks"]["same_split_seed"] is False
    assert summary["decision"] == "FIX_CROSS_CHECKPOINT_COMPARISON_CONTRACT"


def test_rejects_learning_curve_when_test_geometry_panel_changes(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10)
    _write_checkpoint(root, 2, 0.08, identity_prefix="different")

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--minimum-common-test-rows",
            "8",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["comparison_contract"]["checks"]["fixed_common_test_panel_pass"] is False
    assert summary["fixed_common_test_panel"]["checks"]["minimum_first_panel_retention"] is False


def test_rejects_learning_curve_when_common_geometry_target_drifts(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10)
    _write_checkpoint(root, 2, 0.08, target_shift=0.01)

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--minimum-common-test-rows",
            "8",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["fixed_common_test_panel"]["target_mismatch_count"] == 8
    assert summary["fixed_common_test_panel"]["checks"]["targets_stable_across_checkpoints"] is False


def test_rejects_dynamic_intersection_shrink_even_when_retention_threshold_would_pass(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10)
    _write_checkpoint(root, 2, 0.08, omit_sample_index=7)

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--minimum-common-test-rows",
            "7",
            "--minimum-first-panel-retention",
            "0.80",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text(encoding="utf-8")
    )
    panel = summary["fixed_common_test_panel"]
    assert summary["overall_status"] == "FAIL"
    assert panel["fixed_panel_geometry_count"] == 8
    assert panel["common_geometry_count"] == 8
    assert panel["intersection_geometry_count"] == 7
    assert panel["first_panel_retention_fraction"] == 0.875
    assert panel["checks"]["minimum_first_panel_retention"] is True
    assert panel["checks"]["exact_fixed_panel_coverage_all_checkpoints"] is False
    assert panel["checkpoint_evidence"][1]["fixed_panel_missing_count"] == 1
    assert math.isnan(summary["checkpoints"][1]["common_panel_response_range_normalized_rmse"])


def test_waits_truthfully_before_any_checkpoint_exists(tmp_path):
    module = _load_module()
    status = module.main(
        [
            "--checkpoint-root",
            str(tmp_path / "missing"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "WAITING_FOR_CHECKPOINTS"
    assert summary["decision"] == "WAIT_FOR_FIRST_MODEL_CHECKPOINT"


def test_rejects_skipped_checkpoint_prefix(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10)
    _write_checkpoint(root, 3, 0.08)

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--minimum-common-test-rows",
            "8",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["comparison_contract"]["checks"]["checkpoint_indices_form_contiguous_prefix"] is False
    assert summary["checkpoint_schedule"]["actual_indices"] == [1, 3]


def test_malformed_checkpoint_cannot_be_silently_ignored(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10)
    _write_checkpoint(root, 2, 0.08)
    record_path = root / "checkpoint_01_n100000" / "checkpoint_record.json"
    record = json.loads(record_path.read_text())
    Path(record["model_manifest"]).write_text("{broken", encoding="utf-8")

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--minimum-common-test-rows",
            "8",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert len(summary["rejected_checkpoint_records"]) == 1
    assert summary["comparison_contract"]["checks"]["no_rejected_checkpoint_records"] is False
    assert summary["decision"] == "FIX_CROSS_CHECKPOINT_COMPARISON_CONTRACT"


def test_rejects_record_manifest_identity_mismatch(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10)
    record_path = root / "checkpoint_01_n100000" / "checkpoint_record.json"
    record = json.loads(record_path.read_text())
    manifest_path = Path(record["model_manifest"])
    manifest = json.loads(manifest_path.read_text())
    manifest["accepted_checkpoint_count"] = 200000
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    record["model_manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    record_path.write_text(json.dumps(record), encoding="utf-8")

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert "index/count mismatch" in summary["rejected_checkpoint_records"][0]["reason"]


def test_rejects_model_contract_drift(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10)
    _write_checkpoint(root, 2, 0.08)
    tandem_path = root / "checkpoint_02_n200000" / "model_attempt_001" / "tandem_summary.json"
    tandem = json.loads(tandem_path.read_text())
    tandem["model_comparison_contract"]["fingerprint_sha256"] = "c" * 64
    tandem_path.write_text(json.dumps(tandem), encoding="utf-8")
    _refresh_evidence_hashes(root, 2)

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--minimum-common-test-rows",
            "8",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["comparison_contract"]["checks"]["same_model_comparison_fingerprint"] is False


def test_rejects_test_set_contamination_declaration(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10)
    tandem_path = root / "checkpoint_01_n100000" / "model_attempt_001" / "tandem_summary.json"
    tandem = json.loads(tandem_path.read_text())
    tandem["evaluation_isolation"]["test_set_used_for_early_stopping"] = True
    tandem_path.write_text(json.dumps(tandem), encoding="utf-8")
    _refresh_evidence_hashes(root, 1)

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--minimum-common-test-rows",
            "8",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["comparison_contract"]["checks"]["test_rows_excluded_from_training_and_selection"] is False


def test_rejects_tampered_test_prediction_csv(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10)
    predictions = (
        root
        / "checkpoint_01_n100000"
        / "model_attempt_001"
        / "physical_feature_tandem_inverse_test_predictions.csv"
    )
    predictions.write_text(predictions.read_text() + "\n", encoding="utf-8")

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert "test-prediction CSV SHA" in summary["rejected_checkpoint_records"][0]["reason"]


def test_complete_schedule_gate_is_optional_until_final_audit(tmp_path):
    module = _load_module()
    root = tmp_path / "checkpoints"
    _write_checkpoint(root, 1, 0.10)

    status = module.main(
        [
            "--checkpoint-root",
            str(root),
            "--out-dir",
            str(tmp_path / "out"),
            "--minimum-common-test-rows",
            "8",
            "--require-complete-schedule",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_model_learning_curve_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["comparison_contract"]["checks"]["complete_schedule_when_required"] is False
