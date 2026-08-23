import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def _load_builder_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_physical_feature_surrogate_candidate_predictions.py"
    spec = importlib.util.spec_from_file_location("build_physical_feature_surrogate_candidate_predictions_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_selector_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "select_physical_feature_targeted_candidate_geometries.py"
    spec = importlib.util.spec_from_file_location("select_physical_feature_targeted_candidate_geometries_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_planner_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plan_physical_feature_balanced_acquisition.py"
    spec = importlib.util.spec_from_file_location("plan_physical_feature_balanced_acquisition_script_for_candidate_test", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_training_dataset(root: Path) -> None:
    rows = []
    for idx in range(18):
        w = 1.0 + 0.5 * idx
        s = 1.5 + (idx % 5) * 0.2
        rows.append(
            {
                "evaluation": f"r{idx}",
                "ok": "true",
                "geom__w_um": w,
                "geom__s_um": s,
                "lp_nh_center": 0.4 + 0.05 * w,
                "ls_nh_center": 0.6 + 0.04 * w + 0.02 * s,
                "qp_center": 8.0 + 0.4 * s,
                "qs_center": 7.0 + 0.3 * s,
                "q_center": min(8.0 + 0.4 * s, 7.0 + 0.3 * s),
                "k_center": 0.35 + 0.01 * idx,
            }
        )
    _write_csv(root / "dataset_rows.csv", rows)
    (root / "dataset_manifest.json").write_text(
        json.dumps({"bounds": {"w_um": [1.0, 9.5], "s_um": [1.5, 2.3]}}),
        encoding="utf-8",
    )


class BuildPhysicalFeatureSurrogateCandidatePredictionsScriptTest(unittest.TestCase):
    def test_builds_candidate_prediction_csv_from_real_physical_feature_rows(self) -> None:
        mod = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_training_dataset(root)

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "predictions"),
                    "--candidate-count",
                    "20",
                    "--prediction-batch-size",
                    "6",
                    "--seed",
                    "7",
                    "--k-neighbors",
                    "3",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "predictions" / "candidate_physical_feature_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["training_count"], 18)
            self.assertEqual(summary["candidate_count"], 20)
            self.assertEqual(summary["validation"]["status"], "PASS")
            self.assertEqual(summary["bounds"]["geom__w_um"]["source"], "dataset_manifest_bounds")
            with (root / "predictions" / "candidate_physical_feature_predictions.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 20)
            self.assertIn("pred_lp_nh_center", rows[0])
            self.assertIn("pred_k_center", rows[0])
            self.assertIn("geom__w_um", rows[0])
            self.assertEqual(rows[0]["pred_source"], "knn_idw_surrogate_for_candidate_priority_only")

    def test_missing_physical_feature_labels_fails_without_candidates(self) -> None:
        mod = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_csv(root / "dataset_rows.csv", [{"ok": "true", "geom__w_um": 1.0, "geom__s_um": 2.0}])

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "predictions"),
                    "--candidate-count",
                    "8",
                    "--no-plots",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "predictions" / "candidate_physical_feature_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["candidate_count"], 0)

    def test_explicit_geometry_columns_ignore_sparse_legacy_columns(self) -> None:
        mod = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rows = []
            for idx in range(12):
                rows.append(
                    {
                        "evaluation": f"mixed_{idx}",
                        "ok": "true",
                        "geom__width_um": 2.0 + idx,
                        "geom__offset_um": -5.0 + idx,
                        "geom__legacy_a_um": "" if idx >= 6 else 100.0 + idx,
                        "geom__legacy_b_um": "" if idx < 6 else 200.0 + idx,
                        "lp_nh_center": 0.8 + 0.04 * idx,
                        "ls_nh_center": 0.9 + 0.03 * idx,
                        "q_center": 8.0 + 0.2 * idx,
                        "k_abs_center": 0.2 + 0.01 * idx,
                    }
                )
            _write_csv(root / "dataset_rows.csv", rows)

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "predictions"),
                    "--candidate-count",
                    "16",
                    "--geometry-columns",
                    "geom__width_um,geom__offset_um",
                    "--feature-columns",
                    "lp_nh_center,ls_nh_center,q_center,k_abs_center",
                    "--lhs-optimization",
                    "none",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads(
                (root / "predictions" / "candidate_physical_feature_prediction_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["training_count"], 12)
            self.assertEqual(summary["geometry_columns"], ["geom__width_um", "geom__offset_um"])
            self.assertEqual(summary["requested_geometry_columns"], ["geom__width_um", "geom__offset_um"])

    def test_predictions_feed_physical_feature_target_selector(self) -> None:
        builder = _load_builder_module()
        planner = _load_planner_module()
        selector = _load_selector_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_training_dataset(root)

            self.assertEqual(
                planner.main(
                    [
                        str(root),
                        "--out-dir",
                        str(root / "plan"),
                        "--bins",
                        "2",
                        "--target-count-per-bin",
                        "10",
                        "--next-count",
                        "4",
                        "--max-target-bins",
                        "1",
                    ]
                ),
                0,
            )
            self.assertEqual(
                builder.main(
                    [
                        str(root),
                        "--out-dir",
                        str(root / "predictions"),
                        "--candidate-count",
                        "80",
                        "--seed",
                        "11",
                        "--k-neighbors",
                        "3",
                        "--no-plots",
                    ]
                ),
                0,
            )

            status = selector.main(
                [
                    "--plan-dir",
                    str(root / "plan"),
                    "--candidate-csv",
                    str(root / "predictions" / "candidate_physical_feature_predictions.csv"),
                    "--out-dir",
                    str(root / "selection"),
                    "--max-total",
                    "4",
                    "--allow-outside-bin",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "selection" / "physical_feature_targeted_candidate_selection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["selected_candidate_count"], 4)
            self.assertIn("lp_nh_center", summary["feature_columns"])

    def test_target_aware_local_candidates_are_mixed_with_global_lhs(self) -> None:
        builder = _load_builder_module()
        planner = _load_planner_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_training_dataset(root)
            self.assertEqual(
                planner.main(
                    [
                        str(root),
                        "--out-dir",
                        str(root / "plan"),
                        "--bins",
                        "2",
                        "--target-count-per-bin",
                        "10",
                        "--next-count",
                        "20",
                        "--max-target-bins",
                        "4",
                    ]
                ),
                0,
            )

            status = builder.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "predictions"),
                    "--candidate-count",
                    "100",
                    "--seed",
                    "19",
                    "--k-neighbors",
                    "3",
                    "--target-bins-csv",
                    str(root / "plan" / "physical_feature_acquisition_targets.csv"),
                    "--local-target-fraction",
                    "0.6",
                    "--local-seed-count",
                    "3",
                    "--local-perturbation-scales",
                    "0.01,0.04",
                    "--lhs-optimization",
                    "none",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads(
                (root / "predictions" / "candidate_physical_feature_prediction_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["candidate_generation"]["mode_counts"]["local_sparse_target_perturbation"], 60)
            self.assertEqual(summary["candidate_generation"]["mode_counts"]["global_latin_hypercube"], 40)
            self.assertGreater(summary["candidate_generation"]["local_target_bin_count"], 0)
            with (root / "predictions" / "candidate_physical_feature_predictions.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 100)
            self.assertEqual(rows[0]["candidate_generation_mode"], "local_sparse_target_perturbation")
            self.assertTrue(any(row["candidate_generation_mode"] == "global_latin_hypercube" for row in rows))

    def test_pairwise_gap_fraction_generates_real_seed_local_candidates(self) -> None:
        builder = _load_builder_module()
        planner = _load_planner_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_training_dataset(root)
            self.assertEqual(
                planner.main(
                    [
                        str(root),
                        "--out-dir",
                        str(root / "plan"),
                        "--bins",
                        "2",
                        "--target-count-per-bin",
                        "10",
                        "--next-count",
                        "20",
                        "--max-target-bins",
                        "4",
                    ]
                ),
                0,
            )

            status = builder.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "predictions"),
                    "--candidate-count",
                    "100",
                    "--seed",
                    "23",
                    "--k-neighbors",
                    "3",
                    "--pairwise-target-fraction",
                    "0.3",
                    "--pairwise-bins-csv",
                    str(root / "plan" / "physical_feature_acquisition_bins.csv"),
                    "--pairwise-feature-pairs",
                    "lp_nh_center:q_center,ls_nh_center:q_center",
                    "--local-seed-count",
                    "3",
                    "--local-perturbation-scales",
                    "0.01,0.04",
                    "--lhs-optimization",
                    "none",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads(
                (root / "predictions" / "candidate_physical_feature_prediction_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(
                summary["candidate_generation"]["mode_counts"]["local_pairwise_gap_perturbation"],
                30,
            )
            self.assertEqual(summary["candidate_generation"]["mode_counts"]["global_latin_hypercube"], 70)
            self.assertGreater(
                summary["candidate_generation"]["target_bin_count_by_mode"]["local_pairwise_gap_perturbation"],
                0,
            )
            with (root / "predictions" / "candidate_physical_feature_predictions.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                rows = list(csv.DictReader(handle))
            pairwise = [
                row for row in rows if row["candidate_generation_mode"] == "local_pairwise_gap_perturbation"
            ]
            self.assertEqual(len(pairwise), 30)
            self.assertTrue(all(row["candidate_pairwise_features"] for row in pairwise))
            self.assertTrue(all(row["candidate_seed_training_index"] for row in pairwise))
            self.assertTrue(all(float(row["candidate_pairwise_deficit_fraction"]) > 0.0 for row in pairwise))
            self.assertTrue(any(float(row["candidate_seed_anchor_weight"]) > 0.0 for row in pairwise))

    def test_production_targeting_mix_retains_global_exploration(self) -> None:
        builder = _load_builder_module()
        planner = _load_planner_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_training_dataset(root)
            self.assertEqual(
                planner.main(
                    [
                        str(root),
                        "--out-dir",
                        str(root / "plan"),
                        "--bins",
                        "2",
                        "--target-count-per-bin",
                        "10",
                        "--next-count",
                        "20",
                        "--max-target-bins",
                        "4",
                    ]
                ),
                0,
            )
            self.assertEqual(
                builder.main(
                    [
                        str(root),
                        "--out-dir",
                        str(root / "predictions"),
                        "--candidate-count",
                        "100",
                        "--seed",
                        "31",
                        "--k-neighbors",
                        "3",
                        "--target-bins-csv",
                        str(root / "plan" / "physical_feature_acquisition_targets.csv"),
                        "--local-target-fraction",
                        "0.5",
                        "--rare-marginal-fraction",
                        "0.2",
                        "--pairwise-target-fraction",
                        "0.25",
                        "--pairwise-bins-csv",
                        str(root / "plan" / "physical_feature_acquisition_bins.csv"),
                        "--lhs-optimization",
                        "none",
                        "--no-plots",
                    ]
                ),
                0,
            )
            summary = json.loads(
                (root / "predictions" / "candidate_physical_feature_prediction_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                summary["candidate_generation"]["mode_counts"],
                {
                    "local_sparse_target_perturbation": 50,
                    "local_rare_marginal_perturbation": 20,
                    "local_pairwise_gap_perturbation": 25,
                    "global_latin_hypercube": 5,
                },
            )
