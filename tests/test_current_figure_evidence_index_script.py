from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8ffff3f0005fe02fea73581e10000000049454e44ae426082"
)


def _load_figure_index_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "build_figure_evidence_index.py"
    )
    spec = importlib.util.spec_from_file_location("current_figure_evidence_index_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_1X1)


def _configure_module(mod, report_dir: Path) -> None:
    mod.REPORT_DIR = report_dir
    mod.MANIFEST_PATH = report_dir / "report_manifest.json"
    mod.OUT_JSON = report_dir / "figure_evidence_index_20260614.json"
    mod.OUT_MD = report_dir / "FIGURE_EVIDENCE_INDEX_20260614_CN.md"
    mod.OUT_HTML = report_dir / "figure_evidence_index.html"
    mod.FIGURES = []


class CurrentFigureEvidenceIndexScriptTest(TransformerToolboxTestBase):
    def test_dynamic_optional_publisher_assets_are_indexed_with_hashes(self) -> None:
        mod = _load_figure_index_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report"
            _configure_module(mod, report)
            published_assets = {
                "mars_emx_wideband_metrics": "assets/wide_emx.png",
                "hfss_wideband_metrics_for_mars_compare": "assets/wide_hfss.png",
                "mars_emx_hfss_wideband_overlay": "assets/wide_overlay.png",
                "mars_emx_hfss_wideband_percent_error": "assets/wide_error.png",
                "verified_zin_sparse_bin_deficit_heatmap": "assets/zin_plan_heatmap.png",
                "verified_zin_next_targets_overlay": "assets/zin_plan_overlay.png",
                "verified_zin_uniformity_scatter": "assets/zin_scatter.png",
                "verified_zin_uniformity_histograms": "assets/zin_hist.png",
                "verified_zin_uniformity_target_heatmap": "assets/zin_target_heatmap.png",
            }
            for rel in published_assets.values():
                _write_png(report / rel)
            _write_json(
                mod.MANIFEST_PATH,
                {
                    "assets": published_assets,
                    "wideband_verified_result": {
                        "status": "WIDEBAND_CORE_PASS",
                        "strict_checks_pass": True,
                        "core_metrics_pass_5_percent": True,
                        "published_assets": {
                            key: published_assets[key]
                            for key in [
                                "mars_emx_wideband_metrics",
                                "hfss_wideband_metrics_for_mars_compare",
                                "mars_emx_hfss_wideband_overlay",
                                "mars_emx_hfss_wideband_percent_error",
                            ]
                        },
                    },
                    "zin_balanced_verified_plan": {
                        "status": "ZIN_PLAN_VERIFIED_PASS",
                        "strict_checks_pass": True,
                        "published_assets": {
                            key: published_assets[key]
                            for key in [
                                "verified_zin_sparse_bin_deficit_heatmap",
                                "verified_zin_next_targets_overlay",
                            ]
                        },
                    },
                    "zin_uniformity_verified_result": {
                        "status": "ZIN_UNIFORMITY_VERIFIED_PASS",
                        "strict_checks_pass": True,
                        "published_assets": {
                            key: published_assets[key]
                            for key in [
                                "verified_zin_uniformity_scatter",
                                "verified_zin_uniformity_histograms",
                                "verified_zin_uniformity_target_heatmap",
                            ]
                        },
                    },
                },
            )

            self.assertEqual(mod.main(), 0)

            result = json.loads(mod.OUT_JSON.read_text(encoding="utf-8"))
            self.assertEqual(result["figure_count"], 9)
            self.assertEqual(result["status_counts"], {"PRESENT": 9})
            self.assertEqual(result["evidence_class_counts"]["VERIFIED_WIDEBAND_EMX_HFSS_PASS"], 4)
            self.assertEqual(result["evidence_class_counts"]["VERIFIED_ZIN_ACQUISITION_PLAN_PASS"], 2)
            self.assertEqual(result["evidence_class_counts"]["VERIFIED_FINAL_ZIN_UNIFORMITY_PASS"], 3)
            for record in result["records"]:
                self.assertEqual(record["exists_status"], "PRESENT")
                self.assertEqual(len(record["sha256"]), 64)
                self.assertEqual(record["dimensions_px"], {"width": 1, "height": 1})

    def test_failed_optional_wideband_assets_are_not_marked_as_final_pass(self) -> None:
        mod = _load_figure_index_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report"
            _configure_module(mod, report)
            for rel in ["assets/wide_overlay.png", "assets/wide_error.png"]:
                _write_png(report / rel)
            _write_json(
                mod.MANIFEST_PATH,
                {
                    "assets": {
                        "mars_emx_hfss_wideband_overlay": "assets/wide_overlay.png",
                        "mars_emx_hfss_wideband_percent_error": "assets/wide_error.png",
                    },
                    "wideband_verified_result": {
                        "status": "WIDEBAND_CORE_FAIL",
                        "strict_checks_pass": True,
                        "core_metrics_pass_5_percent": False,
                        "published_assets": {
                            "mars_emx_hfss_wideband_overlay": "assets/wide_overlay.png",
                            "mars_emx_hfss_wideband_percent_error": "assets/wide_error.png",
                        },
                    },
                },
            )

            self.assertEqual(mod.main(), 0)

            result = json.loads(mod.OUT_JSON.read_text(encoding="utf-8"))
            self.assertEqual(result["figure_count"], 2)
            self.assertEqual(
                {record["evidence_class"] for record in result["records"]},
                {"VERIFIED_WIDEBAND_EMX_HFSS_FAIL_EVIDENCE"},
            )
            self.assertEqual(
                {record["can_use_for_final_report"] for record in result["records"]},
                {"failure_evidence_only"},
            )
