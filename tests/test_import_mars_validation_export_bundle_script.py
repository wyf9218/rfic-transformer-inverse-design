from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import sys
import tarfile


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xe2k\xb8"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _load_importer_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "import_mars_validation_export_bundle.py"
    spec = importlib.util.spec_from_file_location("import_mars_validation_export_bundle_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_bundle_fixture(root: Path, *, include_emx: bool = True, include_zin_uniformity: bool = True) -> tuple[Path, Path]:
    export = root / "mars_validation_export_20260614"
    plan = export / "zin_plan"
    plan.mkdir(parents=True)
    (plan / "zin_balanced_acquisition_plan_summary.json").write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "dataset_source": {"exists": True, "sha256": "c" * 64, "csv_row_count": 500, "ok_row_count": 500},
            }
        ),
        encoding="utf-8",
    )
    (plan / "zin_balanced_acquisition_plan_verification_summary.json").write_text(
        json.dumps({"overall_status": "PASS", "decision": "ACCEPT_ZIN_BALANCED_ACQUISITION_PLAN"}),
        encoding="utf-8",
    )
    (plan / "zin_balanced_acquisition_targets.csv").write_text("rank,recommended_new_samples\n1,5\n", encoding="utf-8")
    (plan / "zin_balanced_acquisition_bins.csv").write_text("real_bin,imag_bin,current_count,deficit\n0,0,0,1\n", encoding="utf-8")
    (plan / "01_zin_bin_deficit_heatmap.png").write_bytes(PNG_BYTES)
    (plan / "02_next_zin_targets_overlay.png").write_bytes(PNG_BYTES)
    if include_zin_uniformity:
        zin = export / "zin_uniformity_audit"
        zin.mkdir()
        (zin / "zin_coverage_audit_summary.json").write_text(
            json.dumps(
                {
                    "overall_status": "PASS",
                    "label_summary": {
                        "valid_count": 500,
                        "occupied_bins": {"real": 10, "imag": 10, "bins": 10},
                    },
                    "plots": [
                        {"status": "OK", "path": str(zin / "zin_center_scatter.png"), "title": "Center Zin scatter"},
                        {"status": "OK", "path": str(zin / "zin_center_histograms.png"), "title": "Center Zin histograms"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        (zin / "zin_coverage_bins.csv").write_text("real_bin,imag_bin,count,status\n0,0,5,covered\n", encoding="utf-8")
        (zin / "zin_center_scatter.png").write_bytes(PNG_BYTES)
        (zin / "zin_center_histograms.png").write_bytes(PNG_BYTES)
    if include_emx:
        (export / "ec6698dfc575950b_MARS_EMX_WIDEBAND_5_50GHz_step0p1.s4p").write_text(
            "! placeholder S4P; grid is validated by the later discovery gate\n",
            encoding="utf-8",
        )
    (export / "EXPORT_MANIFEST.txt").write_text("remote_emx_status=PRESENT\n", encoding="utf-8")
    bundle = root / "mars_validation_export_latest.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        tar.add(export, arcname=export.name)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    sha = root / "mars_validation_export_latest.tar.gz.sha256"
    sha.write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
    return bundle, sha


class ImportMarsValidationExportBundleScriptTest(TransformerToolboxTestBase):
    def test_imports_bundle_and_copies_known_artifacts(self) -> None:
        mod = _load_importer_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle, sha = _write_bundle_fixture(root)
            out = root / "unpacked"
            local_emx = root / "pull" / "emx.s4p"
            local_plan = root / "pull" / "plan"
            local_zin_uniformity = root / "pull" / "zin_uniformity"

            status = mod.main(
                [
                    str(bundle),
                    "--sha256-file",
                    str(sha),
                    "--out-dir",
                    str(out),
                    "--local-emx",
                    str(local_emx),
                    "--local-zin-plan-dir",
                    str(local_plan),
                    "--local-zin-uniformity-audit-dir",
                    str(local_zin_uniformity),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((out / "mars_validation_export_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_MARS_VALIDATION_EXPORT_BUNDLE")
            self.assertTrue(local_emx.is_file())
            self.assertTrue((local_plan / "zin_balanced_acquisition_plan_summary.json").is_file())
            self.assertTrue((local_zin_uniformity / "zin_coverage_audit_summary.json").is_file())
            self.assertEqual(summary["copied"]["emx_s4p"], str(local_emx.resolve()))
            self.assertEqual(summary["copied"]["zin_plan"], str(local_plan.resolve()))
            self.assertEqual(summary["copied"]["zin_uniformity_audit"], str(local_zin_uniformity.resolve()))

    def test_missing_zin_uniformity_audit_is_warn_unless_required(self) -> None:
        mod = _load_importer_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle, _sha = _write_bundle_fixture(root, include_zin_uniformity=False)
            out = root / "unpacked"

            status = mod.main([str(bundle), "--out-dir", str(out)])

            self.assertEqual(status, 0)
            summary = json.loads((out / "mars_validation_export_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            warning_names = {check["name"] for check in summary["checks"] if check["status"] == "WARN"}
            self.assertIn("Zin uniformity audit directory", warning_names)

            status = mod.main([str(bundle), "--out-dir", str(out), "--require-zin-uniformity-audit", "--no-fail-exit"])
            self.assertEqual(status, 0)
            summary = json.loads((out / "mars_validation_export_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")

    def test_missing_emx_is_warn_unless_required(self) -> None:
        mod = _load_importer_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle, _sha = _write_bundle_fixture(root, include_emx=False)
            out = root / "unpacked"

            status = mod.main([str(bundle), "--out-dir", str(out)])

            self.assertEqual(status, 0)
            summary = json.loads((out / "mars_validation_export_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            warning_names = {check["name"] for check in summary["checks"] if check["status"] == "WARN"}
            self.assertIn("wideband EMX S4P in bundle", warning_names)

            status = mod.main([str(bundle), "--out-dir", str(out), "--require-emx", "--no-fail-exit"])
            self.assertEqual(status, 0)
            summary = json.loads((out / "mars_validation_export_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")

    def test_rejects_tar_path_traversal(self) -> None:
        mod = _load_importer_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            evil = root / "evil.tar.gz"
            payload = root / "payload.txt"
            payload.write_text("bad", encoding="utf-8")
            with tarfile.open(evil, "w:gz") as tar:
                tar.add(payload, arcname="../evil.txt")

            status = mod.main([str(evil), "--out-dir", str(root / "out"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "mars_validation_export_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            failed = {check["name"] for check in summary["checks"] if check["status"] == "FAIL"}
            self.assertIn("safe bundle extraction", failed)
            self.assertFalse((root / "evil.txt").exists())
