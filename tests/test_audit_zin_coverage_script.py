from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_zin_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_zin_coverage.py"
    spec = importlib.util.spec_from_file_location("audit_zin_coverage_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_dataset(root: Path, rows: list[dict[str, object]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "dataset_manifest.json").write_text(
        json.dumps({"requested_count": len(rows), "ok_count": len(rows), "fail_count": 0}),
        encoding="utf-8",
    )
    fields = sorted({key for row in rows for key in row})
    with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class AuditZinCoverageScriptTest(TransformerToolboxTestBase):
    def test_geometry_only_dataset_is_not_ready_for_zin_claims(self) -> None:
        zin = _load_zin_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root, [{"evaluation": "a", "ok": "true", "geom__primary_width_um": 5.0}])

            status = zin.main([str(root), "--out-dir", str(root / "zin"), "--no-plots"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "zin" / "zin_coverage_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            self.assertEqual(summary["label_summary"]["valid_count"], 0)

    def test_zin_labels_pass_configured_span_and_bin_gates(self) -> None:
        zin = _load_zin_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(
                root,
                [
                    {"evaluation": "a", "ok": "true", "zin_center_real_ohm": 10, "zin_center_imag_ohm": -20},
                    {"evaluation": "b", "ok": "true", "zin_center_real_ohm": 25, "zin_center_imag_ohm": 0},
                    {"evaluation": "c", "ok": "true", "zin_center_real_ohm": 40, "zin_center_imag_ohm": 30},
                    {"evaluation": "d", "ok": "true", "zin_center_real_ohm": 55, "zin_center_imag_ohm": 45},
                ],
            )

            status = zin.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "zin"),
                    "--min-valid-count",
                    "4",
                    "--min-real-span-ohm",
                    "40",
                    "--min-imag-span-ohm",
                    "60",
                    "--min-real-bins",
                    "3",
                    "--min-imag-bins",
                    "3",
                    "--min-occupied-2d-bins",
                    "3",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "zin" / "zin_coverage_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["label_summary"]["valid_count"], 4)
            self.assertGreater(summary["label_summary"]["convex_hull_area_ohm2"], 0.0)
            self.assertGreaterEqual(summary["bin_occupancy"]["occupied_2d_bins"], 3)
            bins_csv = root / "zin" / "zin_coverage_bins.csv"
            self.assertTrue(bins_csv.exists())
            with bins_csv.open(newline="", encoding="utf-8") as handle:
                cells = list(csv.DictReader(handle))
            self.assertTrue(any(row["status"] == "empty" for row in cells))
            self.assertTrue(any(row["status"] == "covered" for row in cells))

    def test_zin_span_threshold_failure_is_explicit(self) -> None:
        zin = _load_zin_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(
                root,
                [
                    {"evaluation": "a", "ok": "true", "zin_center_real_ohm": 10, "zin_center_imag_ohm": -5},
                    {"evaluation": "b", "ok": "true", "zin_center_real_ohm": 12, "zin_center_imag_ohm": -4},
                ],
            )

            status = zin.main([str(root), "--out-dir", str(root / "zin"), "--min-real-span-ohm", "10", "--no-plots"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "zin" / "zin_coverage_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            failed = [item for item in summary["checks"] if item["status"] == "FAIL"]
            self.assertTrue(any(item["name"] == "Zin real span" for item in failed))

    def test_target_envelope_coverage_passes_with_corner_points(self) -> None:
        zin = _load_zin_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(
                root,
                [
                    {"evaluation": "a", "ok": "true", "zin_center_real_ohm": 0, "zin_center_imag_ohm": -50},
                    {"evaluation": "b", "ok": "true", "zin_center_real_ohm": 100, "zin_center_imag_ohm": -50},
                    {"evaluation": "c", "ok": "true", "zin_center_real_ohm": 100, "zin_center_imag_ohm": 50},
                    {"evaluation": "d", "ok": "true", "zin_center_real_ohm": 0, "zin_center_imag_ohm": 50},
                ],
            )

            status = zin.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "zin"),
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
                    "--min-target-envelope-area-frac",
                    "0.9",
                    "--min-target-envelope-occupied-2d-bins",
                    "4",
                    "--max-target-envelope-outside-frac",
                    "0",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "zin" / "zin_coverage_audit_summary.json").read_text(encoding="utf-8"))
            target = summary["target_envelope_summary"]
            self.assertEqual(target["status"], "PASS")
            self.assertEqual(target["outside_fraction"], 0.0)
            self.assertGreaterEqual(target["inside_convex_hull_area_fraction"], 0.9)
            self.assertEqual(target["occupied_2d_bins"], 4)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["Zin target envelope hull area"]["status"], "PASS")
            self.assertTrue((root / "zin" / "zin_target_envelope_bins.csv").exists())

    def test_target_envelope_config_file_applies_bounds_and_thresholds(self) -> None:
        zin = _load_zin_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "zin_target_envelope.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "zin_target_envelope.v1",
                        "name": "fixture-target-envelope",
                        "zin_target_envelope": {
                            "real_min_ohm": 0,
                            "real_max_ohm": 100,
                            "imag_min_ohm": -50,
                            "imag_max_ohm": 50,
                            "min_area_fraction": 0.9,
                            "min_occupied_2d_bins": 4,
                            "max_outside_fraction": 0,
                            "target_count_per_bin": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            _write_dataset(
                root,
                [
                    {"evaluation": "a", "ok": "true", "zin_center_real_ohm": 0, "zin_center_imag_ohm": -50},
                    {"evaluation": "b", "ok": "true", "zin_center_real_ohm": 100, "zin_center_imag_ohm": -50},
                    {"evaluation": "c", "ok": "true", "zin_center_real_ohm": 100, "zin_center_imag_ohm": 50},
                    {"evaluation": "d", "ok": "true", "zin_center_real_ohm": 0, "zin_center_imag_ohm": 50},
                ],
            )

            status = zin.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "zin"),
                    "--bins",
                    "2",
                    "--target-envelope-config",
                    str(config),
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "zin" / "zin_coverage_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["target_envelope_config"]["status"], "PASS")
            self.assertEqual(summary["target_envelope_config"]["name"], "fixture-target-envelope")
            self.assertEqual(summary["target_envelope_summary"]["outside_fraction"], 0.0)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["Zin target envelope config"]["status"], "PASS")

    def test_bad_target_envelope_config_fails_even_without_labels(self) -> None:
        zin = _load_zin_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bad_config = root / "bad_zin_target_envelope.json"
            bad_config.write_text("{bad json", encoding="utf-8")
            _write_dataset(root, [{"evaluation": "a", "ok": "true", "geom__primary_width_um": 5.0}])

            status = zin.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "zin"),
                    "--target-envelope-config",
                    str(bad_config),
                    "--no-plots",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "zin" / "zin_coverage_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["Zin target envelope config"]["status"], "FAIL")
            self.assertIn("JSONDecodeError", checks["Zin target envelope config"]["detail"])

    def test_template_only_target_envelope_config_must_be_filled_before_use(self) -> None:
        zin = _load_zin_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "zin_target_envelope_template.json"
            template.write_text(
                json.dumps(
                    {
                        "schema": "zin_target_envelope.v1",
                        "status": "TEMPLATE_ONLY_DO_NOT_USE_FOR_PASS_CLAIMS",
                        "zin_target_envelope": {
                            "real_min_ohm": None,
                            "real_max_ohm": None,
                            "imag_min_ohm": None,
                            "imag_max_ohm": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            _write_dataset(
                root,
                [{"evaluation": "a", "ok": "true", "zin_center_real_ohm": 10, "zin_center_imag_ohm": -5}],
            )

            status = zin.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "zin"),
                    "--target-envelope-config",
                    str(template),
                    "--no-plots",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "zin" / "zin_coverage_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertIn("TEMPLATE_ONLY", checks["Zin target envelope config"]["detail"])

    def test_target_envelope_area_failure_is_explicit(self) -> None:
        zin = _load_zin_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(
                root,
                [
                    {"evaluation": "a", "ok": "true", "zin_center_real_ohm": 48, "zin_center_imag_ohm": -2},
                    {"evaluation": "b", "ok": "true", "zin_center_real_ohm": 52, "zin_center_imag_ohm": -2},
                    {"evaluation": "c", "ok": "true", "zin_center_real_ohm": 52, "zin_center_imag_ohm": 2},
                    {"evaluation": "d", "ok": "true", "zin_center_real_ohm": 48, "zin_center_imag_ohm": 2},
                ],
            )

            status = zin.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "zin"),
                    "--target-real-min-ohm",
                    "0",
                    "--target-real-max-ohm",
                    "100",
                    "--target-imag-min-ohm",
                    "-50",
                    "--target-imag-max-ohm",
                    "50",
                    "--min-target-envelope-area-frac",
                    "0.5",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "zin" / "zin_coverage_audit_summary.json").read_text(encoding="utf-8"))
            failed = [item for item in summary["checks"] if item["status"] == "FAIL"]
            self.assertTrue(any(item["name"] == "Zin target envelope hull area" for item in failed))
