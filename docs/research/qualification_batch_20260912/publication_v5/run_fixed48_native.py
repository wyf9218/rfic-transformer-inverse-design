"""Use the existing bounded executor and scientific candidate pipeline."""
import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import os
from pathlib import Path
import time

import controlled_execution as execution
import controlled_metadata as m
import controlled_result as results
from fixed48_runtime import Manager
from run_controlled_native import Owner as SerialOwner
from run_development_native import CandidateFailure, BudgetEnded, read, write


class Owner(SerialOwner):
    def __init__(self, path):
        super().__init__(path)
        m.require(self.release.get('concurrency_amendment') is not None,'FIXED48_AMENDMENT_REQUIRED')
        m.require(self.pin(__file__) in self.release['sources'],'EXACT_FIXED48_EXECUTOR_SOURCE_REQUIRED')
        self.manager=None

    def admit(self, root, stage):
        self.manager.acquire(root,stage)

    def stage(self, root, name, command, output, completion, native=True):
        adopted=self.config['adopted_stages'].get(root.name,{}).get(name)
        if adopted:
            prior=execution.document(adopted)
            m.require(prior['returncode']==0 and prior['completion'] is not None,'ADOPTED_STAGE_NOT_COMPLETE')
            self.verify(prior['completion'])
            m.require(completion.is_file() and self.pin(completion)['sha256']==prior['completion']['sha256'],
                'ADOPTED_STAGE_COPY_CHANGED')
            receipt=root/(name+'_ADOPTION.json')
            if not receipt.exists():
                write(receipt,dict(status='REUSED_VERIFIED_PARENT_STAGE_NO_NATIVE_CALL',
                    source=adopted,source_completion=prior['completion'],completion=self.pin(completion),
                    release=self.release_pin,physical_geometry_changed=False))
            return
        try:return super().stage(root,name,command,output,completion,native=native)
        finally:
            if native:self.manager.release(root.name)

    def budget_decision(self,job):
        if not self.binding:return None
        _,config,plan,book=execution.load_execution(self.binding,self.batch)
        with book.locked():decision=execution.budget_state(config,plan,book.records(),job)
        if decision['status'] in ('ELIGIBLE_WITHIN_BUDGET','RESOURCE_WAIT_PROJECTED_STORAGE'):
            # The next tool permit waits for projected headroom. This is not a
            # candidate phenotype failure or permission to bypass admission.
            return None
        from fixed48_runtime import suffix
        p=self.out/job['request_id']/('BUDGET_'+suffix()+'.json')
        write(p,decision)
        return results.budget_hold(self.batch,job['request_id'],decision_pin=self.pin(p),plan_pin=self.binding['plan'])

    def one(self,job):
        root=self.out/job['request_id']
        with self.manager.candidate(job):
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
            return self.pin(root/'RESULT.json')

    def run(self):
        from rfic_transformer_inverse_design.campaigns.broadband56_dispatch import bounded_completed
        self.predecessor_closed()
        m.require(all(k in os.environ for k in ('EUCAP15_OWNER_FD','EUCAP15_PRODUCTION_FD','EUCAP15_RESEARCH_FD')),
            'ATOMIC_LAUNCH_LEASE_TRANSFER_REQUIRED')
        with ExitStack() as stack:
            stack.enter_context(self.lease(self.out/'OWNER.lock',int(os.environ['EUCAP15_OWNER_FD'])))
            prod=stack.enter_context(self.lease(self.config['production_lock_path'],int(os.environ['EUCAP15_PRODUCTION_FD'])))
            research=stack.enter_context(self.lease(self.config['global_lock_path'],int(os.environ['EUCAP15_RESEARCH_FD'])))
            self.predecessor_closed();self.fds=(prod,research)
            self.manager=Manager(self);self.manager.start()
            self.event('FIXED48_SINGLE_OWNER_STARTED',release=self.release_pin,executor_capacity=48,
                candidate_pipeline_capacity=self.config['fixed48_policy']['candidate_pipeline_capacity'],
                requested_concurrency=48,actual_native_concurrency='NOT_INFERRED_FROM_EXECUTOR_CAPACITY')
            prior={Path(p['path']).parent.name:p for p in self.release['concurrency_amendment']['prior_results']}
            pending=[j for j in self.jobs if j['request_id'] not in prior]
            try:
                try:
                    with closing(bounded_completed(len(pending),lambda i:self.one(pending[i]),
                            max_workers=self.config['fixed48_policy']['candidate_pipeline_capacity'],
                            admission=self.manager.admit_candidate_count,receipt_dir=self.out/'dispatch')) as completed:
                        for _,future in completed:future.result()
                except Exception:
                    plan=execution.document(self.binding['plan'])
                    expired=datetime.now(timezone.utc)>=datetime.fromisoformat(plan['deadline_utc'])
                    full=execution.allocated_bytes(self.config['budget_root'])>=m.MAX_INCREMENTAL_BYTES
                    if not (expired or full):raise
                    for job in pending:
                        root=self.out/job['request_id']
                        if (root/'RESULT.json').exists():continue
                        value=results.original_hold(self.batch,job['request_id']) if not job['analytic_pass'] else self.budget_decision(job)
                        m.require(value is not None,'TERMINAL_BUDGET_DECISION_REQUIRED')
                        results.publish(root/'RESULT.json',value);self.capture_result(job)
                records=[prior[j['request_id']] if j['request_id'] in prior else self.pin(self.out/j['request_id']/'RESULT.json')
                    for j in self.jobs]
                for p in records:self.verify(p)
                write(self.out/'BATCH_RECEIPT.json',dict(status='ALL_NEW256_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE',
                    utc=execution.now(),release=self.release_pin,plan=self.binding,N_original_requests=256,
                    results=records,production_accepted_added=0,requested_emx=48,executor_capacity=48))
            finally:self.manager.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--release',required=True)
    parser.add_argument('--preflight-only',action='store_true');args=parser.parse_args()
    owner=Owner(args.release)
    if args.preflight_only:
        owner.predecessor_closed()
        m.require(owner.binding is not None,'ORIGINAL_BUDGET_PLAN_REQUIRED')
        execution.load_execution(owner.binding,owner.batch)
        print(dict(status='FIXED48_METADATA_PREFLIGHT_ONLY',native_calls=0,supervisors_started=0,
            executor_capacity=48,original_requests=len(owner.jobs)))
        return
    try:owner.run()
    except BaseException as error:
        owner.event('FIXED48_OWNER_EXIT_NO_CHILD_SIGNAL',error=type(error).__name__+': '+str(error));raise


if __name__=='__main__':main()
