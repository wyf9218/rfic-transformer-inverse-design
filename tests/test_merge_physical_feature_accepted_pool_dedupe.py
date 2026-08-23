import csv
import importlib.util
import itertools
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "merge_physical_feature_accepted_pool.py"
SPEC = importlib.util.spec_from_file_location("merge_physical_feature_accepted_pool", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class MergePhysicalFeatureAcceptedPoolDedupeTest(unittest.TestCase):
    @staticmethod
    def _make_row(index, path, geometry_base=10.0, geometry_delta=0.0):
        row = {
            "ok": "true",
            "evaluation": "eval_{}".format(index),
            "touchstone_path": path,
            "lp_nh_center": 1.0,
            "ls_nh_center": 1.1,
            "q_center": 12.0,
            "k_center": -0.3,
            "k_abs_center": 0.3,
        }
        for offset, column in enumerate(MODULE.INDEPENDENT_GEOMETRY_COLUMNS):
            row[column] = geometry_base + offset / 100.0 + geometry_delta
        return row

    @staticmethod
    def _write_pool(root, name, rows):
        pool = root / name
        pool.mkdir()
        fields = sorted({key for row in rows for key in row})
        with (pool / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return pool

    def test_same_geometry_with_different_touchstone_paths_counts_once(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            rows_by_pool = [
                [self._make_row(1, "/tmp/a.s4p", 10.0)],
                [self._make_row(2, "/tmp/b.s4p", 10.0), self._make_row(3, "/tmp/c.s4p", 20.0)],
            ]
            pool_dirs = [self._write_pool(root, "pool_{}".format(index), rows) for index, rows in enumerate(rows_by_pool)]

            out_dir = root / "out"
            rc = MODULE.main(
                [
                    "--base-pool-dir",
                    str(pool_dirs[0]),
                    "--base-pool-dir",
                    str(pool_dirs[1]),
                    "--out-dir",
                    str(out_dir),
                    "--min-row-count",
                    "2",
                ]
            )
            self.assertEqual(rc, 0)
            summary = json.loads((out_dir / "accepted_pool_merge_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["row_count"], 2)
            self.assertEqual(summary["reject_summary"]["duplicate"], 1)
            self.assertEqual(summary["dedupe_policy"]["primary_key"], "canonical 10-variable independent geometry vector")
            with (out_dir / "dataset_rows.csv").open(newline="", encoding="utf-8") as handle:
                merged = list(csv.DictReader(handle))
            self.assertEqual(len(merged), 2)
            self.assertTrue(all(row["canonical_geometry_fingerprint_sha256"] for row in merged))
            self.assertTrue(
                all(row["canonical_geometry_fingerprint_schema"] == MODULE.GEOMETRY_FINGERPRINT_SCHEMA for row in merged)
            )

    def test_sub_quantum_geometry_difference_is_rejected_as_duplicate(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pool = self._write_pool(
                root,
                "pool",
                [
                    self._make_row(1, "/tmp/a.s4p"),
                    self._make_row(2, "/tmp/b.s4p", geometry_delta=0.4e-6),
                ],
            )
            out_dir = root / "out"
            rc = MODULE.main(["--base-pool-dir", str(pool), "--out-dir", str(out_dir)])
            self.assertEqual(rc, 0)
            summary = json.loads((out_dir / "accepted_pool_merge_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["row_count"], 1)
            self.assertEqual(summary["reject_summary"]["duplicate"], 1)
            self.assertEqual(
                summary["dedupe_policy"]["fingerprint_quantization_um"],
                MODULE.GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
            )

    def test_tampered_declared_geometry_identity_fails_merge(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            row = self._make_row(1, "/tmp/a.s4p")
            row["geometry_fingerprint_sha256"] = "0" * 64
            row["geometry_fingerprint_schema"] = MODULE.GEOMETRY_FINGERPRINT_SCHEMA
            row["geometry_fingerprint_quantization_um"] = MODULE.GEOMETRY_FINGERPRINT_QUANTIZATION_UM
            pool = self._write_pool(root, "pool", [row])
            out_dir = root / "out"
            rc = MODULE.main(["--base-pool-dir", str(pool), "--out-dir", str(out_dir)])
            self.assertEqual(rc, 2)
            summary = json.loads((out_dir / "accepted_pool_merge_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["reject_summary"]["geometry_identity_mismatch"], 1)
            self.assertEqual(summary["row_count"], 0)

    def test_width_alias_mismatch_fails_merge(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            row = self._make_row(1, "/tmp/a.s4p")
            row["geom__primary_width_um"] = float(row["geom__line_width_um"]) + 0.1
            pool = self._write_pool(root, "pool", [row])
            out_dir = root / "out"
            rc = MODULE.main(["--base-pool-dir", str(pool), "--out-dir", str(out_dir)])
            self.assertEqual(rc, 2)
            summary = json.loads((out_dir / "accepted_pool_merge_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["reject_summary"]["geometry_identity_mismatch"], 1)

    def test_required_four_d_balance_gate_is_propagated_and_blocks_concentrated_pool(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cells = [cell for cell in itertools.product(range(4), repeat=4) if sum(cell) % 2 == 0]
            cell_sequence = list(cells) + [cells[0]] * 1000
            rows = []
            for index, (lp_bin, ls_bin, q_bin, k_bin) in enumerate(cell_sequence):
                row = self._make_row(index, f"/tmp/{index}.s4p", geometry_base=10.0 + index)
                row.update(
                    {
                        "lp_nh_center": 0.5 + (lp_bin + 0.5) * (2.5 / 4.0),
                        "ls_nh_center": 0.5 + (ls_bin + 0.5) * (2.5 / 4.0),
                        "q_center": 5.0 + (q_bin + 0.5) * 5.0,
                        "k_center": (k_bin + 0.5) * 0.2,
                        "k_abs_center": (k_bin + 0.5) * 0.2,
                    }
                )
                rows.append(row)

            pool = self._write_pool(root, "pool", rows)
            out_dir = root / "out"
            rc = MODULE.main(
                [
                    "--base-pool-dir",
                    str(pool),
                    "--out-dir",
                    str(out_dir),
                    "--bins",
                    "4",
                    "--pair-bins",
                    "4",
                    "--four-d-bins",
                    "4",
                    "--min-four-d-occupied-frac",
                    "0.5",
                    "--min-four-d-entropy-frac",
                    "0.8",
                    "--max-four-d-bin-imbalance",
                    "4",
                    "--run-uniformity",
                    "--require-four-d-gate",
                    "--no-fail-exit",
                ]
            )
            self.assertEqual(rc, 0)
            summary = json.loads((out_dir / "accepted_pool_merge_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            uniformity = summary["uniformity"]
            self.assertEqual(uniformity["four_d_occupied_fraction"], 0.5)
            self.assertLess(uniformity["four_d_normalized_entropy"], 0.8)
            self.assertGreater(uniformity["four_d_nonzero_bin_imbalance"], 4.0)
            checks = {item["name"]: item["status"] for item in summary["checks"]}
            self.assertEqual(checks["uniformity_audit_passed_required_four_d_balance_gate"], "FAIL")


if __name__ == "__main__":
    unittest.main()
