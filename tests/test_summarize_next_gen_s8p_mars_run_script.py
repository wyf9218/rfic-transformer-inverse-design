from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "summarize_next_gen_s8p_mars_run.py"
    spec = importlib.util.spec_from_file_location("summarize_next_gen_s8p_mars_run_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_dataset_rows(run_dir: Path, count: int) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["evaluation", "ok", "touchstone_path"])
        writer.writeheader()
        for idx in range(count):
            rel = f"evaluations/eval_{idx:03d}/emx/emx.s8p"
            path = run_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("! placeholder; tests skip Touchstone loading\n", encoding="utf-8")
            writer.writerow({"evaluation": f"eval_{idx:03d}", "ok": "true", "touchstone_path": rel})
    _write_json(
        run_dir / "dataset_manifest.json",
        {
            "port_mode": "single_ended_shield_grounded",
            "differential_port_pairs": [[1, 4], [5, 6]],
            "power_line_8port": {
                "enabled": True,
                "bridge_width_um": 10.0,
                "vertical_length_diameter_ratio": 1.5,
                "port_map": ["P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"],
                "ground_frame_width_um": 100.0,
                "ground_frame_policy": "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
            },
            "target_frequency": {
                "start_hz": 5.0e9,
                "stop_hz": 60.0e9,
                "step_hz": 5.0e8,
                "points": 111,
            },
        },
    )


def _write_parallel_summary(run_dir: Path, count: int, jobs: int) -> None:
    _write_json(
        run_dir / "parallel_candidate_queue_dataset_summary.json",
        {
            "overall_status": "PASS",
            "jobs_requested": jobs,
            "merged_row_count": count,
            "checks": [
                {"name": "requested_jobs_match_expected", "pass": True},
                {"name": "merged_count_matches_expected", "pass": True},
            ],
        },
    )


def _write_inverse_chain(quality: Path) -> None:
    contract = {
        "zin_columns": [],
        "lp_columns": ["input__lp_nh_center"],
        "ls_columns": ["input__ls_nh_center"],
        "q_columns": ["input__q_center"],
        "k_columns": ["input__k_center"],
    }
    _write_json(
        quality / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json",
        {
            "overall_status": "PASS",
            "training_count": 12,
            "input_feature_contract": contract,
        },
    )
    _write_json(
        quality / "physical_feature_inverse_model_quality" / "physical_feature_inverse_model_quality_summary.json",
        {
            "overall_status": "PASS",
            "input_feature_contract": contract,
            "quality_summary": {"per_geometry": {"geom__w_um": {"normalized_mae": 0.1}}},
        },
    )
    model_json = quality / "physical_feature_saved_inverse_model" / "physical_feature_inverse_model.json"
    model_json.parent.mkdir(parents=True, exist_ok=True)
    model_json.write_text('{"method":"standardized_polynomial_ridge_regression"}\n', encoding="utf-8")
    _write_json(
        quality / "physical_feature_saved_inverse_model" / "physical_feature_inverse_model_training_summary.json",
        {
            "overall_status": "PASS",
            "method": "standardized_polynomial_ridge_regression",
            "model_json": str(model_json),
            "input_feature_contract": contract,
        },
    )


def _write_final_report_evidence(quality: Path) -> None:
    categories = [
        "physical_feature_distribution",
        "emx_layout_structure",
        "physical_feature_inverse_training_data",
        "physical_feature_inverse_model_quality",
        "physical_feature_inverse_saved_model",
        "hfss_aedt_rebuild_scripts",
        "hfss_model_structure",
        "hfss_rebuild_port_trace",
        "emx_hfss_touchstone_sources",
        "emx_hfss_physical_curves",
        "emx_hfss_ads_style_report_figures",
    ]
    _write_json(
        quality / "s8p_final_report_evidence_packet" / "s8p_final_report_evidence_packet_summary.json",
        {
            "overall_status": "PASS",
            "decision": "READY_TO_USE_S8P_FINAL_REPORT_EVIDENCE",
            "artifacts": [{"category": category, "status": "PASS", "key": category} for category in categories],
        },
    )


def _passing_postrun_checks() -> list[dict[str, str]]:
    names = [
        "HFSS build port manifest exists",
        "HFSS build port manifest schema",
        "HFSS build port manifest has 8 ports",
        "HFSS build port manifest port order is P001-P008",
        "HFSS build port manifest ground names are P001_G-P008_G",
        "HFSS build port manifest records integration lines",
    ]
    return [{"status": "PASS", "name": name, "sample": "1", "evaluation": "eval"} for name in names]


class SummarizeNextGenS8pMarsRunScriptTest(TransformerToolboxTestBase):
    def test_empty_run_reports_waiting_without_fabricating_completion(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            status = mod.main(
                [
                    "--run-dir",
                    str(root / "run"),
                    "--out-dir",
                    str(root / "status"),
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--max-touchstone-checks",
                    "0",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "status" / "next_gen_s8p_mars_run_status_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_MARS_EMX")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["8-worker EMX candidate queue completed"]["status"], "WAITING")
            self.assertEqual(requirements["dataset_rows.csv has expected 500 successful rows"]["status"], "WAITING")

    def test_ready_for_hfss_export_when_payload_exists_and_postrun_waits_for_hfss(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "run"
            quality = run / "dataset_quality_gates_s8p_physical_feature"
            _write_dataset_rows(run, 2)
            _write_parallel_summary(run, 2, 2)
            _write_json(quality / "dataset_quality_gates_summary.json", {"overall_status": "PASS", "decision": "READY"})
            samples = quality / "physical_feature_validation_sample_selection" / "physical_feature_validation_samples.csv"
            samples.parent.mkdir(parents=True, exist_ok=True)
            samples.write_text("evaluation,touchstone_path\neval_000,evaluations/eval_000/emx/emx.s8p\n", encoding="utf-8")
            _write_json(
                quality / "selected_s8p_port_pair_physical_candidate_audit" / "s8p_port_pair_physical_candidate_audit_summary.json",
                {"overall_status": "PASS", "expected_port_pairs": "1,4:5,6", "expected_port_pairs_all_pass": True},
            )
            _write_json(quality / "selected_power_line_8port_layout_audit" / "selected_power_line_8port_layout_audit_summary.json", {"overall_status": "PASS"})
            _write_json(quality / "selected_s8p_hfss_handoff" / "selected_s8p_hfss_handoff_summary.json", {"overall_status": "PASS"})
            _write_json(quality / "selected_s8p_hfss_aedt_scripts" / "hfss_s8p_aedt_script_packet_summary.json", {"overall_status": "PASS"})
            _write_json(
                quality / "selected_s8p_hfss_payload_views" / "hfss_payload_geometry_render_batch_summary.json",
                {"overall_status": "PASS", "rendered_count": 1},
            )
            _write_json(
                quality / "selected_s8p_hfss_postrun_validation" / "s8p_hfss_postrun_validation_summary.json",
                {"overall_status": "WAITING_FOR_HFSS", "status_counts": {"WAITING_FOR_HFSS": 1}},
            )
            _write_inverse_chain(quality)

            status = mod.main(
                [
                    "--run-dir",
                    str(run),
                    "--quality-dir",
                    str(quality),
                    "--out-dir",
                    str(root / "status"),
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--max-touchstone-checks",
                    "0",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "status" / "next_gen_s8p_mars_run_status_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_HFSS_EXPORT")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["EMX/HFSS Lp/Ls/Q/K/Kw postrun comparison completed"]["status"], "WAITING")
            self.assertEqual(requirements["saved Lp/Ls/Q/K-to-geometry inverse model is trained"]["status"], "PASS")
            self.assertEqual(requirements["dataset manifest matches approved S8P topology contract"]["status"], "PASS")

    def test_rejects_returned_rows_with_old_or_wrong_manifest_contract(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "run"
            _write_dataset_rows(run, 2)
            manifest = json.loads((run / "dataset_manifest.json").read_text(encoding="utf-8"))
            manifest["differential_port_pairs"] = [[1, 2], [7, 8]]
            manifest["power_line_8port"]["bridge_width_um"] = 0.01
            manifest["target_frequency"]["stop_hz"] = 16.5e9
            _write_json(run / "dataset_manifest.json", manifest)

            status = mod.main(
                [
                    "--run-dir",
                    str(run),
                    "--out-dir",
                    str(root / "status"),
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--max-touchstone-checks",
                    "0",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "status" / "next_gen_s8p_mars_run_status_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            manifest_gate = requirements["dataset manifest matches approved S8P topology contract"]
            self.assertEqual(manifest_gate["status"], "FAIL")
            self.assertIn("differential_port_pairs", manifest_gate["evidence"])
            self.assertIn("power_line_8port_bridge_width", manifest_gate["evidence"])

    def test_rejects_hfss_labeled_touchstone_as_training_emx_source(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "run"
            _write_dataset_rows(run, 1)
            hfss_rel = "evaluations/eval_000/hfss/HFSSDesign1.s8p"
            hfss_path = run / hfss_rel
            hfss_path.parent.mkdir(parents=True, exist_ok=True)
            hfss_path.write_text(
                "! Touchstone file exported from HFSS 2025.1.0\n# GHz S RI R 50\n",
                encoding="ascii",
            )
            with (run / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["evaluation", "ok", "touchstone_path"])
                writer.writeheader()
                writer.writerow({"evaluation": "eval_000", "ok": "true", "touchstone_path": hfss_rel})

            status = mod.main(
                [
                    "--run-dir",
                    str(run),
                    "--out-dir",
                    str(root / "status"),
                    "--expected-count",
                    "1",
                    "--expected-jobs",
                    "1",
                    "--max-touchstone-checks",
                    "0",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "status" / "next_gen_s8p_mars_run_status_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            source_gate = requirements["all successful rows are traceable to EMX-generated .s8p files"]
            self.assertEqual(source_gate["status"], "FAIL")
            self.assertIn("'HFSS': 1", source_gate["evidence"])
            self.assertIn("failed_source_count=1", source_gate["evidence"])

    def test_postrun_pass_without_inverse_and_final_evidence_is_not_complete(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "run"
            quality = run / "dataset_quality_gates_s8p_physical_feature"
            _write_dataset_rows(run, 2)
            _write_parallel_summary(run, 2, 2)
            _write_json(quality / "dataset_quality_gates_summary.json", {"overall_status": "PASS"})
            _write_json(
                quality / "selected_s8p_hfss_postrun_validation" / "s8p_hfss_postrun_validation_summary.json",
                {"overall_status": "PASS", "status_counts": {"PASS": 1}, "checks": _passing_postrun_checks()},
            )

            status = mod.main(
                [
                    "--run-dir",
                    str(run),
                    "--quality-dir",
                    str(quality),
                    "--out-dir",
                    str(root / "status"),
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--max-touchstone-checks",
                    "0",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "status" / "next_gen_s8p_mars_run_status_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_FINAL_REPORT_EVIDENCE")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["EMX/HFSS Lp/Ls/Q/K/Kw postrun comparison completed"]["status"], "PASS")
            self.assertEqual(requirements["HFSS build port manifest proves 8-port integration lines"]["status"], "PASS")
            self.assertEqual(requirements["saved Lp/Ls/Q/K-to-geometry inverse model is trained"]["status"], "WAITING")
            self.assertEqual(requirements["final report evidence packet passed"]["status"], "WAITING")

    def test_postrun_pass_with_inverse_and_final_evidence_reports_verified_sample_ready(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "run"
            quality = run / "dataset_quality_gates_s8p_physical_feature"
            _write_dataset_rows(run, 2)
            _write_parallel_summary(run, 2, 2)
            _write_json(quality / "dataset_quality_gates_summary.json", {"overall_status": "PASS"})
            _write_inverse_chain(quality)
            _write_json(
                quality / "selected_s8p_hfss_postrun_validation" / "s8p_hfss_postrun_validation_summary.json",
                {"overall_status": "PASS", "status_counts": {"PASS": 1}, "checks": _passing_postrun_checks()},
            )
            _write_final_report_evidence(quality)

            status = mod.main(
                [
                    "--run-dir",
                    str(run),
                    "--quality-dir",
                    str(quality),
                    "--out-dir",
                    str(root / "status"),
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--max-touchstone-checks",
                    "0",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "status" / "next_gen_s8p_mars_run_status_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "READY_TO_REPORT_VERIFIED_NEXT_GEN_S8P_SAMPLE")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["post-EMX inverse training table uses Lp/Ls/Q/K without Zin"]["status"], "PASS")
            self.assertEqual(requirements["final report evidence packet passed"]["status"], "PASS")
