"""Narrow regression of widened response domain, with no training or native work."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .eucap15_range_policy import (POLICY, LEGACY_POLICY, classify_physical15,
    make_range_policy, resolve_range_policy, train_coverage_bounds)
from .eucap15_split811 import build, make_policy, validate_training_split


def physical(lp=.2, ls=3.2, q=100.):
    return dict(lp_nh=lp, ls_nh=ls, qmin=q, qp=q, qs=q+1., k_abs=.6)


def make_fixture(root, *, legacy=False):
    fields=[f'g{i}' for i in range(10)]
    rows=[]
    for i in range(1,11):
        p=physical(.5+i*.1 if legacy else .2+(i-1)*.4,
                   .6+i*.1 if legacy else 2.5+(i-1)*.1,
                   10+i if legacy else 100+i)
        p['k_abs']=.2+i*.01
        rows.append(dict(sequence=i,geometry_sha256=f'{i:064x}',geometry=[i+j for j in range(10)],
            geometry_fields=fields,physical15=p,frequency_hz=15_000_000_000,
            identity_namespaces={},request_id=f'fixture_batch000023-GEOMETRY_DOE-{i:03}',
            source='GEOMETRY_DOE',split='test',model_used_for_proposal=False,
            origin_class='FRESH_EMX_QUALIFICATION',scientific_contract_fingerprint='fixture',record={}))
    source=root/'source.json';source.write_text(json.dumps(dict(members=rows)))
    contract=root/'contract.json';contract.write_text(json.dumps(dict(field_names=fields,lower=[0]*10,upper=[100]*10)))
    return source,contract


class RangePolicyTests(unittest.TestCase):
    def test_default_positive_unbounded_inductance_q_not_pool_filter(self):
        for p in (physical(),physical(lp=10000,ls=.001,q=-2)):
            result=classify_physical15(p)
            self.assertTrue(result['range_eligible'])
            self.assertEqual(result['strict_eligibility'],'NOT_DECIDED_BY_RANGE_HELPER')
        self.assertIsNone(make_range_policy()['lp_ls']['upper_nh'])

    def test_nonpositive_and_nonfinite_rejected(self):
        for key,value in [('lp_nh',0),('ls_nh',-1),('lp_nh',math.inf),('qmin',math.nan),('k_abs',True)]:
            p=physical();p[key]=value
            self.assertFalse(classify_physical15(p)['range_eligible'])

    def test_k_window_unchanged_inclusive(self):
        for k,expected in [(.2,True),(.85,True),(.199999,False),(.850001,False)]:
            p=physical();p['k_abs']=k
            self.assertEqual(classify_physical15(p)['range_eligible'],expected)

    def test_legacy_policy_exact_window_and_implicit_old_config(self):
        legacy=resolve_range_policy(None,legacy_if_missing=True)
        self.assertEqual(legacy['policy_id'],LEGACY_POLICY)
        self.assertFalse(classify_physical15(physical(),legacy)['range_eligible'])
        self.assertTrue(classify_physical15(physical(.5,2),legacy)['range_eligible'])
        changed=make_range_policy();changed['k_abs']['upper']=1
        with self.assertRaises(ValueError):resolve_range_policy(changed)

    def test_train_bounds_are_finite_observed_and_refuse_holdout(self):
        rows=[physical(),physical(4,5)];rows[1]['k_abs']=.8
        b=train_coverage_bounds(rows)
        self.assertEqual(b['lower'],[.2,3.2,.6]);self.assertEqual(b['upper'],[4,5,.8])
        self.assertEqual(b['intended_cells'],512)
        self.assertEqual(b['status'],'FINITE_TRAIN_GRID_READY')
        with self.assertRaisesRegex(ValueError,'validation/test'):
            train_coverage_bounds([dict(split='test',physical15=rows[0])])
        self.assertEqual(train_coverage_bounds([rows[0]])['status'],'DEGENERATE_TRAIN_AXES_NO_GRID')

    def test_actual_builder_and_training_validation_accept_widened_range(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,contract=make_fixture(root)
            policy=make_policy(8,23,target_total=10)
            result=build([source],root/'data',contract_path=contract,policy=policy)
            self.assertEqual(result['counts'],dict(train=8,validation=1,test=1))
            check=validate_training_split(root/'data',expected_range_policy=POLICY)
            self.assertEqual(check['physical_range_policy']['policy_id'],POLICY)
            norm=json.loads((root/'data/normalizer.json').read_text())
            bounds=json.loads((root/'data/TRAIN_COVERAGE_BOUNDS.json').read_text())
            self.assertAlmostEqual(norm['y_mean'][0],1.6)
            self.assertAlmostEqual(bounds['upper'][0],3.)
            self.assertEqual(bounds['train_rows'],8)

    def test_legacy_build_configuration_retains_original_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,contract=make_fixture(root,legacy=True)
            policy=make_policy(8,23,target_total=10);policy.pop('physical_range_policy')
            build([source],root/'legacy',contract_path=contract,policy=policy)
            check=validate_training_split(root/'legacy',expected_range_policy=LEGACY_POLICY)
            self.assertEqual(check['physical_range_policy']['policy_id'],LEGACY_POLICY)
            policy2=make_policy(8,23,target_total=10,physical_range_policy=LEGACY_POLICY)
            source2,contract2=make_fixture(root,legacy=False)
            with self.assertRaisesRegex(ValueError,'OUTSIDE_LEGACY_WINDOW'):
                build([source2],root/'rejected',contract_path=contract2,policy=policy2)

    def test_actual_training_entry_rejects_policy_mismatch_before_update(self):
        from .frequency_tandem import train_from_config
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,contract=make_fixture(root)
            build([source],root/'data',contract_path=contract,policy=make_policy(8,23,target_total=10))
            config=dict(schema='frequency_tandem_train.v1',data_root=str(root/'data'),
                physical_range_policy=LEGACY_POLICY,train=dict(role='forward',frequency_ghz=15,label_mode='STRICT_LUMPED'))
            (root/'config.json').write_text(json.dumps(config))
            with patch('research.broadband56_nn.frequency_tandem.train_bb00') as train:
                with self.assertRaisesRegex(ValueError,'range policy differs'):
                    train_from_config(root/'config.json',root/'training')
                train.assert_not_called()


if __name__=='__main__':unittest.main()
