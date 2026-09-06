"""Normal-checkpoint producers for the existing exact-SHA supervisor launcher.

The caller loads its pinned legacy executor and compatibility bindings first.
These functions reuse that executor's queue, authorization and controller argv
interfaces. They neither stop the prior owner nor issue project-owner approval.
"""
from __future__ import annotations

import copy
import fcntl
import json
import os
from pathlib import Path
import time
import uuid

from rfic_transformer_inverse_design.campaigns import broadband56_checkpoint_handoff as checkpoint
from rfic_transformer_inverse_design.campaigns.broadband56_scheduling import (
    FIXED48_GENERATION_POLICY, concurrency_for_snapshot, fixed_generation_policy,
)

SCOPE = 'APPLY_FIXED48_AT_COMMITTED_CHECKPOINT_THEN_CONTINUE_EXISTING_200K'


def write(path, value):
    with Path(path).open('x') as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write('\n')
    return checkpoint.pin(path)


def validate_candidate(executor, candidate_record):
    candidate = checkpoint.read(checkpoint.bound(candidate_record))
    if (candidate.get('authorization_scope') != SCOPE
            or candidate.get('campaign_id') != checkpoint.CAMPAIGN_ID
            or candidate.get('queue_id') != checkpoint.QUEUE_ID
            or candidate.get('logical_supervisor_id') != checkpoint.SUPERVISOR_ID
            or candidate.get('contract_fingerprint_sha256') != checkpoint.SCIENTIFIC_CONTRACT_FINGERPRINT
            or candidate.get('scientific_contract_changed') is not False
            or candidate.get('nn_training_authorized') is not False
            or candidate.get('execution_authorized') is not False
            or candidate.get('fixed_generation_policy') != FIXED48_GENERATION_POLICY):
        raise ValueError('normal startup candidate scope or contract mismatch')
    executor.validate_static_candidate(candidate)
    return candidate


def validate_authority(executor, candidate_record, approval_record):
    candidate = validate_candidate(executor, candidate_record)
    approval = checkpoint.read(checkpoint.bound(approval_record))
    if (approval.get('overall_status') != 'PASS'
            or approval.get('authorization_scope') != SCOPE
            or approval.get('decision') != 'APPROVE_'+SCOPE
            or approval.get('approved_candidate') != candidate_record
            or approval.get('approved_by') != 'Yufeng Wang, project owner and project leader'
            or executor.parse_utc(approval.get('approved_utc'))
                < executor.parse_utc(candidate.get('generated_utc'))
            or candidate_record['sha256'] not in str(approval.get('approval_reference', ''))
            or approval.get('nn_training_authorized') is not False
            or approval.get('new_campaign_queue_or_logical_supervisor_authorized') is not False):
        raise ValueError('normal startup requires the exact project-owner approval receipt')
    return candidate


def source_state(proof):
    source_root = Path(proof['terminal_receipt']['path']).parent.parent.parent
    actual = checkpoint.committed_boundary(source_root,
        Path(proof['terminal_receipt']['path']).parent,
        backend=proof['source_backend'], authorization=proof['source_authorization'])
    if actual != proof:
        raise ValueError('normal startup checkpoint changed')
    stages = [checkpoint.read(checkpoint.bound(r)) for r in proof['source_stages']]
    stage = checkpoint.stage_for_progress(current_accepted=stages[-1]['accepted_unique_geometries'],
        stage_receipts=stages)
    if stage in ('GOLDEN', 'COMPLETE'):
        raise ValueError('normal startup must resume unfinished post-Golden production')
    state = checkpoint.read(source_root/'CAMPAIGN_STATUS.json')
    state.update(overall_status='QUEUED_WAITING_FOR_CAPACITY', current_stage=stage,
        current_accepted=proof['accepted'], feature_rows=proof['feature_rows'],
        active_simulator_jobs=0, current_concurrency=0, resource_gate='NOT_RUN',
        simulator_action_taken_on_this_iteration=False)
    for key in ('latest_resource_gate', 'latest_resource_snapshot', 'failed_resource_checks'):
        state.pop(key, None)
    return state


def require_exclusive_owner(executor, candidate, isolation, lock_fd):
    prior = checkpoint.read(checkpoint.bound(candidate['bound_files']['prior_supervisor_lease']))
    prior_process = prior['physical_process']
    if (prior.get('lease_generation') != candidate['predecessor_lease_generation']
            or prior_process.get('pid') != candidate['predecessor_physical_pid']
            or candidate['next_lease_generation'] != prior['lease_generation']+1):
        raise ValueError('normal startup prior lease identity mismatch')
    if isolation.read_process_identity(prior_process['pid']) is not None:
        raise ValueError('prior supervisor is still live or its PID was reused')
    current = isolation.read_process_identity(os.getpid())
    if current is None or any(current.get(k) != prior_process.get(k) for k in (
            'uid', 'executable_path', 'executable_sha256')):
        raise ValueError('normal startup private Python or owner identity mismatch')
    roles = executor.project_roles(isolation, include_self=True)
    if roles.get('supervisors') != [os.getpid()] or any(roles[k] for k in (
            'runners', 'cadence', 'calibre', 'emx')):
        raise ValueError('normal startup requires the sole owner and no surviving children')
    lock = Path(candidate['global_lock_path'])
    if (prior.get('campaign_lock') != dict(path=str(lock),
            expected_contents=checkpoint.SUPERVISOR_ID, exclusive_flock_required=True)
            or lock.is_symlink() or not lock.is_absolute()
            or (os.fstat(lock_fd).st_dev, os.fstat(lock_fd).st_ino)
                != (lock.stat().st_dev, lock.stat().st_ino)
            or lock.read_text().strip() != checkpoint.SUPERVISOR_ID):
        raise ValueError('normal startup must retain the existing campaign flock')
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return prior, isolation._public_process_record(current)


def prepare_root(executor, *, candidate, root, backend, verification, stage_launcher, state):
    root = Path(root)
    old_root = Path(candidate['current_campaign_root'])
    if (not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents))
            or root == old_root or root.is_relative_to(old_root) or old_root.is_relative_to(root)
            or not str(root).startswith(candidate['authorized_successor_root_prefix'])):
        raise ValueError('normal startup root is not a new authorized no-clobber path')
    if checkpoint.bound(candidate['bound_files']['current_queue_entry']) != old_root/'MARS_QUEUE_ENTRY.json':
        raise ValueError('normal startup queue must come from the actual source root')
    sums = {}
    for line in (old_root/'SHA256SUMS.txt').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if name in sums:
            raise ValueError('source control checksum index contains duplicate names')
        sums[name] = digest
    for name in executor.IMMUTABLE_ARTIFACTS:
        if checkpoint.pin(old_root/name)['sha256'] != sums.get(name):
            raise ValueError('source immutable control artifact changed: '+name)
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name in checkpoint.CONTROL_ROOT_FILES[:6]:
        source = checkpoint.pin(old_root/name)
        with (root/name).open('xb') as handle:
            handle.write(checkpoint.bound(source).read_bytes())
        checkpoint.bound(source)
    for name in checkpoint.CONTROL_ROOT_DIRS:
        (root/name).mkdir(mode=0o700)
    generated = executor.utc_now()
    entry = checkpoint.read(checkpoint.bound(candidate['bound_files']['current_queue_entry']))
    if entry.get('queue_id') != checkpoint.QUEUE_ID or entry.get('campaign_id') != checkpoint.CAMPAIGN_ID:
        raise ValueError('normal startup cannot create a different queue')
    entry.update(generated_utc=generated, queue_state='QUEUED_WAITING_FOR_CAPACITY',
        backend_identity_manifest=checkpoint.pin(backend),
        backend_identity_verification_receipt=checkpoint.pin(verification),
        stage_launcher=checkpoint.pin(stage_launcher),
        one_authoritative_supervisor=checkpoint.SUPERVISOR_ID,
        simulator_jobs_active=0, simulator_action_taken=False)
    queue = write(root/'MARS_QUEUE_ENTRY.json', entry)
    write(root/'MARS_QUEUE_RECEIPT.json', dict(
        schema='rfic_transformer.broadband56_v2_mars_queue_receipt.v1',
        generated_utc=generated, overall_status='PASS',
        decision='REBIND_EXISTING_QUEUE_AT_COMMITTED_CHECKPOINT',
        queue_id=checkpoint.QUEUE_ID, campaign_id=checkpoint.CAMPAIGN_ID,
        contract_fingerprint_sha256=checkpoint.SCIENTIFIC_CONTRACT_FINGERPRINT,
        queue_entry=queue, simulator_jobs_active=0, simulator_action_taken=False))
    identity = write(root/'SUPERVISOR_IDENTITY.json', dict(
        schema='rfic_transformer.broadband56_v2_authoritative_supervisor.v1',
        generated_utc=generated, campaign_id=checkpoint.CAMPAIGN_ID,
        controller_id=checkpoint.SUPERVISOR_ID, controller_pid=os.getpid(), poll_seconds=60,
        persistent_detached_execution_required=True, one_authoritative_supervisor=True,
        logical_identity_preserved_through_controlled_recovery=True,
        backend_identity_manifest=checkpoint.pin(backend)))
    write(root/'CAMPAIGN_LOCK.json', dict(generated_utc=generated,
        campaign_id=checkpoint.CAMPAIGN_ID, controller_id=checkpoint.SUPERVISOR_ID,
        lock_path=candidate['global_lock_path'], exclusive_flock_required=True))
    with (root/'SHA256SUMS.txt').open('x') as handle:
        for name in executor.IMMUTABLE_ARTIFACTS:
            handle.write(checkpoint.pin(root/name)['sha256']+'  '+name+'\n')
    write(root/'CAMPAIGN_STATUS.json', state)
    return Path(queue['path']), Path(identity['path'])


def validated_lease_fingerprint(candidate, prior, boundary_record, state):
    """Complete a legacy lease only from its verified authority, never by default."""
    files = candidate['bound_files']
    fingerprint = candidate['contract_fingerprint_sha256']
    for key, expected in (('campaign_id', checkpoint.CAMPAIGN_ID),
                          ('queue_id', checkpoint.QUEUE_ID),
                          ('logical_supervisor_id', checkpoint.SUPERVISOR_ID)):
        if prior.get(key) != expected:
            raise ValueError('normal startup prior lease identity mismatch: '+key)
    if ('contract_fingerprint_sha256' in prior
            and prior['contract_fingerprint_sha256'] != fingerprint):
        raise ValueError('prior lease contract fingerprint is invalid or conflicting')
    for key in ('source_authorization', 'source_backend', 'new_backend_manifest'):
        value = checkpoint.read(checkpoint.bound(files[key]))
        if value.get('contract_fingerprint_sha256') != fingerprint:
            raise ValueError('lease fingerprint authority differs: '+key)
    overlay = checkpoint.read(checkpoint.bound(files['current_operational_overlay']))
    if (overlay.get('contract_fingerprint_sha256') != fingerprint
            or overlay.get('campaign_id') != checkpoint.CAMPAIGN_ID
            or overlay.get('queue_id') != checkpoint.QUEUE_ID
            or overlay.get('supervisor_id') != checkpoint.SUPERVISOR_ID
            or overlay.get('corrected_backend_manifest') != prior['backend_identity_manifest']):
        raise ValueError('prior lease and operational overlay binding differs')
    failure_record = files.get('prior_startup_terminal_failure')
    if failure_record is not None:
        handoff_record = checkpoint.validate_failed_control_predecessor(failure_record,
            prior_record=files['prior_supervisor_lease'], boundary_record=boundary_record, state=state)
        if handoff_record != candidate['prior_recovery_handoffs'][-1]:
            raise ValueError('failed successor is not last in the ordered recovery chain')
    elif prior['backend_identity_manifest'] != files['source_backend']:
        raise ValueError('normal startup lease backend differs from committed source')
    return fingerprint


def prepare_controls(executor, *, candidate_record, approval_record, boundary_record,
                     operation_root, successor_root, isolation, lock_fd):
    """Produce the real queue/normal-handoff/lease chain only after exclusive ownership.

    No simulator is launched here. The existing outer launcher must have its
    immutable bundle approved and finish the old attempt before this call.
    """
    candidate = validate_authority(executor, candidate_record, approval_record)
    proof = checkpoint.read(checkpoint.bound(boundary_record))
    state = source_state(proof)
    files, runtime = candidate['bound_files'], candidate['runtime_files']
    if (candidate['current_campaign_root'] != str(Path(proof['terminal_receipt']['path']).parent.parent.parent)
            or files['source_backend'] != proof['source_backend']
            or files['source_authorization'] != proof['source_authorization']):
        raise ValueError('normal startup binds a different source checkpoint')
    prior, current = require_exclusive_owner(executor, candidate, isolation, lock_fd)
    fingerprint = validated_lease_fingerprint(candidate, prior, boundary_record, state)
    operation, root = Path(operation_root), Path(successor_root)
    if (not operation.is_absolute() or any(p.is_symlink() for p in (operation, *operation.parents))
            or operation == root or operation.is_relative_to(root) or root.is_relative_to(operation)
            or operation == Path(candidate['current_campaign_root'])
            or operation.is_relative_to(Path(candidate['current_campaign_root']))):
        raise ValueError('normal startup operation path overlaps campaign data')
    operation.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        backend = checkpoint.bound(files['new_backend_manifest'])
        verification = checkpoint.bound(files['new_backend_verification'])
        queue, identity = prepare_root(executor, candidate=candidate, root=root, backend=backend,
            verification=verification, stage_launcher=checkpoint.bound(runtime['stage_launcher']), state=state)
        rebind, composite = executor.create_rebind_controls(candidate=candidate, operation_root=operation,
            successor_root=root, backend_path=backend, verification_path=verification, queue_entry=queue)
        envelope = write(operation/'CONTROL_ENVELOPE.json', dict(
            overall_status='CONTROL_ENVELOPE_ONLY_NOT_LAUNCH_AUTHORITY', target_root=str(root),
            source_terminal_receipt=proof['terminal_receipt'], target_backend=checkpoint.pin(backend),
            target_authorization=checkpoint.pin(composite),
            control_files={name: checkpoint.pin(root/name) for name in checkpoint.CONTROL_ROOT_FILES}))
        migration = checkpoint.migrate_boundary(proof, target_root=root,
            target_backend=checkpoint.pin(backend), target_authorization=checkpoint.pin(composite),
            golden_template=files['golden_reuse_template'], control_envelope=envelope)
        if checkpoint.verified_resume_state(boundary_record, migration) != state:
            raise ValueError('prepared queue state differs from verified migrated state')
        handoff_value = dict(
            schema=executor.HANDOFF_SCHEMA, generated_utc=executor.utc_now(), overall_status='PASS',
            decision=executor.HANDOFF_DECISION, campaign_id=checkpoint.CAMPAIGN_ID,
            queue_id=checkpoint.QUEUE_ID, supervisor_id=checkpoint.SUPERVISOR_ID,
            contract_fingerprint_sha256=checkpoint.SCIENTIFIC_CONTRACT_FINGERPRINT,
            old_process_pid=prior['physical_process']['pid'], old_process_identity=prior['physical_process'],
            old_process_confirmed_exited=True, new_process_pid=os.getpid(), new_process_identity=current,
            new_process_is_sole_authoritative_supervisor=True, supervisor_count_after=1, overlap_seconds=0,
            new_queue_or_campaign_created=False, nn_training_started=False,
            handoff_scope=checkpoint.CHECKPOINT_HANDOFF_SCOPE, recovery_scope=checkpoint.CHECKPOINT_HANDOFF_SCOPE,
            prior_supervisor_lease=files['prior_supervisor_lease'],
            next_lease_generation=candidate['next_lease_generation'], campaign_lock=prior['campaign_lock'],
            authorization_candidate=candidate_record, authorization_approval=approval_record,
            checkpoint_boundary=boundary_record, checkpoint_migration=migration,
            accepted_preserved=state['current_accepted'], feature_rows_preserved=state['feature_rows'],
            resume_stage=state['current_stage'], active_simulator_jobs=0,
            simulator_action_taken=False, campaign_data_modified=False)
        if 'prior_startup_terminal_failure' in files:
            handoff_value['prior_startup_terminal_failure'] = files['prior_startup_terminal_failure']
        handoff = write(operation/'SUPERVISOR_RECOVERY_HANDOFF_RECEIPT.json', handoff_value)
        checkpoint.validate_checkpoint_handoff(checkpoint.read(checkpoint.bound(handoff)))
        overlay = checkpoint.read(checkpoint.bound(files['current_operational_overlay']))
        overlay.update(generated_utc=executor.utc_now(), corrected_backend_manifest=checkpoint.pin(backend),
            supervisor_recovery_handoffs=[*candidate['prior_recovery_handoffs'], handoff],
            recovery_scope=checkpoint.CHECKPOINT_HANDOFF_SCOPE,
            fixed_generation_policy=copy.deepcopy(FIXED48_GENERATION_POLICY),
            policy_module=runtime['swap_policy_module'], simulator_action_taken=False, campaign_data_modified=False)
        overlay.pop('failure_receipt', None)
        overlay['script_identities'].update(queue_controller=runtime['queue_controller'],
            rebound_helper=files['rebound_helper'], base_rebound_controller=files['base_rebound_controller'],
            resource_gate_auditor=runtime['swap_resource_gate_auditor'],
            base_resource_gate_auditor=files['base_resource_gate_auditor'],
            isolation_identity_auditor=runtime['isolation_identity_auditor'],
            isolation_identity_module=runtime['isolation_identity_module'],
            capacity_policy_module=runtime['capacity_policy_module'], capacity_schema_adapter=runtime['capacity_schema_adapter'])
        overlay_pin = write(operation/'OPERATIONAL_POLICY_OVERLAY_RECOVERY.json', overlay)
        lease = copy.deepcopy(prior)
        lease.update(generated_utc=executor.utc_now(), lease_generation=candidate['next_lease_generation'],
            contract_fingerprint_sha256=fingerprint,
            lease_nonce=uuid.uuid4().hex, validity_state='CURRENT', expires_utc=None,
            physical_process=current, backend_identity_manifest=checkpoint.pin(backend),
            queue_entry=checkpoint.pin(queue), supervisor_identity=checkpoint.pin(identity),
            operational_handoff_receipt=handoff, prior_supervisor_lease=files['prior_supervisor_lease'],
            checkpoint_resume_chain_validated=True, backend_rebind_authorized_by_exact_candidate=True,
            isolation_identity_module=runtime['isolation_identity_module'],
            isolation_identity_auditor=runtime['isolation_identity_auditor'])
        lease.pop('restart_failure_receipt', None)
        lease.pop('restart_chain_validated', None)
        leases = operation/'supervisor_leases'
        leases.mkdir(mode=0o700)
        lease_pin = write(leases/f"SUPERVISOR_LEASE_GENERATION_{candidate['next_lease_generation']:04d}.json", lease)
        persisted = checkpoint.read(checkpoint.bound(lease_pin))
        if (persisted != lease or persisted['physical_process'] != current
                or persisted['lease_generation'] != candidate['next_lease_generation']):
            raise ValueError('serialized successor lease differs from verified controls')
        fixed_generation_policy(dict(campaign_id=candidate['campaign_id'],
            contract_fingerprint_sha256=fingerprint,
            operational_overlay_manifest=overlay_pin, supervisor_lease=lease_pin))
        # Recheck the old PID and all child roles before returning a usable lease.
        require_exclusive_owner(executor, candidate, isolation, lock_fd)
        return dict(candidate=candidate, state=state, boundary=boundary_record, migration=migration,
            root=str(root), operation_root=str(operation), backend=checkpoint.pin(backend),
            composite=checkpoint.pin(composite), rebind=checkpoint.pin(rebind), handoff=handoff,
            overlay=overlay_pin, lease=lease_pin)
    except BaseException as error:
        write(operation/'NORMAL_CHECKPOINT_STARTUP_FAILURE.json', dict(overall_status='FAIL',
            error=repr(error), simulator_action_taken=False, original_production_modified=False))
        raise


def finalize_controls(executor, prepared, *, snapshot, gate):
    """Bind real post-gate progress and the existing controller argv, without invoking it."""
    candidate, state = prepared['candidate'], prepared['state']
    files = candidate['bound_files']
    operation = Path(prepared['operation_root'])
    value = dict(schema='rfic_transformer.broadband56_v2_post_rebind_execution_gate.v1',
        generated_utc=executor.utc_now(), overall_status='PASS', decision=checkpoint.RESUME_DECISION,
        canonical_status='CANONICAL_CURRENT', campaign_id=checkpoint.CAMPAIGN_ID,
        queue_id=checkpoint.QUEUE_ID, supervisor_id=checkpoint.SUPERVISOR_ID,
        contract_fingerprint_sha256=checkpoint.SCIENTIFIC_CONTRACT_FINGERPRINT,
        queue_rebind='PASS', supervisor_rebind='PASS', supervisor_count=1,
        current_stage=state['current_stage'], current_accepted=state['current_accepted'],
        feature_rows=state['feature_rows'], active_simulator_jobs=0,
        resource_and_license_gate='PASS', resource_gate_receipt=checkpoint.pin(gate),
        resource_snapshot=checkpoint.pin(snapshot), queue_rebind_receipt=prepared['rebind'],
        supervisor_handoff_receipt=files['inner_supervisor_handoff'],
        backend_identity_manifest=prepared['backend'],
        corrected_private_configuration=files['corrected_private_configuration'],
        checkpoint_boundary=prepared['boundary'], checkpoint_migration=prepared['migration'],
        simulator_action_taken=False)
    if not checkpoint.post_gate_progress_exact(value, backend_record=prepared['backend'],
            authorization_record=prepared['composite']):
        raise ValueError('post-gate does not bind the actual resumed progress')
    post = write(operation/'POST_REBIND_EXECUTION_GATE.json', value)
    argv = executor.controller_argv(candidate=candidate, operation_root=operation,
        successor_root=Path(prepared['root']), backend_path=checkpoint.bound(prepared['backend']),
        overlay=checkpoint.bound(prepared['overlay']), lease=checkpoint.bound(prepared['lease']),
        rebind_receipt=checkpoint.bound(prepared['rebind']), composite=checkpoint.bound(prepared['composite']),
        post_gate=checkpoint.bound(post))
    return post, argv


def run_controller(executor, prepared, *, wrapper, controller, capacity_hook, capacity_binding, lock_fd):
    """Use one prelaunch and live probe hook, real resource history, and the original controller loop.

    The outer exact-SHA launcher remains responsible for its compatibility
    bootstrap and approval before calling prepare_controls/run_controller.
    """
    candidate, state = prepared['candidate'], prepared['state']
    files, runtime = candidate['bound_files'], candidate['runtime_files']
    root = Path(prepared['root'])
    # The probe emits the overlay schema before wrapper.main installs its policy.
    controller.evaluate_capacity_snapshot = wrapper.swap_policy.evaluate_capacity_snapshot
    controller.adaptive_concurrency = wrapper.swap_policy.adaptive_concurrency
    capacity_hook.install(wrapper, binding=capacity_binding)
    factory = wrapper._swap_override_probe_factory(controller, original_run_probe=controller._run_probe,
        override_receipt_path=checkpoint.bound(files['swap_override_receipt']),
        overlay_manifest_path=checkpoint.bound(prepared['overlay']),
        operational_handoff_path=checkpoint.bound(files['previous_operational_handoff']),
        isolation_hotfix_handoff_path=checkpoint.bound(files['isolation_hotfix_handoff']),
        supervisor_recovery_handoff_paths=[checkpoint.bound(r) for r in
            [*candidate['prior_recovery_handoffs'], prepared['handoff']]],
        isolation_auditor_path=checkpoint.bound(runtime['isolation_identity_auditor']),
        isolation_module_path=checkpoint.bound(runtime['isolation_identity_module']),
        isolation_lease_path=checkpoint.bound(prepared['lease']),
        isolation_lease_generation=candidate['next_lease_generation'],
        backend_manifest_path=checkpoint.bound(prepared['backend']), campaign_root=root,
        campaign_lock=Path(candidate['global_lock_path']), python_bin=checkpoint.bound(files['private_python']))
    os.environ.update({wrapper.OVERRIDE_PATH_ENV: files['swap_override_receipt']['path'],
        wrapper.OVERRIDE_SHA_ENV: files['swap_override_receipt']['sha256'],
        wrapper.OVERLAY_PATH_ENV: prepared['overlay']['path'],
        wrapper.OVERLAY_SHA_ENV: prepared['overlay']['sha256']})
    check_index = state['check_index']
    while True:
        check_index += 1
        snapshot = factory(checkpoint.bound(files['resource_probe']), root/'resource_snapshots', check_index)
        gate = controller._write_resource_gate(inputs=dict(
            python_bin=checkpoint.bound(files['private_python']),
            resource_gate_auditor=checkpoint.bound(runtime['swap_resource_gate_auditor']),
            frozen_contract=checkpoint.bound(files['frozen_contract']),
            preparation_receipt=checkpoint.bound(files['preparation_receipt']),
            policy_approval_receipt=checkpoint.bound(files['policy_approval_receipt'])),
            snapshot_path=snapshot, campaign_root=root, check_index=check_index,
            stage=state['current_stage'], current_accepted=state['current_accepted'])
        measured_bytes = controller._pilot_bytes_per_geometry(root)
        policy = controller.evaluate_capacity_snapshot(checkpoint.read(snapshot), stage=state['current_stage'],
            current_accepted=state['current_accepted'], measured_pilot_bytes_per_geometry=measured_bytes)
        decision = concurrency_for_snapshot(snapshot_path=snapshot, campaign_root=root,
            stage=state['current_stage'], current_accepted=state['current_accepted'],
            policy=policy, legacy_policy=controller.adaptive_concurrency,
            measured_pilot_bytes_per_geometry=measured_bytes,
            pilot_1000_safe_concurrency=controller._pilot_safe_concurrency(root))
        if checkpoint.read(gate).get('overall_status') == 'PASS' and decision['concurrency'] > 0:
            break
        time.sleep(60)
    post, argv = finalize_controls(executor, prepared, snapshot=snapshot, gate=gate)
    # The same probe directory continues in the live loop. Advance the index,
    # not the accepted count, so it cannot reuse prelaunch filenames/history.
    live_state = checkpoint.read(root/'CAMPAIGN_STATUS.json')
    live_state['check_index'] = check_index
    executor.write_json_atomic(root/'CAMPAIGN_STATUS.json', live_state)
    write(Path(prepared['operation_root'])/'NORMAL_CHECKPOINT_STARTUP_BINDING.json', dict(
        overall_status='PASS_CONTROL_BINDING_NOT_SIMULATOR_RESULT', current_accepted=state['current_accepted'],
        feature_rows=state['feature_rows'], current_stage=state['current_stage'],
        lease=prepared['lease'], handoff=prepared['handoff'], checkpoint_migration=prepared['migration'],
        post_gate=post, requested_concurrency=48, executor_capacity=48, admission_decision=decision,
        controller_argv=argv, controller_main_called_at_binding=False, simulator_action_taken_at_binding=False))
    # The existing controller reacquires the same flock before it may dispatch.
    # This is not a handoff boundary: the former owner was already proven dead.
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    return wrapper.main(argv)
