from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_report_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_project_validation_report.py"
    spec = importlib.util.spec_from_file_location("build_project_validation_report_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class BuildProjectValidationReportScriptTest(TransformerToolboxTestBase):
    def test_asset_evidence_use_blocks_final_comparison_figures_when_emx_first_fails(self):
        report = _load_report_module()
        blocked_chain = {
            "overall_status": "BLOCKED_BY_EMX_REFERENCE",
            "decision": "DO_NOT_USE_HFSS_COMPARISON",
        }

        emx_asset = report.Asset(
            "EMX-first gate 核心 L/Q/K 曲线",
            Path("/missing/emx.png"),
            "30a_emx_first_gate_core_metrics.png",
            "blocked EMX plot",
        )
        hfss_asset = report.Asset(
            "HFSS Touchstone ADS 等效指标预检",
            Path("/missing/hfss.png"),
            "18_hfss_touchstone_ads_equivalent_metrics.png",
            "standalone HFSS plot",
        )
        clearance_asset = report.Asset(
            "final500 clearance 通过/拒绝数量",
            Path("/missing/clearance.png"),
            "06_clearance_pass_fail_counts.png",
            "clearance evidence",
        )

        emx_use, emx_note = report._asset_evidence_use(emx_asset, blocked_chain)
        hfss_use, hfss_note = report._asset_evidence_use(hfss_asset, blocked_chain)
        clearance_use, clearance_note = report._asset_evidence_use(clearance_asset, blocked_chain)

        self.assertEqual(emx_use, "BLOCKED_AS_FINAL_EVIDENCE")
        self.assertIn("must not be cited as final EMX-vs-HFSS validation", emx_note)
        self.assertEqual(hfss_use, "DIAGNOSTIC_ONLY")
        self.assertIn("standalone HFSS physical sanity", hfss_note)
        self.assertEqual(clearance_use, "ACCEPTED_FOR_CURRENT_CLAIM")
        self.assertIn("limited claim", clearance_note)

    def test_ads_style_plot_detail_surfaces_blocked_plot_decision(self):
        report = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            summary_dir = package / "ads_style_metric_curves_20260613"
            _write_json(
                summary_dir / "ads_style_metric_plot_summary.json",
                {
                    "overall_status": "BLOCKED_BY_EMX_REFERENCE",
                    "decision": "DO_NOT_USE_AS_FINAL_LP_LS_Q_K_FIGURES",
                    "evidence_use": "BLOCKED_AS_FINAL_EVIDENCE",
                    "common_overlay_frequency_ghz": {"start": 13.5, "stop": 16.5, "points": 9},
                    "hfss_plot_frequency_ghz": {"start": 5.0, "stop": 50.0, "points": 451},
                    "metric_max_percent_errors_common_window": {
                        "k": 4.0,
                        "qp": 3.0,
                        "qs": 2.0,
                        "lp_nh": 1.0,
                        "ls_nh": 1.5,
                        "m_nh": 2.5,
                        "cm_single_primary_y11_plus_y12_ff": 24.0,
                    },
                },
            )

            status, detail = report._ads_style_plot_detail(package)

            self.assertEqual(status, "BLOCKED_BY_EMX_REFERENCE")
            self.assertIn("Decision=DO_NOT_USE_AS_FINAL_LP_LS_Q_K_FIGURES", detail)
            self.assertIn("evidence_use=BLOCKED_AS_FINAL_EVIDENCE", detail)
            self.assertIn("do not prove 5-50 GHz EMX validation", detail)

    def test_mars_emx_return_watch_detail_surfaces_not_accepted_boundary(self):
        report = _load_report_module()
        detail = report._mars_emx_return_watch_detail(
            {
                "overall_status": "WAITING_FOR_MARS_RETURN",
                "decision": "WAIT_FOR_MARS_WIDEBAND_EMX_RETURN",
                "evidence_use": "NOT_ACCEPTED_EMX_REFERENCE",
                "accepted_emx_reference": False,
                "iteration_count": 1,
                "stop_reason": "max_iterations",
                "s4p_candidate_count": 0,
                "tarball_candidate_count": 0,
                "verifier_decision": None,
                "latest_snapshot": {"s4p_candidate_count": 99, "tarball_candidate_count": 99},
            }
        )

        self.assertIn("evidence_use=NOT_ACCEPTED_EMX_REFERENCE", detail)
        self.assertIn("accepted_emx_reference=False", detail)
        self.assertIn("s4p_candidates=0", detail)
        self.assertIn("tarball_candidates=0", detail)
        self.assertIn("final HFSS comparison still requires accepted EMX import evidence", detail)

    def test_status_cards_include_full_pytest_health_step_when_present(self):
        report = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "local_project_health_20260613"
                / "local_project_health_summary.json",
                {
                    "overall_status": "PASS",
                    "steps": [
                        {
                            "name": "full local pytest suite",
                            "status": "PASS",
                            "detail": "449 passed, 52 skipped in 24.92s; optional extras are represented as pytest skips when unavailable",
                        }
                    ],
                },
            )

            summaries = report._load_summaries(root)
            cards = report._status_cards(root, root / "package", summaries)

        health_cards = [card for card in cards if card["name"] == "Local project health check"]
        self.assertEqual(len(health_cards), 1)
        self.assertEqual(health_cards[0]["status"], "PASS")
        self.assertIn("Full pytest gate: 449 passed, 52 skipped", health_cards[0]["detail"])
        self.assertIn("optional extras are represented as pytest skips", health_cards[0]["detail"])

    def test_report_includes_248k_launch_readiness_not_ready_gate(self):
        report = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "mars_handoff_verify_20260613" / "mars_handoff_verify_summary.json",
                {"overall_status": "PASS", "checks": [{"status": "PASS", "name": f"old check {idx}", "detail": "old"} for idx in range(20)]},
            )
            _write_json(
                root / "mars_handoff_verify_20260613_latest" / "mars_handoff_verify_summary.json",
                {"overall_status": "PASS", "checks": [{"status": "PASS", "name": f"latest check {idx}", "detail": "latest"} for idx in range(24)]},
            )
            _write_json(
                root / "248k_launch_readiness_local_20260613" / "248k_launch_readiness_summary.json",
                {
                    "overall_status": "NOT_READY",
                    "checks": [
                        {"status": "PASS", "name": "248k config loads", "detail": "ok"},
                        {"status": "PASS", "name": "248k frequency grid", "detail": "ok"},
                        {"status": "PASS", "name": "248k port mode", "detail": "ok"},
                        {"status": "PASS", "name": "248k cadence pin purpose", "detail": "ok"},
                        {"status": "PASS", "name": "248k shield", "detail": "ok"},
                        {"status": "NOT_READY", "name": "248k EMX/Cadence paths", "detail": "placeholder"},
                        {"status": "NOT_READY", "name": "248k strict path preflight", "detail": "FAIL"},
                        {"status": "NOT_READY", "name": "wideband 500 quality gates", "detail": "missing"},
                        {"status": "NOT_READY", "name": "sampled HFSS/EMX batch gate", "detail": "missing"},
                    ],
                },
            )
            _write_json(
                root / "validation_chain_decision_20260614" / "validation_chain_decision_summary.json",
                {
                    "overall_status": "BLOCKED_BY_EMX_REFERENCE",
                    "decision": "DO_NOT_USE_HFSS_COMPARISON",
                    "stages": [
                        {
                            "name": "EMX-first golden reference",
                            "status": "FAIL",
                            "decision": "BLOCK_HFSS_COMPARISON",
                        },
                        {
                            "name": "HFSS geometry asset traceability",
                            "status": "PASS_DIAGNOSTIC_ONLY",
                            "decision": "DO_NOT_USE_UNTIL_EMX_ACCEPTED",
                        },
                        {
                            "name": "HFSS physical S4P gate",
                            "status": "PASS_DIAGNOSTIC_ONLY",
                            "decision": "DO_NOT_COMPARE_UNTIL_EMX_ACCEPTED",
                        },
                        {
                            "name": "Accepted EMX-vs-HFSS/ADS comparison",
                            "status": "BLOCKED_BY_EMX_REFERENCE",
                            "decision": "DO_NOT_USE_HFSS_COMPARISON",
                        },
                    ],
                },
            )
            _write_json(
                root
                / "hfss_validation"
                / "final500_ec6698dfc575950b"
                / "ads_metric_formula_consistency_20260614"
                / "ads_metric_formula_consistency_summary.json",
                {
                    "overall_status": "PASS",
                    "decision": "ADS_FORMULA_IMPLEMENTATION_ACCEPTED",
                    "frequency_ghz": {"start": 5.0, "stop": 50.0, "step": 0.1, "points": 451},
                    "metric_recovery_errors": {
                        "qp": {"max_percent_error": 1.0e-12},
                        "k": {"max_percent_error": 5.0e-13},
                    },
                    "artifacts": {
                        "ads_data_display_template": str(
                            root
                            / "hfss_validation"
                            / "final500_ec6698dfc575950b"
                            / "ads_metric_formula_consistency_20260614"
                            / "ADS_DATA_DISPLAY_LP_LS_Q_K_TEMPLATE.md"
                        )
                    },
                },
            )
            _write_json(
                root / "mars_next_action_packet_20260614" / "mars_next_action_packet_summary.json",
                {
                    "overall_status": "PASS",
                    "decision": "READY_FOR_MARS_TARGET_EMX_RERUN",
                    "status_counts": {"PASS": 6},
                },
            )
            package_dir = root / "package"
            _write_json(
                package_dir / "ads_photo_reference_candidate_scan_20260613" / "ads_photo_reference_candidate_scan_summary.json",
                {
                    "overall_status": "REVIEW_REQUIRED",
                    "counts": {"candidate_files": 49, "pass_emx": 0, "pass_non_emx": 1, "errors": 0},
                    "best": {
                        "source_kind": "UNKNOWN",
                        "max_percent_error": 0.028,
                        "touchstone": "/home/researcher/Downloads/test of answer 2.s4p",
                    },
                    "best_emx": {
                        "source_kind": "EMX",
                        "max_percent_error": 72.86,
                        "touchstone": "/home/researcher/Downloads/hfss_case_4122/4122e3e28660397a/emx/emx.s4p",
                    },
                },
            )
            _write_json(
                package_dir / "photo_matched_hfss_reference_20260613" / "photo_matched_reference_summary.json",
                {
                    "overall_status": "REVIEW_REQUIRED",
                    "frequency_ghz": {"start": 5.0, "stop": 45.0, "points": 41, "step": 1.0},
                    "metadata": {
                        "header_fields": {
                            "File": "C:/Mac/Home/Desktop/test of answer.aedt",
                            "Design": "HFSSDesign1",
                            "Setup": "Setup2",
                        }
                    },
                    "target_record": {
                        "checks": [
                            {"status": "PASS", "label": "Lp", "percent_error": 0.001},
                            {"status": "PASS", "label": "K", "percent_error": 0.028},
                        ]
                    },
                },
            )
            _write_json(
                package_dir / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json",
                {
                    "overall_status": "FAIL",
                    "decision": "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE",
                    "frequency_ghz": {"start": 13.5, "stop": 16.5, "points": 9, "step": 0.375},
                    "checks": [
                        {"status": "PASS", "name": "passivity", "detail": "sigma_max=0.9"},
                        {"status": "FAIL", "name": "ADS photo anchor", "detail": "6/6 metrics fail"},
                        {"status": "FAIL", "name": "final ADS sweep coverage", "detail": "narrowband only"},
                    ],
                    "target_record": {
                        "checks": [
                            {"status": "FAIL", "label": "Lp", "percent_error": 88.81},
                            {"status": "FAIL", "label": "K", "percent_error": 67.4},
                        ]
                    },
                    "port_pair_sensitivity": {
                        "best": {"port_pairs": "1,2:3,4", "max_percent_error": 88.81},
                        "default": {"port_pairs": "1,2:3,4", "max_percent_error": 88.81},
                    },
                },
            )
            _write_json(
                package_dir / "photo_matched_vs_target_geometry_audit_20260613" / "photo_matched_vs_target_geometry_audit_summary.json",
                {
                    "overall_status": "FAIL",
                    "decision": "DO_NOT_USE_PHOTO_MATCHED_HFSS_AS_TARGET_SAMPLE_REFERENCE",
                    "checks": [
                        {"status": "FAIL", "name": "photo project provenance", "detail": "mismatch"},
                        {"status": "PASS", "name": "target sample identity", "detail": "ok"},
                    ],
                    "dimension_comparisons": [
                        {"name": "primary overall height/diameter", "relative_delta": 1.8225}
                    ],
                },
            )
            _write_json(
                package_dir / "hfss_model_geometry_asset_audit_20260614" / "hfss_model_geometry_asset_audit_summary.json",
                {
                    "overall_status": "PASS",
                    "decision": "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS",
                    "checks": [
                        {
                            "status": "PASS",
                            "name": "HFSS top-view PNG",
                            "detail": "size=1598x1596; bytes=127528; max_channel_delta=253",
                        },
                        {
                            "status": "PASS",
                            "name": "HFSS isometric-view PNG",
                            "detail": "size=1383x1392; bytes=351453; max_channel_delta=255",
                        },
                        {
                            "status": "PASS",
                            "name": "HFSS geometry-quality PNG",
                            "detail": "size=2086x1445; bytes=187125; max_channel_delta=255",
                        },
                        {
                            "status": "PASS",
                            "name": "HFSS STEP model",
                            "detail": "bytes=50181; entity_count=1148; required STEP tokens present",
                        },
                    ],
                },
            )
            out_dir = root / "report"
            rc = report.main(["--project-root", str(root), "--package-dir", str(package_dir), "--out-dir", str(out_dir)])
            self.assertEqual(rc, 0)

            manifest = json.loads((out_dir / "report_manifest.json").read_text(encoding="utf-8"))
            self.assertIn("asset_usage_counts", manifest)
            self.assertGreaterEqual(manifest["asset_usage_counts"].get("MISSING", 0), 1)
            readiness_cards = [card for card in manifest["cards"] if card["name"] == "248k launch readiness gate"]
            self.assertEqual(len(readiness_cards), 1)
            card = readiness_cards[0]
            self.assertEqual(card["status"], "NOT_READY")
            self.assertIn("5 PASS", card["detail"])
            self.assertIn("4 NOT_READY", card["detail"])
            self.assertIn("248k EMX/Cadence paths", card["detail"])
            self.assertIn("wideband 500 quality gates", card["detail"])
            self.assertIn("sampled strict 5% HFSS/EMX evidence", card["detail"])
            candidate_cards = [card for card in manifest["cards"] if card["name"] == "ADS photo S4P candidate scan"]
            self.assertEqual(len(candidate_cards), 1)
            self.assertEqual(candidate_cards[0]["status"], "REVIEW_REQUIRED")
            self.assertIn("EMX PASS=0", candidate_cards[0]["detail"])
            self.assertIn("non-EMX PASS=1", candidate_cards[0]["detail"])
            clue_cards = [card for card in manifest["cards"] if card["name"] == "Photo-matched HFSS reference clue"]
            self.assertEqual(len(clue_cards), 1)
            self.assertEqual(clue_cards[0]["status"], "REVIEW_REQUIRED")
            self.assertIn("HFSSDesign1", clue_cards[0]["detail"])
            self.assertIn("does not satisfy the EMX reference-source gate", clue_cards[0]["detail"])
            emx_gate_cards = [card for card in manifest["cards"] if card["name"] == "EMX-first golden reference gate"]
            self.assertEqual(len(emx_gate_cards), 1)
            self.assertEqual(emx_gate_cards[0]["status"], "FAIL")
            self.assertIn("DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE", emx_gate_cards[0]["detail"])
            self.assertIn("15 GHz ADS-photo anchor", emx_gate_cards[0]["detail"])
            photo_geometry_cards = [card for card in manifest["cards"] if card["name"] == "Photo-matched HFSS target-geometry audit"]
            self.assertEqual(len(photo_geometry_cards), 1)
            self.assertEqual(photo_geometry_cards[0]["status"], "FAIL")
            self.assertIn("DO_NOT_USE_PHOTO_MATCHED_HFSS_AS_TARGET_SAMPLE_REFERENCE", photo_geometry_cards[0]["detail"])
            handoff_cards = [card for card in manifest["cards"] if card["name"] == "MARS handoff verifier"]
            self.assertEqual(len(handoff_cards), 1)
            self.assertIn("24 checks", handoff_cards[0]["detail"])
            self.assertNotIn("20 checks", handoff_cards[0]["detail"])
            action_cards = [card for card in manifest["cards"] if card["name"] == "MARS next-action packet"]
            self.assertEqual(len(action_cards), 1)
            self.assertEqual(action_cards[0]["status"], "PASS")
            self.assertIn("target 5-50 GHz / 0.1 GHz EMX rerun", action_cards[0]["detail"])
            formula_cards = [card for card in manifest["cards"] if card["name"] == "ADS metric formula consistency"]
            self.assertEqual(len(formula_cards), 1)
            self.assertEqual(formula_cards[0]["status"], "PASS")
            self.assertIn("synthetic known coupled transformer", formula_cards[0]["detail"])
            self.assertIn("does not validate any EMX or HFSS", formula_cards[0]["detail"])
            self.assertIn("ADS Data Display template", formula_cards[0]["detail"])
            geometry_cards = [card for card in manifest["cards"] if card["name"] == "HFSS model geometry assets"]
            self.assertEqual(len(geometry_cards), 1)
            self.assertEqual(geometry_cards[0]["status"], "PASS")
            self.assertIn("entity_count=1148", geometry_cards[0]["detail"])
            self.assertIn("proves only geometry asset traceability", geometry_cards[0]["detail"])

            markdown = (out_dir / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613.md").read_text(encoding="utf-8")
            self.assertIn("Claims Not Allowed Yet", markdown)
            self.assertIn("248k launch readiness gate", markdown)
            self.assertIn("MARS next-action packet", markdown)
            self.assertIn("ADS metric formula consistency", markdown)
            self.assertIn("audit_248k_launch_readiness.py", markdown)
            self.assertIn("EMX-first golden reference gate", markdown)
            self.assertIn("Photo-matched HFSS target-geometry audit", markdown)
            self.assertIn("ADS photo S4P candidate scan", markdown)
            self.assertIn("Photo-matched HFSS reference clue", markdown)
            self.assertIn("HFSS model geometry assets", markdown)
            self.assertIn("decodable, sufficiently large, nonblank", markdown)
            self.assertIn("approved port-pair sensitivity CSV gate", markdown)
            self.assertIn("per-adjacent-step frequency-grid checks", markdown)
            self.assertIn("every adjacent frequency step is checked", markdown)
            self.assertIn("accepted_emx_reference_bundle.status=READY_FOR_HFSS", markdown)
            self.assertIn("ads_style_target_marker_values_15ghz.csv", markdown)
            self.assertIn("ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md", markdown)
            self.assertIn("verify_accepted_emx_hfss_ads_figures.py", markdown)
            self.assertIn("ACCEPT_FINAL_LP_LS_Q_K_FIGURES", markdown)
            self.assertIn("Evidence use:", markdown)
