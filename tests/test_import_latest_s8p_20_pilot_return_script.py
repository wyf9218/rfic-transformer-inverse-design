from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "import_latest_s8p_20_pilot_return.py"
    spec = importlib.util.spec_from_file_location("import_latest_s8p_20_pilot_return_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ImportLatestS8p20PilotReturnScriptTest(TransformerToolboxTestBase):
    def test_waits_when_no_return_tarball_is_available(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "out"

            status = mod.main(["--search-root", str(root / "empty"), "--out-dir", str(out_dir)])

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "latest_s8p_20_pilot_return_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_RETURN")
            self.assertEqual(summary["decision"], "WAIT_FOR_MARS_20_PILOT_RETURN")
            self.assertEqual(summary["arguments"]["expected_count"], 20)
            self.assertEqual(summary["arguments"]["expected_ports"], 8)
            self.assertEqual(summary["arguments"]["expected_frequency_stop_ghz"], 60.0)
            self.assertEqual(summary["arguments"]["expected_frequency_step_ghz"], 0.5)
            self.assertEqual(summary["arguments"]["expected_frequency_points"], 111)

    def test_imports_explicit_return_with_twenty_sample_contract(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tarball = root / "next_gen_s8p_mars_return_latest.tar.gz"
            tarball.write_bytes(b"fake tarball; subprocess is mocked")
            out_dir = root / "out"
            seen_commands: list[list[str]] = []

            def fake_run(command, **kwargs):
                seen_commands.append([str(item) for item in command])
                import_summary = out_dir / "return_import" / "next_gen_s8p_mars_return_import_summary.json"
                import_summary.parent.mkdir(parents=True, exist_ok=True)
                import_summary.write_text(
                    json.dumps(
                        {
                            "overall_status": "READY_FOR_LOCAL_NEXT_GATES",
                            "decision": "CONTINUE_LOCAL_S8P_HFSS_AND_REPORT_GATES",
                            "next_steps_result": {"generated": True, "script_path": str(root / "next.sh")},
                            "discovery_result": {
                                "summary": {
                                    "selected_candidate": {"run_dir": str(root / "imported_run")},
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return mod.subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
                status = mod.main(["--return-tarball", str(tarball), "--out-dir", str(out_dir)])

            self.assertEqual(status, 0)
            self.assertEqual(len(seen_commands), 1)
            command = seen_commands[0]
            self.assertIn("import_next_gen_s8p_mars_return_package.py", " ".join(command))
            for token in (
                "--expected-count",
                "20",
                "--expected-jobs",
                "8",
                "--expected-ports",
                "8",
                "--expected-frequency-start-ghz",
                "5",
                "--expected-frequency-stop-ghz",
                "60",
                "--expected-frequency-step-ghz",
                "0.5",
                "--expected-frequency-points",
                "111",
            ):
                self.assertIn(token, command)
            summary = json.loads((out_dir / "latest_s8p_20_pilot_return_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "READY_FOR_HFSS")
            self.assertEqual(summary["decision"], "RUN_AFTER_IMPORT_GATES_AND_HFSS_EXPORT")

    def test_runs_hfss_postrun_validation_when_results_are_provided(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tarball = root / "next_gen_s8p_mars_return_latest.tar.gz"
            tarball.write_bytes(b"fake tarball; subprocess is mocked")
            out_dir = root / "out"
            imported_run = root / "imported_run"
            hfss_results = root / "hfss_results"
            hfss_results.mkdir()
            seen_commands: list[list[str]] = []

            def fake_run(command, **kwargs):
                command_text = " ".join(str(item) for item in command)
                seen_commands.append([str(item) for item in command])
                if "import_next_gen_s8p_mars_return_package.py" in command_text:
                    import_summary = out_dir / "return_import" / "next_gen_s8p_mars_return_import_summary.json"
                    import_summary.parent.mkdir(parents=True, exist_ok=True)
                    import_summary.write_text(
                        json.dumps(
                            {
                                "overall_status": "READY_FOR_LOCAL_NEXT_GATES",
                                "decision": "CONTINUE_LOCAL_S8P_HFSS_AND_REPORT_GATES",
                                "discovery_result": {
                                    "summary": {
                                        "selected_candidate": {"run_dir": str(imported_run)},
                                    }
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                elif "run_s8p_hfss_postrun_validation_from_aedt_packet.py" in command_text:
                    postrun_summary = out_dir / "hfss_postrun_validation" / "s8p_hfss_postrun_validation_summary.json"
                    postrun_summary.parent.mkdir(parents=True, exist_ok=True)
                    postrun_summary.write_text(
                        json.dumps({"overall_status": "PASS", "decision": "ACCEPT_HFSS_EMX_S8P_MATCH"}),
                        encoding="utf-8",
                    )
                return mod.subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
                status = mod.main(
                    [
                        "--return-tarball",
                        str(tarball),
                        "--out-dir",
                        str(out_dir),
                        "--hfss-results-dir",
                        str(hfss_results),
                    ]
                )

            self.assertEqual(status, 0)
            self.assertEqual(len(seen_commands), 2)
            postrun_command = seen_commands[1]
            self.assertIn("run_s8p_hfss_postrun_validation_from_aedt_packet.py", " ".join(postrun_command))
            self.assertIn("--hfss-results-dir", postrun_command)
            self.assertIn(str(hfss_results.resolve()), postrun_command)
            self.assertIn("--max-percent-error", postrun_command)
            self.assertIn("10", postrun_command)
            summary = json.loads((out_dir / "latest_s8p_20_pilot_return_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_EMX_HFSS_S8P_20_PILOT_VALIDATION")
