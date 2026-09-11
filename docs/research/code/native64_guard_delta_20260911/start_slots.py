"""Conservative, append-only start-slot bookkeeping; NOT a native dispatcher.

A reserved slot is never reported as a native start. Unknown/crashed dispatches
retain their slot and cannot be automatically retried. Native observation and
launcher integration are deliberately outside this metadata-only component.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path

from controlled_metadata import ARMS, MANIFEST_SHA, INTENT_SHA, require, parse


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def write_once(path, value):
    with Path(path).open('x',encoding='utf-8') as f:
        json.dump(value,f,sort_keys=True,indent=2,allow_nan=False)
        f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(str(Path(path).parent),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def path_ok(path):
    path=Path(path)
    require(path.is_absolute() and '..' not in path.parts,'ABSOLUTE_SLOT_PATH_REQUIRED')
    require(all(not p.is_symlink() for p in (path,*path.parents)),'SLOT_SYMLINK_PROHIBITED')
    return path


def plan(release_pin, admitted_at_utc, deadline_utc, rows, *, evidence_class):
    require(evidence_class in ('SYNTHETIC_TEST_ONLY','PRIVATE_NOT_YET_NATIVE_RELEASED'), 'NO_SELF_GRANTED_PRODUCTION_AUTHORITY')
    require(set(release_pin)=={'path','sha256','bytes'} and len(release_pin['sha256'])==64, 'RELEASE_PIN_REQUIRED')
    a,b=datetime.fromisoformat(admitted_at_utc),datetime.fromisoformat(deadline_utc)
    require(a.utcoffset() is not None and b.utcoffset() is not None and (b-a).total_seconds()==21600,
            'EXACT_SIX_HOUR_BOUND_REQUIRED')
    indexed={r['candidate_id']:{k:r[k] for k in ('request_id','candidate_id','arm','arm_order',
        'global_order','canonical_geometry_sha256','q_proxy','local_dispatch_eligible')} for r in rows}
    require(len(rows)==len(indexed)==64,'EXACT64_PLAN_REQUIRED')
    for arm in ARMS:
        require(sorted(r['arm_order'] for r in indexed.values() if r['arm']==arm)==list(range(1,33)),
                'ARM32_REQUIRED')
    return dict(schema='eucap15_controlled64_start_slot_plan.v1', evidence_class=evidence_class,
        release=release_pin, manifest_sha256=MANIFEST_SHA, intent_sha256=INTENT_SHA,
        admitted_at_utc=admitted_at_utc, deadline_utc=deadline_utc, per_arm_max=16,total_max=32,
        incremental_storage_max_bytes=2147483648, candidates=indexed,
        native_start_proof='NOT_IMPLEMENTED_THIS_COMPONENT_DOES_NOT_ATTEST_NATIVE_PROCESSES')


class SlotBook:
    def __init__(self, root, bound_plan):
        self.root=path_ok(root)
        self.plan=bound_plan
        self.sha=digest(bound_plan)

    def create(self):
        self.root.mkdir(exist_ok=False)
        write_once(self.root/'PLAN.json',self.plan)
        (self.root/'slots').mkdir()
        with (self.root/'slots.lock').open('x'):pass

    @contextmanager
    def locked(self):
        require(parse((self.root/'PLAN.json').read_bytes())==self.plan,'SLOT_PLAN_CHANGED')
        lock=path_ok(self.root/'slots.lock')
        with lock.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            try:
                require(parse((self.root/'PLAN.json').read_bytes())==self.plan,'SLOT_PLAN_CHANGED')
                yield
            finally:fcntl.flock(f,fcntl.LOCK_UN)

    def records(self):
        out=[]
        for p in sorted((self.root/'slots').iterdir()):
            path_ok(p)
            require(p.is_file() and p.suffix=='.json','UNEXPECTED_SLOT_ENTRY')
            row=parse(p.read_bytes())
            require(row['plan_sha256']==self.sha and row['candidate_id'] in self.plan['candidates'],
                    'FOREIGN_OR_CHANGED_SLOT')
            expected=self.plan['candidates'][row['candidate_id']]
            require(row['candidate']==expected and row['arm']==expected['arm'] and
                    p.name==hashlib.sha256(row['candidate_id'].encode()).hexdigest()+'.json', 'SLOT_IDENTITY_MISMATCH')
            require(row['status']=='RESERVED_NOT_NATIVE_PROOF' and row['native_started'] is None,
                    'RESERVATION_IS_NOT_NATIVE_START')
            require(type(row.get('global_slot')) is int and type(row.get('arm_slot')) is int,
                    'SLOT_SEQUENCE_INTEGER_REQUIRED')
            out.append(row)
        require(len(out)<=self.plan['total_max'] and all(sum(x['arm']==a for x in out)<=self.plan['per_arm_max'] for a in ARMS),
                'EXISTING_SLOT_BUDGET_EXCEEDED')
        require(sorted(x['global_slot'] for x in out)==list(range(1,len(out)+1)),
                'GLOBAL_SLOT_SEQUENCE_GAP_OR_DUPLICATE')
        for arm in ARMS:
            require(sorted(x['arm_slot'] for x in out if x['arm']==arm)==list(range(1,1+sum(x['arm']==arm for x in out))),
                    'ARM_SLOT_SEQUENCE_GAP')
        return out

    def reserve(self,candidate_id,preflight_pin,gds_pin,command,now_utc,observed_incremental_bytes):
        with self.locked():
            rows=self.records()
            require(candidate_id in self.plan['candidates'],'FOREIGN_CANDIDATE')
            c=self.plan['candidates'][candidate_id]
            for pin in (preflight_pin,gds_pin):
                require(set(pin)=={'path','sha256','bytes'},'BOUND_STAGE_PIN_REQUIRED')
            require(isinstance(command,list) and command and all(isinstance(x,str) for x in command),'EXACT_ARGV_REQUIRED')
            binding=dict(preflight=preflight_pin,gds=gds_pin,command=command)
            existing=next((x for x in rows if x['candidate_id']==candidate_id),None)
            if existing:
                require(existing['launch_binding']==binding,'REPEATED_SLOT_BINDING_CHANGED')
                return dict(status='EXISTING_SLOT_NO_AUTOMATIC_REDISPATCH',created=False,slot=existing)
            require(c['local_dispatch_eligible'] is True,'HELD_PROPOSAL_NO_SLOT')
            now=datetime.fromisoformat(now_utc)
            require(now.utcoffset() is not None,'AWARE_TIME_REQUIRED')
            if now<datetime.fromisoformat(self.plan['admitted_at_utc']):raise ValueError('BEFORE_ADMISSION_ORIGIN')
            if now>=datetime.fromisoformat(self.plan['deadline_utc']):
                return dict(status='NOT_DISPATCHED_DEADLINE',created=False,slot=None)
            require(type(observed_incremental_bytes) is int and observed_incremental_bytes>=0,'ACTUAL_STORAGE_OBSERVATION_REQUIRED')
            if observed_incremental_bytes>=self.plan['incremental_storage_max_bytes']:
                return dict(status='NOT_DISPATCHED_STORAGE_CAP',created=False,slot=None)
            arm_n=sum(x['arm']==c['arm'] for x in rows)
            if arm_n>=self.plan['per_arm_max'] or len(rows)>=self.plan['total_max']:
                return dict(status='NOT_DISPATCHED_ARM_START_CAP',created=False,slot=None)
            row=dict(schema='eucap15_controlled64_start_slot.v1',status='RESERVED_NOT_NATIVE_PROOF',
                evidence_class=self.plan['evidence_class'],plan_sha256=self.sha,candidate_id=candidate_id,
                candidate=c,arm=c['arm'],arm_slot=arm_n+1,global_slot=len(rows)+1,reserved_utc=now_utc,
                launch_binding=binding,observed_incremental_bytes=observed_incremental_bytes,
                native_started=None,native_process_evidence=None,automatic_redispatch_allowed=False)
            name=hashlib.sha256(candidate_id.encode()).hexdigest()+'.json'
            write_once(self.root/'slots'/name,row)
            return dict(status='RESERVED_NOT_NATIVE_PROOF',created=True,slot=row)

    def summary(self):
        with self.locked():
            rows=self.records()
            return dict(schema='eucap15_controlled64_start_slot_summary.v1',plan_sha256=self.sha,
                reserved_slots=len(rows),reserved_by_arm={a:sum(r['arm']==a for r in rows) for a in ARMS},
                actual_native_starts=None,native_observer='NOT_IMPLEMENTED',
                dispatch_authorization='NONE_METADATA_ONLY', automatic_retry_of_existing_slot=False)
