from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import sys
import tarfile


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_mars_start_upload_bundle.py"
    spec = importlib.util.spec_from_file_location("audit_mars_start_upload_bundle_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_bundle(root: Path, *, bad_start_script: bool = False, bad_sync_sidecar: bool = False) -> tuple[Path, Path, str]:
    mod = _load_module()
    bundle_dir = root / "bundle"
    bundle_dir.mkdir()
    sync_tar = bundle_dir / mod.DEFAULT_SYNC_NAME
    sync_tar.write_bytes(b"fake sync tar")
    sync_digest = _sha256(sync_tar)
    sidecar_digest = "0" * 64 if bad_sync_sidecar else sync_digest
    (bundle_dir / f"{mod.DEFAULT_SYNC_NAME}.sha256").write_text(f"{sidecar_digest}  {mod.DEFAULT_SYNC_NAME}\n", encoding="utf-8")
    start_text = "\n".join(
        [
            "#!/usr/bin/env bash",
            f"PACKET_NAME={mod.DEFAULT_SYNC_NAME}",
            "locate_packet() { :; }",
            "SHA_PATH=x",
            "sha256sum -c x",
            "NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh",
            "RUN_REAL_EMX=1",
            "",
        ]
    )
    if bad_start_script:
        start_text = "#!/usr/bin/env bash\nsha256sum -c x\n"
    (bundle_dir / mod.DEFAULT_START_SCRIPT).write_text(start_text, encoding="utf-8")
    (bundle_dir / mod.DEFAULT_START_SCRIPT).chmod(0o755)
    (bundle_dir / mod.DEFAULT_README).write_text(
        "\n".join(
            [
                mod.DEFAULT_SYNC_NAME,
                mod.DEFAULT_START_SCRIPT,
                "bash NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh",
                "500",
                ".s8p",
            ]
        ),
        encoding="utf-8",
    )
    sha_lines = []
    for name in [mod.DEFAULT_SYNC_NAME, f"{mod.DEFAULT_SYNC_NAME}.sha256", mod.DEFAULT_START_SCRIPT, mod.DEFAULT_README]:
        sha_lines.append(f"{_sha256(bundle_dir / name)}  {name}\n")
    (bundle_dir / "SHA256SUMS.txt").write_text("".join(sha_lines), encoding="utf-8")
    bundle_tar = root / "bundle.tar.gz"
    with tarfile.open(bundle_tar, "w:gz") as tar:
        for path in sorted(bundle_dir.iterdir()):
            tar.add(path, arcname=path.name)
    bundle_sha = root / "bundle.tar.gz.sha256"
    bundle_sha.write_text(f"{_sha256(bundle_tar)}  {bundle_tar.name}\n", encoding="utf-8")
    return bundle_tar, bundle_sha, sync_digest


class AuditMarsStartUploadBundleScriptTest(TransformerToolboxTestBase):
    def test_accepts_complete_current_upload_bundle(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle, bundle_sha, sync_digest = _write_bundle(root)

            status = mod.main(
                [
                    "--bundle-tar",
                    str(bundle),
                    "--bundle-sha",
                    str(bundle_sha),
                    "--expected-sync-sha",
                    sync_digest,
                    "--out-dir",
                    str(root / "audit"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "mars_start_upload_bundle_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "MARS_START_UPLOAD_BUNDLE_READY")

    def test_rejects_bundle_with_bad_start_script_contract(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle, bundle_sha, sync_digest = _write_bundle(root, bad_start_script=True)

            status = mod.main(
                [
                    "--bundle-tar",
                    str(bundle),
                    "--bundle-sha",
                    str(bundle_sha),
                    "--expected-sync-sha",
                    sync_digest,
                    "--out-dir",
                    str(root / "audit"),
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "mars_start_upload_bundle_audit_summary.json").read_text(encoding="utf-8"))
            failed = [item["name"] for item in summary["checks"] if item["status"] == "FAIL"]
            self.assertIn(f"start script contains {mod.DEFAULT_SYNC_NAME}", failed)
            self.assertIn("start script contains NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh", failed)

    def test_rejects_bundle_with_bad_sync_sidecar(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle, bundle_sha, sync_digest = _write_bundle(root, bad_sync_sidecar=True)

            status = mod.main(
                [
                    "--bundle-tar",
                    str(bundle),
                    "--bundle-sha",
                    str(bundle_sha),
                    "--expected-sync-sha",
                    sync_digest,
                    "--out-dir",
                    str(root / "audit"),
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "mars_start_upload_bundle_audit_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["sync tar sha sidecar matches"]["status"], "FAIL")
