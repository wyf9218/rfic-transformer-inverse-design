from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import sys
import zipfile


def _load_clean_zip_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_clean_delivery_zip.py"
    spec = importlib.util.spec_from_file_location("build_clean_delivery_zip_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BuildCleanDeliveryZipScriptTest(TransformerToolboxTestBase):
    def test_build_clean_zip_writes_manifest_hash_and_omits_macos_metadata(self) -> None:
        clean_zip = _load_clean_zip_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "package"
            package.mkdir()
            (package / "README.md").write_text("delivery\n", encoding="utf-8")
            nested_sha = package / "nested" / "SHA256SUMS.txt"
            nested_sha.parent.mkdir()
            nested_sha.write_text("nested checksum\n", encoding="utf-8")
            (package / ".DS_Store").write_text("metadata\n", encoding="utf-8")
            metadata_dir = package / "__MACOSX" / "package"
            metadata_dir.mkdir(parents=True)
            (metadata_dir / "._README.md").write_text("fork\n", encoding="utf-8")
            cache_dir = package / "validation_scripts" / "__pycache__"
            cache_dir.mkdir(parents=True)
            cache_file = cache_dir / "tool.cpython-312.pyc"
            cache_file.write_bytes(b"bytecode")
            zip_path = root / "package.zip"
            zip_sha = root / "package.zip.sha256"
            out_json = root / "summary.json"

            status = clean_zip.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--out-json",
                    str(out_json),
                ]
            )

            self.assertEqual(status, 0)
            self.assertTrue((package / "SHA256SUMS.txt").exists())
            manifest_text = (package / "SHA256SUMS.txt").read_text(encoding="utf-8")
            self.assertIn("README.md", manifest_text)
            self.assertNotIn(".DS_Store", manifest_text)
            self.assertNotIn("nested/SHA256SUMS.txt", manifest_text)
            self.assertNotIn("__pycache__", manifest_text)
            summary = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertGreaterEqual(summary["pruned_metadata_count"], 2)
            self.assertFalse((package / ".DS_Store").exists())
            self.assertFalse((package / "__MACOSX").exists())
            self.assertFalse(cache_file.exists())
            self.assertEqual(zip_sha.read_text(encoding="utf-8").split()[0], _sha256(zip_path))
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
            self.assertIn("package/README.md", names)
            self.assertIn("package/SHA256SUMS.txt", names)
            self.assertIn("package/nested/SHA256SUMS.txt", names)
            self.assertFalse(any(name.startswith("__MACOSX/") or "/.DS_Store" in name for name in names))
            self.assertFalse(any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names))

    def test_build_clean_zip_is_deterministic_for_unchanged_package(self) -> None:
        clean_zip = _load_clean_zip_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "package"
            package.mkdir()
            (package / "a.txt").write_text("a\n", encoding="utf-8")
            zip_path = root / "package.zip"
            zip_sha = root / "package.zip.sha256"

            self.assertEqual(
                clean_zip.main(["--package-dir", str(package), "--zip-path", str(zip_path), "--zip-sha-record", str(zip_sha)]),
                0,
            )
            first_hash = _sha256(zip_path)
            self.assertEqual(
                clean_zip.main(["--package-dir", str(package), "--zip-path", str(zip_path), "--zip-sha-record", str(zip_sha)]),
                0,
            )
            self.assertEqual(_sha256(zip_path), first_hash)

    def test_syncs_validation_scripts_from_project_source(self) -> None:
        clean_zip = _load_clean_zip_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "project"
            source_scripts = project / "rfic-transformer-inverse-design" / "scripts"
            source_scripts.mkdir(parents=True)
            (source_scripts / "verify_mars_dataset_package.py").write_text("NEW_FLAG = '--require-clearance-audit'\n", encoding="utf-8")
            (source_scripts / "audit_delivery_package.py").write_text("VALUE = 2\n", encoding="utf-8")
            package = root / "package"
            validation_scripts = package / "validation_scripts"
            validation_scripts.mkdir(parents=True)
            (validation_scripts / "verify_mars_dataset_package.py").write_text("OLD = True\n", encoding="utf-8")
            (validation_scripts / "stale.py").write_text("STALE = True\n", encoding="utf-8")
            preserved_dir = validation_scripts / "rfic_transformer_inverse_design"
            preserved_dir.mkdir()
            (preserved_dir / "__init__.py").write_text("", encoding="utf-8")
            zip_path = root / "package.zip"
            zip_sha = root / "package.zip.sha256"

            status = clean_zip.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--project-root",
                    str(project),
                ]
            )

            self.assertEqual(status, 0)
            self.assertIn("--require-clearance-audit", (validation_scripts / "verify_mars_dataset_package.py").read_text(encoding="utf-8"))
            self.assertTrue((validation_scripts / "audit_delivery_package.py").exists())
            self.assertFalse((validation_scripts / "stale.py").exists())
            self.assertTrue((preserved_dir / "__init__.py").exists())

    def test_syncs_hfss_validation_evidence_dirs_from_project_source(self) -> None:
        clean_zip = _load_clean_zip_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "project"
            project.mkdir()
            (project / "EMX_FIRST_CURRENT_DECISION_20260613_CN.md").write_text("current decision\n", encoding="utf-8")
            (project / "MORNING_STATUS_20260614_CN.md").write_text("latest status\n", encoding="utf-8")
            validation = project / "hfss_validation" / "final500_ec6698dfc575950b"
            geometry_source = validation / "geometry_quality_audit_final500_selected_20260613"
            geometry_source.mkdir(parents=True)
            (geometry_source / "geometry_quality_audit_summary.json").write_text(
                json.dumps(
                    {
                        "checks": [
                            {
                                "status": "PASS",
                                "name": "layout grounded labels",
                                "detail": "new grounded evidence",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            gate_source = validation / "emx_first_validation_gate_20260613"
            gate_source.mkdir(parents=True)
            (gate_source / "emx_first_validation_gate_summary.json").write_text(
                json.dumps(
                    {
                        "checks": [
                            {
                                "status": "PASS",
                                "name": "basic numeric physics sanity",
                                "detail": "new evidence",
                            },
                            {
                                "status": "PASS",
                                "name": "physical metric window",
                                "detail": "new evidence",
                            },
                            {
                                "status": "PASS",
                                "name": "smooth transformer metric window",
                                "detail": "new evidence",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            wideband_source = validation / "target_emx_wideband_rerun_20260613"
            wideband_source.mkdir()
            (wideband_source / "target_emx_postrun_validation_command.sh").write_text("new command\n", encoding="utf-8")
            package = root / "package"
            old_geometry = package / "geometry_quality_audit_final500_selected_20260613"
            old_geometry.mkdir(parents=True)
            (old_geometry / "geometry_quality_audit_summary.json").write_text(
                json.dumps({"checks": [{"status": "PASS", "name": "layout port count", "detail": "old"}]}),
                encoding="utf-8",
            )
            old_gate = package / "emx_first_validation_gate_20260613"
            old_gate.mkdir(parents=True)
            (old_gate / "emx_first_validation_gate_summary.json").write_text(
                json.dumps({"checks": [{"status": "PASS", "name": "target transformer sanity", "detail": "old"}]}),
                encoding="utf-8",
            )
            zip_path = root / "package.zip"
            zip_sha = root / "package.zip.sha256"

            status = clean_zip.main(
                [
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(zip_path),
                    "--zip-sha-record",
                    str(zip_sha),
                    "--project-root",
                    str(project),
                ]
            )

            self.assertEqual(status, 0)
            self.assertEqual((package / "EMX_FIRST_CURRENT_DECISION_20260613_CN.md").read_text(encoding="utf-8"), "current decision\n")
            self.assertEqual((package / "MORNING_STATUS_20260614_CN.md").read_text(encoding="utf-8"), "latest status\n")
            synced_geometry = json.loads(
                (package / "geometry_quality_audit_final500_selected_20260613" / "geometry_quality_audit_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            geometry_check_names = {check["name"] for check in synced_geometry["checks"]}
            self.assertIn("layout grounded labels", geometry_check_names)
            self.assertNotIn("layout port count", geometry_check_names)
            synced = json.loads((package / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json").read_text(encoding="utf-8"))
            check_names = {check["name"] for check in synced["checks"]}
            self.assertIn("basic numeric physics sanity", check_names)
            self.assertIn("physical metric window", check_names)
            self.assertIn("smooth transformer metric window", check_names)
            self.assertNotIn("target transformer sanity", check_names)
            self.assertEqual(
                (package / "target_emx_wideband_rerun_20260613" / "target_emx_postrun_validation_command.sh").read_text(
                    encoding="utf-8"
                ),
                "new command\n",
            )
