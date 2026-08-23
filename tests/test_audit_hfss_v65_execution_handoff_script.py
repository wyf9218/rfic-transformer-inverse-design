from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_hfss_v65_execution_handoff.py"
    spec = importlib.util.spec_from_file_location("audit_hfss_v65_execution_handoff_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_valid_diagnostic_s8p(path: Path) -> None:
    freqs_hz = np.asarray([15.0e9, 15.5e9], dtype=float)
    s_matrix = np.repeat(np.eye(8, dtype=np.complex128)[None, :, :] * 0.02, len(freqs_hz), axis=0)
    _write_touchstone(path, freqs_hz, s_matrix)


def _write_handoff(
    root: Path,
    *,
    include_postrun_variant_dir: bool = True,
    exported_s8p: bool = False,
    malformed_s8p: bool = False,
) -> tuple[Path, Path, Path, Path]:
    variant = "v65a"
    sample = "sample_a"
    sample_dir = root / "scripts" / sample
    sample_dir.mkdir(parents=True)
    build = sample_dir / "build_hfss_s8p_from_payload.py"
    solve = sample_dir / "solve_export_hfss_s8p.py"
    build.write_text("print('build')\n", encoding="utf-8")
    solve.write_text("print('solve')\n", encoding="utf-8")
    variant_dir = root / "variants" / variant
    work_dir = variant_dir / sample
    results_dir = work_dir / "hfss_solve_export_results"
    results_dir.mkdir(parents=True)
    if exported_s8p:
        export_path = results_dir / "sample_hfss_export.s8p"
        if malformed_s8p:
            export_path.write_text("! fake\n", encoding="ascii")
        else:
            _write_valid_diagnostic_s8p(export_path)
    export_manifest = work_dir / "hfss_s8p_export_manifest.json"
    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "READY_TO_RUN_HFSS_LP_LS_DIAGNOSTIC_SWEEP",
                "diagnostic_frequency_grid": {"start_ghz": 15.0, "stop_ghz": 15.5, "step_ghz": 0.5, "points": 2},
                "variants": [
                    {
                        "name": variant,
                        "variant_dir": str(variant_dir),
                        "postrun_out_dir": str(variant_dir / "postrun_validation"),
                        "windows_steps": [
                            {
                                "evaluation": sample,
                                "build_script": str(build),
                                "solve_script": str(solve),
                                "hfss_results_dir": str(results_dir),
                                "hfss_export_manifest": str(export_manifest),
                            }
                        ],
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    win_results = "\\\\Mac\\Home\\" + str(results_dir).split("/home/researcher/", 1)[-1].replace("/", "\\")
    win_export = "\\\\Mac\\Home\\" + str(export_manifest).split("/home/researcher/", 1)[-1].replace("/", "\\")
    windows = root / "run.windows.ps1"
    windows.write_text(
        "\n".join(
            [
                f"Write-Host '== {variant} =='",
                f"$env:HFSS_SOLVE_RESULTS_DIR = '{win_results}'",
                f"$env:HFSS_EXPORT_MANIFEST = '{win_export}'",
                "solve_export_hfss_s8p.py",
            ]
        ),
        encoding="utf-8",
    )
    postrun = root / "postrun.sh"
    postrun_dir_text = str(variant_dir) if include_postrun_variant_dir else str(root / "other")
    postrun.write_text(
        f"#!/usr/bin/env bash\nrun --hfss-results-dir '{postrun_dir_text}' --expected-frequency-points 2\n",
        encoding="utf-8",
    )
    postrun.chmod(0o755)
    watch = root / "watch.json"
    watch.write_text(json.dumps({"overall_status": "WAITING_FOR_DIAGNOSTIC_HFSS"}), encoding="utf-8")
    return plan, windows, postrun, watch


def test_handoff_ready_waiting_for_hfss_export(tmp_path):
    module = _load_module()
    plan, windows, postrun, watch = _write_handoff(tmp_path)

    status = module.main(
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
            str(tmp_path / "out"),
            "--expected-variant-count",
            "1",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v65_execution_handoff_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "HANDOFF_READY_WAITING_FOR_HFSS_EXPORT"
    assert summary["hfss_result_status"] == "WAITING_FOR_HFSS_EXPORT"
    assert summary["exported_s8p_count"] == 0


def test_fails_when_postrun_does_not_scan_variant_dir(tmp_path):
    module = _load_module()
    plan, windows, postrun, watch = _write_handoff(tmp_path, include_postrun_variant_dir=False)

    status = module.main(
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
            str(tmp_path / "out"),
            "--expected-variant-count",
            "1",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v65_execution_handoff_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    failed = [check["name"] for check in summary["checks"] if check["status"] == "FAIL"]
    assert "v65a postrun scans variant dir" in failed


def test_reports_existing_hfss_s8p_exports(tmp_path):
    module = _load_module()
    plan, windows, postrun, watch = _write_handoff(tmp_path, exported_s8p=True)

    status = module.main(
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
            str(tmp_path / "out"),
            "--expected-variant-count",
            "1",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v65_execution_handoff_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["hfss_result_status"] == "HFSS_EXPORTS_FOUND_RUN_POSTRUN"
    assert summary["exported_s8p_count"] == 1
    assert summary["exported_s8p_audit_count"] == 1
    assert summary["variants"][0]["exported_s8p_audits"][0]["status"] == "PASS"
    checks = {(item["name"], item["status"]) for item in summary["checks"]}
    assert ("v65a exported S8P has 8 ports", "PASS") in checks
    assert ("v65a exported S8P has expected frequency point count", "PASS") in checks
    assert ("v65a exported S8P starts at planned frequency", "PASS") in checks
    assert ("v65a exported S8P stops at planned frequency", "PASS") in checks
    assert ("v65a exported S8P uses planned frequency step", "PASS") in checks


def test_fails_when_existing_hfss_s8p_is_malformed(tmp_path):
    module = _load_module()
    plan, windows, postrun, watch = _write_handoff(tmp_path, exported_s8p=True, malformed_s8p=True)

    status = module.main(
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
            str(tmp_path / "out"),
            "--expected-variant-count",
            "1",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "hfss_v65_execution_handoff_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["hfss_result_status"] == "HFSS_EXPORTS_FOUND_WITH_SPEC_FAILURES"
    assert summary["decision"] == "FIX_HFSS_EXPORTED_S8P_SPEC_BEFORE_POSTRUN"
    assert summary["variants"][0]["exported_s8p_audits"][0]["status"] == "FAIL"
    failed = [check["name"] for check in summary["checks"] if check["status"] == "FAIL"]
    assert "v65a exported S8P parses as Touchstone" in failed
