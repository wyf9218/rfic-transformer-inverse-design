from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import subprocess
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "watch_hfss_v67_material_mesh_to_million_gate.py"
    spec = importlib.util.spec_from_file_location("watch_hfss_v67_material_mesh_to_million_gate_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_plan(path: Path, variants_root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    variants = []
    for name, diagnostic_only, final_candidate in (
        ("v67a_tight_mesh_baseline", False, True),
        ("v67h_skip_pin_fixture_diagnostic", True, False),
    ):
        out_dir = variants_root / name / "postrun_validation"
        variants.append(
            {
                "name": name,
                "diagnostic_only": diagnostic_only,
                "final_acceptance_candidate": final_candidate,
                "postrun_out_dir": str(out_dir),
            }
        )
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "RUN_V67_IF_V66_FAILS_OR_RUN_IN_PARALLEL_FOR_MATERIAL_MESH_DIAGNOSIS",
                "variant_count": len(variants),
                "variants": variants,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_postrun(out_dir: Path, *, status: str, worst: float | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    decision = {
        "PASS": "ACCEPT_SELECTED_S8P_EMX_HFSS_PHYSICAL_VALIDATION",
        "WAITING_FOR_HFSS": "WAIT_FOR_EXPORTED_HFSS_S8P",
        "FAIL": "DO_NOT_USE_S8P_HFSS_VALIDATION_YET",
    }[status]
    out_dir.joinpath("s8p_hfss_postrun_validation_summary.json").write_text(
        json.dumps(
            {
                "overall_status": status,
                "decision": decision,
                "frequency_grid_mode": "final_5_60_0p5_111",
                "final_acceptance_candidate": True,
                "sample_count": 1,
                "records": [{"status": status, "worst_percent_error": worst}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_campaign(path: Path, *, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": status,
                "decision": "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN" if status == "PASS" else "DO_NOT_START_MILLION_SAMPLE_CAMPAIGN_UNTIL_EMX_HFSS_S8P_GATE_PASSES",
                "chunk_count": 10 if status == "PASS" else 0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_waits_when_v67_hfss_exports_are_missing(tmp_path):
    mod = _load_module()
    plan = tmp_path / "plan.json"
    variants_root = tmp_path / "variants"
    out_dir = tmp_path / "watch"
    _write_plan(plan, variants_root)
    postrun = tmp_path / "postrun.sh"
    postrun.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append([str(item) for item in command])
        return mod.subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--v67-plan-summary",
                str(plan),
                "--v67-postrun-script",
                str(postrun),
                "--out-dir",
                str(out_dir),
                "--timeout-seconds",
                "0",
                "--skip-v67-runner-audit",
            ]
        )

    assert status == 0
    summary = json.loads((out_dir / "hfss_v67_material_mesh_to_million_gate_watch_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_HFSS"
    assert summary["decision"] == "WAIT_FOR_V67_EXPORTED_HFSS_S8P"
    assert summary["latest"]["variant_status_counts"] == {"MISSING": 2}
    assert len(seen) == 1


def test_v67_final_candidate_pass_invokes_million_planner(tmp_path):
    mod = _load_module()
    plan = tmp_path / "plan.json"
    variants_root = tmp_path / "variants"
    campaign_out = tmp_path / "campaign"
    out_dir = tmp_path / "watch"
    _write_plan(plan, variants_root)
    _write_postrun(variants_root / "v67a_tight_mesh_baseline" / "postrun_validation", status="PASS", worst=2.0)
    _write_postrun(variants_root / "v67h_skip_pin_fixture_diagnostic" / "postrun_validation", status="FAIL", worst=99.0)
    postrun = tmp_path / "postrun.sh"
    postrun.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        joined = " ".join(str(item) for item in command)
        if "run_gated_s8p_million_sample_campaign.py" in joined:
            _write_campaign(campaign_out / "s8p_million_sample_campaign_plan_summary.json", status="PASS")
        return mod.subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--v67-plan-summary",
                str(plan),
                "--v67-postrun-script",
                str(postrun),
                "--campaign-out-dir",
                str(campaign_out),
                "--out-dir",
                str(out_dir),
                "--timeout-seconds",
                "0",
                "--skip-v67-runner-audit",
            ]
        )

    assert status == 0
    summary = json.loads((out_dir / "hfss_v67_material_mesh_to_million_gate_watch_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN"
    assert summary["latest"]["selected_variant"]["name"] == "v67a_tight_mesh_baseline"
    assert summary["latest"]["campaign_summary"]["chunk_count"] == 10


def test_all_v67_final_candidate_failures_block_million_planner(tmp_path):
    mod = _load_module()
    plan = tmp_path / "plan.json"
    variants_root = tmp_path / "variants"
    out_dir = tmp_path / "watch"
    _write_plan(plan, variants_root)
    _write_postrun(variants_root / "v67a_tight_mesh_baseline" / "postrun_validation", status="FAIL", worst=35.0)
    _write_postrun(variants_root / "v67h_skip_pin_fixture_diagnostic" / "postrun_validation", status="FAIL", worst=17.0)
    postrun = tmp_path / "postrun.sh"
    postrun.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append([str(item) for item in command])
        return mod.subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--v67-plan-summary",
                str(plan),
                "--v67-postrun-script",
                str(postrun),
                "--out-dir",
                str(out_dir),
                "--timeout-seconds",
                "0",
                "--skip-v67-runner-audit",
                "--no-fail-exit",
            ]
        )

    assert status == 0
    summary = json.loads((out_dir / "hfss_v67_material_mesh_to_million_gate_watch_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "ALL_V67_FINAL_CANDIDATE_VARIANTS_FAILED_EMX_HFSS_GATE"
    assert len(seen) == 1


def test_diagnostic_only_v67_pass_does_not_unlock_million(tmp_path):
    mod = _load_module()
    plan = tmp_path / "plan.json"
    variants_root = tmp_path / "variants"
    out_dir = tmp_path / "watch"
    _write_plan(plan, variants_root)
    _write_postrun(variants_root / "v67a_tight_mesh_baseline" / "postrun_validation", status="FAIL", worst=35.0)
    _write_postrun(variants_root / "v67h_skip_pin_fixture_diagnostic" / "postrun_validation", status="PASS", worst=2.0)
    postrun = tmp_path / "postrun.sh"
    postrun.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append([str(item) for item in command])
        return mod.subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--v67-plan-summary",
                str(plan),
                "--v67-postrun-script",
                str(postrun),
                "--out-dir",
                str(out_dir),
                "--timeout-seconds",
                "0",
                "--skip-v67-runner-audit",
                "--no-fail-exit",
            ]
        )

    assert status == 0
    summary = json.loads((out_dir / "hfss_v67_material_mesh_to_million_gate_watch_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "ONLY_V67_DIAGNOSTIC_VARIANTS_PASSED_DO_NOT_UNLOCK_MILLION"
    assert summary["latest"]["selected_variant"] == {}
    joined = [" ".join(command) for command in seen]
    assert all("run_gated_s8p_million_sample_campaign.py" not in command for command in joined)


def test_v67_runner_audit_failure_blocks_postrun_and_million_planner(tmp_path):
    mod = _load_module()
    plan = tmp_path / "plan.json"
    variants_root = tmp_path / "variants"
    out_dir = tmp_path / "watch"
    audit_out = tmp_path / "audit"
    postrun = tmp_path / "postrun.sh"
    audit_script = tmp_path / "audit_fail.py"
    _write_plan(plan, variants_root)
    postrun.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    audit_script.write_text(
        "import argparse, json, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--out-dir'); p.add_argument('--no-fail-exit', action='store_true'); p.add_argument('--plan-summary'); ns=p.parse_args()\n"
        "out=pathlib.Path(ns.out_dir); out.mkdir(parents=True, exist_ok=True)\n"
        "(out/'hfss_v67_material_mesh_runner_audit_summary.json').write_text(json.dumps({'overall_status':'FAIL','decision':'FIX_V67_RUNNER_PACKET'}, indent=2), encoding='utf-8')\n"
        "print('overall_status=FAIL')\n",
        encoding="utf-8",
    )

    seen: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        seen.append([str(item) for item in command])
        if "audit_fail.py" in " ".join(str(item) for item in command):
            return real_run(command, **kwargs)
        return mod.subprocess.CompletedProcess(command, 0, stdout="unexpected", stderr="")

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        status = mod.main(
            [
                "--v67-plan-summary",
                str(plan),
                "--v67-postrun-script",
                str(postrun),
                "--v67-runner-audit-script",
                str(audit_script),
                "--v67-runner-audit-out-dir",
                str(audit_out),
                "--out-dir",
                str(out_dir),
                "--timeout-seconds",
                "0",
                "--no-fail-exit",
            ]
        )

    assert status == 0
    summary = json.loads((out_dir / "hfss_v67_material_mesh_to_million_gate_watch_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "V67_RUNNER_AUDIT_FAILED"
    assert summary["latest"]["v67_runner_audit_summary"]["overall_status"] == "FAIL"
    joined = [" ".join(command) for command in seen]
    assert sum("audit_fail.py" in command for command in joined) == 1
    assert all(not (command[0].endswith("bash") and str(postrun) in command) for command in seen)
    assert all("run_gated_s8p_million_sample_campaign.py" not in command for command in joined)
