from tests.rfic_transformer_inverse_design.shared import *

import os
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
TSMC65_RUNNER = ROOT / "NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh"
STATUS_CHECK = ROOT / "NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh"
RUN_COMMANDS = ROOT / "NEXT_GEN_S8P_MARS_20260620_RUN_COMMANDS_CN.md"
IMPORT_RETURN = ROOT / "NEXT_GEN_S8P_IMPORT_MARS_RETURN_20260620.sh"
START_CURRENT = ROOT / "NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh"
AFTER_RETURN = ROOT / "NEXT_GEN_S8P_AFTER_MARS_RETURN_AUTOPROCESS_20260620.sh"
MARS_20_AFTER_UNLOCK = ROOT / "MARS_S8P_20_AFTER_UNLOCK_20260626.sh"


class NextGenS8pNoGuiMarsRunnerScriptsTest(TransformerToolboxTestBase):
    def test_no_gui_runner_scripts_are_shell_parseable(self) -> None:
        for path in (TSMC65_RUNNER, STATUS_CHECK, IMPORT_RETURN, START_CURRENT, AFTER_RETURN, MARS_20_AFTER_UNLOCK):
            result = subprocess.run(["bash", "-n", str(path)], check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{path}\nSTDERR:\n{result.stderr}")

    def test_mars_20_after_unlock_packages_return_bundle_for_local_import(self) -> None:
        text = MARS_20_AFTER_UNLOCK.read_text(encoding="utf-8")

        for token in (
            "inverse-candidate-count 20",
            "expected-emx-count 20",
            "--jobs 8",
            "discover_final_valid_emx_s8p_candidates.py",
            "RETURN_PACKAGE_START",
            "RETURN_PACKAGE_DONE",
            "next_gen_s8p_mars_return_latest.tar.gz",
            "next_gen_s8p_mars_return_latest_verify_summary.json",
            "package_mars_dataset_run.py",
            "verify_mars_dataset_package.py",
            "run_stage1_calibration_if_available",
            "RUN_STAGE1_CALIBRATION",
            "calibration_execution_packet_stage1_wideband_20260626.tar.gz",
            "mars_run_emx_calibration.sh",
            "STAGE1_CALIBRATION_START",
            "STAGE1_CALIBRATION_DONE",
            "stage1_emx_calibration_wideband_latest.tar.gz",
            "stage1_emx_calibration_wideband_latest_manifest.json",
            "--expected-count 20",
            "--expected-touchstone-ports 8",
            "--required-touchstone-extension .s8p",
            "--expected-frequency-start-ghz 5",
            "--expected-frequency-stop-ghz 60",
            "--expected-frequency-step-ghz 0.5",
            "--expected-frequency-points 111",
            "--require-s8p-quality-gates",
            "--require-next-gen-s8p-status",
            "--require-run-config",
        ):
            self.assertIn(token, text)

    def test_tsmc65_runner_keeps_real_emx_detached_and_traceable(self) -> None:
        text = TSMC65_RUNNER.read_text(encoding="utf-8")

        for token in (
            "nohup bash -lc",
            "tsmc65_real_emx_500_latest.pid",
            "tsmc65_real_emx_500_latest.log",
            "tsmc65_real_emx_500_latest_monitor.log",
            "tsmc65_real_emx_500_latest_progress.csv",
            "tsmc65_real_emx_500_latest_status_check.log",
            "next_gen_s8p_mars_return_latest.tar.gz",
            "next_gen_s8p_mars_return_latest_verify_summary.json",
            "tsmc65_latest_status.txt",
            "tsmc65_latest_status.json",
            "REAL_EMX_ALREADY_RUNNING",
            "REAL_EMX_BACKGROUND",
            "REAL_EMX_RUNNING",
            "REAL_EMX_500_DONE",
            "REAL_EMX_DONE",
            "REAL_EMX_STOPPED_OR_FAILED",
            "s8p_count",
            "dataset_rows",
            "EXPECTED_S8P_COUNT",
            "kill -0",
            "link_latest",
            "start_real_emx_monitor",
            "FINAL_STATUS_CHECK_START",
            "FINAL_STATUS_CHECK_DONE",
            "RETURN_PACKAGE_START",
            "RETURN_PACKAGE_DONE",
            "RETURN_PACKAGE_CONFIG_COPIED",
            "final_s8p_physical_feature_500.yaml",
            "actual_latest_sha",
            "basename",
            "--include-hfss-validation-assets",
            "--require-next-gen-s8p-status",
            "--require-run-config",
        ):
            self.assertIn(token, text)

    def test_status_check_surfaces_latest_tsmc65_runner_state(self) -> None:
        text = STATUS_CHECK.read_text(encoding="utf-8")

        for token in (
            "== TSMC65 runner status ==",
            "tsmc65_latest_status.txt",
            "tsmc65_latest_status.json",
            "tsmc65_real_emx_500_latest.pid",
            "LATEST_PID_STATUS=RUNNING",
            "LATEST_PID_STATUS=NOT_RUNNING",
            "tsmc65_real_emx_500_latest.log",
            "tsmc65_real_emx_500_latest_monitor.log",
            "tsmc65_real_emx_500_latest_progress.csv",
            "tsmc65_real_emx_500_latest_status_check.log",
            "LATEST_FINAL_STATUS_CHECK_LOG",
            "== Latest MARS return package ==",
            "next_gen_s8p_mars_return_latest.tar.gz",
            "next_gen_s8p_mars_return_latest_verify_summary.json",
            "return_package_verify_status",
            "== HFSS validation chain summaries ==",
            "== Run-status manifest contract gate ==",
            "next_gen_s8p_mars_run_status_summary.json",
            "dataset manifest matches approved S8P topology contract",
            "manifest_contract_status",
            "manifest_contract_evidence",
            "all successful rows are traceable to EMX-generated .s8p files",
            "emx_source_gate_status",
            "emx_source_gate_evidence",
            "selected_s8p_hfss_handoff_summary.json",
            "hfss_s8p_aedt_script_packet_summary.json",
            "hfss_payload_geometry_render_batch_summary.json",
            "s8p_hfss_postrun_validation_summary.json",
            "== HFSS AEDT handoff files ==",
            "run_generated_hfss_s8p_scripts.commands.ps1",
            "hfss_s8p_build_payload.json",
            "build_hfss_s8p_from_payload.py",
            "solve_export_hfss_s8p.py",
            "== HFSS payload geometry views ==",
            "== HFSS exported S8P / validation outputs ==",
            "emx_hfss_ads_comparison_summary.json",
            "ads_style_metric_plot_summary.json",
        ):
            self.assertIn(token, text)

    def test_run_commands_document_latest_no_gui_monitor_files(self) -> None:
        text = RUN_COMMANDS.read_text(encoding="utf-8")

        for token in (
            "tsmc65_latest_status.txt",
            "tsmc65_latest_status.json",
            "tsmc65_real_emx_500_latest.pid",
            "tsmc65_real_emx_500_latest.log",
            "tsmc65_real_emx_500_latest_monitor.log",
            "tsmc65_real_emx_500_latest_progress.csv",
            "tsmc65_real_emx_500_latest_status_check.log",
            "next_gen_s8p_mars_return_latest.tar.gz",
            "next_gen_s8p_mars_return_latest_verify_summary.json",
            "NEXT_GEN_S8P_AFTER_MARS_RETURN_AUTOPROCESS_20260620.sh",
            "NEXT_GEN_S8P_IMPORT_MARS_RETURN_20260620.sh",
            "import_next_gen_s8p_mars_return_package.py",
            "next_gen_s8p_mars_return_import_summary.json",
            "next_gen_s8p_after_import_next_steps.commands.sh",
        ):
            self.assertIn(token, text)

    def test_local_return_import_script_is_strict_and_no_gui(self) -> None:
        text = IMPORT_RETURN.read_text(encoding="utf-8")

        for token in (
            "import_next_gen_s8p_mars_return_package.py",
            "next_gen_s8p_mars_return*.tar.gz",
            "--expected-count",
            "--expected-jobs",
            "--expected-ports",
            "--expected-frequency-start-ghz",
            "--expected-frequency-stop-ghz",
            "--expected-frequency-step-ghz",
            "--expected-frequency-points",
            "--max-touchstone-checks",
            "--max-touchstone-frequency-checks",
            "--require-hfss-validation-assets",
            "next_gen_s8p_mars_return_import_summary.json",
            "It verifies the package before extraction and never starts EMX, HFSS, ADS",
        ):
            self.assertIn(token, text)

    def test_after_return_autoprocess_is_local_only_and_gated_by_import(self) -> None:
        text = AFTER_RETURN.read_text(encoding="utf-8")

        for token in (
            "Local-only post-return processor",
            "This wrapper does not run EMX, HFSS, ADS, Cadence",
            "NEXT_GEN_S8P_IMPORT_MARS_RETURN_20260620.sh",
            "next_gen_s8p_mars_return*.tar.gz",
            "RUN_NEXT_STEPS=0",
            "ERROR: import failed; not running after-import next steps.",
            "next_gen_s8p_mars_return_import_summary.json",
            "next_steps_result",
            "next_gen_s8p_after_import_next_steps.commands.sh",
        ):
            self.assertIn(token, text)

    def test_after_return_autoprocess_does_not_run_next_steps_when_import_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "out"
            missing_return = root / "missing_return.tar.gz"
            result = subprocess.run(
                ["bash", str(AFTER_RETURN), str(missing_return)],
                cwd=root,
                env={**os.environ, "OUT_DIR": str(out_dir)},
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertIn("ERROR: import failed; not running after-import next steps.", combined)
            self.assertNotIn("AFTER_RETURN_AUTOPROCESS_DONE", combined)

    def test_after_return_autoprocess_runs_generated_next_steps_after_successful_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "out"
            return_tar = root / "next_gen_s8p_mars_return_latest.tar.gz"
            return_tar.write_bytes(b"fake return tarball path only")
            fake_importer = root / "fake_import.sh"
            fake_importer.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        "mkdir -p \"$OUT_DIR\"",
                        "next_script=\"$OUT_DIR/next_gen_s8p_after_import_next_steps.commands.sh\"",
                        "cat > \"$next_script\" <<'EOS'",
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        "echo FAKE_NEXT_STEPS_RAN",
                        "EOS",
                        "chmod +x \"$next_script\"",
                        "cat > \"$OUT_DIR/next_gen_s8p_mars_return_import_summary.json\" <<EOF",
                        "{\"overall_status\":\"PASS\",\"next_steps_result\":{\"generated\":true,\"script_path\":\"$next_script\"}}",
                        "EOF",
                    ]
                ),
                encoding="utf-8",
            )
            fake_importer.chmod(0o755)

            result = subprocess.run(
                ["bash", str(AFTER_RETURN), str(return_tar)],
                cwd=root,
                env={**os.environ, "OUT_DIR": str(out_dir), "IMPORT_SCRIPT": str(fake_importer)},
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            combined = result.stdout + result.stderr
            self.assertIn("FAKE_NEXT_STEPS_RAN", combined)
            self.assertIn("NEXT_STEPS_EXIT_CODE=0", combined)
            self.assertIn("AFTER_RETURN_AUTOPROCESS_DONE", combined)

    def test_after_return_autoprocess_can_stop_after_successful_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "out"
            return_tar = root / "next_gen_s8p_mars_return_latest.tar.gz"
            return_tar.write_bytes(b"fake return tarball path only")
            marker = root / "next_steps_ran.marker"
            fake_importer = root / "fake_import.sh"
            fake_importer.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        "mkdir -p \"$OUT_DIR\"",
                        "next_script=\"$OUT_DIR/next_gen_s8p_after_import_next_steps.commands.sh\"",
                        "cat > \"$next_script\" <<EOS",
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f"touch {marker}",
                        "EOS",
                        "chmod +x \"$next_script\"",
                        "cat > \"$OUT_DIR/next_gen_s8p_mars_return_import_summary.json\" <<EOF",
                        "{\"overall_status\":\"PASS\",\"next_steps_result\":{\"generated\":true,\"script_path\":\"$next_script\"}}",
                        "EOF",
                    ]
                ),
                encoding="utf-8",
            )
            fake_importer.chmod(0o755)

            result = subprocess.run(
                ["bash", str(AFTER_RETURN), str(return_tar)],
                cwd=root,
                env={
                    **os.environ,
                    "OUT_DIR": str(out_dir),
                    "IMPORT_SCRIPT": str(fake_importer),
                    "RUN_NEXT_STEPS": "0",
                },
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            combined = result.stdout + result.stderr
            self.assertIn("RUN_NEXT_STEPS is not 1; stopping after verified import.", combined)
            self.assertFalse(marker.exists())
