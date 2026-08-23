from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_gated_s8p_million_sample_campaign.py"
    spec = importlib.util.spec_from_file_location("run_gated_s8p_million_sample_campaign_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _compare_summary(*, k_error: float = 1.5) -> dict[str, object]:
    grid_checks = {
        "ADS no-extrapolation coverage": {"status": "PASS"},
        "expected frequency points": {"status": "PASS"},
        "expected frequency step": {"status": "PASS"},
        "matching HFSS/ADS frequency grid": {"status": "PASS"},
    }
    metrics = {
        "lp_nh": {"status": "PASS", "max_percent_error": 4.0},
        "ls_nh": {"status": "PASS", "max_percent_error": 5.0},
        "q": {"status": "PASS", "max_percent_error": 3.0},
        "k": {"status": "PASS" if k_error <= 10.0 else "FAIL", "max_percent_error": k_error},
        "kw": {"status": "PASS" if k_error <= 10.0 else "FAIL", "max_percent_error": k_error},
        "qp": {"status": "PASS", "max_percent_error": 2.0},
        "qs": {"status": "PASS", "max_percent_error": 3.0},
    }
    return {
        "overall_status": "PASS" if k_error <= 10.0 else "FAIL",
        "criterion": {"max_percent_error": 10.0},
        "frequency_window_hz": {"min": 5.0e9, "max": 60.0e9, "count": 111},
        "frequency_grid_checks": grid_checks,
        "metrics": metrics,
    }


def _write_direct_validation(root: Path, *, k_error: float = 1.5) -> Path:
    emx = root / "sample_emx.s8p"
    hfss = root / "sample_hfss.s8p"
    emx.write_text("! fake touchstone path evidence\n", encoding="utf-8")
    hfss.write_text("! fake touchstone path evidence\n", encoding="utf-8")
    compare_path = root / "emx_hfss_ads_comparison_summary.json"
    compare_path.write_text(json.dumps(_compare_summary(k_error=k_error), indent=2), encoding="utf-8")
    validation = {
        "overall_status": "PASS" if k_error <= 10.0 else "FAIL",
        "decision": "ACCEPT_HFSS_VALIDATION_SAMPLE" if k_error <= 10.0 else "DO_NOT_USE_HFSS_COMPARISON",
        "emx_touchstone": {"path": str(emx), "exists": True},
        "hfss_touchstone": {"path": str(hfss), "exists": True},
        "compare_summary": str(compare_path),
        "arguments": {
            "compare_start_ghz": 5.0,
            "compare_stop_ghz": 60.0,
            "expected_frequency_step_ghz": 0.5,
            "expected_frequency_points": 111,
            "max_percent_error": 10.0,
            "ground_unused_ports": True,
            "hfss_expected_ports": 8,
        },
        "checks": [
            {"status": "PASS", "name": "EMX-vs-HFSS compare core metric errors", "detail": "ok"},
            {"status": "PASS", "name": "EMX-vs-HFSS compare frequency-grid checks", "detail": "ok"},
        ],
    }
    path = root / "accepted_emx_hfss_ads_validation_summary.json"
    path.write_text(json.dumps(validation, indent=2), encoding="utf-8")
    return path


def _write_postrun_validation(root: Path, *, final_candidate: bool = True, frequency_grid_mode: str = "final_5_60_0p5_111") -> Path:
    emx = root / "sample_emx.s8p"
    hfss = root / "sample_hfss.s8p"
    emx.write_text("! fake emx s8p\n", encoding="utf-8")
    hfss.write_text("! fake hfss s8p\n", encoding="utf-8")
    compare_path = root / "emx_hfss_ads_comparison_summary.json"
    compare_path.write_text(json.dumps(_compare_summary(k_error=1.5), indent=2), encoding="utf-8")
    validation = {
        "overall_status": "PASS",
        "decision": "ACCEPT_SELECTED_S8P_EMX_HFSS_PHYSICAL_VALIDATION",
        "frequency_grid_mode": frequency_grid_mode,
        "final_acceptance_candidate": final_candidate,
        "arguments": {
            "compare_start_ghz": 5.0,
            "compare_stop_ghz": 60.0,
            "expected_frequency_step_ghz": 0.5,
            "expected_frequency_points": 111,
            "max_percent_error": 10.0,
            "ground_unused_ports": False,
            "expected_ports": 8,
        },
        "records": [
            {
                "evaluation": "sample_a",
                "status": "PASS",
                "emx_s8p": str(emx),
                "hfss_s8p": str(hfss),
                "worst_percent_error": 5.0,
                "compare_summary": str(compare_path),
            }
        ],
    }
    path = root / "s8p_hfss_postrun_validation_summary.json"
    path.write_text(json.dumps(validation, indent=2), encoding="utf-8")
    return path


class RunGatedS8pMillionSampleCampaignScriptTest(TransformerToolboxTestBase):
    def test_blocks_when_validation_summary_is_missing(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status = mod.main(
                [
                    "--validation-summary",
                    str(root / "missing.json"),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "s8p_million_sample_campaign_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["chunk_count"], 0)
            self.assertIn("DO_NOT_START", summary["decision"])
            command_script = (root / "out" / "s8p_million_sample_campaign.commands.sh").read_text(encoding="utf-8")
            self.assertIn("STOP: EMX-HFSS S8P validation gate has not passed", command_script)

    def test_rejects_validation_summary_when_metric_error_exceeds_gate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_direct_validation(root, k_error=12.5)

            status = mod.main(["--validation-summary", str(validation), "--out-dir", str(root / "out"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "s8p_million_sample_campaign_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            blocking = " ".join(check["name"] for check in summary["validation_gate"]["blocking_checks"])
            self.assertIn("metric k", blocking)
            self.assertEqual(summary["chunk_count"], 0)

    def test_waiting_postrun_summary_is_recognized_not_unknown(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = root / "s8p_hfss_postrun_validation_summary.json"
            validation.write_text(
                json.dumps(
                    {
                        "overall_status": "WAITING_FOR_HFSS",
                        "decision": "WAIT_FOR_EXPORTED_HFSS_S8P",
                        "arguments": {
                            "compare_start_ghz": 5.0,
                            "compare_stop_ghz": 60.0,
                            "expected_frequency_step_ghz": 0.5,
                            "expected_frequency_points": 111,
                            "max_percent_error": 10.0,
                            "ground_unused_ports": False,
                            "expected_ports": 8,
                        },
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(["--validation-summary", str(validation), "--out-dir", str(root / "out"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "s8p_million_sample_campaign_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["validation_gate"]["kind"], "s8p_hfss_postrun_validation")
            self.assertEqual(summary["chunk_count"], 0)

    def test_rejects_postrun_pass_when_not_final_acceptance_candidate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_postrun_validation(root, final_candidate=False)

            status = mod.main(["--validation-summary", str(validation), "--out-dir", str(root / "out"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "s8p_million_sample_campaign_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            blocking = " ".join(check["name"] for check in summary["validation_gate"]["blocking_checks"])
            self.assertIn("postrun final acceptance candidate", blocking)
            self.assertEqual(summary["chunk_count"], 0)

    def test_rejects_postrun_pass_when_frequency_grid_mode_is_not_final(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_postrun_validation(root, frequency_grid_mode="diagnostic_15_15p5_0p5_2")

            status = mod.main(["--validation-summary", str(validation), "--out-dir", str(root / "out"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "s8p_million_sample_campaign_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            blocking = " ".join(check["name"] for check in summary["validation_gate"]["blocking_checks"])
            self.assertIn("postrun frequency grid mode", blocking)
            self.assertEqual(summary["chunk_count"], 0)

    def test_passed_validation_creates_ten_chunk_dry_run_plan(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_direct_validation(root, k_error=1.5)

            status = mod.main(["--validation-summary", str(validation), "--out-dir", str(root / "out")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "s8p_million_sample_campaign_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN")
            self.assertEqual(summary["total_requested_samples"], 1_000_000)
            self.assertEqual(summary["chunk_size"], 100_000)
            self.assertEqual(summary["chunk_count"], 10)
            self.assertEqual(summary["chunks"][-1]["cumulative_count"], 1_000_000)
            first = summary["chunks"][0]["commands"]
            self.assertIn("scripts/run_candidate_queue_dataset_parallel.py", first["run_emx_parallel"])
            self.assertIn("--extract-response-features", first["run_quality_gates"])
            self.assertIn("--build-physical-feature-inverse-training-table", first["run_quality_gates"])
            self.assertIn("scripts/train_physical_feature_inverse_model.py", first["train_inverse_model"])
            self.assertIn("scripts/audit_physical_feature_inverse_model_quality.py", first["audit_inverse_model"])
            self.assertIn("scripts/plan_physical_feature_inverse_nn_architecture_search.py", first["plan_nn_architecture_search"])
            self.assertIn("scripts/train_physical_feature_inverse_nn_architecture_search.py", first["train_nn_architecture_search"])
            self.assertIn("physical_feature_inverse_nn_architecture_candidates.csv", " ".join(first["train_nn_architecture_search"]))
            self.assertIn("scripts/audit_s8p_million_chunk_checkpoint.py", first["audit_chunk_checkpoint"])
            self.assertEqual(summary["chunks"][0]["nn_architecture_dir"].endswith("inverse_nn_architecture_search"), True)
            self.assertEqual(summary["chunks"][0]["nn_training_dir"].endswith("inverse_nn_architecture_training"), True)
            self.assertEqual(summary["chunks"][0]["checkpoint_dir"].endswith("chunk_checkpoint"), True)
            command_script = (root / "out" / "s8p_million_sample_campaign.commands.sh").read_text(encoding="utf-8")
            self.assertIn("[dry-run]", command_script)
            self.assertIn("scripts/run_candidate_queue_dataset_parallel.py", command_script)
            self.assertIn("scripts/plan_physical_feature_inverse_nn_architecture_search.py", command_script)
            self.assertIn("scripts/train_physical_feature_inverse_nn_architecture_search.py", command_script)
            self.assertIn("scripts/audit_s8p_million_chunk_checkpoint.py", command_script)

    def test_allow_real_emx_removes_dry_run_guard_from_parallel_generation_command(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_direct_validation(root, k_error=1.5)

            status = mod.main(
                [
                    "--validation-summary",
                    str(validation),
                    "--out-dir",
                    str(root / "out"),
                    "--allow-real-emx",
                ]
            )

            self.assertEqual(status, 0)
            command_script = (root / "out" / "s8p_million_sample_campaign.commands.sh").read_text(encoding="utf-8")
            self.assertNotIn("[dry-run]", command_script)
            self.assertIn("scripts/run_candidate_queue_dataset_parallel.py", command_script)
