from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import io
import shutil
import sys
import tarfile
import warnings
from unittest import mock


def _load_package_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "package_mars_dataset_run.py"
    spec = importlib.util.spec_from_file_location("package_mars_dataset_run_script_for_verify", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_verify_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "verify_mars_dataset_package.py"
    spec = importlib.util.spec_from_file_location("verify_mars_dataset_package_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class VerifyMarsDatasetPackageScriptTest(TransformerToolboxTestBase):
    def test_verifies_package_inventory_and_progress_audit(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--run-progress-audit",
                    "--expected-count",
                    "1",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                    "--require-clearance-audit",
                    "--require-emx-command",
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                    "--require-progress-evidence",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["inventory_category_counts"]["touchstone_files"], 1)
            report = (root / "verify" / "mars_dataset_package_verify_report.md").read_text(encoding="utf-8")
            self.assertIn("## Inventory Category Counts", report)
            self.assertIn("`touchstone_files`", report)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["inventory file hashes"]["status"], "PASS")
            self.assertEqual(checks["inventory non-empty files"]["status"], "PASS")
            self.assertEqual(checks["inventory Markdown report"]["status"], "PASS")
            self.assertEqual(checks["tar duplicate member hygiene"]["status"], "PASS")
            self.assertEqual(checks["tar inventory exactness"]["status"], "PASS")
            self.assertEqual(checks["inventory category counts"]["status"], "PASS")
            self.assertEqual(checks["inventory expected-count evidence"]["status"], "PASS")
            self.assertEqual(checks["inventory clearance-audit evidence"]["status"], "PASS")
            self.assertEqual(checks["extracted run progress audit"]["status"], "PASS")
            self.assertEqual(checks["packaged run progress evidence"]["status"], "PASS")

    def test_verifies_next_gen_s8p_package_with_8_port_touchstone_contract(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root, ports=8, suffix=".s8p")
            tarball = root / "dataset500_s8p_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--run-progress-audit",
                    "--expected-count",
                    "1",
                    "--expected-touchstone-ports",
                    "8",
                    "--required-touchstone-extension",
                    ".s8p",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                    "--require-emx-command",
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["progress audit contract"]["status"], "PASS")
            self.assertEqual(checks["extracted run progress audit"]["status"], "PASS")

    def test_extracts_package_when_tarfile_filter_argument_is_unavailable(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)
            original_extractall = tarfile.TarFile.extractall
            calls: list[object] = []

            def fake_extractall(self, path=".", members=None, *, numeric_owner=False, filter=None):
                calls.append(filter)
                if filter is not None:
                    raise TypeError("extractall() got an unexpected keyword argument 'filter'")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    return original_extractall(self, path, members, numeric_owner=numeric_owner)

            with mock.patch.object(tarfile.TarFile, "extractall", fake_extractall):
                status = verify.main([str(tarball), "--out-dir", str(root / "verify")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(calls.count("data"), 1)
            self.assertEqual(calls.count(None), 1)

    def test_fails_when_packaged_touchstone_is_empty(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)
            _replace_tar_member_and_refresh_records(
                tarball,
                root / "dataset500_minimal.tar.gz.inventory.json",
                root / "dataset500_minimal.tar.gz.sha256",
                "dataset500/evaluations/abc/emx/emx.s4p",
                b"",
            )

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["inventory non-empty files"]["status"], "FAIL")
            self.assertIn("emx.s4p", checks["inventory non-empty files"]["detail"])

    def test_required_clearance_audit_fails_extracted_progress_audit_when_missing(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            (run / "final500_ground_clearance_audit.json").unlink()
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--run-progress-audit",
                    "--expected-count",
                    "1",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                    "--require-clearance-audit",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["inventory clearance-audit evidence"]["status"], "FAIL")
            self.assertIn("final500_ground_clearance_audit.json", checks["inventory clearance-audit evidence"]["detail"])
            self.assertEqual(checks["extracted run progress audit"]["status"], "FAIL")
            self.assertIn("raw clearance audit file", checks["extracted run progress audit"]["detail"])

    def test_fails_when_inventory_file_hash_does_not_match(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)
            inventory_path = root / "dataset500_minimal.tar.gz.inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory["files"][0]["sha256"] = "0" * 64
            inventory_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["inventory file hashes"]["status"], "FAIL")

    def test_fails_when_inventory_category_counts_do_not_match(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)
            inventory_path = root / "dataset500_minimal.tar.gz.inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory["category_counts"]["touchstone_files"] = 99
            inventory_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["inventory category counts"]["status"], "FAIL")
            self.assertIn("touchstone_files", checks["inventory category counts"]["detail"])

    def test_fails_when_inventory_markdown_report_is_missing(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)
            (root / "dataset500_minimal.tar.gz.inventory.md").unlink()

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["inventory Markdown report"]["status"], "FAIL")
            self.assertIn("missing", checks["inventory Markdown report"]["detail"])

    def test_fails_when_inventory_markdown_report_is_inconsistent(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)
            report_path = root / "dataset500_minimal.tar.gz.inventory.md"
            report_path.write_text(report_path.read_text(encoding="utf-8").replace("| `touchstone_files` | 1 |", ""), encoding="utf-8")

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["inventory Markdown report"]["status"], "FAIL")
            self.assertIn("touchstone_files", checks["inventory Markdown report"]["detail"])

    def test_fails_when_expected_count_exceeds_inventory_evidence(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--run-progress-audit",
                    "--expected-count",
                    "2",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["inventory expected-count evidence"]["status"], "FAIL")
            self.assertIn("touchstone_files=1 < 2", checks["inventory expected-count evidence"]["detail"])

    def test_fails_when_tar_contains_metadata_or_cache_member(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)
            _append_extra_tar_member_and_refresh_records(
                tarball,
                root / "dataset500_minimal.tar.gz.inventory.json",
                root / "dataset500_minimal.tar.gz.sha256",
                "dataset500/__pycache__/stale.cpython-312.pyc",
            )

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["tar metadata/cache hygiene"]["status"], "FAIL")
            self.assertIn("__pycache__", checks["tar metadata/cache hygiene"]["detail"])

    def test_fails_when_tar_contains_uninventoried_regular_file(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)
            _append_extra_tar_member_and_refresh_records(
                tarball,
                root / "dataset500_minimal.tar.gz.inventory.json",
                root / "dataset500_minimal.tar.gz.sha256",
                "dataset500/uninventoried_note.txt",
            )

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["tar inventory exactness"]["status"], "FAIL")
            self.assertIn("uninventoried_note.txt", checks["tar inventory exactness"]["detail"])

    def test_fails_when_tar_contains_duplicate_regular_file_member(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)
            _append_extra_tar_member_and_refresh_records(
                tarball,
                root / "dataset500_minimal.tar.gz.inventory.json",
                root / "dataset500_minimal.tar.gz.sha256",
                "dataset500/dataset_manifest.json",
                payload=(run / "dataset_manifest.json").read_bytes(),
            )

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["tar duplicate member hygiene"]["status"], "FAIL")
            self.assertIn("dataset_manifest.json", checks["tar duplicate member hygiene"]["detail"])

    def test_missing_tarball_writes_fail_summary(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            missing = root / "missing.tar.gz"

            status = verify.main([str(missing), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["tarball exists"]["status"], "FAIL")

    def test_expected_constraints_require_run_progress_audit(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--expected-count",
                    "1",
                    "--expected-pin-purpose",
                    "51",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["progress audit contract"]["status"], "FAIL")
            self.assertIn("--run-progress-audit", checks["progress audit contract"]["detail"])
            self.assertIn("expected_pin_purpose", checks["progress audit contract"]["detail"])

    def test_require_quality_gates_passes_when_packaged_summary_passes(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            _write_quality_gates_summary(run, overall_status="PASS", step_status="PASS")
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--require-quality-gates"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged dataset quality gates"]["status"], "PASS")
            self.assertIn("required clearance audit", checks["packaged dataset quality gates"]["detail"])

    def test_require_quality_gates_fails_when_clearance_contract_missing(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            _write_quality_gates_summary(
                run,
                overall_status="PASS",
                step_status="PASS",
                require_clearance_contract=False,
            )
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--require-quality-gates",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged dataset quality gates"]["status"], "FAIL")
            self.assertIn("require_clearance_audit", checks["packaged dataset quality gates"]["detail"])

    def test_require_quality_gates_fails_when_raw_clearance_audit_missing(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            (run / "final500_ground_clearance_audit.json").unlink()
            _write_quality_gates_summary(run, overall_status="PASS", step_status="PASS")
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--require-quality-gates",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged dataset quality gates"]["status"], "FAIL")
            self.assertIn("final500_ground_clearance_audit.json", checks["packaged dataset quality gates"]["detail"])

    def test_require_quality_gates_fails_when_summary_missing(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--require-quality-gates",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged dataset quality gates"]["status"], "FAIL")
            self.assertIn("missing", checks["packaged dataset quality gates"]["detail"])

    def test_require_s8p_quality_gates_passes_when_scalar_q_and_validation_sample_are_packaged(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root, ports=8, suffix=".s8p")
            _write_s8p_quality_gates_summary(run)
            tarball = root / "dataset500_s8p_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--require-s8p-quality-gates"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged S8P physical-feature quality gates"]["status"], "PASS")
            self.assertIn("scalar-Q", checks["packaged S8P physical-feature quality gates"]["detail"])

    def test_require_s8p_quality_gates_fails_when_scalar_q_summary_is_missing(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root, ports=8, suffix=".s8p")
            _write_s8p_quality_gates_summary(run)
            (run / "dataset_quality_gates_s8p_physical_feature" / "scalar_q_feature_dataset" / "scalar_q_feature_summary.json").unlink()
            tarball = root / "dataset500_s8p_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--require-s8p-quality-gates",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged S8P physical-feature quality gates"]["status"], "FAIL")
            self.assertIn("scalar-Q summary", checks["packaged S8P physical-feature quality gates"]["detail"])

    def test_require_next_gen_s8p_status_passes_when_run_and_objective_summaries_are_packaged(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root, ports=8, suffix=".s8p")
            _write_next_gen_status_evidence(run)
            tarball = root / "dataset500_s8p_status.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--require-next-gen-s8p-status"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["inventory_category_counts"]["next_gen_run_status_summary_files"], 1)
            self.assertEqual(summary["inventory_category_counts"]["objective_acceptance_summary_files"], 1)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged next-gen S8P status evidence"]["status"], "PASS")
            self.assertIn("WAITING_FOR_HFSS_EXPORT", checks["packaged next-gen S8P status evidence"]["detail"])

    def test_require_next_gen_s8p_status_fails_when_objective_acceptance_is_missing(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root, ports=8, suffix=".s8p")
            _write_next_gen_status_evidence(run)
            shutil.rmtree(run / "next_gen_s8p_objective_acceptance")
            tarball = root / "dataset500_s8p_status.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--require-next-gen-s8p-status",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged next-gen S8P status evidence"]["status"], "FAIL")
            self.assertIn("missing next_gen_s8p_objective_acceptance_summary.json", checks["packaged next-gen S8P status evidence"]["detail"])

    def test_require_hfss_validation_assets_passes_when_packaged(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root, ports=8, suffix=".s8p")
            _write_hfss_validation_assets(run)
            tarball = root / "dataset500_s8p_hfss_assets.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball), "--include-hfss-validation-assets"]), 0)

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--require-hfss-validation-assets"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertGreater(summary["inventory_category_counts"]["hfss_validation_asset_files"], 0)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged HFSS validation assets"]["status"], "PASS")
            self.assertIn("hfss_validation_script_files=2", checks["packaged HFSS validation assets"]["detail"])

    def test_require_hfss_validation_assets_fails_when_not_packaged(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root, ports=8, suffix=".s8p")
            _write_hfss_validation_assets(run)
            tarball = root / "dataset500_s8p_hfss_assets.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--require-hfss-validation-assets",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged HFSS validation assets"]["status"], "FAIL")

    def test_require_run_config_passes_when_final_config_is_packaged(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            (run / "final_s8p_physical_feature_500.yaml").write_text("dataset:\n  count: 500\n", encoding="utf-8")
            tarball = root / "dataset500_run_config.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--require-run-config"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged final S8P run config"]["status"], "PASS")

    def test_require_run_config_fails_when_final_config_is_missing(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            tarball = root / "dataset500_missing_run_config.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--require-run-config", "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged final S8P run config"]["status"], "FAIL")

    def test_require_progress_evidence_fails_when_summary_missing(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            shutil.rmtree(run / "mars_run_progress_audit_20260613")
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--require-progress-evidence",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged run progress evidence"]["status"], "FAIL")

    def test_require_quality_figures_passes_when_packaged(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            _write_quality_gates_summary(run, overall_status="PASS", step_status="PASS", write_figure=True)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball), "--include-quality-figures"]), 0)

            status = verify.main([str(tarball), "--out-dir", str(root / "verify"), "--require-quality-figures"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged quality-gate figures"]["status"], "PASS")
            self.assertIn("quality_gate_figure_files=1", checks["packaged quality-gate figures"]["detail"])

    def test_require_quality_figures_fails_when_not_packaged(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            _write_quality_gates_summary(run, overall_status="PASS", step_status="PASS", write_figure=True)
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--require-quality-figures",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged quality-gate figures"]["status"], "FAIL")
            self.assertIn("quality_gate_figure_files=0", checks["packaged quality-gate figures"]["detail"])

    def test_require_quality_gates_fails_when_summary_not_pass(self) -> None:
        package = _load_package_module()
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_minimal_run(root)
            _write_quality_gates_summary(run, overall_status="FAIL", step_status="PASS")
            tarball = root / "dataset500_minimal.tar.gz"
            self.assertEqual(package.main([str(run), "--out", str(tarball)]), 0)

            status = verify.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "verify"),
                    "--require-quality-gates",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_dataset_package_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["packaged dataset quality gates"]["status"], "FAIL")
            self.assertIn("overall_status", checks["packaged dataset quality gates"]["detail"])


def _write_minimal_run(root: Path, *, ports: int = 4, suffix: str = ".s4p") -> Path:
    run = root / "runs" / "dataset500"
    sample = run / "evaluations" / "abc"
    (sample / "emx").mkdir(parents=True)
    (sample / "layout").mkdir(parents=True)
    freqs = np.asarray([5.0e9, 5.1e9, 5.2e9])
    s_matrix = np.zeros((3, ports, ports), dtype=np.complex128)
    for idx in range(ports):
        s_matrix[:, idx, idx] = 0.1
    touchstone_name = f"emx{suffix}"
    _write_touchstone(sample / "emx" / touchstone_name, freqs, s_matrix)
    (run / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "requested_count": 1,
                "ok_count": 1,
                "fail_count": 0,
                "target_frequency": {"start_hz": 5.0e9, "stop_hz": 5.2e9, "step_hz": 1.0e8, "points": 3},
            }
        ),
        encoding="utf-8",
    )
    (run / "dataset_rows.csv").write_text(
        f"sample_id,ok,touchstone_path\nabc,true,evaluations/abc/emx/{touchstone_name}\n",
        encoding="utf-8",
    )
    (run / "final500_ground_clearance_audit.json").write_text(
        json.dumps(
            {
                "candidate_count": 1,
                "pass_count": 1,
                "reject_count": 0,
                "missing_or_other_count": 0,
                "selected": {"cache_key": "abc", "status": "pass_signal_to_shield_clearance"},
                "records": [{"cache_key": "abc", "status": "pass_signal_to_shield_clearance"}],
            }
        ),
        encoding="utf-8",
    )
    (sample / "summary.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (sample / "emx" / "emx_command.json").write_text(json.dumps(_valid_emx_command(freqs, ports=ports, suffix=suffix)), encoding="utf-8")
    (sample / "layout" / "transformer_layout.layout.json").write_text("{}", encoding="utf-8")
    progress_dir = run / "mars_run_progress_audit_20260613"
    progress_dir.mkdir()
    (progress_dir / "mars_run_progress_summary.json").write_text(json.dumps({"overall_status": "PASS"}), encoding="utf-8")
    (progress_dir / "mars_run_progress_report.md").write_text("# progress\n", encoding="utf-8")
    (progress_dir / "mars_run_progress_rows.csv").write_text("key\nabc\n", encoding="utf-8")
    return run


def _valid_emx_command(freqs_hz: np.ndarray, *, ports: int = 4, suffix: str = ".s4p") -> list[str]:
    command = [
        "emx",
        "layout.gds",
        "TRANSFORMER",
        "proc.proc",
        "--touchstone",
        "--s-impedance=50",
        "-s",
        f"emx{suffix}",
        "--include-command-line",
        "--cadence-pins=51",
    ]
    for index in range(ports):
        name = f"P{index + 1:03d}"
        command.append(f"--port={name}={name}:GND")
    command.extend(str(float(freq)) for freq in freqs_hz)
    return command


def _write_quality_gates_summary(
    run: Path,
    *,
    overall_status: str,
    step_status: str,
    write_figure: bool = False,
    require_clearance_contract: bool = True,
) -> None:
    out_dir = run / "dataset_quality_gates"
    out_dir.mkdir(parents=True)
    geometry_command = [
        "python",
        "scripts/audit_geometry_quality.py",
        str(run),
        "--out-dir",
        str(out_dir / "geometry_quality_audit"),
    ]
    if require_clearance_contract:
        geometry_command.append("--require-clearance-audit")
    (out_dir / "dataset_quality_gates_summary.json").write_text(
        json.dumps(
            {
                "overall_status": overall_status,
                "arguments": {"require_clearance_audit": require_clearance_contract},
                "steps": [
                    {
                        "name": "geometry quality audit",
                        "status": step_status,
                        "returncode": 0 if step_status == "PASS" else 2,
                        "command": geometry_command,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    if write_figure:
        (out_dir / "sampling_distribution_uniform_vs_normal_ks.png").write_bytes(b"PNG")


def _write_s8p_quality_gates_summary(run: Path) -> None:
    out_dir = run / "dataset_quality_gates_s8p_physical_feature"
    out_dir.mkdir(parents=True)
    (out_dir / "dataset_quality_gates_summary.json").write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "steps": [
                    {"name": "S8P physical-feature dataset audit", "status": "PASS"},
                    {"name": "scalar Q feature derivation", "status": "PASS"},
                    {"name": "physical-feature validation sample selection", "status": "PASS"},
                ],
            }
        ),
        encoding="utf-8",
    )
    s8p_audit = out_dir / "s8p_physical_feature_dataset_audit"
    s8p_audit.mkdir()
    (s8p_audit / "s8p_physical_feature_dataset_audit_summary.json").write_text(
        json.dumps({"overall_status": "PASS"}),
        encoding="utf-8",
    )
    scalar = out_dir / "scalar_q_feature_dataset"
    scalar.mkdir()
    (scalar / "scalar_q_feature_summary.json").write_text(
        json.dumps({"overall_status": "PASS", "q_definition": "min", "output_column": "q_center"}),
        encoding="utf-8",
    )
    validation = out_dir / "physical_feature_validation_sample_selection"
    validation.mkdir()
    (validation / "physical_feature_validation_sample_summary.json").write_text(
        json.dumps({"overall_status": "PASS", "selected_count": 1}),
        encoding="utf-8",
    )
    (validation / "physical_feature_validation_samples.csv").write_text(
        "evaluation,selection_rank,touchstone_path\nabc,1,evaluations/abc/emx/emx.s8p\n",
        encoding="utf-8",
    )


def _write_next_gen_status_evidence(run: Path) -> None:
    status_dir = run / "next_gen_s8p_mars_run_status"
    status_dir.mkdir()
    (status_dir / "next_gen_s8p_mars_run_status_summary.json").write_text(
        json.dumps({"overall_status": "WAITING_FOR_HFSS_EXPORT", "decision": "RUN_HFSS_SOLVE_AND_EXPORT_S8P"}),
        encoding="utf-8",
    )
    (status_dir / "next_gen_s8p_mars_run_status_report.md").write_text("# run status\n", encoding="utf-8")
    (status_dir / "next_gen_s8p_mars_run_status_evidence.csv").write_text("status\nWAITING\n", encoding="utf-8")
    objective_dir = run / "next_gen_s8p_objective_acceptance"
    objective_dir.mkdir()
    (objective_dir / "next_gen_s8p_objective_acceptance_summary.json").write_text(
        json.dumps({"overall_status": "WAITING", "decision": "DO_NOT_CLAIM_NEXT_GEN_S8P_OBJECTIVE_COMPLETE"}),
        encoding="utf-8",
    )
    (objective_dir / "NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md").write_text("# objective\n", encoding="utf-8")
    (objective_dir / "next_gen_s8p_objective_acceptance_evidence.csv").write_text("status\nWAITING\n", encoding="utf-8")


def _write_hfss_validation_assets(run: Path) -> None:
    aedt = run / "dataset_quality_gates_s8p_physical_feature" / "selected_s8p_hfss_aedt_scripts"
    aedt.mkdir(parents=True)
    (aedt / "run_generated_hfss_s8p_scripts.commands.ps1").write_text("Write-Host ready\n", encoding="utf-8")
    (aedt / "build_hfss_s8p_from_payload.py").write_text("print('ready')\n", encoding="utf-8")
    (aedt / "source_geometry.gds").write_bytes(b"GDS")
    payload_views = run / "dataset_quality_gates_s8p_physical_feature" / "selected_s8p_hfss_payload_views"
    payload_views.mkdir()
    (payload_views / "hfss_payload_geometry_render_summary.json").write_text('{"overall_status":"PASS"}', encoding="utf-8")
    (payload_views / "top_view.png").write_bytes(b"PNG")
    postrun = run / "dataset_quality_gates_s8p_physical_feature" / "selected_s8p_hfss_postrun_validation"
    postrun.mkdir()
    (postrun / "hfss_exported.s8p").write_text("# GHz S RI R 50\n", encoding="utf-8")


def _append_extra_tar_member_and_refresh_records(
    tarball: Path,
    inventory_path: Path,
    sha_path: Path,
    extra_member_name: str,
    *,
    payload: bytes = b"generated bytecode",
) -> None:
    payloads: list[tuple[str, bytes]] = []
    with tarfile.open(tarball, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            payloads.append((member.name, extracted.read() if extracted else b""))
    payloads.append((extra_member_name, payload))
    with tarfile.open(tarball, "w:gz") as archive:
        for name, payload in payloads:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    tar_sha = _sha256(tarball)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["tarball_sha256"] = tar_sha
    inventory_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    sha_path.write_text(f"{tar_sha}  {tarball.name}\n", encoding="utf-8")


def _replace_tar_member_and_refresh_records(
    tarball: Path,
    inventory_path: Path,
    sha_path: Path,
    member_name: str,
    payload: bytes,
) -> None:
    payloads: list[tuple[str, bytes]] = []
    with tarfile.open(tarball, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            data = extracted.read() if extracted else b""
            payloads.append((member.name, payload if member.name == member_name else data))
    with tarfile.open(tarball, "w:gz") as archive:
        for name, data in payloads:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    tar_sha = _sha256(tarball)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["tarball_sha256"] = tar_sha
    for file_record in inventory["files"]:
        if file_record["relative_to_run_parent"] == member_name:
            file_record["size_bytes"] = len(payload)
            file_record["sha256"] = hashlib.sha256(payload).hexdigest()
    inventory_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    sha_path.write_text(f"{tar_sha}  {tarball.name}\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
