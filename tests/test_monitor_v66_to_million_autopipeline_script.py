from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import subprocess
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "monitor_v66_to_million_autopipeline.py"
    spec = importlib.util.spec_from_file_location("monitor_v66_to_million_autopipeline_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fake_autopipeline(root: Path, *, statuses: list[str]) -> Path:
    script = root / "fake_autopipeline.py"
    script.write_text(
        "import argparse, json, pathlib\n"
        "statuses = " + repr(statuses) + "\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--timeout-seconds'); p.add_argument('--no-fail-exit', action='store_true'); p.add_argument('--allow-real-emx', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        "counter=out/'counter.txt'\n"
        "idx=int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(idx+1))\n"
        "status=statuses[min(idx, len(statuses)-1)]\n"
        "decision={'WAITING_FOR_HFSS':'WAIT_FOR_V66_EXPORTED_HFSS_S8P_BEFORE_MILLION_EXECUTION','DRY_RUN':'V66_GATE_PASSED_MILLION_EXECUTOR_DRY_RUN_READY','PASS':'V66_GATE_PASSED_AND_MILLION_EXECUTION_COMPLETED','FAIL':'V66_WATCHER_FAILED_DO_NOT_EXECUTE_MILLION'}[status]\n"
        "summary={'overall_status':status,'decision':decision,'allow_real_emx':ns.allow_real_emx,'watcher_summary':{'overall_status':status},'executor_summary':{},'resilient_audit_summary':{'overall_status':'PASS','decision':'RESILIENT_HFSS_RUNNER_READY_NOT_YET_RUN','resilient_exported_s8p_count':0}}\n"
        "(out/'v66_to_million_autopipeline_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status='+status)\n",
        encoding="utf-8",
    )
    return script


def test_single_check_waits_for_hfss(tmp_path):
    mod = _load_module()
    fake = _write_fake_autopipeline(tmp_path, statuses=["WAITING_FOR_HFSS"])

    status = mod.main(
        [
            "--autopipeline-script",
            str(fake),
            "--autopipeline-out-dir",
            str(tmp_path / "auto"),
            "--out-dir",
            str(tmp_path / "out"),
            "--skip-visible-runner-audit",
            "--skip-resilient-runner-audit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_HFSS"
    assert summary["attempt_count"] == 1
    assert summary["attempts"][0]["resilient_audit_summary"]["decision"] == "RESILIENT_HFSS_RUNNER_READY_NOT_YET_RUN"


def test_monitor_retries_until_dry_run_ready(tmp_path):
    mod = _load_module()
    fake = _write_fake_autopipeline(tmp_path, statuses=["WAITING_FOR_HFSS", "DRY_RUN"])

    status = mod.main(
        [
            "--autopipeline-script",
            str(fake),
            "--autopipeline-out-dir",
            str(tmp_path / "auto"),
            "--out-dir",
            str(tmp_path / "out"),
            "--timeout-seconds",
            "2",
            "--poll-seconds",
            "0",
            "--skip-visible-runner-audit",
            "--skip-resilient-runner-audit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "DRY_RUN"
    assert summary["decision"] == "AUTOPIPELINE_READY_DRY_RUN_COMPLETED"
    assert summary["attempt_count"] == 2


def test_monitor_reports_failure(tmp_path):
    mod = _load_module()
    fake = _write_fake_autopipeline(tmp_path, statuses=["FAIL"])

    status = mod.main(
        [
            "--autopipeline-script",
            str(fake),
            "--autopipeline-out-dir",
            str(tmp_path / "auto"),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
            "--skip-visible-runner-audit",
            "--skip-resilient-runner-audit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "V66_WATCHER_FAILED_DO_NOT_EXECUTE_MILLION"


def test_allow_real_emx_is_forwarded(tmp_path):
    mod = _load_module()
    fake = _write_fake_autopipeline(tmp_path, statuses=["PASS"])

    status = mod.main(
        [
            "--autopipeline-script",
            str(fake),
            "--autopipeline-out-dir",
            str(tmp_path / "auto"),
            "--out-dir",
            str(tmp_path / "out"),
            "--allow-real-emx",
            "--skip-visible-runner-audit",
            "--skip-resilient-runner-audit",
        ]
    )

    assert status == 0
    autopipeline_summary = json.loads((tmp_path / "auto" / "v66_to_million_autopipeline_summary.json").read_text(encoding="utf-8"))
    assert autopipeline_summary["allow_real_emx"] is True
    monitor_summary = json.loads((tmp_path / "out" / "v66_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert monitor_summary["overall_status"] == "PASS"


def _write_fake_visible_audit(root: Path, *, status: str = "PASS", decision: str = "VISIBLE_HFSS_RUNNER_READY_NOT_YET_RUN") -> Path:
    script = root / "fake_visible_audit.py"
    script.write_text(
        "import argparse, json, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--no-fail-exit', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        f"summary={{'overall_status':{status!r},'decision':{decision!r},'exported_s8p_count':0,'export_manifest_count':0}}\n"
        "(out/'hfss_v66_visible_runner_audit_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status='+summary['overall_status'])\n",
        encoding="utf-8",
    )
    return script


def _write_fake_resilient_audit(root: Path, *, status: str = "PASS", decision: str = "RESILIENT_HFSS_RUNNER_READY_NOT_YET_RUN") -> Path:
    script = root / f"fake_resilient_audit_{status.lower()}.py"
    script.write_text(
        "import argparse, json, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--no-fail-exit', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        f"summary={{'overall_status':{status!r},'decision':{decision!r},'filesystem_exported_s8p_count':0}}\n"
        "(out/'hfss_v66_resilient_runner_audit_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status='+summary['overall_status'])\n",
        encoding="utf-8",
    )
    return script


def test_visible_runner_audit_failure_does_not_block_when_resilient_runner_is_available(tmp_path):
    mod = _load_module()
    fake_auto = _write_fake_autopipeline(tmp_path, statuses=["WAITING_FOR_HFSS"])
    fake_visible = _write_fake_visible_audit(tmp_path, status="FAIL", decision="FIX_VISIBLE_HFSS_RUNNER_OR_FAILED_WINDOWS_RUN")
    fake_resilient = _write_fake_resilient_audit(tmp_path, status="PASS", decision="RESILIENT_HFSS_RUNNER_READY_NOT_YET_RUN")
    seen: list[str] = []
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        seen.append(" ".join(str(item) for item in command))
        return real_run(command, **kwargs)

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--autopipeline-script",
                str(fake_auto),
                "--visible-runner-audit-script",
                str(fake_visible),
                "--resilient-runner-audit-script",
                str(fake_resilient),
                "--autopipeline-out-dir",
                str(tmp_path / "auto"),
                "--visible-runner-audit-out-dir",
                str(tmp_path / "visible"),
                "--resilient-runner-audit-out-dir",
                str(tmp_path / "resilient"),
                "--out-dir",
                str(tmp_path / "out"),
                "--no-fail-exit",
            ]
        )

    assert status == 0
    assert any("fake_visible_audit.py" in item for item in seen)
    assert any("fake_resilient_audit_pass.py" in item for item in seen)
    assert any("fake_autopipeline.py" in item for item in seen)
    summary = json.loads((tmp_path / "out" / "v66_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_HFSS"
    assert summary["latest_visible_runner_summary"]["overall_status"] == "FAIL"
    assert summary["latest_resilient_runner_summary"]["overall_status"] == "PASS"


def test_both_runner_audits_failed_block_autopipeline(tmp_path):
    mod = _load_module()
    fake_auto = _write_fake_autopipeline(tmp_path, statuses=["PASS"])
    fake_visible = _write_fake_visible_audit(tmp_path, status="FAIL", decision="FIX_VISIBLE_HFSS_RUNNER_OR_FAILED_WINDOWS_RUN")
    fake_resilient = _write_fake_resilient_audit(tmp_path, status="FAIL", decision="FIX_RESILIENT_HFSS_RUNNER_PACKET")
    seen: list[str] = []
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        seen.append(" ".join(str(item) for item in command))
        return real_run(command, **kwargs)

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--autopipeline-script",
                str(fake_auto),
                "--visible-runner-audit-script",
                str(fake_visible),
                "--resilient-runner-audit-script",
                str(fake_resilient),
                "--autopipeline-out-dir",
                str(tmp_path / "auto"),
                "--visible-runner-audit-out-dir",
                str(tmp_path / "visible"),
                "--resilient-runner-audit-out-dir",
                str(tmp_path / "resilient"),
                "--out-dir",
                str(tmp_path / "out"),
                "--no-fail-exit",
            ]
        )

    assert status == 0
    assert any("fake_visible_audit.py" in item for item in seen)
    assert any("fake_resilient_audit_fail.py" in item for item in seen)
    assert not any("fake_autopipeline.py" in item for item in seen)
    summary = json.loads((tmp_path / "out" / "v66_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert "HFSS_RUNNER_AUDITS_FAILED" in summary["decision"]


def test_visible_runner_audit_is_recorded_before_waiting_autopipeline(tmp_path):
    mod = _load_module()
    fake_auto = _write_fake_autopipeline(tmp_path, statuses=["WAITING_FOR_HFSS"])
    fake_visible = _write_fake_visible_audit(tmp_path, status="PASS", decision="VISIBLE_HFSS_RUNNER_READY_NOT_YET_RUN")
    fake_resilient = _write_fake_resilient_audit(tmp_path, status="PASS", decision="RESILIENT_HFSS_RUNNER_READY_NOT_YET_RUN")

    status = mod.main(
        [
            "--autopipeline-script",
            str(fake_auto),
            "--visible-runner-audit-script",
            str(fake_visible),
            "--resilient-runner-audit-script",
            str(fake_resilient),
            "--autopipeline-out-dir",
            str(tmp_path / "auto"),
            "--visible-runner-audit-out-dir",
            str(tmp_path / "visible"),
            "--resilient-runner-audit-out-dir",
            str(tmp_path / "resilient"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_HFSS"
    assert summary["latest_visible_runner_summary"]["decision"] == "VISIBLE_HFSS_RUNNER_READY_NOT_YET_RUN"
    assert summary["latest_resilient_runner_summary"]["decision"] == "RESILIENT_HFSS_RUNNER_READY_NOT_YET_RUN"
    assert summary["latest_autopipeline_summary"]["resilient_audit_summary"]["resilient_exported_s8p_count"] == 0
    assert summary["attempts"][0]["visible_runner_status"] == "PASS"
    assert summary["attempts"][0]["resilient_runner_status"] == "PASS"
