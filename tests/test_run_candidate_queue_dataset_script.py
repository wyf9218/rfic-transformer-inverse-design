from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys
from types import SimpleNamespace

from rfic_transformer_inverse_design.dataset import GROUND_CLEARANCE_AUDIT_FILENAME


def _load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_candidate_queue_dataset.py"
    spec = importlib.util.spec_from_file_location("run_candidate_queue_dataset_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_candidate_csv(path: Path, cfg, *, inside: bool = True, rows: int = 2) -> Path:
    adapter = TransformerOptimizationAdapter(cfg.bounds)
    midpoint = adapter.to_vector(cfg.bounds.midpoint())
    bounds = np.asarray(cfg.bounds.to_scipy_bounds(), dtype=float)
    span = bounds[:, 1] - bounds[:, 0]
    records = []
    for idx in range(rows):
        vector = midpoint + (idx - rows / 2.0) * 0.01 * span
        vector = np.clip(vector, bounds[:, 0], bounds[:, 1])
        row = {
            "selection_rank": str(idx + 1),
            "candidate_id": f"cand_{idx}",
            "inside_target_bin": str(bool(inside)),
            "target_rank": "1",
            "target_real_bin": "2",
            "target_imag_bin": "3",
            "pred_real_ohm": str(10.0 + idx),
            "pred_imag_ohm": str(-5.0 - idx),
        }
        for name, value in zip(adapter.field_order(), vector):
            row[f"candidate__geom__{name}"] = str(float(value))
        records.append(row)
    fieldnames = []
    for row in records:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    return path


class RunCandidateQueueDatasetScriptTest(TransformerToolboxTestBase):
    def test_execution_modes_are_mutually_exclusive(self) -> None:
        mod = _load_script_module()
        with self.assertRaises(SystemExit):
            mod._parse_args(
                [
                    "--candidate-csv",
                    "queue.csv",
                    "--out-dir",
                    "out",
                    "--create-only",
                    "--cadence-streamout-only",
                ]
            )

    def test_cadence_streamout_contract_binds_gds_and_manifest_ports(self) -> None:
        mod = _load_script_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "evaluations" / "abc"
            streamout_dir = work_dir / "streamout"
            streamout_dir.mkdir(parents=True)
            gds = streamout_dir / "transformer_layout_cadpins.gds"
            gds.write_bytes(b"gds")
            manifest_path = work_dir / "transformer_layout.layout.json"
            ports = [
                {
                    "name": f"port_{index}",
                    "signal_labels": [f"P{index:03d}"],
                    "ground_labels": [f"P{index:03d}_G"],
                    "internal_size_um": [1.0, 1.0],
                }
                for index in range(1, 5)
            ]
            manifest_path.write_text(
                json.dumps(
                    {
                        "layout_path": str(gds),
                        "top_cell": "TRANSFORMER",
                        "ports": ports,
                        "metal_layer": 39,
                        "metal_datatype": 60,
                        "ground_layer": 35,
                        "ground_datatype": 0,
                        "label_layer": 39,
                        "label_datatype": 51,
                        "cadence_pin_purpose": 51,
                    }
                ),
                encoding="utf-8",
            )
            labels = [
                label
                for index in range(1, 5)
                for label in (f"P{index:03d}", f"P{index:03d}_G")
            ]
            (work_dir / "summary_cadence_roundtrip.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "stop_after": "strmout",
                        "artifacts": {"cadence_gds": str(gds)},
                        "cadence": {"pin_labels": labels},
                    }
                ),
                encoding="utf-8",
            )
            result = SimpleNamespace(
                work_dir=work_dir,
                cache_key="abc",
                error=None,
                layout=SimpleNamespace(gds_path=gds, manifest_path=manifest_path),
                touchstone_path=None,
            )

            contract = mod._cadence_streamout_output_contract(
                results=[result],
                enabled=True,
                expected_ports=4,
            )

        self.assertTrue(contract["summary"]["checked"])
        self.assertEqual(contract["summary"]["valid_candidate_bound_gds_count"], 1)
        self.assertEqual(contract["summary"]["touchstone_file_count"], 0)
        self.assertTrue(all(check["pass"] for check in contract["checks"]))

    def test_cadence_streamout_touchstone_contract_is_explicitly_skipped(self) -> None:
        mod = _load_script_module()
        contract = mod._touchstone_output_contract(
            out_dir=Path("out"),
            rows=[],
            create_only=False,
            cadence_streamout_only=True,
            expected_extension=".s4p",
            expected_ports=4,
            expected_frequency_start_ghz=5.0,
            expected_frequency_stop_ghz=60.0,
            expected_frequency_step_ghz=1.0,
            expected_frequency_points=56,
            frequency_tolerance_hz=1.0,
            max_touchstone_checks=1,
        )

        self.assertFalse(contract["summary"]["checked"])
        self.assertEqual(
            contract["summary"]["reason"],
            "cadence_streamout_only_has_no_emx_touchstone_output",
        )

    def test_create_only_runs_fixed_candidate_queue_and_preserves_metadata(self) -> None:
        mod = _load_script_module()
        cfg = default_run_config("1t1t")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            candidate_csv = _write_candidate_csv(root / "queue.csv", cfg, inside=True, rows=2)

            status = mod.main(
                [
                    "--candidate-csv",
                    str(candidate_csv),
                    "--out-dir",
                    str(root / "out"),
                    "--create-only",
                    "--max-count",
                    "2",
                    "--force-wideband-5-60-0p5",
                    "--force-port-mode",
                    "single_ended_shield_grounded",
                    "--force-cadence-pin-purpose",
                    "51",
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "60.0",
                    "--expected-frequency-step-ghz",
                    "0.5",
                    "--expected-frequency-points",
                    "111",
                    "--expected-touchstone-extension",
                    ".s8p",
                    "--expected-ports",
                    "8",
                    "--max-touchstone-checks",
                    "500",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "candidate_queue_dataset_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["result_count"], 2)
            self.assertTrue(summary["create_only"])
            self.assertEqual(summary["target_frequency"]["points"], 111)
            self.assertFalse(summary["touchstone_output_contract"]["checked"])
            self.assertEqual(
                summary["touchstone_output_contract"]["reason"],
                "create_only_run_has_no_emx_touchstone_output",
            )
            with (root / "out" / "dataset_rows.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["queue__candidate_id"], "cand_0")
            self.assertIn("queue__pred_real_ohm", rows[0])
            self.assertTrue((root / "out" / "dataset_manifest.json").is_file())
            self.assertTrue((root / "out" / GROUND_CLEARANCE_AUDIT_FILENAME).is_file())

    def test_rejects_outside_target_bin_rows_by_default(self) -> None:
        mod = _load_script_module()
        cfg = default_run_config("1t1t")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            candidate_csv = _write_candidate_csv(root / "queue.csv", cfg, inside=False, rows=1)

            status = mod.main(
                [
                    "--candidate-csv",
                    str(candidate_csv),
                    "--out-dir",
                    str(root / "out"),
                    "--create-only",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "candidate_queue_dataset_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            failed = {check["name"] for check in summary["checks"] if not check["pass"]}
            self.assertIn("all_rows_inside_target_bin", failed)
            self.assertIn("selected_candidate_rows_present", failed)

    def test_frequency_expectation_mismatch_fails(self) -> None:
        mod = _load_script_module()
        cfg = default_run_config("1t1t")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            candidate_csv = _write_candidate_csv(root / "queue.csv", cfg, inside=True, rows=1)

            status = mod.main(
                [
                    "--candidate-csv",
                    str(candidate_csv),
                    "--out-dir",
                    str(root / "out"),
                    "--create-only",
                    "--expected-frequency-points",
                    "451",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "candidate_queue_dataset_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            failed = {check["name"] for check in summary["checks"] if not check["pass"]}
            self.assertIn("expected_frequency_points", failed)

    def test_touchstone_output_contract_validates_s8p_ports_and_frequency_grid(self) -> None:
        mod = _load_script_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "out"
            touchstone = out_dir / "evaluations" / "eval_000" / "emx" / "emx.s8p"
            touchstone.parent.mkdir(parents=True)
            freqs_hz = np.asarray([5.0e9, 5.1e9], dtype=float)
            s_matrix = np.zeros((2, 8, 8), dtype=np.complex128)
            SParameterResult(freqs_hz=freqs_hz, s_matrix=s_matrix).to_touchstone(touchstone)

            contract = mod._touchstone_output_contract(
                out_dir=out_dir,
                rows=[{"ok": True, "touchstone_path": "evaluations/eval_000/emx/emx.s8p"}],
                create_only=False,
                expected_extension=".s8p",
                expected_ports=8,
                expected_frequency_start_ghz=5.0,
                expected_frequency_stop_ghz=5.1,
                expected_frequency_step_ghz=0.1,
                expected_frequency_points=2,
                frequency_tolerance_hz=1.0,
                max_touchstone_checks=500,
            )

            self.assertTrue(contract["summary"]["checked"])
            self.assertEqual(contract["summary"]["parsed_count"], 1)
            self.assertEqual(contract["summary"]["expected_ports"], 8)
            failed = [check for check in contract["checks"] if not check["pass"]]
            self.assertEqual(failed, [])
