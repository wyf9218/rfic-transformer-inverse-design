from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "derive_scalar_q_feature.py"
    spec = importlib.util.spec_from_file_location("derive_scalar_q_feature_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class DeriveScalarQFeatureScriptTest(TransformerToolboxTestBase):
    def test_derives_min_q_and_rewrites_relative_touchstone_path(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_rows(
                root / "dataset_rows.csv",
                [
                    {
                        "evaluation": "a",
                        "ok": "true",
                        "touchstone_path": "evaluations/a/emx/emx.s8p",
                        "qp_center": 12.0,
                        "qs_center": 9.0,
                    },
                    {
                        "evaluation": "b",
                        "ok": "true",
                        "touchstone_path": "evaluations/b/emx/emx.s8p",
                        "qp_center": 8.0,
                        "qs_center": 10.0,
                    },
                ],
            )
            (root / "dataset_manifest.json").write_text(json.dumps({"ok_count": 2}), encoding="utf-8")

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "scalar"),
                    "--q-definition",
                    "min",
                ]
            )

            self.assertEqual(status, 0)
            rows = list(csv.DictReader((root / "scalar" / "dataset_rows.csv").open(newline="", encoding="utf-8")))
            self.assertEqual([float(row["q_center"]) for row in rows], [9.0, 8.0])
            self.assertTrue(Path(rows[0]["touchstone_path"]).is_absolute())
            manifest = json.loads((root / "scalar" / "dataset_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["scalar_q_feature"]["definition"], "min")
            summary = json.loads((root / "scalar" / "scalar_q_feature_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")

    def test_missing_q_values_fail_without_fabricating_q(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_rows(root / "dataset_rows.csv", [{"evaluation": "a", "ok": "true", "qp_center": 12.0}])
            (root / "dataset_manifest.json").write_text("{}", encoding="utf-8")

            status = mod.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "scalar"),
                    "--q-definition",
                    "mean",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "scalar" / "scalar_q_feature_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            rows = list(csv.DictReader((root / "scalar" / "dataset_rows.csv").open(newline="", encoding="utf-8")))
            self.assertNotIn("q_center", rows[0])
