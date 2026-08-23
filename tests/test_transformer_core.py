from tests.rfic_transformer_inverse_design.shared import *


class TransformerCoreTest(TransformerToolboxTestBase):
    def test_1t2t_defaults_only_enable_secondary_bridge_section_constraint(self) -> None:
        cfg = default_run_config("1t2t")
        geom = cfg.bounds.midpoint()
        self.assertEqual(cfg.bounds.primary.turns, 1)
        self.assertEqual(cfg.bounds.secondary.turns, 2)
        self.assertIsNone(geom.primary.bridge_section)
        self.assertIsNotNone(geom.secondary.bridge_section)

    def test_2t2t_defaults_enable_primary_bridge_section_constraint(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        self.assertIsNotNone(geom.primary.bridge_section)
        self.assertIsNotNone(geom.secondary.bridge_section)

    def test_2t2t_defaults_use_drawing_layer_bridge_route(self) -> None:
        cfg = default_run_config("2t2t")

        self.assertEqual(cfg.emx.m9_layer, 39)
        self.assertEqual(cfg.emx.primary_bridge_layer, 39)
        self.assertEqual(cfg.emx.primary_bridge_lower_layer, 38)
        self.assertEqual(cfg.emx.secondary_bridge_layer, 38)
        self.assertEqual(cfg.bounds.primary.bridge_layer, 39)
        self.assertEqual(cfg.bounds.primary.bridge_lower_layer, 38)
        self.assertEqual(cfg.bounds.secondary.bridge_layer, 38)

    def test_1t1t_defaults_do_not_materialize_bridge_properties(self) -> None:
        cfg = default_run_config("1t1t")
        geom = cfg.bounds.midpoint()
        self.assertIsNone(geom.primary.bridge_section)
        self.assertIsNone(geom.secondary.bridge_section)
    def test_1t1t_search_space_excludes_spacing(self) -> None:
        cfg = default_run_config("1t1t")
        self.assertEqual(
            cfg.bounds.names(),
            (
                "primary_outer_width_um",
                "primary_outer_height_um",
                "secondary_outer_width_um",
                "secondary_outer_height_um",
                "primary_width_um",
                "secondary_width_um",
                "primary_terminal_y_span_um",
                "secondary_terminal_y_span_um",
                "offset_um",
                "primary_feed_extension_um",
                "secondary_feed_extension_um",
            ),
        )
        self.assertEqual(len(cfg.bounds.to_scipy_bounds()), 11)
        self.assertEqual(TransformerOptimizationAdapter(cfg.bounds).field_order(), cfg.bounds.names())
        self.assertEqual(len(TransformerOptimizationAdapter(cfg.bounds).to_vector(cfg.bounds.midpoint())), 11)
        self.assertNotIn("primary_spacing_um", TransformerOptimizationAdapter(cfg.bounds).field_order())
        self.assertNotIn("secondary_spacing_um", TransformerOptimizationAdapter(cfg.bounds).field_order())
    def test_2t2t_search_space_keeps_spacing(self) -> None:
        cfg = default_run_config("2t2t")
        self.assertIn("primary_spacing_um", cfg.bounds.names())
        self.assertIn("secondary_spacing_um", cfg.bounds.names())
        self.assertEqual(len(cfg.bounds.to_scipy_bounds()), 13)
        self.assertEqual(len(TransformerOptimizationAdapter(cfg.bounds).to_vector(cfg.bounds.midpoint())), 13)
    def test_mixed_turn_search_space_only_includes_active_spacing(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            bounds=replace(
                cfg.bounds,
                primary=replace(cfg.bounds.primary, turns=2),
                secondary=replace(cfg.bounds.secondary, turns=1),
            ),
        )
        self.assertEqual(
            cfg.bounds.names(),
            (
                "primary_outer_width_um",
                "primary_outer_height_um",
                "secondary_outer_width_um",
                "secondary_outer_height_um",
                "primary_width_um",
                "primary_spacing_um",
                "secondary_width_um",
                "primary_terminal_y_span_um",
                "secondary_terminal_y_span_um",
                "offset_um",
                "primary_feed_extension_um",
                "secondary_feed_extension_um",
            ),
        )
        self.assertEqual(len(cfg.bounds.to_scipy_bounds()), 12)
        self.assertEqual(len(TransformerOptimizationAdapter(cfg.bounds).to_vector(cfg.bounds.midpoint())), 12)

    def test_2t1t_defaults_only_include_primary_spacing(self) -> None:
        cfg = default_run_config("2t1t")
        self.assertEqual(cfg.bounds.topology_mode, "2t1t")
        self.assertEqual(
            cfg.bounds.names(),
            (
                "primary_outer_width_um",
                "primary_outer_height_um",
                "secondary_outer_width_um",
                "secondary_outer_height_um",
                "primary_width_um",
                "primary_spacing_um",
                "secondary_width_um",
                "primary_terminal_y_span_um",
                "secondary_terminal_y_span_um",
                "offset_um",
                "primary_feed_extension_um",
                "secondary_feed_extension_um",
            ),
        )
    def test_equal_bounds_drop_variable_from_optimizer_dimensions(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            bounds=replace(
                cfg.bounds,
                primary=replace(cfg.bounds.primary, trace_width_um=(4.0, 4.0)),
            ),
        )

        self.assertIn("primary_width_um", cfg.bounds.names())
        self.assertNotIn("primary_width_um", cfg.bounds.optimizable_names())
        self.assertEqual(len(cfg.bounds.to_scipy_bounds()), 10)
        self.assertNotIn("primary_width_um", TransformerOptimizationAdapter(cfg.bounds).field_order())
        self.assertEqual(len(TransformerOptimizationAdapter(cfg.bounds).to_vector(cfg.bounds.midpoint())), 10)
    def test_feed_extensions_are_independent_per_inductor(self) -> None:
        cfg = default_run_config("1t1t")
        geom = cfg.bounds.midpoint()

        _, primary_terms = _build_winding(
            side="left",
            inductor=self._replace_inductor(geom.primary_inductor_spec(), feed_extension_um=28.0),
            center_x_um=0.0,
        )
        _, secondary_terms = _build_winding(
            side="right",
            inductor=self._replace_inductor(geom.secondary_inductor_spec(), feed_extension_um=64.0),
            center_x_um=geom.offset_um,
        )

        primary_outline_x = -geom.primary.outer_width_um * 0.5
        secondary_outline_x = geom.offset_um + geom.secondary.outer_width_um * 0.5
        self.assertAlmostEqual(primary_terms[0][0], primary_outline_x - 28.0, delta=1e-9)
        self.assertAlmostEqual(primary_terms[1][0], primary_outline_x - 28.0, delta=1e-9)
        self.assertAlmostEqual(secondary_terms[0][0], secondary_outline_x + 64.0, delta=1e-9)
        self.assertAlmostEqual(secondary_terms[1][0], secondary_outline_x + 64.0, delta=1e-9)
    def test_outer_size_is_independent_per_inductor(self) -> None:
        cfg = default_run_config("1t1t")
        geom = TransformerGeometrySpec.from_flat_dict(
            {
                **cfg.bounds.midpoint().flat_dict(),
                "primary_outer_width_um": 210.0,
                "primary_outer_height_um": 220.0,
                "secondary_outer_width_um": 180.0,
                "secondary_outer_height_um": 190.0,
            },
            topology_mode=cfg.bounds.topology_mode,
            primary_turns=cfg.bounds.primary_turns,
            secondary_turns=cfg.bounds.secondary.turns,
            primary_center_tap=cfg.bounds.primary.center_tap,
            secondary_center_tap=cfg.bounds.secondary.center_tap,
            primary_bridge_layer=cfg.bounds.primary.bridge_layer,
            secondary_bridge_layer=cfg.bounds.secondary.bridge_layer,
            primary_bridge_via_layer=cfg.bounds.primary.bridge_via_layer,
            secondary_bridge_via_layer=cfg.bounds.secondary.bridge_via_layer,
            primary_bridge_section=cfg.bounds.primary.bridge_section_spec(),
            secondary_bridge_section=cfg.bounds.secondary.bridge_section_spec(),
        )
        self.assertAlmostEqual(geom.primary.outer_width_um, 210.0, delta=1e-9)
        self.assertAlmostEqual(geom.primary.outer_height_um, 220.0, delta=1e-9)
        self.assertAlmostEqual(geom.secondary.outer_width_um, 180.0, delta=1e-9)
        self.assertAlmostEqual(geom.secondary.outer_height_um, 190.0, delta=1e-9)
    def test_transformer_spec_holds_two_inductors_and_offset(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()

        transformer = geom.transformer_spec()

        self.assertEqual(transformer.primary.turns, 2)
        self.assertEqual(transformer.secondary.turns, 2)
        self.assertTrue(transformer.primary.center_tap)
        self.assertTrue(transformer.secondary.center_tap)
        self.assertAlmostEqual(transformer.offset_um, geom.offset_um, delta=1e-12)
    def test_bounds_validate_rejects_spacing_and_width_outside_configured_limits(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()

        bad_spacing = replace(
            geom,
            primary=self._replace_inductor(geom.primary, trace_width_um=20.0, spacing_um=6.0),
        )
        spacing_errors = cfg.bounds.validate(bad_spacing)
        self.assertTrue(any("primary_width_um" in err for err in spacing_errors))

        bad_width = replace(geom, primary=self._replace_inductor(geom.primary, trace_width_um=46.0))
        width_errors = cfg.bounds.validate(bad_width)
        self.assertTrue(any("primary_width_um" in err for err in width_errors))

    def test_bounds_validate_rejects_terminal_span_that_collapses_pins(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()

        bad_primary_span = replace(
            geom,
            primary=self._replace_inductor(
                geom.primary,
                trace_width_um=12.0,
                terminal_y_span_um=10.0,
                center_tap=True,
            ),
        )
        primary_errors = cfg.bounds.validate(bad_primary_span)
        self.assertTrue(any("primary_terminal_y_span_um" in err for err in primary_errors))

        one_turn_cfg = default_run_config("1t1t")
        one_turn_geom = one_turn_cfg.bounds.midpoint()
        bad_secondary_span = replace(
            one_turn_geom,
            secondary=self._replace_inductor(
                one_turn_geom.secondary,
                trace_width_um=18.0,
                terminal_y_span_um=8.0,
                center_tap=False,
            ),
        )
        secondary_errors = one_turn_cfg.bounds.validate(bad_secondary_span)
        self.assertTrue(any("secondary_terminal_y_span_um" in err for err in secondary_errors))

    def test_validate_rejects_invalid_two_turn_candidate_without_auto_repair(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        invalid = replace(
            geom,
            primary=self._replace_inductor(geom.primary, trace_width_um=18.0, spacing_um=4.0),
            secondary=self._replace_inductor(geom.secondary, trace_width_um=21.0, terminal_y_span_um=20.0),
        )

        bounds_errors = cfg.bounds.validate(invalid)
        geometry_errors = cfg.bounds.validate(invalid)
        self.assertTrue(any("primary_width_um" in err for err in bounds_errors))
        self.assertTrue(any("secondary_width_um" in err for err in bounds_errors))
        self.assertTrue(any("primary_width_um" in err for err in geometry_errors))

    def test_validate_rejects_terminal_span_that_reaches_octagon_corner(self) -> None:
        cfg = default_run_config("1t1t")
        geom = cfg.bounds.midpoint()
        invalid = replace(
            geom,
            primary=self._replace_inductor(
                geom.primary,
                terminal_y_span_um=geom.primary.outer_height_um,
            ),
        )

        self.assertTrue(any("straight-section limit" in err for err in invalid.validate()))

    def test_validate_rejects_terminal_span_exceeding_outer_dimensions(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        invalid = replace(
            geom,
            secondary=self._replace_inductor(
                geom.secondary,
                terminal_y_span_um=max(geom.secondary.outer_width_um, geom.secondary.outer_height_um) + 1.0,
            ),
        )

        errors = invalid.validate()
        self.assertTrue(any("secondary outer_width_um" in err for err in errors))
        self.assertTrue(any("secondary outer_height_um" in err for err in errors))

    def test_validate_rejects_2t2t_terminal_span_exceeding_straight_side_limit(self) -> None:
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

        errors = invalid.validate()
        self.assertTrue(any("straight-section limit" in err for err in errors))

    def test_validate_rejects_offset_beyond_feed_support(self) -> None:
        cfg = default_run_config("1t1t")
        geom = cfg.bounds.midpoint()

        left_invalid = replace(
            geom,
            primary=self._replace_inductor(geom.primary, feed_extension_um=24.0),
            offset_um=-32.0,
        )
        right_invalid = replace(
            geom,
            secondary=self._replace_inductor(geom.secondary, feed_extension_um=28.0),
            offset_um=36.0,
        )

        self.assertTrue(any("primary-side feed support" in err for err in left_invalid.validate()))
        self.assertTrue(any("secondary-side feed support" in err for err in right_invalid.validate()))

    def test_validate_rejects_offset_beyond_feed_support_in_each_direction(self) -> None:
        cfg = default_run_config("1t1t")
        geom = cfg.bounds.midpoint()

        invalid_left = replace(
            geom,
            primary=self._replace_inductor(geom.primary, feed_extension_um=24.0),
            offset_um=-80.0,
        )
        invalid_right = replace(
            geom,
            secondary=self._replace_inductor(geom.secondary, feed_extension_um=28.0),
            offset_um=80.0,
        )

        self.assertTrue(any("primary-side feed support" in err for err in invalid_left.validate()))
        self.assertTrue(any("secondary-side feed support" in err for err in invalid_right.validate()))

    def test_2t2t_validate_reports_anchor_window_violations_without_repair(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        invalid = replace(
            geom,
            primary=self._replace_inductor(geom.primary, trace_width_um=16.0, spacing_um=2.0),
            secondary=self._replace_inductor(
                geom.secondary,
                trace_width_um=12.0,
                terminal_y_span_um=50.0,
                feed_extension_um=24.0,
            ),
            offset_um=-24.0,
        )

        report = invalid.constraint_report()
        margin = report["interweaved_feed_x_margin_um"]
        y_margin = report["interweaved_feed_y_margin_um"]

        self.assertLess(invalid.offset_um, report["interweaved_offset_x_min_um"])
        self.assertLess(invalid.secondary.feed_extension_um, report["required_secondary_feed_extension_for_interweaved_um"])
        self.assertFalse(
            report["primary_outer_anchor_x0_um"] >= report["secondary_outer_feed_x0_um"] + margin
            and report["primary_outer_anchor_x1_um"] <= report["secondary_outer_feed_x1_um"] - margin
        )
        self.assertEqual(y_margin, 0.0)
