from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_ads_metric_formula_consistency.py"
    spec = importlib.util.spec_from_file_location("audit_ads_metric_formula_consistency_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AuditAdsMetricFormulaConsistencyScriptTest(TransformerToolboxTestBase):
    def test_formula_audit_recovers_known_synthetic_transformer_metrics(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "formula"
            status = mod.main(["--out-dir", str(out_dir)])

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "ads_metric_formula_consistency_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ADS_FORMULA_IMPLEMENTATION_ACCEPTED")
            self.assertEqual(summary["frequency_ghz"]["points"], 451)
            self.assertEqual(summary["ads_formula_map"]["Zdiff21"], "Z31 - Z32 + Z42 - Z41")
            self.assertTrue((out_dir / "ads_metric_formula_consistency_curves.png").is_file())
            template = out_dir / "ADS_DATA_DISPLAY_LP_LS_Q_K_TEMPLATE.md"
            self.assertTrue(template.is_file())
            template_text = template.read_text(encoding="utf-8")
            self.assertIn("ADS Data Display equation template", template_text)
            self.assertIn("Zp = Z11 - Z12 + Z22 - Z21", template_text)
            self.assertIn("target_marker_ghz = 15", template_text)
            self.assertEqual(summary["artifacts"]["ads_data_display_template"], str(template.resolve()))
            worst_recovery_error = max(
                item["max_percent_error"] for item in summary["metric_recovery_errors"].values()
            )
            self.assertLess(worst_recovery_error, 1.0e-6)
            checks = {check["name"]: check for check in summary["checks"]}
            self.assertEqual(checks["helper formula equals direct ADS expression"]["status"], "PASS")
            self.assertEqual(checks["known transformer metric recovery"]["status"], "PASS")
            self.assertEqual(checks["ADS Data Display equation template"]["status"], "PASS")
