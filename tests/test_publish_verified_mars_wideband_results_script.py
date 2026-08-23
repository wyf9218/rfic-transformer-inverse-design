from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xe2k\xb8"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _load_publisher_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "publish_verified_mars_wideband_results.py"
    )
    spec = importlib.util.spec_from_file_location("publish_verified_mars_wideband_results_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _fixture(
    root: Path,
    *,
    common_start: float = 5.0,
    common_stop: float = 50.0,
    common_points: int = 451,
    common_step: float = 0.1,
    k_error: float = 4.0,
    plausibility_pass: bool = True,
) -> Path:
    wideband_dir = root / "wideband"
    wideband_dir.mkdir()
    emx_s4p = wideband_dir / "ec6698dfc575950b_MARS_EMX_WIDEBAND_5_50GHz_step0p1.s4p"
    emx_s4p.write_text("! placeholder path-only S4P for publisher guard test\n", encoding="ascii")
    artifact_paths = {}
    for name in ["emx", "hfss", "overlay", "error"]:
        png = wideband_dir / f"{name}.png"
        png.write_bytes(PNG_BYTES)
        artifact_paths[name] = str(png)
    curves_csv = wideband_dir / "ads_style_metric_curves.csv"
    curves_csv.write_text(
        "source,freq_ghz,lp_nh,ls_nh,m_nh,k,qp,qs,cm_single_primary_y11_plus_y12_ff\n"
        "EMX,15.0,1.5,1.2,-0.25,-0.2,12,14,80\n"
        "HFSS,15.0,1.51,1.21,-0.24,-0.19,11.8,13.7,81\n",
        encoding="utf-8",
    )

    wide_freq = {"start": 5.0, "stop": 50.0, "points": 451, "step": 0.1}
    common_freq = {
        "start": common_start,
        "stop": common_stop,
        "points": common_points,
        "step": common_step,
    }
    _write_json(
        wideband_dir / "ads_style_metric_plot_summary.json",
        {
            "emx_touchstone": str(emx_s4p),
            "emx_frequency_ghz": wide_freq,
            "common_overlay_frequency_ghz": common_freq,
            "artifact_paths": {
                "emx_common_plot": artifact_paths["emx"],
                "hfss_common_plot": artifact_paths["hfss"],
                "overlay_common_plot": artifact_paths["overlay"],
            },
            "window_named_artifact_paths": {
                "emx_common_plot": artifact_paths["emx"],
                "hfss_common_plot": artifact_paths["hfss"],
                "overlay_common_plot": artifact_paths["overlay"],
            },
        },
    )
    _write_json(
        wideband_dir / "emx_hfss_percent_error_common_window_summary.json",
        {
            "common_frequency_ghz": common_freq,
            "window_named_output_png": artifact_paths["error"],
            "core_metrics": {
                "lp_nh": {"max_percent_error": 1.0},
                "ls_nh": {"max_percent_error": 2.0},
                "k": {"max_percent_error": k_error},
                "qp": {"max_percent_error": 3.0},
                "qs": {"max_percent_error": 4.0},
            },
        },
    )
    _write_json(
        wideband_dir / "mars_emx_return_discovery" / "mars_emx_return_discovery_summary.json",
        {
            "selected": {
                "emx_s4p": {
                    "path": str(emx_s4p),
                    "port_count": 4,
                    "frequency": {
                        "start_ghz": 5.0,
                        "stop_ghz": 50.0,
                        "points": 451,
                        "median_step_hz": 1.0e8,
                    },
                }
            }
        },
    )
    _write_json(
        wideband_dir / "ads_style_curve_plausibility_audit_20260615.json",
        {
            "status": "ADS_STYLE_CURVES_PLAUSIBLE_PASS" if plausibility_pass else "ADS_STYLE_CURVES_PLAUSIBLE_FAIL",
            "strict_checks_pass": plausibility_pass,
            "curves_csv": str(curves_csv),
        },
    )
    return wideband_dir


class PublishVerifiedMarsWidebandResultsScriptTest(TransformerToolboxTestBase):
    def test_publishes_verified_wideband_assets_to_requested_report_dir(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wideband_dir = _fixture(root)
            report_dir = root / "report"

            status = mod.main(["--wideband-dir", str(wideband_dir), "--report-dir", str(report_dir)])

            self.assertEqual(status, 0)
            manifest = json.loads((report_dir / "wideband_verified_result_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "WIDEBAND_CORE_PASS")
            self.assertTrue(manifest["strict_checks_pass"])
            self.assertTrue(manifest["core_metrics_pass_5_percent"])
            for rel_path in manifest["published_assets"].values():
                self.assertTrue((report_dir / rel_path).exists(), rel_path)

    def test_rejects_narrowband_common_window_before_copying_assets(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wideband_dir = _fixture(root, common_start=13.5, common_stop=16.5, common_points=9, common_step=0.375)
            report_dir = root / "report"

            with self.assertRaises(SystemExit) as cm:
                mod.main(["--wideband-dir", str(wideband_dir), "--report-dir", str(report_dir)])

            self.assertIn("summary_common_overlay_frequency_ghz_start_5GHz", str(cm.exception))
            manifest = json.loads((report_dir / "wideband_verified_result_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "FAILED_STRICT_PRECHECK")
            self.assertFalse(manifest["strict_checks_pass"])
            self.assertFalse((report_dir / "assets").exists())

    def test_core_metric_failure_is_published_as_failure_evidence_not_pass(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wideband_dir = _fixture(root, k_error=8.5)
            report_dir = root / "report"

            status = mod.main(["--wideband-dir", str(wideband_dir), "--report-dir", str(report_dir)])

            self.assertEqual(status, 0)
            manifest = json.loads((report_dir / "wideband_verified_result_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "WIDEBAND_CORE_FAIL")
            self.assertFalse(manifest["core_metrics_pass_5_percent"])
            self.assertAlmostEqual(manifest["core_metric_max_percent_errors"]["k"], 8.5)
            for rel_path in manifest["published_assets"].values():
                self.assertTrue((report_dir / rel_path).exists(), rel_path)

    def test_rejects_unphysical_curve_audit_before_copying_assets(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wideband_dir = _fixture(root, plausibility_pass=False)
            report_dir = root / "report"

            with self.assertRaises(SystemExit) as cm:
                mod.main(["--wideband-dir", str(wideband_dir), "--report-dir", str(report_dir)])

            self.assertIn("plausibility_audit_strict_checks_pass", str(cm.exception))
            manifest = json.loads((report_dir / "wideband_verified_result_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "FAILED_STRICT_PRECHECK")
            self.assertFalse(manifest["strict_checks_pass"])
            self.assertFalse((report_dir / "assets").exists())
