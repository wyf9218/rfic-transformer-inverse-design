from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_next_gen_s8p_objective_acceptance_audit.py"
    spec = importlib.util.spec_from_file_location("build_next_gen_s8p_objective_acceptance_audit_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _launch_summary(root: Path) -> Path:
    return _write_json(
        root / "launch" / "physical_feature_s8p_launch_packet_summary.json",
        {
            "overall_status": "PASS",
            "run_dir": str(root / "run" / "new_s8p_physical_feature_emx_500"),
            "input_feature_contract": {
                "zin_columns": [],
                "lp_columns": ["lp_nh_center"],
                "ls_columns": ["ls_nh_center"],
                "q_columns": ["q_center"],
                "k_columns": ["k_center"],
            },
            "port_map_approval_summary": {
                "touchstone_port_order": ["P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"],
            },
            "parallel_emx_contract": {
                "expected_emx_count": 500,
                "emx_max_count": 500,
                "expected_jobs": 8,
                "jobs": 8,
                "wideband_frequency_grid": "5-60 GHz inclusive, 0.5 GHz step, 111 points",
            },
        },
    )


def _combined_summary(root: Path) -> Path:
    return _write_json(
        root / "combined" / "s8p_combined_approval_readiness_summary.json",
        {
            "overall_status": "PASS",
            "can_start_real_emx": True,
            "port_map": {
                "role_records": [
                    {"port": f"P{idx:03d}", "ground": f"P{idx:03d}_G"}
                    for idx in range(1, 9)
                ]
            },
            "geometry_contract": {
                "bridge_width_um": 10.0,
                "vertical_length_diameter_ratio": 1.5,
                "ground_frame_width_um": 100.0,
                "superseded_literal_10nm_bridge_width_um": 0.01,
            },
        },
    )


def _run_status(root: Path, status: str = "WAITING") -> Path:
    requirements = [
        "8-worker EMX candidate queue completed",
        "dataset_rows.csv has expected 500 successful rows",
        "all successful rows point to valid .s8p files",
        "all successful rows are traceable to EMX-generated .s8p files",
        "dataset manifest matches approved S8P topology contract",
        "S8P dataset quality gates",
        "random physical-feature validation sample selected",
        "selected sample S8P port-pair physical diagnostic passed",
        "selected sample 8-port layout audit",
        "selected sample HFSS rebuild handoff",
        "selected sample HFSS AEDT scripts",
        "HFSS payload geometry views rendered",
        "post-EMX inverse training table uses Lp/Ls/Q/K without Zin",
        "post-EMX inverse model quality audit passed",
        "saved Lp/Ls/Q/K-to-geometry inverse model is trained",
        "EMX/HFSS Lp/Ls/Q/K/Kw postrun comparison completed",
        "HFSS build port manifest proves 8-port integration lines",
        "final report evidence packet passed",
    ]
    all_pass = status == "PASS"
    return _write_json(
        root / "run_status" / "next_gen_s8p_mars_run_status_summary.json",
        {
            "overall_status": "PASS" if all_pass else "WAITING_FOR_MARS_EMX",
            "decision": "READY" if all_pass else "CONTINUE",
            "run_dir": str(root / "run_status" / "new_s8p_physical_feature_emx_500"),
            "evidence": [
                {
                    "status": "PASS" if all_pass else "WAITING",
                    "requirement": requirement,
                    "evidence": "fixture",
                    "next_action": "fixture next action",
                }
                for requirement in requirements
            ],
        },
    )


def _sync_summary(root: Path) -> Path:
    return _write_json(
        root / "sync" / "next_gen_s8p_mars_sync_packet_summary_20260619.json",
        {
            "status": "PASS",
            "checks": [{"name": "tarball_sha256_matches", "pass": True}],
        },
    )


class BuildNextGenS8pObjectiveAcceptanceAuditScriptTest(TransformerToolboxTestBase):
    def test_waits_when_external_emx_hfss_and_inverse_evidence_are_missing(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status = mod.main(
                [
                    "--launch-summary",
                    str(_launch_summary(root)),
                    "--combined-approval-summary",
                    str(_combined_summary(root)),
                    "--run-status-summary",
                    str(_run_status(root, "WAITING")),
                    "--sync-summary",
                    str(_sync_summary(root)),
                    "--out-dir",
                    str(root / "audit"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "next_gen_s8p_objective_acceptance_summary.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["final_objective_ready"])
            self.assertEqual(summary["overall_status"], "WAITING")
            self.assertEqual(summary["objective_statuses"]["2"], "PASS")
            self.assertEqual(summary["objective_statuses"]["3"], "WAITING")

    def test_passes_only_when_all_objective_evidence_is_pass(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status = mod.main(
                [
                    "--launch-summary",
                    str(_launch_summary(root)),
                    "--combined-approval-summary",
                    str(_combined_summary(root)),
                    "--run-status-summary",
                    str(_run_status(root, "PASS")),
                    "--sync-summary",
                    str(_sync_summary(root)),
                    "--out-dir",
                    str(root / "audit"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "next_gen_s8p_objective_acceptance_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["final_objective_ready"])
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertTrue(all(status == "PASS" for status in summary["objective_statuses"].values()))

    def test_rejects_mixed_launch_and_run_status_directories(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            launch_path = _launch_summary(root)
            launch = json.loads(launch_path.read_text(encoding="utf-8"))
            launch["run_dir"] = str(root / "launch" / "s8p_emx_candidate_run")
            launch_path.write_text(json.dumps(launch, indent=2), encoding="utf-8")

            status = mod.main(
                [
                    "--launch-summary",
                    str(launch_path),
                    "--combined-approval-summary",
                    str(_combined_summary(root)),
                    "--run-status-summary",
                    str(_run_status(root, "PASS")),
                    "--sync-summary",
                    str(_sync_summary(root)),
                    "--out-dir",
                    str(root / "audit"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "next_gen_s8p_objective_acceptance_summary.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["final_objective_ready"])
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["objective_statuses"]["5"], "FAIL")
            path_check = [
                item
                for item in summary["evidence"]
                if item["requirement"] == "Launch packet and run-status evidence refer to the same candidate run directory"
            ][0]
            self.assertEqual(path_check["status"], "FAIL")
