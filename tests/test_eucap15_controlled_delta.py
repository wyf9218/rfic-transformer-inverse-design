"""Only new delta-path tests; all bytes are SYNTHETIC, never native results.

One new synthetic physical chain uses the actual existing inspector. The old
row is an explicitly saved synthetic baseline boundary, not a fabricated new
physical chain. Old physics files deliberately do not exist. No old tests run.
"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import socket
import subprocess
import pytest
from research.broadband56_nn import eucap15_controlled_results as results
from research.broadband56_nn import eucap15_controlled_evidence as evidence
from tests.test_eucap15_controlled_closed_integration import Integration


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    import torch
    def forbidden(*a,**kw): raise AssertionError('No native/network/model execution')
    for name in ('run','Popen'): monkeypatch.setattr(subprocess,name,forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(torch,'load',forbidden); monkeypatch.setattr(torch.jit,'load',forbidden)


class Delta:
    def __init__(self,tmp_path,monkeypatch):
        f=Integration(tmp_path,monkeypatch); c=f.c
        self.f,self.c,self.ctx=f,c,f.ctx
        epoch=int(datetime(2026,9,11,tzinfo=timezone.utc).timestamp())
        def enrich(v):
            slot=v['slot']; slot.update(schema='eucap15_controlled64_start_slot.v1',status='RESERVED_NOT_NATIVE_PROOF',
                automatic_redispatch_allowed=False,native_started=None,native_process_evidence=None,
                reserved_utc='2026-09-11T00:00:59+00:00',global_slot=2)
            b=v['observations'][0]; b.update(global_slot=2,boot_id='SYNTHETIC_BOOT',clock_ticks_per_second=100,
                kernel_boot_time_epoch_seconds=epoch,kernel_start_since_boot_seconds=60.0,
                kernel_start_utc='2026-09-11T00:01:00+00:00',wall_time_precision_seconds=1)
            b['process']['start_ticks']=6000; b['ancestry'][0]['start_ticks']=6000
        c.edit('observation',enrich)
        c.put('result',c.paths['result'],f.owner.fresh_candidate(f.batch,c.request_id,feature_pin=c.p('feature'),
            observation_pin=c.p('observation'),plan_pin=c.p('plan')))
        fresh=c.read('observation'); self.new_birth=fresh['observations'][0]
        c.put('new_birth',c.solver_root/'solve/native_observation/NATIVE_BIRTH_124_6000.json',self.new_birth)
        rows=results.initial_rows(self.ctx)
        old=next(r for r in rows if r['source']=='GEOMETRY_DOE' and self.ctx['rows'][r['candidate_id']]['local_dispatch_eligible'])
        self.old=old; old_root=c.native/old['request_id']; self.old_root=old_root
        slot=deepcopy(fresh['slot']); slot.update(candidate_id=old['candidate_id'],candidate=c.read('plan')['candidates'][old['candidate_id']],
            arm=old['arm'],arm_slot=1,global_slot=1,reserved_utc='2026-09-11T00:00:20+00:00')
        birth=deepcopy(self.new_birth); birth.update(candidate=slot['candidate'],arm=old['arm'],global_slot=1,
            observed_utc='2026-09-11T00:00:30+00:00',kernel_start_utc='2026-09-11T00:00:30+00:00',
            kernel_start_since_boot_seconds=30.0)
        birth['process'].update(pid=224,start_ticks=3000); birth['ancestry'][0].update(pid=224,start_ticks=3000)
        c.put('old_birth',old_root/'emx_selected/solve/native_observation/NATIVE_BIRTH_224_3000.json',birth)
        c.put('old_observation',old_root/'emx_selected/solve/native_observation/OBSERVATION_RECEIPT.json',
            dict(observations=[birth],slot=slot))
        old_result=dict(path=str(old_root/'RESULT.json'),sha256='a'*64,bytes=123)
        actual=[.6,.7,12.,.3]; cell=results.actual_landing([actual[j] for j in (0,1,3)])
        old.update(state='STRICT_VALID',actual=actual,actual_cell=list(cell),strict_valid=True,core_eligible=True,
            source_result=old_result,terminal_publication_verified=True,native_birth_identity_verified=True,
            native_birth_process=birth['process'],native_observation_pin=c.p('old_observation'),slot=slot,
            native_count_in_this_result=1,physical_chain_verified=True,closed_utc='2026-09-11T00:00:31+00:00')
        prior=c.root/'PRIOR_ACCEPTED_SYNTHETIC'; prior.mkdir()
        c.put('prior_rows',prior/'REQUEST_RESULTS.json',dict(schema='eucap15_controlled64_request_rows.v1',rows=rows))
        c.put('prior_closure',prior/'READ_SOURCE_PINS.json',[dict(original=old_result,resolved=old_result)])
        c.put('prior_spec',prior/'SPEC.json',dict(schema='eucap15_controlled_closed_capture_consumer.v1',
            intent=self.ctx['intent'],preparation_manifest=self.ctx['manifest'],expected_release=c.p('release'),
            expected_owner_config=c.p('owner_config')))
        c.put('prior_receipt',prior/'RECEIPT.json',dict(schema='eucap15_controlled64_closed_capture_consumer_receipt.v1',
            status='PASS_SCOPED_CLOSED_CAPTURE_NOT_FULL_START_LEDGER',source_bytes_unchanged=True,
            native_actions_performed=False,model_loads=0,target_generation=0,training_admission=False,
            spec=c.p('prior_spec'),artifacts={'REQUEST_RESULTS.json':c.p('prior_rows'),'READ_SOURCE_PINS.json':c.p('prior_closure')}))
        monkeypatch.setattr(results,'PRIOR_RECEIPT_SHA',c.p('prior_receipt')['sha256'])
        closed=[]; payload=[]
        for p in self.ctx['rows'].values():
            if p['candidate_id']==old['candidate_id']: rp=old_result; status='FRESH_EMX_EXTRACTED'
            else:
                if p['candidate_id']==c.candidate_id: key='result'; value=c.read(key)
                elif not p['analytic_pass']:
                    key='hold_'+p['request_id']; value=f.owner.original_hold(f.batch,p['request_id'])
                    c.put(key,c.native/p['request_id']/'RESULT.json',value)
                else:
                    key='terminal_'+p['request_id']; value=f.owner.failed_candidate(f.batch,p['request_id'],
                        error='SYNTHETIC retained pre-native failure',stage_evidence=[])
                    c.put(key,c.native/p['request_id']/'RESULT.json',value)
                rp=c.p(key);status=value['status'];payload.append(dict(original=rp,resolved=rp))
            closed.append(dict(request_id=p['request_id'],result=rp,status=status))
        entries=[]
        for key,sl in [('old_birth',slot),('new_birth',fresh['slot'])]:
            entries.append(dict(actual_native_starts=1,births=[dict(pin=c.p(key),birth=c.read(key))],
                reservation_is_not_native_start=True,result_closed=True,slot=sl))
        ledger=dict(schema='eucap15_controlled64_full_native_start_capture.v1',release=c.p('release'),
            plan=c.p('plan'),original_denominator=64,all_reserved_candidates_included=True,unclosed_births_included=True,
            order_basis='LINUX_PROC_START_TICKS_NOT_FROZEN_PROPOSAL_ORDER',capture_started_utc='2026-09-11T00:09:59+00:00',
            cutoff_utc='2026-09-11T00:10:00+00:00',rows=entries,boot_ids=['SYNTHETIC_BOOT'],unresolved_reservations=[],
            actual_native_starts=2,observed_native_start_lower_bound=2,
            actual_start_order=[dict(x['births'][0],order=i) for i,x in enumerate(entries,1)])
        c.put('ledger',c.root/'FULL_LEDGER.json',ledger)
        c.put('batch_terminal',c.native/'BATCH_RECEIPT.json',dict(status='ALL_ORIGINAL64_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE',
            utc='2026-09-11T00:09:00+00:00',release=c.p('release'),plan=dict(plan=c.p('plan'),release=c.p('release')),
            N_original_requests=64,results=[x['result'] for x in closed],production_accepted_added=0))
        c.put('transport',c.root/'TRANSPORT.json',dict(schema='eucap15_controlled64_private_capture.v1',
            utc='2026-09-11T00:10:01+00:00',remote_root=str(c.root),native_actions_by_capture=0,
            production_accepted_added=0,closed=closed,ledger=c.p('ledger'),files=payload))

    def reader(self):
        return results.DeltaReader({str(p):str(p) for p in self.c.paths.values()})

    def run(self):
        c=self.c; self.used=self.reader()
        return results.consume_closed_delta(self.used,c.p('transport'),c.p('prior_receipt'),c.p('batch_terminal'),self.ctx,
            expected_release=c.p('release'),expected_owner_config=c.p('owner_config'))

    def edit_ledger(self,fn):
        c=self.c; value=c.read('ledger'); fn(value); c.put('ledger',c.paths['ledger'],value)
        value=c.read('transport');value['ledger']=c.p('ledger');c.put('transport',c.paths['transport'],value)


@pytest.fixture
def delta(tmp_path,monkeypatch): return Delta(tmp_path,monkeypatch)


def test_real_new_chain_and_saved_old_row_merge_without_old_physics(delta):
    out=delta.run(); rows={r['candidate_id']:r for r in out['rows']}; s=out['summary']
    assert len(rows)==64 and s['state_counts']['ANALYTIC_FAIL']==14
    assert s['new_physical_chains']==1 and s['old_physical_chains_reopened']==0
    assert s['old_physical_rows_reused']==1 and s['solver_starts']==2
    assert s['equal_m']['primary']['status']=='NOT_REACHED_OR_PENDING'
    assert s['equal_m']['maximum_common']['m']==1
    assert s['equal_K']['primary']['status']=='NOT_REACHED'
    assert rows[delta.old['candidate_id']]['actual']==delta.old['actual']
    assert rows[delta.old['candidate_id']]['target'] is None
    assert all(r['state']!='CANDIDATE_FAILURE_UNCLASSIFIED' for r in rows.values())
    assert all(r['actual'] is None for r in rows.values() if r['state']=='PRE_NATIVE_FAILURE_UNCLASSIFIED')
    assert all('/emx_selected/solve/native_observation/' in p for p in delta.used.evidence
               if Path(p).is_relative_to(delta.old_root))


@pytest.mark.parametrize('attack',['duplicate','changed_old','missing','unknown_status'])
def test_delta_full_identity_and_old_pin_immutable(delta,attack):
    c=delta.c; t=c.read('transport')
    if attack=='duplicate':t['closed'][1]=deepcopy(t['closed'][0])
    elif attack=='missing':t['closed'].pop()
    elif attack=='changed_old':
        next(x for x in t['closed'] if x['request_id']==delta.old['request_id'])['result']['sha256']='b'*64
    else: next(x for x in t['closed'] if x['request_id']==c.request_id)['status']='SELF_ASSERTED_PASS'
    c.put('transport',c.paths['transport'],t)
    with pytest.raises(ValueError):delta.run()


@pytest.mark.parametrize('attack',['order','missing_birth','unresolved','cutoff','boot','wrapper','slot','old_geometry'])
def test_full_ledger_must_bind_checked_births(delta,attack):
    def change(v):
        if attack=='order':v['actual_start_order'].reverse()
        elif attack=='missing_birth':v['rows'].pop()
        elif attack=='unresolved':v['unresolved_reservations']=['UNKNOWN']
        elif attack=='cutoff':v['cutoff_utc']='2026-09-11T00:00:00+00:00'
        elif attack=='boot':v['boot_ids']=['DIFFERENT']
        elif attack=='wrapper':v['rows'][1]['births'][0]['birth']['process']['pid']=123
        elif attack=='slot':v['rows'][1]['slot']['global_slot']=1
        else:v['rows'][0]['slot']['candidate']['canonical_geometry_sha256']='e'*64
    delta.edit_ledger(change)
    with pytest.raises(ValueError):delta.run()


@pytest.mark.parametrize('kind',['direct','resolved_alias'])
def test_old_physics_read_guard_both_identity_spaces(tmp_path,kind):
    old=tmp_path/'old';old.mkdir();p=old/'features_56.csv';p.write_bytes(b'SYNTHETIC NEVER READ')
    op=results.pin(p)
    if kind=='resolved_alias':op=dict(op,path=str(tmp_path/'new_alias'))
    reader=results.DeltaReader({op['path']:str(p)},[str(old)])
    with pytest.raises(ValueError,match='must not be reopened'):reader.read(op)


def test_budget_decision_not_status_alone_and_premature_cap_rejected(delta):
    c=delta.c;p=next(p for p in delta.ctx['rows'].values() if p['local_dispatch_eligible'] and
        p['candidate_id'] not in (c.candidate_id,delta.old['candidate_id']))
    cid=p['candidate_id'];root=c.native/p['request_id']
    decision=dict(schema='eucap15_controlled64_budget_decision.v1',candidate_id=cid,
        plan_sha256=delta.f.slots.digest(c.read('plan')),status='NOT_DISPATCHED_ARM_START_CAP',
        observed_utc='2026-09-11T00:08:00+00:00')
    c.put('budget',root/'BUDGET.json',decision)
    value=dict(evidence._result_base(delta.ctx,p),status=decision['status'],plan=c.p('plan'),
        budget_decision=c.p('budget'),stage_evidence=[c.p('budget')])
    c.put('budget_result',root/'RESULT.json',value)
    checked=evidence.inspect_closed_result(delta.reader(),cid,c.p('budget_result'),delta.ctx,
        owner_root=str(c.native),capture_completed_utc='2026-09-11T00:10:00+00:00',
        expected_release=c.p('release'),expected_owner_config=c.p('owner_config'))
    assert checked['state']=='BUDGET_NOT_DISPATCHED' and checked['native_count_in_this_result'] is None
    t=c.read('transport'); own=next(x for x in t['closed'] if x['request_id']==p['request_id'])
    oldpin=own['result'];own.update(result=c.p('budget_result'),status=decision['status'])
    t['files']=[dict(original=c.p('budget_result'),resolved=c.p('budget_result')) if x['original']==oldpin else x for x in t['files']]
    c.put('transport',c.paths['transport'],t)
    b=c.read('batch_terminal');b['results']=[c.p('budget_result') if x==oldpin else x for x in b['results']]
    c.put('batch_terminal',c.paths['batch_terminal'],b)
    with pytest.raises(ValueError,match='cap was not reached'):delta.run()


def test_new_batch_cannot_close_before_last_birth(delta):
    c=delta.c; value=c.read('batch_terminal');value['utc']='2026-09-11T00:00:31+00:00'
    c.put('batch_terminal',c.paths['batch_terminal'],value)
    with pytest.raises(ValueError,match='Feature publication chronology differs'):delta.run()


def test_new_native_birth_after_six_hour_deadline_rejected(delta):
    c=delta.c; birth=c.read('new_birth'); birth['process']['start_ticks']=2160100
    birth['ancestry'][0]['start_ticks']=2160100
    birth.update(kernel_start_since_boot_seconds=21601.0,kernel_start_utc='2026-09-11T06:00:01+00:00',
        observed_utc='2026-09-11T06:00:02+00:00')
    c.put('new_birth',c.solver_root/'solve/native_observation/NATIVE_BIRTH_124_2160100.json',birth)
    obs=c.read('observation');obs['observations']=[birth];c.put('observation',c.paths['observation'],obs)
    rows=results.initial_rows(delta.ctx)
    for i,r in enumerate(rows):
        if r['candidate_id']==delta.old['candidate_id']:rows[i]=deepcopy(delta.old)
        elif r['candidate_id']==c.candidate_id:
            r.update(terminal_publication_verified=True,native_birth_identity_verified=True,
                native_birth_process=birth['process'],native_observation_pin=c.p('observation'),
                slot=obs['slot'],state='STRICT_VALID')
    def change(v):
        v['capture_started_utc']='2026-09-11T06:01:00+00:00';v['cutoff_utc']='2026-09-11T06:01:01+00:00'
        v['rows'][1]['births']=[dict(pin=c.p('new_birth'),birth=birth)]
        v['actual_start_order'][1]=dict(pin=c.p('new_birth'),birth=birth,order=2)
    delta.edit_ledger(change)
    with pytest.raises(ValueError,match='exceeds the frozen deadline'):
        results.bind_full_start_ledger(delta.reader(),c.p('ledger'),delta.ctx,rows,
            expected_release=c.p('release'),expected_owner_config=c.p('owner_config'))


@pytest.mark.parametrize('batch_utc',[
    '2026-09-11T00:01:01.500000+00:00',  # After birth, before solver close.
    '2026-09-11T00:01:02.500000+00:00',  # After solver close, before FEATURE.
])
def test_new_batch_must_follow_new_solver_and_feature(delta,batch_utc):
    c=delta.c; value=c.read('batch_terminal');value['utc']=batch_utc
    c.put('batch_terminal',c.paths['batch_terminal'],value)
    with pytest.raises(ValueError,match='Feature publication chronology differs'):
        delta.run()
