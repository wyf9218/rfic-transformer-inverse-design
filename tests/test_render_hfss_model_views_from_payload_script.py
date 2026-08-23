from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "render_hfss_model_views_from_payload.py"
    spec = importlib.util.spec_from_file_location("render_hfss_model_views_from_payload_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _poly_record(index: int, metal: str, points: list[list[float]], role: str) -> dict:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {
        "index": index,
        "metal": metal,
        "role": role,
        "points_um": points,
        "bbox_um": [min(xs), min(ys), max(xs), max(ys)],
    }


def _write_s8p_payload(path: Path) -> None:
    labels = {}
    signal_points = {
        "P001": (-60.0, 45.0),
        "P002": (-60.0, -45.0),
        "P003": (-36.0, 0.0),
        "P004": (-36.0, -8.0),
        "P005": (36.0, 0.0),
        "P006": (36.0, -8.0),
        "P007": (60.0, 45.0),
        "P008": (60.0, -45.0),
    }
    for name, (x, y) in signal_points.items():
        labels[name] = {"origin_um": [x, y], "metal": "metal10" if name <= "P004" else "metal9"}
        labels[f"{name}_G"] = {"origin_um": [-95.0 if x < 0 else 95.0, y], "metal": "metal5"}
    ports = [
        {
            "port_name": name,
            "role": role,
            "ground_name": f"{name}_G",
            "signal_label": labels[name],
            "ground_label": labels[f"{name}_G"],
            "signal_metal": labels[name]["metal"],
            "ground_metal": "metal5",
            "signal_z_um": 713.0 if labels[name]["metal"] == "metal10" else 711.0,
            "ground_z_um": 706.0,
            "port_sheet_width_um": 3.0,
        }
        for name, role in [
            ("P001", "left_power_top"),
            ("P002", "left_power_bottom"),
            ("P003", "primary_top"),
            ("P004", "primary_bottom"),
            ("P005", "secondary_top"),
            ("P006", "secondary_bottom"),
            ("P007", "right_power_top"),
            ("P008", "right_power_bottom"),
        ]
    ]
    primary = [[-70, -35], [-50, -55], [-20, -55], [5, -30], [5, 30], [-20, 55], [-50, 55], [-70, 35]]
    secondary = [[70, -35], [50, -55], [20, -55], [-5, -30], [-5, 30], [20, 55], [50, 55], [70, 35]]
    polygons = [
        _poly_record(0, "metal5", _rect(-100, 80, 100, 90), "shield_top"),
        _poly_record(1, "metal5", _rect(-100, -90, 100, -80), "shield_bottom"),
        _poly_record(2, "metal5", _rect(-100, -80, -90, 80), "shield_left"),
        _poly_record(3, "metal5", _rect(90, -80, 100, 80), "shield_right"),
        _poly_record(4, "metal10", primary, "primary_octagon"),
        _poly_record(5, "metal9", secondary, "secondary_octagon"),
        _poly_record(6, "metal10", _rect(-65, -60, -55, 60), "left_power_line"),
        _poly_record(7, "metal9", _rect(55, -60, 65, 60), "right_power_line"),
        _poly_record(8, "metal10", _rect(-55, -5, -36, 5), "primary_same_width_bridge"),
        _poly_record(9, "metal9", _rect(36, -5, 55, 5), "secondary_same_width_bridge"),
    ]
    payload = {
        "schema": "rfic_transformer_hfss_s8p_build_payload.v1",
        "sample_id": "eval_s8p_render",
        "source_files": {"emx_s8p": "eval_s8p_render.s8p"},
        "stack": {
            "conductors": {
                "metal5": {"z_bottom_um": 705.0, "z_top_um": 706.0, "thickness_um": 1.0},
                "metal9": {"z_bottom_um": 710.5, "z_top_um": 711.0, "thickness_um": 0.5},
                "metal10": {"z_bottom_um": 712.5, "z_top_um": 713.0, "thickness_um": 0.5},
            }
        },
        "bbox_um": [-100.0, -90.0, 100.0, 90.0],
        "conductor_polygons": polygons,
        "labels": labels,
        "ports": ports,
        "power_line_8port_geometry": {
            "schema": "rfic_transformer_power_line_8port_geometry.v1",
            "ground_frame_width_um": 10.0,
            "ground_frame_policy": "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
            "shield_inner_bbox_um": {"min_x_um": -90.0, "min_y_um": -80.0, "max_x_um": 90.0, "max_y_um": 80.0},
            "shield_outer_bbox_um": {"min_x_um": -100.0, "min_y_um": -90.0, "max_x_um": 100.0, "max_y_um": 90.0},
            "primary_bridge": {
                "coil_anchor": {"x_um": -36.0, "y_um": 0.0},
                "power_line_edge": {"x_um": -55.0, "y_um": 0.0},
                "width_um": 10.0,
                "delta_y_um": 0.0,
                "center_y_um": 0.0,
                "power_line_left_edge_x_um": -65.0,
                "power_line_right_edge_x_um": -55.0,
                "extends_away_from_coil_interior": True,
                "is_horizontal": True,
            },
            "secondary_bridge": {
                "coil_anchor": {"x_um": 36.0, "y_um": 0.0},
                "power_line_edge": {"x_um": 55.0, "y_um": 0.0},
                "width_um": 10.0,
                "delta_y_um": 0.0,
                "center_y_um": 0.0,
                "power_line_left_edge_x_um": 55.0,
                "power_line_right_edge_x_um": 65.0,
                "extends_away_from_coil_interior": True,
                "is_horizontal": True,
            },
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class RenderHfssModelViewsFromPayloadScriptTest(TransformerToolboxTestBase):
    def test_renders_s8p_payload_views_and_quality_summary(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = root / "hfss_s8p_build_payload.json"
            _write_s8p_payload(payload)

            status = mod.main(["--payload-json", str(payload), "--out-dir", str(root / "views")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "views" / "hfss_payload_geometry_render_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(len(summary["hfss_objects"]["ports"]), 8)
            self.assertTrue((root / "views" / "hfss_payload_geometry_top_annotated.png").is_file())
            self.assertTrue((root / "views" / "hfss_payload_geometry_isometric.png").is_file())
            self.assertTrue((root / "views" / "hfss_payload_geometry_quality_checks.png").is_file())
            checks = summary["quality_checks"]
            self.assertTrue(checks["port_checks"]["payload_has_eight_ports"])
            self.assertTrue(checks["summary_flags"]["bridges_match_power_line_width_horizontal_centered"])
            self.assertTrue(checks["summary_flags"]["bridges_stay_outside_coil_interior"])
            self.assertTrue(checks["summary_flags"]["ground_frame_bbox_matches_recorded_width"])
            self.assertTrue(checks["summary_flags"]["ground_frame_policy_is_rectangular"])
            self.assertEqual(
                checks["power_line_ground_frame_checks"]["frame_edges_um"],
                {"left_um": 10.0, "right_um": 10.0, "bottom_um": 10.0, "top_um": 10.0},
            )
            self.assertTrue(checks["summary_flags"]["signal_shield_projection_overlap_zero"])

    def test_quality_summary_marks_bad_ground_frame_bbox(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = root / "hfss_s8p_build_payload.json"
            _write_s8p_payload(payload)
            data = json.loads(payload.read_text(encoding="utf-8"))
            data["power_line_8port_geometry"]["shield_outer_bbox_um"]["max_x_um"] += 2.0
            payload.write_text(json.dumps(data, indent=2), encoding="utf-8")

            status = mod.main(["--payload-json", str(payload), "--out-dir", str(root / "views")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "views" / "hfss_payload_geometry_render_summary.json").read_text(encoding="utf-8"))
            checks = summary["quality_checks"]
            self.assertFalse(checks["summary_flags"]["ground_frame_bbox_matches_recorded_width"])

    def test_renders_all_payloads_from_aedt_packet_summary(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = root / "sample" / "hfss_s8p_build_payload.json"
            _write_s8p_payload(payload)
            packet = root / "hfss_s8p_aedt_script_packet_summary.json"
            packet.write_text(
                json.dumps(
                    {
                        "overall_status": "PASS",
                        "sample_results": [
                            {
                                "overall_status": "PASS",
                                "selection_rank": "1",
                                "evaluation": "eval_s8p_render",
                                "payload_json": str(payload),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(["--aedt-packet-summary", str(packet), "--out-dir", str(root / "batch_views")])

            self.assertEqual(status, 0)
            batch = json.loads((root / "batch_views" / "hfss_payload_geometry_render_batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(batch["overall_status"], "PASS")
            self.assertEqual(batch["rendered_count"], 1)
            self.assertTrue(Path(batch["summary_paths"][0]).is_file())
