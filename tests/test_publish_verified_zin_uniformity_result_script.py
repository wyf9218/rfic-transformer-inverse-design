from tests.rfic_transformer_inverse_design.shared import *

import csv
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
        / "publish_verified_zin_uniformity_result.py"
    )
    spec = importlib.util.spec_from_file_location("publish_verified_zin_uniformity_result_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_uniformity_fixture(
    root: Path,
    *,
    concentrated: bool = False,
    include_plots: bool = True,
) -> Path:
    audit = root / "zin_audit"
    audit.mkdir(parents=True)
    scatter = audit / "zin_center_scatter.png"
    hist = audit / "zin_center_histograms.png"
    heatmap = audit / "zin_target_envelope_heatmap.png"
    plots = []
    if include_plots:
        for path, title in [
            (scatter, "Center Zin scatter"),
            (hist, "Center Zin histograms"),
            (heatmap, "Target-envelope Zin heatmap"),
        ]:
            path.write_bytes(PNG_BYTES)
            plots.append({"status": "OK", "path": str(path), "title": title})
    summary = {
        "overall_status": "PASS",
        "label_summary": {
            "valid_count": 500,
            "real_ohm": {"span": 100.0},
            "imag_ohm": {"span": 100.0},
            "abs_ohm": {"span": 120.0},
            "occupied_bins": {"real": 10, "imag": 10, "bins": 10},
        },
        "target_envelope_summary": {"configured": True, "status": "PASS"},
        "plots": plots,
    }
    (audit / "zin_coverage_audit_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    rows = []
    if concentrated:
        rows = [
            {"real_bin": 0, "imag_bin": 0, "count": 500, "status": "covered"},
            *[
                {"real_bin": i // 10, "imag_bin": i % 10, "count": 0, "status": "empty"}
                for i in range(1, 100)
            ],
        ]
    else:
        rows = [
            {"real_bin": i // 10, "imag_bin": i % 10, "count": 5, "status": "covered"}
            for i in range(100)
        ]
    for name in ["zin_coverage_bins.csv", "zin_target_envelope_bins.csv"]:
        with (audit / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["real_bin", "imag_bin", "count", "status"])
            writer.writeheader()
            writer.writerows(rows)
    return audit


class PublishVerifiedZinUniformityResultScriptTest(TransformerToolboxTestBase):
    def test_publishes_uniform_zin_distribution_assets(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            audit = _write_uniformity_fixture(root)
            report = root / "report"

            status = mod.main(
                [
                    "--audit-dir",
                    str(audit),
                    "--report-dir",
                    str(report),
                    "--require-target-envelope",
                    "--use-target-envelope-bins",
                ]
            )

            self.assertEqual(status, 0)
            manifest = json.loads((report / "zin_uniformity_verified_result_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "ZIN_UNIFORMITY_VERIFIED_PASS")
            self.assertTrue(manifest["strict_checks_pass"])
            self.assertEqual(manifest["metrics"]["occupied_2d_fraction"], 1.0)
            self.assertAlmostEqual(manifest["metrics"]["max_single_bin_fraction"], 0.01)
            for rel_path in manifest["published_assets"].values():
                self.assertTrue((report / rel_path).exists(), rel_path)

    def test_rejects_concentrated_zin_distribution(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            audit = _write_uniformity_fixture(root, concentrated=True)
            report = root / "report"

            status = mod.main(["--audit-dir", str(audit), "--report-dir", str(report), "--no-fail-exit"])

            self.assertEqual(status, 0)
            manifest = json.loads((report / "zin_uniformity_verified_result_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "ZIN_UNIFORMITY_VERIFIED_FAIL")
            self.assertFalse(manifest["strict_checks_pass"])
            failed = {check["name"] for check in manifest["checks"] if not check["pass"]}
            self.assertIn("occupied_2d_bin_fraction", failed)
            self.assertIn("max_single_bin_fraction", failed)
            self.assertFalse((report / "assets").exists())

    def test_rejects_missing_distribution_figures(self) -> None:
        mod = _load_publisher_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            audit = _write_uniformity_fixture(root, include_plots=False)
            report = root / "report"

            status = mod.main(["--audit-dir", str(audit), "--report-dir", str(report), "--no-fail-exit"])

            self.assertEqual(status, 0)
            manifest = json.loads((report / "zin_uniformity_verified_result_manifest_20260614.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "ZIN_UNIFORMITY_VERIFIED_FAIL")
            failed = {check["name"] for check in manifest["checks"] if not check["pass"]}
            self.assertIn("artifact_present_scatter", failed)
            self.assertIn("artifact_present_histograms", failed)
