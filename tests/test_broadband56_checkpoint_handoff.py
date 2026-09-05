"""Checkpoint fixtures, never physical acceptance or live handoff."""
import json
from pathlib import Path

import pytest

from tests.test_broadband56_production_backend import _stage_receipt
from tests.test_broadband56_stage_progress import _receipt
from tests.test_broadband56_scheduling import write
from rfic_transformer_inverse_design.campaigns import broadband56_checkpoint_handoff


@pytest.fixture
def reader():
    return broadband56_checkpoint_handoff


def checkpoint_fixture(tmp_path, module, accepted):
    root = tmp_path/"campaign"
    backend = module.pin(write(tmp_path/"backend.json", {
        "contract_fingerprint_sha256": module.SCIENTIFIC_CONTRACT_FINGERPRINT,
        "scientific_contract": {"fixture_only": True}}))
    authorization = module.pin(write(tmp_path/"authorization.json", {
        "overall_status": "PASS", "authorization_scope": "FULL_CAMPAIGN",
        "backend_identity_manifest": backend, "fixture_only_not_execution": True}))
    previous = None
    for index, (stage, target) in enumerate((("GOLDEN", 1), ("PILOT_32", 32)), 1):
        dest = root/"stages"/f"{index:06d}_{stage.lower()}"
        value = _stage_receipt(dest/"backend", stage=stage, target=target)
        value.update(backend_identity_manifest_sha256=backend['sha256'],
                     full_campaign_authorization_receipt_sha256=authorization['sha256'],
                     prior_stage_receipt_sha256=previous)
        previous = module.pin(write(dest/"STAGE_RECEIPT.json", value))['sha256']
    attempt = root/"stages/000040_pilot_1000_fixture"
    if accepted < 1000:
        path, value = _receipt(attempt, attempt_index=1, before=32, accepted=accepted-32,
                              raw=1000, prior_sha=None, stage="PILOT_1000", cumulative_target=1000)
        if accepted == 100:
            value['round_cumulative_inputs'] = dict(value['artifacts'])
        terminal_status = 'INCOMPLETE'
    else:
        value = _stage_receipt(attempt/"backend", stage="PILOT_1000", target=1000)
        value['prior_stage_receipt_sha256'] = previous
        path = attempt/"STAGE_RECEIPT.json"
        terminal_status = 'PASS'
    value.update(backend_identity_manifest_sha256=backend['sha256'],
                 full_campaign_authorization_receipt_sha256=authorization['sha256'])
    write(path, value)
    roles = list(module.expected_stage_role_order("PILOT_1000"))
    if accepted < 1000:
        roles = roles[:roles.index('stage_attempt_finalizer')+1]
    records = []
    for role in roles:
        receipt = {"overall_status": "PASS", "fixture_only": True}
        if role == 'stage_attempt_finalizer' and accepted < 1000:
            receipt['progress_receipt'] = module.pin(path)
        role_path = write(attempt/'backend/roles'/role/'ROLE_RECEIPT.json', receipt)
        records.append(dict(role=role, return_code=0, receipt=module.pin(role_path)))
    write(attempt/'backend/STAGE_EXECUTION_TRACE.json', {
        'overall_status': terminal_status, 'stage': 'PILOT_1000',
        'campaign_id': module.CAMPAIGN_ID,
        'contract_fingerprint_sha256': module.SCIENTIFIC_CONTRACT_FINGERPRINT,
        'all_role_return_codes_zero': True, 'all_role_receipts_pass': True,
        'role_order': roles, 'roles': records})
    write(root/'CAMPAIGN_STATUS.json', {
        'overall_status': 'QUEUED_WAITING_FOR_CAPACITY', 'check_index': 40,
        'simulator_action_taken_on_this_iteration': True, 'active_simulator_jobs': 0,
        'current_stage': 'PILOT_1000', 'current_accepted': accepted, 'feature_rows': accepted*56,
        'campaign_id': module.CAMPAIGN_ID,
        'contract_fingerprint_sha256': module.SCIENTIFIC_CONTRACT_FINGERPRINT,
        'queue_id': module.QUEUE_ID, 'authoritative_supervisor': module.SUPERVISOR_ID})
    return root, attempt, backend, authorization


@pytest.mark.parametrize('accepted', [100, 861, 999, 1000])
def test_exact_committed_count_not_hardcoded_to_100_or_1000(tmp_path, reader, accepted):
    root, attempt, backend, auth = checkpoint_fixture(tmp_path, reader, accepted)
    before = {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    value = reader.committed_boundary(root, attempt, backend=backend, authorization=auth)
    assert value['accepted'] == accepted and value['feature_rows'] == accepted*56
    assert value['remaining'] == 200000-accepted
    assert not value['actual_process_death_verified'] and not value['sole_lease_transfer_verified']
    assert before == {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('failure', ['files_only','parent_running','parent_old_count',
    'parent_wrong_iteration','new_attempt','missing_qa','role_failed','artifact_drift', 'both_receipts'])
def test_uncommitted_or_corrupt_boundary_cannot_be_a_handoff(tmp_path, reader, failure):
    root, attempt, backend, auth = checkpoint_fixture(tmp_path, reader, 861)
    state_path = root/'CAMPAIGN_STATUS.json'
    state = json.loads(state_path.read_text())
    trace_path = attempt/'backend/STAGE_EXECUTION_TRACE.json'
    trace = json.loads(trace_path.read_text())
    if failure == 'files_only':
        (attempt/'STAGE_PROGRESS_RECEIPT.json').unlink()
        (attempt/'completed.s4p').write_text('not a checkpoint')
    elif failure == 'parent_running':
        state['overall_status'] = 'PILOT_1000_RUNNING'
    elif failure == 'parent_old_count':
        state['current_accepted'] = 100
    elif failure == 'parent_wrong_iteration':
        state['check_index'] = 39
    elif failure == 'new_attempt':
        (root/'stages/000041_next_attempt').mkdir()
    elif failure == 'missing_qa':
        trace['roles'] = [r for r in trace['roles'] if r['role'] != 'full_band_s4p_qa_builder']
    elif failure == 'role_failed':
        trace['roles'][0]['return_code'] = 1
    elif failure == 'artifact_drift':
        value = json.loads((attempt/'STAGE_PROGRESS_RECEIPT.json').read_text())
        Path(value['artifacts']['long_features']['path']).write_text('changed')
    elif failure == 'both_receipts':
        write(attempt/'STAGE_RECEIPT.json', {})
    write(state_path, state)
    write(trace_path, trace)
    with pytest.raises(ValueError):
        reader.committed_boundary(root, attempt, backend=backend, authorization=auth)


@pytest.mark.parametrize('accepted', [100,861,999,1000])
def test_migration_preserves_actual_count_and_original_artifacts(tmp_path,reader,accepted):
    root,attempt,backend,auth = checkpoint_fixture(tmp_path,reader,accepted)
    proof = reader.committed_boundary(root,attempt,backend=backend,authorization=auth)
    new_backend_value = json.loads(Path(backend['path']).read_text())
    new_backend_value['fixture_new_scheduler'] = True
    new_backend = reader.pin(write(tmp_path/'target_backend.json',new_backend_value))
    new_auth = reader.pin(write(tmp_path/'target_auth.json',dict(overall_status='PASS',
        authorization_scope='FULL_CAMPAIGN',backend_identity_manifest=new_backend,fixture_only=True)))
    original = proof['source_stages'][0]
    template = json.loads(Path(original['path']).read_text())
    template['operational_progress_rebind'] = dict(previous_rebound_receipt=original,
        target_backend_manifest=new_backend,target_authorization=new_auth)
    template_pin = reader.pin(write(tmp_path/'golden_fixture.json',template))
    before = {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result = reader.migrate_boundary(proof,target_root=tmp_path/'resume_view',
        target_backend=new_backend,target_authorization=new_auth,golden_template=template_pin)
    value = json.loads(Path(result['path']).read_text())
    assert value['accepted_preserved'] == accepted
    assert value['accepted_increment'] == 0 and value['feature_rows_preserved'] == accepted*56
    assert not value['queue_created'] and not value['supervisor_started']
    assert all(pair['original']['sha256'] == pair['replacement']['sha256'] for pair in value['copied_artifacts'])
    assert before == {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(FileExistsError):
        reader.migrate_boundary(proof,target_root=tmp_path/'resume_view',
            target_backend=new_backend,target_authorization=new_auth,golden_template=template_pin)
    assert reader.pin(Path(result['path'])) == result


def resume_fixture(tmp_path, accepted):
    module = broadband56_checkpoint_handoff
    root, attempt, backend, auth = checkpoint_fixture(tmp_path, module, accepted)
    proof = module.committed_boundary(root, attempt, backend=backend, authorization=auth)
    boundary = module.pin(write(tmp_path/'COMMITTED_CHECKPOINT_FIXTURE.json', proof))
    new_backend = module.pin(write(tmp_path/'target_backend.json', module.read(Path(backend['path']))))
    new_auth = module.pin(write(tmp_path/'target_auth.json', dict(overall_status='PASS',
        authorization_scope='FULL_CAMPAIGN', backend_identity_manifest=new_backend, fixture_only=True)))
    original = proof['source_stages'][0]
    template = module.read(Path(original['path']))
    template['operational_progress_rebind'] = dict(previous_rebound_receipt=original,
        target_backend_manifest=new_backend, target_authorization=new_auth)
    template_pin = module.pin(write(tmp_path/'golden_fixture.json', template))
    migration = module.migrate_boundary(proof, target_root=tmp_path/'resume_view',
        target_backend=new_backend, target_authorization=new_auth, golden_template=template_pin)
    return root, boundary, migration, new_backend, new_auth


def process_fixture(pid):
    return dict(pid=pid, parent_pid=1, uid=1001, start_ticks=pid+400, boot_id='fixture-boot',
        command_line_sha256=f'{pid:064x}', executable_path='/fixture/python',
        executable_sha256='b'*64, state='S')


def normal_handoff_fixture(tmp_path, accepted=861):
    from tests.test_broadband56_swap_override_control_scripts import CONTROLLER as controller
    module = broadband56_checkpoint_handoff
    root, boundary, migration, backend, auth = resume_fixture(tmp_path, accepted)
    lock = dict(path=str(tmp_path/'existing.lock'), expected_contents=module.SUPERVISOR_ID,
                exclusive_flock_required=True)
    lease = dict(campaign_id=module.CAMPAIGN_ID, queue_id=module.QUEUE_ID,
        logical_supervisor_id=module.SUPERVISOR_ID, physical_process=process_fixture(103),
        backend_identity_manifest=module.read(Path(boundary['path']))['source_backend'],
        lease_generation=28, campaign_lock=lock)
    handoff = dict(schema=controller.HANDOFF_SCHEMA, decision=controller.HANDOFF_DECISION,
        overall_status='PASS', campaign_id=module.CAMPAIGN_ID, queue_id=module.QUEUE_ID,
        supervisor_id=module.SUPERVISOR_ID, contract_fingerprint_sha256=module.SCIENTIFIC_CONTRACT_FINGERPRINT,
        old_process_pid=103, new_process_pid=104, old_process_identity=process_fixture(103),
        new_process_identity=process_fixture(104), old_process_confirmed_exited=True,
        new_process_is_sole_authoritative_supervisor=True, supervisor_count_after=1,
        overlap_seconds=0, new_queue_or_campaign_created=False, nn_training_started=False,
        handoff_scope=module.CHECKPOINT_HANDOFF_SCOPE, recovery_scope=module.CHECKPOINT_HANDOFF_SCOPE,
        checkpoint_boundary=boundary, checkpoint_migration=migration,
        prior_supervisor_lease=module.pin(write(tmp_path/'old_lease.json',lease)),
        next_lease_generation=29, campaign_lock=lock, accepted_preserved=accepted,
        feature_rows_preserved=accepted*56, active_simulator_jobs=0, simulator_action_taken=False,
        resume_stage='PILOT_1000' if accepted < 1000 else 'PHASE_A')
    return controller, root, handoff, backend, auth


@pytest.mark.parametrize('accepted', [100, 861, 999, 1000])
def test_resume_state_is_dynamic_and_does_not_restart_golden(tmp_path, accepted):
    module = broadband56_checkpoint_handoff
    _, boundary, migration, _, _ = resume_fixture(tmp_path, accepted)
    state = module.verified_resume_state(boundary, migration)
    assert state['current_accepted'] == accepted and state['feature_rows'] == accepted*56
    assert state['current_stage'] == ('PILOT_1000' if accepted < 1000 else 'PHASE_A')
    assert state['check_index'] == 40 and state['current_concurrency'] == 0
    assert state['resource_gate'] == 'NOT_RUN' and 'latest_resource_gate' not in state


def test_normal_handoff_extends_ordered_chain_without_failure_receipt(tmp_path, monkeypatch):
    controller, _, normal, _, _ = normal_handoff_fixture(tmp_path)
    def legacy(old, new, recovery):
        value = dict(normal, old_process_pid=old, new_process_pid=new,
            old_process_identity=process_fixture(old), new_process_identity=process_fixture(new),
            handoff_scope='ISOLATION_GATE_AUTHORIZED_SUPERVISOR_ANCESTOR_IDENTITY_FIX')
        if recovery:
            value['recovery_scope'] = controller.RECOVERY_SCOPE
        return value
    monkeypatch.setattr(controller.isolation_identity, 'read_process_identity',
        lambda pid: process_fixture(104) if pid == 104 else None)
    assert controller._validate_handoff_chain(
        operational_handoff=legacy(100,101,False), hotfix_handoff=legacy(101,102,False),
        recovery_handoffs=[legacy(102,103,True), normal], expected_hotfix_old_pid=101,
        current_pid=104) == 100
    assert 'restart_failure_receipt' not in normal


@pytest.mark.parametrize('failure', ['old_alive','pid_reuse','old_identity','generation',
    'lock','count','rows','wrong_stage','failure_receipt','missing_checkpoint','migration_drift',
    'extra_receipt','scope_mixing','source_status_changed','next_pid_wrong','private_python'])
def test_normal_handoff_fails_closed_on_incomplete_or_mismatched_evidence(tmp_path,monkeypatch,failure):
    controller, root, handoff, _, _ = normal_handoff_fixture(tmp_path)
    observe = lambda pid: process_fixture(104) if pid == 104 else None
    if failure == 'old_alive':
        observe = lambda pid: process_fixture(pid) if pid in (103,104) else None
    elif failure == 'pid_reuse':
        observe = lambda pid: dict(process_fixture(pid),start_ticks=9000)
    elif failure == 'old_identity': handoff['old_process_identity']['start_ticks'] += 1
    elif failure == 'generation': handoff['next_lease_generation'] = 30
    elif failure == 'lock': handoff['campaign_lock']['path'] += '.other'
    elif failure == 'count': handoff['accepted_preserved'] = 100
    elif failure == 'rows': handoff['feature_rows_preserved'] = 5600
    elif failure == 'wrong_stage': handoff['resume_stage'] = 'GOLDEN'
    elif failure == 'failure_receipt': handoff['restart_failure_receipt'] = {}
    elif failure == 'missing_checkpoint': handoff.pop('checkpoint_boundary')
    elif failure == 'migration_drift': Path(handoff['checkpoint_migration']['path']).write_text('{}')
    elif failure == 'extra_receipt': write(tmp_path/'resume_view/stages/000041_extra/STAGE_PROGRESS_RECEIPT.json',{})
    elif failure == 'scope_mixing': handoff['recovery_scope'] = controller.RECOVERY_SCOPE
    elif failure == 'source_status_changed':
        state = json.loads((root/'CAMPAIGN_STATUS.json').read_text());state['current_accepted']=100
        write(root/'CAMPAIGN_STATUS.json',state)
    elif failure == 'next_pid_wrong': handoff['new_process_identity']['start_ticks'] += 1
    elif failure == 'private_python': handoff['new_process_identity']['executable_sha256'] = 'e'*64
    monkeypatch.setattr(controller.isolation_identity,'read_process_identity',observe)
    assert not controller._operational_handoff_exact(handoff,expected_old_process_pid=103,
        expected_new_process_pid=104,require_process_identities=True)


@pytest.mark.parametrize('failure', [None,'count','stage','gate_stage','gate_not_pass','backend','auth','decision'])
def test_post_gate_preserves_real_progress_and_requires_same_stage_gate(tmp_path,failure):
    module = broadband56_checkpoint_handoff
    _, boundary, migration, backend, auth = resume_fixture(tmp_path,861)
    gate = dict(overall_status='PASS',current_accepted=861,current_stage='PILOT_1000',active_simulator_jobs=0)
    if failure == 'gate_stage': gate['current_stage'] = 'GOLDEN'
    if failure == 'gate_not_pass': gate['overall_status'] = 'WAIT'
    payload = dict(decision=module.RESUME_DECISION,current_accepted=861,feature_rows=861*56,
        current_stage='PILOT_1000',checkpoint_boundary=boundary,checkpoint_migration=migration,
        resource_gate_receipt=module.pin(write(tmp_path/'gate_fixture.json',gate)))
    if failure == 'count': payload['current_accepted']=0
    if failure == 'stage': payload['current_stage']='GOLDEN'
    if failure == 'backend': backend=dict(backend,sha256='f'*64)
    if failure == 'auth': auth=dict(auth,sha256='f'*64)
    if failure == 'decision': payload['decision']='PASS_ANY_STAGE'
    assert module.post_gate_progress_exact(payload,backend_record=backend,authorization_record=auth) == (failure is None)


def test_legacy_post_gate_still_requires_zero(tmp_path):
    module = broadband56_checkpoint_handoff
    payload = dict(decision='START_CORRECTED_RESCUE_GOLDEN',current_accepted=0)
    assert module.post_gate_progress_exact(payload,backend_record={},authorization_record={})
    payload['current_accepted']=100
    assert not module.post_gate_progress_exact(payload,backend_record={},authorization_record={})


@pytest.mark.parametrize('failure', [None,'golden_gate','wrong_boundary','wrong_lease','new_pid','failure_lease'])
def test_normal_handoff_is_bound_to_the_actual_post_gate_and_successor_lease(tmp_path,failure):
    controller, _, handoff, _, _ = normal_handoff_fixture(tmp_path)
    module = broadband56_checkpoint_handoff
    record = module.pin(write(tmp_path/'handoff.json',handoff))
    post = dict(decision=module.RESUME_DECISION,checkpoint_boundary=handoff['checkpoint_boundary'],
        checkpoint_migration=handoff['checkpoint_migration'])
    lease = dict(operational_handoff_receipt=record,physical_process=handoff['new_process_identity'],
        prior_supervisor_lease=handoff['prior_supervisor_lease'],lease_generation=29,
        campaign_lock=handoff['campaign_lock'])
    if failure == 'golden_gate': post['decision']='START_CORRECTED_RESCUE_GOLDEN'
    if failure == 'wrong_boundary': post['checkpoint_boundary']={}
    if failure == 'wrong_lease': lease['lease_generation']=30
    if failure == 'new_pid': lease['physical_process']=process_fixture(105)
    if failure == 'failure_lease': lease['restart_failure_receipt']={}
    if failure is None:
        controller._validate_checkpoint_resume_binding(handoff,handoff_record=record,post_gate=post,lease=lease)
    else:
        with pytest.raises(controller.SwapOverrideControllerError,match='checkpoint gate/lease'):
            controller._validate_checkpoint_resume_binding(handoff,handoff_record=record,post_gate=post,lease=lease)
