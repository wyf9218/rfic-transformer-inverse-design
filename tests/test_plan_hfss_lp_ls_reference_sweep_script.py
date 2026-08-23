from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plan_hfss_lp_ls_reference_sweep.py"
    spec = importlib.util.spec_from_file_location("plan_hfss_lp_ls_reference_sweep_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_diagnosis(path: Path, *, lp_floor: float = 23.0, ls_floor: float = 29.0) -> None:
    path.write_text(
        json.dumps(
            {
                "overall_status": "FAIL",
                "best_overall": {"target_max_pct": 29.3},
                "final_gate_metric_floor": {
                    "Lp": {"min_error_pct": lp_floor},
                    "Ls": {"min_error_pct": ls_floor},
                    "Kw": {"min_error_pct": 0.8},
                },
            }
        ),
        encoding="utf-8",
    )


def _write_aedt_packet(path: Path, root: Path, *, status: str = "PASS") -> None:
    sample_dir = root / "sample"
    sample_dir.mkdir(parents=True)
    build = sample_dir / "build_hfss_s8p_from_payload.py"
    solve = sample_dir / "solve_export_hfss_s8p.py"
    build.write_text("print('build')\n", encoding="utf-8")
    solve.write_text("print('solve')\n", encoding="utf-8")
    path.write_text(
        json.dumps(
            {
                "overall_status": status,
                "sample_results": [
                    {
                        "overall_status": "PASS",
                        "evaluation": "sample_a",
                        "script_dir": str(sample_dir),
                        "build_script": str(build),
                        "solve_script": str(solve),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_plans_lp_ls_reference_sweep_when_diagnosis_shows_inductance_floor_above_gate(tmp_path):
    module = _load_module()
    diagnosis = tmp_path / "diagnosis.json"
    aedt = tmp_path / "aedt.json"
    _write_diagnosis(diagnosis)
    _write_aedt_packet(aedt, tmp_path)

    status = module.main(
        [
            "--diagnosis-summary",
            str(diagnosis),
            "--aedt-packet-summary",
            str(aedt),
            "--out-dir",
            str(tmp_path / "out"),
            "--python-command",
            "python3",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_lp_ls_reference_sweep_plan_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "READY_TO_RUN_HFSS_LP_LS_DIAGNOSTIC_SWEEP"
    assert summary["variant_count"] == 10
    assert summary["diagnostic_frequency_grid"]["points"] == 2
    variants = {item["name"]: item for item in summary["variants"]}
    assert variants["v65a_baseline_local_bbox_smallest"]["env"]["HFSS_PORT_REFERENCE_MODE"] == "local_ground_bbox_smallest"
    assert variants["v65f_port_deembed_direct"]["env"]["HFSS_PORT_DEEMBED"] == "1"
    assert variants["v65g_terminal_reference_local"]["env"]["HFSS_USE_PYAEDT_REFERENCE_PORT"] == "1"
    windows = (tmp_path / "out" / "run_hfss_lp_ls_reference_sweep.windows.ps1").read_text(encoding="utf-8")
    postrun = (tmp_path / "out" / "postrun_validate_hfss_lp_ls_reference_sweep.sh").read_text(encoding="utf-8")
    assert "HFSS_PORT_REFERENCE_MODE" in windows
    assert "solve_export_hfss_s8p.py" in windows
    assert "run_s8p_hfss_postrun_validation_from_aedt_packet.py" in postrun
    assert "--expected-frequency-points 2" in postrun


def test_blocks_when_diagnosis_no_longer_shows_lp_ls_failure(tmp_path):
    module = _load_module()
    diagnosis = tmp_path / "diagnosis.json"
    aedt = tmp_path / "aedt.json"
    _write_diagnosis(diagnosis, lp_floor=8.0, ls_floor=9.0)
    _write_aedt_packet(aedt, tmp_path)

    status = module.main(
        [
            "--diagnosis-summary",
            str(diagnosis),
            "--aedt-packet-summary",
            str(aedt),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_lp_ls_reference_sweep_plan_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    failed = [item["name"] for item in summary["checks"] if item["status"] == "FAIL"]
    assert "diagnosis shows Lp still above gate" in failed
    assert "diagnosis shows Ls still above gate" in failed
