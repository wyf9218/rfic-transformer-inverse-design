from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plan_hfss_v66_calibration_from_recompare.py"
    spec = importlib.util.spec_from_file_location("plan_hfss_v66_calibration_from_recompare_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_inputs(root: Path) -> tuple[Path, Path]:
    recompare = root / "recompare.json"
    recompare.write_text(
        json.dumps(
            {
                "candidate_count": 3,
                "pass_count": 0,
                "best": {"worst_percent_error": 1476.0, "worst_metric": "q"},
                "target15_best": {
                    "target15_worst_percent_error": 40.5,
                    "target15_worst_metric": "ls_nh",
                    "target15_core_percent_errors": {
                        "lp_nh": 35.0,
                        "ls_nh": 40.5,
                        "q": 17.3,
                        "k": 1.5,
                        "kw": 1.5,
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    sample_dir = root / "aedt" / "samples" / "01_sample"
    sample_dir.mkdir(parents=True)
    build = sample_dir / "build_hfss_s8p_from_payload.py"
    solve = sample_dir / "solve_export_hfss_s8p.py"
    payload = sample_dir / "hfss_s8p_build_payload.json"
    build.write_text("print('build')\n", encoding="utf-8")
    solve.write_text("print('solve')\n", encoding="utf-8")
    payload.write_text(json.dumps({"schema": "payload"}), encoding="utf-8")
    v65_plan = root / "v65_plan.json"
    v65_plan.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "variants": [
                    {
                        "name": "v65a",
                        "windows_steps": [
                            {
                                "evaluation": "sample_a",
                                "build_script": str(build),
                                "solve_script": str(solve),
                            }
                        ],
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return recompare, v65_plan


def test_plans_v66_variants_from_failing_recompare(tmp_path):
    module = _load_module()
    recompare, v65_plan = _write_inputs(tmp_path)

    status = module.main(
        [
            "--recompare-summary",
            str(recompare),
            "--v65-plan-summary",
            str(v65_plan),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v66_calibration_plan_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "RUN_V66_HFSS_DIAGNOSTIC_SWEEP_BEFORE_FULL_VALIDATION"
    assert summary["gate_status"]["historical_pass_count"] == 0
    assert summary["diagnosis"]["geometry_coupling_likely_ok"] is True
    assert summary["diagnosis"]["lp_ls_q_gap_present"] is True
    assert summary["variant_count"] >= 6
    assert summary["postrun_validation_contract"]["final_acceptance_candidate"] is True
    assert summary["postrun_validation_contract"]["expected_frequency_points"] == 111
    assert (tmp_path / "out" / "run_hfss_v66_calibration.windows.ps1").is_file()
    postrun = tmp_path / "out" / "postrun_validate_hfss_v66_calibration.sh"
    assert postrun.is_file()
    postrun_text = postrun.read_text(encoding="utf-8")
    assert "--compare-start-ghz 5" in postrun_text
    assert "--compare-stop-ghz 60" in postrun_text
    assert "--expected-frequency-step-ghz 0.5" in postrun_text
    assert "--expected-frequency-points 111" in postrun_text
    variant_dir = tmp_path / "out" / "variants" / "v66a_best_marker_reference_bbox" / "sample_a"
    packet_path = variant_dir / "hfss_v66_single_variant_packet_summary.json"
    assert packet_path.is_file()
    variant_payload = json.loads((variant_dir / "hfss_s8p_build_payload.json").read_text(encoding="utf-8"))
    assert variant_payload["frequency_grid"] == {
        "setup_frequency_ghz": 15.0,
        "start_ghz": 5.0,
        "stop_ghz": 60.0,
        "step_ghz": 0.5,
        "points": 111,
        "expected_points": 111,
    }
    assert (variant_dir / "build_hfss_s8p_from_payload.py").is_file()
    assert (variant_dir / "solve_export_hfss_s8p.py").is_file()
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    sample = packet["sample_results"][0]
    assert sample["payload_json"] == str(variant_dir / "hfss_s8p_build_payload.json")
    assert sample["build_script"] == str(variant_dir / "build_hfss_s8p_from_payload.py")
    assert sample["solve_script"] == str(variant_dir / "solve_export_hfss_s8p.py")
    windows_runner = (tmp_path / "out" / "run_hfss_v66_calibration.windows.ps1").read_text(encoding="utf-8")
    assert str(variant_dir / "hfss_s8p_build_payload.json").replace("/", "\\") in windows_runner


def test_fails_when_source_payload_missing(tmp_path):
    module = _load_module()
    recompare, v65_plan = _write_inputs(tmp_path)
    payload = tmp_path / "aedt" / "samples" / "01_sample" / "hfss_s8p_build_payload.json"
    payload.unlink()

    status = module.main(
        [
            "--recompare-summary",
            str(recompare),
            "--v65-plan-summary",
            str(v65_plan),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v66_calibration_plan_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    failed = [check["name"] for check in summary["checks"] if check["status"] == "FAIL"]
    assert "source payload JSON exists" in failed
