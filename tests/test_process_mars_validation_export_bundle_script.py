from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_process_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "process_mars_validation_export_bundle.py"
    )
    spec = importlib.util.spec_from_file_location("process_mars_validation_export_bundle_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_shell(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)
    return path


def _write_python(path: Path, body: str = "raise SystemExit(0)\n") -> Path:
    path.write_text(body, encoding="utf-8")
    return path


class ProcessMarsValidationExportBundleScriptTest(TransformerToolboxTestBase):
    def test_dry_run_records_bundle_processing_order_and_environment(self) -> None:
        mod = _load_process_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = root / "mars_validation_export_latest.tar.gz"
            sha = root / "mars_validation_export_latest.tar.gz.sha256"
            bundle.write_bytes(b"not opened during dry-run")
            sha.write_text("0" * 64 + "  mars_validation_export_latest.tar.gz\n", encoding="utf-8")
            pull = _write_shell(root / "pull.sh", "exit 0\n")
            publisher = _write_python(root / "publish.py")
            builder = _write_python(root / "build_report.py")
            mod.DEFAULT_BUILDERS = [builder]
            report_dir = root / "report"
            local_root = root / "local"

            status = mod.main(
                [
                    str(bundle),
                    "--sha256-file",
                    str(sha),
                    "--local-root",
                    str(local_root),
                    "--report-dir",
                    str(report_dir),
                    "--python",
                    sys.executable,
                    "--pull-script",
                    str(pull),
                    "--wideband-publisher",
                    str(publisher),
                    "--dry-run",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((report_dir / "mars_validation_export_process_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertTrue(summary["dry_run"])
            self.assertEqual(
                [item["name"] for item in summary["commands"]],
                ["pull_replot_from_bundle", "publish_verified_wideband_results", "build_report"],
            )
            first_env = summary["commands"][0]["env"]
            self.assertEqual(first_env["MARS_EXPORT_BUNDLE"], str(bundle.resolve()))
            self.assertEqual(first_env["MARS_EXPORT_BUNDLE_SHA256"], str(sha.resolve()))
            self.assertEqual(first_env["LOCAL_ROOT"], str(local_root.resolve()))
            self.assertTrue((report_dir / "MARS_VALIDATION_EXPORT_PROCESS_REPORT.md").is_file())

    def test_failure_stops_before_later_steps_without_keep_going(self) -> None:
        mod = _load_process_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = root / "bundle.tar.gz"
            bundle.write_bytes(b"placeholder")
            pull = _write_shell(root / "pull.sh", "exit 7\n")
            publisher = _write_python(root / "publish.py")
            builder = _write_python(root / "build_report.py")
            mod.DEFAULT_BUILDERS = [builder]
            report_dir = root / "report"

            status = mod.main(
                [
                    str(bundle),
                    "--report-dir",
                    str(report_dir),
                    "--python",
                    sys.executable,
                    "--pull-script",
                    str(pull),
                    "--wideband-publisher",
                    str(publisher),
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((report_dir / "mars_validation_export_process_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual([item["name"] for item in summary["commands"]], ["pull_replot_from_bundle"])
            self.assertEqual(summary["commands"][0]["returncode"], 7)

    def test_keep_going_records_later_steps_but_keeps_overall_failure(self) -> None:
        mod = _load_process_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle = root / "bundle.tar.gz"
            bundle.write_bytes(b"placeholder")
            pull = _write_shell(root / "pull.sh", "echo pull failed >&2\nexit 4\n")
            publisher = _write_python(root / "publish.py")
            builder_a = _write_python(root / "build_a.py")
            builder_b = _write_python(root / "build_b.py")
            mod.DEFAULT_BUILDERS = [builder_a, builder_b]
            report_dir = root / "report"

            status = mod.main(
                [
                    str(bundle),
                    "--report-dir",
                    str(report_dir),
                    "--python",
                    sys.executable,
                    "--pull-script",
                    str(pull),
                    "--wideband-publisher",
                    str(publisher),
                    "--keep-going",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((report_dir / "mars_validation_export_process_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(
                [item["status"] for item in summary["commands"]],
                ["FAIL", "PASS", "PASS", "PASS"],
            )
            self.assertIn("pull failed", summary["commands"][0]["stderr_tail"])
