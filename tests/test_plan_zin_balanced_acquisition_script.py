from tests.rfic_transformer_inverse_design.shared import *

import argparse
import csv
import hashlib
import importlib.util
import sys


def _load_plan_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plan_zin_balanced_acquisition.py"
    spec = importlib.util.spec_from_file_location("plan_zin_balanced_acquisition_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_dataset(root: Path, rows: list[dict[str, object]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class PlanZinBalancedAcquisitionScriptTest(TransformerToolboxTestBase):
    def test_geometry_only_dataset_is_not_ready(self) -> None:
        plan = _load_plan_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root, [{"evaluation": "a", "ok": "true", "geom__w": 10.0}])

            status = plan.main([str(root), "--out-dir", str(root / "plan"), "--no-plots"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "plan" / "zin_balanced_acquisition_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            self.assertEqual(summary["plan_status"], "NOT_READY")
            self.assertEqual(summary["label_summary"]["valid_count"], 0)

    def test_underfilled_bins_generate_prioritized_targets(self) -> None:
        plan = _load_plan_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(
                root,
                [
                    {"evaluation": "a", "ok": "true", "zin_center_real_ohm": 5, "zin_center_imag_ohm": -45},
                    {"evaluation": "b", "ok": "true", "zin_center_real_ohm": 10, "zin_center_imag_ohm": -40},
                    {"evaluation": "c", "ok": "true", "zin_center_real_ohm": 15, "zin_center_imag_ohm": -35},
                    {"evaluation": "d", "ok": "true", "zin_center_real_ohm": 20, "zin_center_imag_ohm": -30},
                ],
            )

            status = plan.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "plan"),
                    "--bins",
                    "2",
                    "--target-real-min-ohm",
                    "0",
                    "--target-real-max-ohm",
                    "100",
                    "--target-imag-min-ohm",
                    "-50",
                    "--target-imag-max-ohm",
                    "50",
                    "--desired-total-count",
                    "8",
                    "--next-count",
                    "4",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "plan" / "zin_balanced_acquisition_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["plan_status"], "SPARSE_BINS_PRIORITIZED")
            dataset_bytes = (root / "dataset_rows.csv").read_bytes()
            self.assertEqual(summary["dataset_source"]["sha256"], hashlib.sha256(dataset_bytes).hexdigest())
            self.assertEqual(summary["dataset_source"]["csv_row_count"], 4)
            self.assertEqual(summary["dataset_source"]["ok_row_count"], 4)
            self.assertEqual(summary["planning_envelope"]["target_count_per_bin"], 2)
            self.assertEqual(summary["target_summary"]["recommended_new_sample_count"], 4)
            with (root / "plan" / "zin_balanced_acquisition_targets.csv").open(newline="", encoding="utf-8") as handle:
                targets = list(csv.DictReader(handle))
            self.assertGreaterEqual(len(targets), 2)
            self.assertTrue(all(int(row["recommended_new_samples"]) > 0 for row in targets))
            self.assertTrue(any(int(row["current_count"]) == 0 for row in targets))

    def test_next_count_is_spread_across_sparse_bins_instead_of_one_bin(self) -> None:
        plan = _load_plan_module()

        sparse = [
            {
                "real_bin": idx,
                "imag_bin": 0,
                "target_real_ohm": float(idx),
                "target_imag_ohm": 0.0,
                "real_min_ohm": float(idx),
                "real_max_ohm": float(idx + 1),
                "imag_min_ohm": 0.0,
                "imag_max_ohm": 1.0,
                "current_count": 0,
                "target_count": 2480,
                "deficit": 2480,
            }
            for idx in range(100)
        ]
        args = argparse.Namespace(next_count=500, max_target_bins=None)

        targets = plan._select_targets(sparse, args)

        self.assertEqual(len(targets), 100)
        self.assertEqual(sum(int(row["recommended_new_samples"]) for row in targets), 500)
        self.assertEqual({int(row["recommended_new_samples"]) for row in targets}, {5})

    def test_target_envelope_config_drives_plan(self) -> None:
        plan = _load_plan_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "zin_target_envelope.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "zin_target_envelope.v1",
                        "name": "fixture",
                        "zin_target_envelope": {
                            "real_min_ohm": 0,
                            "real_max_ohm": 100,
                            "imag_min_ohm": -50,
                            "imag_max_ohm": 50,
                            "target_count_per_bin": 2,
                            "next_count": 3,
                        },
                    }
                ),
                encoding="utf-8",
            )
            _write_dataset(root, [{"evaluation": "a", "ok": "true", "zin_center_real_ohm": 5, "zin_center_imag_ohm": -45}])

            status = plan.main([str(root), "--out-dir", str(root / "plan"), "--bins", "2", "--target-envelope-config", str(config), "--no-plots"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "plan" / "zin_balanced_acquisition_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["target_envelope_config"]["status"], "PASS")
            self.assertEqual(summary["planning_envelope"]["target_count_per_bin"], 2)
            self.assertEqual(summary["target_summary"]["recommended_new_sample_count"], 3)
