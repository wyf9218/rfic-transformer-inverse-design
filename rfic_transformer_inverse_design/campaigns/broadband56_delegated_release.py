"""Truthful delegated release authority, separate from personal SHA approvals."""
from datetime import datetime

from . import broadband56_checkpoint_handoff as cp

SCHEMA = 'rfic_transformer.broadband56_standing_owner_authorization.v1'
DECISION = 'DELEGATED_RELEASE_UNDER_STANDING_OWNER_AUTHORIZATION'
OWNER = 'Yufeng Wang, project owner and project leader'
DELEGATE = 'Codex, delegated release operator'
LIMITS = dict(campaign_id=cp.CAMPAIGN_ID, queue_id=cp.QUEUE_ID,
              logical_supervisor_id=cp.SUPERVISOR_ID,
              contract_fingerprint_sha256=cp.SCIENTIFIC_CONTRACT_FINGERPRINT,
              accepted_geometry_target=200000, frequency_rows_target=11200000,
              requested_concurrency=48, executor_capacity=48,
              normalized_load1_max=1.10, normalized_load5_max=1.10,
              min_available_memory_fraction=0.20, independent_healthy_checks=5,
              nn_training_authorized=False, scientific_contract_changes_authorized=False)


def utc(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('authorization timestamp must be timezone-aware')
    return parsed


def validate_standing(record):
    value = cp.read(cp.bound(record))
    if (value.get('schema') != SCHEMA or value.get('granted_by') != OWNER
            or value.get('decision') != 'AUTHORIZE_CONTINUOUS_SCOPED_REPAIR_AND_PRODUCTION'
            or value.get('limits') != LIMITS
            or value.get('individual_sha_approval_superseded') is not True
            or value.get('controlled_waiting_batch_recovery_authorized') is not True
            or value.get('historical_evidence_must_be_preserved') is not True):
        raise ValueError('standing project-owner authorization scope mismatch')
    utc(value['recorded_utc'])
    cp.bound(value['source_message_record'])
    return value


def validate_release(release, candidate, candidate_record, scope):
    authority = validate_standing(release['standing_owner_authorization'])
    if (release.get('overall_status') != 'PASS' or release.get('decision') != DECISION
            or release.get('authorization_scope') != scope
            or release.get('released_candidate') != candidate_record
            or release.get('released_by') != DELEGATE
            or release.get('owner_personally_approved_this_sha') is not False
            or 'approved_by' in release or 'approved_candidate' in release
            or release.get('scope_limits') != LIMITS
            or candidate.get('standing_owner_authorization') != release['standing_owner_authorization']
            or utc(release['released_utc']) < utc(authority['recorded_utc'])
            or utc(release['released_utc']) < utc(candidate['generated_utc'])):
        raise ValueError('delegated release identity/scope mismatch')
    for key in ('new_runtime_manifest', 'new_backend_manifest'):
        if release.get(key) != candidate['bound_files'][key]:
            raise ValueError('delegated release package identity differs')
        cp.bound(release[key])
    for key in ('software_tests', 'full_control_preflight'):
        result = cp.read(cp.bound(release[key]))
        if (result.get('overall_status') != 'PASS'
                or result.get('runtime') != release['new_runtime_manifest']
                or result.get('backend') != release['new_backend_manifest']
                or result.get('simulator_action_taken') is not False):
            raise ValueError('delegated release requires final-package tests/preflight')
    return authority
