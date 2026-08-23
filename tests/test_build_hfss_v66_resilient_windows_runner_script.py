from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_hfss_v66_resilient_windows_runner.py"
    spec = importlib.util.spec_from_file_location("build_hfss_v66_resilient_windows_runner_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_variant_files(root: Path, name: str) -> dict:
    variant_dir = root / "variants" / name / "sample"
    variant_dir.mkdir(parents=True, exist_ok=True)
    payload = variant_dir / "hfss_s8p_build_payload.json"
    build = variant_dir / "build_hfss_s8p_from_payload.py"
    solve = variant_dir / "solve_export_hfss_s8p.py"
    payload.write_text("{}", encoding="utf-8")
    build.write_text("print('build')\n", encoding="utf-8")
    solve.write_text("print('solve')\n", encoding="utf-8")
    return {
        "name": name,
        "variant_dir": str(variant_dir),
        "payload_json": str(payload),
        "hfss_save_path": str(variant_dir / f"{name}.aedt"),
        "hfss_solve_project": str(variant_dir / f"{name}_solve.aedt"),
        "hfss_results_dir": str(variant_dir / "hfss_solve_export_results"),
        "hfss_build_log": str(variant_dir / "hfss_s8p_build.log"),
        "hfss_port_manifest": str(variant_dir / "hfss_s8p_build_port_manifest.json"),
        "hfss_export_manifest": str(variant_dir / "hfss_s8p_export_manifest.json"),
        "build_script": str(build),
        "solve_script": str(solve),
        "env": {
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_PORT_DEEMBED": "0",
        },
    }


def _write_plan(path: Path, variants: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"overall_status": "PASS", "variants": variants}, indent=2),
        encoding="utf-8",
    )


def test_builds_resilient_runner_with_per_variant_try_catch(tmp_path):
    mod = _load_module()
    plan = tmp_path / "plan.json"
    _write_plan(plan, [_write_variant_files(tmp_path, "v66a"), _write_variant_files(tmp_path, "v66b")])

    status = mod.main(["--plan-summary", str(plan), "--out-dir", str(tmp_path / "out")])

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v66_resilient_runner_packet_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    windows = (tmp_path / "out" / "run_hfss_v66_calibration_resilient.windows.ps1").read_text(encoding="utf-8")
    assert "function Run-V66Variant" in windows
    assert "catch {" in windows
    assert windows.count("$VariantResults += Run-V66Variant") == 2
    assert "Run-V66Variant `" in windows
    assert "HFSS_RESILIENT_RUNNER_PARTIAL_EXPORTS_RUN_POSTRUN_GATE" in windows
    assert "Variant completed but did not produce both .s8p and export manifest." in windows
    assert (tmp_path / "out" / "run_hfss_v66_calibration_resilient.windows.cmd").is_file()


def test_resilient_runner_packet_fails_when_variant_script_missing(tmp_path):
    mod = _load_module()
    variant = _write_variant_files(tmp_path, "v66a")
    Path(variant["solve_script"]).unlink()
    plan = tmp_path / "plan.json"
    _write_plan(plan, [variant])

    status = mod.main(["--plan-summary", str(plan), "--out-dir", str(tmp_path / "out"), "--no-fail-exit"])

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v66_resilient_runner_packet_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    failed = [item["name"] for item in summary["checks"] if item["status"] == "FAIL"]
    assert "v66a solve_script exists" in failed
    assert not (tmp_path / "out" / "run_hfss_v66_calibration_resilient.windows.ps1").exists()
