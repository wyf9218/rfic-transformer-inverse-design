from tests.rfic_transformer_inverse_design.shared import *
from rfic_transformer_inverse_design.layout.checks import (
    _bundle_via_rule_checks,
    _generic_same_layer_spacing_checks,
    _primary_intermediate_bridge_pad_clearance_checks,
    run_transformer_gdstk_checks,
)
from rfic_transformer_inverse_design.layout.builders import _build_center_tapped_inductor_geometry, _octagon_vertices, _open_octagon_path
from rfic_transformer_inverse_design.process import parse_proc_file


class TransformerLayoutTest(TransformerToolboxTestBase):
    def test_power_line_8port_export_writes_eight_ports_and_geometry_evidence(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(
                cfg.emx,
                port_mode="single_ended_shield_grounded",
                differential_port_pairs=((0, 3), (4, 5)),
                power_line_8port=PowerLine8PortSpec(
                    enabled=True,
                    bridge_width_um=10.0,
                    port_map=("P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"),
                    role_labels=(
                        ("primary_top", "P001"),
                        ("left_power_top", "P002"),
                        ("left_power_bottom", "P003"),
                        ("primary_bottom", "P004"),
                        ("secondary_bottom", "P005"),
                        ("secondary_top", "P006"),
                        ("right_power_top", "P007"),
                        ("right_power_bottom", "P008"),
                    ),
                ),
            ),
            bounds=replace(
                cfg.bounds,
                primary=replace(
                    cfg.bounds.primary,
                    center_tap=True,
                    vdd_bar=replace(cfg.bounds.primary.vdd_bar, enabled=True, bar_layer=cfg.emx.ap_layer, width_um=10.0, offset_um=12.0),
                ),
                secondary=replace(
                    cfg.bounds.secondary,
                    center_tap=True,
                    vdd_bar=replace(cfg.bounds.secondary.vdd_bar, enabled=True, bar_layer=cfg.emx.m9_layer, width_um=10.0, offset_um=12.0),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                cfg.bounds.midpoint(),
                cfg,
                Path(tmpdir),
                validate_geometry=False,
            )

            manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual([port["name"] for port in manifest["ports"]], [f"P{idx:03d}" for idx in range(1, 9)])
            self.assertTrue(all(port["ground_labels"] for port in manifest["ports"]))
            self.assertEqual(manifest["ports"][0]["ground_labels"], ["P001_G"])
            self.assertEqual(manifest["ports"][7]["ground_labels"], ["P008_G"])

            lib = gdstk.read_gds(str(layout.gds_path))
            labels = {label.text: (float(label.origin[0]), float(label.origin[1])) for cell in lib.cells for label in cell.labels}
            for name in [f"P{idx:03d}" for idx in range(1, 9)] + [f"P{idx:03d}_G" for idx in range(1, 9)]:
                self.assertIn(name, labels)
            self.assertLess(labels["P001"][0], labels["P007"][0])
            self.assertLess(labels["P002"][0], labels["P008"][0])
            self.assertLess(labels["P001_G"][0], labels["P007_G"][0])
            self.assertLess(labels["P002_G"][0], labels["P008_G"][0])

            audit = json.loads((Path(tmpdir) / "power_line_8port_geometry.json").read_text(encoding="utf-8"))
            self.assertTrue(audit["enabled"])
            line_width_um = float(audit["line_width_um"])
            self.assertAlmostEqual(audit["bridge_width_um"], line_width_um, delta=1e-12)
            geom = cfg.bounds.midpoint()
            expected_vertical_length = 1.5 * max(
                geom.primary.outer_height_um,
                geom.secondary.outer_height_um,
            )
            self.assertAlmostEqual(audit["vertical_length_um"], expected_vertical_length, delta=1e-9)
            self.assertAlmostEqual(audit["max_outer_height_um"], expected_vertical_length / 1.5, delta=1e-9)
            self.assertAlmostEqual(audit["vertical_length_diameter_ratio"], 1.5, delta=1e-12)
            self.assertAlmostEqual(audit["expected_vertical_length_um"], expected_vertical_length, delta=1e-9)
            self.assertAlmostEqual(audit["primary_power_line"]["height_um"], expected_vertical_length, delta=1e-9)
            self.assertAlmostEqual(audit["secondary_power_line"]["height_um"], expected_vertical_length, delta=1e-9)
            ports_by_name = {port["name"]: port for port in manifest["ports"]}
            for port_name in ("P001", "P004", "P005", "P006"):
                self.assertEqual(ports_by_name[port_name]["signal_internal_size_um"], [0.5, line_width_um])
                self.assertEqual(ports_by_name[port_name]["ground_internal_size_um"], [0.5, line_width_um])
                self.assertEqual(ports_by_name[port_name]["internal_size_um"], [0.5, line_width_um])
            for port_name in ("P002", "P003", "P007", "P008"):
                self.assertEqual(ports_by_name[port_name]["signal_internal_size_um"], [line_width_um, 0.5])
                self.assertEqual(ports_by_name[port_name]["ground_internal_size_um"], [line_width_um, 0.5])
                self.assertEqual(ports_by_name[port_name]["internal_size_um"], [line_width_um, 0.5])
            self.assertEqual(audit["primary_power_line"]["bar_layer"], cfg.emx.ap_layer)
            self.assertEqual(audit["primary_power_line"]["bar_datatype"], 0)
            self.assertEqual(audit["secondary_power_line"]["bar_layer"], cfg.emx.m9_layer)
            self.assertEqual(audit["secondary_power_line"]["bar_datatype"], 60)
            process_records = audit["process_layer_summary"]["records"]
            self.assertEqual(process_records["primary_m10_draw"]["conductor_name"], "metal10")
            self.assertEqual(process_records["secondary_m9_draw"]["conductor_name"], "metal9")
            shield_inner = audit["shield_inner_bbox_um"]
            shield_outer = audit["shield_outer_bbox_um"]
            self.assertAlmostEqual(audit["ground_frame_width_um"], cfg.bounds.shield.margin_um, delta=1e-9)
            self.assertEqual(
                audit["ground_frame_policy"],
                "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
            )
            self.assertAlmostEqual(
                shield_inner["min_x_um"] - shield_outer["min_x_um"],
                audit["ground_frame_width_um"],
                delta=1e-9,
            )
            self.assertAlmostEqual(
                shield_outer["max_x_um"] - shield_inner["max_x_um"],
                audit["ground_frame_width_um"],
                delta=1e-9,
            )
            self.assertAlmostEqual(
                shield_inner["min_y_um"] - shield_outer["min_y_um"],
                audit["ground_frame_width_um"],
                delta=1e-9,
            )
            self.assertAlmostEqual(
                shield_outer["max_y_um"] - shield_inner["max_y_um"],
                audit["ground_frame_width_um"],
                delta=1e-9,
            )
            port_ground_overlap_um = float(audit["port_ground_overlap_um"])
            self.assertAlmostEqual(
                shield_inner["max_y_um"] - shield_inner["min_y_um"],
                expected_vertical_length - 2.0 * port_ground_overlap_um,
                delta=1e-9,
            )
            for power_line_name in ("primary_power_line", "secondary_power_line"):
                power_line = audit[power_line_name]
                self.assertAlmostEqual(
                    power_line["center_y_um"] + 0.5 * power_line["height_um"],
                    shield_inner["max_y_um"] + port_ground_overlap_um,
                    delta=1e-9,
                )
                self.assertAlmostEqual(
                    power_line["center_y_um"] - 0.5 * power_line["height_um"],
                    shield_inner["min_y_um"] - port_ground_overlap_um,
                    delta=1e-9,
                )
            self.assertEqual(audit["labels"]["left_power_top"], "P002")
            self.assertEqual(audit["labels"]["right_power_bottom"], "P008")
            bars = [audit["primary_power_line"], audit["secondary_power_line"]]
            left_bar = min(bars, key=lambda item: item["center_x_um"])
            right_bar = max(bars, key=lambda item: item["center_x_um"])
            self.assertEqual(left_bar["top_port_label"], "P002")
            self.assertEqual(left_bar["bottom_port_label"], "P003")
            self.assertEqual(right_bar["top_port_label"], "P007")
            self.assertEqual(right_bar["bottom_port_label"], "P008")
            self.assertEqual(audit["physical_left_power_line"]["top_port_label"], "P002")
            self.assertEqual(audit["physical_left_power_line"]["bottom_port_label"], "P003")
            self.assertEqual(audit["physical_right_power_line"]["top_port_label"], "P007")
            self.assertEqual(audit["physical_right_power_line"]["bottom_port_label"], "P008")
            self.assertIn(audit["primary_is_physical_left"], {True, False})
            for bridge_name in ("primary_bridge", "secondary_bridge"):
                bridge = audit[bridge_name]
                self.assertAlmostEqual(bridge["width_um"], line_width_um, delta=1e-12)
                self.assertAlmostEqual(bridge["delta_y_um"], 0.0, delta=1e-12)
                self.assertAlmostEqual(bridge["coil_anchor"]["y_um"], 0.0, delta=1e-12)
                self.assertAlmostEqual(bridge["power_line_edge"]["y_um"], 0.0, delta=1e-12)
                self.assertAlmostEqual(bridge["power_line_center_y_um"], 0.0, delta=1e-12)
                self.assertAlmostEqual(bridge["power_line_edge_alignment_error_um"], 0.0, delta=1e-12)
                self.assertTrue(bridge["is_horizontal"])
                self.assertTrue(bridge["extends_away_from_coil_interior"])
                self.assertGreater(bridge["length_um"], 0.0)
                self.assertLess(bridge["length_um"], expected_vertical_length * 0.4)
            for clearance_name in ("primary_power_line_clearance", "secondary_power_line_clearance"):
                clearance = audit[clearance_name]
                self.assertTrue(clearance["outside_combined_coil_projection"])
                self.assertGreater(clearance["combined_coil_boundary_clearance_um"], 0.0)
                self.assertGreater(clearance["other_coil_boundary_clearance_um"], 0.0)
                self.assertGreater(clearance["own_coil_boundary_clearance_um"], 0.0)
                self.assertAlmostEqual(
                    clearance["combined_coil_boundary_clearance_um"],
                    12.0,
                delta=1e-9,
            )

    def test_power_line_signal_only_s4p_export_writes_four_ports_and_auxiliary_grounds(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(
                cfg.emx,
                port_mode="single_ended_shield_grounded",
                differential_port_pairs=((0, 1), (2, 3)),
                power_line_8port=PowerLine8PortSpec(
                    enabled=True,
                    touchstone_mode="signal_4_grounded_aux",
                    bridge_width_um=10.0,
                    port_map=("P001", "P002", "P003", "P004"),
                    role_labels=(
                        ("primary_top", "P001"),
                        ("primary_bottom", "P002"),
                        ("secondary_top", "P003"),
                        ("secondary_bottom", "P004"),
                        ("left_power_top", "P005"),
                        ("left_power_bottom", "P006"),
                        ("right_power_top", "P007"),
                        ("right_power_bottom", "P008"),
                    ),
                ),
            ),
            bounds=replace(
                cfg.bounds,
                primary=replace(
                    cfg.bounds.primary,
                    center_tap=True,
                    vdd_bar=replace(cfg.bounds.primary.vdd_bar, enabled=True, bar_layer=cfg.emx.ap_layer, width_um=10.0, offset_um=12.0),
                ),
                secondary=replace(
                    cfg.bounds.secondary,
                    center_tap=True,
                    vdd_bar=replace(cfg.bounds.secondary.vdd_bar, enabled=True, bar_layer=cfg.emx.m9_layer, width_um=10.0, offset_um=12.0),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                cfg.bounds.midpoint(),
                cfg,
                Path(tmpdir),
                validate_geometry=False,
            )

            manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual([port["name"] for port in manifest["ports"]], ["P001", "P002", "P003", "P004"])
            for port in manifest["ports"]:
                self.assertEqual(port["ground_labels"], [f"{port['name']}_G"])
                for aux_name in ("P005", "P006", "P007", "P008"):
                    self.assertNotIn(aux_name, port["ground_labels"])
                for aux_name in ("P005_G", "P006_G", "P007_G", "P008_G"):
                    self.assertNotIn(aux_name, port["ground_labels"])

            lib = gdstk.read_gds(str(layout.gds_path))
            labels = {label.text for cell in lib.cells for label in cell.labels}
            expected_labels = [f"P{idx:03d}" for idx in range(1, 9)] + [f"P{idx:03d}_G" for idx in range(1, 5)]
            for name in expected_labels:
                self.assertIn(name, labels)
            for name in [f"P{idx:03d}_G" for idx in range(5, 9)]:
                self.assertNotIn(name, labels)

            audit = json.loads((Path(tmpdir) / "power_line_8port_geometry.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["touchstone_mode"], "signal_4_grounded_aux")
            self.assertEqual(audit["auxiliary_ground_reference_labels"], ["P005", "P006", "P007", "P008"])
            stitches = audit["power_line_ground_stitches"]
            self.assertEqual(sorted(item["label"] for item in stitches), ["P005", "P006", "P007", "P008"])
            self.assertTrue(all(item["ground_label"] == item["label"] for item in stitches))
            self.assertTrue(all(item["target_ground_metal"] == "metal5" for item in stitches))
            source_by_label = {item["label"]: item["source_metal"] for item in stitches}
            self.assertEqual(sorted(label for label, source in source_by_label.items() if source == "metal10"), ["P007", "P008"])
            self.assertEqual(sorted(label for label, source in source_by_label.items() if source == "metal9"), ["P005", "P006"])
            all_via_layers = sorted({via["layer"] for item in stitches for via in item["via_stack"]})
            self.assertEqual(all_via_layers, [55, 56, 57, 58, 85])

    def test_power_line_8port_export_rejects_missing_center_taps(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(
                cfg.emx,
                port_mode="single_ended_shield_grounded",
                differential_port_pairs=((0, 1), (6, 7)),
                power_line_8port=PowerLine8PortSpec(
                    enabled=True,
                    bridge_width_um=10.0,
                    port_map=("P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"),
                    role_labels=(
                        ("primary_top", "P001"),
                        ("left_power_top", "P002"),
                        ("left_power_bottom", "P003"),
                        ("primary_bottom", "P004"),
                        ("secondary_bottom", "P005"),
                        ("secondary_top", "P006"),
                        ("right_power_top", "P007"),
                        ("right_power_bottom", "P008"),
                    ),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "primary_center_tap=true"):
                export_transformer_layout(cfg.bounds.midpoint(), cfg, Path(tmpdir), validate_geometry=False)

    @staticmethod
    def _find_polygon_box_containing_point(lib: gdstk.Library, layer: int, point: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]]:
        px, py = point
        for cell in lib.cells:
            for poly in cell.polygons:
                if int(poly.layer) != int(layer):
                    continue
                bbox = poly.bounding_box()
                if bbox is None:
                    continue
                (min_x, min_y), (max_x, max_y) = bbox
                if float(min_x) - 1e-9 <= px <= float(max_x) + 1e-9 and float(min_y) - 1e-9 <= py <= float(max_y) + 1e-9:
                    return ((float(min_x), float(min_y)), (float(max_x), float(max_y)))
        raise AssertionError(f"No polygon on layer {layer} contains point {point}")

    def test_layout_export_smoke_for_both_topologies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for topology in ("1t1t", "2t2t"):
                cfg = default_run_config(topology)
                cfg = replace(cfg, bounds=replace(cfg.bounds, shield=replace(cfg.bounds.shield, enabled=False)))
                layout = export_transformer_layout(
                    geometry=cfg.bounds.midpoint(),
                    run_config=cfg,
                    out_dir=root / topology,
                    validate_geometry=False,
                )
                self.assertTrue(layout.gds_path.exists(), topology)
                self.assertTrue(layout.manifest_path.exists(), topology)
                self.assertTrue(layout.preview_path.exists(), topology)
                self.assertTrue(layout.debug_preview_path.exists(), topology)

                manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(len(manifest["ports"]), 4)
                self.assertEqual(manifest["top_cell"], cfg.emx.top_cell_prefix)

                lib = gdstk.read_gds(str(layout.gds_path))
                layers = {int(poly.layer) for cell in lib.cells for poly in cell.polygons}
                proc_info = parse_proc_file(cfg.emx.emx_process_file)
                primary_draw_layer = proc_info.preferred_draw_pair_for_layer(cfg.emx.ap_layer).layer
                secondary_draw_layer = proc_info.preferred_draw_pair_for_layer(cfg.emx.m9_layer).layer
                primary_pin_layer = proc_info.preferred_pin_pair_for_layer(cfg.emx.ap_layer).layer
                secondary_pin_layer = proc_info.preferred_pin_pair_for_layer(cfg.emx.m9_layer).layer
                self.assertIn(primary_draw_layer, layers)
                self.assertIn(secondary_draw_layer, layers)
                self.assertIn(primary_pin_layer, layers)
                self.assertIn(secondary_pin_layer, layers)
                self.assertNotIn(cfg.emx.m5_layer, layers)
                if topology == "2t2t":
                    self.assertIn(proc_info.preferred_draw_pair_for_layer(cfg.emx.primary_bridge_layer).layer, layers)
                    self.assertIn(proc_info.preferred_draw_pair_for_layer(cfg.emx.secondary_bridge_layer).layer, layers)

                for port in manifest["ports"]:
                    self.assertFalse(port["ground_labels"])
                self.assertIsNone(manifest["ground_layer"])

    def test_rectangular_octagon_outline_diagonals_are_45_degrees(self) -> None:
        vertices = _octagon_vertices(half_width_um=150.0, half_height_um=90.0)

        diagonal_edges = [
            (vertices[1], vertices[2]),
            (vertices[3], vertices[4]),
            (vertices[5], vertices[6]),
            (vertices[7], vertices[0]),
        ]
        for start, end in diagonal_edges:
            dx = abs(float(end[0]) - float(start[0]))
            dy = abs(float(end[1]) - float(start[1]))
            self.assertAlmostEqual(dx, dy, delta=1.0e-9)

    def test_rectangular_open_octagon_path_diagonals_are_45_degrees(self) -> None:
        points = _open_octagon_path(
            "left",
            half_width_um=150.0,
            half_height_um=90.0,
            direction="down",
            center_x_um=0.0,
            terminal_y_span_um=80.0,
        )

        diagonal_edges = [
            (points[1], points[2]),
            (points[3], points[4]),
            (points[5], points[6]),
            (points[7], points[8]),
        ]
        for start, end in diagonal_edges:
            dx = abs(float(end[0]) - float(start[0]))
            dy = abs(float(end[1]) - float(start[1]))
            self.assertAlmostEqual(dx, dy, delta=1.0e-9)

    def test_single_turn_winding_angle_check_allows_only_terminal_90_degree_interfaces(self) -> None:
        cfg = default_run_config("1t1t")
        result = run_transformer_gdstk_checks(
            geometry=cfg.bounds.midpoint(),
            run_config=cfg,
        )

        self.assertEqual(result.errors, tuple())
        self.assertEqual(result.metrics["primary_winding_centerline_internal_turn_count"], 8)
        self.assertEqual(result.metrics["secondary_winding_centerline_internal_turn_count"], 8)
        self.assertEqual(result.metrics["primary_winding_centerline_terminal_interface_count"], 2)
        self.assertEqual(result.metrics["secondary_winding_centerline_terminal_interface_count"], 2)
        self.assertEqual(result.metrics["primary_winding_centerline_diagonal_segment_count"], 4)
        self.assertEqual(result.metrics["secondary_winding_centerline_diagonal_segment_count"], 4)
        self.assertAlmostEqual(result.metrics["primary_winding_centerline_min_internal_angle_deg"], 135.0)
        self.assertAlmostEqual(result.metrics["primary_winding_centerline_max_internal_angle_deg"], 135.0)
        self.assertAlmostEqual(result.metrics["secondary_winding_centerline_min_internal_angle_deg"], 135.0)
        self.assertAlmostEqual(result.metrics["secondary_winding_centerline_max_internal_angle_deg"], 135.0)
        self.assertAlmostEqual(result.metrics["primary_winding_centerline_min_terminal_angle_deg"], 90.0)
        self.assertAlmostEqual(result.metrics["primary_winding_centerline_max_terminal_angle_deg"], 90.0)
        self.assertAlmostEqual(result.metrics["secondary_winding_centerline_min_terminal_angle_deg"], 90.0)
        self.assertAlmostEqual(result.metrics["secondary_winding_centerline_max_terminal_angle_deg"], 90.0)

    def test_two_turn_winding_angle_check_records_octagon_and_bridge_route_evidence(self) -> None:
        cfg = default_run_config("2t2t")
        result = run_transformer_gdstk_checks(
            geometry=cfg.bounds.midpoint(),
            run_config=cfg,
        )

        angle_related_errors = [
            error
            for error in result.errors
            if "angle" in error or "45 deg" in error or "winding" in error or "octagon" in error
        ]
        self.assertEqual(angle_related_errors, [])
        self.assertNotIn("primary_winding_angle_check_skipped", result.metrics)
        self.assertNotIn("secondary_winding_angle_check_skipped", result.metrics)
        for prefix in ("primary", "secondary"):
            self.assertEqual(result.metrics[f"{prefix}_winding_centerline_template"], "two_turn_octagon_rings_plus_45_routes")
            self.assertEqual(result.metrics[f"{prefix}_winding_centerline_internal_turn_count"], 16)
            self.assertEqual(result.metrics[f"{prefix}_winding_centerline_terminal_interface_count"], 3)
            self.assertEqual(result.metrics[f"{prefix}_winding_centerline_diagonal_segment_count"], 8)
            self.assertEqual(result.metrics[f"{prefix}_bridge_route_count"], 2)
            self.assertEqual(result.metrics[f"{prefix}_bridge_route_diagonal_segment_count"], 2)
            self.assertAlmostEqual(result.metrics[f"{prefix}_winding_centerline_min_internal_angle_deg"], 135.0)
            self.assertAlmostEqual(result.metrics[f"{prefix}_winding_centerline_max_internal_angle_deg"], 135.0)
            self.assertAlmostEqual(result.metrics[f"{prefix}_winding_centerline_min_terminal_angle_deg"], 90.0)
            self.assertAlmostEqual(result.metrics[f"{prefix}_winding_centerline_max_terminal_angle_deg"], 90.0)
            self.assertAlmostEqual(result.metrics[f"{prefix}_bridge_route_min_bend_angle_deg"], 135.0)
            self.assertAlmostEqual(result.metrics[f"{prefix}_bridge_route_max_bend_angle_deg"], 135.0)

    def test_layout_export_places_signal_port_labels_on_pin_layers_for_cadence_pins(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(cfg, bounds=replace(cfg.bounds, shield=replace(cfg.bounds.shield, enabled=False)))
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
                validate_geometry=False,
            )
            manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["cadence_pin_purpose"], 51)

            lib = gdstk.read_gds(str(layout.gds_path))
            labels = {
                label.text: (int(label.layer), int(label.texttype))
                for cell in lib.cells
                for label in cell.labels
            }
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            primary_pin_pair = proc_info.preferred_pin_pair_for_layer(cfg.emx.ap_layer)
            secondary_pin_pair = proc_info.preferred_pin_pair_for_layer(cfg.emx.m9_layer)

            self.assertEqual(labels["P001"], (int(primary_pin_pair.layer), int(primary_pin_pair.datatype)))
            self.assertEqual(labels["P002"], (int(primary_pin_pair.layer), int(primary_pin_pair.datatype)))
            self.assertEqual(labels["P003"], (int(secondary_pin_pair.layer), int(secondary_pin_pair.datatype)))
            self.assertEqual(labels["P004"], (int(secondary_pin_pair.layer), int(secondary_pin_pair.datatype)))

    def test_2t2t_export_uses_proc_datatypes_for_multilayer_geometry(self) -> None:
        cfg = default_run_config("2t2t")
        cfg = replace(cfg, bounds=replace(cfg.bounds, shield=replace(cfg.bounds.shield, enabled=False)))
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
                validate_geometry=False,
            )

            lib = gdstk.read_gds(str(layout.gds_path))
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            expected_datatypes = {
                int(proc_info.preferred_draw_pair_for_layer(cfg.emx.ap_layer).layer): int(proc_info.preferred_draw_pair_for_layer(cfg.emx.ap_layer).datatype),
                int(proc_info.preferred_draw_pair_for_layer(cfg.emx.m9_layer).layer): int(proc_info.preferred_draw_pair_for_layer(cfg.emx.m9_layer).datatype),
                int(proc_info.preferred_draw_pair_for_layer(cfg.emx.secondary_bridge_layer).layer): int(proc_info.preferred_draw_pair_for_layer(cfg.emx.secondary_bridge_layer).datatype),
                int(proc_info.preferred_draw_pair_for_layer(cfg.emx.primary_bridge_layer).layer): int(proc_info.preferred_draw_pair_for_layer(cfg.emx.primary_bridge_layer).datatype),
                int(proc_info.preferred_draw_pair_for_layer(cfg.emx.secondary_bridge_via_layer).layer): int(proc_info.preferred_draw_pair_for_layer(cfg.emx.secondary_bridge_via_layer).datatype),
                int(proc_info.preferred_draw_pair_for_layer(cfg.emx.primary_bridge_via_layer).layer): int(proc_info.preferred_draw_pair_for_layer(cfg.emx.primary_bridge_via_layer).datatype),
            }

            polygon_datatypes_by_layer: dict[int, set[int]] = {}
            for cell in lib.cells:
                for poly in cell.polygons:
                    polygon_datatypes_by_layer.setdefault(int(poly.layer), set()).add(int(poly.datatype))

            for layer, expected_datatype in expected_datatypes.items():
                self.assertIn(layer, polygon_datatypes_by_layer)
                self.assertEqual(
                    polygon_datatypes_by_layer[layer],
                    {expected_datatype},
                    msg=f"layer {layer} should only use proc datatype {expected_datatype}",
                )

    def test_2t2t_export_rejects_terminal_span_exceeding_straight_side_limit(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        invalid = replace(
            geom,
            primary=self._replace_inductor(
                geom.primary,
                outer_height_um=160.0,
                terminal_y_span_um=100.0,
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "straight-side span limit"):
                export_transformer_layout(
                    geometry=invalid,
                    run_config=cfg,
                    out_dir=Path(tmpdir),
                    validate_geometry=False,
                )

    def test_export_validate_geometry_rejects_spec_failure_before_layout_write(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        invalid = replace(
            geom,
            secondary=self._replace_inductor(
                geom.secondary,
                terminal_y_span_um=max(geom.secondary.outer_width_um, geom.secondary.outer_height_um) + 1.0,
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "geometry validation failed"):
                export_transformer_layout(
                    geometry=invalid,
                    run_config=cfg,
                    out_dir=Path(tmpdir),
                )

    def test_export_validate_geometry_rejects_gdstk_failure_before_layout_write(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        invalid = replace(
            geom,
            primary=self._replace_inductor(
                geom.primary,
                trace_width_um=8.0,
                spacing_um=2.0,
                terminal_y_span_um=20.0,
            ),
            secondary=self._replace_inductor(
                geom.secondary,
                trace_width_um=8.0,
                spacing_um=2.0,
                terminal_y_span_um=20.0,
            ),
            offset_um=-40.0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "gdstk geometry check failed"):
                export_transformer_layout(
                    geometry=invalid,
                    run_config=cfg,
                    out_dir=Path(tmpdir),
                )

    def test_layout_export_adds_shield_ring_and_ground_labels_when_enabled(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(cfg.emx, shield_layer=35, port_mode="single_ended_shield_grounded"),
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, enabled=True, width_um=12.0),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
                validate_geometry=False,
            )
            manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            self.assertEqual(manifest["ground_layer"], proc_info.preferred_pin_pair_for_layer(35).layer)
            self.assertTrue(all(port["ground_labels"] for port in manifest["ports"]))

            lib = gdstk.read_gds(str(layout.gds_path))
            layers = {int(poly.layer) for cell in lib.cells for poly in cell.polygons}
            self.assertIn(proc_info.preferred_draw_pair_for_layer(35).layer, layers)
            self.assertIn(proc_info.preferred_pin_pair_for_layer(35).layer, layers)
            labels = {label.text: (label.origin[0], label.origin[1]) for cell in lib.cells for label in cell.labels}
            shield_boxes = [
                poly.bounding_box()
                for cell in lib.cells
                for poly in cell.polygons
                if int(poly.layer) == proc_info.preferred_draw_pair_for_layer(35).layer and poly.bounding_box() is not None
            ]
            outer_min_y = min(float(box[0][1]) for box in shield_boxes)
            outer_max_y = max(float(box[1][1]) for box in shield_boxes)
            outer_min_x = min(float(box[0][0]) for box in shield_boxes)
            outer_max_x = max(float(box[1][0]) for box in shield_boxes)
            for name in ("P001", "P002", "P003", "P004"):
                self.assertIn(name, labels)
            for name in ("P001_G", "P002_G", "P003_G", "P004_G"):
                self.assertIn(name, labels)
            self.assertAlmostEqual(labels["P001_G"][1], labels["P001"][1], delta=1e-9)
            self.assertAlmostEqual(labels["P002_G"][1], labels["P002"][1], delta=1e-9)
            self.assertAlmostEqual(labels["P003_G"][1], labels["P003"][1], delta=1e-9)
            self.assertAlmostEqual(labels["P004_G"][1], labels["P004"][1], delta=1e-9)
            self.assertAlmostEqual(labels["P001_G"][0], labels["P001"][0], delta=1e-9)
            self.assertAlmostEqual(labels["P002_G"][0], labels["P002"][0], delta=1e-9)
            self.assertAlmostEqual(labels["P003_G"][0], labels["P003"][0], delta=1e-9)
            self.assertAlmostEqual(labels["P004_G"][0], labels["P004"][0], delta=1e-9)

            primary_draw_layer = proc_info.preferred_draw_pair_for_layer(cfg.emx.ap_layer).layer
            secondary_draw_layer = proc_info.preferred_draw_pair_for_layer(cfg.emx.m9_layer).layer
            self.assertAlmostEqual(
                min(
                    float(poly.bounding_box()[0][0])
                    for cell in lib.cells
                    for poly in cell.polygons
                    if int(poly.layer) == primary_draw_layer and poly.bounding_box() is not None
                ),
                outer_min_x,
                delta=1e-9,
            )
            self.assertAlmostEqual(
                max(
                    float(poly.bounding_box()[1][0])
                    for cell in lib.cells
                    for poly in cell.polygons
                    if int(poly.layer) == secondary_draw_layer and poly.bounding_box() is not None
                ),
                outer_max_x,
                delta=1e-9,
            )

    def test_layout_export_rejects_signal_body_overlap_with_shield_ring(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(cfg.emx, shield_layer=35, port_mode="single_ended_shield_grounded"),
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, enabled=True, margin_um=100.0, width_um=10.0),
            ),
        )
        geom = cfg.bounds.midpoint()
        geom = replace(
            geom,
            primary=replace(
                geom.primary,
                geometry=replace(
                    geom.primary.geometry,
                    outer_width_um=162.06620216417537,
                    outer_height_um=352.1085060885809,
                    trace_width_um=3.852510756382424,
                    spacing_um=8.0,
                    terminal_y_span_um=43.6887201068222,
                    feed_extension_um=198.7823510499785,
                ),
            ),
            secondary=replace(
                geom.secondary,
                geometry=replace(
                    geom.secondary.geometry,
                    outer_width_um=485.8910447044955,
                    outer_height_um=175.4520035352828,
                    trace_width_um=3.0867966780050606,
                    spacing_um=8.0,
                    terminal_y_span_um=38.27277482457716,
                    feed_extension_um=177.6717130647376,
                ),
            ),
            offset_um=-46.22821080743601,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "secondary signal-to-shield clearance violation"):
                export_transformer_layout(
                    geometry=geom,
                    run_config=cfg,
                    out_dir=Path(tmpdir),
                )
            audit = json.loads((Path(tmpdir) / "signal_shield_clearance_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "reject_signal_to_shield_clearance")
            self.assertGreater(audit["signal_shield_clearance_violation_area_um2"], 0.0)

    def test_layout_export_writes_signal_shield_clearance_audit_sidecar(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(cfg, emx=replace(cfg.emx, port_mode="single_ended_shield_grounded"))

        with tempfile.TemporaryDirectory() as tmpdir:
            export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
            )

            audit = json.loads((Path(tmpdir) / "signal_shield_clearance_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "pass_signal_to_shield_clearance")
            self.assertEqual(audit["direct_signal_shield_overlap_area_um2"], 0.0)
            self.assertEqual(audit["signal_shield_clearance_violation_area_um2"], 0.0)
            self.assertEqual(len(audit["records"]), 2)

    def test_layout_export_uses_trace_scaled_pin_rectangles_for_signal_and_ground(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(cfg.emx, shield_layer=35, port_mode="single_ended_shield_grounded"),
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, enabled=True, width_um=50.0),
                primary=replace(cfg.bounds.primary, trace_width_um=(12.0, 12.0)),
                secondary=replace(cfg.bounds.secondary, trace_width_um=(8.0, 8.0)),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
                validate_geometry=False,
            )
            lib = gdstk.read_gds(str(layout.gds_path))
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            primary_pin_layer = proc_info.preferred_pin_pair_for_layer(cfg.emx.ap_layer).layer
            secondary_pin_layer = proc_info.preferred_pin_pair_for_layer(cfg.emx.m9_layer).layer
            shield_pin_layer = proc_info.preferred_pin_pair_for_layer(cfg.emx.shield_layer).layer
            labels = {label.text: (float(label.origin[0]), float(label.origin[1])) for cell in lib.cells for label in cell.labels}

            expected_sizes = {
                "P001": (0.5, 12.0, primary_pin_layer),
                "P002": (0.5, 12.0, primary_pin_layer),
                "P001_G": (0.5, 12.0, shield_pin_layer),
                "P002_G": (0.5, 12.0, shield_pin_layer),
                "P003": (0.5, 8.0, secondary_pin_layer),
                "P004": (0.5, 8.0, secondary_pin_layer),
                "P003_G": (0.5, 8.0, shield_pin_layer),
                "P004_G": (0.5, 8.0, shield_pin_layer),
            }
            for name, (expected_w, expected_h, layer) in expected_sizes.items():
                bbox = self._find_polygon_box_containing_point(lib, layer, labels[name])
                width = bbox[1][0] - bbox[0][0]
                height = bbox[1][1] - bbox[0][1]
                self.assertAlmostEqual(width, expected_w, delta=1e-9, msg=name)
                self.assertAlmostEqual(height, expected_h, delta=1e-9, msg=name)
            self.assertAlmostEqual(labels["P001_G"][0], labels["P001"][0], delta=1e-9)
            self.assertAlmostEqual(labels["P002_G"][0], labels["P002"][0], delta=1e-9)
            self.assertAlmostEqual(labels["P003_G"][0], labels["P003"][0], delta=1e-9)
            self.assertAlmostEqual(labels["P004_G"][0], labels["P004"][0], delta=1e-9)

    def test_layout_export_locks_shield_sides_to_feed_start_reach(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(cfg.emx, shield_layer=35),
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, enabled=True, margin_um=10.0, width_um=12.0),
                primary=replace(cfg.bounds.primary, feed_extension_um=(60.0, 60.0)),
                secondary=replace(cfg.bounds.secondary, feed_extension_um=(90.0, 90.0)),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
            )
            lib = gdstk.read_gds(str(layout.gds_path))
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            shield_boxes = [
                poly.bounding_box()
                for cell in lib.cells
                for poly in cell.polygons
                if int(poly.layer) == proc_info.preferred_draw_pair_for_layer(35).layer and poly.bounding_box() is not None
            ]
            outer_min_x = min(float(box[0][0]) for box in shield_boxes)
            outer_max_x = max(float(box[1][0]) for box in shield_boxes)
            geom = cfg.bounds.midpoint()
            expected_left_inner_x = -0.5 * float(geom.primary.outer_width_um) - float(geom.primary.feed_extension_um)
            expected_right_inner_x = (
                float(geom.offset_um)
                + 0.5 * float(geom.secondary.outer_width_um)
                + float(geom.secondary.feed_extension_um)
            )
            largest_coil_height_um = max(float(geom.primary.outer_height_um), float(geom.secondary.outer_height_um))
            expected_inner_height_um = largest_coil_height_um + 2.0 * float(cfg.bounds.shield.margin_um)
            shield_width_um = float(cfg.bounds.shield.width_um)
            outer_min_y = min(float(box[0][1]) for box in shield_boxes)
            outer_max_y = max(float(box[1][1]) for box in shield_boxes)
            self.assertAlmostEqual(outer_min_x + shield_width_um, expected_left_inner_x, delta=1e-6)
            self.assertAlmostEqual(outer_max_x - shield_width_um, expected_right_inner_x, delta=1e-6)
            self.assertAlmostEqual((outer_max_y - outer_min_y) - 2.0 * shield_width_um, expected_inner_height_um, delta=1e-6)
    def test_layout_export_keeps_shield_but_not_ground_labels_in_differential_port_mode(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(cfg.emx, shield_layer=35, port_mode="differential_pairs"),
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, enabled=True, width_um=12.0),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
            )
            manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["ports"]), 2)
            self.assertEqual(manifest["ports"][0]["signal_labels"], ["P001"])
            self.assertEqual(manifest["ports"][0]["ground_labels"], ["P002"])
            self.assertEqual(manifest["ports"][1]["signal_labels"], ["P003"])
            self.assertEqual(manifest["ports"][1]["ground_labels"], ["P004"])
            labels = {label.text for cell in gdstk.read_gds(str(layout.gds_path)).cells for label in cell.labels}
            self.assertNotIn("P001_G", labels)
            self.assertNotIn("P004_G", labels)
    def test_layout_export_rejects_shield_ground_port_mode_without_shield(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(cfg.emx, port_mode="single_ended_shield_grounded"),
            bounds=replace(cfg.bounds, shield=replace(cfg.bounds.shield, enabled=False)),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "single_ended_shield_grounded"):
                export_transformer_layout(
                    geometry=cfg.bounds.midpoint(),
                    run_config=cfg,
                    out_dir=Path(tmpdir),
                )
    def test_layout_export_rejects_enabled_shield_without_layer(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(cfg.emx, shield_layer=None),
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, enabled=True, width_um=12.0),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "shield_layer"):
                export_transformer_layout(
                    geometry=cfg.bounds.midpoint(),
                    run_config=cfg,
                    out_dir=Path(tmpdir),
                )
    def test_center_tapped_inductor_can_be_mirrored_into_the_secondary(self) -> None:
        base_inductor = default_run_config("2t2t").bounds.midpoint().primary
        left = InductorLayoutSpec(
            geometry=self._replace_inductor(
                base_inductor,
                outer_width_um=276.0,
                outer_height_um=264.0,
                trace_width_um=14.0,
                spacing_um=21.0,
                terminal_y_span_um=68.0,
                feed_extension_um=42.0,
                turns=2,
                center_tap=True,
                bridge_layer=85,
            ),
            center_x_um=0.0,
            center_y_um=0.0,
            bridge_offset_y_um=-4.0,
            bridge_anchor_gap_cap_um=None,
            metal_layer=74,
            bridge_layer=85,
            mirror_x=False,
        )
        right = replace(left, center_x_um=18.0, metal_layer=139, mirror_x=True)

        left_lib = gdstk.Library(unit=1e-6, precision=1e-9)
        left_cell = left_lib.new_cell("LEFT")
        right_lib = gdstk.Library(unit=1e-6, precision=1e-9)
        right_cell = right_lib.new_cell("RIGHT")

        left_terms = _build_center_tapped_inductor(left_cell, left)
        right_terms = _build_center_tapped_inductor(right_cell, right)

        self.assertLess(left_terms.top[0], 0.0)
        self.assertLess(left_terms.bottom[0], 0.0)
        self.assertGreater(right_terms.top[0], 0.0)
        self.assertGreater(right_terms.bottom[0], 0.0)
        self.assertAlmostEqual(left_terms.top[1], right_terms.top[1], delta=1e-9)
        self.assertAlmostEqual(left_terms.bottom[1], right_terms.bottom[1], delta=1e-9)
        self.assertAlmostEqual(right_terms.top[0] - 18.0, -(left_terms.top[0]), delta=1e-9)
        self.assertAlmostEqual(right_terms.center_tap[0] - 18.0, -(left_terms.center_tap[0]), delta=1e-9)

    def test_center_tapped_inductor_geometry_uses_explicit_target_x_when_mirrored(self) -> None:
        base_inductor = default_run_config("2t2t").bounds.midpoint().secondary
        spec = InductorLayoutSpec(
            geometry=self._replace_inductor(
                base_inductor,
                outer_width_um=220.0,
                outer_height_um=180.0,
                trace_width_um=10.0,
                spacing_um=12.0,
                terminal_y_span_um=56.0,
                feed_extension_um=35.0,
                turns=2,
                center_tap=True,
                bridge_layer=58,
            ),
            center_x_um=40.0,
            center_y_um=0.0,
            bridge_offset_y_um=0.0,
            bridge_anchor_gap_cap_um=None,
            metal_layer=39,
            bridge_layer=58,
            mirror_x=True,
        )
        lib = gdstk.Library(unit=1e-6, precision=1e-9)
        cell = lib.new_cell("RIGHT_TARGET")

        terminals = _build_center_tapped_inductor(
            cell,
            spec,
            include_center_tap_feed=True,
            center_tap_target_x_um=185.0,
        )

        self.assertIsNotNone(terminals.center_tap)
        self.assertAlmostEqual(terminals.center_tap[0], 185.0, delta=1e-9)

    def test_two_turn_windings_keep_the_same_circulation_sense(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        _ap_points, ap_terminals = _build_winding(
            side="left",
            inductor=geom.primary_inductor_spec(),
            center_x_um=0.0,
        )
        _m9_points, m9_terminals = _build_winding(
            side="right",
            inductor=geom.secondary_inductor_spec(),
            center_x_um=geom.offset_um,
        )

        self.assertGreater(ap_terminals[0][1], 0.0)
        self.assertLess(ap_terminals[1][1], 0.0)
        self.assertGreater(m9_terminals[0][1], 0.0)
        self.assertLess(m9_terminals[1][1], 0.0)
    def test_one_turn_terminal_span_controls_terminal_separation(self) -> None:
        cfg = default_run_config("1t1t")
        geom = cfg.bounds.midpoint()

        _, small_primary_terms = _build_winding(
            side="left",
            inductor=self._replace_inductor(geom.primary_inductor_spec(), terminal_y_span_um=40.0),
            center_x_um=0.0,
        )
        _, large_primary_terms = _build_winding(
            side="left",
            inductor=self._replace_inductor(geom.primary_inductor_spec(), terminal_y_span_um=100.0),
            center_x_um=0.0,
        )
        self.assertAlmostEqual(small_primary_terms[0][1], 20.0, delta=1e-9)
        self.assertAlmostEqual(small_primary_terms[1][1], -20.0, delta=1e-9)
        self.assertAlmostEqual(large_primary_terms[0][1], 50.0, delta=1e-9)
        self.assertAlmostEqual(large_primary_terms[1][1], -50.0, delta=1e-9)

        _, secondary_terms = _build_winding(
            side="right",
            inductor=self._replace_inductor(geom.secondary_inductor_spec(), terminal_y_span_um=60.0),
            center_x_um=geom.offset_um,
        )
        self.assertAlmostEqual(secondary_terms[0][1], 30.0, delta=1e-9)
        self.assertAlmostEqual(secondary_terms[1][1], -30.0, delta=1e-9)
    def test_one_turn_center_tap_layout_exports(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            bounds=replace(
                cfg.bounds,
                primary=replace(cfg.bounds.primary, turns=1, center_tap=True),
                secondary=replace(cfg.bounds.secondary, turns=1, center_tap=True),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
            )
            self.assertTrue(layout.gds_path.exists())
            lib = gdstk.read_gds(str(layout.gds_path))
            labels = [label.text for cell in lib.cells for label in cell.labels]
            self.assertIn("PRI_CT", labels)
            self.assertIn("SEC_CT", labels)
    def test_vdd_bar_exports_from_center_tap_feed(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, margin_um=10.0),
                primary=replace(
                    cfg.bounds.primary,
                    turns=1,
                    center_tap=True,
                    vdd_bar=replace(cfg.bounds.primary.vdd_bar, enabled=True, bar_layer=139, route_layer=139, route_via_layer=85),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
            )
            lib = gdstk.read_gds(str(layout.gds_path))
            label_points = {label.text: (float(label.origin[0]), float(label.origin[1])) for cell in lib.cells for label in cell.labels}
            labels = set(label_points)
            layers = {int(poly.layer) for cell in lib.cells for poly in cell.polygons}
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            vdd_draw_layer = proc_info.preferred_draw_pair_for_layer(139).layer
            shield_draw_layer = proc_info.preferred_draw_pair_for_layer(cfg.emx.shield_layer).layer
            self.assertIn("PRI_VDD", labels)
            self.assertIn(vdd_draw_layer, layers)
            self.assertIn(85, layers)
            vdd_boxes = [
                poly.bounding_box()
                for cell in lib.cells
                for poly in cell.polygons
                if int(poly.layer) == vdd_draw_layer and poly.bounding_box() is not None
            ]
            self.assertTrue(vdd_boxes)
            shield_boxes = [
                poly.bounding_box()
                for cell in lib.cells
                for poly in cell.polygons
                if int(poly.layer) == shield_draw_layer and poly.bounding_box() is not None
            ]
            self.assertTrue(shield_boxes)
            bar_width_um = float(cfg.bounds.primary.vdd_bar.width_um)
            bar_boxes = [
                box
                for box in vdd_boxes
                if abs((float(box[1][0]) - float(box[0][0])) - bar_width_um) <= 1e-9
            ]
            self.assertTrue(bar_boxes)
            right_shield_outer_x = max(float(box[1][0]) for box in shield_boxes)
            expected_bar_inner_x = right_shield_outer_x
            self.assertTrue(any(abs(float(box[0][0]) - expected_bar_inner_x) <= 1e-9 for box in bar_boxes))
            self.assertAlmostEqual(label_points["PRI_CT"][0], expected_bar_inner_x, delta=1e-9)
            bar_height_um = max(
                float(box[1][1]) - float(box[0][1])
                for box in vdd_boxes
                if abs((float(box[1][0]) - float(box[0][0])) - bar_width_um) <= 1e-9
            )
            geom = cfg.bounds.midpoint()
            expected_height_um = max(
                float(geom.primary.outer_height_um),
                float(geom.secondary.outer_height_um),
            ) + 2.0 * float(cfg.bounds.shield.margin_um)
            self.assertAlmostEqual(bar_height_um, expected_height_um, delta=1e-9)
    def test_vdd_bar_uses_configured_width_when_present(self) -> None:
        cfg = default_run_config("1t1t")
        configured_width_um = 18.0
        cfg = replace(
            cfg,
            bounds=replace(
                cfg.bounds,
                primary=replace(
                    cfg.bounds.primary,
                    turns=1,
                    center_tap=True,
                    vdd_bar=replace(
                        cfg.bounds.primary.vdd_bar,
                        enabled=True,
                        width_um=configured_width_um,
                        bar_layer=139,
                        route_layer=139,
                        route_via_layer=85,
                    ),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
            )
            lib = gdstk.read_gds(str(layout.gds_path))
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            vdd_draw_layer = proc_info.preferred_draw_pair_for_layer(139).layer
            geom = cfg.bounds.midpoint()
            expected_height_um = max(
                float(geom.primary.outer_height_um),
                float(geom.secondary.outer_height_um),
            ) + 2.0 * float(cfg.bounds.shield.margin_um)
            vdd_boxes = [
                poly.bounding_box()
                for cell in lib.cells
                for poly in cell.polygons
                if int(poly.layer) == vdd_draw_layer and poly.bounding_box() is not None
            ]
            self.assertTrue(vdd_boxes)
            bar_widths = [
                float(box[1][0]) - float(box[0][0])
                for box in vdd_boxes
                if abs((float(box[1][1]) - float(box[0][1])) - expected_height_um) <= 1e-9
            ]
            self.assertTrue(bar_widths)
            self.assertTrue(any(abs(width - configured_width_um) <= 1e-9 for width in bar_widths))
    def test_vdd_bar_uses_configured_offset_beyond_shield(self) -> None:
        cfg = default_run_config("1t1t")
        configured_offset_um = 6.0
        cfg = replace(
            cfg,
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, margin_um=10.0),
                primary=replace(
                    cfg.bounds.primary,
                    turns=1,
                    center_tap=True,
                    vdd_bar=replace(
                        cfg.bounds.primary.vdd_bar,
                        enabled=True,
                        bar_layer=139,
                        route_layer=139,
                        route_via_layer=85,
                        offset_um=configured_offset_um,
                    ),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
            )
            lib = gdstk.read_gds(str(layout.gds_path))
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            vdd_draw_layer = proc_info.preferred_draw_pair_for_layer(139).layer
            shield_draw_layer = proc_info.preferred_draw_pair_for_layer(cfg.emx.shield_layer).layer
            bar_width_um = float(cfg.bounds.primary.vdd_bar.width_um)
            vdd_boxes = [
                poly.bounding_box()
                for cell in lib.cells
                for poly in cell.polygons
                if int(poly.layer) == vdd_draw_layer and poly.bounding_box() is not None
            ]
            shield_boxes = [
                poly.bounding_box()
                for cell in lib.cells
                for poly in cell.polygons
                if int(poly.layer) == shield_draw_layer and poly.bounding_box() is not None
            ]
            self.assertTrue(vdd_boxes)
            self.assertTrue(shield_boxes)
            bar_boxes = [
                box
                for box in vdd_boxes
                if abs((float(box[1][0]) - float(box[0][0])) - bar_width_um) <= 1e-9
            ]
            self.assertTrue(bar_boxes)
            right_shield_outer_x = max(float(box[1][0]) for box in shield_boxes)
            expected_bar_inner_x = right_shield_outer_x + configured_offset_um
            self.assertTrue(any(abs(float(box[0][0]) - expected_bar_inner_x) <= 1e-9 for box in bar_boxes))
    def test_2t2t_vdd_bars_stay_on_feed_sides(self) -> None:
        cfg = default_run_config("2t2t")
        cfg = replace(
            cfg,
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, margin_um=10.0),
                primary=replace(
                    cfg.bounds.primary,
                    center_tap=True,
                    vdd_bar=replace(
                        cfg.bounds.primary.vdd_bar,
                        enabled=True,
                        bar_layer=139,
                        route_layer=139,
                        route_via_layer=85,
                    ),
                ),
                secondary=replace(
                    cfg.bounds.secondary,
                    center_tap=True,
                    vdd_bar=replace(
                        cfg.bounds.secondary.vdd_bar,
                        enabled=True,
                        bar_layer=138,
                        route_layer=138,
                        route_via_layer=58,
                    ),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
                validate_geometry=False,
            )
            lib = gdstk.read_gds(str(layout.gds_path))
            label_points = {
                label.text: (float(label.origin[0]), float(label.origin[1]))
                for cell in lib.cells
                for label in cell.labels
            }
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            shield_draw_layer = proc_info.preferred_draw_pair_for_layer(cfg.emx.shield_layer).layer
            primary_vdd_draw_layer = proc_info.preferred_draw_pair_for_layer(139).layer
            secondary_vdd_draw_layer = proc_info.preferred_draw_pair_for_layer(138).layer
            primary_bar_width_um = float(cfg.bounds.primary.vdd_bar.width_um)
            secondary_bar_width_um = float(cfg.bounds.secondary.vdd_bar.width_um)

            shield_boxes = [
                poly.bounding_box()
                for cell in lib.cells
                for poly in cell.polygons
                if int(poly.layer) == shield_draw_layer and poly.bounding_box() is not None
            ]
            self.assertTrue(shield_boxes)
            left_shield_outer_x = min(float(box[0][0]) for box in shield_boxes)
            right_shield_outer_x = max(float(box[1][0]) for box in shield_boxes)

            primary_bar_boxes = [
                box
                for cell in lib.cells
                for poly in cell.polygons
                if int(poly.layer) == primary_vdd_draw_layer
                for box in [poly.bounding_box()]
                if box is not None and abs((float(box[1][0]) - float(box[0][0])) - primary_bar_width_um) <= 1e-9
            ]
            secondary_bar_boxes = [
                box
                for cell in lib.cells
                for poly in cell.polygons
                if int(poly.layer) == secondary_vdd_draw_layer
                for box in [poly.bounding_box()]
                if box is not None and abs((float(box[1][0]) - float(box[0][0])) - secondary_bar_width_um) <= 1e-9
            ]
            self.assertTrue(primary_bar_boxes)
            self.assertTrue(secondary_bar_boxes)
            self.assertTrue(any(abs(float(box[1][0]) - left_shield_outer_x) <= 1e-9 for box in primary_bar_boxes))
            self.assertTrue(any(abs(float(box[0][0]) - right_shield_outer_x) <= 1e-9 for box in secondary_bar_boxes))
            self.assertGreaterEqual(label_points["PRI_CT"][0], max(float(box[1][0]) for box in primary_bar_boxes))
            self.assertLessEqual(label_points["SEC_CT"][0], min(float(box[0][0]) for box in secondary_bar_boxes))
    def test_vdd_bar_adds_top_bottom_signal_and_ground_pin_markers_for_cadence_flow(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(cfg.emx, shield_layer=35, port_mode="single_ended_shield_grounded"),
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, enabled=True, width_um=50.0),
                primary=replace(
                    cfg.bounds.primary,
                    turns=1,
                    center_tap=True,
                    trace_width_um=(11.748, 11.748),
                    vdd_bar=replace(
                        cfg.bounds.primary.vdd_bar,
                        enabled=True,
                        width_um=11.748,
                        offset_um=12.0,
                        bar_layer=139,
                        route_layer=139,
                        route_via_layer=85,
                    ),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
            )
            lib = gdstk.read_gds(str(layout.gds_path))
            manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            primary_pin_layer = proc_info.preferred_pin_pair_for_layer(cfg.emx.ap_layer).layer
            shield_pin_layer = proc_info.preferred_pin_pair_for_layer(cfg.emx.shield_layer).layer
            labels = {label.text: (float(label.origin[0]), float(label.origin[1])) for cell in lib.cells for label in cell.labels}
            for name in ("PVDD_TOP", "PVDD_BOT", "PVDD_TOP_G", "PVDD_BOT_G"):
                self.assertIn(name, labels)
            self.assertEqual(
                [port["name"] for port in manifest["ports"]],
                ["P001", "P002", "P003", "P004", "PVDD_TOP", "PVDD_BOT"],
            )
            self.assertEqual(manifest["ports"][4]["ground_labels"], ["PVDD_TOP_G"])
            self.assertEqual(manifest["ports"][5]["ground_labels"], ["PVDD_BOT_G"])

            expected_sizes = {
                "PVDD_TOP": (11.748, 0.5, primary_pin_layer),
                "PVDD_BOT": (11.748, 0.5, primary_pin_layer),
                "PVDD_TOP_G": (11.748, 0.5, shield_pin_layer),
                "PVDD_BOT_G": (11.748, 0.5, shield_pin_layer),
            }
            for name, (expected_w, expected_h, layer) in expected_sizes.items():
                bbox = self._find_polygon_box_containing_point(lib, layer, labels[name])
                width = bbox[1][0] - bbox[0][0]
                height = bbox[1][1] - bbox[0][1]
                self.assertAlmostEqual(width, expected_w, delta=1e-9, msg=name)
                self.assertAlmostEqual(height, expected_h, delta=1e-9, msg=name)
            self.assertEqual(labels["PVDD_TOP_G"], labels["PVDD_TOP"])
            self.assertEqual(labels["PVDD_BOT_G"], labels["PVDD_BOT"])

    def test_dual_vdd_bars_export_eight_single_ended_ports(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(cfg.emx, shield_layer=35, port_mode="single_ended_shield_grounded"),
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, enabled=True, width_um=50.0),
                primary=replace(
                    cfg.bounds.primary,
                    turns=1,
                    center_tap=True,
                    vdd_bar=replace(
                        cfg.bounds.primary.vdd_bar,
                        enabled=True,
                        width_um=10.0,
                        offset_um=12.0,
                        bar_layer=139,
                        route_layer=139,
                        route_via_layer=85,
                    ),
                ),
                secondary=replace(
                    cfg.bounds.secondary,
                    turns=1,
                    center_tap=True,
                    vdd_bar=replace(
                        cfg.bounds.secondary.vdd_bar,
                        enabled=True,
                        width_um=10.0,
                        offset_um=12.0,
                        bar_layer=138,
                        route_layer=138,
                        route_via_layer=58,
                    ),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
            )
            lib = gdstk.read_gds(str(layout.gds_path))
            labels = {label.text for cell in lib.cells for label in cell.labels}
            manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))

            for name in ("PVDD_TOP", "PVDD_BOT", "PVDD_TOP_G", "PVDD_BOT_G", "SVDD_TOP", "SVDD_BOT", "SVDD_TOP_G", "SVDD_BOT_G"):
                self.assertIn(name, labels)
            self.assertEqual(
                [port["name"] for port in manifest["ports"]],
                ["P001", "P002", "P003", "P004", "PVDD_TOP", "PVDD_BOT", "SVDD_TOP", "SVDD_BOT"],
            )
            self.assertEqual(manifest["ports"][4]["ground_labels"], ["PVDD_TOP_G"])
            self.assertEqual(manifest["ports"][5]["ground_labels"], ["PVDD_BOT_G"])
            self.assertEqual(manifest["ports"][6]["ground_labels"], ["SVDD_TOP_G"])
            self.assertEqual(manifest["ports"][7]["ground_labels"], ["SVDD_BOT_G"])

    def test_two_turn_no_center_tap_keeps_bridged_geometry(self) -> None:
        cfg = default_run_config("2t2t")
        cfg = replace(
            cfg,
            bounds=replace(
                cfg.bounds,
                primary=replace(cfg.bounds.primary, center_tap=False),
                secondary=replace(cfg.bounds.secondary, center_tap=False),
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
                validate_geometry=False,
            )
            lib = gdstk.read_gds(str(layout.gds_path))
            layers = {int(poly.layer) for cell in lib.cells for poly in cell.polygons}
            labels = [label.text for cell in lib.cells for label in cell.labels]
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            self.assertIn(proc_info.preferred_draw_pair_for_layer(cfg.emx.primary_bridge_layer).layer, layers)
            self.assertIn(proc_info.preferred_draw_pair_for_layer(cfg.emx.secondary_bridge_layer).layer, layers)
            self.assertNotIn("PRI_CT", labels)
            self.assertNotIn("SEC_CT", labels)
    def test_primary_and_secondary_bridge_layers_can_differ(self) -> None:
        cfg = default_run_config("2t2t")
        cfg = replace(
            cfg,
            emx=replace(
                cfg.emx,
                primary_bridge_layer=85,
                primary_bridge_via_layer=84,
                secondary_bridge_layer=86,
                secondary_bridge_via_layer=87,
            ),
            bounds=replace(
                cfg.bounds,
                primary=replace(cfg.bounds.primary, bridge_layer=85, bridge_via_layer=84),
                secondary=replace(cfg.bounds.secondary, bridge_layer=86, bridge_via_layer=87),
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            layout = export_transformer_layout(
                geometry=cfg.bounds.midpoint(),
                run_config=cfg,
                out_dir=Path(tmpdir),
            )
            lib = gdstk.read_gds(str(layout.gds_path))
            layers = {int(poly.layer) for cell in lib.cells for poly in cell.polygons}
            manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
            proc_info = parse_proc_file(cfg.emx.emx_process_file)
            expected_layer_order = [
                proc_info.preferred_draw_pair_for_layer(cfg.emx.m5_layer).layer,
                87,
                86,
                58,
                proc_info.preferred_draw_pair_for_layer(138).layer,
                proc_info.preferred_draw_pair_for_layer(cfg.emx.m9_layer).layer,
                84,
                85,
                proc_info.preferred_draw_pair_for_layer(cfg.emx.ap_layer).layer,
                proc_info.preferred_pin_pair_for_layer(cfg.emx.ap_layer).layer,
                proc_info.preferred_pin_pair_for_layer(cfg.emx.m9_layer).layer,
                proc_info.preferred_pin_pair_for_layer(cfg.emx.m5_layer).layer,
            ]

            self.assertIn(85, layers)
            self.assertIn(86, layers)
            self.assertIn(84, layers)
            self.assertIn(87, layers)
            self.assertEqual(manifest["layer_draw_order"], expected_layer_order)

    def test_bridge_section_ratios_shrink_bridge_and_via_pads(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        default_primary = geom.primary
        tuned_primary = replace(
            default_primary,
            fixed=replace(
                default_primary.fixed,
                bridge_section=replace(
                    default_primary.bridge_section,
                    pad_width_ratio=0.55,
                    pad_height_ratio=0.35,
                    via_size_ratio=0.45,
                ),
            ),
        )

        def _bundle_for(inductor: InductorSpec) -> CenterTappedInductorGeometry:
            return _build_center_tapped_inductor_geometry(
                InductorLayoutSpec(
                    geometry=inductor,
                    center_x_um=0.0,
                    center_y_um=0.0,
                    bridge_offset_y_um=0.0,
                    bridge_anchor_gap_cap_um=None,
                    metal_layer=139,
                    bridge_layer=int(inductor.bridge_layer),
                    bridge_via_layer=inductor.bridge_via_layer,
                    bridge_lower_layer=inductor.bridge_lower_layer,
                    bridge_lower_via_layer=inductor.bridge_lower_via_layer,
                    mirror_x=False,
                ),
                include_center_tap_feed=inductor.center_tap,
            )

        def _stage_size(bundle: CenterTappedInductorGeometry, stack_name: str, stage_name: str) -> tuple[float, float]:
            for stack in bundle.bridge_endpoint_stacks:
                if stack.name != stack_name:
                    continue
                for stage in stack.stages:
                    if stage.name != stage_name:
                        continue
                    bbox = stage.polygons[0].bounding_box()
                    self.assertIsNotNone(bbox)
                    (min_x, min_y), (max_x, max_y) = bbox
                    return (float(max_x - min_x), float(max_y - min_y))
            raise AssertionError(f"Missing stage {stack_name}/{stage_name}")

        default_bundle = _bundle_for(default_primary)
        tuned_bundle = _bundle_for(tuned_primary)

        default_bridge_size = _stage_size(default_bundle, "outer", "bridge_pad")
        tuned_bridge_size = _stage_size(tuned_bundle, "outer", "bridge_pad")
        tuned_via_size = _stage_size(tuned_bundle, "outer", "upper_via_pad")

        self.assertLess(tuned_bridge_size[1], default_bridge_size[1])
        self.assertEqual(tuned_bridge_size[0], default_bridge_size[0])
        self.assertLess(tuned_via_size[0], tuned_bridge_size[0])
        self.assertLess(tuned_via_size[1], tuned_bridge_size[1])

    def test_2t2t_bridge_routes_use_reference_style_45_degree_polygon(self) -> None:
        cfg = default_run_config("2t2t")
        inductor = cfg.bounds.midpoint().primary
        bundle = _build_center_tapped_inductor_geometry(
            InductorLayoutSpec(
                geometry=inductor,
                center_x_um=0.0,
                center_y_um=0.0,
                bridge_offset_y_um=0.0,
                bridge_anchor_gap_cap_um=None,
                metal_layer=139,
                bridge_layer=int(inductor.bridge_layer),
                bridge_via_layer=inductor.bridge_via_layer,
                bridge_lower_layer=inductor.bridge_lower_layer,
                bridge_lower_via_layer=inductor.bridge_lower_via_layer,
                mirror_x=False,
            ),
            include_center_tap_feed=inductor.center_tap,
        )

        bridge_vertex_counts = [len(poly.points) for poly in bundle.bridge_polygons]
        self.assertTrue(any(count > 4 for count in bridge_vertex_counts))

    def test_2t2t_bridge_endpoint_stacks_use_via_arrays(self) -> None:
        cfg = default_run_config("2t2t")
        inductor = cfg.bounds.midpoint().primary
        bundle = _build_center_tapped_inductor_geometry(
            InductorLayoutSpec(
                geometry=inductor,
                center_x_um=0.0,
                center_y_um=0.0,
                bridge_offset_y_um=0.0,
                bridge_anchor_gap_cap_um=None,
                metal_layer=139,
                bridge_layer=int(inductor.bridge_layer),
                bridge_via_layer=inductor.bridge_via_layer,
                bridge_lower_layer=inductor.bridge_lower_layer,
                bridge_lower_via_layer=inductor.bridge_lower_via_layer,
                mirror_x=False,
            ),
            include_center_tap_feed=inductor.center_tap,
        )

        upper_via_stage_counts = [
            len(stage.polygons)
            for stack in bundle.bridge_endpoint_stacks
            for stage in stack.stages
            if stage.name == "upper_via_pad"
        ]
        lower_via_stage_counts = [
            len(stage.polygons)
            for stack in bundle.bridge_endpoint_stacks
            for stage in stack.stages
            if stage.name == "lower_via_pad"
        ]

        self.assertTrue(upper_via_stage_counts)
        self.assertTrue(lower_via_stage_counts)
        self.assertTrue(all(count > 1 for count in upper_via_stage_counts))
        self.assertTrue(all(count > 1 for count in lower_via_stage_counts))

    def test_2t2t_reports_primary_anchor_gap_against_secondary_feed_opening(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()

        self.assertEqual(geom.topology_mode, "2t2t")
        self.assertEqual(geom.primary.turns, 2)
        self.assertEqual(geom.secondary.turns, 2)

        report = geom.constraint_report()
        self.assertIn("primary_bridge_anchor_gap_height_um", report)
        self.assertIn("secondary_feed_clear_opening_um", report)
        self.assertIn("primary_required_width_for_secondary_window_um", report)
        self.assertIn("required_secondary_terminal_span_for_interweaved_um", report)
        self.assertIn("primary_outer_anchor_x0_um", report)
        self.assertIn("secondary_outer_feed_x0_um", report)
        self.assertIn("interweaved_feed_x_margin_um", report)
        self.assertIn("interweaved_feed_y_margin_um", report)
        margin = report["interweaved_feed_x_margin_um"]
        y_margin = report["interweaved_feed_y_margin_um"]
        self.assertLessEqual(
            report["primary_bridge_anchor_gap_height_um"],
            report["secondary_feed_clear_opening_um"],
        )
        self.assertGreater(report["primary_required_width_for_secondary_window_um"], 0.0)
        self.assertGreaterEqual(
            geom.secondary.terminal_y_span_um,
            report["required_secondary_terminal_span_for_interweaved_um"],
        )
        self.assertGreaterEqual(margin, 0.0)
        self.assertGreaterEqual(y_margin, 0.0)
    def test_2t2t_gdstk_catches_secondary_feed_opening_that_is_too_small(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        bad_geom = replace(
            geom,
            primary=self._replace_inductor(
                geom.primary,
                trace_width_um=24.0,
                terminal_y_span_um=32.0,
            ),
        )

        errors = run_transformer_gdstk_checks(bad_geom, cfg).errors
        self.assertTrue(any("clearance violation" in msg for msg in errors))
    def test_2t2t_gdstk_catches_bridge_pad_too_close_to_secondary_feeds_in_x(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        bad_geom = replace(
            geom,
            primary=self._replace_inductor(
                geom.primary,
                trace_width_um=24.0,
                terminal_y_span_um=32.0,
            ),
        )

        errors = run_transformer_gdstk_checks(bad_geom, cfg).errors
        self.assertTrue(any("clearance violation" in msg for msg in errors))
    def test_2t2t_gdstk_catches_bridge_pad_too_close_to_secondary_feeds_in_y(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        bad_geom = replace(
            geom,
            primary=self._replace_inductor(
                geom.primary,
                trace_width_um=24.0,
                terminal_y_span_um=32.0,
            ),
        )

        errors = run_transformer_gdstk_checks(bad_geom, cfg).errors
        self.assertTrue(any("clearance violation" in msg for msg in errors))
    def test_gdstk_checks_report_default_2t2t_intermediate_bridge_pad_metrics(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        result = run_transformer_gdstk_checks(geom, cfg)
        self.assertEqual(result.metrics["primary_conductive_components"], 1)
        self.assertEqual(result.metrics["secondary_conductive_components"], 1)
        self.assertEqual(result.metrics["primary_intermediate_bridge_pad_count"], 2)
        self.assertGreaterEqual(result.metrics["primary_intermediate_bridge_pad_bbox_checks"], 1)
        self.assertGreaterEqual(result.metrics["primary_intermediate_bridge_pad_clearance_violations"], 0)
        self.assertGreaterEqual(float(result.metrics["elapsed_ms"]), 0.0)

    def test_via_rule_checks_accept_legal_via_and_emit_no_errors(self) -> None:
        cfg = replace(default_run_config("2t2t"), emx=replace(default_run_config("2t2t").emx, enable_large_plate_warnings=False))
        coil = gdstk.rectangle((0.0, 0.0), (0.30, 0.30), layer=74, datatype=0)
        intermediate = gdstk.rectangle((0.0, 0.0), (0.30, 0.30), layer=39, datatype=0)
        via = gdstk.rectangle((0.05, 0.05), (0.25, 0.25), layer=85, datatype=0)
        bundle = CenterTappedInductorGeometry(
            coil_polygons=(coil,),
            bridge_polygons=(intermediate,),
            via_polygons=(via,),
            intermediate_bridge_pad_polygons=(),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=InductorTerminals(top=(0.0, 0.0), bottom=(0.0, 0.0), center_tap=None),
            bridge_endpoint_stacks=(
                BridgeEndpointStack(
                    name="inner",
                    stages=(
                        BridgePadStage(name="coil_pad", layer=74, polygons=(coil,)),
                        BridgePadStage(name="upper_via_pad", layer=85, polygons=(via,)),
                        BridgePadStage(name="intermediate_pad", layer=39, polygons=(intermediate,)),
                    ),
                ),
            ),
        )

        errors, warnings, metrics = _bundle_via_rule_checks(bundle=bundle, run_config=cfg, prefix="primary")
        self.assertEqual(errors, [])
        self.assertTrue(any("single VIAy via" in msg for msg in warnings))
        self.assertEqual(metrics["primary_via_checked"], 1)

    def test_via_rule_checks_catch_bad_size_and_coverage(self) -> None:
        cfg = replace(default_run_config("2t2t"), emx=replace(default_run_config("2t2t").emx, enable_large_plate_warnings=False))
        coil = gdstk.rectangle((0.0, 0.0), (0.30, 0.30), layer=74, datatype=0)
        intermediate = gdstk.rectangle((0.06, 0.06), (0.24, 0.24), layer=39, datatype=0)
        via = gdstk.rectangle((0.05, 0.05), (0.23, 0.23), layer=85, datatype=0)
        bundle = CenterTappedInductorGeometry(
            coil_polygons=(coil,),
            bridge_polygons=(intermediate,),
            via_polygons=(via,),
            intermediate_bridge_pad_polygons=(),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=InductorTerminals(top=(0.0, 0.0), bottom=(0.0, 0.0), center_tap=None),
            bridge_endpoint_stacks=(
                BridgeEndpointStack(
                    name="inner",
                    stages=(
                        BridgePadStage(name="coil_pad", layer=74, polygons=(coil,)),
                        BridgePadStage(name="upper_via_pad", layer=85, polygons=(via,)),
                        BridgePadStage(name="intermediate_pad", layer=39, polygons=(intermediate,)),
                    ),
                ),
            ),
        )

        errors, warnings, metrics = _bundle_via_rule_checks(bundle=bundle, run_config=cfg, prefix="primary")
        self.assertTrue(any("size" in msg for msg in errors))
        self.assertTrue(any("fully covered" in msg for msg in errors))
        self.assertTrue(any("single VIAy via" in msg for msg in warnings))
        self.assertGreaterEqual(metrics["primary_via_size_violations"], 1)
        self.assertGreaterEqual(metrics["primary_via_coverage_violations"], 1)

    def test_via_rule_checks_warn_on_recommended_enclosure_and_large_plate_placeholder(self) -> None:
        cfg = default_run_config("2t2t")
        via_layer_rules = dict(cfg.emx.via_layer_rules)
        via_layer_rules[85] = replace(via_layer_rules[85], family="VIAx")
        cfg = replace(cfg, emx=replace(cfg.emx, via_layer_rules=via_layer_rules, enable_large_plate_warnings=True))
        coil = gdstk.rectangle((0.0, 0.0), (0.10, 0.10), layer=74, datatype=0)
        intermediate = gdstk.rectangle((0.0, 0.0), (0.10, 0.10), layer=39, datatype=0)
        via = gdstk.rectangle((0.0, 0.0), (0.10, 0.10), layer=85, datatype=0)
        bundle = CenterTappedInductorGeometry(
            coil_polygons=(coil,),
            bridge_polygons=(intermediate,),
            via_polygons=(via,),
            intermediate_bridge_pad_polygons=(),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=InductorTerminals(top=(0.0, 0.0), bottom=(0.0, 0.0), center_tap=None),
            bridge_endpoint_stacks=(
                BridgeEndpointStack(
                    name="inner",
                    stages=(
                        BridgePadStage(name="coil_pad", layer=74, polygons=(coil,)),
                        BridgePadStage(name="upper_via_pad", layer=85, polygons=(via,)),
                        BridgePadStage(name="intermediate_pad", layer=39, polygons=(intermediate,)),
                    ),
                ),
            ),
        )

        errors, warnings, metrics = _bundle_via_rule_checks(bundle=bundle, run_config=cfg, prefix="primary")
        self.assertEqual(errors, [])
        self.assertTrue(any("recommended" in msg for msg in warnings))
        self.assertTrue(any("large-plate proximity thresholds" in msg for msg in warnings))
        self.assertGreaterEqual(metrics["primary_via_recommended_enclosure_warnings"], 1)
        self.assertGreaterEqual(metrics["primary_via_large_plate_warnings"], 1)
    def test_generic_same_layer_spacing_checks_catch_expanded_region_collision(self) -> None:
        layer = 74
        terminals = InductorTerminals(top=(0.0, 0.0), bottom=(0.0, 0.0), center_tap=None)
        primary_bundle = CenterTappedInductorGeometry(
            coil_polygons=(gdstk.rectangle((0.0, 0.0), (10.0, 2.0), layer=layer, datatype=0),),
            bridge_polygons=(),
            via_polygons=(),
            intermediate_bridge_pad_polygons=(),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=terminals,
        )
        secondary_bundle = CenterTappedInductorGeometry(
            coil_polygons=(gdstk.rectangle((10.5, 0.0), (20.5, 2.0), layer=layer, datatype=0),),
            bridge_polygons=(),
            via_polygons=(),
            intermediate_bridge_pad_polygons=(),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=terminals,
        )

        errors, metrics = _generic_same_layer_spacing_checks(
            primary_bundle=primary_bundle,
            primary_trace_width_um=8.0,
            secondary_bundle=secondary_bundle,
            secondary_trace_width_um=8.0,
        )

        self.assertTrue(any("same-layer spacing/intersection violation" in err for err in errors))
        self.assertEqual(metrics["same_layer_spacing_violations"], 1)
        self.assertEqual(metrics["same_layer_region_pairs_checked"], 1)
    def test_primary_intermediate_bridge_pad_checks_catch_shared_layer_collision(self) -> None:
        bridge_layer = 139
        terminals = InductorTerminals(top=(0.0, 0.0), bottom=(0.0, 0.0), center_tap=None)
        source_bundle = CenterTappedInductorGeometry(
            coil_polygons=(),
            bridge_polygons=(gdstk.rectangle((4.0, 0.0), (8.0, 4.0), layer=bridge_layer, datatype=0),),
            via_polygons=(),
            intermediate_bridge_pad_polygons=(gdstk.rectangle((4.0, 0.0), (8.0, 4.0), layer=bridge_layer, datatype=0),),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=terminals,
        )
        target_bundle = CenterTappedInductorGeometry(
            coil_polygons=(gdstk.rectangle((7.6, -1.0), (17.6, 5.0), layer=bridge_layer, datatype=0),),
            bridge_polygons=(),
            via_polygons=(),
            intermediate_bridge_pad_polygons=(),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=terminals,
        )

        errors, metrics = _primary_intermediate_bridge_pad_clearance_checks(
            source_bundle=source_bundle,
            target_bundle=target_bundle,
            intermediate_layer=bridge_layer,
            margin_um=1.0,
        )

        self.assertTrue(any("primary intermediate bridge pad overlaps" in err for err in errors))
        self.assertEqual(metrics["primary_intermediate_bridge_pad_count"], 1)
        self.assertEqual(metrics["primary_intermediate_bridge_pad_bbox_checks"], 1)
        self.assertEqual(metrics["primary_intermediate_bridge_pad_clearance_violations"], 1)

    def test_generic_same_layer_spacing_checks_catch_direct_primary_secondary_overlap(self) -> None:
        layer = 138
        terminals = InductorTerminals(top=(0.0, 0.0), bottom=(0.0, 0.0), center_tap=None)
        primary_bundle = CenterTappedInductorGeometry(
            coil_polygons=(gdstk.rectangle((0.0, 0.0), (10.0, 4.0), layer=layer, datatype=0),),
            bridge_polygons=(),
            via_polygons=(),
            intermediate_bridge_pad_polygons=(),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=terminals,
        )
        secondary_bundle = CenterTappedInductorGeometry(
            coil_polygons=(gdstk.rectangle((6.0, 1.0), (16.0, 5.0), layer=layer, datatype=0),),
            bridge_polygons=(),
            via_polygons=(),
            intermediate_bridge_pad_polygons=(),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=terminals,
        )

        errors, metrics = _generic_same_layer_spacing_checks(
            primary_bundle=primary_bundle,
            primary_trace_width_um=8.0,
            secondary_bundle=secondary_bundle,
            secondary_trace_width_um=8.0,
        )

        self.assertTrue(any("same-layer spacing/intersection violation" in err for err in errors))
        self.assertEqual(metrics["same_layer_spacing_violations"], 1)
        self.assertEqual(metrics["same_layer_region_pairs_checked"], 1)

    def test_primary_intermediate_bridge_pad_checks_catch_secondary_bridge_collision(self) -> None:
        bridge_layer = 138
        terminals = InductorTerminals(top=(0.0, 0.0), bottom=(0.0, 0.0), center_tap=None)
        source_bundle = CenterTappedInductorGeometry(
            coil_polygons=(),
            bridge_polygons=(),
            via_polygons=(),
            intermediate_bridge_pad_polygons=(),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=terminals,
            bridge_endpoint_stacks=(
                BridgeEndpointStack(
                    name="outer",
                    stages=(
                        BridgePadStage(
                            name="bridge_pad",
                            layer=bridge_layer,
                            polygons=(gdstk.rectangle((4.0, 0.0), (8.0, 4.0), layer=bridge_layer, datatype=0),),
                        ),
                    ),
                ),
            ),
        )
        target_bundle = CenterTappedInductorGeometry(
            coil_polygons=(),
            bridge_polygons=(gdstk.rectangle((7.6, -1.0), (17.6, 5.0), layer=bridge_layer, datatype=0),),
            via_polygons=(),
            intermediate_bridge_pad_polygons=(),
            top_feed_polygons=(),
            center_feed_polygons=(),
            bottom_feed_polygons=(),
            outer_anchor_pad=(),
            inner_anchor_pad=(),
            top_terminal_window=(),
            bottom_terminal_window=(),
            terminals=terminals,
            bridge_groups=(
                LayerPolygonGroup(
                    layer=bridge_layer,
                    polygons=(gdstk.rectangle((7.6, -1.0), (17.6, 5.0), layer=bridge_layer, datatype=0),),
                    role="bridge",
                ),
            ),
        )

        errors, metrics = _primary_intermediate_bridge_pad_clearance_checks(
            source_bundle=source_bundle,
            target_bundle=target_bundle,
            margin_um=1.0,
        )

        self.assertTrue(any("primary intermediate bridge pad overlaps secondary geometry" in err for err in errors))
        self.assertEqual(metrics["primary_intermediate_bridge_pad_clearance_violations"], 1)

    def test_gdstk_checks_catch_zero_margin_between_primary_feeds(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        bad_geom = replace(
            geom,
            primary=self._replace_inductor(
                geom.primary,
                trace_width_um=16.0,
                terminal_y_span_um=32.0,
            ),
        )

        errors = run_transformer_gdstk_checks(bad_geom, cfg).errors
        self.assertTrue(any("primary top-feed to center-feed clearance violation" in msg for msg in errors))
    def test_gdstk_checks_catch_2t2t_bridge_pad_feed_clearance_failure(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        bad_geom = replace(
            geom,
            primary=self._replace_inductor(
                geom.primary,
                trace_width_um=24.0,
                terminal_y_span_um=32.0,
            ),
        )
        result = run_transformer_gdstk_checks(bad_geom, cfg)
        self.assertTrue(any("clearance violation" in err for err in result.errors))
