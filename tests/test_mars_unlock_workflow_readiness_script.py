from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_readiness_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "reports"
        / "current_validation_status_20260614"
        / "verify_mars_unlock_workflow_readiness.py"
    )
    spec = importlib.util.spec_from_file_location("mars_unlock_workflow_readiness_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_script(path: Path, text: str = "#!/usr/bin/env bash\nset -euo pipefail\necho ok\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class MarsUnlockWorkflowReadinessScriptTest(TransformerToolboxTestBase):
    def test_current_workflow_is_ready_in_real_workspace(self) -> None:
        mod = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report"
            report.mkdir(parents=True)
            (report / "objective_evidence_audit_20260615.json").write_text(
                json.dumps({"final_objective_ready": False}, indent=2),
                encoding="utf-8",
            )
            status = mod.main(["--report-dir", str(report)])

            self.assertEqual(status, 0)
            result = json.loads((report / "mars_unlock_workflow_readiness_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "MARS_UNLOCK_WORKFLOW_READY")
            self.assertTrue(result["strict_checks_pass"])
            self.assertEqual(len(result["workflow_steps"]), 6)
            self.assertTrue((report / "mars_unlock_workflow_readiness.html").is_file())

    def test_missing_script_marks_not_ready(self) -> None:
        mod = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = root / "report"
            good = _write_script(root / "good.sh")
            missing = root / "missing.sh"
            old_steps = mod.WORKFLOW_STEPS
            old_support = mod.SUPPORT_FILES
            try:
                mod.WORKFLOW_STEPS = [
                    {
                        "id": "good",
                        "phase": "test",
                        "script": good,
                        "command": "run good",
                        "expected_output": "ok",
                        "evidence_gate": "test",
                        "purpose": "test",
                    },
                    {
                        "id": "missing",
                        "phase": "test",
                        "script": missing,
                        "command": "run missing",
                        "expected_output": "missing",
                        "evidence_gate": "test",
                        "purpose": "test",
                    },
                ]
                mod.SUPPORT_FILES = [good]

                status = mod.main(["--report-dir", str(report), "--no-fail-exit"])
            finally:
                mod.WORKFLOW_STEPS = old_steps
                mod.SUPPORT_FILES = old_support

            self.assertEqual(status, 0)
            result = json.loads((report / "mars_unlock_workflow_readiness_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "MARS_UNLOCK_WORKFLOW_NOT_READY")
            failed = {check["name"] for check in result["checks"] if not check["pass"]}
            self.assertIn("missing:script_exists", failed)

    def test_shell_syntax_failure_marks_not_ready(self) -> None:
        mod = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = root / "report"
            bad = _write_script(root / "bad.sh", "#!/usr/bin/env bash\nif true; then\n  echo bad\n")
            old_steps = mod.WORKFLOW_STEPS
            old_support = mod.SUPPORT_FILES
            try:
                mod.WORKFLOW_STEPS = [
                    {
                        "id": "bad",
                        "phase": "test",
                        "script": bad,
                        "command": "run bad",
                        "expected_output": "bad",
                        "evidence_gate": "test",
                        "purpose": "test",
                    }
                ]
                mod.SUPPORT_FILES = [bad]

                status = mod.main(["--report-dir", str(report), "--no-fail-exit"])
            finally:
                mod.WORKFLOW_STEPS = old_steps
                mod.SUPPORT_FILES = old_support

            self.assertEqual(status, 0)
            result = json.loads((report / "mars_unlock_workflow_readiness_20260615.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "MARS_UNLOCK_WORKFLOW_NOT_READY")
            failed = {check["name"] for check in result["checks"] if not check["pass"]}
            self.assertIn("bad:bash_syntax", failed)
