from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import subprocess
import sys


def _load_watch_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "watch_mars_emx_return.py"
    spec = importlib.util.spec_from_file_location("watch_mars_emx_return_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_completed() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["fake"], returncode=0, stdout="", stderr="")


class WatchMarsEmxReturnScriptTest(TransformerToolboxTestBase):
    def test_watch_stops_on_accepted_emx_and_writes_history(self) -> None:
        watch = _load_watch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            def fake_run(discovery_out: Path, args):
                discovery_out.mkdir(parents=True, exist_ok=True)
                (discovery_out / "target_emx_postrun_import").mkdir()
                (discovery_out / "mars_emx_return_discovery_summary.json").write_text(
                    json.dumps(
                        {
                            "overall_status": "PASS",
                            "decision": "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS",
                            "selected": {
                                "tarball": {"path": str(root / "validation_20260613_transfer.tar.gz"), "status": "PASS"},
                                "emx_s4p": {"path": str(root / "emx.s4p"), "status": "PASS"},
                            },
                            "verifier_result": {
                                "returncode": 0,
                                "summary": {"decision": "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS"},
                            },
                            "checks": [{"status": "PASS", "name": "post-run import verifier", "detail": "accepted"}],
                            "status_counts": {"PASS": 1},
                            "tarball_candidates": [{"path": str(root / "validation_20260613_transfer.tar.gz")}],
                            "s4p_candidates": [{"path": str(root / "emx.s4p")}],
                        }
                    ),
                    encoding="utf-8",
                )
                (discovery_out / "MARS_EMX_RETURN_DISCOVERY_REPORT.md").write_text("# report\n", encoding="utf-8")
                (discovery_out / "target_emx_postrun_import" / "target_emx_postrun_import_summary.json").write_text(
                    json.dumps({"decision": "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS"}),
                    encoding="utf-8",
                )
                return _fake_completed()

            watch._run_discovery = fake_run
            status = watch.main(
                [
                    "--search-root",
                    str(root),
                    "--out-dir",
                    str(root / "watch"),
                    "--interval-sec",
                    "0",
                    "--max-iterations",
                    "3",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "watch" / "mars_emx_return_watch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS")
            self.assertEqual(summary["evidence_use"], "ACCEPTED_EMX_REFERENCE_FOR_HFSS_INPUT")
            self.assertIs(summary["accepted_emx_reference"], True)
            self.assertIs(summary["hfss_comparison_allowed"], True)
            self.assertEqual(summary["s4p_candidate_count"], 1)
            self.assertEqual(summary["tarball_candidate_count"], 1)
            self.assertEqual(summary["selected_emx_s4p"], str(root / "emx.s4p"))
            self.assertEqual(summary["selected_tarball"], str(root / "validation_20260613_transfer.tar.gz"))
            self.assertEqual(summary["verifier_decision"], "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS")
            self.assertEqual(summary["next_required_action"], "RUN_ACCEPTED_EMX_HFSS_ADS_VALIDATION")
            self.assertEqual(summary["stop_reason"], "pass")
            self.assertEqual(summary["iteration_count"], 1)
            history = (root / "watch" / "mars_emx_return_watch_history.csv").read_text(encoding="utf-8")
            self.assertIn("ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS", history)
            self.assertTrue((root / "watch" / "snapshots" / "iteration_000001_mars_emx_return_discovery_summary.json").exists())
            self.assertTrue((root / "watch" / "snapshots" / "iteration_000001_target_emx_postrun_import_summary.json").exists())

    def test_watch_returns_waiting_after_max_iterations(self) -> None:
        watch = _load_watch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            def fake_run(discovery_out: Path, args):
                discovery_out.mkdir(parents=True, exist_ok=True)
                (discovery_out / "mars_emx_return_discovery_summary.json").write_text(
                    json.dumps(
                        {
                            "overall_status": "WAITING_FOR_MARS_RETURN",
                            "decision": "WAIT_FOR_MARS_WIDEBAND_EMX_RETURN",
                            "selected": {"tarball": None, "emx_s4p": None},
                            "verifier_result": None,
                            "checks": [
                                {"status": "WARN", "name": "search root", "detail": str(root)},
                                {"status": "WARN", "name": "post-run import verifier", "detail": "not run"},
                            ],
                            "status_counts": {"WARN": 2},
                            "tarball_candidates": [],
                            "s4p_candidates": [],
                        }
                    ),
                    encoding="utf-8",
                )
                (discovery_out / "MARS_EMX_RETURN_DISCOVERY_REPORT.md").write_text("# report\n", encoding="utf-8")
                return _fake_completed()

            watch._run_discovery = fake_run
            status = watch.main(
                [
                    "--search-root",
                    str(root),
                    "--out-dir",
                    str(root / "watch"),
                    "--interval-sec",
                    "0",
                    "--max-iterations",
                    "2",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "watch" / "mars_emx_return_watch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_MARS_RETURN")
            self.assertEqual(summary["decision"], "WAIT_FOR_MARS_WIDEBAND_EMX_RETURN")
            self.assertEqual(summary["evidence_use"], "NOT_ACCEPTED_EMX_REFERENCE")
            self.assertIs(summary["accepted_emx_reference"], False)
            self.assertIs(summary["hfss_comparison_allowed"], False)
            self.assertEqual(summary["s4p_candidate_count"], 0)
            self.assertEqual(summary["tarball_candidate_count"], 0)
            self.assertIsNone(summary["selected_emx_s4p"])
            self.assertIsNone(summary["selected_tarball"])
            self.assertIsNone(summary["verifier_decision"])
            self.assertEqual(summary["next_required_action"], "WAIT_FOR_AND_IMPORT_MARS_WIDEBAND_EMX_RETURN")
            self.assertIn("not an accepted EMX reference", " ".join(summary["limitations"]))
            self.assertEqual(summary["stop_reason"], "max_iterations")
            self.assertEqual(summary["iteration_count"], 2)
            self.assertEqual(summary["latest_snapshot"]["warning_checks"], ["search root", "post-run import verifier"])
