from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys
import tarfile


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "import_stage1_mars_calibration_return.py"
    spec = importlib.util.spec_from_file_location("import_stage1_mars_calibration_return_script", script_path)
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


def test_import_stage1_mars_return_unpacks_and_compares_hfss_case(tmp_path):
    module = _load_module()
    freqs = np.asarray([15.0e9, 15.5e9])

    return_root = tmp_path / "return_root"
    packet = return_root / "stage1_work" / "calibration_execution_packet_stage1_wideband_20260626"
    packet.mkdir(parents=True)
    (packet / "compare_calibration_s2p_rlc.py").write_text(
        (Path(__file__).resolve().parents[1] / "scripts" / "compare_calibration_s2p_rlc.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    packet_summary = {
        "structures": [{"name": "m9_straight_line", "remote_emx_output": "/remote/m9.s2p"}],
        "hfss_calibration_variants": [{"name": "air_baseline"}],
    }
    (packet / "calibration_execution_summary.json").write_text(json.dumps(packet_summary), encoding="utf-8")
    _write_s2p(
        return_root / "emx_hfss_calibration_20260626" / "m9_straight_line" / "emx" / "m9_straight_line.s2p",
        freqs,
        scale=1.0,
    )
    (return_root / "stage1_emx_calibration_wideband_latest_manifest.json").write_text(
        json.dumps({"status": "PASS", "s2p_count": 1}),
        encoding="utf-8",
    )
    return_tar = tmp_path / "stage1_emx_calibration_wideband_latest.tar.gz"
    with tarfile.open(return_tar, "w:gz") as tar:
        tar.add(return_root, arcname="stage1_return")

    hfss_case = tmp_path / "hfss_case"
    hfss_case.mkdir()
    (hfss_case / "calibration_execution_summary.json").write_text(json.dumps(packet_summary), encoding="utf-8")
    _write_s2p(
        hfss_case
        / "windows_results"
        / "calibration_m9_straight_line_air_baseline"
        / "hfss_direct_results"
        / "m9_straight_line_hfss_calibration_air_baseline_Setup_15GHz_Sweep_15p0_15p5_direct.s2p",
        freqs,
        scale=1.02,
    )

    out_dir = tmp_path / "imported"
    status = module.main(
        [
            str(return_tar),
            "--out-dir",
            str(out_dir),
            "--hfss-case",
            str(hfss_case),
            "--require-matching-frequency-grid",
        ]
    )

    assert status == 0
    summary = json.loads((out_dir / "stage1_mars_return_import_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["hfss_case_count"] == 1
    assert summary["rows"][0]["overall_status"] == "PASS"
    assert summary["rows"][0]["status_counts"] == {"PASS": 1}
    assert (out_dir / "STAGE1_MARS_RETURN_IMPORT_REPORT_CN.md").is_file()


def test_import_stage1_mars_return_reports_missing_hfss_case(tmp_path):
    module = _load_module()
    return_root = tmp_path / "return_root"
    (return_root / "somewhere").mkdir(parents=True)
    _write_s2p(return_root / "somewhere" / "m9_straight_line.s2p", np.asarray([15.0e9]), scale=1.0)
    packet = return_root / "packet"
    packet.mkdir()
    (packet / "calibration_execution_summary.json").write_text(
        json.dumps({"structures": [], "hfss_calibration_variants": []}),
        encoding="utf-8",
    )
    return_tar = tmp_path / "stage1_return.tar.gz"
    with tarfile.open(return_tar, "w:gz") as tar:
        tar.add(return_root, arcname="stage1_return")

    status = module.main(
        [
            str(return_tar),
            "--out-dir",
            str(tmp_path / "out"),
            "--hfss-case",
            str(tmp_path / "missing_case"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "stage1_mars_return_import_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "INCOMPLETE"
    assert summary["hfss_case_count"] == 0
