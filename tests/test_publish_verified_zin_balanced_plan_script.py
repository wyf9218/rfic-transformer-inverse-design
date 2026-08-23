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
        / "publish_verified_zin_balanced_plan.py"
    )
    spec = importlib.util.spec_from_file_location("publish_verified_zin_balanced_plan_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_plan(root: Path, allocations: list[int]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "zin_balanced_acquisition_plan_summary.json").write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "target_summary": {
                    "target_bin_count": len(allocations),
                    "recommended_new_sample_count": sum(allocations),
                },
                "dataset_source": {
                    "path": str(root.parent / "dataset_rows.csv"),
                    "exists": True,
                    "sha256": "b" * 64,
                    "csv_row_count": 500,
                    "ok_row_count": 500,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    with (root / "zin_balanced_acquisition_targets.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rank", "real_bin", "imag_bin", "recommended_new_samples"])
        writer.writeheader()
        for idx, allocation in enumerate(allocations, start=1):
            writer.writerow({"rank": idx, "real_bin": idx - 1, "imag_bin": 0, "recommended_new_samples": allocation})
    with (root / "zin_balanced_acquisition_bins.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["real_bin", "imag_bin", "current_count", "deficit"])
        writer.writeheader()
        for idx, allocation in enumerate(allocations):
            writer.writerow({"real_bin": idx, "imag_bin": 0, "current_count": 0, "deficit": 2480 - allocation})
    (root / "01_zin_bin_deficit_heatmap.png").write_bytes(PNG_BYTES)
    (root / "02_next_zin_targets_overlay.png").write_bytes(PNG_BYTES)


class PublishVerifiedZinBalancedPlanScriptTest(TransformerToolboxTestBase):
    def test_publishes_verified_spread_plan_assets(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            report = root / "report"
            _write_plan(plan, [5] * 100)

            status = mod.main(["--plan-dir", str(plan), "--report-dir", str(report)])

            self.assertEqual(status, 0)
            manifest = json.loads((report / "zin_balanced_verified_plan_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "ZIN_PLAN_VERIFIED_PASS")
            self.assertTrue(manifest["strict_checks_pass"])
            self.assertEqual(manifest["verifier_summary"]["overall_status"], "PASS")
            for rel_path in manifest["published_assets"].values():
                self.assertTrue((report / rel_path).exists(), rel_path)

    def test_rejects_concentrated_plan_without_copying_assets(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            report = root / "report"
            _write_plan(plan, [500])

            with self.assertRaises(SystemExit) as cm:
                mod.main(["--plan-dir", str(plan), "--report-dir", str(report)])

            self.assertIn("verifier_overall_status_pass", str(cm.exception))
            manifest = json.loads((report / "zin_balanced_verified_plan_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "ZIN_PLAN_VERIFIED_FAIL")
            self.assertFalse(manifest["strict_checks_pass"])
            self.assertFalse((report / "assets").exists())

    def test_missing_required_figures_fail_strict_precheck(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            report = root / "report"
            _write_plan(plan, [5] * 100)
            (plan / "02_next_zin_targets_overlay.png").unlink()

            with self.assertRaises(SystemExit) as cm:
                mod.main(["--plan-dir", str(plan), "--report-dir", str(report)])

            self.assertIn("targets_overlay_exists", str(cm.exception))
            manifest = json.loads((report / "zin_balanced_verified_plan_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "FAILED_STRICT_PRECHECK")
