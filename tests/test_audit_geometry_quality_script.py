from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_geometry_audit_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_geometry_quality.py"
    spec = importlib.util.spec_from_file_location("audit_geometry_quality_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest(angle: float = 135.0) -> dict:
    angle_block = {"min": {"min": angle, "max": angle}, "max": {"min": angle, "max": angle}}
    terminal_block = {"min": {"min": 90.0, "max": 90.0}, "max": {"min": 90.0, "max": 90.0}}
    return {
        "ok_count": 2,
        "fail_count": 0,
        "port_mode": "single_ended_shield_grounded",
        "cadence_pin_purpose": 51,
        "shield_enabled": True,
        "geometry_quality": {
            "geometry_check_count": 2,
            "geometry_check_ok_count": 2,
            "angle_checked_count": 2,
            "primary_internal_angle_deg": angle_block,
            "secondary_internal_angle_deg": angle_block,
            "primary_terminal_interface_angle_deg": terminal_block,
            "secondary_terminal_interface_angle_deg": terminal_block,
        },
    }


def _clearance(selected_status: str = "pass_signal_to_shield_clearance") -> dict:
    return {
        "candidate_count": 2,
        "pass_count": 1 if selected_status == "pass_signal_to_shield_clearance" else 0,
        "reject_count": 1 if selected_status == "pass_signal_to_shield_clearance" else 2,
        "missing_or_other_count": 0,
        "selected": {"cache_key": "abc", "status": selected_status},
        "records": [
            {
                "cache_key": "abc",
                "status": selected_status,
                "direct_signal_shield_overlap_area_um2": 0.0,
                "signal_shield_clearance_violation_area_um2": 0.0,
            },
            {
                "cache_key": "def",
                "status": "reject_signal_body_touches_or_too_close_to_shield",
                "direct_signal_shield_overlap_area_um2": 4.0,
                "signal_shield_clearance_violation_area_um2": 5.0,
            },
        ],
    }


def _layout() -> dict:
    return {
        "cadence_pin_purpose": 51,
        "ports": [
            {"name": "P001", "signal_labels": ["P001"], "ground_labels": ["P001_G"], "internal_signal_labels": True, "internal_ground_labels": True},
            {"name": "P002", "signal_labels": ["P002"], "ground_labels": ["P002_G"], "internal_signal_labels": True, "internal_ground_labels": True},
            {"name": "P003", "signal_labels": ["P003"], "ground_labels": ["P003_G"], "internal_signal_labels": True, "internal_ground_labels": True},
            {"name": "P004", "signal_labels": ["P004"], "ground_labels": ["P004_G"], "internal_signal_labels": True, "internal_ground_labels": True},
        ],
    }


def _layout8() -> dict:
    return {
        "cadence_pin_purpose": 51,
        "ports": [
            {"name": f"P{index:03d}", "signal_labels": [f"P{index:03d}"], "ground_labels": [f"P{index:03d}_G"], "internal_signal_labels": True, "internal_ground_labels": True}
            for index in range(1, 9)
        ],
    }


def _power_line_geometry(
    bridge_width_um: float = 10.0,
    secondary_height_um: float = 390.0,
    vertical_length_diameter_ratio: float = 1.5,
) -> dict:
    max_outer_height_um = 260.0
    vertical_length_um = max_outer_height_um * vertical_length_diameter_ratio
    return {
        "schema": "rfic_transformer_power_line_8port_geometry.v1",
        "enabled": True,
        "placement_policy": "coil_opening_fixed_10um_port_ground_overlap",
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
        "bridge_width_um": bridge_width_um,
        "port_ground_overlap_um": 10.0,
        "port_ground_overlap_evidence": {
            "ports": {
                f"P{index:03d}": {"measured_overlap_um": 10.0}
                for index in range(1, 9)
            }
        },
        "center_tap_topology": "primary_right_secondary_left",
        "primary_power_line": {
            "center_x_um": 210.0,
            "center_y_um": 0.0,
            "width_um": 10.0,
            "height_um": vertical_length_um,
            "bar_layer": 74,
            "bar_datatype": 0,
            "pin_layer": 126,
            "pin_datatype": 0,
            "top_port_label": "P007",
            "bottom_port_label": "P008",
            "top_ground_label": "P007_G",
            "bottom_ground_label": "P008_G",
        },
        "secondary_power_line": {
            "center_x_um": -210.0,
            "center_y_um": 0.0,
            "width_um": 10.0,
            "height_um": secondary_height_um,
            "bar_layer": 39,
            "bar_datatype": 60,
            "pin_layer": 139,
            "pin_datatype": 0,
            "top_port_label": "P002",
            "bottom_port_label": "P003",
            "top_ground_label": "P002_G",
            "bottom_ground_label": "P003_G",
        },
        "primary_power_line_clearance": {
            "placement_side": "right",
            "bar_left_edge_x_um": 205.0,
            "bar_right_edge_x_um": 215.0,
            "own_coil_left_edge_x_um": -150.0,
            "own_coil_right_edge_x_um": 150.0,
            "other_coil_left_edge_x_um": -150.0,
            "other_coil_right_edge_x_um": 150.0,
            "combined_coil_left_edge_x_um": -150.0,
            "combined_coil_right_edge_x_um": 150.0,
            "own_coil_boundary_clearance_um": 55.0,
            "other_coil_boundary_clearance_um": 55.0,
            "combined_coil_boundary_clearance_um": 55.0,
            "outside_combined_coil_projection": True,
        },
        "secondary_power_line_clearance": {
            "placement_side": "left",
            "bar_left_edge_x_um": -215.0,
            "bar_right_edge_x_um": -205.0,
            "own_coil_left_edge_x_um": -150.0,
            "own_coil_right_edge_x_um": 150.0,
            "other_coil_left_edge_x_um": -150.0,
            "other_coil_right_edge_x_um": 150.0,
            "combined_coil_left_edge_x_um": -150.0,
            "combined_coil_right_edge_x_um": 150.0,
            "own_coil_boundary_clearance_um": 55.0,
            "other_coil_boundary_clearance_um": 55.0,
            "combined_coil_boundary_clearance_um": 55.0,
            "outside_combined_coil_projection": True,
        },
        "primary_bridge": {
            "coil_anchor": {"x_um": 150.0, "y_um": 0.0},
            "power_line_edge": {"x_um": 205.0, "y_um": 0.0},
            "width_um": bridge_width_um,
            "length_um": 55.0,
            "delta_y_um": 0.0,
            "center_y_um": 0.0,
            "power_line_center_y_um": 0.0,
            "power_line_left_edge_x_um": 205.0,
            "power_line_right_edge_x_um": 215.0,
            "nearest_power_line_edge_x_um": 205.0,
            "power_line_edge_alignment_error_um": 0.0,
            "extends_away_from_coil_interior": True,
            "is_horizontal": True,
        },
        "secondary_bridge": {
            "coil_anchor": {"x_um": -150.0, "y_um": 0.0},
            "power_line_edge": {"x_um": -205.0, "y_um": 0.0},
            "width_um": bridge_width_um,
            "length_um": 55.0,
            "delta_y_um": 0.0,
            "center_y_um": 0.0,
            "power_line_center_y_um": 0.0,
            "power_line_left_edge_x_um": -215.0,
            "power_line_right_edge_x_um": -205.0,
            "nearest_power_line_edge_x_um": -205.0,
            "power_line_edge_alignment_error_um": 0.0,
            "extends_away_from_coil_interior": True,
            "is_horizontal": True,
        },
        "shield_inner_bbox_um": {
            "min_x_um": -230.0,
            "min_y_um": -190.0,
            "max_x_um": 230.0,
            "max_y_um": 190.0,
        },
        "shield_outer_bbox_um": {
            "min_x_um": -330.0,
            "min_y_um": -290.0,
            "max_x_um": 330.0,
            "max_y_um": 290.0,
        },
        "ground_frame_width_um": 100.0,
        "ground_frame_policy": "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
        "process_layer_summary": {
            "schema": "rfic_transformer_process_layer_summary.v1",
            "process_file": "test.proc",
            "records": {
                "primary_m10_draw": {
                    "conductor_name": "metal10",
                    "layer": 74,
                    "datatype": 0,
                    "conductor_thickness_um": 2.8,
                },
                "primary_m10_pin": {
                    "conductor_name": "metal10",
                    "layer": 126,
                    "datatype": 0,
                    "conductor_thickness_um": 2.8,
                },
                "secondary_m9_draw": {
                    "conductor_name": "metal9",
                    "layer": 39,
                    "datatype": 60,
                    "conductor_thickness_um": 3.4,
                },
                "secondary_m9_pin": {
                    "conductor_name": "metal9",
                    "layer": 139,
                    "datatype": 0,
                    "conductor_thickness_um": 3.4,
                },
            },
        },
    }


def _target_summary(internal_angle: float = 135.0, terminal_angle: float = 90.0) -> dict:
    return {
        "geometry_check": {
            "errors": [],
            "warnings": [],
            "metrics": {
                "primary_winding_centerline_min_internal_angle_deg": internal_angle,
                "primary_winding_centerline_max_internal_angle_deg": internal_angle,
                "primary_winding_centerline_min_terminal_angle_deg": terminal_angle,
                "primary_winding_centerline_max_terminal_angle_deg": terminal_angle,
                "primary_winding_centerline_diagonal_segment_count": 4,
                "secondary_winding_centerline_min_internal_angle_deg": internal_angle,
                "secondary_winding_centerline_max_internal_angle_deg": internal_angle,
                "secondary_winding_centerline_min_terminal_angle_deg": terminal_angle,
                "secondary_winding_centerline_max_terminal_angle_deg": terminal_angle,
                "secondary_winding_centerline_diagonal_segment_count": 4,
            },
        },
    }


class AuditGeometryQualityScriptTest(TransformerToolboxTestBase):
    def test_geometry_audit_passes_manifest_clearance_and_layout(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "dataset_manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
            (root / "final500_ground_clearance_audit.json").write_text(json.dumps(_clearance()), encoding="utf-8")
            (root / "transformer_layout.layout.json").write_text(json.dumps(_layout()), encoding="utf-8")

            status = audit.main([str(root), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertTrue(summary["clearance_audit_path"].endswith("final500_ground_clearance_audit.json"))
            self.assertTrue(summary["layout_json_path"].endswith("transformer_layout.layout.json"))

    def test_geometry_audit_fails_on_bad_internal_angle(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "dataset_manifest.json").write_text(json.dumps(_manifest(angle=120.0)), encoding="utf-8")

            status = audit.main([str(root), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["primary_internal_angle_deg"]["status"], "FAIL")

    def test_geometry_audit_can_require_clearance_evidence(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "dataset_manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")

            status = audit.main([str(root), "--out-dir", str(root / "audit"), "--require-clearance-audit"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["clearance audit"]["status"], "FAIL")

    def test_geometry_audit_fails_on_selected_clearance_reject(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "final500_ground_clearance_audit.json").write_text(
                json.dumps(_clearance(selected_status="reject_signal_body_touches_or_too_close_to_shield")),
                encoding="utf-8",
            )

            status = audit.main([str(root), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["selected clearance sample"]["status"], "FAIL")

    def test_geometry_audit_fails_when_no_raw_evidence_source_exists(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            status = audit.main([str(root), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["geometry evidence source"]["status"], "FAIL")

    def test_geometry_audit_requires_each_expected_port_ground_label(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            layout = _layout()
            layout["ports"][2]["ground_labels"] = []
            (root / "transformer_layout.layout.json").write_text(json.dumps(layout), encoding="utf-8")

            status = audit.main([str(root), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["layout grounded labels"]["status"], "FAIL")
            self.assertIn("P003", checks["layout grounded labels"]["detail"])

    def test_geometry_audit_accepts_power_line_8port_geometry_evidence(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "transformer_layout.layout.json").write_text(json.dumps(_layout8()), encoding="utf-8")
            (root / "power_line_8port_geometry.json").write_text(json.dumps(_power_line_geometry()), encoding="utf-8")

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-port-names",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--require-power-line-8port-geometry",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["layout port count"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port placement policy"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port bridge width"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port shared line width"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port equal vertical heights"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port vertical length ratio"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port vertical length equals 1.5*max coil height"]["status"], "PASS")
            self.assertEqual(checks["process layer primary_m10_draw"]["status"], "PASS")
            self.assertEqual(checks["process layer secondary_m9_draw"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port role label map"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port center-tap topology"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port physical left/right order"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port physical left top port label"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port physical right bottom port label"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port primary bridge centered y=0"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port primary bridge horizontal"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port secondary bridge touches power-line edge"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port primary power-line outside combined coil projection"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port primary power-line clears other coil boundary"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port secondary power-line outside combined coil projection"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port secondary power-line clears other coil boundary"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port ground frame width"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port bars cross opening and reach ground frame"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port bridges inside shield opening"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port fixed port-ground overlap"]["status"], "PASS")
            self.assertEqual(summary["power_line_8port_counts"]["label_count"], 8)
            self.assertEqual(summary["power_line_8port_counts"]["placement_policy"], "coil_opening_fixed_10um_port_ground_overlap")

    def test_geometry_audit_rejects_legacy_outer_power_line_8port_placement(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            geometry = _power_line_geometry()
            geometry["placement_policy"] = "outside_shield"
            geometry["primary_power_line"]["center_x_um"] = -260.0
            geometry["primary_bridge"]["power_line_edge"]["x_um"] = -255.0
            (root / "transformer_layout.layout.json").write_text(json.dumps(_layout8()), encoding="utf-8")
            (root / "power_line_8port_geometry.json").write_text(json.dumps(geometry), encoding="utf-8")

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-port-names",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--require-power-line-8port-geometry",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["power_line_8port placement policy"]["status"], "FAIL")
            self.assertEqual(checks["power_line_8port bars cross opening and reach ground frame"]["status"], "FAIL")
            self.assertEqual(checks["power_line_8port bridges inside shield opening"]["status"], "FAIL")

    def test_geometry_audit_rejects_power_line_8port_left_right_label_position_mismatch(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            geometry = _power_line_geometry()
            geometry["primary_power_line"]["center_x_um"] = -210.0
            geometry["secondary_power_line"]["center_x_um"] = 210.0
            geometry["center_tap_topology"] = "primary_left_secondary_right"
            (root / "transformer_layout.layout.json").write_text(json.dumps(_layout8()), encoding="utf-8")
            (root / "power_line_8port_geometry.json").write_text(json.dumps(geometry), encoding="utf-8")

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-port-names",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--require-power-line-8port-geometry",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(checks["power_line_8port center-tap topology"]["status"], "FAIL")
            self.assertEqual(checks["power_line_8port physical left/right order"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port physical left top port label"]["status"], "FAIL")
            self.assertEqual(checks["power_line_8port physical right bottom port label"]["status"], "FAIL")

    def test_geometry_audit_rejects_bad_power_line_8port_bridge_width(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "transformer_layout.layout.json").write_text(json.dumps(_layout8()), encoding="utf-8")
            (root / "power_line_8port_geometry.json").write_text(json.dumps(_power_line_geometry(bridge_width_um=0.02)), encoding="utf-8")

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-port-names",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--require-power-line-8port-geometry",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["power_line_8port bridge width"]["status"], "PASS")
            self.assertEqual(checks["power_line_8port shared line width"]["status"], "FAIL")
            self.assertEqual(checks["power_line_8port primary bridge width matches power-line edge width"]["status"], "FAIL")
            self.assertEqual(checks["power_line_8port secondary bridge width matches power-line edge width"]["status"], "FAIL")

    def test_geometry_audit_rejects_power_line_8port_other_coil_clearance_violation(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            geometry = _power_line_geometry()
            geometry["primary_power_line_clearance"]["other_coil_boundary_clearance_um"] = -0.5
            geometry["primary_power_line_clearance"]["combined_coil_boundary_clearance_um"] = -0.5
            geometry["primary_power_line_clearance"]["outside_combined_coil_projection"] = False
            (root / "transformer_layout.layout.json").write_text(json.dumps(_layout8()), encoding="utf-8")
            (root / "power_line_8port_geometry.json").write_text(json.dumps(geometry), encoding="utf-8")

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-port-names",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--require-power-line-8port-geometry",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(checks["power_line_8port primary power-line outside combined coil projection"]["status"], "FAIL")
            self.assertEqual(checks["power_line_8port primary power-line clears other coil boundary"]["status"], "FAIL")

    def test_geometry_audit_rejects_bad_power_line_8port_vertical_length_ratio(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            geometry = _power_line_geometry()
            geometry["vertical_length_diameter_ratio"] = 1.25
            geometry["vertical_length_um"] = 325.0
            geometry["expected_vertical_length_um"] = 325.0
            geometry["primary_power_line"]["height_um"] = 325.0
            geometry["secondary_power_line"]["height_um"] = 325.0
            (root / "transformer_layout.layout.json").write_text(json.dumps(_layout8()), encoding="utf-8")
            (root / "power_line_8port_geometry.json").write_text(json.dumps(geometry), encoding="utf-8")

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-port-names",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--require-power-line-8port-geometry",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["power_line_8port vertical length ratio"]["status"], "FAIL")
            self.assertEqual(checks["power_line_8port vertical length equals 1.5*max coil height"]["status"], "FAIL")

    def test_geometry_audit_rejects_off_center_power_line_8port_bridge(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            geometry = _power_line_geometry()
            geometry["primary_bridge"]["power_line_edge"]["y_um"] = 0.25
            geometry["primary_bridge"]["delta_y_um"] = 0.25
            geometry["primary_bridge"]["center_y_um"] = 0.125
            geometry["primary_bridge"]["is_horizontal"] = False
            (root / "transformer_layout.layout.json").write_text(json.dumps(_layout8()), encoding="utf-8")
            (root / "power_line_8port_geometry.json").write_text(json.dumps(geometry), encoding="utf-8")

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-port-names",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--require-power-line-8port-geometry",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["power_line_8port primary bridge centered y=0"]["status"], "FAIL")
            self.assertEqual(checks["power_line_8port primary bridge horizontal"]["status"], "FAIL")

    def test_geometry_audit_accepts_target_summary_angle_evidence(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "summary.json").write_text(json.dumps(_target_summary()), encoding="utf-8")

            status = audit.main([str(root), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target summary geometry errors"]["status"], "PASS")
            self.assertEqual(checks["target summary primary internal winding angles"]["status"], "PASS")
            self.assertEqual(checks["target summary secondary terminal interface angles"]["status"], "PASS")

    def test_geometry_audit_fails_target_summary_bad_angle(self) -> None:
        audit = _load_geometry_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "summary.json").write_text(json.dumps(_target_summary(internal_angle=120.0)), encoding="utf-8")

            status = audit.main([str(root), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "geometry_quality_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target summary primary internal winding angles"]["status"], "FAIL")
