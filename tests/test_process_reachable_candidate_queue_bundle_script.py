from tests.rfic_transformer_inverse_design.shared import *

import csv
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


def _load_process_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "process_reachable_candidate_queue_bundle.py"
    )
    spec = importlib.util.spec_from_file_location("process_reachable_candidate_queue_bundle_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_queue(root: Path, selected: int = 5) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "reachable_zin_targeted_candidate_selection.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["selection_rank", "candidate_id", "inside_target_bin"])
        writer.writeheader()
        for idx in range(selected):
            writer.writerow({"selection_rank": idx + 1, "candidate_id": f"cand_{idx}", "inside_target_bin": "True"})
    summary = {
        "overall_status": "PASS",
        "candidate_source": {"path": str(root.parent / "candidate_zin_predictions.csv"), "sha256": "a" * 64},
        "targets_source": {"path": str(root.parent / "zin_balanced_acquisition_targets.csv"), "sha256": "b" * 64},
        "selected_count": selected,
        "effective_requested_candidate_count": selected,
        "selected_inside_target_bin_count": selected,
        "reachable_target_count": selected,
        "unreachable_target_count": 0,
        "reachable_inside_candidate_capacity": selected,
    }
    (root / "reachable_candidate_queue_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (root / "01_reachable_candidate_queue_zin_overlay.png").write_bytes(PNG_BYTES)
    (root / "02_reachable_selected_candidate_zin_histograms.png").write_bytes(PNG_BYTES)


def _make_bundle(bundle: Path, source_dir: Path) -> str:
    with tarfile.open(bundle, "w:gz") as tar:
        tar.add(source_dir, arcname=source_dir.name)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    (bundle.with_suffix(bundle.suffix + ".sha256")).write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
    return digest


class ProcessReachableCandidateQueueBundleScriptTest(TransformerToolboxTestBase):
    def test_processes_bundle_and_publishes_queue(self) -> None:
        mod = _load_process_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            queue = root / "mars_zin_candidate_queue_reachable_20260615_010101"
            _write_queue(queue, selected=5)
            bundle = root / "reachable.tar.gz"
            _make_bundle(bundle, queue)
            report = root / "report"
            local_queue = root / "local_queue"

            status = mod.main(
                [
                    str(bundle),
                    "--sha256-file",
                    str(bundle.with_suffix(bundle.suffix + ".sha256")),
                    "--out-dir",
                    str(root / "unpacked"),
                    "--local-queue-dir",
                    str(local_queue),
                    "--report-dir",
                    str(report),
                    "--python",
                    sys.executable,
                    "--skip-report-rebuild",
                ]
            )

            self.assertEqual(status, 0)
            process_summary = json.loads((report / "reachable_candidate_queue_bundle_process_summary_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(process_summary["overall_status"], "PASS")
            self.assertTrue((local_queue / "reachable_candidate_queue_summary.json").is_file())
            publisher_manifest = json.loads((report / "reachable_candidate_queue_verified_manifest_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(publisher_manifest["status"], "REACHABLE_CANDIDATE_QUEUE_VERIFIED_PASS")

    def test_rejects_sha_mismatch(self) -> None:
        mod = _load_process_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            queue = root / "queue"
            _write_queue(queue)
            bundle = root / "reachable.tar.gz"
            _make_bundle(bundle, queue)
            bad_sha = root / "bad.sha256"
            bad_sha.write_text(f"{'0' * 64}  {bundle.name}\n", encoding="utf-8")

            status = mod.main(
                [
                    str(bundle),
                    "--sha256-file",
                    str(bad_sha),
                    "--out-dir",
                    str(root / "unpacked"),
                    "--local-queue-dir",
                    str(root / "local_queue"),
                    "--report-dir",
                    str(root / "report"),
                    "--skip-publisher",
                    "--skip-report-rebuild",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            process_summary = json.loads((root / "report" / "reachable_candidate_queue_bundle_process_summary_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(process_summary["overall_status"], "FAIL")
            failed = {check["name"] for check in process_summary["checks"] if not check["pass"]}
            self.assertIn("bundle_sha256_matches_expected", failed)

    def test_rejects_tar_path_traversal(self) -> None:
        mod = _load_process_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = root / "payload.txt"
            payload.write_text("unsafe", encoding="utf-8")
            bundle = root / "unsafe.tar.gz"
            with tarfile.open(bundle, "w:gz") as tar:
                tar.add(payload, arcname="../escape.txt")

            status = mod.main(
                [
                    str(bundle),
                    "--out-dir",
                    str(root / "unpacked"),
                    "--local-queue-dir",
                    str(root / "local_queue"),
                    "--report-dir",
                    str(root / "report"),
                    "--skip-publisher",
                    "--skip-report-rebuild",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            process_summary = json.loads((root / "report" / "reachable_candidate_queue_bundle_process_summary_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(process_summary["overall_status"], "FAIL")
            failed = {check["name"] for check in process_summary["checks"] if not check["pass"]}
            self.assertIn("safe_bundle_extraction", failed)
