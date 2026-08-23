from tests.rfic_transformer_inverse_design.shared import *

import argparse
import importlib.util
import sys

import numpy as np


def _load_scan_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "scan_s4p_ads_photo_reference_candidates.py"
    spec = importlib.util.spec_from_file_location("scan_s4p_ads_photo_reference_candidates_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ScanS4pAdsPhotoReferenceCandidatesScriptTest(TransformerToolboxTestBase):
    def test_overall_pass_requires_emx_match(self) -> None:
        scan = _load_scan_module()
        self.assertEqual(scan._overall_status([{"status": "PASS"}], [{"status": "PASS"}], []), "PASS")

    def test_non_emx_match_requires_review(self) -> None:
        scan = _load_scan_module()
        self.assertEqual(scan._overall_status([{"status": "PASS"}], [], [{"status": "PASS"}]), "REVIEW_REQUIRED")

    def test_source_kind_does_not_classify_downloads_as_ads(self) -> None:
        scan = _load_scan_module()
        self.assertEqual(scan._source_kind(Path("/home/researcher/Downloads/plain_candidate.s4p")), "UNKNOWN")
        self.assertEqual(scan._source_kind(Path("/tmp/ads_roundtrip/emx_ads_roundtrip.s4p")), "EMX")
        self.assertEqual(scan._source_kind(Path("/tmp/HFSSDesign1.s4p")), "HFSS")

    def test_source_kind_reads_hfss_touchstone_header_when_path_is_ambiguous(self) -> None:
        scan = _load_scan_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test of answer 2.s4p"
            path.write_text(
                "! Touchstone file exported from HFSS 2025.1.0\n"
                "! File: C:/Mac/Home/Desktop/test of answer.aedt\n"
                "# Hz S RI R 50\n",
                encoding="utf-8",
            )
            self.assertEqual(scan._source_kind(path), "HFSS")

    def test_candidate_record_passes_exact_photo_reference_metrics(self) -> None:
        scan = _load_scan_module()

        class Curves:
            freq_hz = np.array([15.0e9])
            lp_nh = np.array([0.8843])
            ls_nh = np.array([0.8183])
            k = np.array([-0.512])
            qp = np.array([16.113])
            qs = np.array([14.243])
            cm_single_primary_ff = np.array([95.43])

        scan._extract_metric_curves = lambda label, path, port_pairs: Curves()
        args = argparse.Namespace(port_pairs="1,2:3,4", target_ghz=15.0, max_frequency_distance_ghz=0.05, max_percent_error=None)
        row = scan._candidate_record(Path("/tmp/example_emx.s4p"), args)

        self.assertEqual(row["status"], "PASS")
        self.assertEqual(row["source_kind"], "EMX")
        self.assertEqual(row["metric_fail_count"], 0)
        self.assertEqual(row["frequency_status"], "PASS")
