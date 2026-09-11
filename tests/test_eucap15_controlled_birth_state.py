"""Birth-only synthetic metadata checks; no physical/native/model execution.

Exercise the actual _closed_birth path with content-pinned in-memory documents.
No existing suite or real RESULT/FEATURE/GDS/S4P is read or recomputed.
"""
from copy import deepcopy
import hashlib
import json
import socket
import subprocess

import pytest

from research.broadband56_nn import eucap15_controlled_evidence as evidence


def opaque(name):
    raw=name.encode()
    return dict(path='/synthetic-controlled/'+name,
                sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


class MetadataReader:
    def __init__(self): self.documents={}

    def put(self,name,value):
        raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        p=dict(path='/synthetic-controlled/'+name,
               sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
        self.documents[p['path']]=(p,deepcopy(value))
        return p

    def document(self,p):
        expected,value=self.documents[p['path']]
        assert p==expected
        return deepcopy(value)


class BirthCase:
    def __init__(self):
        self.reader=MetadataReader()
        self.original=dict(request_id='synthetic-request',candidate_id='synthetic-request-q10',
            arm='COVERAGE_DIRECTED',arm_order=1,global_order=1,
            canonical_geometry_sha256='a'*64,q_proxy=10,local_dispatch_eligible=True)
        self.batch=dict(manifest=opaque('manifest'),intent=opaque('intent'),
                        rows={self.original['candidate_id']:self.original})
        self.command=['/synthetic-controlled/wrapper','/synthetic-controlled/gds','TRANSFORMER']
        exe=opaque('native-never-executed')
        runtime=dict(repo='/synthetic-controlled/repo',source_pins=[],
                     emx_wrapper=opaque('wrapper'),process_file=opaque('process'))
        self.config=self.reader.put('config',dict(original_manifest=self.batch['manifest'],
            emx_runtime=dict(runtime,native_executable=exe)))
        self.release=self.reader.put('release',dict(config=self.config))
        self.proof=dict(request=self.reader.put('request',dict(runtime=runtime)),
                        gds=opaque('gds'),command=self.command)
        self.preflight=opaque('preflight')
        plan=dict(schema='eucap15_controlled64_start_slot_plan.v1',release=self.release,
            manifest_sha256=self.batch['manifest']['sha256'],intent_sha256=self.batch['intent']['sha256'],
            per_arm_max=16,total_max=32,incremental_storage_max_bytes=2147483648,
            candidates=self.batch['rows'],admitted_at_utc='2026-09-11T00:00:00+00:00',
            deadline_utc='2026-09-11T06:00:00+00:00')
        self.plan=self.reader.put('plan',plan)
        plan_sha=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        self.slot=dict(plan_sha256=plan_sha,candidate_id=self.original['candidate_id'],
            candidate=self.original,arm=self.original['arm'],arm_slot=1,global_slot=1,
            launch_binding=dict(preflight=self.preflight,gds=self.proof['gds'],command=self.command))
        process=dict(pid=124,ppid=123,start_ticks=457,uid=789,state='R',
                     argv=[exe['path'],*self.command[1:]])
        ancestor=dict(pid=123,ppid=1,start_ticks=456,uid=789,state='S',argv=self.command)
        self.birth=dict(schema='eucap15_controlled64_native_birth.v1',status='OBSERVED_EXACT_NATIVE_PROCESS',
            native_started=True,candidate=self.original,arm=self.original['arm'],arm_slot=1,global_slot=1,
            plan_sha256=plan_sha,launch_binding=self.slot['launch_binding'],command=self.command,
            expected_executable=exe,executable_sha256=exe['sha256'],executable_bytes=exe['bytes'],
            process=process,ancestor=ancestor,ancestry=[deepcopy(process),deepcopy(ancestor)],
            observed_utc='2026-09-11T00:01:00+00:00')

    def inspect(self):
        obs=self.reader.put('observation',dict(schema='eucap15_controlled64_native_observation.v1',
            status='ONE_NATIVE_BIRTH_OBSERVED',actual_native_starts=1,native_starts_observed=1,
            errors=[],observations=[self.birth],slot=self.slot,wrapper_pid=123,
            ended_utc='2026-09-11T00:01:01+00:00'))
        return evidence._closed_birth(self.reader,
            dict(plan=self.plan,native_observation=obs,_feature_preflight=self.preflight),
            self.proof,dict(artifacts=[obs],ended_utc='2026-09-11T00:01:02+00:00'),
            self.batch,self.original,expected_release=self.release,expected_owner_config=self.config,
            capture_completed_utc='2026-09-11T00:10:00+00:00')


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def forbidden(*args,**kwargs): raise AssertionError('Birth-only metadata check forbids execution')
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(evidence,'inspect_feature_chain',forbidden)
    monkeypatch.setattr(evidence.frame,'load_context',forbidden)


@pytest.mark.parametrize('role,direct_state,chain_state',[
    ('ancestor','R','S'),('ancestor','S','R'),('process','R','S'),('process','S','R')])
def test_r_s_observation_transition_keeps_exact_birth(role,direct_state,chain_state):
    f=BirthCase();f.birth[role]['state']=direct_state
    f.birth['ancestry'][0 if role=='process' else 1]['state']=chain_state
    original=deepcopy(f.birth)
    row=f.inspect()
    assert row['native_birth_identity_verified'] is True
    assert row['native_count_in_this_result']==1
    assert row['native_birth_process']==f.birth['process']
    assert row['solver_start_verified'] is False
    assert row['solver_start_order'] is row['solver_started_utc'] is None
    assert f.birth==original


@pytest.mark.parametrize('role',['process','ancestor'])
@pytest.mark.parametrize('key',['pid','start_ticks','uid','ppid','argv'])
def test_stable_identity_drift_is_rejected_even_with_rehashed_metadata(role,key):
    f=BirthCase()
    if key=='argv':f.birth[role][key]=[*f.birth[role][key],'foreign-argument']
    else:f.birth[role][key]+=1
    with pytest.raises(ValueError,match='Native ancestry identity conflict'):f.inspect()


@pytest.mark.parametrize('key',['pid','start_ticks','uid','ppid','argv','state'])
def test_missing_direct_identity_or_state_fails_closed(key):
    f=BirthCase();del f.birth['ancestor'][key]
    with pytest.raises(ValueError,match='Missing process identity or state'):f.inspect()


@pytest.mark.parametrize('location',['process','ancestor','chain_process','chain_ancestor'])
@pytest.mark.parametrize('state',['Z',None])
def test_native_wrapper_and_chain_need_explicit_live_state(location,state):
    f=BirthCase()
    p=f.birth['ancestry'][0 if location=='chain_process' else 1] if location.startswith('chain_') else f.birth[location]
    p['state']=state
    with pytest.raises(ValueError,match='non-live process state'):f.inspect()


def test_different_chain_birth_is_not_a_state_transition():
    f=BirthCase();f.birth['ancestry'][0]['start_ticks']+=1
    with pytest.raises(ValueError,match='Native ancestry identity conflict'):f.inspect()
