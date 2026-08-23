from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "watch_s8p_20_pilot_return_and_process.py"
    spec = importlib.util.spec_from_file_location("watch_s8p_20_pilot_return_and_process_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WatchS8p20PilotReturnAndProcessScriptTest(TransformerToolboxTestBase):
    def test_waits_when_no_return_package_is_found(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "out"

            status = mod.main(["--search-root", str(root / "empty"), "--out-dir", str(out_dir), "--timeout-seconds", "0"])

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "watch_s8p_20_pilot_return_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_RETURN")
            self.assertEqual(summary["decision"], "WAIT_FOR_MARS_20_PILOT_RETURN")
            self.assertEqual(summary["attempt_count"], 1)
            self.assertEqual(summary["selected_tarball"], "")

    def test_imports_found_return_package_with_twenty_pilot_importer(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tarball = root / "next_gen_s8p_mars_return_latest.tar.gz"
            tarball.write_bytes(b"fake tarball; importer subprocess is mocked")
            out_dir = root / "out"
            seen_commands: list[list[str]] = []

            def fake_run(command, **kwargs):
                seen_commands.append([str(item) for item in command])
                import_summary = out_dir / "latest_return_import" / "latest_s8p_20_pilot_return_import_summary.json"
                import_summary.parent.mkdir(parents=True, exist_ok=True)
                import_summary.write_text(
                    json.dumps(
                        {
                            "overall_status": "READY_FOR_HFSS",
                            "decision": "RUN_AFTER_IMPORT_GATES_AND_HFSS_EXPORT",
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
            self.assertIn("import_latest_s8p_20_pilot_return.py", " ".join(command))
            self.assertIn("--return-tarball", command)
            self.assertIn(str(tarball.resolve()), command)
            self.assertIn("--max-percent-error", command)
            self.assertIn("10", command)
            summary = json.loads((out_dir / "watch_s8p_20_pilot_return_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "READY_FOR_HFSS")
            self.assertEqual(summary["decision"], "RUN_AFTER_IMPORT_GATES_AND_HFSS_EXPORT")
            self.assertEqual(summary["selected_tarball"], str(tarball.resolve()))

    def test_forwards_hfss_results_and_after_import_flags(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tarball = root / "next_gen_s8p_mars_return_20_after_unlock_20260626.tar.gz"
            tarball.write_bytes(b"fake tarball; importer subprocess is mocked")
            hfss_results = root / "hfss"
            hfss_results.mkdir()
            out_dir = root / "out"
            seen_commands: list[list[str]] = []

            def fake_run(command, **kwargs):
                seen_commands.append([str(item) for item in command])
                import_summary = out_dir / "latest_return_import" / "latest_s8p_20_pilot_return_import_summary.json"
                import_summary.parent.mkdir(parents=True, exist_ok=True)
                import_summary.write_text(
                    json.dumps({"overall_status": "PASS", "decision": "ACCEPT_EMX_HFSS_S8P_20_PILOT_VALIDATION"}),
                    encoding="utf-8",
                )
                return mod.subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
                status = mod.main(
                    [
                        "--search-root",
                        str(root),
                        "--out-dir",
                        str(out_dir),
                        "--run-after-import",
                        "--hfss-results-dir",
                        str(hfss_results),
                    ]
                )

            self.assertEqual(status, 0)
            command = seen_commands[0]
            self.assertIn("--run-after-import", command)
            self.assertIn("--hfss-results-dir", command)
            self.assertIn(str(hfss_results.resolve()), command)
            summary = json.loads((out_dir / "watch_s8p_20_pilot_return_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_EMX_HFSS_S8P_20_PILOT_VALIDATION")
