"""Read exact development128 EMX failures; never dispatch or extract labels.

The caller already loaded the frozen128 context and supplies its per-request
remote_context plus the same finite Mirror. Original native bytes and the128
denominator are unchanged. FINAL binding/schema functions are never called.
Only source-reviewed positive native exits or known exact56 extractor errors
after a successful solver are publishable. Other evidence remains NO_GO.
"""
from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path
import re

from . import eucap15_selected_evidence as selected
from .eucap15_final_failures import (
    _Closure, _flags, FAILURE_SOURCE_SHA, FAILURE_SOURCE_PATH,
    FEATURE_PREFIXES, FEATURE_EXACT, NON_FAILURE,
)
from .frequency_research_emx import GEOMETRY_CHECKS, FOUNDRY_DECK_SHA256
from .io import canonical_sha
from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import (
    GdsIdentityError, gds_timestamp_normalized_sha256,
)

RELEASE_SHA = '2c24abe9d81e951cc77286499b8976a8c7dfee6d0b6e2a8b051becf8065911bc'
OWNER_SHA = '4adc200a7d47d49b26f32e195199a15c3160352e7f6f4236b05f4b929c3e3b8b'
WRAPPER_SHA = 'd4c5c1f937b81d534a34858d41262598bfd8ced1fe26cde094de6050a5ba1c43'
SOURCE = 'DEVELOPMENT_CURRENT_SNAPSHOT_UNIFORM_TRIPLE'
NON_FEATURE_IO = ('[Errno ', 'PermissionError', 'FileNotFoundError', 'OSError:', 'MemoryError')
require, fields = selected._require, selected._fields


def exact(value, expected, label):
    require(selected._same(value, expected), label + ' differs')


def _context(c, ctx, item):
    """Reuse the selected reader's exact scope, then pin just this original11."""
    def frozen(value):
        return c.check(dict(value, path=ctx.path_map.get(value['path'], value['path'])))

    exact(c.document(ctx.manifest_pin), ctx.manifest, 'Original manifest')
    exact(c.document(ctx.freeze_pin), ctx.freeze, 'Original freeze')
    protocol, binding = selected._development_scope(ctx, item, c.document(ctx.reference_pin),
        checked=c.check, frozen=frozen)
    require(len(ctx.manifest['requests']) == 128 and
        sum(selected._same(row, item) for row in ctx.manifest['requests']) == 1,
        'Original unchanged128 item required')
    records_pin = frozen(item['source_records'])
    exact(records_pin, ctx.records_pin, 'Resolved original11')
    records = [selected._strict_json(line) for line in c.raw(records_pin).splitlines() if line.strip()]
    require(len(records) == 11 and [row['q_target'] for row in records] == list(range(10, 21))
        and len({row['candidate_id'] for row in records}) == 11, 'Original11 identities required')
    chosen = [row for row in records if row['candidate_id'] == item['candidate_id']]
    require(len(chosen) == 1, 'Original selected candidate missing')
    chosen = chosen[0]
    fields(chosen, dict(request_id=item['request_id'], q_target=item['q_proxy'], q_proxy=item['q_proxy'],
        proxy_preselected=True, analytic_grid=True, model_id=selected.DEVELOPMENT_MODEL,
        frequency_ghz=15, dataset_scope=selected.DEVELOPMENT_SCOPE, target_source=SOURCE,
        evidence_source='SELF_PROXY', emx_status='NOT_RUN', actual_response=None), 'Original selected row')
    require(item['selected_analytic_pass'] is True and type(item['record_line_number']) is int and
        1 <= item['record_line_number'] <= 11 and records[item['record_line_number']-1] == chosen and
        canonical_sha(chosen) == item['source_record_canonical_sha256'], 'Selected original line/hash drift')
    fields(item, dict(selected_target=chosen['target'], selected_grid_proxy=chosen['grid_proxy'],
        candidate_geometry_identity_sha256=chosen['candidate_geometry_identity_sha256']), 'Selected handoff')
    for name in ('target', 'grid_proxy'):
        values = chosen[name]
        require(isinstance(values, list) and len(values) == 4 and
            all(type(v) in (int, float) and math.isfinite(v) for v in values), 'Nonfinite original vector')
    require(all(v > 0 for v in chosen['target']) and chosen['target'][2] == item['q_proxy'], 'Original target drift')
    return protocol, binding, records_pin, records, chosen


def _owner_and_process(c, root, result, ctx, item, release_pin):
    release_pin = c.check(release_pin)
    require(release_pin['sha256'] == RELEASE_SHA and
        release_pin['path'] == str(root.parent/'RELEASE.json'), 'Unreviewed development release')
    release = c.document(release_pin)
    fields(release, dict(schema='eucap15_development128_delegated_prepared_release.v1'), 'Owner release')
    require(release['config']['path'] == str(root.parent/'CONFIG.json'), 'Foreign owner configuration')
    config = c.document(release['config'])
    fields(config, dict(schema='eucap15_development128_successor_owner.v1',
        model_id=selected.DEVELOPMENT_MODEL, original_manifest=ctx.original_manifest_pin,
        out=str(root.parent)), 'Owner configuration')
    exact(config['path_map'], ctx.path_map, 'Original native path relocation')
    code_sources = [c.check(p) for p in release['sources'] + config['source_pins']]
    owner = [p for p in release['sources'] if Path(p['path']).name == 'run_development_native.py']
    require(len(owner) == 1 and owner[0]['sha256'] == OWNER_SHA, 'Unreviewed original owner semantics')
    wrapper_path = str(selected._path(config['code_root'])/'research/broadband56_nn/frequency_research_emx.py')
    wrappers = {p['path']: p for p in code_sources if p['path'] == wrapper_path}
    require(len(wrappers) == 1 and wrappers[wrapper_path]['sha256'] == WRAPPER_SHA, 'Unreviewed EMX wrapper')
    wrapper = wrappers[wrapper_path]
    result_pin = c.known(root/'RESULT.json')
    exact(c.document(result_pin), result, 'Original RESULT')
    fields(result, dict(status='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION', request_id=item['request_id'],
        candidate_id=item['candidate_id'], q_proxy=item['q_proxy'], model_id=selected.DEVELOPMENT_MODEL,
        dataset_scope=selected.DEVELOPMENT_SCOPE, original_request_denominator=128,
        source_manifest=ctx.original_manifest_pin, original_records=item['source_records'],
        candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256']), 'Original failed identity')
    process_pin = c.known(root/'emx_PROCESS.json')
    process = c.document(process_pin)
    intent = c.document(c.known(root/'emx_INTENT.json'))
    exact(process['intent'], intent, 'Original process/intent')
    out = root/'emx_selected'
    expected_command = [config['python'], '-B', '-m', 'research.broadband56_nn.frequency_research_emx',
        'run', '--request', str(root/'EMX_REQUEST.json'), '--output', str(out),
        '--inherited-global-lease-fd', '<inherited-fd>']
    fields(intent, dict(release=release_pin, command=expected_command, output=str(out),
        completion=str(out/'features/FEATURE_RECEIPT.json')), 'Original lease-inheriting EMX intent')
    require(type(process['returncode']) is int and process['returncode'] > 0 and
        process['completion'] is None, 'Resource, signal, successful or incomplete pipeline is NO_GO')
    log = c.check(process['log'])
    require(Path(log['path']).parent == root and Path(log['path']).name.startswith('emx_') and
        Path(log['path']).suffix == '.log', 'Cross-request process log')
    require(result['error'] in ('PRIOR_STAGE_FAILED: emx', 'STAGE_EXECUTION_FAILED: emx; '+log['path']),
        'Unknown or partial owner failure')
    lines = c.raw(log).decode('utf-8').rstrip().splitlines()
    require(lines and not any(s.lower() in lines[-1].lower() for s in NON_FAILURE), 'Nonphysical pipeline stop')
    return config, wrapper, process_pin, lines[-1]


def _preflight(c, root, ctx, item, binding, protocol, records_pin, records, chosen, config, wrapper):
    """Original development schema; no FINAL fields or substituted geometry."""
    out = root/'emx_selected'
    proof_pin = c.known(out/'PREFLIGHT.json')
    proof = c.document(proof_pin)
    candidate_sha = hashlib.sha256(item['candidate_id'].encode()).hexdigest()
    common = dict(request_id=item['request_id'], candidate_id=item['candidate_id'],
        model_id=selected.DEVELOPMENT_MODEL, dataset_scope=selected.DEVELOPMENT_SCOPE,
        frequency_ghz=15, q_requested=item['q_proxy'], q_proxy=item['q_proxy'])
    fields(proof, dict(**common, schema=selected.PROOF_SCHEMA, status='PASS', q_emx=None,
        candidate_id_sha256=candidate_sha, geometry_sha256=item['candidate_geometry_identity_sha256'],
        original_record=chosen, protocol=protocol, executed_hit_tolerances=protocol['absolute_tolerances'],
        physical_selection='Q_PROXY_ONLY', selected_manifest=ctx.manifest_pin, reference=ctx.reference_pin,
        development_binding=binding, original_request_denominator=128,
        unselected_physical_status='NOT_REQUESTED_MAIN_PILOT',
        full11_physical_optimum='NOT_EVALUATED_SINGLE_PRESELECTED_CANDIDATE',
        frequency_grid_hz=selected.FREQUENCIES, port_order=['P001','P002','P003','P004'],
        port_permutation=[0,1,3,2], config_differential_port_pairs=[[0,1],[2,3]], reference_ohm=50,
        production_membership=False, no_gds_generation=True, no_example_target_objective=True,
        output=str(out)), 'Development preflight')
    request_pin = c.known(root/'EMX_REQUEST.json')
    exact(proof['request'], request_pin, 'Original EMX request pin')
    request = c.document(request_pin)
    fields(request, dict(**common, schema='eucap15_selected_emx_request.v1', target_source=SOURCE,
        development_binding=binding, production_campaign_membership=False,
        records=records_pin, qscan_freeze=ctx.freeze_pin, selected_manifest=ctx.original_manifest_pin,
        private_config=config['configuration'], runtime=config['emx_runtime'],
        dispatch_deadline_utc=config['dispatch_deadline_utc'], resource_budget=config['resource_budget'],
        global_lock_path=config['global_lock_path']), 'Original selected EMX request')
    sources = [c.check(p) for p in proof['source_pins']]
    require(len({p['path'] for p in sources}) == len(sources), 'Duplicate preflight source')
    runtime = request['runtime']
    runtime_sources = [c.check(p) for p in runtime['source_pins']]
    require(len({p['path'] for p in runtime_sources}) == len(runtime_sources), 'Duplicate runtime source')
    for name, relative in FAILURE_SOURCE_PATH.items():
        expected_path = str(selected._path(runtime['repo'])/relative)
        matches = [p for p in runtime_sources if p['path'] == expected_path]
        require(len(matches) == 1 and matches[0]['sha256'] == FAILURE_SOURCE_SHA[name] and
            matches[0] in sources, 'Unreviewed actual failure runtime: '+name)
    require(wrapper in sources and all(p in sources for p in runtime_sources), 'Actual wrapper/runtime missing from preflight')
    for key in ('process_file', 'emx_wrapper'):
        require(c.check(runtime[key]) in sources, 'Native process/wrapper omitted')
    command = proof['command']
    require(isinstance(command, list) and command and all(isinstance(v, str) for v in command) and
        command[0] == runtime['emx_wrapper']['path'] and command.count(proof['gds']['path']) == 1,
        'Native argv does not bind original wrapper and GDS')
    _flags(command, {'--sweep-stepsize':'1000000000'})
    require(command.count('--sweep') == 1 and command[command.index('--sweep')+1:command.index('--sweep')+3] ==
        ['5000000000','60000000000'] and '--parallel=2' in command and '--s-impedance=50' in command and
        '--cadence-pins=51' in command and '--simultaneous-frequencies=0' in command,
        'Original exact56 native command drift')
    require([v for v in command if v.startswith('--port=')] ==
        [f'--port={p}={p}:{p}_G' for p in ('P001','P002','P003','P004')], 'Original native ports drift')
    audit_pin = c.known(root/'gds_audit/REQUEST_GDS_AUDIT.json')
    exact(request['gds_audit'], audit_pin, 'Original GDS audit path')
    audit = c.document(audit_pin)
    fields(audit, dict(schema='eucap15_selected_request_gds_audit.v1', physical_selection='Q_PROXY_ONLY',
        selected_candidate_id=item['candidate_id'], N_selected=1, N_audit_attempted=1,
        development_binding=binding, original_request_denominator=128), 'Selected GDS audit')
    fields(audit['source_pins'], dict(eleven_records=records_pin, qscan_freeze=ctx.freeze_pin,
        selected_manifest=ctx.manifest_pin, reference=ctx.reference_pin,
        private_config=request['private_config']), 'GDS source identity')
    audited = audit['records']
    require(len(audited) == 11 and {r['candidate_id'] for r in audited} == {r['candidate_id'] for r in records},
        'Original11 GDS denominator drift')
    for row in audited:
        if row['candidate_id'] == item['candidate_id']:
            fields(row, dict(status='PASS', candidate_id_sha256=candidate_sha,
                candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256'],
                gds=proof['gds'], port_manifest=proof['port_manifest']), 'Same selected GDS')
            selected_row = row
        else:
            fields(row, dict(status='NOT_REQUESTED_MAIN_PILOT', audit_attempted=False,
                cadence_routed=False, calibre_eligible=False), 'Unselected Q must remain unrequested')
    exact(proof['original_candidate_statuses'],
        [dict(candidate_id=r['candidate_id'], status=r['status']) for r in audited], 'Original candidate statuses')
    for key in ('gds', 'port_manifest', 'geometry_audit'):
        require(Path(c.check(selected_row[key])['path']).is_relative_to(root), 'Foreign physical artifact')
    geometry_pin = selected_row['geometry_audit']
    geometry = c.document(geometry_pin)
    normalized = gds_timestamp_normalized_sha256(Path(c.mirror.check(proof['gds'])['path']))
    c.check(proof['gds'])
    fields(geometry, dict(overall_status='PASS', candidate_id_sha256=candidate_sha,
        candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256'],
        gds_path=proof['gds']['path'], gds_sha256=proof['gds']['sha256'],
        gds_timestamp_normalized_sha256=normalized), 'Actual original GDS geometry')
    require(all(geometry['checks'].get(k) is True for k in GEOMETRY_CHECKS) and
        all(v is True for v in geometry['checks'].values()), 'Actual geometry checks failed')
    originals = [c.check(p) for p in geometry['original_artifacts']]
    require(proof['gds'] in originals and proof['port_manifest'] in originals, 'Original GDS/ports omitted')
    index_pin = c.known(root/'calibre/drc_index.csv')
    exact(request['calibre_index'], index_pin, 'Original DRC index')
    index_rows = c.rows(index_pin)
    matching = [r for r in index_rows if r['candidate_id_sha256'] == candidate_sha]
    require(len(matching) == 1 and matching[0]['overall_status'] == 'PASS' and
        matching[0]['drc_summary_path'] == proof['calibre']['path'] and
        matching[0]['drc_summary_sha256'] == proof['calibre']['sha256'], 'Same-candidate DRC summary missing')
    drc_pin = c.check(proof['calibre'])
    require(Path(drc_pin['path']).is_relative_to(root/'calibre'), 'Foreign DRC summary')
    drc = c.document(drc_pin)
    fields(drc, dict(overall_status='PASS', blocking_drc_violation_count=0,
        drc_scope='foundry_macro_ip_back_end', candidate_id_sha256=candidate_sha,
        candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256'],
        gds_path=proof['gds']['path'], gds_sha256=proof['gds']['sha256'],
        geometry_audit_sha256=geometry_pin['sha256'], gds_timestamp_normalized_sha256=normalized,
        process_token='/TSMC65_05_12_26/', gds_top_cell='TRANSFORMER',
        drc_source_rule_deck_sha256=FOUNDRY_DECK_SHA256), 'Same-GDS zero-blocking DRC')
    require(all(drc['checks'].get(k) is True for k in (*GEOMETRY_CHECKS,
        'foundry_drc_pass','no_blocking_drc_violations','calibre_result_accounting_complete')) and
        all(v is True for v in drc['checks'].values()), 'DRC checks missing or failed')
    drc_sources = []
    for pk, sk in (('drc_report_path','drc_report_sha256'),('drc_source_rule_deck_path','drc_source_rule_deck_sha256')):
        artifact = c.known(drc[pk])
        require(artifact['sha256'] == drc[sk], 'DRC report/deck changed')
        drc_sources.append(artifact)
    required = [request_pin, records_pin, ctx.freeze_pin, ctx.manifest_pin, ctx.reference_pin,
        request['private_config'], audit_pin, index_pin, proof['gds'], proof['port_manifest'], geometry_pin, drc_pin,
        *originals, *drc_sources]
    require(all(c.check(p) in sources for p in required), 'Required preflight source omitted')
    return proof_pin, proof


def inspect_emx_failure(result, item, root, ctx, mirror, release_pin):
    """Return (SOLVER_FAIL|FEATURE_FAIL, detail), or SelectedEvidenceError.

    detail preserves solver S4P identity for FEATURE_FAIL but no labels/hit.
    Export completeness belongs to the caller; an absent Mirror entry is not
    server-side absence. Every unknown/partial/resource/signal case stays NO_GO.
    """
    try:
        return _inspect(result, item, root, ctx, mirror, release_pin)
    except selected.SelectedEvidenceError:
        raise
    except (KeyError, ValueError, OSError, TypeError, IndexError, OverflowError, csv.Error, GdsIdentityError) as error:
        raise selected.SelectedEvidenceError('Invalid development128 failed evidence: '+str(error)) from error


def _inspect(result, item, root, ctx, mirror, release_pin):
    root = selected._path(str(root))
    require(root.name == item['request_id'], 'Original request root required')
    c = _Closure(mirror)
    protocol, binding, records_pin, records, chosen = _context(c, ctx, item)
    config, wrapper, process_pin, tail = _owner_and_process(c, root, result, ctx, item, release_pin)
    out = root/'emx_selected'
    solver_failed, feature_failed = c.has(out/'SOLVER_FAILURE.json'), c.has(out/'FEATURE_FAILURE.json')
    require(solver_failed != feature_failed, 'Exactly one specific failure required; generic pipeline is NO_GO')
    require(not c.has(out/'features/FEATURE_RECEIPT.json') and not c.has(out/'features/MANIFEST.json'),
        'Published completed/partial feature receipt contradicts failure')
    proof_pin, proof = _preflight(c, root, ctx, item, binding, protocol, records_pin, records, chosen, config, wrapper)
    failure_pin = c.known(out/('SOLVER_FAILURE.json' if solver_failed else 'FEATURE_FAILURE.json'))
    failure = c.document(failure_pin)
    fields(failure, dict(status='FAIL_NO_AUTOMATIC_RETRY'), 'Original substage failure')
    error = failure['error']
    require(isinstance(error, str) and error and isinstance(failure['ended_utc'], str) and
        tail == error and not any(p.lower() in error.lower() for p in NON_FAILURE), 'Unclosed/unknown/nonphysical failure')
    native_dir = out/'solve/emx'
    native_command = c.known(native_dir/'emx_command.json')
    exact(c.document(native_command), proof['command'], 'Original native command')
    c.known(native_dir/'emx_stdout.log'); stderr = c.known(native_dir/'emx_stderr.log')
    touchstone, native_rc = None, None
    if solver_failed:
        require(not c.has(out/'SOLVER_RECEIPT.json'), 'Published solver success contradicts failure')
        match = re.fullmatch(r'RuntimeError: EMX failed with exit code ([1-9][0-9]*)\. See '+
            re.escape(stderr['path'])+r' for details\.', error)
        require(match is not None, 'No exact positive native exit; signal/setup/unknown solver failure is NO_GO')
        native_rc, state = int(match[1]), 'SOLVER_FAIL'
    else:
        solver_pin = c.known(out/'SOLVER_RECEIPT.json')
        solver = c.document(solver_pin)
        fields(solver, dict(schema='frequency_research_fresh_solver.v1', status='PASS',
            candidate_id=item['candidate_id'], preflight=proof_pin, source_gds_before=proof['gds'],
            source_gds_after=proof['gds'], real_emx=True, production_modified=False, q_emx=None), 'Successful original solver')
        artifacts = [c.check(p) for p in solver['artifacts']]
        require(len({p['path'] for p in artifacts}) == len(artifacts) and
            all(Path(p['path']).is_relative_to(out/'solve') for p in artifacts), 'Foreign/duplicate solver output')
        s4p = c.check(solver['touchstone'])
        require(s4p in artifacts and s4p['bytes'] > 0 and Path(s4p['path']).suffix == '.s4p' and
            native_command in artifacts and stderr in artifacts and c.known(native_dir/'emx_stdout.log') in artifacts,
            'Successful own solver artifact closure incomplete')
        prefix = 'Broadband56S4pQaError: '
        message = error[len(prefix):] if error.startswith(prefix) else ''
        require(not any(value.lower() in message.lower() for value in NON_FEATURE_IO) and
            (message in FEATURE_EXACT or any(message.startswith(p) and len(message)>len(p) for p in FEATURE_PREFIXES)),
            'No recognized original56 extractor error; identity/I/O/partial is NO_GO')
        touchstone, state = s4p['sha256'], 'FEATURE_FAIL'
    c.recheck()
    return state, dict(stage='emx', failed_substage='solver' if solver_failed else 'extractor',
        process=process_pin, failure=failure_pin, detail=result['error'], original_substage_error=error,
        interpretation='CLOSED_DEVELOPMENT128_FAILURE_NOT_NATIVE_ATTEMPT_COUNT',
        touchstone_sha=touchstone, actual=None, strict_joint_hit=None, native_returncode=native_rc,
        native_attempts=None, original_request_denominator=128, q_proxy=item['q_proxy'], q_emx=None,
        evidence_pins=[c.used[k]['original'] for k in sorted(c.used)],
        resolution_evidence=[c.used[k] for k in sorted(c.used)],
        native_calls=0, model_inferences=0, FINAL=False)
