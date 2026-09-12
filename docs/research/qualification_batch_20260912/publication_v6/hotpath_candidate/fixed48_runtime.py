"""Per-tool permits within one native owner's bounded executor."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import threading
import time

import controlled_execution as e
import controlled_metadata as m
import native_resource_probe as probe
import native_birth
import start_slots as slots
from fixed48_capacity import ResourceHistory, InflightReservations, TOOLS, stamp


def clock():return datetime.now(timezone.utc).isoformat()


def suffix():return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def pointer(path, value):
    temporary=path.with_name(path.name+'.'+suffix()+'.tmp')
    slots.write_once(temporary,value)
    os.replace(temporary,path)


def own_peers(config, identity):
    """Only this exact still-live owner's descendants share its admission."""
    current=native_birth.proc_info(identity['pid'])
    m.require(current['start_ticks']==identity['start_ticks'] and current['uid']==os.getuid()
        and current['state'] not in ('Z','T','t'),'AUTHORITATIVE_OWNER_NOT_ALIVE')
    peers=probe.competing_processes(config);foreign=[]
    for item in peers:
        pid=item['pid'];seen=set();allowed=False
        while pid and pid not in seen:
            seen.add(pid)
            try:info=native_birth.proc_info(pid)
            except (FileNotFoundError,ProcessLookupError):break
            if info['uid']!=os.getuid():break
            if pid==identity['pid']:
                allowed=info['start_ticks']==identity['start_ticks'];break
            pid=info['ppid']
        if not allowed:foreign.append(item)
    return foreign


class Manager:
    def __init__(self, owner):
        self.owner=owner;self.config=owner.config;self.policy=self.config['fixed48_policy']
        m.require(self.policy['candidate_pipeline_capacity']==64 and
            self.policy['tool_executor_capacity']['emx']==48,'DISTINCT_PIPELINE_AND_EMX_CAPACITIES_REQUIRED')
        self.root=owner.out/'fixed48';self.root.mkdir(exist_ok=False)
        self.identity=native_birth.proc_info(os.getpid())
        self.binding=dict(release=owner.release_pin,owner={k:self.identity[k] for k in ('pid','start_ticks','uid')})
        self.history=ResourceHistory(self.binding)
        # Resource publication must never wait for release hashing or disk walks.
        # Dispatch/claims remain serialized; the nested order is dispatch -> state.
        self.lock=threading.RLock();self.dispatch_lock=threading.RLock()
        self.stop=threading.Event();self.fault=None
        self.permits={};self.last_pin=None
        self.storage=InflightReservations(m.MAX_INCREMENTAL_BYTES)
        self.storage_pin=None
        self.thread=threading.Thread(target=self.sample_loop,name='fixed48-resource-sampler',daemon=True)

    def start(self):self.thread.start()

    def sample_loop(self):
        try:
            while not self.stop.is_set():
                at=clock();before=probe.counters();began=time.monotonic()
                if self.stop.wait(60):return
                after=probe.counters();elapsed=time.monotonic()-began
                cad=probe.license_query('/cae/apps/env/cadence-EMX_25.10.000','CDS_LIC_FILE')
                cal=probe.license_query('/cae/apps/env/mentor-CALIBRE_2024.4_39','LM_LICENSE_FILE')
                c,d=cad['free'],cal['free']
                licences=dict(cadence=max([0,*[v for k,v in c.items() if k.startswith('virtuoso_layout_suite_')]]) if cad['pass_query'] else 0,
                    calibre=max(0,d.get('calibredrc',0)) if cal['pass_query'] else 0,
                    emx=max(0,min(c.get('emx_modelgen',0),c.get('emx_solver',0))) if cad['pass_query'] else 0)
                state=probe.evaluate(self.config,before,after,elapsed,{t:True for t in TOOLS},
                    shutil.disk_usage(self.owner.out).free,os.getloadavg(),os.cpu_count(),own_peers(self.config,self.identity))
                del state['checks']['licenses']
                state['binding']=self.binding;state['started_utc']=at
                state['free_license_slots']=licences;state['license_evidence']=dict(cadence=cad,calibre=cal)
                state['normalized_load1']=state['load'][0]/state['logical_cpus']
                state['normalized_load5']=state['load'][1]/state['logical_cpus']
                # The approved fixed48 CPU gate replaces the old serial
                # load<cpus-2 shortcut. Measured idle CPU is budgeted below.
                state['checks']['cpu']=(state['logical_cpus']>=4 and
                    state['normalized_load1']<=1.10 and state['normalized_load5']<=1.10)
                state['status']='PASS' if all(state['checks'].values()) else 'WAIT'
                state['failed_checks']=[key for key,value in state['checks'].items() if not value]
                state['total_memory_bytes']=after['memory']['MemTotal']
                delta=state['cpu_deltas'];state['idle_cpu_equivalents']=state['logical_cpus']*delta[3]/sum(delta) if state['cpu_sample_valid'] else 0
                p=self.root/('RESOURCE_'+suffix()+'.json');slots.write_once(p,state)
                resource_pin=e.pin(p)
                streak=self.publish_resource(state,resource_pin)
                self.owner.event('FIXED48_RESOURCE_OBSERVED',evidence=e.pin(p),
                    healthy_check_streak=streak,free_license_slots=licences)
        except BaseException as error:
            with self.lock:self.fault=type(error).__name__+': '+str(error)
            self.owner.event('FIXED48_SAMPLER_FAULT_STOP_NEW_DISPATCH',error=self.fault)

    def publish_resource(self,state,resource_pin):
        with self.lock:
            self.history.observe(state,resource_pin);self.last_pin=resource_pin
            streak=self.history.streak
        pointer(self.root/'LATEST_RESOURCE.json',resource_pin)
        return streak

    def counts(self,exclude=None):
        with self.lock:
            reflected={t:0 for t in TOOLS};pending={t:0 for t in TOOLS}
            began=stamp(self.history.last['started_utc']) if self.history.last else float('-inf')
            for rid,value in self.permits.items():
                if rid==exclude:continue
                (pending if stamp(value['issued_utc'])>began else reflected)[value['tool']]+=1
            return reflected,pending

    def resource_decision(self,exclude=None):
        with self.lock:
            running,pending=self.counts(exclude)
            return self.history.limits(clock(),running,pending,self.policy),self.last_pin

    def claimed_count(self):
        with self.storage.lock:return len(self.storage.claims)

    def storage_snapshot(self):
        allocations={rid:e.allocated_bytes(self.owner.out/rid) for rid in self.storage.claims}
        self.storage.refresh(allocations)
        total=e.allocated_bytes(self.config['budget_root'])
        record=dict(schema='eucap15_fixed48_inflight_storage.v1',binding=self.binding,utc=clock(),
            budget_root=self.config['budget_root'],ceiling_bytes=m.MAX_INCREMENTAL_BYTES,
            total_allocated_bytes=total,claims=self.storage.claims,
            remaining_projected_bytes=sum(v['remaining'] for v in self.storage.claims.values()),
            allocation_by_candidate=allocations,reservations_are_estimates_not_hard_peak_bounds=True)
        p=self.root/('STORAGE_'+suffix()+'.json');slots.write_once(p,record)
        self.storage_pin=e.pin(p);pointer(self.root/'LATEST_STORAGE.json',self.storage_pin)
        return record

    def require_wait_ready(self):
        with self.lock:fault=self.fault
        if fault:raise RuntimeError('FIXED48_SHARED_RESOURCE_FAULT: '+fault)
        if self.stop.is_set():raise RuntimeError('FIXED48_OWNER_DRAINING')
        self.owner.deadline()

    def require_ready(self):
        self.require_wait_ready();self.owner.verify_release()
        if e.allocated_bytes(self.config['budget_root'])>=m.MAX_INCREMENTAL_BYTES:
            from run_development_native import BudgetEnded
            raise BudgetEnded('INCREMENTAL_STORAGE_CAP_NO_CHILD_INTERRUPTED')

    def admit_candidate_count(self):
        self.require_wait_ready()
        decision,_=self.resource_decision()
        if not any(decision['additional'].values()):return self.claimed_count()
        with self.dispatch_lock:
            self.require_wait_ready()
            decision,_=self.resource_decision()
            # Already started candidates may wait for their own tool permit.
            # Their execution is drained, never cancelled by this lower count.
            if not any(decision['additional'].values()):return self.claimed_count()
            self.require_ready()
            state=self.storage_snapshot()
            self.require_wait_ready()
            decision,_=self.resource_decision()
            if not any(decision['additional'].values()):return self.claimed_count()
            headroom=m.MAX_INCREMENTAL_BYTES-state['total_allocated_bytes']-state['remaining_projected_bytes']
            new=max(0,headroom//self.policy['candidate_projected_peak_bytes'])
            return min(self.policy['candidate_pipeline_capacity'],self.claimed_count()+new)

    @contextmanager
    def candidate(self,job):
        rid=job['request_id']
        with self.dispatch_lock:
            self.require_ready();state=self.storage_snapshot()
            m.require(self.storage.claim(rid,e.allocated_bytes(self.owner.out/rid),state['total_allocated_bytes'],
                self.policy['candidate_projected_peak_bytes']),'PROJECTED_INFLIGHT_STORAGE_UNAVAILABLE')
            self.storage_snapshot()
        try:yield
        finally:
            with self.dispatch_lock:
                self.storage.release(rid);self.storage_snapshot()

    def acquire(self,root,tool):
        rid=root.name
        self.release(rid)
        while True:
            self.require_wait_ready()
            decision,_=self.resource_decision()
            if decision['additional'][tool]>0:
                with self.dispatch_lock:
                    # A queued waiter must recheck capacity before expensive work.
                    self.require_wait_ready()
                    decision,_=self.resource_decision()
                    if decision['additional'][tool]>0:
                        self.require_ready()
                        state=self.storage_snapshot()
                        fits=state['total_allocated_bytes']+state['remaining_projected_bytes']<=m.MAX_INCREMENTAL_BYTES
                        self.require_wait_ready()
                        with self.lock:
                            # Never issue the decision sampled before heavy I/O.
                            decision,resource_pin=self.resource_decision()
                            if fits and decision['additional'][tool]>0 and not self.fault:
                                value=dict(schema='eucap15_fixed48_tool_permit.v1',binding=self.binding,
                                    request_id=rid,tool=tool,issued_utc=clock(),resource=resource_pin,
                                    storage=self.storage_pin,decision=decision,active=True)
                                self.permits[rid]=value
                            else:value=None
                        if value is not None:
                            # Reserve before persistence, without blocking sampling.
                            p=root/(tool+'_PERMIT_'+suffix()+'.json');slots.write_once(p,value)
                            pointer(root/'ACTIVE_TOOL_PERMIT.json',e.pin(p))
                            self.require_wait_ready()
                            with self.lock:
                                latest,_=self.resource_decision(exclude=rid)
                                age=stamp(clock())-stamp(value['decision']['observed_utc'])
                                eligible=(0<=age<=90 and latest['additional'][tool]>0 and not self.fault)
                                permit_count=len(self.permits)
                            if eligible:
                                self.owner.event('FIXED48_TOOL_PERMIT_GRANTED',request=rid,tool=tool,permit=e.pin(p),
                                    requested_emx=48,executor_capacity=48,wrapper_permits=permit_count,native_concurrency_proven=False)
                                return
                            # A slow write can itself age a snapshot; keep its
                            # evidence, revoke it, and wait without launching.
                            self.release(rid)
                            self.owner.event('FIXED48_PERMIT_REVOKED_BEFORE_TOOL_START',request=rid,tool=tool,permit=e.pin(p))
            if self.stop.wait(2):raise RuntimeError('FIXED48_OWNER_DRAINING')

    def release(self,rid):
        with self.dispatch_lock:
            with self.lock:value=self.permits.pop(rid,None)
            if value is not None:
                p=self.owner.out/rid/('PERMIT_RELEASED_'+suffix()+'.json')
                slots.write_once(p,dict(issued=value,released_utc=clock()))
                pointer(self.owner.out/rid/'ACTIVE_TOOL_PERMIT.json',dict(active=False,release=e.pin(p)))

    def close(self):
        self.stop.set();self.thread.join(timeout=90)
        m.require(not self.thread.is_alive(),'SAMPLER_NOT_DRAINED')


def verify_emx_permit(config, release_pin, row):
    root=Path(config['out'])/row['request_id']
    identity=e.document(e.pin(root/'ACTIVE_TOOL_PERMIT.json'))
    value=e.document(identity)
    m.require(value['binding']['release']==release_pin and value['request_id']==row['request_id'] and
        value['tool']=='emx' and value['active'] is True,'EXACT_ACTIVE_EMX_PERMIT_REQUIRED')
    m.require(not own_peers(config,value['binding']['owner']),'FOREIGN_NATIVE_CHAIN')
    resource=e.document(value['resource']);storage=e.document(value['storage'])
    m.require(resource['binding']==storage['binding']==value['binding'],'PERMIT_SNAPSHOT_BINDING')
    age=stamp(clock())-stamp(resource['utc'])
    m.require(0<=age<=90 and all(resource['checks'].values()) and
        value['decision']['healthy_check_streak']>=5 and value['decision']['additional']['emx']>=1,
        'STALE_OR_INELIGIBLE_NATIVE_PERMIT')
    return value
