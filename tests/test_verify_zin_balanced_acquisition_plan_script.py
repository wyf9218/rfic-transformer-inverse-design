from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_verify_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "verify_zin_balanced_acquisition_plan.py"
    spec = importlib.util.spec_from_file_location("verify_zin_balanced_acquisition_plan_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_plan(root: Path, allocations: list[int], *, overall_status: str = "PASS", include_dataset_source: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    summary = {
        "overall_status": overall_status,
        "target_summary": {
            "target_bin_count": len(allocations),
            "recommended_new_sample_count": sum(allocations),
        },
    }
    if include_dataset_source:
        summary["dataset_source"] = {
            "path": str(root.parent / "dataset_rows.csv"),
            "exists": True,
            "sha256": "a" * 64,
            "csv_row_count": 500,
            "ok_row_count": 500,
        }
    (root / "zin_balanced_acquisition_plan_summary.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )
    with (root / "zin_balanced_acquisition_targets.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rank", "real_bin", "imag_bin", "recommended_new_samples"])
        writer.writeheader()
        for idx, allocation in enumerate(allocations, start=1):
            writer.writerow(
                {
                    "rank": idx,
                    "real_bin": idx - 1,
                    "imag_bin": 0,
                    "recommended_new_samples": allocation,
                }
            )


class VerifyZinBalancedAcquisitionPlanScriptTest(TransformerToolboxTestBase):
    def test_accepts_spread_plan(self) -> None:
        mod = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            _write_plan(plan, [5] * 100)

            status = mod.main(
                [
                    str(plan),
                    "--expected-new-sample-count",
                    "500",
                    "--min-target-bins",
                    "50",
                    "--max-single-bin-fraction",
                    "0.05",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((plan / "zin_balanced_acquisition_plan_verification_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["target_metrics"]["recommended_new_sample_count"], 500)
            self.assertEqual(summary["target_metrics"]["nonzero_target_bin_count"], 100)
            self.assertAlmostEqual(summary["target_metrics"]["max_single_bin_fraction"], 0.01)
            self.assertEqual(summary["dataset_source"]["sha256"], "a" * 64)

    def test_rejects_one_bin_concentrated_plan(self) -> None:
        mod = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            _write_plan(plan, [500])

            status = mod.main(
                [
                    str(plan),
                    "--expected-new-sample-count",
                    "500",
                    "--min-target-bins",
                    "50",
                    "--max-single-bin-fraction",
                    "0.05",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((plan / "zin_balanced_acquisition_plan_verification_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "REJECT_CONCENTRATED_ZIN_ACQUISITION_PLAN")
            failed = {check["name"] for check in summary["checks"] if check["status"] == "FAIL"}
            self.assertIn("minimum nonzero target bins", failed)
            self.assertIn("maximum single-bin allocation fraction", failed)

    def test_missing_or_failed_planner_summary_rejects(self) -> None:
        mod = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            _write_plan(plan, [5] * 100, overall_status="NOT_READY")

            status = mod.main(
                [
                    str(plan),
                    "--expected-new-sample-count",
                    "500",
                    "--min-target-bins",
                    "50",
                    "--max-single-bin-fraction",
                    "0.05",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((plan / "zin_balanced_acquisition_plan_verification_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            failed = {check["name"] for check in summary["checks"] if check["status"] == "FAIL"}
            self.assertIn("planner overall status", failed)

    def test_missing_dataset_traceability_rejects(self) -> None:
        mod = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            _write_plan(plan, [5] * 100, include_dataset_source=False)

            status = mod.main(
                [
                    str(plan),
                    "--expected-new-sample-count",
                    "500",
                    "--min-target-bins",
                    "50",
                    "--max-single-bin-fraction",
                    "0.05",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((plan / "zin_balanced_acquisition_plan_verification_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            failed = {check["name"] for check in summary["checks"] if check["status"] == "FAIL"}
            self.assertIn("dataset source traceability", failed)
