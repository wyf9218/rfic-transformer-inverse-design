from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_cm_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_cm_mismatch.py"
    spec = importlib.util.spec_from_file_location("diagnose_cm_mismatch_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_capacitive_s4p(path: Path, freqs_hz: np.ndarray, *, primary_ff: float, secondary_ff: float) -> None:
    s_rows = []
    conductance = 0.02
    for freq_hz in freqs_hz:
        omega = 2.0 * np.pi * freq_hz
        y = np.eye(4, dtype=np.complex128) * conductance
        y[0, 0] += 1j * omega * primary_ff * 1.0e-15
        y[1, 1] += 1j * omega * primary_ff * 1.0e-15
        y[2, 2] += 1j * omega * secondary_ff * 1.0e-15
        y[3, 3] += 1j * omega * secondary_ff * 1.0e-15
        z = np.linalg.inv(y)
        s_rows.append(z_to_s(z, z0=50.0))
    _write_touchstone(path, freqs_hz, np.asarray(s_rows))


class DiagnoseCmMismatchScriptTest(TransformerToolboxTestBase):
    def test_identical_touchstones_pass_selected_cm_definition(self) -> None:
        cm = _load_cm_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs = np.asarray([10.0e9, 15.0e9, 20.0e9])
            emx = root / "emx.s4p"
            hfss = root / "hfss.s4p"
            _write_capacitive_s4p(emx, freqs, primary_ff=50.0, secondary_ff=60.0)
            _write_capacitive_s4p(hfss, freqs, primary_ff=50.0, secondary_ff=60.0)

            status = cm.main(["--emx", str(emx), "--hfss", str(hfss), "--out-dir", str(root / "cm"), "--no-plot"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "cm" / "cm_mismatch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            selected = summary["definitions"]["single_primary_y11_plus_y12_ff"]
            self.assertAlmostEqual(selected["max_percent_error"], 0.0)

    def test_changed_primary_capacitance_fails_selected_cm_definition(self) -> None:
        cm = _load_cm_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs = np.asarray([10.0e9, 15.0e9, 20.0e9])
            emx = root / "emx.s4p"
            hfss = root / "hfss.s4p"
            _write_capacitive_s4p(emx, freqs, primary_ff=50.0, secondary_ff=60.0)
            _write_capacitive_s4p(hfss, freqs, primary_ff=60.0, secondary_ff=60.0)

            status = cm.main(["--emx", str(emx), "--hfss", str(hfss), "--out-dir", str(root / "cm"), "--no-plot"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "cm" / "cm_mismatch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            selected = summary["definitions"]["single_primary_y11_plus_y12_ff"]
            self.assertGreater(selected["max_percent_error"], 5.0)
