"""Sequential batch continuation. No native leases while the prior owner lives."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import batch_registration as reg
import controlled_execution as e
import controlled_metadata as m
import native_birth
import start_slots as s


def save(path,value):s.write_once(path,value)


def event(chain,status,**values):
    value=dict(utc=e.now(),pid=os.getpid(),status=status,**values)
    with (chain/'CONTINUATION_EVENTS.jsonl').open('a') as f:
        f.write(json.dumps(value,allow_nan=False)+'\n');f.flush();os.fsync(f.fileno())


def verify_deployment(chain):
    d=e.document(e.pin(chain/'DEPLOYMENT.json'))
    m.require(d['schema']=='eucap15_sequential_fixed48_deployment.v1' and d['target_qualified']==100000,
        'CONTINUOUS15GHZ_SCOPE_REQUIRED')
    m.require(d['initial_release']['sha256']==reg.BASE,'WRONG_FIRST_OWNER')
    for p in d['runtime_sources']:m.read_pin(p)
    for p in d['authorizations']:e.document(p)
    m.require(Path(sys.executable).resolve()==Path(d['python']).resolve(),'PRIVATE_PYTHON_MISMATCH')
    return d


def resumed_parent(d):
    """A same-batch recovery changes the physical owner, never the 256 budget."""
    if not d.get('same_batch_resume'):return d['initial_release']
    old=e.document(d['initial_release']);previous=e.document(old['config'])
    cursor=d['same_batch_resume'];seen=set();latest=None;expected=None
    while True:
        m.require(cursor['sha256'] not in seen,'CYCLIC_SAME_BATCH_RESUME_CHAIN')
        seen.add(cursor['sha256'])
        receipt=e.document(cursor);rp=receipt['resumed_release']
        if latest is None:latest=rp
        else:m.require(rp==expected,'BROKEN_SAME_BATCH_RESUME_PARENT')
        release=e.document(rp);config=e.document(release['config']);a=release['concurrency_amendment']
        m.require(receipt['schema']=='eucap15_samebatch_continuation_resume.v1' and
            receipt['previous_release']==a['parent_release'] and
            receipt['original_plan']==a['original_plan'] and
            a.get('recovery_kind')=='SAME_BATCH_FIXED48_HOTPATH_RECOVERY', 'SAME_BATCH_RESUME_IDENTITY_CHANGED')
        m.require(config['original_manifest']==previous['original_manifest'] and
            config['budget_root']==previous['budget_root'] and
            e.plan_release(release,config)==e.document(a['original_plan'])['release'], 'SAME_BATCH_BUDGET_RESET')
        start=e.document(a['parent_start'])
        m.require(not reg.alive(start),'OLD_OWNER_NOT_DEAD_AT_RESUME')
        if receipt['previous_release']==d['initial_release']:return latest
        m.require('previous_resume' in receipt,'MISSING_SAME_BATCH_RESUME_PARENT')
        expected=receipt['previous_release'];cursor=receipt['previous_resume']


def replay_waiting_state(d,events):
    """Rebuild continuation position from completed launches, not batch names."""
    if d.get('continuation_checkpoint'):
        current,index,prior=continuation_state(d)
    else:current=resumed_parent(d);index=1;prior=deepcopy(d['factory_template']['prior_batches'])
    for item in events:
        if item['status']!='SUCCESSOR_OWNER_LAUNCHED':continue
        following=item['release'];release=e.document(following)
        m.require(release['parent_release']==current,'CONTINUATION_RELEASE_CHAIN_CHANGED')
        start=e.document(item['start'])
        m.require(start['release']==following,'CONTINUATION_LAUNCH_START_CHANGED')
        inputs=e.document(release['successor_registration']['inputs']);mp=inputs['manifest']
        manifest=m.parse(m.read_pin(mp,inputs['path_map']))
        if mp['sha256'] not in {v['manifest']['sha256'] for v in prior}:
            prior.append(dict(manifest=mp,candidates=manifest['files']['SELECTED_CANDIDATES.jsonl']))
        current=following;index+=1
    m.require(events and events[-1]['status']=='WAITING_FOR_CURRENT_BATCH_TERMINAL_AND_OWNER_EXIT'
        and events[-1]['release']==current,'CONTINUATION_NOT_AT_WAITING_BOUNDARY')
    return dict(current_release=current,next_index=index,prior_batches=prior)


def continuation_state(d):
    if not d.get('continuation_checkpoint'):
        return resumed_parent(d),1,deepcopy(d['factory_template']['prior_batches'])
    checkpoint=e.document(d['continuation_checkpoint'])
    m.require(checkpoint['schema']=='eucap15_terminal_boundary_continuation_checkpoint.v1',
        'UNKNOWN_CONTINUATION_CHECKPOINT')
    previous=e.document(checkpoint['previous_deployment'])
    for key in ('schema','target_qualified','initial_release','same_batch_resume','authorizations',
            'python','formal_ledger','factory_template','endpoint_upgrade','first_registration'):
        m.require(d.get(key)==previous.get(key),'CONTINUATION_SCOPE_CHANGED:'+key)
    start=e.document(checkpoint['previous_start']);handoff=e.document(checkpoint['handoff'])
    m.require(start['deployment']==checkpoint['previous_deployment'] and
        handoff['previous_start']==checkpoint['previous_start'] and
        handoff['status']=='TERMINAL_BOUNDARY_STANDBY_REPLACED_NO_CHILD_SIGNAL' and
        handoff['child_signals']==0 and handoff['all_threads_stopped'] is True and
        handoff['descendants']==[] and handoff['native']==[], 'UNSAFE_CONTINUATION_HANDOFF')
    m.require(not reg.alive(start['process']),'PREVIOUS_CONTINUATION_STILL_LIVE')
    events=[json.loads(line) for line in m.read_pin(checkpoint['events']).splitlines()]
    state=replay_waiting_state(previous,events)
    m.require(state==checkpoint['state'],'CONTINUATION_CHECKPOINT_STATE_CHANGED')
    release=e.document(state['current_release']);config=e.document(release['config'])
    parent_start=e.document(e.pin(Path(config['out'])/'START_RECEIPT.json'))
    terminal=e.document(checkpoint['terminal'])
    m.require(not reg.alive(parent_start) and parent_start['release']==state['current_release'] and
        terminal['release']==state['current_release'] and terminal['N_original_requests']==256 and
        terminal['status']=='ALL_NEW256_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE',
        'CURRENT_BATCH_NOT_CLOSED_AT_CONTINUATION_HANDOFF')
    return state['current_release'],state['next_index'],state['prior_batches']


def launch(chain,release_pin):
    from run_fixed48_native import Owner
    owner=Owner(release_pin['path']);owner.predecessor_closed();out=owner.out
    m.require(not (out/'START_RECEIPT.json').exists() and not (out/'LAUNCH_INTENT.json').exists(),
        'START_ALREADY_USED_NO_DUPLICATE_LAUNCH')
    cmd=[owner.config['python'],'-B',str(chain/'runtime/run_fixed48_native.py'),'--release',release_pin['path']]
    with ExitStack() as stack:
        fds=[stack.enter_context(owner.lease(p)) for p in
            (out/'OWNER.lock',owner.config['production_lock_path'],owner.config['global_lock_path'])]
        owner.predecessor_closed();owner.verify_release()
        save(out/'LAUNCH_INTENT.json',dict(utc=e.now(),release=release_pin,command=cmd,
            no_existing_start=True,budget_reset=False,parent_closure=owner.release['successor_registration']['parent_closure']))
        env=owner.env.copy();env.update(dict(zip(('EUCAP15_OWNER_FD','EUCAP15_PRODUCTION_FD','EUCAP15_RESEARCH_FD'),map(str,fds))))
        with (out/'OWNER.stdout.log').open('xb') as log:
            p=subprocess.Popen(cmd,env=env,cwd=owner.config['repo'],stdin=subprocess.DEVNULL,
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=fds)
        identity=native_birth.proc_info(p.pid)
        save(out/'START_RECEIPT.json',dict(status='ONE_SUCCESSOR_OWNER_STARTED_NOT_NATIVE_PROOF',
            utc=e.now(),pid=p.pid,start_ticks=identity['start_ticks'],uid=identity['uid'],
            command=cmd,release=release_pin,parent_release=owner.release['parent_release'],
            locks_transferred_without_reacquire_gap=True,requested_emx=48,executor_capacity=48))
        event(chain,'SUCCESSOR_OWNER_LAUNCHED',release=release_pin,start=e.pin(out/'START_RECEIPT.json'))
    return p


def attach_publication(chain,release_pin):
    """Reuse the installed metadata-only reader; never controls native jobs."""
    specification=e.document(e.pin(chain/'PUBLICATION.json'))
    for p in specification['sources']:m.read_pin(p)
    release=e.document(release_pin);config=e.document(release['config'])
    root=chain/'publication_workers'/release_pin['sha256'];root.mkdir(parents=True,exist_ok=False)
    prefix=specification.get('state_prefix','native_candidate_publication_fixed48_')
    m.require(prefix in ('native_candidate_publication_fixed48_','native_candidate_publication_family_v3_'),
        'UNKNOWN_PUBLICATION_STATE_NAMESPACE')
    state=Path(release['successor_registration']['formal_ledger']).parent.parent/(prefix+release_pin['sha256'][:12])
    command=[config['python'],'-B',specification['entry']['path'],
        '--fixed48-entry',specification['fixed48_entry']['path'],
        '--legacy-bridge',specification['legacy_bridge']['path'],
        '--library-dir',specification['library_dir'],
        '--release',release_pin['path'],'--contract-inputs',specification['contract_inputs']['path'],
        '--state',str(state),*specification.get('extra_arguments',[])]
    save(root/'DEPLOYMENT.json',dict(release=release_pin,command=command,sources=specification['sources']))
    with (root/'METADATA.log').open('xb') as log:
        process=subprocess.Popen([config['python'],'-B',specification['worker']['path'],str(root/'DEPLOYMENT.json')],
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    save(root/'START.json',dict(utc=e.now(),process=native_birth.proc_info(process.pid),
        deployment=e.pin(root/'DEPLOYMENT.json'),native_actions=0))
    event(chain,'SUCCESSOR_METADATA_READER_STARTED_NOT_A_NATIVE_CONTROLLER',release=release_pin,start=e.pin(root/'START.json'))


def next_inputs(chain,d,index,prior):
    if index==1:return d['first_registration']
    sys.path.insert(0,str(chain/'factory'))
    import successor_factory as factory
    for p in d['factory_sources']:m.read_pin(p)
    request=deepcopy(d['factory_template'])
    request['batch_id']='eucap15_continuous_doe_neighborhood_20260912_batch'+f'{index:06d}'
    request['seeds']=dict(doe=2026091203+2*(index-1),neighbor=2026091204+2*(index-1),split=17)
    request['prior_batches']=prior
    path=chain/'factory_requests'/f'{index:06d}.json';path.parent.mkdir(exist_ok=True)
    if path.exists():m.require(e.document(e.pin(path))==request,'FACTORY_REQUEST_ALREADY_FROZEN_DIFFERENTLY')
    else:save(path,request)
    answer=factory.generate_once(e.pin(path),chain/'input_factory')
    mp=answer['manifest'];completion=chain/'input_factory/batches'/request['batch_id']/'COMPLETED.json'
    return reg.register(mp,request['path_map'],d['authorizations'],factory_completion=e.pin(completion))


def run(chain):
    chain=Path(chain);d=verify_deployment(chain)
    base=e.document(d['initial_release']);config=e.document(base['config'])
    if d.get('endpoint_upgrade'):
        from endpoint_upgrade import load
        repo=load(d['endpoint_upgrade'])['new_repo']
    else:repo=config['repo']
    sys.path.insert(0,repo)
    from research.broadband56_nn.frequency_research_emx import global_lease
    with global_lease(chain/'CONTINUATION.lock'):
        save(chain/'CONTINUATION_START.json',dict(utc=e.now(),process=native_birth.proc_info(os.getpid()),
            deployment=e.pin(chain/'DEPLOYMENT.json'),mode='STANDBY_NO_PRODUCTION_LOCKS_NO_NATIVE_DISPATCH'))
        current,index,prior=continuation_state(d);child=None
        while True:
            verify_deployment(chain)
            p=e.document(current);cfg=e.document(p['config']);out=Path(cfg['out'])
            start=e.document(e.pin(out/'START_RECEIPT.json'))
            m.require(start['release']==current,'CURRENT_START_IDENTITY_MISMATCH')
            event(chain,'WAITING_FOR_CURRENT_BATCH_TERMINAL_AND_OWNER_EXIT',release=current,
                expected_process=dict(pid=start['pid'],start_ticks=start['start_ticks']),production_locks_held=False)
            while reg.alive(start):
                time.sleep(30)
                if child is not None:child.poll()
            if child is not None:child.wait()
            closure=reg.require_parent_terminal(current)
            event(chain,'PRIOR_BATCH_CLOSED_AND_OWNER_DEAD',release=current,closure=closure['terminal'])
            from live_dedup import Ledger
            ledger=Ledger(d['formal_ledger']);ledger.update()
            if len(ledger.pins)>=d['target_qualified']:
                save(chain/'CONTINUATION_TERMINAL.json',dict(status='QUALIFIED_TARGET_REACHED_NO_NEW_BATCH',
                    utc=e.now(),formal_head=ledger.pins[-1],count=len(ledger.pins),last_release=current));return
            inputs=next_inputs(chain,d,index,prior)
            registered=e.document(inputs);mp=registered['manifest']
            manifest=m.parse(m.read_pin(mp,registered['path_map']))
            if mp['sha256'] not in {v['manifest']['sha256'] for v in prior}:
                prior.append(dict(manifest=mp,candidates=manifest['files']['SELECTED_CANDIDATES.jsonl']))
            from prepare_successor import prepare
            following=prepare(chain,inputs,current)
            while True:
                try:child=launch(chain,following);break
                except BlockingIOError:
                    event(chain,'SHARED_LEASE_BUSY_NO_NATIVE_LAUNCH',release=following);time.sleep(30)
            current=following;index+=1
            try:attach_publication(chain,following)
            except Exception as error:
                event(chain,'METADATA_ATTACHMENT_ERROR_NATIVE_CHILD_PRESERVED',release=following,
                    error=type(error).__name__+': '+str(error))


def main():
    chain=Path(sys.argv[1])
    try:run(chain)
    except BaseException as error:
        path=chain/'CONTINUATION_FAILURE.json'
        if not path.exists():save(path,dict(utc=e.now(),status='CONTINUATION_STOPPED_PRESERVE_NATIVE_CHILDREN',
            error_type=type(error).__name__,error=str(error),child_signals=0))
        raise


if __name__=='__main__':main()
