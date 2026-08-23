from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys

from PIL import Image, ImageDraw


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_hfss_model_geometry_assets.py"
    spec = importlib.util.spec_from_file_location("audit_hfss_model_geometry_assets_script", script_path)
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
        draw.rectangle((90, 100, 810, 500), outline=(10, 80, 180), width=8)
        draw.line((120, 480, 780, 140), fill=(190, 40, 40), width=6)
        draw.text((120, 120), "HFSS geometry", fill=(20, 20, 20))
    image.save(path)


def _write_step(path: Path, *, valid: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not valid:
        path.write_text("not a step file\n", encoding="utf-8")
        return
    entities = "\n".join(f"#{idx}=CARTESIAN_POINT('',({idx}.0,0.0,0.0));" for idx in range(1, 30))
    path.write_text(
        "\n".join(
            [
                "ISO-10303-21;",
                "HEADER;",
                "FILE_DESCRIPTION(('fixture'),'2;1');",
                "ENDSEC;",
                "DATA;",
                entities,
                "ENDSEC;",
                "END-ISO-10303-21;",
                "",
            ]
        ),
        encoding="utf-8",
    )


class AuditHfssModelGeometryAssetsScriptTest(TransformerToolboxTestBase):
    def test_accepts_nonblank_model_views_and_valid_step(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "package"
            model = package / "hfss_model_views"
            _write_png(model / "hfss_payload_geometry_top_annotated.png")
            _write_png(model / "hfss_payload_geometry_isometric.png")
            _write_png(model / "hfss_payload_geometry_quality_checks.png")
            _write_step(model / "ec6698dfc575950b_hfss_model_no_air.step")

            status = mod.main(
                [
                    "--package-dir",
                    str(package),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-png-bytes",
                    "128",
                    "--min-step-bytes",
                    "128",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "hfss_model_geometry_asset_audit_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS")
            self.assertTrue(all(check["status"] == "PASS" for check in summary["checks"]))

    def test_rejects_blank_png_and_invalid_step(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "package"
            model = package / "hfss_model_views"
            _write_png(model / "hfss_payload_geometry_top_annotated.png", blank=True)
            _write_png(model / "hfss_payload_geometry_isometric.png")
            _write_png(model / "hfss_payload_geometry_quality_checks.png")
            _write_step(model / "ec6698dfc575950b_hfss_model_no_air.step", valid=False)

            status = mod.main(
                [
                    "--package-dir",
                    str(package),
                    "--out-dir",
                    str(root / "audit"),
                    "--min-png-bytes",
                    "128",
                    "--min-step-bytes",
                    "128",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "hfss_model_geometry_asset_audit_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_USE_HFSS_MODEL_GEOMETRY_ASSETS")
            by_name = {check["name"]: check for check in summary["checks"]}
            self.assertIn("blank or nearly constant PNG", by_name["HFSS top-view PNG"]["detail"])
            self.assertIn("missing STEP tokens", by_name["HFSS STEP model"]["detail"])
