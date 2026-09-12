"""One-shot immutable standby handoff at an already supported full-batch boundary.

Never touches a live native owner or its descendants. No resource polling,
simulator call, new campaign, or new candidate-selection policy is implemented.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(root/'runtime'))
import continue_batches as c
import controlled_execution as e
import controlled_metadata as m
import native_birth
import native_resource_probe as probe
import quiescent_handoff as q
import start_slots as s


def processes(identity,config):
    records={}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            row=native_birth.proc_info(int(p.name))
            if row['uid']==os.getuid() and row['state']!='Z':records[row['pid']]=row
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    descendants=set();front={identity['pid']}
    while front:
        front={pid for pid,row in records.items() if row['ppid'] in front and pid not in descendants}
        descendants.update(front)
    return dict(descendants=[records[p] for p in sorted(descendants)],
        native=[v for v in probe.competing_processes(config) if v['native']])


def main():
    staged=e.document(e.pin(root/'STAGED.json'));old=Path(staged['previous_chain'])
    for pin in staged['runtime_sources']:m.read_pin(pin)
    m.require(e.document(staged['tests'])['status']=='PASS','TARGETED_TESTS_NOT_PASS')
    previous=e.document(staged['previous_deployment'])
    start_pin=e.pin(old/'CONTINUATION_START.json');start=e.document(start_pin)
    identity=start['process']
    m.require(start['deployment']==staged['previous_deployment'],'STANDBY_DEPLOYMENT_CHANGED')
    base=e.document(previous['initial_release']);base_config=e.document(base['config'])
    repo=base_config['repo']
    if previous.get('endpoint_upgrade'):
        from endpoint_upgrade import load
        repo=load(previous['endpoint_upgrade'])['new_repo']
    sys.path.insert(0,repo)
    from research.broadband56_nn.frequency_research_emx import global_lease
    deadline=time.monotonic()+5400
    with global_lease(old/'PERMIT_RELEASE_UPGRADE.lock'):
        s.write_once(root/'BOUNDARY_INSTALLER_START.json',dict(utc=e.now(),
            process=native_birth.proc_info(os.getpid()),previous_start=start_pin,
            supported_boundary='BATCH_TERMINAL_AND_OWNER_EXIT',native_actions=0,
            maximum_wait_seconds=5400))
        while time.monotonic()<deadline:
            m.require(c.reg.alive(identity),'PREVIOUS_STANDBY_EXITED_NO_BLIND_RESTART')
            events_path=old/'CONTINUATION_EVENTS.jsonl'
            events=[json.loads(line) for line in events_path.read_bytes().splitlines()]
            if not events or events[-1]['status']!='WAITING_FOR_CURRENT_BATCH_TERMINAL_AND_OWNER_EXIT':
                time.sleep(5);continue
            rp=events[-1]['release'];release=e.document(rp);config=e.document(release['config'])
            out=Path(config['out']);owner_start=e.document(e.pin(out/'START_RECEIPT.json'))
            if c.reg.alive(owner_start):time.sleep(5);continue
            if not (out/'BATCH_RECEIPT.json').exists():
                raise RuntimeError('CURRENT_OWNER_DEAD_WITHOUT_TERMINAL')
            # Existing exact-PID helper refuses any live child or native process.
            fd=q.freeze(identity,lambda:processes(identity,config))
            if fd is None:time.sleep(5);continue
            terminated=False
            try:
                events=[json.loads(line) for line in events_path.read_bytes().splitlines()]
                state=c.replay_waiting_state(previous,events)
                m.require(state['current_release']==rp,'BOUNDARY_MOVED_PRESERVE_OLD_STANDBY')
                terminal_pin=e.pin(out/'BATCH_RECEIPT.json');terminal=e.document(terminal_pin)
                m.require(terminal['release']==rp and terminal['N_original_requests']==256 and
                    terminal['status']=='ALL_NEW256_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE',
                    'EXACT_FULL_BATCH_TERMINAL_REQUIRED')
                quiet=processes(identity,config)
                m.require(quiet==dict(descendants=[],native=[]),'CHILD_APPEARED_PRESERVE_NATIVE')
                event_pin=e.pin(events_path)
                s.write_once(root/'BOUNDARY_PREFLIGHT.json',dict(utc=e.now(),status='PASS',
                    previous_start=start_pin,previous_deployment=staged['previous_deployment'],
                    events=event_pin,state=state,terminal=terminal_pin,processes=quiet,
                    source_tests=staged['tests'],native_actions=0))
                q.terminate_frozen(identity,fd);terminated=True
                s.write_once(root/'HANDOFF.json',dict(utc=e.now(),
                    status='TERMINAL_BOUNDARY_STANDBY_REPLACED_NO_CHILD_SIGNAL',
                    previous_start=start_pin,all_threads_stopped=True,child_signals=0,
                    descendants=[],native=[],current_owner_already_dead=True))
                checkpoint=dict(schema='eucap15_terminal_boundary_continuation_checkpoint.v1',
                    utc=e.now(),previous_deployment=staged['previous_deployment'],previous_start=start_pin,
                    handoff=e.pin(root/'HANDOFF.json'),events=event_pin,state=state,terminal=terminal_pin)
                s.write_once(root/'CONTINUATION_CHECKPOINT.json',checkpoint)
                deployment=deepcopy(previous)
                deployment.update(utc=e.now(),runtime_sources=staged['runtime_sources'],
                    factory_sources=staged['factory_sources'],
                    continuation_checkpoint=e.pin(root/'CONTINUATION_CHECKPOINT.json'))
                s.write_once(root/'DEPLOYMENT.json',deployment)
                m.require(c.continuation_state(c.verify_deployment(root))==
                    (state['current_release'],state['next_index'],state['prior_batches']),
                    'FINAL_READBACK_POSITION_MISMATCH')
                with (root/'CONTINUATION.log').open('xb') as log:
                    process=subprocess.Popen([deployment['python'],'-B',str(root/'runtime/continue_batches.py'),str(root)],
                        stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                new_identity=native_birth.proc_info(process.pid)
                s.write_once(root/'CONTINUATION_LAUNCH.json',dict(utc=e.now(),process=new_identity,
                    deployment=e.pin(root/'DEPLOYMENT.json')))
                time.sleep(3)
                m.require(process.poll() is None,'NEW_STANDBY_EXITED_SEE_CONTINUATION_LOG')
                receipt=dict(status='NEXT_BATCH_RUNTIME_INSTALLED_NOT_NATIVE_CONCURRENCY_PROOF',utc=e.now(),
                    previous_start=start_pin,checkpoint=e.pin(root/'CONTINUATION_CHECKPOINT.json'),
                    deployment=e.pin(root/'DEPLOYMENT.json'),standby=native_birth.proc_info(process.pid),
                    runtime=e.pin(root/'runtime/fixed48_runtime.py'),
                    publication=e.pin(root/'PUBLICATION.json'),healthy_native_signals=0,
                    previous_batch_restarted=False,next_index=state['next_index'])
                s.write_once(root/'INSTALL_RECEIPT.json',receipt)
                print(json.dumps(dict(receipt=e.pin(root/'INSTALL_RECEIPT.json'),**receipt)),flush=True)
                return
            finally:
                if not terminated:q.resume(fd)
        raise TimeoutError('BOUNDARY_NOT_REACHED_WITHIN_90_MINUTES_OLD_CHAIN_PRESERVED')


if __name__=='__main__':
    try:main()
    except BaseException as error:
        path=root/'INSTALL_FAILURE.json'
        if not path.exists():s.write_once(path,dict(utc=e.now(),status='INSTALL_NOT_COMPLETE',
            error=type(error).__name__+': '+str(error),native_signals=0))
        raise
