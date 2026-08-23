from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xe2k\xb8"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _load_audit_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "build_objective_evidence_audit.py"
    )
    spec = importlib.util.spec_from_file_location("objective_evidence_audit_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_BYTES)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _item(result: dict, requirement: str) -> dict:
    for item in result["requirements"]:
        if item["requirement"] == requirement:
            return item
    raise AssertionError(f"missing requirement: {requirement}")


def _base_fixture(root: Path) -> tuple[Path, Path, Path]:
    report = root / "reports" / "current_validation_status_20260614"
    packet = root / "reports" / "random_sample_validation_packet_20260615" / "random_sample_validation_packet_manifest.json"
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
            "mars_current_state_readonly",
        ]
    }
    for rel in assets.values():
        _write_png(report / rel)
    _write_json(
        report / "report_manifest.json",
        {
            "sample_id": "sample123",
            "report_status": "CURRENT_STATUS_NOT_FINAL_ACCEPTANCE",
            "assets": assets,
            "source_summaries": {
                "mars_zin_label_audit": {
                    "valid_zin_count": 500,
                    "occupied_2d_bins": 53,
                    "total_2d_bins": 100,
                    "max_2d_bin_fraction": 0.166,
                    "entropy_fraction": {"real": 0.76, "imag": 0.82, "abs": 0.82},
                },
                "figure_evidence_index_json": str(report / "figure_evidence_index_20260614.json"),
            },
        },
    )
    _write_json(
        report / "goal_acceptance_matrix_20260614.json",
        {
            "final_goal_ready": False,
            "items": [
                {
                    "requirement": "Core EMX/HFSS metrics Lp/Ls/Kw/Qp/Qs are within 5% on the available common window",
                    "status": "PROVEN_DIAGNOSTIC",
                }
            ],
        },
    )
    _write_json(
        packet,
        {
            "status": "PASS_PACKET_READY_WITH_CAVEATS",
            "final_acceptance": False,
            "status_counts": {"present": 13, "missing": 0},
        },
    )
    _write_json(report / "figure_evidence_index_20260614.json", {"figure_count": 13})
    _write_json(report / "report_provenance_manifest_20260614.json", {"record_count": 1})
    _write_json(
        report / "ads_style_curve_plausibility_audit_20260615.json",
        {
            "status": "ADS_STYLE_CURVES_PLAUSIBLE_PASS",
            "strict_checks_pass": True,
        },
    )
    return report, packet, report / "report_manifest.json"


class ObjectiveEvidenceAuditScriptTest(TransformerToolboxTestBase):
    def test_current_diagnostic_state_is_not_final_ready(self) -> None:
        mod = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            report, packet, manifest = _base_fixture(Path(tmpdir))

            status = mod.main(
                [
                    "--report-dir",
                    str(report),
                    "--report-manifest",
                    str(manifest),
                    "--goal-matrix",
                    str(report / "goal_acceptance_matrix_20260614.json"),
                    "--random-sample-packet",
                    str(packet),
                ]
            )

            self.assertEqual(status, 0)
            result = json.loads((report / "objective_evidence_audit_20260615.json").read_text(encoding="utf-8"))
            self.assertFalse(result["final_objective_ready"])
            self.assertEqual(_item(result, "MARS rerun/regeneration path is ready")["status"], "WAITING_REMOTE")
            self.assertEqual(_item(result, "Zin distribution images prove basically uniform final labels")["status"], "PARTIAL")
            self.assertEqual(_item(result, "Final 5-50 GHz EMX/HFSS physical curve comparison passes 5% core gate")["status"], "MISSING")
            self.assertTrue((report / "objective_evidence_audit.html").is_file())

    def test_final_publisher_manifests_can_make_objective_ready(self) -> None:
        mod = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report, packet, manifest = _base_fixture(root)
            for rel in [
                "assets/zin_scatter_final.png",
                "assets/reachable_overlay.png",
                "assets/reachable_hist.png",
                "assets/wide_emx.png",
                "assets/wide_hfss.png",
                "assets/wide_overlay.png",
                "assets/wide_error.png",
            ]:
                _write_png(report / rel)
            _write_json(
                report / "zin_uniformity_verified_result_manifest_20260614.json",
                {
                    "status": "ZIN_UNIFORMITY_VERIFIED_PASS",
                    "strict_checks_pass": True,
                    "metrics": {"occupied_2d_fraction": 0.9},
                    "published_assets": {"verified_zin_uniformity_scatter": "assets/zin_scatter_final.png"},
                },
            )
            _write_json(
                report / "reachable_candidate_queue_verified_manifest_20260615.json",
                {
                    "status": "REACHABLE_CANDIDATE_QUEUE_VERIFIED_PASS",
                    "strict_checks_pass": True,
                    "metrics": {"selected_count": 500},
                    "published_assets": {
                        "verified_reachable_candidate_queue_overlay": "assets/reachable_overlay.png",
                        "verified_reachable_candidate_queue_histograms": "assets/reachable_hist.png",
                    },
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
                },
            )

            status = mod.main(
                [
                    "--report-dir",
                    str(report),
                    "--report-manifest",
                    str(manifest),
                    "--goal-matrix",
                    str(report / "goal_acceptance_matrix_20260614.json"),
                    "--random-sample-packet",
                    str(packet),
                ]
            )

            self.assertEqual(status, 0)
            result = json.loads((report / "objective_evidence_audit_20260615.json").read_text(encoding="utf-8"))
            self.assertTrue(result["final_objective_ready"])
            self.assertEqual(_item(result, "MARS rerun/regeneration path is ready")["status"], "PROVEN")
            self.assertEqual(_item(result, "Zin distribution images prove basically uniform final labels")["status"], "PROVEN")
            self.assertEqual(
                _item(result, "Final 5-50 GHz EMX/HFSS physical curve comparison passes 5% core gate")["status"],
                "PROVEN",
            )
