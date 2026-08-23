from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "prepare_target_emx_wideband_rerun.py"
    spec = importlib.util.spec_from_file_location("prepare_target_emx_wideband_rerun_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _summary_payload() -> dict:
    return {
        "cache_key": "ec6698dfc575950b",
        "ok": True,
        "error": None,
        "work_dir": "/home/researcher/project/runs/evaluations/ec6698dfc575950b",
        "touchstone_path": "/home/researcher/project/runs/evaluations/ec6698dfc575950b/emx/emx.s4p",
        "command": [
            "/cae/apps/data/cadence-2025/installs/EMX20251/bin/emx",
            "/home/researcher/project/runs/cadence_batches/batch/streamout/transformer_layout_cadpins.gds",
            "TRANSFORMER_021_ec6698df",
            "/path/to/pdk/proc.proc",
            "--touchstone",
            "--s-impedance=50",
            "-s",
            "/home/researcher/project/runs/evaluations/ec6698dfc575950b/emx/emx.s4p",
            "--include-command-line",
            "--edge-width=1",
            "--accuracy=standard",
            "--verbose=2",
            "--cadence-pins=51",
            "--port=P001=P001:P001_G",
            "--port=P002=P002:P002_G",
            "--port=P003=P003:P003_G",
            "--port=P004=P004:P004_G",
            "13500000000",
            "13875000000",
            "14250000000",
            "14625000000",
            "15000000000",
            "15375000000",
            "15750000000",
            "16125000000",
            "16500000000",
        ],
    }


class PrepareTargetEmxWidebandRerunScriptTest(TransformerToolboxTestBase):
    def test_generates_451_point_mars_command_from_saved_target_command(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            summary_path = root / "summary.json"
            summary_path.write_text(json.dumps(_summary_payload(), indent=2), encoding="utf-8")

            status = mod.main(["--summary-json", str(summary_path), "--out-dir", str(root / "out")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_wideband_rerun_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "READY_FOR_MARS_EMX_RERUN_COMMAND_ONLY")
            self.assertEqual(summary["generated_frequency_hz"]["points"], 451)
            self.assertEqual(summary["generated_frequency_hz"]["start"], 5_000_000_000)
            self.assertEqual(summary["generated_frequency_hz"]["stop"], 50_000_000_000)
            self.assertEqual(summary["generated_frequency_hz"]["step"], 100_000_000)
            self.assertIn("emx_wideband_5_50_0p1", summary["generated_output_s4p"])
            self.assertNotEqual(summary["generated_output_s4p"], summary["original_output_s4p"])
            command = summary["generated_command"]
            prefix, freqs = mod._split_trailing_frequencies(command)
            self.assertEqual(len(freqs), 451)
            self.assertEqual(freqs[0], 5_000_000_000)
            self.assertEqual(freqs[-1], 50_000_000_000)
            self.assertIn("--cadence-pins=51", prefix)
            for flag in mod.EXPECTED_PORT_FLAGS:
                self.assertIn(flag, prefix)
            self.assertTrue((root / "out" / "target_emx_wideband_rerun.commands.sh").exists())
            self.assertTrue((root / "out" / "target_emx_wideband_frequency_grid.csv").exists())

    def test_blocks_reusing_the_old_narrowband_output_path(self) -> None:
        mod = _load_module()
        payload = _summary_payload()
        old_output = payload["command"][7]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            summary_path = root / "summary.json"
            summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            status = mod.main(
                [
                    "--summary-json",
                    str(summary_path),
                    "--out-dir",
                    str(root / "out"),
                    "--output-s4p",
                    old_output,
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_wideband_rerun_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_RUN_COMMAND_UNTIL_CHECKS_PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["output path is not old narrowband file"]["status"], "FAIL")
            self.assertEqual(checks["output path labels wideband rerun"]["status"], "FAIL")
