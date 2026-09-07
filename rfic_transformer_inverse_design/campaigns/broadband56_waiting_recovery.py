"""Validate and reuse an interrupted zero-dispatch stage's physical prefix.

Original receipts, GDS and DRC outputs are never modified. Rebound role receipts
are explicitly derived reuse records, not claims of new Cadence/Calibre runs.
Process fencing is a separate deployment action, required before activation.
"""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
from pathlib import Path

from . import broadband56_checkpoint_handoff as cp
from .broadband56_delegated_release import validate_standing
from .broadband56_stage_execution import expected_stage_role_order

PREFIX_SCHEMA = 'rfic_transformer.broadband56_waiting_stage_prefix.v1'
INTERRUPTION_SCHEMA = 'rfic_transformer.broadband56_controlled_waiting_interruption.v1'


def write(path, value):
    from .broadband56_checkpoint_startup import write as exclusive_write
    return exclusive_write(path, value)


def rows(record):
    with cp.bound(record).open(newline='') as handle:
        return list(csv.DictReader(handle))


def pins_in(value):
    if isinstance(value, dict):
        if set(value) == {'path', 'size_bytes', 'sha256'}:
            yield value
        else:
            for child in value.values():
                yield from pins_in(child)
    elif isinstance(value, list):
        for child in value:
            yield from pins_in(child)


def capture_prefix(stage_dir, *, lease_record, boundary_record, standing_record):
    """Read actual pinned role receipts and their physical inputs, without execution."""
    validate_standing(standing_record)
    stage_dir = Path(stage_dir)
    context_record = cp.pin(stage_dir/'backend/STAGE_CONTEXT.json')
    context = cp.read(cp.bound(context_record))
    lease = cp.read(cp.bound(lease_record))
    handoff = cp.read(cp.bound(lease['operational_handoff_receipt']))
    state = cp.verified_resume_state(boundary_record, handoff['checkpoint_migration'])
    root = stage_dir.parent.parent
    live_state = cp.read(root/'CAMPAIGN_STATUS.json')
    backend_record = context['backend_identity_manifest']
    backend = cp.read(cp.bound(backend_record))
    profile = cp.read(cp.bound(context['stage_execution_profile']))
    stage = context['stage']
    expected = list(expected_stage_role_order(stage))
    count = expected.index('exact_audited_gds_emx_runner')
    if (count != 7 or context['campaign_root'] != str(root)
            or lease['backend_identity_manifest'] != backend_record
            or handoff['checkpoint_boundary'] != boundary_record
            or context['current_accepted'] != state['current_accepted']
            or context['stage'] != state['current_stage']
            or live_state['current_accepted'] != state['current_accepted']
            or live_state['feature_rows'] != state['feature_rows']
            or context['contract_fingerprint_sha256'] != cp.SCIENTIFIC_CONTRACT_FINGERPRINT
            or context['campaign_id'] != cp.CAMPAIGN_ID
            or context['private_configuration'] != backend['runtime_identities']['private_configuration']):
        raise ValueError('waiting prefix checkpoint/lease/context mismatch')
    later = [p for p in (root/'stages').iterdir() if p.is_dir()
             and p.name[:6].isdigit() and p.name[:6] > stage_dir.name[:6]]
    if later or any((stage_dir/p).exists() for p in
                    ('STAGE_RECEIPT.json', 'STAGE_PROGRESS_RECEIPT.json')):
        raise ValueError('waiting stage is already committed or superseded')
    physical = []
    records = []
    for i, role in enumerate(expected[:count], 1):
        command = profile['stages'][stage]['commands'][i-1]
        if command['role'] != role:
            raise ValueError('waiting prefix profile order mismatch')
        path = stage_dir/'backend/roles'/f'{i:02d}_{role}'/command['receipt']
        record = cp.pin(path)
        value = cp.read(path)
        if value.get('overall_status') != 'PASS':
            raise ValueError('waiting prefix role not PASS: '+role)
        for key in ('campaign_id', 'contract_fingerprint_sha256', 'stage',
                    'backend_identity_manifest', 'full_campaign_authorization_receipt'):
            if key in value and value[key] != context[key]:
                raise ValueError('waiting prefix role identity mismatch: '+role+'/'+key)
        for ref in pins_in(value):
            cp.bound(ref)
            physical.append(ref)
            if str(ref['path']).endswith('.csv'):
                for row in rows(ref):
                    for key, path_value in row.items():
                        sha_key = key[:-5]+'_sha256' if key.endswith('_path') else ''
                        if path_value and row.get(sha_key):
                            actual = cp.pin(path_value)
                            if actual['sha256'] != row[sha_key]:
                                raise ValueError('waiting CSV artifact drift: '+key)
                            physical.append(actual)
        records.append(dict(role=role, original_receipt=record,
                            original_script=backend['script_identities'][role],
                            relative_receipt=str(path.relative_to(stage_dir/'backend'))))
    cadence = cp.read(cp.bound(records[2]['original_receipt']))
    zero = cp.read(cp.bound(records[-1]['original_receipt']))
    all_rows = rows(cadence['input_candidate_queue'])
    good = rows(zero['pass_index'])
    failures = rows(cadence['failure_index'])
    ids = lambda data: [r['candidate_id_sha256'] for r in data]
    all_ids, good_ids, failed_ids = ids(all_rows), ids(good), ids(failures)
    if (len(set(all_ids)) != len(all_ids) or len(set(good_ids)) != len(good_ids)
            or set(good_ids) & set(failed_ids)
            or set(good_ids) | set(failed_ids) != set(all_ids)
            or len(good) != zero['receipt_pass_count']
            or len(failures) != cadence['cadence_fail_count']):
        raise ValueError('waiting candidate partition differs')
    for row in good:
        receipt = cp.read(Path(row['calibre_receipt_path']))
        if (receipt.get('overall_status') != 'PASS'
                or receipt.get('calibre_blocking_violations') != 0
                or receipt.get('candidate_id_sha256') != row['candidate_id_sha256']
                or receipt.get('geometry_identity_sha256') != row['geometry_sha256']):
            raise ValueError('waiting candidate Calibre evidence differs')
    emx_dir = stage_dir/'backend/roles'/f'{count+1:02d}_{expected[count]}'
    dispatch = cp.pin(emx_dir/'dispatch/DISPATCH_EVENTS.jsonl')
    if dispatch['size_bytes'] or any((emx_dir/'candidates').iterdir()):
        raise ValueError('prefix recovery is only for a proven zero-dispatch batch')
    for ref in physical:
        cp.bound(ref)
    return dict(schema=PREFIX_SCHEMA, overall_status='PASS_PHYSICAL_PREFIX_NOT_ACCEPTED',
        generated_utc=datetime.now(timezone.utc).isoformat(),
        campaign_id=cp.CAMPAIGN_ID, queue_id=cp.QUEUE_ID,
        logical_supervisor_id=cp.SUPERVISOR_ID,
        contract_fingerprint_sha256=cp.SCIENTIFIC_CONTRACT_FINGERPRINT,
        standing_owner_authorization=standing_record, prior_supervisor_lease=lease_record,
        checkpoint_boundary=boundary_record, source_stage_dir=str(stage_dir),
        source_context=context_record, source_backend=backend_record,
        source_authorization=context['full_campaign_authorization_receipt'],
        source_profile=context['stage_execution_profile'],
        source_private_configuration=context['private_configuration'],
        current_accepted=state['current_accepted'], feature_rows=state['feature_rows'],
        stage=stage, roles=records, candidate_ids=all_ids,
        emx_candidate_ids=good_ids, failed_candidate_ids=failed_ids,
        dispatch=dispatch, physical_sources=list({r['path']:r for r in physical}.values()),
        simulator_action_taken=False, accepted_increment=0, source_modified=False)


def validate_prefix(record):
    value = cp.read(cp.bound(record))
    if value.get('schema') != PREFIX_SCHEMA or value.get('overall_status') != 'PASS_PHYSICAL_PREFIX_NOT_ACCEPTED':
        raise ValueError('validated waiting physical prefix required')
    # Recompute from unchanged original receipts, not assertions in a new manifest.
    actual = capture_prefix(value['source_stage_dir'],
        lease_record=value['prior_supervisor_lease'], boundary_record=value['checkpoint_boundary'],
        standing_record=value['standing_owner_authorization'])
    actual['generated_utc'] = value['generated_utc']
    if actual != value:
        raise ValueError('waiting physical prefix changed')
    return value


def validate_interrupted_predecessor(record, *, prior_record, boundary_record, state):
    value = cp.read(cp.bound(record))
    prefix = validate_prefix(value['physical_prefix'])
    prior = cp.read(cp.bound(prior_record))
    if (value.get('schema') != INTERRUPTION_SCHEMA
            or value.get('overall_status') != 'PASS_CONTROLLED_INTERRUPTION_NOT_STAGE_COMPLETION'
            or value.get('prior_supervisor_lease') != prior_record
            or prefix['prior_supervisor_lease'] != prior_record
            or prefix['checkpoint_boundary'] != boundary_record
            or value.get('checkpoint_boundary') != boundary_record
            or value.get('standing_owner_authorization') != prefix['standing_owner_authorization']
            or value.get('dispatch_fenced_before_termination') is not True
            or value.get('healthy_native_solvers_terminated') != 0
            or value.get('surviving_project_processes') != []
            or value.get('old_process_confirmed_dead') is not True
            or value.get('exclusive_lock_reacquired') is not True
            or value.get('current_accepted') != state['current_accepted']
            or value.get('feature_rows') != state['feature_rows']
            or prefix['current_accepted'] != state['current_accepted']
            or prefix['feature_rows'] != state['feature_rows']):
        raise ValueError('controlled waiting interruption identity/proof mismatch')
    expected = prior['physical_process']
    frozen = cp.read(cp.bound(value['frozen_tree_evidence']))
    dead = cp.read(cp.bound(value['process_death_evidence']))
    if (frozen.get('owner') != expected or dead.get('owner') != expected
            or not frozen.get('frozen_control_processes')
            or any(p.get('state') not in ('T', 't') for p in frozen['frozen_control_processes'])
            or frozen.get('native_solver_pids') != [] or dead.get('survivors') != []
            or frozen.get('dispatch') != prefix['dispatch'] or dead.get('dispatch') != prefix['dispatch']):
        raise ValueError('controlled interruption lacks frozen tree/death evidence')
    return prior['operational_handoff_receipt']


def validate_relocated_profile(original_record, replacement_record):
    """Only relocate the same hash-bound queue delegate; preserve every argument."""
    original = cp.read(cp.bound(original_record))
    replacement = cp.read(cp.bound(replacement_record))
    expected = copy.deepcopy(original)
    for stage, value in expected['stages'].items():
        for index, command in enumerate(value['commands']):
            if command['role'] != 'phase_a_queue_builder':
                continue
            argv = command['argv']
            path_index = argv.index('--delegate-script')+1
            digest = argv[argv.index('--delegate-sha256')+1]
            other = replacement['stages'][stage]['commands'][index]['argv'][path_index]
            before, after = cp.pin(argv[path_index]), cp.pin(other)
            if (before['sha256'] != digest or after['sha256'] != digest
                    or before['size_bytes'] != after['size_bytes']
                    or Path(other).name != Path(argv[path_index]).name):
                raise ValueError('relocated profile queue delegate bytes differ')
            argv[path_index] = other
    if expected != replacement:
        raise ValueError('relocated profile changed beyond identical delegate paths')


def prepare_reused_prefix(*, backend, context, out_dir):
    """Rebind only the first unfinished recovery attempt; never replay this cohort twice."""
    binding = backend.get('waiting_stage_recovery')
    if binding is None:
        return {}
    root = Path(context['campaign_root'])
    previous = list(root.joinpath('stages').glob('*/backend/WAITING_PREFIX_REUSE_RECEIPT.json'))
    if previous:
        if len(previous) != 1 or not any((previous[0].parent.parent/name).is_file()
                                        for name in ('STAGE_RECEIPT.json', 'STAGE_PROGRESS_RECEIPT.json')):
            raise ValueError('earlier recovery attempt is not committed; do not drop its cohort')
        return {}
    prefix = validate_prefix(binding['physical_prefix'])
    if context['current_accepted'] != prefix['current_accepted']:
        return {}  # Later formally committed production uses the unchanged sampler.
    if context['stage'] != prefix['stage']:
        raise ValueError('recovery prefix stage differs from current stage')
    old = cp.read(cp.bound(prefix['source_backend']))
    if (old['scientific_contract'] != backend['scientific_contract']
            or context['private_configuration'] != prefix['source_private_configuration']):
        raise ValueError('recovery prefix scientific/config/profile changed')
    validate_relocated_profile(prefix['source_profile'], context['stage_execution_profile'])
    initial = cp.read(cp.bound(context['initial_resource_snapshot']))
    lease = cp.read(cp.bound(initial['supervisor_lease']))
    handoff = cp.read(cp.bound(lease['operational_handoff_receipt']))
    interruption_record = handoff['prior_waiting_batch_interruption']
    interruption = cp.read(cp.bound(interruption_record))
    if interruption['physical_prefix'] != binding['physical_prefix']:
        raise ValueError('recovery backend interruption/prefix mismatch')
    # The startup already validates death and the authoritative lease transfer.
    validate_interrupted_predecessor(interruption_record,
        prior_record=prefix['prior_supervisor_lease'], boundary_record=prefix['checkpoint_boundary'],
        state=dict(current_accepted=prefix['current_accepted'], feature_rows=prefix['feature_rows']))
    out = Path(out_dir)
    results, translations = {}, {}
    for item in prefix['roles']:
        role, original = item['role'], item['original_receipt']
        if any(backend['script_identities'][role][key] != item['original_script'][key]
               for key in ('sha256', 'size_bytes')):
            raise ValueError('reused scientific role implementation changed: '+role)
        value = copy.deepcopy(cp.read(cp.bound(original)))
        def replace_refs(obj):
            if isinstance(obj, dict):
                if set(obj) == {'path', 'sha256', 'size_bytes'}:
                    return translations.get(obj['path'], obj)
                return {k:replace_refs(v) for k,v in obj.items()}
            if isinstance(obj, list):
                return [replace_refs(v) for v in obj]
            return obj
        value = replace_refs(value)
        for key in ('backend_identity_manifest', 'full_campaign_authorization_receipt'):
            if key in value:
                value[key] = context[key]
        value['artifact_reuse'] = dict(original_receipt=original,
            physical_prefix=binding['physical_prefix'], interruption=interruption_record,
            original_simulator_action_taken=value.get('simulator_action_taken', False),
            fresh_execution_on_reuse=False, original_bytes_unchanged=True)
        value['simulator_action_taken'] = False
        target = out/item['relative_receipt']
        target.parent.mkdir(parents=True, exist_ok=True)
        record = write(target, value)
        translations[original['path']] = record
        results[role] = dict(receipt=record, value=value, original_receipt=original)
    write(out/'WAITING_PREFIX_REUSE_RECEIPT.json', dict(overall_status='PASS_REUSE_NOT_NEW_SIMULATION',
        physical_prefix=binding['physical_prefix'], interruption=interruption_record,
        accepted_increment=0, simulator_action_taken=False,
        backend_identity_manifest=context['backend_identity_manifest'],
        reused_roles=[dict(role=k, receipt=v['receipt'], original=v['original_receipt']) for k,v in results.items()]))
    return results
