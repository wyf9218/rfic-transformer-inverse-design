import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "select_balanced_physical_feature_checkpoint.py"
SPEC = importlib.util.spec_from_file_location("select_balanced_physical_feature_checkpoint", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class SelectBalancedPhysicalFeatureCheckpointTest(unittest.TestCase):
    def test_selects_exact_deterministic_geometry_unique_rows(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "accepted.csv"
            fields = list(MODULE.FEATURE_COLUMNS + MODULE.GEOMETRY_COLUMNS) + ["ok", "evaluation"]
            rows = []
            cell_values = [
                (0.7, 0.7, 7.0, 0.1, 10),
                (2.2, 0.7, 7.0, 0.1, 4),
                (0.7, 2.2, 18.0, 0.1, 4),
                (2.2, 2.2, 18.0, 0.6, 2),
            ]
            geometry_index = 0
            for lp, ls, q, k_abs, count in cell_values:
                for _ in range(count):
                    row = {
                        "lp_nh_center": lp,
                        "ls_nh_center": ls,
                        "q_center": q,
                        "k_abs_center": k_abs,
                        "ok": "true",
                        "evaluation": "eval_{:03d}".format(geometry_index),
                    }
                    for offset, column in enumerate(MODULE.GEOMETRY_COLUMNS):
                        row[column] = geometry_index + offset / 100.0
                    rows.append(row)
                    geometry_index += 1
            rows.append(dict(rows[0], evaluation="duplicate_geometry"))
            rows.append(dict(rows[1], evaluation="outside", q_center=30.0))
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            outputs = []
            for suffix in ("a", "b"):
                out_dir = root / suffix
                rc = MODULE.main(
                    [
                        "--input-csv",
                        str(source),
                        "--out-dir",
                        str(out_dir),
                        "--target-count",
                        "12",
                        "--four-d-bins",
                        "2",
                        "--min-four-d-occupied-fraction",
                        "0.25",
                        "--seed",
                        "17",
                    ]
                )
                self.assertEqual(rc, 0)
                summary = json.loads(
                    (out_dir / "balanced_physical_feature_checkpoint_summary.json").read_text(encoding="utf-8")
                )
                self.assertEqual(summary["overall_status"], "PASS")
                self.assertEqual(summary["valid_unique_count"], 20)
                self.assertEqual(summary["selected_count"], 12)
                self.assertEqual(summary["occupied_cells_after"], 4)
                self.assertEqual(summary["reject_summary"]["duplicate_geometry"], 1)
                self.assertEqual(summary["reject_summary"]["outside_range"], 1)
                outputs.append((out_dir / "dataset_rows.csv").read_bytes())
            self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
