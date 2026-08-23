from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys
import tarfile


def _load_process_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "process_targeted_dataset_bundle.py"
    )
    spec = importlib.util.spec_from_file_location("process_targeted_dataset_bundle_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_python(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _write_fake_dataset_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "dataset_rows.csv").write_text(
        "sample_id,emx_status,touchstone_path\n"
        "sample_000,PASS,/tmp/sample_000.s4p\n",
        encoding="utf-8",
    )
    (run_dir / "dataset_manifest.json").write_text(
        json.dumps({"count": 1, "source": "unit-test"}, indent=2),
        encoding="utf-8",
    )
    audit_dir = run_dir / "dataset_quality_gates_20260615" / "zin_coverage_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "zin_coverage_audit_summary.json").write_text(
        json.dumps({"status": "PASS", "sample_count": 1}, indent=2),
        encoding="utf-8",
    )


def _make_bundle(bundle: Path, source_dir: Path) -> None:
    with tarfile.open(bundle, "w:gz") as tar:
        tar.add(source_dir, arcname=source_dir.name)


def _fake_package_verifier(path: Path, exit_code: int = 0) -> Path:
    return _write_python(
        path,
        "import sys\n"
        "print('fake package verifier')\n"
        f"raise SystemExit({exit_code})\n",
    )


def _fake_zin_publisher(path: Path) -> Path:
    return _write_python(
        path,
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        "report_dir = Path(sys.argv[sys.argv.index('--report-dir') + 1])\n"
        "audit_dir = Path(sys.argv[sys.argv.index('--audit-dir') + 1])\n"
        "report_dir.mkdir(parents=True, exist_ok=True)\n"
        "manifest = {'status': 'ZIN_UNIFORMITY_VERIFIED_PASS', 'audit_dir': str(audit_dir), 'published_assets': {}}\n"
        "(report_dir / 'zin_uniformity_verified_result_manifest_20260614.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')\n"
        "print('fake zin publisher')\n",
    )


def _fake_random_sample_preparer(path: Path) -> Path:
    return _write_python(
        path,
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        "out_dir = Path(sys.argv[sys.argv.index('--out-dir') + 1])\n"
        "dataset_dir = Path(sys.argv[sys.argv.index('--dataset-dir') + 1])\n"
        "out_dir.mkdir(parents=True, exist_ok=True)\n"
        "summary = {'status': 'PASS', 'decision': 'RANDOM_TARGETED_EMX_SAMPLE_READY_FOR_HFSS', 'dataset_dir': str(dataset_dir), 'sample_id': 'sample_000'}\n"
        "(out_dir / 'random_targeted_hfss_validation_sample_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')\n"
        "print('fake random sample preparer')\n",
    )


class ProcessTargetedDatasetBundleScriptTest(TransformerToolboxTestBase):
    def test_dry_run_records_strict_processing_commands(self) -> None:
        mod = _load_process_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_verifier = root / "fake_verifier.py"
            fake_publisher = root / "fake_publisher.py"
            fake_random_sample = root / "fake_random_sample.py"
            builder = root / "builder.py"
            _fake_package_verifier(fake_verifier)
            _fake_zin_publisher(fake_publisher)
            _fake_random_sample_preparer(fake_random_sample)
            _write_python(builder, "print('builder')\n")
            mod.DEFAULT_BUILDERS = [builder]

            report_dir = root / "report"
            status = mod.main(
                [
                    str(root / "targeted_dataset.tar.gz"),
                    "--report-dir",
                    str(report_dir),
                    "--python",
                    sys.executable,
                    "--package-verifier",
                    str(fake_verifier),
                    "--zin-uniformity-publisher",
                    str(fake_publisher),
                    "--random-sample-preparer",
                    str(fake_random_sample),
                    "--dry-run",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((report_dir / "targeted_dataset_bundle_process_summary_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "DRY_RUN")
            self.assertEqual(
                [item["name"] for item in summary["commands"]],
                [
                    "verify_targeted_dataset_package",
                    "publish_verified_zin_uniformity_result",
                    "prepare_random_targeted_hfss_validation_sample",
                    "builder",
                ],
            )
            verifier_cmd = summary["commands"][0]["cmd"]
            self.assertIn("--require-quality-gates", verifier_cmd)
            self.assertIn("--require-quality-figures", verifier_cmd)
            self.assertIn("--require-progress-evidence", verifier_cmd)
            self.assertTrue((report_dir / "TARGETED_DATASET_BUNDLE_PROCESS_20260615_CN.md").is_file())

    def test_processes_bundle_and_publishes_zin_uniformity_manifest(self) -> None:
        mod = _load_process_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "mars_reachable_queue_emx_dataset_20260615"
            _write_fake_dataset_run(run_dir)
            bundle = root / "mars_reachable_queue_emx_dataset_latest.tar.gz"
            _make_bundle(bundle, run_dir)
            fake_verifier = _fake_package_verifier(root / "fake_verifier.py")
            fake_publisher = _fake_zin_publisher(root / "fake_publisher.py")
            fake_random_sample = _fake_random_sample_preparer(root / "fake_random_sample.py")
            report_dir = root / "report"
            local_dataset_dir = root / "local_dataset"

            status = mod.main(
                [
                    str(bundle),
                    "--out-dir",
                    str(root / "unpacked"),
                    "--local-dataset-dir",
                    str(local_dataset_dir),
                    "--report-dir",
                    str(report_dir),
                    "--python",
                    sys.executable,
                    "--package-verifier",
                    str(fake_verifier),
                    "--zin-uniformity-publisher",
                    str(fake_publisher),
                    "--random-sample-preparer",
                    str(fake_random_sample),
                    "--skip-report-rebuild",
                ]
            )

            self.assertEqual(status, 0)
            self.assertTrue((local_dataset_dir / "dataset_rows.csv").is_file())
            self.assertTrue((local_dataset_dir / "dataset_manifest.json").is_file())
            process_summary = json.loads((report_dir / "targeted_dataset_bundle_process_summary_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(process_summary["overall_status"], "PASS")
            self.assertTrue(process_summary["zin_audit_dir"].endswith("dataset_quality_gates_20260615/zin_coverage_audit"))
            publisher_manifest = json.loads((report_dir / "zin_uniformity_verified_result_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(publisher_manifest["status"], "ZIN_UNIFORMITY_VERIFIED_PASS")
            random_summary = json.loads(
                (
                    report_dir
                    / "random_targeted_hfss_validation_sample_20260615"
                    / "random_targeted_hfss_validation_sample_summary.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(random_summary["decision"], "RANDOM_TARGETED_EMX_SAMPLE_READY_FOR_HFSS")

    def test_rejects_unsafe_tar_path_traversal(self) -> None:
        mod = _load_process_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = root / "payload.txt"
            payload.write_text("unsafe", encoding="utf-8")
            bundle = root / "unsafe.tar.gz"
            with tarfile.open(bundle, "w:gz") as tar:
                tar.add(payload, arcname="../escape.txt")
            fake_verifier = _fake_package_verifier(root / "fake_verifier.py")
            report_dir = root / "report"

            status = mod.main(
                [
                    str(bundle),
                    "--out-dir",
                    str(root / "unpacked"),
                    "--local-dataset-dir",
                    str(root / "local_dataset"),
                    "--report-dir",
                    str(report_dir),
                    "--python",
                    sys.executable,
                    "--package-verifier",
                    str(fake_verifier),
                    "--skip-zin-uniformity-publisher",
                    "--skip-report-rebuild",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            process_summary = json.loads((report_dir / "targeted_dataset_bundle_process_summary_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(process_summary["overall_status"], "FAIL")
            failed = {check["name"] for check in process_summary["checks"] if not check["pass"]}
            self.assertIn("safe_bundle_extraction", failed)
