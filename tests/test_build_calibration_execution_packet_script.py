from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys


def _load_script(name):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_reference_payload(path):
    payload = {
        "stack": {
            "conductors": {
                "metal5": {"z_top_um": 703.416, "z_bottom_um": 703.196, "thickness_um": 0.22},
                "metal9": {"z_top_um": 710.636, "z_bottom_um": 707.236, "thickness_um": 3.4},
                "metal10": {"z_top_um": 716.961, "z_bottom_um": 714.161, "thickness_um": 2.8},
            },
            "dielectrics": [],
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_stub_scripts(tmp_path):
    build_script = tmp_path / "build_hfss_s8p_from_payload.py"
    build_script.write_text("# stub build script\n", encoding="utf-8")
    solve_script = tmp_path / "run_hfss_explicit_sweep_export.py"
    solve_script.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "PROJECT = Path('demo.aedt')",
                "RESULTS_DIR = Path('.')",
                "SETUP_NAME = 'Setup'",
                "SWEEP_NAME = 'Sweep'",
                "PAYLOAD = {'hfss': {'expected_touchstone_suffix': '.s2p'}, 'ports': [{}, {}]}",
                "def log(message): pass",
                "def main():",
                '    output_file = RESULTS_DIR / f"{PROJECT.stem}_{SETUP_NAME}_{SWEEP_NAME}.s8p"',
                "    return output_file",
            ]
        ),
        encoding="utf-8",
    )
    return build_script, solve_script


def test_builds_stage1_packet_with_remote_safe_gds_paths_and_s2p_exports(tmp_path):
    structure_module = _load_script("build_emx_hfss_calibration_structures.py")
    packet_module = _load_script("build_calibration_execution_packet.py")
    calibration_dir = tmp_path / "calibration_structures"
    packet_dir = tmp_path / "packet"
    reference_payload = tmp_path / "reference_hfss_payload.json"
    build_script, solve_script = _write_stub_scripts(tmp_path)

    assert structure_module.main(["--out-dir", str(calibration_dir)]) == 0
    _write_reference_payload(reference_payload)

    assert (
        packet_module.main(
            [
                "--calibration-manifest",
                str(calibration_dir / "calibration_structures_manifest.json"),
                "--reference-hfss-payload",
                str(reference_payload),
                "--out-dir",
                str(packet_dir),
                "--build-script",
                str(build_script),
                "--solve-script",
                str(solve_script),
                "--remote-root",
                "/remote/calibration",
                "--stage",
                "stage1_straight_line",
            ]
        )
        == 0
    )

    mars_script = (packet_dir / "mars_run_emx_calibration.sh").read_text(encoding="utf-8")
    assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in mars_script
    assert 'cp "${SCRIPT_DIR}/m9_straight_line/m9_straight_line.gds"' in mars_script
    assert str(packet_dir) not in mars_script
    assert (packet_dir / "compare_calibration_s2p_rlc.py").is_file()
    assert (packet_dir / "windows_copy_hfss_results_to_mac.ps1").is_file()
    summary = json.loads((packet_dir / "calibration_execution_summary.json").read_text(encoding="utf-8"))
    assert summary["calibration_compare_script"].endswith("compare_calibration_s2p_rlc.py")
    assert summary["windows_copy_results_script"].endswith("windows_copy_hfss_results_to_mac.ps1")
    assert [variant["name"] for variant in summary["hfss_calibration_variants"]] == [
        "air_baseline",
        "m5_united_air",
        "m5_perfecte_air",
        "substrate_conductivity",
        "beol_lossless_dielectric",
        "full_local_stack_loss_tangent",
    ]

    windows_script = (packet_dir / "windows_run_hfss_calibration.ps1").read_text(encoding="utf-8")
    assert 'calibration_stage1_status.csv' in windows_script
    assert '"structure,variant,status,message" | Set-Content -Path $statusCsv' in windows_script
    assert "Remove-Item -Recurse -Force $dst" in windows_script
    assert "build_hfss failed for m9_straight_line/air_baseline" in windows_script
    assert "solve_export failed for m10_straight_line/full_local_stack_loss_tangent" in windows_script
    assert "calibration_m9_straight_line_air_baseline" in windows_script
    assert "calibration_m9_straight_line_m5_united_air" in windows_script
    assert "calibration_m9_straight_line_m5_perfecte_air" in windows_script
    assert "calibration_m9_straight_line_substrate_conductivity" in windows_script
    assert "calibration_m10_straight_line_beol_lossless_dielectric" in windows_script
    assert "calibration_m10_straight_line_full_local_stack_loss_tangent" in windows_script
    assert '$env:HFSS_PORT_REFERENCE_MODE = "all_m5"' in windows_script
    assert '$env:HFSS_UNITE_STRATEGY = "all_by_metal"' in windows_script
    assert '$env:HFSS_M5_SHIELD_BOUNDARY = "perfecte"' in windows_script
    assert '$env:HFSS_DIELECTRIC_Z_MIN_UM = "0"' in windows_script
    assert '$env:HFSS_DIELECTRIC_Z_MAX_UM = "718.643"' in windows_script
    assert '$env:HFSS_DIELECTRIC_CONDUCTIVITY_MODE = "loss_tangent"' in windows_script
    assert 'm9_straight_line,air_baseline,PASS,ok' in windows_script
    assert 'm10_straight_line,full_local_stack_loss_tangent,FAIL,$msg' in windows_script
    copy_script = (packet_dir / "windows_copy_hfss_results_to_mac.ps1").read_text(encoding="utf-8")
    assert "calibration_stage1_status.csv" in copy_script
    assert "calibration_m9_straight_line_air_baseline" in copy_script
    assert "calibration_m10_straight_line_full_local_stack_loss_tangent" in copy_script
    assert "hfss_direct_results" in copy_script

    for name in ["m9_straight_line", "m10_straight_line"]:
        payload = json.loads((packet_dir / name / "hfss_s8p_build_payload.json").read_text(encoding="utf-8"))
        assert payload["hfss"]["expected_touchstone_suffix"] == ".s2p"
        assert len(payload["ports"]) == 2
        assert payload["differential_port_pairs"] == "1,2"

        solve_text = (packet_dir / name / "run_hfss_explicit_sweep_export.py").read_text(encoding="utf-8")
        assert "expected_touchstone_suffix" in solve_text
        assert 'f"{PROJECT.stem}_{SETUP_NAME}_{SWEEP_NAME}{suffix}"' in solve_text
        assert 'f"{PROJECT.stem}_{SETUP_NAME}_{SWEEP_NAME}.s8p"' not in solve_text


def test_builds_filtered_hfss_variant_packet(tmp_path):
    structure_module = _load_script("build_emx_hfss_calibration_structures.py")
    packet_module = _load_script("build_calibration_execution_packet.py")
    calibration_dir = tmp_path / "calibration_structures"
    packet_dir = tmp_path / "packet"
    reference_payload = tmp_path / "reference_hfss_payload.json"
    build_script, solve_script = _write_stub_scripts(tmp_path)

    assert structure_module.main(["--out-dir", str(calibration_dir)]) == 0
    _write_reference_payload(reference_payload)

    assert (
        packet_module.main(
            [
                "--calibration-manifest",
                str(calibration_dir / "calibration_structures_manifest.json"),
                "--reference-hfss-payload",
                str(reference_payload),
                "--out-dir",
                str(packet_dir),
                "--build-script",
                str(build_script),
                "--solve-script",
                str(solve_script),
                "--stage",
                "stage1_straight_line",
                "--hfss-variant",
                "air_baseline",
                "--hfss-variant",
                "substrate_conductivity",
            ]
        )
        == 0
    )

    summary = json.loads((packet_dir / "calibration_execution_summary.json").read_text(encoding="utf-8"))
    assert [variant["name"] for variant in summary["hfss_calibration_variants"]] == [
        "air_baseline",
        "substrate_conductivity",
    ]
    windows_script = (packet_dir / "windows_run_hfss_calibration.ps1").read_text(encoding="utf-8")
    assert "calibration_m9_straight_line_air_baseline" in windows_script
    assert "calibration_m10_straight_line_substrate_conductivity" in windows_script
    assert "m5_united_air" not in windows_script
    assert "m5_perfecte_air" not in windows_script
    assert "beol_lossless_dielectric" not in windows_script
    assert "full_local_stack_loss_tangent" not in windows_script


def test_builds_filtered_structure_packet(tmp_path):
    structure_module = _load_script("build_emx_hfss_calibration_structures.py")
    packet_module = _load_script("build_calibration_execution_packet.py")
    calibration_dir = tmp_path / "calibration_structures"
    packet_dir = tmp_path / "packet"
    reference_payload = tmp_path / "reference_hfss_payload.json"
    build_script, solve_script = _write_stub_scripts(tmp_path)

    assert structure_module.main(["--out-dir", str(calibration_dir)]) == 0
    _write_reference_payload(reference_payload)

    assert (
        packet_module.main(
            [
                "--calibration-manifest",
                str(calibration_dir / "calibration_structures_manifest.json"),
                "--reference-hfss-payload",
                str(reference_payload),
                "--out-dir",
                str(packet_dir),
                "--build-script",
                str(build_script),
                "--solve-script",
                str(solve_script),
                "--stage",
                "stage1_straight_line",
                "--structure",
                "m10_straight_line",
                "--hfss-variant",
                "air_baseline",
            ]
        )
        == 0
    )

    summary = json.loads((packet_dir / "calibration_execution_summary.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in summary["structures"]] == ["m10_straight_line"]
    windows_script = (packet_dir / "windows_run_hfss_calibration.ps1").read_text(encoding="utf-8")
    assert "calibration_m10_straight_line_air_baseline" in windows_script
    assert "calibration_m9_straight_line" not in windows_script
