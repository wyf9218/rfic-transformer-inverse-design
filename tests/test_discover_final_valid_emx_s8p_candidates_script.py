from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys

from rfic_transformer_inverse_design.sim.base import SParameterResult
from tests.test_audit_selected_power_line_8port_layout_samples_script import _write_layout_evidence


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "discover_final_valid_emx_s8p_candidates.py"
    spec = importlib.util.spec_from_file_location("discover_final_valid_emx_s8p_candidates_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_flat_s8p(path: Path, *, points: int = 111) -> None:
    freqs = np.linspace(5.0e9, 60.0e9, points)
    s_matrix = np.zeros((len(freqs), 8, 8), dtype=np.complex128)
    SParameterResult(freqs_hz=freqs, s_matrix=s_matrix, reference_impedance_ohm=50.0).to_touchstone(path)


class DiscoverFinalValidEmxS8pCandidatesScriptTest(TransformerToolboxTestBase):
    def test_discovers_final_valid_real_emx_candidate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = root / "evaluations" / "eval_good"
            _write_layout_evidence(eval_dir / "layout")
            _write_flat_s8p(eval_dir / "emx" / "emx.s8p")

            status = mod.main(["--search-root", str(root), "--out-dir", str(root / "discovery")])

            self.assertEqual(status, 0)
            summary = json.loads(
                (root / "discovery" / "final_valid_emx_s8p_candidate_discovery_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["final_valid_count"], 1)
            self.assertEqual(summary["results"][0]["final_validation_candidate_status"], "PASS")

    def test_rejects_legacy_layout_even_when_s8p_contract_passes(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = root / "evaluations" / "eval_legacy"
            _write_layout_evidence(eval_dir / "layout", legacy_main_footprints=True)
            _write_flat_s8p(eval_dir / "emx" / "emx.s8p")

            status = mod.main(["--search-root", str(root), "--out-dir", str(root / "discovery")])

            self.assertEqual(status, 2)
            summary = json.loads(
                (root / "discovery" / "final_valid_emx_s8p_candidate_discovery_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["touchstone_contract_pass_count"], 1)
            self.assertEqual(summary["layout_audit_pass_count"], 0)
            self.assertEqual(summary["results"][0]["layout_audit_status"], "FAIL")

    def test_rejects_wrong_frequency_grid(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eval_dir = root / "evaluations" / "eval_wrong_grid"
            _write_layout_evidence(eval_dir / "layout")
            _write_flat_s8p(eval_dir / "emx" / "emx.s8p", points=101)

            status = mod.main(["--search-root", str(root), "--out-dir", str(root / "discovery")])

            self.assertEqual(status, 2)
            summary = json.loads(
                (root / "discovery" / "final_valid_emx_s8p_candidate_discovery_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["touchstone_contract_pass_count"], 0)
            self.assertEqual(summary["layout_audit_pass_count"], 1)
            self.assertEqual(summary["final_valid_count"], 0)
