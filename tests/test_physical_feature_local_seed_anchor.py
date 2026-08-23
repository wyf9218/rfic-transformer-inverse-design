import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_physical_feature_surrogate_candidate_predictions.py"
SPEC = importlib.util.spec_from_file_location("physical_feature_candidate_builder_anchor_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class PhysicalFeatureLocalSeedAnchorTest(unittest.TestCase):
    def test_anchor_preserves_rare_high_q_seed_for_local_priority(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            rows = []
            for index in range(12):
                high = index == 11
                rows.append(
                    {
                        "ok": "true",
                        "evaluation": "row_{}".format(index),
                        "geom__width_um": 1.0 + index,
                        "geom__offset_um": -3.0 + 0.5 * index,
                        "lp_nh_center": 1.8 if high else 0.8 + 0.03 * index,
                        "ls_nh_center": 1.9 if high else 0.9 + 0.02 * index,
                        "q_center": 23.8 if high else 10.0 + 0.2 * index,
                        "k_abs_center": 0.22 if high else 0.40 + 0.01 * index,
                    }
                )
            write_csv(root / "dataset_rows.csv", rows)
            (root / "dataset_manifest.json").write_text(
                json.dumps({"bounds": {"width_um": [1.0, 12.0], "offset_um": [-3.0, 2.5]}}),
                encoding="utf-8",
            )
            target = {"bin_key": "rare_high_q", "recommended_new_samples": 80}
            for name, center, low, high in (
                ("lp_nh_center", 1.8, 1.5, 2.1),
                ("ls_nh_center", 1.9, 1.6, 2.2),
                ("q_center", 24.0, 23.0, 25.0),
                ("k_abs_center", 0.22, 0.16, 0.28),
            ):
                target["{}__target".format(name)] = center
                target["{}__min".format(name)] = low
                target["{}__max".format(name)] = high
            write_csv(root / "targets.csv", [target])

            statistics = {}
            for label, strength in (("off", "0"), ("on", "0.95")):
                out = root / label
                rc = MODULE.main(
                    [
                        str(root),
                        "--out-dir",
                        str(out),
                        "--candidate-count",
                        "80",
                        "--prediction-batch-size",
                        "40",
                        "--seed",
                        "19",
                        "--k-neighbors",
                        "4",
                        "--feature-columns",
                        "lp_nh_center,ls_nh_center,q_center,k_abs_center",
                        "--geometry-columns",
                        "geom__width_um,geom__offset_um",
                        "--target-bins-csv",
                        str(root / "targets.csv"),
                        "--local-target-fraction",
                        "1",
                        "--local-seed-count",
                        "1",
                        "--local-perturbation-scales",
                        "0.01",
                        "--local-seed-anchor-strength",
                        strength,
                        "--local-seed-anchor-radius",
                        "0.03",
                        "--lhs-optimization",
                        "none",
                        "--no-plots",
                    ]
                )
                self.assertEqual(rc, 0)
                with (out / "candidate_physical_feature_predictions.csv").open(
                    newline="", encoding="utf-8"
                ) as handle:
                    predictions = list(csv.DictReader(handle))
                q_values = [float(row["pred_q_center"]) for row in predictions]
                statistics[label] = {
                    "max": max(q_values),
                    "mean": sum(q_values) / len(q_values),
                    "above_23": sum(value > 23.0 for value in q_values),
                }
                if label == "on":
                    self.assertTrue(
                        any(float(row["candidate_seed_anchor_weight"]) > 0.5 for row in predictions)
                    )
                    self.assertTrue(
                        any("local_real_seed_anchor" in row["pred_source"] for row in predictions)
                    )
            self.assertGreater(statistics["on"]["mean"], statistics["off"]["mean"])
            self.assertGreaterEqual(statistics["on"]["above_23"], statistics["off"]["above_23"])
            self.assertGreater(statistics["on"]["max"], 23.0)


if __name__ == "__main__":
    unittest.main()
