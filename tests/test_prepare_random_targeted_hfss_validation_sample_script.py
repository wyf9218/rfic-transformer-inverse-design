from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_sample_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "prepare_random_targeted_hfss_validation_sample.py"
    )
    spec = importlib.util.spec_from_file_location("prepare_random_targeted_hfss_validation_sample_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_manifest(dataset_dir: Path) -> None:
    (dataset_dir / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "port_mode": "single_ended_shield_grounded",
                "cadence_pin_purpose": 51,
                "target_frequency": {
                    "frequency_start_hz": 5.0e9,
                    "frequency_stop_hz": 50.0e9,
                    "frequency_step_hz": 0.1e9,
                    "band_points": 451,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_rows(dataset_dir: Path, rows: list[dict[str, object]]) -> None:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with (dataset_dir / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


class PrepareRandomTargetedHfssValidationSampleScriptTest(TransformerToolboxTestBase):
    def test_selects_traceable_sample_and_copies_touchstone(self) -> None:
        mod = _load_sample_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            dataset.mkdir()
            touchstone = dataset / "sample_a.s4p"
            touchstone.write_text("! fake s4p for selection only\n", encoding="ascii")
            _write_manifest(dataset)
            _write_rows(
                dataset,
                [
                    {
                        "evaluation": "sample_a",
                        "ok": "true",
                        "inside_target_bin": "true",
                        "touchstone_path": "/remote/mars/path/sample_a.s4p",
                        "geom__primary_outer_width_um": 120,
                        "geom__secondary_outer_width_um": 100,
                        "queue__candidate_id": "cand_a",
                    }
                ],
            )
            out = root / "out"

            status = mod.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--out-dir",
                    str(out),
                    "--expected-count",
                    "1",
                    "--copy-touchstone",
                    "--seed",
                    "unit-test",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((out / "random_targeted_hfss_validation_sample_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(summary["sample_id"], "sample_a")
            self.assertEqual(summary["geometry_field_count"], 2)
            self.assertTrue(Path(summary["touchstone"]["packet_copy"]).is_file())
            self.assertTrue((out / "geometry_for_hfss.json").is_file())
            self.assertTrue((out / "selected_sample_row.csv").is_file())
            self.assertTrue((out / "RANDOM_TARGETED_HFSS_VALIDATION_SAMPLE_CN.md").is_file())

    def test_fails_without_resolvable_touchstone(self) -> None:
        mod = _load_sample_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            dataset.mkdir()
            _write_manifest(dataset)
            _write_rows(
                dataset,
                [
                    {
                        "evaluation": "sample_missing",
                        "ok": "true",
                        "inside_target_bin": "true",
                        "touchstone_path": "/remote/mars/path/sample_missing.s4p",
                        "geom__primary_outer_width_um": 120,
                    }
                ],
            )
            out = root / "out"

            status = mod.main(
                [
                    "--dataset-dir",
                    str(dataset),
                    "--out-dir",
                    str(out),
                    "--expected-count",
                    "1",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((out / "random_targeted_hfss_validation_sample_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "FAIL")
            failed = {check["name"] for check in summary["checks"] if not check["pass"]}
            self.assertIn("eligible_emx_touchstone_rows_present", failed)
