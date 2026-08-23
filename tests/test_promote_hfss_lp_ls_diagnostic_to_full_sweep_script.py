from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "promote_hfss_lp_ls_diagnostic_to_full_sweep.py"
    spec = importlib.util.spec_from_file_location("promote_hfss_lp_ls_diagnostic_to_full_sweep_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_diagnostic_aedt(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "frequency_grid_purpose": "diagnostic",
                "handoff_summary": str(path.parent / "handoff.json"),
                "proc_file": str(path.parent / "proc.proc"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_postrun(path: Path, *, status: str, worst: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if status == "PASS":
        data = {
            "overall_status": "PASS",
            "decision": "ACCEPT_DIAGNOSTIC_S8P_EMX_HFSS_SCREENING_ONLY_NOT_FINAL",
            "frequency_grid_mode": "diagnostic_screening_only",
            "final_acceptance_candidate": False,
            "records": [{"status": "PASS", "worst_percent_error": worst}],
        }
    elif status == "WAITING_FOR_HFSS":
        data = {
            "overall_status": "WAITING_FOR_HFSS",
            "decision": "WAIT_FOR_EXPORTED_HFSS_S8P",
            "frequency_grid_mode": "diagnostic_screening_only",
            "final_acceptance_candidate": False,
            "records": [{"status": "WAITING_FOR_HFSS", "worst_percent_error": None}],
        }
    else:
        data = {
            "overall_status": "FAIL",
            "decision": "DO_NOT_USE_S8P_HFSS_VALIDATION_YET",
            "frequency_grid_mode": "diagnostic_screening_only",
            "final_acceptance_candidate": False,
            "records": [{"status": "FAIL", "worst_percent_error": worst}],
        }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_plan(root: Path, *, statuses: dict[str, tuple[str, float | None]]) -> Path:
    aedt = root / "diag_aedt" / "hfss_s8p_aedt_script_packet_summary.json"
    aedt.parent.mkdir(parents=True)
    _write_diagnostic_aedt(aedt)
    variants = []
    for name, (status, worst) in statuses.items():
        postrun_dir = root / "variants" / name / "postrun_validation"
        _write_postrun(postrun_dir / "s8p_hfss_postrun_validation_summary.json", status=status, worst=worst)
        variants.append(
            {
                "name": name,
                "postrun_out_dir": str(postrun_dir),
                "env": {
                    "HFSS_PORT_REFERENCE_MODE": name,
                    "HFSS_PORT_DEEMBED": "1" if name.endswith("best") else "0",
                },
            }
        )
    plan = root / "hfss_lp_ls_reference_sweep_plan_summary.json"
    plan.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "aedt_packet_summary": str(aedt),
                "variants": variants,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return plan


def _write_full_aedt(path: Path) -> None:
    sample_dir = path.parent / "samples" / "sample_a"
    sample_dir.mkdir(parents=True)
    build = sample_dir / "build_hfss_s8p_from_payload.py"
    solve = sample_dir / "solve_export_hfss_s8p.py"
    build.write_text("print('build')\n", encoding="utf-8")
    solve.write_text("print('solve')\n", encoding="utf-8")
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "frequency_grid_purpose": "production",
                "frequency_grid": {
                    "start_ghz": 5.0,
                    "stop_ghz": 60.0,
                    "step_ghz": 0.5,
                    "points": 111,
                },
                "out_dir": str(path.parent),
                "sample_results": [
                    {
                        "overall_status": "PASS",
                        "evaluation": "sample_a",
                        "script_dir": str(sample_dir),
                        "build_script": str(build),
                        "solve_script": str(solve),
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_waits_when_all_diagnostic_variants_wait_for_hfss(tmp_path):
    module = _load_module()
    plan = _write_plan(
        tmp_path,
        statuses={
            "v65a": ("WAITING_FOR_HFSS", None),
            "v65b": ("WAITING_FOR_HFSS", None),
        },
    )

    status = module.main(
        [
            "--diagnostic-plan-summary",
            str(plan),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_lp_ls_full_sweep_promotion_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_DIAGNOSTIC_HFSS"
    assert summary["decision"] == "WAIT_FOR_V65_DIAGNOSTIC_HFSS_S8P"
    assert summary["selected_variant"] == {}
    assert summary["full_windows_runner"] == ""


def test_blocks_when_no_diagnostic_variant_passed(tmp_path):
    module = _load_module()
    plan = _write_plan(
        tmp_path,
        statuses={
            "v65a": ("FAIL", 28.0),
            "v65b": ("FAIL", 15.0),
        },
    )

    status = module.main(
        [
            "--diagnostic-plan-summary",
            str(plan),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_lp_ls_full_sweep_promotion_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "NO_DIAGNOSTIC_VARIANT_PASSED_DO_NOT_RUN_FULL_SWEEP"


def test_selects_best_diagnostic_pass_and_builds_full_runner(tmp_path):
    module = _load_module()
    plan = _write_plan(
        tmp_path,
        statuses={
            "v65_ok": ("PASS", 8.8),
            "v65_best": ("PASS", 3.2),
        },
    )
    full = tmp_path / "full_aedt" / "hfss_s8p_aedt_script_packet_summary.json"
    full.parent.mkdir()
    _write_full_aedt(full)

    status = module.main(
        [
            "--diagnostic-plan-summary",
            str(plan),
            "--full-aedt-packet-summary",
            str(full),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_lp_ls_full_sweep_promotion_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "READY_TO_RUN_SELECTED_VARIANT_FULL_5_60_HFSS_SWEEP"
    assert summary["selected_variant"]["name"] == "v65_best"
    assert summary["selected_variant"]["worst_percent_error"] == 3.2
    windows = Path(summary["full_windows_runner"]).read_text(encoding="utf-8")
    postrun = Path(summary["full_postrun_validator"]).read_text(encoding="utf-8")
    assert "HFSS_PORT_REFERENCE_MODE = 'v65_best'" in windows
    assert "HFSS_PORT_DEEMBED = '1'" in windows
    assert "solve_export_hfss_s8p.py" in windows
    assert "--compare-start-ghz 5.0" in postrun
    assert "--compare-stop-ghz 60.0" in postrun
    assert "--expected-frequency-points 111" in postrun
