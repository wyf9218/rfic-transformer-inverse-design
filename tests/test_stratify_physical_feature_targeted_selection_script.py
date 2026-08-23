from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "stratify_physical_feature_targeted_selection.py"
    spec = importlib.util.spec_from_file_location("stratify_physical_feature_targeted_selection_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StratifyPhysicalFeatureTargetedSelectionTest(TransformerToolboxTestBase):
    def test_preserves_target_bin_proportions_and_best_scores(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "selection.csv"
            rows = []
            for idx in range(8):
                rows.append({"candidate_id": f"a{idx}", "target_bin_key": "A", "inside_target_bin": "true", "selection_score": idx})
            for idx in range(4):
                rows.append({"candidate_id": f"b{idx}", "target_bin_key": "B", "inside_target_bin": "true", "selection_score": idx})
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            status = module.main(
                [
                    "--selection-csv",
                    str(source),
                    "--out-dir",
                    str(root / "out"),
                    "--count",
                    "6",
                    "--output-order",
                    "source",
                ]
            )

            self.assertEqual(status, 0)
            with (root / "out" / "physical_feature_targeted_candidate_selection.csv").open(newline="", encoding="utf-8") as handle:
                selected = list(csv.DictReader(handle))
            self.assertEqual([row["candidate_id"] for row in selected], ["a0", "a1", "a2", "a3", "b0", "b1"])
            summary = json.loads((root / "out" / "physical_feature_targeted_selection_stratification_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["selected_count"], 6)
            self.assertEqual({row["target_bin_key"]: row["selected_count"] for row in summary["groups"]}, {"A": 4, "B": 2})

    def test_interleaves_target_bins_for_balanced_partial_checkpoints(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "selection.csv"
            rows = [
                {"candidate_id": f"{group}{idx}", "target_bin_key": group, "inside_target_bin": "true", "selection_score": idx}
                for group in ("A", "B")
                for idx in range(3)
            ]
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            status = module.main(["--selection-csv", str(source), "--out-dir", str(root / "out"), "--count", "6"])

            self.assertEqual(status, 0)
            with (root / "out" / "physical_feature_targeted_candidate_selection.csv").open(newline="", encoding="utf-8") as handle:
                selected = list(csv.DictReader(handle))
            self.assertEqual([row["candidate_id"] for row in selected], ["A0", "B0", "A1", "B1", "A2", "B2"])

    def test_can_prioritize_lower_em_cost_inside_each_target_bin(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "selection.csv"
            rows = []
            for candidate_id, width in (("large", 20.0), ("small", 10.0)):
                row = {
                    "candidate_id": candidate_id,
                    "target_bin_key": "A",
                    "inside_target_bin": "true",
                    "selection_score": "0.1",
                }
                for field in (
                    "primary_outer_width_um",
                    "primary_outer_height_um",
                    "secondary_outer_width_um",
                    "secondary_outer_height_um",
                ):
                    row[f"candidate__geom__{field}"] = width
                rows.append(row)
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            status = module.main(
                [
                    "--selection-csv",
                    str(source),
                    "--out-dir",
                    str(root / "out"),
                    "--count",
                    "2",
                    "--within-group-order",
                    "estimated_em_cost",
                ]
            )

            self.assertEqual(status, 0)
            with (root / "out" / "physical_feature_targeted_candidate_selection.csv").open(newline="", encoding="utf-8") as handle:
                selected = list(csv.DictReader(handle))
            self.assertEqual([row["candidate_id"] for row in selected], ["small", "large"])
