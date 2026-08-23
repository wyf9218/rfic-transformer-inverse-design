from tests.rfic_transformer_inverse_design.shared import *

import argparse
import csv
import hashlib
import importlib.util
import sys


def _load_plan_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plan_physical_feature_balanced_acquisition.py"
    spec = importlib.util.spec_from_file_location("plan_physical_feature_balanced_acquisition_script", script_path)
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


class PlanPhysicalFeatureBalancedAcquisitionScriptTest(TransformerToolboxTestBase):
    def test_geometry_only_dataset_is_not_ready(self) -> None:
        plan = _load_plan_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root, [{"evaluation": "a", "ok": "true", "geom__w": 10.0}])

            status = plan.main([str(root), "--out-dir", str(root / "plan")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "plan" / "physical_feature_acquisition_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            self.assertEqual(summary["plan_status"], "NOT_READY")
            self.assertEqual(summary["label_summary"]["valid_count"], 0)

    def test_underfilled_physical_feature_bins_generate_targets(self) -> None:
        plan = _load_plan_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(
                root,
                [
                    {"evaluation": "a", "ok": "true", "lp_nh_center": 1.0, "ls_nh_center": 1.0, "q_center": 10.0, "k_center": -0.5},
                    {"evaluation": "b", "ok": "true", "lp_nh_center": 1.2, "ls_nh_center": 1.1, "q_center": 11.0, "k_center": -0.4},
                    {"evaluation": "c", "ok": "true", "lp_nh_center": 2.5, "ls_nh_center": 2.8, "q_center": 18.0, "k_center": -0.1},
                ],
            )

            status = plan.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "plan"),
                    "--feature-columns",
                    "lp_nh_center,ls_nh_center,q_center,k_center",
                    "--bins",
                    "2",
                    "--desired-total-count",
                    "16",
                    "--next-count",
                    "4",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "plan" / "physical_feature_acquisition_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["plan_status"], "SPARSE_FEATURE_BINS_PRIORITIZED")
            dataset_bytes = (root / "dataset_rows.csv").read_bytes()
            self.assertEqual(summary["dataset_source"]["sha256"], hashlib.sha256(dataset_bytes).hexdigest())
            self.assertEqual(summary["planning_envelope"]["target_count_per_bin"], 1)
            self.assertEqual(summary["visual_evidence"]["status"], "PASS")
            self.assertTrue(Path(summary["visual_evidence"]["figures"]["marginal_histograms"]).is_file())
            self.assertTrue(Path(summary["visual_evidence"]["figures"]["pairwise_scatter"]).is_file())
            self.assertTrue(Path(summary["visual_evidence"]["figures"]["bin_coverage_heatmap"]).is_file())
            self.assertIn("empty_bin_fraction", summary["bin_summary"])
            self.assertIn("normalized_entropy", summary["bin_summary"])
            self.assertIn("count_cv", summary["bin_summary"])
            self.assertEqual(summary["target_summary"]["recommended_new_sample_count"], 4)
            with (root / "plan" / "physical_feature_acquisition_targets.csv").open(newline="", encoding="utf-8") as handle:
                targets = list(csv.DictReader(handle))
            self.assertEqual(len(targets), 4)
            self.assertTrue(all(int(row["recommended_new_samples"]) == 1 for row in targets))
            self.assertTrue(all(int(row["current_count"]) == 0 for row in targets))

    def test_next_count_is_spread_across_sparse_feature_bins(self) -> None:
        plan = _load_plan_module()
        sparse = [
            {"bin_key": str(idx), "current_count": 0, "target_count": 20, "deficit": 20}
            for idx in range(10)
        ]
        args = argparse.Namespace(next_count=30, max_target_bins=None)

        targets = plan._select_targets(sparse, args)

        self.assertEqual(len(targets), 10)
        self.assertEqual(sum(int(row["recommended_new_samples"]) for row in targets), 30)
        self.assertEqual({int(row["recommended_new_samples"]) for row in targets}, {3})

    def test_q_center_can_be_derived_from_qp_qs_diagnostics(self) -> None:
        plan = _load_plan_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(
                root,
                [
                    {"evaluation": "a", "ok": "true", "lp_nh_center": 1.0, "ls_nh_center": 1.1, "qp_center": 12.0, "qs_center": 9.0, "k_center": 0.40},
                    {"evaluation": "b", "ok": "true", "lp_nh_center": 1.2, "ls_nh_center": 1.3, "qp_center": 8.0, "qs_center": 11.0, "k_center": 0.45},
                ],
            )

            status = plan.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "plan"),
                    "--feature-columns",
                    "lp_nh_center,ls_nh_center,q_center,k_center",
                    "--bins",
                    "2",
                    "--desired-total-count",
                    "8",
                    "--next-count",
                    "2",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "plan" / "physical_feature_acquisition_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["label_summary"]["valid_count"], 2)
            self.assertEqual(summary["label_summary"]["missing_counts"]["q_center"], 0)
            self.assertIn("min(qp_center, qs_center)", summary["label_summary"]["derived_feature_rules"]["q_center"])
            self.assertAlmostEqual(summary["label_summary"]["features"]["q_center"]["min"], 8.0)
            self.assertAlmostEqual(summary["label_summary"]["features"]["q_center"]["max"], 9.0)

    def test_target_envelope_config_selects_feature_columns_and_bounds(self) -> None:
        plan = _load_plan_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "physical_feature_target_envelope.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "physical_feature_target_envelope.v1",
                        "name": "fixture",
                        "physical_feature_target_envelope": {
                            "feature_columns": ["lp_nh_center", "ls_nh_center", "k_center"],
                            "features": {
                                "lp_nh_center": {"min": 1.0, "max": 3.0},
                                "ls_nh_center": {"min": 1.0, "max": 3.0},
                                "k_center": {"min": -0.6, "max": 0.0},
                            },
                            "bins": 2,
                            "target_count_per_bin": 1,
                            "next_count": 2,
                        },
                    }
                ),
                encoding="utf-8",
            )
            _write_dataset(root, [{"evaluation": "a", "ok": "true", "lp_nh_center": 1.0, "ls_nh_center": 1.0, "k_center": -0.5}])

            status = plan.main([str(root), "--out-dir", str(root / "plan"), "--target-envelope-config", str(config)])

            self.assertEqual(status, 0)
            summary = json.loads((root / "plan" / "physical_feature_acquisition_plan_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["target_envelope_config"]["status"], "PASS")
            self.assertEqual(summary["feature_columns"], ["lp_nh_center", "ls_nh_center", "k_center"])
            self.assertEqual(summary["planning_envelope"]["feature_bounds"]["k_center"]["min"], -0.6)
            self.assertEqual(summary["target_summary"]["recommended_new_sample_count"], 2)

    def test_cumulative_cli_target_overrides_static_checkpoint_floor(self) -> None:
        plan = _load_plan_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "physical_feature_target_envelope.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "physical_feature_target_envelope.v1",
                        "physical_feature_target_envelope": {
                            "feature_columns": ["lp_nh_center", "ls_nh_center", "q_center", "k_abs_center"],
                            "features": {
                                "lp_nh_center": {"min": 0.5, "max": 3.0},
                                "ls_nh_center": {"min": 0.5, "max": 3.0},
                                "q_center": {"min": 5.0, "max": 25.0},
                                "k_abs_center": {"min": 0.0, "max": 0.8},
                            },
                            "bins": 4,
                            "target_count_per_bin": 391,
                            "desired_total_count": 100000,
                            "next_count": 8000,
                        },
                    }
                ),
                encoding="utf-8",
            )
            _write_dataset(
                root,
                [
                    {
                        "evaluation": "a",
                        "ok": "true",
                        "lp_nh_center": 1.0,
                        "ls_nh_center": 1.1,
                        "q_center": 10.0,
                        "k_abs_center": 0.4,
                    }
                ],
            )

            status = plan.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "plan"),
                    "--target-envelope-config",
                    str(config),
                    "--target-count-per-bin",
                    "782",
                    "--desired-total-count",
                    "200000",
                    "--next-count",
                    "120000",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads(
                (root / "plan" / "physical_feature_acquisition_plan_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["planning_envelope"]["target_count_per_bin"], 782)
            self.assertEqual(summary["planning_envelope"]["desired_total_count"], 200000)
            self.assertEqual(summary["target_summary"]["recommended_new_sample_count"], 120000)
