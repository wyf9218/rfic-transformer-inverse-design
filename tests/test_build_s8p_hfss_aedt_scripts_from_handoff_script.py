from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import py_compile
import sys

import gdstk


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_s8p_hfss_aedt_scripts_from_handoff.py"
    spec = importlib.util.spec_from_file_location("build_s8p_hfss_aedt_scripts_from_handoff_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_gds(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lib = gdstk.Library()
    cell = lib.new_cell("S8P_SAMPLE")
    cell.add(gdstk.rectangle((-60, -20), (0, 20), layer=74, datatype=0))
    cell.add(gdstk.rectangle((5, -18), (65, 18), layer=39, datatype=60))
    cell.add(gdstk.rectangle((-90, -70), (90, -60), layer=35, datatype=0))
    cell.add(gdstk.rectangle((-90, 60), (90, 70), layer=35, datatype=0))
    cell.add(gdstk.rectangle((-90, -60), (-80, 60), layer=35, datatype=0))
    cell.add(gdstk.rectangle((80, -60), (90, 60), layer=35, datatype=0))
    signal_layers = {
        "P001": 126,
        "P002": 126,
        "P003": 126,
        "P004": 126,
        "P005": 139,
        "P006": 139,
        "P007": 139,
        "P008": 139,
    }
    signal_points = {
        "P001": (-80, 55),
        "P002": (-80, -55),
        "P003": (-50, 10),
        "P004": (-50, -10),
        "P005": (50, 10),
        "P006": (50, -10),
        "P007": (80, 55),
        "P008": (80, -55),
    }
    for name, (x, y) in signal_points.items():
        cell.add(gdstk.Label(name, (x, y), layer=signal_layers[name], texttype=0))
        ground_y = 65 if y >= 0 else -65
        cell.add(gdstk.Label(f"{name}_G", (x, ground_y), layer=135, texttype=0))
    lib.write_gds(str(path))


def _power_line_geometry(
    *,
    bridge_width_um: float = 10.0,
    vertical_length_diameter_ratio: float = 1.5,
    primary_on_right: bool = True,
    ground_frame_width_um: float = 100.0,
) -> dict:
    max_outer_height_um = 180.0
    vertical_length_um = max_outer_height_um * vertical_length_diameter_ratio
    primary_x = 80.0 if primary_on_right else -80.0
    secondary_x = -80.0 if primary_on_right else 80.0
    primary_top = "P007" if primary_on_right else "P002"
    primary_bottom = "P008" if primary_on_right else "P003"
    secondary_top = "P002" if primary_on_right else "P007"
    secondary_bottom = "P003" if primary_on_right else "P008"
    primary_bridge = _bridge(50.0, 75.0, bridge_width_um) if primary_on_right else _bridge(-50.0, -75.0, bridge_width_um)
    secondary_bridge = _bridge(-50.0, -75.0, bridge_width_um) if primary_on_right else _bridge(50.0, 75.0, bridge_width_um)
    shield_inner_bbox = {"min_x_um": -130.0, "min_y_um": -110.0, "max_x_um": 130.0, "max_y_um": 110.0}
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
        "bridge_width_um": bridge_width_um,
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


def _process_layer_summary() -> dict:
    return {
        "schema": "rfic_transformer_process_layer_summary.v1",
        "process_file": "/remote/foundry/actual_emx.proc",
        "records": {
            "shield_m5_draw": {
                "semantic_role": "ground shield / M5 draw",
                "logical_name": "metal5",
                "conductor_name": "metal5",
                "conductor_thickness_um": 0.22,
                "conductor_z_bottom_um": 703.196,
                "conductor_z_top_um": 703.416,
            },
            "secondary_m9_draw": {
                "semantic_role": "secondary winding / M9 draw",
                "logical_name": "metal9",
                "conductor_name": "metal9",
                "conductor_thickness_um": 3.4,
                "conductor_z_bottom_um": 707.236,
                "conductor_z_top_um": 710.636,
            },
            "primary_m10_draw": {
                "semantic_role": "primary winding / M10 draw",
                "logical_name": "metal10",
                "conductor_name": "metal10",
                "conductor_thickness_um": 2.8,
                "conductor_z_bottom_um": 714.161,
                "conductor_z_top_um": 716.961,
            },
        },
    }


def _bridge(coil_x: float, edge_x: float, bridge_width_um: float) -> dict:
    left_edge = edge_x if edge_x >= 0.0 else edge_x - 10.0
    right_edge = edge_x + 10.0 if edge_x >= 0.0 else edge_x
    return {
        "coil_anchor": {"x_um": coil_x, "y_um": 0.0},
        "power_line_edge": {"x_um": edge_x, "y_um": 0.0},
        "width_um": bridge_width_um,
        "length_um": abs(edge_x - coil_x),
        "delta_y_um": 0.0,
        "center_y_um": 0.0,
        "power_line_center_y_um": 0.0,
        "power_line_left_edge_x_um": left_edge,
        "power_line_right_edge_x_um": right_edge,
        "nearest_power_line_edge_x_um": edge_x,
        "power_line_edge_alignment_error_um": 0.0,
        "extends_away_from_coil_interior": True,
        "is_horizontal": True,
    }


def _write_handoff(
    root: Path,
    *,
    include_gds: bool = True,
    include_formula_trace: bool = True,
    power_line_geometry: dict | None = None,
) -> Path:
    sample_dir = root / "handoff" / "samples" / "01_eval_a"
    sample_dir.mkdir(parents=True, exist_ok=True)
    handoff_dir = root / "handoff"
    formula_trace = handoff_dir / "hfss_ads_formula_trace.md"
    if include_formula_trace:
        formula_trace.write_text(
            "# ADS/Python Formula Trace\n\n- Port pairs: `1,4:5,6`\n- Metrics: Lp/Ls/Qp/Qs/K\n",
            encoding="utf-8",
        )
    gds = sample_dir / "source_geometry.gds"
    if include_gds:
        _write_gds(gds)
    layout = sample_dir / "transformer_layout.layout.json"
    layout.write_text(json.dumps({"ports": [{"name": f"P{idx:03d}"} for idx in range(1, 9)]}), encoding="utf-8")
    power = sample_dir / "power_line_8port_geometry.json"
    power.write_text(json.dumps(power_line_geometry or _power_line_geometry()), encoding="utf-8")
    emx = sample_dir / "emx_reference.s8p"
    emx.write_text("# Hz S RI R 50\n", encoding="utf-8")
    manifest = {
        "selection_rank": "1",
        "evaluation": "eval_a",
        "touchstone_path": str(emx),
        "layout_json_path": str(layout),
        "power_line_8port_geometry_json_path": str(power),
        "gds_path": str(gds),
        "copied_artifacts": {
            "emx_s8p": str(emx),
            "layout_json": str(layout),
            "power_line_8port_geometry_json": str(power),
            "gds": str(gds),
        },
        "port_pairs": [
            {"pair_index": 1, "plus_port_index": 1, "minus_port_index": 4},
            {"pair_index": 2, "plus_port_index": 5, "minus_port_index": 6},
        ],
    }
    (sample_dir / "sample_handoff_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    summary = {
        "overall_status": "PASS",
        "decision": "READY_FOR_HFSS_REBUILD_HANDOFF",
        "artifacts": {"ads_formula_trace": str(formula_trace)} if include_formula_trace else {},
        "sample_results": [
            {
                "selection_rank": "1",
                "evaluation": "eval_a",
                "overall_status": "PASS",
                "handoff_sample_dir": str(sample_dir),
                "gds_path": str(gds),
                "touchstone_path": str(emx),
                "layout_json_path": str(layout),
                "power_line_8port_geometry_json_path": str(power),
            }
        ],
    }
    summary_path = root / "handoff" / "selected_s8p_hfss_handoff_summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    return summary_path


class BuildS8PHFSSAEDTScriptsFromHandoffScriptTest(TransformerToolboxTestBase):
    def test_generates_hfss_payload_build_and_solve_scripts(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)

            status = mod.main(["--handoff-summary", str(handoff), "--out-dir", str(root / "aedt")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            self.assertEqual(len(payload["ports"]), 8)
            self.assertEqual(
                [port["port_sheet_width_um"] for port in payload["ports"]],
                [10.0] * 8,
            )
            self.assertEqual(
                {port["port_name"]: port["port_sheet_axis"] for port in payload["ports"]},
                {
                    "P001": "y",
                    "P002": "x",
                    "P003": "x",
                    "P004": "y",
                    "P005": "y",
                    "P006": "y",
                    "P007": "x",
                    "P008": "x",
                },
            )
            self.assertEqual(payload["contract_evidence"]["actual_port_names"], [f"P{idx:03d}" for idx in range(1, 9)])
            self.assertTrue(payload["contract_evidence"]["power_line_enabled"])
            self.assertAlmostEqual(payload["contract_evidence"]["bridge_width_um"], 10.0, delta=1e-12)
            self.assertAlmostEqual(payload["contract_evidence"]["vertical_length_diameter_ratio"], 1.5, delta=1e-12)
            self.assertAlmostEqual(payload["contract_evidence"]["ground_frame_width_um"], 100.0, delta=1e-12)
            self.assertEqual(
                payload["contract_evidence"]["ground_frame_policy"],
                "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
            )
            self.assertEqual(
                payload["contract_evidence"]["ground_frame_edges_um"],
                {"left_um": 100.0, "right_um": 100.0, "bottom_um": 100.0, "top_um": 100.0},
            )
            self.assertFalse(payload["contract_evidence"]["primary_is_physical_left"])
            self.assertEqual(payload["contract_evidence"]["physical_left_power_line"]["top_port_label"], "P002")
            self.assertEqual(payload["contract_evidence"]["physical_left_power_line"]["top_ground_label"], "P002_G")
            self.assertEqual(payload["contract_evidence"]["physical_right_power_line"]["bottom_port_label"], "P008")
            self.assertEqual(payload["contract_evidence"]["physical_right_power_line"]["bottom_ground_label"], "P008_G")
            self.assertEqual(payload["frequency_grid"]["points"], 111)
            self.assertEqual(payload["hfss"]["expected_touchstone_suffix"], ".s8p")
            self.assertEqual(payload["hfss"]["solution_type"], "Terminal")
            self.assertEqual(payload["hfss"]["calibration_profile"]["name"], "diagnosis_v71_terminal_dual_local_air_best_measured")
            self.assertEqual(payload["hfss"]["calibration_env_defaults"]["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(payload["hfss"]["calibration_env_defaults"]["HFSS_PORT_REFERENCE_EXPECTED_COUNT"], "0")
            self.assertEqual(payload["hfss"]["calibration_env_defaults"]["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(payload["hfss"]["calibration_env_defaults"]["HFSS_M5_SHIELD_BOUNDARY"], "finite")
            self.assertEqual(payload["hfss"]["calibration_env_defaults"]["HFSS_DIELECTRIC_CONDUCTIVITY_MODE"], "ignore")
            self.assertEqual(payload["hfss"]["calibration_env_defaults"]["HFSS_AIR_MARGIN_UM"], "250")
            self.assertEqual(payload["contract_evidence"]["hfss_calibration_profile_name"], "diagnosis_v71_terminal_dual_local_air_best_measured")
            self.assertAlmostEqual(payload["contract_evidence"]["line_width_um"], 10.0, delta=1e-12)
            self.assertTrue(payload["stack"]["dielectrics"])
            self.assertIn("conductivity_s_per_m", payload["stack"]["dielectrics"][0])
            self.assertTrue(Path(payload["source_files"]["ads_formula_trace"]).is_file())
            self.assertTrue((Path(sample["script_dir"]) / "hfss_ads_formula_trace.md").is_file())
            build_script = Path(sample["build_script"]).read_text(encoding="utf-8")
            solve_script = Path(sample["solve_script"]).read_text(encoding="utf-8")
            sample_readme = Path(sample["sample_report"]).read_text(encoding="utf-8")
            self.assertIn("Sweep_5_60_0p5", build_script)
            self.assertIn("AssignLumpedPort", build_script)
            self.assertIn("modeler.create_polyline", build_script)
            self.assertIn("create_rectangular_frame_boxes", build_script)
            self.assertIn("hfss.modeler.unite", build_script)
            self.assertIn("HFSS_CONDUCTOR_SOLVE_INSIDE", build_script)
            self.assertIn("CALIBRATION_ENV_DEFAULTS", build_script)
            self.assertIn("CALIBRATION_PROFILE", build_script)
            self.assertIn("env_str", build_script)
            self.assertIn("effective_hfss_env", build_script)
            self.assertIn("calibration_env_defaults", build_script)
            self.assertIn("calibration_profile", build_script)
            self.assertIn("HFSS_M5_SHIELD_BOUNDARY", build_script)
            self.assertIn("AssignPerfectE", build_script)
            self.assertIn("M5_Grounded_Shield", build_script)
            self.assertIn("set_solve_inside", build_script)
            self.assertIn("HFSS_DIELECTRIC_Z_MIN_UM", build_script)
            self.assertIn("HFSS_AIR_ABOVE_UM", build_script)
            self.assertIn("HFSS_UNITE_STRATEGY", build_script)
            self.assertIn("HFSS_UNITE_BY_METAL", build_script)
            self.assertIn("HFSS_UNITE_CONNECTED_M5", build_script)
            self.assertIn("HFSS_PORT_REFERENCE_MODE", build_script)
            self.assertIn("HFSS_PORT_REFERENCE_EXPECTED_COUNT", build_script)
            self.assertIn("HFSS_PORT_DEEMBED", build_script)
            self.assertIn('"DoDeembed:=", PORT_DEEMBED', build_script)
            self.assertIn("deembed=PORT_DEEMBED", build_script)
            self.assertIn("HFSS_PORT_MODE_RENORM_IMP", build_script)
            self.assertIn("RenormImp:=", build_script)
            self.assertIn("port_deembed", build_script)
            self.assertIn("HFSS_SETUP_PORT_ACCURACY", build_script)
            self.assertIn("HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY", build_script)
            self.assertIn("EnhancedLowFreqAccuracy", build_script)
            self.assertIn("setup.update()", build_script)
            self.assertIn("setup_props_after_update", build_script)
            self.assertIn("local_ground_bbox", build_script)
            self.assertIn("local_ground_bbox_smallest", build_script)
            self.assertIn("validate_port_reference_conductors", build_script)
            self.assertIn("port_reference_conductors", build_script)
            self.assertIn("unite_connected_m5", build_script)
            self.assertIn("maybe_unite_component_records", build_script)
            self.assertIn("connected_by_bbox", build_script)
            self.assertIn("connected_components_by_bbox", build_script)
            self.assertIn("hfss_s8p_build_port_manifest.json", build_script)
            self.assertIn("rfic_transformer_hfss_s8p_build_port_manifest.v1", build_script)
            self.assertIn("export_results", solve_script)
            self.assertIn("expected_touchstone_suffix=.s8p", solve_script)
            self.assertIn("ADS/Python formula trace", sample_readme)
            self.assertIn("Build-time port manifest", sample_readme)
            self.assertIn("HFSS Calibration Profile", sample_readme)
            self.assertIn("diagnosis_v71_terminal_dual_local_air_best_measured", sample_readme)
            self.assertIn("HFSS_PORT_REFERENCE_MODE", sample_readme)
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload power-line bridge width matches vertical power-line width")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload power-line vertical length equals 1.5 max coil height")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload power-line ground frame policy is rectangular shield frame")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload power-line ground frame width is explicit and positive")]["status"], "PASS")
            self.assertEqual(
                checks[("eval_a", "payload power-line shield outer bbox expands inner window by ground frame width")]["status"],
                "PASS",
            )
            self.assertEqual(checks[("eval_a", "payload power-line physical left top power port")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload power-line physical left top ground")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload power-line physical right bottom power port")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload power-line physical right bottom ground")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload primary bridge touches power-line edge")]["status"], "PASS")
            py_compile.compile(sample["build_script"], doraise=True)
            py_compile.compile(sample["solve_script"], doraise=True)

    def test_v69_power_line_width_overrides_wider_layout_pin_footprint(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v69_direct_local_reference_width_locked",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            self.assertEqual([port["port_sheet_width_um"] for port in payload["ports"]], [10.0] * 8)
            self.assertEqual(
                set(payload["contract_evidence"]["port_sheet_width_sources"].values()),
                {"power_line_8port_geometry.line_width_um_or_bridge_width_um"},
            )
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths equal synchronized line width")]["status"], "PASS")

    def test_v70_can_use_emx_pin_footprint_as_excitation_sheet_width(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v70_direct_local_reference_emx_port_footprint",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["hfss"]["calibration_profile"]["name"], "diagnosis_v70_direct_local_reference_emx_port_footprint")
            self.assertEqual(payload["contract_evidence"]["port_sheet_width_mode"], "emx_pin_footprint")
            self.assertAlmostEqual(payload["contract_evidence"]["line_width_um"], 10.0, delta=1e-12)
            self.assertEqual([port["port_sheet_width_um"] for port in payload["ports"]], [4.0] * 8)
            self.assertEqual(
                set(payload["contract_evidence"]["port_sheet_width_sources"].values()),
                {"layout_manifest_emx_pin_footprint"},
            )
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(
                checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"],
                "PASS",
            )

    def test_v71_replays_best_measured_terminal_dual_local_air_settings(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v71_terminal_dual_local_air_best_measured",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(payload["hfss"]["calibration_profile"]["name"], "diagnosis_v71_terminal_dual_local_air_best_measured")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_PORT_REFERENCE_EXPECTED_COUNT"], "0")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MIN_UM"], "700")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MAX_UM"], "700")
            self.assertEqual(env["HFSS_AIR_MARGIN_UM"], "250")
            self.assertEqual(env["HFSS_RADIATION_MARGIN_UM"], "350")
            self.assertEqual(payload["contract_evidence"]["port_sheet_width_mode"], "emx_pin_footprint")
            self.assertEqual([port["port_sheet_width_um"] for port in payload["ports"]], [4.0] * 8)
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(
                checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"],
                "PASS",
            )
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v72_skips_pin_conductors_with_terminal_frame_reference(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v72_terminal_frame_reference_skip_pin_local_air",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(payload["hfss"]["calibration_profile"]["name"], "diagnosis_v72_terminal_frame_reference_skip_pin_local_air")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_SKIP_PIN_CONDUCTORS"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MIN_UM"], "700")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MAX_UM"], "700")
            self.assertEqual(payload["contract_evidence"]["port_sheet_width_mode"], "emx_pin_footprint")
            self.assertEqual([port["port_sheet_width_um"] for port in payload["ports"]], [4.0] * 8)
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v73_enables_terminal_lowfreq_port_accuracy_settings(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v73_terminal_lowfreq_port_accuracy",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(payload["hfss"]["calibration_profile"]["name"], "diagnosis_v73_terminal_lowfreq_port_accuracy")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_SKIP_PIN_CONDUCTORS"], "0")
            self.assertEqual(env["HFSS_SETUP_MAX_DELTA_S"], "0.005")
            self.assertEqual(env["HFSS_SETUP_MAX_PASSES"], "20")
            self.assertEqual(env["HFSS_SETUP_MIN_PASSES"], "4")
            self.assertEqual(env["HFSS_SETUP_BASIS_ORDER"], "2")
            self.assertEqual(env["HFSS_SETUP_PORT_ACCURACY"], "3")
            self.assertEqual(env["HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY"], "1")
            self.assertEqual(env["HFSS_SWEEP_TYPE"], "Interpolating")
            build_script = Path(sample["build_script"]).read_text(encoding="utf-8")
            self.assertIn('"PortAccuracy": int(env_str("HFSS_SETUP_PORT_ACCURACY"', build_script)
            self.assertIn('"EnhancedLowFreqAccuracy": env_bool("HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY"', build_script)
            self.assertIn("setup.update()", build_script)
            self.assertIn("setup_props_after_update", build_script)
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v74a_uses_global_m5_terminal_reference_as_diagnostic_variant(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v74a_terminal_global_m5_reference",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(payload["hfss"]["calibration_profile"]["name"], "diagnosis_v74a_terminal_global_m5_reference")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "global_m5")
            self.assertEqual(env["HFSS_REQUIRE_LOCAL_GROUND_REFERENCE"], "0")
            self.assertEqual(env["HFSS_SETUP_MAX_DELTA_S"], "0.005")
            self.assertEqual(env["HFSS_SETUP_PORT_ACCURACY"], "3")
            self.assertEqual(env["HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY"], "1")
            build_script = Path(sample["build_script"]).read_text(encoding="utf-8")
            self.assertIn('"global_m5"', build_script)
            self.assertIn("reference=reference_conductors", build_script)
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v74b_grounds_m5_shield_as_diagnostic_variant(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v74b_terminal_grounded_m5_shield",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(payload["hfss"]["calibration_profile"]["name"], "diagnosis_v74b_terminal_grounded_m5_shield")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "global_m5")
            self.assertEqual(env["HFSS_M5_SHIELD_BOUNDARY"], "perfecte")
            build_script = Path(sample["build_script"]).read_text(encoding="utf-8")
            self.assertIn("AssignPerfectE", build_script)
            self.assertIn("M5_Grounded_Shield", build_script)
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v75a_uses_physical_port_sheet_width_with_terminal_accuracy(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v75a_terminal_physical_port_width",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(payload["hfss"]["calibration_profile"]["name"], "diagnosis_v75a_terminal_physical_port_width")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_PORT_SHEET_WIDTH_MODE"], "physical_line_width")
            self.assertEqual(env["HFSS_SETUP_PORT_ACCURACY"], "3")
            self.assertEqual(env["HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY"], "1")
            line_width = payload["contract_evidence"]["line_width_um"]
            self.assertTrue(all(abs(port["port_sheet_width_um"] - line_width) < 1e-12 for port in payload["ports"]))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths equal synchronized line width")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v76a_uses_full_pdk_dielectric_stack_window(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v76a_terminal_full_pdk_dielectric_stack",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(payload["hfss"]["calibration_profile"]["name"], "diagnosis_v76a_terminal_full_pdk_dielectric_stack")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_PORT_SHEET_WIDTH_MODE"], "emx_pin_footprint")
            self.assertEqual(env["HFSS_DIELECTRIC_CONDUCTIVITY_MODE"], "loss_tangent")
            self.assertNotIn("HFSS_DIELECTRIC_Z_MIN_UM", env)
            self.assertNotIn("HFSS_DIELECTRIC_Z_MAX_UM", env)
            build_script = Path(sample["build_script"]).read_text(encoding="utf-8")
            self.assertIn('env_str("HFSS_DIELECTRIC_Z_MIN_UM", str(full_dielectric_z_min))', build_script)
            self.assertIn('env_str("HFSS_DIELECTRIC_Z_MAX_UM", str(full_dielectric_z_max))', build_script)
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v76b_uses_backend_dielectric_stack_without_substrate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v76b_terminal_backend_dielectric_stack_no_substrate",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(
                payload["hfss"]["calibration_profile"]["name"],
                "diagnosis_v76b_terminal_backend_dielectric_stack_no_substrate",
            )
            self.assertEqual(env["HFSS_DIELECTRIC_CONDUCTIVITY_MODE"], "loss_tangent")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MIN_UM"], "700")
            self.assertNotIn("HFSS_DIELECTRIC_Z_MAX_UM", env)
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_PORT_SHEET_WIDTH_MODE"], "emx_pin_footprint")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v77a_solves_inside_thick_metal_with_v73_baseline(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v77a_terminal_solve_inside_thick_metal",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(
                payload["hfss"]["calibration_profile"]["name"],
                "diagnosis_v77a_terminal_solve_inside_thick_metal",
            )
            self.assertEqual(env["HFSS_CONDUCTOR_SOLVE_INSIDE"], "1")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MIN_UM"], "700")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MAX_UM"], "700")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_PORT_SHEET_WIDTH_MODE"], "emx_pin_footprint")
            self.assertEqual(env["HFSS_SETUP_PORT_ACCURACY"], "3")
            self.assertEqual(env["HFSS_SETUP_ENHANCED_LOW_FREQ_ACCURACY"], "1")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v78a_uses_midplane_port_calibration_with_v73_baseline(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v78a_terminal_midplane_port_calibration",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(
                payload["hfss"]["calibration_profile"]["name"],
                "diagnosis_v78a_terminal_midplane_port_calibration",
            )
            self.assertEqual(env["HFSS_CONDUCTOR_SOLVE_INSIDE"], "0")
            self.assertEqual(env["HFSS_PORT_SIGNAL_Z_MODE"], "mid")
            self.assertEqual(env["HFSS_PORT_GROUND_Z_MODE"], "mid")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MIN_UM"], "700")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MAX_UM"], "700")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_PORT_SHEET_WIDTH_MODE"], "emx_pin_footprint")
            build_script = Path(sample["build_script"]).read_text(encoding="utf-8")
            self.assertIn('if mode in {"mid", "middle", "center", "centre", "metal_mid"}', build_script)
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v78b_uses_signal_top_port_surface_with_v73_baseline(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v78b_terminal_signal_top_port_surface",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(
                payload["hfss"]["calibration_profile"]["name"],
                "diagnosis_v78b_terminal_signal_top_port_surface",
            )
            self.assertEqual(env["HFSS_CONDUCTOR_SOLVE_INSIDE"], "0")
            self.assertEqual(env["HFSS_PORT_SIGNAL_Z_MODE"], "top")
            self.assertEqual(env["HFSS_PORT_GROUND_Z_MODE"], "top")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MIN_UM"], "700")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MAX_UM"], "700")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_PORT_SHEET_WIDTH_MODE"], "emx_pin_footprint")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")

    def test_v79a_uses_edge_contact_ports_with_solve_inside_repair(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v79a_terminal_edge_contact_port_repair",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(
                payload["hfss"]["calibration_profile"]["name"],
                "diagnosis_v79a_terminal_edge_contact_port_repair",
            )
            self.assertEqual(env["HFSS_CONDUCTOR_SOLVE_INSIDE"], "1")
            self.assertEqual(env["HFSS_PORT_GEOMETRY_MODE"], "edge_contact")
            self.assertEqual(env["HFSS_PORT_EDGE_EPS_UM"], "0")
            self.assertEqual(env["HFSS_PORT_SIGNAL_Z_MODE"], "payload")
            self.assertEqual(env["HFSS_PORT_GROUND_Z_MODE"], "payload")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_PORT_SHEET_WIDTH_MODE"], "emx_pin_footprint")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")

    def test_v80a_uses_foundry_proc_solve_inside_repair_defaults(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v80a_foundry_proc_solve_inside_repair",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(
                payload["hfss"]["calibration_profile"]["name"],
                "diagnosis_v80a_foundry_proc_solve_inside_repair",
            )
            self.assertEqual(env["HFSS_CONDUCTOR_SOLVE_INSIDE"], "1")
            self.assertEqual(env["HFSS_PORT_GEOMETRY_MODE"], "label_center")
            self.assertEqual(env["HFSS_PORT_SIGNAL_Z_MODE"], "payload")
            self.assertEqual(env["HFSS_PORT_GROUND_Z_MODE"], "payload")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            self.assertEqual(env["HFSS_PORT_SHEET_WIDTH_MODE"], "emx_pin_footprint")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")

    def test_v81a_uses_compact_backend_dielectric_window_defaults(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v81a_foundry_backend_dielectric_compact",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(
                payload["hfss"]["calibration_profile"]["name"],
                "diagnosis_v81a_foundry_backend_dielectric_compact",
            )
            self.assertEqual(env["HFSS_CONDUCTOR_SOLVE_INSIDE"], "1")
            self.assertEqual(env["HFSS_DIELECTRIC_XY_MARGIN_UM"], "80")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MIN_UM"], "703.416")
            self.assertNotIn("HFSS_DIELECTRIC_Z_MAX_UM", env)
            self.assertEqual(env["HFSS_PORT_GEOMETRY_MODE"], "label_center")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v82a_uses_effective_backend_dielectric_mode(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v82a_foundry_effective_backend_dielectric_compact",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            build_script = Path(sample["build_script"]).read_text(encoding="utf-8")
            self.assertEqual(
                payload["hfss"]["calibration_profile"]["name"],
                "diagnosis_v82a_foundry_effective_backend_dielectric_compact",
            )
            self.assertEqual(env["HFSS_CONDUCTOR_SOLVE_INSIDE"], "1")
            self.assertEqual(env["HFSS_DIELECTRIC_EFFECTIVE_MODE"], "metal_gap_weighted")
            self.assertEqual(env["HFSS_DIELECTRIC_XY_MARGIN_UM"], "80")
            self.assertEqual(env["HFSS_DIELECTRIC_Z_MIN_UM"], "703.416")
            self.assertNotIn("HFSS_DIELECTRIC_Z_MAX_UM", env)
            self.assertIn("def effective_dielectric_layers", build_script)
            self.assertIn("HFSS_DIELECTRIC_EFFECTIVE_MODE", build_script)
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration port reference mode is explicitly diagnosed")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload HFSS calibration keeps M5 shield finite by default")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v83a_uses_exportable_effective_dielectric_convergence_gate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v83a_foundry_effective_dielectric_exportable_delta03",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(
                payload["hfss"]["calibration_profile"]["name"],
                "diagnosis_v83a_foundry_effective_dielectric_exportable_delta03",
            )
            self.assertEqual(env["HFSS_DIELECTRIC_EFFECTIVE_MODE"], "metal_gap_weighted")
            self.assertEqual(env["HFSS_SETUP_MAX_DELTA_S"], "0.03")
            self.assertEqual(env["HFSS_SETUP_MAX_PASSES"], "10")
            self.assertEqual(env["HFSS_SETUP_MIN_CONVERGED_PASSES"], "1")
            self.assertEqual(env["HFSS_SETUP_BASIS_ORDER"], "2")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            self.assertEqual(env["HFSS_PORT_REFERENCE_MODE"], "local_ground_bbox")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_v84a_screens_effective_dielectric_with_surface_metal(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)
            layout_path = root / "handoff" / "samples" / "01_eval_a" / "transformer_layout.layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "ports": [
                            {
                                "name": f"P{idx:03d}",
                                "internal_size_um": [4.0, 4.0],
                                "signal_internal_size_um": [4.0, 4.0],
                                "ground_internal_size_um": [4.0, 4.0],
                            }
                            for idx in range(1, 9)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--hfss-calibration-profile",
                    "diagnosis_v84a_foundry_effective_dielectric_surface_metal_screen",
                    "--frequency-start-ghz",
                    "14.5",
                    "--frequency-stop-ghz",
                    "15.5",
                    "--frequency-step-ghz",
                    "0.5",
                    "--expected-frequency-points",
                    "3",
                    "--allow-diagnostic-frequency-grid",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            sample = summary["sample_results"][0]
            payload = json.loads(Path(sample["payload_json"]).read_text(encoding="utf-8"))
            env = payload["hfss"]["calibration_env_defaults"]
            self.assertEqual(summary["frequency_grid_purpose"], "diagnostic")
            self.assertEqual(payload["frequency_grid"]["points"], 3)
            self.assertEqual(
                payload["hfss"]["calibration_profile"]["name"],
                "diagnosis_v84a_foundry_effective_dielectric_surface_metal_screen",
            )
            self.assertEqual(env["HFSS_CONDUCTOR_SOLVE_INSIDE"], "0")
            self.assertEqual(env["HFSS_DIELECTRIC_EFFECTIVE_MODE"], "metal_gap_weighted")
            self.assertEqual(env["HFSS_SETUP_MAX_DELTA_S"], "0.08")
            self.assertEqual(env["HFSS_SETUP_MAX_PASSES"], "6")
            self.assertEqual(env["HFSS_SETUP_BASIS_ORDER"], "1")
            self.assertEqual(env["HFSS_SWEEP_TYPE"], "Discrete")
            self.assertEqual(env["HFSS_USE_PYAEDT_REFERENCE_PORT"], "1")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload port sheet widths use EMX pin footprint excitation mode")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "payload embeds diagnosed HFSS calibration profile")]["status"], "PASS")

    def test_payload_stack_uses_emx_sidecar_process_layer_summary(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            power = _power_line_geometry()
            power["process_layer_summary"] = _process_layer_summary()
            handoff = _write_handoff(root, power_line_geometry=power)

            status = mod.main(["--handoff-summary", str(handoff), "--out-dir", str(root / "aedt")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            payload = json.loads(Path(summary["sample_results"][0]["payload_json"]).read_text(encoding="utf-8"))
            conductors = payload["stack"]["conductors"]
            self.assertAlmostEqual(conductors["metal5"]["z_bottom_um"], 703.196, delta=1e-12)
            self.assertAlmostEqual(conductors["metal5"]["z_top_um"], 703.416, delta=1e-12)
            self.assertAlmostEqual(conductors["metal9"]["z_bottom_um"], 707.236, delta=1e-12)
            self.assertAlmostEqual(conductors["metal9"]["z_top_um"], 710.636, delta=1e-12)
            self.assertAlmostEqual(conductors["metal10"]["z_bottom_um"], 714.161, delta=1e-12)
            self.assertAlmostEqual(conductors["metal10"]["z_top_um"], 716.961, delta=1e-12)
            self.assertEqual(
                set(payload["stack"]["sidecar_process_layer_overrides"]),
                {"metal5", "metal9", "metal10"},
            )

    def test_rejects_handoff_without_formula_trace(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root, include_formula_trace=False)

            status = mod.main(["--handoff-summary", str(handoff), "--out-dir", str(root / "aedt")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["handoff ADS/Python formula trace exists"]["status"], "FAIL")

    def test_rejects_handoff_sample_without_gds(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root, include_gds=False)

            status = mod.main(["--handoff-summary", str(handoff), "--out-dir", str(root / "aedt")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "GDS exists for HFSS geometry extraction")]["status"], "FAIL")

    def test_rejects_power_line_payload_with_bad_ground_frame_bbox(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bad_power_line = _power_line_geometry()
            bad_power_line["shield_outer_bbox_um"]["max_x_um"] += 3.0
            handoff = _write_handoff(root, power_line_geometry=bad_power_line)

            status = mod.main(["--handoff-summary", str(handoff), "--out-dir", str(root / "aedt")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(
                checks[("eval_a", "payload power-line shield outer bbox expands inner window by ground frame width")]["status"],
                "FAIL",
            )

    def test_rejects_non_contract_frequency_grid(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--frequency-stop-ghz",
                    "40",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["frequency grid is 5-60 GHz / 0.5 GHz / 111 points"]["status"], "FAIL")

    def test_allows_explicit_two_point_diagnostic_frequency_grid(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff = _write_handoff(root)

            status = mod.main(
                [
                    "--handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "aedt"),
                    "--frequency-start-ghz",
                    "15.0",
                    "--frequency-stop-ghz",
                    "15.5",
                    "--frequency-step-ghz",
                    "0.5",
                    "--expected-frequency-points",
                    "2",
                    "--allow-diagnostic-frequency-grid",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["frequency_grid_purpose"], "diagnostic")
            self.assertEqual(summary["frequency_grid"]["points"], 2)
            payload = json.loads(Path(summary["sample_results"][0]["payload_json"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["frequency_grid"]["start_ghz"], 15.0)
            self.assertEqual(payload["frequency_grid"]["stop_ghz"], 15.5)
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload frequency grid has expected point count")]["status"], "PASS")
            self.assertNotIn(("eval_a", "payload frequency grid is final 5-60 GHz contract"), checks)

    def test_rejects_bad_power_line_payload_contract(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bad_power_line = _power_line_geometry(bridge_width_um=0.02, vertical_length_diameter_ratio=1.25)
            handoff = _write_handoff(root, power_line_geometry=bad_power_line)

            status = mod.main(["--handoff-summary", str(handoff), "--out-dir", str(root / "aedt")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "payload power-line bridge width matches vertical power-line width")]["status"], "FAIL")
            self.assertEqual(checks[("eval_a", "payload power-line vertical length ratio is 1.5")]["status"], "FAIL")
