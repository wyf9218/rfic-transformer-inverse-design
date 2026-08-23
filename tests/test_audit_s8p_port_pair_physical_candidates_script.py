from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_s8p_port_pair_physical_candidates.py"
    spec = importlib.util.spec_from_file_location("audit_s8p_port_pair_physical_candidates_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_synthetic_s8p_transformer(path: Path) -> None:
    target = default_target_spec("1t1t")
    freqs = np.linspace(5.0e9, 50.0e9, 451)
    diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
    z_diff = s_to_z(diff.s_matrix, z0=target.differential_reference_impedance_ohm)
    z8 = np.repeat(np.eye(8, dtype=np.complex128)[None, :, :] * 5.0, len(freqs), axis=0)
    transform = np.zeros((8, 2), dtype=np.complex128)
    transform[0, 0] = 1.0
    transform[3, 0] = -1.0
    transform[4, 1] = 1.0
    transform[5, 1] = -1.0
    z8 += 0.25 * np.einsum("ai,fij,bj->fab", transform, z_diff, transform)
    _write_touchstone(path, freqs, z_to_s(z8, z0=50.0))


class AuditS8pPortPairPhysicalCandidatesScriptTest(TransformerToolboxTestBase):
    def test_expected_power_line_port_pair_passes_and_writes_artifacts(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            s8p = root / "emx.s8p"
            _write_synthetic_s8p_transformer(s8p)

            status = mod.main(
                [
                    "--touchstone",
                    str(s8p),
                    "--out-dir",
                    str(root / "audit"),
                    "--candidate-port-pairs",
                    "1,4:5,6;1,2:7,8",
                    "--expected-port-pairs",
                    "1,4:5,6",
                    "--skip-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "s8p_port_pair_physical_candidate_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertTrue(summary["expected_port_pairs_all_pass"])
            records = {item["port_pairs"]: item for item in summary["records"]}
            self.assertEqual(records["1,4:5,6"]["status"], "PASS")
            self.assertGreater(float(records["1,4:5,6"]["lp_nh_target"]), 0.02)
            self.assertTrue((root / "audit" / "emx" / "1_4_5_6" / "metrics_by_frequency.csv").is_file())

    def test_wrong_expected_pair_requires_review_instead_of_silent_acceptance(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            s8p = root / "emx.s8p"
            _write_synthetic_s8p_transformer(s8p)

            status = mod.main(
                [
                    "--touchstone",
                    str(s8p),
                    "--out-dir",
                    str(root / "audit"),
                    "--candidate-port-pairs",
                    "1,4:5,6;2,3:7,8",
                    "--expected-port-pairs",
                    "2,3:7,8",
                    "--skip-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "s8p_port_pair_physical_candidate_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "REVIEW")
            self.assertFalse(summary["expected_port_pairs_all_pass"])
            self.assertEqual(summary["decision"], "REVIEW_S8P_PORT_PAIR_DIAGNOSTIC_BEFORE_HFSS_HANDOFF")

    def test_samples_csv_resolves_relative_touchstone_paths(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset = root / "dataset"
            emx_dir = dataset / "evaluations" / "eval001" / "emx"
            emx_dir.mkdir(parents=True)
            s8p = emx_dir / "emx.s8p"
            _write_synthetic_s8p_transformer(s8p)
            samples = root / "samples.csv"
            samples.write_text(
                "evaluation,touchstone_path\n"
                "eval001,evaluations/eval001/emx/emx.s8p\n",
                encoding="utf-8",
            )

            status = mod.main(
                [
                    "--samples-csv",
                    str(samples),
                    "--dataset-dir",
                    str(dataset),
                    "--out-dir",
                    str(root / "audit"),
                    "--candidate-port-pairs",
                    "1,4:5,6",
                    "--expected-port-pairs",
                    "1,4:5,6",
                    "--skip-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "audit" / "s8p_port_pair_physical_candidate_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["sample_count"], 1)
            self.assertEqual(summary["records"][0]["sample"], "eval001")
