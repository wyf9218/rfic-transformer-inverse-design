"""New usage-evidence tests; no model, native work or historical test reruns."""
import copy
import unittest
from unittest.mock import patch
from .eucap15_split811 import assignments, exposure_status, make_policy, reserve_doe_families


def row(i, **changes):
    r=dict(sequence=i,geometry_sha256=f'{i:064x}',geometry=[i+j/10 for j in range(10)],
        source='GEOMETRY_DOE',request_id=f'old_batch000001-GEOMETRY_DOE-{i}',
        split='test',model_used_for_proposal=False,base_train_id=None,base_geometry_hash=None,
        exposure=dict(gradient_training=False,tuning=False,development_evaluation=False,
            sampling_feedback=False,model_preprocessing_fit=False,fixed_qualification_read=True,
            unused_normalizer_fit=True,usage_scope_complete=True,family_status='INDEPENDENT_DOE_ROOT',
            evidence_refs=[dict(path='/fixture/frozen_usage_audit.json',sha256='a'*64)]))
    r.update(changes)
    return r


class ActualExposureTests(unittest.TestCase):
    def test_old_unused_doe_gets_actual_811_without_time_gate(self):
        m=assignments([row(i) for i in range(1,101)],make_policy(100,99,target_total=100))
        self.assertEqual(m['counts'],dict(train=80,validation=10,test=10))

    def test_fixed_read_and_unused_normalizer_do_not_mark_development(self):
        self.assertEqual(exposure_status(row(1)),'PROVEN_UNUSED_INDEPENDENT_DOE')

    def test_actual_development_usage_and_active_normalizer_are_train_only(self):
        rows=[]
        for i,k in enumerate(('gradient_training','tuning','development_evaluation','sampling_feedback','model_preprocessing_fit'),1):
            r=row(i);r['exposure'][k]=True;rows.append(r)
        m=assignments(rows,make_policy(0,0,target_total=10))
        self.assertEqual(m['counts'],dict(train=5,validation=0,test=0))

    def test_missing_usage_never_implicitly_becomes_train_or_independent(self):
        for alteration in ({}, {'gradient_training':False}, dict(row(1)['exposure'],evidence_refs=[])):
            m=assignments([row(1,exposure=alteration)],make_policy(999,99,target_total=10))
            self.assertEqual(m['rows'][0]['split'],'PENDING_EXPOSURE')
            self.assertEqual(sum(m['counts'].values()),0)

    def test_parent_and_neighborhood_cannot_enter_holdout(self):
        a=row(1);b=row(2,base_train_id=a['geometry_sha256'],source='TRAIN_NEIGHBORHOOD')
        m=assignments([a,b],make_policy(0,0,target_total=10))
        self.assertEqual({x['split'] for x in m['rows']},{'train'})

    def test_duplicate_alias_exposure_cannot_be_dropped(self):
        a=row(1);b=row(2,geometry=a['geometry']);b['exposure']['tuning']=True
        m=assignments([a,b],make_policy(0,0,target_total=10))
        self.assertEqual(m['counts'],dict(train=1,validation=0,test=0))

    def test_later_exposure_preserves_old_mapping_and_raises(self):
        rows=[row(i) for i in range(1,101)];p=make_policy(0,0,target_total=100)
        prior=assignments(rows,p);before=copy.deepcopy(prior)
        h=next(r['geometry_sha256'] for r in prior['rows'] if r['split']=='test')
        next(r for r in rows if r['geometry_sha256']==h)['exposure']['sampling_feedback']=True
        with self.assertRaisesRegex(ValueError,'HOLDOUT_FAMILY_EXPOSURE_CONFLICT'):
            assignments(rows,p,prior)
        self.assertEqual(prior,before)

    def test_future_reservation_does_not_look_at_physical_outcomes(self):
        rows=[row(i) for i in range(1,101)];p=make_policy(999,99,target_total=100)
        prior=reserve_doe_families(rows,p)
        self.assertEqual(prior['counts'],dict(train=80,validation=10,test=10))
        self.assertEqual(prior['by_geometry_sha256'],reserve_doe_families(list(reversed(rows)),p,prior)['by_geometry_sha256'])
        rows[0]['physical15']={}
        with self.assertRaisesRegex(ValueError,'before reading its response'):
            reserve_doe_families(rows,p)

    def test_unknown_family_member_blocks_otherwise_unused_doe(self):
        a=row(1);b=row(2,identity_namespaces={'alias':a['geometry_sha256']},exposure={})
        m=assignments([a,b],make_policy(0,0,target_total=10))
        self.assertEqual({r['split'] for r in m['rows']},{'PENDING_EXPOSURE'})

    def test_formal_entry_forwards_actual_evidence_overlay(self):
        from .eucap15_formal_development_view import main
        args=['formal-view','--snapshot','snapshot.json','--out','out','--contract-pin','{}',
            '--exposure-cutoff','8866','--first-doe-batch','23','--exposure-map','usage.json']
        with patch('sys.argv',args), patch('builtins.print'), patch(
                'research.broadband56_nn.eucap15_formal_development_view.checked',return_value='contract.json'), patch(
                'research.broadband56_nn.eucap15_split811.build',return_value={}) as builder:
            main()
        self.assertEqual(builder.call_args.kwargs['exposure_mapping'],'usage.json')

    def test_exploration_exact_fixed_lhs_recipe_can_reserve_without_relabelling(self):
        r=row(1,source='EXPLORATION')
        r['exposure'].update(proposal_method='FIXED_GEOMETRY_LHS',no_proxy_selection=True,
            geometry_recipe_ref=dict(path='/fixture/frozen_recipe.json',sha256='b'*64))
        self.assertEqual(exposure_status(r),'PROVEN_UNUSED_INDEPENDENT_DOE')
        self.assertEqual(r['source'],'EXPLORATION')

    def test_exploration_missing_recipe_or_proxy_evidence_stays_unknown(self):
        for updates in ({},dict(proposal_method='FIXED_GEOMETRY_LHS',no_proxy_selection=True),
                dict(proposal_method='FIXED_GEOMETRY_LHS',no_proxy_selection=False,
                    geometry_recipe_ref=dict(path='/fixture/recipe',sha256='b'*64))):
            r=row(1,source='EXPLORATION');r['exposure'].update(updates)
            self.assertEqual(exposure_status(r),'UNKNOWN_EXPOSURE_OR_FAMILY')

    def test_compact_unknown_proposal_flag_needs_exact_recipe_closure(self):
        for source in ('GEOMETRY_DOE','EXPLORATION'):
            r=row(1,source=source,model_used_for_proposal=None)
            self.assertEqual(exposure_status(r),'UNKNOWN_EXPOSURE_OR_FAMILY')
            r['exposure'].update(proposal_method='FIXED_GEOMETRY_LHS',no_proxy_selection=True,
                proposal_model_used=False,geometry_recipe_ref=dict(path='/fixture/recipe',sha256='b'*64))
            self.assertEqual(exposure_status(r),'PROVEN_UNUSED_INDEPENDENT_DOE')
            r['model_used_for_proposal']=True
            self.assertEqual(exposure_status(r),'DEVELOPMENT_DERIVED_FAMILY')


if __name__=='__main__':unittest.main()
