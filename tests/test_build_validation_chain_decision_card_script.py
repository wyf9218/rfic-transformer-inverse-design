from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_validation_chain_decision_card.py"
    spec = importlib.util.spec_from_file_location("build_validation_chain_decision_card_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _pass_checks(names: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"status": "PASS", "name": name, "detail": "ok"} for name in names]


def _emx_summary(mod, *, accepted: bool = True) -> dict:
    checks = _pass_checks(mod.EMX_REQUIRED_CHECKS)
    if not accepted:
        for item in checks:
            if item["name"] == "ADS photo anchor":
                item["status"] = "FAIL"
                item["detail"] = "6/6 metrics fail; worst=Lp 88.81%"
            if item["name"] == "final ADS sweep coverage":
                item["status"] = "FAIL"
                item["detail"] = "starts 13.5 GHz > required 5 GHz"
    return {
        "overall_status": "PASS" if accepted else "FAIL",
        "decision": "ACCEPT_AS_GOLDEN_EMX_REFERENCE" if accepted else "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE",
        "frequency_ghz": (
            {"start": 5.0, "stop": 50.0, "step": 0.1, "points": 451}
            if accepted
            else {"start": 13.5, "stop": 16.5, "step": 0.375, "points": 9}
        ),
        "target_record": {
            "nearest_frequency_ghz": 15.0,
            "actuals": {"lp_nh": 0.88, "ls_nh": 0.82, "k": -0.51, "qp": 16.1, "qs": 14.2},
        },
        "checks": checks,
    }


def _hfss_summary(mod, *, accepted: bool = True) -> dict:
    checks = _pass_checks(mod.HFSS_REQUIRED_CHECKS)
    if not accepted:
        for item in checks:
            if item["name"] == "target-frequency transformer metrics":
                item["status"] = "FAIL"
                item["detail"] = "abs(K) < 0.05"
    return {
        "overall_status": "PASS" if accepted else "FAIL",
        "frequency": {
            "start_hz": 1.0e8,
            "stop_hz": 50.0e9,
            "step_hz": 1.0e8,
            "points": 500,
        },
        "metric_summary": {
            "target_point": {"freq_hz": 15.0e9, "lp_nh": 1.0, "ls_nh": 1.2, "k": -0.5, "qp": 12.0, "qs": 13.0}
        },
        "checks": checks,
    }


def _geometry_summary(mod, *, accepted: bool = True) -> dict:
    checks = _pass_checks(mod.HFSS_GEOMETRY_REQUIRED_CHECKS)
    if not accepted:
        for item in checks:
            if item["name"] == "HFSS STEP model":
                item["status"] = "FAIL"
                item["detail"] = "missing STEP file"
    return {
        "overall_status": "PASS" if accepted else "FAIL",
        "decision": "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS" if accepted else "DO_NOT_USE_HFSS_MODEL_GEOMETRY_ASSETS",
        "artifacts": {
            "top_png": "/tmp/top.png",
            "isometric_png": "/tmp/iso.png",
            "quality_png": "/tmp/quality.png",
            "step": "/tmp/model.step",
        },
        "checks": checks,
    }


def _accepted_summary(mod, *, accepted: bool = True, max_error: float = 4.0) -> dict:
    checks = _pass_checks(mod.ACCEPTED_COMPARISON_REQUIRED_CHECKS)
    if not accepted:
        for item in checks:
            if item["name"] == "EMX-vs-HFSS compare core metric errors":
                item["status"] = "FAIL"
                item["detail"] = "metric_k_max_percent_error=8.0"
    return {
        "overall_status": "PASS" if accepted else "FAIL",
        "decision": "ACCEPT_HFSS_VALIDATION_SAMPLE" if accepted else "DO_NOT_USE_HFSS_COMPARISON",
        "frequency_window_hz": {"min": 5.0e9, "max": 50.0e9, "count": 451},
        "metrics": {
            name: {
                "status": "PASS" if accepted else ("FAIL" if name == "k" else "PASS"),
                "max_percent_error": max_error if accepted else (8.0 if name == "k" else 1.0),
                "mean_percent_error": 0.5,
            }
            for name in mod.CORE_METRICS
        },
        "checks": checks,
    }


class BuildValidationChainDecisionCardScriptTest(TransformerToolboxTestBase):
    def test_blocks_final_comparison_when_emx_first_fails_even_if_hfss_passes(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.json"
            geometry = root / "geometry.json"
            hfss = root / "hfss.json"
            _write_json(emx, _emx_summary(mod, accepted=False))
            _write_json(geometry, _geometry_summary(mod, accepted=True))
            _write_json(hfss, _hfss_summary(mod, accepted=True))

            status = mod.main(
                [
                    "--emx-first-summary",
                    str(emx),
                    "--hfss-geometry-summary",
                    str(geometry),
                    "--hfss-physical-summary",
                    str(hfss),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "validation_chain_decision_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "BLOCKED_BY_EMX_REFERENCE")
            self.assertEqual(summary["decision"], "DO_NOT_USE_HFSS_COMPARISON")
            stages = {stage["name"]: stage for stage in summary["stages"]}
            self.assertEqual(stages["EMX-first golden reference"]["status"], "FAIL")
            self.assertEqual(stages["HFSS geometry asset traceability"]["status"], "PASS_DIAGNOSTIC_ONLY")
            self.assertEqual(stages["HFSS physical S4P gate"]["status"], "PASS_DIAGNOSTIC_ONLY")
            self.assertEqual(
                stages["Accepted EMX-vs-HFSS/ADS comparison"]["status"],
                "BLOCKED_BY_EMX_REFERENCE",
            )
            self.assertIn("6/6 metrics fail", stages["EMX-first golden reference"]["finding"])
            report = (root / "out" / "validation_chain_decision_report.md").read_text(encoding="utf-8")
            self.assertIn("A diagnostic HFSS geometry or physical PASS cannot override", report)

    def test_waits_for_final_runner_after_emx_and_hfss_pass(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.json"
            geometry = root / "geometry.json"
            hfss = root / "hfss.json"
            _write_json(emx, _emx_summary(mod, accepted=True))
            _write_json(geometry, _geometry_summary(mod, accepted=True))
            _write_json(hfss, _hfss_summary(mod, accepted=True))

            status = mod.main(
                [
                    "--emx-first-summary",
                    str(emx),
                    "--hfss-geometry-summary",
                    str(geometry),
                    "--hfss-physical-summary",
                    str(hfss),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "validation_chain_decision_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            self.assertEqual(summary["decision"], "WAIT_FOR_ACCEPTED_COMPARISON")
            stages = {stage["name"]: stage for stage in summary["stages"]}
            self.assertEqual(stages["EMX-first golden reference"]["status"], "PASS")
            self.assertEqual(stages["HFSS geometry asset traceability"]["status"], "PASS")
            self.assertEqual(stages["HFSS physical S4P gate"]["status"], "PASS")
            self.assertEqual(stages["Accepted EMX-vs-HFSS/ADS comparison"]["status"], "MISSING")

    def test_blocks_final_comparison_when_geometry_traceability_fails(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.json"
            geometry = root / "geometry.json"
            hfss = root / "hfss.json"
            _write_json(emx, _emx_summary(mod, accepted=True))
            _write_json(geometry, _geometry_summary(mod, accepted=False))
            _write_json(hfss, _hfss_summary(mod, accepted=True))

            status = mod.main(
                [
                    "--emx-first-summary",
                    str(emx),
                    "--hfss-geometry-summary",
                    str(geometry),
                    "--hfss-physical-summary",
                    str(hfss),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "validation_chain_decision_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "BLOCKED_BY_HFSS_GEOMETRY_GATE")
            stages = {stage["name"]: stage for stage in summary["stages"]}
            self.assertEqual(stages["HFSS geometry asset traceability"]["status"], "FAIL")
            self.assertEqual(
                stages["Accepted EMX-vs-HFSS/ADS comparison"]["status"],
                "BLOCKED_BY_HFSS_GEOMETRY_GATE",
            )
            self.assertIn("missing STEP file", stages["HFSS geometry asset traceability"]["finding"])

    def test_accepts_full_chain_only_when_final_runner_passes_core_metric_gate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.json"
            geometry = root / "geometry.json"
            hfss = root / "hfss.json"
            accepted = root / "accepted.json"
            _write_json(emx, _emx_summary(mod, accepted=True))
            _write_json(geometry, _geometry_summary(mod, accepted=True))
            _write_json(hfss, _hfss_summary(mod, accepted=True))
            _write_json(accepted, _accepted_summary(mod, accepted=True, max_error=4.9))

            status = mod.main(
                [
                    "--emx-first-summary",
                    str(emx),
                    "--hfss-geometry-summary",
                    str(geometry),
                    "--hfss-physical-summary",
                    str(hfss),
                    "--accepted-validation-summary",
                    str(accepted),
                    "--out-dir",
                    str(root / "out"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "validation_chain_decision_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN")

    def test_rejects_final_runner_when_any_core_metric_exceeds_five_percent(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx = root / "emx.json"
            geometry = root / "geometry.json"
            hfss = root / "hfss.json"
            accepted = root / "accepted.json"
            _write_json(emx, _emx_summary(mod, accepted=True))
            _write_json(geometry, _geometry_summary(mod, accepted=True))
            _write_json(hfss, _hfss_summary(mod, accepted=True))
            _write_json(accepted, _accepted_summary(mod, accepted=False))

            status = mod.main(
                [
                    "--emx-first-summary",
                    str(emx),
                    "--hfss-geometry-summary",
                    str(geometry),
                    "--hfss-physical-summary",
                    str(hfss),
                    "--accepted-validation-summary",
                    str(accepted),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "validation_chain_decision_summary.json").read_text())
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            stages = {stage["name"]: stage for stage in summary["stages"]}
            self.assertEqual(stages["Accepted EMX-vs-HFSS/ADS comparison"]["status"], "FAIL")
            self.assertIn("metric_k_max_percent_error", stages["Accepted EMX-vs-HFSS/ADS comparison"]["finding"])
