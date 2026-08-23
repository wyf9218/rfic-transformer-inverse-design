from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_photo_reference_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_photo_matched_hfss_reference_evidence.py"
    spec = importlib.util.spec_from_file_location("build_photo_matched_hfss_reference_evidence_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BuildPhotoMatchedHfssReferenceEvidenceScriptTest(TransformerToolboxTestBase):
    def test_parse_touchstone_metadata_extracts_header_variables_and_ports(self) -> None:
        mod = _load_photo_reference_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "example.s4p"
            path.write_text(
                "\n".join(
                    [
                        "! Touchstone file exported from HFSS 2025.1.0",
                        "!        File:           C:/Mac/Home/Desktop/test of answer.aedt",
                        "!        Design:         HFSSDesign1",
                        "! Variables:",
                        "!        $D1 = 170um",
                        "!        $theta_bridge = 45deg",
                        "# GHz S MA R 50.000000",
                        "! Port[1] = Rectangle10_T2",
                        "! Port[2] = Rectangle11_T2",
                        "5 0 0",
                    ]
                ),
                encoding="utf-8",
            )

            metadata = mod.parse_touchstone_metadata(path)

            self.assertEqual(metadata["header_fields"]["File"], "C:/Mac/Home/Desktop/test of answer.aedt")
            self.assertEqual(metadata["header_fields"]["Design"], "HFSSDesign1")
            self.assertEqual(metadata["variables"]["$D1"], "170um")
            self.assertEqual(metadata["variables"]["$theta_bridge"], "45deg")
            self.assertEqual(metadata["ports"]["1"], "Rectangle10_T2")
            self.assertEqual(metadata["option_line"], "# GHz S MA R 50.000000")

    def test_declares_hfss_from_header(self) -> None:
        mod = _load_photo_reference_module()
        self.assertTrue(mod._declares_hfss({"header_fields": {"Design": "HFSSDesign1"}}))
        self.assertFalse(mod._declares_hfss({"header_fields": {"Design": "EMXDesign"}}))
