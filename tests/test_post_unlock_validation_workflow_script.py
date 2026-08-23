from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_workflow_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "run_post_unlock_validation_workflow.py"
    )
    spec = importlib.util.spec_from_file_location("run_post_unlock_validation_workflow_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_python(path: Path, exit_code: int = 0, stdout: str = "") -> Path:
    path.write_text(
        "import sys\n"
        f"print({stdout!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return path


class PostUnlockValidationWorkflowScriptTest(TransformerToolboxTestBase):
    def test_dry_run_records_full_command_order_without_execution(self) -> None:
        mod = _load_workflow_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            readiness = _write_python(root / "readiness.py")
            reachable_processor = _write_python(root / "reachable_processor.py")
            validation_processor = _write_python(root / "validation_processor.py")
            targeted_processor = _write_python(root / "targeted_processor.py")
            builder = _write_python(root / "builder.py")
            mod.DEFAULT_BUILDERS = [builder]
            reachable_bundle = root / "reachable.tar.gz"
            targeted_bundle = root / "targeted_dataset.tar.gz"
            validation_bundle = root / "validation.tar.gz"
            reachable_bundle.write_bytes(b"dry-run reachable placeholder")
            targeted_bundle.write_bytes(b"dry-run targeted placeholder")
            validation_bundle.write_bytes(b"dry-run validation placeholder")
            report_dir = root / "report"

            status = mod.main(
                [
                    "--local-root",
                    str(root / "local"),
                    "--report-dir",
                    str(report_dir),
                    "--python",
                    sys.executable,
                    "--readiness-script",
                    str(readiness),
                    "--reachable-processor",
                    str(reachable_processor),
                    "--validation-processor",
                    str(validation_processor),
                    "--targeted-dataset-processor",
                    str(targeted_processor),
                    "--reachable-bundle",
                    str(reachable_bundle),
                    "--targeted-dataset-bundle",
                    str(targeted_bundle),
                    "--validation-bundle",
                    str(validation_bundle),
                    "--dry-run",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((report_dir / "post_unlock_validation_workflow_summary_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "DRY_RUN")
            self.assertEqual(summary["decision"], "POST_UNLOCK_WORKFLOW_DRY_RUN_ONLY")
            self.assertEqual(
                [item["name"] for item in summary["commands"]],
                [
                    "verify_mars_unlock_workflow_readiness",
                    "process_reachable_candidate_queue_bundle",
                    "process_mars_validation_export_bundle",
                    "process_targeted_reachable_queue_emx_dataset_bundle",
                    "builder",
                ],
            )
            self.assertEqual({item["status"] for item in summary["commands"]}, {"DRY_RUN"})
            targeted_cmd = next(item["cmd"] for item in summary["commands"] if item["name"] == "process_targeted_reachable_queue_emx_dataset_bundle")
            self.assertIn(str(targeted_processor.resolve()), targeted_cmd)
            self.assertIn("--skip-report-rebuild", targeted_cmd)
            self.assertIn("--report-dir", targeted_cmd)
            self.assertTrue((report_dir / "POST_UNLOCK_VALIDATION_WORKFLOW_20260615_CN.md").is_file())
            self.assertTrue((report_dir / "post_unlock_validation_workflow.html").is_file())

    def test_required_missing_bundle_fails_without_running_later_steps(self) -> None:
        mod = _load_workflow_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report_dir = root / "report"
            status = mod.main(
                [
                    "--report-dir",
                    str(report_dir),
                    "--skip-readiness",
                    "--skip-final-report-rebuild",
                    "--require-reachable",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((report_dir / "post_unlock_validation_workflow_summary_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "POST_UNLOCK_WORKFLOW_FAILED")
            self.assertEqual([item["name"] for item in summary["commands"]], ["process_reachable_candidate_queue_bundle"])
            self.assertEqual(summary["commands"][0]["status"], "MISSING_INPUT")

    def test_keep_going_records_later_steps_but_keeps_overall_failure(self) -> None:
        mod = _load_workflow_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            readiness = _write_python(root / "readiness.py", exit_code=5, stdout="readiness failed")
            validation_processor = _write_python(root / "validation_processor.py", stdout="validation processed")
            builder = _write_python(root / "builder.py", stdout="rebuilt")
            mod.DEFAULT_BUILDERS = [builder]
            validation_bundle = root / "validation.tar.gz"
            validation_bundle.write_bytes(b"placeholder")
            report_dir = root / "report"

            status = mod.main(
                [
                    "--report-dir",
                    str(report_dir),
                    "--python",
                    sys.executable,
                    "--readiness-script",
                    str(readiness),
                    "--validation-processor",
                    str(validation_processor),
                    "--validation-bundle",
                    str(validation_bundle),
                    "--keep-going",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((report_dir / "post_unlock_validation_workflow_summary_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(
                [item["name"] for item in summary["commands"]],
                [
                    "verify_mars_unlock_workflow_readiness",
                    "process_reachable_candidate_queue_bundle",
                    "process_mars_validation_export_bundle",
                    "process_targeted_reachable_queue_emx_dataset_bundle",
                    "builder",
                ],
            )
            self.assertEqual(summary["commands"][0]["status"], "FAIL")
            self.assertEqual(summary["commands"][1]["status"], "SKIPPED")
            self.assertEqual(summary["commands"][2]["status"], "PASS")
            self.assertEqual(summary["commands"][3]["status"], "SKIPPED")
            self.assertEqual(summary["commands"][4]["status"], "PASS")
