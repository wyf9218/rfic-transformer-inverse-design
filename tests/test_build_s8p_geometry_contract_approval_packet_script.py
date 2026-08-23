from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_s8p_geometry_contract_approval_packet.py"
    spec = importlib.util.spec_from_file_location("build_s8p_geometry_contract_approval_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_contract_audit(path: Path, *, bridge_width_um: float = 10.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "READY_FOR_8PORT_MARS_EMX_RUN",
                "expected": {
                    "bridge_width_contract_basis": "latest clarified geometry contract: bridge width must equal the vertical power-line width",
                    "superseded_literal_10nm_bridge_width_um": 0.01,
                    "vertical_length_reference_dimension": "max(primary_outer_height_um, secondary_outer_height_um)",
                },
                "power_line_8port": {
                    "bridge_width_um": bridge_width_um,
                    "vertical_length_diameter_ratio": 1.5,
                    "ground_frame_width_um": 100.0,
                    "ground_frame_policy": "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
                    "port_map": ["P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"],
                },
                "differential_pair_label_map": [
                    {"pair": 1, "role": "primary_response_pair", "ports": [1, 4], "labels": ["P001", "P004"]},
                    {"pair": 2, "role": "secondary_response_pair", "ports": [5, 6], "labels": ["P005", "P006"]},
                ],
                "checks": [
                    {"status": "PASS", "name": "literal 10nm bridge interpretation is rejected", "detail": "ok"},
                ],
            }
        ),
        encoding="utf-8",
    )


class BuildS8pGeometryContractApprovalPacketScriptTest(TransformerToolboxTestBase):
    def test_candidate_summary_records_unapproved_geometry_contract(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            audit = root / "audit" / "power_line_8port_contract_audit_summary.json"
            _write_contract_audit(audit)

            status = mod.main(["--contract-audit-summary", str(audit), "--out-dir", str(root / "approval")])

            summary = json.loads((root / "approval" / "s8p_geometry_contract_approval_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(summary["overall_status"], "PASS")
        self.assertEqual(summary["approval_status"], "CANDIDATE_REQUIRES_USER_ADVISOR_APPROVAL")
        self.assertEqual(summary["decision"], "AWAITING_USER_ADVISOR_GEOMETRY_CONTRACT_APPROVAL")
        checks = {item["name"]: item for item in summary["checks"]}
        self.assertEqual(checks["literal 10nm bridge interpretation is rejected"]["status"], "PASS")

    def test_approved_summary_allows_geometry_contract_gate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            audit = root / "audit" / "power_line_8port_contract_audit_summary.json"
            _write_contract_audit(audit)

            status = mod.main(["--contract-audit-summary", str(audit), "--out-dir", str(root / "approval"), "--approved"])

            summary = json.loads((root / "approval" / "s8p_geometry_contract_approval_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(summary["overall_status"], "PASS")
        self.assertEqual(summary["approval_status"], "APPROVED")
        self.assertEqual(summary["decision"], "GEOMETRY_CONTRACT_APPROVED_FOR_MARS_EMX_RUN")
        self.assertEqual(summary["approved_geometry_contract"]["bridge_width_um"], 10.0)

    def test_literal_10nm_geometry_contract_is_rejected(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            audit = root / "audit" / "power_line_8port_contract_audit_summary.json"
            _write_contract_audit(audit, bridge_width_um=0.01)

            status = mod.main(
                [
                    "--contract-audit-summary",
                    str(audit),
                    "--out-dir",
                    str(root / "approval"),
                    "--approved",
                    "--no-fail-exit",
                ]
            )

            summary = json.loads((root / "approval" / "s8p_geometry_contract_approval_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(summary["overall_status"], "FAIL")
        failed = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
        self.assertIn("bridge width is same-width 10um contract", failed)
