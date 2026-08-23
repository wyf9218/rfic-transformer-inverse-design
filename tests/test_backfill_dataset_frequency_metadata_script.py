from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_backfill_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "backfill_dataset_frequency_metadata.py"
    spec = importlib.util.spec_from_file_location("backfill_dataset_frequency_metadata_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BackfillDatasetFrequencyMetadataScriptTest(TransformerToolboxTestBase):
    def test_backfills_frequency_columns_and_manifest_target_frequency(self) -> None:
        backfill = _load_backfill_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs_hz = np.array([5.0e9, 5.1e9, 5.2e9])
            touchstone = root / "evaluations" / "a" / "emx" / "emx.s4p"
            touchstone.parent.mkdir(parents=True, exist_ok=True)
            _write_touchstone(touchstone, freqs_hz, np.zeros((3, 4, 4), dtype=np.complex128))
            self._write_dataset(root, touchstone.relative_to(root))

            status = backfill.main(
                [
                    str(root),
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                ]
            )

            self.assertEqual(status, 0)
            rows = list(csv.DictReader((root / "dataset_rows_frequency_backfilled.csv").open(newline="", encoding="utf-8")))
            self.assertEqual(rows[0]["sparam_freq_points"], "3")
            self.assertAlmostEqual(float(rows[0]["sparam_freq_start_hz"]), 5.0e9, delta=1.0)
            manifest = json.loads((root / "dataset_manifest_frequency_backfilled.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["target_frequency"]["points"], 3)
            summary = json.loads((root / "frequency_backfill_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["parsed_count"], 1)

    def test_backfill_fails_when_expected_frequency_does_not_match_touchstone(self) -> None:
        backfill = _load_backfill_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs_hz = np.array([13.5e9, 13.875e9, 14.25e9])
            touchstone = root / "emx.s4p"
            _write_touchstone(touchstone, freqs_hz, np.zeros((3, 4, 4), dtype=np.complex128))
            self._write_dataset(root, touchstone.name)

            status = backfill.main(
                [
                    str(root),
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "50.0",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "451",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "frequency_backfill_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["mismatch_count"], 1)

    def _write_dataset(self, root: Path, touchstone_path: Path | str) -> None:
        manifest = {
            "requested_count": 1,
            "ok_count": 1,
            "fail_count": 0,
            "port_mode": "single_ended_shield_grounded",
            "cadence_pin_purpose": 51,
            "sparameter_quality": {"valid_sparameter_count": 1},
            "zin_coverage": {"valid_zin_count": 1},
        }
        (root / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        row = {"evaluation": "a", "ok": "true", "touchstone_path": str(touchstone_path)}
        with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
