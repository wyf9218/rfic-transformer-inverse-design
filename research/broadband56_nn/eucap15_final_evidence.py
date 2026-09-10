"""Read a completed FINAL candidate's native feature closure, never dispatch.

This is the FINAL-specific binding of the existing same-GDS/DRC/solver/56-row
validation rules. It cannot consume pilot64/128 receipts by renaming their
scope. MAIN and AUDIT share one candidate identity; q_requested is q_target,
not necessarily q_proxy. Native code never supplies q_emx to this reader.

Only completed extraction is accepted here (STRICT_VALID or EMX_INVALID).
Pending/analytic slots remain in the original frame's accounting. Separate
failed native stages require their own evidence consumer, never a guessed
classification from an absent manifest or a generic EMX-pipeline error.
The new schema is a code-level interface, not an installed native producer.
"""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from .eucap15_final_binding import candidate_binding
from .eucap15_final_statistics import record_sha256, _publication
from .eucap15_feature_values import validate_feature_values
from .eucap15_selected_evidence import FREQUENCIES, _fields, _path, _require, SelectedEvidenceError
from .frequency_research_emx import GEOMETRY_CHECKS, FOUNDRY_DECK_SHA256
from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import (
    GdsIdentityError, gds_timestamp_normalized_sha256,
)


def _exact(actual, wanted, label):
    _require(record_sha256(actual) == record_sha256(wanted), label + ' differs')


def inspect_features(ctx, candidate_id, manifest_pin, mirror, *, expected_private_config):
    """Return one publication plus the closed, exact source/resolved pin list.

    ctx must be the result of load_context, consumed once for the entire frozen
    frame. expected_private_config is the exact physics-configuration pin from
    the caller's frozen owner release, not a value inferred from this result.
    mirror is the existing development128_results.Mirror with explicit
    byte-identical mappings; there is no SSH, implicit path relocation, S4P
    re-extraction, model loading, target generation, Q reranking or file write.
    A successful return establishes receipt-chain consistency, not independent
    solver certification or FINAL model-selection QA. Synthetic fixtures are
    only software tests and must never be published as experimental evidence.
    """
    try:
        return _inspect(ctx, candidate_id, manifest_pin, mirror, expected_private_config)
    except SelectedEvidenceError:
        raise
    except (KeyError, ValueError, OSError, TypeError, IndexError, OverflowError,
            csv.Error, GdsIdentityError) as error:
        raise SelectedEvidenceError('Invalid FINAL completed evidence: ' + str(error)) from error


def _inspect(ctx, candidate_id, manifest_pin, mirror, expected_private_config):
    binding = candidate_binding(ctx, candidate_id)
    _require(binding['analytic_grid'] is True and
             binding['candidate_geometry_identity_sha256'] is not None,
             'Frozen analytic failure cannot acquire a completed physical result')
    consumed = {}

    def checked(value):
        resolved = mirror.check(value)
        original = mirror.known(value['path'])
        _require(original['path'] not in consumed or consumed[original['path']]['original'] == original,
                 'Conflicting source pin in one candidate')
        consumed[original['path']] = dict(original=original, resolved=resolved)
        return original

    def document(value):
        checked(value)
        result = mirror.document(value)
        checked(value)
        return result

    def raw(value):
        resolved = mirror.check(checked(value))
        data = Path(resolved['path']).read_bytes()
        _require(len(data) == value['bytes'] and hashlib.sha256(data).hexdigest() == value['sha256'],
                 'Artifact changed while reading')
        checked(value)
        return data

    manifest_pin = checked(manifest_pin)
    feature_dir = _path(manifest_pin['path']).parent
    solver_root, root = feature_dir.parent, feature_dir.parent.parent
    _require(Path(manifest_pin['path']).name == 'MANIFEST.json' and feature_dir.name == 'features'
             and solver_root.name == 'emx_selected' and root.name == candidate_id
             and root.parent.name == binding['request_id'],
             'FINAL native candidate-Q independent directory required')
    manifest = document(manifest_pin)
    _fields(manifest, dict(schema='eucap15_final_feature_manifest.v1', inputs_unchanged=True), 'manifest')
    binding_pin = checked(manifest['final_binding'])
    _require(binding_pin['path'] == str(root/'FINAL_BINDING.json'), 'Binding belongs to another candidate root')
    _exact(document(binding_pin), binding, 'Frozen FINAL candidate binding')
    # Recheck only this candidate's consumed frozen metadata; no repeated
    # dataset/checkpoint reads and no global source-index scan per candidate.
    for source in binding['source_pins'].values():
        checked(source)
    artifacts = manifest['artifacts']
    _require(isinstance(artifacts, list) and len(artifacts) == 2 and
             {Path(p['path']).name for p in artifacts} == {'FEATURE_RECEIPT.json', 'features_56.csv'},
             'Closed original feature receipt and exact56 CSV required')
    files = {}
    for value in artifacts:
        value = checked(value)
        _require(Path(value['path']).parent == feature_dir, 'Feature artifact escaped candidate tree')
        files[Path(value['path']).name] = value
    feature = document(files['FEATURE_RECEIPT.json'])
    common = dict(final_binding=binding_pin, candidate_id=candidate_id,
        model_id=binding['model_id'], dataset_scope='FINAL_FROZEN_DATASET', frequency_ghz=15,
        q_requested=binding['q_target'], q_proxy=binding['q_proxy'], q_emx=None,
        physical_selection='FROZEN_MAIN_AUDIT_UNION', memberships=binding['memberships'],
        original_request_denominator=binding['original_request_denominator'])
    wanted, proxy = binding['target'], binding['grid_proxy']
    scale, tau = binding['score_scale'], binding['absolute_tolerances']
    _fields(feature, {**common, 'schema': 'eucap15_final_fresh_features.v1', 'status': 'PASS_EXTRACTION',
        'target': wanted, 'proxy_self': proxy, 'score_scale': scale, 'absolute_hit_tolerances': tau,
        'q_optimum_status': 'NOT_EVALUATED_BY_NATIVE_OWNER', 'production_membership': False}, 'feature')
    proof_pin, solver_pin = checked(feature['preflight']), checked(feature['solver_receipt'])
    _require(proof_pin['path'] == str(solver_root/'PREFLIGHT.json') and
             solver_pin['path'] == str(solver_root/'SOLVER_RECEIPT.json'), 'Cross-candidate solver/proof')
    proof = document(proof_pin)
    candidate_sha = hashlib.sha256(candidate_id.encode()).hexdigest()
    geometry_sha = binding['candidate_geometry_identity_sha256']
    protocol = dict(q_values=list(range(10, 21)), q_scalar='min(Qp,Qs)',
                    score_scale=scale, absolute_tolerances=tau)
    _fields(proof, {**common, 'schema': 'eucap15_final_emx_preflight.v1', 'status': 'PASS',
        'request_id': binding['request_id'], 'candidate_id_sha256': candidate_sha,
        'geometry_sha256': geometry_sha, 'protocol': protocol, 'executed_hit_tolerances': tau,
        'frequency_grid_hz': FREQUENCIES, 'port_order': ['P001', 'P002', 'P003', 'P004'],
        'port_permutation': [0, 1, 3, 2], 'reference_ohm': 50,
        'full11_physical_optimum': 'NOT_EVALUATED_BY_NATIVE_OWNER',
        'production_membership': False, 'output': str(solver_root)}, 'preflight')
    _exact(proof['original_record'], binding['original_record'], 'Proof original record')
    sources = [checked(p) for p in proof['source_pins']]
    _require(len({p['path'] for p in sources}) == len(sources), 'Duplicate proof source')
    request_pin = checked(proof['request'])
    _require(request_pin['path'] == str(root/'EMX_REQUEST.json'), 'Cross-candidate EMX request')
    request = document(request_pin)
    _exact(request['private_config'], checked(expected_private_config), 'Frozen native physics configuration')
    _fields(request, {**{k: v for k, v in common.items() if k != 'q_emx'},
        'schema': 'eucap15_final_emx_request.v1', 'request_id': binding['request_id'],
        'target_source': binding['original_record']['target_source'],
        'production_campaign_membership': False}, 'native request')
    for name in ('gds', 'port_manifest', 'calibre'):
        checked(proof[name])
        _require(Path(proof[name]['path']).is_relative_to(root), 'Physical source escaped candidate root')
    required = [binding_pin, request_pin, *binding['source_pins'].values(),
        request['gds_audit'], request['calibre_index'], request['private_config'],
        proof['gds'], proof['port_manifest'], proof['calibre']]
    _require(all(checked(p) in sources for p in required), 'Proof omitted required original/native source')
    audit_pin = checked(request['gds_audit'])
    _require(audit_pin['path'] == str(root/'gds_audit'/'REQUEST_GDS_AUDIT.json'), 'Cross-candidate GDS audit')
    audit = document(audit_pin)
    _fields(audit, dict(schema='eucap15_final_candidate_gds_audit.v1', final_binding=binding_pin,
        physical_selection='FROZEN_MAIN_AUDIT_UNION', candidate_id=candidate_id,
        N_audit_attempted=1, N_audit_pass=1), 'GDS audit')
    _exact(audit['source_pins'], {**binding['source_pins'], 'final_binding': binding_pin,
        'private_config': request['private_config']}, 'GDS sources')
    _require(isinstance(audit['records'], list) and len(audit['records']) == 1,
             'Exactly one original union candidate per native root')
    row = audit['records'][0]
    _fields(row, dict(status='PASS', candidate_id=candidate_id, candidate_id_sha256=candidate_sha,
        candidate_geometry_identity_sha256=geometry_sha, gds=proof['gds'],
        port_manifest=proof['port_manifest']), 'GDS identity')
    # Existing GDS producer stores these checks in its original geometry-audit
    # artifact, not in the request summary row. Never invent copied row checks.
    geometry_pin = checked(row['geometry_audit'])
    _require(Path(geometry_pin['path']).is_relative_to(root/'gds_audit'), 'Foreign geometry audit')
    geom = document(geometry_pin)
    normalized_gds = gds_timestamp_normalized_sha256(Path(mirror.check(proof['gds'])['path']))
    checked(proof['gds'])
    _fields(geom, dict(schema='independent_research_candidate_gds_geometry_audit.v1',
        overall_status='PASS', candidate_id=candidate_id, candidate_id_sha256=candidate_sha,
        candidate_geometry_identity_sha256=geometry_sha,
        gds_path=proof['gds']['path'], gds_sha256=proof['gds']['sha256'],
        gds_timestamp_normalized_sha256=normalized_gds, process_token='/TSMC65_05_12_26/',
        original_artifacts_unchanged=True, production_campaign_membership=False), 'original geometry audit')
    _require(isinstance(geom.get('checks'), dict) and
        all(geom['checks'].get(k) is True for k in GEOMETRY_CHECKS) and
        all(v is True for v in geom['checks'].values()), 'Incomplete or failed geometry checks')
    originals = [checked(p) for p in geom['original_artifacts']]
    _require(len({p['path'] for p in originals}) == len(originals) and
             proof['gds'] in originals and proof['port_manifest'] in originals,
             'Geometry audit omits original GDS or actual port manifest')
    for evidence in geom['evidence']:
        checked(evidence)
    _require(geometry_pin in sources, 'Proof omitted actual geometry audit')
    drc_index = csv.DictReader(io.StringIO(raw(request['calibre_index']).decode('utf-8'), newline=''))
    names = drc_index.fieldnames
    _require(names and len(names) == len(set(names)), 'DRC index duplicate/missing columns')
    drc_rows = list(drc_index)
    _require(all(None not in r and all(v is not None for v in r.values()) for r in drc_rows),
             'Malformed DRC index row')
    matching = [r for r in drc_rows if r['candidate_id_sha256'] == candidate_sha]
    _require(len(matching) == 1 and matching[0]['drc_summary_path'] == proof['calibre']['path']
             and matching[0]['drc_summary_sha256'] == proof['calibre']['sha256'],
             'DRC index must identify exactly this candidate and summary')
    drc = document(proof['calibre'])
    _fields(drc, dict(overall_status='PASS', blocking_drc_violation_count=0,
        drc_scope='foundry_macro_ip_back_end', candidate_id_sha256=candidate_sha,
        candidate_geometry_identity_sha256=geometry_sha,
        gds_path=proof['gds']['path'], gds_sha256=proof['gds']['sha256'],
        geometry_audit_sha256=geometry_pin['sha256'],
        gds_timestamp_normalized_sha256=normalized_gds, process_token='/TSMC65_05_12_26/',
        gds_top_cell='TRANSFORMER', drc_source_rule_deck_sha256=FOUNDRY_DECK_SHA256), 'same-GDS Calibre')
    _require(isinstance(drc.get('checks'), dict) and drc['checks'] and
             all(value is True for value in drc['checks'].values()) and
             all(drc['checks'].get(k) is True for k in (*GEOMETRY_CHECKS,
                 'foundry_drc_pass', 'no_blocking_drc_violations', 'calibre_result_accounting_complete')),
             'Calibre checks missing or not all PASS')
    for path_key, sha_key in (('drc_report_path', 'drc_report_sha256'),
                             ('drc_source_rule_deck_path', 'drc_source_rule_deck_sha256')):
        original = checked(mirror.known(drc[path_key]))
        _require(original['sha256'] == drc[sha_key], 'DRC source/report differs from summary')
    solver = document(solver_pin)
    _fields(solver, dict(schema='frequency_research_fresh_solver.v1', status='PASS',
        candidate_id=candidate_id, preflight=proof_pin, source_gds_before=proof['gds'],
        source_gds_after=proof['gds'], real_emx=True, production_modified=False, q_emx=None), 'fresh solver')
    outputs = [checked(p) for p in solver['artifacts']]
    solve_dir = solver_root/'solve'
    _require(len({p['path'] for p in outputs}) == len(outputs) and
             all(Path(p['path']).is_relative_to(solve_dir) for p in outputs), 'Invalid solver artifact closure')
    touchstone = checked(solver['touchstone'])
    _require(touchstone in outputs and Path(touchstone['path']).suffix == '.s4p'
             and touchstone['bytes'] > 0, 'Own nonempty S4P missing')
    commands = [p for p in outputs if Path(p['path']).name == 'emx_command.json']
    _require(len(commands) == 1, 'Exactly one saved solver command required')
    _exact(document(commands[0]), proof['command'], 'Actual EMX command')
    values = validate_feature_values(feature, raw(files['features_56.csv']),
        wanted=wanted, proxy=proxy, scale=scale, tau=tau)
    # A per-candidate recheck, not Mirror.recheck() over every earlier candidate.
    for entry in list(consumed.values()):
        checked(entry['original'])
    publication = dict(frame_sha256=ctx['context']['frame_sha256'],
        model_freeze_sha256=ctx['context']['model_freeze_sha256'], model_id=binding['model_id'],
        request_id=binding['request_id'], candidate_id=candidate_id,
        frozen_record_sha256=binding['frozen_record_sha256'], state=values['status'],
        actual=values['actual'], evidence_ref=manifest_pin, touchstone_sha=touchstone['sha256'],
        candidate_geometry_identity_sha256=geometry_sha,
        reason_code=None if values['valid_for_strict_comparison'] else 'ORIGINAL56_STRICT_OR_PHYSICS_INVALID')
    _publication(publication, ctx['slots'][candidate_id], ctx['context'])
    return dict(schema='eucap15_final_completed_evidence_check.v1', publication=publication,
        strict_joint_hit=values['strict_joint_hit'],
        evidence_pins=[consumed[k]['original'] for k in sorted(consumed)],
        resolution_evidence=[consumed[k] for k in sorted(consumed)],
        verified_scope='FINAL_CANDIDATE_RECEIPT_CHAIN_AND_LABEL_CONSISTENCY',
        native_dispatch_authorized=False, native_executions_by_reader=0,
        final_model_selection_qa=False, q_emx=None)
