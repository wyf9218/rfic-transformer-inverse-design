from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


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


def _write_targets(plan_dir: Path) -> None:
    _write_csv(
        plan_dir / "zin_balanced_acquisition_targets.csv",
        [
            {
                "rank": 1,
                "real_bin": 0,
                "imag_bin": 0,
                "target_real_ohm": 5,
                "target_imag_ohm": -5,
                "real_min_ohm": 0,
                "real_max_ohm": 10,
                "imag_min_ohm": -10,
                "imag_max_ohm": 0,
                "recommended_new_samples": 2,
            },
            {
                "rank": 2,
                "real_bin": 1,
                "imag_bin": 0,
                "target_real_ohm": 15,
                "target_imag_ohm": -5,
                "real_min_ohm": 10,
                "real_max_ohm": 20,
                "imag_min_ohm": -10,
                "imag_max_ohm": 0,
                "recommended_new_samples": 1,
            },
        ],
    )


class SelectZinTargetedCandidateGeometriesScriptTest(TransformerToolboxTestBase):
    def test_selects_nearest_candidates_per_target_quota(self) -> None:
        mod = _load_selector_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            _write_targets(plan)
            candidates = root / "candidate_predictions.csv"
            _write_csv(
                candidates,
                [
                    {"candidate_id": "a", "pred_zin_center_real_ohm": 4.8, "pred_zin_center_imag_ohm": -5.1, "geom__w": 10},
                    {"candidate_id": "b", "pred_zin_center_real_ohm": 5.5, "pred_zin_center_imag_ohm": -4.8, "geom__w": 11},
                    {"candidate_id": "c", "pred_zin_center_real_ohm": 15.2, "pred_zin_center_imag_ohm": -5.0, "geom__w": 12},
                    {"candidate_id": "outside", "pred_zin_center_real_ohm": 80, "pred_zin_center_imag_ohm": 80, "geom__w": 13},
                ],
            )

            status = mod.main(["--plan-dir", str(plan), "--candidate-csv", str(candidates), "--out-dir", str(root / "out")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "zin_targeted_candidate_selection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["requested_candidate_count"], 3)
            self.assertEqual(summary["selected_candidate_count"], 3)
            with (root / "out" / "zin_targeted_candidate_selection.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual({row["candidate_id"] for row in rows}, {"a", "b", "c"})
            self.assertTrue(all(row["inside_target_bin"] == "True" for row in rows))

    def test_missing_prediction_columns_fails_without_selection(self) -> None:
        mod = _load_selector_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            _write_targets(plan)
            candidates = root / "candidate_predictions.csv"
            _write_csv(candidates, [{"candidate_id": "a", "geom__w": 10}])

            status = mod.main(
                [
                    "--plan-dir",
                    str(plan),
                    "--candidate-csv",
                    str(candidates),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "zin_targeted_candidate_selection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["selected_candidate_count"], 0)
            self.assertFalse(next(item for item in summary["checks"] if item["name"] == "prediction_real_column_present")["pass"])

    def test_insufficient_candidates_is_partial(self) -> None:
        mod = _load_selector_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            _write_targets(plan)
            candidates = root / "candidate_predictions.csv"
            _write_csv(
                candidates,
                [{"candidate_id": "a", "pred_zin_center_real_ohm": 4.8, "pred_zin_center_imag_ohm": -5.1}],
            )

            status = mod.main(
                [
                    "--plan-dir",
                    str(plan),
                    "--candidate-csv",
                    str(candidates),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "zin_targeted_candidate_selection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PARTIAL")
            self.assertEqual(summary["requested_candidate_count"], 3)
            self.assertEqual(summary["selected_candidate_count"], 1)

    def test_reachable_only_redistributes_quota_to_inside_candidates(self) -> None:
        mod = _load_selector_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = root / "plan"
            _write_targets(plan)
            candidates = root / "candidate_predictions.csv"
            _write_csv(
                candidates,
                [
                    {"candidate_id": "a", "pred_zin_center_real_ohm": 4.8, "pred_zin_center_imag_ohm": -5.1, "geom__w": 10},
                    {"candidate_id": "b", "pred_zin_center_real_ohm": 5.5, "pred_zin_center_imag_ohm": -4.8, "geom__w": 11},
                    {"candidate_id": "c", "pred_zin_center_real_ohm": 6.0, "pred_zin_center_imag_ohm": -6.0, "geom__w": 12},
                    {"candidate_id": "outside", "pred_zin_center_real_ohm": 80, "pred_zin_center_imag_ohm": 80, "geom__w": 13},
                ],
            )

            status = mod.main(
                [
                    "--plan-dir",
                    str(plan),
                    "--candidate-csv",
                    str(candidates),
                    "--out-dir",
                    str(root / "out"),
                    "--reachable-targets-only",
                    "--redistribute-reachable-quota",
                    "--max-total",
                    "3",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "zin_targeted_candidate_selection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["original_requested_candidate_count"], 3)
            self.assertEqual(summary["requested_candidate_count"], 3)
            self.assertEqual(summary["selected_candidate_count"], 3)
            self.assertEqual(summary["selected_inside_target_bin_count"], 3)
            self.assertEqual(summary["selection_diagnostics"]["reachable_target_count"], 1)
            self.assertEqual(summary["selection_diagnostics"]["unreachable_target_count"], 1)
            skipped = [row for row in summary["per_target"] if row["status"] == "UNREACHABLE_SKIPPED"]
            self.assertEqual(len(skipped), 1)
            with (root / "out" / "zin_targeted_candidate_selection.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual({row["candidate_id"] for row in rows}, {"a", "b", "c"})
            self.assertTrue(all(row["target_real_bin"] == "0" for row in rows))
            self.assertTrue(all(row["inside_target_bin"] == "True" for row in rows))
