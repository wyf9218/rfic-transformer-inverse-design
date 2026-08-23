from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_audit_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_photo_matched_vs_target_geometry.py"
    spec = importlib.util.spec_from_file_location("audit_photo_matched_vs_target_geometry_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class AuditPhotoMatchedVsTargetGeometryScriptTest(TransformerToolboxTestBase):
    def test_blocks_photo_matched_hfss_when_geometry_and_provenance_do_not_match(self) -> None:
        audit = _load_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            photo = root / "photo_summary.json"
            geometry = root / "geometry.json"
            layout = root / "layout.json"
            modeling = root / "modeling.json"
            render = root / "render.json"
            _write_json(
                photo,
                {
                    "touchstone": "/tmp/test of answer 2.s4p",
                    "source_kind_from_path": "UNKNOWN",
                    "source_declares_hfss": True,
                    "frequency_ghz": {"start": 5.0, "stop": 45.0, "points": 41, "step": 1.0},
                    "metadata": {
                        "header_fields": {"File": "C:/Mac/Home/Desktop/test of answer.aedt", "Project": "test of answer", "Design": "HFSSDesign1", "Setup": "Setup2"},
                        "ports": {"1": "Rectangle10_T2", "2": "Rectangle11_T2", "3": "Rectangle15_T2", "4": "Rectangle14_T2"},
                        "variables": {
                            "$D1": "170um",
                            "$D2": "195um",
                            "$m10_w_inner": "7.137551um",
                            "$m10_w_outer": "7.137551um",
                            "$m9_w_inner": "6.303158um",
                            "$m9_w_outer": "6.303158um",
                            "$s": "3um",
                        },
                    },
                },
            )
            _write_json(geometry, {})
            _write_json(
                layout,
                {
                    "layout_path": "/runs/evaluations/ec6698dfc575950b/layout/transformer_layout.gds",
                    "top_cell": "TRANSFORMER_021_ec6698df",
                    "ports": [{"name": "P001"}, {"name": "P002"}, {"name": "P003"}, {"name": "P004"}],
                },
            )
            _write_json(
                modeling,
                {
                    "cache_key": "ec6698dfc575950b",
                    "geometry_parameters_from_summary": {
                        "primary_outer_width_um": 281.5,
                        "primary_outer_height_um": 479.8,
                        "secondary_outer_width_um": 424.8,
                        "secondary_outer_height_um": 188.5,
                        "primary_width_um": 5.612,
                        "secondary_width_um": 6.893,
                        "primary_spacing_um": 8.0,
                    },
                },
            )
            _write_json(render, {"sample_id": "ec6698dfc575950b", "hfss_objects": {"ports": ["P001", "P002", "P003", "P004"]}})

            status = audit.main(
                [
                    "--photo-summary",
                    str(photo),
                    "--target-geometry",
                    str(geometry),
                    "--target-layout",
                    str(layout),
                    "--target-modeling",
                    str(modeling),
                    "--target-hfss-render",
                    str(render),
                    "--out-dir",
                    str(root / "audit"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "photo_matched_vs_target_geometry_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_USE_PHOTO_MATCHED_HFSS_AS_TARGET_SAMPLE_REFERENCE")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target sample identity"]["status"], "PASS")
            self.assertEqual(checks["photo project provenance"]["status"], "FAIL")
            self.assertEqual(checks["port-name alignment"]["status"], "FAIL")
            self.assertEqual(checks["geometry scale comparison"]["status"], "FAIL")
            self.assertTrue((root / "audit" / "photo_matched_vs_target_geometry_scale.png").exists())
