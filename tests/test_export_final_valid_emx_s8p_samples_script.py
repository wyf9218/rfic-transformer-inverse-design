from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "export_final_valid_emx_s8p_samples.py"
    spec = importlib.util.spec_from_file_location("export_final_valid_emx_s8p_samples_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ExportFinalValidEmxS8pSamplesScriptTest(TransformerToolboxTestBase):
    def test_exports_only_final_valid_candidates_and_merges_original_columns(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            discovery = root / "discovery.json"
            discovery.write_text(
                json.dumps(
                    {
                        "overall_status": "PASS",
                        "final_valid_count": 1,
                        "results": [
                            {
                                "evaluation": "eval_good",
                                "touchstone_path": str(root / "evaluations" / "eval_good" / "emx" / "emx.s8p"),
                                "source": str(root / "evaluations" / "eval_good" / "layout"),
                                "layout_json_path": str(root / "evaluations" / "eval_good" / "layout" / "transformer_layout.layout.json"),
                                "power_line_8port_geometry_json_path": str(root / "evaluations" / "eval_good" / "layout" / "power_line_8port_geometry.json"),
                                "summary_json_path": str(root / "evaluations" / "eval_good" / "summary.json"),
                                "final_validation_candidate_status": "PASS",
                                "touchstone_contract_status": "PASS",
                                "layout_evidence_status": "PASS",
                                "layout_audit_status": "PASS",
                            },
                            {
                                "evaluation": "eval_bad",
                                "touchstone_path": str(root / "evaluations" / "eval_bad" / "emx" / "emx.s8p"),
                                "source": str(root / "evaluations" / "eval_bad" / "layout"),
                                "final_validation_candidate_status": "FAIL",
                                "touchstone_contract_status": "PASS",
                                "layout_evidence_status": "PASS",
                                "layout_audit_status": "FAIL",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            original = root / "original.csv"
            original.write_text(
                "selection_rank,evaluation,line_width_um,lp_nh_center\n"
                "99,eval_good,8.5,1.23\n"
                "100,eval_bad,7.0,2.34\n",
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--discovery-summary",
                    str(discovery),
                    "--out-dir",
                    str(root / "out"),
                    "--original-samples-csv",
                    str(original),
                ]
            )

            self.assertEqual(status, 0)
            rows = list(csv.DictReader((root / "out" / "physical_feature_validation_samples.csv").open()))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["selection_rank"], "1")
            self.assertEqual(rows[0]["evaluation"], "eval_good")
            self.assertEqual(rows[0]["final_validation_candidate_status"], "PASS")
            self.assertEqual(rows[0]["line_width_um"], "8.5")
            self.assertEqual(rows[0]["lp_nh_center"], "1.23")
            summary = json.loads((root / "out" / "final_valid_emx_s8p_sample_selection_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "FINAL_VALID_EMX_SAMPLES_READY_FOR_HFSS_HANDOFF")

    def test_fails_without_final_valid_candidates(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            discovery = root / "discovery.json"
            discovery.write_text(
                json.dumps({"overall_status": "FAIL", "final_valid_count": 0, "results": []}),
                encoding="utf-8",
            )

            status = mod.main(["--discovery-summary", str(discovery), "--out-dir", str(root / "out")])

            self.assertEqual(status, 2)
            rows = list(csv.DictReader((root / "out" / "physical_feature_validation_samples.csv").open()))
            self.assertEqual(rows, [])
            summary = json.loads((root / "out" / "final_valid_emx_s8p_sample_selection_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "NO_FINAL_VALID_EMX_SAMPLES_TO_EXPORT")
