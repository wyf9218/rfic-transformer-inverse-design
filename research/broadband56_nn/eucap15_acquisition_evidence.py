"""Read-only acceptance of frozen acquisition publications, not pilot64.

Reuses the selected-evidence parser and physical-label checks. Never imports a
native executor or re-extracts S4P. DOE/exploration targets remain null. Evidence
pins authenticate recorded bytes/links, not mesh convergence or silicon truth.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
from pathlib import Path

from .eucap15_selected_evidence import (
    _require, _strict_json, _same, _fields, _path, _boolean_text,
    _numeric_text, _original_row_matches_csv, FREQUENCIES, PHYSICAL_FIELDS,
    FLAG_FIELDS, clean, pin,
)

MANIFEST_SHA = '68c43a11f580856af08768845ca24875475d2f66741100bd29a82a38019a454e'
RECIPE_SHA = 'e24e5b84dd4d0e7010613397322ece2d5cdc0241c63fca5265f1894f0482a342'
ROWS_SHA = '720c399860e121b597c07f40b61f2897a6ba4e65aafe8e169d7c19b80020d2b9'
CONFIG_SHA = '431ad59c22df2471746ab5a2eb73a3a7a6484fa511b71c9d6e6c93fb4b3d04d7'
DECK_SHA = '8252a77efecf92d3b187d83f7047df45433ce662c53683996c175b2aa80653ef'
SCALE = [2.5, 2.5, 20.0, 0.8]
TAU = [0.125, 0.125, 1.0, 0.04]
GEOMETRY_CHECKS = ('geometry_range_pass','topology_pass','line_width_sync_pass','angle_45_135_pass',
    'ground_clearance_pass','foundry_layout_audit_pass','manufacturing_grid_canonicalization_pass',
    'foundry_slotted_ground_frame_pass','foundry_power_line_contract_pass',
    'foundry_via_stack_and_landing_pad_pass','foundry_bridge_connection_pass')


class MirrorReader:
    """Exact-path map, hash before/after reads; no fallback or prefix rewriting."""
    def __init__(self, path_map):
        _require(isinstance(path_map, dict), 'Exact physical path map required')
        self.paths = dict(path_map)
        self.evidence = {}

    def read(self, expected):
        _require(isinstance(expected, dict) and {'path', 'sha256', 'bytes'} <= expected.keys(), 'Pin required')
        p = str(_path(expected['path']))
        _require(p in self.paths, 'Missing exact mirror path: ' + p)
        local = _path(self.paths[p])
        _require(type(expected['bytes']) is int and expected['bytes'] >= 0 and
                 isinstance(expected['sha256'], str) and len(expected['sha256']) == 64 and
                 set(expected['sha256']) <= set('0123456789abcdef'), 'Malformed pin')
        original = {k: expected[k] for k in ('path', 'sha256', 'bytes')}
        raw = local.read_bytes()
        actual = dict(path=str(local), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        _require(actual['sha256'] == original['sha256'] and actual['bytes'] == original['bytes'], 'Mirror SHA/size mismatch: ' + p)
        _require(pin(local) == actual, 'Artifact changed during read: ' + p)
        entry = dict(original=original, resolved=actual)
        _require(p not in self.evidence or self.evidence[p] == entry, 'Conflicting source pin: ' + p)
        self.evidence[p] = entry
        return raw

    def document(self, expected):
        return _strict_json(self.read(expected))

    def mapped_pin(self, original):
        p = str(_path(original))
        _require(p in self.paths, 'Missing exact mirror path: ' + p)
        value = pin(_path(self.paths[p]))
        return dict(value, path=p)

    def recheck(self):
        for item in list(self.evidence.values()):
            self.read(item['original'])


def load_frozen_batch(reader, manifest_pin):
    _require(manifest_pin['sha256'] == MANIFEST_SHA, 'Frozen acquisition manifest mismatch')
    manifest = reader.document(manifest_pin)
    parent = Path(manifest_pin['path']).parent
    files = {}
    for name, value in manifest['files'].items():
        _require(Path(name).name == name, 'Non-flat acquisition manifest member')
        files[name] = dict(value, path=str(parent/name))
        reader.read(files[name])
    _require(files['RECIPE_FREEZE.json']['sha256'] == RECIPE_SHA and
             files['SELECTED_CANDIDATES.jsonl']['sha256'] == ROWS_SHA, 'Recipe/proposal identity mismatch')
    recipe = reader.document(files['RECIPE_FREEZE.json'])
    rows = [_strict_json(s) for s in reader.read(files['SELECTED_CANDIDATES.jsonl']).splitlines()]
    _require(len(rows) == 256 and [r['global_order'] for r in rows] == list(range(1,257)), 'Frozen256 order differs')
    _require(len({r['candidate_id'] for r in rows}) == 256, 'Duplicate frozen candidate')
    return dict(manifest=manifest_pin, recipe=files['RECIPE_FREEZE.json'], proposals=files['SELECTED_CANDIDATES.jsonl'],
                recipe_value=recipe, rows={r['candidate_id']: r for r in rows})


def verify_labels(feature, proposal, raw_csv):
    """Reconcile original56 CSV and saved labels, including null DOE objectives."""
    parser = csv.DictReader(io.StringIO(raw_csv.decode('utf-8'), newline=''))
    rows = list(parser)
    _require(parser.fieldnames and len(set(parser.fieldnames)) == len(parser.fieldnames) and len(rows) == 56,
             'Exact56 unique columns required')
    _require([int(r['frequency_hz']) for r in rows] == FREQUENCIES, 'Wrong frequency sweep')
    _fields(feature['original_56_summary'], dict(port_count=4, frequency_points=56,
        frequency_start_hz=FREQUENCIES[0], frequency_stop_hz=FREQUENCIES[-1], frequency_step_hz=10**9), '56summary')
    row = rows[10]
    _original_row_matches_csv(feature['original_frequency_row'], row)
    actual = [_numeric_text(row[k], k) for k in PHYSICAL_FIELDS]
    qp, qs = [_numeric_text(row[k], k) for k in ('qp','qs')]
    q = min(qp, qs) if math.isfinite(qp) and math.isfinite(qs) else math.nan
    _require(_same(clean(actual[2]), clean(q)), 'Qmin differs from qp/qs')
    _require(_same(clean(actual[3]), clean(abs(_numeric_text(row['signed_k'], 'signed_k')))), 'Absolute k mismatch')
    descriptor = _boolean_text(row['broadband_descriptor_valid'], 'descriptor')
    strict = _boolean_text(row['strict_lumped_valid'], 'strict')
    _require(descriptor == all(_boolean_text(row[k], k) for k in FLAG_FIELDS), 'Descriptor predicate mismatch')
    _require(strict == (descriptor and _boolean_text(row['below_half_srf'], 'below_half_srf')), 'SRF/strict predicate mismatch')
    physics = row['passivity_status'] == row['reciprocity_status'] == 'PASS'
    finite = all(math.isfinite(v) for v in actual)
    valid = bool(finite and descriptor and strict and physics)
    wanted, proxy = proposal['target'], proposal['proxy']
    targeted = proposal['source'] == 'SPARSE_TARGETED'
    if targeted:
        _require(isinstance(wanted,list) and isinstance(proxy,list) and len(wanted)==len(proxy)==4 and
                 all(type(v) in (float,int) and math.isfinite(v) and v > 0 for v in wanted) and
                 all(type(v) in (float,int) and math.isfinite(v) for v in proxy), 'Finite targeted values required')
        _require(proposal['q_proxy'] in range(10,21) and wanted[2] == proposal['q_proxy'], 'Frozen Q changed')
    else:
        _require(proposal['source'] in ('EXPLORATION','GEOMETRY_DOE') and
                 wanted is proxy is proposal['q_proxy'] is None, 'DOE/exploration has no target')
    errors = [a-t for a,t in zip(actual,wanted)] if targeted else None
    proxy_errors = [a-p for a,p in zip(actual,proxy)] if targeted else None
    hits = [math.isfinite(e) and abs(e)<=t for e,t in zip(errors,TAU)] if targeted else None
    core = bool(valid and .5<=actual[0]<=2 and .5<=actual[1]<=2 and .2<=actual[3]<=.85)
    score = math.sqrt(sum((e/s)**2 for e,s in zip(errors,SCALE))/4) if finite and targeted else None
    expected = dict(actual_fresh_emx=clean(actual), target=wanted, proxy_self=proxy,
        emx_minus_target=clean(errors), emx_minus_proxy=clean(proxy_errors), normalized_response_score=score,
        within_tolerance=hits, joint_response_hit=all(hits) if targeted else None,
        descriptor_valid=descriptor, strict_lumped_valid=strict, physics_qa_pass=physics,
        valid_for_strict_comparison=valid, strict_joint_hit=bool(all(hits) and valid) if targeted else None,
        target_relative_signed_percent=clean([100*e/t for e,t in zip(errors,wanted)]) if targeted else None,
        target_relative_absolute_percent=clean([100*abs(e)/t for e,t in zip(errors,wanted)]) if targeted else None,
        target_errors_defined=targeted, core15_eligible=core,
        q10_to20_supported=bool(finite and 10<=actual[2]<=20))
    _fields(feature, expected, 'Reconciled15 labels')
    return dict(actual=clean(actual), strict_valid=valid, core_eligible=core, target_errors_defined=targeted,
        emx_minus_target=clean(errors), emx_minus_proxy=clean(proxy_errors), strict_joint_hit=expected['strict_joint_hit'],
        below_half_srf=_boolean_text(row['below_half_srf'],'below_half_srf'),
        descriptor_valid=descriptor, physics_qa_pass=physics, q10_to20_supported=expected['q10_to20_supported'])


def inspect_chain(reader, entry, batch):
    """Verify one owner closed entry against immutable proposal and native chain."""
    cid = entry['candidate_id']
    _require(cid in batch['rows'], 'Unknown candidate')
    original = batch['rows'][cid]
    from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import GEOMETRY_FIELDS, canonical_geometry_sha256
    from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import gds_timestamp_normalized_sha256
    _require(original['geometry_fields']==list(GEOMETRY_FIELDS) and len(original['geometry'])==10,
             'Frozen geometry fields differ')
    _require(canonical_geometry_sha256(dict(zip(GEOMETRY_FIELDS,original['geometry'])))==original['canonical_geometry_sha256'],
             'Canonical geometry digest differs')
    _require(original['local_dispatch_eligible'] is True and original['analytic_pass'] is True, 'Held proposal dispatched')
    geom, candidate_sha = original['canonical_geometry_sha256'], hashlib.sha256(cid.encode()).hexdigest()
    _fields(entry, dict(geometry_sha256=geom, arm=original['arm']), 'Owner identity')
    feature = reader.document(entry['feature'])
    result = reader.document(entry['result'])
    source_context = dict(request_id=original['request_id'], frequency_ghz=15, q_proxy=original['q_proxy'],
        model_id='ACQUISITION_RECIPE_BOUND_NOT_MODEL_INFERRED_HERE', dataset_scope='DEVELOPMENT_ACQUISITION_PAIR',
        target_source=original['source'], arm=original['arm'], arm_order=original['arm_order'], global_order=original['global_order'])
    common = dict(candidate_id=cid, frequency_ghz=15, q_requested=original['q_proxy'], q_proxy=original['q_proxy'], q_emx=None,
        model_id=source_context['model_id'], dataset_scope=source_context['dataset_scope'], production_membership=False,
        physical_selection='FROZEN_ACQUISITION_SINGLE', acquisition_manifest=batch['manifest'], acquisition_recipe=batch['recipe'])
    _fields(feature, dict(common, schema='eucap15_acquisition_fresh_features.v1', status='PASS_EXTRACTION',
        original_proposal=original, original_proposal_denominator=256, score_scale=SCALE, absolute_hit_tolerances=TAU,
        q_optimum_status='NOT_EVALUATED_NO_Q_REPLACEMENT'), 'Acquisition feature')
    feature_root = Path(entry['feature']['path']).parent
    root = feature_root.parent
    _require(feature_root.name=='features' and Path(entry['feature']['path']).name=='FEATURE_RECEIPT.json', 'Feature path')
    fm = reader.document(reader.mapped_pin(str(feature_root/'MANIFEST.json')))
    _fields(fm, dict(inputs_unchanged=True), 'Feature manifest')
    artifacts = fm['artifacts']
    _require(len(artifacts)==2 and {Path(x['path']).name for x in artifacts}=={'FEATURE_RECEIPT.json','features_56.csv'} and
             all(Path(x['path']).parent==feature_root for x in artifacts), 'Feature artifact closure')
    _require(entry['feature'] in artifacts, 'Feature manifest does not bind receipt')
    csv_pin = next(x for x in artifacts if Path(x['path']).name=='features_56.csv')
    proof_pin, solver_pin = feature['preflight'], feature['solver_receipt']
    _require(Path(proof_pin['path'])==root/'PREFLIGHT.json' and Path(solver_pin['path'])==root/'SOLVER_RECEIPT.json' and
             solver_pin==entry['solver'], 'Output tree/solver differs')
    proof, solver = reader.document(proof_pin), reader.document(solver_pin)
    record = dict(original, grid_geometry=original['geometry'], grid_proxy=original['proxy'], analytic_grid=original['analytic_pass'],
        candidate_id_sha256=candidate_sha, candidate_geometry_identity_sha256=geom)
    _fields(proof, dict(common, schema='eucap15_acquisition_emx_preflight.v1',status='PASS',request_id=original['request_id'],
        candidate_id_sha256=candidate_sha, geometry_sha256=geom, original_record=record, original_proposal=original,
        original_request_denominator=256, protocol=dict(score_scale=SCALE,absolute_tolerances=TAU),
        frequency_grid_hz=FREQUENCIES,port_order=['P001','P002','P003','P004'],port_permutation=[0,1,3,2],
        config_differential_port_pairs=[[0,1],[2,3]],reference_ohm=50,output=str(root),
        full11_physical_optimum='NOT_EVALUATED_NO_Q_REPLACEMENT'), 'Acquisition preflight')
    source_pins = proof['source_pins']
    _require(len({p['path'] for p in source_pins})==len(source_pins), 'Duplicate source path')
    for p in source_pins: reader.read(p)
    request = reader.document(proof['request'])
    _fields(request, dict(source_context,schema='eucap15_acquisition_emx_request.v1',candidate_id=cid,
        q_requested=original['q_proxy'],acquisition_manifest=batch['manifest'],production_campaign_membership=False), 'Acquisition request')
    _require(request['private_config']['sha256']==CONFIG_SHA, 'Physical config identity differs')
    required = [proof['request'],*map(batch.get,('manifest','recipe','proposals')),
                request['gds_audit'],request['calibre_index'],request['private_config'],proof['gds'],proof['port_manifest'],proof['calibre']]
    _require(all(p in source_pins for p in required), 'Missing native source binding')
    audit = reader.document(request['gds_audit'])
    _fields(audit,dict(schema='eucap15_acquisition_gds_audit.v1',N_logical=1,N_audit_attempted=1,request=source_context), 'GDS audit')
    _require(audit['source_pins']==dict(manifest=batch['manifest'],recipe=batch['recipe'],proposals=batch['proposals'],
             private_config=request['private_config']), 'GDS acquisition source chain differs')
    _require(len(audit['records'])==1, 'Not a single frozen candidate')
    ar = audit['records'][0]
    _fields(ar,dict(candidate_id=cid,status='PASS',candidate_id_sha256=candidate_sha,
        candidate_geometry_identity_sha256=geom,gds=proof['gds'],port_manifest=proof['port_manifest']), 'GDS candidate')
    _require(proof['original_candidate_statuses']==feature['original_candidate_statuses']==[dict(candidate_id=cid,status='PASS')], 'Candidate statuses differ')
    geometry_audit = reader.document(ar['geometry_audit'])
    drc = reader.document(proof['calibre'])
    for name,value in [('geometry',geometry_audit),('DRC',drc)]:
        _fields(value,dict(overall_status='PASS',candidate_id_sha256=candidate_sha,
            candidate_geometry_identity_sha256=geom,gds_path=proof['gds']['path'],gds_sha256=proof['gds']['sha256']), name)
        _require(value['checks'] and all(x is True for x in value['checks'].values()), name+' checks not all PASS')
        _require(all(value['checks'].get(k) is True for k in GEOMETRY_CHECKS), name+' required geometry check absent')
    _require(all(drc['checks'].get(k) is True for k in ('foundry_drc_pass','no_blocking_drc_violations',
             'calibre_result_accounting_complete')), 'Required Calibre check absent')
    _fields(drc,dict(blocking_drc_violation_count=0,drc_scope='foundry_macro_ip_back_end',
        geometry_audit_sha256=ar['geometry_audit']['sha256'],drc_source_rule_deck_sha256=DECK_SHA,
        process_token='/TSMC65_05_12_26/',gds_top_cell='TRANSFORMER',
        gds_timestamp_normalized_sha256=geometry_audit['gds_timestamp_normalized_sha256']), 'Same GDS DRC')
    _require(proof['port_manifest'] in geometry_audit['original_artifacts'], 'Port manifest not geometry-audited')
    _require(ar['geometry_audit'] in source_pins and all(p in source_pins for p in geometry_audit['original_artifacts']),
             'Geometry source closure omitted from preflight')
    for p in geometry_audit['original_artifacts']: reader.read(p)
    _require(gds_timestamp_normalized_sha256(_path(reader.paths[proof['gds']['path']]))==geometry_audit['gds_timestamp_normalized_sha256'],
             'Normalized GDS identity mismatch')
    drc_rows=list(csv.DictReader(io.StringIO(reader.read(request['calibre_index']).decode('utf-8'),newline='')))
    own_drc=[r for r in drc_rows if r['candidate_id_sha256']==candidate_sha]
    _require(len(own_drc)==1 and own_drc[0]['drc_summary_path']==proof['calibre']['path'] and
             own_drc[0]['drc_summary_sha256']==proof['calibre']['sha256'], 'Calibre index unique row differs')
    for pk,sk in [('drc_report_path','drc_report_sha256'),('drc_source_rule_deck_path','drc_source_rule_deck_sha256')]:
        p=reader.mapped_pin(drc[pk]); _require(p['sha256']==drc[sk] and p in source_pins, 'DRC source SHA/closure mismatch'); reader.read(p)
    _fields(solver,dict(schema='frequency_research_fresh_solver.v1',status='PASS',candidate_id=cid,preflight=proof_pin,
        source_gds_before=proof['gds'],source_gds_after=proof['gds'],real_emx=True,production_modified=False,q_emx=None), 'Solver')
    _require(solver['touchstone']==entry['s4p'], 'Owner S4P differs')
    solved = solver['artifacts']
    _require(len({p['path'] for p in solved})==len(solved) and solver['touchstone'] in solved, 'Solver artifact closure')
    _require(all(Path(p['path']).is_relative_to(root/'solve') for p in solved), 'Solver artifact outside original solve')
    for p in solved: reader.read(p)
    _require(Path(entry['s4p']['path']).suffix=='.s4p' and entry['s4p']['bytes']>0, 'S4P absent')
    commands=[p for p in solved if Path(p['path']).name=='emx_command.json']
    _require(len(commands)==1 and reader.document(commands[0])==proof['command'], 'Executed command differs')
    command=proof['command']
    _require(isinstance(command,list) and all(isinstance(x,str) for x in command), 'Malformed EMX command')
    _require([x for x in command if x.lower().endswith('.gds')]==[proof['gds']['path']], 'Command uses another GDS')
    _require(all(x in command for x in ('--s-impedance=50','--cadence-pins=51','--parallel=2','--simultaneous-frequencies=0')) and
             [x for x in command if x.startswith('--port=')]==[f'--port={p}={p}:{p}_G' for p in ('P001','P002','P003','P004')],
             'EMX port/config flags differ')
    _require(command.count('--sweep')==1 and command[command.index('--sweep')+1:command.index('--sweep')+3]==['5000000000','60000000000'] and
             command.count('--sweep-stepsize')==1 and command[command.index('--sweep-stepsize')+1]=='1000000000', 'EMX sweep differs')
    labels=verify_labels(feature, original, reader.read(csv_pin))
    _fields(entry,dict(strict_valid=labels['strict_valid'],core_eligible=labels['core_eligible']), 'Owner validity')
    _fields(result,dict(status='FRESH_EMX_EXTRACTED',request_id=original['request_id'],candidate_id=cid,
        q_proxy=original['q_proxy'],feature=entry['feature'],valid_for_strict_comparison=labels['strict_valid'],
        strict_joint_hit=labels['strict_joint_hit'],absolute_percent_error=feature['target_relative_absolute_percent'],
        production_accepted=False), 'Owner terminal RESULT')
    reader.recheck()
    return dict(labels,candidate_id=cid,request_id=original['request_id'],arm=original['arm'],
        arm_order=original['arm_order'],global_order=original['global_order'],source=original['source'],
        geometry_sha256=geom,parameter_geometry_hash=original['parameter_geometry_hash'],
        q_proxy=original['q_proxy'],s4p=entry['s4p'],feature=entry['feature'],
        evidence_class='FRESH_REAL_EMX',source_validation='PINNED_CHAIN_AND_ORIGINAL56_RECONCILED',
        solver_attempt_count='NOT_INFERRED_FROM_FEATURE_COMPLETION')
