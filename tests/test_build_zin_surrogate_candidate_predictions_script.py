from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_builder_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_zin_surrogate_candidate_predictions.py"
    spec = importlib.util.spec_from_file_location("build_zin_surrogate_candidate_predictions_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_selector_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "select_zin_targeted_candidate_geometries.py"
    spec = importlib.util.spec_from_file_location("select_zin_targeted_candidate_geometries_script", script_path)
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


def _write_training_dataset(root: Path) -> None:
    rows = []
    for idx in range(12):
        w = 1.0 + idx
        s = 2.0 + (idx % 4)
        rows.append(
            {
                "evaluation": f"r{idx}",
                "ok": "true",
                "geom__w_um": w,
                "geom__s_um": s,
                "zin_center_real_ohm": 10.0 + 2.0 * w,
                "zin_center_imag_ohm": -20.0 + 3.0 * s,
            }
        )
    _write_csv(root / "dataset_rows.csv", rows)
    (root / "dataset_manifest.json").write_text(
        json.dumps({"bounds": {"w_um": [1.0, 12.0], "s_um": [2.0, 5.0]}}),
        encoding="utf-8",
    )


class BuildZinSurrogateCandidatePredictionsScriptTest(TransformerToolboxTestBase):
    def test_builds_candidate_prediction_csv_from_real_training_rows(self) -> None:
        mod = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_training_dataset(root)

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "predictions"),
                    "--candidate-count",
                    "16",
                    "--prediction-batch-size",
                    "5",
                    "--seed",
                    "7",
                    "--k-neighbors",
                    "3",
                    "--no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "predictions" / "candidate_zin_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["training_count"], 12)
            self.assertEqual(summary["candidate_count"], 16)
            self.assertEqual(summary["validation"]["status"], "PASS")
            self.assertEqual(summary["bounds"]["geom__w_um"]["source"], "dataset_manifest_bounds")
            with (root / "predictions" / "candidate_zin_predictions.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 16)
            self.assertIn("pred_zin_center_real_ohm", rows[0])
            self.assertIn("geom__w_um", rows[0])
            self.assertEqual(rows[0]["pred_source"], "knn_idw_surrogate_for_candidate_priority_only")

    def test_nonpositive_prediction_batch_size_fails_without_candidates(self) -> None:
        mod = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_training_dataset(root)

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "predictions"),
                    "--candidate-count",
                    "8",
                    "--prediction-batch-size",
                    "0",
                    "--no-plots",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "predictions" / "candidate_zin_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["candidate_count"], 0)

    def test_missing_zin_labels_fails_without_fabricating_candidates(self) -> None:
        mod = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_csv(root / "dataset_rows.csv", [{"ok": "true", "geom__w_um": 1.0, "geom__s_um": 2.0}])

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "predictions"),
                    "--candidate-count",
                    "8",
                    "--no-plots",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "predictions" / "candidate_zin_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["candidate_count"], 0)

    def test_predictions_feed_target_selector(self) -> None:
        builder = _load_builder_module()
        selector = _load_selector_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_training_dataset(root)
            self.assertEqual(
                builder.main(
                    [
                        str(root),
                        "--out-dir",
                        str(root / "predictions"),
                        "--candidate-count",
                        "64",
                        "--seed",
                        "11",
                        "--k-neighbors",
                        "3",
                        "--no-plots",
                    ]
                ),
                0,
            )
            _write_csv(
                root / "plan" / "zin_balanced_acquisition_targets.csv",
                [
                    {
                        "rank": 1,
                        "real_bin": 0,
                        "imag_bin": 0,
                        "target_real_ohm": 22,
                        "target_imag_ohm": -10,
                        "real_min_ohm": 15,
                        "real_max_ohm": 35,
                        "imag_min_ohm": -16,
                        "imag_max_ohm": -4,
                        "recommended_new_samples": 4,
                    }
                ],
            )

            status = selector.main(
                [
                    "--plan-dir",
                    str(root / "plan"),
                    "--candidate-csv",
                    str(root / "predictions" / "candidate_zin_predictions.csv"),
                    "--out-dir",
                    str(root / "selection"),
                    "--max-total",
                    "4",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "selection" / "zin_targeted_candidate_selection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["selected_candidate_count"], 4)
