import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", "_test_module"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


BUILDER = load_script("build_physical_feature_surrogate_candidate_predictions.py")
SELECTOR = load_script("select_physical_feature_targeted_candidate_geometries.py")


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class RareMarginalCandidateSelectionTest(unittest.TestCase):
    def test_reserves_selection_capacity_for_high_q_marginal_seed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            training = []
            for index in range(20):
                high = index == 19
                training.append(
                    {
                        "ok": "true",
                        "evaluation": "r{}".format(index),
                        "geom__width_um": 2.0 + 0.2 * index,
                        "geom__offset_um": -2.0 + 0.1 * index,
                        "lp_nh_center": 0.9 + 0.02 * index,
                        "ls_nh_center": 1.0 + 0.015 * index,
                        "q_center": 23.8 if high else 10.0 + 0.15 * index,
                        "k_abs_center": 0.22 if high else 0.35 + 0.005 * index,
                    }
                )
            write_csv(root / "dataset_rows.csv", training)

            prediction_dir = root / "predictions"
            rc = BUILDER.main(
                [
                    str(root),
                    "--out-dir",
                    str(prediction_dir),
                    "--candidate-count",
                    "100",
                    "--seed",
                    "23",
                    "--k-neighbors",
                    "4",
                    "--feature-columns",
                    "lp_nh_center,ls_nh_center,q_center,k_abs_center",
                    "--geometry-columns",
                    "geom__width_um,geom__offset_um",
                    "--local-target-fraction",
                    "0",
                    "--rare-marginal-fraction",
                    "0.5",
                    "--rare-marginal-bins",
                    "10",
                    "--rare-marginal-feature-weights",
                    "0.5,0.5,2.0,1.5",
                    "--local-seed-anchor-strength",
                    "0.95",
                    "--lhs-optimization",
                    "none",
                    "--no-plots",
                ]
            )
            self.assertEqual(rc, 0)

            plan = root / "plan"
            target = {
                "bin_key": "moderate",
                "rank": 1,
                "recommended_new_samples": 20,
            }
            for name, center, low, high in (
                ("lp_nh_center", 1.1, 0.8, 1.4),
                ("ls_nh_center", 1.1, 0.8, 1.4),
                ("q_center", 12.0, 9.0, 15.0),
                ("k_abs_center", 0.4, 0.3, 0.5),
            ):
                target["{}__target".format(name)] = center
                target["{}__min".format(name)] = low
                target["{}__max".format(name)] = high
            write_csv(plan / "physical_feature_acquisition_targets.csv", [target])

            selection = root / "selection"
            rc = SELECTOR.main(
                [
                    "--plan-dir",
                    str(plan),
                    "--candidate-csv",
                    str(prediction_dir / "candidate_physical_feature_predictions.csv"),
                    "--out-dir",
                    str(selection),
                    "--feature-columns",
                    "lp_nh_center,ls_nh_center,q_center,k_abs_center",
                    "--max-total",
                    "20",
                    "--rare-marginal-max-total",
                    "10",
                    "--allow-outside-bin",
                    "--no-fail-exit",
                ]
            )
            self.assertEqual(rc, 0)
            summary = json.loads(
                (selection / "physical_feature_targeted_candidate_selection_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertGreater(summary["selected_rare_marginal_count"], 0)
            with (selection / "physical_feature_targeted_candidate_selection.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                rows = list(csv.DictReader(handle))
            rare_q = [
                row
                for row in rows
                if row.get("selection_source") == "rare_marginal_real_seed"
                and row.get("candidate__candidate_marginal_feature") == "q_center"
            ]
            self.assertTrue(rare_q)
            self.assertTrue(any(float(row["pred_q_center"]) > 23.0 for row in rare_q))


if __name__ == "__main__":
    unittest.main()
