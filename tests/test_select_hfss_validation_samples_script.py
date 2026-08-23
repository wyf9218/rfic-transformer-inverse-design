from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_selector_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "select_hfss_validation_samples.py"
    spec = importlib.util.spec_from_file_location("select_hfss_validation_samples_script", script_path)
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
        for row in rows:
            writer.writerow(row)


class SelectHfssValidationSamplesScriptTest(TransformerToolboxTestBase):
    def test_selects_representative_labeled_samples(self) -> None:
        selector = _load_selector_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rows = []
            for idx in range(10):
                touchstone = root / "evaluations" / f"s{idx}" / "emx" / "emx.s4p"
                touchstone.parent.mkdir(parents=True)
                touchstone.write_text("# GHz S RI R 50\n", encoding="ascii")
                rows.append(
                    {
                        "evaluation": f"s{idx}",
                        "ok": "true",
                        "touchstone_path": str(touchstone.relative_to(root)),
                        "zin_center_real_ohm": 10 + idx * 5,
                        "zin_center_imag_ohm": -20 + idx * 7,
                        "zin_center_abs_ohm": 30 + idx * 4,
                        "k_center": -0.1 + idx * 0.01,
                        "qp_center": 8 + idx,
                        "qs_center": 7 + idx,
                        "lp_nh_center": 0.8 + idx * 0.1,
                        "ls_nh_center": 0.7 + idx * 0.1,
                    }
                )
            _write_dataset(root, rows)

            status = selector.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "selected"),
                    "--sample-count",
                    "6",
                    "--seed",
                    "123",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "selected" / "hfss_validation_sample_selection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["candidate_count"], 10)
            self.assertEqual(summary["selected_count"], 6)
            reasons = {reason for item in summary["selected"] for reason in item["selection_reasons"]}
            self.assertIn("min_abs_zin", reasons)
            self.assertIn("max_abs_zin", reasons)
            self.assertIn("selected_reason_counts", summary)
            self.assertIn("zin_bin_coverage_summary", summary)
            self.assertGreater(summary["zin_bin_coverage_summary"]["selected_occupied_2d_bins"], 0)
            self.assertTrue((root / "selected" / "hfss_validation_samples.csv").exists())
            self.assertTrue((root / "selected" / "hfss_validation_next_commands.md").exists())
            self.assertTrue((root / "selected" / "hfss_validation_sample_zin_map.png").exists())
            self.assertTrue((root / "selected" / "hfss_validation_sample_zin_map.svg").exists())

    def test_missing_zin_labels_are_not_ready(self) -> None:
        selector = _load_selector_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "evaluations" / "a" / "emx" / "emx.s4p"
            touchstone.parent.mkdir(parents=True)
            touchstone.write_text("# GHz S RI R 50\n", encoding="ascii")
            _write_dataset(root, [{"evaluation": "a", "ok": "true", "touchstone_path": str(touchstone.relative_to(root))}])

            status = selector.main([str(root), "--out-dir", str(root / "selected")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "selected" / "hfss_validation_sample_selection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            self.assertEqual(summary["candidate_count"], 0)

    def test_require_touchstone_filters_missing_files(self) -> None:
        selector = _load_selector_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(
                root,
                [
                    {
                        "evaluation": "a",
                        "ok": "true",
                        "touchstone_path": "missing.s4p",
                        "zin_center_real_ohm": 20,
                        "zin_center_imag_ohm": 30,
                    }
                ],
            )

            status = selector.main([str(root), "--out-dir", str(root / "selected")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "selected" / "hfss_validation_sample_selection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
