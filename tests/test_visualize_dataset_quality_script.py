from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_visualize_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "visualize_dataset_quality.py"
    spec = importlib.util.spec_from_file_location("visualize_dataset_quality_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class VisualizeDatasetQualityScriptTest(TransformerToolboxTestBase):
    def test_report_ready_gate_fails_when_expected_frequency_mismatches_manifest(self) -> None:
        visualize = _load_visualize_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_minimal_report_ready_dataset(root, target_frequency={"start_hz": 13.5e9, "stop_hz": 16.5e9, "step_hz": 0.375e9, "points": 9})

            status = visualize.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "figures"),
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "50",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "451",
                    "--require-report-ready",
                ]
            )

            self.assertEqual(status, 3)
            summary = json.loads((root / "figures" / "visualization_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["data_status"]["status_label"], "PRELIMINARY")
            self.assertTrue(any("target_frequency start_hz mismatch" in reason for reason in summary["data_status"]["reasons"]))

    def test_report_ready_gate_accepts_matching_expected_frequency(self) -> None:
        visualize = _load_visualize_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_minimal_report_ready_dataset(root, target_frequency={"start_hz": 5.0e9, "stop_hz": 50.0e9, "step_hz": 0.1e9, "points": 451})

            status = visualize.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "figures"),
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "50",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "451",
                    "--require-report-ready",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "figures" / "visualization_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["data_status"]["status_label"], "REPORT_READY")

    def _write_minimal_report_ready_dataset(self, root: Path, *, target_frequency: dict[str, float | int]) -> None:
        manifest = {
            "requested_count": 1,
            "ok_count": 1,
            "fail_count": 0,
            "target_frequency": target_frequency,
            "sparameter_quality": {"valid_sparameter_count": 1},
            "zin_coverage": {"valid_zin_count": 1},
        }
        row = {"ok": "true"}
        (root / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
