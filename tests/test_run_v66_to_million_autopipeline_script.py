from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import subprocess
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_v66_to_million_autopipeline.py"
    spec = importlib.util.spec_from_file_location("run_v66_to_million_autopipeline_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fake_scripts(root: Path, *, watcher_status: str, executor_status: str = "DRY_RUN", report_status: str = "WAITING_FOR_HFSS") -> tuple[Path, Path, Path, Path]:
    watcher = root / "watcher.py"
    executor = root / "executor.py"
    resilient = root / "resilient.py"
    report = root / "report.py"
    watcher.write_text(
        "import argparse, json, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--timeout-seconds'); p.add_argument('--poll-seconds'); p.add_argument('--no-fail-exit', action='store_true'); p.add_argument('--allow-real-emx', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        f"status={watcher_status!r}\n"
        "decision='READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN' if status=='PASS' else ('WAIT_FOR_V66_EXPORTED_HFSS_S8P' if status=='WAITING_FOR_HFSS' else 'ALL_V66_VARIANTS_FAILED_EMX_HFSS_GATE')\n"
        "summary={'overall_status':status,'decision':decision,'latest':{'variant_status_counts':{'WAITING_FOR_HFSS':8},'execution_packet_audit_summary':{'overall_status':'PASS','hfss_result_status':'WAITING_FOR_HFSS_EXPORT','exported_s8p_count':0}},'arguments':{'allow_real_emx':ns.allow_real_emx}}\n"
        "(out/'hfss_v66_calibration_to_million_gate_watch_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status='+status)\n",
        encoding="utf-8",
    )
    executor.write_text(
        "import argparse, json, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--no-fail-exit', action='store_true'); p.add_argument('--allow-real-emx', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        f"status={executor_status!r}\n"
        "summary={'overall_status':status,'decision':'MILLION_EXECUTOR_'+status,'allow_real_emx':ns.allow_real_emx,'selected_chunk_count':10,'completed_chunk_count':10 if status=='PASS' else 0}\n"
        "(out/'s8p_million_campaign_execution_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status='+status)\n",
        encoding="utf-8",
    )
    resilient.write_text(
        "import argparse, json, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--no-fail-exit', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        "summary={'overall_status':'PASS','decision':'RESILIENT_HFSS_RUNNER_READY_NOT_YET_RUN','filesystem_exported_s8p_count':0}\n"
        "(out/'hfss_v66_resilient_runner_audit_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status=PASS')\n",
        encoding="utf-8",
    )
    report.write_text(
        "import argparse, json, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--resilient-runner-summary'); p.add_argument('--no-fail-exit', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        f"status={report_status!r}\n"
        "decision='READY_FOR_PROFESSOR_REPORT_AND_MILLION_GATE' if status=='PASS' else ('WAIT_FOR_HFSS_S8P_BEFORE_REPORTING_PASS' if status=='WAITING_FOR_HFSS' else 'GATE_EVIDENCE_INCOMPLETE')\n"
        "summary={'overall_status':status,'decision':decision,'postrun_status':status,'million_execution_status':'FAIL','resilient_runner_summary':ns.resilient_runner_summary,'historical_recompare_candidate_count':22,'historical_recompare_pass_count':0,'historical_recompare_best_target15_worst_percent_error':40.54}\n"
        "(out/'v66_validation_report_packet_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status='+status)\n",
        encoding="utf-8",
    )
    return watcher, executor, resilient, report


def test_waiting_for_hfss_does_not_run_executor(tmp_path):
    mod = _load_module()
    watcher, executor, resilient, report = _write_fake_scripts(tmp_path, watcher_status="WAITING_FOR_HFSS")
    seen: list[str] = []
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        seen.append(" ".join(str(item) for item in command))
        return real_run(command, **kwargs)

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--watcher-script",
                str(watcher),
                "--executor-script",
                str(executor),
                "--resilient-audit-script",
                str(resilient),
                "--report-packet-script",
                str(report),
                "--watch-out-dir",
                str(tmp_path / "watch"),
                "--executor-out-dir",
                str(tmp_path / "exec"),
                "--resilient-audit-out-dir",
                str(tmp_path / "resilient"),
                "--report-packet-out-dir",
                str(tmp_path / "report"),
                "--out-dir",
                str(tmp_path / "out"),
            ]
        )

    assert status == 0
    assert any("watcher.py" in item for item in seen)
    assert any("resilient.py" in item for item in seen)
    assert any("report.py" in item for item in seen)
    assert not any("executor.py" in item for item in seen)
    summary = json.loads((tmp_path / "out" / "v66_to_million_autopipeline_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_HFSS"
    assert summary["decision"] == "WAIT_FOR_V66_EXPORTED_HFSS_S8P_BEFORE_MILLION_EXECUTION"
    assert summary["resilient_audit_summary"]["resilient_runner_status"] == "PASS"
    assert summary["report_packet_summary"]["report_packet_status"] == "WAITING_FOR_HFSS"
    assert summary["report_packet_summary"]["historical_recompare_pass_count"] == 0


def test_passed_watcher_runs_executor_dry_run_by_default(tmp_path):
    mod = _load_module()
    watcher, executor, resilient, report = _write_fake_scripts(tmp_path, watcher_status="PASS", executor_status="DRY_RUN", report_status="PASS")

    status = mod.main(
        [
            "--watcher-script",
            str(watcher),
            "--executor-script",
            str(executor),
            "--resilient-audit-script",
            str(resilient),
            "--report-packet-script",
            str(report),
            "--watch-out-dir",
            str(tmp_path / "watch"),
            "--executor-out-dir",
            str(tmp_path / "exec"),
            "--resilient-audit-out-dir",
            str(tmp_path / "resilient"),
            "--report-packet-out-dir",
            str(tmp_path / "report"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_to_million_autopipeline_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "DRY_RUN"
    assert summary["decision"] == "V66_GATE_PASSED_MILLION_EXECUTOR_DRY_RUN_READY"
    assert summary["report_packet_summary"]["report_packet_status"] == "PASS"
    assert summary["report_packet_summary"]["historical_recompare_best_target15_worst_percent_error"] == 40.54
    executor_summary = json.loads((tmp_path / "exec" / "s8p_million_campaign_execution_summary.json").read_text(encoding="utf-8"))
    assert executor_summary["allow_real_emx"] is False


def test_allow_real_emx_is_forwarded_to_watcher_and_executor(tmp_path):
    mod = _load_module()
    watcher, executor, resilient, report = _write_fake_scripts(tmp_path, watcher_status="PASS", executor_status="PASS", report_status="PASS")

    status = mod.main(
        [
            "--watcher-script",
            str(watcher),
            "--executor-script",
            str(executor),
            "--resilient-audit-script",
            str(resilient),
            "--report-packet-script",
            str(report),
            "--watch-out-dir",
            str(tmp_path / "watch"),
            "--executor-out-dir",
            str(tmp_path / "exec"),
            "--resilient-audit-out-dir",
            str(tmp_path / "resilient"),
            "--report-packet-out-dir",
            str(tmp_path / "report"),
            "--out-dir",
            str(tmp_path / "out"),
            "--allow-real-emx",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_to_million_autopipeline_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "V66_GATE_PASSED_AND_MILLION_EXECUTION_COMPLETED"
    watcher_summary = json.loads((tmp_path / "watch" / "hfss_v66_calibration_to_million_gate_watch_summary.json").read_text(encoding="utf-8"))
    executor_summary = json.loads((tmp_path / "exec" / "s8p_million_campaign_execution_summary.json").read_text(encoding="utf-8"))
    assert watcher_summary["arguments"]["allow_real_emx"] is True
    assert executor_summary["allow_real_emx"] is True
