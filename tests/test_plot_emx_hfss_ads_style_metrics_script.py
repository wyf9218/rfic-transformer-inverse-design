from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_plot_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plot_emx_hfss_ads_style_metrics.py"
    spec = importlib.util.spec_from_file_location("plot_emx_hfss_ads_style_metrics_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_touchstone_ri(path: Path, freqs_hz: np.ndarray, s_matrix: np.ndarray, *, reference_ohm: float = 50.0) -> None:
    n_ports = int(s_matrix.shape[1])
    rows = [f"# GHz S RI R {reference_ohm:g}"]
    for idx, freq_hz in enumerate(freqs_hz):
        values = [f"{freq_hz / 1.0e9:.12g}"]
        for row in range(n_ports):
            for col in range(n_ports):
                value = complex(s_matrix[idx, row, col])
                values.extend([f"{value.real:.16e}", f"{value.imag:.16e}"])
        rows.append(" ".join(values))
    path.write_text("\n".join(rows) + "\n", encoding="ascii")


class PlotEmxHfssAdsStyleMetricsScriptTest(TransformerToolboxTestBase):
    def test_plot_summary_blocks_when_emx_first_is_not_accepted(self) -> None:
        plot = _load_plot_module()

        evidence = plot._plot_evidence_boundary(
            {
                "overall_status": "FAIL",
                "decision": "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE",
            },
            {
                "overall_status": "BLOCKED_BY_EMX_REFERENCE",
                "decision": "DO_NOT_USE_HFSS_COMPARISON",
            },
        )

        self.assertEqual(evidence["overall_status"], "BLOCKED_BY_EMX_REFERENCE")
        self.assertEqual(evidence["decision"], "DO_NOT_USE_AS_FINAL_LP_LS_Q_K_FIGURES")
        self.assertEqual(evidence["evidence_use"], "BLOCKED_AS_FINAL_EVIDENCE")
        self.assertIn("must not be used as final", evidence["note"])

    def test_plot_summary_stays_diagnostic_without_full_chain_acceptance(self) -> None:
        plot = _load_plot_module()

        evidence = plot._plot_evidence_boundary(
            {
                "overall_status": "PASS",
                "decision": "ACCEPT_AS_GOLDEN_EMX_REFERENCE",
            },
            {
                "overall_status": "BLOCKED_BY_HFSS_GEOMETRY_GATE",
                "decision": "WAIT_FOR_HFSS_GEOMETRY_AUDIT",
            },
        )

        self.assertEqual(evidence["overall_status"], "DIAGNOSTIC_ONLY")
        self.assertEqual(evidence["decision"], "WAIT_FOR_ACCEPTED_EMX_HFSS_ADS_VALIDATION_CHAIN")
        self.assertEqual(evidence["evidence_use"], "DIAGNOSTIC_ONLY")

    def test_plot_summary_defers_to_final_figure_verifier_even_after_chain_acceptance(self) -> None:
        plot = _load_plot_module()

        evidence = plot._plot_evidence_boundary(
            {
                "overall_status": "PASS",
                "decision": "ACCEPT_AS_GOLDEN_EMX_REFERENCE",
            },
            {
                "overall_status": "PASS",
                "decision": "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN",
            },
        )

        self.assertEqual(evidence["overall_status"], "DIAGNOSTIC_ONLY")
        self.assertEqual(evidence["decision"], "USE_ACCEPTED_FINAL_FIGURE_VERIFIER_FOR_REPORTABLE_LP_LS_Q_K")
        self.assertIn("accepted final-figure verifier", evidence["note"])

    def test_common_window_names_are_derived_from_frequency_grid(self) -> None:
        plot = _load_plot_module()

        freqs = np.asarray([5.0e9, 15.0e9, 50.0e9])

        self.assertEqual(plot._window_tag_from_freq_hz(freqs), "5_50GHz")
        self.assertEqual(plot._window_label_from_freq_hz(freqs), "5-50 GHz")
        self.assertIn("5-50 GHz", plot._emx_scope_note(freqs))

    def test_emx_scope_note_warns_for_narrowband_reference(self) -> None:
        plot = _load_plot_module()

        freqs = np.asarray([13.5e9, 15.0e9, 16.5e9])

        note = plot._emx_scope_note(freqs)
        self.assertIn("13.5-16.5 GHz", note)
        self.assertIn("do not claim 5-60 GHz", note)

    def test_s8p_plot_extraction_requires_explicit_port_pairs(self) -> None:
        plot = _load_plot_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "sample.s8p"
            freq_hz = np.asarray([10.0e9], dtype=float)
            z = np.eye(8, dtype=np.complex128).reshape(1, 8, 8) * 50.0
            _write_touchstone_ri(touchstone, freq_hz, z_to_s(z, z0=50.0))

            with self.assertRaisesRegex(ValueError, "S8P ADS-style metric plotting requires explicit differential port pairs"):
                plot._extract_metric_curves("EMX", touchstone, None)

    def test_s8p_plot_extraction_uses_the_best_winding_pairs(self) -> None:
        plot = _load_plot_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "sample.s8p"
            freq_hz = np.asarray([10.0e9], dtype=float)
            omega = 2.0 * np.pi * freq_hz[0]
            z = np.eye(8, dtype=np.complex128).reshape(1, 8, 8) * 5.0
            for port in (0, 3):
                z[0, port, port] = 20.0 + 1j * omega * 0.5e-9
            for port in (4, 5):
                z[0, port, port] = 18.0 + 1j * omega * 0.6e-9
            z[0, 0, 4] = 1j * omega * 0.30e-9
            z[0, 4, 0] = 1j * omega * 0.30e-9
            _write_touchstone_ri(touchstone, freq_hz, z_to_s(z, z0=50.0))

            curves = plot._extract_metric_curves("EMX", touchstone, "1,4:5,6")

            self.assertEqual(curves.n_ports, 8)
            self.assertEqual(curves.port_pairs, "1,4:5,6")
            self.assertAlmostEqual(curves.lp_nh[0], 1.0, places=6)
            self.assertAlmostEqual(curves.ls_nh[0], 1.2, places=6)
            self.assertAlmostEqual(curves.k[0], 0.30e-9 / np.sqrt(1.0e-9 * 1.2e-9), places=6)
            self.assertAlmostEqual(curves.qp[0], (omega * 1.0e-9) / 40.0, places=6)
            self.assertAlmostEqual(curves.qs[0], (omega * 1.2e-9) / 36.0, places=6)
            self.assertAlmostEqual(curves.q[0], min(curves.qp[0], curves.qs[0]), places=6)
