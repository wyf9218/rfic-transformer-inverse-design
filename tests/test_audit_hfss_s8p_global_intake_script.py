from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_hfss_s8p_global_intake.py"
    spec = importlib.util.spec_from_file_location("audit_hfss_s8p_global_intake_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_s8p(path: Path, *, points: int = 111, start_ghz: float = 5.0, step_ghz: float = 0.5, ports: int = 8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value_count = 2 * ports * ports
    lines = ["# GHz S RI R 50\n"]
    for index in range(points):
        freq = start_ghz + index * step_ghz
        lines.append(f"{freq:.12g} " + " ".join(["0"] * value_count) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


def _write_recompare(path: Path, *, pass_count: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "candidate_count": 22,
                "pass_count": pass_count,
                "best": {"worst_percent_error": 1476.8879},
                "target15_best": {"worst_percent_error": 40.5412},
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_no_s8p_files_waits_for_hfss(tmp_path):
    mod = _load_module()
    recompare = tmp_path / "strict" / "summary.json"
    _write_recompare(recompare)

    status = mod.main(
        [
            "--search-root",
            str(tmp_path / "empty"),
            "--current-gate-root",
            str(tmp_path / "gate"),
            "--strict-recompare-summary",
            str(recompare),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_global_intake_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_CURRENT_GATE_HFSS"
    assert summary["decision"] == "NO_HFSS_S8P_FOUND"
    assert summary["counts"]["global_s8p_count"] == 0


def test_current_gate_spec_pass_file_requests_unified_monitor(tmp_path):
    mod = _load_module()
    gate = tmp_path / "outputs" / "hfss_v67_material_mesh_calibration_plan_current"
    _write_s8p(gate / "variants" / "v67a" / "sample" / "hfss_export.s8p")

    status = mod.main(
        [
            "--search-root",
            str(tmp_path),
            "--current-gate-root",
            str(gate),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_global_intake_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "CURRENT_GATE_HFSS_S8P_FOUND_RUN_UNIFIED_MONITOR"
    assert summary["counts"]["current_gate_spec_pass_count"] == 1


def test_only_historical_report_spec_pass_does_not_unlock_current_gate(tmp_path):
    mod = _load_module()
    report = tmp_path / "reports" / "old_hfss"
    _write_s8p(report / "historical.s8p")

    status = mod.main(
        [
            "--search-root",
            str(tmp_path),
            "--current-gate-root",
            str(tmp_path / "outputs" / "hfss_v66_calibration_plan_current"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_global_intake_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_CURRENT_GATE_HFSS"
    assert summary["decision"] == "ONLY_HISTORICAL_OR_REPORT_S8P_FOUND_CURRENT_GATE_STILL_EMPTY"
    assert summary["counts"]["historical_or_report_spec_pass_count"] == 1
    assert summary["counts"]["current_gate_spec_pass_count"] == 0


def test_user_drop_spec_pass_requires_import_or_mapping(tmp_path):
    mod = _load_module()
    desktop = tmp_path / "Desktop"
    _write_s8p(desktop / "manual_export.s8p")

    status = mod.main(
        [
            "--search-root",
            str(desktop),
            "--current-gate-root",
            str(tmp_path / "outputs" / "hfss_v66_calibration_plan_current"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_global_intake_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_CURRENT_GATE_HFSS"
    assert summary["decision"] == "USER_DROP_S8P_FOUND_IMPORT_OR_MAP_BEFORE_GATE"
    assert summary["counts"]["user_drop_spec_pass_count"] == 1


def test_wrong_grid_file_is_counted_but_not_spec_pass(tmp_path):
    mod = _load_module()
    _write_s8p(tmp_path / "bad_grid.s8p", points=2, start_ghz=15.0)

    status = mod.main(
        [
            "--search-root",
            str(tmp_path),
            "--current-gate-root",
            str(tmp_path / "gate"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_global_intake_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["decision"] == "S8P_FILES_FOUND_BUT_NONE_MATCH_FINAL_TOUCHSTONE_CONTRACT"
    assert summary["counts"]["global_s8p_count"] == 1
    assert summary["counts"]["global_spec_pass_count"] == 0
