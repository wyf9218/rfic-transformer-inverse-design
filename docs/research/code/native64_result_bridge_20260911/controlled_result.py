"""Owner RESULT publication from closed evidence; no launch or physical re-QA."""
from pathlib import Path

from controlled_metadata import parse, read_pin, require
from native_birth import matches_native
from start_slots import digest, write_once


def load(pin):
    return parse(read_pin(pin))


def base(batch, request_id):
    item=batch.candidate(request_id);row=item['original']
    return dict(schema='eucap15_controlled64_result.v1',
        request_id=row['request_id'],candidate_id=row['candidate_id'],
        original_proposal=row,original_proposal_denominator=64,
        original_request_denominator=64,controlled_manifest=batch.manifest_pin,
        controlled_intent=batch.intent_pin,arm=row['arm'],arm_order=row['arm_order'],
        global_order=row['global_order'],source=row['source'],q_proxy=row['q_proxy'],q_emx=None,
        candidate_geometry_identity_sha256=row['canonical_geometry_sha256'],
        model_used_for_proposal=item['model_used_for_proposal'],candidate_model_id=row['model_id'],
        production_accepted=False,actual_response=None,actual_native_starts=None,
        valid_for_strict_comparison=None,strict_joint_hit=None,feature=None,
        native_observation=None,stage_evidence=[],automatic_retry_allowed=False)


def original_hold(batch,request_id):
    value=base(batch,request_id)
    require(value['original_proposal']['analytic_pass'] is False,'NOT_AN_ORIGINAL_ANALYTIC_HOLD')
    value.update(status='ANALYTIC_FAIL_NOT_DISPATCHED',actual_native_starts=0)
    return value


def failed_candidate(batch,request_id,*,error,stage_evidence):
    value=base(batch,request_id)
    require(value['original_proposal']['local_dispatch_eligible'] is True,'ORIGINAL_HOLD_MUST_STAY_HOLD')
    require(isinstance(error,str) and error,'EXPLICIT_CANDIDATE_FAILURE_REQUIRED')
    for pin in stage_evidence:read_pin(pin)
    value.update(status='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION',error=error,
        stage_evidence=stage_evidence)
    # Absence of a birth receipt does not prove that a failed child never ran.
    return value


def fresh_candidate(batch,request_id,*,feature_pin,observation_pin,plan_pin):
    value=base(batch,request_id);row=value['original_proposal']
    require(row['local_dispatch_eligible'] is True,'HELD_PROPOSAL_CANNOT_HAVE_FRESH_RESULT')
    f=load(feature_pin);proof=load(f['preflight']);solver=load(f['solver_receipt'])
    obs=load(observation_pin);plan=load(plan_pin)
    release=load(plan['release']);config=load(release['config'])
    expected_executable=config['emx_runtime'].get('native_executable')
    require(expected_executable is not None and config['original_manifest']==batch.manifest_pin,
        'MISSING_OR_FOREIGN_BOUND_NATIVE_EXECUTABLE')
    require(proof['schema']=='eucap15_controlled_acquisition_emx_preflight.v1' and
        proof['status']=='PASS' and proof['frequency_grid_hz']==[n*10**9 for n in range(5,61)] and
        proof['port_order']==['P001','P002','P003','P004'] and proof['port_permutation']==[0,1,3,2] and
        proof['reference_ohm']==50 and f['frequency_ghz']==15 and
        f['dataset_scope']=='DEVELOPMENT_CONTROLLED_ACQUISITION64',
        'EXACT_PHYSICAL_CONTEXT_REQUIRED')
    require(f['schema']=='eucap15_controlled_acquisition_fresh_features.v1' and
        f['status']=='PASS_EXTRACTION' and f['original_proposal']==row and
        f['candidate_id']==row['candidate_id'] and f['q_proxy']==row['q_proxy'] and
        f['q_requested']==row['q_proxy'] and f['q_emx'] is None and
        f['original_proposal_denominator']==64 and f['production_membership'] is False,
        'FEATURE_ORIGINAL_CANDIDATE_BINDING_MISMATCH')
    require(f['original_controlled_manifest']==batch.manifest_pin and
        f['original_controlled_intent']==batch.intent_pin and
        proof['original_controlled_manifest']==batch.manifest_pin and
        proof['original_controlled_intent']==batch.intent_pin and
        proof['original_proposal']==row and proof['candidate_id']==row['candidate_id'] and
        proof['request_id']==request_id and proof['geometry_sha256']==row['canonical_geometry_sha256'],
        'PREFLIGHT_FROZEN_SOURCE_BINDING_MISMATCH')
    require(f['target']==row['target'] and f['proxy_self']==row['proxy'] and
        f['model_used_for_proposal']==value['model_used_for_proposal'] and
        f['candidate_model_id']==row['model_id'],'TARGET_PROXY_OR_MODEL_CHANGED')
    require(solver['status']=='PASS' and solver['real_emx'] is True and
        solver['candidate_id']==row['candidate_id'] and solver['preflight']==f['preflight'] and
        solver['source_gds_before']==solver['source_gds_after']==proof['gds'],
        'SOLVER_GDS_IDENTITY_MISMATCH')
    for pin in [proof['gds'],proof['calibre'],solver['touchstone'],*solver['artifacts']]:read_pin(pin)
    require(observation_pin in solver['artifacts'],'NATIVE_OBSERVER_NOT_BOUND_TO_SOLVER')
    solve_root=Path(f['preflight']['path']).parent/'solve'
    require(Path(solver['touchstone']['path']).is_relative_to(solve_root) and
        Path(solver['touchstone']['path']).suffix=='.s4p' and
        Path(observation_pin['path']).is_relative_to(solve_root),'FOREIGN_NATIVE_OUTPUT')
    require(obs['schema']=='eucap15_controlled64_native_observation.v1' and
        obs['status']=='ONE_NATIVE_BIRTH_OBSERVED' and type(obs['actual_native_starts']) is int and
        obs['actual_native_starts']==1 and type(obs['native_starts_observed']) is int and
        obs['native_starts_observed']==1 and obs['errors']==[] and len(obs['observations'])==1,
        'EXACT_NATIVE_BIRTH_REQUIRED_NOT_RESERVATION')
    slot=obs['slot'];birth=obs['observations'][0]
    require(plan['manifest_sha256']==batch.manifest_pin['sha256'] and
        plan['intent_sha256']==batch.intent_pin['sha256'] and
        plan['per_arm_max']==16 and plan['total_max']==32 and
        plan['candidates'][row['candidate_id']]=={k:row[k] for k in ('request_id','candidate_id','arm',
            'arm_order','global_order','canonical_geometry_sha256','q_proxy','local_dispatch_eligible')} and
        slot['plan_sha256']==digest(plan) and slot['candidate_id']==row['candidate_id'] and
        slot['candidate']==plan['candidates'][row['candidate_id']] and
        slot['arm']==row['arm'] and type(slot['arm_slot']) is int and 1<=slot['arm_slot']<=16 and
        type(slot['global_slot']) is int and 1<=slot['global_slot']<=32 and
        slot['launch_binding']==dict(preflight=f['preflight'],gds=proof['gds'],command=proof['command']),
        'NATIVE_SLOT_PLAN_OR_GDS_BINDING_MISMATCH')
    require(birth['schema']=='eucap15_controlled64_native_birth.v1' and birth['native_started'] is True and
        birth['status']=='OBSERVED_EXACT_NATIVE_PROCESS' and birth['candidate']==slot['candidate'] and
        birth['arm']==slot['arm'] and birth['arm_slot']==slot['arm_slot'] and
        birth['global_slot']==slot['global_slot'] and birth['plan_sha256']==slot['plan_sha256'] and
        birth['launch_binding']==slot['launch_binding'] and birth['command']==proof['command'] and
        type(birth['process']['pid']) is int and birth['process']['pid']>0 and
        type(birth['process']['start_ticks']) is int and birth['process']['start_ticks']>0 and
        birth['expected_executable']==expected_executable and
        birth['executable_sha256']==birth['expected_executable']['sha256'] and
        birth['executable_bytes']==birth['expected_executable']['bytes'],
        'NATIVE_PROCESS_IDENTITY_MISMATCH')
    chain={p['pid']:p for p in birth['ancestry']}
    require(len(chain)==len(birth['ancestry']) and obs['wrapper_pid']==birth['ancestor']['pid'] and
        matches_native(birth['process'],birth['ancestor'],chain,proof['command'],
            birth['executable_sha256'],birth['expected_executable']['sha256'],birth['ancestor']['uid']),
        'NATIVE_ANCESTRY_OR_COMMAND_MISMATCH')
    value.update(status='FRESH_EMX_EXTRACTED',feature=feature_pin,native_observation=observation_pin,
        plan=plan_pin,release=plan['release'],actual_native_starts=1,actual_response=f['actual_fresh_emx'],
        valid_for_strict_comparison=f['valid_for_strict_comparison'],strict_joint_hit=f['strict_joint_hit'],
        core15_eligible=f['core15_eligible'],q10_to20_supported=f['q10_to20_supported'],
        absolute_percent_error=f['target_relative_absolute_percent'],
        stage_evidence=[f['preflight'],f['solver_receipt'],proof['calibre'],proof['gds'],solver['touchstone']])
    return value


def publish(path,value):
    require(Path(path).name=='RESULT.json','OWNER_RESULT_PATH_REQUIRED')
    write_once(path,value)
    require(parse(Path(path).read_bytes())==value,'RESULT_READBACK_MISMATCH')
