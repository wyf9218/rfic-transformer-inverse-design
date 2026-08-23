from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_selfcheck_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_package_selfcheck_compare.py"
    spec = importlib.util.spec_from_file_location("run_package_selfcheck_compare_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fake_compare(path: Path, *, status: str = "PASS") -> None:
    path.write_text(
        f"""
import argparse, json, pathlib, sys
p=argparse.ArgumentParser()
p.add_argument('--emx')
p.add_argument('--hfss')
p.add_argument('--out-dir')
p.add_argument('--emx-port-pairs')
p.add_argument('--hfss-port-pairs')
p.add_argument('--compare-start-ghz')
p.add_argument('--compare-stop-ghz')
p.add_argument('--min-frequency-points')
p.add_argument('--expected-frequency-step-ghz')
p.add_argument('--expected-frequency-points')
p.add_argument('--frequency-tolerance-hz')
p.add_argument('--require-matching-frequency-grid', action='store_true')
p.add_argument('--max-percent-error')
p.add_argument('--plot', action='store_true')
a=p.parse_args()
out=pathlib.Path(a.out_dir)
out.mkdir(parents=True, exist_ok=True)
json.dump({{
  'overall_status': {status!r},
  'frequency_window_hz': {{'min': 13.5e9, 'max': 16.5e9, 'count': 9}},
  'metrics': {{
    'k': {{'status': {status!r}, 'max_percent_error': 4.5 if {status!r} == 'PASS' else 5.5}},
    'qp': {{'status': 'PASS', 'max_percent_error': 4.0}},
    'qs': {{'status': 'PASS', 'max_percent_error': 4.0}},
    'lp_nh': {{'status': 'PASS', 'max_percent_error': 0.5}},
    'ls_nh': {{'status': 'PASS', 'max_percent_error': 4.2}}
  }}
}}, open(out/'emx_hfss_ads_comparison_summary.json', 'w'))
(out/'emx_hfss_ads_comparison_report.md').write_text('# report\\n')
print('fake compare complete')
sys.exit(0 if {status!r} == 'PASS' else 2)
""",
        encoding="utf-8",
    )


class RunPackageSelfcheckCompareScriptTest(TransformerToolboxTestBase):
    def test_selfcheck_passes_and_writes_wrapper_summary(self) -> None:
        selfcheck = _load_selfcheck_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "package"
            package.mkdir()
            compare = root / "fake_compare.py"
            _write_fake_compare(compare)

            status = selfcheck.main(
                [
                    "--package-dir",
                    str(package),
                    "--compare-script",
                    str(compare),
                ]
            )

            self.assertEqual(status, 0)
            out_dir = package / "package_selfcheck_compare_window_20260613"
            wrapper = json.loads((out_dir / "package_selfcheck_compare_run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(wrapper["overall_status"], "PASS")
            self.assertEqual(wrapper["scope"], "NARROWBAND_PACKAGE_SELF_CONSISTENCY_ONLY")
            self.assertEqual(wrapper["decision"], "NOT_A_GOLDEN_EMX_REFERENCE_GATE")
            self.assertEqual(wrapper["evidence_use"], "NOT_FINAL_LP_LS_Q_K_EVIDENCE")
            self.assertIn("must not be cited as EMX-first approval", " ".join(wrapper["limitations"]))
            self.assertIn("must not be used as final Lp/Ls/Q/K evidence", " ".join(wrapper["limitations"]))
            self.assertTrue((out_dir / "emx_hfss_ads_comparison_summary.json").exists())

    def test_selfcheck_fails_when_compare_fails(self) -> None:
        selfcheck = _load_selfcheck_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "package"
            package.mkdir()
            compare = root / "fake_compare.py"
            _write_fake_compare(compare, status="FAIL")

            status = selfcheck.main(
                [
                    "--package-dir",
                    str(package),
                    "--compare-script",
                    str(compare),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            out_dir = package / "package_selfcheck_compare_window_20260613"
            wrapper = json.loads((out_dir / "package_selfcheck_compare_run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(wrapper["overall_status"], "FAIL")
