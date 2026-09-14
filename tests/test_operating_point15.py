"""Only changed-policy paths; fixtures are software tests, not EM results."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
import numpy as np
from research.broadband56_nn.operating_point15 import classify, feature_annotations, LABEL_POLICY
from research.broadband56_nn.frequency_profile import frequency_mask

def sample():
    return dict(frequency_hz=15e9, physical15=dict(lp_nh=3.,ls_nh=.3,q_min=5.,k_abs=.4),
        descriptor_valid=True,strict_valid=False,below_half_srf=False,
        s_z_roundtrip_abs_max=1e-12,passivity_sigma_max=.99,reciprocity_abs_max=0.)

class OperatingPointTests(unittest.TestCase):
    def test_half_srf_failure_recovers_without_l_window_or_q_window(self):
        a=classify(sample(),compatibility='COMPATIBLE_PROVEN')
        self.assertTrue(a['fully_qualified']);self.assertTrue(a['recovered_only_by_removing_half_srf'])
    def test_unknown_srf_does_not_reject(self):
        r=sample();r.pop('below_half_srf');r.pop('strict_valid')
        a=classify(r);self.assertTrue(a['operating_point_valid']);self.assertEqual(a['below_half_srf'],'UNKNOWN')
    def test_negative_or_nonfinite_rejected(self):
        for k,v in [('lp_nh',-1),('ls_nh',0),('q_min',float('nan')),('k_abs',.86)]:
            r=sample();r['physical15'][k]=v;self.assertFalse(classify(r)['operating_point_valid'])
    def test_descriptor_and_numerical_qa_unchanged(self):
        for k,v in [('descriptor_valid',False),('s_z_roundtrip_abs_max',1e-7),('reciprocity_abs_max',1e-3)]:
            r=sample();r[k]=v;self.assertFalse(classify(r)['operating_point_valid'])
    def test_compatibility_remains_separate(self):
        for c in ('UNKNOWN','INCOMPATIBLE'):
            r=classify(sample(),compatibility=c);self.assertTrue(r['operating_point_valid']);self.assertFalse(r['fully_qualified'])
    def test_old_pass_and_original_fields_preserved(self):
        r=sample();r['strict_valid']=r['below_half_srf']=True;before=deepcopy(r)
        self.assertTrue(classify(r)['old_policy_valid']);feature_annotations(r);self.assertEqual(r,before)
    def test_training_mask_not_legacy_strict(self):
        b=SimpleNamespace(manifest={'label_policy':LABEL_POLICY},arrays=dict(frequency_hz=np.array([15e9]),
            y=np.ones((2,1,4)),y_valid=np.ones((2,1,4),dtype=bool),
            operating_point_valid=np.array([[True],[False]]),strict_lumped_valid=np.zeros((2,1),dtype=bool)))
        self.assertEqual(frequency_mask(b,15,'OPERATING_POINT_15GHZ').tolist(),[True,False])
        self.assertEqual(frequency_mask(b,15,'STRICT_LUMPED').tolist(),[False,False])
    def test_wrong_or_missing_policy_refused(self):
        b=SimpleNamespace(manifest={},arrays=dict(frequency_hz=np.array([15e9]),y=np.ones((1,1,4))))
        with self.assertRaises(ValueError):frequency_mask(b,15,'OPERATING_POINT_15GHZ')
    def test_normalizer_uses_new_valid_train_not_validation(self):
        from research.broadband56_nn.bb00 import prepare_bb00
        a=dict(frequency_hz=np.array([15e9]),geometry=np.array([[1.,2.],[3.,4.],[100.,200.]]),
            y=np.array([[[1.,2.,5.,.3]],[[3.,4.,7.,.5]],[[100.,200.,90.,.8]]]),
            split=np.array([0,0,1]),operating_point_valid=np.ones((3,1),dtype=bool),
            strict_lumped_valid=np.zeros((3,1),dtype=bool))
        b=SimpleNamespace(manifest={'label_policy':LABEL_POLICY},arrays=a,norm={'field_names':['a','b']},
            train=np.array([0,1]),val=np.array([2]))
        norm,train,val,_,_=prepare_bb00(b,{'field_names':['a','b']},(2.5,2.5,20,.8),True,label_mode='OPERATING_POINT_15GHZ')
        self.assertEqual(norm['y_mean'],[2.,3.,6.,.4]);self.assertEqual(norm['label_policy'],LABEL_POLICY)
        self.assertEqual(train.tolist(),[0,1]);self.assertEqual(val.tolist(),[2])
    def test_new_evaluation_preserves_q_and_failure_denominator(self):
        from research.broadband56_nn.frequency_physical_statistics import operating_point_rows
        f=sample();f.update(candidate_id='r1-Q12',frequency_ghz=15,q_proxy=12,
            actual_fresh_emx=[3.,.3,5.,.4],target=[3.,.3,5.,.4],proxy_self=[3.,.3,5.,.4],
            absolute_hit_tolerances=[.1,.1,1.,.1])
        requests=[dict(request_id='r1',candidate_id='r1-Q12',q_proxy=12,status='SOLVED',feature=f),
                  dict(request_id='r2',q_proxy=13,status='PENDING',feature=None)]
        x=operating_point_rows(requests,analysis_kind='POST_HOC_ORIGINAL_PROTOCOL_PRESERVED')
        self.assertEqual(x['joint_hit_count'],1);self.assertEqual(x['full_denominator_joint_hit_fraction'],.5)
        self.assertEqual(x['metrics_conditional_on_operating_point']['emx_minus_target'][0]['p95'],0)
        requests[0]['q_proxy']=11
        with self.assertRaises(ValueError):operating_point_rows(requests,analysis_kind='PREDECLARED_OPERATING_POINT')
    def test_actual_dataset_build_preserves_both_masks(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from research.broadband56_nn.eucap15_split811 import build,make_policy,USAGE_FIELDS
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);members=[]
            for i in range(1,11):
                evidence=dict.fromkeys((*USAGE_FIELDS,'model_preprocessing_fit'),False)
                evidence.update(gradient_training=i<=8,usage_scope_complete=True,family_status='INDEPENDENT_DOE_ROOT',
                    evidence_refs=[dict(path='SYNTHETIC_SOFTWARE_TEST',sha256='a'*64)])
                p=dict(lp_nh=3.+i*.01,ls_nh=.3+i*.01,qmin=5.,qp=5.,qs=6.,k_abs=.4)
                f=sample();f['physical15']=p;proof=classify(f,compatibility='COMPATIBLE_PROVEN')
                members.append(dict(sequence=i,geometry_sha256=hashlib.sha256(str(i).encode()).hexdigest(),geometry=[i,i+.2],geometry_fields=['a','b'],
                    request_id=f'SYNTHETIC-{i}',source='GEOMETRY_DOE',split='train',record='SYNTHETIC',exposure=evidence,
                    model_used_for_proposal=False,frequency_hz=15e9,physical15=p,label_policy=LABEL_POLICY,
                    operating_point_policy_evidence=proof,scientific_contract_fingerprint='SYNTHETIC'))
            source=root/'source.json';source.write_text(json.dumps({'members':members}))
            contract=root/'contract.json';contract.write_text(json.dumps(dict(field_names=['a','b'],lower=[0,0],upper=[20,20])))
            receipt=build([source],root/'out',contract_path=contract,policy=make_policy(10,1,target_total=10))
            with np.load(root/'out/dataset.npz') as a:
                self.assertTrue(a['operating_point_valid'].all());self.assertFalse(a['strict_lumped_valid'].any())
                self.assertFalse(a['below_half_srf'].any())
            self.assertEqual(receipt['counts'],{'train':8,'validation':1,'test':1})

if __name__=='__main__':unittest.main()
