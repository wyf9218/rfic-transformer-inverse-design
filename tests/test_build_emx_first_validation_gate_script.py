from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys
from types import SimpleNamespace


def _load_gate_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_emx_first_validation_gate.py"
    spec = importlib.util.spec_from_file_location("build_emx_first_validation_gate_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_synthetic_transformer_s4p(path: Path, freqs_hz: np.ndarray) -> None:
    target = default_target_spec()
    diff = build_lumped_transformer_sparameters(freqs_hz=freqs_hz, target=target, q_primary=18.0, q_secondary=16.0)
    single = differential_2port_to_4port_s(
        freqs_hz=freqs_hz,
        s_diff=diff.s_matrix,
        diff_z0_ohm=target.differential_reference_impedance_ohm,
        single_z0_ohm=50.0,
    )
    _write_touchstone(path, single.freqs_hz, single.s_matrix)


class BuildEmxFirstValidationGateScriptTest(TransformerToolboxTestBase):
    def test_port_pair_enumeration_covers_ordered_four_port_pairings(self) -> None:
        gate = _load_gate_module()

        pairs = gate._all_ordered_port_pairs()

        self.assertEqual(len(pairs), 24)
        self.assertIn("1,2:3,4", pairs)
        for spec in pairs:
            first, second = spec.split(":")
            ports = [int(item) for item in first.split(",") + second.split(",")]
            self.assertEqual(sorted(ports), [1, 2, 3, 4])

    def test_narrow_mismatched_emx_is_blocked_as_golden_reference(self) -> None:
        gate = _load_gate_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "evaluations" / "a" / "emx" / "emx.s4p"
            touchstone.parent.mkdir(parents=True)
            _write_synthetic_transformer_s4p(touchstone, np.asarray([13.5e9, 15.0e9, 16.5e9]))

            status = gate.main(
                [
                    "--emx-s4p",
                    str(touchstone),
                    "--out-dir",
                    str(root / "gate"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "gate" / "emx_first_validation_gate_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["final ADS sweep coverage"]["status"], "FAIL")
            self.assertEqual(checks["ADS no-extrapolation plot grid"]["status"], "FAIL")
            self.assertIn("ADS would extrapolate", checks["ADS no-extrapolation plot grid"]["detail"])
            self.assertEqual(checks["ADS photo anchor"]["status"], "FAIL")
            self.assertEqual(checks["differential Z finiteness"]["status"], "PASS")
            self.assertEqual(checks["differential Z reciprocity"]["status"], "PASS")
            self.assertEqual(checks["differential Z positive-realness"]["status"], "PASS")
            self.assertIn("differential_z_quality", summary)
            self.assertIn("basic numeric physics sanity", checks)
            self.assertEqual(checks["physical metric window"]["status"], "FAIL")
            self.assertIn("window_start_missing", checks["physical metric window"]["detail"])
            self.assertEqual(checks["smooth transformer metric window"]["status"], "FAIL")
            self.assertIn("window_stop_missing", checks["smooth transformer metric window"]["detail"])
            self.assertNotIn("target transformer sanity", checks)
            self.assertEqual(checks["approved port-pair photo alignment"]["status"], "FAIL")
            self.assertTrue(
                any("not a golden-reference acceptance" in note for note in summary["method_notes"])
            )
            self.assertTrue(
                any("differential Z gate" in note for note in summary["method_notes"])
            )
            self.assertTrue((root / "gate" / "emx_first_validation_gate_ads_style_metrics.png").exists())
            self.assertTrue((root / "gate" / "emx_first_validation_gate_port_pair_sensitivity.csv").exists())

    def test_frequency_gate_rejects_nonuniform_grid_even_when_median_step_matches(self) -> None:
        gate = _load_gate_module()
        freqs_hz = np.asarray([5.0e9, 5.1e9, 5.2e9, 5.4e9, 5.45e9, 5.5e9], dtype=float)
        args = SimpleNamespace(
            required_sweep_start_ghz=5.0,
            required_sweep_stop_ghz=5.5,
            required_sweep_step_ghz=0.1,
            required_sweep_points=6,
            frequency_tolerance_hz=1.0e5,
            target_ghz=5.2,
        )

        checks = {item.name: item for item in gate._frequency_checks(freqs_hz, args)}

        self.assertEqual(checks["final ADS sweep coverage"].status, "FAIL")
        self.assertIn("per-step grid", checks["final ADS sweep coverage"].detail)
        self.assertEqual(checks["ADS no-extrapolation plot grid"].status, "FAIL")
        self.assertIn("missing_requested_plot_points", checks["ADS no-extrapolation plot grid"].detail)

    def test_physical_window_rejects_near_zero_coupling(self) -> None:
        gate = _load_gate_module()
        freqs_hz = np.linspace(5.0e9, 30.0e9, 251)
        curves = SimpleNamespace(
            freq_hz=freqs_hz,
            lp_nh=np.full(freqs_hz.size, 0.85),
            ls_nh=np.full(freqs_hz.size, 0.82),
            k=np.full(freqs_hz.size, 0.01),
            qp=np.full(freqs_hz.size, 15.0),
            qs=np.full(freqs_hz.size, 14.0),
            cm_single_primary_ff=np.full(freqs_hz.size, 10.0),
        )
        args = SimpleNamespace(
            physical_window_start_ghz=5.0,
            physical_window_stop_ghz=30.0,
            frequency_tolerance_hz=1.0e5,
            min_target_inductance_nh=0.05,
            min_target_q=1.0,
            min_target_abs_k=0.05,
            min_window_abs_k=None,
            max_target_abs_k=0.98,
        )

        checks = {item.name: item for item in gate._physical_window_checks(curves, args)}

        self.assertEqual(checks["physical metric window"].status, "FAIL")
        self.assertIn("abs_K_min", checks["physical metric window"].detail)

    def test_shape_window_rejects_spiky_metric_curve(self) -> None:
        gate = _load_gate_module()
        freqs_hz = np.linspace(5.0e9, 30.0e9, 251)
        qp = np.full(freqs_hz.size, 15.0)
        qp[120] = 500.0
        curves = SimpleNamespace(
            freq_hz=freqs_hz,
            lp_nh=np.full(freqs_hz.size, 0.85),
            ls_nh=np.full(freqs_hz.size, 0.82),
            k=np.full(freqs_hz.size, 0.5),
            qp=qp,
            qs=np.full(freqs_hz.size, 14.0),
            cm_single_primary_ff=np.full(freqs_hz.size, 10.0),
        )
        args = SimpleNamespace(
            shape_window_start_ghz=5.0,
            shape_window_stop_ghz=30.0,
            frequency_tolerance_hz=1.0e5,
            max_shape_spike_ratio=4.0,
            max_shape_relative_step=0.25,
        )

        checks = {item.name: item for item in gate._shape_window_checks(curves, args)}

        self.assertEqual(checks["smooth transformer metric window"].status, "FAIL")
        self.assertIn("Qp", checks["smooth transformer metric window"].detail)

    def test_frequency_gate_rejects_wrong_point_count(self) -> None:
        gate = _load_gate_module()
        freqs_hz = np.asarray([5.0e9, 5.1e9, 5.2e9], dtype=float)
        args = SimpleNamespace(
            required_sweep_start_ghz=5.0,
            required_sweep_stop_ghz=5.2,
            required_sweep_step_ghz=0.1,
            required_sweep_points=4,
            frequency_tolerance_hz=1.0e5,
            target_ghz=5.1,
        )

        checks = {item.name: item for item in gate._frequency_checks(freqs_hz, args)}

        self.assertEqual(checks["final ADS sweep coverage"].status, "FAIL")
        self.assertIn("points 3 != required 4", checks["final ADS sweep coverage"].detail)
        self.assertEqual(checks["ADS no-extrapolation plot grid"].status, "FAIL")
        self.assertIn("requested_grid_inconsistent", checks["ADS no-extrapolation plot grid"].detail)
        self.assertIn("file_point_count=3", checks["ADS no-extrapolation plot grid"].detail)
