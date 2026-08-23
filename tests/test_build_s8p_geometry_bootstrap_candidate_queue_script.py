from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_s8p_geometry_bootstrap_candidate_queue.py"
    spec = importlib.util.spec_from_file_location("build_s8p_geometry_bootstrap_candidate_queue_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_queue_runner():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_candidate_queue_dataset.py"
    spec = importlib.util.spec_from_file_location("run_candidate_queue_dataset_script", script_path)
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


class BuildS8PGeometryBootstrapCandidateQueueScriptTest(TransformerToolboxTestBase):
    def test_builds_bootstrap_candidate_queue_from_final_s8p_config(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--out-dir",
                    str(root / "bootstrap"),
                    "--count",
                    "16",
                    "--expected-count",
                    "16",
                    "--sampler",
                    "lhs_optimized",
                    "--seed",
                    "20260616",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "bootstrap" / "s8p_geometry_bootstrap_candidate_queue_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "USE_BOOTSTRAP_CANDIDATE_QUEUE_FOR_FIRST_S8P_EMX_RUN")
            self.assertEqual(summary["sample_count"], 16)
            self.assertEqual(summary["requested_count"], 16)
            self.assertIn("run_candidate_queue_dataset_parallel.py", summary["run_command_hint"])
            self.assertEqual(summary["uniformity"]["count"], 16)
            self.assertGreater(len(summary["field_order"]), 0)
            with Path(summary["candidate_csv"]).open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 16)
            self.assertEqual(rows[0]["candidate_id"], "s8p_bootstrap_00001")
            self.assertEqual(rows[0]["inside_target_bin"], "true")
            for field in summary["field_order"]:
                self.assertIn(f"geom__{field}", rows[0])
                self.assertIn(f"unit__{field}", rows[0])

    def test_generated_queue_is_accepted_by_candidate_queue_create_only_runner(self) -> None:
        mod = _load_module()
        queue_runner = _load_queue_runner()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)
            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--out-dir",
                    str(root / "bootstrap"),
                    "--count",
                    "2",
                    "--expected-count",
                    "2",
                ]
            )
            self.assertEqual(status, 0)

            runner_status = queue_runner.main(
                [
                    "--candidate-csv",
                    str(root / "bootstrap" / "s8p_geometry_bootstrap_candidate_queue.csv"),
                    "--out-dir",
                    str(root / "create_only"),
                    "--config",
                    str(config),
                    "--max-count",
                    "1",
                    "--batch-size",
                    "1",
                    "--create-only",
                    "--force-wideband-5-60-0p5",
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
                    "--fail-on-error",
                ]
            )

            self.assertEqual(runner_status, 0)
            run_summary = json.loads((root / "create_only" / "candidate_queue_dataset_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(run_summary["overall_status"], "PASS")
            self.assertEqual(run_summary["selected_row_count"], 1)
            self.assertEqual(run_summary["run_emx"], False)

    def test_rejects_non_500_default_bootstrap_count(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "s8p.yaml"
            _write_valid_s8p_config(config)

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--out-dir",
                    str(root / "bootstrap"),
                    "--count",
                    "16",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "bootstrap" / "s8p_geometry_bootstrap_candidate_queue_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["requested count matches expected EMX bootstrap count"]["pass"])

    def test_rejects_template_with_unresolved_placeholders(self) -> None:
        mod = _load_module()
        config = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            status = mod.main(
                [
                    "--config",
                    str(config),
                    "--out-dir",
                    str(root / "bootstrap"),
                    "--count",
                    "4",
                    "--expected-count",
                    "4",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "bootstrap" / "s8p_geometry_bootstrap_candidate_queue_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["config has no unresolved placeholders"]["pass"])
