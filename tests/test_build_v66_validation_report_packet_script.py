from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_v66_validation_report_packet.py"
    spec = importlib.util.spec_from_file_location("build_v66_validation_report_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _write_file(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ok\n", encoding="utf-8")
    return str(path)


def _base_inputs(root: Path, *, postrun: dict) -> tuple[Path, Path, Path, Path, Path, Path]:
    postrun_path = _write_json(root / "postrun.json", postrun)
    geometry_path = _write_json(
        root / "geometry.json",
        {
            "overall_status": "PASS",
            "physical_model_inputs": ["lp_nh_center", "ls_nh_center", "q_center", "k_center"],
            "geometry_contract": {"geometry_columns": ["geom__w", "geom__s"]},
        },
    )
    visible_path = _write_json(
        root / "visible.json",
        {"overall_status": "PASS", "decision": "VISIBLE_HFSS_RUNNER_READY_NOT_YET_RUN"},
    )
    resilient_path = _write_json(
        root / "resilient.json",
        {"overall_status": "PASS", "decision": "RESILIENT_HFSS_RUNNER_READY_NOT_YET_RUN"},
    )
    historical_path = _write_json(
        root / "historical_recompare.json",
        {
            "candidate_count": 22,
            "pass_count": 0,
            "best": {"worst_percent_error": 1476.88},
            "target15_best": {
                "target15_worst_percent_error": 40.54,
                "target15_core_percent_errors": {"lp_nh": 35.0, "ls_nh": 40.54, "q": 17.33, "k": 1.49},
            },
        },
    )
    million_path = _write_json(
        root / "million.json",
        {"overall_status": "FAIL", "decision": "DO_NOT_EXECUTE_MILLION_CAMPAIGN_PLAN_NOT_READY"},
    )
    return postrun_path, geometry_path, visible_path, resilient_path, historical_path, million_path


def _run_packet(mod, root: Path, inputs: tuple[Path, Path, Path, Path, Path, Path]) -> int:
    postrun_path, geometry_path, visible_path, resilient_path, historical_path, million_path = inputs
    return mod.main(
        [
            "--postrun-summary",
            str(postrun_path),
            "--geometry-summary",
            str(geometry_path),
            "--visible-runner-summary",
            str(visible_path),
            "--resilient-runner-summary",
            str(resilient_path),
            "--historical-recompare-summary",
            str(historical_path),
            "--million-execution-summary",
            str(million_path),
            "--out-dir",
            str(root / "out"),
            "--no-fail-exit",
        ]
    )


def test_report_packet_waits_without_selected_hfss_variant(tmp_path):
    mod = _load_module()
    inputs = _base_inputs(
        tmp_path,
        postrun={
            "overall_status": "WAITING_FOR_HFSS",
            "decision": "WAIT_FOR_V66_EXPORTED_HFSS_S8P",
            "selected_variant": {},
            "acceptance_gate": {"metrics": ["lp_nh", "ls_nh", "q", "k", "kw"], "target_marker_ghz": 15.0, "max_percent_error": 10.0},
        },
    )

    status = _run_packet(mod, tmp_path, inputs)

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_validation_report_packet_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_HFSS"
    assert summary["decision"] == "WAIT_FOR_HFSS_S8P_BEFORE_REPORTING_PASS"
    assert summary["historical_recompare_candidate_count"] == 22
    assert summary["historical_recompare_pass_count"] == 0
    assert summary["historical_recompare_best_target15_worst_percent_error"] == 40.54
    assert summary["artifact_rows"]
    assert all(row["exists"] == "MISSING" for row in summary["artifact_rows"])
    report = (tmp_path / "out" / "V66_VALIDATION_REPORT_PACKET_CN.md").read_text(encoding="utf-8")
    assert "No selected passing V66 variant yet" in report


def test_report_packet_passes_with_selected_variant_artifacts(tmp_path):
    mod = _load_module()
    artifacts = {
        "emx_s8p": _write_file(tmp_path / "artifacts" / "emx.s8p"),
        "hfss_s8p": _write_file(tmp_path / "artifacts" / "hfss.s8p"),
        "target_marker_csv": _write_file(tmp_path / "artifacts" / "marker.csv"),
        "compare_summary": _write_file(tmp_path / "artifacts" / "compare.json"),
        "ads_style_plot_summary": _write_file(tmp_path / "artifacts" / "plot_summary.json"),
        "emx_plot": _write_file(tmp_path / "artifacts" / "emx.png"),
        "hfss_plot": _write_file(tmp_path / "artifacts" / "hfss.png"),
        "overlay_plot": _write_file(tmp_path / "artifacts" / "overlay.png"),
        "percent_error_plot": "",
        "metric_csv": _write_file(tmp_path / "artifacts" / "metrics.csv"),
    }
    inputs = _base_inputs(
        tmp_path,
        postrun={
            "overall_status": "PASS",
            "decision": "V66_EMX_HFSS_GATE_EVIDENCE_READY",
            "selected_variant": {
                "name": "v66b",
                "worst_metric": "kw",
                "worst_percent_error": 2.5,
                "artifacts": artifacts,
            },
            "acceptance_gate": {"metrics": ["lp_nh", "ls_nh", "q", "k", "kw"], "target_marker_ghz": 15.0, "max_percent_error": 10.0},
        },
    )

    status = _run_packet(mod, tmp_path, inputs)

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_validation_report_packet_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "READY_FOR_PROFESSOR_REPORT_AND_MILLION_GATE"
    assert summary["historical_recompare_pass_count"] == 0
    required_rows = [row for row in summary["artifact_rows"] if row["required"] == "yes"]
    assert required_rows
    assert all(row["exists"] == "PASS" for row in required_rows)
    assert (tmp_path / "out" / "v66_validation_report_artifacts.csv").is_file()


def test_report_packet_fails_when_selected_variant_artifact_is_missing(tmp_path):
    mod = _load_module()
    artifacts = {
        "emx_s8p": _write_file(tmp_path / "artifacts" / "emx.s8p"),
        "hfss_s8p": "",
        "target_marker_csv": _write_file(tmp_path / "artifacts" / "marker.csv"),
        "compare_summary": _write_file(tmp_path / "artifacts" / "compare.json"),
        "ads_style_plot_summary": _write_file(tmp_path / "artifacts" / "plot_summary.json"),
        "emx_plot": _write_file(tmp_path / "artifacts" / "emx.png"),
        "hfss_plot": _write_file(tmp_path / "artifacts" / "hfss.png"),
        "overlay_plot": _write_file(tmp_path / "artifacts" / "overlay.png"),
        "metric_csv": _write_file(tmp_path / "artifacts" / "metrics.csv"),
    }
    inputs = _base_inputs(
        tmp_path,
        postrun={
            "overall_status": "PASS",
            "decision": "V66_EMX_HFSS_GATE_EVIDENCE_READY",
            "selected_variant": {"name": "v66b", "artifacts": artifacts},
        },
    )

    status = _run_packet(mod, tmp_path, inputs)

    assert status == 0
    summary = json.loads((tmp_path / "out" / "v66_validation_report_packet_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "GATE_EVIDENCE_INCOMPLETE"
    assert any(row["key"] == "hfss_s8p" and row["exists"] == "MISSING" for row in summary["artifact_rows"])
