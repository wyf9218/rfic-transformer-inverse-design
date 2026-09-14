import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from rfic_transformer_inverse_design.sim.base import SParameterResult
from rfic_transformer_inverse_design.campaigns import broadband56_s4p_qa as qa
from research.broadband56_nn.historical_s4p_reextract import derive, EvidenceError


class HistoricalGridTests(unittest.TestCase):
    def fixture(self, n, cross=False):
        f = np.linspace(5e9, 60e9, n) if n > 1 else np.array([15e9])
        z = np.empty((n, 4, 4), complex)
        for i, freq in enumerate(f):
            x = 2*np.pi*freq*1e-9
            if cross:
                x *= 1-freq/20e9
            z[i] = np.eye(4) * (5+1j*x)
            z[i, 0, 3] = z[i, 3, 0] = .2j*x
        return SParameterResult(f, qa.z_to_s(z, z0=50.))

    def test_56_agrees_with_existing_extractor(self):
        ts = self.fixture(56)
        with TemporaryDirectory() as tmp:
            p = Path(tmp)/"sample.s4p"
            ts.to_touchstone(p)
            legacy = derive(qa.load_touchstone(p))
            native = qa.audit_exact56_s4p(p)
            row = native.rows[10]
            for name in ("lp_nh", "ls_nh", "qp", "qs", "signed_k", "k_abs"):
                self.assertAlmostEqual(float(row[name]), legacy["physical15"][name])
            self.assertEqual(str(row["strict_lumped_valid"]).lower(), str(legacy["strict_valid"]).lower())

    def test_111_exact_point_and_srf(self):
        out = derive(self.fixture(111))
        self.assertEqual(out["frequency_count"],111)
        self.assertTrue(out["strict_valid"])
        self.assertEqual(out["primary_srf"]["lower_bound_hz"],60e9)

    def test_single_point_is_missing_evidence_not_strict_fail(self):
        with self.assertRaisesRegex(EvidenceError,"FREQUENCY_EVIDENCE_INSUFFICIENT"):
            derive(self.fixture(1))

    def test_observed_srf_failure(self):
        self.assertFalse(derive(self.fixture(111, cross=True))["strict_valid"])


if __name__ == "__main__":
    unittest.main()
