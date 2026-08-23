from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xe2k\xb8"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _load_publisher_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "publish_verified_reachable_candidate_queue.py"
    )
    spec = importlib.util.spec_from_file_location("publish_verified_reachable_candidate_queue_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_queue(root: Path, *, selected: int = 5, inside_rows: bool = True, status: str = "PASS") -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "reachable_zin_targeted_candidate_selection.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "selection_rank",
                "candidate_id",
                "inside_target_bin",
                "target_real_bin",
                "target_imag_bin",
                "pred_real_ohm",
                "pred_imag_ohm",
            ],
        )
        writer.writeheader()
        for idx in range(selected):
            writer.writerow(
                {
                    "selection_rank": idx + 1,
                    "candidate_id": f"cand_{idx}",
                    "inside_target_bin": "True" if inside_rows else ("False" if idx == 0 else "True"),
                    "target_real_bin": idx,
                    "target_imag_bin": 0,
                    "pred_real_ohm": 10 + idx,
                    "pred_imag_ohm": -5 + idx,
                }
            )
    summary = {
        "overall_status": status,
        "decision": "USE_REACHABLE_SELECTED_CANDIDATES_FOR_NEXT_EMX_BATCH",
        "candidate_source": {"path": str(root.parent / "candidate_zin_predictions.csv"), "sha256": "a" * 64},
        "targets_source": {"path": str(root.parent / "zin_balanced_acquisition_targets.csv"), "sha256": "b" * 64},
        "selected_count": selected,
        "effective_requested_candidate_count": selected,
        "selected_inside_target_bin_count": selected if inside_rows else selected - 1,
        "reachable_target_count": selected,
        "unreachable_target_count": 3,
        "reachable_inside_candidate_capacity": selected,
    }
    (root / "reachable_candidate_queue_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (root / "01_reachable_candidate_queue_zin_overlay.png").write_bytes(PNG_BYTES)
    (root / "02_reachable_selected_candidate_zin_histograms.png").write_bytes(PNG_BYTES)


class PublishVerifiedReachableCandidateQueueScriptTest(TransformerToolboxTestBase):
    def test_publishes_reachable_candidate_queue_assets(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            queue = root / "queue"
            report = root / "report"
            _write_queue(queue, selected=5)

            status = mod.main(["--queue-dir", str(queue), "--report-dir", str(report)])

            self.assertEqual(status, 0)
            manifest = json.loads((report / "reachable_candidate_queue_verified_manifest_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "REACHABLE_CANDIDATE_QUEUE_VERIFIED_PASS")
            self.assertTrue(manifest["strict_checks_pass"])
            self.assertEqual(manifest["metrics"]["selected_count"], 5)
            for rel_path in manifest["published_assets"].values():
                self.assertTrue((report / rel_path).exists(), rel_path)

    def test_rejects_queue_with_fallback_rows(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            queue = root / "queue"
            report = root / "report"
            _write_queue(queue, selected=5, inside_rows=False)

            status = mod.main(["--queue-dir", str(queue), "--report-dir", str(report), "--no-fail-exit"])

            self.assertEqual(status, 0)
            manifest = json.loads((report / "reachable_candidate_queue_verified_manifest_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "REACHABLE_CANDIDATE_QUEUE_VERIFIED_FAIL")
            failed = {check["name"] for check in manifest["checks"] if not check["pass"]}
            self.assertIn("selected_inside_target_bin_count_matches_selected", failed)
            self.assertIn("all_csv_rows_marked_inside_target_bin", failed)
            self.assertFalse((report / "assets").exists())

    def test_missing_figures_fail_strict_precheck(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            queue = root / "queue"
            report = root / "report"
            _write_queue(queue, selected=5)
            (queue / "01_reachable_candidate_queue_zin_overlay.png").unlink()

            with self.assertRaises(SystemExit) as cm:
                mod.main(["--queue-dir", str(queue), "--report-dir", str(report)])

            self.assertIn("overlay_exists", str(cm.exception))
            manifest = json.loads((report / "reachable_candidate_queue_verified_manifest_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "REACHABLE_CANDIDATE_QUEUE_VERIFIED_FAIL")
