from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "monitor_s8p_validation_to_million_autopipeline.py"
    spec = importlib.util.spec_from_file_location("monitor_s8p_validation_to_million_autopipeline_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fake_autopipeline(root: Path, *, statuses: list[str]) -> Path:
    script = root / "fake_unified_autopipeline.py"
    script.write_text(
        "import argparse, json, pathlib\n"
        "statuses = " + repr(statuses) + "\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--timeout-seconds'); p.add_argument('--no-fail-exit', action='store_true'); p.add_argument('--allow-real-emx', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        "counter=out/'counter.txt'\n"
        "idx=int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(idx+1))\n"
        "status=statuses[min(idx, len(statuses)-1)]\n"
        "decision={'WAITING_FOR_HFSS':'WAIT_FOR_ANY_S8P_VALIDATION_BRANCH_HFSS_EXPORT','DRY_RUN':'S8P_GATE_PASSED_MILLION_EXECUTOR_DRY_RUN_READY','PASS':'S8P_GATE_PASSED_AND_MILLION_EXECUTION_COMPLETED','FAIL':'ALL_S8P_VALIDATION_BRANCHES_FAILED'}[status]\n"
        "summary={'overall_status':status,'decision':decision,'allow_real_emx':ns.allow_real_emx,'selected_source':'v67' if status in {'PASS','DRY_RUN'} else '', 'watchers':[{'label':'v66','summary':{'overall_status':'WAITING_FOR_HFSS','decision':'WAIT_FOR_V66_EXPORTED_HFSS_S8P'}},{'label':'v67','summary':{'overall_status':status,'decision':decision,'selected_variant':'v67a' if status in {'PASS','DRY_RUN'} else ''}}], 'executor_summary':{'overall_status':status,'decision':'EXECUTOR_'+status,'selected_chunk_count':10 if status in {'PASS','DRY_RUN'} else 0,'completed_chunk_count':10 if status=='PASS' else 0}}\n"
        "(out/'s8p_validation_to_million_autopipeline_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
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
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "s8p_validation_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_HFSS"
    assert summary["decision"] == "MONITOR_TIMEOUT_OR_SINGLE_CHECK_WAITING_FOR_HFSS"
    assert summary["attempt_count"] == 1
    assert summary["attempts"][0]["watchers"][1]["overall_status"] == "WAITING_FOR_HFSS"


def test_monitor_retries_until_unified_dry_run_ready(tmp_path):
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
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "s8p_validation_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "DRY_RUN"
    assert summary["decision"] == "UNIFIED_AUTOPIPELINE_READY_DRY_RUN_COMPLETED"
    assert summary["attempt_count"] == 2
    assert summary["latest_autopipeline_summary"]["selected_source"] == "v67"


def test_allow_real_emx_is_forwarded_to_unified_autopipeline(tmp_path):
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
        ]
    )

    assert status == 0
    autopipeline = json.loads((tmp_path / "auto" / "s8p_validation_to_million_autopipeline_summary.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "out" / "s8p_validation_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert autopipeline["allow_real_emx"] is True
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "UNIFIED_AUTOPIPELINE_COMPLETED"


def test_monitor_reports_unified_autopipeline_failure(tmp_path):
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
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "s8p_validation_to_million_autopipeline_monitor_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "ALL_S8P_VALIDATION_BRANCHES_FAILED"
    assert summary["attempt_count"] == 1
