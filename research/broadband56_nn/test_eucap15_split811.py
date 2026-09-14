import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from .eucap15_split811 import assignments, make_policy, build, validate_training_split, POLICY

def row(i, *, parent=None, source='GEOMETRY_DOE'):
    h=f'{i:064x}'
    return dict(sequence=i,geometry_sha256=h,geometry=[float(i+j) for j in range(10)],
        identity_namespaces={},request_id=f'fixture_batch000023-{source}-{i:03}',source=source,split='test',
        model_used_for_proposal=False,base_geometry_hash=parent,base_train_id=parent,
        origin_class='FRESH_EMX_QUALIFICATION')

class Split811Tests(unittest.TestCase):
    def test_pre_policy_exposed_never_new_test(self):
        m=assignments([row(i) for i in range(1,9)],make_policy(8,23,target_total=10))
        self.assertEqual(m['counts'],dict(train=8,validation=0,test=0))
        self.assertEqual(m['deficit'],dict(train=0,validation=1,test=1))

    def test_exact_quotas_without_geometry_frequency_split(self):
        m=assignments([row(i) for i in range(1,101)],make_policy(0,23,target_total=100))
        self.assertEqual(m['counts'],dict(train=80,validation=10,test=10))
        self.assertTrue(m['exact_target_complete'])

    def test_parent_child_do_not_cross(self):
        rows=[row(1),row(2,parent=f'{1:064x}',source='TRAIN_NEIGHBORHOOD')]
        m=assignments(rows,make_policy(1,23,target_total=10))
        self.assertEqual({r['split'] for r in m['rows']},{'train'})
        self.assertEqual(len({r['family'] for r in m['rows']}),1)

    def test_prior_family_merger_conflict_fails_closed(self):
        rows=[row(i) for i in range(1,11)];p=make_policy(0,23,target_total=10);old=assignments(rows,p)
        val=next(r for r in old['rows'] if r['split']=='validation')['geometry_sha256']
        child=row(11,parent=val,source='TRAIN_NEIGHBORHOOD')
        with self.assertRaisesRegex(ValueError,'HOLDOUT_FAMILY_EXPOSURE_CONFLICT'):
            assignments(rows+[child],p,old)

    def test_resume_is_deterministic_and_preserves(self):
        rows=[row(i) for i in range(1,11)];p=make_policy(0,23,target_total=20)
        a=assignments(rows,p);b=assignments(list(reversed(rows)),p,a)
        self.assertEqual(a['by_geometry_sha256'],b['by_geometry_sha256'])

    def test_used_feedback_cannot_be_holdout(self):
        r=row(1);r['used_for_sampling_feedback']=True
        self.assertEqual(assignments([r],make_policy(0,23,target_total=10))['counts']['train'],1)

    def test_resume_reserves_old_quota_before_new_family_extension(self):
        rows=[row(i) for i in range(1,9)];p=make_policy(8,23,target_total=10)
        old=assignments(rows,p);child=row(9,parent=f'{1:064x}',source='TRAIN_NEIGHBORHOOD')
        new=assignments(rows+[child],p,old)
        self.assertEqual(new['by_geometry_sha256'],old['by_geometry_sha256'])
        self.assertEqual(new['rows'][-1]['split'],'PENDING_QUOTA')

    def test_exact_duplicate_not_counted(self):
        a=row(1);b=row(2);b['geometry']=a['geometry'][:]
        m=assignments([a,b],make_policy(2,23,target_total=10))
        self.assertEqual(m['counts']['train'],1);self.assertEqual(m['pending']['DUPLICATE'],1)

    def test_new_policy_not_old_split_inheritance(self):
        r=row(1);r['split']='test'
        self.assertEqual(assignments([r],make_policy(1,23,target_total=10))['by_geometry_sha256'][r['geometry_sha256']],'train')

    def test_build_train_only_normalizer_and_no_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);fields=[f'g{i}' for i in range(10)];rows=[row(i) for i in range(1,11)]
            for i,r in enumerate(rows):
                r.update(geometry_fields=fields,physical15=dict(lp_nh=.5+i*.1,ls_nh=.6+i*.1,qp=10+i,qs=11+i,qmin=10+i,k_abs=.2+i*.02),
                    frequency_hz=15000000000,scientific_contract_fingerprint='contract',record={})
            (d/'source.json').write_text(json.dumps(dict(members=rows)))
            (d/'contract.json').write_text(json.dumps(dict(field_names=fields,lower=[0]*10,upper=[100]*10)))
            r=build([d/'source.json'],d/'data',contract_path=d/'contract.json',policy=make_policy(8,23,target_total=10))
            self.assertEqual(r['counts'],dict(train=8,validation=1,test=1))
            self.assertEqual(validate_training_split(d/'data')['status'],'PASS')
            n=json.loads((d/'data/normalizer.json').read_text())
            self.assertAlmostEqual(n['y_mean'][0],.85)
            self.assertEqual(n['training_geometries'],8)
            with self.assertRaises(FileExistsError):build([d/'source.json'],d/'data',contract_path=d/'contract.json',policy=make_policy(8,23,target_total=10))

    def test_real_training_entry_blocks_legacy_new_start_before_update(self):
        from .frequency_tandem import train_from_config
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);(d/'data_manifest.json').write_text(json.dumps(dict(schema='bb_data_manifest.v1')))
            (d/'config.json').write_text(json.dumps(dict(schema='frequency_tandem_train.v1',data_root=str(d),train=dict(role='forward',frequency_ghz=15,label_mode='STRICT_LUMPED'))))
            with patch('research.broadband56_nn.frequency_tandem.train_bb00') as train:
                with self.assertRaisesRegex(ValueError,'NEW_TRAINING_REQUIRES_FORMAL_811'):
                    train_from_config(d/'config.json',d/'out')
                train.assert_not_called()

if __name__=='__main__':unittest.main()
