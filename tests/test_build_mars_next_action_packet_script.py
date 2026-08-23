from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_mars_next_action_packet.py"
    spec = importlib.util.spec_from_file_location("build_mars_next_action_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class BuildMarsNextActionPacketScriptTest(TransformerToolboxTestBase):
    def test_packet_is_ready_when_emx_blocked_but_mars_rerun_artifacts_are_prepared(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "project"
            package = root / "package"
            target_dir = project / "hfss_validation" / "final500_ec6698dfc575950b" / "target_emx_wideband_rerun_20260613"
            handoff_target = project / "mars_handoff_bundle_20260613" / "project_runbook" / "target_emx_wideband_rerun_20260613"

            _write_json(
                project / "validation_chain_decision_20260614" / "validation_chain_decision_summary.json",
                {
                    "overall_status": "BLOCKED_BY_EMX_REFERENCE",
                    "decision": "DO_NOT_USE_HFSS_COMPARISON",
                    "stages": [
                        {"name": "EMX-first golden reference", "status": "FAIL"},
                        {"name": "HFSS geometry asset traceability", "status": "PASS_DIAGNOSTIC_ONLY"},
                        {"name": "HFSS physical S4P gate", "status": "PASS_DIAGNOSTIC_ONLY"},
                        {"name": "Accepted EMX-vs-HFSS/ADS comparison", "status": "BLOCKED_BY_EMX_REFERENCE"},
                    ],
                },
            )
            _write_json(
                target_dir / "target_emx_wideband_rerun_summary.json",
                {
                    "overall_status": "PASS",
                    "decision": "READY_FOR_MARS_EMX_RERUN_COMMAND_ONLY",
                    "generated_output_s4p": "emx_wideband_5_50_0p1/emx.s4p",
                    "generated_frequency_hz": {
                        "start": 5.0e9,
                        "stop": 50.0e9,
                        "step": 1.0e8,
                        "points": 451,
                    },
                },
            )
            _write_json(
                target_dir / "target_emx_wideband_postrun_validation_summary.json",
                {
                    "overall_status": "PASS",
                    "decision": "READY_FOR_MARS_POSTRUN_VALIDATION",
                    "expected_emx_s4p": "emx_wideband_5_50_0p1/emx.s4p",
                    "default_validation_dir": "emx_wideband_5_50_0p1/validation_20260613",
                    "checks": [{"status": "PASS", "name": "post-run validation command fragments", "detail": "ok"}],
                },
            )
            _write_json(
                project / "mars_handoff_verify_20260613_latest" / "mars_handoff_verify_summary.json",
                {"overall_status": "PASS", "checks": [{"status": "PASS", "name": "required files", "detail": "ok"}]},
            )
            _write_json(
                project / "acceptance_matrix_20260613.json",
                {"overall_status": "INCOMPLETE", "status_counts": {"PASS": 15, "BLOCKED": 1}},
            )
            _write_json(
                package / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json",
                {"asset_usage_counts": {"BLOCKED_AS_FINAL_EVIDENCE": 9, "DIAGNOSTIC_ONLY": 9}},
            )
            for path in (
                handoff_target / "target_emx_wideband_rerun.commands.sh",
                handoff_target / "target_emx_wideband_postrun_validation.commands.sh",
                project / "mars_handoff_bundle_20260613.tar.gz",
                project / "mars_handoff_bundle_20260613.tar.gz.sha256",
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("echo ok\n", encoding="utf-8")

            status = mod.main(
                [
                    "--project-root",
                    str(project),
                    "--package-dir",
                    str(package),
                    "--out-dir",
                    str(root / "out"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "mars_next_action_packet_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "READY_FOR_MARS_TARGET_EMX_RERUN")
            checks = {check["name"]: check for check in summary["checks"]}
            self.assertEqual(checks["validation-chain blocks HFSS comparison"]["status"], "PASS")
            self.assertIn("target_emx_wideband_rerun.commands.sh", summary["mars_commands"][1]["command_file"])
            transfer_files = "\n".join(summary["expected_transfer_files_after_postrun"])
            self.assertIn("emx.s4p", transfer_files)
            self.assertIn("validation_20260613_transfer.tar.gz", transfer_files)
            self.assertIn("emx_first_validation_gate_20260613/emx_first_validation_gate_summary.json", transfer_files)
            self.assertIn(
                "emx_first_validation_gate_20260613/emx_first_validation_gate_port_pair_sensitivity.csv",
                transfer_files,
            )
            self.assertNotIn("emx_first_validation_gate/emx_first_validation_gate_summary.json", transfer_files)
            self.assertNotIn("target_emx_postrun_validation_package.tar.gz", transfer_files)
            local_commands = "\n".join(command["command"] for command in summary["local_after_mars_commands"])
            self.assertIn("rsync -av --progress", local_commands)
            self.assertIn("verify_target_emx_postrun_package.py", local_commands)
            self.assertIn("--require-emx-s4p", local_commands)
            self.assertIn("run_accepted_emx_hfss_ads_validation.py", local_commands)
            self.assertIn("--hfss-geometry-summary", local_commands)
            self.assertIn("verify_accepted_emx_hfss_ads_figures.py", local_commands)
            self.assertIn("target_emx_postrun_download_20260613", local_commands)
            self.assertIn("target_emx_postrun_import_summary.json", local_commands)
            self.assertIn(
                "approved pair 1,2:3,4 PASS",
                "\n".join(summary["local_postrun_import_requirements"]),
            )
            self.assertIn(
                "ADS no-extrapolation plot grid PASS",
                "\n".join(summary["local_postrun_import_requirements"]),
            )
            self.assertIn(
                "accepted_emx_reference_bundle.status=READY_FOR_HFSS",
                "\n".join(summary["local_postrun_import_requirements"]),
            )
            final_requirements = "\n".join(summary["final_hfss_ads_evidence_requirements"])
            self.assertIn("ACCEPT_HFSS_VALIDATION_SAMPLE", final_requirements)
            self.assertIn("ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS", final_requirements)
            self.assertIn("ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md", final_requirements)
            self.assertIn("ACCEPT_FINAL_LP_LS_Q_K_FIGURES", final_requirements)
            self.assertIn("ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS", "\n".join(summary["guardrails"]))
            self.assertIn("15 GHz marker table", "\n".join(summary["guardrails"]))
            report = (root / "out" / "MARS_NEXT_ACTION_PACKET_20260614_CN.md").read_text(encoding="utf-8")
            self.assertIn("MARS 下一步操作包", report)
            self.assertIn("Do not run HFSS comparison", report)
            self.assertIn("本地拉回/导入/最终验证命令模板", report)
            self.assertIn("Pull target EMX post-run files to local desktop", report)
            self.assertIn("Verify local accepted EMX import bundle", report)
            self.assertIn("Run accepted EMX vs HFSS/ADS validation after HFSS export exists", report)
            self.assertIn("本地 post-run import 必须通过的门禁", report)
            self.assertIn("EMX 通过后最终 HFSS/ADS 证据门禁", report)
            self.assertIn("ADS no-extrapolation plot grid PASS", report)
            self.assertIn("EMX-first port-pair sensitivity CSV gate PASS", report)
            self.assertIn("accepted_emx_reference_bundle.status=READY_FOR_HFSS", report)
            self.assertIn("ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md", report)
            self.assertIn("ACCEPT_FINAL_LP_LS_Q_K_FIGURES", report)
