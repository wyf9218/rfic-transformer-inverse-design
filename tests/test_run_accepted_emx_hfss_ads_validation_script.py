from tests.rfic_transformer_inverse_design.shared import *

import argparse
import csv
import hashlib
import importlib.util
import sys
from io import BytesIO

from PIL import Image, ImageDraw


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_accepted_emx_hfss_ads_validation.py"
    spec = importlib.util.spec_from_file_location("run_accepted_emx_hfss_ads_validation_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_metric_csv(path: Path, *, scale: float = 1.0) -> None:
    rows = ["freq_ghz,k,qp,qs,lp_nh,ls_nh"]
    for index in range(451):
        freq = 5.0 + 0.1 * index
        rows.append(
            f"{freq:.1f},"
            f"{(0.50 + 0.0001 * index) * scale:.8g},"
            f"{(10.0 + 0.01 * index) * scale:.8g},"
            f"{(12.0 + 0.01 * index) * scale:.8g},"
            f"{(1.00 + 0.001 * index) * scale:.8g},"
            f"{(1.20 + 0.001 * index) * scale:.8g}"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _valid_png_bytes(width: int = 900, height: int = 600, *, byte_count: int = 4096) -> bytes:
    image = Image.new("RGB", (width, height), (246, 247, 249))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 13):
        draw.line((x, 0, width - 1 - (x % width), height - 1), fill=((x * 3) % 255, 80, 180), width=2)
    for y in range(0, height, 17):
        draw.line((0, y, width - 1, height - 1 - (y % height)), fill=(180, (y * 5) % 255, 40), width=1)
    draw.rectangle((40, 40, min(width - 40, 260), min(height - 40, 160)), outline=(20, 80, 160), width=3)
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=4)
    data = buffer.getvalue()
    if len(data) < byte_count:
        buffer = BytesIO()
        image.save(buffer, format="PNG", compress_level=0)
        data = buffer.getvalue()
    return data


def _blank_png_bytes(width: int = 900, height: int = 600) -> bytes:
    image = Image.new("RGB", (width, height), (255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=0)
    return buffer.getvalue()


def _write_import_summary(
    path: Path,
    emx_path: Path,
    *,
    decision: str = "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS",
    include_verifier_checks: bool = True,
    include_core_metric_artifact: bool = True,
    include_artifact_bundle: bool = True,
) -> None:
    checks = []
    validation_root = path.parent / "target_emx_postrun_import" / "extracted" / "validation_20260613"
    touchstone_dir = validation_root / "touchstone_physical_gate"
    emx_first_dir = validation_root / "emx_first_validation_gate_20260613"
    touchstone_dir.mkdir(parents=True, exist_ok=True)
    emx_first_dir.mkdir(parents=True, exist_ok=True)
    bundle_artifact_paths = {
        "touchstone_summary": touchstone_dir / "touchstone_transformer_audit_summary.json",
        "touchstone_metrics_csv": touchstone_dir / "touchstone_transformer_metrics.csv",
        "touchstone_ads_equivalent_plot": touchstone_dir / "touchstone_ads_equivalent_metrics.png",
        "emx_first_summary": emx_first_dir / "emx_first_validation_gate_summary.json",
        "emx_first_metrics_csv": emx_first_dir / "emx_first_validation_gate_metrics.csv",
        "emx_first_ads_style_plot": emx_first_dir / "emx_first_validation_gate_ads_style_metrics.png",
        "emx_first_core_plot": emx_first_dir / "emx_first_validation_gate_core_metrics.png",
        "port_pair_sensitivity_csv": emx_first_dir / "emx_first_validation_gate_port_pair_sensitivity.csv",
        "port_pair_sensitivity_plot": emx_first_dir / "emx_first_validation_gate_port_pair_sensitivity.png",
    }
    for name, artifact_path in bundle_artifact_paths.items():
        if name == "emx_first_core_plot" and not include_core_metric_artifact:
            continue
        if artifact_path.suffix == ".png":
            artifact_path.write_bytes(_valid_png_bytes())
        elif artifact_path.suffix == ".csv":
            artifact_path.write_text("freq_ghz,k,qp,qs,lp_nh,ls_nh\n15,0.5,10,12,1,1.2\n", encoding="utf-8")
        else:
            artifact_path.write_text(json.dumps({"overall_status": "PASS"}, indent=2), encoding="utf-8")
    if include_core_metric_artifact:
        bundle_artifact_paths["emx_first_core_plot"].write_bytes(_valid_png_bytes())
    if include_verifier_checks:
        checks = [
            {"status": "PASS", "name": "post-run validation artifacts", "detail": "ok"},
            {"status": "PASS", "name": "post-run validation artifact content", "detail": "ok"},
            {"status": "PASS", "name": "Touchstone physical gate frequency grid", "detail": "ok"},
            {"status": "PASS", "name": "Touchstone physical gate coupling arguments", "detail": "ok"},
            {"status": "PASS", "name": "Touchstone physical gate required physics checks", "detail": "ok"},
            {"status": "PASS", "name": "Touchstone physical gate internal checks", "detail": "ok"},
            {"status": "PASS", "name": "EMX-first gate frequency grid", "detail": "ok"},
            {"status": "PASS", "name": "EMX-first gate internal checks", "detail": "ok"},
            {"status": "PASS", "name": "local EMX S4P SHA", "detail": "ok"},
        ]
    artifact_bundle = None
    if include_artifact_bundle:
        artifact_bundle = {
            "status": "READY_FOR_HFSS" if decision == "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS" else "NOT_READY",
            "mars_emx_sha256": _sha256(emx_path),
            "emx_s4p": {"path": str(emx_path), "exists": True, "bytes": emx_path.stat().st_size, "sha256": _sha256(emx_path)},
            "validation_root": str(validation_root),
            "artifacts": {
                name: {
                    "path": str(artifact_path),
                    "exists": artifact_path.is_file(),
                    "bytes": artifact_path.stat().st_size if artifact_path.is_file() else None,
                    "sha256": _sha256(artifact_path) if artifact_path.is_file() else None,
                }
                for name, artifact_path in bundle_artifact_paths.items()
            },
        }
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS" if decision == "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS" else "FAIL",
                "decision": decision,
                "mars_emx_sha256": _sha256(emx_path),
                "emx_s4p": {"path": str(emx_path), "sha256": _sha256(emx_path)},
                "validation_root": str(validation_root),
                "accepted_emx_reference_bundle": artifact_bundle,
                "checks": checks,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_geometry_summary(path: Path, *, decision: str = "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS") -> None:
    asset_dir = path.parent / "hfss_model_views"
    asset_dir.mkdir(parents=True, exist_ok=True)
    top_png = asset_dir / "top.png"
    iso_png = asset_dir / "iso.png"
    quality_png = asset_dir / "quality.png"
    step = asset_dir / "model.step"
    for png in (top_png, iso_png, quality_png):
        png.write_bytes(_valid_png_bytes())
    step.write_text(
        "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n"
        + "\n".join(f"#{index}=CARTESIAN_POINT('',(0.,0.,0.));" for index in range(1, 30))
        + "\nENDSEC;\nEND-ISO-10303-21;\n",
        encoding="utf-8",
    )
    status = "PASS" if decision == "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS" else "FAIL"
    path.write_text(
        json.dumps(
            {
                "overall_status": status,
                "decision": decision,
                "artifacts": {
                    "top_png": str(top_png),
                    "isometric_png": str(iso_png),
                    "quality_png": str(quality_png),
                    "step": str(step),
                },
                "checks": [
                    {"status": status, "name": "HFSS top-view PNG", "detail": "ok"},
                    {"status": status, "name": "HFSS isometric-view PNG", "detail": "ok"},
                    {"status": status, "name": "HFSS geometry-quality PNG", "detail": "ok"},
                    {"status": status, "name": "HFSS STEP model", "detail": "ok"},
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _valid_compare_summary_for_checks(emx_source: Path, hfss_source: Path) -> dict:
    return {
        "overall_status": "PASS",
        "criterion": {"max_percent_error": 5.0},
        "emx_source": str(emx_source),
        "hfss_ads_source": str(hfss_source),
        "frequency_window_hz": {"min": 5.0e9, "max": 50.0e9, "count": 451},
        "frequency_grid_checks": {
            "ADS no-extrapolation coverage": {"status": "PASS", "detail": "ok"},
            "expected frequency points": {"status": "PASS", "detail": "ok"},
            "expected frequency step": {"status": "PASS", "detail": "ok"},
            "matching HFSS/ADS frequency grid": {"status": "PASS", "detail": "ok"},
        },
        "metrics": {
            name: {
                "status": "PASS",
                "max_abs_error": 0.0,
                "mean_abs_error": 0.0,
                "max_percent_error": 0.0,
                "mean_percent_error": 0.0,
            }
            for name in ("k", "qp", "qs", "lp_nh", "ls_nh")
        },
    }


class RunAcceptedEmxHfssAdsValidationScriptTest(TransformerToolboxTestBase):
    def test_touchstone_expected_ports_are_inferred_from_snp_suffix(self) -> None:
        mod = _load_module()

        self.assertEqual(mod._expected_ports_for_touchstone(Path("sample.s8p"), None), 8)
        self.assertEqual(mod._expected_ports_for_touchstone(Path("sample.s4p"), None), 4)
        self.assertEqual(mod._expected_ports_for_touchstone(Path("sample.csv"), None), 4)
        self.assertEqual(mod._expected_ports_for_touchstone(Path("sample.s8p"), 6), 6)

    def test_dry_run_generates_compare_summary_and_ads_style_figures(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.csv"
            hfss = root / "hfss.csv"
            _write_metric_csv(emx, scale=1.0)
            _write_metric_csv(hfss, scale=1.02)
            import_summary = root / "emx_import_summary.json"
            _write_import_summary(import_summary, emx)
            geometry_summary = root / "hfss_geometry_summary.json"
            _write_geometry_summary(geometry_summary)

            status = mod.main(
                [
                    "--emx-import-summary",
                    str(import_summary),
                    "--emx-s4p",
                    str(emx),
                    "--hfss-s4p",
                    str(hfss),
                    "--hfss-geometry-summary",
                    str(geometry_summary),
                    "--out-dir",
                    str(root / "out"),
                    "--compare-stop-ghz",
                    "50",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "451",
                    "--skip-hfss-touchstone-audit",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "accepted_emx_hfss_ads_validation_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_USE_HFSS_COMPARISON")
            self.assertFalse(summary["arguments"]["ground_unused_ports"])
            compare_command = summary["command_log"][0]["args"]
            self.assertNotIn("--ground-unused-ports", compare_command)
            self.assertTrue((root / "out" / "emx_hfss_ads_compare" / "emx_hfss_ads_comparison_summary.json").exists())
            self.assertTrue(Path(summary["formula_note"]).is_file())
            formula_note = Path(summary["formula_note"]).read_text(encoding="utf-8")
            self.assertIn("# ADS/Python Formula Cross-Check", formula_note)
            self.assertIn("ADS Data Display equation template", formula_note)
            self.assertIn("Zp = Z11 - Z12 + Z22 - Z21", formula_note)
            self.assertIn("Lp = imag(Zdiff[1,1]) / omega", formula_note)
            for path in summary["figure_paths"].values():
                self.assertTrue(Path(path).is_file())
            for path in summary["target_marker_paths"].values():
                self.assertTrue(Path(path).is_file())
            self.assertEqual(set(summary["figure_records"]), set(summary["figure_paths"]))
            self.assertEqual(set(summary["target_marker_records"]), set(summary["target_marker_paths"]))
            for name, record in summary["figure_records"].items():
                path = Path(summary["figure_paths"][name])
                self.assertEqual(record["sha256"], _sha256(path))
                self.assertEqual(record["bytes"], path.stat().st_size)
            for name, record in summary["target_marker_records"].items():
                path = Path(summary["target_marker_paths"][name])
                self.assertEqual(record["sha256"], _sha256(path))
                self.assertEqual(record["bytes"], path.stat().st_size)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted EMX import artifact bundle"]["status"], "PASS")
            self.assertEqual(checks["HFSS geometry asset audit evidence"]["status"], "PASS")
            marker_csv = Path(summary["target_marker_paths"]["csv"])
            marker_rows = list(csv.DictReader(marker_csv.open(newline="", encoding="utf-8")))
            self.assertEqual({row["metric"] for row in marker_rows}, {"lp_nh", "ls_nh", "qp", "qs", "k"})
            self.assertTrue(all(abs(float(row["nearest_freq_ghz"]) - 15.0) < 1e-9 for row in marker_rows))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["HFSS Touchstone physical gate"]["status"], "FAIL")
            self.assertIn("review-only", checks["HFSS Touchstone physical gate"]["detail"])
            self.assertEqual(checks["accepted EMX import core metric artifact"]["status"], "PASS")
            self.assertEqual(checks["ADS/Python formula note"]["status"], "PASS")
            self.assertEqual(checks["ADS-style plot_data integrity"]["status"], "PASS")
            self.assertEqual(checks["ADS-style core metric figures"]["status"], "PASS")
            self.assertIn("valid dimensions", checks["ADS-style core metric figures"]["detail"])
            self.assertEqual(checks["ADS-style core metric figure manifest"]["status"], "PASS")
            self.assertIn("SHA256", checks["ADS-style core metric figure manifest"]["detail"])
            self.assertEqual(checks["ADS-style target marker table"]["status"], "PASS")
            self.assertIn("15 GHz", checks["ADS-style target marker table"]["detail"])
            self.assertEqual(checks["ADS-style target marker manifest"]["status"], "PASS")
            self.assertEqual(checks["EMX-vs-HFSS compare frequency-grid checks"]["status"], "PASS")
            self.assertIn("grid/no-extrapolation", checks["EMX-vs-HFSS compare frequency-grid checks"]["detail"])

    def test_rejects_missing_hfss_geometry_summary_for_final_traceability(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.csv"
            hfss = root / "hfss.csv"
            _write_metric_csv(emx, scale=1.0)
            _write_metric_csv(hfss, scale=1.0)
            import_summary = root / "emx_import_summary.json"
            _write_import_summary(import_summary, emx)

            status = mod.main(
                [
                    "--emx-import-summary",
                    str(import_summary),
                    "--emx-s4p",
                    str(emx),
                    "--hfss-s4p",
                    str(hfss),
                    "--out-dir",
                    str(root / "out"),
                    "--skip-hfss-touchstone-audit",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "accepted_emx_hfss_ads_validation_summary.json").read_text())
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["HFSS geometry asset audit evidence"]["status"], "FAIL")
            self.assertIn("--hfss-geometry-summary", checks["HFSS geometry asset audit evidence"]["detail"])

    def test_fails_when_compare_output_missing_formula_note(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.csv"
            hfss = root / "hfss.csv"
            _write_metric_csv(emx, scale=1.0)
            _write_metric_csv(hfss, scale=1.0)
            import_summary = root / "emx_import_summary.json"
            _write_import_summary(import_summary, emx)

            def fake_run_compare(emx_path: Path, hfss_path: Path, out_dir: Path, args: argparse.Namespace):
                out_dir.mkdir(parents=True, exist_ok=True)
                freq_hz = [5.0e9 + 1.0e8 * index for index in range(451)]
                values = {
                    "k": [0.5] * 451,
                    "qp": [10.0] * 451,
                    "qs": [12.0] * 451,
                    "lp_nh": [1.0] * 451,
                    "ls_nh": [1.2] * 451,
                }
                (out_dir / "emx_hfss_ads_comparison_summary.json").write_text(
                    json.dumps(
                        {
                            "overall_status": "PASS",
                            "criterion": {"max_percent_error": 5.0},
                            "emx_source": str(emx_path),
                            "hfss_ads_source": str(hfss_path),
                            "frequency_window_hz": {"min": 5.0e9, "max": 50.0e9, "count": 451},
                            "frequency_grid_checks": {
                                "ADS no-extrapolation coverage": {"status": "PASS", "detail": "ok"},
                                "expected frequency points": {"status": "PASS", "detail": "ok"},
                                "expected frequency step": {"status": "PASS", "detail": "ok"},
                                "matching HFSS/ADS frequency grid": {"status": "PASS", "detail": "ok"},
                            },
                            "metrics": {
                                name: {
                                    "status": "PASS",
                                    "max_abs_error": 0.0,
                                    "mean_abs_error": 0.0,
                                    "max_percent_error": 0.0,
                                    "mean_percent_error": 0.0,
                                }
                                for name in ("k", "qp", "qs", "lp_nh", "ls_nh")
                            },
                            "plot_data": {"freq_hz": freq_hz, "emx": values, "hfss_ads": values},
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                return mod.subprocess.CompletedProcess(args=["fake_compare"], returncode=0, stdout="", stderr="")

            with mock.patch.object(mod, "_run_compare", side_effect=fake_run_compare):
                status = mod.main(
                    [
                        "--emx-import-summary",
                        str(import_summary),
                        "--emx-s4p",
                        str(emx),
                        "--hfss-s4p",
                        str(hfss),
                        "--out-dir",
                        str(root / "out"),
                        "--skip-hfss-touchstone-audit",
                        "--no-fail-exit",
                    ]
                )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "accepted_emx_hfss_ads_validation_summary.json").read_text())
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["ADS/Python formula note"]["status"], "FAIL")
            self.assertIn("missing", checks["ADS/Python formula note"]["detail"])

    def test_figure_check_rejects_invalid_png_content(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_png = Path(tmpdir) / "bad.png"
            bad_png.write_bytes(b"\x89PNG\r\n\x1a\nplot")

            checks = mod._figure_checks({"bad_metric": str(bad_png)})

            self.assertEqual(checks[0].status, "FAIL")
            self.assertEqual(checks[0].name, "ADS-style core metric figures")
            self.assertIn("missing PNG IHDR chunk", checks[0].detail)

    def test_figure_check_rejects_blank_png_content(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            blank_png = Path(tmpdir) / "blank.png"
            blank_png.write_bytes(_blank_png_bytes())

            checks = mod._figure_checks({"blank_metric": str(blank_png)})

            self.assertEqual(checks[0].status, "FAIL")
            self.assertEqual(checks[0].name, "ADS-style core metric figures")
            self.assertIn("blank or nearly constant PNG", checks[0].detail)

    def test_plot_data_checks_reject_nonfinite_core_metric_values(self) -> None:
        mod = _load_module()
        args = mod.build_parser().parse_args(
            [
                "--emx-import-summary",
                "import.json",
                "--emx-s4p",
                "emx.s4p",
                "--hfss-s4p",
                "hfss.s4p",
            ]
        )
        freq_hz = [5.0e9 + 1.0e8 * index for index in range(451)]
        values = {
            "k": [0.5] * 451,
            "qp": [10.0] * 451,
            "qs": [12.0] * 451,
            "lp_nh": [1.0] * 451,
            "ls_nh": [1.2] * 451,
        }
        summary = _valid_compare_summary_for_checks(Path("emx.s4p").resolve(), Path("hfss.s4p").resolve())
        summary["plot_data"] = {
            "freq_hz": freq_hz,
            "emx": {**values, "k": [float("nan")] + [0.5] * 450},
            "hfss_ads": values,
        }

        checks = {check.name: check for check in mod._plot_data_checks(summary, args)}

        self.assertEqual(checks["ADS-style plot_data integrity"].status, "FAIL")
        self.assertIn("plot_data.emx.k contains non-finite values", checks["ADS-style plot_data integrity"].detail)

    def test_target_marker_rejects_missing_exact_target_frequency(self) -> None:
        mod = _load_module()
        args = mod.build_parser().parse_args(
            [
                "--emx-import-summary",
                "import.json",
                "--emx-s4p",
                "emx.s4p",
                "--hfss-s4p",
                "hfss.s4p",
            ]
        )
        freq_hz = [5.05e9 + 1.0e8 * index for index in range(451)]
        values = {
            "k": [0.5] * 451,
            "qp": [10.0] * 451,
            "qs": [12.0] * 451,
            "lp_nh": [1.0] * 451,
            "ls_nh": [1.2] * 451,
        }
        summary = _valid_compare_summary_for_checks(Path("emx.s4p").resolve(), Path("hfss.s4p").resolve())
        summary["plot_data"] = {"freq_hz": freq_hz, "emx": values, "hfss_ads": values}

        with self.assertRaisesRegex(ValueError, "target marker frequency 15"):
            mod._target_marker_records(summary, args)

    def test_compare_checks_reject_summary_without_no_extrapolation_evidence(self) -> None:
        mod = _load_module()
        args = mod.build_parser().parse_args(
            [
                "--emx-import-summary",
                "import.json",
                "--emx-s4p",
                "emx.s4p",
                "--hfss-s4p",
                "hfss.s4p",
            ]
        )
        summary = _valid_compare_summary_for_checks(Path("emx.s4p").resolve(), Path("hfss.s4p").resolve())
        summary["frequency_grid_checks"].pop("ADS no-extrapolation coverage")

        checks = {
            check.name: check
            for check in mod._compare_checks(summary, args, Path("emx.s4p").resolve(), Path("hfss.s4p").resolve())
        }

        self.assertEqual(checks["EMX-vs-HFSS compare frequency-grid checks"].status, "FAIL")
        self.assertIn("ADS no-extrapolation coverage", checks["EMX-vs-HFSS compare frequency-grid checks"].detail)

    def test_compare_checks_reject_metric_error_over_gate_even_when_status_pass(self) -> None:
        mod = _load_module()
        args = mod.build_parser().parse_args(
            [
                "--emx-import-summary",
                "import.json",
                "--emx-s4p",
                "emx.s4p",
                "--hfss-s4p",
                "hfss.s4p",
                "--max-percent-error",
                "5",
            ]
        )
        summary = _valid_compare_summary_for_checks(Path("emx.s4p").resolve(), Path("hfss.s4p").resolve())
        summary["metrics"]["k"]["max_percent_error"] = 5.1

        checks = {
            check.name: check
            for check in mod._compare_checks(summary, args, Path("emx.s4p").resolve(), Path("hfss.s4p").resolve())
        }

        self.assertEqual(checks["EMX-vs-HFSS compare core metric errors"].status, "FAIL")
        self.assertIn("metric_k_max_percent_error=5.1", checks["EMX-vs-HFSS compare core metric errors"].detail)

    def test_compare_checks_reject_relaxed_compare_criterion(self) -> None:
        mod = _load_module()
        args = mod.build_parser().parse_args(
            [
                "--emx-import-summary",
                "import.json",
                "--emx-s4p",
                "emx.s4p",
                "--hfss-s4p",
                "hfss.s4p",
                "--max-percent-error",
                "5",
            ]
        )
        summary = _valid_compare_summary_for_checks(Path("emx.s4p").resolve(), Path("hfss.s4p").resolve())
        summary["criterion"]["max_percent_error"] = 10.0

        checks = {
            check.name: check
            for check in mod._compare_checks(summary, args, Path("emx.s4p").resolve(), Path("hfss.s4p").resolve())
        }

        self.assertEqual(checks["EMX-vs-HFSS compare criterion"].status, "FAIL")
        self.assertIn("criterion_max_percent_error=10", checks["EMX-vs-HFSS compare criterion"].detail)

    def test_compare_checks_reject_mismatched_summary_sources(self) -> None:
        mod = _load_module()
        args = mod.build_parser().parse_args(
            [
                "--emx-import-summary",
                "import.json",
                "--emx-s4p",
                "emx.s4p",
                "--hfss-s4p",
                "hfss.s4p",
            ]
        )
        summary = _valid_compare_summary_for_checks(Path("other_emx.s4p").resolve(), Path("hfss.s4p").resolve())

        checks = {
            check.name: check
            for check in mod._compare_checks(summary, args, Path("emx.s4p").resolve(), Path("hfss.s4p").resolve())
        }

        self.assertEqual(checks["EMX-vs-HFSS compare source traceability"].status, "FAIL")
        self.assertIn("emx_source_mismatch", checks["EMX-vs-HFSS compare source traceability"].detail)

    def test_rejects_nonaccepted_emx_import_summary(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.csv"
            hfss = root / "hfss.csv"
            _write_metric_csv(emx, scale=1.0)
            _write_metric_csv(hfss, scale=1.0)
            import_summary = root / "emx_import_summary.json"
            _write_import_summary(import_summary, emx, decision="VALIDATION_EVIDENCE_TRANSFERRED_NO_LOCAL_EMX")

            status = mod.main(
                [
                    "--emx-import-summary",
                    str(import_summary),
                    "--emx-s4p",
                    str(emx),
                    "--hfss-s4p",
                    str(hfss),
                    "--out-dir",
                    str(root / "out"),
                    "--skip-hfss-touchstone-audit",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "accepted_emx_hfss_ads_validation_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_USE_HFSS_COMPARISON")
            self.assertEqual(summary["command_log"], [])
            self.assertEqual(summary["figure_paths"], {})
            self.assertFalse((root / "out" / "emx_hfss_ads_compare" / "emx_hfss_ads_comparison_summary.json").exists())
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted EMX import decision"]["status"], "FAIL")
            self.assertEqual(checks["EMX-first stop before HFSS comparison"]["status"], "FAIL")

    def test_rejects_accepted_import_summary_without_verifier_evidence(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.csv"
            hfss = root / "hfss.csv"
            _write_metric_csv(emx, scale=1.0)
            _write_metric_csv(hfss, scale=1.0)
            import_summary = root / "emx_import_summary.json"
            _write_import_summary(import_summary, emx, include_verifier_checks=False)

            status = mod.main(
                [
                    "--emx-import-summary",
                    str(import_summary),
                    "--emx-s4p",
                    str(emx),
                    "--hfss-s4p",
                    str(hfss),
                    "--out-dir",
                    str(root / "out"),
                    "--skip-hfss-touchstone-audit",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "accepted_emx_hfss_ads_validation_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["command_log"], [])
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted EMX import verifier evidence"]["status"], "FAIL")

    def test_rejects_accepted_import_summary_without_artifact_bundle(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.csv"
            hfss = root / "hfss.csv"
            _write_metric_csv(emx, scale=1.0)
            _write_metric_csv(hfss, scale=1.0)
            import_summary = root / "emx_import_summary.json"
            _write_import_summary(import_summary, emx, include_artifact_bundle=False)

            status = mod.main(
                [
                    "--emx-import-summary",
                    str(import_summary),
                    "--emx-s4p",
                    str(emx),
                    "--hfss-s4p",
                    str(hfss),
                    "--out-dir",
                    str(root / "out"),
                    "--skip-hfss-touchstone-audit",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "accepted_emx_hfss_ads_validation_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_USE_HFSS_COMPARISON")
            self.assertEqual(summary["command_log"], [])
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted EMX import verifier evidence"]["status"], "PASS")
            self.assertEqual(checks["accepted EMX import artifact bundle"]["status"], "FAIL")
            self.assertIn("missing accepted_emx_reference_bundle", checks["accepted EMX import artifact bundle"]["detail"])

    def test_rejects_accepted_import_summary_without_emx_first_core_metric_artifact(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.csv"
            hfss = root / "hfss.csv"
            _write_metric_csv(emx, scale=1.0)
            _write_metric_csv(hfss, scale=1.0)
            import_summary = root / "emx_import_summary.json"
            _write_import_summary(import_summary, emx, include_core_metric_artifact=False)

            status = mod.main(
                [
                    "--emx-import-summary",
                    str(import_summary),
                    "--emx-s4p",
                    str(emx),
                    "--hfss-s4p",
                    str(hfss),
                    "--out-dir",
                    str(root / "out"),
                    "--skip-hfss-touchstone-audit",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "accepted_emx_hfss_ads_validation_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_USE_HFSS_COMPARISON")
            self.assertEqual(summary["figure_paths"], {})
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted EMX import verifier evidence"]["status"], "PASS")
            self.assertEqual(checks["accepted EMX import artifact bundle"]["status"], "FAIL")
            self.assertEqual(checks["accepted EMX import core metric artifact"]["status"], "FAIL")
            self.assertIn("emx_first_validation_gate_core_metrics.png", checks["accepted EMX import core metric artifact"]["detail"])

    def test_hfss_audit_command_requires_nonzero_coupling_gate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            args = mod.build_parser().parse_args(
                [
                    "--emx-import-summary",
                    str(root / "import.json"),
                    "--emx-s4p",
                    str(root / "emx.s4p"),
                    "--hfss-s4p",
                    str(root / "hfss.s4p"),
                    "--min-target-abs-k",
                    "0.07",
                    "--min-window-abs-k",
                    "0.08",
                ]
            )
            completed = mod.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            with mock.patch.object(mod.subprocess, "run", return_value=completed) as run_mock:
                mod._run_hfss_audit(root / "hfss.s4p", root / "audit", args)

            command = run_mock.call_args.args[0]
            self.assertIn("--expected-source-kind", command)
            self.assertEqual(command[command.index("--expected-source-kind") + 1], "HFSS")
            self.assertIn("--min-target-abs-k", command)
            self.assertEqual(command[command.index("--min-target-abs-k") + 1], "0.07")
            self.assertIn("--min-window-abs-k", command)
            self.assertEqual(command[command.index("--min-window-abs-k") + 1], "0.08")
            self.assertNotIn("--ground-unused-ports", command)
