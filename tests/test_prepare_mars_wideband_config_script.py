from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys

from rfic_transformer_inverse_design.core import load_run_config


def _load_prepare_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "prepare_mars_wideband_config.py"
    spec = importlib.util.spec_from_file_location("prepare_mars_wideband_config_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PrepareMarsWidebandConfigScriptTest(TransformerToolboxTestBase):
    def test_generates_wideband_500_config_and_commands(self) -> None:
        prepare = _load_prepare_module()
        template = Path(__file__).resolve().parents[1] / "configs" / "mars_dataset_248k_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_config = root / "dataset500_wideband.yaml"

            status = prepare.main(
                [
                    "--template",
                    str(template),
                    "--out-config",
                    str(out_config),
                    "--run-dir",
                    "runs/dataset500_wideband_test",
                    "--count",
                    "500",
                    "--seed",
                    "99",
                ]
            )

            self.assertEqual(status, 0)
            cfg = load_run_config(out_config)
            freqs = cfg.target.frequency_points_hz()
            self.assertEqual(len(freqs), 451)
            self.assertAlmostEqual(freqs[0], 5.0e9)
            self.assertAlmostEqual(freqs[-1], 50.0e9)
            self.assertAlmostEqual(freqs[1] - freqs[0], 0.1e9)
            self.assertEqual(cfg.emx.port_mode, "single_ended_shield_grounded")
            self.assertEqual(cfg.emx.cadence_pin_purpose, 51)
            self.assertTrue(cfg.bounds.shield.enabled)
            summary = json.loads(out_config.with_suffix(".yaml.summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["frequency"]["points"], 451)
            self.assertEqual(summary["count"], 500)
            commands = out_config.with_suffix(".yaml.commands.sh").read_text(encoding="utf-8")
            self.assertIn("scripts/preflight_dataset_config.py", commands)
            self.assertIn("sample-dataset", commands)
            self.assertIn("--count 500", commands)
            self.assertIn("--seed 99", commands)
            self.assertIn("scripts/audit_mars_run_progress.py", commands)
            self.assertIn("--require-emx-command", commands)
            self.assertIn("--expected-port-mode single_ended_shield_grounded", commands)
            self.assertIn("--expected-pin-purpose 51", commands)
            self.assertIn("--require-geometry-quality", commands)
            self.assertIn("--internal-angle-deg 135", commands)
            self.assertIn("--terminal-angle-deg 90", commands)
            self.assertIn("scripts/run_dataset_quality_gates.py", commands)
            self.assertIn("--require-clearance-audit", commands)
            self.assertIn("--touchstone-all", commands)
            self.assertIn("--sampling-require-uniform-closer-than-normal", commands)
            self.assertIn("--sampling-min-uniform-vs-normal-fields-fraction 0.8", commands)
            self.assertIn("--sampling-min-histogram-entropy-frac 0.85", commands)
            self.assertIn("--sampling-max-min-norm 0.05", commands)
            self.assertIn("--sampling-min-max-norm 0.95", commands)
            self.assertIn("--sampling-space-filling-strata 20", commands)
            self.assertIn("--sampling-max-space-filling-empty-strata-frac 0", commands)
            self.assertIn("--sampling-max-space-filling-duplicate-frac 0", commands)
            self.assertIn("--touchstone-shape-window-start-ghz 5.0", commands)
            self.assertIn("--touchstone-shape-window-stop-ghz 30", commands)
            self.assertIn("--touchstone-max-shape-spike-ratio 4", commands)
            self.assertIn("--touchstone-max-shape-relative-step 0.25", commands)
            self.assertIn("--extract-response-features", commands)
            self.assertIn("--audit-response-feature-coverage", commands)
            self.assertIn("--response-require-cm", commands)
            self.assertIn("--response-min-valid-count 500", commands)
            self.assertIn("--audit-zin-coverage", commands)
            self.assertIn("--zin-min-valid-count 500", commands)
            self.assertIn("--select-hfss-samples", commands)
            self.assertIn("post_run_progress_audit_command", summary)
            self.assertIn("post_run_quality_gate_command", summary)

    def test_rejects_non_divisible_frequency_grid(self) -> None:
        prepare = _load_prepare_module()
        template = Path(__file__).resolve().parents[1] / "configs" / "mars_dataset_248k_template.yaml"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_config = root / "bad.yaml"

            with self.assertRaises(SystemExit):
                prepare.main(
                    [
                        "--template",
                        str(template),
                        "--out-config",
                        str(out_config),
                        "--frequency-start-ghz",
                        "5",
                        "--frequency-stop-ghz",
                        "50",
                        "--frequency-step-ghz",
                        "0.13",
                    ]
                )
