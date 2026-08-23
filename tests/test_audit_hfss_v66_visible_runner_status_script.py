from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_hfss_v66_visible_runner_status.py"
    spec = importlib.util.spec_from_file_location("audit_hfss_v66_visible_runner_status_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_wrapper(root: Path, *, status: dict | None = None) -> tuple[Path, Path, Path, Path]:
    runner = root / "run_hfss_v66_calibration.windows.ps1"
    wrapper = root / "run_hfss_v66_calibration_visible.windows.ps1"
    cmd_launcher = root / "run_hfss_v66_calibration_visible.windows.cmd"
    status_path = root / "visible_run_status" / "hfss_v66_visible_run_status.json"
    runner.write_text("Write-Host 'runner'\n", encoding="utf-8")
    wrapper.write_text(
        "$Runner='run_hfss_v66_calibration.windows.ps1'\n"
        "Start-Transcript -Path 'x'\n"
        "$Payload | ConvertTo-Json | Set-Content -Path 'hfss_v66_visible_run_status.json'\n"
        "& $Runner\n"
        "$ExpectedVariantCount = 1\n"
        "Write-V66Status -Phase 'completed_hfss_v66_runner_no_exports' -Status 'FAIL'\n",
        encoding="utf-8",
    )
    cmd_launcher.write_text(
        '@echo off\n'
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_hfss_v66_calibration_visible.windows.ps1"\n',
        encoding="utf-8",
    )
    if status is not None:
        status_path.parent.mkdir(parents=True)
        status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return wrapper, runner, status_path, cmd_launcher


def _write_s8p(path: Path, *, points: int = 111, start_ghz: float = 5.0, step_ghz: float = 0.5, ports: int = 8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value_count = 2 * ports * ports
    lines = ["# GHz S RI R 50\n"]
    for index in range(points):
        freq = start_ghz + index * step_ghz
        lines.append(f"{freq:.12g} " + " ".join(["0"] * value_count) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


def test_visible_runner_ready_when_status_json_missing(tmp_path):
    mod = _load_module()
    wrapper, runner, status_path, cmd_launcher = _write_wrapper(tmp_path)

    status = mod.main(
        [
            "--visible-wrapper",
            str(wrapper),
            "--cmd-launcher",
            str(cmd_launcher),
            "--base-runner",
            str(runner),
            "--status-json",
            str(status_path),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v66_visible_runner_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "VISIBLE_HFSS_RUNNER_READY_NOT_YET_RUN"
    assert summary["cmd_launcher"].endswith("run_hfss_v66_calibration_visible.windows.cmd")


def test_visible_runner_running_status_passes(tmp_path):
    mod = _load_module()
    wrapper, runner, status_path, cmd_launcher = _write_wrapper(
        tmp_path,
        status={"phase": "running_hfss_v66_runner", "status": "RUNNING", "exported_s8p_count": 1, "export_manifest_count": 1},
    )

    status = mod.main(
        [
            "--visible-wrapper",
            str(wrapper),
            "--cmd-launcher",
            str(cmd_launcher),
            "--base-runner",
            str(runner),
            "--status-json",
            str(status_path),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v66_visible_runner_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "VISIBLE_HFSS_RUNNER_RUNNING_WAIT_FOR_EXPORTS"
    assert summary["exported_s8p_count"] == 1


def test_visible_runner_failed_status_fails(tmp_path):
    mod = _load_module()
    wrapper, runner, status_path, cmd_launcher = _write_wrapper(
        tmp_path,
        status={"phase": "failed_hfss_v66_runner", "status": "FAIL", "error": "boom"},
    )

    status = mod.main(
        [
            "--visible-wrapper",
            str(wrapper),
            "--cmd-launcher",
            str(cmd_launcher),
            "--base-runner",
            str(runner),
            "--status-json",
            str(status_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v66_visible_runner_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "FIX_VISIBLE_HFSS_RUNNER_OR_FAILED_WINDOWS_RUN"
    failed = [item["name"] for item in summary["checks"] if item["status"] == "FAIL"]
    assert "status not failed" in failed


def test_visible_runner_completed_without_exports_fails(tmp_path):
    mod = _load_module()
    wrapper, runner, status_path, cmd_launcher = _write_wrapper(
        tmp_path,
        status={
            "phase": "completed_hfss_v66_runner",
            "status": "PASS",
            "exported_s8p_count": 0,
            "export_manifest_count": 0,
        },
    )

    status = mod.main(
        [
            "--visible-wrapper",
            str(wrapper),
            "--cmd-launcher",
            str(cmd_launcher),
            "--base-runner",
            str(runner),
            "--status-json",
            str(status_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v66_visible_runner_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "FIX_VISIBLE_HFSS_RUNNER_COMPLETED_WITHOUT_EXPORTS"
    failed = [item["name"] for item in summary["checks"] if item["status"] == "FAIL"]
    assert "completed PASS has exported S8P" in failed
    assert "completed PASS has export manifest" in failed


def test_visible_runner_completed_with_valid_s8p_contract_passes(tmp_path):
    mod = _load_module()
    variant = tmp_path / "variants" / "v66a" / "sample"
    _write_s8p(variant / "hfss_export.s8p")
    (variant / "hfss_s8p_export_manifest.json").write_text("{}", encoding="utf-8")
    wrapper, runner, status_path, cmd_launcher = _write_wrapper(
        tmp_path,
        status={
            "phase": "completed_hfss_v66_runner",
            "status": "PASS",
            "expected_variant_count": 1,
            "exported_s8p_count": 1,
            "export_manifest_count": 1,
        },
    )

    status = mod.main(
        [
            "--visible-wrapper",
            str(wrapper),
            "--cmd-launcher",
            str(cmd_launcher),
            "--base-runner",
            str(runner),
            "--status-json",
            str(status_path),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v66_visible_runner_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "VISIBLE_HFSS_RUNNER_COMPLETED_RUN_POSTRUN_MONITOR"
    assert summary["filesystem_exported_s8p_count"] == 1


def test_visible_runner_completed_with_wrong_s8p_grid_fails(tmp_path):
    mod = _load_module()
    variant = tmp_path / "variants" / "v66a" / "sample"
    _write_s8p(variant / "hfss_export.s8p", points=2, start_ghz=15.0, step_ghz=0.5)
    (variant / "hfss_s8p_export_manifest.json").write_text("{}", encoding="utf-8")
    wrapper, runner, status_path, cmd_launcher = _write_wrapper(
        tmp_path,
        status={
            "phase": "completed_hfss_v66_runner",
            "status": "PASS",
            "expected_variant_count": 1,
            "exported_s8p_count": 1,
            "export_manifest_count": 1,
        },
    )

    status = mod.main(
        [
            "--visible-wrapper",
            str(wrapper),
            "--cmd-launcher",
            str(cmd_launcher),
            "--base-runner",
            str(runner),
            "--status-json",
            str(status_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v66_visible_runner_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "FIX_VISIBLE_HFSS_RUNNER_COMPLETED_WITH_INVALID_EXPORTS"
    failed = [item["name"] for item in summary["checks"] if item["status"] == "FAIL"]
    assert any(name.startswith("Touchstone frequency point count") for name in failed)
    assert any(name.startswith("Touchstone frequency start") for name in failed)
