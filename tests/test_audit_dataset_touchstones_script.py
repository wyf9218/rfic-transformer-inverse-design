from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_dataset_audit_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_dataset_touchstones.py"
    spec = importlib.util.spec_from_file_location("audit_dataset_touchstones_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_good_s4p(path: Path, freqs_hz: np.ndarray) -> None:
    target = default_target_spec()
    diff = build_lumped_transformer_sparameters(freqs_hz=freqs_hz, target=target, q_primary=18.0, q_secondary=16.0)
    single = differential_2port_to_4port_s(
        freqs_hz=freqs_hz,
        s_diff=diff.s_matrix,
        diff_z0_ohm=target.differential_reference_impedance_ohm,
        single_z0_ohm=50.0,
    )
    _write_touchstone(path, single.freqs_hz, single.s_matrix)


class AuditDatasetTouchstonesScriptTest(TransformerToolboxTestBase):
    def test_dataset_touchstone_audit_summarizes_pass_and_fail(self) -> None:
        audit = _load_dataset_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            good = root / "evaluations" / "good" / "emx" / "emx.s4p"
            bad = root / "evaluations" / "bad" / "emx" / "emx.s4p"
            good.parent.mkdir(parents=True)
            bad.parent.mkdir(parents=True)
            freqs_hz = np.asarray([5.0e9, 10.0e9, 15.0e9])
            _write_good_s4p(good, freqs_hz)
            _write_touchstone(bad, freqs_hz, np.zeros((3, 4, 4), dtype=np.complex128))
            with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ok", "touchstone_path"])
                writer.writeheader()
                writer.writerow({"ok": "true", "touchstone_path": str(good)})
                writer.writerow({"ok": "true", "touchstone_path": str(bad)})

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--all",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "15",
                    "--expected-frequency-step-ghz",
                    "5",
                    "--expected-frequency-points",
                    "3",
                    "--required-sweep-start-ghz",
                    "5",
                    "--required-sweep-stop-ghz",
                    "15",
                    "--target-frequency-ghz",
                    "10",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "dataset_touchstone_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["discovered_count"], 2)
            self.assertEqual(summary["audited_count"], 2)
            self.assertEqual(summary["pass_count"], 1)
            self.assertEqual(summary["fail_count"], 1)
            self.assertEqual(summary["matrix_quality_summary"]["audited_with_matrix_quality"], 2)
            self.assertIsNotNone(summary["matrix_quality_summary"]["passivity_sigma_max"])
            self.assertIn("target-frequency transformer metrics", summary["failure_reason_counts"])
            rows = list(csv.DictReader((root / "audit" / "dataset_touchstone_audit_rows.csv").open(encoding="utf-8")))
            self.assertEqual(sorted(row["overall_status"] for row in rows), ["FAIL", "PASS"])
            self.assertTrue(all(row["reciprocity_error_abs_max"] for row in rows))
            self.assertTrue(all(row["passivity_sigma_max"] for row in rows))
            self.assertTrue((root / "audit" / "dataset_touchstone_audit_report.md").exists())

    def test_dataset_touchstone_audit_can_sample_from_glob_without_rows_csv(self) -> None:
        audit = _load_dataset_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for name in ("a", "b", "c"):
                path = root / "evaluations" / name / "emx" / "emx.s4p"
                path.parent.mkdir(parents=True)
                _write_good_s4p(path, np.asarray([5.0e9, 10.0e9, 15.0e9]))

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--sample-size",
                    "2",
                    "--seed",
                    "7",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "15",
                    "--expected-frequency-step-ghz",
                    "5",
                    "--expected-frequency-points",
                    "3",
                    "--required-sweep-start-ghz",
                    "5",
                    "--required-sweep-stop-ghz",
                    "15",
                    "--target-frequency-ghz",
                    "10",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "dataset_touchstone_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["discovered_count"], 3)
            self.assertEqual(summary["audited_count"], 2)
            self.assertEqual(summary["pass_count"], 2)

    def test_dataset_touchstone_audit_discovers_parallel_shards_without_rows_csv(self) -> None:
        audit = _load_dataset_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for shard, name in (("000", "a"), ("001", "b"), ("001", "c")):
                path = root / "parallel_shards" / f"shard_{shard}" / "evaluations" / name / "emx" / "emx.s4p"
                path.parent.mkdir(parents=True)
                _write_good_s4p(path, np.asarray([5.0e9, 10.0e9, 15.0e9]))

            status = audit.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "audit"),
                    "--sample-size",
                    "2",
                    "--seed",
                    "7",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "15",
                    "--expected-frequency-step-ghz",
                    "5",
                    "--expected-frequency-points",
                    "3",
                    "--required-sweep-start-ghz",
                    "5",
                    "--required-sweep-stop-ghz",
                    "15",
                    "--target-frequency-ghz",
                    "10",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "dataset_touchstone_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["discovered_count"], 3)
            self.assertEqual(summary["audited_count"], 2)
            self.assertEqual(summary["pass_count"], 2)
            self.assertTrue(all("parallel_shards" in path for path in summary["selected_paths"]))

    def test_dataset_touchstone_audit_fails_when_no_touchstones_are_found(self) -> None:
        audit = _load_dataset_audit_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            status = audit.main([str(root), "--out-dir", str(root / "audit")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "audit" / "dataset_touchstone_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["discovered_count"], 0)
            self.assertEqual(summary["audited_count"], 0)
