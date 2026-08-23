from tests.rfic_transformer_inverse_design.shared import *

import base64
import importlib.util
import sys


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_s8p_final_report_evidence_packet.py"
    spec = importlib.util.spec_from_file_location("build_s8p_final_report_evidence_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_1X1)
    return path


def _write_text(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_complete_fixture(root: Path, *, bad_metric: bool = False) -> Path:
    quality = root / "quality"
    scalar_q = quality / "scalar_q_feature_dataset"
    scalar_rows = _write_text(
        scalar_q / "dataset_rows.csv",
        "evaluation,lp_nh_center,ls_nh_center,qp_center,qs_center,q_center,k_center\n"
        "eval001,1,1.2,10,11,10,0.5\n",
    )
    scalar_manifest = _write_text(scalar_q / "dataset_manifest.json", '{"scalar_q_feature":{"definition":"min"}}\n')
    scalar_report = _write_text(scalar_q / "scalar_q_feature_report.md", "scalar q report\n")
    _write_json(
        scalar_q / "scalar_q_feature_summary.json",
        {
            "overall_status": "PASS",
            "definition": "min",
            "output_column": "q_center",
            "valid_q_count": 12,
            "fail_count": 0,
            "output_rows_csv": str(scalar_rows),
            "output_manifest": str(scalar_manifest),
            "report": str(scalar_report),
            "arguments": {"q_definition": "min", "output_column": "q_center"},
        },
    )
    coverage = quality / "physical_feature_balanced_acquisition_plan"
    coverage_figures = {
        "marginal_histograms": str(_write_png(coverage / "physical_feature_marginal_histograms.png")),
        "pairwise_scatter": str(_write_png(coverage / "physical_feature_pairwise_scatter.png")),
        "bin_coverage_heatmap": str(_write_png(coverage / "physical_feature_bin_coverage_heatmap.png")),
    }
    _write_json(
        coverage / "physical_feature_acquisition_plan_summary.json",
        {"overall_status": "PASS", "visual_evidence": {"status": "PASS", "figures": coverage_figures}},
    )

    layout_dir = root / "eval001" / "layout"
    layout_json = _write_text(layout_dir / "transformer_layout.layout.json", "{}\n")
    power_json = _write_text(layout_dir / "power_line_8port_geometry.json", "{}\n")
    _write_png(layout_dir / "transformer_layout_preview.png")
    _write_png(layout_dir / "transformer_port_debug.png")
    _write_json(
        quality / "selected_power_line_8port_layout_audit" / "selected_power_line_8port_layout_audit_summary.json",
        {
            "overall_status": "PASS",
            "sample_results": [
                {
                    "overall_status": "PASS",
                    "evaluation": "eval001",
                    "layout_json_path": str(layout_json),
                    "power_line_8port_geometry_json_path": str(power_json),
                }
            ],
        },
    )

    inverse_contract = {
        "zin_columns": [],
        "lp_columns": ["input__lp_nh_center"],
        "ls_columns": ["input__ls_nh_center"],
        "q_columns": ["input__q_center"],
        "k_columns": ["input__k_center"],
    }
    inverse_table_dir = quality / "physical_feature_inverse_training_table"
    inverse_training_csv = _write_text(
        inverse_table_dir / "physical_feature_inverse_training_table.csv",
        "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_center,geom__w_um\n1,1,10,0.5,5\n",
    )
    _write_text(inverse_table_dir / "physical_feature_inverse_training_report.md", "inverse training report\n")
    _write_json(
        inverse_table_dir / "physical_feature_inverse_training_manifest.json",
        {
            "overall_status": "PASS",
            "training_csv": str(inverse_training_csv),
            "input_feature_contract": inverse_contract,
            "training_count": 12,
        },
    )

    inverse_quality_dir = quality / "physical_feature_inverse_model_quality"
    inverse_cv = _write_text(inverse_quality_dir / "physical_feature_inverse_model_cv_predictions.csv", "row,pred\n0,1\n")
    inverse_errors = _write_text(inverse_quality_dir / "physical_feature_inverse_model_geometry_errors.csv", "geom,mae\nw,0.1\n")
    inverse_quality_report = _write_text(inverse_quality_dir / "physical_feature_inverse_model_quality_report.md", "inverse quality report\n")
    _write_json(
        inverse_quality_dir / "physical_feature_inverse_model_quality_summary.json",
        {
            "overall_status": "PASS",
            "report": str(inverse_quality_report),
            "cv_predictions_csv": str(inverse_cv),
            "geometry_errors_csv": str(inverse_errors),
            "input_feature_contract": inverse_contract,
            "training_count": 12,
            "quality_summary": {"per_geometry": {"geom__w_um": {"normalized_mae": 0.1}}},
        },
    )

    saved_model_dir = quality / "physical_feature_saved_inverse_model"
    saved_model_json = _write_text(saved_model_dir / "physical_feature_inverse_model.json", '{"method":"standardized_polynomial_ridge_regression"}\n')
    saved_cv = _write_text(saved_model_dir / "physical_feature_inverse_model_training_cv_predictions.csv", "row,pred\n0,1\n")
    saved_errors = _write_text(saved_model_dir / "physical_feature_inverse_model_training_geometry_errors.csv", "geom,mae\nw,0.1\n")
    saved_targets = _write_text(saved_model_dir / "physical_feature_inverse_model_target_predictions.csv", "candidate_id,geom__w_um\nsaved_inverse_target_000_candidate_001,5\n")
    saved_report = _write_text(saved_model_dir / "physical_feature_inverse_model_training_report.md", "saved model report\n")
    _write_json(
        saved_model_dir / "physical_feature_inverse_model_training_summary.json",
        {
            "overall_status": "PASS",
            "method": "standardized_polynomial_ridge_regression",
            "model_json": str(saved_model_json),
            "report": str(saved_report),
            "cv_predictions_csv": str(saved_cv),
            "geometry_errors_csv": str(saved_errors),
            "target_predictions_csv": str(saved_targets),
            "input_feature_contract": inverse_contract,
            "target_count": 1,
            "target_prediction_count": 1,
            "training_count": 12,
            "quality_summary": {"per_geometry": {"geom__w_um": {"normalized_mae": 0.1}}},
        },
    )

    target_smoke = quality / "physical_feature_saved_inverse_target_layout_smoke"
    target_eval_layout = target_smoke / "evaluations" / "saved_inverse_target_000_candidate_001" / "layout"
    _write_text(target_eval_layout / "transformer_layout.layout.json", "{}\n")
    _write_text(target_eval_layout / "power_line_8port_geometry.json", "{}\n")
    _write_png(target_eval_layout / "transformer_layout_preview.png")
    _write_png(target_eval_layout / "transformer_port_debug.png")
    target_rows = _write_text(
        target_smoke / "dataset_rows.csv",
        f"evaluation,ok,work_dir\nsaved_inverse_target_000_candidate_001,True,{target_eval_layout.parent}\n",
    )
    target_manifest = _write_text(target_smoke / "dataset_manifest.json", "{}\n")
    _write_json(
        target_smoke / "candidate_queue_dataset_summary.json",
        {
            "overall_status": "PASS",
            "dataset_rows_csv": str(target_rows),
            "dataset_manifest": str(target_manifest),
            "create_only": True,
            "ok_count": 1,
        },
    )

    aedt_dir = quality / "selected_s8p_hfss_aedt_scripts"
    sample_script_dir = aedt_dir / "samples" / "01_eval001"
    _write_text(aedt_dir / "run_generated_hfss_s8p_scripts.commands.ps1", "python build_hfss_s8p_from_payload.py\n")
    _write_text(sample_script_dir / "hfss_s8p_build_payload.json", "{}\n")
    _write_text(sample_script_dir / "build_hfss_s8p_from_payload.py", "print('build')\n")
    _write_text(sample_script_dir / "solve_export_hfss_s8p.py", "print('solve')\n")
    _write_text(sample_script_dir / "source_geometry.gds", "gds\n")
    _write_text(sample_script_dir / "hfss_s8p_script_packet_README.md", "readme\n")
    _write_json(
        aedt_dir / "hfss_s8p_aedt_script_packet_summary.json",
        {
            "overall_status": "PASS",
            "sample_results": [
                {
                    "overall_status": "PASS",
                    "evaluation": "eval001",
                    "script_dir": str(sample_script_dir),
                    "payload_json": str(sample_script_dir / "hfss_s8p_build_payload.json"),
                    "build_script": str(sample_script_dir / "build_hfss_s8p_from_payload.py"),
                    "solve_script": str(sample_script_dir / "solve_export_hfss_s8p.py"),
                    "sample_report": str(sample_script_dir / "hfss_s8p_script_packet_README.md"),
                }
            ],
        },
    )

    hfss_render_dir = quality / "selected_s8p_hfss_payload_views" / "01_eval001"
    hfss_image = _write_png(hfss_render_dir / "hfss_payload_top_view.png")
    render_summary = hfss_render_dir / "hfss_payload_geometry_render_summary.json"
    _write_json(render_summary, {"overall_status": "PASS", "sample_id": "eval001", "image_paths": [str(hfss_image)]})
    _write_json(
        quality / "selected_s8p_hfss_payload_views" / "hfss_payload_geometry_render_batch_summary.json",
        {"overall_status": "PASS", "rendered_count": 1, "summary_paths": [str(render_summary)]},
    )

    postrun = quality / "selected_s8p_hfss_postrun_validation"
    compare_dir = postrun / "samples" / "01_eval001" / "emx_vs_hfss_compare"
    plot_dir = postrun / "samples" / "01_eval001" / "ads_style_metric_plots"
    emx_s8p = _write_text(postrun / "samples" / "01_eval001" / "eval001_emx.s8p", "! emx\n")
    hfss_s8p = _write_text(postrun / "samples" / "01_eval001" / "eval001_hfss.s8p", "! hfss\n")
    hfss_port_manifest = _write_text(postrun / "samples" / "01_eval001" / "hfss_s8p_build_port_manifest.json", "{}\n")
    emx_audit = compare_dir.parent / "emx_touchstone_audit" / "touchstone_transformer_audit_summary.json"
    hfss_audit = compare_dir.parent / "hfss_touchstone_audit" / "touchstone_transformer_audit_summary.json"
    _write_json(emx_audit, {"overall_status": "PASS"})
    _write_json(hfss_audit, {"overall_status": "PASS"})
    for filename in ("emx_hfss_ads_curves.csv", "emx_hfss_ads_metric_errors.csv", "emx_hfss_ads_target_marker_metrics.csv"):
        _write_text(compare_dir / filename, "metric,value\nlp_nh,1\n")
    for metric in ("lp_nh", "ls_nh", "q", "k", "kw"):
        _write_png(compare_dir / f"{metric}_comparison.png")
    metric_error = 8.0 if bad_metric else 1.0
    metric_status = "FAIL" if bad_metric else "PASS"
    metrics = {
        metric: {"status": metric_status if metric == "k" and bad_metric else "PASS", "max_percent_error": metric_error if metric == "k" else 1.0}
        for metric in ("lp_nh", "ls_nh", "q", "k", "kw", "qp", "qs")
    }
    marker_metrics = {
        metric: {"status": metric_status if metric == "k" and bad_metric else "PASS", "percent_error": metric_error if metric == "k" else 1.0}
        for metric in ("lp_nh", "ls_nh", "q", "k", "kw")
    }
    compare_summary = compare_dir / "emx_hfss_ads_comparison_summary.json"
    _write_json(
        compare_summary,
        {
            "overall_status": "FAIL" if bad_metric else "PASS",
            "metrics": metrics,
            "target_marker": {"status": "FAIL" if bad_metric else "PASS", "nearest_frequency_ghz": 15.0, "metrics": marker_metrics},
        },
    )
    plot_artifacts = {
        "metric_csv": str(_write_text(plot_dir / "ads_style_metric_curves.csv", "freq_ghz,emx_lp_nh\n15,1\n")),
        "emx_common_plot": str(_write_png(plot_dir / "emx_ads_style_metrics_common_5_50GHz.png")),
        "hfss_common_plot": str(_write_png(plot_dir / "hfss_ads_style_metrics_common_5_50GHz.png")),
        "overlay_common_plot": str(_write_png(plot_dir / "emx_vs_hfss_ads_style_overlay_common_5_50GHz.png")),
    }
    plot_summary = plot_dir / "ads_style_metric_plot_summary.json"
    _write_json(
        plot_summary,
        {
            "overall_status": "PASS",
            "artifact_paths": plot_artifacts,
            "window_named_artifact_paths": {},
            "metric_max_percent_errors_common_window": {"lp_nh": 1.0, "ls_nh": 1.0, "q": 1.0, "k": metric_error, "kw": metric_error},
            "emx_n_ports": 8,
            "hfss_n_ports": 8,
        },
    )
    _write_json(
        postrun / "s8p_hfss_postrun_validation_summary.json",
        {
            "overall_status": "FAIL" if bad_metric else "PASS",
            "checks": _passing_postrun_manifest_checks(),
            "records": [
                {
                    "evaluation": "eval001",
                    "status": "FAIL" if bad_metric else "PASS",
                    "emx_s8p": str(emx_s8p),
                    "hfss_s8p": str(hfss_s8p),
                    "hfss_port_manifest": str(hfss_port_manifest),
                    "emx_audit_summary": str(emx_audit),
                    "hfss_audit_summary": str(hfss_audit),
                    "compare_summary": str(compare_summary),
                    "target_marker_csv": str(compare_dir / "emx_hfss_ads_target_marker_metrics.csv"),
                    "ads_style_plot_summary": str(plot_summary),
                }
            ],
        },
    )
    objective = quality / "next_gen_s8p_objective_acceptance"
    _write_json(
        objective / "next_gen_s8p_objective_acceptance_summary.json",
        {
            "overall_status": "PASS",
            "decision": "READY_TO_CLAIM_NEXT_GEN_S8P_OBJECTIVE_COMPLETE",
            "final_objective_ready": True,
            "objective_statuses": {"1": "PASS", "2": "PASS", "3": "PASS", "4": "PASS", "5": "PASS"},
            "status_counts": {"PASS": 23},
        },
    )
    _write_text(objective / "NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md", "objective acceptance report\n")
    _write_text(objective / "next_gen_s8p_objective_acceptance_evidence.csv", "objective_id,status\n1,PASS\n")
    return quality


def _passing_postrun_manifest_checks() -> list[dict[str, str]]:
    names = [
        "HFSS build port manifest exists",
        "HFSS build port manifest schema",
        "HFSS build port manifest has 8 ports",
        "HFSS build port manifest port order is P001-P008",
        "HFSS build port manifest ground names are P001_G-P008_G",
        "HFSS build port manifest records integration lines",
    ]
    return [{"status": "PASS", "name": name, "sample": "1", "evaluation": "eval001"} for name in names]


class BuildS8pFinalReportEvidencePacketScriptTest(TransformerToolboxTestBase):
    def test_builds_pass_packet_from_complete_report_evidence(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            quality = _write_complete_fixture(root)

            status = mod.main(["--quality-dir", str(quality), "--out-dir", str(root / "packet")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "s8p_final_report_evidence_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "READY_TO_USE_S8P_FINAL_REPORT_EVIDENCE")
            categories = {item["category"] for item in summary["artifacts"] if item["status"] == "PASS"}
            self.assertIn("physical_feature_scalar_q_dataset", categories)
            self.assertIn("physical_feature_distribution", categories)
            self.assertIn("emx_layout_structure", categories)
            self.assertIn("physical_feature_inverse_training_data", categories)
            self.assertIn("physical_feature_inverse_model_quality", categories)
            self.assertIn("physical_feature_inverse_saved_model", categories)
            self.assertIn("physical_feature_inverse_target_structure", categories)
            self.assertIn("hfss_aedt_rebuild_scripts", categories)
            self.assertIn("hfss_model_structure", categories)
            self.assertIn("hfss_rebuild_port_trace", categories)
            self.assertIn("emx_hfss_touchstone_sources", categories)
            self.assertIn("emx_hfss_physical_curves", categories)
            self.assertIn("emx_hfss_ads_style_report_figures", categories)
            self.assertIn("objective_acceptance_audit", categories)
            self.assertIn("objective_acceptance_summary", summary["inputs"])
            self.assertIn("scalar_q_summary", summary["inputs"])
            self.assertTrue((root / "packet" / "s8p_final_report_artifact_manifest.csv").is_file())

    def test_waits_without_scalar_q_derivation_evidence(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            quality = _write_complete_fixture(root)
            (quality / "scalar_q_feature_dataset" / "scalar_q_feature_summary.json").unlink()

            status = mod.main(["--quality-dir", str(quality), "--out-dir", str(root / "packet"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "s8p_final_report_evidence_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["scalar-Q feature derivation summary exists"]["status"], "WAITING")
            self.assertEqual(checks["scalar-Q derived dataset uses q_center"]["status"], "WAITING")

    def test_waits_without_postrun_validation_evidence(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            quality = _write_complete_fixture(root)
            (quality / "selected_s8p_hfss_postrun_validation" / "s8p_hfss_postrun_validation_summary.json").unlink()

            status = mod.main(["--quality-dir", str(quality), "--out-dir", str(root / "packet"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "s8p_final_report_evidence_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["S8P EMX/HFSS postrun validation summary exists"]["status"], "WAITING")

    def test_fails_when_postrun_port_manifest_checks_are_missing(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            quality = _write_complete_fixture(root)
            postrun = quality / "selected_s8p_hfss_postrun_validation" / "s8p_hfss_postrun_validation_summary.json"
            payload = json.loads(postrun.read_text(encoding="utf-8"))
            payload["checks"] = []
            postrun.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            status = mod.main(["--quality-dir", str(quality), "--out-dir", str(root / "packet"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "s8p_final_report_evidence_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["postrun HFSS port manifest checks passed"]["status"], "FAIL")

    def test_fails_when_postrun_metrics_exceed_configured_percent_gate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            quality = _write_complete_fixture(root, bad_metric=True)

            status = mod.main(["--quality-dir", str(quality), "--out-dir", str(root / "packet"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "packet" / "s8p_final_report_evidence_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["eval001 EMX/HFSS <= 10% full-window metrics"]["status"], "FAIL")
            self.assertEqual(checks["eval001 15GHz marker metrics"]["status"], "FAIL")
