"""New-path integration only: synthetic physical bytes, real existing components.

The owner RESULT writer and capture are called on handwritten64 metadata; the
research feature/label inspector is not mocked. No private frame, model or
native program is read/executed, and no prior test suite is collected.
"""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from research.broadband56_nn import eucap15_controlled_results as results
from research.broadband56_nn import eucap15_controlled_evidence as evidence
from tests.fixtures import eucap15_controlled_evidence_fixture as fixture_module
from tests.test_eucap15_controlled_results import frame as synthetic_frame, coverage


@pytest.fixture(autouse=True)
def forbid_execution(monkeypatch):
    import torch
    def forbidden(*args,**kwargs): raise AssertionError('Synthetic-only integration forbids native/network/model execution')
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(torch,'load',forbidden)
    monkeypatch.setattr(torch.jit,'load',forbidden)


class Integration:
    def __init__(self,tmp_path,monkeypatch,*,source='SPARSE_TARGETED',invalid=False):
        def proposals():
            rows=synthetic_frame()
            for row in rows:
                if (16<=row['arm_order']<=25 if row['arm']==results.ARMS[0] else row['arm_order']>28):
                    row['analytic_pass']=row['local_dispatch_eligible']=False
            return rows
        monkeypatch.setattr(fixture_module,'frame',proposals)
        self.c=fixture_module.ControlledEvidence(tmp_path,monkeypatch,source=source,below_half_srf=not invalid)
        c=self.c
        # Existing public review-only owner components, not private/native paths.
        code=Path(__file__).resolve().parents[1]/'docs/research/code/native64_result_bridge_20260911'
        monkeypatch.syspath_prepend(str(code))
        self.owner=importlib.import_module('controlled_result')
        self.capture_module=importlib.import_module('closed_export')
        self.slots=importlib.import_module('start_slots')
        self.metadata=importlib.import_module('controlled_metadata')
        self.ctx=dict(c.batch,before=coverage(),excluded_hashes={'f'*64})
        self.batch=self.metadata.FrozenBatch(c.batch['manifest'],c.batch['intent'],c.batch['proposals'],
            c.batch['preparation'],c.batch['intent_value'],tuple(c.batch['rows'].values()),())
        monkeypatch.setattr(self.slots,'MANIFEST_SHA',c.batch['manifest']['sha256'])
        monkeypatch.setattr(self.slots,'INTENT_SHA',c.batch['intent']['sha256'])
        monkeypatch.setattr(self.capture_module,'utc',lambda:'2026-09-11T00:10:00+00:00')
        c.blob('native_executable',c.native/'SYNTHETIC_BINARY',b'SYNTHETIC BINARY NEVER EXECUTED')
        c.put('owner_config',c.native/'OWNER_CONFIG.json',dict(original_manifest=c.batch['manifest'],
            emx_runtime=dict(c.runtime,native_executable=c.p('native_executable'))))
        c.put('release',c.native/'RELEASE.json',dict(config=c.p('owner_config')))
        plan=self.slots.plan(c.p('release'),'2026-09-11T00:00:00+00:00','2026-09-11T06:00:00+00:00',
            self.batch.rows,evidence_class='SYNTHETIC_TEST_ONLY')
        c.put('plan',c.native/'PLAN.json',plan)
        slot=dict(plan_sha256=self.slots.digest(plan),candidate_id=c.candidate_id,
            candidate=plan['candidates'][c.candidate_id],arm=c.original['arm'],arm_slot=1,global_slot=1,
            launch_binding=dict(preflight=c.p('proof'),gds=c.p('gds'),command=c.command))
        ancestor=dict(pid=123,ppid=1,start_ticks=456,uid=789,state='S',argv=c.command)
        process=dict(pid=124,ppid=123,start_ticks=457,uid=789,state='R',
            argv=[c.p('native_executable')['path'],*c.command[1:]])
        birth=dict(schema='eucap15_controlled64_native_birth.v1',status='OBSERVED_EXACT_NATIVE_PROCESS',
            native_started=True,observed_utc='2026-09-11T00:01:00+00:00',
            process=process,ancestor=ancestor,ancestry=[process,ancestor],expected_executable=c.p('native_executable'),
            executable_sha256=c.p('native_executable')['sha256'],executable_bytes=c.p('native_executable')['bytes'],
            candidate=slot['candidate'],arm=slot['arm'],arm_slot=1,global_slot=1,
            plan_sha256=slot['plan_sha256'],launch_binding=slot['launch_binding'],command=c.command)
        c.put('observation',c.solver_root/'solve/observation/OBSERVATION_RECEIPT.json',dict(
            schema='eucap15_controlled64_native_observation.v1',status='ONE_NATIVE_BIRTH_OBSERVED',
            actual_native_starts=1,native_starts_observed=1,errors=[],observations=[birth],slot=slot,
            wrapper_pid=123,ended_utc='2026-09-11T00:01:01+00:00'))
        c.edit('solver',lambda v:v.update(artifacts=[*v['artifacts'],c.p('observation')],
            ended_utc='2026-09-11T00:01:02+00:00'))
        c.edit('feature',lambda v:v.update(generated_utc='2026-09-11T00:01:03+00:00'))
        self.declared={}
        for p in self.batch.rows:
            if not p['analytic_pass']:
                value=self.owner.original_hold(self.batch,p['request_id'])
                key='hold_'+p['request_id']; c.paths[key]=c.native/p['request_id']/'RESULT.json'
                c.paths[key].parent.mkdir(parents=True,exist_ok=True)
                self.owner.publish(c.paths[key],value); self.declared[p['request_id']]=c.p(key)
        self.failure=next(p for p in self.batch.rows if p['local_dispatch_eligible'] and p['candidate_id']!=c.candidate_id)
        value=self.owner.failed_candidate(self.batch,self.failure['request_id'],error='SYNTHETIC unknown stage',stage_evidence=[])
        c.paths['failure']=c.native/self.failure['request_id']/'RESULT.json'
        c.paths['failure'].parent.mkdir(parents=True,exist_ok=True)
        self.owner.publish(c.paths['failure'],value); self.declared[self.failure['request_id']]=c.p('failure')
        self.write_success()
        self.capture()

    def write_success(self):
        c=self.c
        value=self.owner.fresh_candidate(self.batch,c.request_id,feature_pin=c.p('feature'),
            observation_pin=c.p('observation'),plan_pin=c.p('plan'))
        c.paths['result']=c.native/c.request_id/'RESULT.json'
        self.owner.publish(c.paths['result'],value); self.declared[c.request_id]=c.p('result')

    def capture(self):
        c=self.c
        out=c.root/'OWNER_CAPTURE'
        self.capture_module.capture(self.batch,self.declared,c.native,out)
        c.paths['capture']=out/'CLOSED_METADATA_CAPTURE.json'

    def consume(self,reader=None,**kwargs):
        c=self.c
        return results.consume_closed_capture(reader or c.mirror(),c.p('capture'),self.ctx,
            expected_release=kwargs.get('release',c.p('release')),
            expected_owner_config=kwargs.get('config',c.p('owner_config')))

    def edit_capture(self,fn):
        c=self.c; value=json.loads(c.paths['capture'].read_bytes());fn(value)
        c.paths['capture'].write_text(json.dumps(value,allow_nan=False))


@pytest.fixture
def integration(tmp_path,monkeypatch): return Integration(tmp_path,monkeypatch)


@pytest.mark.parametrize('source,invalid',[('SPARSE_TARGETED',False),('GEOMETRY_DOE',False),('EXPLORATION',True)])
def test_actual_owner_capture_to_true_chain_and_compare_preserves64(tmp_path,monkeypatch,source,invalid):
    f=Integration(tmp_path,monkeypatch,source=source,invalid=invalid); result=f.consume();c=f.c
    rows=result['rows']; summary=result['summary']; by_id={r['candidate_id']:r for r in rows}
    assert len(rows)==64 and sum(r['state']=='ANALYTIC_FAIL' for r in rows)==14
    for p in f.batch.rows:
        if not p['analytic_pass']:
            r=by_id[p['candidate_id']]
            assert r['actual'] is None and r['state']=='ANALYTIC_FAIL'
            assert r['source_result']==f.declared[p['request_id']]
    physical=by_id[c.candidate_id]
    assert physical['state']==('EMX_INVALID' if invalid else 'STRICT_VALID')
    assert physical['physical_chain_verified'] is physical['terminal_publication_verified'] is True
    assert physical['native_birth_identity_verified'] is True
    assert physical['native_observation']=='VERIFIED_BIRTH_OBSERVATION_FULL_START_CHRONOLOGY_UNRESOLVED'
    assert physical['solver_start_order'] is physical['solver_started_utc'] is None
    assert physical['solver_start_verified'] is False and physical['native_count_in_this_result']==1
    assert by_id[f.failure['candidate_id']]['state']=='CANDIDATE_FAILURE_UNCLASSIFIED'
    assert by_id[f.failure['candidate_id']]['actual'] is None
    assert sum(r['state']=='NO_CLOSED_RESULT_IN_CAPTURE' for r in rows)==48
    assert summary['equal_m'] is summary['equal_K'] is summary['solver_starts'] is None
    assert summary['observed_native_births_in_closed_results']==1
    if source!='SPARSE_TARGETED':
        assert physical['target'] is physical['strict_joint_hit'] is physical['q_proxy'] is None


@pytest.mark.parametrize('attack',['embedded','duplicate','missing','count','rank','unknown_status'])
def test_capture_identity_count_and_unimplemented_state_reject(integration,attack):
    f=integration
    def change(v):
        r=next(r for r in v['records'] if r['candidate_id']==f.c.candidate_id)
        if attack=='embedded':r['result']['actual_response'][0]+=1
        elif attack=='duplicate':v['records'][1]=deepcopy(v['records'][0])
        elif attack=='missing':v['records'].pop()
        elif attack=='count':v['observed_native_births_in_closed_results']=2
        elif attack=='rank':v['actual_native_start_order']='FROM_CLOSED_RESULT_SUBSET'
        else:r['capture_state']='SELF_REPORTED_PASS'
    f.edit_capture(change)
    with pytest.raises(ValueError):f.consume()


@pytest.mark.parametrize('attack',['wrong_release','wrong_config','source_missing','mirror_tampered','before_birth'])
def test_authority_mirror_and_chronology_are_real_gates(integration,attack):
    f=integration;c=f.c;reader=None;kwargs={}
    if attack=='wrong_release':kwargs['release']=c.p('owner_config')
    elif attack=='wrong_config':kwargs['config']=c.p('release')
    elif attack in ('source_missing','mirror_tampered'):
        reader=c.mirror()
        if attack=='source_missing':reader.paths.pop(c.p('gds')['path'])
        else:Path(reader.paths[c.p('s4p')['path']]).write_bytes(b'TAMPERED SYNTHETIC')
    else:
        f.edit_capture(lambda v:v.update(capture_started_utc='2026-09-11T00:00:01+00:00',
            capture_completed_utc='2026-09-11T00:00:02+00:00'))
    with pytest.raises(ValueError):f.consume(reader=reader,**kwargs)


def test_runtime_is_bound_to_caller_config_not_just_self_consistent_result(integration):
    f=integration;c=f.c
    config=c.read('owner_config'); config['emx_runtime']['repo']=str(c.native/'FOREIGN_REPO')
    c.put('foreign_config',c.native/'FOREIGN_CONFIG.json',config)
    c.put('foreign_release',c.native/'FOREIGN_RELEASE.json',dict(config=c.p('foreign_config')))
    # Test the independent guard directly after the genuine feature closure.
    result=c.read('result');plan=c.read('plan');plan['release']=c.p('foreign_release')
    c.put('foreign_plan',c.native/'FOREIGN_PLAN.json',plan)
    with pytest.raises(ValueError,match='bound owner runtime'):
        evidence._closed_birth(c.mirror(),dict(result,plan=c.p('foreign_plan'),_feature_preflight=c.p('proof')),
            c.read('proof'),c.read('solver'),f.ctx,c.original,expected_release=c.p('foreign_release'),
            expected_owner_config=c.p('foreign_config'),capture_completed_utc='2026-09-11T00:10:00+00:00')


def test_no_result_capture_keeps_original_failures_and_unknown_not_zero(integration):
    f=integration;c=f.c
    out=c.root/'EMPTY_OWNER_CAPTURE'
    f.capture_module.capture(f.batch,{},c.native,out)
    c.paths['capture']=out/'CLOSED_METADATA_CAPTURE.json'
    result=f.consume()
    assert len(result['rows'])==64 and result['summary']['physical_rows']==0
    assert result['summary']['status']=='NOT_RUN_NO_VERIFIED_NATIVE_PUBLICATION'
    assert result['summary']['solver_starts'] is None
    assert sum(r['state']=='ANALYTIC_FAIL' for r in result['rows'])==14
