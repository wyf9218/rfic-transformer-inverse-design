from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8ffff3f0005fe02fea73581e10000000049454e44ae426082"
)


def _load_packet_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "build_random_sample_validation_packet.py"
    )
    spec = importlib.util.spec_from_file_location("random_sample_validation_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_1X1)


class RandomSampleValidationPacketScriptTest(TransformerToolboxTestBase):
    def test_builds_packet_with_hashes_when_assets_exist(self) -> None:
        mod = _load_packet_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = root / "report"
            assets = {}
            for item in mod.PACKET_ITEMS:
                rel = f"assets/{item['key']}.png"
                assets[item["key"]] = rel
                _write_png(report / rel)
            manifest = report / "report_manifest.json"
            manifest.write_text(
                json.dumps({"sample_id": "sample123", "report_status": "CURRENT_STATUS_NOT_FINAL_ACCEPTANCE", "assets": assets}),
                encoding="utf-8",
            )

            status = mod.main(report_dir=report, manifest_path=manifest, out_dir=root / "packet")

            self.assertEqual(status, 0)
            result = json.loads((root / "packet" / "random_sample_validation_packet_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "PASS_PACKET_READY_WITH_CAVEATS")
            self.assertFalse(result["final_acceptance"])
            self.assertEqual(result["status_counts"]["present"], len(mod.PACKET_ITEMS))
            for record in result["records"]:
                self.assertTrue(Path(record["packet_path"]).is_file())
                self.assertEqual(len(record["sha256"]), 64)
            self.assertTrue((root / "packet" / "README_CN.md").is_file())
            self.assertTrue((root / "packet" / "index.html").is_file())

    def test_missing_assets_make_packet_partial(self) -> None:
        mod = _load_packet_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = root / "report"
            first = mod.PACKET_ITEMS[0]
            _write_png(report / "assets" / f"{first['key']}.png")
            manifest = report / "report_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "sample_id": "sample123",
                        "report_status": "CURRENT_STATUS_NOT_FINAL_ACCEPTANCE",
                        "assets": {first["key"]: f"assets/{first['key']}.png"},
                    }
                ),
                encoding="utf-8",
            )

            status = mod.main(report_dir=report, manifest_path=manifest, out_dir=root / "packet")

            self.assertEqual(status, 2)
            result = json.loads((root / "packet" / "random_sample_validation_packet_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "PARTIAL_MISSING_ASSETS")
            self.assertEqual(result["status_counts"]["present"], 1)
            self.assertEqual(result["status_counts"]["missing"], len(mod.PACKET_ITEMS) - 1)
