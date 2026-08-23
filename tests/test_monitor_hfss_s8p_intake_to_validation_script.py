from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "monitor_hfss_s8p_intake_to_validation.py"
    spec = importlib.util.spec_from_file_location("monitor_hfss_s8p_intake_to_validation_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fake_intake(root: Path, *, counts: dict[str, int], decision: str = "FAKE_INTAKE_DECISION") -> Path:
    script = root / "fake_intake.py"
    script.write_text(
        "import argparse, json, pathlib\n"
        "counts = " + repr(counts) + "\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--no-fail-exit', action='store_true'); p.add_argument('--search-root', action='append'); p.add_argument('--current-gate-root', action='append'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        "summary={'overall_status':'PASS' if counts.get('current_gate_spec_pass_count', 0) else 'WAITING_FOR_CURRENT_GATE_HFSS','decision':" + repr(decision) + ",'counts':counts}\n"
        "(out/'hfss_s8p_global_intake_audit_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status='+summary['overall_status'])\n",
        encoding="utf-8",
    )
    return script


def _write_fake_validation_monitor(root: Path, *, status: str = "DRY_RUN") -> Path:
    script = root / "fake_validation_monitor.py"
    script.write_text(
        "import argparse, json, pathlib\n"
        "status = " + repr(status) + "\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--timeout-seconds'); p.add_argument('--poll-seconds'); p.add_argument('--no-fail-exit', action='store_true'); p.add_argument('--allow-real-emx', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        "(out/'called.txt').write_text('yes', encoding='utf-8')\n"
        "summary={'overall_status':status,'decision':'VALIDATION_'+status,'attempt_count':1,'arguments':{'allow_real_emx':ns.allow_real_emx}}\n"
        "(out/'s8p_validation_to_million_autopipeline_monitor_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status='+status)\n",
        encoding="utf-8",
    )
    return script


def test_current_gate_spec_pass_runs_validation_monitor(tmp_path):
    mod = _load_module()
    fake_intake = _write_fake_intake(
        tmp_path,
        counts={
            "global_s8p_count": 1,
            "current_gate_spec_pass_count": 1,
            "user_drop_spec_pass_count": 0,
            "historical_or_report_spec_pass_count": 0,
        },
    )
    fake_validation = _write_fake_validation_monitor(tmp_path, status="DRY_RUN")

    status = mod.main(
        [
            "--intake-script",
            str(fake_intake),
            "--intake-out-dir",
            str(tmp_path / "intake"),
            "--validation-monitor-script",
            str(fake_validation),
            "--validation-monitor-out-dir",
            str(tmp_path / "validation"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    assert (tmp_path / "validation" / "called.txt").is_file()
    summary = json.loads((tmp_path / "out" / "hfss_s8p_intake_to_validation_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "DRY_RUN"
    assert summary["decision"] == "CURRENT_GATE_VALIDATION_READY_DRY_RUN_COMPLETED"
    assert summary["attempts"][0]["intake_status"] == "CURRENT_GATE_READY"


def test_user_drop_spec_pass_waits_for_staging_without_running_validation(tmp_path):
    mod = _load_module()
    fake_intake = _write_fake_intake(
        tmp_path,
        counts={
            "global_s8p_count": 1,
            "current_gate_spec_pass_count": 0,
            "user_drop_spec_pass_count": 1,
            "historical_or_report_spec_pass_count": 0,
        },
    )
    fake_validation = _write_fake_validation_monitor(tmp_path, status="DRY_RUN")

    status = mod.main(
        [
            "--intake-script",
            str(fake_intake),
            "--intake-out-dir",
            str(tmp_path / "intake"),
            "--validation-monitor-script",
            str(fake_validation),
            "--validation-monitor-out-dir",
            str(tmp_path / "validation"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    assert not (tmp_path / "validation" / "called.txt").exists()
    summary = json.loads((tmp_path / "out" / "hfss_s8p_intake_to_validation_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_STAGING"
    assert summary["decision"] == "USER_DROP_S8P_FOUND_STAGE_TO_CURRENT_GATE_BEFORE_VALIDATION"
    assert "stage_hfss_s8p_manual_import_to_gate.py" in summary["recommended_next_action"]


def test_only_historical_report_spec_pass_waits_without_running_validation(tmp_path):
    mod = _load_module()
    fake_intake = _write_fake_intake(
        tmp_path,
        counts={
            "global_s8p_count": 22,
            "current_gate_spec_pass_count": 0,
            "user_drop_spec_pass_count": 0,
            "historical_or_report_spec_pass_count": 22,
        },
        decision="ONLY_HISTORICAL_OR_REPORT_S8P_FOUND_CURRENT_GATE_STILL_EMPTY",
    )
    fake_validation = _write_fake_validation_monitor(tmp_path, status="DRY_RUN")

    status = mod.main(
        [
            "--intake-script",
            str(fake_intake),
            "--intake-out-dir",
            str(tmp_path / "intake"),
            "--validation-monitor-script",
            str(fake_validation),
            "--validation-monitor-out-dir",
            str(tmp_path / "validation"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    assert not (tmp_path / "validation" / "called.txt").exists()
    summary = json.loads((tmp_path / "out" / "hfss_s8p_intake_to_validation_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_CURRENT_GATE_HFSS"
    assert summary["decision"] == "ONLY_HISTORICAL_OR_REPORT_S8P_FOUND_CURRENT_GATE_STILL_EMPTY"


def test_validation_monitor_failure_is_reported_after_current_gate_ready(tmp_path):
    mod = _load_module()
    fake_intake = _write_fake_intake(
        tmp_path,
        counts={
            "global_s8p_count": 1,
            "current_gate_spec_pass_count": 1,
            "user_drop_spec_pass_count": 0,
            "historical_or_report_spec_pass_count": 0,
        },
    )
    fake_validation = _write_fake_validation_monitor(tmp_path, status="FAIL")

    status = mod.main(
        [
            "--intake-script",
            str(fake_intake),
            "--intake-out-dir",
            str(tmp_path / "intake"),
            "--validation-monitor-script",
            str(fake_validation),
            "--validation-monitor-out-dir",
            str(tmp_path / "validation"),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_s8p_intake_to_validation_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "VALIDATION_FAIL"
