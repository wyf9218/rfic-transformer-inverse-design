from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_quality_gates_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_dataset_quality_gates.py"
    spec = importlib.util.spec_from_file_location("run_dataset_quality_gates_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_geometry_manifest(root: Path) -> None:
    angle_block = {"min": {"min": 135.0, "max": 135.0}, "max": {"min": 135.0, "max": 135.0}}
    terminal_block = {"min": {"min": 90.0, "max": 90.0}, "max": {"min": 90.0, "max": 90.0}}
    manifest = {
        "requested_count": 1,
        "ok_count": 1,
        "fail_count": 0,
        "port_mode": "single_ended_shield_grounded",
        "cadence_pin_purpose": 51,
        "shield_enabled": True,
        "uniformity": {"count": 1, "bins": 1, "fields": {"primary_outer_width_um": {"histogram_min": 1, "histogram_max": 1}}},
        "geometry_quality": {
            "geometry_check_count": 1,
            "geometry_check_ok_count": 1,
            "angle_checked_count": 1,
            "primary_internal_angle_deg": angle_block,
            "secondary_internal_angle_deg": angle_block,
            "primary_terminal_interface_angle_deg": terminal_block,
            "secondary_terminal_interface_angle_deg": terminal_block,
        },
    }
    (root / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "dataset_rows.csv").write_text("ok,touchstone_path\ntrue,\n", encoding="utf-8")


def _write_clearance_audit(root: Path) -> None:
    clearance = {
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
    (root / "final500_ground_clearance_audit.json").write_text(json.dumps(clearance), encoding="utf-8")


def _write_synthetic_transformer_s4p(path: Path, freqs_hz: np.ndarray) -> None:
    target = default_target_spec()
    diff = build_lumped_transformer_sparameters(freqs_hz=freqs_hz, target=target, q_primary=18.0, q_secondary=16.0)
    single = differential_2port_to_4port_s(
        freqs_hz=freqs_hz,
        s_diff=diff.s_matrix,
        diff_z0_ohm=target.differential_reference_impedance_ohm,
        single_z0_ohm=50.0,
    )
    _write_touchstone(path, single.freqs_hz, single.s_matrix)


class RunDatasetQualityGatesScriptTest(TransformerToolboxTestBase):
    def test_quality_gates_can_run_geometry_only_pass(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_geometry_manifest(root)

            status = gates.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-touchstone-audit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "quality" / "dataset_quality_gates_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual([step["name"] for step in summary["steps"]], ["geometry quality audit"])
            self.assertTrue((root / "quality" / "geometry_quality_audit" / "geometry_quality_audit_summary.json").exists())

    def test_quality_gates_forwards_clearance_audit_requirements(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "zin_target_envelope.json"
            args = gates._parse_args(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-touchstone-audit",
                    "--require-clearance-audit",
                    "--min-clearance-pass-fraction",
                    "0.9",
                    "--max-clearance-overlap-area-um2",
                    "0.001",
                    "--max-clearance-violation-area-um2",
                    "0.001",
                ]
            )

            steps = gates._build_steps(root, root / "quality", args)

            self.assertEqual(steps[0].name, "geometry quality audit")
            command = " ".join(steps[0].command)
            self.assertIn("--require-clearance-audit", command)
            self.assertIn("--min-clearance-pass-fraction 0.9", command)
            self.assertIn("--max-clearance-overlap-area-um2 0.001", command)
            self.assertIn("--max-clearance-violation-area-um2 0.001", command)

    def test_quality_gates_fail_when_required_clearance_audit_is_missing(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_geometry_manifest(root)

            status = gates.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-touchstone-audit",
                    "--require-clearance-audit",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "quality" / "dataset_quality_gates_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")

    def test_quality_gates_pass_when_required_clearance_audit_exists(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_geometry_manifest(root)
            _write_clearance_audit(root)

            status = gates.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-touchstone-audit",
                    "--require-clearance-audit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "quality" / "dataset_quality_gates_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")

    def test_quality_gates_can_run_sampling_distribution_audit(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rows = [
                {"ok": "true", "geom__w_um": 0.5 + idx, "geom__h_um": 0.5 + ((idx * 9) % 20)}
                for idx in range(20)
            ]
            (root / "dataset_manifest.json").write_text(
                json.dumps({"bounds": {"w_um": [0, 20], "h_um": [0, 20]}, "requested_count": 20, "ok_count": 20, "fail_count": 0}),
                encoding="utf-8",
            )
            with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ok", "geom__w_um", "geom__h_um"])
                writer.writeheader()
                writer.writerows(rows)

            status = gates.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--audit-sampling-distribution",
                    "--bins",
                    "10",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "quality" / "dataset_quality_gates_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual([step["name"] for step in summary["steps"]], ["sampling distribution audit"])
            command = " ".join(summary["steps"][0]["command"])
            self.assertIn("--require-uniform-closer-than-normal", command)
            self.assertIn("--min-uniform-vs-normal-fields-fraction 1.0", command)
            self.assertIn("--max-min-norm 0.05", command)
            self.assertIn("--min-max-norm 0.95", command)
            self.assertIn("--space-filling-strata 20", command)
            self.assertIn("--max-space-filling-empty-strata-frac 0.0", command)
            self.assertIn("--max-space-filling-duplicate-frac 0.0", command)

    def test_quality_gates_can_plan_zin_balanced_acquisition(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_geometry_manifest(root)
            with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["evaluation", "ok", "zin_center_real_ohm", "zin_center_imag_ohm"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"evaluation": "a", "ok": "true", "zin_center_real_ohm": 5, "zin_center_imag_ohm": -45},
                        {"evaluation": "b", "ok": "true", "zin_center_real_ohm": 10, "zin_center_imag_ohm": -40},
                    ]
                )

            status = gates.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--plan-zin-balanced-acquisition",
                    "--zin-bins",
                    "2",
                    "--zin-target-real-min-ohm",
                    "0",
                    "--zin-target-real-max-ohm",
                    "100",
                    "--zin-target-imag-min-ohm",
                    "-50",
                    "--zin-target-imag-max-ohm",
                    "50",
                    "--zin-plan-desired-total-count",
                    "8",
                    "--zin-plan-next-count",
                    "3",
                    "--zin-plan-no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "quality" / "dataset_quality_gates_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual([step["name"] for step in summary["steps"]], ["Zin balanced acquisition plan"])
            command = " ".join(summary["steps"][0]["command"])
            self.assertIn("--desired-total-count 8", command)
            self.assertIn("--next-count 3", command)
            self.assertTrue((root / "quality" / "zin_balanced_acquisition_plan" / "zin_balanced_acquisition_targets.csv").exists())

    def test_quality_gates_can_select_zin_targeted_candidates_after_plan(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_geometry_manifest(root)
            with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["evaluation", "ok", "zin_center_real_ohm", "zin_center_imag_ohm"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"evaluation": "a", "ok": "true", "zin_center_real_ohm": 5, "zin_center_imag_ohm": -45},
                        {"evaluation": "b", "ok": "true", "zin_center_real_ohm": 10, "zin_center_imag_ohm": -40},
                    ]
                )
            candidate_csv = root / "candidate_predictions.csv"
            with candidate_csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "candidate_id",
                        "pred_zin_center_real_ohm",
                        "pred_zin_center_imag_ohm",
                        "geom__w",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"candidate_id": "c0", "pred_zin_center_real_ohm": 75, "pred_zin_center_imag_ohm": -25, "geom__w": 10},
                        {"candidate_id": "c1", "pred_zin_center_real_ohm": 25, "pred_zin_center_imag_ohm": 25, "geom__w": 11},
                    ]
                )

            status = gates.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--plan-zin-balanced-acquisition",
                    "--select-zin-targeted-candidates",
                    "--zin-candidate-predictions-csv",
                    str(candidate_csv),
                    "--zin-bins",
                    "2",
                    "--zin-target-real-min-ohm",
                    "0",
                    "--zin-target-real-max-ohm",
                    "100",
                    "--zin-target-imag-min-ohm",
                    "-50",
                    "--zin-target-imag-max-ohm",
                    "50",
                    "--zin-plan-desired-total-count",
                    "8",
                    "--zin-plan-next-count",
                    "2",
                    "--zin-plan-no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "quality" / "dataset_quality_gates_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(
                [step["name"] for step in summary["steps"]],
                ["Zin balanced acquisition plan", "Zin-targeted candidate geometry selection"],
            )
            self.assertTrue((root / "quality" / "zin_targeted_candidate_selection" / "zin_targeted_candidate_selection.csv").exists())

    def test_quality_gates_can_build_surrogate_candidates_and_select_targets(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_geometry_manifest(root)
            rows = []
            for idx in range(10):
                rows.append(
                    {
                        "evaluation": f"r{idx}",
                        "ok": "true",
                        "geom__w_um": 1.0 + idx,
                        "geom__s_um": 2.0 + (idx % 3),
                        "zin_center_real_ohm": 10.0 + idx,
                        "zin_center_imag_ohm": -30.0 + 2.0 * idx,
                    }
                )
            with (root / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            status = gates.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--plan-zin-balanced-acquisition",
                    "--build-zin-surrogate-candidates",
                    "--select-zin-targeted-candidates",
                    "--zin-candidate-allow-outside-bin",
                    "--zin-bins",
                    "2",
                    "--zin-target-real-min-ohm",
                    "0",
                    "--zin-target-real-max-ohm",
                    "30",
                    "--zin-target-imag-min-ohm",
                    "-40",
                    "--zin-target-imag-max-ohm",
                    "0",
                    "--zin-plan-desired-total-count",
                    "12",
                    "--zin-plan-next-count",
                    "3",
                    "--zin-plan-no-plots",
                    "--zin-surrogate-candidate-count",
                    "32",
                    "--zin-surrogate-k-neighbors",
                    "3",
                    "--zin-surrogate-no-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "quality" / "dataset_quality_gates_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(
                [step["name"] for step in summary["steps"]],
                [
                    "Zin balanced acquisition plan",
                    "Zin surrogate candidate prediction",
                    "Zin-targeted candidate geometry selection",
                ],
            )
            self.assertTrue((root / "quality" / "zin_surrogate_candidate_predictions" / "candidate_zin_predictions.csv").exists())
            self.assertTrue((root / "quality" / "zin_targeted_candidate_selection" / "zin_targeted_candidate_selection.csv").exists())

    def test_quality_gates_forwards_sampling_distribution_thresholds(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "zin_target_envelope.json"
            args = gates._parse_args(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--audit-sampling-distribution",
                    "--sampling-min-uniform-vs-normal-fields-fraction",
                    "0.8",
                    "--sampling-min-histogram-entropy-frac",
                    "0.85",
                    "--sampling-max-min-norm",
                    "0.1",
                    "--sampling-min-max-norm",
                    "0.9",
                    "--sampling-space-filling-strata",
                    "12",
                    "--sampling-max-space-filling-empty-strata-frac",
                    "0.1",
                    "--sampling-max-space-filling-duplicate-frac",
                    "0.01",
                    "--sampling-min-space-filling-median-nn-distance",
                    "0.05",
                ]
            )

            steps = gates._build_steps(root, root / "quality", args)

            self.assertEqual(steps[0].name, "sampling distribution audit")
            command = " ".join(steps[0].command)
            self.assertIn("--require-uniform-closer-than-normal", command)
            self.assertIn("--min-uniform-vs-normal-fields-fraction 0.8", command)
            self.assertIn("--min-histogram-entropy-frac 0.85", command)
            self.assertIn("--max-min-norm 0.1", command)
            self.assertIn("--min-max-norm 0.9", command)
            self.assertIn("--space-filling-strata 12", command)
            self.assertIn("--max-space-filling-empty-strata-frac 0.1", command)
            self.assertIn("--max-space-filling-duplicate-frac 0.01", command)
            self.assertIn("--min-space-filling-median-nn-distance 0.05", command)

    def test_quality_gates_reports_touchstone_failure(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_geometry_manifest(root)

            status = gates.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "50",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "451",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "quality" / "dataset_quality_gates_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["steps"][0]["name"], "dataset Touchstone preflight")
            self.assertEqual(summary["steps"][0]["status"], "FAIL")

    def test_quality_gates_forwards_touchstone_shape_window(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "response_target_envelopes.json"
            args = gates._parse_args(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--touchstone-shape-window-start-ghz",
                    "5",
                    "--touchstone-shape-window-stop-ghz",
                    "30",
                    "--touchstone-max-shape-spike-ratio",
                    "4",
                    "--touchstone-max-shape-relative-step",
                    "0.25",
                ]
            )

            steps = gates._build_steps(root, root / "quality", args)

            self.assertEqual(steps[0].name, "dataset Touchstone preflight")
            command = " ".join(steps[0].command)
            self.assertIn("--shape-window-start-ghz 5.0", command)
            self.assertIn("--shape-window-stop-ghz 30.0", command)
            self.assertIn("--max-shape-spike-ratio 4.0", command)
            self.assertIn("--max-shape-relative-step 0.25", command)

    def test_quality_gates_can_add_s8p_physical_feature_dataset_audit(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            args = gates._parse_args(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--audit-s8p-physical-feature-dataset",
                    "--s8p-expected-count",
                    "500",
                    "--s8p-expected-ok-count",
                    "500",
                    "--s8p-max-touchstone-checks",
                    "8",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "50",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "451",
                ]
            )

            steps = gates._build_steps(root, root / "quality", args)

            self.assertEqual([step.name for step in steps], ["S8P physical-feature dataset audit"])
            command = " ".join(steps[0].command)
            self.assertIn("audit_s8p_physical_feature_dataset.py", command)
            self.assertIn("--expected-count 500", command)
            self.assertIn("--expected-ok-count 500", command)
            self.assertIn("--max-touchstone-checks 8", command)
            self.assertIn("--expected-frequency-points 451", command)
            self.assertIn("--require-power-line-8port", command)

    def test_s8p_physical_feature_audit_uses_scalar_q_dataset_when_derived(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            args = gates._parse_args(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--derive-scalar-q-feature",
                    "--scalar-q-definition",
                    "min",
                    "--physical-feature-columns",
                    "lp_nh_center,ls_nh_center,q_center,k_center",
                    "--audit-s8p-physical-feature-dataset",
                    "--s8p-expected-count",
                    "500",
                    "--s8p-expected-ok-count",
                    "500",
                ]
            )

            steps = gates._build_steps(root, root / "quality", args)

            self.assertEqual(
                [step.name for step in steps],
                [
                    "scalar Q feature derivation",
                    "S8P physical-feature dataset audit",
                ],
            )
            audit_command = " ".join(steps[1].command)
            self.assertIn(str(root / "quality" / "scalar_q_feature_dataset"), audit_command)
            self.assertNotIn(f" {root} --out-dir", audit_command)
            self.assertIn("--scalar-q-definition min", audit_command)
            self.assertIn("--coverage-feature-columns lp_nh_center,ls_nh_center,q_center,k_center", audit_command)

    def test_quality_gates_can_add_physical_feature_acquisition_candidate_steps(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            args = gates._parse_args(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--plan-physical-feature-balanced-acquisition",
                    "--physical-feature-columns",
                    "lp_nh_center,ls_nh_center,k_center",
                    "--physical-feature-bins",
                    "3",
                    "--physical-feature-target-count-per-bin",
                    "5",
                    "--physical-feature-plan-next-count",
                    "24",
                    "--physical-feature-plan-max-target-bins",
                    "6",
                    "--build-physical-feature-surrogate-candidates",
                    "--physical-feature-surrogate-candidate-count",
                    "256",
                    "--physical-feature-surrogate-prediction-batch-size",
                    "32",
                    "--physical-feature-surrogate-no-plots",
                    "--select-physical-feature-targeted-candidates",
                    "--physical-feature-candidate-max-total",
                    "24",
                    "--physical-feature-candidate-allow-outside-bin",
                    "--select-physical-feature-validation-samples",
                    "--physical-feature-validation-sample-count",
                    "1",
                    "--physical-feature-validation-mode",
                    "coverage_then_random",
                ]
            )

            steps = gates._build_steps(root, root / "quality", args)

            self.assertEqual(
                [step.name for step in steps],
                [
                    "physical-feature balanced acquisition plan",
                    "physical-feature surrogate candidate prediction",
                    "physical-feature targeted candidate geometry selection",
                    "physical-feature validation sample selection",
                ],
            )
            plan_command = " ".join(steps[0].command)
            self.assertIn("plan_physical_feature_balanced_acquisition.py", plan_command)
            self.assertIn("--feature-columns lp_nh_center,ls_nh_center,k_center", plan_command)
            self.assertIn("--bins 3", plan_command)
            self.assertIn("--target-count-per-bin 5", plan_command)
            self.assertIn("--next-count 24", plan_command)
            self.assertIn("--max-target-bins 6", plan_command)
            predict_command = " ".join(steps[1].command)
            self.assertIn("build_physical_feature_surrogate_candidate_predictions.py", predict_command)
            self.assertIn("--candidate-count 256", predict_command)
            self.assertIn("--prediction-batch-size 32", predict_command)
            self.assertIn("--no-plots", predict_command)
            select_command = " ".join(steps[2].command)
            self.assertIn("select_physical_feature_targeted_candidate_geometries.py", select_command)
            self.assertIn("--max-total 24", select_command)
            self.assertIn("--allow-outside-bin", select_command)
            validation_command = " ".join(steps[3].command)
            self.assertIn("select_physical_feature_validation_samples.py", validation_command)
            self.assertIn("--sample-count 1", validation_command)
            self.assertIn("--mode coverage_then_random", validation_command)

    def test_quality_gates_can_add_physical_feature_inverse_design_steps(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            args = gates._parse_args(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--physical-feature-columns",
                    "lp_nh_center,ls_nh_center,q_center,k_center",
                    "--build-physical-feature-inverse-training-table",
                    "--no-inverse-training-require-touchstone-path",
                    "--inverse-geometry-config",
                    str(root / "s8p_config.yaml"),
                    "--predict-geometry-from-physical-features",
                    "--inverse-target",
                    "lp_nh_center=0.8",
                    "--inverse-target",
                    "ls_nh_center=1.1",
                    "--inverse-target",
                    "q_center=9",
                    "--inverse-target",
                    "k_center=0.45",
                    "--inverse-candidate-count",
                    "3",
                    "--inverse-k-neighbors",
                    "4",
                ]
            )

            steps = gates._build_steps(root, root / "quality", args)

            self.assertEqual(
                [step.name for step in steps],
                [
                    "physical-feature inverse training table",
                    "physical-feature inverse geometry prediction",
                ],
            )
            table_command = " ".join(steps[0].command)
            self.assertIn("build_physical_feature_inverse_training_table.py", table_command)
            self.assertIn("--feature-columns lp_nh_center,ls_nh_center,q_center,k_center", table_command)
            self.assertIn("--no-require-touchstone-path", table_command)
            self.assertIn(f"--config {root / 's8p_config.yaml'}", table_command)
            predict_command = " ".join(steps[1].command)
            self.assertIn("predict_geometry_from_physical_features.py", predict_command)
            self.assertIn("--target lp_nh_center=0.8", predict_command)
            self.assertIn("--target k_center=0.45", predict_command)
            self.assertIn("--candidate-count 3", predict_command)
            self.assertIn("--k-neighbors 4", predict_command)
            self.assertIn(f"--config {root / 's8p_config.yaml'}", predict_command)

    def test_quality_gates_can_derive_scalar_q_before_physical_feature_steps(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            args = gates._parse_args(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--derive-scalar-q-feature",
                    "--scalar-q-definition",
                    "min",
                    "--physical-feature-columns",
                    "lp_nh_center,ls_nh_center,q_center,k_center",
                    "--plan-physical-feature-balanced-acquisition",
                    "--build-physical-feature-inverse-training-table",
                    "--no-inverse-training-require-touchstone-path",
                ]
            )

            steps = gates._build_steps(root, root / "quality", args)

            self.assertEqual(
                [step.name for step in steps],
                [
                    "scalar Q feature derivation",
                    "physical-feature balanced acquisition plan",
                    "physical-feature inverse training table",
                ],
            )
            scalar_command = " ".join(steps[0].command)
            self.assertIn("derive_scalar_q_feature.py", scalar_command)
            self.assertIn("--q-definition min", scalar_command)
            self.assertIn("--output-column q_center", scalar_command)
            plan_command = " ".join(steps[1].command)
            self.assertIn(str(root / "quality" / "scalar_q_feature_dataset"), plan_command)
            self.assertIn("--feature-columns lp_nh_center,ls_nh_center,q_center,k_center", plan_command)
            table_command = " ".join(steps[2].command)
            self.assertIn(str(root / "quality" / "scalar_q_feature_dataset"), table_command)

    def test_output_status_treats_incomplete_summary_as_failure(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            summary_path = root / "summary.json"
            summary_path.write_text('{"overall_status": "INCOMPLETE"}', encoding="utf-8")

            result = gates._output_status({"summary": summary_path})

            self.assertEqual(result["status"], "FAIL")
            self.assertIn("overall_status=INCOMPLETE", result["reasons"][0])

    def test_quality_gates_can_extract_response_features_and_audit_zin(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touchstone = root / "evaluations" / "a" / "emx" / "emx.s4p"
            touchstone.parent.mkdir(parents=True)
            _write_synthetic_transformer_s4p(touchstone, np.asarray([5.0e9, 10.0e9, 15.0e9]))
            (root / "dataset_rows.csv").write_text("evaluation,ok\na,true\n", encoding="utf-8")

            status = gates.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--extract-response-features",
                    "--audit-response-feature-coverage",
                    "--response-require-cm",
                    "--response-min-valid-count",
                    "1",
                    "--response-min-occupied-k-q-bins",
                    "1",
                    "--audit-zin-coverage",
                    "--select-hfss-samples",
                    "--hfss-sample-count",
                    "1",
                    "--zin-min-valid-count",
                    "1",
                    "--zin-min-occupied-2d-bins",
                    "1",
                    "--touchstone-target-frequency-ghz",
                    "10",
                    "--expected-frequency-start-ghz",
                    "5",
                    "--expected-frequency-stop-ghz",
                    "15",
                    "--expected-frequency-step-ghz",
                    "5",
                    "--expected-frequency-points",
                    "3",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "quality" / "dataset_quality_gates_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(
                [step["name"] for step in summary["steps"]],
                [
                    "response feature extraction",
                    "response feature coverage audit",
                    "Zin coverage audit",
                    "HFSS validation sample selection",
                ],
            )
            self.assertTrue((root / "quality" / "response_features" / "response_features.csv").exists())
            self.assertTrue((root / "quality" / "response_feature_coverage_audit" / "response_feature_coverage_summary.json").exists())
            self.assertTrue((root / "quality" / "hfss_validation_sample_selection" / "hfss_validation_samples.csv").exists())
            zin_summary = json.loads((root / "quality" / "zin_coverage_audit" / "zin_coverage_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(zin_summary["overall_status"], "PASS")
            self.assertEqual(zin_summary["bin_occupancy"]["occupied_2d_bins"], 1)

    def test_quality_gates_forwards_zin_target_envelope_thresholds(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "zin_target_envelope.json"
            args = gates._parse_args(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--audit-zin-coverage",
                    "--zin-target-envelope-config",
                    str(config),
                    "--zin-target-real-min-ohm",
                    "0",
                    "--zin-target-real-max-ohm",
                    "100",
                    "--zin-target-imag-min-ohm",
                    "-50",
                    "--zin-target-imag-max-ohm",
                    "50",
                    "--zin-min-target-envelope-area-frac",
                    "0.4",
                    "--zin-min-target-envelope-occupied-2d-bins",
                    "12",
                    "--zin-max-target-envelope-outside-frac",
                    "0.1",
                ]
            )

            steps = gates._build_steps(root, root / "quality", args)

            self.assertEqual(steps[-1].name, "Zin coverage audit")
            command = " ".join(steps[-1].command)
            self.assertIn(f"--target-envelope-config {config}", command)
            self.assertIn("--target-real-min-ohm 0.0", command)
            self.assertIn("--target-real-max-ohm 100.0", command)
            self.assertIn("--target-imag-min-ohm -50.0", command)
            self.assertIn("--target-imag-max-ohm 50.0", command)
            self.assertIn("--min-target-envelope-area-frac 0.4", command)
            self.assertIn("--min-target-envelope-occupied-2d-bins 12", command)
            self.assertIn("--max-target-envelope-outside-frac 0.1", command)

    def test_quality_gates_forwards_response_target_envelope_thresholds(self) -> None:
        gates = _load_quality_gates_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "response_target_envelopes.json"
            args = gates._parse_args(
                [
                    str(root),
                    "--out-dir",
                    str(root / "quality"),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--audit-response-feature-coverage",
                    "--response-target-envelope-config",
                    str(config),
                    "--response-target-k-min",
                    "-0.5",
                    "--response-target-k-max",
                    "0",
                    "--response-target-qp-min",
                    "5",
                    "--response-target-qp-max",
                    "15",
                    "--response-min-target-k-qp-area-frac",
                    "0.4",
                    "--response-min-target-k-qp-occupied-2d-bins",
                    "8",
                    "--response-max-target-k-qp-outside-frac",
                    "0.1",
                    "--response-target-lp-min-nh",
                    "0.5",
                    "--response-target-lp-max-nh",
                    "3",
                    "--response-target-ls-min-nh",
                    "0.5",
                    "--response-target-ls-max-nh",
                    "3",
                    "--response-min-target-lp-ls-area-frac",
                    "0.3",
                    "--response-min-target-lp-ls-occupied-2d-bins",
                    "6",
                    "--response-max-target-lp-ls-outside-frac",
                    "0.2",
                ]
            )

            steps = gates._build_steps(root, root / "quality", args)

            self.assertEqual(steps[-1].name, "response feature coverage audit")
            command = " ".join(steps[-1].command)
            self.assertIn(f"--target-envelope-config {config}", command)
            self.assertIn("--target-k-min -0.5", command)
            self.assertIn("--target-k-max 0.0", command)
            self.assertIn("--target-qp-min 5.0", command)
            self.assertIn("--target-qp-max 15.0", command)
            self.assertIn("--min-target-k-qp-area-frac 0.4", command)
            self.assertIn("--min-target-k-qp-occupied-2d-bins 8", command)
            self.assertIn("--max-target-k-qp-outside-frac 0.1", command)
            self.assertIn("--target-lp-min-nh 0.5", command)
            self.assertIn("--target-lp-max-nh 3.0", command)
            self.assertIn("--target-ls-min-nh 0.5", command)
            self.assertIn("--target-ls-max-nh 3.0", command)
            self.assertIn("--min-target-lp-ls-area-frac 0.3", command)
            self.assertIn("--min-target-lp-ls-occupied-2d-bins 6", command)
            self.assertIn("--max-target-lp-ls-outside-frac 0.2", command)
