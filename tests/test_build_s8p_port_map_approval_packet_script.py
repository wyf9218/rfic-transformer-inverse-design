from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_s8p_port_map_approval_packet.py"
    spec = importlib.util.spec_from_file_location("build_s8p_port_map_approval_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_power_line(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": True,
        "labels": {
            "left_power_top": "P002",
            "left_power_bottom": "P003",
            "primary_top": "P001",
            "primary_bottom": "P004",
            "secondary_top": "P006",
            "secondary_bottom": "P005",
            "right_power_top": "P007",
            "right_power_bottom": "P008",
        },
        "physical_left_power_line": {
            "top_port_label": "P002",
            "bottom_port_label": "P003",
            "top_ground_label": "P002_G",
            "bottom_ground_label": "P003_G",
        },
        "physical_right_power_line": {
            "top_port_label": "P007",
            "bottom_port_label": "P008",
            "top_ground_label": "P007_G",
            "bottom_ground_label": "P008_G",
        },
        "secondary_power_line": {
            "top_port_label": "P002",
            "bottom_port_label": "P003",
            "top_ground_label": "P002_G",
            "bottom_ground_label": "P003_G",
        },
        "primary_power_line": {
            "top_port_label": "P007",
            "bottom_port_label": "P008",
            "top_ground_label": "P007_G",
            "bottom_ground_label": "P008_G",
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_layout(path: Path) -> None:
    path.write_text(json.dumps({"ports": [{"name": f"P{index:03d}"} for index in range(1, 9)]}), encoding="utf-8")


class BuildS8PPortMapApprovalPacketScriptTest(TransformerToolboxTestBase):
    def test_the_best_order_candidate_packet_is_structurally_pass_but_not_approved(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            power = root / "power_line_8port_geometry.json"
            layout = root / "transformer_layout.layout.json"
            _write_power_line(power)
            _write_layout(layout)

            status = mod.main(
                [
                    "--power-line-geometry",
                    str(power),
                    "--layout-json",
                    str(layout),
                    "--out-dir",
                    str(root / "approval"),
                    "--port-pairs",
                    "1,4:5,6",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "approval" / "s8p_port_map_approval_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["approval_status"], "CANDIDATE_REQUIRES_USER_ADVISOR_APPROVAL")
            self.assertEqual(summary["decision"], "AWAITING_USER_ADVISOR_PORT_MAP_APPROVAL")
            pairs = summary["pair_records"]
            self.assertEqual(pairs[0]["plus_port"], "P001")
            self.assertEqual(pairs[0]["pair_winding_role"], "primary")
            self.assertTrue(pairs[0]["winding_role_matches_expected"])
            self.assertEqual(pairs[1]["minus_port"], "P006")
            self.assertEqual(pairs[1]["pair_winding_role"], "secondary")
            self.assertTrue(pairs[1]["winding_role_matches_expected"])
            formula = (root / "approval" / "s8p_ads_python_formula_trace.md").read_text(encoding="utf-8")
            self.assertIn("Lp = imag(Zp) / omega", formula)
            self.assertIn("K  = M / sqrt(abs(Lp * Ls))", formula)

    def test_reversed_pair_order_fails_primary_secondary_winding_check(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            power = root / "power_line_8port_geometry.json"
            _write_power_line(power)

            status = mod.main(
                [
                    "--power-line-geometry",
                    str(power),
                    "--out-dir",
                    str(root / "approval"),
                    "--port-pairs",
                    "7,8:1,4",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "approval" / "s8p_port_map_approval_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["differential_pair_winding_roles_match_primary_secondary_order"]["status"], "FAIL")
            self.assertEqual(summary["pair_records"][0]["pair_winding_role"], "primary")
            self.assertEqual(summary["pair_records"][1]["pair_winding_role"], "primary")

    def test_approved_packet_records_launch_ready_decision(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            power = root / "power_line_8port_geometry.json"
            _write_power_line(power)

            status = mod.main(
                [
                    "--power-line-geometry",
                    str(power),
                    "--out-dir",
                    str(root / "approval"),
                    "--approved",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "approval" / "s8p_port_map_approval_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["approval_status"], "APPROVED")
            self.assertEqual(summary["decision"], "PORT_MAP_APPROVED_FOR_MARS_EMX_RUN")
