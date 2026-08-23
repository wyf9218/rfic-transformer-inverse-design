from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_selected_power_line_8port_layout_samples.py"
    spec = importlib.util.spec_from_file_location("audit_selected_power_line_8port_layout_samples_script", script_path)
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


def _layout8(line_width_um: float = 10.0, *, legacy_main_footprints: bool = False) -> dict:
    side_size = [4.0, 4.0] if legacy_main_footprints else [0.5, float(line_width_um)]
    vertical_size = [float(line_width_um), 0.5]
    side_ports = {"P001", "P004", "P005", "P006"}
    return {
        "cadence_pin_purpose": 51,
        "ports": [
            {
                "name": f"P{index:03d}",
                "signal_labels": [f"P{index:03d}"],
                "ground_labels": [f"P{index:03d}_G"],
                "internal_signal_labels": True,
                "internal_ground_labels": True,
                "signal_internal_size_um": side_size if f"P{index:03d}" in side_ports else vertical_size,
                "ground_internal_size_um": side_size if f"P{index:03d}" in side_ports else vertical_size,
                "internal_size_um": side_size if f"P{index:03d}" in side_ports else vertical_size,
            }
            for index in range(1, 9)
        ],
    }


def _process_layer_summary() -> dict:
    return {
        "schema": "rfic_transformer_process_layer_summary.v1",
        "process_file": "test.proc",
        "records": {
            "primary_m10_draw": {"conductor_name": "metal10", "layer": 74, "datatype": 0, "conductor_thickness_um": 2.8},
            "primary_m10_pin": {"conductor_name": "metal10", "layer": 126, "datatype": 0, "conductor_thickness_um": 2.8},
            "secondary_m9_draw": {"conductor_name": "metal9", "layer": 39, "datatype": 60, "conductor_thickness_um": 3.4},
            "secondary_m9_pin": {"conductor_name": "metal9", "layer": 139, "datatype": 0, "conductor_thickness_um": 3.4},
        },
    }


def _power_line_geometry(bridge_width_um: float = 10.0, vertical_length_diameter_ratio: float = 1.5) -> dict:
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
        "bridge_width_um": bridge_width_um,
        "line_width_um": bridge_width_um,
        "center_tap_topology": "primary_right_secondary_left",
        "primary_power_line": {
            "center_x_um": 210.0,
            "center_y_um": 0.0,
            "width_um": bridge_width_um,
            "route_width_um": bridge_width_um,
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
            "width_um": bridge_width_um,
            "route_width_um": bridge_width_um,
            "height_um": vertical_length_um,
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
            "min_y_um": -185.0,
            "max_x_um": 230.0,
            "max_y_um": 185.0,
        },
        "shield_outer_bbox_um": {
            "min_x_um": -330.0,
            "min_y_um": -285.0,
            "max_x_um": 330.0,
            "max_y_um": 285.0,
        },
        "port_ground_overlap_um": 10.0,
        "port_ground_overlap_evidence": {
            "expected_um": 10.0,
            "ports": {
                f"P{index:03d}": {"measured_overlap_um": 10.0}
                for index in range(1, 9)
            },
        },
        "ground_frame_width_um": 100.0,
        "ground_frame_policy": "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
        "process_layer_summary": _process_layer_summary(),
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


def _write_layout_evidence(
    layout_dir: Path,
    bridge_width_um: float = 10.0,
    vertical_length_diameter_ratio: float = 1.5,
    internal_angle: float = 135.0,
    legacy_main_footprints: bool = False,
) -> None:
    layout_dir.mkdir(parents=True, exist_ok=True)
    (layout_dir.parent / "summary.json").write_text(json.dumps(_target_summary(internal_angle=internal_angle)), encoding="utf-8")
    (layout_dir / "transformer_layout.layout.json").write_text(
        json.dumps(_layout8(bridge_width_um, legacy_main_footprints=legacy_main_footprints)),
        encoding="utf-8",
    )
    (layout_dir / "power_line_8port_geometry.json").write_text(
        json.dumps(_power_line_geometry(bridge_width_um, vertical_length_diameter_ratio)),
        encoding="utf-8",
    )


class AuditSelectedPowerLine8PortLayoutSamplesScriptTest(TransformerToolboxTestBase):
    def test_selected_sample_layout_audit_passes_from_work_dir(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            work_dir = root / "evaluations" / "eval_a"
            _write_layout_evidence(work_dir / "layout")
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "row_index": 3,
                        "evaluation": "eval_a",
                        "work_dir": str(work_dir),
                        "touchstone_path": str(work_dir / "emx" / "emx.s8p"),
                    }
                ],
            )

            status = mod.main(
                [
                    "--samples-csv",
                    str(samples),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-power-line-bridge-width-um",
                    "10",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "selected_power_line_8port_layout_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["sample_results"][0]["overall_status"], "PASS")
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_a", "power_line_8port center-tap topology")]["status"], "PASS")
            self.assertEqual(checks[("eval_a", "target summary primary internal winding angles")]["status"], "PASS")

    def test_selected_sample_layout_audit_finds_layout_from_relative_touchstone(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = root / "evaluations" / "eval_b"
            _write_layout_evidence(eval_dir / "layout")
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "row_index": 4,
                        "evaluation": "eval_b",
                        "touchstone_path": "evaluations/eval_b/emx/emx.s8p",
                    }
                ],
            )

            status = mod.main(
                [
                    "--samples-csv",
                    str(samples),
                    "--dataset-dir",
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "selected_power_line_8port_layout_audit_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["sample_results"][0]["layout_json_path"].endswith("transformer_layout.layout.json"))

    def test_selected_sample_layout_audit_rejects_wrong_bridge_width(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            work_dir = root / "evaluations" / "eval_bad"
            _write_layout_evidence(work_dir / "layout", bridge_width_um=0.02)
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_bad",
                        "work_dir": str(work_dir),
                    }
                ],
            )

            status = mod.main(
                [
                    "--samples-csv",
                    str(samples),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-power-line-bridge-width-um",
                    "10",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "selected_power_line_8port_layout_audit_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_bad", "power_line_8port bridge width")]["status"], "FAIL")

    def test_selected_sample_layout_audit_rejects_legacy_main_port_footprints(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            work_dir = root / "evaluations" / "eval_legacy_ports"
            _write_layout_evidence(work_dir / "layout", legacy_main_footprints=True)
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_legacy_ports",
                        "work_dir": str(work_dir),
                    }
                ],
            )

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "selected_power_line_8port_layout_audit_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(
                checks[("eval_legacy_ports", "power_line_8port P001 side-port footprint sync")]["status"],
                "FAIL",
            )
            self.assertEqual(
                checks[("eval_legacy_ports", "power_line_8port P002 vertical-port footprint sync")]["status"],
                "PASS",
            )

    def test_selected_sample_layout_audit_rejects_wrong_vertical_length_ratio(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            work_dir = root / "evaluations" / "eval_bad_ratio"
            _write_layout_evidence(work_dir / "layout", vertical_length_diameter_ratio=1.25)
            samples = root / "samples.csv"
            _write_csv(
                samples,
                [
                    {
                        "selection_rank": 1,
                        "evaluation": "eval_bad_ratio",
                        "work_dir": str(work_dir),
                    }
                ],
            )

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "selected_power_line_8port_layout_audit_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_bad_ratio", "power_line_8port vertical length ratio")]["status"], "FAIL")
            self.assertEqual(
                checks[("eval_bad_ratio", "power_line_8port vertical length equals 1.5*max coil height")]["status"],
                "FAIL",
            )

    def test_selected_sample_layout_audit_rejects_bad_winding_angle_summary(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            work_dir = root / "evaluations" / "eval_bad_angle"
            _write_layout_evidence(work_dir / "layout", internal_angle=120.0)
            samples = root / "samples.csv"
            _write_csv(samples, [{"selection_rank": 1, "evaluation": "eval_bad_angle", "work_dir": str(work_dir)}])

            status = mod.main(["--samples-csv", str(samples), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "selected_power_line_8port_layout_audit_summary.json").read_text(encoding="utf-8"))
            checks = {(item["evaluation"], item["name"]): item for item in summary["checks"]}
            self.assertEqual(checks[("eval_bad_angle", "target summary primary internal winding angles")]["status"], "FAIL")
