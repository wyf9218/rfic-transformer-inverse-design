from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_physical_feature_s8p_launch_packet.py"
    spec = importlib.util.spec_from_file_location("build_physical_feature_s8p_launch_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_valid_s8p_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "target:",
                "  topology_mode: 1t1t",
                "  frequency_start_hz: 5000000000.0",
                "  frequency_stop_hz: 60000000000.0",
                "  frequency_step_hz: 500000000.0",
                "  band_points: 111",
                "topology:",
                "  primary:",
                "    turns: 1",
                "    center_tap: true",
                "    vdd_bar:",
                "      enabled: true",
                "      bar_layer: 74",
                "      width_um: 10.0",
                "      offset_um: 12.0",
                "  secondary:",
                "    turns: 1",
                "    center_tap: true",
                "    vdd_bar:",
                "      enabled: true",
                "      bar_layer: 39",
                "      width_um: 10.0",
                "      offset_um: 12.0",
                "emx:",
                "  port_mode: single_ended_shield_grounded",
                "  cadence_pin_purpose: 51",
                "  differential_port_pairs: '1,4:5,6'",
                "  power_line_8port:",
                "    enabled: true",
                "    bridge_width_um: 10.0",
                "    vertical_length_diameter_ratio: 1.5",
                "    bridge_y_policy: center",
                "    bridge_motion_axis: x_only",
                "    port_ground_reference: shield",
                "    port_map: [P001, P002, P003, P004, P005, P006, P007, P008]",
                "    role_labels:",
                "      primary_top: P001",
                "      left_power_top: P002",
                "      left_power_bottom: P003",
                "      primary_bottom: P004",
                "      secondary_bottom: P005",
                "      secondary_top: P006",
                "      right_power_top: P007",
                "      right_power_bottom: P008",
            ]
        ),
        encoding="utf-8",
    )


def _write_port_map_approval_summary(path: Path, *, approved: bool = True, port_pairs: str = "1,4:5,6") -> None:
    status = "APPROVED" if approved else "CANDIDATE_REQUIRES_USER_ADVISOR_APPROVAL"
    decision = "PORT_MAP_APPROVED_FOR_MARS_EMX_RUN" if approved else "AWAITING_USER_ADVISOR_PORT_MAP_APPROVAL"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "approval_status": status,
                "decision": decision,
                "port_pairs": port_pairs,
                "touchstone_port_order": [f"P{index:03d}" for index in range(1, 9)],
                "role_records": [
                    {"order": 1, "port": "P002", "role": "left_power_top"},
                    {"order": 2, "port": "P003", "role": "left_power_bottom"},
                    {"order": 3, "port": "P001", "role": "primary_top"},
                    {"order": 4, "port": "P004", "role": "primary_bottom"},
                    {"order": 5, "port": "P006", "role": "secondary_top"},
                    {"order": 6, "port": "P005", "role": "secondary_bottom"},
                    {"order": 7, "port": "P007", "role": "right_power_top"},
                    {"order": 8, "port": "P008", "role": "right_power_bottom"},
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_geometry_contract_approval_summary(path: Path, *, approved: bool = True) -> None:
    status = "APPROVED" if approved else "CANDIDATE_REQUIRES_USER_ADVISOR_APPROVAL"
    decision = "GEOMETRY_CONTRACT_APPROVED_FOR_MARS_EMX_RUN" if approved else "AWAITING_USER_ADVISOR_GEOMETRY_CONTRACT_APPROVAL"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "approval_status": status,
                "decision": decision,
                "approved_geometry_contract": {
                    "bridge_width_um": 10.0,
                    "superseded_literal_10nm_bridge_width_um": 0.01,
                    "vertical_length_reference_dimension": "max(primary_outer_height_um, secondary_outer_height_um)",
                    "vertical_length_diameter_ratio": 1.5,
                    "ground_frame_width_um": 100.0,
                    "ground_frame_policy": "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
                    "differential_pair_label_map": [
                        {"pair": 1, "ports": [1, 4], "labels": ["P001", "P004"]},
                        {"pair": 2, "ports": [5, 6], "labels": ["P005", "P006"]},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )


class BuildPhysicalFeatureS8PLaunchPacketScriptTest(TransformerToolboxTestBase):
    def test_builds_bootstrap_launch_packet_without_existing_physical_feature_dataset(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--bootstrap-geometry-candidate-queue",
                    "--config",
                    str(config),
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--out-dir",
                    str(root / "packet"),
                    "--inverse-candidate-count",
                    "12",
                    "--emx-max-count",
                    "12",
                    "--expected-emx-count",
                    "12",
                    "--jobs",
                    "4",
                    "--expected-jobs",
                    "4",
                    "--no-package",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "physical_feature_s8p_launch_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["port_map_approval_summary"]["approval_status"], "APPROVED")
            self.assertEqual(summary["candidate_source_mode"], "bootstrap_geometry_queue")
            self.assertEqual(summary["dataset_dir"], "")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertTrue(checks["dataset_dir_exists_or_bootstrap_mode"]["pass"])
            self.assertTrue(checks["target_features_present_or_bootstrap_mode"]["pass"])
            commands = (root / "packet" / "physical_feature_s8p_launch.commands.sh").read_text(encoding="utf-8")
            self.assertIn('PYTHON="${REPO_ROOT}/.venv/bin/python"', commands)
            self.assertIn("command -v python3", commands)
            self.assertIn('"${PYTHON}"', commands)
            self.assertIn('"${REPO_ROOT}/scripts/audit_power_line_8port_contract.py"', commands)
            self.assertNotIn("--expected-bridge-width-um", commands)
            self.assertNotIn("--expected-power-line-bridge-width-um", commands)
            self.assertIn('"${REPO_ROOT}/scripts/build_s8p_geometry_bootstrap_candidate_queue.py"', commands)
            self.assertNotIn(str(Path(__file__).resolve().parents[1] / "scripts" / "audit_power_line_8port_contract.py"), commands)
            self.assertIn("build_s8p_geometry_bootstrap_candidate_queue.py", commands)
            self.assertIn("s8p_geometry_bootstrap_candidate_queue.csv", commands)
            self.assertIn("--expected-differential-port-pairs", commands)
            self.assertIn("run_candidate_queue_dataset_parallel.py", commands)
            self.assertIn("summarize_next_gen_s8p_mars_run.py", commands)
            self.assertIn("run_s8p_hfss_postrun_validation_from_aedt_packet.py", commands)
            self.assertIn("build_s8p_final_report_evidence_packet.py", commands)
            self.assertIn("build_next_gen_s8p_objective_acceptance_audit.py", commands)
            self.assertIn("next_gen_s8p_objective_acceptance", commands)
            self.assertIn("s8p_final_report_evidence_packet", commands)
            self.assertIn("Build post-EMX Lp/Ls/Q/K inverse training table from generated S8P labels", json.dumps(summary["commands"]))
            self.assertIn("build_physical_feature_inverse_training_table.py", commands)
            self.assertIn("audit_physical_feature_inverse_model_quality.py", commands)
            self.assertIn("train_physical_feature_inverse_model.py", commands)
            self.assertIn("physical_feature_saved_inverse_model", commands)
            self.assertIn("scalar_q_feature_dataset", commands)
            self.assertNotIn("physical_feature_saved_inverse_target_layout_smoke", commands)
            self.assertNotIn("physical_feature_inverse_geometry_prediction", commands)
            self.assertEqual(
                Path(summary["inverse_model_artifacts"]["post_emx_inverse_training_manifest"]).resolve(),
                (
                    root
                    / "packet"
                    / "s8p_emx_candidate_run"
                    / "dataset_quality_gates_s8p_physical_feature"
                    / "physical_feature_inverse_training_table"
                    / "physical_feature_inverse_training_manifest.json"
                ).resolve(),
            )

    def test_builds_ready_launch_packet_from_valid_config_and_targets(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            dataset.mkdir()
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--config",
                    str(config),
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--out-dir",
                    str(root / "packet"),
                    "--inverse-target",
                    "lp_nh_center=0.8",
                    "--inverse-target",
                    "ls_nh_center=1.1",
                    "--inverse-target",
                    "q_center=9",
                    "--inverse-target",
                    "k_center=0.45",
                    "--inverse-candidate-count",
                    "12",
                    "--emx-max-count",
                    "12",
                    "--expected-emx-count",
                    "12",
                    "--jobs",
                    "4",
                    "--expected-jobs",
                    "4",
                    "--no-package",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "physical_feature_s8p_launch_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "READY_TO_REVIEW_AND_RUN_ON_MARS")
            self.assertEqual(summary["port_map_approval_summary"]["status"], "PASS")
            self.assertEqual(summary["targets"][0]["k_center"], 0.45)
            self.assertEqual(summary["parallel_emx_contract"]["emx_max_count"], 12)
            self.assertEqual(summary["parallel_emx_contract"]["jobs"], 4)
            self.assertEqual(summary["parallel_emx_contract"]["max_touchstone_files_checked"], 500)
            self.assertIn("full 500-row run", summary["parallel_emx_contract"]["touchstone_check_scope"])
            self.assertEqual(summary["input_feature_contract"]["zin_columns"], [])
            self.assertEqual(len(summary["commands"]), 21)
            emx_command = next(
                item for item in summary["commands"] if item["name"] == "Run candidate geometries through 8-worker EMX dataset generation"
            )
            self.assertIn("--expected-touchstone-extension", emx_command["command"])
            self.assertIn(".s8p", emx_command["command"])
            self.assertIn("--expected-ports", emx_command["command"])
            self.assertIn("8", emx_command["command"])
            self.assertIn("--max-touchstone-checks", emx_command["command"])
            self.assertEqual(emx_command["command"][emx_command["command"].index("--max-touchstone-checks") + 1], "500")
            quality_command = next(
                item for item in summary["commands"] if item["name"] == "Run S8P physical-feature quality gates and select validation sample"
            )
            self.assertIn("--s8p-max-touchstone-checks", quality_command["command"])
            self.assertEqual(quality_command["command"][quality_command["command"].index("--s8p-max-touchstone-checks") + 1], "500")
            run_status_command = next(
                item for item in summary["commands"] if item["name"] == "Summarize current next-gen S8P MARS run status"
            )
            self.assertIn("--max-touchstone-checks", run_status_command["command"])
            self.assertEqual(run_status_command["command"][run_status_command["command"].index("--max-touchstone-checks") + 1], "500")
            commands = (root / "packet" / "physical_feature_s8p_launch.commands.sh").read_text(encoding="utf-8")
            self.assertIn("audit_power_line_8port_contract.py", commands)
            self.assertIn("run_candidate_queue_dataset.py", commands)
            self.assertIn("layout_smoke_create_only", commands)
            self.assertIn("layout_smoke_8port_audit", commands)
            self.assertIn("run_candidate_queue_dataset_parallel.py", commands)
            self.assertIn("audit_selected_power_line_8port_layout_samples.py", commands)
            self.assertIn("audit_s8p_port_pair_physical_candidates.py", commands)
            self.assertIn("selected_s8p_port_pair_physical_candidate_audit", commands)
            self.assertIn("build_selected_s8p_hfss_handoff_packet.py", commands)
            self.assertIn("build_s8p_hfss_aedt_scripts_from_handoff.py", commands)
            self.assertIn("render_hfss_model_views_from_payload.py", commands)
            self.assertIn("selected_s8p_hfss_payload_views", commands)
            self.assertIn("run_s8p_hfss_postrun_validation_from_aedt_packet.py", commands)
            self.assertIn("selected_s8p_hfss_postrun_validation", commands)
            self.assertNotIn("--expected-bridge-width-um", commands)
            self.assertNotIn("--expected-power-line-bridge-width-um", commands)
            self.assertIn("build_s8p_final_report_evidence_packet.py", commands)
            self.assertIn("s8p_final_report_evidence_packet", commands)
            self.assertIn("summarize_next_gen_s8p_mars_run.py", commands)
            self.assertIn("next_gen_s8p_mars_run_status", commands)
            self.assertIn("build_next_gen_s8p_objective_acceptance_audit.py", commands)
            self.assertIn("--combined-approval-summary", commands)
            self.assertIn("next_gen_s8p_objective_acceptance", commands)
            self.assertIn("--objective-acceptance-summary", commands)
            self.assertIn("Refresh final S8P report evidence packet with objective acceptance audit", json.dumps(summary["commands"]))
            self.assertIn("--max-percent-error", commands)
            self.assertIn("--jobs", commands)
            self.assertIn("--expected-count", commands)
            self.assertIn("--expected-jobs", commands)
            self.assertIn("--resume-completed", commands)
            self.assertIn("--inverse-target-json", commands)
            self.assertIn("--inverse-geometry-config", commands)
            self.assertIn("audit-s8p-physical-feature-dataset", commands)
            self.assertIn("plan_physical_feature_balanced_acquisition.py", commands)
            self.assertIn("physical_feature_balanced_acquisition_plan", commands)
            self.assertIn("Build post-EMX Lp/Ls/Q/K inverse training table from generated S8P labels", json.dumps(summary["commands"]))
            self.assertIn("build_physical_feature_inverse_training_table.py", commands)
            self.assertIn("Audit post-EMX Lp/Ls/Q/K inverse-model quality with leave-one-out KNN", json.dumps(summary["commands"]))
            self.assertIn("audit_physical_feature_inverse_model_quality.py", commands)
            self.assertIn("Train saved baseline Lp/Ls/Q/K-to-geometry inverse model", json.dumps(summary["commands"]))
            self.assertIn("train_physical_feature_inverse_model.py", commands)
            self.assertIn("--target-json", commands)
            self.assertIn("physical_feature_inverse_model_quality", commands)
            self.assertIn("physical_feature_saved_inverse_model", commands)
            self.assertIn("physical_feature_inverse_model_target_predictions.csv", json.dumps(summary["commands"]))
            self.assertIn("Run saved-model target geometry create-only layout smoke", json.dumps(summary["commands"]))
            self.assertIn("physical_feature_saved_inverse_target_layout_smoke", commands)
            self.assertIn("physical_feature_inverse_training_table", commands)
            self.assertIn("scalar_q_feature_dataset", commands)
            self.assertIn("post_emx_inverse_training_manifest", json.dumps(summary["inverse_model_artifacts"]))
            self.assertIn("post_emx_inverse_model_quality_summary", json.dumps(summary["inverse_model_artifacts"]))
            self.assertIn("post_emx_saved_inverse_model_summary", json.dumps(summary["inverse_model_artifacts"]))
            self.assertIn("objective_acceptance_summary", json.dumps(summary["inverse_model_artifacts"]))
            self.assertIn("physical_feature_marginal_histograms.png", json.dumps(summary["commands"]))
            coverage_command = next(
                item
                for item in summary["commands"]
                if item["name"] == "Plan Lp/Ls/Q/K response-space coverage and sparse-bin acquisition targets"
            )
            self.assertIn("scalar_q_feature_dataset", " ".join(coverage_command["command"]))
            self.assertIn("lp_nh_center,ls_nh_center,q_center,k_center", coverage_command["command"])
            self.assertLess(commands.index("layout_smoke_create_only"), commands.index("run_candidate_queue_dataset_parallel.py"))
            self.assertLess(commands.index("run_dataset_quality_gates.py"), commands.index("plan_physical_feature_balanced_acquisition.py"))
            self.assertLess(commands.index("audit-s8p-physical-feature-dataset"), commands.index("build_physical_feature_inverse_training_table.py"))
            self.assertLess(commands.index("build_physical_feature_inverse_training_table.py"), commands.index("audit_physical_feature_inverse_model_quality.py"))
            self.assertLess(commands.index("audit_physical_feature_inverse_model_quality.py"), commands.index("train_physical_feature_inverse_model.py"))
            self.assertLess(commands.index("train_physical_feature_inverse_model.py"), commands.index("physical_feature_saved_inverse_target_layout_smoke"))
            self.assertLess(commands.index("run_s8p_hfss_postrun_validation_from_aedt_packet.py"), commands.index("build_s8p_final_report_evidence_packet.py"))
            self.assertLess(commands.index("summarize_next_gen_s8p_mars_run.py"), commands.index("build_next_gen_s8p_objective_acceptance_audit.py"))
            self.assertLess(commands.index("build_next_gen_s8p_objective_acceptance_audit.py"), commands.rindex("build_s8p_final_report_evidence_packet.py"))

    def test_candidate_port_map_approval_summary_is_not_ready(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            dataset.mkdir()
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval, approved=False)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--config",
                    str(config),
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--out-dir",
                    str(root / "packet"),
                    "--inverse-target",
                    "lp_nh_center=0.8",
                    "--inverse-target",
                    "ls_nh_center=1.1",
                    "--inverse-target",
                    "q_center=9",
                    "--inverse-target",
                    "k_center=0.45",
                    "--no-fail-exit",
                    "--no-package",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "physical_feature_s8p_launch_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["port_map_approval_summary_approved"]["pass"])
            self.assertEqual(summary["port_map_approval_summary"]["approval_status"], "CANDIDATE_REQUIRES_USER_ADVISOR_APPROVAL")

    def test_candidate_geometry_contract_approval_summary_is_not_ready(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            dataset.mkdir()
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry, approved=False)

            status = mod.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--config",
                    str(config),
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--out-dir",
                    str(root / "packet"),
                    "--inverse-target",
                    "lp_nh_center=0.8",
                    "--inverse-target",
                    "ls_nh_center=1.1",
                    "--inverse-target",
                    "q_center=9",
                    "--inverse-target",
                    "k_center=0.45",
                    "--no-fail-exit",
                    "--no-package",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "physical_feature_s8p_launch_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["geometry_contract_approval_summary_approved"]["pass"])
            self.assertEqual(summary["geometry_contract_approval_summary"]["approval_status"], "CANDIDATE_REQUIRES_USER_ADVISOR_APPROVAL")

    def test_todo_template_is_not_ready_but_still_writes_runbook(self) -> None:
        mod = _load_module()
        config = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            dataset.mkdir()
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--config",
                    str(config),
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--out-dir",
                    str(root / "packet"),
                    "--inverse-target",
                    "lp_nh_center=0.8",
                    "--no-fail-exit",
                    "--no-package",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "physical_feature_s8p_launch_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["config_loads"]["pass"])
            self.assertTrue((root / "packet" / "physical_feature_s8p_launch.commands.sh").is_file())

    def test_scalar_q_definition_switches_packet_to_single_q_feature_columns(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            dataset.mkdir()
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--config",
                    str(config),
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--out-dir",
                    str(root / "packet"),
                    "--scalar-q-definition",
                    "min",
                    "--inverse-target",
                    "lp_nh_center=0.8",
                    "--inverse-target",
                    "ls_nh_center=1.1",
                    "--inverse-target",
                    "q_center=9.5",
                    "--inverse-target",
                    "k_center=0.45",
                    "--no-package",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "physical_feature_s8p_launch_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["feature_columns"], ["lp_nh_center", "ls_nh_center", "q_center", "k_center"])
            quality_command = next(
                item for item in summary["commands"] if item["name"] == "Run S8P physical-feature quality gates and select validation sample"
            )
            self.assertIn("--derive-scalar-q-feature", quality_command["command"])
            self.assertIn("--scalar-q-definition", quality_command["command"])
            self.assertIn("min", quality_command["command"])
            self.assertTrue(
                any("scalar_q_feature_dataset/dataset_rows.csv" in output for output in quality_command["expected_outputs"])
            )
            commands = (root / "packet" / "physical_feature_s8p_launch.commands.sh").read_text(encoding="utf-8")
            self.assertIn("--derive-scalar-q-feature", commands)
            self.assertIn("--scalar-q-definition", commands)
            self.assertIn("q_center", commands)

    def test_placeholder_port_map_config_is_not_ready_even_if_yaml_loads(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            dataset.mkdir()
            config = root / "s8p_placeholder_ports.yaml"
            _write_valid_s8p_config(config)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)
            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    "[P001, P002, P003, P004, P005, P006, P007, P008]",
                    "[TODO_P001, TODO_P002, P003, P004, P005, P006, P007, P008]",
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--config",
                    str(config),
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--out-dir",
                    str(root / "packet"),
                    "--inverse-target",
                    "lp_nh_center=0.8",
                    "--inverse-target",
                    "ls_nh_center=1.1",
                    "--inverse-target",
                    "q_center=9",
                    "--inverse-target",
                    "k_center=0.45",
                    "--no-fail-exit",
                    "--no-package",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "physical_feature_s8p_launch_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["config_loads"]["pass"])
            self.assertIn("TODO", checks["config_loads"]["detail"])

    def test_non_500_or_non_8_parallel_goal_is_not_ready_by_default(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            dataset.mkdir()
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--config",
                    str(config),
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--out-dir",
                    str(root / "packet"),
                    "--inverse-target",
                    "lp_nh_center=0.8",
                    "--inverse-target",
                    "ls_nh_center=1.1",
                    "--inverse-target",
                    "q_center=9",
                    "--inverse-target",
                    "k_center=0.45",
                    "--emx-max-count",
                    "12",
                    "--jobs",
                    "4",
                    "--no-fail-exit",
                    "--no-package",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "physical_feature_s8p_launch_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["emx_sample_count_matches_goal"]["pass"])
            self.assertFalse(checks["parallel_worker_count_matches_goal"]["pass"])

    def test_zin_feature_columns_are_not_ready_for_physical_feature_inverse_goal(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            dataset.mkdir()
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--config",
                    str(config),
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--out-dir",
                    str(root / "packet"),
                    "--physical-feature-columns",
                    "zin_real_center_ohm,zin_imag_center_ohm,k_center",
                    "--inverse-target",
                    "zin_real_center_ohm=50",
                    "--inverse-target",
                    "zin_imag_center_ohm=120",
                    "--inverse-target",
                    "k_center=0.45",
                    "--no-fail-exit",
                    "--no-package",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "physical_feature_s8p_launch_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["inverse_inputs_do_not_use_zin"]["pass"])
            self.assertFalse(checks["inverse_inputs_include_lp_ls_q_k"]["pass"])
            self.assertIn("zin_real_center_ohm", summary["input_feature_contract"]["zin_columns"])
