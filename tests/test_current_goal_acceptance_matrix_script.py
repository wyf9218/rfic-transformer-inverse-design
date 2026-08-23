from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_matrix_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "build_goal_acceptance_matrix.py"
    )
    spec = importlib.util.spec_from_file_location("current_goal_acceptance_matrix_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


def _item(result: dict, requirement: str) -> dict:
    for item in result["items"]:
        if item["requirement"] == requirement:
            return item
    raise AssertionError(f"missing requirement: {requirement}")


def _base_fixture(root: Path, mod) -> tuple[Path, Path]:
    report = root / "reports" / "current_validation_status_20260614"
    mod.ROOT = root
    mod.REPORT_DIR = report
    mod.MANIFEST_PATH = report / "report_manifest.json"
    mod.OUT_JSON = report / "goal_acceptance_matrix_20260614.json"
    mod.OUT_MD = report / "GOAL_ACCEPTANCE_MATRIX_20260614_CN.md"

    overlay = root / "overlay.json"
    error = root / "error.json"
    figure_index = report / "figure_evidence_index_20260614.json"
    _write_json(
        overlay,
        {
            "overall_status": "PASS",
            "evidence_use": "diagnostic",
            "emx_touchstone": "narrowband_emx.s4p",
            "common_overlay_frequency_ghz": {"start": 13.5, "stop": 16.5, "points": 9, "step": 0.375},
            "metric_max_percent_errors_common_window": {
                "cm_single_primary_y11_plus_y12_ff": 24.37,
            },
        },
    )
    _write_json(
        error,
        {
            "common_frequency_ghz": {"start": 13.5, "stop": 16.5, "points": 9, "step": 0.375},
            "core_metrics": {
                "lp_nh": {"max_percent_error": 1.0},
                "ls_nh": {"max_percent_error": 2.0},
                "k": {"max_percent_error": 3.0},
                "qp": {"max_percent_error": 4.0},
                "qs": {"max_percent_error": 4.5},
            },
        },
    )
    _write_json(
        figure_index,
        {
            "figure_count": 1,
            "status_counts": {"PRESENT": 1},
            "evidence_class_counts": {"TEST": 1},
            "records": [{"sha256": "a" * 64, "evidence_class": "TEST", "can_use_for_final_report": "yes"}],
        },
    )
    assets = {
        key: f"assets/{key}.png"
        for key in [
            "mars_zin_scatter",
            "mars_zin_hist",
            "mars_zin_heatmap",
            "mars_zin_band",
            "mars_zin_entropy",
            "emx_layout",
            "hfss_top",
            "hfss_iso",
            "emx_metrics_common",
            "hfss_metrics_common",
            "overlay",
            "emx_hfss_error_common",
        ]
    }
    for rel in assets.values():
        _touch(report / rel)
    _write_json(
        mod.MANIFEST_PATH,
        {
            "assets": assets,
            "source_summaries": {
                "mars_zin_label_audit": {
                    "valid_zin_count": 500,
                    "touchstone_nonzero": 513,
                    "touchstone_total": 513,
                    "entropy_fraction": {"real": 0.76, "imag": 0.82, "abs": 0.82},
                    "occupied_2d_bins": 53,
                    "total_2d_bins": 100,
                    "max_2d_bin_fraction": 0.166,
                },
                "emx_hfss_overlay": str(overlay),
                "emx_hfss_error_summary": str(error),
                "figure_evidence_index_json": str(figure_index),
                "zin_balanced_acquisition_planner": str(root / "planner.py"),
                "mars_zin_balanced_acquisition_handoff": str(root / "paste.sh"),
                "zin_balanced_acquisition_plan_verifier": str(root / "verify.py"),
            },
        },
    )
    return root, report


class CurrentGoalAcceptanceMatrixScriptTest(TransformerToolboxTestBase):
    def test_missing_verified_manifests_keep_final_goal_unready(self) -> None:
        mod = _load_matrix_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            _root, report = _base_fixture(Path(tmpdir), mod)

            self.assertEqual(mod.main(), 0)

            result = json.loads((report / "goal_acceptance_matrix_20260614.json").read_text(encoding="utf-8"))
            self.assertFalse(result["final_goal_ready"])
            self.assertEqual(
                _item(result, "Target-aware next acquisition plan exists for improving Zin uniformity")["status"],
                "PARTIAL_REMOTE_RUN_SUPERSEDED",
            )
            self.assertEqual(
                _item(result, "Verified wideband core EMX/HFSS metrics Lp/Ls/Kw/Qp/Qs are within 5%")["status"],
                "MISSING",
            )

    def test_verified_zin_and_wideband_manifests_are_reflected_as_proven(self) -> None:
        mod = _load_matrix_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            _root, report = _base_fixture(Path(tmpdir), mod)
            for rel in [
                "assets/zin_heatmap.png",
                "assets/zin_overlay.png",
                "assets/wide_emx.png",
                "assets/wide_hfss.png",
                "assets/wide_overlay.png",
                "assets/wide_error.png",
            ]:
                _touch(report / rel)
            _write_json(
                report / "zin_balanced_verified_plan_manifest_20260614.json",
                {
                    "status": "ZIN_PLAN_VERIFIED_PASS",
                    "strict_checks_pass": True,
                    "published_assets": {
                        "verified_zin_sparse_bin_deficit_heatmap": "assets/zin_heatmap.png",
                        "verified_zin_next_targets_overlay": "assets/zin_overlay.png",
                    },
                    "thresholds": {"expected_new_sample_count": 500, "min_target_bins": 50, "max_single_bin_fraction": 0.05},
                    "verifier_summary": {"target_metrics": {"nonzero_target_bin_count": 100}},
                },
            )
            _write_json(
                report / "wideband_verified_result_manifest_20260614.json",
                {
                    "status": "WIDEBAND_CORE_PASS",
                    "strict_checks_pass": True,
                    "core_metrics_pass_5_percent": True,
                    "core_metric_max_percent_errors": {"lp_nh": 1, "ls_nh": 2, "k": 3, "qp": 4, "qs": 4.5},
                    "published_assets": {
                        "mars_emx_wideband_metrics": "assets/wide_emx.png",
                        "hfss_wideband_metrics_for_mars_compare": "assets/wide_hfss.png",
                        "mars_emx_hfss_wideband_overlay": "assets/wide_overlay.png",
                        "mars_emx_hfss_wideband_percent_error": "assets/wide_error.png",
                    },
                    "summary_path": "summary.json",
                    "error_summary_path": "error.json",
                    "discovery_summary_path": "discovery.json",
                },
            )

            self.assertEqual(mod.main(), 0)

            result = json.loads((report / "goal_acceptance_matrix_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(
                _item(result, "Target-aware next acquisition plan exists for improving Zin uniformity")["status"],
                "PROVEN",
            )
            self.assertEqual(
                _item(result, "MARS real 5-50 GHz EMX wideband S4P has been pulled locally and replotted")["status"],
                "PROVEN",
            )
            self.assertEqual(
                _item(result, "Final comparison covers requested 5-50 GHz / 0.1 GHz / 451-point range")["status"],
                "PROVEN",
            )
            self.assertEqual(
                _item(result, "Verified wideband core EMX/HFSS metrics Lp/Ls/Kw/Qp/Qs are within 5%")["status"],
                "PROVEN",
            )
            self.assertFalse(result["final_goal_ready"], "actual regenerated Zin distribution is still only PARTIAL")

    def test_wideband_core_metric_failure_blocks_final_goal(self) -> None:
        mod = _load_matrix_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            _root, report = _base_fixture(Path(tmpdir), mod)
            for rel in ["assets/wide_emx.png", "assets/wide_hfss.png", "assets/wide_overlay.png", "assets/wide_error.png"]:
                _touch(report / rel)
            _write_json(
                report / "wideband_verified_result_manifest_20260614.json",
                {
                    "status": "WIDEBAND_CORE_FAIL",
                    "strict_checks_pass": True,
                    "core_metrics_pass_5_percent": False,
                    "core_metric_max_percent_errors": {"k": 8.5},
                    "published_assets": {
                        "mars_emx_wideband_metrics": "assets/wide_emx.png",
                        "hfss_wideband_metrics_for_mars_compare": "assets/wide_hfss.png",
                        "mars_emx_hfss_wideband_overlay": "assets/wide_overlay.png",
                        "mars_emx_hfss_wideband_percent_error": "assets/wide_error.png",
                    },
                },
            )

            self.assertEqual(mod.main(), 0)

            result = json.loads((report / "goal_acceptance_matrix_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(
                _item(result, "MARS real 5-50 GHz EMX wideband S4P has been pulled locally and replotted")["status"],
                "PROVEN",
            )
            self.assertEqual(
                _item(result, "Verified wideband core EMX/HFSS metrics Lp/Ls/Kw/Qp/Qs are within 5%")["status"],
                "FAIL",
            )
            self.assertFalse(result["final_goal_ready"])
