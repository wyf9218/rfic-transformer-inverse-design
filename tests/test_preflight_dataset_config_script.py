from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_preflight_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "preflight_dataset_config.py"
    spec = importlib.util.spec_from_file_location("preflight_dataset_config_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PreflightDatasetConfigScriptTest(TransformerToolboxTestBase):
    def test_template_passes_wideband_preflight_with_placeholder_warning(self) -> None:
        preflight = _load_preflight_module()
        config = Path(__file__).resolve().parents[1] / "configs" / "mars_dataset_248k_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = Path(tmpdir) / "summary.json"
            report = Path(tmpdir) / "report.md"
            status = preflight.main(
                [
                    str(config),
                    "--summary",
                    str(summary),
                    "--report",
                    str(report),
                    "--expected-frequency-stop-ghz",
                    "50.0",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "451",
                ]
            )

            self.assertEqual(status, 0)
            data = json.loads(summary.read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in data["checks"]}
            self.assertEqual(data["overall_status"], "PASS")
            self.assertEqual(checks["frequency grid"]["status"], "PASS")
            self.assertEqual(checks["port mode"]["status"], "PASS")
            self.assertEqual(checks["cadence pin purpose"]["status"], "PASS")
            self.assertEqual(checks["shield"]["status"], "PASS")
            self.assertEqual(checks["EMX/Cadence paths"]["status"], "WARN")

    def test_s8p_physical_feature_template_fails_until_todos_are_confirmed(self) -> None:
        preflight = _load_preflight_module()
        config = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = Path(tmpdir) / "summary.json"
            report = Path(tmpdir) / "report.md"
            status = preflight.main(
                [
                    str(config),
                    "--summary",
                    str(summary),
                    "--report",
                    str(report),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            data = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(data["overall_status"], "FAIL")
            checks = {item["name"]: item for item in data["checks"]}
            self.assertEqual(checks["config loads"]["status"], "FAIL")
            self.assertTrue(
                "differential_port_pairs" in checks["config loads"]["detail"]
                or "power_line_8port configuration is incomplete" in checks["config loads"]["detail"]
                or "/REPLACE/WITH/REAL" in checks["config loads"]["detail"]
            )

    def test_wrong_port_mode_fails(self) -> None:
        preflight = _load_preflight_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "cfg.yaml"
            config.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "  frequency_start_hz: 5000000000.0",
                        "  frequency_stop_hz: 50000000000.0",
                        "  frequency_step_hz: 100000000.0",
                        "emx:",
                        "  port_mode: single_ended_floating",
                        "  cadence_pin_purpose: 51",
                    ]
                ),
                encoding="utf-8",
            )

            status = preflight.main([str(config)])

            self.assertEqual(status, 2)
            data = json.loads(config.with_suffix(".preflight_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in data["checks"]}
            self.assertEqual(checks["port mode"]["status"], "FAIL")

    def test_mismatched_band_points_fails_at_config_load(self) -> None:
        preflight = _load_preflight_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "cfg.yaml"
            config.write_text(
                "\n".join(
                    [
                        "target:",
                        "  topology_mode: 1t1t",
                        "  frequency_start_hz: 5000000000.0",
                        "  frequency_stop_hz: 50000000000.0",
                        "  frequency_step_hz: 100000000.0",
                        "  band_points: 9",
                    ]
                ),
                encoding="utf-8",
            )

            status = preflight.main([str(config), "--no-fail-exit"])

            self.assertEqual(status, 0)
            data = json.loads(config.with_suffix(".preflight_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(data["overall_status"], "FAIL")
            checks = {item["name"]: item for item in data["checks"]}
            self.assertEqual(checks["config loads"]["status"], "FAIL")
            self.assertIn("band_points must match explicit frequency sweep", checks["config loads"]["detail"])

    def test_forbid_dry_run_paths_rejects_true_binary_even_when_it_exists(self) -> None:
        preflight = _load_preflight_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
            process = (
                Path(__file__).resolve().parents[1]
                / "rfic_transformer_inverse_design"
                / "process"
                / "assets"
                / "proc"
                / "default_typical.proc"
            )
            cds_lib = root / "cds.lib"
            layer_map = root / "layers.layermap"
            cadence_root = root / "cadence"
            cds_lib.write_text("DEFINE tsmc65lp .\n", encoding="utf-8")
            layer_map.write_text("layer map", encoding="utf-8")
            cadence_root.mkdir()
            config = root / "cfg.yaml"
            text = template.read_text(encoding="utf-8")
            replacements = {
                "/REPLACE/WITH/REAL/EMX/BINARY": "/usr/bin/true",
                "/REPLACE/WITH/REAL/TSMC65_OR_EMX_PROC_FILE.proc": str(process),
                "/REPLACE/WITH/REAL/CADENCE/IC/ROOT": str(cadence_root),
                "/REPLACE/WITH/REAL/PDK/cds.lib": str(cds_lib),
                "REPLACE_WITH_REAL_TECH_LIB_NAME": "tsmc65lp",
                "/REPLACE/WITH/REAL/PDK/layers.layermap": str(layer_map),
                "TODO_CONFIRM_P001_TO_P008": "1,4:5,6",
                "TODO_P001_PRIMARY_TOP": "P001",
                "TODO_P002_LEFT_POWER_TOP": "P002",
                "TODO_P003_LEFT_POWER_BOTTOM": "P003",
                "TODO_P004_PRIMARY_BOTTOM": "P004",
                "TODO_P005_SECONDARY_BOTTOM": "P005",
                "TODO_P006_SECONDARY_TOP": "P006",
                "TODO_P007_RIGHT_POWER_TOP": "P007",
                "TODO_P008_RIGHT_POWER_BOTTOM": "P008",
            }
            for old, new in replacements.items():
                text = text.replace(old, new)
            config.write_text(text, encoding="utf-8")

            status = preflight.main([str(config), "--check-emx-paths", "--forbid-dry-run-paths", "--no-fail-exit"])

            self.assertEqual(status, 0)
            data = json.loads(config.with_suffix(".preflight_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(data["overall_status"], "FAIL")
            checks = {item["name"]: item for item in data["checks"]}
            self.assertEqual(checks["EMX/Cadence paths"]["status"], "FAIL")
            self.assertIn("dry-run-placeholder", checks["EMX/Cadence paths"]["detail"])

    def test_check_emx_paths_rejects_cadence_root_without_oa_tools(self) -> None:
        preflight = _load_preflight_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
            process = (
                Path(__file__).resolve().parents[1]
                / "rfic_transformer_inverse_design"
                / "process"
                / "assets"
                / "proc"
                / "default_typical.proc"
            )
            emx_binary = root / "bin" / "emx"
            emx_binary.parent.mkdir(parents=True)
            emx_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            emx_binary.chmod(0o755)
            cds_lib = root / "cds.lib"
            layer_map = root / "layers.layermap"
            cadence_root = root / "cae_apps_wrapper_root"
            cds_lib.write_text("DEFINE tsmc65lp .\n", encoding="utf-8")
            layer_map.write_text("layer map", encoding="utf-8")
            cadence_root.mkdir()
            config = root / "cfg.yaml"
            text = template.read_text(encoding="utf-8")
            replacements = {
                "/REPLACE/WITH/REAL/EMX/BINARY": str(emx_binary),
                "/REPLACE/WITH/REAL/TSMC65_OR_EMX_PROC_FILE.proc": str(process),
                "/REPLACE/WITH/REAL/CADENCE/IC/ROOT": str(cadence_root),
                "/REPLACE/WITH/REAL/PDK/cds.lib": str(cds_lib),
                "REPLACE_WITH_REAL_TECH_LIB_NAME": "tsmc65lp",
                "/REPLACE/WITH/REAL/PDK/layers.layermap": str(layer_map),
                "TODO_CONFIRM_P001_TO_P008": "1,4:5,6",
                "TODO_P001_PRIMARY_TOP": "P001",
                "TODO_P002_LEFT_POWER_TOP": "P002",
                "TODO_P003_LEFT_POWER_BOTTOM": "P003",
                "TODO_P004_PRIMARY_BOTTOM": "P004",
                "TODO_P005_SECONDARY_BOTTOM": "P005",
                "TODO_P006_SECONDARY_TOP": "P006",
                "TODO_P007_RIGHT_POWER_TOP": "P007",
                "TODO_P008_RIGHT_POWER_BOTTOM": "P008",
            }
            for old, new in replacements.items():
                text = text.replace(old, new)
            config.write_text(text, encoding="utf-8")

            status = preflight.main([str(config), "--check-emx-paths", "--no-fail-exit"])

            self.assertEqual(status, 0)
            data = json.loads(config.with_suffix(".preflight_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(data["overall_status"], "FAIL")
            checks = {item["name"]: item for item in data["checks"]}
            self.assertEqual(checks["EMX/Cadence paths"]["status"], "FAIL")
            self.assertIn("cadence_install_root/bin/dbAccess", checks["EMX/Cadence paths"]["detail"])
