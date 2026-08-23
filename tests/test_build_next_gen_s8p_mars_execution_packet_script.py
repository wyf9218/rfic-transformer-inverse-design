from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys

THE_BEST_ROLE_LABELS = (
    "primary_top=P001,left_power_top=P002,left_power_bottom=P003,primary_bottom=P004,"
    "secondary_bottom=P005,secondary_top=P006,right_power_top=P007,right_power_bottom=P008"
)


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_next_gen_s8p_mars_execution_packet.py"
    spec = importlib.util.spec_from_file_location("build_next_gen_s8p_mars_execution_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
                    "differential_pair_label_map": [
                        {"pair": 1, "ports": [1, 4], "labels": ["P001", "P004"]},
                        {"pair": 2, "ports": [5, 6], "labels": ["P005", "P006"]},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )


class BuildNextGenS8pMarsExecutionPacketScriptTest(TransformerToolboxTestBase):
    def test_missing_confirmations_writes_guarded_packet_but_not_ready(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            status = mod.main(["--out-dir", str(root / "packet"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "next_gen_s8p_mars_execution_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "FILL_REQUIRED_CONFIRMATIONS_BEFORE_MARS_RUN")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["P001-P008 port map is specified"]["status"], "FAIL")
            self.assertEqual(checks["approved S8P port map summary is specified"]["status"], "FAIL")
            self.assertEqual(checks["approved S8P geometry contract summary is specified"]["status"], "FAIL")
            self.assertEqual(checks["scalar Q definition is specified"]["status"], "FAIL")
            command_text = (root / "packet" / "next_gen_s8p_mars_execution.commands.sh").read_text(encoding="utf-8")
            self.assertIn("RUN_EMX=${RUN_EMX:-0}", command_text)
            self.assertIn("command -v python3", command_text)
            self.assertIn("command -v python", command_text)
            self.assertIn("export PYTHON", command_text)
            self.assertIn("No usable Python found for S8P MARS execution runbook.", command_text)
            self.assertIn("AUTO_INSTALL_PY_DEPS=${AUTO_INSTALL_PY_DEPS:-0}", command_text)
            self.assertIn("Missing Python dependencies", command_text)
            self.assertIn("RUN_EMX is not 1; stopping before the launch packet smoke/audit and 500-sample EMX queue.", command_text)
            self.assertIn("POWER_LINE_8PORT_PLACEMENT_POLICY=coil_opening_fixed_10um_port_ground_overlap", command_text)
            required_inputs = json.loads((root / "packet" / "next_gen_s8p_required_inputs.json").read_text(encoding="utf-8"))
            self.assertEqual(required_inputs["recommended_scalar_q_definition"], "min")
            self.assertEqual(required_inputs["power_line_8port_placement_policy"], "coil_opening_fixed_10um_port_ground_overlap")

    def test_full_confirmations_generate_ready_safe_mars_runbook(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "existing_dataset"
            dataset.mkdir()
            target = root / "target.json"
            target.write_text(json.dumps({"lp_nh_center": 1.0, "ls_nh_center": 1.2, "q_center": 10.0, "k_center": 0.5}), encoding="utf-8")
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--out-dir",
                    str(root / "packet"),
                    "--existing-dataset-dir",
                    str(dataset),
                    "--inverse-target-json",
                    str(target),
                    "--port-map",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--role-labels",
                    THE_BEST_ROLE_LABELS,
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "next_gen_s8p_mars_execution_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "MARS_S8P_EXECUTION_RUNBOOK_READY")
            self.assertEqual(
                summary["run_emx_guard"],
                "RUN_EMX=1 is required; the launch packet then runs a one-sample layout smoke/audit before the 500-sample EMX queue.",
            )
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["power-line placement policy uses fixed 10um port-ground overlap"]["status"], "PASS")
            self.assertEqual(
                checks["power-line placement policy uses fixed 10um port-ground overlap"]["detail"],
                "coil_opening_fixed_10um_port_ground_overlap",
            )
            self.assertEqual(checks["approved S8P port map summary is specified"]["status"], "PASS")
            self.assertEqual(checks["approved S8P geometry contract summary is specified"]["status"], "PASS")
            self.assertEqual(checks["strict final config preflight exists"]["status"], "PASS")
            self.assertEqual(checks["objective acceptance audit exists"]["status"], "PASS")
            self.assertIn("hfss_payload_render_summary", summary["readiness_artifacts"])
            self.assertIn("port_pair_candidate_audit_summary", summary["readiness_artifacts"])
            self.assertIn("post_emx_inverse_training_manifest", summary["readiness_artifacts"])
            self.assertIn("postrun_validation_summary", summary["readiness_artifacts"])
            self.assertTrue((root / "packet" / "next_gen_s8p_mars_execution.commands.sh").stat().st_mode & 0o111)
            command_text = (root / "packet" / "next_gen_s8p_mars_execution.commands.sh").read_text(encoding="utf-8")
            self.assertIn("discover_mars_emx_cadence_paths.py", command_text)
            self.assertIn("prepare_final_s8p_physical_feature_config.py", command_text)
            self.assertIn("--check-paths", command_text)
            self.assertIn("preflight_dataset_config.py", command_text)
            self.assertIn("--forbid-dry-run-paths", command_text)
            self.assertIn("final_s8p_physical_feature_500.preflight_summary.json", command_text)
            self.assertIn("audit_power_line_8port_contract.py", command_text)
            self.assertIn("--expected-differential-port-pairs \"${DIFFERENTIAL_PORT_PAIRS}\"", command_text)
            self.assertIn("build_physical_feature_s8p_launch_packet.py", command_text)
            self.assertIn("PORT_MAP=${PORT_MAP:-P001,P002,P003,P004,P005,P006,P007,P008}", command_text)
            self.assertIn("ROLE_LABELS=${ROLE_LABELS:-primary_top=P001,left_power_top=P002,left_power_bottom=P003,primary_bottom=P004,secondary_bottom=P005,secondary_top=P006,right_power_top=P007,right_power_bottom=P008}", command_text)
            self.assertIn("DIFFERENTIAL_PORT_PAIRS=${DIFFERENTIAL_PORT_PAIRS:-1,4:5,6}", command_text)
            self.assertIn("PORT_MAP_APPROVAL_SUMMARY=${PORT_MAP_APPROVAL_SUMMARY:-", command_text)
            self.assertIn(
                'PORT_MAP_APPROVAL_SUMMARY_FALLBACK="${REPO_ROOT}/outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/s8p_port_map_approval_summary.json"',
                command_text,
            )
            self.assertIn('PORT_MAP_APPROVAL_SUMMARY="${PORT_MAP_APPROVAL_SUMMARY_FALLBACK}"', command_text)
            self.assertIn("--port-map-approval-summary \"${PORT_MAP_APPROVAL_SUMMARY}\"", command_text)
            self.assertIn("GEOMETRY_CONTRACT_APPROVAL_SUMMARY=${GEOMETRY_CONTRACT_APPROVAL_SUMMARY:-", command_text)
            self.assertIn(
                'GEOMETRY_CONTRACT_APPROVAL_SUMMARY_FALLBACK="${REPO_ROOT}/outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_user_approved_20260619/s8p_geometry_contract_approval_summary.json"',
                command_text,
            )
            self.assertIn(
                'GEOMETRY_CONTRACT_APPROVAL_SUMMARY="${GEOMETRY_CONTRACT_APPROVAL_SUMMARY_FALLBACK}"',
                command_text,
            )
            self.assertIn("--geometry-contract-approval-summary \"${GEOMETRY_CONTRACT_APPROVAL_SUMMARY}\"", command_text)
            self.assertIn("COMBINED_APPROVAL_SUMMARY=${COMBINED_APPROVAL_SUMMARY:-", command_text)
            self.assertIn(
                'COMBINED_APPROVAL_SUMMARY_FALLBACK="${REPO_ROOT}/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/s8p_combined_approval_readiness_summary.json"',
                command_text,
            )
            self.assertIn('COMBINED_APPROVAL_SUMMARY="${COMBINED_APPROVAL_SUMMARY_FALLBACK}"', command_text)
            self.assertIn("--combined-approval-summary \"${COMBINED_APPROVAL_SUMMARY}\"", command_text)
            self.assertIn("--combined-approval-readiness-summary \"${COMBINED_APPROVAL_SUMMARY}\"", command_text)
            self.assertIn("audit_next_gen_s8p_goal_readiness.py", command_text)
            self.assertIn("--dataset-quality-summary", command_text)
            self.assertIn("--port-pair-candidate-audit-summary", command_text)
            self.assertIn("--selected-handoff-summary", command_text)
            self.assertIn("--aedt-packet-summary", command_text)
            self.assertIn("--hfss-payload-render-summary", command_text)
            self.assertIn("--inverse-training-manifest", command_text)
            self.assertIn("--postrun-validation-summary", command_text)
            self.assertIn("--jobs 8", command_text)
            self.assertIn("--emx-max-count 500", command_text)
            self.assertIn("--expected-emx-count 500", command_text)
            self.assertIn("--expected-jobs 8", command_text)
            self.assertIn("--scalar-q-definition \"${SCALAR_Q_DEFINITION}\"", command_text)
            self.assertIn("POWER_LINE_8PORT_PLACEMENT_POLICY=coil_opening_fixed_10um_port_ground_overlap", command_text)
            self.assertIn("bash ", command_text)
            required_inputs = json.loads((root / "packet" / "next_gen_s8p_required_inputs.json").read_text(encoding="utf-8"))
            self.assertEqual(required_inputs["power_line_8port_placement_policy"], "coil_opening_fixed_10um_port_ground_overlap")
            self.assertEqual(required_inputs["port_map_approval_summary"], str(approval))
            self.assertEqual(required_inputs["geometry_contract_approval_summary"], str(geometry))
            self.assertEqual(required_inputs["combined_approval_summary"], "")
            report = (root / "packet" / "NEXT_GEN_S8P_MARS_EXECUTION_PACKET_CN.md").read_text(encoding="utf-8")
            self.assertIn("RUN_EMX=1", report)
            self.assertIn("one-sample create-only layout smoke/audit", report)
            self.assertIn("Readiness Artifact Paths", report)
            self.assertIn("Execution Order", report)
            self.assertIn("strict final-config preflight", report)
            self.assertIn("coil_opening_fixed_10um_port_ground_overlap", report)

    def test_bootstrap_geometry_mode_does_not_require_existing_dataset_or_inverse_target(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--out-dir",
                    str(root / "packet"),
                    "--bootstrap-geometry-candidate-queue",
                    "--port-map",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--role-labels",
                    THE_BEST_ROLE_LABELS,
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "next_gen_s8p_mars_execution_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["existing dataset specified or bootstrap geometry mode enabled"]["status"], "PASS")
            self.assertEqual(checks["inverse target JSON specified or bootstrap geometry mode enabled"]["status"], "PASS")
            command_text = (root / "packet" / "next_gen_s8p_mars_execution.commands.sh").read_text(encoding="utf-8")
            self.assertIn("--bootstrap-geometry-candidate-queue", command_text)
            self.assertIn("--bootstrap-sampler lhs_optimized", command_text)
            self.assertNotIn("--dataset-dir \"${EXISTING_DATASET_DIR}\"", command_text)

    def test_expected_sample_count_controls_pilot_queue_size(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--out-dir",
                    str(root / "packet"),
                    "--bootstrap-geometry-candidate-queue",
                    "--port-map",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--role-labels",
                    THE_BEST_ROLE_LABELS,
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                    "--expected-sample-count",
                    "20",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "next_gen_s8p_mars_execution_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(
                summary["run_emx_guard"],
                "RUN_EMX=1 is required; the launch packet then runs a one-sample layout smoke/audit before the 20-sample EMX queue.",
            )
            command_text = (root / "packet" / "next_gen_s8p_mars_execution.commands.sh").read_text(encoding="utf-8")
            self.assertIn("new_s8p_physical_feature_emx_20", command_text)
            self.assertIn("--inverse-candidate-count 20", command_text)
            self.assertIn("--emx-max-count 20", command_text)
            self.assertIn("--expected-emx-count 20", command_text)
            self.assertIn("20-sample EMX queue", command_text)
            self.assertNotIn("--inverse-target-json \"${INVERSE_TARGET_JSON}\"", command_text)
            required_inputs = json.loads((root / "packet" / "next_gen_s8p_required_inputs.json").read_text(encoding="utf-8"))
            self.assertEqual(required_inputs["candidate_source_mode"], "bootstrap_geometry_queue")

    def test_candidate_port_map_approval_summary_is_rejected(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval, approved=False)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry)

            status = mod.main(
                [
                    "--out-dir",
                    str(root / "packet"),
                    "--bootstrap-geometry-candidate-queue",
                    "--port-map",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--role-labels",
                    THE_BEST_ROLE_LABELS,
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "next_gen_s8p_mars_execution_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["approved S8P port map summary is specified"]["status"], "FAIL")

    def test_candidate_geometry_contract_approval_summary_is_rejected(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            approval = root / "approval" / "s8p_port_map_approval_summary.json"
            _write_port_map_approval_summary(approval)
            geometry = root / "approval" / "s8p_geometry_contract_approval_summary.json"
            _write_geometry_contract_approval_summary(geometry, approved=False)

            status = mod.main(
                [
                    "--out-dir",
                    str(root / "packet"),
                    "--bootstrap-geometry-candidate-queue",
                    "--port-map",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--role-labels",
                    THE_BEST_ROLE_LABELS,
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--port-map-approval-summary",
                    str(approval),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "next_gen_s8p_mars_execution_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["approved S8P geometry contract summary is specified"]["status"], "FAIL")

    def test_invalid_port_map_is_rejected(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status = mod.main(
                [
                    "--out-dir",
                    str(root / "packet"),
                    "--existing-dataset-dir",
                    "/dataset",
                    "--inverse-target-json",
                    "/target.json",
                    "--port-map",
                    "P001,P001,P003",
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "next_gen_s8p_mars_execution_packet_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["P001-P008 port map is specified"]["status"], "FAIL")

    def test_missing_role_labels_are_rejected_for_the_best_port_order(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status = mod.main(
                [
                    "--out-dir",
                    str(root / "packet"),
                    "--bootstrap-geometry-candidate-queue",
                    "--port-map",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "next_gen_s8p_mars_execution_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["P001-P008 role labels are specified"]["status"], "FAIL")
