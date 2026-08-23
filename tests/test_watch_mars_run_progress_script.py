from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_watch_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "watch_mars_run_progress.py"
    spec = importlib.util.spec_from_file_location("watch_mars_run_progress_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_valid_run(root: Path) -> None:
    sample = root / "evaluations" / "abc"
    (sample / "emx").mkdir(parents=True)
    (sample / "layout").mkdir(parents=True)
    freqs = np.asarray([5.0e9, 5.1e9, 5.2e9])
    s_matrix = np.zeros((3, 4, 4), dtype=np.complex128)
    _write_touchstone(sample / "emx" / "emx.s4p", freqs, s_matrix)
    (sample / "summary.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (sample / "layout" / "transformer_layout.layout.json").write_text("{}", encoding="utf-8")
    (sample / "emx" / "emx_command.json").write_text(json.dumps(_valid_emx_command(freqs)), encoding="utf-8")
    (root / "dataset_manifest.json").write_text(json.dumps({"requested_count": 1, "ok_count": 1}), encoding="utf-8")
    (root / "dataset_rows.csv").write_text(
        "sample_id,ok,touchstone_path\nabc,true,evaluations/abc/emx/emx.s4p\n",
        encoding="utf-8",
    )
    (root / "final500_ground_clearance_audit.json").write_text(
        json.dumps(
            {
                "candidate_count": 1,
                "pass_count": 1,
                "reject_count": 0,
                "missing_or_other_count": 0,
                "selected": {"cache_key": "abc", "status": "pass_signal_to_shield_clearance"},
                "records": [
                    {
                        "cache_key": "abc",
                        "status": "pass_signal_to_shield_clearance",
                        "direct_signal_shield_overlap_area_um2": 0.0,
                        "signal_shield_clearance_violation_area_um2": 0.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _valid_emx_command(freqs_hz: np.ndarray) -> list[str]:
    command = [
        "emx",
        "layout.gds",
        "TRANSFORMER",
        "proc.proc",
        "--touchstone",
        "--s-impedance=50",
        "-s",
        "emx.s4p",
        "--include-command-line",
        "--cadence-pins=51",
    ]
    for index in range(4):
        name = f"P{index + 1:03d}"
        command.append(f"--port={name}={name}:GND")
    command.extend(str(float(freq)) for freq in freqs_hz)
    return command


class WatchMarsRunProgressScriptTest(TransformerToolboxTestBase):
    def test_watch_stops_on_pass_and_writes_history(self) -> None:
        watch = _load_watch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "run"
            root.mkdir()
            _write_valid_run(root)

            status = watch.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "watch"),
                    "--interval-sec",
                    "0",
                    "--max-iterations",
                    "3",
                    "--expected-count",
                    "1",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                    "--require-emx-command",
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                    "--require-clearance-audit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "watch" / "mars_run_progress_watch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["latest_snapshot"]["clearance_candidate_count"], 1)
            self.assertEqual(summary["stop_reason"], "pass")
            self.assertEqual(summary["iteration_count"], 1)
            history = (root / "watch" / "mars_run_progress_watch_history.csv").read_text(encoding="utf-8")
            self.assertIn("overall_status", history)
            self.assertIn("PASS", history)
            self.assertTrue((root / "watch" / "snapshots" / "iteration_000001_mars_run_progress_summary.json").exists())

    def test_watch_returns_incomplete_after_max_iterations(self) -> None:
        watch = _load_watch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "run"
            (root / "evaluations" / "abc").mkdir(parents=True)
            (root / "dataset_manifest.json").write_text(json.dumps({"requested_count": 1, "ok_count": 0}), encoding="utf-8")
            (root / "dataset_rows.csv").write_text("sample_id,ok,touchstone_path\nabc,true,\n", encoding="utf-8")

            status = watch.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "watch"),
                    "--interval-sec",
                    "0",
                    "--max-iterations",
                    "1",
                    "--expected-count",
                    "1",
                    "--require-emx-command",
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "watch" / "mars_run_progress_watch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            self.assertEqual(summary["stop_reason"], "max_iterations")
            self.assertIn("per-evaluation Touchstone files", summary["latest_snapshot"]["failed_checks"])
            self.assertIn("per-evaluation EMX command files", summary["latest_snapshot"]["failed_checks"])
