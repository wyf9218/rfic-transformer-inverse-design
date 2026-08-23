from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_power_line_8port_contract.py"
    spec = importlib.util.spec_from_file_location("audit_power_line_8port_contract_script", script_path)
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


class AuditPowerLine8PortContractScriptTest(TransformerToolboxTestBase):
    def test_valid_s8p_config_passes_contract_audit(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)

            status = mod.main(["--config", str(config), "--out-dir", str(root / "audit")])

            summary = json.loads((root / "audit" / "power_line_8port_contract_audit_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(summary["overall_status"], "PASS")
        self.assertEqual(summary["decision"], "READY_FOR_8PORT_MARS_EMX_RUN")
        self.assertEqual(
            summary["expected"]["bridge_width_contract_basis"],
            "latest clarified geometry contract: bridge width must equal the vertical power-line width",
        )
        self.assertIsNone(summary["expected"]["bridge_width_um"])
        self.assertEqual(summary["expected"]["superseded_literal_10nm_bridge_width_um"], 0.01)
        self.assertEqual(
            summary["expected"]["vertical_length_reference_dimension"],
            "max(primary_outer_height_um, secondary_outer_height_um)",
        )
        self.assertEqual(summary["power_line_8port"]["bridge_width_um"], 10.0)
        self.assertEqual(summary["power_line_8port"]["ground_frame_width_um"], 100.0)
        self.assertEqual(
            summary["power_line_8port"]["ground_frame_policy"],
            "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
        )
        checks = {item["name"]: item for item in summary["checks"]}
        self.assertEqual(checks["ground frame width"]["status"], "PASS")
        self.assertEqual(
            checks["differential port pairs match expected physical-feature extraction path"]["status"],
            "PASS",
        )
        self.assertEqual(checks["literal 10nm bridge interpretation is rejected"]["status"], "PASS")
        self.assertEqual(checks["vertical length reference dimension"]["status"], "PASS")
        self.assertEqual(summary["differential_pair_label_map"][0]["labels"], ["P001", "P004"])
        self.assertEqual(summary["differential_pair_label_map"][0]["role"], "primary_response_pair")
        self.assertEqual(summary["differential_pair_label_map"][1]["labels"], ["P005", "P006"])
        self.assertEqual(summary["differential_pair_label_map"][1]["role"], "secondary_response_pair")

    def test_template_config_fails_until_todos_are_replaced(self) -> None:
        mod = _load_module()
        config = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            status = mod.main(["--config", str(config), "--out-dir", str(root / "audit"), "--no-fail-exit"])

            summary = json.loads((root / "audit" / "power_line_8port_contract_audit_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(summary["overall_status"], "FAIL")
        failed = {item["name"]: item["detail"] for item in summary["checks"] if item["status"] == "FAIL"}
        self.assertIn("no unresolved config placeholders", failed)
        self.assertIn("config loads", failed)

    def test_placeholder_port_labels_fail_even_when_yaml_loads(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p_placeholder_ports.yaml"
            _write_valid_s8p_config(config)
            text = config.read_text(encoding="utf-8").replace(
                "[P001, P002, P003, P004, P005, P006, P007, P008]",
                "[TODO_P001, TODO_P002, P003, P004, P005, P006, P007, P008]",
            )
            text = text.replace("primary_top: P001", "primary_top: TODO_P001")
            text = text.replace("left_power_top: P002", "left_power_top: TODO_P002")
            config.write_text(text, encoding="utf-8")

            status = mod.main(["--config", str(config), "--out-dir", str(root / "audit"), "--no-fail-exit"])

            summary = json.loads((root / "audit" / "power_line_8port_contract_audit_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(summary["overall_status"], "FAIL")
        failed_names = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
        self.assertIn("no unresolved config placeholders", failed_names)
        self.assertIn("port map labels finalized", failed_names)

    def test_wrong_bridge_width_fails_same_width_contract(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p_wrong_bridge.yaml"
            _write_valid_s8p_config(config)
            config.write_text(config.read_text(encoding="utf-8").replace("bridge_width_um: 10.0", "bridge_width_um: 9.0"), encoding="utf-8")

            status = mod.main(["--config", str(config), "--out-dir", str(root / "audit"), "--no-fail-exit"])

            summary = json.loads((root / "audit" / "power_line_8port_contract_audit_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(summary["overall_status"], "FAIL")
        failed = {item["name"]: item["detail"] for item in summary["checks"] if item["status"] == "FAIL"}
        self.assertIn("bridge width matches primary vertical power-line width", failed)
        self.assertIn("bridge_width_um=9.0", failed["bridge width matches primary vertical power-line width"])
        self.assertIn("primary_power_line_width_um=10.0", failed["bridge width matches primary vertical power-line width"])

    def test_literal_10nm_bridge_width_fails_superseded_contract(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p_literal_10nm_bridge.yaml"
            _write_valid_s8p_config(config)
            config.write_text(config.read_text(encoding="utf-8").replace("bridge_width_um: 10.0", "bridge_width_um: 0.01"), encoding="utf-8")

            status = mod.main(["--config", str(config), "--out-dir", str(root / "audit"), "--no-fail-exit"])

            summary = json.loads((root / "audit" / "power_line_8port_contract_audit_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(summary["overall_status"], "FAIL")
        failed = {item["name"]: item["detail"] for item in summary["checks"] if item["status"] == "FAIL"}
        self.assertIn("literal 10nm bridge interpretation is rejected", failed)
        self.assertIn("superseded 10nm/0.01um", failed["literal 10nm bridge interpretation is rejected"])

    def test_unexpected_differential_pairs_fail_extraction_path_contract(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p_wrong_pairs.yaml"
            _write_valid_s8p_config(config)
            config.write_text(config.read_text(encoding="utf-8").replace("differential_port_pairs: '1,4:5,6'", "differential_port_pairs: '3,4:5,6'"), encoding="utf-8")

            status = mod.main(["--config", str(config), "--out-dir", str(root / "audit"), "--no-fail-exit"])

            summary = json.loads((root / "audit" / "power_line_8port_contract_audit_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(summary["overall_status"], "FAIL")
        failed = {item["name"]: item["detail"] for item in summary["checks"] if item["status"] == "FAIL"}
        self.assertIn("differential port pairs match expected physical-feature extraction path", failed)
        self.assertIn("expected [[1, 4], [5, 6]]", failed["differential port pairs match expected physical-feature extraction path"])
