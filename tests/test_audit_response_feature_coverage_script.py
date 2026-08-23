from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_response_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_response_feature_coverage.py"
    spec = importlib.util.spec_from_file_location("audit_response_feature_coverage_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_rows(root: Path, rows: list[dict[str, object]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _valid_row(evaluation: str, offset: float = 0.0) -> dict[str, object]:
    return {
        "evaluation": evaluation,
        "ok": "true",
        "lp_nh_center": 1.0 + offset,
        "ls_nh_center": 1.2 + offset,
        "k_center": -0.2 + offset * 0.05,
        "qp_center": 10.0 + offset,
        "qs_center": 12.0 + offset,
        "zin_center_real_ohm": 30.0 + offset,
        "zin_center_imag_ohm": 15.0 + offset,
        "zin_center_abs_ohm": 33.5 + offset,
        "cm_single_primary_y11_plus_y12_ff_center": 70.0 + offset,
        "cm_single_primary_y22_plus_y21_ff_center": 69.0 + offset,
        "cm_single_secondary_y33_plus_y34_ff_center": 80.0 + offset,
        "cm_single_secondary_y44_plus_y43_ff_center": 79.0 + offset,
        "cm_diff_primary_y11_plus_y12_ff_center": -82.0 - offset,
        "cm_diff_secondary_y22_plus_y21_ff_center": -95.0 - offset,
    }


class AuditResponseFeatureCoverageScriptTest(TransformerToolboxTestBase):
    def test_geometry_only_dataset_is_not_ready(self) -> None:
        response = _load_response_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_rows(root, [{"evaluation": "a", "ok": "true", "geom__w_um": 10}])

            status = response.main([str(root), "--out-dir", str(root / "response"), "--no-plots"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "response" / "response_feature_coverage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            self.assertEqual(summary["label_summary"]["valid_count"], 0)

    def test_response_labels_pass_physical_and_span_gates(self) -> None:
        response = _load_response_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_rows(root, [_valid_row("a", 0.0), _valid_row("b", 0.5), _valid_row("c", 1.0)])

            status = response.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "response"),
                    "--require-cm",
                    "--min-valid-count",
                    "3",
                    "--min-lp-span-nh",
                    "0.9",
                    "--min-occupied-k-q-bins",
                    "2",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "response" / "response_feature_coverage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["label_summary"]["valid_count"], 3)
            self.assertGreaterEqual(summary["coverage"]["k_qp_occupied_bins"], 2)
            self.assertTrue((root / "response" / "response_feature_metric_summary.csv").exists())

    def test_unphysical_k_fails(self) -> None:
        response = _load_response_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            row = _valid_row("a", 0.0)
            row["k_center"] = 1.4
            _write_rows(root, [row])

            status = response.main([str(root), "--out-dir", str(root / "response"), "--no-plots"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "response" / "response_feature_coverage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            failed = [item for item in summary["checks"] if item["status"] == "FAIL"]
            self.assertTrue(any(item["name"] == "K magnitude" for item in failed))

    def test_require_cm_keeps_missing_cm_from_passing(self) -> None:
        response = _load_response_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            row = _valid_row("a", 0.0)
            for key in list(row):
                if key.startswith("cm_"):
                    del row[key]
            _write_rows(root, [row])

            status = response.main([str(root), "--out-dir", str(root / "response"), "--require-cm", "--no-plots"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "response" / "response_feature_coverage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")

    def test_target_response_envelopes_pass_with_corner_points(self) -> None:
        response = _load_response_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rows = []
            corners = [
                ("a", -0.5, 5.0, 1.0, 1.0),
                ("b", 0.0, 5.0, 3.0, 1.0),
                ("c", 0.0, 15.0, 3.0, 3.0),
                ("d", -0.5, 15.0, 1.0, 3.0),
            ]
            for name, k, qp, lp, ls in corners:
                row = _valid_row(name, 0.0)
                row["k_center"] = k
                row["qp_center"] = qp
                row["lp_nh_center"] = lp
                row["ls_nh_center"] = ls
                rows.append(row)
            _write_rows(root, rows)

            status = response.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "response"),
                    "--bins",
                    "2",
                    "--target-k-min",
                    "-0.5",
                    "--target-k-max",
                    "0",
                    "--target-qp-min",
                    "5",
                    "--target-qp-max",
                    "15",
                    "--min-target-k-qp-area-frac",
                    "0.9",
                    "--min-target-k-qp-occupied-2d-bins",
                    "4",
                    "--max-target-k-qp-outside-frac",
                    "0",
                    "--target-lp-min-nh",
                    "1",
                    "--target-lp-max-nh",
                    "3",
                    "--target-ls-min-nh",
                    "1",
                    "--target-ls-max-nh",
                    "3",
                    "--min-target-lp-ls-area-frac",
                    "0.9",
                    "--min-target-lp-ls-occupied-2d-bins",
                    "4",
                    "--max-target-lp-ls-outside-frac",
                    "0",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "response" / "response_feature_coverage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["target_envelopes"]["k_qp"]["occupied_2d_bins"], 4)
            self.assertGreaterEqual(summary["target_envelopes"]["k_qp"]["inside_convex_hull_area_fraction"], 0.9)
            self.assertEqual(summary["target_envelopes"]["lp_ls"]["occupied_2d_bins"], 4)
            self.assertTrue((root / "response" / "response_target_k_qp_bins.csv").exists())
            self.assertTrue((root / "response" / "response_target_lp_ls_bins.csv").exists())

    def test_target_response_envelope_config_file_applies_both_envelopes(self) -> None:
        response = _load_response_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "response_target_envelopes.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "response_target_envelopes.v1",
                        "name": "fixture-response-targets",
                        "response_target_envelopes": {
                            "target_count_per_bin": 1,
                            "k_qp": {
                                "k_min": -0.5,
                                "k_max": 0,
                                "qp_min": 5,
                                "qp_max": 15,
                                "min_area_fraction": 0.9,
                                "min_occupied_2d_bins": 4,
                                "max_outside_fraction": 0,
                            },
                            "lp_ls": {
                                "lp_min_nh": 1,
                                "lp_max_nh": 3,
                                "ls_min_nh": 1,
                                "ls_max_nh": 3,
                                "min_area_fraction": 0.9,
                                "min_occupied_2d_bins": 4,
                                "max_outside_fraction": 0,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            rows = []
            for name, k, qp, lp, ls in [
                ("a", -0.5, 5.0, 1.0, 1.0),
                ("b", 0.0, 5.0, 3.0, 1.0),
                ("c", 0.0, 15.0, 3.0, 3.0),
                ("d", -0.5, 15.0, 1.0, 3.0),
            ]:
                row = _valid_row(name, 0.0)
                row["k_center"] = k
                row["qp_center"] = qp
                row["lp_nh_center"] = lp
                row["ls_nh_center"] = ls
                rows.append(row)
            _write_rows(root, rows)

            status = response.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "response"),
                    "--bins",
                    "2",
                    "--target-envelope-config",
                    str(config),
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "response" / "response_feature_coverage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["target_envelope_config"]["status"], "PASS")
            self.assertEqual(summary["target_envelope_config"]["name"], "fixture-response-targets")
            self.assertEqual(summary["target_envelopes"]["k_qp"]["occupied_2d_bins"], 4)
            self.assertEqual(summary["target_envelopes"]["lp_ls"]["occupied_2d_bins"], 4)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["response target envelope config"]["status"], "PASS")

    def test_bad_response_target_envelope_config_fails_even_without_labels(self) -> None:
        response = _load_response_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bad_config = root / "bad_response_target_envelopes.json"
            bad_config.write_text("{bad json", encoding="utf-8")
            _write_rows(root, [{"evaluation": "a", "ok": "true", "geom__w_um": 10}])

            status = response.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "response"),
                    "--target-envelope-config",
                    str(bad_config),
                    "--no-plots",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "response" / "response_feature_coverage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["response target envelope config"]["status"], "FAIL")
            self.assertIn("JSONDecodeError", checks["response target envelope config"]["detail"])

    def test_template_only_response_target_envelope_config_must_be_filled_before_use(self) -> None:
        response = _load_response_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "response_target_envelopes_template.json"
            template.write_text(
                json.dumps(
                    {
                        "schema": "response_target_envelopes.v1",
                        "status": "TEMPLATE_ONLY_DO_NOT_USE_FOR_PASS_CLAIMS",
                        "response_target_envelopes": {
                            "k_qp": {"k_min": None, "k_max": None, "qp_min": None, "qp_max": None},
                            "lp_ls": {"lp_min_nh": None, "lp_max_nh": None, "ls_min_nh": None, "ls_max_nh": None},
                        },
                    }
                ),
                encoding="utf-8",
            )
            _write_rows(root, [_valid_row("a", 0.0)])

            status = response.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "response"),
                    "--target-envelope-config",
                    str(template),
                    "--no-plots",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "response" / "response_feature_coverage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertIn("TEMPLATE_ONLY", checks["response target envelope config"]["detail"])

    def test_target_response_envelope_area_failure_is_explicit(self) -> None:
        response = _load_response_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rows = []
            for idx, (k, qp) in enumerate([(-0.24, 9.8), (-0.22, 9.8), (-0.22, 10.2), (-0.24, 10.2)]):
                row = _valid_row(chr(ord("a") + idx), 0.0)
                row["k_center"] = k
                row["qp_center"] = qp
                rows.append(row)
            _write_rows(root, rows)

            status = response.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "response"),
                    "--target-k-min",
                    "-0.5",
                    "--target-k-max",
                    "0",
                    "--target-qp-min",
                    "5",
                    "--target-qp-max",
                    "15",
                    "--min-target-k-qp-area-frac",
                    "0.5",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "response" / "response_feature_coverage_summary.json").read_text(encoding="utf-8"))
            failed = [item for item in summary["checks"] if item["status"] == "FAIL"]
            self.assertTrue(any(item["name"] == "k/qp target envelope hull area" for item in failed))
