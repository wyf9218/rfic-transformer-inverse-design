from tests.rfic_transformer_inverse_design.shared import *
from rfic_transformer_inverse_design.optimize.backends import _invalid_geometry_penalty


class TransformerExecutionTest(TransformerToolboxTestBase):
    def test_evaluator_penalizes_terminal_span_exceeding_outer_dimensions_before_export(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        bad_geom = replace(
            geom,
            secondary=self._replace_inductor(
                geom.secondary,
                terminal_y_span_um=max(geom.secondary.outer_width_um, geom.secondary.outer_height_um) + 1.0,
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            result = evaluator.evaluate_geometry(bad_geom, run_emx=True)

            self.assertFalse(result.ok())
            self.assertIsNotNone(result.error)
            self.assertIn("geometry validation failed", result.error)
            self.assertIsNotNone(result.geometry_check)
            self.assertEqual(result.geometry_check["backend"], "geometry")
            self.assertFalse(result.geometry_check["ok"])
            self.assertTrue(any("outer_width_um" in err for err in result.geometry_check["errors"]))
            self.assertTrue(any("outer_height_um" in err for err in result.geometry_check["errors"]))
            self.assertIsNone(result.touchstone_path)
            self.assertTrue((result.work_dir / "summary.json").exists())

    def test_evaluator_penalizes_gdstk_geometry_failure_before_emx(self) -> None:
        cfg = default_run_config("2t2t")
        geom = cfg.bounds.midpoint()
        bad_geom = replace(
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
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            result = evaluator.evaluate_geometry(bad_geom, run_emx=True)

            self.assertFalse(result.ok())
            self.assertIsNotNone(result.error)
            self.assertIn("gdstk geometry check failed", result.error)
            self.assertIsNotNone(result.geometry_check)
            self.assertFalse(result.geometry_check["ok"])
            self.assertGreater(len(result.geometry_check["errors"]), 0)
            self.assertIsNone(result.touchstone_path)
            self.assertTrue((result.work_dir / "summary.json").exists())

    def test_evaluator_penalizes_signal_shield_clearance_failure_before_emx(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(cfg, emx=replace(cfg.emx, port_mode="single_ended_shield_grounded"))
        geom = cfg.bounds.midpoint()
        bad_geom = replace(
            geom,
            primary=self._replace_inductor(
                geom.primary,
                outer_width_um=162.06620216417537,
                outer_height_um=352.1085060885809,
                trace_width_um=3.852510756382424,
                spacing_um=8.0,
                terminal_y_span_um=43.6887201068222,
                feed_extension_um=198.7823510499785,
            ),
            secondary=self._replace_inductor(
                geom.secondary,
                outer_width_um=485.8910447044955,
                outer_height_um=175.4520035352828,
                trace_width_um=3.0867966780050606,
                spacing_um=8.0,
                terminal_y_span_um=38.27277482457716,
                feed_extension_um=177.6717130647376,
            ),
            offset_um=-46.22821080743601,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            result = evaluator.evaluate_geometry(bad_geom, run_emx=True)

            self.assertFalse(result.ok())
            self.assertIsNotNone(result.error)
            self.assertIn("signal-to-shield clearance violation", result.error)
            self.assertIsNone(result.touchstone_path)
            self.assertIsNotNone(result.geometry_check)
            self.assertFalse(result.geometry_check["ok"])
            metrics = result.geometry_check["metrics"]
            self.assertEqual(metrics["signal_shield_clearance_status"], "reject_signal_to_shield_clearance")
            self.assertGreater(metrics["signal_shield_clearance_violation_area_um2"], 0.0)
            self.assertTrue((result.work_dir / "layout" / "signal_shield_clearance_audit.json").exists())

    def test_evaluator_penalizes_layout_export_geometry_failure_before_emx(self) -> None:
        cfg = default_run_config("1t1t")
        geom = cfg.bounds.midpoint()

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            with mock.patch(
                "rfic_transformer_inverse_design.execution.evaluator.export_transformer_layout",
                side_effect=ValueError("turn size collapsed"),
            ):
                result = evaluator.evaluate_geometry(geom, run_emx=True)

            self.assertFalse(result.ok())
            self.assertIsNotNone(result.error)
            self.assertIn("layout export failed", result.error)
            self.assertIsNotNone(result.geometry_check)
            self.assertEqual(result.geometry_check["backend"], "layout_export")
            self.assertFalse(result.geometry_check["ok"])
            self.assertTrue(any("turn size collapsed" in err for err in result.geometry_check["errors"]))
            self.assertIsNotNone(_invalid_geometry_penalty(result))
            self.assertIsNone(result.touchstone_path)
            self.assertTrue((result.work_dir / "summary.json").exists())

    def test_evaluator_can_route_through_cadence_pin_roundtrip(self) -> None:
        cfg = default_run_config("1t1t")
        geometry = cfg.bounds.midpoint()

        def _write_touchstone(path: Path, freqs_hz: np.ndarray, s_matrix: np.ndarray) -> None:
            with Path(path).open("w", encoding="ascii") as handle:
                handle.write("! 4-port synthetic data\n")
                handle.write("# GHz S RI R 50\n")
                for idx, freq_hz in enumerate(freqs_hz):
                    values = [f"{freq_hz / 1e9:.12g}"]
                    for row in range(4):
                        for col in range(4):
                            s = complex(s_matrix[idx, row, col])
                            values.extend([f"{s.real:.16e}", f"{s.imag:.16e}"])
                    handle.write(" ".join(values) + "\n")

        def _fake_roundtrip(*, run_config, geometry, root_dir, stop_after, cadence_install_root, pdk_cds_lib, tech_lib_name, layer_map_path):
            self.assertEqual(stop_after, "emx")
            self.assertEqual(run_config, cfg)
            evaluator = TransformerEmxEvaluator(run_config=run_config, root_dir=Path(root_dir))
            cache_key = evaluator.cache_key(geometry)
            work_dir = Path(root_dir) / "evaluations" / cache_key
            layout_dir = work_dir / "layout"
            emx_dir = work_dir / "emx"
            layout_dir.mkdir(parents=True, exist_ok=True)
            emx_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = layout_dir / "transformer_layout.layout.json"
            cadence_gds = work_dir / "streamout" / "transformer_layout_cadpins.gds"
            cadence_preview = work_dir / "streamout" / "transformer_layout_preview.png"
            cadence_debug = work_dir / "streamout" / "transformer_port_debug.png"
            cadence_gds.parent.mkdir(parents=True, exist_ok=True)
            cadence_gds.write_text("dummy gds", encoding="utf-8")
            cadence_preview.write_text("dummy preview", encoding="utf-8")
            cadence_debug.write_text("dummy debug", encoding="utf-8")
            manifest_path.write_text(
                json.dumps(
                    {
                        "layout_path": str(cadence_gds),
                        "top_cell": run_config.emx.top_cell_prefix,
                        "ports": [
                            {
                                "name": "P001",
                                "signal_labels": ["P001"],
                                "ground_labels": [],
                                "internal_size_um": [4.0, 4.0],
                            },
                            {
                                "name": "P002",
                                "signal_labels": ["P002"],
                                "ground_labels": [],
                                "internal_size_um": [4.0, 4.0],
                            },
                            {
                                "name": "P003",
                                "signal_labels": ["P003"],
                                "ground_labels": [],
                                "internal_size_um": [4.0, 4.0],
                            },
                            {
                                "name": "P004",
                                "signal_labels": ["P004"],
                                "ground_labels": [],
                                "internal_size_um": [4.0, 4.0],
                            },
                        ],
                        "metal_layer": 1,
                        "metal_datatype": 0,
                        "ground_layer": None,
                        "ground_datatype": None,
                        "label_layer": 10,
                        "label_datatype": 0,
                        "cadence_pin_purpose": 51,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            freqs = np.linspace(*run_config.target.band_edges_hz(), run_config.target.band_points)
            diff = build_lumped_transformer_sparameters(
                freqs_hz=freqs,
                target=run_config.target,
                q_primary=18.0,
                q_secondary=16.0,
            )
            single = differential_2port_to_4port_s(
                freqs_hz=freqs,
                s_diff=diff.s_matrix,
                diff_z0_ohm=run_config.target.differential_reference_impedance_ohm,
                single_z0_ohm=50.0,
            )
            touchstone_path = emx_dir / "emx.s4p"
            _write_touchstone(touchstone_path, single.freqs_hz, single.s_matrix)
            return {
                "ok": True,
                "touchstone_path": str(touchstone_path),
                "command": ["emx", str(cadence_gds)],
                "artifacts": {
                    "export_manifest": str(manifest_path),
                    "cadence_gds": str(cadence_gds),
                    "cadence_preview": str(cadence_preview),
                    "cadence_debug_preview": str(cadence_debug),
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            with mock.patch("rfic_transformer_inverse_design.execution.evaluator.run_transformer_zeus_cadence_roundtrip", _fake_roundtrip):
                result = evaluator.evaluate_geometry(geometry, run_emx=True)

            self.assertTrue(result.ok())
            self.assertIsNotNone(result.layout)
            self.assertTrue(str(result.layout.gds_path).endswith("transformer_layout_cadpins.gds"))
            self.assertIsNotNone(result.touchstone_path)
            self.assertTrue(str(result.touchstone_path).endswith(".s4p"))
            self.assertEqual(result.command, ["emx", str(result.layout.gds_path)])
            self.assertTrue((result.work_dir / "differential_analysis.npz").exists())
            self.assertTrue((result.work_dir / "lumped_compare.png").exists())

    def test_evaluator_can_route_through_remote_ssh_roundtrip(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(
                cfg.emx,
                execution_mode="remote_ssh",
                remote_ssh_host="zeus",
                remote_repo_root="/srv/rfic_transformer_inverse_design-git",
                remote_work_root="/srv/rfic_transformer_inverse_design-runs",
                remote_python="python3",
            ),
        )
        geometry = cfg.bounds.midpoint()

        def _write_touchstone(path: Path, freqs_hz: np.ndarray, s_matrix: np.ndarray) -> None:
            with Path(path).open("w", encoding="ascii") as handle:
                handle.write("! 4-port synthetic data\n")
                handle.write("# GHz S RI R 50\n")
                for idx, freq_hz in enumerate(freqs_hz):
                    values = [f"{freq_hz / 1e9:.12g}"]
                    for row in range(4):
                        for col in range(4):
                            s = complex(s_matrix[idx, row, col])
                            values.extend([f"{s.real:.16e}", f"{s.imag:.16e}"])
                    handle.write(" ".join(values) + "\n")

        def _fake_remote_roundtrip(*, run_config, geometry, local_work_dir, cache_key):
            self.assertEqual(run_config, cfg)
            self.assertEqual(cache_key, TransformerEmxEvaluator(run_config=run_config, root_dir=Path(local_work_dir).parent.parent).cache_key(geometry))
            layout_dir = Path(local_work_dir) / "layout"
            emx_dir = Path(local_work_dir) / "emx"
            streamout_dir = Path(local_work_dir) / "streamout"
            layout_dir.mkdir(parents=True, exist_ok=True)
            emx_dir.mkdir(parents=True, exist_ok=True)
            streamout_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = layout_dir / "transformer_layout.layout.json"
            cadence_gds = streamout_dir / "transformer_layout_cadpins.gds"
            cadence_preview = streamout_dir / "transformer_layout_preview.png"
            cadence_debug = streamout_dir / "transformer_port_debug.png"
            cadence_gds.write_text("dummy gds", encoding="utf-8")
            cadence_preview.write_text("dummy preview", encoding="utf-8")
            cadence_debug.write_text("dummy debug", encoding="utf-8")
            manifest_path.write_text(
                json.dumps(
                    {
                        "layout_path": str(cadence_gds),
                        "top_cell": run_config.emx.top_cell_prefix,
                        "ports": [
                            {"name": "P001", "signal_labels": ["P001"], "ground_labels": [], "internal_size_um": [4.0, 4.0]},
                            {"name": "P002", "signal_labels": ["P002"], "ground_labels": [], "internal_size_um": [4.0, 4.0]},
                            {"name": "P003", "signal_labels": ["P003"], "ground_labels": [], "internal_size_um": [4.0, 4.0]},
                            {"name": "P004", "signal_labels": ["P004"], "ground_labels": [], "internal_size_um": [4.0, 4.0]},
                        ],
                        "metal_layer": 1,
                        "metal_datatype": 0,
                        "ground_layer": None,
                        "ground_datatype": None,
                        "label_layer": 10,
                        "label_datatype": 0,
                        "cadence_pin_purpose": 51,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            freqs = np.linspace(*run_config.target.band_edges_hz(), run_config.target.band_points)
            diff = build_lumped_transformer_sparameters(
                freqs_hz=freqs,
                target=run_config.target,
                q_primary=18.0,
                q_secondary=16.0,
            )
            single = differential_2port_to_4port_s(
                freqs_hz=freqs,
                s_diff=diff.s_matrix,
                diff_z0_ohm=run_config.target.differential_reference_impedance_ohm,
                single_z0_ohm=50.0,
            )
            touchstone_path = emx_dir / "emx.s4p"
            _write_touchstone(touchstone_path, single.freqs_hz, single.s_matrix)
            return {
                "ok": True,
                "touchstone_path": str(touchstone_path),
                "command": ["ssh", "zeus", "emx", str(cadence_gds)],
                "artifacts": {
                    "export_manifest": str(manifest_path),
                    "cadence_gds": str(cadence_gds),
                    "cadence_preview": str(cadence_preview),
                    "cadence_debug_preview": str(cadence_debug),
                    "top_cell": run_config.emx.top_cell_prefix,
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            with mock.patch("rfic_transformer_inverse_design.execution.evaluator.run_transformer_remote_ssh_roundtrip", _fake_remote_roundtrip):
                result = evaluator.evaluate_geometry(geometry, run_emx=True)

            self.assertTrue(result.ok())
            self.assertIsNotNone(result.layout)
            self.assertEqual(result.command[:2], ["ssh", "zeus"])
            self.assertTrue((result.work_dir / "differential_analysis.npz").exists())
            self.assertTrue((result.work_dir / "lumped_compare.png").exists())

    def test_evaluator_accepts_differential_roundtrip_results(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(cfg, emx=replace(cfg.emx, port_mode="differential_pairs"))
        geometry = cfg.bounds.midpoint()

        def _write_touchstone(path: Path, freqs_hz: np.ndarray, s_matrix: np.ndarray) -> None:
            with Path(path).open("w", encoding="ascii") as handle:
                handle.write("! 2-port synthetic data\n")
                handle.write("# GHz S RI R 100\n")
                for idx, freq_hz in enumerate(freqs_hz):
                    values = [f"{freq_hz / 1e9:.12g}"]
                    for row in range(2):
                        for col in range(2):
                            s = complex(s_matrix[idx, row, col])
                            values.extend([f"{s.real:.16e}", f"{s.imag:.16e}"])
                    handle.write(" ".join(values) + "\n")

        def _fake_roundtrip(*, run_config, geometry, root_dir, stop_after, cadence_install_root, pdk_cds_lib, tech_lib_name, layer_map_path):
            self.assertEqual(stop_after, "emx")
            evaluator = TransformerEmxEvaluator(run_config=run_config, root_dir=Path(root_dir))
            cache_key = evaluator.cache_key(geometry)
            work_dir = Path(root_dir) / "evaluations" / cache_key
            layout_dir = work_dir / "layout"
            emx_dir = work_dir / "emx"
            layout_dir.mkdir(parents=True, exist_ok=True)
            emx_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = layout_dir / "transformer_layout.layout.json"
            cadence_gds = work_dir / "streamout" / "transformer_layout_cadpins.gds"
            cadence_preview = work_dir / "streamout" / "transformer_layout_preview.png"
            cadence_debug = work_dir / "streamout" / "transformer_port_debug.png"
            cadence_gds.parent.mkdir(parents=True, exist_ok=True)
            cadence_gds.write_text("dummy gds", encoding="utf-8")
            cadence_preview.write_text("dummy preview", encoding="utf-8")
            cadence_debug.write_text("dummy debug", encoding="utf-8")
            manifest_path.write_text(
                json.dumps(
                    {
                        "layout_path": str(cadence_gds),
                        "top_cell": run_config.emx.top_cell_prefix,
                        "ports": [
                            {
                                "name": "PPRI",
                                "signal_labels": ["P001"],
                                "ground_labels": ["P002"],
                                "internal_size_um": [4.0, 4.0],
                            },
                            {
                                "name": "PSEC",
                                "signal_labels": ["P003"],
                                "ground_labels": ["P004"],
                                "internal_size_um": [4.0, 4.0],
                            },
                        ],
                        "metal_layer": 1,
                        "metal_datatype": 0,
                        "ground_layer": None,
                        "ground_datatype": None,
                        "label_layer": 10,
                        "label_datatype": 0,
                        "cadence_pin_purpose": 51,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            freqs = np.linspace(*run_config.target.band_edges_hz(), run_config.target.band_points)
            diff = build_lumped_transformer_sparameters(
                freqs_hz=freqs,
                target=run_config.target,
                q_primary=18.0,
                q_secondary=16.0,
            )
            touchstone_path = emx_dir / "emx.s2p"
            _write_touchstone(touchstone_path, diff.freqs_hz, diff.s_matrix)
            return {
                "ok": True,
                "touchstone_path": str(touchstone_path),
                "command": ["emx", str(cadence_gds)],
                "artifacts": {
                    "export_manifest": str(manifest_path),
                    "cadence_gds": str(cadence_gds),
                    "cadence_preview": str(cadence_preview),
                    "cadence_debug_preview": str(cadence_debug),
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            with mock.patch("rfic_transformer_inverse_design.execution.evaluator.run_transformer_zeus_cadence_roundtrip", _fake_roundtrip):
                result = evaluator.evaluate_geometry(geometry, run_emx=True)

            self.assertTrue(result.ok())
            self.assertIsNone(result.single_ended_sparams)
            self.assertIsNotNone(result.differential_sparams)
            self.assertEqual(result.differential_sparams.num_ports, 2)
            self.assertTrue(str(result.touchstone_path).endswith(".s2p"))

    def test_evaluator_reduces_vdd_augmented_roundtrip_results_back_to_four_ports(self) -> None:
        cfg = default_run_config("1t1t")
        geometry = replace(
            cfg.bounds.midpoint(),
            primary=self._replace_inductor(
                cfg.bounds.midpoint().primary,
                center_tap=True,
                vdd_bar=replace(
                    cfg.bounds.midpoint().primary.fixed.vdd_bar,
                    enabled=True,
                    width_um=10.0,
                    bar_layer=74,
                ),
            ),
            shield=replace(cfg.bounds.midpoint().shield, enabled=True),
        )
        cfg = replace(
            cfg,
            emx=replace(cfg.emx, shield_layer=35, port_mode="single_ended_shield_grounded"),
            bounds=replace(
                cfg.bounds,
                shield=replace(cfg.bounds.shield, enabled=True),
                primary=replace(
                    cfg.bounds.primary,
                    center_tap=True,
                    vdd_bar=replace(cfg.bounds.primary.vdd_bar, enabled=True, width_um=10.0, bar_layer=74),
                ),
            ),
        )

        def _write_touchstone(path: Path, freqs_hz: np.ndarray, s_matrix: np.ndarray) -> None:
            n_ports = int(s_matrix.shape[1])
            with Path(path).open("w", encoding="ascii") as handle:
                handle.write(f"! {n_ports}-port synthetic data\n")
                handle.write("# GHz S RI R 50\n")
                for idx, freq_hz in enumerate(freqs_hz):
                    values = [f"{freq_hz / 1e9:.12g}"]
                    for row in range(n_ports):
                        for col in range(n_ports):
                            s = complex(s_matrix[idx, row, col])
                            values.extend([f"{s.real:.16e}", f"{s.imag:.16e}"])
                    handle.write(" ".join(values) + "\n")

        def _fake_roundtrip(*, run_config, geometry, root_dir, stop_after, cadence_install_root, pdk_cds_lib, tech_lib_name, layer_map_path):
            self.assertEqual(stop_after, "emx")
            evaluator = TransformerEmxEvaluator(run_config=run_config, root_dir=Path(root_dir))
            cache_key = evaluator.cache_key(geometry)
            work_dir = Path(root_dir) / "evaluations" / cache_key
            layout_dir = work_dir / "layout"
            emx_dir = work_dir / "emx"
            layout_dir.mkdir(parents=True, exist_ok=True)
            emx_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = layout_dir / "transformer_layout.layout.json"
            cadence_gds = work_dir / "streamout" / "transformer_layout_cadpins.gds"
            cadence_preview = work_dir / "streamout" / "transformer_layout_preview.png"
            cadence_debug = work_dir / "streamout" / "transformer_port_debug.png"
            cadence_gds.parent.mkdir(parents=True, exist_ok=True)
            cadence_gds.write_text("dummy gds", encoding="utf-8")
            cadence_preview.write_text("dummy preview", encoding="utf-8")
            cadence_debug.write_text("dummy debug", encoding="utf-8")
            manifest_path.write_text(
                json.dumps(
                    {
                        "layout_path": str(cadence_gds),
                        "top_cell": run_config.emx.top_cell_prefix,
                        "ports": [
                            {"name": "P001", "signal_labels": ["P001"], "ground_labels": ["P001_G"], "internal_size_um": [4.0, 4.0]},
                            {"name": "P002", "signal_labels": ["P002"], "ground_labels": ["P002_G"], "internal_size_um": [4.0, 4.0]},
                            {"name": "P003", "signal_labels": ["P003"], "ground_labels": ["P003_G"], "internal_size_um": [4.0, 4.0]},
                            {"name": "P004", "signal_labels": ["P004"], "ground_labels": ["P004_G"], "internal_size_um": [4.0, 4.0]},
                            {"name": "PVDD_TOP", "signal_labels": ["PVDD_TOP"], "ground_labels": ["PVDD_TOP_G"], "internal_size_um": [10.0, 0.5]},
                            {"name": "PVDD_BOT", "signal_labels": ["PVDD_BOT"], "ground_labels": ["PVDD_BOT_G"], "internal_size_um": [10.0, 0.5]},
                        ],
                        "metal_layer": 1,
                        "metal_datatype": 0,
                        "ground_layer": 2,
                        "ground_datatype": 0,
                        "label_layer": 10,
                        "label_datatype": 0,
                        "cadence_pin_purpose": 51,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            freqs = np.linspace(*run_config.target.band_edges_hz(), run_config.target.band_points)
            diff = build_lumped_transformer_sparameters(
                freqs_hz=freqs,
                target=run_config.target,
                q_primary=18.0,
                q_secondary=16.0,
            )
            single = differential_2port_to_4port_s(
                freqs_hz=freqs,
                s_diff=diff.s_matrix,
                diff_z0_ohm=run_config.target.differential_reference_impedance_ohm,
                single_z0_ohm=50.0,
            )
            s6 = np.zeros((single.num_freqs, 6, 6), dtype=np.complex128)
            s6[:, :4, :4] = single.s_matrix
            touchstone_path = emx_dir / "emx.s6p"
            _write_touchstone(touchstone_path, freqs, s6)
            return {
                "ok": True,
                "touchstone_path": str(touchstone_path),
                "command": ["emx", str(cadence_gds)],
                "artifacts": {
                    "export_manifest": str(manifest_path),
                    "cadence_gds": str(cadence_gds),
                    "cadence_preview": str(cadence_preview),
                    "cadence_debug_preview": str(cadence_debug),
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            with mock.patch("rfic_transformer_inverse_design.execution.evaluator.run_transformer_zeus_cadence_roundtrip", _fake_roundtrip):
                result = evaluator.evaluate_geometry(geometry, run_emx=True)

            self.assertTrue(result.ok())
            self.assertIsNotNone(result.single_ended_sparams)
            self.assertEqual(result.single_ended_sparams.num_ports, 4)
            self.assertIsNotNone(result.touchstone_path)
            self.assertTrue(str(result.touchstone_path).endswith("_reduced.s4p"))
            self.assertTrue(result.touchstone_path.exists())

    def test_evaluator_accepts_power_line_8port_s8p_roundtrip_results(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(
                cfg.emx,
                shield_layer=35,
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
                    vdd_bar=replace(cfg.bounds.primary.vdd_bar, enabled=True, width_um=10.0, bar_layer=cfg.emx.ap_layer, offset_um=12.0),
                ),
                secondary=replace(
                    cfg.bounds.secondary,
                    center_tap=True,
                    vdd_bar=replace(cfg.bounds.secondary.vdd_bar, enabled=True, width_um=10.0, bar_layer=cfg.emx.m9_layer, offset_um=12.0),
                ),
            ),
        )
        geometry = cfg.bounds.midpoint()

        def _write_touchstone(path: Path, freqs_hz: np.ndarray, s_matrix: np.ndarray) -> None:
            n_ports = int(s_matrix.shape[1])
            with Path(path).open("w", encoding="ascii") as handle:
                handle.write(f"! {n_ports}-port synthetic data\n")
                handle.write("# GHz S RI R 50\n")
                for idx, freq_hz in enumerate(freqs_hz):
                    values = [f"{freq_hz / 1e9:.12g}"]
                    for row in range(n_ports):
                        for col in range(n_ports):
                            s = complex(s_matrix[idx, row, col])
                            values.extend([f"{s.real:.16e}", f"{s.imag:.16e}"])
                    handle.write(" ".join(values) + "\n")

        def _fake_roundtrip(*, run_config, geometry, root_dir, stop_after, cadence_install_root, pdk_cds_lib, tech_lib_name, layer_map_path):
            self.assertEqual(stop_after, "emx")
            evaluator = TransformerEmxEvaluator(run_config=run_config, root_dir=Path(root_dir))
            cache_key = evaluator.cache_key(geometry)
            work_dir = Path(root_dir) / "evaluations" / cache_key
            layout_dir = work_dir / "layout"
            emx_dir = work_dir / "emx"
            streamout_dir = work_dir / "streamout"
            emx_dir.mkdir(parents=True, exist_ok=True)
            streamout_dir.mkdir(parents=True, exist_ok=True)
            cadence_gds = streamout_dir / "transformer_layout_cadpins.gds"
            cadence_preview = streamout_dir / "transformer_layout_preview.png"
            cadence_debug = streamout_dir / "transformer_port_debug.png"
            cadence_gds.write_text("dummy gds", encoding="utf-8")
            cadence_preview.write_text("dummy preview", encoding="utf-8")
            cadence_debug.write_text("dummy debug", encoding="utf-8")
            manifest_path = layout_dir / "transformer_layout.layout.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual([port["name"] for port in manifest["ports"]], [f"P{idx:03d}" for idx in range(1, 9)])

            freqs = np.linspace(*run_config.target.band_edges_hz(), run_config.target.band_points)
            diff = build_lumped_transformer_sparameters(
                freqs_hz=freqs,
                target=run_config.target,
                q_primary=18.0,
                q_secondary=16.0,
            )
            single4 = differential_2port_to_4port_s(
                freqs_hz=freqs,
                s_diff=diff.s_matrix,
                diff_z0_ohm=run_config.target.differential_reference_impedance_ohm,
                single_z0_ohm=50.0,
            )
            s8 = np.zeros((single4.num_freqs, 8, 8), dtype=np.complex128)
            selected = [0, 3, 4, 5]
            for out_i, raw_i in enumerate(selected):
                for out_j, raw_j in enumerate(selected):
                    s8[:, raw_i, raw_j] = single4.s_matrix[:, out_i, out_j]
            touchstone_path = emx_dir / "emx.s8p"
            _write_touchstone(touchstone_path, single4.freqs_hz, s8)
            return {
                "ok": True,
                "touchstone_path": str(touchstone_path),
                "command": ["emx", str(cadence_gds), "-s", str(touchstone_path)],
                "artifacts": {
                    "export_manifest": str(manifest_path),
                    "cadence_gds": str(cadence_gds),
                    "cadence_preview": str(cadence_preview),
                    "cadence_debug_preview": str(cadence_debug),
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            with mock.patch("rfic_transformer_inverse_design.execution.evaluator.run_transformer_zeus_cadence_roundtrip", _fake_roundtrip):
                result = evaluator.evaluate_geometry(geometry, run_emx=True)

            self.assertTrue(result.ok(), result.error)
            self.assertIsNotNone(result.single_ended_sparams)
            self.assertEqual(result.single_ended_sparams.num_ports, 8)
            self.assertIsNotNone(result.differential_sparams)
            self.assertEqual(result.differential_sparams.num_ports, 2)
            self.assertTrue(str(result.touchstone_path).endswith(".s8p"))
            self.assertTrue((result.work_dir / "layout" / "power_line_8port_geometry.json").exists())
            self.assertIsNotNone(result.geometry_check)
            metrics = result.geometry_check["metrics"]
            power_line_geometry = json.loads((result.work_dir / "layout" / "power_line_8port_geometry.json").read_text(encoding="utf-8"))
            self.assertEqual(power_line_geometry["center_tap_topology"], "primary_right_secondary_left")
            self.assertFalse(power_line_geometry["primary_is_physical_left"])
            self.assertGreater(
                power_line_geometry["primary_power_line"]["center_x_um"],
                power_line_geometry["secondary_power_line"]["center_x_um"],
            )
            self.assertEqual(power_line_geometry["primary_power_line"]["top_port_label"], "P007")
            self.assertEqual(power_line_geometry["secondary_power_line"]["top_port_label"], "P002")
            self.assertTrue(metrics["power_line_8port_geometry_audit_enabled"])
            shared_line_width_um = float(power_line_geometry["line_width_um"])
            self.assertAlmostEqual(metrics["power_line_8port_bridge_width_um"], shared_line_width_um, delta=1e-12)
            self.assertAlmostEqual(metrics["power_line_8port_ground_frame_width_um"], 100.0, delta=1e-9)
            self.assertEqual(
                metrics["power_line_8port_ground_frame_policy"],
                "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
            )
            self.assertAlmostEqual(
                metrics["power_line_8port_primary_height_um"],
                metrics["power_line_8port_secondary_height_um"],
                delta=1e-12,
            )
            self.assertAlmostEqual(metrics["power_line_8port_primary_bridge_width_um"], shared_line_width_um, delta=1e-12)
            self.assertAlmostEqual(metrics["power_line_8port_secondary_bridge_width_um"], shared_line_width_um, delta=1e-12)
            self.assertAlmostEqual(metrics["power_line_8port_primary_bridge_delta_y_um"], 0.0, delta=1e-12)
            self.assertAlmostEqual(metrics["power_line_8port_secondary_bridge_delta_y_um"], 0.0, delta=1e-12)
            self.assertAlmostEqual(metrics["power_line_8port_primary_bridge_edge_alignment_error_um"], 0.0, delta=1e-12)
            self.assertAlmostEqual(metrics["power_line_8port_secondary_bridge_edge_alignment_error_um"], 0.0, delta=1e-12)
            self.assertGreater(metrics["power_line_8port_primary_bridge_length_um"], 0.0)
            self.assertGreater(metrics["power_line_8port_secondary_bridge_length_um"], 0.0)

    def test_evaluator_batch_routes_many_geometries_through_batched_cadence_roundtrip(self) -> None:
        cfg = default_run_config("1t1t")
        geometry_a = cfg.bounds.midpoint()
        geometry_b = replace(geometry_a, offset_um=geometry_a.offset_um + 2.0)

        def _write_touchstone(path: Path, freqs_hz: np.ndarray, s_matrix: np.ndarray) -> None:
            with Path(path).open("w", encoding="ascii") as handle:
                handle.write("! 4-port synthetic data\n")
                handle.write("# GHz S RI R 50\n")
                for idx, freq_hz in enumerate(freqs_hz):
                    values = [f"{freq_hz / 1e9:.12g}"]
                    for row in range(4):
                        for col in range(4):
                            s = complex(s_matrix[idx, row, col])
                            values.extend([f"{s.real:.16e}", f"{s.imag:.16e}"])
                    handle.write(" ".join(values) + "\n")

        def _fake_roundtrip_batch(*, run_config, exports, stop_after, cadence_install_root, pdk_cds_lib, tech_lib_name, layer_map_path):
            self.assertEqual(stop_after, "emx")
            self.assertEqual(run_config, cfg)
            self.assertEqual(len(exports), 2)
            self.assertNotEqual(exports[0].layout.top_cell, exports[1].layout.top_cell)
            shared_gds = exports[0].work_dir.parent / "batched_streamout.gds"
            shared_gds.write_text("dummy gds", encoding="utf-8")
            payloads = {}
            for export in exports:
                preview = export.work_dir / "streamout" / "transformer_layout_preview.png"
                debug = export.work_dir / "streamout" / "transformer_port_debug.png"
                preview.parent.mkdir(parents=True, exist_ok=True)
                preview.write_text("dummy preview", encoding="utf-8")
                debug.write_text("dummy debug", encoding="utf-8")
                freqs = np.linspace(*run_config.target.band_edges_hz(), run_config.target.band_points)
                diff = build_lumped_transformer_sparameters(
                    freqs_hz=freqs,
                    target=run_config.target,
                    q_primary=18.0,
                    q_secondary=16.0,
                )
                single = differential_2port_to_4port_s(
                    freqs_hz=freqs,
                    s_diff=diff.s_matrix,
                    diff_z0_ohm=run_config.target.differential_reference_impedance_ohm,
                    single_z0_ohm=50.0,
                )
                touchstone_path = export.work_dir / "emx" / "emx.s4p"
                touchstone_path.parent.mkdir(parents=True, exist_ok=True)
                _write_touchstone(touchstone_path, single.freqs_hz, single.s_matrix)
                payloads[export.cache_key] = {
                    "ok": True,
                    "touchstone_path": str(touchstone_path),
                    "command": ["emx", str(shared_gds), export.layout.top_cell],
                    "artifacts": {
                        "export_manifest": str(export.layout.manifest_path),
                        "cadence_gds": str(shared_gds),
                        "cadence_preview": str(preview),
                        "cadence_debug_preview": str(debug),
                        "top_cell": export.layout.top_cell,
                    },
                }
            return payloads

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            with mock.patch(
                "rfic_transformer_inverse_design.execution.evaluator.run_transformer_zeus_cadence_roundtrip_batch",
                _fake_roundtrip_batch,
            ):
                results = evaluator.evaluate_geometry_batch([geometry_a, geometry_b], run_emx=True)

            self.assertEqual(len(results), 2)
            self.assertTrue(all(result.ok() for result in results))
            self.assertNotEqual(results[0].layout.top_cell, results[1].layout.top_cell)
            self.assertTrue(results[0].layout.top_cell.startswith(cfg.emx.top_cell_prefix))
            self.assertTrue(results[1].layout.top_cell.startswith(cfg.emx.top_cell_prefix))
            self.assertTrue((results[0].work_dir / "summary.json").exists())
            self.assertTrue((results[1].work_dir / "summary.json").exists())
