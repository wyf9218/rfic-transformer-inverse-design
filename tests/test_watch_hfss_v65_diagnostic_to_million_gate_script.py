from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "watch_hfss_v65_diagnostic_to_million_gate.py"
    spec = importlib.util.spec_from_file_location("watch_hfss_v65_diagnostic_to_million_gate_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_promotion(path: Path, *, status: str, full_validator: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "overall_status": status,
        "decision": "WAIT_FOR_V65_DIAGNOSTIC_HFSS_S8P" if status == "WAITING_FOR_DIAGNOSTIC_HFSS" else "READY_TO_RUN_SELECTED_VARIANT_FULL_5_60_HFSS_SWEEP",
        "out_dir": str(path.parent),
        "full_postrun_validator": "" if full_validator is None else str(full_validator),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_postrun(path: Path, *, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    decision = {
        "PASS": "ACCEPT_SELECTED_S8P_EMX_HFSS_PHYSICAL_VALIDATION",
        "WAITING_FOR_HFSS": "WAIT_FOR_EXPORTED_HFSS_S8P",
        "FAIL": "DO_NOT_USE_S8P_HFSS_VALIDATION_YET",
    }[status]
    path.write_text(
        json.dumps(
            {
                "overall_status": status,
                "decision": decision,
                "frequency_grid_mode": "final_5_60_0p5_111",
                "records": [{"status": status, "worst_percent_error": 2.0 if status == "PASS" else None}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_campaign(path: Path, *, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "overall_status": status,
                "decision": "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN" if status == "PASS" else "DO_NOT_START_MILLION_SAMPLE_CAMPAIGN_UNTIL_EMX_HFSS_S8P_GATE_PASSES",
                "chunk_count": 10 if status == "PASS" else 0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


class WatchHfssV65DiagnosticToMillionGateScriptTest(TransformerToolboxTestBase):
    def test_waits_when_v65_diagnostic_is_not_back(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            promotion_out = root / "promotion"
            out_dir = root / "watch"
            seen: list[list[str]] = []

            def fake_run(command, **kwargs):
                seen.append([str(item) for item in command])
                if "promote_hfss" in " ".join(command):
                    _write_promotion(promotion_out / "hfss_lp_ls_full_sweep_promotion_summary.json", status="WAITING_FOR_DIAGNOSTIC_HFSS")
                return mod.subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
                status = mod.main(
                    [
                        "--diagnostic-postrun-script",
                        str(root / "diag.sh"),
                        "--promotion-out-dir",
                        str(promotion_out),
                        "--out-dir",
                        str(out_dir),
                        "--timeout-seconds",
                        "0",
                    ]
                )

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "hfss_v65_diagnostic_to_million_gate_watch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_DIAGNOSTIC_HFSS")
            self.assertEqual(summary["decision"], "WAIT_FOR_V65_DIAGNOSTIC_HFSS_S8P")
            self.assertEqual(summary["attempt_count"], 1)
            self.assertEqual(len(seen), 1)  # Missing diagnostic shell is recorded without subprocess; promotion still runs.

    def test_waits_for_full_hfss_after_diagnostic_promotion(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            promotion_out = root / "promotion"
            validator = root / "full_postrun.sh"
            validator.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            full_summary = promotion_out / "full_selected_variant_postrun_validation" / "s8p_hfss_postrun_validation_summary.json"
            out_dir = root / "watch"

            def fake_run(command, **kwargs):
                joined = " ".join(str(item) for item in command)
                if "promote_hfss" in joined:
                    _write_promotion(
                        promotion_out / "hfss_lp_ls_full_sweep_promotion_summary.json",
                        status="PASS",
                        full_validator=validator,
                    )
                if str(validator) in joined:
                    _write_postrun(full_summary, status="WAITING_FOR_HFSS")
                return mod.subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
                status = mod.main(
                    [
                        "--diagnostic-postrun-script",
                        str(root / "missing_diag.sh"),
                        "--promotion-out-dir",
                        str(promotion_out),
                        "--out-dir",
                        str(out_dir),
                        "--timeout-seconds",
                        "0",
                    ]
                )

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "hfss_v65_diagnostic_to_million_gate_watch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_FULL_HFSS")
            self.assertEqual(summary["decision"], "WAIT_FOR_SELECTED_VARIANT_FULL_5_60_HFSS_S8P")
            self.assertEqual(summary["latest"]["full_postrun_summary"]["overall_status"], "WAITING_FOR_HFSS")

    def test_full_postrun_pass_unlocks_million_planner(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            promotion_out = root / "promotion"
            campaign_out = root / "campaign"
            validator = root / "full_postrun.sh"
            validator.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            full_summary = promotion_out / "full_selected_variant_postrun_validation" / "s8p_hfss_postrun_validation_summary.json"
            campaign_summary = campaign_out / "s8p_million_sample_campaign_plan_summary.json"
            out_dir = root / "watch"

            def fake_run(command, **kwargs):
                joined = " ".join(str(item) for item in command)
                if "promote_hfss" in joined:
                    _write_promotion(
                        promotion_out / "hfss_lp_ls_full_sweep_promotion_summary.json",
                        status="PASS",
                        full_validator=validator,
                    )
                elif str(validator) in joined:
                    _write_postrun(full_summary, status="PASS")
                elif "run_gated_s8p_million_sample_campaign.py" in joined:
                    _write_campaign(campaign_summary, status="PASS")
                return mod.subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
                status = mod.main(
                    [
                        "--diagnostic-postrun-script",
                        str(root / "missing_diag.sh"),
                        "--promotion-out-dir",
                        str(promotion_out),
                        "--campaign-out-dir",
                        str(campaign_out),
                        "--out-dir",
                        str(out_dir),
                        "--timeout-seconds",
                        "0",
                    ]
                )

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "hfss_v65_diagnostic_to_million_gate_watch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN")
            self.assertEqual(summary["latest"]["campaign_summary"]["chunk_count"], 10)
