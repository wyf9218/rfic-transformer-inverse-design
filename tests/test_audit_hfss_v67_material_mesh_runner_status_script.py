from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_hfss_v67_material_mesh_runner_status.py"
    spec = importlib.util.spec_from_file_location("audit_hfss_v67_material_mesh_runner_status_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_packet(root: Path, *, status: dict | None = None, variant_count: int = 2) -> tuple[Path, Path, Path, Path]:
    plan = root / "hfss_v67_material_mesh_calibration_plan_summary.json"
    runner = root / "run_hfss_v67_material_mesh_calibration_resilient.windows.ps1"
    cmd_launcher = root / "run_hfss_v67_material_mesh_calibration_resilient.windows.cmd"
    status_path = root / "resilient_run_status" / "hfss_v67_resilient_run_status.json"
    variants = [
        {
            "name": "v67a_tight_mesh_baseline",
            "diagnostic_only": False,
            "final_acceptance_candidate": True,
        },
        {
            "name": "v67h_skip_pin_fixture_diagnostic",
            "diagnostic_only": True,
            "final_acceptance_candidate": False,
        },
    ][:variant_count]
    plan.write_text(
        json.dumps({"overall_status": "PASS", "variant_count": len(variants), "variants": variants}, indent=2),
        encoding="utf-8",
    )
    runner.write_text(
        "Start-Transcript -Path 'x'\n"
        "$Payload | ConvertTo-Json | Set-Content -Path 'hfss_v67_resilient_run_status.json'\n"
        "function Run-V67Variant {}\n"
        + "".join(["$VariantResults += Run-V67Variant `\n" for _ in range(len(variants))])
        + "throw 'Variant completed but did not produce both .s8p and export manifest.'\n",
        encoding="utf-8",
    )
    cmd_launcher.write_text(
        "@echo off\n"
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_hfss_v67_material_mesh_calibration_resilient.windows.ps1"\n',
        encoding="utf-8",
    )
    if status is not None:
        status_path.parent.mkdir(parents=True)
        status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return plan, runner, cmd_launcher, status_path


def _write_s8p(path: Path, *, points: int = 111, start_ghz: float = 5.0, step_ghz: float = 0.5, ports: int = 8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value_count = 2 * ports * ports
    lines = ["# GHz S RI R 50\n"]
    for index in range(points):
        freq = start_ghz + index * step_ghz
        lines.append(f"{freq:.12g} " + " ".join(["0"] * value_count) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


def test_v67_runner_ready_when_status_json_missing(tmp_path):
    mod = _load_module()
    plan, runner, cmd_launcher, status_path = _write_packet(tmp_path)

    status = mod.main(
        [
            "--plan-summary",
            str(plan),
            "--runner",
            str(runner),
            "--cmd-launcher",
            str(cmd_launcher),
            "--status-json",
            str(status_path),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "hfss_v67_material_mesh_runner_audit_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "V67_RUNNER_READY_NOT_YET_RUN"
    assert summary["expected_variant_count"] == 2


def test_v67_runner_completed_with_final_candidate_valid_s8p_contract_passes(tmp_path):
    mod = _load_module()
    variant = tmp_path / "variants" / "v67a_tight_mesh_baseline" / "sample"
    _write_s8p(variant / "hfss_export.s8p")
    (variant / "hfss_s8p_export_manifest.json").write_text("{}", encoding="utf-8")
    plan, runner, cmd_launcher, status_path = _write_packet(
        tmp_path,
        status={
            "overall_status": "PARTIAL_OR_FULL_EXPORTS_READY_FOR_POSTRUN",
            "pass_count": 1,
            "variants": [{"name": "v67a_tight_mesh_baseline", "status": "PASS"}],
        },
    )

    status = mod.main(
        [
            "--plan-summary",
            str(plan),
            "--runner",
            str(runner),
            "--cmd-launcher",
            str(cmd_launcher),
            "--status-json",
            str(status_path),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "hfss_v67_material_mesh_runner_audit_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "V67_RUNNER_COMPLETED_RUN_POSTRUN_VALIDATION"
    assert summary["final_candidate_pass_count"] == 1


def test_v67_runner_completed_with_wrong_s8p_grid_fails(tmp_path):
    mod = _load_module()
    variant = tmp_path / "variants" / "v67a_tight_mesh_baseline" / "sample"
    _write_s8p(variant / "hfss_export.s8p", points=2, start_ghz=15.0, step_ghz=0.5)
    (variant / "hfss_s8p_export_manifest.json").write_text("{}", encoding="utf-8")
    plan, runner, cmd_launcher, status_path = _write_packet(
        tmp_path,
        status={
            "overall_status": "PARTIAL_OR_FULL_EXPORTS_READY_FOR_POSTRUN",
            "pass_count": 1,
            "variants": [{"name": "v67a_tight_mesh_baseline", "status": "PASS"}],
        },
    )

    status = mod.main(
        [
            "--plan-summary",
            str(plan),
            "--runner",
            str(runner),
            "--cmd-launcher",
            str(cmd_launcher),
            "--status-json",
            str(status_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "hfss_v67_material_mesh_runner_audit_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "FIX_V67_RUNNER_COMPLETED_WITH_INVALID_EXPORTS"
    failed = [item["name"] for item in summary["checks"] if item["status"] == "FAIL"]
    assert "Touchstone frequency point count: hfss_export.s8p" in failed


def test_v67_runner_completed_diagnostic_only_is_not_final(tmp_path):
    mod = _load_module()
    variant = tmp_path / "variants" / "v67h_skip_pin_fixture_diagnostic" / "sample"
    _write_s8p(variant / "hfss_export.s8p")
    (variant / "hfss_s8p_export_manifest.json").write_text("{}", encoding="utf-8")
    plan, runner, cmd_launcher, status_path = _write_packet(
        tmp_path,
        status={
            "overall_status": "PARTIAL_OR_FULL_EXPORTS_READY_FOR_POSTRUN",
            "pass_count": 1,
            "variants": [{"name": "v67h_skip_pin_fixture_diagnostic", "status": "PASS"}],
        },
    )

    status = mod.main(
        [
            "--plan-summary",
            str(plan),
            "--runner",
            str(runner),
            "--cmd-launcher",
            str(cmd_launcher),
            "--status-json",
            str(status_path),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "hfss_v67_material_mesh_runner_audit_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "V67_RUNNER_COMPLETED_DIAGNOSTIC_ONLY_NOT_FINAL"
    assert summary["final_candidate_pass_count"] == 0
    assert summary["diagnostic_only_pass_count"] == 1
