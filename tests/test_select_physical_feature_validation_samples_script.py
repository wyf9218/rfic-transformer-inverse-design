from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "select_physical_feature_validation_samples.py"
    spec = importlib.util.spec_from_file_location("select_physical_feature_validation_samples_script", script_path)
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


def _write_dataset(root: Path) -> None:
    rows = []
    for idx in range(10):
        rows.append(
            {
                "evaluation": f"eval_{idx}",
                "ok": "true",
                "touchstone_path": f"evaluations/eval_{idx}/emx/emx.s8p",
                "lp_nh_center": 0.5 + 0.1 * idx,
                "ls_nh_center": 0.7 + 0.08 * idx,
                "qp_center": 8.0 + idx,
                "qs_center": 7.0 + 0.5 * idx,
                "q_center": min(8.0 + idx, 7.0 + 0.5 * idx),
                "k_center": 0.3 + 0.02 * idx,
            }
        )
    _write_csv(root / "dataset_rows.csv", rows)


class SelectPhysicalFeatureValidationSamplesScriptTest(TransformerToolboxTestBase):
    def test_selects_seeded_random_sample_from_physical_feature_rows(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "samples"),
                    "--sample-count",
                    "1",
                    "--seed",
                    "123",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "samples" / "physical_feature_validation_sample_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["candidate_count"], 10)
            self.assertEqual(summary["selected_sample_count"], 1)
            selected = summary["selected"][0]
            self.assertEqual(selected["selection_reason"], "deterministic_random_seeded_sample")
            self.assertTrue(str(selected["touchstone_path"]).endswith(".s8p"))
            self.assertIn("lp_nh_center", selected)

    def test_missing_physical_feature_labels_fails_without_fabricating_sample(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_csv(root / "dataset_rows.csv", [{"ok": "true", "touchstone_path": "a.s8p"}])

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "samples"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "samples" / "physical_feature_validation_sample_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["selected_sample_count"], 0)

    def test_coverage_mode_includes_feature_extremes_before_random_fill(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "samples"),
                    "--sample-count",
                    "4",
                    "--mode",
                    "coverage_then_random",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "samples" / "physical_feature_validation_sample_summary.json").read_text(encoding="utf-8"))
            reasons = {row["selection_reason"] for row in summary["selected"]}
            self.assertTrue(any(reason.startswith("min_") for reason in reasons))
            self.assertTrue(any(reason.startswith("max_") for reason in reasons))
            self.assertEqual(summary["selected_sample_count"], 4)
