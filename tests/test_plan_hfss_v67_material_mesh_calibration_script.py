from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plan_hfss_v67_material_mesh_calibration.py"
    spec = importlib.util.spec_from_file_location("plan_hfss_v67_material_mesh_calibration_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_v66_plan(root: Path) -> Path:
    sample_dir = root / "v66" / "variants" / "v66a" / "sample_a"
    sample_dir.mkdir(parents=True)
    build = sample_dir / "build_hfss_s8p_from_payload.py"
    solve = sample_dir / "solve_export_hfss_s8p.py"
    payload = sample_dir / "hfss_s8p_build_payload.json"
    build.write_text("print('build')\n", encoding="utf-8")
    solve.write_text("print('solve')\n", encoding="utf-8")
    payload.write_text(
        json.dumps(
            {
                "schema": "payload",
                "frequency_grid": {
                    "setup_frequency_ghz": 15.0,
                    "start_ghz": 5.0,
                    "stop_ghz": 60.0,
                    "step_ghz": 0.5,
                    "points": 111,
                },
                "hfss": {"setup_name": "Setup_15GHz", "sweep_name": "Sweep_5_60_0p5"},
            }
        ),
        encoding="utf-8",
    )
    plan = root / "v66_plan.json"
    plan.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "gate_status": {
                    "historical_pass_count": 0,
                    "best_target15_worst_percent_error": 40.5,
                },
                "diagnosis": {
                    "primary_root_cause_hypothesis": "HFSS stack/reference-ground/port treatment mismatch",
                },
                "variants": [
                    {
                        "name": "v66a_best_marker_reference_bbox",
                        "evaluation": "sample_a",
                        "build_script": str(build),
                        "solve_script": str(solve),
                        "payload_json": str(payload),
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return plan


def test_plans_v67_material_mesh_variants_from_v66_packet(tmp_path):
    module = _load_module()
    v66_plan = _write_v66_plan(tmp_path)

    status = module.main(
        [
            "--v66-plan-summary",
            str(v66_plan),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary_path = tmp_path / "out" / "hfss_v67_material_mesh_calibration_plan_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "RUN_V67_IF_V66_FAILS_OR_RUN_IN_PARALLEL_FOR_MATERIAL_MESH_DIAGNOSIS"
    assert summary["variant_count"] == 8
    assert summary["postrun_validation_contract"]["hfss_touchstone_suffix"] == ".s8p"
    assert summary["postrun_validation_contract"]["expected_ports"] == 8
    assert summary["postrun_validation_contract"]["expected_frequency_points"] == 111
    assert summary["postrun_validation_contract"]["final_acceptance_candidate"] is True

    variants = {item["name"]: item for item in summary["variants"]}
    assert variants["v67b_solve_inside_conductors"]["env"]["HFSS_CONDUCTOR_SOLVE_INSIDE"] == "1"
    assert variants["v67d_dielectric_conductivity_stack"]["env"]["HFSS_DIELECTRIC_CONDUCTIVITY_MODE"] == "conductivity"
    assert variants["v67g_basis_order2_mesh"]["env"]["HFSS_SETUP_BASIS_ORDER"] == "2"
    assert variants["v67h_skip_pin_fixture_diagnostic"]["diagnostic_only"] is True
    assert variants["v67h_skip_pin_fixture_diagnostic"]["final_acceptance_candidate"] is False

    variant_dir = tmp_path / "out" / "variants" / "v67a_tight_mesh_baseline" / "sample_a"
    assert (variant_dir / "build_hfss_s8p_from_payload.py").is_file()
    assert (variant_dir / "solve_export_hfss_s8p.py").is_file()
    payload = json.loads((variant_dir / "hfss_s8p_build_payload.json").read_text(encoding="utf-8"))
    assert payload["frequency_grid"] == {
        "setup_frequency_ghz": 15.0,
        "start_ghz": 5.0,
        "stop_ghz": 60.0,
        "step_ghz": 0.5,
        "points": 111,
        "expected_points": 111,
    }
    assert payload["v67_patch"]["variant"] == "v67a_tight_mesh_baseline"

    runner = (tmp_path / "out" / "run_hfss_v67_material_mesh_calibration_resilient.windows.ps1").read_text(
        encoding="utf-8"
    )
    assert "Start-Transcript" in runner
    assert "hfss_v67_resilient_run_transcript.txt" in runner
    assert "function Run-V67Variant" in runner
    assert "catch {" in runner
    assert runner.count("$VariantResults += Run-V67Variant") == 8
    assert "Variant completed but did not produce both .s8p and export manifest." in runner
    assert (tmp_path / "out" / "run_hfss_v67_material_mesh_calibration_resilient.windows.cmd").is_file()

    postrun = (tmp_path / "out" / "postrun_validate_hfss_v67_material_mesh_calibration.sh").read_text(
        encoding="utf-8"
    )
    assert "--compare-start-ghz 5" in postrun
    assert "--compare-stop-ghz 60" in postrun
    assert "--expected-frequency-step-ghz 0.5" in postrun
    assert "--expected-frequency-points 111" in postrun


def test_fails_when_v66_source_payload_missing(tmp_path):
    module = _load_module()
    v66_plan = _write_v66_plan(tmp_path)
    source_payload = tmp_path / "v66" / "variants" / "v66a" / "sample_a" / "hfss_s8p_build_payload.json"
    source_payload.unlink()

    status = module.main(
        [
            "--v66-plan-summary",
            str(v66_plan),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "hfss_v67_material_mesh_calibration_plan_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "FAIL"
    failed = [check["name"] for check in summary["checks"] if check["status"] == "FAIL"]
    assert "source payload JSON exists" in failed
    assert not (tmp_path / "out" / "run_hfss_v67_material_mesh_calibration_resilient.windows.ps1").exists()
