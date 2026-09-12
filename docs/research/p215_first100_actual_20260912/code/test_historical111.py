"""Three new synthetic111 tests, not historical labels or physical qualification."""
import csv
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import numpy as np
import extract_historical111 as h

ROOT=Path(__file__).parents[4]
REPO=ROOT/'github_worktrees/eucap15-mlp-capacity-20260909'
h.configure_repository(REPO)
from rfic_transformer_inverse_design.network_analysis import z_to_s
from rfic_transformer_inverse_design.sim.base import SParameterResult
from rfic_transformer_inverse_design.campaigns import broadband56_s4p_qa as original


def toy(path,frequencies=None,srf_hz=None):
    frequencies=np.asarray(h.FREQUENCY_GRID_HZ if frequencies is None else frequencies,dtype=float)
    x=2*np.pi*frequencies*.5e-9
    if srf_hz is not None:x=x*(1-(frequencies/srf_hz)**2)
    z=np.zeros((len(frequencies),4,4),dtype=complex)
    for i in range(4):z[:,i,i]=1+1j*x
    SParameterResult(frequencies,z_to_s(z,z0=50.),50.).to_touchstone(path)
    return path


class Historical111Tests(unittest.TestCase):
    def test_all111_preserved_actual15_index20_and_not_qualified_output(self):
        with tempfile.TemporaryDirectory(prefix='historical111_SYNTHETIC_') as td:
            root=Path(td).resolve();source=toy(root/'SYNTHETIC_RL.s4p');out=root/'output'
            rp=h.run(SimpleNamespace(repo=REPO,s4p=source,s4p_sha256=h.pin(source)['sha256'],out=out))
            receipt=json.loads(Path(rp['path']).read_text());target=json.loads((out/'TARGET15.json').read_text())
            with (out/'historical_features_all111.csv').open() as f:rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),111)
            self.assertEqual([int(x['frequency_hz']) for x in rows],list(h.FREQUENCY_GRID_HZ))
            self.assertEqual(target['original_frequency_index_zero_based'],20)
            self.assertEqual(target['frequency_hz'],15_000_000_000)
            self.assertAlmostEqual(target['row']['lp_nh'],1.,places=10)
            self.assertTrue(target['current_definition_strict_lumped_valid'])
            self.assertEqual(receipt['formal_physical_qualification'],'UNKNOWN')
            self.assertFalse(receipt['fresh_emx_performed'])
            self.assertEqual(receipt['production_accepted_added'],0)
            self.assertEqual(receipt['summary']['primary_srf']['status'],'CENSORED_ABOVE_60_GHZ')
            self.assertEqual(len(original.FREQUENCY_GRID_HZ),56) # never modify original global
            self.assertEqual(receipt['summary']['frequency_step_hz'],500_000_000)
            self.assertFalse(receipt['frequency_interpolation_or_resampling'])

    def test_illegal_grid_count_or_changed_actual15_is_rejected_not_resampled(self):
        with tempfile.TemporaryDirectory(prefix='historical111_badgrid_SYNTHETIC_') as td:
            root=Path(td).resolve();normal=list(h.FREQUENCY_GRID_HZ)
            changed=list(normal);changed[20]+=100_000_000
            repeated=list(normal);repeated[20]=repeated[19]
            for number,freq in enumerate((normal[::2],normal[:-1],changed,repeated)):
                path=toy(root/f'SYNTHETIC_INVALID_{number}.s4p',freq)
                with self.subTest(number=number),self.assertRaises(h.Broadband56S4pQaError):
                    h.extract111_under_current_mapping(path)

    def test_full111_srf_evidence_controls_strict15_without_spectrum_interpolation(self):
        with tempfile.TemporaryDirectory(prefix='historical111_srf_SYNTHETIC_') as td:
            path=toy(Path(td).resolve()/'SYNTHETIC_RESONANT.s4p',srf_hz=20_250_000_000)
            result=h.extract111_under_current_mapping(path);target=h.target15_summary(result)
            self.assertEqual(len(result.rows),111)
            self.assertEqual(result.summary['primary_srf']['status'],'BRACKETED_20000000000_20500000000_HZ')
            self.assertEqual(target['row']['broadband_descriptor_valid'],'true')
            self.assertFalse(target['current_definition_strict_lumped_valid'])
            self.assertEqual(target['strict_failure_reasons'],['below_half_srf'])


if __name__=='__main__':unittest.main()
