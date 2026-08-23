from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys
from unittest import mock

import yaml


def _load_patch_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "patch_mars_config_paths.py"
    spec = importlib.util.spec_from_file_location("patch_mars_config_paths_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_placeholder_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "target:",
                "  frequency_start_hz: 5000000000.0",
                "  frequency_stop_hz: 50000000000.0",
                "  frequency_step_hz: 100000000.0",
                "  band_points: 451",
                "emx:",
                "  emx_binary: /REPLACE/WITH/REAL/EMX/BINARY",
                "  emx_process_file: /REPLACE/WITH/REAL/PROC.proc",
                "  cadence_install_root: /REPLACE/WITH/REAL/CADENCE",
                "  cadence_pdk_cds_lib: /REPLACE/WITH/REAL/cds.lib",
                "  cadence_tech_lib: REPLACE_WITH_REAL_TECH_LIB_NAME",
                "  cadence_layer_map: /REPLACE/WITH/REAL/layers.layermap",
                "  port_mode: single_ended_shield_grounded",
                "  cadence_pin_purpose: 51",
                "bounds:",
                "  primary:",
                "    outer_width_um:",
                "    - 160.0",
                "    - 520.0",
                "  offset_um:",
                "  - -90.0",
                "  - 90.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


_REAL_IMPORT = __import__


def _block_yaml_import(name, *args, **kwargs):
    if name == "yaml":
        raise ModuleNotFoundError("No module named yaml")
    return _REAL_IMPORT(name, *args, **kwargs)


class PatchMarsConfigPathsScriptTest(TransformerToolboxTestBase):
    def test_patches_all_required_paths_and_passes_path_check(self) -> None:
        patcher = _load_patch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "wideband.yaml"
            _write_placeholder_config(config)
            emx_binary = root / "emx"
            proc = root / "proc.proc"
            cadence = root / "cadence"
            cds = root / "cds.lib"
            layer_map = root / "layers.layermap"
            for path in (emx_binary, proc, cds, layer_map):
                path.write_text("x", encoding="utf-8")
            cadence.mkdir()
            out_config = root / "wideband_patched.yaml"

            status = patcher.main(
                [
                    str(config),
                    "--out-config",
                    str(out_config),
                    "--emx-binary",
                    str(emx_binary),
                    "--emx-process-file",
                    str(proc),
                    "--cadence-install-root",
                    str(cadence),
                    "--cadence-pdk-cds-lib",
                    str(cds),
                    "--cadence-tech-lib",
                    "tsmc65lp",
                    "--cadence-layer-map",
                    str(layer_map),
                    "--check-paths",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads(out_config.with_suffix(".yaml.path_patch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["remaining_placeholder_fields"], [])
            patched = yaml.safe_load(out_config.read_text(encoding="utf-8"))
            self.assertEqual(patched["emx"]["cadence_tech_lib"], "tsmc65lp")
            self.assertEqual(patched["emx"]["emx_binary"], str(emx_binary))
            self.assertEqual(summary["yaml_backend"], "pyyaml")

    def test_patches_required_paths_without_pyyaml(self) -> None:
        patcher = _load_patch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "wideband.yaml"
            _write_placeholder_config(config)
            emx_binary = root / "emx"
            proc = root / "proc.proc"
            cadence = root / "cadence"
            cds = root / "cds.lib"
            layer_map = root / "layers.layermap"
            for path in (emx_binary, proc, cds, layer_map):
                path.write_text("x", encoding="utf-8")
            cadence.mkdir()
            out_config = root / "wideband_patched.yaml"

            with mock.patch("builtins.__import__", side_effect=_block_yaml_import):
                status = patcher.main(
                    [
                        str(config),
                        "--out-config",
                        str(out_config),
                        "--emx-binary",
                        str(emx_binary),
                        "--emx-process-file",
                        str(proc),
                        "--cadence-install-root",
                        str(cadence),
                        "--cadence-pdk-cds-lib",
                        str(cds),
                        "--cadence-tech-lib",
                        "tsmc65lp",
                        "--cadence-layer-map",
                        str(layer_map),
                        "--check-paths",
                    ]
                )

            self.assertEqual(status, 0)
            summary = json.loads(out_config.with_suffix(".yaml.path_patch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["yaml_backend"], "internal-simple-yaml")
            patched = yaml.safe_load(out_config.read_text(encoding="utf-8"))
            self.assertEqual(patched["target"]["band_points"], 451)
            self.assertEqual(patched["bounds"]["primary"]["outer_width_um"], [160.0, 520.0])
            self.assertEqual(patched["bounds"]["offset_um"], [-90.0, 90.0])
            self.assertEqual(patched["emx"]["cadence_tech_lib"], "tsmc65lp")
            self.assertEqual(patched["emx"]["emx_binary"], str(emx_binary))

    def test_partial_patch_is_incomplete(self) -> None:
        patcher = _load_patch_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "wideband.yaml"
            _write_placeholder_config(config)
            out_config = root / "wideband_partial.yaml"

            status = patcher.main(
                [
                    str(config),
                    "--out-config",
                    str(out_config),
                    "--cadence-tech-lib",
                    "tsmc65lp",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads(out_config.with_suffix(".yaml.path_patch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            self.assertIn("emx_binary", summary["remaining_placeholder_fields"])
