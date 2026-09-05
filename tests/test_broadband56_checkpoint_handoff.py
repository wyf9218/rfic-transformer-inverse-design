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
