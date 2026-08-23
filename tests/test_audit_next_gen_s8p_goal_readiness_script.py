from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_next_gen_s8p_goal_readiness.py"
    spec = importlib.util.spec_from_file_location("audit_next_gen_s8p_goal_readiness_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_valid_s8p_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "target:",
                "  topology_mode: 1t1t",
                "  frequency_start_hz: 5000000000.0",
                "  frequency_stop_hz: 60000000000.0",
                "  frequency_step_hz: 500000000.0",
                "  band_points: 111",
                "topology:",
                "  primary:",
                "    turns: 1",
                "    center_tap: true",
                "    vdd_bar:",
                "      enabled: true",
                "      bar_layer: 74",
                "      width_um: 10.0",
                "      offset_um: 12.0",
                "  secondary:",
                "    turns: 1",
                "    center_tap: true",
                "    vdd_bar:",
                "      enabled: true",
                "      bar_layer: 39",
                "      width_um: 10.0",
                "      offset_um: 12.0",
                "emx:",
                "  port_mode: single_ended_shield_grounded",
                "  cadence_pin_purpose: 51",
                "  differential_port_pairs: '1,4:5,6'",
                "  power_line_8port:",
                "    enabled: true",
                "    bridge_width_um: 10.0",
                "    vertical_length_diameter_ratio: 1.5",
                "    bridge_y_policy: center",
                "    bridge_motion_axis: x_only",
                "    port_ground_reference: shield",
                "    port_map: [P001, P002, P003, P004, P005, P006, P007, P008]",
                "    role_labels:",
                "      primary_top: P001",
                "      left_power_top: P002",
                "      left_power_bottom: P003",
                "      primary_bottom: P004",
                "      secondary_bottom: P005",
                "      secondary_top: P006",
                "      right_power_top: P007",
                "      right_power_bottom: P008",
            ]
        ),
        encoding="utf-8",
    )


def _write_launch_summary(
    path: Path,
    *,
    scalar_q: bool = True,
    include_smoke: bool = True,
    bootstrap_mode: bool = False,
    with_targets: bool = False,
    include_target_smoke: bool | None = None,
) -> None:
    if include_target_smoke is None:
        include_target_smoke = with_targets
    feature_columns = ["lp_nh_center", "ls_nh_center", "q_center", "k_center"] if scalar_q else [
        "lp_nh_center",
        "ls_nh_center",
        "qp_center",
        "qs_center",
        "k_center",
    ]
    source_command = (
        {
            "name": "Build bootstrap geometry candidate queue for first S8P EMX labels",
            "command": [
                "python",
                "build_s8p_geometry_bootstrap_candidate_queue.py",
                "--config",
                "s8p.yaml",
                "--out-dir",
                "geometry_bootstrap_candidate_queue",
            ],
        }
        if bootstrap_mode
        else {
            "name": "Build inverse training table and candidate geometries",
            "command": [
                "python",
                "run_dataset_quality_gates.py",
                "--build-physical-feature-inverse-training-table",
                "--inverse-geometry-config",
                "s8p.yaml",
            ]
            + (
                [
                    "--derive-scalar-q-feature",
                    "--scalar-q-definition",
                    "min",
                    "--scalar-q-output-column",
                    "q_center",
                ]
                if scalar_q
                else []
            ),
        }
    )
    smoke_commands = [
        {
            "name": "Audit fixed 8-port power-line and ground-frame contract before EMX",
            "command": [
                "python",
                "audit_power_line_8port_contract.py",
                "--config",
                "s8p.yaml",
                "--expected-ground-frame-width-um",
                "100",
                "--expected-ground-frame-policy",
                "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
            ],
        },
        source_command,
        {
            "name": "Run one-candidate create-only layout smoke before EMX queue",
            "command": [
                "python",
                "run_candidate_queue_dataset.py",
                "--out-dir",
                "layout_smoke_create_only",
                "--max-count",
                "1",
                "--create-only",
                "--force-wideband-5-60-0p5",
            ],
        },
        {
            "name": "Audit one-candidate smoke 8-port power-line layout evidence",
            "command": [
                "python",
                "audit_selected_power_line_8port_layout_samples.py",
                "--samples-csv",
                "layout_smoke_create_only/dataset_rows.csv",
                "--out-dir",
                "layout_smoke_8port_audit",
                "--internal-angle-deg",
                "135.0",
                "--terminal-angle-deg",
                "90.0",
                "--angle-tolerance-deg",
                "0.001",
            ],
        },
    ]
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "READY_TO_REVIEW_AND_RUN_ON_MARS",
                "out_dir": str(path.parent),
                "candidate_source_mode": "bootstrap_geometry_queue" if bootstrap_mode else "inverse_physical_feature_prediction",
                "feature_columns": feature_columns,
                "arguments": {"jobs": 8, "emx_max_count": 500},
                "parallel_emx_contract": {
                    "expected_emx_count": 500,
                    "emx_max_count": 500,
                    "expected_jobs": 8,
                    "jobs": 8,
                },
                "inverse_model_artifacts": {
                    "post_emx_inverse_training_manifest": str(
                        path.parent
                        / "new_s8p_physical_feature_emx_500"
                        / "dataset_quality_gates_s8p_physical_feature"
                        / "physical_feature_inverse_training_table"
                        / "physical_feature_inverse_training_manifest.json"
                    ),
                    "post_emx_inverse_model_quality_summary": str(
                        path.parent
                        / "new_s8p_physical_feature_emx_500"
                        / "dataset_quality_gates_s8p_physical_feature"
                        / "physical_feature_inverse_model_quality"
                        / "physical_feature_inverse_model_quality_summary.json"
                    ),
                    "post_emx_saved_inverse_model_summary": str(
                        path.parent
                        / "new_s8p_physical_feature_emx_500"
                        / "dataset_quality_gates_s8p_physical_feature"
                        / "physical_feature_saved_inverse_model"
                        / "physical_feature_inverse_model_training_summary.json"
                    ),
                    "post_emx_saved_inverse_target_predictions": str(
                        path.parent
                        / "new_s8p_physical_feature_emx_500"
                        / "dataset_quality_gates_s8p_physical_feature"
                        / "physical_feature_saved_inverse_model"
                        / "physical_feature_inverse_model_target_predictions.csv"
                    ),
                    "post_emx_saved_inverse_target_layout_smoke_summary": str(
                        path.parent
                        / "new_s8p_physical_feature_emx_500"
                        / "dataset_quality_gates_s8p_physical_feature"
                        / "physical_feature_saved_inverse_target_layout_smoke"
                        / "candidate_queue_dataset_summary.json"
                    ),
                },
                "targets": (
                    [
                        {
                            "lp_nh_center": 0.8,
                            "ls_nh_center": 1.1,
                            "q_center": 9.0,
                            "k_center": 0.45,
                        }
                    ]
                    if with_targets
                    else []
                ),
                "commands": (smoke_commands if include_smoke else [])
                + [
                    {
                        "name": "Run candidate geometries through 8-worker EMX dataset generation",
                        "command": [
                            "python",
                            "run_candidate_queue_dataset_parallel.py",
                            "--jobs",
                            "8",
                            "--expected-jobs",
                            "8",
                            "--max-count",
                            "500",
                            "--expected-count",
                            "500",
                            "--resume-completed",
                            "--force-wideband-5-60-0p5",
                        ],
                    },
                    {
                        "name": "Run S8P physical-feature quality gates and select validation sample",
                        "command": [
                            "python",
                            "run_dataset_quality_gates.py",
                            "new_s8p_physical_feature_emx_500",
                            "--out-dir",
                            "dataset_quality_gates_s8p_physical_feature",
                            "--audit-s8p-physical-feature-dataset",
                        ]
                        + (
                            [
                                "--derive-scalar-q-feature",
                                "--scalar-q-definition",
                                "min",
                                "--scalar-q-output-column",
                                "q_center",
                            ]
                            if scalar_q
                            else []
                        ),
                    },
                    {
                        "name": "Plan Lp/Ls/Q/K response-space coverage and sparse-bin acquisition targets",
                        "command": [
                            "python",
                            "plan_physical_feature_balanced_acquisition.py",
                            "dataset_quality_gates_s8p_physical_feature/scalar_q_feature_dataset",
                            "--out-dir",
                            "dataset_quality_gates_s8p_physical_feature/physical_feature_balanced_acquisition_plan",
                            "--feature-columns",
                            ",".join(feature_columns),
                        ],
                    },
                    {
                        "name": "Build post-EMX Lp/Ls/Q/K inverse training table from generated S8P labels",
                        "command": [
                            "python",
                            "build_physical_feature_inverse_training_table.py",
                            "dataset_quality_gates_s8p_physical_feature/scalar_q_feature_dataset",
                            "--out-dir",
                            "dataset_quality_gates_s8p_physical_feature/physical_feature_inverse_training_table",
                            "--feature-columns",
                            ",".join(feature_columns),
                            "--config",
                            "s8p.yaml",
                            "--no-fail-exit",
                        ],
                    },
                    {
                        "name": "Audit post-EMX Lp/Ls/Q/K inverse-model quality with leave-one-out KNN",
                        "command": [
                            "python",
                            "audit_physical_feature_inverse_model_quality.py",
                            "--training-csv",
                            "dataset_quality_gates_s8p_physical_feature/physical_feature_inverse_training_table/physical_feature_inverse_training_table.csv",
                            "--out-dir",
                            "dataset_quality_gates_s8p_physical_feature/physical_feature_inverse_model_quality",
                            "--k-neighbors",
                            "8",
                            "--no-fail-exit",
                        ],
                    },
                    {
                        "name": "Train saved baseline Lp/Ls/Q/K-to-geometry inverse model",
                        "command": [
                            "python",
                            "train_physical_feature_inverse_model.py",
                            "--training-csv",
                            "dataset_quality_gates_s8p_physical_feature/physical_feature_inverse_training_table/physical_feature_inverse_training_table.csv",
                            "--out-dir",
                            "dataset_quality_gates_s8p_physical_feature/physical_feature_saved_inverse_model",
                            "--config",
                            "s8p.yaml",
                            "--target-json",
                            "physical_feature_inverse_targets.json",
                            "--no-fail-exit",
                        ],
                        "expected_outputs": [
                            "dataset_quality_gates_s8p_physical_feature/physical_feature_saved_inverse_model/physical_feature_inverse_model.json",
                            "dataset_quality_gates_s8p_physical_feature/physical_feature_saved_inverse_model/physical_feature_inverse_model_training_summary.json",
                            "dataset_quality_gates_s8p_physical_feature/physical_feature_saved_inverse_model/physical_feature_inverse_model_target_predictions.csv",
                        ],
                    },
                    *(
                        [
                            {
                                "name": "Run saved-model target geometry create-only layout smoke",
                                "command": [
                                    "python",
                                    "run_candidate_queue_dataset.py",
                                    "--candidate-csv",
                                    "dataset_quality_gates_s8p_physical_feature/physical_feature_saved_inverse_model/physical_feature_inverse_model_target_predictions.csv",
                                    "--out-dir",
                                    "dataset_quality_gates_s8p_physical_feature/physical_feature_saved_inverse_target_layout_smoke",
                                    "--config",
                                    "s8p.yaml",
                                    "--max-count",
                                    "1",
                                    "--batch-size",
                                    "1",
                                    "--create-only",
                                    "--force-wideband-5-60-0p5",
                                ],
                            }
                        ]
                        if with_targets and include_target_smoke
                        else []
                    ),
                    {"name": "Build selected sample HFSS rebuild handoff packet", "command": ["python", "build_selected_s8p_hfss_handoff_packet.py"]},
                    {"name": "Generate selected sample HFSS AEDT build/solve scripts", "command": ["python", "build_s8p_hfss_aedt_scripts_from_handoff.py"]},
                    {"name": "Render selected sample HFSS payload geometry views", "command": ["python", "render_hfss_model_views_from_payload.py"]},
                    {
                        "name": "Prepare post-HFSS EMX/HFSS S8P physical validation gate",
                        "command": [
                            "python",
                            "run_s8p_hfss_postrun_validation_from_aedt_packet.py",
                            "--out-dir",
                            "dataset_quality_gates_s8p_physical_feature/selected_s8p_hfss_postrun_validation",
                            "--max-percent-error",
                            "5.0",
                            "--no-fail-exit",
                        ],
                    },
                    {
                        "name": "Build final S8P report evidence packet",
                        "command": [
                            "python",
                            "build_s8p_final_report_evidence_packet.py",
                            "--quality-dir",
                            "dataset_quality_gates_s8p_physical_feature",
                            "--out-dir",
                            "dataset_quality_gates_s8p_physical_feature/s8p_final_report_evidence_packet",
                            "--max-percent-error",
                            "5.0",
                            "--target-ghz",
                            "15.0",
                            "--no-fail-exit",
                        ],
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_inverse_quality_outputs(packet_dir: Path) -> tuple[Path, Path]:
    inverse_root = packet_dir / "inverse_quality_gates"
    training_manifest = inverse_root / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json"
    prediction_summary = inverse_root / "physical_feature_inverse_geometry_prediction" / "physical_feature_inverse_prediction_summary.json"
    post_emx_training_manifest = (
        packet_dir
        / "new_s8p_physical_feature_emx_500"
        / "dataset_quality_gates_s8p_physical_feature"
        / "physical_feature_inverse_training_table"
        / "physical_feature_inverse_training_manifest.json"
    )
    post_emx_quality_summary = (
        packet_dir
        / "new_s8p_physical_feature_emx_500"
        / "dataset_quality_gates_s8p_physical_feature"
        / "physical_feature_inverse_model_quality"
        / "physical_feature_inverse_model_quality_summary.json"
    )
    post_emx_saved_model_dir = (
        packet_dir
        / "new_s8p_physical_feature_emx_500"
        / "dataset_quality_gates_s8p_physical_feature"
        / "physical_feature_saved_inverse_model"
    )
    post_emx_saved_model_json = post_emx_saved_model_dir / "physical_feature_inverse_model.json"
    post_emx_saved_model_summary = post_emx_saved_model_dir / "physical_feature_inverse_model_training_summary.json"
    field_order = [
        "primary_outer_width_um",
        "primary_outer_height_um",
        "secondary_outer_width_um",
        "secondary_outer_height_um",
        "primary_width_um",
        "secondary_width_um",
        "primary_terminal_y_span_um",
        "secondary_terminal_y_span_um",
        "offset_um",
        "primary_feed_extension_um",
        "secondary_feed_extension_um",
    ]
    geometry_columns = [f"geom__{name}" for name in field_order]
    training_manifest_data = {
        "overall_status": "PASS",
        "training_count": 500,
        "input_feature_contract": {
            "zin_columns": [],
            "lp_columns": ["lp_nh_center"],
            "ls_columns": ["ls_nh_center"],
            "q_columns": ["q_center"],
            "k_columns": ["k_center"],
        },
        "geometry_contract": {
            "source": "config_adapter_field_order",
            "field_order": field_order,
            "geometry_columns": geometry_columns,
        },
        "geometry_columns": geometry_columns,
    }
    _write_json(training_manifest, training_manifest_data)
    _write_json(post_emx_training_manifest, training_manifest_data)
    _write_json(
        post_emx_quality_summary,
        {
            "overall_status": "PASS",
            "training_count": 500,
            "input_feature_contract": {
                "zin_columns": [],
                "lp_columns": ["input__lp_nh_center"],
                "ls_columns": ["input__ls_nh_center"],
                "q_columns": ["input__q_center"],
                "k_columns": ["input__k_center"],
            },
            "quality_summary": {
                "method": "leave_one_out_knn_idw",
                "training_count": 500,
                "per_geometry": {
                    "geom__primary_outer_width_um": {
                        "mae": 0.01,
                        "rmse": 0.02,
                        "max_abs_error": 0.04,
                        "normalized_mae": 0.01,
                        "normalized_rmse": 0.02,
                        "normalized_max_abs_error": 0.04,
                    }
                },
                "max_normalized_mae": 0.01,
                "max_normalized_rmse": 0.02,
                "max_normalized_max_abs_error": 0.04,
            },
        },
    )
    _write_json(
        post_emx_saved_model_json,
        {
            "method": "standardized_polynomial_ridge_regression",
            "input_columns": ["input__lp_nh_center", "input__ls_nh_center", "input__q_center", "input__k_center"],
            "geometry_columns": geometry_columns,
            "coefficients": [[1.0]],
            "terms": [{"name": "constant", "powers": [0, 0, 0, 0]}],
        },
    )
    _write_json(
        post_emx_saved_model_summary,
        {
            "overall_status": "PASS",
            "training_count": 500,
            "model_json": str(post_emx_saved_model_json),
            "method": "standardized_polynomial_ridge_regression",
            "input_feature_contract": {
                "zin_columns": [],
                "lp_columns": ["input__lp_nh_center"],
                "ls_columns": ["input__ls_nh_center"],
                "q_columns": ["input__q_center"],
                "k_columns": ["input__k_center"],
            },
            "quality_summary": {
                "method": "leave_one_out_polynomial_ridge",
                "training_count": 500,
                "per_geometry": {
                    "geom__primary_outer_width_um": {
                        "mae": 0.01,
                        "rmse": 0.02,
                        "max_abs_error": 0.04,
                        "normalized_mae": 0.01,
                        "normalized_rmse": 0.02,
                        "normalized_max_abs_error": 0.04,
                    }
                },
                "max_normalized_mae": 0.01,
                "max_normalized_rmse": 0.02,
                "max_normalized_max_abs_error": 0.04,
            },
        },
    )
    _write_json(
        prediction_summary,
        {
            "overall_status": "PASS",
            "candidate_count": 500,
            "candidate_geometry_contract": {
                "config_exists": True,
                "candidate_count": 500,
                "valid_candidate_count": 500,
                "field_order": field_order,
                "expected_geometry_columns": geometry_columns,
                "missing_field_rows": [],
                "invalid_candidate_rows": [],
            },
            "checks": [
                {"name": "inverse_geometry_candidate_fields_match_config", "pass": True},
                {"name": "inverse_geometry_candidates_rebuild_from_config", "pass": True},
            ],
        },
    )
    return training_manifest, prediction_summary


def _write_combined_approval_summary(path: Path, *, approved: bool = False, board: bool = True) -> None:
    board_path = path.parent / "s8p_combined_approval_readiness_board.png"
    if board:
        board_path.parent.mkdir(parents=True, exist_ok=True)
        board_path.write_bytes(b"review-board-placeholder")
    _write_json(
        path,
        {
            "schema": "rfic_transformer_s8p_combined_approval_readiness.v1",
            "overall_status": "PASS",
            "decision": "READY_TO_START_REAL_EMX" if approved else "AWAITING_USER_ADVISOR_APPROVALS",
            "can_start_real_emx": bool(approved),
            "approval_state": {
                "port_map_approved": bool(approved),
                "geometry_contract_approved": bool(approved),
                "mars_execution_packet_ready": bool(approved),
            },
            "artifacts": {"approval_board_png": str(board_path)},
            "visual_artifacts": {"approval_board": str(board_path)},
        },
    )


def _write_candidate_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "parallel_candidate_queue_dataset_summary.json").write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "jobs_requested": 8,
                "expected_jobs": 8,
                "expected_count": 500,
                "merged_row_count": 500,
            }
        ),
        encoding="utf-8",
    )
    with (run_dir / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["evaluation", "ok", "touchstone_path"])
        writer.writeheader()
        for idx in range(500):
            writer.writerow({"evaluation": f"eval_{idx:03d}", "ok": "true", "touchstone_path": f"evaluations/eval_{idx:03d}/emx/emx.s8p"})


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _passing_postrun_checks() -> list[dict[str, str]]:
    names = [
        "ADS-style EMX physical plot exists",
        "ADS-style HFSS physical plot exists",
        "ADS-style EMX/HFSS overlay plot exists",
        "ADS-style metric CSV exists",
        "ADS-style EMX plot source is 8-port",
        "ADS-style HFSS plot source is 8-port",
        "ADS-style EMX/HFSS plot port pairs match",
        "formula trace contains port_pair_syntax",
        "formula trace contains differential_transform",
        "formula trace contains lp_formula",
        "formula trace contains ls_formula",
        "formula trace contains m_formula",
        "formula trace contains qp_formula",
        "formula trace contains qs_formula",
        "formula trace contains k_formula",
        "HFSS build port manifest exists",
        "HFSS build port manifest schema",
        "HFSS build port manifest has 8 ports",
        "HFSS build port manifest port order is P001-P008",
        "HFSS build port manifest ground names are P001_G-P008_G",
        "HFSS build port manifest records integration lines",
        "k <= 10% max error",
        "qp <= 10% max error",
        "qs <= 10% max error",
        "lp_nh <= 10% max error",
        "ls_nh <= 10% max error",
    ]
    return [{"status": "PASS", "name": name, "sample": "1", "evaluation": "eval"} for name in names]


class AuditNextGenS8pGoalReadinessScriptTest(TransformerToolboxTestBase):
    def test_missing_external_artifacts_are_not_reported_as_complete(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            status = mod.main(["--out-dir", str(root / "readiness"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            self.assertEqual(summary["decision"], "DO_NOT_CLAIM_NEXT_GEN_S8P_GOAL_COMPLETE")
            self.assertIn("QUESTION", summary["status_counts"])
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["unresolved question: scalar Q definition"]["status"], "QUESTION")

    def test_valid_config_and_launch_packet_wait_for_real_mars_hfss_outputs(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "launch_summary.json"
            combined = root / "combined" / "s8p_combined_approval_readiness_summary.json"
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=True)
            _write_combined_approval_summary(combined, approved=False)

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--combined-approval-readiness-summary",
                    str(combined),
                    "--launch-packet-summary",
                    str(launch),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_MARS_OR_HFSS")
            self.assertEqual(summary["decision"], "CONTINUE_EXTERNAL_EMX_HFSS_RUNS")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["single-Q definition is explicit for Lp/Ls/Q/K input"]["status"], "PASS")
            self.assertEqual(requirements["inverse-design input does not use Zin columns"]["status"], "PASS")
            self.assertEqual(requirements["ground frame width derives to expected rectangular shield frame"]["status"], "PASS")
            self.assertEqual(requirements["ground frame policy is rectangular shield frame"]["status"], "PASS")
            self.assertEqual(requirements["combined port-map and geometry approval readiness is recorded"]["status"], "PASS")
            self.assertEqual(requirements["combined approval gate allows real EMX start"]["status"], "WAITING")
            self.assertEqual(requirements["combined approval board PNG exists for advisor review"]["status"], "PASS")
            self.assertEqual(requirements["launch packet explicitly derives scalar Q before inverse training"]["status"], "PASS")
            self.assertEqual(requirements["launch packet parallel EMX contract is 500 samples with 8 workers"]["status"], "PASS")
            self.assertEqual(requirements["launch packet can resume completed EMX shards"]["status"], "PASS")
            self.assertEqual(requirements["launch packet audits rectangular ground frame before EMX"]["status"], "PASS")
            self.assertEqual(requirements["launch packet explicitly gates winding 135deg and terminal 90deg geometry"]["status"], "PASS")
            self.assertEqual(requirements["launch packet requires expected 8-worker audit"]["status"], "PASS")
            self.assertEqual(requirements["launch packet requires expected 500-row merge audit"]["status"], "PASS")
            self.assertEqual(requirements["launch packet validates inverse geometry candidates with config"]["status"], "PASS")
            self.assertEqual(requirements["launch packet builds post-EMX Lp/Ls/Q/K inverse training table"]["status"], "PASS")
            self.assertEqual(requirements["launch packet audits post-EMX inverse model quality"]["status"], "PASS")
            self.assertEqual(requirements["launch packet trains saved post-EMX Lp/Ls/Q/K inverse model artifact"]["status"], "PASS")
            self.assertEqual(requirements["launch packet saves target predictions from Lp/Ls/Q/K inverse model"]["status"], "PASS")
            self.assertEqual(
                requirements[
                    "launch packet keeps saved-model target layout smoke conditional on explicit Lp/Ls/Q/K targets"
                ]["status"],
                "PASS",
            )
            self.assertEqual(requirements["launch packet renders HFSS payload geometry views"]["status"], "PASS")
            self.assertEqual(requirements["launch packet builds final report evidence packet after EMX/HFSS validation"]["status"], "PASS")
            self.assertEqual(requirements["physical-feature inverse candidates rebuild into config geometry"]["status"], "WAITING")
            self.assertEqual(requirements["post-EMX inverse model quality audit passes"]["status"], "WAITING")
            self.assertEqual(requirements["post-EMX saved Lp/Ls/Q/K inverse model artifact is trained"]["status"], "WAITING")
            self.assertEqual(requirements["500 new S8P EMX samples are generated with 8 workers"]["status"], "WAITING")

    def test_unapproved_launch_packet_is_waiting_not_failed_when_only_approval_gate_blocks(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "launch_summary.json"
            combined = root / "combined" / "s8p_combined_approval_readiness_summary.json"
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=True)
            launch_data = json.loads(launch.read_text(encoding="utf-8"))
            launch_data["overall_status"] = "NOT_READY"
            launch_data["decision"] = "DO_NOT_RUN_UNTIL_CHECKS_PASS"
            launch_data["checks"] = [
                {"name": "config_loads", "pass": True, "detail": "ok"},
                {
                    "name": "port_map_approval_summary_approved",
                    "pass": False,
                    "detail": "candidate summary awaits user/advisor approval",
                },
            ]
            launch.write_text(json.dumps(launch_data, indent=2), encoding="utf-8")
            _write_combined_approval_summary(combined, approved=False)

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--combined-approval-readiness-summary",
                    str(combined),
                    "--launch-packet-summary",
                    str(launch),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_MARS_OR_HFSS")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["physical-feature inverse launch packet is ready"]["status"], "WAITING")
            self.assertIn("port_map_approval_summary_approved", requirements["physical-feature inverse launch packet is ready"]["evidence"])

    def test_bootstrap_launch_packet_is_not_failed_for_missing_inverse_geometry_config(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "bootstrap_launch_summary.json"
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=True, bootstrap_mode=True)

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--launch-packet-summary",
                    str(launch),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(
                requirements["launch packet uses config-backed bootstrap geometry candidate queue"]["status"],
                "PASS",
            )
            self.assertNotIn("launch packet validates inverse geometry candidates with config", requirements)

    def test_full_supplied_evidence_passes_goal_readiness_gate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "launch_summary.json"
            combined = root / "combined" / "s8p_combined_approval_readiness_summary.json"
            run_dir = root / "candidate_run"
            quality = run_dir / "dataset_quality_gates_s8p_physical_feature" / "dataset_quality_gates_summary.json"
            port_pair_audit = (
                run_dir
                / "dataset_quality_gates_s8p_physical_feature"
                / "selected_s8p_port_pair_physical_candidate_audit"
                / "s8p_port_pair_physical_candidate_audit_summary.json"
            )
            handoff = root / "handoff" / "selected_s8p_hfss_handoff_summary.json"
            aedt = root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json"
            render = root / "payload_views" / "hfss_payload_geometry_render_batch_summary.json"
            postrun = root / "postrun" / "s8p_hfss_postrun_validation_summary.json"
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=True)
            _write_combined_approval_summary(combined, approved=True)
            _write_inverse_quality_outputs(launch.parent)
            _write_candidate_run(run_dir)
            _write_json(quality, {"overall_status": "PASS", "steps": [{"name": "s8p physical-feature dataset audit"}]})
            _write_json(
                port_pair_audit,
                {
                    "overall_status": "PASS",
                    "expected_port_pairs": "1,4:5,6",
                    "expected_port_pairs_all_pass": True,
                },
            )
            formula_trace = root / "handoff" / "hfss_ads_formula_trace.md"
            formula_trace.parent.mkdir(parents=True, exist_ok=True)
            formula_trace.write_text("Lp = imag(Zdiff[1,1]) / omega\n", encoding="utf-8")
            _write_json(handoff, {"overall_status": "PASS", "sample_count": 1, "artifacts": {"ads_formula_trace": str(formula_trace)}})
            _write_json(aedt, {"overall_status": "PASS", "sample_count": 1})
            sample_render = root / "payload_views" / "01_eval" / "hfss_payload_geometry_render_summary.json"
            _write_json(sample_render, {"overall_status": "PASS"})
            _write_json(render, {"overall_status": "PASS", "rendered_count": 1, "summary_paths": [str(sample_render)]})
            _write_json(postrun, {"overall_status": "PASS", "status_counts": {"PASS": 1}, "checks": _passing_postrun_checks()})

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--combined-approval-readiness-summary",
                    str(combined),
                    "--launch-packet-summary",
                    str(launch),
                    "--candidate-run-dir",
                    str(run_dir),
                    "--selected-handoff-summary",
                    str(handoff),
                    "--aedt-packet-summary",
                    str(aedt),
                    "--hfss-payload-render-summary",
                    str(render),
                    "--postrun-validation-summary",
                    str(postrun),
                    "--out-dir",
                    str(root / "readiness"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(
                requirements["combined approval gate allows real EMX start"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["physical-feature inverse candidates rebuild into config geometry"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["post-EMX inverse model quality audit passes"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["inverse model quality audit input contract is Lp/Ls/Q/K without Zin"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["inverse model quality audit reports leave-one-out geometry error metrics"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["post-EMX saved Lp/Ls/Q/K inverse model artifact is trained"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["saved inverse model input contract is Lp/Ls/Q/K without Zin"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["saved inverse model reports reproducible geometry-error metrics"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["selected S8P sample has candidate port-pair physical-feature diagnostic"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["postrun validation generated EMX, HFSS, and overlay physical-feature figures"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["postrun validation proves port-pair formula trace consistency"]["status"],
                "PASS",
            )
            self.assertEqual(
                requirements["postrun validation proves Lp/Ls/Q/K/Kw metrics within 10 percent"]["status"],
                "PASS",
            )

    def test_port_pair_candidate_review_is_a_question(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "launch_summary.json"
            run_dir = root / "candidate_run"
            port_pair_audit = (
                run_dir
                / "dataset_quality_gates_s8p_physical_feature"
                / "selected_s8p_port_pair_physical_candidate_audit"
                / "s8p_port_pair_physical_candidate_audit_summary.json"
            )
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=True)
            _write_candidate_run(run_dir)
            _write_json(
                port_pair_audit,
                {
                    "overall_status": "REVIEW",
                    "expected_port_pairs": "1,4:5,6",
                    "expected_port_pairs_all_pass": False,
                },
            )

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--launch-packet-summary",
                    str(launch),
                    "--candidate-run-dir",
                    str(run_dir),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(
                requirements["selected S8P sample has candidate port-pair physical-feature diagnostic"]["status"],
                "QUESTION",
            )

    def test_postrun_overall_pass_without_required_figures_is_not_ready(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "launch_summary.json"
            run_dir = root / "candidate_run"
            quality = run_dir / "dataset_quality_gates_s8p_physical_feature" / "dataset_quality_gates_summary.json"
            handoff = root / "handoff" / "selected_s8p_hfss_handoff_summary.json"
            aedt = root / "aedt" / "hfss_s8p_aedt_script_packet_summary.json"
            render = root / "payload_views" / "hfss_payload_geometry_render_batch_summary.json"
            postrun = root / "postrun" / "s8p_hfss_postrun_validation_summary.json"
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=True)
            _write_candidate_run(run_dir)
            _write_json(quality, {"overall_status": "PASS", "steps": [{"name": "s8p physical-feature dataset audit"}]})
            formula_trace = root / "handoff" / "hfss_ads_formula_trace.md"
            formula_trace.parent.mkdir(parents=True, exist_ok=True)
            formula_trace.write_text("Lp = imag(Zdiff[1,1]) / omega\n", encoding="utf-8")
            _write_json(handoff, {"overall_status": "PASS", "sample_count": 1, "artifacts": {"ads_formula_trace": str(formula_trace)}})
            _write_json(aedt, {"overall_status": "PASS", "sample_count": 1})
            sample_render = root / "payload_views" / "01_eval" / "hfss_payload_geometry_render_summary.json"
            _write_json(sample_render, {"overall_status": "PASS"})
            _write_json(render, {"overall_status": "PASS", "rendered_count": 1, "summary_paths": [str(sample_render)]})
            _write_json(postrun, {"overall_status": "PASS", "status_counts": {"PASS": 1}, "checks": []})

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--launch-packet-summary",
                    str(launch),
                    "--candidate-run-dir",
                    str(run_dir),
                    "--dataset-quality-summary",
                    str(quality),
                    "--selected-handoff-summary",
                    str(handoff),
                    "--aedt-packet-summary",
                    str(aedt),
                    "--hfss-payload-render-summary",
                    str(render),
                    "--postrun-validation-summary",
                    str(postrun),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(
                requirements["postrun validation generated EMX, HFSS, and overlay physical-feature figures"]["status"],
                "FAIL",
            )

    def test_handoff_without_formula_trace_is_not_ready(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "launch_summary.json"
            handoff = root / "handoff" / "selected_s8p_hfss_handoff_summary.json"
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=True)
            _write_json(handoff, {"overall_status": "PASS", "sample_count": 1, "artifacts": {"ads_formula_trace": str(root / "missing.md")}})

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--launch-packet-summary",
                    str(launch),
                    "--selected-handoff-summary",
                    str(handoff),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["HFSS handoff includes ADS/Python formula trace"]["status"], "FAIL")

    def test_qp_qs_launch_packet_is_a_question_when_single_q_is_required(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "launch_summary.json"
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=False)

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--launch-packet-summary",
                    str(launch),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["single-Q definition is explicit for Lp/Ls/Q/K input"]["status"], "QUESTION")
            self.assertEqual(requirements["launch packet explicitly derives scalar Q before inverse training"]["status"], "FAIL")
            self.assertEqual(requirements["unresolved question: scalar Q definition"]["status"], "QUESTION")

    def test_launch_packet_without_layout_smoke_gate_is_not_ready(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "launch_summary.json"
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=True, include_smoke=False)

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--launch-packet-summary",
                    str(launch),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["launch packet includes one-sample create-only layout smoke"]["status"], "FAIL")
            self.assertEqual(requirements["launch packet audits smoke 8-port layout before EMX"]["status"], "FAIL")

    def test_launch_packet_with_inverse_targets_must_rebuild_saved_model_prediction_geometry(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "launch_summary.json"
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=True, with_targets=True)

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--launch-packet-summary",
                    str(launch),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(requirements["launch packet saves target predictions from Lp/Ls/Q/K inverse model"]["status"], "PASS")
            self.assertEqual(
                requirements["launch packet validates saved-model target geometry with create-only layout smoke"]["status"],
                "PASS",
            )

    def test_launch_packet_with_inverse_targets_without_target_smoke_is_not_ready(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            launch = root / "launch_summary.json"
            _write_valid_s8p_config(config)
            _write_launch_summary(launch, scalar_q=True, with_targets=True, include_target_smoke=False)

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--launch-packet-summary",
                    str(launch),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "next_gen_s8p_goal_readiness_summary.json").read_text(encoding="utf-8"))
            requirements = {item["requirement"]: item for item in summary["evidence"]}
            self.assertEqual(
                requirements["launch packet validates saved-model target geometry with create-only layout smoke"]["status"],
                "FAIL",
            )
