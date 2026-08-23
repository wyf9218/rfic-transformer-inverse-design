from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_validate_dataset_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_dataset.py"
    spec = importlib.util.spec_from_file_location("validate_dataset_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ValidateDatasetScriptTest(TransformerToolboxTestBase):
    def test_require_emx_with_no_ok_rows_reports_touchstone_check_as_not_applicable(self) -> None:
        validate_dataset = _load_validate_dataset_module()
        manifest = {
            "requested_count": 1,
            "ok_count": 0,
            "fail_count": 1,
            "port_mode": "single_ended_shield_grounded",
            "cadence_pin_purpose": 51,
            "uniformity": {
                "count": 1,
                "bins": 1,
                "fields": {"primary_outer_width_um": {"histogram_min": 1, "histogram_max": 1}},
            },
        }
        rows = [{"ok": "false", "touchstone_path": ""}]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ok", "touchstone_path"])
                writer.writeheader()
                writer.writerows(rows)

            status = validate_dataset.main([str(root), "--require-emx", "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "validation_summary.json").read_text(encoding="utf-8"))
            touchstone_checks = [item for item in summary["checks"] if item["name"] == "touchstone files"]
            self.assertEqual(touchstone_checks[0]["status"], "WARN")
            self.assertIn("not applicable", touchstone_checks[0]["detail"])

    def test_require_emx_checks_accessible_touchstone_frequency_grid(self) -> None:
        validate_dataset = _load_validate_dataset_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone_path = root / "sample.s4p"
            freqs_hz = np.array([5.0e9, 5.1e9])
            _write_touchstone(touchstone_path, freqs_hz, np.zeros((2, 4, 4), dtype=np.complex128))
            self._write_minimal_valid_dataset(root, touchstone_path, freqs_hz)

            status = validate_dataset.main(
                [
                    str(root),
                    "--require-emx",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "5.1",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "2",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "validation_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["CSV frequency metadata"]["status"], "PASS")
            self.assertEqual(checks["Touchstone frequency coverage"]["status"], "PASS")

    def test_require_emx_fails_on_touchstone_frequency_mismatch(self) -> None:
        validate_dataset = _load_validate_dataset_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone_path = root / "sample.s4p"
            freqs_hz = np.array([13.5e9, 13.875e9, 14.25e9])
            _write_touchstone(touchstone_path, freqs_hz, np.zeros((3, 4, 4), dtype=np.complex128))
            self._write_minimal_valid_dataset(root, touchstone_path, freqs_hz)

            status = validate_dataset.main(
                [
                    str(root),
                    "--require-emx",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "50.0",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "451",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "validation_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["CSV frequency metadata"]["status"], "FAIL")
            self.assertEqual(checks["Touchstone frequency coverage"]["status"], "FAIL")
            self.assertIn("expected=5000000000.0", checks["Touchstone frequency coverage"]["detail"])

    def _write_minimal_valid_dataset(self, root: Path, touchstone_path: Path, freqs_hz: np.ndarray) -> None:
        diffs = np.diff(freqs_hz)
        manifest = {
            "requested_count": 1,
            "ok_count": 1,
            "fail_count": 0,
            "port_mode": "single_ended_shield_grounded",
            "cadence_pin_purpose": 51,
            "target_frequency": {
                "start_hz": float(freqs_hz[0]),
                "stop_hz": float(freqs_hz[-1]),
                "step_hz": float(diffs[0]) if len(diffs) else None,
                "points": int(len(freqs_hz)),
            },
            "uniformity": {
                "count": 1,
                "bins": 1,
                "fields": {"primary_outer_width_um": {"histogram_min": 1, "histogram_max": 1}},
            },
            "geometry_quality": {
                "angle_checked_count": 1,
                "geometry_check_ok_count": 1,
                "primary_internal_angle_deg": {"min": {"min": 135.0, "max": 135.0}, "max": {"min": 135.0, "max": 135.0}},
                "secondary_internal_angle_deg": {"min": {"min": 135.0, "max": 135.0}, "max": {"min": 135.0, "max": 135.0}},
                "primary_terminal_interface_angle_deg": {"min": {"min": 90.0, "max": 90.0}, "max": {"min": 90.0, "max": 90.0}},
                "secondary_terminal_interface_angle_deg": {"min": {"min": 90.0, "max": 90.0}, "max": {"min": 90.0, "max": 90.0}},
            },
            "sparameter_quality": {
                "valid_sparameter_count": 1,
                "reciprocity_error_abs": {"max": 0.0},
                "passivity_sigma_max": {"max": 0.9},
                "passivity_excess": {"max": 0.0},
                "passivity_excess_count_gt_1e_3": 0,
            },
            "zin_coverage": {
                "valid_zin_count": 1,
                "real_ohm": {"min": 10.0, "max": 20.0},
                "imag_ohm": {"min": -5.0, "max": 5.0},
                "abs_ohm": {"min": 10.0, "max": 21.0},
            },
        }
        row = {
            "ok": "true",
            "touchstone_path": str(touchstone_path),
            "sparam_freq_start_hz": str(float(freqs_hz[0])),
            "sparam_freq_stop_hz": str(float(freqs_hz[-1])),
            "sparam_freq_step_hz": str(float(diffs[0]) if len(diffs) else ""),
            "sparam_freq_points": str(int(len(freqs_hz))),
        }
        (root / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
