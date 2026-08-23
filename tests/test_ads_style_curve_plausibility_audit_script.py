from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_audit_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "audit_ads_style_curve_plausibility.py"
    )
    spec = importlib.util.spec_from_file_location("ads_style_curve_plausibility_audit_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_curves(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source",
                "freq_hz",
                "freq_ghz",
                "lp_nh",
                "ls_nh",
                "m_nh",
                "k",
                "qp",
                "qs",
                "cm_single_primary_y11_plus_y12_ff",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _good_rows() -> list[dict[str, object]]:
    rows = []
    for source, k_sign in [("EMX", -1), ("HFSS", -1)]:
        for idx, freq in enumerate([14.5, 15.0, 15.5]):
            rows.append(
                {
                    "source": source,
                    "freq_hz": freq * 1e9,
                    "freq_ghz": freq,
                    "lp_nh": 1.4 + 0.05 * idx,
                    "ls_nh": 1.1 + 0.04 * idx,
                    "m_nh": k_sign * 0.25,
                    "k": k_sign * (0.22 + 0.01 * idx),
                    "qp": 12.0 - idx,
                    "qs": 14.0 - idx,
                    "cm_single_primary_y11_plus_y12_ff": 80.0 + idx,
                }
            )
    return rows


class AdsStyleCurvePlausibilityAuditScriptTest(TransformerToolboxTestBase):
    def test_good_transformer_like_curves_pass(self) -> None:
        mod = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            curves = root / "curves.csv"
            _write_curves(curves, _good_rows())

            status = mod.main(["--curves-csv", str(curves), "--out-dir", str(root / "out")])

            self.assertEqual(status, 0)
            result = json.loads((root / "out" / "ads_style_curve_plausibility_audit_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "ADS_STYLE_CURVES_PLAUSIBLE_PASS")
            self.assertTrue(result["strict_checks_pass"])
            self.assertEqual({item["source_status"] for item in result["source_results"]}, {"PASS"})

    def test_bad_flat_uncoupled_curves_fail(self) -> None:
        mod = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            curves = root / "curves.csv"
            rows = []
            for freq in [14.5, 15.0, 15.5]:
                rows.append(
                    {
                        "source": "EMX",
                        "freq_hz": freq * 1e9,
                        "freq_ghz": freq,
                        "lp_nh": 0.0,
                        "ls_nh": 0.0,
                        "m_nh": 0.0,
                        "k": 0.0,
                        "qp": 0.2,
                        "qs": 0.1,
                        "cm_single_primary_y11_plus_y12_ff": 0.0,
                    }
                )
            _write_curves(curves, rows)

            status = mod.main(["--curves-csv", str(curves), "--out-dir", str(root / "out"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            result = json.loads((root / "out" / "ads_style_curve_plausibility_audit_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "ADS_STYLE_CURVES_PLAUSIBLE_FAIL")
            failed = {check["name"] for check in result["checks"] if not check["pass"]}
            self.assertIn("EMX:target_lp_positive", failed)
            self.assertIn("EMX:target_abs_k_has_coupling", failed)
            self.assertIn("EMX:target_qp_positive", failed)

    def test_missing_required_column_fails_precheck(self) -> None:
        mod = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            curves = root / "curves.csv"
            curves.write_text("source,freq_ghz,lp_nh\nEMX,15,1.0\n", encoding="utf-8")

            status = mod.main(["--curves-csv", str(curves), "--out-dir", str(root / "out"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            result = json.loads((root / "out" / "ads_style_curve_plausibility_audit_20260615.json").read_text(encoding="utf-8"))
            failed = {check["name"] for check in result["checks"] if not check["pass"]}
            self.assertIn("required_column_k", failed)
            self.assertIn("required_column_qp", failed)
