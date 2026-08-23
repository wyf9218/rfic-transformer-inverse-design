from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys
from types import SimpleNamespace


def _load_audit_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_touchstone_transformer.py"
    spec = importlib.util.spec_from_file_location("audit_touchstone_transformer_script", script_path)
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


def _prepend_comments(path: Path, lines: list[str]) -> None:
    original = path.read_text(encoding="utf-8")
    path.write_text("".join(f"! {line}\n" for line in lines) + original, encoding="utf-8")


class AuditTouchstoneTransformerScriptTest(TransformerToolboxTestBase):
    def test_metric_extraction_uses_ads_reverse_transfer_mutual_term(self) -> None:
        audit = _load_audit_module()
        freq_hz = np.asarray([10.0e9], dtype=float)
        omega = 2.0 * np.pi * freq_hz[0]
        z = np.zeros((1, 4, 4), dtype=np.complex128)
        z[0, 0, 0] = 20.0 + 1j * omega * 0.5e-9
        z[0, 1, 1] = 20.0 + 1j * omega * 0.5e-9
        z[0, 2, 2] = 20.0 + 1j * omega * 0.6e-9
        z[0, 3, 3] = 20.0 + 1j * omega * 0.6e-9
        z[0, 0, 2] = 1j * omega * 0.10e-9
        z[0, 2, 0] = 1j * omega * 0.30e-9
        args = SimpleNamespace(port_pairs="1,2:3,4")

        metrics = audit._extract_metric_curves(z_to_s(z, z0=50.0), freq_hz, args)

        self.assertAlmostEqual(metrics.k[0], 0.30e-9 / np.sqrt(1.0e-9 * 1.2e-9), places=6)
        self.assertNotAlmostEqual(metrics.k[0], 0.10e-9 / np.sqrt(1.0e-9 * 1.2e-9), places=6)

    def test_s8p_metric_extraction_requires_explicit_port_pairs(self) -> None:
        audit = _load_audit_module()
        freq_hz = np.asarray([10.0e9], dtype=float)
        z = np.eye(8, dtype=np.complex128).reshape(1, 8, 8) * 50.0

        with self.assertRaisesRegex(ValueError, "S8P transformer audit requires explicit differential port pairs"):
            audit._differential_z_from_s(z_to_s(z, z0=50.0), 50.0, None)

    def test_synthetic_transformer_s8p_passes_with_explicit_power_line_pairs(self) -> None:
        audit = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "good.s8p"
            freq_hz = np.asarray([10.0e9, 11.0e9], dtype=float)
            z = np.repeat(np.eye(8, dtype=np.complex128)[None, :, :] * 5.0, len(freq_hz), axis=0)
            for idx, freq in enumerate(freq_hz):
                omega = 2.0 * np.pi * freq
                for port in (0, 3):
                    z[idx, port, port] = 20.0 + 1j * omega * 0.5e-9
                for port in (4, 5):
                    z[idx, port, port] = 18.0 + 1j * omega * 0.6e-9
                z[idx, 0, 4] = 1j * omega * 0.30e-9
                z[idx, 4, 0] = 1j * omega * 0.30e-9
            _write_touchstone(touchstone, freq_hz, z_to_s(z, z0=50.0))

            status = audit.main(
                [
                    str(touchstone),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-ports",
                    "8",
                    "--port-pairs",
                    "1,4:5,6",
                    "--target-frequency-ghz",
                    "10",
                    "--target-frequency-tolerance-ghz",
                    "0.05",
                    "--min-target-inductance-nh",
                    "0.05",
                    "--min-target-q",
                    "1",
                    "--min-target-abs-k",
                    "0.05",
                    "--max-target-abs-k",
                    "0.98",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "touchstone_transformer_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["port count"]["status"], "PASS")
            self.assertIn("ports=8", checks["port count"]["detail"])
            self.assertEqual(checks["target-frequency transformer metrics"]["status"], "PASS")

    def test_synthetic_transformer_s4p_passes_preflight(self) -> None:
        audit = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "good.s4p"
            _write_synthetic_transformer_s4p(touchstone, np.asarray([5.0e9, 10.0e9, 15.0e9]))

            status = audit.main(
                [
                    str(touchstone),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "15",
                    "--expected-frequency-step-ghz",
                    "5",
                    "--expected-frequency-points",
                    "3",
                    "--required-sweep-start-ghz",
                    "5",
                    "--required-sweep-stop-ghz",
                    "15",
                    "--target-frequency-ghz",
                    "10",
                    "--positive-window-start-ghz",
                    "5",
                    "--positive-window-stop-ghz",
                    "15",
                    "--shape-window-start-ghz",
                    "5",
                    "--shape-window-stop-ghz",
                    "15",
                    "--max-shape-spike-ratio",
                    "4",
                    "--max-shape-relative-step",
                    "1.0",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "touchstone_transformer_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["frequency"]["points"], 3)
            self.assertTrue((root / "audit" / "touchstone_transformer_metrics.csv").exists())
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["source identity"]["status"], "WARN")
            self.assertEqual(checks["differential Z finiteness"]["status"], "PASS")
            self.assertEqual(checks["differential Z reciprocity"]["status"], "PASS")
            self.assertEqual(checks["differential Z positive-realness"]["status"], "PASS")
            self.assertEqual(checks["target-frequency transformer metrics"]["status"], "PASS")
            self.assertEqual(checks["smooth transformer metric window"]["status"], "PASS")
            self.assertIn("differential_z_quality", summary)

    def test_differential_z_gate_rejects_nonreciprocal_zdiff(self) -> None:
        audit = _load_audit_module()
        z_diff = np.zeros((1, 2, 2), dtype=np.complex128)
        z_diff[0, 0, 0] = 10.0 + 1j
        z_diff[0, 1, 1] = 11.0 + 1j
        z_diff[0, 0, 1] = 1.0j
        z_diff[0, 1, 0] = 4.0j
        args = SimpleNamespace(
            max_differential_z_reciprocity_error_ohm=1.0e-6,
            max_differential_z_reciprocity_relative_error=1.0e-6,
            min_differential_z_real_eigenvalue_ohm=-1.0e-9,
            min_differential_self_resistance_ohm=0.0,
        )

        quality = audit._differential_z_quality(z_diff)
        checks = {item.name: item for item in audit._differential_z_checks(quality, args)}

        self.assertEqual(checks["differential Z finiteness"].status, "PASS")
        self.assertEqual(checks["differential Z reciprocity"].status, "FAIL")
        self.assertEqual(checks["differential Z positive-realness"].status, "PASS")

    def test_differential_z_gate_rejects_negative_self_resistance(self) -> None:
        audit = _load_audit_module()
        z_diff = np.zeros((1, 2, 2), dtype=np.complex128)
        z_diff[0, 0, 0] = -0.25 + 1j
        z_diff[0, 1, 1] = 10.0 + 1j
        args = SimpleNamespace(
            max_differential_z_reciprocity_error_ohm=1.0e-6,
            max_differential_z_reciprocity_relative_error=1.0e-6,
            min_differential_z_real_eigenvalue_ohm=-1.0e-9,
            min_differential_self_resistance_ohm=0.0,
        )

        quality = audit._differential_z_quality(z_diff)
        checks = {item.name: item for item in audit._differential_z_checks(quality, args)}

        self.assertEqual(checks["differential Z positive-realness"].status, "FAIL")
        self.assertIn("self_resistance_min_ohm", checks["differential Z positive-realness"].detail)

    def test_expected_hfss_source_kind_passes_from_touchstone_header(self) -> None:
        audit = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "sample.s4p"
            _write_synthetic_transformer_s4p(touchstone, np.asarray([5.0e9, 10.0e9, 15.0e9]))
            _prepend_comments(touchstone, ["Touchstone file exported from HFSS 2025.1.0", "Project: provenance_test"])

            status = audit.main(
                [
                    str(touchstone),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-source-kind",
                    "HFSS",
                    "--target-frequency-ghz",
                    "10",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "touchstone_transformer_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["source identity"]["status"], "PASS")
            self.assertEqual(summary["provenance"]["header_source_kind"], "HFSS")
            self.assertEqual(summary["provenance"]["inferred_source_kind"], "HFSS")

    def test_expected_source_kind_rejects_wrong_touchstone_header(self) -> None:
        audit = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "sample.s4p"
            _write_synthetic_transformer_s4p(touchstone, np.asarray([5.0e9, 10.0e9, 15.0e9]))
            _prepend_comments(touchstone, ["Touchstone file exported from HFSS 2025.1.0"])

            status = audit.main(
                [
                    str(touchstone),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-source-kind",
                    "EMX",
                    "--target-frequency-ghz",
                    "10",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "touchstone_transformer_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["source identity"]["status"], "FAIL")
            self.assertIn("expected=EMX", checks["source identity"]["detail"])

    def test_required_sweep_outside_touchstone_range_fails(self) -> None:
        audit = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "narrow.s4p"
            _write_synthetic_transformer_s4p(touchstone, np.asarray([13.5e9, 15.0e9, 16.5e9]))

            status = audit.main(
                [
                    str(touchstone),
                    "--out-dir",
                    str(root / "audit"),
                    "--required-sweep-start-ghz",
                    "5",
                    "--required-sweep-stop-ghz",
                    "50",
                    "--target-frequency-ghz",
                    "15",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "touchstone_transformer_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["required ADS sweep coverage"]["status"], "FAIL")
            self.assertIn("later than required", checks["required ADS sweep coverage"]["detail"])

    def test_expected_frequency_grid_rejects_nonuniform_internal_steps(self) -> None:
        audit = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "nonuniform.s4p"
            freqs_hz = np.asarray([5.0e9, 6.0e9, 7.5e9, 8.0e9, 9.0e9])
            _write_synthetic_transformer_s4p(touchstone, freqs_hz)

            status = audit.main(
                [
                    str(touchstone),
                    "--out-dir",
                    str(root / "audit"),
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "9",
                    "--expected-frequency-step-ghz",
                    "1",
                    "--expected-frequency-points",
                    "5",
                    "--target-frequency-ghz",
                    "8",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "touchstone_transformer_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertGreater(summary["frequency"]["max_expected_step_error_hz"], 0.0)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["expected frequency grid"]["status"], "FAIL")
            self.assertIn("per-step grid", checks["expected frequency grid"]["detail"])

    def test_zero_sparameters_fail_target_transformer_metric_gate(self) -> None:
        audit = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "zero.s4p"
            freqs_hz = np.asarray([5.0e9, 10.0e9, 15.0e9])
            _write_touchstone(touchstone, freqs_hz, np.zeros((3, 4, 4), dtype=np.complex128))

            status = audit.main(
                [
                    str(touchstone),
                    "--out-dir",
                    str(root / "audit"),
                    "--required-sweep-start-ghz",
                    "5",
                    "--required-sweep-stop-ghz",
                    "15",
                    "--target-frequency-ghz",
                    "10",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "touchstone_transformer_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target-frequency transformer metrics"]["status"], "FAIL")
            self.assertIn("Lp_nH", checks["target-frequency transformer metrics"]["detail"])

    def test_low_coupling_fails_abs_k_target_and_window_gate(self) -> None:
        audit = _load_audit_module()
        freqs_hz = np.asarray([5.0e9, 10.0e9, 15.0e9])
        metrics = audit.MetricCurves(
            freq_hz=freqs_hz,
            lp_nh=np.ones(3),
            ls_nh=np.ones(3),
            m_nh=0.01 * np.ones(3),
            k=0.01 * np.ones(3),
            qp=12.0 * np.ones(3),
            qs=14.0 * np.ones(3),
        )
        args = SimpleNamespace(
            target_frequency_ghz=10.0,
            target_frequency_tolerance_ghz=None,
            frequency_tolerance_hz=1.0e5,
            min_target_inductance_nh=0.05,
            min_target_q=1.0,
            min_target_abs_k=0.05,
            max_target_abs_k=0.98,
            positive_window_start_ghz=5.0,
            positive_window_stop_ghz=15.0,
            min_window_abs_k=0.05,
        )

        checks = {item.name: item for item in [*audit._target_checks(metrics, args), *audit._positive_window_checks(metrics, args)]}

        self.assertEqual(checks["target-frequency transformer metrics"].status, "FAIL")
        self.assertIn("abs(K) < 0.05", checks["target-frequency transformer metrics"].detail)
        self.assertEqual(checks["positive metric window"].status, "FAIL")
        self.assertIn("abs_K_min", checks["positive metric window"].detail)

    def test_shape_window_catches_metric_spikes(self) -> None:
        audit = _load_audit_module()
        freqs_hz = np.asarray([5.0e9, 6.0e9, 7.0e9, 8.0e9, 9.0e9])
        metrics = audit.MetricCurves(
            freq_hz=freqs_hz,
            lp_nh=np.asarray([1.0, 1.0, 12.0, 1.0, 1.0]),
            ls_nh=np.ones(5),
            m_nh=0.2 * np.ones(5),
            k=0.2 * np.ones(5),
            qp=12.0 * np.ones(5),
            qs=14.0 * np.ones(5),
        )
        args = SimpleNamespace(
            shape_window_start_ghz=5.0,
            shape_window_stop_ghz=9.0,
            frequency_tolerance_hz=1.0e5,
            max_shape_spike_ratio=4.0,
            max_shape_relative_step=0.5,
        )

        checks = audit._shape_window_checks(metrics, args)

        self.assertEqual(checks[0].status, "FAIL")
        self.assertIn("Lp", checks[0].detail)
