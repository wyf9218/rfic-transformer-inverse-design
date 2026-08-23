from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_selected_s8p_hfss_handoff_packet.py"
    spec = importlib.util.spec_from_file_location("build_selected_s8p_hfss_handoff_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _layout8() -> dict:
    return {
        "cadence_pin_purpose": 51,
        "ports": [
            {
                "name": f"P{index:03d}",
                "signal_labels": [f"P{index:03d}"],
                "ground_labels": [f"P{index:03d}_G"],
                "internal_signal_labels": True,
                "internal_ground_labels": True,
            }
            for index in range(1, 9)
        ],
    }


def _power_line_geometry(delta_y_um: float = 0.0, *, primary_on_right: bool = False) -> dict:
    primary_x = 210.0 if primary_on_right else -210.0
    secondary_x = -210.0 if primary_on_right else 210.0
    max_outer_height_um = 260.0
    vertical_length_diameter_ratio = 1.5
    vertical_length_um = max_outer_height_um * vertical_length_diameter_ratio
    primary_top = "P007" if primary_on_right else "P002"
    primary_bottom = "P008" if primary_on_right else "P003"
    secondary_top = "P002" if primary_on_right else "P007"
    secondary_bottom = "P003" if primary_on_right else "P008"
    primary_bridge = _bridge(150.0, 205.0, delta_y_um) if primary_on_right else _bridge(-150.0, -205.0, delta_y_um)
    secondary_bridge = _bridge(-150.0, -205.0, 0.0) if primary_on_right else _bridge(150.0, 205.0, 0.0)
    shield_inner_bbox = {"min_x_um": -260.0, "min_y_um": -195.0, "max_x_um": 260.0, "max_y_um": 195.0}
    ground_frame_width_um = 100.0
    shield_outer_bbox = {
        "min_x_um": shield_inner_bbox["min_x_um"] - ground_frame_width_um,
        "min_y_um": shield_inner_bbox["min_y_um"] - ground_frame_width_um,
        "max_x_um": shield_inner_bbox["max_x_um"] + ground_frame_width_um,
        "max_y_um": shield_inner_bbox["max_y_um"] + ground_frame_width_um,
    }
    return {
        "schema": "rfic_transformer_power_line_8port_geometry.v1",
        "enabled": True,
        "labels": {
            "primary_top": "P001",
            "left_power_top": "P002",
            "left_power_bottom": "P003",
            "primary_bottom": "P004",
            "secondary_bottom": "P005",
            "secondary_top": "P006",
            "right_power_top": "P007",
            "right_power_bottom": "P008",
        },
        "vertical_length_um": vertical_length_um,
        "max_outer_height_um": max_outer_height_um,
        "vertical_length_diameter_ratio": vertical_length_diameter_ratio,
        "expected_vertical_length_um": vertical_length_um,
        "line_width_um": 10.0,
        "bridge_width_um": 10.0,
        "ground_frame_width_um": ground_frame_width_um,
        "ground_frame_policy": "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
        "shield_inner_bbox_um": shield_inner_bbox,
        "shield_outer_bbox_um": shield_outer_bbox,
        "primary_power_line": {
            "center_x_um": primary_x,
            "center_y_um": 0.0,
            "width_um": 10.0,
            "height_um": vertical_length_um,
            "top_port_label": primary_top,
            "bottom_port_label": primary_bottom,
            "top_ground_label": f"{primary_top}_G",
            "bottom_ground_label": f"{primary_bottom}_G",
        },
        "secondary_power_line": {
            "center_x_um": secondary_x,
            "center_y_um": 0.0,
            "width_um": 10.0,
            "height_um": vertical_length_um,
            "top_port_label": secondary_top,
            "bottom_port_label": secondary_bottom,
            "top_ground_label": f"{secondary_top}_G",
            "bottom_ground_label": f"{secondary_bottom}_G",
        },
        "primary_bridge": primary_bridge,
        "secondary_bridge": secondary_bridge,
    }


def _bridge(coil_x: float, edge_x: float, delta_y_um: float) -> dict:
    return {
        "coil_anchor": {"x_um": coil_x, "y_um": 0.0},
        "power_line_edge": {"x_um": edge_x, "y_um": delta_y_um},
        "width_um": 10.0,
        "expected_width_um": 10.0,
        "line_width_um": 10.0,
        "length_um": abs(edge_x - coil_x),
        "delta_y_um": delta_y_um,
        "center_y_um": delta_y_um / 2.0,
        "power_line_center_y_um": 0.0,
        "power_line_left_edge_x_um": edge_x - 10.0,
        "power_line_right_edge_x_um": edge_x,
        "nearest_power_line_edge_x_um": edge_x,
        "power_line_edge_alignment_error_um": 0.0,
        "extends_away_from_coil_interior": True,
        "is_horizontal": delta_y_um == 0.0,
    }


def _write_sample(
    root: Path,
    evaluation: str,
    *,
    touchstone_suffix: str = ".s8p",
    bridge_delta_y: float = 0.0,
    primary_on_right: bool = False,
) -> Path:
    eval_dir = root / "evaluations" / evaluation
    layout_dir = eval_dir / "layout"
    emx_dir = eval_dir / "emx"
    layout_dir.mkdir(parents=True, exist_ok=True)
    emx_dir.mkdir(parents=True, exist_ok=True)
    (layout_dir / "transformer_layout.layout.json").write_text(json.dumps(_layout8()), encoding="utf-8")
    (layout_dir / "power_line_8port_geometry.json").write_text(
        json.dumps(_power_line_geometry(bridge_delta_y, primary_on_right=primary_on_right)),
        encoding="utf-8",
    )
    touchstone = emx_dir / f"{evaluation}{touchstone_suffix}"
    touchstone.write_text("# Hz S RI R 50\n", encoding="utf-8")
    (layout_dir / "transformer_layout.gds").write_bytes(b"GDS")
    return eval_dir


def _write_passed_layout_audit(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "SELECTED_SAMPLE_LAYOUTS_READY_FOR_HFSS_ADS_VALIDATION",
            }
        ),
        encoding="utf-8",
    )


class BuildSelectedS8PHFSSHandoffPacketScriptTest(TransformerToolboxTestBase):
    def test_builds_handoff_packet_from_selected_s8p_sample(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = _write_sample(root, "eval_a", primary_on_right=True)
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "row_index": 7,
                        "evaluation": "eval_a",
                        "work_dir": str(eval_dir),
                        "touchstone_path": str(eval_dir / "emx" / "eval_a.s8p"),
                        "lp_nh_center": 0.8,
                        "ls_nh_center": 1.1,
                        "qp_center": 10.0,
                        "qs_center": 9.0,
                        "k_center": 0.45,
                    }
                ],
            )
            layout_audit = root / "layout_audit" / "selected_power_line_8port_layout_audit_summary.json"
            _write_passed_layout_audit(layout_audit)

            status = mod.main(
                [
                    "--samples-csv",
                    str(samples),
                    "--out-dir",
                    str(root / "handoff"),
                    "--layout-audit-summary",
                    str(layout_audit),
                    "--port-pairs",
                    "1,4:5,6",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "handoff" / "selected_s8p_hfss_handoff_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "READY_FOR_HFSS_REBUILD_HANDOFF")
            self.assertEqual(summary["sample_results"][0]["port_labels"]["left_power_top"], "P002")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "8 port signal labels present")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "8 port ground labels present")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "8 role labels match approved P001-P008 order")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "physical left top power port")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "power_line_8port ground frame policy")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "power_line_8port ground frame width matches contract")]["status"], "PASS")
            self.assertEqual(
                checks[("eval_a", "power_line_8port shield outer bbox expands inner window by ground frame width")]["status"],
                "PASS",
            )
            self.assertEqual(checks[("eval_a", "physical left top ground")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "physical right bottom power port")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "physical right bottom ground")]["status"], "PASS")
            self.assertTrue((root / "handoff" / "samples" / "01_eval_a" / "emx_reference.s8p").is_file())
            self.assertIn("primary_bridge", (root / "handoff" / "hfss_bridge_geometry.csv").read_text(encoding="utf-8"))
            self.assertIn("P001", (root / "handoff" / "hfss_port_map.csv").read_text(encoding="utf-8"))
            formula_trace = (root / "handoff" / "hfss_ads_formula_trace.md").read_text(encoding="utf-8")
            self.assertIn("1,4:5,6", formula_trace)
            self.assertIn("P001", formula_trace)
            self.assertIn("P006", formula_trace)
            self.assertIn("Z_diff = transpose(T) * Z_single * T", formula_trace)
            self.assertIn("Lp = imag(Zdiff[1,1]) / omega", formula_trace)
            self.assertIn("Q  = min(Qp, Qs)", formula_trace)
            self.assertIn("K  = M / sqrt(abs(Lp * Ls))", formula_trace)
            self.assertIn("Kw = K", formula_trace)

    def test_rejects_missing_layout_audit_summary_for_final_handoff(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = _write_sample(root, "eval_no_layout_audit", primary_on_right=True)
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_no_layout_audit",
                        "work_dir": str(eval_dir),
                        "touchstone_path": str(eval_dir / "emx" / "eval_no_layout_audit.s8p"),
                    }
                ],
            )

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "handoff"), "--port-pairs", "1,4:5,6"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "handoff" / "selected_s8p_hfss_handoff_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_BUILD_HFSS_MODEL_FROM_THIS_HANDOFF")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("", "selected layout audit summary supplied")]["status"], "FAIL")

    def test_rejects_selected_sample_without_s8p_touchstone(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = _write_sample(root, "eval_bad_suffix", touchstone_suffix=".s4p")
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_bad_suffix",
                        "work_dir": str(eval_dir),
                        "touchstone_path": str(eval_dir / "emx" / "eval_bad_suffix.s4p"),
                    }
                ],
            )

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "handoff"), "--port-pairs", "1,4:5,6"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "handoff" / "selected_s8p_hfss_handoff_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_bad_suffix", "EMX Touchstone suffix is .s8p")]["status"], "FAIL")

    def test_rejects_layout_port_without_ground_label(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = _write_sample(root, "eval_missing_ground")
            layout_path = eval_dir / "layout" / "transformer_layout.layout.json"
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            for port in layout["ports"]:
                if port["name"] == "P006":
                    port["ground_labels"] = []
            layout_path.write_text(json.dumps(layout), encoding="utf-8")
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_missing_ground",
                        "work_dir": str(eval_dir),
                        "touchstone_path": str(eval_dir / "emx" / "eval_missing_ground.s8p"),
                    }
                ],
            )

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "handoff"), "--port-pairs", "1,4:5,6"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "handoff" / "selected_s8p_hfss_handoff_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_missing_ground", "8 port ground labels present")]["status"], "FAIL")
            self.assertIn("P006_G", checks[("eval_missing_ground", "8 port ground labels present")]["detail"])

    def test_rejects_selected_sample_with_wrong_role_label_mapping(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = _write_sample(root, "eval_wrong_roles")
            power_path = eval_dir / "layout" / "power_line_8port_geometry.json"
            power = json.loads(power_path.read_text(encoding="utf-8"))
            power["labels"]["primary_top"] = "P002"
            power_path.write_text(json.dumps(power), encoding="utf-8")
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_wrong_roles",
                        "work_dir": str(eval_dir),
                        "touchstone_path": str(eval_dir / "emx" / "eval_wrong_roles.s8p"),
                    }
                ],
            )

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "handoff"), "--port-pairs", "1,4:5,6"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "handoff" / "selected_s8p_hfss_handoff_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_wrong_roles", "8 role labels present")]["status"], "PASS")
            self.assertEqual(checks[("eval_wrong_roles", "8 role labels match approved P001-P008 order")]["status"], "FAIL")
            self.assertIn("primary_top", checks[("eval_wrong_roles", "8 role labels match approved P001-P008 order")]["detail"])

    def test_rejects_off_center_non_horizontal_bridge(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = _write_sample(root, "eval_bad_bridge", bridge_delta_y=0.25)
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_bad_bridge",
                        "work_dir": str(eval_dir),
                        "touchstone_path": str(eval_dir / "emx" / "eval_bad_bridge.s8p"),
                    }
                ],
            )

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "handoff"), "--port-pairs", "1,4:5,6"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "handoff" / "selected_s8p_hfss_handoff_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_bad_bridge", "primary_bridge is horizontal")]["status"], "FAIL")
            self.assertEqual(checks[("eval_bad_bridge", "primary_bridge centered at y=0")]["status"], "FAIL")

    def test_rejects_bridge_that_intrudes_into_coil_interior(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = _write_sample(root, "eval_bad_bridge_direction")
            power_path = eval_dir / "layout" / "power_line_8port_geometry.json"
            power = json.loads(power_path.read_text(encoding="utf-8"))
            power["primary_bridge"]["extends_away_from_coil_interior"] = False
            power_path.write_text(json.dumps(power), encoding="utf-8")
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_bad_bridge_direction",
                        "work_dir": str(eval_dir),
                        "touchstone_path": str(eval_dir / "emx" / "eval_bad_bridge_direction.s8p"),
                    }
                ],
            )

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "handoff"), "--port-pairs", "1,4:5,6"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "handoff" / "selected_s8p_hfss_handoff_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_bad_bridge_direction", "primary_bridge stays outside coil interior")]["status"], "FAIL")
            self.assertIn(
                "extends_away_from_coil_interior=False",
                checks[("eval_bad_bridge_direction", "primary_bridge stays outside coil interior")]["detail"],
            )

    def test_rejects_power_line_vertical_length_not_one_point_five_max_height(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = _write_sample(root, "eval_bad_vertical_length")
            power_path = eval_dir / "layout" / "power_line_8port_geometry.json"
            power = json.loads(power_path.read_text(encoding="utf-8"))
            power["vertical_length_diameter_ratio"] = 1.25
            power["vertical_length_um"] = 325.0
            power["expected_vertical_length_um"] = 325.0
            power["primary_power_line"]["height_um"] = 325.0
            power["secondary_power_line"]["height_um"] = 325.0
            power_path.write_text(json.dumps(power), encoding="utf-8")
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_bad_vertical_length",
                        "work_dir": str(eval_dir),
                        "touchstone_path": str(eval_dir / "emx" / "eval_bad_vertical_length.s8p"),
                    }
                ],
            )

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "handoff"), "--port-pairs", "1,4:5,6"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "handoff" / "selected_s8p_hfss_handoff_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_bad_vertical_length", "power_line_8port vertical length ratio")]["status"], "FAIL")
            self.assertEqual(
                checks[("eval_bad_vertical_length", "power_line_8port vertical length equals 1.5*max coil height")]["status"],
                "FAIL",
            )

    def test_rejects_bad_ground_frame_bbox_for_selected_sample(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = _write_sample(root, "eval_bad_ground_frame", primary_on_right=True)
            power_path = eval_dir / "layout" / "power_line_8port_geometry.json"
            power = json.loads(power_path.read_text(encoding="utf-8"))
            power["shield_outer_bbox_um"]["max_x_um"] += 4.0
            power_path.write_text(json.dumps(power), encoding="utf-8")
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_bad_ground_frame",
                        "work_dir": str(eval_dir),
                        "touchstone_path": str(eval_dir / "emx" / "eval_bad_ground_frame.s8p"),
                    }
                ],
            )

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "handoff"), "--port-pairs", "1,4:5,6"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "handoff" / "selected_s8p_hfss_handoff_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(
                checks[("eval_bad_ground_frame", "power_line_8port shield outer bbox expands inner window by ground frame width")]["status"],
                "FAIL",
            )
