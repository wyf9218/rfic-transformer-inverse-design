from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plan_hfss_next_calibration_queue_from_diagnosis.py"
    spec = importlib.util.spec_from_file_location("plan_hfss_next_calibration_queue_from_diagnosis_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# dummy\n", encoding="utf-8")
    return str(path)


def _variant(root: Path, name: str, *, diagnostic_only: bool = False, source: str = "v67") -> dict:
    variant_dir = root / source / name / "sample"
    return {
        "name": name,
        "purpose": f"purpose {name}",
        "diagnostic_only": diagnostic_only,
        "final_acceptance_candidate": not diagnostic_only,
        "variant_dir": str(variant_dir),
        "hfss_results_dir": str(variant_dir / "hfss_solve_export_results"),
        "hfss_save_path": str(variant_dir / f"{name}.aedt"),
        "hfss_solve_project": str(variant_dir / f"{name}_solve.aedt"),
        "hfss_build_log": str(variant_dir / "hfss_s8p_build.log"),
        "hfss_port_manifest": str(variant_dir / "hfss_s8p_build_port_manifest.json"),
        "hfss_export_manifest": str(variant_dir / "hfss_s8p_export_manifest.json"),
        "build_script": _touch(variant_dir / "build_hfss_s8p_from_payload.py"),
        "solve_script": _touch(variant_dir / "solve_export_hfss_s8p.py"),
        "payload_json": _touch(variant_dir / "hfss_s8p_build_payload.json"),
        "single_variant_packet_summary": _touch(variant_dir / "single_variant_packet_summary.json"),
        "postrun_out_dir": str(variant_dir / "postrun_validation"),
        "env": {"HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore"},
    }


def _write_inputs(tmp_path: Path, *, current_gate_count: int = 0) -> tuple[Path, Path, Path, Path]:
    diagnosis = tmp_path / "diagnosis.json"
    diagnosis.write_text(
        json.dumps(
            {
                "pass_count": 0,
                "best_target_marker": {
                    "target15_worst_percent_error": 40.5,
                    "target15_worst_metric": "ls_nh",
                    "target15_core_percent_errors": {
                        "lp_nh": 35.0,
                        "ls_nh": 40.5,
                        "q": 17.0,
                        "k": 1.5,
                        "kw": 1.5,
                    },
                },
                "failure_mode_counts": {
                    "HFSS_INDUCTANCE_SCALE_TOO_SMALL_CHECK_GEOMETRY_UNITS_OR_METAL_STACK": 11,
                    "NON_POSITIVE_Q_CHECK_LOSS_MODEL_TERMINAL_REFERENCE_OR_GROUND": 11,
                    "COUPLING_SIGN_MISMATCH_CHECK_PORT_ORDER_POLARITY_WINDING_DIRECTION": 10,
                },
                "hfss_to_emx_ratio_statistics": {
                    "lp_nh": {"median": 0.26},
                    "ls_nh": {"median": 0.08},
                },
                "sign_mismatch_counts": {"k": 10, "kw": 10},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    v66 = tmp_path / "v66.json"
    v67 = tmp_path / "v67.json"
    v66.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "out_dir": str(tmp_path / "v66_out"),
                "variants": [
                    _variant(tmp_path, "v66a_best_marker_reference_bbox", source="v66"),
                    _variant(tmp_path, "v66b_pyaedt_terminal_reference", source="v66"),
                    _variant(tmp_path, "v66c_all_m5_reference", source="v66"),
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    v67.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "out_dir": str(tmp_path / "v67_out"),
                "variants": [
                    _variant(tmp_path, "v67a_tight_mesh_baseline"),
                    _variant(tmp_path, "v67b_solve_inside_conductors"),
                    _variant(tmp_path, "v67c_solve_inside_loss_tangent"),
                    _variant(tmp_path, "v67e_no_unite_solve_inside"),
                    _variant(tmp_path, "v67h_skip_pin_fixture_diagnostic", diagnostic_only=True),
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    intake = tmp_path / "intake.json"
    intake.write_text(
        json.dumps(
            {
                "latest_intake_summary": {
                    "counts": {"current_gate_spec_pass_count": current_gate_count}
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return diagnosis, v66, v67, intake


def test_prioritizes_v67_material_mesh_when_k_is_close_but_lp_ls_q_fail(tmp_path):
    mod = _load_module()
    diagnosis, v66, v67, intake = _write_inputs(tmp_path)

    status = mod.main(
        [
            "--diagnosis-summary",
            str(diagnosis),
            "--v66-plan-summary",
            str(v66),
            "--v67-plan-summary",
            str(v67),
            "--intake-monitor-summary",
            str(intake),
            "--out-dir",
            str(tmp_path / "out"),
            "--max-variants",
            "4",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_next_calibration_queue_summary.json").read_text(encoding="utf-8"))
    names = [item["name"] for item in summary["queue"]]
    assert summary["decision"] == "RUN_PRIORITIZED_HFSS_QUEUE_THEN_POSTRUN_GATE"
    assert names[0] == "v67b_solve_inside_conductors"
    assert "v67a_tight_mesh_baseline" in names
    assert "v67h_skip_pin_fixture_diagnostic" not in names
    assert "million" in " ".join(summary["safety_notes"]).lower()


def test_current_gate_file_changes_decision_to_validation_first(tmp_path):
    mod = _load_module()
    diagnosis, v66, v67, intake = _write_inputs(tmp_path, current_gate_count=1)

    status = mod.main(
        [
            "--diagnosis-summary",
            str(diagnosis),
            "--v66-plan-summary",
            str(v66),
            "--v67-plan-summary",
            str(v67),
            "--intake-monitor-summary",
            str(intake),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_next_calibration_queue_summary.json").read_text(encoding="utf-8"))
    assert summary["decision"] == "CURRENT_GATE_HFSS_S8P_EXISTS_RUN_VALIDATION_MONITOR_BEFORE_MORE_HFSS"
    assert summary["current_gate_spec_pass_count"] == 1


def test_missing_required_variant_file_fails_queue_inputs(tmp_path):
    mod = _load_module()
    diagnosis, v66, v67, intake = _write_inputs(tmp_path)
    plan = json.loads(v67.read_text(encoding="utf-8"))
    Path(plan["variants"][0]["build_script"]).unlink()
    v67.write_text(json.dumps(plan, indent=2), encoding="utf-8")

    status = mod.main(
        [
            "--diagnosis-summary",
            str(diagnosis),
            "--v66-plan-summary",
            str(v66),
            "--v67-plan-summary",
            str(v67),
            "--intake-monitor-summary",
            str(intake),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_next_calibration_queue_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert any(check["status"] == "FAIL" and "required file exists" in check["name"] for check in summary["checks"])


def test_windows_path_prefix_is_applied_to_runner_and_cmd_launcher(tmp_path):
    mod = _load_module()
    diagnosis, v66, v67, intake = _write_inputs(tmp_path)

    status = mod.main(
        [
            "--diagnosis-summary",
            str(diagnosis),
            "--v66-plan-summary",
            str(v66),
            "--v67-plan-summary",
            str(v67),
            "--intake-monitor-summary",
            str(intake),
            "--out-dir",
            str(tmp_path / "out"),
            "--max-variants",
            "1",
            "--windows-path-prefix",
            f"{tmp_path}=Z:\\hfss_queue",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_next_calibration_queue_summary.json").read_text(encoding="utf-8"))
    runner = (tmp_path / "out" / "run_hfss_priority_calibration_queue.windows.ps1").read_text(encoding="utf-8")
    cmd = (tmp_path / "out" / "run_hfss_priority_calibration_queue.windows.cmd").read_text(encoding="utf-8")
    assert summary["windows_path_mapping"]["status"] == "EXPLICIT_PREFIX_MAPPING"
    assert "Z:\\hfss_queue\\out\\priority_queue_run_status\\hfss_priority_queue_run_status.json" in runner
    assert "Z:\\hfss_queue\\out\\run_hfss_priority_calibration_queue.windows.ps1" in cmd
    assert str(tmp_path) not in runner


def test_windows_runner_is_utf8_bom_for_windows_powershell_unicode_paths(tmp_path):
    mod = _load_module()
    diagnosis, v66, v67, intake = _write_inputs(tmp_path)

    status = mod.main(
        [
            "--diagnosis-summary",
            str(diagnosis),
            "--v66-plan-summary",
            str(v66),
            "--v67-plan-summary",
            str(v67),
            "--intake-monitor-summary",
            str(intake),
            "--out-dir",
            str(tmp_path / "out"),
            "--max-variants",
            "1",
        ]
    )

    assert status == 0
    runner_bytes = (tmp_path / "out" / "run_hfss_priority_calibration_queue.windows.ps1").read_bytes()
    assert runner_bytes.startswith(b"\xef\xbb\xbf")
