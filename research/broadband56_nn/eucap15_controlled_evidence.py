"""Read-only physical feature-chain checks for the unchanged controlled64.

This is not an owner RESULT/export verifier, solver-start observer, dispatcher
or production admission interface. Call load_context once for the exact frozen
frame, then inspect only genuinely new owner-provided candidate evidence. No
old256 identity is substituted into the new64 receipts; no simulator is loaded.
"""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from . import eucap15_controlled_results as frame
from .eucap15_acquisition_evidence import (
    CONFIG_SHA, DECK_SHA, FREQUENCIES, GEOMETRY_CHECKS, SCALE, TAU,
    MirrorReader, _fields, _path, _require, pin, verify_labels,
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
