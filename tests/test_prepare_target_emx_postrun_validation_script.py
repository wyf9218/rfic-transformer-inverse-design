from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "prepare_target_emx_postrun_validation.py"
    spec = importlib.util.spec_from_file_location("prepare_target_emx_postrun_validation_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_rerun_summary(path: Path, *, status: str = "PASS") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": status,
                "decision": "READY_FOR_MARS_EMX_RERUN_COMMAND_ONLY",
                "sample_id": "ec6698dfc575950b",
                "generated_output_s4p": "/home/researcher/project/runs/evaluations/ec6698dfc575950b/emx_wideband_5_50_0p1/emx.s4p",
                "generated_frequency_hz": {
                    "start": 5_000_000_000,
                    "stop": 50_000_000_000,
                    "step": 100_000_000,
                    "points": 451,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


class PrepareTargetEmxPostrunValidationScriptTest(TransformerToolboxTestBase):
    def test_generates_postrun_validation_command_with_physical_and_emx_first_gates(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rerun_summary = root / "rerun" / "target_emx_wideband_rerun_summary.json"
            _write_rerun_summary(rerun_summary)

            status = mod.main(["--rerun-summary", str(rerun_summary), "--out-dir", str(root / "out")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_wideband_postrun_validation_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "READY_FOR_MARS_POSTRUN_VALIDATION")
            required_fragments = summary["required_command_fragments"]
            self.assertIn("--expected-frequency-points 451", required_fragments)
            self.assertIn("--required-sweep-points 451", required_fragments)
            self.assertIn("--min-window-abs-k 0.05", required_fragments)
            command = (root / "out" / "target_emx_wideband_postrun_validation.commands.sh").read_text()
            self.assertIn("test -s \"$EMX_S4P\"", command)
            self.assertIn('scripts/audit_touchstone_transformer.py "$EMX_S4P"', command)
            self.assertIn('--out-dir "$OUT_DIR/touchstone_physical_gate"', command)
            self.assertIn('scripts/build_emx_first_validation_gate.py --emx-s4p "$EMX_S4P"', command)
            self.assertIn('--out-dir "$OUT_DIR/emx_first_validation_gate_20260613"', command)
            self.assertNotIn("'$EMX_S4P'", command)
            self.assertNotIn("'$OUT_DIR", command)
            self.assertIn("scripts/audit_touchstone_transformer.py", command)
            self.assertIn("scripts/build_emx_first_validation_gate.py", command)
            self.assertIn("--expected-source-kind EMX", command)
            self.assertIn("--expected-frequency-points 451", command)
            self.assertIn("--expected-frequency-step-ghz 0.1", command)
            self.assertIn("--required-sweep-stop-ghz 50.0", command)
            self.assertIn("--required-sweep-points 451", command)
            self.assertIn("--min-target-abs-k 0.05", command)
            self.assertIn("--min-window-abs-k 0.05", command)
            self.assertIn("--physical-window-start-ghz 5.0", command)
            self.assertIn("--physical-window-stop-ghz 30.0", command)
            self.assertIn("--max-shape-spike-ratio 4", command)
            self.assertIn("--max-shape-relative-step 0.25", command)
            self.assertIn("--photo-max-percent-error 5.0", command)
            self.assertIn("tar -czf", command)
            self.assertNotIn("/home/researcher", command)

    def test_blocks_when_rerun_summary_is_not_pass(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rerun_summary = root / "rerun" / "target_emx_wideband_rerun_summary.json"
            _write_rerun_summary(rerun_summary, status="FAIL")

            status = mod.main(
                [
                    "--rerun-summary",
                    str(rerun_summary),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_wideband_postrun_validation_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_USE_POSTRUN_COMMAND")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["rerun command preparation status"]["status"], "FAIL")
