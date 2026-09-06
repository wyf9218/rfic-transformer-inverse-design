"""Read a committed production boundary; no signal, process, queue or lease writes."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from rfic_transformer_inverse_design.campaigns import broadband56_production_backend as production
from rfic_transformer_inverse_design.campaigns import broadband56_stage_progress as progress
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import CAMPAIGN_ID
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT, stage_for_progress,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import expected_stage_role_order

QUEUE_ID = 'b56-v2-queue-20260901T184307Z'
SUPERVISOR_ID = 'b56-v2-controller-3184781-20260901T184307Z'
CHECKPOINT_HANDOFF_SCOPE = 'SAME_LOGICAL_SUPERVISOR_AFTER_COMMITTED_ATTEMPT'
RESUME_DECISION = 'RESUME_FROM_COMMITTED_CHECKPOINT_WITHOUT_GOLDEN'


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


CONTROL_ROOT_FILES = (
    'CAMPAIGN_CONTRACT.json', 'SCIENTIFIC_CONTRACT_IDENTITY.json',
    'OPERATIONAL_POLICY_IDENTITY.json', 'FREQUENCY_CONTRACT.json',
    'PORT_AND_GROUNDING_CONTRACT.json', 'DRC_AND_LAYOUT_CONTRACT.json',
    'MARS_QUEUE_ENTRY.json', 'MARS_QUEUE_RECEIPT.json', 'SUPERVISOR_IDENTITY.json',
    'CAMPAIGN_LOCK.json', 'SHA256SUMS.txt', 'CAMPAIGN_STATUS.json',
    'FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json',
)
CONTROL_ROOT_DIRS = ('resource_snapshots', 'resource_gates', 'resource_snapshot_adapters', 'stages')


def validate_empty_control_envelope(record, *, proof, target_root, backend, authorization):
    """Permit only a bound, newly constructed empty control envelope, never a resume overwrite."""
    value = read(bound(record))
    target = Path(target_root)
    if (value.get('overall_status') != 'CONTROL_ENVELOPE_ONLY_NOT_LAUNCH_AUTHORITY'
            or value.get('target_root') != str(target)
            or value.get('source_terminal_receipt') != proof['terminal_receipt']
            or value.get('target_backend') != backend
            or value.get('target_authorization') != authorization
            or set(value.get('control_files', {})) != set(CONTROL_ROOT_FILES)):
        raise ValueError('control envelope identity mismatch')
    if set(p.name for p in target.iterdir()) != set(CONTROL_ROOT_FILES+CONTROL_ROOT_DIRS):
        raise ValueError('control envelope contains unexpected or existing execution outputs')
    for name in CONTROL_ROOT_FILES:
        if bound(value['control_files'][name]) != target/name:
            raise ValueError('control envelope file escaped target root')
    for name in CONTROL_ROOT_DIRS:
        path = target/name
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise ValueError('control envelope execution directories must be empty')
    source_root = Path(proof['terminal_receipt']['path']).parent.parent.parent
    for name in CONTROL_ROOT_FILES[:6]:
        if (target/name).read_bytes() != (source_root/name).read_bytes():
            raise ValueError('control envelope changed an immutable campaign contract')
    entry = read(target/'MARS_QUEUE_ENTRY.json')
    state = read(target/'CAMPAIGN_STATUS.json')
    if (entry.get('queue_id') != QUEUE_ID or entry.get('campaign_id') != CAMPAIGN_ID
            or entry.get('backend_identity_manifest') != backend
            or state.get('current_accepted') != proof['accepted']
            or state.get('feature_rows') != proof['feature_rows']
            or (target/'FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json').read_bytes()
                != bound(authorization).read_bytes()):
        raise ValueError('control envelope queue, progress or authorization mismatch')


def migrate_boundary(proof, *, target_root, target_backend, target_authorization, golden_template,
                     control_envelope=None):
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
    if control_envelope is None:
        target.mkdir(mode=0o700, parents=False, exist_ok=False)
    else:
        validate_empty_control_envelope(control_envelope, proof=proof, target_root=target,
            backend=target_backend, authorization=target_authorization)
        # An exclusive claim prevents a second migration into the same new envelope.
        with (target/'CHECKPOINT_MIGRATION_CLAIM.json').open('x') as handle:
            json.dump(dict(control_envelope=control_envelope), handle, sort_keys=True)
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
        if control_envelope is not None:
            result['control_envelope'] = control_envelope
        return write_new(target/'CHECKPOINT_REBIND_RECEIPT.json',result)
    except BaseException as error:
        write_new(target/'CHECKPOINT_REBIND_FAILURE.json',dict(overall_status='FAIL',error=repr(error),
            source_evidence_modified=False,simulator_action_taken=False))
        raise


def verified_resume_state(boundary_record, migration_record):
    """Validate the actual source and migrated chains before deriving resume state.

    This is a startup-only check. It does not transfer the lease, assert process
    death, authorize a runtime, or substitute for fresh capacity admission.
    """
    proof = read(bound(boundary_record))
    terminal_path = bound(proof['terminal_receipt'])
    source_root = terminal_path.parent.parent.parent
    observed = committed_boundary(source_root, terminal_path.parent,
        backend=proof['source_backend'], authorization=proof['source_authorization'])
    if proof != observed:
        raise ValueError('saved boundary differs from the complete committed checkpoint')
    migration_path = bound(migration_record)
    migration = read(migration_path)
    if (migration.get('overall_status') != 'PASS_MIGRATION_ONLY_NOT_LAUNCH_AUTHORITY'
            or migration.get('source_terminal_receipt') != proof['terminal_receipt']
            or migration.get('source_stages') != proof['source_stages']
            or migration.get('source_progress') != proof['source_progress']
            or migration.get('accepted_preserved') != proof['accepted']
            or migration.get('feature_rows_preserved') != proof['feature_rows']
            or migration.get('accepted_increment') != 0
            or any(migration.get(k) is not False for k in (
                'source_evidence_modified', 'simulator_action_taken', 'queue_created',
                'supervisor_started', 'nn_training_started'))):
        raise ValueError('migration does not preserve the committed checkpoint')
    target_root = migration_path.parent
    target_backend = read(bound(migration['target_backend']))
    target_auth = read(bound(migration['target_authorization']))
    source_backend = read(bound(proof['source_backend']))
    if (target_backend.get('scientific_contract') != source_backend['scientific_contract']
            or target_backend.get('contract_fingerprint_sha256') != SCIENTIFIC_CONTRACT_FINGERPRINT
            or target_auth.get('overall_status') != 'PASS'
            or target_auth.get('authorization_scope') != 'FULL_CAMPAIGN'
            or target_auth.get('backend_identity_manifest') != migration['target_backend']):
        raise ValueError('resume scientific or authorization binding differs')
    chains = []
    for key, name in (('stage_receipts', 'STAGE_RECEIPT.json'),
                      ('progress_receipts', 'STAGE_PROGRESS_RECEIPT.json')):
        chain = [(bound(item), read(bound(item))) for item in migration[key]]
        if ({p for p, _ in chain} != set((target_root/'stages').glob('*/'+name))
                or len(chain) != len({p for p, _ in chain})):
            raise ValueError('resume view contains missing, duplicate or unbound receipts')
        chains.append(chain)
    stages, records = chains
    target_args = dict(backend_manifest_sha256=migration['target_backend']['sha256'],
        authorization_receipt_sha256=migration['target_authorization']['sha256'], verify_artifacts=True)
    errors = production.validate_stage_receipt_chain(stages, **target_args)
    preceding = [v for _, v in stages if v['stage'] != proof['current_stage']]
    base = int(preceding[-1]['accepted_unique_geometries']) if preceding else 0
    errors += progress.validate_stage_progress_chain(records, stage=proof['current_stage'],
        base_accepted=base, **target_args)
    if errors:
        raise ValueError('resume chains failed: '+repr(errors[:12]))
    for pair in migration['copied_artifacts']:
        source, replacement = bound(pair['original']), bound(pair['replacement'])
        if (not source.is_relative_to(source_root) or not replacement.is_relative_to(target_root)
                or pair['original']['sha256'] != pair['replacement']['sha256']
                or pair['original']['size_bytes'] != pair['replacement']['size_bytes']):
            raise ValueError('migrated artifact identity or location differs')
    accepted = (progress.accepted_after_progress(records, base_accepted=base)
                if terminal_path.name == 'STAGE_PROGRESS_RECEIPT.json'
                else stages[-1][1]['accepted_unique_geometries'])
    if accepted != proof['accepted']:
        raise ValueError('resume receipts changed the actual accepted count')
    # The stage selector consumes completed-stage totals, not partial progress.
    next_stage = stage_for_progress(
        current_accepted=stages[-1][1]['accepted_unique_geometries'] if stages else 0,
        stage_receipts=[v for _, v in stages])
    if next_stage == 'GOLDEN':
        raise ValueError('checkpoint resume must not rerun Golden')
    state = read(source_root/'CAMPAIGN_STATUS.json')
    state.update(overall_status='COMPLETE_200K' if next_stage == 'COMPLETE' else 'QUEUED_WAITING_FOR_CAPACITY',
        current_stage=next_stage, current_accepted=accepted, feature_rows=accepted*56,
        active_simulator_jobs=0, current_concurrency=0, resource_gate='NOT_RUN',
        simulator_action_taken_on_this_iteration=False)
    for key in ('latest_resource_gate', 'latest_resource_snapshot', 'failed_resource_checks'):
        state.pop(key, None)
    return state


def validate_failed_control_predecessor(failure_record, *, prior_record, boundary_record, state):
    """Bind a dead control-only successor to its unchanged production checkpoint."""
    failure = read(bound(failure_record))
    prior = read(bound(prior_record))
    handoff_record = prior['operational_handoff_receipt']
    handoff = read(bound(handoff_record))
    if (failure.get('overall_status') != 'FAIL'
            or failure.get('successor_lease') != prior_record
            or failure.get('boundary') != boundary_record
            or failure.get('failed_physical_pid') != prior['physical_process']['pid']
            or failure.get('physical_process_confirmed_absent') is not True
            or failure.get('fixed48_solver_execution_started') is not False
            or failure.get('source_raw_artifacts_untouched') is not True
            or failure.get('nn_training_started') is not False
            or failure.get('current_accepted') != state['current_accepted']
            or failure.get('current_feature_rows') != state['feature_rows']
            or handoff.get('new_process_identity') != prior['physical_process']
            or handoff.get('next_lease_generation') != prior['lease_generation']
            or handoff.get('checkpoint_boundary') != boundary_record
            or validate_checkpoint_handoff(handoff) != state):
        raise ValueError('failed successor is not bound to the preserved checkpoint')
    migration = read(bound(handoff['checkpoint_migration']))
    if migration.get('target_backend') != prior['backend_identity_manifest']:
        raise ValueError('failed successor migration backend differs')
    source_status = read(bound(failure['source_campaign_status']))
    successor_status = read(bound(failure['successor_campaign_status']))
    if (any(s.get('current_accepted') != state['current_accepted']
            or s.get('feature_rows') != state['feature_rows']
            for s in (source_status, successor_status))
            or successor_status.get('simulator_action_taken_on_this_iteration') is not False):
        raise ValueError('failed successor changed accepted progress or executed simulators')
    return handoff_record


def validate_checkpoint_handoff(payload):
    """Check a pinned normal handoff without inventing a failure or an approval."""
    if (payload.get('handoff_scope') != CHECKPOINT_HANDOFF_SCOPE
            or payload.get('recovery_scope') != CHECKPOINT_HANDOFF_SCOPE
            or 'restart_failure_receipt' in payload
            or payload.get('active_simulator_jobs') != 0
            or payload.get('simulator_action_taken') is not False):
        raise ValueError('normal handoff is not a committed-checkpoint transition')
    state = verified_resume_state(payload['checkpoint_boundary'], payload['checkpoint_migration'])
    proof = read(bound(payload['checkpoint_boundary']))
    old_lease = read(bound(payload['prior_supervisor_lease']))
    failure_record = payload.get('prior_startup_terminal_failure')
    if failure_record is not None:
        validate_failed_control_predecessor(failure_record,
            prior_record=payload['prior_supervisor_lease'],
            boundary_record=payload['checkpoint_boundary'], state=state)
    elif old_lease.get('backend_identity_manifest') != proof['source_backend']:
        raise ValueError('normal handoff lease backend differs from committed source')
    old_process, new_process = payload.get('old_process_identity', {}), payload.get('new_process_identity', {})
    if (old_lease.get('campaign_id') != CAMPAIGN_ID or old_lease.get('queue_id') != QUEUE_ID
            or old_lease.get('logical_supervisor_id') != SUPERVISOR_ID
            or old_lease.get('physical_process') != payload.get('old_process_identity')
            or any(old_process.get(k) != new_process.get(k) for k in (
                'uid', 'executable_path', 'executable_sha256'))
            or type(old_lease.get('lease_generation')) is not int
            or payload.get('next_lease_generation') != old_lease['lease_generation']+1
            or payload.get('campaign_lock') != old_lease.get('campaign_lock')
            or payload.get('accepted_preserved') != state['current_accepted']
            or payload.get('feature_rows_preserved') != state['feature_rows']
            or payload.get('resume_stage') != state['current_stage']):
        raise ValueError('normal handoff changed lease or checkpoint identity')
    return state


def post_gate_progress_exact(payload, *, backend_record, authorization_record):
    """Distinguish legacy Golden start from a fully bound checkpoint resume."""
    if payload.get('decision') == 'START_CORRECTED_RESCUE_GOLDEN':
        return payload.get('current_accepted') == 0
    if payload.get('decision') != RESUME_DECISION:
        return False
    try:
        state = verified_resume_state(payload['checkpoint_boundary'], payload['checkpoint_migration'])
        migration = read(bound(payload['checkpoint_migration']))
        gate = read(bound(payload['resource_gate_receipt']))
        return (migration['target_backend'] == backend_record
            and migration['target_authorization'] == authorization_record
            and payload.get('current_accepted') == state['current_accepted']
            and payload.get('feature_rows') == state['feature_rows']
            and payload.get('current_stage') == state['current_stage']
            and state['current_stage'] not in ('GOLDEN', 'COMPLETE')
            and gate.get('overall_status') == 'PASS'
            and gate.get('current_accepted') == state['current_accepted']
            and gate.get('current_stage') == state['current_stage']
            and gate.get('active_simulator_jobs') == 0)
    except (KeyError, OSError, TypeError, ValueError):
        return False
