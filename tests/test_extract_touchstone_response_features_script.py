from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_extract_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "extract_touchstone_response_features.py"
    spec = importlib.util.spec_from_file_location("extract_touchstone_response_features_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_zin_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_zin_coverage.py"
    spec = importlib.util.spec_from_file_location("audit_zin_coverage_script_for_extract", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_synthetic_transformer_s4p(path: Path, freqs_hz: np.ndarray) -> None:
    target = default_target_spec()
    diff = build_lumped_transformer_sparameters(freqs_hz=freqs_hz, target=target, q_primary=18.0, q_secondary=16.0)
    single = differential_2port_to_4port_s(
        freqs_hz=freqs_hz,
        s_diff=diff.s_matrix,
        diff_z0_ohm=target.differential_reference_impedance_ohm,
        single_z0_ohm=50.0,
    )
    _write_touchstone(path, single.freqs_hz, single.s_matrix)


class ExtractTouchstoneResponseFeaturesScriptTest(TransformerToolboxTestBase):
    def test_extracts_zin_labels_compatible_with_zin_audit(self) -> None:
        extract = _load_extract_module()
        zin = _load_zin_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "evaluations" / "a" / "emx" / "emx.s4p"
            touchstone.parent.mkdir(parents=True)
            _write_synthetic_transformer_s4p(touchstone, np.asarray([5.0e9, 10.0e9, 15.0e9]))
            (root / "dataset_rows.csv").write_text("evaluation,ok\na,true\n", encoding="utf-8")

            status = extract.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "features"),
                    "--target-frequency-ghz",
                    "10",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "15",
                    "--expected-frequency-step-ghz",
                    "5",
                    "--expected-frequency-points",
                    "3",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "features" / "response_feature_extraction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["counts"]["ok_rows"], 1)
            with (root / "features" / "dataset_rows.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["ok"], "true")
            self.assertTrue(float(rows[0]["zin_center_abs_ohm"]) > 0.0)
            self.assertEqual(rows[0]["zin_center_mode"], "primary_loaded_50ohm_differential")
            self.assertIn("cm_single_primary_y11_plus_y12_ff_center", rows[0])
            self.assertIn("cm_diff_primary_y11_plus_y12_ff_center", rows[0])
            self.assertTrue(np.isfinite(float(rows[0]["cm_single_primary_y11_plus_y12_ff_center"])))
            self.assertTrue(np.isfinite(float(rows[0]["cm_diff_primary_y11_plus_y12_ff_center"])))
            self.assertIn("cm_definitions", summary)

            zin_status = zin.main([str(root / "features"), "--out-dir", str(root / "zin"), "--min-valid-count", "1", "--no-plots"])
            self.assertEqual(zin_status, 0)

    def test_missing_touchstone_declared_in_rows_fails(self) -> None:
        extract = _load_extract_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "dataset_rows.csv").write_text("evaluation,ok\na,true\n", encoding="utf-8")

            status = extract.main([str(root), "--out-dir", str(root / "features")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "features" / "response_feature_extraction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["counts"]["fail_rows"], 1)
            self.assertIn("FileNotFoundError", summary["failures"][0]["error"])

    def test_empty_directory_is_incomplete_not_pass(self) -> None:
        extract = _load_extract_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            status = extract.main([str(root), "--out-dir", str(root / "features")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "features" / "response_feature_extraction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            self.assertEqual(summary["counts"]["touchstone_candidates"], 0)
