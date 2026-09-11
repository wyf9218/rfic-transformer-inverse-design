"""Read-only physical feature-chain checks for the unchanged controlled64.

The feature inspector alone is not an owner RESULT/export verifier. The closed
result adapter below binds the existing owner publication without inferring a
complete solver-start ledger. This is never a dispatcher or admission interface.
Call load_context once for the exact frozen
frame, then inspect only genuinely new owner-provided candidate evidence. No
old256 identity is substituted into the new64 receipts; no simulator is loaded.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

from . import eucap15_controlled_results as frame
from .eucap15_acquisition_evidence import (
    CONFIG_SHA, DECK_SHA, FREQUENCIES, GEOMETRY_CHECKS, SCALE, TAU,
    MirrorReader, _fields, _path, _require, _same, pin, verify_labels,
)

SCOPE = 'DEVELOPMENT_CONTROLLED_ACQUISITION64'
SELECTION = 'FROZEN_CONTROLLED_ACQUISITION_SINGLE'
REQUIRED_RUNTIME = (
    'rfic_transformer_inverse_design/api.py',
    'rfic_transformer_inverse_design/core/types.py',
    'rfic_transformer_inverse_design/core/defaults.py',
    'rfic_transformer_inverse_design/network_analysis.py',
    'rfic_transformer_inverse_design/paths.py',
    'rfic_transformer_inverse_design/execution/zeus_cadence.py',
    'rfic_transformer_inverse_design/sim/emx/simulation.py',
    'rfic_transformer_inverse_design/sim/emx/layout_export.py',
    'rfic_transformer_inverse_design/sim/touchstone.py',
    'rfic_transformer_inverse_design/sim/base.py',
    'rfic_transformer_inverse_design/analysis/extraction.py',
    'rfic_transformer_inverse_design/campaigns/broadband56_balanced200k.py',
    'rfic_transformer_inverse_design/campaigns/broadband56_gds_identity.py',
    'rfic_transformer_inverse_design/campaigns/broadband56_s4p_qa.py',
)


def load_context(reader, intent_pin, manifest_pin):
    """Reuse the tested frozen64 loader; expose the already-pinned preparation."""
    ctx = frame.load_context(reader, intent_pin, manifest_pin)
    manifest = reader.document(manifest_pin)
    ctx['preparation'] = manifest['files']['PREPARATION_RECEIPT.json']
    return ctx


def _native_sources(reader, request, batch):
    """Bind original input identity to exact native paths, without prefix rewrites."""
    mapping = request.get('path_map', {})
    _require(isinstance(mapping, dict), 'Native source path_map must be an object')
    _require(request['controlled_manifest'] == batch['manifest'],
             'Original controlled manifest changed in request')
    resolved = {}
    for name in ('manifest', 'intent', 'proposals', 'preparation'):
        original = batch[name]
        native_path = str(_path(mapping.get(original['path'], original['path'])))
        native_pin = dict(original, path=native_path)
        # The mirror must explicitly map even unchanged original/native paths.
        reader.read(native_pin)
        resolved[name] = native_pin
    return resolved


def inspect_feature_chain(reader, entry, batch):
    """Check one candidate's real-byte chain, never infer its native start count.

    batch must come from load_context; it is not a caller-authored authorization.
    Publication closure, runtime-release authority and actual solver-birth/order
    belong to the outer owner-result consumer, which is intentionally separate.
    """
    cid = entry['candidate_id']
    _require(cid in batch['rows'], 'Unknown controlled candidate')
    original = batch['rows'][cid]
    from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
        GEOMETRY_FIELDS, canonical_geometry_sha256,
    )
    from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import (
        gds_timestamp_normalized_sha256,
    )
    _require(original['geometry_fields'] == list(GEOMETRY_FIELDS) and
             len(original['geometry']) == 10, 'Frozen geometry fields differ')
    geom = canonical_geometry_sha256(dict(zip(GEOMETRY_FIELDS, original['geometry'])))
    _require(geom == original['canonical_geometry_sha256'], 'Canonical geometry digest differs')
    _require(original['local_dispatch_eligible'] is True and original['analytic_pass'] is True,
             'Held original proposal dispatched')
    candidate_sha = hashlib.sha256(cid.encode()).hexdigest()
    _fields(entry, dict(geometry_sha256=geom, arm=original['arm']), 'Owner candidate identity')

    feature = reader.document(entry['feature'])
    feature_root = Path(entry['feature']['path']).parent
    root = feature_root.parent
    _require(feature_root.name == 'features' and
             Path(entry['feature']['path']).name == 'FEATURE_RECEIPT.json', 'Feature path differs')
    fm = reader.document(reader.mapped_pin(str(feature_root/'MANIFEST.json')))
    _fields(fm, dict(inputs_unchanged=True), 'Feature manifest')
    artifacts = fm['artifacts']
    _require(len(artifacts) == 2 and
             {Path(x['path']).name for x in artifacts} == {'FEATURE_RECEIPT.json', 'features_56.csv'} and
             all(Path(x['path']).parent == feature_root for x in artifacts), 'Feature artifact closure')
    _require(entry['feature'] in artifacts, 'Feature manifest does not bind receipt')
    csv_pin = next(x for x in artifacts if Path(x['path']).name == 'features_56.csv')
    proof_pin, solver_pin = feature['preflight'], feature['solver_receipt']
    _require(Path(proof_pin['path']) == root/'PREFLIGHT.json' and
             Path(solver_pin['path']) == root/'SOLVER_RECEIPT.json' and
             solver_pin == entry['solver'], 'Output tree or solver differs')
    proof, solver = reader.document(proof_pin), reader.document(solver_pin)
    request = reader.document(proof['request'])
    sources = _native_sources(reader, request, batch)

    targeted = original['source'] == 'SPARSE_TARGETED'
    source_context = dict(request_id=original['request_id'], frequency_ghz=15,
        q_proxy=original['q_proxy'], model_id=batch['model_id'],
        model_used_for_proposal=targeted, candidate_model_id=original['model_id'],
        dataset_scope=SCOPE, target_source=original['source'], arm=original['arm'],
        arm_order=original['arm_order'], global_order=original['global_order'])
    _require(original['model_id'] == (batch['model_id'] if targeted else None),
             'Candidate model ownership differs')
    common = dict(candidate_id=cid, frequency_ghz=15, q_requested=original['q_proxy'],
        q_proxy=original['q_proxy'], q_emx=None, model_id=batch['model_id'], dataset_scope=SCOPE,
        production_membership=False, physical_selection=SELECTION,
        controlled_manifest=sources['manifest'], controlled_intent=sources['intent'],
        original_controlled_manifest=batch['manifest'], original_controlled_intent=batch['intent'],
        model_used_for_proposal=targeted, candidate_model_id=original['model_id'])
    _fields(feature, dict(common, schema='eucap15_controlled_acquisition_fresh_features.v1',
        status='PASS_EXTRACTION', original_proposal=original, original_proposal_denominator=64,
        score_scale=SCALE, absolute_hit_tolerances=TAU,
        q_optimum_status='NOT_EVALUATED_NO_Q_REPLACEMENT'), 'Controlled feature')
    record = dict(original, grid_geometry=original['geometry'], grid_proxy=original['proxy'],
        analytic_grid=original['analytic_pass'], candidate_id_sha256=candidate_sha,
        candidate_geometry_identity_sha256=geom)
    _fields(proof, dict(common, schema='eucap15_controlled_acquisition_emx_preflight.v1',
        status='PASS', request_id=original['request_id'], candidate_id_sha256=candidate_sha,
        geometry_sha256=geom, original_record=record, original_proposal=original,
        original_request_denominator=64, protocol=dict(score_scale=SCALE, absolute_tolerances=TAU),
        frequency_grid_hz=FREQUENCIES, port_order=['P001', 'P002', 'P003', 'P004'],
        port_permutation=[0,1,3,2], config_differential_port_pairs=[[0,1],[2,3]],
        reference_ohm=50, output=str(root),
        full11_physical_optimum='NOT_EVALUATED_NO_Q_REPLACEMENT'), 'Controlled preflight')
    _fields(request, dict(source_context, schema='eucap15_controlled_acquisition_emx_request.v1',
        candidate_id=cid, q_requested=original['q_proxy'], controlled_manifest=batch['manifest'],
        production_campaign_membership=False), 'Controlled request')
    _require(request['private_config']['sha256'] == CONFIG_SHA, 'Physical config identity differs')
    source_pins = proof['source_pins']
    _require(len({p['path'] for p in source_pins}) == len(source_pins), 'Duplicate native source path')
    runtime = request['runtime']
    repo = _path(runtime['repo'])
    runtime_pins = runtime['source_pins']
    _require(isinstance(runtime_pins, list) and runtime_pins and
             len({p['path'] for p in runtime_pins}) == len(runtime_pins), 'Runtime source closure differs')
    runtime_paths = {str(_path(p['path'])) for p in runtime_pins}
    _require(all(str(repo/name) in runtime_paths for name in REQUIRED_RUNTIME),
             'Required runtime source omitted')
    required = [proof['request'], *sources.values(), request['gds_audit'], request['calibre_index'],
        request['private_config'], proof['gds'], proof['port_manifest'], proof['calibre'],
        *runtime_pins, runtime['process_file'], runtime['emx_wrapper']]
    _require(all(p in source_pins for p in required), 'Missing native source binding')
    for p in source_pins:
        reader.read(p)

    audit = reader.document(request['gds_audit'])
    _fields(audit, dict(schema='eucap15_controlled_acquisition_gds_audit.v1',
        N_logical=1, N_audit_attempted=1, request=source_context), 'Controlled GDS audit')
    _require(audit['source_pins'] == dict(sources, private_config=request['private_config']),
             'Controlled GDS source chain differs')
    _require(len(audit['records']) == 1, 'Not a single frozen candidate')
    ar = audit['records'][0]
    _fields(ar, dict(candidate_id=cid, status='PASS', candidate_id_sha256=candidate_sha,
        candidate_geometry_identity_sha256=geom, gds=proof['gds'],
        port_manifest=proof['port_manifest']), 'GDS candidate')
    _require(proof['original_candidate_statuses'] == feature['original_candidate_statuses'] ==
             [dict(candidate_id=cid, status='PASS')], 'Candidate statuses differ')
    geometry_audit = reader.document(ar['geometry_audit'])
    drc = reader.document(proof['calibre'])
    for name, value in [('geometry', geometry_audit), ('DRC', drc)]:
        _fields(value, dict(overall_status='PASS', candidate_id_sha256=candidate_sha,
            candidate_geometry_identity_sha256=geom, gds_path=proof['gds']['path'],
            gds_sha256=proof['gds']['sha256']), name)
        _require(value['checks'] and all(x is True for x in value['checks'].values()),
                 name+' checks not all PASS')
        _require(all(value['checks'].get(k) is True for k in GEOMETRY_CHECKS),
                 name+' required geometry check absent')
    _require(all(drc['checks'].get(k) is True for k in ('foundry_drc_pass',
        'no_blocking_drc_violations', 'calibre_result_accounting_complete')), 'Required Calibre check absent')
    _fields(drc, dict(blocking_drc_violation_count=0, drc_scope='foundry_macro_ip_back_end',
        geometry_audit_sha256=ar['geometry_audit']['sha256'], drc_source_rule_deck_sha256=DECK_SHA,
        process_token='/TSMC65_05_12_26/', gds_top_cell='TRANSFORMER',
        gds_timestamp_normalized_sha256=geometry_audit['gds_timestamp_normalized_sha256']), 'Same GDS DRC')
    _require(proof['port_manifest'] in geometry_audit['original_artifacts'], 'Port manifest not audited')
    _require(ar['geometry_audit'] in source_pins and
             all(p in source_pins for p in geometry_audit['original_artifacts']), 'Geometry source closure omitted')
    for p in geometry_audit['original_artifacts']:
        reader.read(p)
    _require(gds_timestamp_normalized_sha256(_path(reader.paths[proof['gds']['path']])) ==
             geometry_audit['gds_timestamp_normalized_sha256'], 'Normalized GDS identity mismatch')
    drc_rows = list(csv.DictReader(io.StringIO(reader.read(request['calibre_index']).decode(), newline='')))
    own_drc = [r for r in drc_rows if r['candidate_id_sha256'] == candidate_sha]
    _require(len(own_drc) == 1 and own_drc[0]['drc_summary_path'] == proof['calibre']['path'] and
             own_drc[0]['drc_summary_sha256'] == proof['calibre']['sha256'], 'Calibre index unique row differs')
    for pk, sk in [('drc_report_path', 'drc_report_sha256'), ('drc_source_rule_deck_path', 'drc_source_rule_deck_sha256')]:
        p = reader.mapped_pin(drc[pk])
        _require(p['sha256'] == drc[sk] and p in source_pins, 'DRC source SHA or closure differs')
        reader.read(p)

    _fields(solver, dict(schema='frequency_research_fresh_solver.v1', status='PASS', candidate_id=cid,
        preflight=proof_pin, source_gds_before=proof['gds'], source_gds_after=proof['gds'],
        real_emx=True, production_modified=False, q_emx=None), 'Solver')
    _require(solver['touchstone'] == entry['s4p'], 'Owner S4P differs')
    solved = solver['artifacts']
    _require(len({p['path'] for p in solved}) == len(solved) and solver['touchstone'] in solved,
             'Solver artifact closure')
    _require(all(Path(p['path']).is_relative_to(root/'solve') for p in solved), 'Solver artifact outside solve')
    for p in solved:
        reader.read(p)
    _require(Path(entry['s4p']['path']).suffix == '.s4p' and entry['s4p']['bytes'] > 0, 'S4P absent')
    commands = [p for p in solved if Path(p['path']).name == 'emx_command.json']
    _require(len(commands) == 1 and reader.document(commands[0]) == proof['command'], 'Executed command differs')
    command = proof['command']
    _require(isinstance(command, list) and all(isinstance(x, str) for x in command), 'Malformed EMX command')
    _require(command[:4] == [runtime['emx_wrapper']['path'], proof['gds']['path'],
        'TRANSFORMER', runtime['process_file']['path']], 'Wrapper/GDS/top-cell/process command binding differs')
    # The native simulation has a port manifest, so its real output finder
    # requires the exact -s path and does not fall back to an arbitrary S4P.
    _require(command.count('-s') == 1 and command.index('-s')+1 < len(command) and
             command[command.index('-s')+1] == entry['s4p']['path'], 'Command Touchstone output differs')
    _require([x for x in command if x.lower().endswith('.gds')] == [proof['gds']['path']], 'Command uses another GDS')
    _require(all(x in command for x in ('--s-impedance=50', '--cadence-pins=51', '--parallel=2',
        '--simultaneous-frequencies=0')) and [x for x in command if x.startswith('--port=')] ==
        [f'--port={p}={p}:{p}_G' for p in ('P001','P002','P003','P004')], 'EMX port/config flags differ')
    _require(command.count('--sweep') == 1 and
        command[command.index('--sweep')+1:command.index('--sweep')+3] == ['5000000000','60000000000'] and
        command.count('--sweep-stepsize') == 1 and command[command.index('--sweep-stepsize')+1] == '1000000000',
        'EMX sweep differs')
    labels = verify_labels(feature, original, reader.read(csv_pin))
    reader.recheck()
    return dict(labels, candidate_id=cid, request_id=original['request_id'], arm=original['arm'],
        arm_order=original['arm_order'], global_order=original['global_order'], source=original['source'],
        geometry_sha256=geom, parameter_geometry_hash=original['parameter_geometry_hash'],
        q_proxy=original['q_proxy'], s4p=entry['s4p'], feature=entry['feature'],
        solver=solver_pin, preflight=proof_pin, evidence_class='FRESH_REAL_EMX',
        source_validation='PINNED_CANDIDATE_CHAIN_AND_ORIGINAL56_RECONCILED',
        terminal_publication_verified=False, solver_start_verified=False, production_admission=False)


def _result_base(batch, original):
    """Exact fields of the published controlled_result.base, not new authority."""
    return dict(schema='eucap15_controlled64_result.v1',
        request_id=original['request_id'], candidate_id=original['candidate_id'],
        original_proposal=original, original_proposal_denominator=64,
        original_request_denominator=64, controlled_manifest=batch['manifest'],
        controlled_intent=batch['intent'], arm=original['arm'], arm_order=original['arm_order'],
        global_order=original['global_order'], source=original['source'], q_proxy=original['q_proxy'], q_emx=None,
        candidate_geometry_identity_sha256=original['canonical_geometry_sha256'],
        model_used_for_proposal=original['source']=='SPARSE_TARGETED', candidate_model_id=original['model_id'],
        production_accepted=False, actual_response=None, actual_native_starts=None,
        valid_for_strict_comparison=None, strict_joint_hit=None, feature=None,
        native_observation=None, stage_evidence=[], automatic_retry_allowed=False)


def _closed_birth(reader, result, proof, solver, batch, original, *,
                  expected_release, expected_owner_config, capture_completed_utc):
    """Verify one saved birth identity; neither slot rank nor observed UTC is start order/time."""
    plan = reader.document(result['plan'])
    _require(plan['release']==expected_release, 'Result release is not the caller-frozen release')
    release = reader.document(expected_release)
    _require(release['config']==expected_owner_config, 'Owner config differs from caller-frozen config')
    config = reader.document(expected_owner_config)
    _require(config['original_manifest']==batch['manifest'], 'Owner config belongs to another frame')
    request = reader.document(proof['request'])
    rt = config['emx_runtime']
    _fields(request['runtime'], {k:rt[k] for k in ('repo','source_pins','emx_wrapper','process_file')},
            'Request runtime is not the bound owner runtime')
    exe = rt['native_executable']
    _require(isinstance(exe,dict) and set(exe)=={'path','sha256','bytes'} and
             str(_path(exe['path']))==exe['path'] and type(exe['bytes']) is int and exe['bytes']>0 and
             isinstance(exe['sha256'],str) and len(exe['sha256'])==64 and
             set(exe['sha256'])<=set('0123456789abcdef'), 'Bound native executable pin required')
    # Its opaque digest is the caller-bound binary identity; no executable or model is loaded.
    keys=('request_id','candidate_id','arm','arm_order','global_order',
          'canonical_geometry_sha256','q_proxy','local_dispatch_eligible')
    _fields(plan, dict(schema='eucap15_controlled64_start_slot_plan.v1',
        manifest_sha256=batch['manifest']['sha256'], intent_sha256=batch['intent']['sha256'],
        per_arm_max=16,total_max=32,incremental_storage_max_bytes=2147483648,
        candidates={cid:{k:p[k] for k in keys} for cid,p in batch['rows'].items()}), 'Frozen start-slot plan')
    admitted=frame._time(plan['admitted_at_utc'],'admission')
    _require((frame._time(plan['deadline_utc'],'deadline')-admitted).total_seconds()==21600,
             'Frozen six-hour plan differs')
    observation_pin=result['native_observation']
    _require(observation_pin in solver['artifacts'], 'Native observation absent from solver closure')
    observation=reader.document(observation_pin)
    _fields(observation, dict(schema='eucap15_controlled64_native_observation.v1',
        status='ONE_NATIVE_BIRTH_OBSERVED',actual_native_starts=1,native_starts_observed=1,
        errors=[]), 'One exact native observation required')
    _require(len(observation['observations'])==1, 'Ambiguous native birth')
    slot=observation['slot']; birth=observation['observations'][0]
    plan_sha=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    _fields(slot,dict(plan_sha256=plan_sha,candidate_id=original['candidate_id'],
        candidate=plan['candidates'][original['candidate_id']],arm=original['arm'],
        launch_binding=dict(preflight=result['_feature_preflight'],gds=proof['gds'],command=proof['command'])),
        'Candidate native slot binding')
    _require(type(slot['arm_slot']) is int and 1<=slot['arm_slot']<=16 and
             type(slot['global_slot']) is int and 1<=slot['global_slot']<=32, 'Slot bounds differ')
    _fields(birth,dict(schema='eucap15_controlled64_native_birth.v1',status='OBSERVED_EXACT_NATIVE_PROCESS',
        native_started=True,candidate=slot['candidate'],arm=slot['arm'],arm_slot=slot['arm_slot'],
        global_slot=slot['global_slot'],plan_sha256=slot['plan_sha256'],launch_binding=slot['launch_binding'],
        command=proof['command'],expected_executable=exe,executable_sha256=exe['sha256'],executable_bytes=exe['bytes']),
        'Native birth source binding')
    process=birth['process']; ancestor=birth['ancestor']; ancestry=birth['ancestry']
    _require(isinstance(ancestry,list) and ancestry, 'Missing native ancestry')
    identity=('pid','start_ticks','uid','ppid','argv')
    for p in [process,ancestor,*ancestry]:
        _require(isinstance(p,dict) and set(identity)|{'state'}<=set(p),
                 'Missing process identity or state')
        _require(all(type(p[k]) is int and p[k]>0 for k in ('pid','start_ticks')) and
                 type(p['uid']) is int and p['uid']>=0 and type(p['ppid']) is int and p['ppid']>=0 and
                 isinstance(p['argv'],list) and p['argv'] and
                 all(isinstance(a,str) for a in p['argv']), 'Malformed process identity')
        _require(p['state'] in ('R','S','D','T','t','I','W','K','P'),
                 'Missing or non-live process state')
    chain={p['pid']:p for p in ancestry}
    # /proc observations are not simultaneous: R/S may change without a new
    # process. Keep both live observations, but compare only stable identity.
    _require(len(chain)==len(ancestry) and process['pid'] in chain and ancestor['pid'] in chain and
             all(_same(p[k],chain[p['pid']][k]) for p in (process,ancestor) for k in identity) and
             observation['wrapper_pid']==ancestor['pid'],
             'Native ancestry identity conflict')
    _require(process['state']!='Z' and process['uid']==ancestor['uid'] and
             isinstance(process['argv'],list) and len(process['argv'])==len(proof['command']) and
             process['argv'][1:]==proof['command'][1:], 'Not the exact native executable invocation')
    current=process['pid']; visited=set()
    while current in chain and current not in visited and current!=ancestor['pid']:
        visited.add(current); current=chain[current]['ppid']
    _require(current==ancestor['pid'], 'Native process is not a wrapper descendant')
    observed=frame._time(birth['observed_utc'],'birth observation')
    ended=frame._time(observation['ended_utc'],'wrapper observation close')
    solver_ended=frame._time(solver['ended_utc'],'solver close')
    _require(admitted<=observed<=ended<=solver_ended<=frame._time(capture_completed_utc,'capture close'),
             'Native observation/publication chronology differs')
    return dict(native_birth_identity_verified=True,native_birth_process=process,
        native_observation='VERIFIED_BIRTH_OBSERVATION_FULL_START_CHRONOLOGY_UNRESOLVED',
        native_birth_observed_utc=birth['observed_utc'],native_count_in_this_result=1,
        native_observation_pin=observation_pin,slot=slot,
        solver_start_verified=False,solver_start_order=None,solver_started_utc=None)


def inspect_closed_result(reader, candidate_id, result_pin, batch, *, owner_root,
                          capture_completed_utc, expected_release, expected_owner_config):
    """Consume the existing RESULT schema; unknown failure class stays unknown."""
    _require(candidate_id in batch['rows'], 'Foreign closed candidate')
    original=batch['rows'][candidate_id]
    candidate_root=_path(owner_root)/original['request_id']
    _require(Path(result_pin['path'])==candidate_root/'RESULT.json', 'Foreign owner RESULT path')
    value=reader.document(result_pin); base=_result_base(batch,original)
    status=value['status']
    if status=='ANALYTIC_FAIL_NOT_DISPATCHED':
        _require(original['analytic_pass'] is False, 'Original analytical failure replaced')
        _require(_same(value,dict(base,status=status,actual_native_starts=0)), 'Original hold RESULT differs')
        return dict(state='ANALYTIC_FAIL',native_count_in_this_result=0,terminal_publication_verified=True)
    _require(original['local_dispatch_eligible'] is True, 'Held proposal executed')
    if status=='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION':
        _require(isinstance(value['error'],str) and value['error'] and isinstance(value['stage_evidence'],list),
                 'Malformed retained failure')
        for p in value['stage_evidence']: reader.read(p)
        _require(_same(value,dict(base,status=status,error=value['error'],stage_evidence=value['stage_evidence'])),
                 'Failure RESULT fabricated values or changed identity')
        return dict(state='CANDIDATE_FAILURE_UNCLASSIFIED',error=value['error'],
            native_count_in_this_result=None,terminal_publication_verified=True,
            failure_classification='NOT_ESTABLISHED_BY_ERROR_TEXT_OR_UNTYPED_STAGE_PINS')
    _require(status=='FRESH_EMX_EXTRACTED', 'Unimplemented owner terminal status: '+str(status))
    feature=reader.document(value['feature'])
    proof=reader.document(feature['preflight']); solver=reader.document(feature['solver_receipt'])
    _require(Path(value['feature']['path'])==candidate_root/'emx_selected'/'features'/'FEATURE_RECEIPT.json',
             'Feature belongs to a different owner candidate directory')
    entry=dict(candidate_id=candidate_id,geometry_sha256=original['canonical_geometry_sha256'],
        arm=original['arm'],feature=value['feature'],solver=feature['solver_receipt'],s4p=solver['touchstone'])
    checked=inspect_feature_chain(reader,entry,batch)
    _require(frame._time(solver['ended_utc'],'solver close')<=frame._time(feature['generated_utc'],'feature close')<=
             frame._time(capture_completed_utc,'capture close'), 'Feature publication chronology differs')
    expected=dict(base,status=status,feature=value['feature'],native_observation=value['native_observation'],
        plan=value['plan'],release=expected_release,actual_native_starts=1,actual_response=checked['actual'],
        valid_for_strict_comparison=checked['strict_valid'],strict_joint_hit=checked['strict_joint_hit'],
        core15_eligible=checked['core_eligible'],q10_to20_supported=checked['q10_to20_supported'],
        absolute_percent_error=feature['target_relative_absolute_percent'],
        stage_evidence=[feature['preflight'],feature['solver_receipt'],proof['calibre'],proof['gds'],solver['touchstone']])
    _require(_same(value,expected), 'RESULT does not match the checked original56 physical chain')
    birth=_closed_birth(reader,dict(value,_feature_preflight=feature['preflight']),proof,solver,batch,original,
        expected_release=expected_release,expected_owner_config=expected_owner_config,
        capture_completed_utc=capture_completed_utc)
    reader.recheck()
    return dict(checked,**birth,state='STRICT_VALID' if checked['strict_valid'] else 'EMX_INVALID',
        terminal_publication_verified=True,closed_utc=solver['ended_utc'])
