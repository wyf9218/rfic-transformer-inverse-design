import argparse

from tests.rfic_transformer_inverse_design.shared import *
from tests.rfic_transformer_inverse_design.shared import _load_script_module, _write_touchstone


class TransformerCliTest(TransformerToolboxTestBase):
    def test_cli_topology_overrides_keep_target_and_bounds_in_sync_for_mixed_turns(self) -> None:
        module = _load_script_module("rfic_transformer_inverse_design_cli_topology_override_test")
        cfg = default_run_config("1t1t")
        args = argparse.Namespace(
            primary_turns=2,
            secondary_turns=1,
            primary_center_tap=None,
            secondary_center_tap=None,
            optimizer_name=None,
        )
        updated = module._apply_topology_overrides(args, cfg)
        self.assertEqual(updated.bounds.topology_mode, "2t1t")
        self.assertEqual(updated.target.topology_mode, "2t1t")
        self.assertEqual(updated.bounds.primary.turns, 2)
        self.assertEqual(updated.bounds.secondary.turns, 1)

    def test_cli_topology_overrides_rebuild_bridge_stack_for_2t2t(self) -> None:
        module = _load_script_module("rfic_transformer_inverse_design_cli_topology_override_2t2t_test")
        cfg = default_run_config("1t1t")
        args = argparse.Namespace(
            primary_turns=2,
            secondary_turns=2,
            primary_center_tap=None,
            secondary_center_tap=None,
            optimizer_name=None,
        )
        updated = module._apply_topology_overrides(args, cfg)
        self.assertEqual(updated.bounds.topology_mode, "2t2t")
        self.assertEqual(updated.target.topology_mode, "2t2t")
        self.assertEqual(updated.bounds.primary.turns, 2)
        self.assertEqual(updated.bounds.secondary.turns, 2)
        self.assertEqual(updated.emx.primary_bridge_layer, 39)
        self.assertEqual(updated.emx.primary_bridge_lower_layer, 38)
        self.assertEqual(updated.emx.secondary_bridge_layer, 38)
        self.assertEqual(updated.bounds.primary.bridge_layer, 39)
        self.assertEqual(updated.bounds.primary.bridge_lower_layer, 38)
        self.assertEqual(updated.bounds.secondary.bridge_layer, 38)

    def test_cli_topology_overrides_preserve_parsed_s8p_differential_pairs(self) -> None:
        module = _load_script_module("rfic_transformer_inverse_design_cli_s8p_port_pair_test")
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            emx=replace(
                cfg.emx,
                differential_port_pairs=((0, 1), (6, 7)),
            ),
        )
        args = argparse.Namespace(
            primary_turns=None,
            secondary_turns=None,
            primary_center_tap=None,
            secondary_center_tap=None,
            optimizer_name=None,
        )

        updated = module._apply_topology_overrides(args, cfg)

        self.assertEqual(updated.emx.differential_port_pairs, ((0, 1), (6, 7)))

    def test_cli_create_only_and_compare_lumped(self) -> None:
        module = _load_script_module("rfic_transformer_inverse_design_cli_test")
        target = default_target_spec("1t1t")
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target)
        single = differential_2port_to_4port_s(freqs, diff.s_matrix)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            create_argv = [
                "rfic-transformer-inverse-design",
                "create-only",
                "--out-dir",
                str(root / "create"),
            ]
            with mock.patch("sys.argv", create_argv):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    module.main()
            create_payload = json.loads(stdout.getvalue())
            self.assertTrue(create_payload["ok"])
            self.assertTrue(Path(create_payload["artifacts"]["gds"]).exists())

            touchstone_path = root / "synthetic.s4p"
            _write_touchstone(touchstone_path, single.freqs_hz, single.s_matrix)
            compare_argv = [
                "rfic-transformer-inverse-design",
                "compare-lumped",
                "--touchstone",
                str(touchstone_path),
                "--out-dir",
                str(root / "compare"),
            ]
            with mock.patch("sys.argv", compare_argv):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    module.main()
            compare_payload = json.loads(stdout.getvalue())
            self.assertTrue(Path(compare_payload["compare_path"]).exists())
