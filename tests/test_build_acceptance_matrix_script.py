from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_matrix_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_acceptance_matrix.py"
    spec = importlib.util.spec_from_file_location("build_acceptance_matrix_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_package_file(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_sha256s(package_dir: Path, files: list[Path]) -> None:
    lines = []
    for path in files:
        rel = path.relative_to(package_dir)
        lines.append(f"{'1' * 64}  {rel}")
    (package_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _target_emx_postrun_command_text(matrix) -> str:
    return "\n".join(matrix.TARGET_EMX_POSTRUN_REQUIRED_FRAGMENTS) + "\n"


class BuildAcceptanceMatrixScriptTest(TransformerToolboxTestBase):
    def test_matrix_keeps_overall_incomplete_until_external_runs_exist(self) -> None:
        matrix = _load_matrix_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "project"
            package = root / "package"
            repo = project / "rfic-transformer-inverse-design"
            for script in (
                "build_mars_handoff_bundle.py",
                "verify_mars_handoff_install.py",
                "package_mars_dataset_run.py",
                "verify_mars_dataset_package.py",
                "audit_mars_run_progress.py",
                "watch_mars_run_progress.py",
                "discover_and_verify_mars_emx_return.py",
                "watch_mars_emx_return.py",
                "discover_mars_emx_cadence_paths.py",
                "patch_mars_config_paths.py",
                "preflight_dataset_config.py",
                "prepare_mars_wideband_config.py",
                "prepare_target_emx_wideband_rerun.py",
                "prepare_target_emx_postrun_validation.py",
                "backfill_ground_clearance_audit.py",
                "run_dataset_quality_gates.py",
                "extract_touchstone_response_features.py",
                "audit_response_feature_coverage.py",
                "audit_zin_coverage.py",
                "audit_sampling_distribution.py",
                "select_hfss_validation_samples.py",
                "diagnose_cm_mismatch.py",
                "audit_delivery_package.py",
                "build_clean_delivery_zip.py",
                "run_local_project_health_check.py",
                "compare_emx_hfss_ads.py",
                "run_package_selfcheck_compare.py",
                "run_hfss_emx_validation_batch.py",
                "run_accepted_emx_hfss_ads_validation.py",
                "verify_accepted_emx_hfss_ads_figures.py",
                "audit_hfss_model_geometry_assets.py",
                "build_validation_chain_decision_card.py",
                "build_mars_next_action_packet.py",
                "audit_248k_launch_readiness.py",
                "audit_ads_metric_formula_consistency.py",
                "audit_ads_photo_reference_alignment.py",
                "build_emx_first_validation_gate.py",
                "audit_photo_matched_vs_target_geometry.py",
                "scan_s4p_ads_photo_reference_candidates.py",
                "build_photo_matched_hfss_reference_evidence.py",
                "plot_emx_hfss_ads_style_metrics.py",
                "verify_target_emx_postrun_package.py",
            ):
                _write_package_file(repo / "scripts" / script)
            _write_package_file(
                repo / "scripts" / "compare_emx_hfss_ads.py",
                "\n".join(matrix.COMPARE_SCRIPT_REQUIRED_FLAGS) + "\n",
            )
            _write_package_file(
                repo / "scripts" / "run_accepted_emx_hfss_ads_validation.py",
                "\n".join(matrix.ACCEPTED_EMX_HFSS_RUNNER_REQUIRED_FRAGMENTS) + "\n",
            )
            _write_package_file(
                repo / "scripts" / "verify_accepted_emx_hfss_ads_figures.py",
                "\n".join(matrix.ACCEPTED_FIGURE_VERIFIER_REQUIRED_FRAGMENTS) + "\n",
            )
            path_discovery_source = (
                "--hint-command\n"
                "DEFAULT_HINT_COMMANDS\n"
                "target_emx_wideband_rerun.commands.sh\n"
                "hint_command_files\n"
                "hint-command:\n"
            )
            _write_package_file(repo / "scripts" / "discover_mars_emx_cadence_paths.py", path_discovery_source)
            _write_package_file(
                repo / "tests" / "test_verify_accepted_emx_hfss_ads_figures_script.py",
                "test_accepts_complete_final_figure_evidence\n"
                "test_rejects_metric_error_over_gate\n"
                "test_rejects_missing_plot_data_and_blank_png\n",
            )
            _write_package_file(
                repo / "tests" / "test_audit_ads_metric_formula_consistency_script.py",
                "test_formula_audit_recovers_known_synthetic_transformer_metrics\n",
            )
            _write_package_file(
                repo / "scripts" / "build_validation_chain_decision_card.py",
                "BLOCKED_BY_EMX_REFERENCE\n"
                "PASS_DIAGNOSTIC_ONLY\n"
                "DO_NOT_USE_HFSS_COMPARISON\n"
                "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN\n"
                "--hfss-geometry-summary\n"
                "HFSS geometry asset traceability\n"
                "BLOCKED_BY_HFSS_GEOMETRY_GATE\n"
                "WAIT_FOR_HFSS_GEOMETRY_AUDIT\n"
                "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS\n"
                "A diagnostic HFSS geometry or physical PASS cannot override\n",
            )
            _write_package_file(
                repo / "tests" / "test_run_accepted_emx_hfss_ads_validation_script.py",
                "test_rejects_accepted_import_summary_without_verifier_evidence\n"
                "test_hfss_audit_command_requires_nonzero_coupling_gate\n"
                "test_compare_checks_reject_metric_error_over_gate_even_when_status_pass\n"
                "test_compare_checks_reject_relaxed_compare_criterion\n"
                "test_compare_checks_reject_mismatched_summary_sources\n"
                "test_rejects_missing_hfss_geometry_summary_for_final_traceability\n"
                "review-only\n",
            )
            _write_json(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "touchstone_preflight_hfss_wideband_20260613"
                / "touchstone_transformer_audit_summary.json",
                {
                    "overall_status": "PASS",
                    "port_count": 4,
                    "frequency": {"start_hz": 1e8, "stop_hz": 50e9, "step_hz": 1e8, "points": 500},
                    "checks": [
                        {"name": "source identity", "status": "PASS", "detail": "expected=HFSS"},
                        {"name": "differential Z finiteness", "status": "PASS", "detail": "finite"},
                        {"name": "differential Z reciprocity", "status": "PASS", "detail": "ok"},
                        {"name": "differential Z positive-realness", "status": "PASS", "detail": "ok"},
                        {"name": "ADS-equivalent metric finiteness", "status": "PASS", "detail": "finite"},
                    ],
                },
            )
            _write_json(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "full_sheetimpedance_freqexpr_m9pow1p5_refined_analysis"
                / "emx_hfss_ads_comparison_summary.json",
                {
                    "overall_status": "PASS",
                    "frequency_overlap_hz": {"count": 9},
                    "metrics": {name: {"status": "PASS"} for name in ("k", "qp", "qs", "lp_nh", "ls_nh")},
                },
            )
            _write_json(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "emx_first_validation_gate_20260613"
                / "emx_first_validation_gate_summary.json",
                {
                    "overall_status": "FAIL",
                    "decision": "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE",
                    "checks": [
                        {"name": "final ADS sweep coverage", "status": "FAIL", "detail": "narrowband only"},
                        {"name": "ADS photo anchor", "status": "FAIL", "detail": "mismatch"},
                    ],
                },
            )
            _write_json(
                project
                / "validation_chain_decision_20260614"
                / "validation_chain_decision_summary.json",
                {
                    "overall_status": "BLOCKED_BY_EMX_REFERENCE",
                    "decision": "DO_NOT_USE_HFSS_COMPARISON",
                    "stages": [
                        {
                            "name": "EMX-first golden reference",
                            "status": "FAIL",
                            "decision": "BLOCK_HFSS_COMPARISON",
                        },
                        {
                            "name": "HFSS geometry asset traceability",
                            "status": "PASS_DIAGNOSTIC_ONLY",
                            "decision": "DO_NOT_USE_UNTIL_EMX_ACCEPTED",
                        },
                        {
                            "name": "HFSS physical S4P gate",
                            "status": "PASS_DIAGNOSTIC_ONLY",
                            "decision": "DO_NOT_COMPARE_UNTIL_EMX_ACCEPTED",
                        },
                        {
                            "name": "Accepted EMX-vs-HFSS/ADS comparison",
                            "status": "BLOCKED_BY_EMX_REFERENCE",
                            "decision": "DO_NOT_USE_HFSS_COMPARISON",
                        },
                    ],
                },
            )
            _write_package_file(
                project / "validation_chain_decision_20260614" / "validation_chain_decision_report.md",
            )
            formula_dir = (
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "ads_metric_formula_consistency_20260614"
            )
            _write_json(
                formula_dir / "ads_metric_formula_consistency_summary.json",
                {
                    "overall_status": "PASS",
                    "decision": "ADS_FORMULA_IMPLEMENTATION_ACCEPTED",
                    "frequency_ghz": {"start": 5.0, "stop": 50.0, "step": 0.1, "points": 451},
                    "checks": [
                        {
                            "status": "PASS",
                            "name": "helper formula equals direct ADS expression",
                            "detail": "ok",
                        },
                        {"status": "PASS", "name": "known transformer metric recovery", "detail": "ok"},
                        {"status": "PASS", "name": "formula audit frequency grid", "detail": "ok"},
                        {"status": "PASS", "name": "ADS Data Display equation template", "detail": "ok"},
                    ],
                    "metric_recovery_errors": {
                        "qp": {"max_percent_error": 1.0e-12},
                        "k": {"max_percent_error": 5.0e-13},
                    },
                },
            )
            _write_package_file(formula_dir / "ads_metric_formula_consistency_report.md")
            _write_package_file(formula_dir / "ads_metric_formula_consistency_curves.png")
            _write_package_file(formula_dir / "ADS_DATA_DISPLAY_LP_LS_Q_K_TEMPLATE.md")
            _write_json(
                project / "mars_next_action_packet_20260614" / "mars_next_action_packet_summary.json",
                {
                    "overall_status": "PASS",
                    "decision": "READY_FOR_MARS_TARGET_EMX_RERUN",
                    "status_counts": {"PASS": 6},
                    "guardrails": [
                        "Do not run HFSS comparison until EMX-first accepts a regenerated target EMX S4P.",
                    ],
                    "local_postrun_import_requirements": [
                        "EMX-first port-pair sensitivity CSV gate PASS: 24 ordered four-port pairings checked, approved pair 1,2:3,4 PASS, and max_percent_error <= 5%",
                    ],
                },
            )
            _write_package_file(
                project / "mars_next_action_packet_20260614" / "MARS_NEXT_ACTION_PACKET_20260614_CN.md",
            )
            _write_json(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "geometry_quality_audit_final500_selected_20260613"
                / "geometry_quality_audit_summary.json",
                {
                    "overall_status": "PASS",
                    "layout_counts": {
                        "port_count": 4,
                        "cadence_pin_purpose": 51,
                        "signal_labeled_port_count": 4,
                        "grounded_port_count": 4,
                        "internal_signal_labeled_port_count": 4,
                        "internal_ground_labeled_port_count": 4,
                    },
                },
            )
            angle_metrics = {}
            for prefix in ("primary", "secondary"):
                angle_metrics.update(
                    {
                        f"{prefix}_winding_centerline_internal_turn_count": 8,
                        f"{prefix}_winding_centerline_terminal_interface_count": 2,
                        f"{prefix}_winding_centerline_min_internal_angle_deg": 135.0,
                        f"{prefix}_winding_centerline_max_internal_angle_deg": 135.0,
                        f"{prefix}_winding_centerline_min_terminal_angle_deg": 90.0,
                        f"{prefix}_winding_centerline_max_terminal_angle_deg": 90.0,
                    }
                )
            _write_json(
                project / "hfss_validation" / "final500_ec6698dfc575950b" / "summary.json",
                {"geometry_check": {"ok": True, "metrics": angle_metrics}},
            )
            _write_json(
                project / "final500_clearance_audit_visuals_20260613" / "clearance_audit_visual_summary.json",
                {
                    "record_count": 500,
                    "pass_count": 468,
                    "reject_count": 32,
                    "selected_status": "pass_signal_to_shield_clearance",
                },
            )
            _write_json(project / "mars_dataset_500_wideband_20260613_preflight.json", {"overall_status": "PASS"})
            _write_json(project / "mars_dataset_500_wideband_20260613_preflight_strict_paths.json", {"overall_status": "FAIL"})
            _write_json(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "target_emx_wideband_rerun_20260613"
                / "target_emx_wideband_rerun_summary.json",
                {
                    "overall_status": "PASS",
                    "decision": "READY_FOR_MARS_EMX_RERUN_COMMAND_ONLY",
                    "generated_frequency_hz": {
                        "start": 5_000_000_000,
                        "stop": 50_000_000_000,
                        "step": 100_000_000,
                        "points": 451,
                    },
                },
            )
            _write_package_file(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "target_emx_wideband_rerun_20260613"
                / "target_emx_wideband_rerun.commands.sh",
            )
            _write_package_file(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "target_emx_wideband_rerun_20260613"
                / "target_emx_wideband_frequency_grid.csv",
            )
            _write_json(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "target_emx_wideband_rerun_20260613"
                / "target_emx_wideband_postrun_validation_summary.json",
                {
                    "overall_status": "PASS",
                    "decision": "READY_FOR_MARS_POSTRUN_VALIDATION",
                    "checks": [{"status": "PASS", "name": "post-run validation command fragments", "detail": "ok"}],
                },
            )
            _write_package_file(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "target_emx_wideband_rerun_20260613"
                / "target_emx_wideband_postrun_validation.commands.sh",
                _target_emx_postrun_command_text(matrix),
            )
            _write_package_file(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "target_emx_wideband_rerun_20260613"
                / "target_emx_wideband_postrun_validation_report.md",
            )
            _write_json(
                project / "248k_launch_readiness_local_20260613" / "248k_launch_readiness_summary.json",
                {
                    "overall_status": "NOT_READY",
                    "checks": [
                        {"status": "PASS", "name": "248k config loads", "detail": "ok"},
                        {"status": "NOT_READY", "name": "248k EMX/Cadence paths", "detail": "placeholder"},
                        {"status": "NOT_READY", "name": "wideband 500 quality gates", "detail": "missing"},
                        {"status": "NOT_READY", "name": "sampled HFSS/EMX batch gate", "detail": "missing"},
                    ],
                },
            )
            _write_package_file(project / "248k_launch_readiness_local_20260613" / "248k_launch_readiness_report.md")
            _write_json(
                project / "literature_improvement_create_only_50" / "zin_coverage_audit_20260613" / "zin_coverage_audit_summary.json",
                {"overall_status": "NOT_READY"},
            )
            response_dir = project / "hfss_validation" / "final500_ec6698dfc575950b" / "response_feature_extraction_package_demo_20260613"
            _write_json(
                response_dir / "response_feature_extraction_summary.json",
                {"overall_status": "PASS", "counts": {"ok_rows": 2}},
            )
            _write_package_file(response_dir / "response_features.csv", "evaluation,ok,zin_center_real_ohm\na,true,50\n")
            _write_json(response_dir / "zin_coverage_audit_min500_20260613" / "zin_coverage_audit_summary.json", {"overall_status": "FAIL"})
            _write_package_file(project / "literature_improvement_create_only_50" / "dataset_visualizations_20260613_evidence" / "13_dataset_dashboard.png")
            _write_json(
                project / "literature_improvement_create_only_50" / "sampling_distribution_audit_20260613" / "sampling_distribution_audit_summary.json",
                {"overall_status": "PASS", "space_filling_summary": {"status": "PASS"}},
            )
            _write_package_file(
                project
                / "literature_improvement_create_only_50"
                / "sampling_distribution_audit_20260613"
                / "sampling_distribution_space_filling_strata.png"
            )

            package_files = [
                package / "ec6698dfc575950b_HFSS_WIDEBAND_0p1_50GHz_step0p1.s4p",
                package / "hfss_model_views" / "hfss_payload_geometry_top_annotated.png",
                package / "hfss_model_views" / "hfss_payload_geometry_isometric.png",
                package / "hfss_model_views" / "hfss_payload_geometry_quality_checks.png",
                package / "hfss_model_views" / "ec6698dfc575950b_hfss_model_no_air.step",
                package / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json",
            ]
            for path in package_files[:-1]:
                _write_package_file(path)
            _write_json(
                package / "hfss_model_geometry_asset_audit_20260614" / "hfss_model_geometry_asset_audit_summary.json",
                {
                    "overall_status": "PASS",
                    "decision": "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS",
                    "checks": [
                        {"name": "HFSS top-view PNG", "status": "PASS"},
                        {"name": "HFSS isometric-view PNG", "status": "PASS"},
                        {"name": "HFSS geometry-quality PNG", "status": "PASS"},
                        {"name": "HFSS STEP model", "status": "PASS"},
                    ],
                },
            )
            fake_assets = [
                {
                    "title": "blocked emx figure",
                    "file": "assets/30a_emx_first_gate_core_metrics.png",
                    "status": "OK",
                    "evidence_use": "BLOCKED_AS_FINAL_EVIDENCE",
                    "usage_note": "blocked until EMX-first passes",
                },
                {
                    "title": "diagnostic hfss figure",
                    "file": "assets/18_hfss_touchstone_ads_equivalent_metrics.png",
                    "status": "OK",
                    "evidence_use": "DIAGNOSTIC_ONLY",
                    "usage_note": "standalone HFSS diagnostic only",
                },
                {
                    "title": "clearance figure",
                    "file": "assets/06_clearance_pass_fail_counts.png",
                    "status": "OK",
                    "evidence_use": "ACCEPTED_FOR_CURRENT_CLAIM",
                    "usage_note": "limited clearance claim",
                },
            ]
            _write_json(
                package_files[-1],
                {
                    "asset_count": 27,
                    "asset_usage_counts": {
                        "ACCEPTED_FOR_CURRENT_CLAIM": 1,
                        "BLOCKED_AS_FINAL_EVIDENCE": 1,
                        "DIAGNOSTIC_ONLY": 1,
                    },
                    "assets": fake_assets,
                },
            )
            _write_sha256s(package, package_files)
            _write_package_file(project / "hfss_validation" / "final500_ec6698dfc575950b" / "FINAL_DESKTOP_PACKAGE_HASH_20260613.txt")
            _write_package_file(project / "mars_handoff_bundle_20260613.tar.gz")
            _write_package_file(project / "mars_handoff_bundle_20260613.tar.gz.sha256")
            _write_package_file(project / "mars_handoff_bundle_20260613" / "MARS_HANDOFF_INVENTORY_20260613.json")
            _write_package_file(project / "mars_handoff_bundle_20260613" / "SHA256SUMS.txt")
            _write_package_file(project / "mars_handoff_bundle_20260613" / "configs" / "mars_dataset_500_wideband_20260613.yaml")
            _write_package_file(project / "mars_handoff_bundle_20260613" / "scripts" / "backfill_ground_clearance_audit.py")
            _write_package_file(project / "mars_handoff_bundle_20260613" / "scripts" / "discover_and_verify_mars_emx_return.py")
            _write_package_file(project / "mars_handoff_bundle_20260613" / "scripts" / "watch_mars_emx_return.py")
            _write_package_file(
                project / "mars_handoff_bundle_20260613" / "scripts" / "discover_mars_emx_cadence_paths.py",
                path_discovery_source,
            )
            _write_package_file(project / "mars_handoff_bundle_20260613" / "scripts" / "prepare_target_emx_wideband_rerun.py")
            _write_package_file(project / "mars_handoff_bundle_20260613" / "scripts" / "prepare_target_emx_postrun_validation.py")
            _write_package_file(project / "mars_handoff_bundle_20260613" / "rfic_transformer_inverse_design" / "dataset.py")
            _write_package_file(project / "mars_handoff_bundle_20260613" / "rfic_transformer_inverse_design" / "execution" / "evaluator.py")
            _write_package_file(project / "mars_handoff_bundle_20260613" / "rfic_transformer_inverse_design" / "layout" / "export.py")
            compare_command = "python3 " + " ".join(matrix.COMPARE_GATE_REQUIRED_FRAGMENTS) + "\n"
            final_figure_gate = (
                "python3 scripts/verify_accepted_emx_hfss_ads_figures.py "
                "--accepted-summary /path/to/accepted_emx_hfss_ads_validation_summary.json\n"
                "python3 scripts/run_accepted_emx_hfss_ads_validation.py "
                "--hfss-geometry-summary /path/to/hfss_model_geometry_asset_audit_summary.json\n"
                "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS\n"
                "overall_status=PASS\n"
                "decision=ACCEPT_FINAL_LP_LS_Q_K_FIGURES\n"
                "DO_NOT_USE_FINAL_LP_LS_Q_K_FIGURES\n"
                "ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md\n"
                "Lp/Ls/Qp/Qs/K figures must remain diagnostic or blocked\n"
            )
            runbook_text = compare_command + final_figure_gate
            _write_package_file(project / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md", runbook_text)
            _write_package_file(
                project / "mars_handoff_bundle_20260613" / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md",
                runbook_text,
            )
            _write_package_file(
                project / "mars_handoff_bundle_20260613" / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh",
                "CONFIG=${1:-configs/mars_dataset_500_wideband_20260613.yaml}\n"
                ".venv/bin/python scripts/run_dataset_quality_gates.py \"$RUN_DIR\"\n"
                "--touchstone-shape-window-start-ghz 5.0\n"
                "--touchstone-shape-window-stop-ghz 30\n"
                "--touchstone-max-shape-spike-ratio 4\n"
                "--touchstone-max-shape-relative-step 0.25\n",
            )
            _write_package_file(
                project
                / "mars_handoff_bundle_20260613"
                / "project_runbook"
                / "target_emx_wideband_rerun_20260613"
                / "target_emx_wideband_rerun.commands.sh",
                "/cae/apps/data/cadence-2025/installs/EMX20251/bin/emx TRANSFORMER_021_ec6698df --cadence-pins=51 "
                "--port=P001=P001:P001_G --port=P002=P002:P002_G --port=P003=P003:P003_G --port=P004=P004:P004_G "
                "emx_wideband_5_50_0p1/emx.s4p 5000000000 50000000000\n",
            )
            _write_package_file(
                project
                / "mars_handoff_bundle_20260613"
                / "project_runbook"
                / "target_emx_wideband_rerun_20260613"
                / "target_emx_wideband_postrun_validation.commands.sh",
            )
            _write_json(
                project / "mars_handoff_verify_20260613_latest" / "mars_handoff_verify_summary.json",
                {
                    "overall_status": "PASS",
                    "checks": [
                        {"name": "HFSS/EMX compare grid gate", "status": "PASS", "detail": "ok"},
                        {"name": "accepted EMX/HFSS final runner contract", "status": "PASS", "detail": "ok"},
                        {"name": "accepted final figure verifier contract", "status": "PASS", "detail": "ok"},
                        {"name": "accepted final figure verifier runbook contract", "status": "PASS", "detail": "ok"},
                    ],
                },
            )
            _write_json(
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "delivery_package_audit_20260613"
                / "delivery_package_audit_summary.json",
                {
                    "overall_status": "PASS",
                    "checks": [
                        {
                            "name": name,
                            "status": "PASS",
                            "detail": (
                                "usage_counts={'ACCEPTED_FOR_CURRENT_CLAIM': 1, 'BLOCKED_AS_FINAL_EVIDENCE': 1, 'DIAGNOSTIC_ONLY': 1}; "
                                "blocked_final_comparison_assets=1; diagnostic_hfss_standalone_assets=1; "
                                "validation_chain_accepted=False"
                                if name == "report asset usage contract"
                                else "ok"
                            ),
                        }
                        for name in (
                            "package SHA manifest",
                            "package bytecode/cache hygiene",
                            "desktop zip integrity",
                            "desktop zip clean metadata",
                            "desktop zip bytecode/cache hygiene",
                            "desktop zip external SHA",
                            "report manifest counts",
                            "report assets",
                            "report asset usage contract",
                            "report local health pytest gate",
                            "report html image references",
                            "report image nonblank",
                            "package selfcheck compare gate",
                            "ADS metric formula consistency evidence",
                            "validation scripts inventory",
                            "validation scripts syntax",
                            "local health-check runner contract",
                            "target EMX return watcher contract",
                            "MARS dataset package helper contract",
                            "MARS dataset package verifier contract",
                            "HFSS/EMX batch compare runner contract",
                            "accepted EMX/HFSS final runner contract",
                            "accepted final figure verifier contract",
                            "EMX-first gate script contract",
                            "target EMX post-run import verifier contract",
                            "248k launch readiness contract",
                            "MARS handoff package source contract",
                            "MARS handoff config contract",
                            "MARS handoff target EMX rerun command contract",
                            "MARS handoff target EMX post-run validation command contract",
                            "MARS handoff path discovery helper contract",
                            "MARS handoff target EMX return watcher contract",
                            "MARS handoff path patcher smoke",
                            "MARS handoff run-progress contract",
                            "MARS handoff quality-gate contract",
                            "MARS handoff HFSS/EMX compare grid gate",
                            "MARS handoff accepted EMX/HFSS final runner contract",
                            "MARS handoff accepted final figure verifier contract",
                            "MARS handoff accepted final figure verifier runbook contract",
                            "MARS handoff EMX-first gate script contract",
                            "MARS handoff target EMX post-run import verifier contract",
                            "MARS handoff HFSS/EMX batch runner contract",
                            "MARS handoff 248k launch readiness contract",
                            "MARS handoff tar SHA",
                            "MARS handoff tar contents",
                            "MARS handoff shape-window gate",
                            "MARS handoff extracted package source contract",
                            "MARS handoff extracted config contract",
                            "MARS handoff extracted target EMX rerun command contract",
                            "MARS handoff extracted target EMX post-run validation command contract",
                            "MARS handoff extracted path discovery helper contract",
                            "MARS handoff extracted target EMX return watcher contract",
                            "MARS handoff extracted path patcher smoke",
                            "MARS handoff extracted run-progress contract",
                            "MARS handoff extracted quality-gate contract",
                            "MARS handoff extracted HFSS/EMX compare grid gate",
                            "MARS handoff extracted accepted EMX/HFSS final runner contract",
                            "MARS handoff extracted accepted final figure verifier contract",
                            "MARS handoff extracted accepted final figure verifier runbook contract",
                            "MARS handoff extracted EMX-first gate script contract",
                            "MARS handoff extracted target EMX post-run import verifier contract",
                            "MARS handoff extracted HFSS/EMX batch runner contract",
                            "MARS handoff extracted 248k launch readiness contract",
                            "MARS handoff extracted shape-window gate",
                            "acceptance matrix boundary",
                        )
                    ],
                },
            )

            out_json = root / "acceptance.json"
            out_md = root / "acceptance.md"
            status = matrix.main(
                [
                    "--project-root",
                    str(project),
                    "--package-dir",
                    str(package),
                    "--out-json",
                    str(out_json),
                    "--out-md",
                    str(out_md),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            data = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(data["generated_utc"], matrix.FIXED_MATRIX_GENERATED_UTC)
            self.assertIn(matrix.FIXED_MATRIX_GENERATED_UTC, out_md.read_text(encoding="utf-8"))
            self.assertEqual(data["overall_status"], "INCOMPLETE")
            by_req = {item["requirement"]: item for item in data["items"]}
            self.assertEqual(by_req["Single HFSS validation sample has usable wideband .s4p evidence"]["status"], "PASS")
            self.assertEqual(by_req["Touchstone response feature extractor can generate real Zin labels"]["status"], "PASS")
            self.assertEqual(
                by_req["Geometry gate enforces grounded shield ports, pin 51, and manufacturable angles"]["status"],
                "PASS",
            )
            self.assertIn(
                "135 deg octagon",
                by_req["Geometry gate enforces grounded shield ports, pin 51, and manufacturable angles"]["finding"],
            )
            self.assertEqual(by_req["Zin coverage spans enough impedance space for inverse training"]["status"], "PENDING")
            self.assertEqual(by_req["Wideband 500 pilot config is ready for MARS execution"]["status"], "PARTIAL")
            self.assertEqual(
                by_req["Target EMX .s4p is accepted as the golden ADS/physics reference"]["status"],
                "BLOCKED",
            )
            self.assertIn(
                "final ADS sweep coverage",
                by_req["Target EMX .s4p is accepted as the golden ADS/physics reference"]["finding"],
            )
            self.assertEqual(
                by_req["Strict HFSS/EMX wideband compare gate is enforced in handoff and delivery audits"]["status"],
                "PASS",
            )
            self.assertEqual(
                by_req[
                    "Accepted-EMX HFSS/ADS final runner enforces EMX verifier evidence and nonzero coupling"
                ]["status"],
                "PASS",
            )
            self.assertEqual(
                by_req[
                    "Accepted final Lp/Ls/Q/K figure verifier enforces plot_data, no-extrapolation, <=5%, PNG sanity, and 15 GHz marker table"
                ]["status"],
                "PASS",
            )
            self.assertIn(
                "451-point no-extrapolation plot_data",
                by_req[
                    "Accepted final Lp/Ls/Q/K figure verifier enforces plot_data, no-extrapolation, <=5%, PNG sanity, and 15 GHz marker table"
                ]["finding"],
            )
            self.assertEqual(
                by_req["EMX/HFSS/ADS validation-chain decision gate is generated and conservative"]["status"],
                "PASS",
            )
            self.assertIn(
                "HFSS geometry and physical PASS evidence are explicitly diagnostic-only",
                by_req["EMX/HFSS/ADS validation-chain decision gate is generated and conservative"]["finding"],
            )
            self.assertEqual(
                by_req["ADS-style Lp/Ls/M/K/Q extraction formulas are self-checked on a known transformer"][
                    "status"
                ],
                "PASS",
            )
            self.assertIn(
                "Formula audit PASS",
                by_req["ADS-style Lp/Ls/M/K/Q extraction formulas are self-checked on a known transformer"][
                    "finding"
                ],
            )
            self.assertIn(
                "ADS Data Display template",
                by_req["ADS-style Lp/Ls/M/K/Q extraction formulas are self-checked on a known transformer"][
                    "finding"
                ],
            )
            self.assertIn(
                "proves extraction math",
                by_req["ADS-style Lp/Ls/M/K/Q extraction formulas are self-checked on a known transformer"][
                    "next_action"
                ],
            )
            self.assertEqual(
                by_req["MARS next-action packet is generated and keeps EMX-first as the next gate"]["status"],
                "PASS",
            )
            self.assertIn(
                "target 5-50 GHz / 0.1 GHz EMX rerun",
                by_req["MARS next-action packet is generated and keeps EMX-first as the next gate"]["finding"],
            )
            self.assertIn(
                "approved port-pair CSV gate",
                by_req["MARS next-action packet is generated and keeps EMX-first as the next gate"]["finding"],
            )
            self.assertIn(
                "decodable, sufficiently large, and nonblank",
                by_req[
                    "Accepted-EMX HFSS/ADS final runner enforces EMX verifier evidence and nonzero coupling"
                ]["finding"],
            )
            self.assertEqual(by_req["Target sample EMX wideband rerun command is traceable"]["status"], "PASS")
            self.assertEqual(by_req["Target sample EMX post-run validation command is traceable"]["status"], "PASS")
            self.assertEqual(by_req["MARS pull, progress, path, local gate, and handoff helpers exist"]["status"], "PASS")
            self.assertEqual(by_req["HFSS modeled geometry is visually traceable"]["status"], "PASS")
            self.assertIn(
                "decodable, sufficiently large, and nonblank",
                by_req["HFSS modeled geometry is visually traceable"]["finding"],
            )
            self.assertEqual(by_req["Desktop delivery package passes self-audit"]["status"], "PASS")
            self.assertIn(
                "decodable sufficiently large nonblank PNG plot checks",
                by_req["Desktop delivery package passes self-audit"]["finding"],
            )
            self.assertIn(
                "ADS metric formula consistency evidence",
                by_req["Desktop delivery package passes self-audit"]["finding"],
            )
            self.assertIn(
                "report local health full pytest gate",
                by_req["Desktop delivery package passes self-audit"]["finding"],
            )
            self.assertIn(
                "approved port-pair CSV gate",
                by_req["Desktop delivery package passes self-audit"]["finding"],
            )
            self.assertEqual(
                by_req["Report visual assets have explicit evidence-use boundaries"]["status"],
                "PASS",
            )
            self.assertIn(
                "BLOCKED_AS_FINAL_EVIDENCE",
                by_req["Report visual assets have explicit evidence-use boundaries"]["finding"],
            )
            self.assertEqual(by_req["248k production dataset is generated, audited, and report-ready"]["status"], "PENDING")
            self.assertIn(
                "launch readiness gate is NOT_READY",
                by_req["248k production dataset is generated, audited, and report-ready"]["finding"],
            )
            self.assertIn(
                "sampled HFSS/EMX batch gate",
                by_req["248k production dataset is generated, audited, and report-ready"]["finding"],
            )
            self.assertEqual(by_req["Desktop package is reproducible and hash-tracked"]["status"], "PASS")

    def test_target_emx_postrun_command_missing_coupling_gate_is_pending(self) -> None:
        matrix = _load_matrix_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "project"
            package = root / "package"
            summary_path = (
                project
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "target_emx_wideband_rerun_20260613"
                / "target_emx_wideband_postrun_validation_summary.json"
            )
            command_path = summary_path.with_name("target_emx_wideband_postrun_validation.commands.sh")
            _write_json(
                summary_path,
                {
                    "overall_status": "PASS",
                    "decision": "READY_FOR_MARS_POSTRUN_VALIDATION",
                    "checks": [{"status": "PASS", "name": "post-run validation command fragments", "detail": "ok"}],
                },
            )
            command_path.parent.mkdir(parents=True, exist_ok=True)
            command_path.write_text(
                _target_emx_postrun_command_text(matrix).replace("--min-target-abs-k 0.05\n", ""),
                encoding="utf-8",
            )

            item = matrix._target_emx_postrun_validation_item(project, json.loads(summary_path.read_text()))

            self.assertEqual(item.status, "PENDING")
            self.assertIn("--min-target-abs-k 0.05", item.finding)

    def test_delivery_package_self_audit_requires_local_pytest_gate(self) -> None:
        matrix = _load_matrix_module()
        item = matrix._delivery_package_audit_item(
            Path("/fixture/project"),
            {
                "overall_status": "PASS",
                "checks": [
                    {"name": "package SHA manifest", "status": "PASS", "detail": "ok"},
                ],
            },
        )

        self.assertEqual(item.status, "PENDING")
        self.assertIn("report local health pytest gate", item.finding)

    def test_missing_package_sha_keeps_package_reproducibility_pending(self) -> None:
        matrix = _load_matrix_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "project"
            package = root / "package"
            report_manifest = package / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json"
            _write_json(
                report_manifest,
                {
                    "asset_count": 27,
                    "asset_usage_counts": {"ACCEPTED_FOR_CURRENT_CLAIM": 1},
                    "assets": [
                        {
                            "title": "fixture",
                            "status": "OK",
                            "file": "assets/fixture.png",
                            "evidence_use": "ACCEPTED_FOR_CURRENT_CLAIM",
                            "usage_note": "fixture",
                        }
                    ],
                },
            )
            _write_package_file(project / "hfss_validation" / "final500_ec6698dfc575950b" / "FINAL_DESKTOP_PACKAGE_HASH_20260613.txt")

            out_json = root / "acceptance.json"
            status = matrix.main(
                [
                    "--project-root",
                    str(project),
                    "--package-dir",
                    str(package),
                    "--out-json",
                    str(out_json),
                    "--out-md",
                    str(root / "acceptance.md"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            data = json.loads(out_json.read_text(encoding="utf-8"))
            by_req = {item["requirement"]: item for item in data["items"]}
            self.assertEqual(by_req["Desktop package is reproducible and hash-tracked"]["status"], "PENDING")
