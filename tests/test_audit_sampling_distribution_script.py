from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_sampling_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_sampling_distribution.py"
    spec = importlib.util.spec_from_file_location("audit_sampling_distribution_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_dataset(root: Path, rows: list[dict[str, object]], bounds: dict[str, list[float]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "dataset_manifest.json").write_text(json.dumps({"bounds": bounds, "requested_count": len(rows), "ok_count": len(rows), "fail_count": 0}), encoding="utf-8")
    fields = sorted({key for row in rows for key in row})
    with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class AuditSamplingDistributionScriptTest(TransformerToolboxTestBase):
    def test_balanced_rows_pass_uniformity_audit(self) -> None:
        audit = _load_sampling_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rows = []
            for idx in range(20):
                rows.append(
                    {
                        "ok": "true",
                        "geom__w_um": 0.5 + idx,
                        "geom__h_um": 0.5 + ((idx * 9) % 20),
                    }
                )
            _write_dataset(root, rows, {"w_um": [0, 20], "h_um": [0, 20]})

            status = audit.main([str(root), "--out-dir", str(root / "audit"), "--bins", "10"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "sampling_distribution_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["field_count"], 2)
            self.assertTrue(all(record["uniform_closer_than_normal"] for record in summary["field_records"]))
            self.assertTrue(all(record["boundary_coverage_ok"] for record in summary["field_records"]))
            self.assertEqual(summary["uniform_vs_normal_summary"]["status"], "PASS")
            self.assertGreaterEqual(summary["uniform_vs_normal_summary"]["closer_to_uniform_fraction"], 1.0)
            self.assertEqual(summary["space_filling_summary"]["status"], "PASS")
            self.assertEqual(summary["space_filling_summary"]["duplicate_status"], "PASS")
            self.assertEqual(summary["space_filling_summary"]["strata_status"], "PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["per-field boundary coverage"]["status"], "PASS")
            self.assertEqual(checks["space-filling duplicate vectors"]["status"], "PASS")
            self.assertEqual(checks["space-filling strata coverage"]["status"], "PASS")
            self.assertTrue((root / "audit" / "sampling_distribution_fields.csv").exists())
            self.assertTrue((root / "audit" / "sampling_distribution_uniform_vs_normal_ks.png").exists())
            self.assertTrue((root / "audit" / "sampling_distribution_histogram_entropy.png").exists())
            self.assertTrue((root / "audit" / "sampling_distribution_boundary_coverage.png").exists())
            self.assertTrue((root / "audit" / "sampling_distribution_space_filling_strata.png").exists())
            self.assertTrue((root / "audit" / "sampling_distribution_nearest_neighbor_distances.png").exists())

    def test_clustered_rows_fail_uniformity_audit(self) -> None:
        audit = _load_sampling_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rows = [{"ok": "true", "geom__w_um": 5.0 + 0.01 * idx} for idx in range(20)]
            _write_dataset(root, rows, {"w_um": [0, 20]})

            status = audit.main([str(root), "--out-dir", str(root / "audit"), "--bins", "10"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "sampling_distribution_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            failed = [item for item in summary["field_records"] if item["status"] == "FAIL"]
            self.assertEqual(len(failed), 1)
            self.assertGreater(failed[0]["max_abs_imbalance_frac"], 0.25)

    def test_normal_like_rows_fail_uniform_vs_normal_evidence(self) -> None:
        audit = _load_sampling_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rng = np.random.default_rng(20260613)
            values = np.clip(rng.normal(loc=0.5, scale=0.11, size=240), 0.0, 1.0)
            rows = [{"ok": "true", "geom__w_um": float(value * 20.0)} for value in values]
            _write_dataset(root, rows, {"w_um": [0, 20]})

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--bins",
                    "10",
                    "--max-histogram-imbalance-frac",
                    "10",
                    "--min-uniform-vs-normal-fields-fraction",
                    "1.0",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "sampling_distribution_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["uniform_vs_normal_summary"]["status"], "FAIL")
            record = summary["field_records"][0]
            self.assertFalse(record["uniform_closer_than_normal"])
            self.assertLess(record["uniform_vs_normal_ks_margin"], 0.0)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["uniform-vs-normal evidence"]["status"], "FAIL")

    def test_middle_only_rows_fail_boundary_coverage(self) -> None:
        audit = _load_sampling_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rows = [
                {"ok": "true", "geom__w_um": 4.0 + idx * (12.0 / 19.0)}
                for idx in range(20)
            ]
            _write_dataset(root, rows, {"w_um": [0, 20]})

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--bins",
                    "10",
                    "--max-histogram-imbalance-frac",
                    "10",
                    "--no-require-uniform-closer-than-normal",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "sampling_distribution_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            record = summary["field_records"][0]
            self.assertFalse(record["boundary_coverage_ok"])
            self.assertFalse(record["min_boundary_ok"])
            self.assertFalse(record["max_boundary_ok"])
            self.assertAlmostEqual(record["min_norm"], 0.2)
            self.assertAlmostEqual(record["max_norm"], 0.8)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["per-field boundary coverage"]["status"], "FAIL")

    def test_duplicate_design_vectors_fail_space_filling_gate(self) -> None:
        audit = _load_sampling_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rows = []
            for idx in range(10):
                rows.append({"ok": "true", "geom__w_um": 0.5 + idx, "geom__h_um": 0.5 + idx})
                rows.append({"ok": "true", "geom__w_um": 0.5 + idx, "geom__h_um": 0.5 + idx})
            _write_dataset(root, rows, {"w_um": [0, 10], "h_um": [0, 10]})

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--bins",
                    "10",
                    "--max-histogram-imbalance-frac",
                    "10",
                    "--no-require-uniform-closer-than-normal",
                    "--max-space-filling-empty-strata-frac",
                    "1",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "sampling_distribution_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["space_filling_summary"]["duplicate_status"], "FAIL")
            self.assertGreater(summary["space_filling_summary"]["duplicate_fraction"], 0.0)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["space-filling duplicate vectors"]["status"], "FAIL")

    def test_missing_bounds_are_not_ready(self) -> None:
        audit = _load_sampling_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "dataset_manifest.json").write_text(json.dumps({}), encoding="utf-8")
            (root / "dataset_rows.csv").write_text("ok,geom__w_um\ntrue,1\n", encoding="utf-8")

            status = audit.main([str(root), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "sampling_distribution_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
