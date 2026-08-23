from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys

import yaml

THE_BEST_ROLE_LABELS = (
    "primary_top=P001,left_power_top=P002,left_power_bottom=P003,primary_bottom=P004,"
    "secondary_bottom=P005,secondary_top=P006,right_power_top=P007,right_power_bottom=P008"
)


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "prepare_final_s8p_physical_feature_config.py"
    spec = importlib.util.spec_from_file_location("prepare_final_s8p_physical_feature_config_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_required_paths(root: Path) -> dict[str, Path]:
    paths = {
        "emx_binary": root / "tools" / "emx" / "bin" / "emx",
        "emx_process_file": root / "pdk" / "proc" / "rf.proc",
        "cadence_install_root": root / "cadence" / "ICADVM",
        "cadence_pdk_cds_lib": root / "pdk" / "cds.lib",
        "cadence_layer_map": root / "pdk" / "layers.layermap",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix or path.name == "emx":
            path.write_text("x", encoding="utf-8")
        else:
            path.mkdir(exist_ok=True)
    paths["emx_binary"].chmod(0o755)
    paths["cadence_install_root"].mkdir(exist_ok=True)
    cadence_bin = paths["cadence_install_root"] / "bin"
    cadence_bin.mkdir(exist_ok=True)
    for tool in ("dbAccess", "strmin", "strmout"):
        tool_path = cadence_bin / tool
        tool_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool_path.chmod(0o755)
    return paths


def _write_discovery_summary(path: Path, paths: dict[str, Path]) -> None:
    selected = {
        key: {"path": str(value), "source": "test", "exists": True}
        for key, value in paths.items()
    }
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "selected_candidates": selected,
                "tech_lib_candidates": ["tsmc65lp"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


class PrepareFinalS8pPhysicalFeatureConfigScriptTest(TransformerToolboxTestBase):
    def test_prepares_pass_config_from_discovery_summary_and_confirmed_choices(self) -> None:
        mod = _load_module()
        template = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = _make_required_paths(root)
            discovery = root / "path_discovery.json"
            _write_discovery_summary(discovery, paths)
            out_config = root / "final_s8p.yaml"

            status = mod.main(
                [
                    "--template",
                    str(template),
                    "--path-discovery-summary",
                    str(discovery),
                    "--out-config",
                    str(out_config),
                    "--out-dir",
                    str(root / "prepared"),
                    "--port-map",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--role-labels",
                    THE_BEST_ROLE_LABELS,
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                    "--check-paths",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "prepared" / "final_s8p_physical_feature_config_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "READY_FOR_S8P_LAUNCH_PACKET")
            config = yaml.safe_load(out_config.read_text(encoding="utf-8"))
            self.assertEqual(config["target"]["frequency_start_hz"], 5.0e9)
            self.assertEqual(config["target"]["frequency_stop_hz"], 60.0e9)
            self.assertEqual(config["target"]["frequency_step_hz"], 5.0e8)
            self.assertEqual(config["target"]["band_points"], 111)
            self.assertEqual(config["emx"]["emx_process_file"], str(paths["emx_process_file"]))
            self.assertEqual(config["emx"]["differential_port_pairs"], "1,4:5,6")
            self.assertEqual(config["emx"]["power_line_8port"]["bridge_width_um"], 10.0)
            self.assertEqual(config["emx"]["power_line_8port"]["port_map"], ["P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"])
            self.assertEqual(config["emx"]["power_line_8port"]["role_labels"]["primary_top"], "P001")
            self.assertEqual(config["emx"]["power_line_8port"]["role_labels"]["secondary_bottom"], "P005")
            self.assertEqual(config["topology"]["primary"]["vdd_bar"]["bar_layer"], 74)
            self.assertEqual(config["topology"]["secondary"]["vdd_bar"]["bar_layer"], 39)
            self.assertEqual(config["physical_feature_inverse"]["scalar_q_definition"], "min")
            launch_defaults = json.loads((root / "prepared" / "final_s8p_physical_feature_launch_defaults.json").read_text(encoding="utf-8"))
            self.assertEqual(launch_defaults["physical_feature_columns"], "lp_nh_center,ls_nh_center,q_center,k_center")
            self.assertEqual(launch_defaults["jobs"], 8)
            self.assertEqual(launch_defaults["emx_max_count"], 500)

    def test_missing_confirmations_write_draft_but_do_not_pass(self) -> None:
        mod = _load_module()
        template = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_config = root / "draft_s8p.yaml"

            status = mod.main(
                [
                    "--template",
                    str(template),
                    "--out-config",
                    str(out_config),
                    "--out-dir",
                    str(root / "prepared"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            self.assertTrue(out_config.exists())
            summary = json.loads((root / "prepared" / "final_s8p_physical_feature_config_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_RUN_GENERATED_S8P_CONFIG_YET")
            failed = {item["name"]: item for item in summary["checks"] if item["status"] == "FAIL"}
            self.assertIn("scalar Q definition finalized", failed)
            self.assertIn("P001-P008 port map finalized", failed)
            self.assertIn("emx_binary finalized", failed)

    def test_dry_run_paths_do_not_pass_as_finalized_paths(self) -> None:
        mod = _load_module()
        template = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = _make_required_paths(root)
            paths["emx_binary"] = Path("/usr/bin/true")
            discovery = root / "path_discovery.json"
            _write_discovery_summary(discovery, paths)
            out_config = root / "final_s8p.yaml"

            status = mod.main(
                [
                    "--template",
                    str(template),
                    "--path-discovery-summary",
                    str(discovery),
                    "--out-config",
                    str(out_config),
                    "--out-dir",
                    str(root / "prepared"),
                    "--port-map",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--role-labels",
                    THE_BEST_ROLE_LABELS,
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                    "--check-paths",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "prepared" / "final_s8p_physical_feature_config_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_RUN_GENERATED_S8P_CONFIG_YET")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["emx_binary finalized"]["status"], "FAIL")
            self.assertEqual(checks["emx_binary exists on this filesystem"]["status"], "PASS")

    def test_cli_paths_override_discovery_summary(self) -> None:
        mod = _load_module()
        template = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = _make_required_paths(root / "discovered")
            discovery = root / "path_discovery.json"
            _write_discovery_summary(discovery, paths)
            override_proc = root / "override" / "override.proc"
            override_proc.parent.mkdir(parents=True)
            override_proc.write_text("override", encoding="utf-8")
            out_config = root / "final_s8p.yaml"

            status = mod.main(
                [
                    "--template",
                    str(template),
                    "--path-discovery-summary",
                    str(discovery),
                    "--emx-process-file",
                    str(override_proc),
                    "--out-config",
                    str(out_config),
                    "--out-dir",
                    str(root / "prepared"),
                    "--port-map",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--role-labels",
                    THE_BEST_ROLE_LABELS,
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                ]
            )

            self.assertEqual(status, 0)
            config = yaml.safe_load(out_config.read_text(encoding="utf-8"))
            self.assertEqual(config["emx"]["emx_process_file"], str(override_proc))

    def test_check_paths_rejects_cadence_wrapper_root_without_oa_tools(self) -> None:
        mod = _load_module()
        template = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = _make_required_paths(root)
            bad_cadence_root = root / "cae_apps_wrapper_root"
            bad_cadence_root.mkdir()
            paths["cadence_install_root"] = bad_cadence_root
            discovery = root / "path_discovery.json"
            _write_discovery_summary(discovery, paths)
            out_config = root / "final_s8p.yaml"

            status = mod.main(
                [
                    "--template",
                    str(template),
                    "--path-discovery-summary",
                    str(discovery),
                    "--out-config",
                    str(out_config),
                    "--out-dir",
                    str(root / "prepared"),
                    "--port-map",
                    "P001,P002,P003,P004,P005,P006,P007,P008",
                    "--role-labels",
                    THE_BEST_ROLE_LABELS,
                    "--differential-port-pairs",
                    "1,4:5,6",
                    "--scalar-q-definition",
                    "min",
                    "--primary-power-line-layer",
                    "74",
                    "--secondary-power-line-layer",
                    "39",
                    "--check-paths",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "prepared" / "final_s8p_physical_feature_config_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["cadence_install_root exists on this filesystem"]["status"], "PASS")
            self.assertEqual(checks["cadence dbAccess executable exists"]["status"], "FAIL")
            self.assertIn("cae_apps_wrapper_root/bin/dbAccess", checks["cadence dbAccess executable exists"]["detail"])
