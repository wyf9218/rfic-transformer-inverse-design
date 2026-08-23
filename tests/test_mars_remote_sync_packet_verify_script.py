from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import sys
import tarfile


def _load_verify_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "verify_mars_remote_sync_packet.py"
    )
    spec = importlib.util.spec_from_file_location("verify_mars_remote_sync_packet_script", script_path)
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


def _make_packet(root: Path, missing: str | None = None) -> tuple[Path, Path, Path, Path, Path]:
    packet = root / "mars_remote_sync_packet_20260615"
    for rel in [
        "files/scripts/run_candidate_queue_dataset.py",
        "files/scripts/select_zin_targeted_candidate_geometries.py",
        "files/mars_visible_rerun_packet_20260614/PASTE_MARS_REACHABLE_CANDIDATE_SELECTION_20260615.sh",
        "files/mars_visible_rerun_packet_20260614/PASTE_MARS_RUN_REACHABLE_QUEUE_DATASET_20260615.sh",
    ]:
        path = packet / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n", encoding="utf-8")
    (packet / "README_CN.md").write_text("readme\n", encoding="utf-8")
    installer = packet / "INSTALL_ON_MARS.sh"
    installer.write_text("#!/usr/bin/env bash\necho install\n", encoding="utf-8")
    installer.chmod(0o755)
    if missing:
        (packet / missing).unlink()
    sha_lines = []
    for path in sorted(p for p in packet.rglob("*") if p.is_file()):
        rel = path.relative_to(packet)
        if rel == Path("SHA256SUMS.txt"):
            continue
        sha_lines.append(f"{_sha256(path)}  ./{rel.as_posix()}\n")
    (packet / "SHA256SUMS.txt").write_text("".join(sha_lines), encoding="utf-8")
    tar_path = root / "mars_remote_sync_packet_20260615.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(packet, arcname=packet.name)
    tar_sha = root / "mars_remote_sync_packet_20260615.tar.gz.sha256"
    tar_sha.write_text(f"{_sha256(tar_path)}  {tar_path.name}\n", encoding="utf-8")
    bootstrap = root / "mars_remote_sync_packet_20260615_BOOTSTRAP.sh"
    bootstrap.write_text(
        "#!/usr/bin/env bash\n"
        "base64 -d mars_remote_sync_packet_20260615.tar.gz.b64 > mars_remote_sync_packet_20260615.tar.gz\n"
        "tar -xzf mars_remote_sync_packet_20260615.tar.gz\n"
        "bash mars_remote_sync_packet_20260615/INSTALL_ON_MARS.sh\n",
        encoding="utf-8",
    )
    bootstrap.chmod(0o755)
    bootstrap_sha = root / "mars_remote_sync_packet_20260615_BOOTSTRAP.sh.sha256"
    bootstrap_sha.write_text(f"{_sha256(bootstrap)}  {bootstrap.name}\n", encoding="utf-8")
    return packet, tar_path, tar_sha, bootstrap, bootstrap_sha


class MarsRemoteSyncPacketVerifyScriptTest(TransformerToolboxTestBase):
    def test_valid_packet_passes(self) -> None:
        mod = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet, tar_path, tar_sha, bootstrap, bootstrap_sha = _make_packet(root)
            report = root / "report"

            status = mod.main(
                [
                    "--packet-dir",
                    str(packet),
                    "--tar-path",
                    str(tar_path),
                    "--tar-sha-path",
                    str(tar_sha),
                    "--bootstrap-path",
                    str(bootstrap),
                    "--bootstrap-sha-path",
                    str(bootstrap_sha),
                    "--report-dir",
                    str(report),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((report / "mars_remote_sync_packet_verify_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "PASS")

    def test_missing_required_file_fails(self) -> None:
        mod = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet, tar_path, tar_sha, bootstrap, bootstrap_sha = _make_packet(root, missing="files/scripts/run_candidate_queue_dataset.py")
            report = root / "report"

            status = mod.main(
                [
                    "--packet-dir",
                    str(packet),
                    "--tar-path",
                    str(tar_path),
                    "--tar-sha-path",
                    str(tar_sha),
                    "--bootstrap-path",
                    str(bootstrap),
                    "--bootstrap-sha-path",
                    str(bootstrap_sha),
                    "--report-dir",
                    str(report),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((report / "mars_remote_sync_packet_verify_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "FAIL")
            failed = {check["name"] for check in summary["checks"] if not check["pass"]}
            self.assertIn("packet_file_exists:files/scripts/run_candidate_queue_dataset.py", failed)
