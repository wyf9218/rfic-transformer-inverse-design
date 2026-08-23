from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import sys
from io import BytesIO

from PIL import Image, ImageDraw


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "verify_accepted_emx_hfss_ads_figures.py"
    spec = importlib.util.spec_from_file_location("verify_accepted_emx_hfss_ads_figures_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_png(path: Path, *, blank: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (900, 600), (255, 255, 255) if blank else (246, 248, 251))
    if not blank:
        draw = ImageDraw.Draw(image)
        for x in range(0, 900, 19):
            draw.line((x, 0, 899 - (x % 300), 599), fill=((x * 3) % 255, 75, 170), width=2)
        draw.rectangle((50, 50, 260, 170), outline=(20, 80, 150), width=4)
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=0)
    path.write_bytes(buffer.getvalue())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_records(paths: dict[str, Path]) -> dict[str, dict]:
    return {
        name: {
            "path": str(path),
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": _sha256(path) if path.is_file() else None,
        }
        for name, path in paths.items()
    }


def _plot_data() -> dict:
    freq_hz = [5.0e9 + 5.0e8 * index for index in range(111)]
    values = {
        "k": [0.50 + 0.0001 * index for index in range(111)],
        "qp": [10.0 + 0.01 * index for index in range(111)],
        "qs": [12.0 + 0.01 * index for index in range(111)],
        "lp_nh": [1.0 + 0.001 * index for index in range(111)],
        "ls_nh": [1.2 + 0.001 * index for index in range(111)],
    }
    return {"freq_hz": freq_hz, "emx": values, "hfss_ads": values}


def _required_checks() -> list[dict]:
    return [
        {"status": "PASS", "name": name, "detail": "ok"}
        for name in (
            "accepted EMX import status",
            "accepted EMX import decision",
            "accepted EMX local SHA",
            "accepted EMX import verifier evidence",
            "accepted EMX import core metric artifact",
            "HFSS geometry asset audit evidence",
            "HFSS geometry required asset checks",
            "HFSS geometry artifact paths",
            "HFSS Touchstone physical gate status",
            "HFSS Touchstone required differential/physics checks",
            "HFSS Touchstone physical gate internal checks",
            "EMX-vs-HFSS compare status",
            "EMX-vs-HFSS compare source traceability",
            "EMX-vs-HFSS compare criterion",
            "EMX-vs-HFSS compare frequency window",
            "EMX-vs-HFSS compare frequency-grid checks",
            "EMX-vs-HFSS compare core metric errors",
            "ADS/Python formula note",
            "ADS-style plot_data integrity",
            "ADS-style core metric figures",
            "ADS-style core metric figure manifest",
            "ADS-style target marker table",
            "ADS-style target marker manifest",
        )
    ]


def _write_evidence(
    root: Path,
    *,
    metric_error: float = 4.9,
    omit_plot_data: bool = False,
    blank_png: bool = False,
    omit_marker: bool = False,
    corrupt_marker: bool = False,
    corrupt_manifest: bool = False,
) -> Path:
    emx = root / "accepted_emx.s4p"
    hfss = root / "accepted_hfss.s4p"
    emx.write_text("emx\n", encoding="utf-8")
    hfss.write_text("hfss\n", encoding="utf-8")
    compare = root / "emx_hfss_ads_compare" / "emx_hfss_ads_comparison_summary.json"
    formula = root / "emx_hfss_ads_compare" / "ads_python_formula_crosscheck.md"
    hfss_audit = root / "hfss_touchstone_physical_gate" / "touchstone_transformer_audit_summary.json"
    hfss_geometry = root / "hfss_model_geometry_asset_audit" / "hfss_model_geometry_asset_audit_summary.json"
    figure_dir = root / "ads_style_core_metric_figures"
    figure_paths = {
        "emx_ads_style_core_metrics": figure_dir / "emx_ads_style_core_metrics.png",
        "hfss_ads_style_core_metrics": figure_dir / "hfss_ads_style_core_metrics.png",
        "emx_vs_hfss_ads_style_core_overlay": figure_dir / "emx_vs_hfss_ads_style_core_overlay.png",
    }
    for path in figure_paths.values():
        _write_png(path, blank=blank_png)

    compare.parent.mkdir(parents=True, exist_ok=True)
    compare_payload = {
        "overall_status": "PASS",
        "emx_source": str(emx),
        "hfss_ads_source": str(hfss),
        "frequency_window_hz": {"min": 5.0e9, "max": 60.0e9, "count": 111},
        "frequency_grid_checks": {
            "ADS no-extrapolation coverage": {"status": "PASS", "detail": "ok"},
            "expected frequency points": {"status": "PASS", "detail": "ok"},
            "expected frequency step": {"status": "PASS", "detail": "ok"},
            "matching HFSS/ADS frequency grid": {"status": "PASS", "detail": "ok"},
        },
        "metrics": {
            name: {"status": "PASS", "max_percent_error": metric_error}
            for name in ("k", "qp", "qs", "lp_nh", "ls_nh")
        },
    }
    if not omit_plot_data:
        compare_payload["plot_data"] = _plot_data()
    compare.write_text(json.dumps(compare_payload, indent=2), encoding="utf-8")
    marker_dir = root / "ads_style_target_marker_values"
    marker_csv = marker_dir / "ads_style_target_marker_values_15ghz.csv"
    marker_md = marker_dir / "ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md"
    if not omit_marker:
        marker_dir.mkdir(parents=True, exist_ok=True)
        plot_data = _plot_data()
        target_index = 20
        rows = [
            "target_ghz,nearest_freq_ghz,metric,emx,hfss_ads,abs_error,percent_error,metric_gate_max_percent,metric_status"
        ]
        for metric in ("lp_nh", "ls_nh", "qp", "qs", "k"):
            emx_value = plot_data["emx"][metric][target_index]
            hfss_value = plot_data["hfss_ads"][metric][target_index]
            if corrupt_marker and metric == "k":
                hfss_value += 0.123
            rows.append(
                f"15.0,15.0,{metric},{emx_value:.12g},{hfss_value:.12g},"
                f"{abs(hfss_value - emx_value):.12g},0.0,{metric_error:.12g},PASS"
            )
        marker_csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
        marker_md.write_text(
            "# ADS-Style Target Marker Values at 15 GHz\n"
            "EMX\n"
            "HFSS/ADS\n"
            "Percent error\n"
            "Required final statement still depends on the full-run decision.\n"
            "lp_nh ls_nh qp qs k\n",
            encoding="utf-8",
        )
    figure_records = _artifact_records(figure_paths)
    marker_paths = {"csv": marker_csv, "markdown": marker_md} if not omit_marker else {}
    target_marker_records = _artifact_records(marker_paths)
    if corrupt_manifest:
        figure_records["emx_ads_style_core_metrics"]["sha256"] = "0" * 64
    formula.write_text(
        "# ADS/Python Formula Cross-Check\n"
        "Touchstone 2.1\n"
        "port pairing must be recorded\n"
        "Touchstone reference impedance\n"
        "Z_diff = transpose(T) * Z_single * T\n"
        "ADS Data Display equation template\n"
        "Zp = Z11 - Z12 + Z22 - Z21\n"
        "Zs = Z33 - Z34 + Z44 - Z43\n"
        "Zm = Z31 - Z32 + Z42 - Z41\n"
        "Lp = imag(Zdiff[1,1]) / omega\n"
        "Ls = imag(Zdiff[2,2]) / omega\n"
        "M  = imag(Zdiff[2,1]) / omega\n"
        "K  = M / sqrt(abs(Lp * Ls))\n"
        "k  = M / sqrt(abs(Lp * Ls))\n"
        "Qp = imag(Zdiff[1,1]) / real(Zdiff[1,1])\n"
        "Qs = imag(Zdiff[2,2]) / real(Zdiff[2,2])\n"
        "ADS no-extrapolation coverage\n",
        encoding="utf-8",
    )
    hfss_audit.parent.mkdir(parents=True, exist_ok=True)
    hfss_audit.write_text(json.dumps({"overall_status": "PASS"}), encoding="utf-8")
    hfss_geometry.parent.mkdir(parents=True, exist_ok=True)
    hfss_geometry.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS",
                "checks": [
                    {"name": "HFSS top-view PNG", "status": "PASS", "detail": "ok"},
                    {"name": "HFSS isometric-view PNG", "status": "PASS", "detail": "ok"},
                    {"name": "HFSS geometry-quality PNG", "status": "PASS", "detail": "ok"},
                    {"name": "HFSS STEP model", "status": "PASS", "detail": "ok"},
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    accepted = root / "accepted_emx_hfss_ads_validation_summary.json"
    accepted.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "ACCEPT_HFSS_VALIDATION_SAMPLE",
                "emx_s4p": {"path": str(emx)},
                "hfss_s4p": {"path": str(hfss)},
                "compare_summary": str(compare),
                "formula_note": str(formula),
                "hfss_audit_summary": str(hfss_audit),
                "hfss_geometry_summary": str(hfss_geometry),
                "figure_paths": {name: str(path) for name, path in figure_paths.items()},
                "figure_records": figure_records,
                "target_marker_paths": {name: str(path) for name, path in marker_paths.items()},
                "target_marker_records": target_marker_records,
                "checks": _required_checks(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return accepted


class VerifyAcceptedEmxHfssAdsFiguresScriptTest(TransformerToolboxTestBase):
    def test_accepts_complete_final_figure_evidence(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            accepted = _write_evidence(root)

            status = mod.main(["--accepted-summary", str(accepted), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "accepted_figure_evidence_audit_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_FINAL_LP_LS_Q_K_FIGURES")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted K/Qp/Qs/Lp/Ls <= 10% errors"]["status"], "PASS")
            self.assertEqual(checks["accepted ADS-style plot_data arrays"]["status"], "PASS")
            self.assertEqual(checks["accepted ADS/Python formula note"]["status"], "PASS")
            self.assertEqual(checks["accepted final figure PNGs"]["status"], "PASS")
            self.assertEqual(checks["accepted final figure manifest"]["status"], "PASS")
            self.assertEqual(checks["accepted target marker table"]["status"], "PASS")
            self.assertEqual(checks["accepted target marker manifest"]["status"], "PASS")

    def test_rejects_tampered_figure_manifest(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            accepted = _write_evidence(root, corrupt_manifest=True)

            status = mod.main(
                [
                    "--accepted-summary",
                    str(accepted),
                    "--out-dir",
                    str(root / "audit"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "accepted_figure_evidence_audit_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted final figure manifest"]["status"], "FAIL")
            self.assertIn("sha256 mismatch", checks["accepted final figure manifest"]["detail"])

    def test_rejects_missing_or_corrupt_target_marker_table(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            accepted = _write_evidence(root, corrupt_marker=True)

            status = mod.main(["--accepted-summary", str(accepted), "--out-dir", str(root / "audit"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "accepted_figure_evidence_audit_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted target marker table"]["status"], "FAIL")
            self.assertIn("k: hfss_ads", checks["accepted target marker table"]["detail"])

    def test_rejects_formula_note_without_port_and_mutual_basis(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            accepted = _write_evidence(root)
            formula = root / "emx_hfss_ads_compare" / "ads_python_formula_crosscheck.md"
            formula.write_text(
                "# ADS/Python Formula Cross-Check\n"
                "Z_diff = transpose(T) * Z_single * T\n"
                "Lp = imag(Zdiff[1,1]) / omega\n"
                "Ls = imag(Zdiff[2,2]) / omega\n"
                "k  = M / sqrt(abs(Lp * Ls))\n"
                "ADS no-extrapolation coverage\n",
                encoding="utf-8",
            )

            status = mod.main(["--accepted-summary", str(accepted), "--out-dir", str(root / "audit"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "accepted_figure_evidence_audit_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted ADS/Python formula note"]["status"], "FAIL")
            self.assertIn("M  = imag(Zdiff[2,1]) / omega", checks["accepted ADS/Python formula note"]["detail"])
            self.assertIn("Touchstone reference impedance", checks["accepted ADS/Python formula note"]["detail"])
            self.assertIn("ADS Data Display equation template", checks["accepted ADS/Python formula note"]["detail"])

    def test_rejects_metric_error_over_gate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            accepted = _write_evidence(root, metric_error=5.1)

            status = mod.main(
                [
                    "--accepted-summary",
                    str(accepted),
                    "--out-dir",
                    str(root / "audit"),
                    "--max-percent-error",
                    "5",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "accepted_figure_evidence_audit_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted K/Qp/Qs/Lp/Ls <= 5% errors"]["status"], "FAIL")
            self.assertIn("k_max_percent_error=5.1", checks["accepted K/Qp/Qs/Lp/Ls <= 5% errors"]["detail"])

    def test_rejects_missing_plot_data_and_blank_png(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            accepted = _write_evidence(root, omit_plot_data=True, blank_png=True)

            status = mod.main(["--accepted-summary", str(accepted), "--out-dir", str(root / "audit"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "accepted_figure_evidence_audit_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted ADS-style plot_data arrays"]["status"], "FAIL")
            self.assertEqual(checks["accepted final figure PNGs"]["status"], "FAIL")
            self.assertIn("blank or nearly constant PNG", checks["accepted final figure PNGs"]["detail"])
