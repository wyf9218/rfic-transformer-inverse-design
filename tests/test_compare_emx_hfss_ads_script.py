from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_compare_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "compare_emx_hfss_ads.py"
    spec = importlib.util.spec_from_file_location("compare_emx_hfss_ads_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_metric_csv(path: Path, *, scale: float = 1.0, freqs_ghz: tuple[float, ...] = (5.0, 6.0, 7.0)) -> None:
    rows = ["freq_ghz,k,qp,qs,lp_nh,ls_nh"]
    for index, freq in enumerate(freqs_ghz):
        rows.append(
            f"{freq},{0.50 + 0.01 * index:.6g},{(10.0 + 0.2 * index) * scale:.6g},"
            f"{(12.0 + 0.2 * index) * scale:.6g},{(1.00 + 0.01 * index) * scale:.6g},"
            f"{(1.20 + 0.01 * index) * scale:.6g}"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


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


class CompareEmxHfssAdsScriptTest(TransformerToolboxTestBase):
    def test_s4p_k_uses_ads_reverse_transfer_mutual_term(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "nonreciprocal_ads_formula_check.s4p"
            freq_hz = np.asarray([10.0e9], dtype=float)
            omega = 2.0 * np.pi * freq_hz[0]
            z = np.zeros((1, 4, 4), dtype=np.complex128)
            diag = 20.0 + 1j * omega * 0.5e-9
            z[0, 0, 0] = diag
            z[0, 1, 1] = diag
            z[0, 2, 2] = 20.0 + 1j * omega * 0.6e-9
            z[0, 3, 3] = 20.0 + 1j * omega * 0.6e-9
            z[0, 0, 2] = 1j * omega * 0.10e-9
            z[0, 2, 0] = 1j * omega * 0.30e-9
            _write_touchstone(touchstone, freq_hz, z_to_s(z, z0=50.0))

            curves = compare.load_touchstone_curves(touchstone, port_pairs=((0, 1), (2, 3)))

            expected_k = 0.30e-9 / np.sqrt(1.0e-9 * 1.2e-9)
            old_forward_k = 0.10e-9 / np.sqrt(1.0e-9 * 1.2e-9)
            self.assertAlmostEqual(curves.metrics["k"][0], expected_k, places=6)
            self.assertNotAlmostEqual(curves.metrics["k"][0], old_forward_k, places=6)

    def test_s4p_reference_impedance_from_touchstone_is_used_for_z_conversion(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "r75_reference.s4p"
            freq_hz = np.asarray([10.0e9], dtype=float)
            omega = 2.0 * np.pi * freq_hz[0]
            z = np.zeros((1, 4, 4), dtype=np.complex128)
            z[0, 0, 0] = 20.0 + 1j * omega * 0.5e-9
            z[0, 1, 1] = 20.0 + 1j * omega * 0.5e-9
            z[0, 2, 2] = 18.0 + 1j * omega * 0.6e-9
            z[0, 3, 3] = 18.0 + 1j * omega * 0.6e-9
            z[0, 2, 0] = 1j * omega * 0.30e-9
            _write_touchstone_ri(touchstone, freq_hz, z_to_s(z, z0=75.0), reference_ohm=75.0)

            curves = compare.load_touchstone_curves(touchstone, port_pairs=((0, 1), (2, 3)))

            self.assertAlmostEqual(curves.metrics["lp_nh"][0], 1.0, places=6)
            self.assertAlmostEqual(curves.metrics["ls_nh"][0], 1.2, places=6)
            self.assertAlmostEqual(curves.metrics["k"][0], 0.30e-9 / np.sqrt(1.0e-9 * 1.2e-9), places=6)

    def test_s8p_requires_explicit_port_pairs(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "power_line_topology.s8p"
            freq_hz = np.asarray([10.0e9], dtype=float)
            z = np.eye(8, dtype=np.complex128).reshape(1, 8, 8) * 50.0
            _write_touchstone_ri(touchstone, freq_hz, z_to_s(z, z0=50.0))

            with self.assertRaisesRegex(ValueError, "8-port Touchstone extraction requires explicit differential port pairs"):
                compare.load_touchstone_curves(touchstone)

    def test_s8p_explicit_port_pairs_extract_transformer_metrics(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "power_line_topology.s8p"
            freq_hz = np.asarray([10.0e9], dtype=float)
            omega = 2.0 * np.pi * freq_hz[0]
            z = np.eye(8, dtype=np.complex128).reshape(1, 8, 8) * 5.0
            for port in (0, 1):
                z[0, port, port] = 20.0 + 1j * omega * 0.5e-9
            for port in (6, 7):
                z[0, port, port] = 18.0 + 1j * omega * 0.6e-9
            z[0, 6, 0] = 1j * omega * 0.30e-9
            _write_touchstone_ri(touchstone, freq_hz, z_to_s(z, z0=50.0))

            curves = compare.load_touchstone_curves(touchstone, port_pairs=((0, 1), (6, 7)))

            self.assertAlmostEqual(curves.metrics["lp_nh"][0], 1.0, places=6)
            self.assertAlmostEqual(curves.metrics["ls_nh"][0], 1.2, places=6)
            self.assertAlmostEqual(curves.metrics["k"][0], 0.30e-9 / np.sqrt(1.0e-9 * 1.2e-9), places=6)
            self.assertAlmostEqual(curves.metrics["qp"][0], (omega * 1.0e-9) / 40.0, places=6)
            self.assertAlmostEqual(curves.metrics["qs"][0], (omega * 1.2e-9) / 36.0, places=6)
            self.assertAlmostEqual(curves.metrics["q"][0], min(curves.metrics["qp"][0], curves.metrics["qs"][0]), places=6)

    def test_main_enforces_final_s8p_contract_when_requested(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_path = root / "emx.s8p"
            hfss_path = root / "hfss.s8p"
            out_dir = root / "out"
            freq_hz = np.asarray([10.0e9, 10.5e9], dtype=float)
            z = np.zeros((2, 8, 8), dtype=np.complex128)
            for idx, freq in enumerate(freq_hz):
                omega = 2.0 * np.pi * freq
                z[idx] = np.eye(8, dtype=np.complex128) * 5.0
                for port in (0, 3):
                    z[idx, port, port] = 20.0 + 1j * omega * 0.5e-9
                for port in (4, 5):
                    z[idx, port, port] = 18.0 + 1j * omega * 0.6e-9
                z[idx, 4, 0] = 1j * omega * 0.30e-9
            s = z_to_s(z, z0=50.0)
            _write_touchstone_ri(emx_path, freq_hz, s, reference_ohm=50.0)
            _write_touchstone_ri(hfss_path, freq_hz, s, reference_ohm=50.0)

            status = compare.main(
                [
                    "--emx",
                    str(emx_path),
                    "--hfss",
                    str(hfss_path),
                    "--out-dir",
                    str(out_dir),
                    "--emx-port-pairs",
                    "1,4:5,6",
                    "--hfss-port-pairs",
                    "1,4:5,6",
                    "--compare-start-ghz",
                    "10",
                    "--compare-stop-ghz",
                    "10.5",
                    "--min-frequency-points",
                    "2",
                    "--expected-frequency-step-ghz",
                    "0.5",
                    "--expected-frequency-points",
                    "2",
                    "--require-matching-frequency-grid",
                    "--require-touchstone-suffix",
                    ".s8p",
                    "--expected-port-count",
                    "8",
                    "--expected-reference-ohm",
                    "50",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "emx_hfss_ads_comparison_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")

    def test_main_rejects_non_s8p_when_final_contract_requested(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_path = root / "emx.s4p"
            hfss_path = root / "hfss.s8p"
            out_dir = root / "out"
            freq_hz = np.asarray([10.0e9, 10.5e9], dtype=float)
            _write_touchstone_ri(emx_path, freq_hz, np.zeros((2, 4, 4), dtype=np.complex128), reference_ohm=50.0)
            _write_touchstone_ri(hfss_path, freq_hz, np.zeros((2, 8, 8), dtype=np.complex128), reference_ohm=50.0)

            with self.assertRaisesRegex(ValueError, "EMX input must be .s8p"):
                compare.main(
                    [
                        "--emx",
                        str(emx_path),
                        "--hfss",
                        str(hfss_path),
                        "--out-dir",
                        str(out_dir),
                        "--emx-port-pairs",
                        "1,4:5,6",
                        "--hfss-port-pairs",
                        "1,4:5,6",
                        "--require-touchstone-suffix",
                        ".s8p",
                        "--expected-port-count",
                        "8",
                        "--expected-reference-ohm",
                        "50",
                    ]
                )

    def test_explicit_frequency_window_passes_when_fully_covered(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_path = root / "emx.csv"
            hfss_path = root / "hfss.csv"
            _write_metric_csv(emx_path, scale=1.0)
            _write_metric_csv(hfss_path, scale=1.02)

            emx = compare.load_curves(emx_path, port_pairs=((0, 1), (2, 3)))
            hfss = compare.load_curves(hfss_path, port_pairs=((0, 1), (2, 3)))
            result = compare.compare_curves(
                emx,
                hfss,
                max_percent_error=5.0,
                compare_start_hz=5.0e9,
                compare_stop_hz=7.0e9,
                min_frequency_points=3,
                expected_frequency_step_hz=1.0e9,
                expected_frequency_points=3,
                require_matching_frequency_grid=True,
            )

            self.assertEqual(result["overall_status"], "PASS")
            self.assertIn("q", result["metrics"])
            self.assertEqual(result["metrics"]["q"]["status"], "PASS")
            self.assertEqual(result["frequency_window_hz"]["count"], 3)
            self.assertEqual(result["frequency_window_hz"]["min"], 5.0e9)
            self.assertEqual(result["frequency_window_hz"]["max"], 7.0e9)
            self.assertEqual(result["frequency_grid_checks"]["expected frequency step"]["status"], "PASS")
            self.assertEqual(result["frequency_grid_checks"]["matching HFSS/ADS frequency grid"]["status"], "PASS")
            self.assertEqual(result["frequency_grid_checks"]["ADS no-extrapolation coverage"]["status"], "PASS")
            self.assertIn(
                "requested_window_hz=5000000000.0-7000000000.0",
                result["frequency_grid_checks"]["ADS no-extrapolation coverage"]["detail"],
            )

    def test_main_outputs_use_fixed_generated_timestamp(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_path = root / "emx.csv"
            hfss_path = root / "hfss.csv"
            out_dir = root / "out"
            _write_metric_csv(emx_path, scale=1.0)
            _write_metric_csv(hfss_path, scale=1.02)

            status = compare.main(
                [
                    "--emx",
                    str(emx_path),
                    "--hfss",
                    str(hfss_path),
                    "--out-dir",
                    str(out_dir),
                    "--compare-start-ghz",
                    "5",
                    "--compare-stop-ghz",
                    "7",
                    "--min-frequency-points",
                    "3",
                    "--expected-frequency-step-ghz",
                    "1",
                    "--expected-frequency-points",
                    "3",
                    "--target-ghz",
                    "6",
                    "--require-matching-frequency-grid",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "emx_hfss_ads_comparison_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["target_marker"]["status"], "PASS")
            self.assertAlmostEqual(summary["target_marker"]["nearest_frequency_ghz"], 6.0)
            self.assertIn("lp_nh", summary["target_marker"]["metrics"])
            self.assertIn("kw", summary["target_marker"]["metrics"])
            self.assertIn("kw", summary["metrics"])
            self.assertEqual(summary["target_marker"]["metrics"]["kw"], summary["target_marker"]["metrics"]["k"])
            target_marker_csv = out_dir / "emx_hfss_ads_target_marker_metrics.csv"
            self.assertTrue(target_marker_csv.is_file())
            target_lines = target_marker_csv.read_text(encoding="utf-8").splitlines()
            target_header = target_lines[0]
            self.assertIn("nearest_frequency_ghz", target_header)
            self.assertIn("percent_error", target_header)
            self.assertTrue(any(",kw," in line for line in target_lines[1:]))
            manifest = json.loads((out_dir / "ads_python_crosscheck_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["generated_utc"], compare.FIXED_COMPARE_GENERATED_UTC)
            self.assertTrue(any(item["path"] == str(target_marker_csv.resolve()) for item in manifest["outputs"]))
            formula_note = (out_dir / "ads_python_formula_crosscheck.md").read_text(encoding="utf-8")
            self.assertIn(compare.FIXED_COMPARE_GENERATED_UTC, formula_note)
            self.assertIn("Touchstone 2.1", formula_note)
            self.assertIn("port pairing must be recorded", formula_note)
            self.assertIn("Touchstone reference impedance", formula_note)
            self.assertIn("ADS Data Display equation template", formula_note)
            self.assertIn("Zp = Z11 - Z12 + Z22 - Z21", formula_note)
            self.assertIn("Zm = Z31 - Z32 + Z42 - Z41", formula_note)
            self.assertIn("M  = imag(Zdiff[2,1]) / omega", formula_note)
            self.assertIn("Q  = min(Qp, Qs)", formula_note)
            self.assertIn("Kw = K", formula_note)
            self.assertIn("Target-frequency marker", formula_note)
            curves_header = (out_dir / "emx_hfss_ads_curves.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("emx_q", curves_header)
            self.assertIn("hfss_ads_q", curves_header)
            self.assertIn("emx_kw", curves_header)
            self.assertIn("hfss_ads_kw", curves_header)

    def test_expected_frequency_grid_failures_make_overall_fail(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_path = root / "emx.csv"
            hfss_path = root / "hfss.csv"
            _write_metric_csv(emx_path, freqs_ghz=(5.0, 6.0, 7.0))
            _write_metric_csv(hfss_path, freqs_ghz=(5.0, 6.0, 7.2))

            emx = compare.load_curves(emx_path, port_pairs=((0, 1), (2, 3)))
            hfss = compare.load_curves(hfss_path, port_pairs=((0, 1), (2, 3)))
            result = compare.compare_curves(
                emx,
                hfss,
                max_percent_error=5.0,
                compare_start_hz=5.0e9,
                compare_stop_hz=7.0e9,
                min_frequency_points=3,
                expected_frequency_step_hz=0.5e9,
                expected_frequency_points=4,
                require_matching_frequency_grid=True,
            )

            self.assertEqual(result["overall_status"], "FAIL")
            self.assertEqual(result["frequency_grid_checks"]["expected frequency step"]["status"], "FAIL")
            self.assertEqual(result["frequency_grid_checks"]["expected frequency points"]["status"], "FAIL")
            self.assertEqual(result["frequency_grid_checks"]["matching HFSS/ADS frequency grid"]["status"], "FAIL")

    def test_requested_window_must_be_fully_covered(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_path = root / "emx.csv"
            hfss_path = root / "hfss.csv"
            _write_metric_csv(emx_path)
            _write_metric_csv(hfss_path)

            emx = compare.load_curves(emx_path, port_pairs=((0, 1), (2, 3)))
            hfss = compare.load_curves(hfss_path, port_pairs=((0, 1), (2, 3)))

            with self.assertRaisesRegex(ValueError, "not fully covered"):
                compare.compare_curves(
                    emx,
                    hfss,
                    max_percent_error=5.0,
                    compare_start_hz=4.0e9,
                    compare_stop_hz=7.0e9,
                    min_frequency_points=3,
                )

    def test_minimum_frequency_points_is_enforced(self) -> None:
        compare = _load_compare_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_path = root / "emx.csv"
            hfss_path = root / "hfss.csv"
            _write_metric_csv(emx_path)
            _write_metric_csv(hfss_path)

            emx = compare.load_curves(emx_path, port_pairs=((0, 1), (2, 3)))
            hfss = compare.load_curves(hfss_path, port_pairs=((0, 1), (2, 3)))

            with self.assertRaisesRegex(ValueError, "required at least 4"):
                compare.compare_curves(
                    emx,
                    hfss,
                    max_percent_error=5.0,
                    compare_start_hz=5.0e9,
                    compare_stop_hz=7.0e9,
                    min_frequency_points=4,
                )
