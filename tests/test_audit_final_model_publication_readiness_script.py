from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import sys


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "audit_final_model_publication_readiness.py"
    spec = importlib.util.spec_from_file_location("audit_final_model_publication_readiness_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _base_evidence(tmp_path: Path, *, complete: bool = True) -> tuple[Path, Path, Path]:
    manifest = tmp_path / "model" / "final_manifest.json"
    _write_json(
        manifest,
        {
            "overall_status": "PASS",
            "model_test_status": "PASS",
            "accepted_checkpoint_count": 10,
            "input_contract": ["Lp", "Ls", "Q=min(Qp,Qs)", "|K|"],
            "output_contract": "10 independent geometry variables",
            "uniformity_status": "PASS",
            "broadband_sparameter_readiness_status": "PASS",
            "input_ablation_readiness_status": "PASS",
            "direct_tandem_shared_split_fingerprint": True,
            "physical_feature_boundary_ood_stress_status": "NOT_REPEATED_AFTER_900K",
            "physical_spec_spectral_expander_status": "COMPLETE_REVIEW_REQUIRED",
            "physical_spec_spectral_expander_test_complex_rmse": 0.08,
            "tandem_ood_split_audit": {
                "split_mode": "physical_cell_grouped",
                "physical_cell_overlap_count": 0,
                "all_rows_assigned_once": True,
                "physical_cell_partition_stable_for_existing_cells": True,
            },
            "tandem_ood_metrics": {
                "range_normalization": {"source": "declared_physical_cell_range"},
                "tandem_inverse": {"test_response_range_normalized_rmse": 0.05},
            },
        },
    )
    required = {
        "pool_summary_pass": True,
        "accepted_count_at_least_expected": True,
        "all_rows_finite_and_in_range": True,
        "all_rows_have_complete_geometry": True,
        "independent_geometry_unique": True,
        "touchstone_paths_unique": True,
        "all_touchstone_paths_are_s4p": True,
        "all_touchstone_files_nonempty": True,
        "all_frequency_metadata_match": True,
        "q_equals_min_qp_qs": True,
        "checkpoint_contract_pass": True,
        "learning_curve_has_ten_comparable_checkpoints": True,
        "final_uniformity_contract_pass": True,
        "final_model_manifest_pass": True,
    }
    completion = tmp_path / "campaign" / "completion.json"
    _write_json(
        completion,
        {
            "overall_status": "PASS" if complete else "FAIL",
            "decision": "ACCEPTED_1M_REAL_EMX_CAMPAIGN_COMPLETE" if complete else "DO_NOT_CLAIM_ONE_MILLION_COMPLETE",
            "checks": required,
            "dataset_audit": {
                "row_count": 10,
                "unique_geometry_digest_count": 10,
                "unique_touchstone_path_digest_count": 10,
            },
            "final_uniformity_checks": {
                "overall_status": True,
                "valid_count": True,
                "one_d_all_features": True,
                "all_six_pairs": True,
                "four_d_occupied": True,
                "explicit_ranges": True,
            },
            "artifacts": {"final_model_manifest": str(manifest)},
        },
    )
    learning = tmp_path / "campaign" / "learning.json"
    _write_json(
        learning,
        {"overall_status": "PASS", "checkpoint_count": 10, "comparison_contract": {"comparable": True}},
    )
    return completion, learning, manifest


def _validation_record(
    tmp_path: Path,
    index: int,
    *,
    k_error: float = 5.0,
    ports: int = 4,
    bias_sign: float = 1.0,
) -> Path:
    root = tmp_path / f"hfss_{index}"
    emx = root / f"sample_{index}_emx.s4p"
    hfss = root / f"sample_{index}_hfss.s4p"
    emx.parent.mkdir(parents=True, exist_ok=True)
    emx.write_text("! real EMX synthetic fixture\n", encoding="ascii")
    hfss.write_text("! real HFSS synthetic fixture\n", encoding="ascii")
    target_base = {"lp_nh": 1.0, "ls_nh": 1.2, "q": 12.0, "k": 0.5}
    target_errors = {"lp_nh": 4.0, "ls_nh": 6.0, "q": 7.0, "k": k_error}
    target_metrics = {}
    for name, error in target_errors.items():
        emx_value = target_base[name]
        hfss_value = emx_value * (1.0 + float(bias_sign) * error / 100.0)
        target_metrics[name] = {
            "status": "PASS" if error <= 10.0 else "FAIL",
            "emx": emx_value,
            "hfss_ads": hfss_value,
            "abs_error": abs(hfss_value - emx_value),
            "percent_error": error,
        }
    valid_metrics = {
        name: {"status": "PASS" if error <= 10.0 else "FAIL", "max_percent_error": error}
        for name, error in {"lp_nh": 5.0, "ls_nh": 7.0, "q": 8.0, "k": k_error}.items()
    }
    full = root / "full.json"
    valid = root / "valid.json"
    common = {"emx_source": str(emx), "hfss_ads_source": str(hfss)}
    _write_json(
        full,
        {
            **common,
            "frequency_window_hz": {"min": 5e9, "max": 60e9, "count": 111},
            "frequency_grid_checks": {
                "expected frequency points": {"status": "PASS"},
                "expected frequency step": {"status": "PASS"},
                "matching grid": {"status": "PASS"},
            },
            "target_marker": {
                "frequency_status": "PASS",
                "nearest_frequency_hz": 15e9,
                "metrics": target_metrics,
            },
        },
    )
    _write_json(
        valid,
        {
            **common,
            "criterion": {"max_percent_error": 10.0},
            "frequency_window_hz": {"min": 5e9, "max": 15e9, "count": 21},
            "metrics": valid_metrics,
        },
    )
    fingerprint = hashlib.sha256(f"geometry-{index}".encode()).hexdigest()
    record = root / "record.json"
    _write_json(
        record,
        {
            "overall_status": "PASS",
            "sample_id": f"sample-{index}",
            "full_grid_comparison_summary": str(full),
            "valid_window_comparison_summary": str(valid),
            "contract": {
                "same_geometry_verified": True,
                "same_process_stack_verified": True,
                "same_port_mapping_verified": True,
                "independent_geometry": True,
                "geometry_contract_sha256": fingerprint,
                "process_stack_contract_sha256": hashlib.sha256(b"process").hexdigest(),
                "port_contract_sha256": hashlib.sha256(b"ports").hexdigest(),
                "expected_touchstone_suffix": ".s4p",
                "expected_port_count": ports,
                "port_pairs": "1,2:3,4",
                "valid_window_basis": "BELOW_MIN_SRF_OVER_2",
                "emx_touchstone_sha256": _sha(emx),
                "hfss_touchstone_sha256": _sha(hfss),
            },
        },
    )
    return record


def _run(module, completion: Path, learning: Path, manifest: Path, out_dir: Path, records=()) -> int:
    argv = [
        "--campaign-completion-summary",
        str(completion),
        "--learning-curve-summary",
        str(learning),
        "--final-model-manifest",
        str(manifest),
        "--out-dir",
        str(out_dir),
        "--expected-total",
        "10",
        "--expected-checkpoints",
        "10",
        "--min-hfss-samples",
        "5",
    ]
    for record in records:
        argv.extend(["--hfss-validation-record", str(record)])
    return module.main(argv)


def test_complete_campaign_waits_truthfully_when_hfss_evidence_is_absent(tmp_path):
    module = _load_module()
    completion, learning, manifest = _base_evidence(tmp_path)
    out_dir = tmp_path / "out"

    assert _run(module, completion, learning, manifest, out_dir) == 0
    summary = json.loads((out_dir / "final_model_publication_readiness_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["campaign_completion_status"] == "PASS"
    assert summary["model_evidence_status"] == "PASS"
    assert summary["publication_readiness_status"] == "WAITING_FOR_EMX_HFSS_VALIDATION"
    assert (out_dir / "final_model_evidence_matrix.pass").is_file()
    assert not (out_dir / "final_model_publication_ready.pass").exists()


def test_five_independent_same_contract_hfss_samples_can_pass_publication_gate(tmp_path):
    module = _load_module()
    completion, learning, manifest = _base_evidence(tmp_path)
    records = [_validation_record(tmp_path, index) for index in range(5)]
    out_dir = tmp_path / "out"

    assert _run(module, completion, learning, manifest, out_dir, records) == 0
    summary = json.loads((out_dir / "final_model_publication_readiness_summary.json").read_text())
    assert summary["cross_solver_validation_status"] == "PASS"
    assert summary["publication_readiness_status"] == "PASS"
    assert summary["hfss_validation"]["passing_record_count"] == 5
    assert summary["cross_solver_bias_diagnostic"]["status"] == "READY"
    assert summary["cross_solver_bias_diagnostic"]["decision"] == "REVIEW_COKRIGING_STYLE_CROSS_SOLVER_RESIDUAL_ABLATION"
    assert set(summary["cross_solver_bias_diagnostic"]["stable_bias_metrics"]) == {"lp_nh", "ls_nh", "q", "k"}
    assert (out_dir / "final_model_publication_ready.pass").is_file()


def test_alternating_cross_solver_bias_does_not_nominate_residual_calibration(tmp_path):
    module = _load_module()
    completion, learning, manifest = _base_evidence(tmp_path)
    records = [
        _validation_record(tmp_path, index, bias_sign=1.0 if index % 2 == 0 else -1.0)
        for index in range(6)
    ]
    out_dir = tmp_path / "out"

    assert _run(module, completion, learning, manifest, out_dir, records) == 0
    summary = json.loads((out_dir / "final_model_publication_readiness_summary.json").read_text())
    diagnostic = summary["cross_solver_bias_diagnostic"]
    assert summary["publication_readiness_status"] == "PASS"
    assert diagnostic["status"] == "READY"
    assert diagnostic["decision"] == "NO_STABLE_CROSS_SOLVER_BIAS_DETECTED"
    assert diagnostic["stable_bias_metrics"] == []


def test_missing_signed_values_waits_for_bias_diagnostic_without_overriding_raw_pass(tmp_path):
    module = _load_module()
    completion, learning, manifest = _base_evidence(tmp_path)
    records = [_validation_record(tmp_path, index) for index in range(5)]
    for record in records:
        record_data = json.loads(record.read_text())
        full_path = Path(record_data["full_grid_comparison_summary"])
        full = json.loads(full_path.read_text())
        for item in full["target_marker"]["metrics"].values():
            item.pop("emx", None)
            item.pop("hfss_ads", None)
        _write_json(full_path, full)
    out_dir = tmp_path / "out"

    assert _run(module, completion, learning, manifest, out_dir, records) == 0
    summary = json.loads((out_dir / "final_model_publication_readiness_summary.json").read_text())
    diagnostic = summary["cross_solver_bias_diagnostic"]
    assert summary["publication_readiness_status"] == "PASS"
    assert diagnostic["status"] == "WAITING_FOR_SIGNED_TARGET_METRICS"
    assert diagnostic["decision"] == "REGENERATE_COMPARISON_SUMMARIES_WITH_SIGNED_EMX_HFSS_VALUES"


def test_wrong_port_contract_or_metric_error_cannot_be_published(tmp_path):
    module = _load_module()
    completion, learning, manifest = _base_evidence(tmp_path)
    records = [_validation_record(tmp_path, index) for index in range(3)]
    records.extend(
        [
            _validation_record(tmp_path, 3, ports=8),
            _validation_record(tmp_path, 4, k_error=12.0),
        ]
    )
    out_dir = tmp_path / "out"

    assert _run(module, completion, learning, manifest, out_dir, records) == 0
    summary = json.loads((out_dir / "final_model_publication_readiness_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["cross_solver_validation_status"] == "FAIL_EMX_HFSS_VALIDATION"
    assert summary["publication_readiness_status"] == "FAIL_EMX_HFSS_VALIDATION"
    assert not (out_dir / "final_model_publication_ready.pass").exists()


def test_incomplete_campaign_fails_final_model_evidence_matrix(tmp_path):
    module = _load_module()
    completion, learning, manifest = _base_evidence(tmp_path, complete=False)
    out_dir = tmp_path / "out"

    assert _run(module, completion, learning, manifest, out_dir) == 2
    summary = json.loads((out_dir / "final_model_publication_readiness_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["campaign_completion_status"] == "FAIL"
    assert summary["publication_readiness_status"] == "FAIL_BASE_MODEL_EVIDENCE"
    assert not (out_dir / "final_model_evidence_matrix.pass").exists()
