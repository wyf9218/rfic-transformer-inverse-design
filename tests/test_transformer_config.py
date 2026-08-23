from tests.rfic_transformer_inverse_design.shared import *


class TransformerConfigTest(TransformerToolboxTestBase):
    def test_config_loading_defaults_to_cma_es(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text("target:\n  topology_mode: 1t1t\n", encoding="utf-8")
            cfg = load_run_config(path)
        self.assertEqual(cfg.optimizer.name, "cma_es")
        self.assertEqual(cfg.emx.shield_layer, 35)
        self.assertEqual(cfg.emx.cadence_pin_purpose, 51)
        self.assertEqual(cfg.emx.via_layer_rules[85].family, "VIAy")
        self.assertAlmostEqual(cfg.emx.via_family_rules["VIAz"].size_um, 0.36, delta=1e-12)
        self.assertEqual(cfg.bounds.primary.vdd_bar.bar_layer, 74)
        self.assertEqual(cfg.bounds.secondary.vdd_bar.bar_layer, 74)
        self.assertAlmostEqual(cfg.bounds.primary.vdd_bar.width_um, 10.0, delta=1e-12)
        self.assertAlmostEqual(cfg.bounds.secondary.vdd_bar.width_um, 10.0, delta=1e-12)
        self.assertAlmostEqual(cfg.bounds.primary.vdd_bar.offset_um, 0.0, delta=1e-12)
        self.assertAlmostEqual(cfg.bounds.secondary.vdd_bar.offset_um, 0.0, delta=1e-12)

    def test_config_loading_accepts_via_rule_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  via_layer_rules:",
                        "    85:",
                        "      family: VIAz",
                        "      connected_metal_layers: [74, 39]",
                        "  via_family_rules:",
                        "    VIAz:",
                        "      size_um: 0.40",
                        "      min_spacing_um: 0.36",
                        "      legal_min_all_sides_um: [0.02]",
                        "      legal_min_opposite_sides_um: [0.08]",
                        "      wide_metal_requirements:",
                        "        - min_width_um: 2.0",
                        "          min_length_um: 2.0",
                        "          options:",
                        "            - min_via_count: 2",
                        "              max_spacing_um: 1.5",
                        "  enable_large_plate_warnings: false",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.emx.via_layer_rules[85].family, "VIAz")
        self.assertEqual(cfg.emx.via_layer_rules[85].connected_metal_layers, (74, 39))
        self.assertAlmostEqual(cfg.emx.via_family_rules["VIAz"].size_um, 0.40, delta=1e-12)
        self.assertAlmostEqual(cfg.emx.via_family_rules["VIAz"].min_spacing_um, 0.36, delta=1e-12)
        self.assertEqual(cfg.emx.via_family_rules["VIAz"].wide_metal_requirements[0].options[0].min_via_count, 2)
        self.assertFalse(cfg.emx.enable_large_plate_warnings)
    def test_config_loading_supports_transformer_shield_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "transformer:",
                        "  shield:",
                        "    enabled: true",
                        "    kind: ring",
                        "    margin_um: 22.5",
                        "    width_um: 11.0",
                        "emx:",
                        "  shield_layer: 35",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertTrue(cfg.bounds.shield.enabled)
        self.assertEqual(cfg.bounds.shield.kind, "ring")
        self.assertAlmostEqual(cfg.bounds.shield.margin_um, 22.5, delta=1e-12)
        self.assertAlmostEqual(cfg.bounds.shield.width_um, 11.0, delta=1e-12)
        self.assertEqual(cfg.emx.shield_layer, 35)
    def test_config_loading_supports_topology_shield_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "topology:",
                        "  shield:",
                        "    enabled: true",
                        "    kind: ring",
                        "    margin_um: 19.5",
                        "    width_um: 9.0",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertTrue(cfg.bounds.shield.enabled)
        self.assertEqual(cfg.bounds.shield.kind, "ring")
        self.assertAlmostEqual(cfg.bounds.shield.margin_um, 19.5, delta=1e-12)
        self.assertAlmostEqual(cfg.bounds.shield.width_um, 9.0, delta=1e-12)
    def test_config_loading_maps_legacy_emx_shield_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  shielding_layer: 36",
                        "  ground_ring_margin_um: 18.0",
                        "  ground_ring_width_um: 7.5",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.emx.shield_layer, 36)
        self.assertAlmostEqual(cfg.bounds.shield.margin_um, 18.0, delta=1e-12)
        self.assertAlmostEqual(cfg.bounds.shield.width_um, 7.5, delta=1e-12)
    def test_config_loading_reads_emx_port_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  port_mode: differential_pairs",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.emx.port_mode, "differential_pairs")

    def test_config_loading_reads_differential_port_pairs_from_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  differential_port_pairs: '1,4:5,6'",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.emx.differential_port_pairs, ((0, 3), (4, 5)))

    def test_config_loading_reads_differential_port_pairs_from_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  differential_port_pairs:",
                        "    - [1, 4]",
                        "    - [5, 6]",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.emx.differential_port_pairs, ((0, 3), (4, 5)))

    def test_config_loading_rejects_ambiguous_differential_port_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  differential_port_pairs: '1,2:2,3'",
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "four distinct"):
                load_run_config(path)

    def test_config_loading_reads_complete_power_line_8port_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  port_mode: single_ended_shield_grounded",
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
            cfg = load_run_config(path)
        self.assertTrue(cfg.emx.power_line_8port.enabled)
        self.assertAlmostEqual(cfg.emx.power_line_8port.bridge_width_um, 10.0, delta=1e-12)
        self.assertEqual(cfg.emx.power_line_8port.port_map, ("P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"))

    def test_config_loading_reads_signal_only_power_line_s4p_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  port_mode: single_ended_shield_grounded",
                        "  differential_port_pairs: '1,2:3,4'",
                        "  power_line_8port:",
                        "    enabled: true",
                        "    touchstone_mode: signal_4_grounded_aux",
                        "    bridge_width_um: 10.0",
                        "    vertical_length_diameter_ratio: 1.5",
                        "    bridge_y_policy: center",
                        "    bridge_motion_axis: x_only",
                        "    port_ground_reference: shield",
                        "    port_map: [P001, P002, P003, P004]",
                        "    role_labels:",
                        "      primary_top: P001",
                        "      primary_bottom: P002",
                        "      secondary_top: P003",
                        "      secondary_bottom: P004",
                        "      left_power_top: P005",
                        "      left_power_bottom: P006",
                        "      right_power_top: P007",
                        "      right_power_bottom: P008",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertTrue(cfg.emx.power_line_8port.enabled)
        self.assertEqual(cfg.emx.power_line_8port.touchstone_mode, "signal_4_grounded_aux")
        self.assertEqual(cfg.emx.power_line_8port.port_map, ("P001", "P002", "P003", "P004"))

    def test_config_loading_rejects_incomplete_power_line_8port_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  port_mode: single_ended_shield_grounded",
                        "  power_line_8port:",
                        "    enabled: true",
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "power_line_8port configuration is incomplete"):
                load_run_config(path)

    def test_config_loading_rejects_power_line_8port_without_explicit_role_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  port_mode: single_ended_shield_grounded",
                        "  differential_port_pairs: '1,4:5,6'",
                        "  power_line_8port:",
                        "    enabled: true",
                        "    bridge_width_um: 10.0",
                        "    vertical_length_diameter_ratio: 1.5",
                        "    bridge_y_policy: center",
                        "    bridge_motion_axis: x_only",
                        "    port_ground_reference: shield",
                        "    port_map: [P001, P002, P003, P004, P005, P006, P007, P008]",
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "role_labels must explicitly map every physical role"):
                load_run_config(path)

    def test_config_loading_reads_cadence_pin_purpose(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  cadence_pin_purpose: 99",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.emx.cadence_pin_purpose, 99)
    def test_config_loading_reads_cadence_workspace_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  cadence_install_root: /cadence/IC231",
                        "  cadence_pdk_cds_lib: /disk/pdk/cds.lib",
                        "  cadence_tech_lib: myTech",
                        "  cadence_layer_map: /disk/pdk/my.layermap",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.emx.cadence_install_root, "/cadence/IC231")
        self.assertEqual(cfg.emx.cadence_pdk_cds_lib, "/disk/pdk/cds.lib")
        self.assertEqual(cfg.emx.cadence_tech_lib, "myTech")
        self.assertEqual(cfg.emx.cadence_layer_map, "/disk/pdk/my.layermap")
    def test_config_loading_reads_remote_ssh_execution_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  execution_mode: remote_ssh",
                        "  remote_ssh_host: zeus",
                        "  remote_repo_root: /srv/rfic_transformer_inverse_design-git",
                        "  remote_work_root: /srv/rfic_transformer_inverse_design-runs",
                        "  remote_python: python3",
                        "  remote_ssh_command: wsl -d Ubuntu-22.04 -e ssh",
                        "  remote_scp_command: wsl -d Ubuntu-22.04 -e scp",
                        "  remote_venv_activate: /srv/rfic_transformer_inverse_design-git/.venv/bin/activate",
                        "  remote_emx_process_file: /disk/pdk/typical.proc",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.emx.execution_mode, "remote_ssh")
        self.assertEqual(cfg.emx.remote_ssh_host, "zeus")
        self.assertEqual(cfg.emx.remote_repo_root, "/srv/rfic_transformer_inverse_design-git")
        self.assertEqual(cfg.emx.remote_work_root, "/srv/rfic_transformer_inverse_design-runs")
        self.assertEqual(cfg.emx.remote_python, "python3")
        self.assertEqual(cfg.emx.remote_ssh_command, "wsl -d Ubuntu-22.04 -e ssh")
        self.assertEqual(cfg.emx.remote_scp_command, "wsl -d Ubuntu-22.04 -e scp")
        self.assertEqual(
            cfg.emx.remote_venv_activate,
            "/srv/rfic_transformer_inverse_design-git/.venv/bin/activate",
        )
        self.assertEqual(cfg.emx.remote_emx_process_file, "/disk/pdk/typical.proc")
    def test_config_loading_reads_fractional_bandwidth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "  f0_hz: 36000000000.0",
                        "  fractional_bandwidth: 1.8888888888888888",
                        "  band_points: 69",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertAlmostEqual(cfg.target.fractional_bandwidth, 1.8888888888888888, delta=1e-12)
        self.assertEqual(cfg.target.band_points, 69)
        self.assertEqual(cfg.target.band_edges_hz(), (2.0e9, 70.0e9))
    def test_config_loading_reads_explicit_frequency_sweep(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "  f0_hz: 15000000000.0",
                        "  frequency_start_hz: 5000000000.0",
                        "  frequency_stop_hz: 50000000000.0",
                        "  frequency_step_hz: 100000000.0",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        freqs = cfg.target.frequency_points_hz()
        self.assertEqual(cfg.target.band_edges_hz(), (5.0e9, 50.0e9))
        self.assertEqual(cfg.target.band_points, 451)
        self.assertEqual(len(freqs), 451)
        self.assertAlmostEqual(freqs[0], 5.0e9, delta=1.0)
        self.assertAlmostEqual(freqs[-1], 50.0e9, delta=1.0)
        self.assertAlmostEqual(freqs[1] - freqs[0], 0.1e9, delta=1.0)

    def test_config_rejects_explicit_frequency_sweep_with_mismatched_band_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "  f0_hz: 15000000000.0",
                        "  frequency_start_hz: 5000000000.0",
                        "  frequency_stop_hz: 50000000000.0",
                        "  frequency_step_hz: 100000000.0",
                        "  band_points: 9",
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "band_points must match explicit frequency sweep"):
                load_run_config(path)

    def test_config_loading_defaults_q_target_mode_to_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text("target:\n  topology_mode: 1t1t\n", encoding="utf-8")
            cfg = load_run_config(path)
        self.assertEqual(cfg.target.q_target_mode, "max")
        self.assertIsNone(cfg.target.q_primary_target)
        self.assertIsNone(cfg.target.q_secondary_target)
    def test_config_loading_reads_explicit_q_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "  q_target_mode: target",
                        "  q_primary_target: 18.5",
                        "  q_secondary_target: 16.25",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.target.q_target_mode, "target")
        self.assertAlmostEqual(cfg.target.q_primary_target, 18.5, delta=1e-12)
        self.assertAlmostEqual(cfg.target.q_secondary_target, 16.25, delta=1e-12)
    def test_config_loading_infers_target_q_mode_when_q_targets_are_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "  q_primary_target: 19.0",
                        "  q_secondary_target: 17.0",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.target.q_target_mode, "target")
        self.assertAlmostEqual(cfg.target.q_primary_target, 19.0, delta=1e-12)
        self.assertAlmostEqual(cfg.target.q_secondary_target, 17.0, delta=1e-12)
    def test_config_loading_rejects_target_q_mode_without_both_q_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "  q_target_mode: target",
                        "  q_primary_target: 18.5",
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "q_target_mode='target'"):
                load_run_config(path)
    def test_config_loading_supports_primary_vdd_bar_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "topology:",
                        "  primary_center_tap: true",
                        "  primary_vdd_bar_enabled: true",
                        "  primary_vdd_bar_layer: 139",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertTrue(cfg.bounds.primary.vdd_bar.enabled)
        self.assertEqual(cfg.bounds.primary.vdd_bar.bar_layer, 139)
        self.assertIsNotNone(cfg.bounds.primary.vdd_bar.route_layer)
        self.assertIsNotNone(cfg.bounds.primary.vdd_bar.route_via_layer)
    def test_config_loading_supports_primary_vdd_bar_width(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "topology:",
                        "  primary_center_tap: true",
                        "  primary_vdd_bar_enabled: true",
                        "  primary_vdd_bar_layer: 139",
                        "  primary_vdd_bar_width_um: 18.5",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertTrue(cfg.bounds.primary.vdd_bar.enabled)
        self.assertAlmostEqual(cfg.bounds.primary.vdd_bar.width_um, 18.5, delta=1e-12)
    def test_config_loading_supports_primary_vdd_bar_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "topology:",
                        "  primary_center_tap: true",
                        "  primary_vdd_bar_enabled: true",
                        "  primary_vdd_bar_layer: 139",
                        "  primary_vdd_bar_offset_um: 6.5",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertTrue(cfg.bounds.primary.vdd_bar.enabled)
        self.assertAlmostEqual(cfg.bounds.primary.vdd_bar.offset_um, 6.5, delta=1e-12)
    def test_config_loading_supports_nested_topology_primary_vdd_bar(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "topology:",
                        "  primary:",
                        "    center_tap: true",
                        "    vdd_bar:",
                        "      enabled: true",
                        "      bar_layer: 139",
                        "      width_um: 18.5",
                        "      offset_um: 4.0",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertTrue(cfg.bounds.primary.vdd_bar.enabled)
        self.assertEqual(cfg.bounds.primary.vdd_bar.bar_layer, 139)
        self.assertAlmostEqual(cfg.bounds.primary.vdd_bar.width_um, 18.5, delta=1e-12)
        self.assertAlmostEqual(cfg.bounds.primary.vdd_bar.offset_um, 4.0, delta=1e-12)
    def test_config_loading_rejects_vdd_bar_without_center_tap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "topology:",
                        "  primary_center_tap: false",
                        "  primary_vdd_bar_enabled: true",
                        "  primary_vdd_bar_layer: 139",
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "primary_vdd_bar"):
                load_run_config(path)
    def test_config_loading_supports_nested_optimizer_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "optimizer:",
                        "  name: turbo",
                        "  max_evaluations: 22",
                        "  warm_start_samples: 5",
                        "  seed: 19",
                        "  turbo:",
                        "    initial_length: 0.6",
                        "    raw_samples: 64",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.optimizer.name, "turbo")
        self.assertEqual(cfg.optimizer.max_evaluations, 22)
        self.assertEqual(cfg.optimizer.warm_start_samples, 5)
        self.assertEqual(cfg.optimizer.warm_start_paths, tuple())
        self.assertEqual(cfg.optimizer.seed, 19)
        self.assertFalse(cfg.optimizer.resume_from_checkpoint)
        self.assertEqual(cfg.optimizer.checkpoint_interval_evaluations, 1)
        self.assertAlmostEqual(cfg.optimizer.turbo.initial_length, 0.6, delta=1e-12)
        self.assertEqual(cfg.optimizer.turbo.raw_samples, 64)

    def test_config_loading_reads_checkpoint_and_warm_start_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "optimizer:",
                        "  name: cma_es",
                        "  warm_start_paths:",
                        "    - /tmp/run_a/optimization_summary.json",
                        "    - /tmp/run_b/summary.json",
                        "  resume_from_checkpoint: true",
                        "  checkpoint_interval_evaluations: 7",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(
            cfg.optimizer.warm_start_paths,
            ("/tmp/run_a/optimization_summary.json", "/tmp/run_b/summary.json"),
        )
        self.assertTrue(cfg.optimizer.resume_from_checkpoint)
        self.assertEqual(cfg.optimizer.checkpoint_interval_evaluations, 7)
    def test_config_loading_rejects_removed_optimizer_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "optimizer:",
                        "  name: legacy_de_powell",
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Only 'cma_es' and 'turbo' are supported"):
                load_run_config(path)
    def test_config_loading_allows_single_turn_override_with_null_bridge_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 2t2t",
                        "topology:",
                        "  primary_turns: 1",
                        "  secondary_turns: 1",
                        "  primary_center_tap: false",
                        "  secondary_center_tap: false",
                        "  primary_bridge_section_containment_margin_ratio: null",
                        "  secondary_bridge_section_containment_margin_ratio: null",
                        "bounds:",
                        "  primary_bridge_section_containment_margin_ratio: null",
                        "  secondary_bridge_section_containment_margin_ratio: null",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.bounds.primary.turns, 1)
        self.assertEqual(cfg.bounds.secondary.turns, 1)
        self.assertIsNone(cfg.bounds.primary.bridge_section)
        self.assertIsNone(cfg.bounds.secondary.bridge_section)

    def test_config_loading_accepts_gui_style_topology_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "gui_current_config.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 2t2t",
                        "topology:",
                        "  primary_turns: 2",
                        "  secondary_turns: 2",
                        "  primary_center_tap: true",
                        "  secondary_center_tap: true",
                        "bounds:",
                        "  primary_outer_width_um: [224.0, 256.0]",
                        "  primary_outer_height_um: [224.0, 256.0]",
                        "  secondary_outer_width_um: [224.0, 256.0]",
                        "  secondary_outer_height_um: [224.0, 256.0]",
                        "  primary_width_um: [12.0, 18.0]",
                        "  primary_spacing_um: [18.0, 26.0]",
                        "  primary_terminal_y_span_um: [54.0, 76.0]",
                        "  primary_feed_extension_um: [54.0, 70.0]",
                        "  secondary_width_um: [12.0, 18.0]",
                        "  secondary_spacing_um: [18.0, 26.0]",
                        "  secondary_terminal_y_span_um: [54.0, 76.0]",
                        "  secondary_feed_extension_um: [54.0, 70.0]",
                        "  offset_um: [-40.0, -24.0]",
                        "emx:",
                        "  emx_process_file: examples/proc/synthetic_typical.proc",
                        "  ap_layer: 74",
                        "  m9_layer: 139",
                        "  m5_layer: 35",
                        "  primary_bridge_layer: 139",
                        "  secondary_bridge_layer: 138",
                        "optimizer:",
                        "  name: cma_es",
                        "  max_evaluations: 32",
                        "  warm_start_samples: 4",
                        "  seed: 7",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.bounds.topology_mode, "2t2t")
        self.assertEqual(cfg.bounds.primary.turns, 2)
        self.assertEqual(cfg.bounds.secondary.turns, 2)
        self.assertAlmostEqual(cfg.bounds.midpoint().offset_um, -32.0, delta=1e-12)
    def test_config_loading_accepts_nested_topology_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "gui_nested_topology.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 2t2t",
                        "topology:",
                        "  primary:",
                        "    turns: 1",
                        "    center_tap: true",
                        "    vdd_bar:",
                        "      enabled: true",
                        "      bar_layer: 139",
                        "      width_um: 18.0",
                        "  secondary:",
                        "    turns: 2",
                        "    center_tap: true",
                        "  shield:",
                        "    enabled: true",
                        "    kind: ring",
                        "    margin_um: 14.0",
                        "    width_um: 8.0",
                        "bounds:",
                        "  primary_outer_width_um: [224.0, 256.0]",
                        "  primary_outer_height_um: [224.0, 256.0]",
                        "  secondary_outer_width_um: [224.0, 256.0]",
                        "  secondary_outer_height_um: [224.0, 256.0]",
                        "  primary_width_um: [12.0, 18.0]",
                        "  primary_terminal_y_span_um: [54.0, 76.0]",
                        "  primary_feed_extension_um: [54.0, 70.0]",
                        "  secondary_width_um: [12.0, 18.0]",
                        "  secondary_spacing_um: [18.0, 26.0]",
                        "  secondary_terminal_y_span_um: [54.0, 76.0]",
                        "  secondary_feed_extension_um: [54.0, 70.0]",
                        "  offset_um: [-40.0, -24.0]",
                        "emx:",
                        "  ap_layer: 74",
                        "  m9_layer: 139",
                        "  m5_layer: 35",
                        "  primary_bridge_layer: 139",
                        "  secondary_bridge_layer: 138",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.bounds.topology_mode, "1t2t")
        self.assertEqual(cfg.bounds.primary.turns, 1)
        self.assertEqual(cfg.bounds.secondary.turns, 2)
        self.assertTrue(cfg.bounds.primary.center_tap)
        self.assertTrue(cfg.bounds.primary.vdd_bar.enabled)
        self.assertTrue(cfg.bounds.shield.enabled)
        self.assertAlmostEqual(cfg.bounds.shield.margin_um, 14.0, delta=1e-12)
    def test_config_loading_accepts_model_shaped_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "model_shaped.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 2t2t",
                        "  f0_hz: 15000000000.0",
                        "  lp_h: 1.155247e-09",
                        "  ls_h: 1.39977e-09",
                        "  k_target: 0.759752",
                        "bounds:",
                        "  topology_mode: 2t2t",
                        "  offset_um: [-40.0, -24.0]",
                        "  shield:",
                        "    enabled: true",
                        "    kind: ring",
                        "    margin_um: 50.0",
                        "    width_um: 10.0",
                        "  primary:",
                        "    outer_width_um: [160.0, 320.0]",
                        "    outer_height_um: [160.0, 320.0]",
                        "    trace_width_um: [16.0, 24.0]",
                        "    spacing_um: [2.0, 14.0]",
                        "    terminal_y_span_um: [24.0, 180.0]",
                        "    feed_extension_um: [24.0, 100.0]",
                        "    turns: 1",
                        "    center_tap: true",
                        "    bridge_section: null",
                        "    vdd_bar:",
                        "      enabled: true",
                        "      bar_layer: 74",
                        "  secondary:",
                        "    outer_width_um: [160.0, 320.0]",
                        "    outer_height_um: [160.0, 320.0]",
                        "    trace_width_um: [8.0, 12.0]",
                        "    spacing_um: [2.0, 14.0]",
                        "    terminal_y_span_um: [60.0, 180.0]",
                        "    feed_extension_um: [24.0, 100.0]",
                        "    turns: 1",
                        "    center_tap: false",
                        "    bridge_section: null",
                        "    vdd_bar:",
                        "      enabled: false",
                        "      bar_layer: 74",
                        "emx:",
                        "  ap_layer: 74",
                        "  m9_layer: 139",
                        "  m5_layer: 35",
                        "  primary_bridge_layer: 139",
                        "  primary_bridge_via_layer: 85",
                        "  secondary_bridge_layer: 138",
                        "  secondary_bridge_via_layer: 58",
                        "  shield_layer: 35",
                        "optimizer:",
                        "  name: cma_es",
                        "  max_evaluations: 552",
                        "  warm_start_samples: 18",
                        "  seed: 1234",
                        "  cma_es:",
                        "    population_size: null",
                        "    sigma0: null",
                        "    verbose: -9",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.bounds.topology_mode, "1t1t")
        self.assertTrue(cfg.bounds.shield.enabled)
        self.assertAlmostEqual(cfg.bounds.shield.margin_um, 50.0, delta=1e-12)
        self.assertEqual(cfg.bounds.primary.turns, 1)
        self.assertTrue(cfg.bounds.primary.center_tap)
        self.assertTrue(cfg.bounds.primary.vdd_bar.enabled)
        self.assertEqual(cfg.emx.ap_layer, 74)
        self.assertEqual(cfg.emx.m9_layer, 39)
        self.assertEqual(cfg.optimizer.name, "cma_es")

    def test_config_loading_inferrs_mixed_topology_from_turn_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "mixed_turns.yaml"
            path.write_text(
                "\n".join(
                    [
                        "topology:",
                        "  primary_turns: 1",
                        "  secondary_turns: 2",
                        "emx:",
                        "  primary_bridge_layer: 139",
                        "  secondary_bridge_layer: 138",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.bounds.topology_mode, "1t2t")
        self.assertEqual(cfg.target.topology_mode, "1t2t")
        self.assertEqual(cfg.bounds.primary.turns, 1)
        self.assertEqual(cfg.bounds.secondary.turns, 2)
        self.assertIsNone(cfg.bounds.primary.bridge_section)
        self.assertIsNotNone(cfg.bounds.secondary.bridge_section)

    def test_config_loading_accepts_semantic_coil_layer_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "semantic_layers.yaml"
            path.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "emx:",
                        "  primary_coil_layer: 74",
                        "  secondary_coil_layer: 139",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = load_run_config(path)
        self.assertEqual(cfg.emx.primary_coil_layer, 74)
        self.assertEqual(cfg.emx.secondary_coil_layer, 39)
        self.assertEqual(cfg.emx.ap_layer, 74)
        self.assertEqual(cfg.emx.m9_layer, 39)
