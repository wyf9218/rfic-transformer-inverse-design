from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import subprocess
import sys


def _load_health_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_local_project_health_check.py"
    spec = importlib.util.spec_from_file_location("run_local_project_health_check_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class RunLocalProjectHealthCheckScriptTest(TransformerToolboxTestBase):
    def test_health_check_accepts_incomplete_acceptance_matrix_boundary(self) -> None:
        health = _load_health_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            project = root / "project"
            package = root / "package"
            handoff = project / "mars_handoff_bundle_20260613"
            for directory in (repo / "scripts", project, package, handoff):
                directory.mkdir(parents=True, exist_ok=True)
            self._write_fake_helpers(repo)

            status = health.main(
                [
                    "--repo-root",
                    str(repo),
                    "--project-root",
                    str(project),
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(root / "package.zip"),
                    "--zip-sha-record",
                    str(root / "package.zip.sha256"),
                    "--handoff-root",
                    str(handoff),
                    "--out-dir",
                    str(root / "health"),
                    "--python",
                    sys.executable,
                    "--rebuild-delivery-zip",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "health" / "local_project_health_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            by_name = {step["name"]: step for step in summary["steps"]}
            self.assertEqual(by_name["package narrowband selfcheck compare"]["status"], "PASS")
            self.assertIn("not an EMX golden-reference gate", by_name["package narrowband selfcheck compare"]["detail"])
            self.assertEqual(by_name["MARS handoff bundle rebuild"]["status"], "PASS")
            self.assertEqual(by_name["EMX/HFSS/ADS validation-chain decision"]["status"], "PASS")
            self.assertIn(
                "HFSS comparison is correctly blocked",
                by_name["EMX/HFSS/ADS validation-chain decision"]["detail"],
            )
            self.assertEqual(by_name["ADS metric formula consistency"]["status"], "PASS")
            self.assertIn("synthetic formula audit only", by_name["ADS metric formula consistency"]["detail"])
            self.assertEqual(by_name["HFSS model geometry asset audit"]["status"], "PASS")
            self.assertIn("geometry assets are inspectable only", by_name["HFSS model geometry asset audit"]["detail"])
            self.assertEqual(by_name["clean delivery zip build"]["status"], "PASS")
            self.assertEqual(by_name["delivery package audit"]["status"], "PASS")
            self.assertEqual(by_name["MARS handoff install verifier"]["status"], "PASS")
            self.assertEqual(by_name["project acceptance matrix pre-sync"]["status"], "PASS")
            self.assertEqual(by_name["project acceptance matrix post-audit"]["status"], "PASS")
            self.assertEqual(by_name["final clean delivery zip build"]["status"], "PASS")
            self.assertEqual(by_name["final delivery package audit"]["status"], "PASS")
            self.assertEqual(by_name["acceptance matrix boundary"]["status"], "PASS")
            self.assertEqual(by_name["MARS next-action packet"]["status"], "PASS")
            self.assertIn(
                "READY_FOR_MARS_TARGET_EMX_RERUN",
                by_name["MARS next-action packet"]["detail"],
            )
            self.assertEqual(by_name["MARS target EMX return watcher"]["status"], "PASS")
            self.assertIn("WAITING_FOR_MARS_RETURN", by_name["MARS target EMX return watcher"]["detail"])
            self.assertIn("evidence_use=NOT_ACCEPTED_EMX_REFERENCE", by_name["MARS target EMX return watcher"]["detail"])
            self.assertIn("accepted_emx_reference=False", by_name["MARS target EMX return watcher"]["detail"])
            self.assertIn("not an accepted EMX reference", by_name["MARS target EMX return watcher"]["detail"])
            self.assertIn("watcher records local pull state only", by_name["MARS target EMX return watcher"]["detail"])
            step_names = [step["name"] for step in summary["steps"]]
            self.assertLess(step_names.index("package narrowband selfcheck compare"), step_names.index("MARS handoff bundle rebuild"))
            self.assertLess(
                step_names.index("MARS handoff bundle rebuild"),
                step_names.index("EMX/HFSS/ADS validation-chain decision"),
            )
            self.assertLess(
                step_names.index("EMX/HFSS/ADS validation-chain decision"),
                step_names.index("ADS metric formula consistency"),
            )
            self.assertLess(
                step_names.index("ADS metric formula consistency"),
                step_names.index("HFSS model geometry asset audit"),
            )
            self.assertLess(
                step_names.index("HFSS model geometry asset audit"),
                step_names.index("project acceptance matrix pre-sync"),
            )
            self.assertLess(step_names.index("project acceptance matrix pre-sync"), step_names.index("clean delivery zip build"))
            self.assertLess(step_names.index("MARS handoff bundle rebuild"), step_names.index("MARS handoff install verifier"))
            self.assertLess(step_names.index("MARS handoff install verifier"), step_names.index("MARS next-action packet"))
            self.assertLess(step_names.index("MARS next-action packet"), step_names.index("MARS target EMX return watcher"))
            self.assertLess(step_names.index("MARS target EMX return watcher"), step_names.index("project acceptance matrix post-audit"))
            self.assertLess(step_names.index("project acceptance matrix post-audit"), step_names.index("final clean delivery zip build"))
            self.assertLess(step_names.index("final clean delivery zip build"), step_names.index("final delivery package audit"))
            self.assertTrue((project / "acceptance_matrix_20260613.json").exists())
            self.assertTrue((project / "ACCEPTANCE_MATRIX_20260613_CN.md").exists())
            self.assertTrue(
                (project / "validation_chain_decision_20260614" / "validation_chain_decision_summary.json").exists()
            )
            self.assertTrue((project / "mars_handoff_bundle_20260613.tar.gz").exists())
            self.assertTrue((project / "mars_handoff_bundle_20260613.tar.gz.sha256").exists())
            self.assertTrue((project / "mars_handoff_verify_20260613_latest" / "mars_handoff_verify_summary.json").exists())
            self.assertTrue((project / "mars_next_action_packet_20260614" / "mars_next_action_packet_summary.json").exists())
            self.assertTrue(
                (
                    project
                    / "hfss_validation"
                    / "final500_ec6698dfc575950b"
                    / "mars_emx_return_watch_20260614"
                    / "mars_emx_return_watch_summary.json"
                ).exists()
            )
            self.assertIn("INCOMPLETE", by_name["acceptance matrix boundary"]["detail"])
            self.assertTrue((root / "health" / "local_project_health_report.md").exists())

    def test_health_check_fails_when_delivery_audit_fails(self) -> None:
        health = _load_health_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            project = root / "project"
            package = root / "package"
            handoff = project / "mars_handoff_bundle_20260613"
            for directory in (repo / "scripts", project, package, handoff):
                directory.mkdir(parents=True, exist_ok=True)
            self._write_fake_helpers(repo, delivery_status="FAIL")

            status = health.main(
                [
                    "--repo-root",
                    str(repo),
                    "--project-root",
                    str(project),
                    "--package-dir",
                    str(package),
                    "--zip-path",
                    str(root / "package.zip"),
                    "--zip-sha-record",
                    str(root / "package.zip.sha256"),
                    "--handoff-root",
                    str(handoff),
                    "--out-dir",
                    str(root / "health"),
                    "--python",
                    sys.executable,
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "health" / "local_project_health_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            by_name = {step["name"]: step for step in summary["steps"]}
            self.assertEqual(by_name["EMX/HFSS/ADS validation-chain decision"]["status"], "PASS")
            self.assertEqual(by_name["ADS metric formula consistency"]["status"], "PASS")
            self.assertEqual(by_name["MARS target EMX return watcher"]["status"], "PASS")
            self.assertEqual(by_name["delivery package audit"]["status"], "FAIL")

    def test_run_tests_gate_uses_full_pytest_with_optional_skip_boundary(self) -> None:
        health = _load_health_module()
        completed = subprocess.CompletedProcess(
            args=["python", "-m", "pytest", "-q"],
            returncode=0,
            stdout="449 passed, 52 skipped in 24.92s\n",
            stderr="",
        )

        with mock.patch.object(health, "_run", return_value=completed) as run_mock:
            result = health._run_core_tests(repo_root=Path("/repo"), python="python")

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.name, "full local pytest suite")
        self.assertEqual(result.command, ["python", "-m", "pytest", "-q"])
        self.assertNotIn("--ignore=tests/test_transformer_gui_qt.py", result.command)
        self.assertNotIn("--ignore=tests/test_transformer_optimizer.py", result.command)
        self.assertIn("449 passed, 52 skipped", result.detail)
        self.assertIn("optional extras are represented as pytest skips", result.detail)
        run_mock.assert_called_once()

    def _write_fake_helpers(self, repo: Path, *, delivery_status: str = "PASS") -> None:
        _write_script(
            repo / "scripts" / "run_package_selfcheck_compare.py",
            """
import argparse, json, pathlib
p=argparse.ArgumentParser()
p.add_argument('--package-dir')
a=p.parse_args()
out=pathlib.Path(a.package_dir)/'package_selfcheck_compare_window_20260613'
out.mkdir(parents=True, exist_ok=True)
json.dump({
  'overall_status':'PASS',
  'frequency_window_hz': {'min': 13.5e9, 'max': 16.5e9, 'count': 9},
  'metrics': {'k': {'status':'PASS', 'max_percent_error': 4.76}}
}, open(out/'emx_hfss_ads_comparison_summary.json', 'w'))
(out/'emx_hfss_ads_comparison_report.md').write_text('# compare\\n')
print('compare ok')
""",
        )
        _write_script(
            repo / "scripts" / "build_clean_delivery_zip.py",
            """
import argparse, json
p=argparse.ArgumentParser()
p.add_argument('--package-dir')
p.add_argument('--zip-path')
p.add_argument('--zip-sha-record')
p.add_argument('--out-json')
a=p.parse_args()
json.dump({'package_file_count': 3, 'zip_entry_count': 4, 'metadata_entry_count': 0}, open(a.out_json, 'w'))
print('zip ok')
""",
        )
        _write_script(
            repo / "scripts" / "build_mars_handoff_bundle.py",
            """
import argparse, json, pathlib
p=argparse.ArgumentParser()
p.add_argument('--repo-root')
p.add_argument('--project-root')
p.add_argument('--out')
p.add_argument('--staging-dir')
p.add_argument('--force', action='store_true')
a=p.parse_args()
staging=pathlib.Path(a.staging_dir)
staging.mkdir(parents=True, exist_ok=True)
json.dump({'file_count': 5, 'files': []}, open(staging/'MARS_HANDOFF_INVENTORY_20260613.json', 'w'))
(staging/'SHA256SUMS.txt').write_text('fake  file\\n')
tar=pathlib.Path(a.out)
tar.parent.mkdir(parents=True, exist_ok=True)
tar.write_bytes(b'tar')
tar.with_suffix(tar.suffix + '.sha256').write_text('abc123  '+tar.name+'\\n')
print('handoff ok')
""",
        )
        _write_script(
            repo / "scripts" / "build_validation_chain_decision_card.py",
            """
import argparse, json, pathlib
p=argparse.ArgumentParser()
p.add_argument('--emx-first-summary')
p.add_argument('--hfss-geometry-summary')
p.add_argument('--hfss-physical-summary')
p.add_argument('--accepted-validation-summary')
p.add_argument('--out-dir')
p.add_argument('--no-fail-exit', action='store_true')
a=p.parse_args()
out=pathlib.Path(a.out_dir)
out.mkdir(parents=True, exist_ok=True)
payload={
  'overall_status':'BLOCKED_BY_EMX_REFERENCE',
  'decision':'DO_NOT_USE_HFSS_COMPARISON',
  'stages':[
    {'name':'EMX-first golden reference','status':'FAIL','decision':'BLOCK_HFSS_COMPARISON'},
    {'name':'HFSS geometry asset traceability','status':'PASS_DIAGNOSTIC_ONLY','decision':'DO_NOT_USE_UNTIL_EMX_ACCEPTED'},
    {'name':'HFSS physical S4P gate','status':'PASS_DIAGNOSTIC_ONLY','decision':'DO_NOT_COMPARE_UNTIL_EMX_ACCEPTED'},
    {'name':'Accepted EMX-vs-HFSS/ADS comparison','status':'BLOCKED_BY_EMX_REFERENCE','decision':'DO_NOT_USE_HFSS_COMPARISON'}
  ],
}
json.dump(payload, open(out/'validation_chain_decision_summary.json', 'w'))
(out/'validation_chain_decision_report.md').write_text('# chain\\n')
print('chain ok')
""",
        )
        _write_script(
            repo / "scripts" / "audit_delivery_package.py",
            f"""
import argparse, json, pathlib, sys
p=argparse.ArgumentParser()
p.add_argument('--package-dir')
p.add_argument('--zip-path')
p.add_argument('--zip-sha-record')
p.add_argument('--out-dir')
a=p.parse_args()
out=pathlib.Path(a.out_dir)
out.mkdir(parents=True, exist_ok=True)
status={delivery_status!r}
checks=[{{'name':'package SHA manifest','status':status,'detail':'ok' if status=='PASS' else 'bad manifest'}}]
json.dump({{'overall_status':status,'checks':checks}}, open(out/'delivery_package_audit_summary.json', 'w'))
(out/'delivery_package_audit_report.md').write_text('# audit\\n')
sys.exit(0 if status=='PASS' else 2)
""",
        )
        _write_script(
            repo / "scripts" / "audit_hfss_model_geometry_assets.py",
            """
import argparse, json, pathlib
p=argparse.ArgumentParser()
p.add_argument('--package-dir')
p.add_argument('--out-dir')
a=p.parse_args()
out=pathlib.Path(a.out_dir)
out.mkdir(parents=True, exist_ok=True)
json.dump({
  'overall_status':'PASS',
  'decision':'ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS',
  'checks':[
    {'name':'HFSS top-view PNG','status':'PASS','detail':'fixture'},
    {'name':'HFSS isometric-view PNG','status':'PASS','detail':'fixture'},
    {'name':'HFSS geometry-quality PNG','status':'PASS','detail':'fixture'},
    {'name':'HFSS STEP model','status':'PASS','detail':'fixture'}
  ]
}, open(out/'hfss_model_geometry_asset_audit_summary.json', 'w'))
(out/'hfss_model_geometry_asset_audit_report.md').write_text('# geometry assets\\n')
print('geometry asset audit ok')
""",
        )
        _write_script(
            repo / "scripts" / "audit_ads_metric_formula_consistency.py",
            """
import argparse, json, pathlib
p=argparse.ArgumentParser()
p.add_argument('--out-dir')
a=p.parse_args()
out=pathlib.Path(a.out_dir)
out.mkdir(parents=True, exist_ok=True)
json.dump({
  'overall_status':'PASS',
  'decision':'ADS_FORMULA_IMPLEMENTATION_ACCEPTED',
  'metric_recovery_errors': {'qp': {'max_percent_error': 1e-12}},
  'checks':[{'name':'known transformer metric recovery','status':'PASS','detail':'fixture'}]
}, open(out/'ads_metric_formula_consistency_summary.json', 'w'))
(out/'ads_metric_formula_consistency_report.md').write_text('# formula\\n')
print('formula audit ok')
""",
        )
        _write_script(
            repo / "scripts" / "verify_mars_handoff_install.py",
            """
import argparse, json, pathlib
p=argparse.ArgumentParser()
p.add_argument('handoff_root')
p.add_argument('--out-dir')
a=p.parse_args()
out=pathlib.Path(a.out_dir)
out.mkdir(parents=True, exist_ok=True)
json.dump({'overall_status':'PASS','checks':[{'name':'required handoff files','status':'PASS','detail':'ok'}]}, open(out/'mars_handoff_verify_summary.json', 'w'))
(out/'mars_handoff_verify_report.md').write_text('# verify\\n')
""",
        )
        _write_script(
            repo / "scripts" / "build_mars_next_action_packet.py",
            """
import argparse, json, pathlib
p=argparse.ArgumentParser()
p.add_argument('--project-root')
p.add_argument('--package-dir')
p.add_argument('--out-dir')
p.add_argument('--no-fail-exit', action='store_true')
a=p.parse_args()
out=pathlib.Path(a.out_dir)
out.mkdir(parents=True, exist_ok=True)
json.dump({
  'overall_status':'PASS',
  'decision':'READY_FOR_MARS_TARGET_EMX_RERUN',
  'status_counts':{'PASS':6},
  'checks':[{'name':'validation-chain blocks HFSS comparison','status':'PASS','detail':'ok'}]
}, open(out/'mars_next_action_packet_summary.json', 'w'))
(out/'MARS_NEXT_ACTION_PACKET_20260614_CN.md').write_text('# next action\\n')
print('next action ok')
""",
        )
        _write_script(
            repo / "scripts" / "watch_mars_emx_return.py",
            """
import argparse, json, pathlib
p=argparse.ArgumentParser()
p.add_argument('--out-dir')
p.add_argument('--interval-sec')
p.add_argument('--max-iterations')
p.add_argument('--no-fail-exit', action='store_true')
a=p.parse_args()
out=pathlib.Path(a.out_dir)
out.mkdir(parents=True, exist_ok=True)
summary={
  'overall_status':'WAITING_FOR_MARS_RETURN',
  'decision':'WAIT_FOR_MARS_WIDEBAND_EMX_RETURN',
  'evidence_use':'NOT_ACCEPTED_EMX_REFERENCE',
  'accepted_emx_reference':False,
  'hfss_comparison_allowed':False,
  's4p_candidate_count':0,
  'tarball_candidate_count':0,
  'selected_emx_s4p':None,
  'selected_tarball':None,
  'verifier_decision':None,
  'next_required_action':'WAIT_FOR_AND_IMPORT_MARS_WIDEBAND_EMX_RETURN',
  'iteration_count':1,
  'latest_snapshot':{'s4p_candidate_count':0,'tarball_candidate_count':0},
}
json.dump(summary, open(out/'mars_emx_return_watch_summary.json', 'w'))
(out/'mars_emx_return_watch_history.csv').write_text('overall_status\\nWAITING_FOR_MARS_RETURN\\n')
print('watch waiting')
""",
        )
        _write_script(
            repo / "scripts" / "build_acceptance_matrix.py",
            """
import argparse, json, pathlib, sys
p=argparse.ArgumentParser()
p.add_argument('--project-root')
p.add_argument('--package-dir')
p.add_argument('--out-json')
p.add_argument('--out-md')
a=p.parse_args()
pathlib.Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
json.dump({'overall_status':'INCOMPLETE','status_counts':{'PASS':1,'PENDING':1},'items':[]}, open(a.out_json, 'w'))
pathlib.Path(a.out_md).write_text('# matrix\\n')
sys.exit(2)
""",
        )
