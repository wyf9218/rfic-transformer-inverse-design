from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import subprocess
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_s8p_validation_to_million_autopipeline.py"
    spec = importlib.util.spec_from_file_location("run_s8p_validation_to_million_autopipeline_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fake_watcher(path: Path, *, label: str, status: str) -> None:
    summary_name = {
        "v66": "hfss_v66_calibration_to_million_gate_watch_summary.json",
        "v67": "hfss_v67_material_mesh_to_million_gate_watch_summary.json",
    }[label]
    wait_decision = {
        "v66": "WAIT_FOR_V66_EXPORTED_HFSS_S8P",
        "v67": "WAIT_FOR_V67_EXPORTED_HFSS_S8P",
    }[label]
    fail_decision = {
        "v66": "ALL_V66_VARIANTS_FAILED_EMX_HFSS_GATE",
        "v67": "ALL_V67_FINAL_CANDIDATE_VARIANTS_FAILED_EMX_HFSS_GATE",
    }[label]
    path.write_text(
        "import argparse, json, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--timeout-seconds'); p.add_argument('--poll-seconds'); p.add_argument('--no-fail-exit', action='store_true'); p.add_argument('--allow-real-emx', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        f"status={status!r}\n"
        f"decision='READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN' if status=='PASS' else ({wait_decision!r} if status=='WAITING_FOR_HFSS' else {fail_decision!r})\n"
        "summary={'overall_status':status,'decision':decision,'latest':{'variant_status_counts':{status:1},'selected_variant':{'name':'selected_'+status if status=='PASS' else ''},'campaign_summary':{'overall_status':'PASS'} if status=='PASS' else {}},'arguments':{'allow_real_emx':ns.allow_real_emx}}\n"
        f"(out/{summary_name!r}).write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status='+status)\n",
        encoding="utf-8",
    )


def _write_fake_executor(path: Path, *, status: str = "DRY_RUN") -> None:
    path.write_text(
        "import argparse, json, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--no-fail-exit', action='store_true'); p.add_argument('--allow-real-emx', action='store_true'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        f"status={status!r}\n"
        "summary={'overall_status':status,'decision':'EXECUTOR_'+status,'allow_real_emx':ns.allow_real_emx,'selected_chunk_count':10,'completed_chunk_count':10 if status=='PASS' else 0}\n"
        "(out/'s8p_million_campaign_execution_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('overall_status='+status)\n",
        encoding="utf-8",
    )


def test_waits_when_v66_and_v67_wait_for_hfss(tmp_path):
    mod = _load_module()
    v66 = tmp_path / "v66.py"
    v67 = tmp_path / "v67.py"
    executor = tmp_path / "executor.py"
    _write_fake_watcher(v66, label="v66", status="WAITING_FOR_HFSS")
    _write_fake_watcher(v67, label="v67", status="WAITING_FOR_HFSS")
    _write_fake_executor(executor)
    seen: list[str] = []
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        seen.append(" ".join(str(item) for item in command))
        return real_run(command, **kwargs)

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--v66-watcher-script",
                str(v66),
                "--v67-watcher-script",
                str(v67),
                "--executor-script",
                str(executor),
                "--v66-watch-out-dir",
                str(tmp_path / "v66_out"),
                "--v67-watch-out-dir",
                str(tmp_path / "v67_out"),
                "--executor-out-dir",
                str(tmp_path / "exec"),
                "--out-dir",
                str(tmp_path / "out"),
            ]
        )

    assert status == 0
    assert any("v66.py" in item for item in seen)
    assert any("v67.py" in item for item in seen)
    assert not any("executor.py" in item for item in seen)
    summary = json.loads((tmp_path / "out" / "s8p_validation_to_million_autopipeline_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_HFSS"
    assert summary["decision"] == "WAIT_FOR_ANY_S8P_VALIDATION_BRANCH_HFSS_EXPORT"
    assert summary["selected_source"] == ""


def test_v67_pass_runs_executor_when_v66_waits(tmp_path):
    mod = _load_module()
    v66 = tmp_path / "v66.py"
    v67 = tmp_path / "v67.py"
    executor = tmp_path / "executor.py"
    _write_fake_watcher(v66, label="v66", status="WAITING_FOR_HFSS")
    _write_fake_watcher(v67, label="v67", status="PASS")
    _write_fake_executor(executor, status="DRY_RUN")

    status = mod.main(
        [
            "--v66-watcher-script",
            str(v66),
            "--v67-watcher-script",
            str(v67),
            "--executor-script",
            str(executor),
            "--v66-watch-out-dir",
            str(tmp_path / "v66_out"),
            "--v67-watch-out-dir",
            str(tmp_path / "v67_out"),
            "--executor-out-dir",
            str(tmp_path / "exec"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "s8p_validation_to_million_autopipeline_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "DRY_RUN"
    assert summary["decision"] == "S8P_GATE_PASSED_MILLION_EXECUTOR_DRY_RUN_READY"
    assert summary["selected_source"] == "v67"
    assert summary["executor_summary"]["allow_real_emx"] is False


def test_v66_pass_skips_v67_and_runs_executor(tmp_path):
    mod = _load_module()
    v66 = tmp_path / "v66.py"
    v67 = tmp_path / "v67.py"
    executor = tmp_path / "executor.py"
    _write_fake_watcher(v66, label="v66", status="PASS")
    _write_fake_watcher(v67, label="v67", status="FAIL")
    _write_fake_executor(executor, status="PASS")
    seen: list[str] = []
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        seen.append(" ".join(str(item) for item in command))
        return real_run(command, **kwargs)

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--v66-watcher-script",
                str(v66),
                "--v67-watcher-script",
                str(v67),
                "--executor-script",
                str(executor),
                "--v66-watch-out-dir",
                str(tmp_path / "v66_out"),
                "--v67-watch-out-dir",
                str(tmp_path / "v67_out"),
                "--executor-out-dir",
                str(tmp_path / "exec"),
                "--out-dir",
                str(tmp_path / "out"),
                "--allow-real-emx",
            ]
        )

    assert status == 0
    assert any("v66.py" in item for item in seen)
    assert not any("v67.py" in item for item in seen)
    assert any("executor.py" in item for item in seen)
    summary = json.loads((tmp_path / "out" / "s8p_validation_to_million_autopipeline_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "S8P_GATE_PASSED_AND_MILLION_EXECUTION_COMPLETED"
    assert summary["selected_source"] == "v66"
    watcher_summary = json.loads((tmp_path / "v66_out" / "hfss_v66_calibration_to_million_gate_watch_summary.json").read_text(encoding="utf-8"))
    executor_summary = json.loads((tmp_path / "exec" / "s8p_million_campaign_execution_summary.json").read_text(encoding="utf-8"))
    assert watcher_summary["arguments"]["allow_real_emx"] is True
    assert executor_summary["allow_real_emx"] is True


def test_all_validation_branches_fail(tmp_path):
    mod = _load_module()
    v66 = tmp_path / "v66.py"
    v67 = tmp_path / "v67.py"
    executor = tmp_path / "executor.py"
    _write_fake_watcher(v66, label="v66", status="FAIL")
    _write_fake_watcher(v67, label="v67", status="FAIL")
    _write_fake_executor(executor)

    status = mod.main(
        [
            "--v66-watcher-script",
            str(v66),
            "--v67-watcher-script",
            str(v67),
            "--executor-script",
            str(executor),
            "--v66-watch-out-dir",
            str(tmp_path / "v66_out"),
            "--v67-watch-out-dir",
            str(tmp_path / "v67_out"),
            "--executor-out-dir",
            str(tmp_path / "exec"),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "s8p_validation_to_million_autopipeline_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "ALL_S8P_VALIDATION_BRANCHES_FAILED"
    assert summary["executor_result"] is None
