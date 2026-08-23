from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "compare_calibration_s2p_rlc.py"
    spec = importlib.util.spec_from_file_location("compare_calibration_s2p_rlc_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_line_s2p(path: Path, freqs_hz: np.ndarray, *, scale: float) -> None:
    z = np.zeros((len(freqs_hz), 2, 2), dtype=np.complex128)
    for idx, freq_hz in enumerate(freqs_hz):
        omega = 2.0 * np.pi * freq_hz
        series_z = scale * (0.8 + 1j * omega * 1.2e-9)
        z[idx, 0, 0] = series_z / 2.0
        z[idx, 1, 1] = series_z / 2.0
    _write_touchstone(path, freqs_hz, z_to_s(z, z0=50.0))


def test_s2p_line_comparison_passes_small_error_and_writes_marker_table(tmp_path):
    module = _load_module()
    freqs_hz = np.asarray([15.0e9, 15.5e9])
    emx = tmp_path / "emx.s2p"
    hfss = tmp_path / "hfss.s2p"
    _write_line_s2p(emx, freqs_hz, scale=1.0)
    _write_line_s2p(hfss, freqs_hz, scale=1.03)

    status = module.main(
        [
            "--emx",
            str(emx),
            "--hfss",
            str(hfss),
            "--out-dir",
            str(tmp_path / "out"),
            "--target-ghz",
            "15",
            "--max-percent-error",
            "10",
            "--require-matching-frequency-grid",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "calibration_s2p_rlc_comparison_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["metrics"]["series_l_nh"]["status"] == "PASS"
    assert summary["metrics"]["series_r_ohm"]["percent_error"] == pytest.approx(3.0)
    assert (tmp_path / "out" / "calibration_s2p_marker_metrics.csv").is_file()


def test_s2p_line_comparison_fails_large_error(tmp_path):
    module = _load_module()
    freqs_hz = np.asarray([15.0e9, 15.5e9])
    emx = tmp_path / "emx.s2p"
    hfss = tmp_path / "hfss.s2p"
    _write_line_s2p(emx, freqs_hz, scale=1.0)
    _write_line_s2p(hfss, freqs_hz, scale=1.35)

    status = module.main(
        [
            "--emx",
            str(emx),
            "--hfss",
            str(hfss),
            "--out-dir",
            str(tmp_path / "out"),
            "--target-ghz",
            "15",
            "--max-percent-error",
            "10",
        ]
    )

    assert status == 2
    summary = json.loads((tmp_path / "out" / "calibration_s2p_rlc_comparison_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["metrics"]["series_l_nh"]["status"] == "FAIL"
