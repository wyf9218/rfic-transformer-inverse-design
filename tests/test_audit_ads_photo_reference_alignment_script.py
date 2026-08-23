from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_alignment_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_ads_photo_reference_alignment.py"
    spec = importlib.util.spec_from_file_location("audit_ads_photo_reference_alignment_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AuditAdsPhotoReferenceAlignmentScriptTest(TransformerToolboxTestBase):
    def test_metric_check_fails_large_deviation_from_photo_reference(self) -> None:
        alignment = _load_alignment_module()
        spec = alignment.MetricSpec("k", "K", -0.512, "", 10.0)
        check = alignment._metric_check(spec, -0.1669, None)
        self.assertEqual(check["status"], "FAIL")
        self.assertGreater(check["percent_error"], 60.0)

    def test_metric_check_passes_within_percent_limit(self) -> None:
        alignment = _load_alignment_module()
        spec = alignment.MetricSpec("lp_nh", "Lp", 0.8843, "nH", 10.0)
        check = alignment._metric_check(spec, 0.90, None)
        self.assertEqual(check["status"], "PASS")
        self.assertLess(check["percent_error"], 10.0)
