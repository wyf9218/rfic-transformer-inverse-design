import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_accepted_1m_campaign_controller.sh"


def test_controller_requires_strict_final_completion_audit():
    syntax = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert syntax.returncode == 0, syntax.stderr

    source = SCRIPT.read_text(encoding="utf-8")
    required_fragments = (
        "audit_accepted_1m_campaign_completion.py",
        "audit_final_model_publication_readiness.py",
        "benchmark_emx_hfss_cross_solver_residual.py",
        "audit_physical_feature_extraction_frequency_stability.py",
        '--expected-total "$TOTAL_ACCEPTED"',
        '--checkpoint-count "$CHECKPOINTS"',
        '--checkpoint-size "$CHECKPOINT_SIZE"',
        "--check-touchstone-exists",
        '[[ ! -f "$FINAL_AUDIT_MARKER" ]]',
        '$(json_value "$FINAL_AUDIT_SUMMARY" overall_status)',
        'cp "$FINAL_AUDIT_SUMMARY" "$CAMPAIGN_ROOT/accepted_1m_campaign_final_summary.json"',
        '--campaign-completion-summary "$FINAL_AUDIT_SUMMARY"',
        '--min-hfss-samples "$PUBLICATION_MIN_HFSS_SAMPLES"',
        '[[ ! -f "$FINAL_EVIDENCE_MARKER" ]]',
        '$(json_value "$FINAL_EVIDENCE_SUMMARY" overall_status)',
        '$(json_value "$FINAL_EVIDENCE_SUMMARY" publication_readiness_status)',
        'cp "$FINAL_EVIDENCE_SUMMARY" "$CAMPAIGN_ROOT/accepted_1m_model_evidence_final_summary.json"',
        '$(json_value "$FINAL_RESIDUAL_SUMMARY" overall_status)',
        'cp "$FINAL_RESIDUAL_SUMMARY" "$CAMPAIGN_ROOT/accepted_1m_cross_solver_residual_final_summary.json"',
        'final cross-solver residual benchmark skipped because raw publication status is',
        '--pairwise-fallback-fraction "$PAIRWISE_FALLBACK_FRACTION"',
        '--pairwise-feature-pairs "$PAIRWISE_FEATURE_PAIRS"',
        '--pairwise-marginal-features "$PAIRWISE_MARGINAL_FEATURES"',
        'PREPARED_CHUNK2_POLICY_VERSION="${PREPARED_CHUNK2_POLICY_VERSION:-3}"',
        "physical_target_count_per_bin()",
        'local total_bins=$((PHYSICAL_FEATURE_BINS ** PHYSICAL_FEATURE_DIMENSIONS))',
        '--desired-total-count "$desired_total"',
        '--target-count-per-bin "$target_per_bin"',
        "planning_envelope.desired_total_count",
        "planning_envelope.target_count_per_bin",
        "adaptive queue cumulative target contract failed",
        "cumulative_uniformity_targets_scale",
        "cumulative_target_count_per_4d_bin",
        "cumulative_per_bin_targets[-1]==3907",
        "chunk2_queue_preparation_policy.json",
        "prepared chunk2 queue rejected before wait: missing or stale policy",
        'data.get("proxy_values_are_acquisition_only") is True',
        'int(args.get("pairwise_fallback_max_total") or 0)',
        'str(args.get("pairwise_feature_pairs") or "")==pairs',
        "refresh_campaign_proxy_to_real_calibration.py",
        "audit_proxy_uncertainty_real_emx_reliability.py",
        "benchmark_physical_feature_sample_efficiency.py",
        "audit_tandem_predicted_geometry_feasibility.py",
        '--geometry-config "$CONFIG"',
        'ACTIVE_CALIBRATION="$CALIBRATION_ROOT/active_proxy_to_real_calibration.json"',
        '--prediction-calibration-json "$ACTIVE_CALIBRATION"',
        'refresh_proxy_to_real_calibration "$round_number"',
        'run_first100k_sample_efficiency',
        '--training-counts 2400,3216,4000,8000,16000,32000,64000',
        '--expected-source-rows "$CHECKPOINT_SIZE"',
        '--expected-geometry-columns 10',
        '--require-training-csv-sha256 "$training_sha"',
        'could not freeze training-table SHA',
        "Old rounds\n# without prediction provenance are ignored",
        "checkpoint_record_valid()",
        '--expected-checkpoint-size "$CHECKPOINT_SIZE"',
        '--expected-total-checkpoints "$CHECKPOINTS"',
        "accepted_pool_row_count_at_record",
        "model manifest checkpoint index mismatch",
        "model manifest accepted count mismatch",
        "stale checkpoint marker rejected",
        'rm -f "$checkpoint_dir/model_test.complete" "$checkpoint_dir/formal_checkpoint.pass"',
        'mv "$record_dir/checkpoint_record.json.tmp" "$record_dir/checkpoint_record.json"',
        'ACQUISITION_MIX_JSON="${ACQUISITION_MIX_JSON:-}"',
        "select_physical_feature_acquisition_mix.py",
        "acquisition_mix_contract_authorized_exact",
        "active five-arm acquisition mix requires a fresh queue",
        '--acquisition-mix-json "$ACQUISITION_MIX_JSON"',
        "adaptive queue acquisition-mix source contract failed",
    )
    assert all(fragment in source for fragment in required_fragments)

    strict_audit_position = source.rfind('"$PYTHON_BIN" "$SCRIPT_DIR/audit_accepted_1m_campaign_completion.py"')
    evidence_matrix_position = source.rfind('"$PYTHON_BIN" "$SCRIPT_DIR/audit_final_model_publication_readiness.py"')
    residual_benchmark_position = source.rfind('"$PYTHON_BIN" "$SCRIPT_DIR/benchmark_emx_hfss_cross_solver_residual.py"')
    complete_marker_position = source.rfind('touch "$COMPLETE_MARKER"')
    assert 0 <= strict_audit_position < evidence_matrix_position < residual_benchmark_position < complete_marker_position

    # Campaign completion is allowed to be publication-pending, but the honest
    # evidence matrix itself must exist before the completion marker is written.
    final_block = source[evidence_matrix_position:complete_marker_position]
    assert "publication_readiness_status" in final_block
    assert "final_model_publication_ready.pass" not in final_block
    assert 'if [[ "$publication_status" == "PASS" ]]' in final_block
    assert "benchmark_emx_hfss_cross_solver_residual.py" in final_block


def test_controller_preflight_fails_closed_on_unauthorized_mix(tmp_path):
    counts = {
        "coarse_4d": 35704,
        "rare_marginal": 35712,
        "pairwise_gap": 24584,
        "random_exploration": 12000,
        "geometry_diversity": 12000,
    }
    contract = tmp_path / "mix.json"
    payload = {
        "overall_status": "PASS",
        "automatic_command_authorized": True,
        "proxy_values_are_acquisition_only": True,
        "production_acquisition_mix": {"queue_count": 120000, "counts": counts},
    }
    contract.write_text(json.dumps(payload), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("emx:\n  extra_args:\n    - --parallel=2\n", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "BASE": str(tmp_path / "base"),
            "CAMPAIGN_ROOT": str(tmp_path / "campaign"),
            "PYTHON_BIN": sys.executable,
            "ACQUISITION_MIX_JSON": str(contract),
            "CONFIG": str(config),
        }
    )

    passed = subprocess.run(
        ["bash", str(SCRIPT), "--preflight-only"],
        cwd=SCRIPT.parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert passed.returncode == 0, passed.stdout + passed.stderr
    summary = json.loads((tmp_path / "campaign" / "controller_preflight_summary.json").read_text())
    assert summary["checks"]["acquisition_mix_contract_authorized_exact"] is True
    assert summary["acquisition_mix_contract"]["production_acquisition_mix"]["counts"] == counts

    payload["automatic_command_authorized"] = False
    contract.write_text(json.dumps(payload), encoding="utf-8")
    failed = subprocess.run(
        ["bash", str(SCRIPT), "--preflight-only"],
        cwd=SCRIPT.parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode != 0
    summary = json.loads((tmp_path / "campaign" / "controller_preflight_summary.json").read_text())
    assert summary["checks"]["acquisition_mix_contract_authorized_exact"] is False
