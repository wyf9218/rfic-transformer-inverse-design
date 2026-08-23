from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_next_gen_s8p_handoff_index.py"
    spec = importlib.util.spec_from_file_location("build_next_gen_s8p_handoff_index_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_project_fixture(root: Path, *, audit_status: str = "PASS", run_doc_after_return: bool = True) -> None:
    upload = root / "next_gen_s8p_mars_start_current_upload_bundle_20260620.tar.gz"
    upload.write_bytes(b"current upload bundle")
    (root / f"{upload.name}.sha256").write_text(f"{_sha256(upload)}  {upload.name}\n", encoding="utf-8")

    _write_json(
        root / "outputs" / "mars_start_upload_bundle_audit_current" / "mars_start_upload_bundle_audit_summary.json",
        {"overall_status": audit_status, "decision": "MARS_START_UPLOAD_BUNDLE_READY" if audit_status == "PASS" else "DO_NOT_UPLOAD_MARS_START_BUNDLE"},
    )
    _write_json(
        root / "outputs" / "next_gen_s8p_objective_acceptance_aligned_current_20260620" / "next_gen_s8p_objective_acceptance_summary.json",
        {"overall_status": "WAITING", "decision": "DO_NOT_CLAIM_NEXT_GEN_S8P_OBJECTIVE_COMPLETE"},
    )

    for name in ("NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh", "NEXT_GEN_S8P_AFTER_MARS_RETURN_AUTOPROCESS_20260620.sh"):
        path = root / name
        path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")
        path.chmod(0o755)

    run_doc_tokens = ["NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh"]
    if run_doc_after_return:
        run_doc_tokens.append("NEXT_GEN_S8P_AFTER_MARS_RETURN_AUTOPROCESS_20260620.sh")
    (root / "NEXT_GEN_S8P_MARS_20260620_RUN_COMMANDS_CN.md").write_text("\n".join(run_doc_tokens), encoding="utf-8")


class BuildNextGenS8pHandoffIndexScriptTest(TransformerToolboxTestBase):
    def test_reports_current_upload_ready_without_claiming_objective_complete(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _make_project_fixture(root)

            status = mod.main(["--project-root", str(root), "--out-dir", str(root / "handoff")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "handoff" / "next_gen_s8p_handoff_index.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["ready_to_upload_to_mars"])
            self.assertFalse(summary["objective_complete"])
            self.assertEqual(summary["objective_status"], "WAITING")
            self.assertIn("Run the real 500-row / 8-worker EMX job on MARS.", summary["remaining_external_work"])

    def test_rejects_failing_upload_bundle_audit(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _make_project_fixture(root, audit_status="FAIL")

            status = mod.main(["--project-root", str(root), "--out-dir", str(root / "handoff")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "handoff" / "next_gen_s8p_handoff_index.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["ready_to_upload_to_mars"])
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["bundle audit passes"]["status"], "FAIL")

    def test_rejects_run_commands_missing_after_return_step(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _make_project_fixture(root, run_doc_after_return=False)

            status = mod.main(["--project-root", str(root), "--out-dir", str(root / "handoff")])

            self.assertEqual(status, 2)
            summary = json.loads((root / "handoff" / "next_gen_s8p_handoff_index.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["run commands document after-return"]["status"], "FAIL")
