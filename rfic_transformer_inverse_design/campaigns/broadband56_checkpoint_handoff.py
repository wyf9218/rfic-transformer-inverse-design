"""Read a committed production boundary; no signal, process, queue or lease writes."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from rfic_transformer_inverse_design.campaigns import broadband56_production_backend as production
from rfic_transformer_inverse_design.campaigns import broadband56_stage_progress as progress
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import CAMPAIGN_ID
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import SCIENTIFIC_CONTRACT_FINGERPRINT
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import expected_stage_role_order

QUEUE_ID = 'b56-v2-queue-20260901T184307Z'
SUPERVISOR_ID = 'b56-v2-controller-3184781-20260901T184307Z'


class CheckpointNotReady(ValueError):
    pass


def pin(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('checkpoint source must be absolute without symlinks')
    data = path.read_bytes()
    return dict(path=str(path), size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def read(path):
    value = json.loads(Path(path).read_bytes())
    if not isinstance(value, dict):
        raise ValueError('checkpoint object required')
    return value


def bound(record):
    path = Path(record['path'])
    if pin(path) != record:
        raise ValueError('checkpoint source identity drift: '+str(path))
    return path


def committed_boundary(campaign_root, attempt_root, *, backend, authorization):
    """Bind an entire committed attempt, without assuming its accepted count.

    Physical process death and lock ownership are separate execution conditions.
    This function cannot authorize or perform a handoff by itself.
    """
    root, attempt = Path(campaign_root), Path(attempt_root)
    if attempt.parent != root/'stages':
        raise ValueError('attempt is outside the existing campaign')
    state_path = root/'CAMPAIGN_STATUS.json'
    state_pin, state = pin(state_path), read(state_path)
    terminal = [attempt/name for name in ('STAGE_RECEIPT.json', 'STAGE_PROGRESS_RECEIPT.json')
                if (attempt/name).is_file()]
    if len(terminal) != 1:
        raise CheckpointNotReady('expected attempt has no unique committed terminal receipt')
    terminal_path = terminal[0]
    terminal_pin, receipt = pin(terminal_path), read(terminal_path)
    stage = receipt['stage']
    if state.get('overall_status') not in ('QUEUED_WAITING_FOR_CAPACITY', 'COMPLETE_200K'):
        raise CheckpointNotReady('parent controller has not committed its returned attempt state')
    index = int(attempt.name.split('_', 1)[0])
    if (type(state.get('check_index')) is not int or state['check_index'] != index
            or state.get('simulator_action_taken_on_this_iteration') is not True):
        raise CheckpointNotReady('parent state is not the exact completed attempt')
    for key, expected in (('campaign_id', CAMPAIGN_ID),
                          ('contract_fingerprint_sha256', SCIENTIFIC_CONTRACT_FINGERPRINT),
                          ('queue_id', QUEUE_ID), ('authoritative_supervisor', SUPERVISOR_ID)):
        if state.get(key) != expected:
            raise ValueError('parent checkpoint identity mismatch: '+key)
    # A newer attempt must not be hidden by a stale status or a brief native-job gap.
    if any(p.is_dir() and p.name[:6].isdigit() and int(p.name[:6]) > index
           for p in (root/'stages').iterdir()):
        raise CheckpointNotReady('a newer attempt directory already exists')
    backend_path, authorization_path = bound(backend), bound(authorization)
    if read(backend_path).get('contract_fingerprint_sha256') != SCIENTIFIC_CONTRACT_FINGERPRINT:
        raise ValueError('source backend scientific identity differs')
    auth = read(authorization_path)
    if (auth.get('overall_status') != 'PASS' or auth.get('authorization_scope') != 'FULL_CAMPAIGN'
            or auth.get('backend_identity_manifest', {}).get('sha256') != backend['sha256']):
        raise ValueError('source checkpoint authorization identity differs')
    stages = [(p, read(p)) for p in sorted((root/'stages').glob('*/STAGE_RECEIPT.json'))]
    errors = production.validate_stage_receipt_chain(stages,
        backend_manifest_sha256=backend['sha256'], authorization_receipt_sha256=authorization['sha256'],
        verify_artifacts=True)
    preceding = [value for _, value in stages if value['stage'] != stage]
    base = int(preceding[-1]['accepted_unique_geometries']) if preceding else 0
    records = [(p, read(p)) for p in sorted((root/'stages').glob('*/STAGE_PROGRESS_RECEIPT.json'))
               if read(p)['stage'] == stage]
    errors += progress.validate_stage_progress_chain(records, stage=stage, base_accepted=base,
        backend_manifest_sha256=backend['sha256'], authorization_receipt_sha256=authorization['sha256'],
        verify_artifacts=True)
    if errors:
        raise ValueError('committed receipt chain invalid: '+repr(errors[:12]))
    accepted = (progress.accepted_after_progress(records, base_accepted=base)
                if terminal_path.name == 'STAGE_PROGRESS_RECEIPT.json'
                else receipt['accepted_unique_geometries'])
    if type(accepted) is not int or not 0 <= accepted <= 200000:
        raise ValueError('invalid committed accepted count')
    if (type(state.get('current_accepted')) is not int or type(state.get('feature_rows')) is not int
            or state.get('current_accepted') != accepted or state.get('feature_rows') != accepted*56
            or state.get('active_simulator_jobs') != 0):
        raise CheckpointNotReady('parent count/rows/active-jobs do not match committed receipt')
    trace_path = attempt/'backend/STAGE_EXECUTION_TRACE.json'
    trace_pin, trace = pin(trace_path), read(trace_path)
    expected_roles = list(expected_stage_role_order(stage))
    incomplete = terminal_path.name == 'STAGE_PROGRESS_RECEIPT.json'
    if incomplete:
        expected_roles = expected_roles[:expected_roles.index('stage_attempt_finalizer')+1]
    roles = trace.get('roles', [])
    if (trace.get('overall_status') != ('INCOMPLETE' if incomplete else 'PASS')
            or trace.get('stage') != stage or trace.get('campaign_id') != CAMPAIGN_ID
            or trace.get('contract_fingerprint_sha256') != SCIENTIFIC_CONTRACT_FINGERPRINT
            or trace.get('all_role_return_codes_zero') is not True
            or trace.get('all_role_receipts_pass') is not True
            or trace.get('role_order') != expected_roles
            or [r.get('role') for r in roles] != expected_roles):
        raise ValueError('attempt trace has not completed the required physical and QA chain')
    sources = [state_pin, terminal_pin, trace_pin, backend, authorization]
    for role in roles:
        record = role['receipt']
        role_path = bound(record)
        if not role_path.is_relative_to(attempt) or role.get('return_code') != 0:
            raise ValueError('role output or return code differs')
        value = read(role_path)
        if value.get('overall_status') != 'PASS':
            raise ValueError('physical/QA role did not pass')
        if role['role'] == 'stage_attempt_finalizer' and incomplete:
            frozen = bound(value['progress_receipt'])
            if frozen.read_bytes() != terminal_path.read_bytes():
                raise ValueError('finalizer progress is not the committed progress')
        sources.append(record)
    for path, _ in stages + records:
        sources.append(pin(path))
    for source in sources:
        bound(source)
    return dict(overall_status='COMMITTED_BOUNDARY_VERIFIED_NOT_HANDOFF_AUTHORITY',
        campaign_id=CAMPAIGN_ID, queue_id=QUEUE_ID, logical_supervisor_id=SUPERVISOR_ID,
        current_stage=stage, check_index=index, accepted=accepted, feature_rows=accepted*56,
        remaining=200000-accepted, terminal_receipt=terminal_pin,
        source_stages=[pin(p) for p, _ in stages], source_progress=[pin(p) for p, _ in records],
        sources=sources, source_backend=backend, source_authorization=authorization,
        actual_process_death_verified=False, sole_lease_transfer_verified=False,
        simulator_action_taken=False, source_modified=False)


def migrate_boundary(proof, *, target_root, target_backend, target_authorization, golden_template):
    """Copy only verified receipt artifacts into a no-clobber resume view.

    Original GDS/S4P and source receipts remain untouched. Creating this view
    neither registers a new queue nor permits a supervisor to start.
    """
    if proof.get('overall_status') != 'COMMITTED_BOUNDARY_VERIFIED_NOT_HANDOFF_AUTHORITY':
        raise ValueError('verified committed boundary required')
    for source in proof['sources']:
        bound(source)
    target = Path(target_root)
    source_root = Path(proof['terminal_receipt']['path']).parent.parent.parent
    if target == source_root or target.is_relative_to(source_root) or source_root.is_relative_to(target):
        raise ValueError('resume view must not overlap the original campaign')
    backend = read(bound(target_backend))
    auth = read(bound(target_authorization))
    old_backend = read(bound(proof['source_backend']))
    if (not isinstance(backend.get('scientific_contract'), dict)
            or backend.get('scientific_contract') != old_backend.get('scientific_contract')
            or backend.get('contract_fingerprint_sha256') != SCIENTIFIC_CONTRACT_FINGERPRINT
            or auth.get('overall_status') != 'PASS' or auth.get('authorization_scope') != 'FULL_CAMPAIGN'
            or auth.get('backend_identity_manifest') != target_backend):
        raise ValueError('resume target scientific/authorization identity mismatch')
    template = read(bound(golden_template))
    if template.get('stage') != 'GOLDEN':
        raise ValueError('exact prepared Golden reuse template required')
    target.mkdir(mode=0o700, parents=False, exist_ok=False)
    copied, stages, records = [], [], []

    def write_new(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write('\n')
        return pin(path)

    def copy_artifact(record, old_root, new_root):
        source = bound(record)
        destination = new_root/source.relative_to(old_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            result = pin(destination)
            if any(result[k] != record[k] for k in ('size_bytes','sha256')):
                raise ValueError('resume artifact collision with different bytes')
            return result
        with destination.open('xb') as handle:
            handle.write(source.read_bytes())
        bound(record)
        result = pin(destination)
        if any(result[k] != record[k] for k in ('size_bytes','sha256')):
            raise ValueError('resume artifact copy changed bytes')
        copied.append(dict(original=record, replacement=result))
        return result

    try:
        prior = None
        for record in proof['source_stages']:
            path = bound(record)
            original = read(path)
            destination = target/'stages'/path.parent.name
            value = copy.deepcopy(template if original['stage']=='GOLDEN' else original)
            for key, artifact in original['artifacts'].items():
                value['artifacts'][key] = copy_artifact(artifact, path.parent/'backend', destination/'backend')
            value.update(backend_identity_manifest_sha256=target_backend['sha256'],
                full_campaign_authorization_receipt_sha256=target_authorization['sha256'],
                prior_stage_receipt_sha256=prior)
            if original['stage']=='GOLDEN':
                binding = value['operational_progress_rebind']
                if binding.get('previous_rebound_receipt') != record:
                    raise ValueError('prepared Golden template binds a different source receipt')
                binding.update(target_backend_manifest=target_backend, target_authorization=target_authorization)
            else:
                value['operational_progress_rebind'] = dict(original_stage_receipt=record,
                    target_backend_manifest=target_backend, target_authorization=target_authorization,
                    kind='REUSE_COMPLETED_STAGE_UNCHANGED_SCIENTIFIC_CONTRACT',
                    new_simulator_execution=False, accepted_count_increment=0)
            out = destination/'STAGE_RECEIPT.json'
            prior = write_new(out,value)['sha256']
            stages.append((out,value))
        prior = None
        for record in proof['source_progress']:
            path = bound(record)
            original = read(path)
            destination = target/'stages'/path.parent.name
            value = copy.deepcopy(original)
            for group in ('artifacts','round_cumulative_inputs'):
                for key, artifact in (original.get(group) or {}).items():
                    value[group][key] = copy_artifact(artifact,path.parent,destination)
            value.update(backend_identity_manifest_sha256=target_backend['sha256'],
                full_campaign_authorization_receipt_sha256=target_authorization['sha256'],
                prior_progress_receipt_sha256=prior,
                operational_progress_rebind=dict(kind='REUSE_EXISTING_REAL_EMX_PROGRESS',
                    original_receipt=record, original_backend=proof['source_backend'],
                    original_authorization=proof['source_authorization'], target_backend=target_backend,
                    target_authorization=target_authorization, simulator_execution_repeated=False,
                    new_generated_geometries=0))
            out = destination/'STAGE_PROGRESS_RECEIPT.json'
            prior = write_new(out,value)['sha256']
            records.append((out,value))
        errors = production.validate_stage_receipt_chain(stages,
            backend_manifest_sha256=target_backend['sha256'],
            authorization_receipt_sha256=target_authorization['sha256'], verify_artifacts=True)
        preceding = [v for _,v in stages if v['stage'] != proof['current_stage']]
        base = int(preceding[-1]['accepted_unique_geometries']) if preceding else 0
        errors += progress.validate_stage_progress_chain(records, stage=proof['current_stage'],
            base_accepted=base, backend_manifest_sha256=target_backend['sha256'],
            authorization_receipt_sha256=target_authorization['sha256'], verify_artifacts=True)
        if errors:
            raise ValueError('resume receipt chain failed: '+repr(errors[:12]))
        terminal = read(bound(proof['terminal_receipt']))
        if 'accepted_after' in terminal:
            accepted = progress.accepted_after_progress(records,base_accepted=base)
        else:
            accepted = stages[-1][1]['accepted_unique_geometries']
        if accepted != proof['accepted']:
            raise ValueError('resume changed the accepted count')
        for source in proof['sources']:
            bound(source)
        for source in (target_backend,target_authorization,golden_template):
            bound(source)
        for pair in copied:
            bound(pair['original']); bound(pair['replacement'])
        result = dict(overall_status='PASS_MIGRATION_ONLY_NOT_LAUNCH_AUTHORITY',
            accepted_preserved=accepted, feature_rows_preserved=accepted*56, accepted_increment=0,
            source_terminal_receipt=proof['terminal_receipt'], target_backend=target_backend,
            target_authorization=target_authorization, source_stages=proof['source_stages'],
            source_progress=proof['source_progress'], stage_receipts=[pin(p) for p,_ in stages],
            progress_receipts=[pin(p) for p,_ in records], copied_artifacts=copied,
            source_evidence_modified=False, simulator_action_taken=False, queue_created=False,
            supervisor_started=False, nn_training_started=False)
        return write_new(target/'CHECKPOINT_REBIND_RECEIPT.json',result)
    except BaseException as error:
        write_new(target/'CHECKPOINT_REBIND_FAILURE.json',dict(overall_status='FAIL',error=repr(error),
            source_evidence_modified=False,simulator_action_taken=False))
        raise
