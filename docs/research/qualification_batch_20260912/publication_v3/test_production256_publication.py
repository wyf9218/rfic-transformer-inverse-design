"""New production256-only synthetic predicates and shared append integration.

No actual source artifacts, remote ledger, old tests, physical QA or extraction.
The publication tests patch only the already-built evidence callback; ledger,
legacy record serialization, flock/exclusive append/readback run for real in tmp.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import production256_publication as c
from geometry_helpers import GEOMETRY_FIELDS


def saved_member(source='GEOMETRY_DOE',split='validation'):
    fields=list(GEOMETRY_FIELDS);g=[300.,320.,330.,340.,7.,50.,55.,2.,170.,180.]
    keys=c.history.identities(g,fields);rid=c.STUDY+'-'+source+'-SYNTHETIC'
    p=dict(request_id=rid,candidate_id=rid,recipe_sha256=c.INTENT_SHA,source=source,
        arm=source+'_PRODUCTION',analytic_pass=True,local_dispatch_eligible=True,duplicate_reasons=[],
        assigned_development_split=split,target=None,proxy=None,q_proxy=None,q_emx=None,model_id=None,
        requested_triple=None,geometry_fields=fields,geometry=g,geometry_units='um',frequency_hz=15000000000,
        canonical_geometry_sha256=keys['canonical_9dp'],production_geometry_fingerprint=keys['production_1e6'],
        base_metadata={'assigned_development_split':'train'},final_independent_test_eligible=False)
    r=dict(original_proposal=p,request_id=rid,candidate_id=rid,q_proxy=None,q_emx=None,candidate_model_id=None,
        model_used_for_proposal=False,candidate_geometry_identity_sha256=keys['canonical_9dp'],
        status='FRESH_EMX_EXTRACTED',actual_native_starts=1,production_accepted=False,core15_eligible=True,
        valid_for_strict_comparison=True,strict_joint_hit=None,actual_response=[1.,1.2,14.,.4])
    f=dict(original_proposal=p,candidate_id=rid,target=None,proxy_self=None,q_proxy=None,q_requested=None,
        q_emx=None,model_id=None,candidate_model_id=None,model_used_for_proposal=False,
        schema='eucap15_production_geometry_fresh_features.v1',status='PASS_EXTRACTION',
        dataset_scope='DEVELOPMENT_PRODUCTION_GEOMETRY256',frequency_ghz=15,production_membership=False,
        descriptor_valid=True,physics_qa_pass=True,strict_lumped_valid=True,valid_for_strict_comparison=True,
        core15_eligible=True,actual_fresh_emx=[1.,1.2,14.,.4],q10_to20_supported=True,
        original_56_summary=dict(port_count=4,frequency_points=56,frequency_start_hz=5000000000,
            frequency_stop_hz=60000000000,frequency_step_hz=1000000000,passivity_fail_frequency_count=0,
            reciprocity_fail_frequency_count=0,checks=dict.fromkeys(c.GRID_CHECKS,True)),
        original_frequency_row=dict(lp_nh=1.,ls_nh=1.2,qp=14.,qs=15.,qmin=14.,signed_k=-.4,k_abs=.4,
            frequency_hz=15000000000,below_half_srf='true',strict_lumped_valid='true'))
    contract=dict(geometry_order=fields,geometry_bounds=dict(lower=[-1000]*10,upper=[1000]*10))
    receipt=dict(request_id=rid,original_split=split,original_target=None,original_q_proxy=None)
    return r,f,contract,receipt


class Production256Tests(unittest.TestCase):
    def test_doe_and_train_neighborhood_saved_shape_qualify_without_targets(self):
        for source,split in [('GEOMETRY_DOE','validation'),('GEOMETRY_DOE','test'),('TRAIN_NEIGHBORHOOD','train')]:
            x=saved_member(source,split);keys,physical=c.validate_member(*x)
            self.assertEqual(len(keys),3);self.assertEqual(physical['signed_k'],-.4)
            self.assertEqual(x[0]['original_proposal']['assigned_development_split'],split)

    def test_q_support_is_not_an_additional_core_admission_gate(self):
        r,f,contract,receipt=saved_member();f['q10_to20_supported']=False
        f['original_frequency_row'].update(qp=25.,qs=26.,qmin=25.)
        f['actual_fresh_emx'][2]=r['actual_response'][2]=25.
        self.assertEqual(c.validate_member(r,f,contract,receipt)[1]['qmin'],25.)

    def test_invalid_range_strict_half_srf_and_nonfinite_rejected(self):
        for name,value in [('lp_nh',2.1),('k_abs',.19),('qmin',float('nan')),
                           ('below_half_srf','false'),('strict_lumped_valid','false')]:
            with self.subTest(field=name):
                x=saved_member();x[1]['original_frequency_row'][name]=value
                with self.assertRaises(ValueError):c.validate_member(*x)
        x=saved_member();x[1]['core15_eligible']=False
        with self.assertRaises(ValueError):c.validate_member(*x)

    def test_empty_missing_or_false_original56_checks_fail_closed(self):
        for checks in ({},{'port_count_exact_four':True},dict.fromkeys(c.GRID_CHECKS,False)):
            x=saved_member();x[1]['original_56_summary']['checks']=checks
            with self.assertRaises(ValueError):c.validate_member(*x)
        for checks in ({},{'foundry_drc_pass':True},dict.fromkeys(c.DRC_CHECKS,False)):
            with self.assertRaises(ValueError):c.true_checks(checks,c.DRC_CHECKS,'synthetic DRC')

    def test_model_target_q_or_train_family_split_injection_rejected(self):
        for location,key,value in [(0,'candidate_model_id','old-model'),(0,'model_used_for_proposal',True),
                                    (1,'q_proxy',15),(1,'target',[1,1,15,.4])]:
            x=saved_member();x[location][key]=value
            with self.assertRaises(ValueError):c.validate_member(*x)
        x=saved_member('TRAIN_NEIGHBORHOOD','test')
        with self.assertRaises(ValueError):c.validate_member(*x)

    def test_geometry_fingerprint_and_saved_actual_are_not_trusted_by_name(self):
        x=saved_member();x[0]['original_proposal']['production_geometry_fingerprint']='0'*64
        with self.assertRaises(ValueError):c.validate_member(*x)
        x=saved_member();x[0]['actual_response'][3]=.5
        with self.assertRaises(ValueError):c.validate_member(*x)

    def test_shared_append_dynamic_sequence_replay_and_three_namespace_duplicate(self):
        with tempfile.TemporaryDirectory(prefix='eucap256fixture_') as td:
            root=Path(td).resolve()/'qualified15_single_member_v1';(root/'records').mkdir(parents=True)
            def evidence(rid,g,split):
                ids=c.history.identities(g,list(GEOMETRY_FIELDS))
                m=dict(request_id=rid,candidate_id=rid,geometry=g,geometry_fields=list(GEOMETRY_FIELDS),
                    geometry_sha256=ids['canonical_9dp'],split=split,source='SYNTHETIC',full56_evidence={})
                return dict(schema=c.EVIDENCE_SCHEMA,member=m,identities=ids,source_inputs={},source_request_id=rid,
                    physical15=dict(lp_nh=1.,ls_nh=1.2,qp=14.,qs=15.,qmin=14.,signed_k=.4,k_abs=.4),
                    qualification=c.QUALIFICATION,scientific_contract_fingerprint=c.legacy.FP)
            old=evidence('old',[200.,220.,230.,240.,6.,40.,45.,1.,150.,160.],'validation')
            value=dict(schema='eucap15_frozen_holdout_qualified_increment.v3',status=c.legacy.STATUS,
                record=c.legacy.make_record(old,1),evidence=old,evidence_digest=c.digest(old),
                checkpoint=dict(increment_accepted=1,increment_15ghz_rows=1,referenced_frequency_rows=56,
                                last_increment_sequence=1,prior_commit=None))
            first=c.atomic_json(root/'records/000001.json',value,immutable=True)
            prefix=(root/'records/000001.json').read_bytes()
            new=evidence('new',[300.,320.,330.,340.,7.,50.,55.,2.,170.,180.],'train')
            with patch.object(c,'build_evidence',return_value=copy.deepcopy(new)):
                one=c.publish(root,new,'SYNTHETIC',first_sha=first)
                replay=c.publish(root,new,'SYNTHETIC',first_sha=first)
            self.assertEqual((one['qualification_sequence'],one['added'],replay['added']),(2,1,0))
            self.assertEqual(one['sha256'],replay['sha256'])
            self.assertEqual(prefix,(root/'records/000001.json').read_bytes())
            rows=c.history.ledger(root,first)
            self.assertEqual(len(rows),2);self.assertEqual(rows[-1]['value']['record']['split'],'train')
            self.assertEqual(rows[-1]['value']['counting']['old_broadband_added'],0)
            for delta,namespace in [(0.,'canonical_9dp'),(.0000001,'production_1e6'),(.001,'nominal_grid_5nm')]:
                g=list(new['member']['geometry']);g[0]+=delta;dup=evidence('duplicate'+str(delta),g,'test')
                out=c.publish(root,dup,'SYNTHETIC',first_sha=first)
                self.assertEqual(out['added'],0);self.assertIn(namespace,out['matching_namespaces'])
            self.assertEqual(len(c.history.ledger(root,first)),2)
            altered=copy.deepcopy(new);altered['member']['split']='test'
            with self.assertRaises(ValueError):c.publish(root,altered,'SYNTHETIC',first_sha=first)

    def test_rebuild_source_failure_cannot_append_or_overwrite(self):
        with tempfile.TemporaryDirectory(prefix='eucap256fixture_') as td:
            root=Path(td).resolve()/'qualified15_single_member_v1';(root/'records').mkdir(parents=True)
            r,f,contract,receipt=saved_member();keys,physical=c.validate_member(r,f,contract,receipt)
            m=dict(r['original_proposal'],geometry_sha256=keys['canonical_9dp'],split='validation',full56_evidence={})
            e=dict(schema=c.EVIDENCE_SCHEMA,member=m,identities=keys,physical15=physical,source_inputs={},
                source_request_id=m['request_id'],qualification=c.QUALIFICATION,scientific_contract_fingerprint=c.legacy.FP)
            with patch.object(c.history,'ledger',return_value=[]),patch.object(c,'build_evidence',side_effect=ValueError('synthetic source drift')):
                with self.assertRaises(ValueError):c.publish(root,e,'SYNTHETIC')
            self.assertEqual(list((root/'records').iterdir()),[])


if __name__=='__main__':unittest.main()
