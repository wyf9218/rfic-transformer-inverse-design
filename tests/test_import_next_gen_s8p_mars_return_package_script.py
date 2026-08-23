from tests.rfic_transformer_inverse_design.shared import *
from tests.rfic_transformer_inverse_design.shared import _write_touchstone

import csv
import importlib.util
import os
import subprocess
import sys


def _load_script(name: str, rel: str):
    script_path = Path(__file__).resolve().parents[1] / rel
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_import_module():
    return _load_script("import_next_gen_s8p_mars_return_package_script", "scripts/import_next_gen_s8p_mars_return_package.py")


def _load_package_module():
    return _load_script("package_mars_dataset_run_script_for_import_test", "scripts/package_mars_dataset_run.py")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_next_gen_run(root: Path, *, count: int = 2, include_objective: bool = True) -> Path:
    run = root / "runs" / "new_s8p_physical_feature_emx_500"
    run.mkdir(parents=True, exist_ok=True)
    (run / "final_s8p_physical_feature_500.yaml").write_text("dataset:\n  count: 500\n", encoding="utf-8")
    _write_json(
        run / "dataset_manifest.json",
        {
            "requested_count": count,
            "ok_count": count,
            "fail_count": 0,
            "port_mode": "single_ended_shield_grounded",
            "cadence_pin_purpose": 51,
            "differential_port_pairs": [[1, 4], [5, 6]],
            "power_line_8port": {
                "enabled": True,
                "bridge_width_um": 10.0,
                "vertical_length_diameter_ratio": 1.5,
                "port_map": ["P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"],
                "ground_frame_width_um": 100.0,
                "ground_frame_policy": "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
            },
            "target_frequency": {
                "start_hz": 5.0e9,
                "stop_hz": 5.2e9,
                "step_hz": 1.0e8,
                "points": 3,
            },
        },
    )
    _write_json(
        run / "parallel_candidate_queue_dataset_summary.json",
        {
            "overall_status": "PASS",
            "jobs_requested": 2,
            "merged_row_count": count,
            "checks": [
                {"name": "requested_jobs_match_expected", "pass": True},
                {"name": "merged_count_matches_expected", "pass": True},
            ],
        },
    )
    freqs = np.asarray([5.0e9, 5.1e9, 5.2e9])
    with (run / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", "ok", "touchstone_path"])
        writer.writeheader()
        for idx in range(count):
            key = f"eval_{idx:03d}"
            sample = run / "evaluations" / key
            (sample / "emx").mkdir(parents=True)
            (sample / "layout").mkdir()
            s_matrix = np.zeros((3, 8, 8), dtype=np.complex128)
            for port in range(8):
                s_matrix[:, port, port] = 0.05 + 0.01j
            s_matrix[:, 0, 4] = 0.02j
            s_matrix[:, 4, 0] = 0.02j
            _write_touchstone(sample / "emx" / "emx.s8p", freqs, s_matrix)
            _write_json(sample / "summary.json", {"ok": True})
            (sample / "layout" / "transformer_layout.layout.json").write_text("{}", encoding="utf-8")
            _write_json(sample / "emx" / "emx_command.json", _valid_emx_command(freqs))
            writer.writerow({"sample_id": key, "ok": "true", "touchstone_path": f"evaluations/{key}/emx/emx.s8p"})
    _write_s8p_quality_gates(run)
    _write_next_gen_status(run, include_objective=include_objective)
    return run


def _valid_emx_command(freqs: np.ndarray) -> list[str]:
    command = [
        "emx",
        "layout.gds",
        "TRANSFORMER",
        "proc.proc",
        "--touchstone",
        "--s-impedance=50",
        "-s",
        "emx.s8p",
        "--include-command-line",
        "--cadence-pins=51",
    ]
    for idx in range(8):
        command.append(f"--port=P{idx + 1:03d}=P{idx + 1:03d}:GND")
    command.extend(str(float(freq)) for freq in freqs)
    return command


def _write_s8p_quality_gates(run: Path) -> None:
    out_dir = run / "dataset_quality_gates_s8p_physical_feature"
    _write_json(
        out_dir / "dataset_quality_gates_summary.json",
        {
            "overall_status": "PASS",
            "steps": [
                {"name": "S8P physical-feature dataset audit", "status": "PASS"},
                {"name": "scalar Q feature derivation", "status": "PASS"},
                {"name": "physical-feature validation sample selection", "status": "PASS"},
            ],
        },
    )
    _write_json(out_dir / "s8p_physical_feature_dataset_audit" / "s8p_physical_feature_dataset_audit_summary.json", {"overall_status": "PASS"})
    _write_json(out_dir / "scalar_q_feature_dataset" / "scalar_q_feature_summary.json", {"overall_status": "PASS"})
    validation = out_dir / "physical_feature_validation_sample_selection"
    _write_json(validation / "physical_feature_validation_sample_summary.json", {"overall_status": "PASS", "selected_count": 1})
    (validation / "physical_feature_validation_samples.csv").write_text(
        "evaluation,selection_rank,touchstone_path\neval_000,1,evaluations/eval_000/emx/emx.s8p\n",
        encoding="utf-8",
    )


def _write_next_gen_status(run: Path, *, include_objective: bool) -> None:
    status_dir = run / "next_gen_s8p_mars_run_status"
    _write_json(
        status_dir / "next_gen_s8p_mars_run_status_summary.json",
        {"overall_status": "EMX_DATASET_READY_FOR_HFSS_HANDOFF", "decision": "BUILD_SELECTED_SAMPLE_HFSS_HANDOFF"},
    )
    (status_dir / "next_gen_s8p_mars_run_status_report.md").write_text("# run status\n", encoding="utf-8")
    (status_dir / "next_gen_s8p_mars_run_status_evidence.csv").write_text("status\nPASS\n", encoding="utf-8")
    if include_objective:
        objective_dir = run / "next_gen_s8p_objective_acceptance"
        _write_json(
            objective_dir / "next_gen_s8p_objective_acceptance_summary.json",
            {"overall_status": "WAITING", "decision": "DO_NOT_CLAIM_NEXT_GEN_S8P_OBJECTIVE_COMPLETE"},
        )
        (objective_dir / "NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md").write_text("# objective\n", encoding="utf-8")
        (objective_dir / "next_gen_s8p_objective_acceptance_evidence.csv").write_text("status\nWAITING\n", encoding="utf-8")


class ImportNextGenS8pMarsReturnPackageScriptTest(TransformerToolboxTestBase):
    def test_imports_verified_return_package_and_runs_strict_discovery(self) -> None:
        importer = _load_import_module()
        packager = _load_package_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_next_gen_run(root)
            tarball = root / "next_gen_s8p_mars_return_latest.tar.gz"
            self.assertEqual(packager.main([str(run), "--out", str(tarball)]), 0)

            status = importer.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "import"),
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                    "--max-touchstone-checks",
                    "0",
                    "--max-touchstone-frequency-checks",
                    "3",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "import" / "next_gen_s8p_mars_return_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "READY_FOR_LOCAL_NEXT_GATES")
            self.assertEqual(summary["decision"], "CONTINUE_LOCAL_S8P_HFSS_AND_REPORT_GATES")
            self.assertTrue(summary["verifier_result"]["accepted_for_import"])
            self.assertEqual(summary["discovery_result"]["summary"]["overall_status"], "READY_FOR_NEXT_GATES")
            self.assertTrue((root / "import" / "extracted" / run.name / "dataset_rows.csv").is_file())
            next_steps = summary["next_steps_result"]
            self.assertTrue(next_steps["generated"])
            next_steps_script = Path(next_steps["script_path"])
            next_steps_report = Path(next_steps["report_path"])
            self.assertTrue(next_steps_script.is_file())
            self.assertTrue(next_steps_report.is_file())
            self.assertTrue(os.access(next_steps_script, os.X_OK))
            shell_check = subprocess.run(["bash", "-n", str(next_steps_script)], check=False, capture_output=True, text=True)
            self.assertEqual(shell_check.returncode, 0, shell_check.stderr)
            script_text = next_steps_script.read_text(encoding="utf-8")
            self.assertIn("REPO_ROOT=${REPO_ROOT:-", script_text)
            self.assertNotIn("REPO_ROOT=\"${REPO_ROOT:-'", script_text)
            for token in (
                "02_final_s8p_config/final_s8p_physical_feature_500.yaml",
                "[1/16] Plan Lp/Ls/Q/K response-space coverage",
                "[2/16] Build post-EMX Lp/Ls/Q/K inverse training table",
                "[3/16] Audit Lp/Ls/Q/K inverse-model quality",
                "[4/16] Train saved baseline Lp/Ls/Q/K-to-geometry inverse model",
                "[5/16] Write editable target Lp/Ls/Q/K JSON template from trained envelope",
                "[7/16] Discover final-valid real EMX S8P candidates before HFSS handoff",
                "[16/16] Optional objective-level audit if launch/approval summaries are provided",
                "build_physical_feature_inverse_training_table.py",
                "train_physical_feature_inverse_model.py",
                "TARGET_JSON",
                "TARGET_TEMPLATE_JSON",
                "target_lp_ls_q_k_template.json",
                "_feature_envelope",
                "target template refused: saved inverse model contains Zin inputs",
                "target template refused: saved inverse model is missing required Lp/Ls/Q/K inputs",
                'required_features = {"lp_nh_center", "ls_nh_center", "q_center", "k_center"}',
                "physical_feature_inverse_model_target_predictions.csv",
                "physical_feature_saved_inverse_target_layout_smoke",
                "run_candidate_queue_dataset.py",
                "--create-only",
                "discover_final_valid_emx_s8p_candidates.py",
                "No final-valid real EMX .s8p candidate found; do not build HFSS handoff from this return.",
                "FINAL_VALID_SAMPLES_DIR",
                "export_final_valid_emx_s8p_samples.py",
                "--discovery-summary \"$FINAL_VALID_DISCOVERY_DIR/final_valid_emx_s8p_candidate_discovery_summary.json\"",
                "SAMPLES_CSV=\"$FINAL_VALID_SAMPLES_DIR/physical_feature_validation_samples.csv\"",
                "build_selected_s8p_hfss_handoff_packet.py",
                "build_s8p_hfss_aedt_scripts_from_handoff.py",
                "render_hfss_model_views_from_payload.py",
                "run_s8p_hfss_postrun_validation_from_aedt_packet.py",
                "build_s8p_final_report_evidence_packet.py",
                "summarize_next_gen_s8p_mars_run.py",
                "--max-touchstone-checks 0",
                str(root / "import" / "extracted" / run.name),
            ):
                self.assertIn(token, script_text)
            self.assertNotIn("--expected-power-line-bridge-width-um 10", script_text)
            self.assertNotIn("--expected-bridge-width-um 10", script_text)
            ordered_tokens = [f'echo "[{idx}/16]' for idx in range(1, 17)]
            positions = [script_text.index(token) for token in ordered_tokens]
            self.assertEqual(positions, sorted(positions))
            report_text = next_steps_report.read_text(encoding="utf-8")
            self.assertIn("TARGET_JSON=/path/to/target_lp_ls_q_k.json", report_text)
            self.assertIn("target_lp_ls_q_k_template.json", report_text)

    def test_rejects_return_package_without_objective_acceptance_evidence(self) -> None:
        importer = _load_import_module()
        packager = _load_package_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = _write_next_gen_run(root, include_objective=False)
            tarball = root / "next_gen_s8p_mars_return_latest.tar.gz"
            self.assertEqual(packager.main([str(run), "--out", str(tarball)]), 0)

            status = importer.main(
                [
                    str(tarball),
                    "--out-dir",
                    str(root / "import"),
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-points",
                    "3",
                    "--max-touchstone-checks",
                    "0",
                    "--max-touchstone-frequency-checks",
                    "3",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "import" / "next_gen_s8p_mars_return_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertFalse(summary["verifier_result"]["accepted_for_import"])
            self.assertFalse(summary["next_steps_result"]["generated"])
            verifier_summary = summary["verifier_result"]["summary"]
            checks = {item["name"]: item for item in verifier_summary["checks"]}
            self.assertEqual(checks["packaged next-gen S8P status evidence"]["status"], "FAIL")
