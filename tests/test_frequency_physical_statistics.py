"""Synthetic evidence and numerical regression tests; no model/network/physics."""
import copy
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.broadband56_nn import frequency_physical_statistics as stats


def row(q=14,request='R',scope='FORMAL_10K'):
    target=[1.,1.,float(q),.2]
    return dict(request_id=request,frequency_ghz=5,model_id='synthetic-model',dataset_scope=scope,
        target_source='HELDOUT_TRIPLE_AUDIT',label_mode='STRICT_LUMPED',protocol_sha256='protocol',dispatch_order=0,
        candidate_id=f'{request}-q{q}',q_target=q,q_proxy=14,selected_before_emx=q==14,analytic_pass=True,
        stage='SOLVED_STRICT_VALID',request_accounted=True,solved=True,strict_valid=True,joint_hit=True,
        target=target,proxy=[v+.01 for v in target],actual=[v+.02 for v in target],
        emx_minus_target=[.02]*4,emx_minus_frozen_proxy=[.01]*4,score_scale=[2.5,2.5,20.,.8],
        absolute_tolerances=[.125,.125,1.,.04],proxy_score=.01,emx_score=.02,
        solver_identity=f'solver-{request}-{q}',s4p_sha256=f's4p-{request}-{q}',source_pins={})


def capture_fixture(root, *, nonfinite_q=None, drc_failed=()):
    rows=[row(q) for q in range(10,21)]
    job={k:rows[0][k] for k in ('request_id','frequency_ghz','model_id','dataset_scope','q_proxy')}
    protocol=dict(score_scale=[2.5,2.5,20.,.8],absolute_tolerances=[.125,.125,1.,.04])
    context=dict(job=job,freeze=dict(protocol=protocol))
    files=[];verified=[];candidates=[]
    def artifact(name,value):
        local=root/name;stats.save(local,value);p=stats.pin(local)
        remote=dict(p,path='/remote/'+name);files.append(dict(local=p,remote=remote))
        return remote
    for r in rows:
        q=r['q_target']
        r.update(stage='PENDING',request_accounted=False,solved=False,strict_valid=None,joint_hit=None,actual=None)
        if q in drc_failed:
            candidates.append(dict(q=q,candidate_id=r['candidate_id'],stage='CALIBRE_FAIL'));continue
        gds=dict(path=f'/remote/g{q}',sha256=str(q)*32,bytes=1)
        touchstone=dict(path=f'/remote/s{q}',sha256=str(q)*32,bytes=2)
        verified.extend([gds,touchstone])
        solver=artifact(f'solver{q}.json',dict(status='PASS',candidate_id=r['candidate_id'],source_gds_before=gds,
            source_gds_after=gds,touchstone=touchstone))
        actual=[v+.02 for v in r['target']]
        if q==nonfinite_q:actual[0]=None
        good=q!=nonfinite_q
        residual=[a-t if a is not None else None for a,t in zip(actual,r['target'])]
        score=math.sqrt(sum((e/s)**2 for e,s in zip(residual,r['score_scale']))/4) if good else None
        feature=artifact(f'feature{q}.json',dict(status='PASS_EXTRACTION',candidate_id=r['candidate_id'],q_requested=q,
            frequency_ghz=5,model_id=r['model_id'],dataset_scope=r['dataset_scope'],q_proxy=14,target=r['target'],
            proxy_self=r['proxy'],score_scale=r['score_scale'],absolute_hit_tolerances=r['absolute_tolerances'],
            solver_receipt=solver,actual_fresh_emx=actual,descriptor_valid=good,strict_lumped_valid=good,physics_qa_pass=good,
            valid_for_strict_comparison=good,within_tolerance=[good,True,True,True],joint_response_hit=good,
            strict_joint_hit=good,normalized_response_score=score))
        candidates.append(dict(q=q,candidate_id=r['candidate_id'],feature_pin=feature,stage='REAL_EMX_STRICT_VALID' if good else 'REAL_EMX_INVALID'))
    n=11-len(drc_failed);valid=n-int(nonfinite_q is not None)
    # Equal residuals can have float-roundoff; freeze exact source-score winner.
    feature_scores=[]
    for entry in files:
        if '/feature' in entry['remote']['path']:
            f=stats.verified_read(entry['local'])
            if f['valid_for_strict_comparison']:feature_scores.append((f['normalized_response_score'],f['q_requested']))
    winner=min(feature_scores)[1] if valid==11 else None
    receipt=artifact('request.json',dict(frozen_job=job,N_logical=11,status='REQUEST_ACCOUNTED',N_solved=n,q_emx=winner))
    summary=dict(**job,N_original=11,N_solved=n,N_strict_valid=valid,q_emx=winner,candidates=candidates,
                 selected=next(c for c in candidates if c['q']==14),receipt=receipt)
    path=root/'CAPTURE.json';stats.save(path,dict(summary=summary,files=files,verified_remote=verified))
    return stats.pin(path),context,rows


class NumericStatistics(unittest.TestCase):
    def test_physical_signed_and_absolute_definitions(self):
        value=stats.error_metrics([-1.,0.,2.])
        self.assertEqual(value['mae'],1.)
        self.assertAlmostEqual(value['rmse'],math.sqrt(5/3))
        self.assertAlmostEqual(value['bias'],1/3)
        self.assertEqual(value['abs_error_p50'],1.)
        self.assertAlmostEqual(value['abs_error_p90'],1.8)
        self.assertAlmostEqual(value['abs_error_p95'],1.9)
    def test_empty_metrics_are_null_not_zero(self):
        self.assertEqual(stats.error_metrics([])['n'],0)
        self.assertTrue(all(v is None for k,v in stats.error_metrics([]).items() if k!='n'))
    def test_one_request_eleven_candidates_not_eleven_iid_samples(self):
        ci=stats.cluster_ci({'one_request':[.1]*11})
        self.assertEqual(ci['n_request_groups'],1)
        self.assertEqual(ci['ci_status'],'NOT_ESTIMABLE')
        self.assertIsNone(ci['mae_ci_low'])
    def test_request_cluster_bootstrap_reproducible(self):
        stats._CI_CACHE.clear()
        with patch.object(stats,'CI_REPLICATES',20):
            first=stats.cluster_ci({'a':[0,0,0],'b':[1,1]})
            stats._CI_CACHE.clear();second=stats.cluster_ci({'a':[0,0,0],'b':[1,1]})
        self.assertEqual(first,second)
        self.assertEqual(first['ci_status'],'PROVISIONAL_SMALL_REQUEST_N')
    def test_percent_near_zero_never_redefines_physical_error(self):
        self.assertEqual(stats.percentage(.1,0.,.8),(None,'NEAR_ZERO_REFERENCE_NOT_REPORTED'))
        self.assertEqual(stats.percentage(.1,1e-8,.8)[0],None)
        self.assertAlmostEqual(stats.percentage(.21,.2,.8)[0],5)
    def test_missing_and_nonfinite_percent(self):
        self.assertIsNone(stats.percentage(None,.2,.8)[0])
        self.assertIsNone(stats.percentage(float('nan'),.2,.8)[0])
        self.assertEqual(stats.clean([float('inf'),float('nan')]),[None,None])
    def test_pending_not_counted_as_failed_accuracy(self):
        r=row();r.update(solved=False,stage='PENDING',strict_valid=None,joint_hit=None,request_accounted=False)
        counts=stats.counts([r])
        self.assertEqual(counts['N_pending'],1)
        self.assertIsNone(counts['joint_hit_fraction_original'])
        self.assertIsNone(counts['joint_hit_fraction_strict'])
    def test_accounted_original_retains_failed_slots_but_not_future_requests(self):
        rows=[row(q) for q in range(10,21)]
        rows[0].update(solved=False,stage='DRC_FAIL',strict_valid=None,joint_hit=None)
        future=row(10,request='FUTURE')
        future.update(solved=False,stage='PENDING',strict_valid=None,joint_hit=None,request_accounted=False)
        counts=stats.counts(rows+[future])
        self.assertEqual(counts['N_accounted_candidates'],11)
        self.assertAlmostEqual(counts['joint_hit_fraction_accounted_original'],10/11)
        self.assertIsNone(counts['joint_hit_fraction_original'])
        self.assertEqual(counts['joint_hit_accounted_original_ci_status'],'NOT_ESTIMABLE')
    def test_independent_solves_count_receipt_instances(self):
        first=row(10);second=row(11);second['solver_identity']=first['solver_identity']
        self.assertEqual(stats.counts([first,second])['N_solved'],2)
        self.assertEqual(stats.counts([first,second])['N_independent_solves'],1)
    def test_identical_s4p_bytes_not_assumed_same_native_solve(self):
        first=row(10);second=row(11);second['s4p_sha256']=first['s4p_sha256']
        self.assertEqual(stats.counts([first,second])['N_independent_solves'],2)
        self.assertEqual(stats.counts([first,second])['N_unique_s4p_sha'],1)
    def test_finite_strict_invalid_excluded_from_primary_error(self):
        invalid=row();invalid.update(strict_valid=False,stage='SOLVED_INVALID',joint_hit=False)
        values=stats.metrics_table([invalid])
        self.assertTrue(all(v['n']==0 and v['mae'] is None for v in values if v['validity']=='strict_valid'))
        self.assertTrue(all(v['n']==1 for v in values if v['validity']=='all_finite_diagnostic'))
    def test_model_scope_groups_never_pooled(self):
        self.assertEqual(len(stats.grouped_rows([row(scope='FORMAL_10K'),row(scope='DEVELOPMENT_5K_NOT_FORMAL_10K')])),2)
    def test_two_estimands_and_proxy_target_comparisons_separate(self):
        values=stats.metrics_table([row(q) for q in range(10,21)])
        self.assertEqual(len(values),32)
        target=next(v for v in values if v['comparison']=='emx_minus_target' and v['estimand']=='all_candidates')
        selected=next(v for v in values if v['comparison']=='emx_minus_frozen_proxy' and v['estimand']=='selected_q_proxy')
        self.assertEqual(target['n'],11);self.assertEqual(selected['n'],1)
        self.assertAlmostEqual(target['mae'],.02);self.assertAlmostEqual(selected['mae'],.01)
    def test_common_complete_selection_comparison_only(self):
        rows=[row(q) for q in range(10,21)]
        complete=stats.selection_comparison(rows)
        self.assertTrue(all(r['N_common_complete_requests']==1 for r in complete))
        rows[0].update(strict_valid=False,stage='SOLVED_INVALID')
        incomplete=stats.selection_comparison(rows)
        self.assertTrue(all(r['status']=='NOT_AVAILABLE' and r['n']==0 and r['mae'] is None for r in incomplete))


class CaptureIntegrity(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_exact_complete_receipt_accepted(self):
        p,c,rows=capture_fixture(self.root);stats.apply_capture(p,c,rows,[])
        self.assertEqual(stats.counts(rows)['N_strict_valid'],11)
        self.assertEqual(stats.request_status(rows)[0]['full11_gate'],'PASS')
    def test_drc_failures_preserve_original11(self):
        p,c,rows=capture_fixture(self.root,drc_failed=(15,17,18));stats.apply_capture(p,c,rows,[])
        counts=stats.counts(rows)
        self.assertEqual((counts['N_original'],counts['N_solved'],counts['N_drc_fail']),(11,8,3))
        self.assertIsNone(rows[5]['actual'])
        self.assertIsNone(stats.request_status(rows)[0]['q_emx'])
    def test_nonfinite_solved_is_invalid_not_zero(self):
        p,c,rows=capture_fixture(self.root,nonfinite_q=10);stats.apply_capture(p,c,rows,[])
        self.assertEqual(stats.counts(rows)['N_invalid'],1)
        self.assertIsNone(rows[0]['actual'][0]);self.assertIsNone(rows[0]['emx_minus_target'][0])
    def test_selected_identity_rejected(self):
        p,c,rows=capture_fixture(self.root);c['job']['q_proxy']=15
        with self.assertRaisesRegex(ValueError,'identity/scope'):stats.apply_capture(p,c,rows,[])
    def test_local_artifact_tampering_rejected(self):
        p,c,rows=capture_fixture(self.root)
        with (self.root/'feature10.json').open('a') as stream:stream.write(' ')
        with self.assertRaisesRegex(ValueError,'local artifact changed'):stats.apply_capture(p,c,rows,[])
    def test_raw_unpublished_paths_refused(self):
        with self.assertRaises(ValueError):stats.publication_entries([self.root/'CAPTURE.json'])
        with self.assertRaises(ValueError):stats.publication_entries(dict(schema='frequency_physical_capture_publication.v1',status='WRITING',captures=[]))
    def test_no_clobber_and_failure_marker(self):
        bad=self.root/'manifest.json';stats.save(bad,dict(schema='wrong'))
        with self.assertRaises(ValueError):stats.build(bad,{},self.root/'failed')
        self.assertTrue((self.root/'failed/FAILURE_RECEIPT.json').exists())
        self.assertFalse((self.root/'failed/STATS_RECEIPT.json').exists())
        with self.assertRaises(FileExistsError):stats.build(bad,{},self.root/'failed')


if __name__=='__main__':unittest.main()
