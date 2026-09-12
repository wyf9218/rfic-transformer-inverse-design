"""New bounded geometry256 window using the existing physical-stage owner."""
import argparse
from contextlib import ExitStack
import csv
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import time

import controlled_execution as execution
import controlled_metadata as m
import controlled_result as results
import closed_export
import native_birth
import start_slots as slots
from native_resource_probe import competing_processes, probe, utc
from run_development_native import Owner as PreviousOwner, CandidateFailure, BudgetEnded, read, write, frozen


class Owner(PreviousOwner):
    def __init__(self, release_path):
        self.release_pin=execution.pin(release_path)
        self.release=execution.document(self.release_pin)
        self.config=execution.document(self.release['config'])
        self.config_path=self.release['config']['path']
        sys.path[:0]=[self.config['code_root'],self.config['repo']]
        from research.broadband56_nn.frequency_research_emx import pin,verify,global_lease,guard
        from research.broadband56_nn.eucap15_controlled_context import build_request
        guard();self.pin=pin;self.verify=verify;self.lease=global_lease;self.build=build_request
        self.out=Path(self.config['out']);self.fds=();self.binding=None
        self.verify_release()
        self.batch=m.load_batch(self.config['original_manifest'],self.config['path_map'])
        self.jobs=list(self.batch.rows)
        self.env={k:v for k,v in os.environ.items() if not k.startswith(('BROADBAND56_','FREQUENCY_RESEARCH_OPERATIONAL'))}
        self.env.update(PYTHONPATH=self.config['code_root']+os.pathsep+self.config['repo'],
            PYTHONOPTIMIZE='0',PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2')
        ledger=Path(self.config['budget_root'])/'start_ledger'
        if (ledger/'PLAN.json').exists():
            self.binding=dict(release=self.release_pin,plan=self.pin(ledger/'PLAN.json'),ledger_root=str(ledger))
            execution.load_execution(self.binding,self.batch)

    def verify_release(self):
        super().verify_release()
        m.require(self.pin(__file__) in self.release['sources'],'WRONG_CONTROLLED_OWNER')
        m.require(self.release['schema']=='eucap15_production256_delegated_release.v1' and
            self.release['authority']['type']=='PROJECT_OWNER_STANDING_AUTHORIZATION' and
            self.release['authority']['scope']=='15GHZ_EUCAP_NATIVE_ONLY_NO_NN','DELEGATED_15GHZ_SCOPE_REQUIRED')
        m.require(Path(sys.executable).resolve()==Path(self.config['python']).resolve(),'PRIVATE_PYTHON_CHANGED')
        self.verify(self.config['emx_runtime']['native_executable'])
        m.require(self.config['max_native_concurrency']==(48 if self.release.get('concurrency_amendment') or self.release.get('successor_registration') else 1),
            'EXACT_AUTHORIZED_NATIVE_OWNER_CAPACITY_REQUIRED')

    def predecessor_closed(self):
        if self.release.get('successor_registration'):
            from batch_registration import require_parent_terminal
            execution.plan_release(self.release,self.config)
            require_parent_terminal(self.release['successor_registration']['parent_release'])
            m.require(not competing_processes(self.config),'SURVIVING_NATIVE_CHAIN')
            return
        if self.release.get('concurrency_amendment'):
            a=self.release['concurrency_amendment']
            execution.plan_release(self.release,self.config)
            start=execution.document(a['parent_start'])
            try:live=native_birth.proc_info(start['pid'])
            except (FileNotFoundError,ProcessLookupError):live=None
            m.require(live is None or live['state']=='Z' or live['start_ticks']!=int(start['start_ticks']),
                'IMMEDIATE_FIXED48_PARENT_STILL_ALIVE')
            m.require(not competing_processes(self.config),'SURVIVING_NATIVE_CHAIN')
            return
        recovery=self.release.get('startup_recovery')
        if recovery is not None:
            execution.plan_release(self.release,self.config)
            start=execution.document(recovery['parent_start'])
            try: live=native_birth.proc_info(start['pid'])
            except (FileNotFoundError,ProcessLookupError): live=None
            m.require(live is None or live['state']=='Z' or live['start_ticks']!=int(start['start_ticks']),
                'RECOVERY_PARENT_STILL_ALIVE')
            m.require(not competing_processes(self.config),'SURVIVING_NATIVE_CHAIN')
            return
        previous=self.config['predecessor']
        self.verify(previous['terminal']);self.verify(previous['release']);self.verify(previous['start'])
        start=read(previous['start']['path'])
        m.require(start['release']==previous['release'] and
            start['pid']==previous['process_identity']['pid'] and
            int(start['start_ticks'])==previous['process_identity']['start_ticks'], 'PREDECESSOR_START_BINDING')
        terminal=read(previous['terminal']['path'])
        m.require(terminal['status']=='ALL_ORIGINAL64_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE' and terminal['release']==previous['release'] and
            len(terminal['results'])==64 and len({r['path'] for r in terminal['results']})==64,
            'ACTUAL_PREDECESSOR64_NOT_CLOSED')
        identity=previous['process_identity']
        try:
            live=native_birth.proc_info(identity['pid'])
        except (FileNotFoundError,ProcessLookupError):
            live=None
        m.require(live is None or live['state']=='Z' or live['start_ticks']!=identity['start_ticks'],
            'OLD_OWNER_STILL_ALIVE')
        m.require(not competing_processes(self.config),'SURVIVING_NATIVE_CHAIN')

    def event(self,status,**data):
        if status=='NATIVE_STARTED':status='TOOL_WRAPPER_STARTED_NOT_NATIVE_BIRTH'
        return super().event(status,**data)

    def deadline(self):
        if self.binding:
            plan=execution.document(self.binding['plan'])
            if datetime.now(timezone.utc)>=datetime.fromisoformat(plan['deadline_utc']):
                raise BudgetEnded('DISPATCH_DEADLINE_NO_CHILD_INTERRUPTED')

    def ensure_plan(self, admission_pin):
        if self.binding:return
        admitted=datetime.now(timezone.utc)
        ledger=Path(self.config['budget_root'])/'start_ledger'
        plan=slots.plan(self.release_pin,admitted.isoformat(),(admitted+timedelta(seconds=m.MAX_WALL_SECONDS)).isoformat(),
            self.jobs,evidence_class='DELEGATED_NATIVE_RELEASE',
            manifest_sha256=self.batch.manifest_pin['sha256'],intent_sha256=self.batch.intent_pin['sha256'])
        slots.SlotBook(ledger,plan).create()
        self.binding=dict(release=self.release_pin,plan=self.pin(ledger/'PLAN.json'),ledger_root=str(ledger))
        self.event('FIRST_NATIVE_CANDIDATE_ADMISSION',resource_receipt=admission_pin,plan=self.binding['plan'])

    def admit(self,root,stage):
        while True:
            self.deadline();self.verify_release()
            if execution.allocated_bytes(self.config['budget_root']) >= m.MAX_INCREMENTAL_BYTES:
                raise BudgetEnded('INCREMENTAL_STORAGE_CAP_NO_CHILD_INTERRUPTED')
            state=probe(self.config)
            path=root/(stage+'_RESOURCE_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
            write(path,state)
            if state['status']=='PASS':
                self.ensure_plan(self.pin(path))
                self.deadline()
                return
            self.event('RESOURCE_WAIT',request=root.name,stage_name=stage,evidence=self.pin(path),failed=state['failed_checks'])
            time.sleep(30)

    def budget_decision(self,job):
        if not self.binding:return None
        _,config,plan,book=execution.load_execution(self.binding,self.batch)
        with book.locked():decision=execution.budget_state(config,plan,book.records(),job)
        if decision['status']=='ELIGIBLE_WITHIN_BUDGET':return None
        p=self.out/job['request_id']/('BUDGET_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        slots.write_once(p,decision)
        return results.budget_hold(self.batch,job['request_id'],decision_pin=self.pin(p),plan_pin=self.binding['plan'])

    def cadence_command(self, root):
        policy = self.config['endpoint_policy']
        m.require(policy in ('shared_port_edges_20260912_v1', 'shared_port_edges_lineage_20260912_v2'),
            'UNSUPPORTED_BOUND_ENDPOINT_POLICY')
        return [*super().cadence_command(root), '--port-endpoint-policy', policy]

    def candidate(self,job):
        root=self.out/job['request_id'];bindings=read(root/'OWNER_BINDINGS.json')
        frozen(root/'GDS_REQUEST.json',self.build(self.config['original_manifest'],job['request_id'],'gds',bindings))
        self.stage(root,'cadence',self.cadence_command(root),root/'cadence_only',root/'cadence_only/parallel_candidate_queue_dataset_summary.json')
        audit=root/'gds_audit'
        self.stage(root,'gds_audit',self.module('frequency_research_gds_audit','--request',root/'GDS_REQUEST.json','--out',audit),audit,audit/'REQUEST_GDS_AUDIT.json',native=False)
        a=read(audit/'REQUEST_GDS_AUDIT.json')
        if a['N_audit_pass']!=1:raise CandidateFailure('ACTUAL_GDS_AUDIT_REJECTED')
        drc=root/'calibre';req=root/'CALIBRE_REQUEST.json'
        frozen(req,dict(schema='frequency_research_calibre_request.v1',input_index=a['calibre_input'],
            script=self.config['calibre_script'],gds_hash_source=self.config['calibre_gds_hash'],
            runtime_sources=list(self.config['gds_runtime']['core_sources'].values()),repo=self.config['repo'],
            out=str(drc),global_lock_path=self.config['global_lock_path']))
        wrapper=self.release['calibre_wrapper']['path']
        loader="import importlib.util,sys; s=importlib.util.spec_from_file_location('research.broadband56_nn.frequency_research_calibre',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.run(sys.argv[2],int(sys.argv[3]))"
        self.stage(root,'calibre',[self.config['python'],'-B','-c',loader,wrapper,str(req),str(self.fds[1])],drc,drc/'RESEARCH_WRAPPER_RECEIPT.json')
        with (drc/'drc_index.csv').open(newline='') as f:drc_rows=list(csv.DictReader(f))
        if len(drc_rows)!=1 or drc_rows[0]['overall_status']!='PASS':raise CandidateFailure('CALIBRE_ZERO_BLOCKING_NOT_PASS')
        plan=execution.document(self.binding['plan'])
        bindings['emx']=dict(gds_audit=self.pin(audit/'REQUEST_GDS_AUDIT.json'),calibre_index=self.pin(drc/'drc_index.csv'),
            private_config=self.config['configuration'],runtime=self.config['emx_runtime'],global_lock_path=self.config['global_lock_path'],
            resource_budget=self.config['resource_budget'],dispatch_deadline_utc=plan['deadline_utc'],controlled_execution=self.binding)
        req=root/'EMX_REQUEST.json';frozen(req,self.build(self.config['original_manifest'],job['request_id'],'emx',bindings))
        output=root/'emx_selected'
        self.stage(root,'emx',self.module('frequency_research_emx','run','--request',req,'--output',output,
            '--inherited-global-lease-fd',self.fds[1]),output,output/'features/FEATURE_RECEIPT.json')
        return results.fresh_candidate(self.batch,job['request_id'],feature_pin=self.pin(output/'features/FEATURE_RECEIPT.json'),
            observation_pin=self.pin(output/'solve/native_observation/OBSERVATION_RECEIPT.json'),plan_pin=self.binding['plan'],
            execution_release=self.release_pin)

    def capture_result(self,job):
        root=self.out/job['request_id']
        export=self.out.parent/'exports'/job['request_id']
        closed_export.capture(self.batch,{job['request_id']:self.pin(root/'RESULT.json')},self.out,export)
        if self.binding:
            execution.full_start_capture(self.binding,self.batch,export/'FULL_NATIVE_START_LEDGER.json')
        self.event('NEW_RESULT_CAPTURED',request=job['request_id'],capture=self.pin(export/'CLOSED_METADATA_CAPTURE.json'))

    def run(self):
        self.predecessor_closed()
        with self.lease(self.out/'OWNER.lock'):
            while True:
                if competing_processes(self.config):
                    self.event('SHARED_NATIVE_BUSY_NO_DISPATCH');time.sleep(60);continue
                with ExitStack() as stack:
                    try:
                        prod=stack.enter_context(self.lease(self.config['production_lock_path']))
                        research=stack.enter_context(self.lease(self.config['global_lock_path']))
                    except BlockingIOError:
                        self.event('SHARED_LEASE_BUSY_NO_DISPATCH');time.sleep(60);continue
                    self.predecessor_closed();self.fds=(prod,research)
                    self.event('EXCLUSIVE_PRODUCTION256_OWNER',release=self.release_pin)
                    for job in self.jobs:
                        root=self.out/job['request_id']
                        if (root/'RESULT.json').exists():continue
                        if not job['analytic_pass']:
                            value=results.original_hold(self.batch,job['request_id'])
                        elif job['request_id'] in self.config['intake_duplicate_ids']:
                            value=results.intake_hold(self.batch,job['request_id'],self.config['dedup_receipt'])
                        else:
                            value=self.budget_decision(job)
                            if value is None:
                                try:value=self.candidate(job)
                                except BudgetEnded:
                                    value=self.budget_decision(job)
                                    if value is None:raise
                                except CandidateFailure as error:
                                    evidence=[self.pin(p) for p in sorted(root.glob('*_PROCESS.json'))]
                                    value=results.failed_candidate(self.batch,job['request_id'],error=str(error),stage_evidence=evidence)
                        results.publish(root/'RESULT.json',value)
                        self.capture_result(job)
                    write(self.out/'BATCH_RECEIPT.json',dict(status='ALL_NEW256_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE',
                        utc=utc(),release=self.release_pin,plan=self.binding,N_original_requests=256,
                        results=[self.pin(self.out/j['request_id']/'RESULT.json') for j in self.jobs],production_accepted_added=0))
                    return


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--release',required=True)
    parser.add_argument('--preflight-only',action='store_true');args=parser.parse_args()
    owner=Owner(args.release)
    if args.preflight_only:
        owner.predecessor_closed()
        from research.broadband56_nn.frequency_research_gds_audit import load_request
        first=next(j for j in owner.jobs if j['analytic_pass'])
        root=owner.out/first['request_id'];load_request(root/'GDS_REQUEST.json')
        print(dict(status='METADATA_PREFLIGHT_PASS_NOT_RESOURCE_ADMISSION',native_calls=0,
            requests=256,cadence_command=owner.cadence_command(root)))
        return
    try:owner.run()
    except BaseException as error:
        owner.event('OWNER_STOPPED_NO_CHILD_SIGNAL',error=type(error).__name__+': '+str(error));raise


if __name__=='__main__':main()
