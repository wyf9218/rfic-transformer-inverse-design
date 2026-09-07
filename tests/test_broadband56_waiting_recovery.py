"""Explicit synthetic authority/artifact/control fixtures, never production evidence."""
import copy
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_checkpoint_handoff as cp
from rfic_transformer_inverse_design.campaigns import broadband56_delegated_release as delegated
from rfic_transformer_inverse_design.campaigns import broadband56_waiting_recovery as recovery
from rfic_transformer_inverse_design.campaigns import broadband56_waiting_fence as fence


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return cp.pin(path)


@pytest.fixture
def authority(tmp_path):
    source = save(tmp_path/'TEST_ONLY_OWNER_MESSAGE.json', {'fixture_only': True})
    value = dict(schema=delegated.SCHEMA, granted_by=delegated.OWNER,
        recorded_utc='2026-09-07T03:00:00+00:00',
        decision='AUTHORIZE_CONTINUOUS_SCOPED_REPAIR_AND_PRODUCTION', limits=copy.deepcopy(delegated.LIMITS),
        source_message_record=source, individual_sha_approval_superseded=True,
        controlled_waiting_batch_recovery_authorized=True, historical_evidence_must_be_preserved=True)
    return value, save(tmp_path/'TEST_ONLY_AUTHORITY.json', value)


@pytest.mark.parametrize('key', list(delegated.LIMITS))
def test_scope_changes_are_rejected(authority, tmp_path, key):
    value, _ = authority
    value['limits'][key] = 'WRONG_TEST_VALUE'
    with pytest.raises(ValueError, match='scope mismatch'):
        delegated.validate_standing(save(tmp_path/'bad.json', value))


def test_standing_record_binds_source_and_timezone(authority, tmp_path):
    value, record = authority
    assert delegated.validate_standing(record) == value
    value['recorded_utc'] = '2026-09-07T03:00:00'
    with pytest.raises(ValueError, match='timezone-aware'):
        delegated.validate_standing(save(tmp_path/'naive.json', value))
    cp.bound(value['source_message_record']).write_text('CHANGED')
    with pytest.raises(ValueError, match='drift'):
        delegated.validate_standing(record)


@pytest.fixture
def release(authority, tmp_path):
    _, auth = authority
    runtime = save(tmp_path/'runtime.json', {'fixture_only':True})
    backend = save(tmp_path/'backend.json', {'fixture_only':True})
    evidence = save(tmp_path/'TEST_ONLY_PREFLIGHT.json', dict(overall_status='PASS',
        runtime=runtime, backend=backend, simulator_action_taken=False, fixture_only=True))
    candidate = dict(generated_utc='2026-09-07T03:01:00+00:00', standing_owner_authorization=auth,
        bound_files=dict(new_runtime_manifest=runtime, new_backend_manifest=backend))
    candidate_record = save(tmp_path/'candidate.json', candidate)
    value = dict(overall_status='PASS', decision=delegated.DECISION, authorization_scope='TEST_SCOPE',
        released_candidate=candidate_record, released_by=delegated.DELEGATE,
        owner_personally_approved_this_sha=False, scope_limits=copy.deepcopy(delegated.LIMITS),
        standing_owner_authorization=auth, released_utc='2026-09-07T03:02:00+00:00',
        new_runtime_manifest=runtime, new_backend_manifest=backend,
        software_tests=evidence, full_control_preflight=evidence)
    return value, candidate, candidate_record


def test_delegated_release_is_not_personal_sha_approval(release):
    value, candidate, record = release
    delegated.validate_release(value, candidate, record, 'TEST_SCOPE')
    value['approved_by'] = delegated.OWNER
    with pytest.raises(ValueError, match='identity/scope'):
        delegated.validate_release(value, candidate, record, 'TEST_SCOPE')


@pytest.mark.parametrize('key', ['released_candidate','released_by','owner_personally_approved_this_sha',
                               'scope_limits','authorization_scope','new_runtime_manifest','new_backend_manifest'])
def test_wrong_release_identity_rejected(release, key):
    value, candidate, record = release
    value[key] = None
    with pytest.raises(ValueError):
        delegated.validate_release(value, candidate, record, 'TEST_SCOPE')


def test_release_evidence_must_bind_tested_package(release, tmp_path):
    value, candidate, record = release
    value['software_tests'] = save(tmp_path/'fail.json', dict(overall_status='FAIL'))
    with pytest.raises(ValueError, match='final-package'):
        delegated.validate_release(value, candidate, record, 'TEST_SCOPE')


def test_pin_walk_and_no_clobber(tmp_path):
    record = save(tmp_path/'input.json', dict(fixture_only=True))
    assert list(recovery.pins_in(dict(a=[dict(b=record)]))) == [record]
    output = tmp_path/'output.json'
    recovery.write(output, dict(first=True))
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        recovery.write(output, dict(second=True))
    assert output.read_bytes() == before


def test_absent_recovery_binding_does_not_change_normal_execution(tmp_path):
    assert recovery.prepare_reused_prefix(backend={}, context={}, out_dir=tmp_path) == {}


def test_fence_ignores_dead_sampler_but_not_live_descendants(tmp_path):
    for pid, state, parent in [(100,'T',1),(101,'T',100),(102,'Z',101),(103,'S',101)]:
        path = tmp_path/str(pid)
        path.mkdir()
        (path/'stat').write_text(f'{pid} (fixture) {state} {parent} 0 0')
    assert fence.descendants(100, tmp_path) == [100,101,103]


def test_profile_allows_only_identical_queue_delegate_relocation(tmp_path):
    left, right = tmp_path/'old/queue.py', tmp_path/'new/queue.py'
    for path in (left,right):
        path.parent.mkdir()
        path.write_text('# fixture\n')
    original = dict(stages={'PILOT_1000':{'commands':[dict(role='phase_a_queue_builder',
        argv=['--delegate-script',str(left),'--delegate-sha256',cp.pin(left)['sha256']])],
        'max_candidates_per_attempt':192}})
    before = save(tmp_path/'old_profile.json',original)
    moved = copy.deepcopy(original)
    moved['stages']['PILOT_1000']['commands'][0]['argv'][1] = str(right)
    recovery.validate_relocated_profile(before, save(tmp_path/'moved.json',moved))
    changed = copy.deepcopy(moved)
    changed['stages']['PILOT_1000']['max_candidates_per_attempt'] = 48
    with pytest.raises(ValueError, match='beyond identical'):
        recovery.validate_relocated_profile(before, save(tmp_path/'changed.json',changed))
    right.write_text('# drift\n')
    with pytest.raises(ValueError, match='delegate bytes'):
        recovery.validate_relocated_profile(before, cp.pin(tmp_path/'moved.json'))


def test_uncommitted_prefix_must_not_be_dropped(tmp_path):
    marker = tmp_path/'stages/000001_pilot_1000/backend/WAITING_PREFIX_REUSE_RECEIPT.json'
    save(marker, dict(fixture_only=True))
    with pytest.raises(ValueError, match='not committed'):
        recovery.prepare_reused_prefix(backend={'waiting_stage_recovery':{}},
            context={'campaign_root':str(tmp_path)}, out_dir=tmp_path/'new')


def test_committed_prefix_not_reused_twice(tmp_path):
    base = tmp_path/'stages/000001_pilot_1000'
    save(base/'backend/WAITING_PREFIX_REUSE_RECEIPT.json', dict(fixture_only=True))
    save(base/'STAGE_PROGRESS_RECEIPT.json', dict(fixture_only=True))
    assert recovery.prepare_reused_prefix(backend={'waiting_stage_recovery':{}},
        context={'campaign_root':str(tmp_path)}, out_dir=tmp_path/'new') == {}


def test_control_fence_requires_no_native_and_exact_chain(monkeypatch):
    owner = dict(pid=100, executable_sha256='p', uid=55)
    children = [dict(pid=101,parent_pid=100,executable_sha256='p'),
                dict(pid=102,parent_pid=101,executable_sha256='p'),
                dict(pid=103,parent_pid=102,executable_sha256='p',
                     command_text='run_broadband56_v2_exact_gds_emx_batch.py /TEST/stage/backend/roles/08_exact_audited_gds_emx_runner')]
    monkeypatch.setattr(fence.iso,'enumerate_owner_processes',lambda *a,**k:children)
    monkeypatch.setattr(fence.iso,'read_process_identity',lambda pid:owner)
    monkeypatch.setattr(fence.iso,'process_identity_matches',lambda a,b:a==b)
    binding = dict(owned_pids=[101,102,103],native_counts={})
    monkeypatch.setattr(fence,'running_stage_children',lambda **k:binding)
    args = dict(prefix={'source_stage_dir':'/TEST/stage'},lease={'physical_process':owner},history='/TEST/history')
    assert [p['pid'] for p in fence.verify_control_tree(**args)] == [100,101,102,103]
    binding['native_counts']={'emx':1}
    with pytest.raises(ValueError, match='no native'):
        fence.verify_control_tree(**args)
    binding['native_counts']={}
    children[2]['parent_pid']=999
    with pytest.raises(ValueError, match='ancestry'):
        fence.verify_control_tree(**args)


@pytest.mark.parametrize('extra_child', [False, True])
def test_pidfd_fence_order_and_abort_restoration(tmp_path, monkeypatch, extra_child):
    owner = dict(pid=100, parent_pid=1, uid=55, state='S', executable_sha256='p')
    chain = [owner, dict(pid=101,parent_pid=100,uid=55,state='S',executable_sha256='p'),
             dict(pid=102,parent_pid=101,uid=55,state='S',executable_sha256='p'),
             dict(pid=103,parent_pid=102,uid=55,state='S',executable_sha256='p')]
    mutable = {p['pid']:dict(p) for p in chain}
    lock = tmp_path/'TEST_ONLY.lock'
    lock.write_text(cp.SUPERVISOR_ID+'\n')
    lease = save(tmp_path/'lease.json', dict(physical_process=owner, campaign_lock={'path':str(lock)}))
    prefix = dict(prior_supervisor_lease=lease, source_context=save(tmp_path/'context.json',
        dict(stage_resource_history='/TEST')), standing_owner_authorization={'fixture_only':True},
        checkpoint_boundary={'fixture_only':True}, dispatch={'fixture_only':True}, current_accepted=861,feature_rows=48216)
    monkeypatch.setattr(fence,'validate_prefix',lambda record:prefix)
    monkeypatch.setattr(fence,'verify_control_tree',lambda **kwargs:copy.deepcopy(chain))
    monkeypatch.setattr(fence.iso,'read_process_identity',lambda pid:mutable.get(pid))
    monkeypatch.setattr(fence.iso,'process_identity_matches',lambda a,b:a==b)
    monkeypatch.setattr(fence.iso,'_public_process_record',lambda value:dict(value))
    monkeypatch.setattr(fence.iso,'enumerate_owner_processes',lambda *a,**k:[])
    monkeypatch.setattr(fence.os,'pidfd_open',lambda pid:pid+1000,raising=False)
    monkeypatch.setattr(fence.os,'close',lambda fd:None)
    monkeypatch.setattr(fence,'descendants',lambda pid:list(mutable)+([999] if extra_child else []))
    clock = iter(range(0,1000,100))
    monkeypatch.setattr(fence.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(fence.time,'sleep',lambda seconds:None)
    signals = []
    def send(fd, sig):
        pid = fd-1000
        signals.append((pid,sig))
        if sig == fence.signal.SIGSTOP: mutable[pid]['state']='T'
        elif sig == fence.signal.SIGCONT: mutable[pid]['state']='S'
        elif sig == fence.signal.SIGKILL: mutable.pop(pid)
    monkeypatch.setattr(fence.signal,'pidfd_send_signal',send,raising=False)
    if extra_child:
        with pytest.raises(ValueError,match='frozen boundary'):
            fence.interrupt(prefix_record={'fixture_only':True},out_dir=tmp_path/'out')
        assert not any(sig == fence.signal.SIGKILL for _,sig in signals)
        assert all(p['state']=='S' for p in mutable.values())
    else:
        receipt = cp.read(cp.bound(fence.interrupt(prefix_record={'fixture_only':True},out_dir=tmp_path/'out')))
        assert receipt['overall_status']=='PASS_CONTROLLED_INTERRUPTION_NOT_STAGE_COMPLETION'
        assert not receipt['stage_completed']
        assert receipt['current_accepted']==861
        assert signals[:4]==[(pid,fence.signal.SIGSTOP) for pid in (103,102,101,100)]
        assert signals[4:]==[(pid,fence.signal.SIGKILL) for pid in (103,102,101,100)]
        assert not mutable
