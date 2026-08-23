from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "summarize_stage1_calibration_results.py"
    spec = importlib.util.spec_from_file_location("summarize_stage1_calibration_results_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_s2p(path: Path, freqs_hz: np.ndarray, *, scale: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    z = np.zeros((len(freqs_hz), 2, 2), dtype=np.complex128)
    for idx, freq_hz in enumerate(freqs_hz):
        omega = 2.0 * np.pi * freq_hz
        series_z = scale * (0.8 + 1j * omega * 1.2e-9)
        z[idx, 0, 0] = series_z / 2.0
        z[idx, 1, 1] = series_z / 2.0
    _write_touchstone(path, freqs_hz, z_to_s(z, z0=50.0))


def test_stage1_summary_compares_available_pairs_and_marks_missing(tmp_path):
    module = _load_module()
    packet = tmp_path / "packet"
    packet.mkdir()
    compare_script = Path(__file__).resolve().parents[1] / "scripts" / "compare_calibration_s2p_rlc.py"
    (packet / "compare_calibration_s2p_rlc.py").write_text(compare_script.read_text(encoding="utf-8"), encoding="utf-8")
    summary = {
        "structures": [
            {"name": "m9_straight_line", "remote_emx_output": "/remote/m9.s2p"},
            {"name": "m10_straight_line", "remote_emx_output": "/remote/m10.s2p"},
        ],
        "hfss_calibration_variants": [
            {"name": "air_baseline"},
            {"name": "substrate_conductivity"},
        ],
    }
    (packet / "calibration_execution_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    freqs = np.asarray([15.0e9, 15.5e9])
    emx_root = tmp_path / "emx"
    hfss_root = tmp_path / "hfss"
    _write_s2p(emx_root / "m9_straight_line" / "emx" / "m9_straight_line.s2p", freqs, scale=1.0)
    _write_s2p(
        hfss_root
        / "calibration_m9_straight_line_air_baseline"
        / "hfss_direct_results"
        / "m9_straight_line_hfss_calibration_air_baseline_Setup_15GHz_Sweep_15p0_15p5_direct.s2p",
        freqs,
        scale=1.02,
    )

    status = module.main(
        [
            "--packet-dir",
            str(packet),
            "--emx-results-root",
            str(emx_root),
            "--hfss-results-root",
            str(hfss_root),
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
    result = json.loads((tmp_path / "out" / "stage1_calibration_summary.json").read_text(encoding="utf-8"))
    assert result["overall_status"] == "INCOMPLETE"
    assert result["status_counts"] == {"PASS": 1, "MISSING": 3}
    first = result["rows"][0]
    assert first["status"] == "PASS"
    assert first["series_l_nh_percent_error"] == pytest.approx(2.0)
    assert (tmp_path / "out" / "m9_straight_line_air_baseline" / "calibration_s2p_rlc_comparison_report.md").is_file()


def test_stage1_summary_accepts_cadence_roundtrip_s2p_names(tmp_path):
    module = _load_module()
    packet = tmp_path / "packet"
    packet.mkdir()
    compare_script = Path(__file__).resolve().parents[1] / "scripts" / "compare_calibration_s2p_rlc.py"
    (packet / "compare_calibration_s2p_rlc.py").write_text(compare_script.read_text(encoding="utf-8"), encoding="utf-8")
    summary = {
        "structures": [
            {"name": "m9_straight_line", "remote_emx_output": "/remote/m9_straight_line_cadence.s2p"},
        ],
        "hfss_calibration_variants": [
            {"name": "air_baseline"},
        ],
    }
    (packet / "calibration_execution_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    freqs = np.asarray([15.0e9, 15.5e9])
    emx_root = tmp_path / "returned"
    hfss_root = tmp_path / "hfss"
    _write_s2p(emx_root / "emx" / "m9_straight_line" / "m9_straight_line_cadence.s2p", freqs, scale=1.0)
    _write_s2p(
        hfss_root
        / "calibration_m9_straight_line_air_baseline"
        / "hfss_direct_results"
        / "m9_straight_line_hfss_calibration_air_baseline_Setup_15GHz_Sweep_15p0_15p5_direct.s2p",
        freqs,
        scale=1.01,
    )

    status = module.main(
        [
            "--packet-dir",
            str(packet),
            "--emx-results-root",
            str(emx_root),
            "--hfss-results-root",
            str(hfss_root),
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
    result = json.loads((tmp_path / "out" / "stage1_calibration_summary.json").read_text(encoding="utf-8"))
    assert result["overall_status"] == "PASS"
    assert result["rows"][0]["emx_s2p"].endswith("m9_straight_line_cadence.s2p")
