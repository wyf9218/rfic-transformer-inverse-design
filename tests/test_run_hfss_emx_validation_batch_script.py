from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_batch_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_hfss_emx_validation_batch.py"
    spec = importlib.util.spec_from_file_location("run_hfss_emx_validation_batch_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_metric_csv(path: Path, *, offset: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["freq_ghz", "k", "qp", "qs", "lp_nh", "ls_nh"])
        writer.writeheader()
        for index in range(111):
            freq = 5.0 + index * 0.5
            writer.writerow(
                {
                    "freq_ghz": f"{freq:.1f}",
                    "k": f"{0.42 + offset:.6g}",
                    "qp": f"{12.0 + offset:.6g}",
                    "qs": f"{11.0 + offset:.6g}",
                    "lp_nh": f"{0.82 + offset:.6g}",
                    "ls_nh": f"{0.76 + offset:.6g}",
                }
            )


def _write_selection(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["rank", "evaluation", "selection_reasons", "touchstone_path"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class RunHfssEmxValidationBatchScriptTest(TransformerToolboxTestBase):
    def test_plans_missing_hfss_files_without_claiming_pass(self) -> None:
        batch = _load_batch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx" / "sample_a.csv"
            _write_metric_csv(emx)
            selection = root / "selected" / "hfss_validation_samples.csv"
            _write_selection(
                selection,
                [
                    {
                        "rank": "1",
                        "evaluation": "sample_a",
                        "selection_reasons": "seeded_random_fill",
                        "touchstone_path": str(emx),
                    }
                ],
            )

            status = batch.main(
                [
                    "--selection-csv",
                    str(selection),
                    "--hfss-dir",
                    str(root / "hfss_exports"),
                    "--out-dir",
                    str(root / "batch"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "batch" / "hfss_emx_validation_batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_HFSS")
            self.assertEqual(summary["status_counts"], {"MISSING_HFSS": 1})
            self.assertTrue((root / "batch" / "hfss_emx_validation_batch_commands.sh").exists())
            missing = (root / "batch" / "hfss_emx_validation_missing_files.csv").read_text(encoding="utf-8")
            self.assertIn("sample_a", missing)

    def test_runs_available_pairs_with_strict_grid_gate(self) -> None:
        batch = _load_batch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx" / "sample_a.csv"
            hfss = root / "hfss_exports" / "sample_a.csv"
            _write_metric_csv(emx)
            _write_metric_csv(hfss)
            selection = root / "selected" / "hfss_validation_samples.csv"
            _write_selection(
                selection,
                [
                    {
                        "rank": "1",
                        "evaluation": "sample_a",
                        "selection_reasons": "seeded_random_fill",
                        "touchstone_path": str(emx),
                    }
                ],
            )

            status = batch.main(
                [
                    "--selection-csv",
                    str(selection),
                    "--hfss-dir",
                    str(root / "hfss_exports"),
                    "--out-dir",
                    str(root / "batch"),
                    "--run-available",
                    "--require-all-present",
                    "--require-all-pass",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "batch" / "hfss_emx_validation_batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["status_counts"], {"PASS": 1})
            record = summary["records"][0]
            self.assertEqual(record["worst_percent_error"], 0.0)
            self.assertEqual(record["no_extrapolation_status"], "PASS")
            self.assertIn("ADS no-extrapolation coverage=PASS", record["detail"])
            compare_summary = Path(record["summary_path"])
            self.assertTrue(compare_summary.exists())
            compare_data = json.loads(compare_summary.read_text(encoding="utf-8"))
            self.assertEqual(compare_data["overall_status"], "PASS")
            grid_checks = {name: item["status"] for name, item in compare_data["frequency_grid_checks"].items()}
            self.assertEqual(grid_checks["expected frequency points"], "PASS")
            self.assertEqual(grid_checks["expected frequency step"], "PASS")
            self.assertEqual(grid_checks["ADS no-extrapolation coverage"], "PASS")

    def test_rejects_compare_summary_without_no_extrapolation_evidence(self) -> None:
        batch = _load_batch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx" / "sample_a.csv"
            hfss = root / "hfss_exports" / "sample_a.csv"
            _write_metric_csv(emx)
            _write_metric_csv(hfss)
            selection = root / "selected" / "hfss_validation_samples.csv"
            _write_selection(
                selection,
                [
                    {
                        "rank": "1",
                        "evaluation": "sample_a",
                        "selection_reasons": "seeded_random_fill",
                        "touchstone_path": str(emx),
                    }
                ],
            )
            fake_compare = root / "fake_compare.py"
            fake_compare.write_text(
                "\n".join(
                    [
                        "import argparse, json",
                        "from pathlib import Path",
                        "parser = argparse.ArgumentParser()",
                        "parser.add_argument('--out-dir', required=True)",
                        "parser.add_argument('--emx')",
                        "parser.add_argument('--hfss')",
                        "parser.add_argument('--emx-port-pairs')",
                        "parser.add_argument('--hfss-port-pairs')",
                        "parser.add_argument('--compare-start-ghz')",
                        "parser.add_argument('--compare-stop-ghz')",
                        "parser.add_argument('--min-frequency-points')",
                        "parser.add_argument('--expected-frequency-step-ghz')",
                        "parser.add_argument('--expected-frequency-points')",
                        "parser.add_argument('--frequency-tolerance-hz')",
                        "parser.add_argument('--max-percent-error')",
                        "parser.add_argument('--require-matching-frequency-grid', action='store_true')",
                        "parser.add_argument('--no-fail-exit', action='store_true')",
                        "args = parser.parse_args()",
                        "out = Path(args.out_dir)",
                        "out.mkdir(parents=True, exist_ok=True)",
                        "summary = {",
                        "  'overall_status': 'PASS',",
                        "  'frequency_grid_checks': {'expected frequency points': {'status': 'PASS'}},",
                        "  'metrics': {name: {'status': 'PASS', 'max_percent_error': 0.0} for name in ('k','qp','qs','lp_nh','ls_nh')},",
                        "}",
                        "(out / 'emx_hfss_ads_comparison_summary.json').write_text(json.dumps(summary), encoding='utf-8')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            status = batch.main(
                [
                    "--selection-csv",
                    str(selection),
                    "--hfss-dir",
                    str(root / "hfss_exports"),
                    "--out-dir",
                    str(root / "batch"),
                    "--compare-script",
                    str(fake_compare),
                    "--run-available",
                    "--require-all-present",
                    "--require-all-pass",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "batch" / "hfss_emx_validation_batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            record = summary["records"][0]
            self.assertEqual(record["status"], "FAIL")
            self.assertEqual(record["no_extrapolation_status"], "MISSING")

    def test_rejects_spoofed_passing_summary_with_metric_error_over_gate(self) -> None:
        batch = _load_batch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx" / "sample_a.csv"
            hfss = root / "hfss_exports" / "sample_a.csv"
            _write_metric_csv(emx)
            _write_metric_csv(hfss)
            selection = root / "selected" / "hfss_validation_samples.csv"
            _write_selection(
                selection,
                [
                    {
                        "rank": "1",
                        "evaluation": "sample_a",
                        "selection_reasons": "seeded_random_fill",
                        "touchstone_path": str(emx),
                    }
                ],
            )
            fake_compare = root / "fake_compare_metric_fail.py"
            fake_compare.write_text(
                "\n".join(
                    [
                        "import argparse, json",
                        "from pathlib import Path",
                        "parser = argparse.ArgumentParser()",
                        "parser.add_argument('--out-dir', required=True)",
                        "parser.add_argument('--emx')",
                        "parser.add_argument('--hfss')",
                        "parser.add_argument('--emx-port-pairs')",
                        "parser.add_argument('--hfss-port-pairs')",
                        "parser.add_argument('--compare-start-ghz')",
                        "parser.add_argument('--compare-stop-ghz')",
                        "parser.add_argument('--min-frequency-points')",
                        "parser.add_argument('--expected-frequency-step-ghz')",
                        "parser.add_argument('--expected-frequency-points')",
                        "parser.add_argument('--frequency-tolerance-hz')",
                        "parser.add_argument('--max-percent-error')",
                        "parser.add_argument('--require-matching-frequency-grid', action='store_true')",
                        "parser.add_argument('--no-fail-exit', action='store_true')",
                        "args = parser.parse_args()",
                        "out = Path(args.out_dir)",
                        "out.mkdir(parents=True, exist_ok=True)",
                        "summary = {",
                        "  'overall_status': 'PASS',",
                        "  'criterion': {'max_percent_error': 5.0},",
                        "  'emx_source': str(Path(args.emx).resolve()),",
                        "  'hfss_ads_source': str(Path(args.hfss).resolve()),",
                        "  'frequency_window_hz': {'min': 5.0e9, 'max': 50.0e9, 'count': 451},",
                        "  'frequency_grid_checks': {",
                        "    'ADS no-extrapolation coverage': {'status': 'PASS'},",
                        "    'expected frequency points': {'status': 'PASS'},",
                        "    'expected frequency step': {'status': 'PASS'},",
                        "    'matching HFSS/ADS frequency grid': {'status': 'PASS'},",
                        "  },",
                        "  'metrics': {name: {'status': 'PASS', 'max_percent_error': (6.0 if name == 'k' else 0.0)} for name in ('k','qp','qs','lp_nh','ls_nh')},",
                        "}",
                        "(out / 'emx_hfss_ads_comparison_summary.json').write_text(json.dumps(summary), encoding='utf-8')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            status = batch.main(
                [
                    "--selection-csv",
                    str(selection),
                    "--hfss-dir",
                    str(root / "hfss_exports"),
                    "--out-dir",
                    str(root / "batch"),
                    "--compare-script",
                    str(fake_compare),
                    "--run-available",
                    "--require-all-present",
                    "--require-all-pass",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "batch" / "hfss_emx_validation_batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            record = summary["records"][0]
            self.assertEqual(record["status"], "FAIL")
            self.assertIn("metric_k_max_percent_error=6.0", record["detail"])

    def test_require_all_present_fails_when_hfss_is_missing(self) -> None:
        batch = _load_batch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx" / "sample_a.csv"
            _write_metric_csv(emx)
            selection = root / "selected" / "hfss_validation_samples.csv"
            _write_selection(
                selection,
                [
                    {
                        "rank": "1",
                        "evaluation": "sample_a",
                        "selection_reasons": "seeded_random_fill",
                        "touchstone_path": str(emx),
                    }
                ],
            )

            status = batch.main(
                [
                    "--selection-csv",
                    str(selection),
                    "--hfss-dir",
                    str(root / "hfss_exports"),
                    "--out-dir",
                    str(root / "batch"),
                    "--require-all-present",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "batch" / "hfss_emx_validation_batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
