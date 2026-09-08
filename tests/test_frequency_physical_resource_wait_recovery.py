"""Synthetic resource-only recovery; no simulator, model or remote commands."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.broadband56_nn import frequency_physical_dispatch as d
from research.broadband56_nn import frequency_research_emx as emx
from tests.test_frequency_operational_budget import setup, write, use_receipt, natural_partial


@pytest.fixture
def case(setup, monkeypatch):
    old = d.Dispatcher(setup.base['path'], setup.path)
    use_receipt(setup, old)
    natural_partial(setup)
    root = old.out / 'requests/synthetic-0'
    request = {k: setup.jobs[0][k] for k in
        ('request_id', 'frequency_ghz', 'model_id', 'dataset_scope', 'q_proxy')}
    request.update(schema='frequency_research_emx_request.v1', candidate_id='synthetic-0-q17',
        q_requested=17, records=setup.jobs[0]['candidate_records'],
        dispatch_deadline_utc=setup.config['dispatch_deadline_utc'],
        global_lock_path=setup.config['global_lock_path'], resource_budget=setup.config['resource_budget'],
        private_config=setup.config['configuration'], runtime=setup.config['emx_runtime'])
    output = root / 'emx_q17'; completion = output / 'features/FEATURE_RECEIPT.json'
    request_pin = write(root / 'EMX_Q17_REQUEST.json', request)
    wrapper = emx.pin(setup.current / 'research/broadband56_nn/frequency_research_emx.py')
    proof = dict(schema='frequency_research_emx_preflight.v1', status='PASS', request=request_pin,
        output=str(output), source_pins=[wrapper], **{k: request[k] for k in
        ('request_id', 'candidate_id', 'frequency_ghz', 'q_requested', 'q_proxy', 'model_id', 'dataset_scope')})
    preflight = write(output / 'PREFLIGHT.json', proof)
    command = old.module_command('frequency_research_emx', 'run', '--request', request_pin['path'],
        '--output', output, '--inherited-global-lease-fd', '101')
    semantic = command[:-1] + ['<inherited-global-lease-fd>']
    intent = dict(config=setup.base, command=semantic, output=str(output), completion=str(completion))
    intent_pin = write(root / 'emx_q17_INTENT.json', intent)
    log = root / 'emx_q17.log'
    log.write_text('Traceback (synthetic fixture only)\nResearchEmxError: RESOURCE_WAIT_REQUIRED_NO_DISPATCH\n')
    process = dict(intent=intent, returncode=1, completion=None, log=emx.pin(log),
        operational_budget_use=old.operational_use)
    process_pin = write(root / 'emx_q17_PROCESS.json', process)
    failure = write(old.out / 'FAILURE_synthetic.json', dict(status='FAIL_NO_RETRY', config=setup.base,
        error='ResearchEmxError: Native stage did not finish with evidence'))
    release = setup.tmp / 'recovery_release'
    pins=[]
    for p in setup.overlay['release']['source_pins']:
        rel=Path(p['path']).relative_to(setup.current)
        dest=release / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(p['path']).read_bytes() + (b'\n# SYNTHETIC RECOVERY RELEASE IDENTITY\n'
            if dest.name=='frequency_physical_dispatch.py' else b''))
        pins.append(emx.pin(dest))
    entry=dict(request=request_pin,intent=intent_pin,process=process_pin,log=emx.pin(log),
        preflight=preflight,old_wrapper=wrapper)
    manifest=dict(schema='frequency_physical_resource_wait_recovery.v1',base_config=setup.base,
        previous_operational_budget=emx.pin(setup.path),release=dict(code_root=str(release),source_pins=pins),
        acknowledged_failures=[failure],stages=[entry])
    path=setup.tmp/'recovery.json'
    write(path,manifest)
    setup.held[-1].close()
    monkeypatch.setattr(d,'__file__',str(release/'research/broadband56_nn/frequency_physical_dispatch.py'))
    return SimpleNamespace(s=setup,root=root,output=output,completion=completion,request=request,
        proof=proof,intent=intent,process=process,entry=entry,manifest=manifest,path=path,command=command,
        release=release,failure=failure)


def runner(case):
    value=d.Dispatcher(case.s.base['path'],case.s.path,case.path)
    value.fd,value.queue_fd=101,102
    value.admit=lambda: {'SYNTHETIC_RESOURCE_PASS':True}
    value.event=lambda *a,**k:None
    value.operational_use=write(value.out/'operational_budget_uses/USE_recovery_synthetic.json',
        dict(status='ADMITTED_UNDER_ORIGINAL_QUEUE_AND_GLOBAL_LEASES',budget=emx.pin(case.s.path),
        base_config=case.s.base,original_deadline_utc=case.s.config['dispatch_deadline_utc'],
        new_deadline_utc=case.s.overlay['new_dispatch_deadline_utc'],release=case.s.overlay['release'],
        resource_wait_recovery=emx.pin(case.path)))
    return value


def repin_manifest(case,key,content):
    target=case.entry[key]['path']
    updated=write(Path(target),content)
    case.manifest['stages'][0][key]=updated
    write(case.path,case.manifest)


def launch_stub(case,monkeypatch,rejections=0,unknown=False):
    calls=[];solves=[]
    def launch(argv,**kwargs):
        calls.append((argv,kwargs))
        assert kwargs['pass_fds']==(101,102)
        assert kwargs['env']['PYTHONPATH'].split(os.pathsep)[0]==str(case.s.current)
        assert kwargs['env'][emx.OPERATIONAL_BUDGET_ENV]==str(case.s.path)
        use=json.loads(kwargs['env'][emx.OPERATIONAL_USE_ENV])
        assert emx.read_json(emx.verify(use))['budget']==emx.pin(case.s.path)
        if len(calls)<=rejections:
            kwargs['stdout'].write(b'Traceback (synthetic)\nResearchEmxError: RESOURCE_WAIT_REQUIRED_NO_DISPATCH\n')
            return SimpleNamespace(pid=999999,wait=lambda:1)
        if unknown:
            kwargs['stdout'].write(b'Unknown failure must not retry\n')
            return SimpleNamespace(pid=999999,wait=lambda:1)
        solves.append(case.output)
        write(case.completion,{'SYNTHETIC_FEATURE_COMPLETION':True})
        return SimpleNamespace(pid=999999,wait=lambda:0)
    monkeypatch.setattr(d.subprocess,'Popen',launch)
    return calls,solves


def test_exact_manifest_and_success_resume_one_native_call(case,monkeypatch):
    value=runner(case);value.verify_resource_recovery(failures=True)
    before={p['path']:emx.pin(p['path']) for p in [*case.entry.values(),case.failure,
        case.s.base,emx.pin(case.s.path)]}
    calls,solves=launch_stub(case,monkeypatch)
    one=value.process(case.root,'emx_q17',case.command,case.output,case.completion)
    two=value.process(case.root,'emx_q17',case.command[:-1]+['303'],case.output,case.completion)
    again=runner(case)
    three=again.process(case.root,'emx_q17',case.command,case.output,case.completion)
    assert one==two==three and len(calls)==len(solves)==1
    assert one['status']=='RESOURCE_WAIT_RECOVERY_PROCESS_COMPLETE'
    assert one['original_process']==case.entry['process']
    assert all(emx.pin(p)==h for p,h in before.items())
    assert (case.root/'emx_q17_RESOURCE_WAIT_RECOVERY_PROCESS.json').is_file()
    artifact_paths={p['path'] for p in value.resource_artifacts(case.root)}
    assert {p['path'] for p in case.entry.values()} <= artifact_paths
    assert str(case.path) in artifact_paths and case.failure['path'] in artifact_paths
    assert str(case.root/'emx_q17_RESOURCE_WAIT_RECOVERY.json') in artifact_paths
    assert {str(case.root/'emx_q17_RESOURCE_WAIT_ATTEMPTS/000001'/name)
        for name in ('INTENT.json','PROCESS.json','wrapper.log')} <= artifact_paths
    assert case.process['operational_budget_use']['path'] in artifact_paths


@pytest.mark.parametrize('key',['request','intent','process','log','preflight','old_wrapper'])
def test_any_changed_frozen_evidence_pin_refused(case,key):
    with open(case.entry[key]['path'],'ab') as stream:stream.write(b' ')
    with pytest.raises((emx.ResearchEmxError,ValueError)):runner(case)


@pytest.mark.parametrize('what',['base','budget','extra_science','release_dependency','release_missing'])
def test_manifest_base_budget_science_and_release_are_exact(case,what):
    if what=='base':case.manifest['base_config']=write(case.s.tmp/'foreign.json',case.s.config)
    elif what=='budget':case.manifest['previous_operational_budget']=write(case.s.tmp/'budget_copy.json',case.s.overlay)
    elif what=='extra_science':case.manifest['max_global_solvers']=48
    elif what=='release_missing':case.manifest['release']['source_pins'].pop()
    else:
        p=next(p for p in case.manifest['release']['source_pins'] if p['path'].endswith('/io.py'))
        Path(p['path']).write_text('# unrelated modification')
        p.update(emx.pin(p['path']))
    write(case.path,case.manifest)
    with pytest.raises(emx.ResearchEmxError):runner(case)


@pytest.mark.parametrize('what',['foreign_stage','preflight_status','preflight_request','preflight_model','old_wrapper','process_rc','log_reason'])
def test_repin_does_not_turn_wrong_evidence_into_resource_permission(case,what):
    if what=='foreign_stage':
        request=dict(case.request,q_requested=18)
        repin_manifest(case,'request',request)
    elif what.startswith('preflight'):
        proof=copy.deepcopy(case.proof)
        if what=='preflight_status':proof['status']='FAIL'
        if what=='preflight_request':proof['request']=case.entry['intent']
        if what=='preflight_model':proof['model_id']='foreign'
        repin_manifest(case,'preflight',proof)
    elif what=='old_wrapper':
        case.manifest['stages'][0]['old_wrapper']=emx.pin(case.s.old/'research/broadband56_nn/frequency_research_emx.py')
        write(case.path,case.manifest)
    elif what=='process_rc':repin_manifest(case,'process',dict(case.process,returncode=2))
    else:
        Path(case.entry['log']['path']).write_text('ResearchEmxError: actual_solver_failed\n')
        log=emx.pin(case.entry['log']['path'])
        case.manifest['stages'][0]['log']=log
        repin_manifest(case,'process',dict(case.process,log=log))
    with pytest.raises(emx.ResearchEmxError):runner(case)


@pytest.mark.parametrize('name',['solve','RUNNING.json','SOLVER_RECEIPT.json','SOLVER_FAILURE.json','FEATURE_FAILURE.json','features'])
def test_any_solver_or_feature_or_extra_artifact_forbids_recovery(case,name):
    path=case.output/name
    if name in ('solve','features'):path.mkdir()
    else:write(path,{'SYNTHETIC_PARTIAL_OR_FAILURE':True})
    with pytest.raises(emx.ResearchEmxError,match='only PREFLIGHT'):runner(case)


def test_extra_queue_failure_not_acknowledged(case):
    value=runner(case)
    write(value.out/'FAILURE_extra.json',{'status':'FAIL_NO_RETRY','config':case.s.base,'error':'other'})
    with pytest.raises(emx.ResearchEmxError,match='Unacknowledged'):value.verify_resource_recovery(failures=True)


def test_runtime_peer_does_not_reject_already_admitted_solver(case):
    value=runner(case)
    value.verify_resource_recovery(failures=True)
    write(case.output/'solve/RUNNING.json',{'SYNTHETIC_ALREADY_ADMITTED_CHILD':True})
    # A different candidate's resource/source check occurs under the same
    # inherited queue/global ownership, while this admitted child is running.
    value.verify_resource_recovery(failures='pins_only')
    # A fresh startup may NOT turn this same partial physics into a new solver.
    with pytest.raises(emx.ResearchEmxError,match='only PREFLIGHT'):
        value.verify_resource_recovery(failures=True)


def test_foreign_failed_stage_without_evidence_refused(case):
    value=runner(case)
    write(value.out/'requests/foreign/cadence_PROCESS.json',{'returncode':1})
    with pytest.raises(emx.ResearchEmxError,match='Foreign failed stage'):value.verify_resource_recovery(failures=True)


def test_unknown_failure_default_no_manifest_remains_no_retry(case,monkeypatch):
    monkeypatch.setattr(d,'__file__',str(case.s.current/'research/broadband56_nn/frequency_physical_dispatch.py'))
    value=d.Dispatcher(case.s.base['path'],case.s.path)
    with pytest.raises(emx.ResearchEmxError,match='Previous failure preserved'):value.run()
    assert emx.pin(case.failure['path'])==case.failure


def test_resource_race_rejections_wait_without_duplicate_solver(case,monkeypatch):
    value=runner(case);sleeps=[]
    monkeypatch.setattr(d.time,'sleep',sleeps.append)
    calls,solves=launch_stub(case,monkeypatch,rejections=2)
    result=value.process(case.root,'emx_q17',case.command,case.output,case.completion)
    assert result['returncode']==0 and len(calls)==3 and len(solves)==1 and sleeps==[30,30]
    attempts=sorted((case.root/'emx_q17_RESOURCE_WAIT_ATTEMPTS').glob('*/PROCESS.json'))
    assert [emx.read_json(p)['returncode'] for p in attempts]==[1,1,0]
    assert all((p.parent/'wrapper.log').is_file() for p in attempts)
    assert emx.pin(case.entry['process']['path'])==case.entry['process']


@pytest.mark.parametrize('rejections',[0,2])
def test_closed_success_attempt_adopted_when_sidecar_write_was_lost(case,monkeypatch,rejections):
    value=runner(case)
    monkeypatch.setattr(d.time,'sleep',lambda seconds:None)
    calls,solves=launch_stub(case,monkeypatch,rejections=rejections)
    one=value.process(case.root,'emx_q17',case.command,case.output,case.completion)
    # Synthetic crash window after attempt PROCESS is durable, before sidecar.
    sidecar=case.root/'emx_q17_RESOURCE_WAIT_RECOVERY_PROCESS.json'
    sidecar.unlink()
    again=runner(case)
    two=again.process(case.root,'emx_q17',case.command,case.output,case.completion)
    assert one==two and len(calls)==rejections+1 and len(solves)==1
    assert sidecar.is_file()


def test_success_followed_by_any_extra_attempt_is_not_adopted(case,monkeypatch):
    value=runner(case);calls,solves=launch_stub(case,monkeypatch)
    value.process(case.root,'emx_q17',case.command,case.output,case.completion)
    (case.root/'emx_q17_RESOURCE_WAIT_ATTEMPTS/000002').mkdir()
    with pytest.raises(emx.ResearchEmxError,match='Attempt after success'):runner(case)
    assert len(calls)==len(solves)==1


def test_unknown_recovery_child_failure_stops_and_cannot_retry(case,monkeypatch):
    value=runner(case);calls,solves=launch_stub(case,monkeypatch,unknown=True)
    for _ in range(2):
        with pytest.raises(emx.ResearchEmxError,match='RESOURCE_WAIT_REQUIRED'):
            value.process(case.root,'emx_q17',case.command,case.output,case.completion)
    assert len(calls)==1 and not solves


def test_unreceipted_attempt_never_repeated(case,monkeypatch):
    value=runner(case)
    def interrupt(*args,**kwargs):raise RuntimeError('synthetic launch interruption')
    monkeypatch.setattr(d.subprocess,'Popen',interrupt)
    with pytest.raises(RuntimeError,match='launch interruption'):
        value.process(case.root,'emx_q17',case.command,case.output,case.completion)
    monkeypatch.setattr(d.subprocess,'Popen',lambda *a,**k:pytest.fail('must not relaunch'))
    with pytest.raises(emx.ResearchEmxError,match='Unreceipted resource attempt'):
        value.process(case.root,'emx_q17',case.command,case.output,case.completion)


def test_budget_ends_before_recovery_launch_preserves_all_old_evidence(case):
    value=runner(case)
    def ended():raise d.BudgetEnded('synthetic expired unchanged budget')
    value.admit=ended
    with pytest.raises(d.BudgetEnded):
        value.process(case.root,'emx_q17',case.command,case.output,case.completion)
    assert emx.pin(case.entry['process']['path'])==case.entry['process']
    assert not (case.root/'emx_q17_RESOURCE_WAIT_ATTEMPTS/000001').exists()


@pytest.mark.parametrize('lease',['queue','global'])
def test_original_leases_remain_hard_gates(case,lease):
    import fcntl
    value=runner(case)
    first=Path(case.s.jobs[0]['existing_root'])/'remaining_q11to18_queue_v1/TERMINAL.json'
    write(first,{})
    value.first15_reuse=lambda job:{}
    lockpath=value.out/'queue.lock' if lease=='queue' else Path(case.s.config['global_lock_path'])
    with lockpath.open('a+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert value.run()['status']=='ALREADY_OWNED_NO_DUPLICATE'


def test_run_records_new_held_lease_use_without_extending_budget(case):
    value=runner(case)
    first=Path(case.s.jobs[0]['existing_root'])/'remaining_q11to18_queue_v1/TERMINAL.json'
    write(first,{})
    value.first15_reuse=lambda job:{}
    def budget_stop(job):raise d.BudgetEnded('synthetic next dispatch boundary')
    value.execute_request=budget_stop
    result=value.run()
    use=emx.read_json(emx.verify(result['operational_budget_use']))
    assert use['budget']==emx.pin(case.s.path)
    assert use['new_deadline_utc']==case.s.overlay['new_dispatch_deadline_utc']
    assert use['resource_wait_recovery']==emx.pin(case.path)
    assert result['status']=='PARTIAL_BUDGET_ENDED_NO_CHILD_STOPPED'


def test_future_new_stage_auto_waits_and_preserves_original_process(case,monkeypatch):
    # This is a separate not-yet-dispatched Q18, not a replay of Q17.
    monkeypatch.setattr(d,'__file__',str(case.s.current/'research/broadband56_nn/frequency_physical_dispatch.py'))
    value=d.Dispatcher(case.s.base['path'],case.s.path)
    value.fd,value.queue_fd=101,102;value.admit=lambda:{};value.event=lambda *a,**k:None
    value.operational_use=case.process['operational_budget_use']
    out=case.root/'emx_q18';completion=out/'features/FEATURE_RECEIPT.json'
    request=dict(case.request,q_requested=18,candidate_id='synthetic-0-q18')
    rp=write(case.root/'EMX_Q18_REQUEST.json',request)
    command=value.module_command('frequency_research_emx','run','--request',rp['path'],
        '--output',out,'--inherited-global-lease-fd','101')
    calls=[];solves=[];sleeps=[]
    monkeypatch.setattr(d.time,'sleep',sleeps.append)
    def launch(argv,**kwargs):
        calls.append(argv)
        assert kwargs['pass_fds']==(101,102)
        if len(calls)<=2:
            proof=dict(case.proof,request=rp,output=str(out),q_requested=18,candidate_id='synthetic-0-q18')
            if not out.exists():write(out/'PREFLIGHT.json',proof)
            kwargs['stdout'].write(b'Traceback (synthetic)\nResearchEmxError: RESOURCE_WAIT_REQUIRED_NO_DISPATCH\n')
            return SimpleNamespace(pid=999999,wait=lambda:1)
        solves.append(out);write(completion,{'SYNTHETIC':True})
        return SimpleNamespace(pid=999999,wait=lambda:0)
    monkeypatch.setattr(d.subprocess,'Popen',launch)
    one=value.process(case.root,'emx_q18',command,out,completion)
    assert one==value.process(case.root,'emx_q18',command,out,completion)
    assert len(calls)==3 and len(solves)==1 and sleeps==[30]
    assert emx.read_json(case.root/'emx_q18_PROCESS.json')['returncode']==1
    assert one['original_process']==emx.pin(case.root/'emx_q18_PROCESS.json')
