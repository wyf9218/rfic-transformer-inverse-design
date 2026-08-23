from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_s8p_combined_approval_readiness_packet.py"
    spec = importlib.util.spec_from_file_location("build_s8p_combined_approval_readiness_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_port_summary(path: Path, *, approved: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "approval_status": "APPROVED" if approved else "CANDIDATE_REQUIRES_USER_ADVISOR_APPROVAL",
                "decision": "PORT_MAP_APPROVED_FOR_MARS_EMX_RUN" if approved else "AWAITING_USER_ADVISOR_PORT_MAP_APPROVAL",
                "preview_image": "/tmp/preview.png",
                "port_debug_image": "/tmp/port_debug.png",
                "port_pairs": "1,4:5,6",
                "touchstone_port_order": [f"P{index:03d}" for index in range(1, 9)],
                "role_records": [
                    {"order": 1, "port": "P002", "ground": "P002_G", "role": "left_power_top", "winding_role": "secondary", "physical_side": "left"},
                    {"order": 2, "port": "P003", "ground": "P003_G", "role": "left_power_bottom", "winding_role": "secondary", "physical_side": "left"},
                    {"order": 3, "port": "P001", "ground": "P001_G", "role": "primary_top", "winding_role": "primary", "physical_side": "coil"},
                    {"order": 4, "port": "P004", "ground": "P004_G", "role": "primary_bottom", "winding_role": "primary", "physical_side": "coil"},
                    {"order": 5, "port": "P006", "ground": "P006_G", "role": "secondary_top", "winding_role": "secondary", "physical_side": "coil"},
                    {"order": 6, "port": "P005", "ground": "P005_G", "role": "secondary_bottom", "winding_role": "secondary", "physical_side": "coil"},
                    {"order": 7, "port": "P007", "ground": "P007_G", "role": "right_power_top", "winding_role": "primary", "physical_side": "right"},
                    {"order": 8, "port": "P008", "ground": "P008_G", "role": "right_power_bottom", "winding_role": "primary", "physical_side": "right"},
                ],
                "pair_records": [
                    {"pair_index": 1, "pair_role": "primary_response_pair", "syntax": "1,4"},
                    {"pair_index": 2, "pair_role": "secondary_response_pair", "syntax": "5,6"},
                ],
                "artifacts": {"report": "/tmp/port_report.md"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_geometry_summary(path: Path, *, approved: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "approval_status": "APPROVED" if approved else "CANDIDATE_REQUIRES_USER_ADVISOR_APPROVAL",
                "decision": "GEOMETRY_CONTRACT_APPROVED_FOR_MARS_EMX_RUN"
                if approved
                else "AWAITING_USER_ADVISOR_GEOMETRY_CONTRACT_APPROVAL",
                "approved_geometry_contract": {
                    "bridge_width_um": 10.0,
                    "bridge_width_contract_basis": "latest clarified geometry contract: bridge width must equal the vertical power-line width",
                    "superseded_literal_10nm_bridge_width_um": 0.01,
                    "vertical_length_reference_dimension": "max(primary_outer_height_um, secondary_outer_height_um)",
                    "vertical_length_diameter_ratio": 1.5,
                    "ground_frame_width_um": 100.0,
                    "ground_frame_policy": "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
                },
                "artifacts": {"report": "/tmp/geometry_report.md"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_execution_summary(path: Path, *, ready: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS" if ready else "FAIL",
                "decision": "MARS_S8P_EXECUTION_RUNBOOK_READY" if ready else "FILL_REQUIRED_CONFIRMATIONS_BEFORE_MARS_RUN",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


class BuildS8pCombinedApprovalReadinessPacketScriptTest(TransformerToolboxTestBase):
    def test_candidate_summaries_are_review_ready_but_not_emx_ready(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            port = root / "port.json"
            geometry = root / "geometry.json"
            execution = root / "execution.json"
            out_dir = root / "out"
            _write_port_summary(port)
            _write_geometry_summary(geometry)
            _write_execution_summary(execution)

            status = mod.main(
                [
                    "--port-map-approval-summary",
                    str(port),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--execution-packet-summary",
                    str(execution),
                    "--out-dir",
                    str(out_dir),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "s8p_combined_approval_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "AWAITING_USER_ADVISOR_APPROVALS")
            self.assertFalse(summary["can_start_real_emx"])
            self.assertFalse(summary["approval_state"]["port_map_approved"])
            self.assertFalse(summary["approval_state"]["geometry_contract_approved"])
            self.assertIn("CONFIRM_S8P_GEOMETRY_CONTRACT_APPROVED=YES", summary["required_approval_command"])
            self.assertTrue((out_dir / "s8p_combined_approval_readiness_board.png").is_file())
            self.assertEqual(summary["visual_artifacts"]["approval_board"], str((out_dir / "s8p_combined_approval_readiness_board.png").resolve()))
            report = (out_dir / "S8P_COMBINED_APPROVAL_READINESS_REPORT_CN.md").read_text(encoding="utf-8")
            self.assertIn("Approval board PNG", report)
            self.assertIn("Bridge width", report)
            self.assertIn("P001-P004", report)

    def test_approved_summaries_and_ready_execution_allow_real_emx_gate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            port = root / "port.json"
            geometry = root / "geometry.json"
            execution = root / "execution.json"
            out_dir = root / "out"
            _write_port_summary(port, approved=True)
            _write_geometry_summary(geometry, approved=True)
            _write_execution_summary(execution, ready=True)

            status = mod.main(
                [
                    "--port-map-approval-summary",
                    str(port),
                    "--geometry-contract-approval-summary",
                    str(geometry),
                    "--execution-packet-summary",
                    str(execution),
                    "--out-dir",
                    str(out_dir),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "s8p_combined_approval_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["decision"], "APPROVED_AND_MARS_EXECUTION_PACKET_READY")
            self.assertTrue(summary["can_start_real_emx"])
            self.assertTrue(summary["approval_state"]["mars_execution_packet_ready"])
