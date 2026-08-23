from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_hfss_v66_execution_packet.py"
    spec = importlib.util.spec_from_file_location("audit_hfss_v66_execution_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_s8p(path: Path, freqs_ghz: list[float], *, ports: int = 8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pair_count = ports * ports
    with path.open("w", encoding="ascii") as handle:
        handle.write(f"! synthetic {ports}-port file for packet audit\n")
        handle.write("# GHz S RI R 50\n")
        for freq in freqs_ghz:
            values = [f"{freq:.12g}"]
            for _ in range(pair_count):
                values.extend(["0.0", "0.0"])
            handle.write(" ".join(values) + "\n")


def _write_packet(root: Path, module, *, old_grid: bool = False, exported_bad_s8p: bool = False) -> tuple[Path, Path, Path, Path, Path]:
    out = root / "hfss_v66_calibration_plan_current"
    variant_dir = out / "variants" / "v66a_best_marker_reference_bbox" / "sample_a"
    results_dir = variant_dir / "hfss_results"
    postrun_out = variant_dir / "postrun_validation"
    payload = variant_dir / "hfss_s8p_build_payload.json"
    build = variant_dir / "build_hfss_s8p_from_payload.py"
    solve = variant_dir / "solve_export_hfss_s8p.py"
    packet = variant_dir / "hfss_v66_single_variant_packet_summary.json"
    manifest = results_dir / "hfss_export_manifest.json"
    emx_s8p = root / "reference" / "emx_reference.s8p"

    variant_dir.mkdir(parents=True)
    results_dir.mkdir(parents=True)
    postrun_out.mkdir(parents=True)
    _write_s8p(emx_s8p, [5.0, 5.5, 6.0])
    if exported_bad_s8p:
        _write_s8p(variant_dir / "bad_hfss_export.s8p", [5.0, 5.25, 6.0])

    grid = {
        "setup_frequency_ghz": 15.0,
        "start_ghz": 15.0 if old_grid else 5.0,
        "stop_ghz": 15.5 if old_grid else 6.0,
        "step_ghz": 0.5,
        "points": 2 if old_grid else 3,
        "expected_points": 2 if old_grid else 3,
    }
    payload.write_text(
        json.dumps({"frequency_grid": grid, "source_files": {"emx_s8p": str(emx_s8p)}}, indent=2),
        encoding="utf-8",
    )
    build.write_text("print('build')\n", encoding="utf-8")
    solve.write_text("print('solve')\n", encoding="utf-8")
    packet.write_text(
        json.dumps(
            {
                "sample_results": [
                    {
                        "payload_json": str(payload),
                        "build_script": str(build),
                        "solve_script": str(solve),
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    plan = out / "hfss_v66_calibration_plan_summary.json"
    plan.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "RUN_V66_HFSS_DIAGNOSTIC_SWEEP_BEFORE_FULL_VALIDATION",
                "postrun_validation_contract": {
                    "final_acceptance_candidate": True,
                    "compare_start_ghz": 5.0,
                    "compare_stop_ghz": 6.0,
                    "expected_frequency_step_ghz": 0.5,
                    "expected_frequency_points": 3,
                    "expected_ports": 8,
                },
                "variants": [
                    {
                        "name": "v66a_best_marker_reference_bbox",
                        "variant_dir": str(variant_dir),
                        "payload_json": str(payload),
                        "build_script": str(build),
                        "solve_script": str(solve),
                        "single_variant_packet_summary": str(packet),
                        "hfss_results_dir": str(results_dir),
                        "hfss_export_manifest": str(manifest),
                        "postrun_out_dir": str(postrun_out),
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    windows = out / "run_hfss_v66_calibration.windows.ps1"
    windows.write_text(
        "\n".join(
            [
                "# hfss_v66_calibration_plan_current",
                "$env:HFSS_S8P_PAYLOAD='x'",
                "python build_hfss_s8p_from_payload.py",
                "python solve_export_hfss_s8p.py",
                module._windows_path(payload),
                module._windows_path(build),
                module._windows_path(solve),
                module._windows_path(results_dir),
                module._windows_path(manifest),
            ]
        ),
        encoding="utf-8",
    )
    postrun = out / "postrun_validate_hfss_v66_calibration.sh"
    postrun.write_text(
        f"#!/usr/bin/env bash\npython validate.py --compare-start-ghz 5 --compare-stop-ghz 6 --expected-frequency-points 3 {packet} {results_dir}\n",
        encoding="utf-8",
    )
    postrun.chmod(0o755)
    watch = root / "watch" / "hfss_v66_calibration_to_million_gate_watch_summary.json"
    watch.parent.mkdir(parents=True)
    watch.write_text(json.dumps({"overall_status": "WAITING_FOR_HFSS"}, indent=2), encoding="utf-8")
    return plan, windows, postrun, watch, out


def test_packet_audit_passes_while_waiting_for_hfss_exports(tmp_path):
    mod = _load_module()
    plan, windows, postrun, watch, _out = _write_packet(tmp_path, mod)
    audit_out = tmp_path / "audit"

    status = mod.main(
        [
            "--plan-summary",
            str(plan),
            "--windows-runner",
            str(windows),
            "--postrun-script",
            str(postrun),
            "--watch-summary",
            str(watch),
            "--out-dir",
            str(audit_out),
            "--expected-variant-count",
            "1",
            "--expected-frequency-start-ghz",
            "5",
            "--expected-frequency-stop-ghz",
            "6",
            "--expected-frequency-step-ghz",
            "0.5",
            "--expected-frequency-points",
            "3",
        ]
    )

    assert status == 0
    summary = json.loads((audit_out / "hfss_v66_execution_packet_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "HANDOFF_READY_WAITING_FOR_HFSS_EXPORT"
    assert summary["hfss_result_status"] == "WAITING_FOR_HFSS_EXPORT"
    assert summary["variants"][0]["emx_s8p_audit"]["status"] == "PASS"


def test_packet_audit_fails_old_narrow_frequency_grid(tmp_path):
    mod = _load_module()
    plan, windows, postrun, watch, _out = _write_packet(tmp_path, mod, old_grid=True)
    audit_out = tmp_path / "audit"

    status = mod.main(
        [
            "--plan-summary",
            str(plan),
            "--windows-runner",
            str(windows),
            "--postrun-script",
            str(postrun),
            "--watch-summary",
            str(watch),
            "--out-dir",
            str(audit_out),
            "--expected-variant-count",
            "1",
            "--expected-frequency-start-ghz",
            "5",
            "--expected-frequency-stop-ghz",
            "6",
            "--expected-frequency-step-ghz",
            "0.5",
            "--expected-frequency-points",
            "3",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((audit_out / "hfss_v66_execution_packet_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    failed = [check["name"] for check in summary["checks"] if check["status"] == "FAIL"]
    assert "v66a_best_marker_reference_bbox payload start GHz" in failed
    assert "v66a_best_marker_reference_bbox payload not old narrow V65 grid" in failed


def test_packet_audit_fails_bad_exported_hfss_s8p(tmp_path):
    mod = _load_module()
    plan, windows, postrun, watch, _out = _write_packet(tmp_path, mod, exported_bad_s8p=True)
    audit_out = tmp_path / "audit"

    status = mod.main(
        [
            "--plan-summary",
            str(plan),
            "--windows-runner",
            str(windows),
            "--postrun-script",
            str(postrun),
            "--watch-summary",
            str(watch),
            "--out-dir",
            str(audit_out),
            "--expected-variant-count",
            "1",
            "--expected-frequency-start-ghz",
            "5",
            "--expected-frequency-stop-ghz",
            "6",
            "--expected-frequency-step-ghz",
            "0.5",
            "--expected-frequency-points",
            "3",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((audit_out / "hfss_v66_execution_packet_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["hfss_result_status"] == "HFSS_EXPORTS_FOUND_WITH_SPEC_FAILURES"
    failed = [check["name"] for check in summary["checks"] if check["status"] == "FAIL"]
    assert any(name.startswith("v66a_best_marker_reference_bbox exported S8P contract") for name in failed)
