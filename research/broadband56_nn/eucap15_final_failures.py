"""Read closed FINAL candidate failures; never dispatch, retry or infer success.

This additive interface preserves the owner's original PROCESS/INTENT and
failure bytes. A FINAL producer/exporter must supply a frozen candidate-Q
closure and an independently selected release; it is not installed by this
reader. Unknown, resource, interrupted, partial or inconsistent evidence raises
NO_GO, rather than inventing a physical failure or dropping its original slot.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
from pathlib import Path
import re

from .eucap15_final_binding import candidate_binding
from .eucap15_final_evidence import _exact
from .eucap15_final_statistics import _publication
from .eucap15_selected_evidence import FREQUENCIES, SelectedEvidenceError, _fields, _path, _require
from .frequency_research_emx import GEOMETRY_CHECKS, FOUNDRY_DECK_SHA256
from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import (
    GdsIdentityError, gds_timestamp_normalized_sha256,
)

# These unchanged implementations establish the exact exception semantics.
# Future runtime versions need a source review, not a permissive text match.
FAILURE_SOURCE_SHA = {
    'simulation': '30924946cff8ad0ad52cd25174d3d420468e10b0aad228b767a68dd8755ff30e',
    'extractor': '5fbf0e3e22737874ea8b74fc758fb0f6451aca44a146fedb35a2ec95eeb0ac29',
}
FAILURE_SOURCE_PATH = {
    'simulation': 'rfic_transformer_inverse_design/sim/emx/simulation.py',
    'extractor': 'rfic_transformer_inverse_design/campaigns/broadband56_s4p_qa.py',
}
CALIBRE_LOADER = "import importlib.util,sys; s=importlib.util.spec_from_file_location('research.broadband56_nn.frequency_research_calibre',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.run(sys.argv[2],int(sys.argv[3]))"
STAGES = {
    'cadence': ('cadence_only', 'cadence_only/parallel_candidate_queue_dataset_summary.json', 'GDS_FAIL'),
    'gds_audit': ('gds_audit', 'gds_audit/REQUEST_GDS_AUDIT.json', 'GDS_FAIL'),
    'calibre': ('calibre', 'calibre/RESEARCH_WRAPPER_RECEIPT.json', 'DRC_FAIL'),
    'emx': ('emx_selected', 'emx_selected/features/FEATURE_RECEIPT.json', None),
}
GATES = {'ACTUAL_GDS_AUDIT_REJECTED': 'gds_audit', 'CALIBRE_ZERO_BLOCKING_NOT_PASS': 'calibre'}
FEATURE_PREFIXES = (
    'Touchstone parse failed: ', 'fresh-EMX S4P exact contract failed: ',
    'S-to-Z conversion failed: ', 'Z-to-S roundtrip failed: ',
    'S-to-Z roundtrip exceeds tolerance: ',
)
FEATURE_EXACT = {
    'S-to-Z conversion produced incomplete or non-finite Z',
    'differential Z projection produced incomplete or non-finite data',
    'SRF reactance series is incomplete or non-finite',
}
NON_FAILURE = ('RESOURCE_WAIT', 'NO_DISPATCH', 'Dispatch budget expired',
    'PARTIAL_', 'FAILED_FEATURES', 'KeyboardInterrupt', 'SystemExit',
    'OWNER_STOPPED', 'LEASE_BUSY', 'SOURCE_IDENTITY', 'IDENTITY_CHANGED',
    'changed during', 'command changed', 'identity mismatch')


class _Closure:
    """Only consumed candidate pins are rechecked, never the whole campaign."""
    def __init__(self, mirror):
        self.mirror, self.used = mirror, {}

    def check(self, pin):
        resolved = self.mirror.check(pin)
        original = self.mirror.known(pin['path'])
        _require(original['path'] not in self.used or self.used[original['path']]['original'] == original,
                 'Conflicting candidate source')
        self.used[original['path']] = dict(original=original, resolved=resolved)
        return original

    def known(self, path):
        return self.check(self.mirror.known(str(path)))

    def has(self, path):
        # Unpublished is NOT proof of absence on the server. Only used to reject
        # contradictory positive evidence; classification requires positive pins.
        return str(path) in self.mirror.entries

    def document(self, pin):
        self.check(pin)
        value = self.mirror.document(pin)
        self.check(pin)
        return value

    def raw(self, pin):
        resolved = self.mirror.check(self.check(pin))
        value = Path(resolved['path']).read_bytes()
        _require(len(value) == pin['bytes'] and hashlib.sha256(value).hexdigest() == pin['sha256'],
                 'Artifact changed while reading')
        self.check(pin)
        return value

    def rows(self, pin):
        reader = csv.DictReader(io.StringIO(self.raw(pin).decode('utf-8'), newline=''))
        _require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)),
                 'Missing or duplicate CSV columns')
        rows = list(reader)
        _require(all(None not in row and all(v is not None for v in row.values()) for row in rows),
                 'Malformed CSV row')
        return rows

    def recheck(self):
        for entry in list(self.used.values()):
            self.check(entry['original'])


def _common(binding, binding_pin):
    return dict(final_binding=binding_pin, request_id=binding['request_id'],
        candidate_id=binding['candidate_id'], model_id=binding['model_id'],
        dataset_scope='FINAL_FROZEN_DATASET', frequency_ghz=15,
        q_requested=binding['q_target'], q_proxy=binding['q_proxy'], q_emx=None,
        memberships=binding['memberships'], physical_selection='FROZEN_MAIN_AUDIT_UNION',
        original_request_denominator=binding['original_request_denominator'])


def _flags(command, flags):
    for key, value in flags.items():
        _require(command.count(key) == 1, 'Missing or repeated stage flag: ' + key)
        offset = command.index(key) + 1
        _require(offset < len(command) and command[offset] == str(value), 'Wrong stage flag: ' + key)


def _stage(c, root, name, error, release, release_document):
    output, completion, state = STAGES[name]
    pin = c.known(root/(name+'_PROCESS.json'))
    process = c.document(pin)
    intent = c.document(c.known(root/(name+'_INTENT.json')))
    _exact(process['intent'], intent, 'Original stage intent')
    _fields(intent, dict(release=release, output=str(root/output), completion=str(root/completion)), 'stage intent')
    command = intent['command']
    _require(isinstance(command, list) and command and all(isinstance(v, str) and v for v in command),
             'Original argv required')
    argument = root/('cadence_candidates.csv' if name == 'cadence' else
        'GDS_REQUEST.json' if name == 'gds_audit' else 'CALIBRE_REQUEST.json' if name == 'calibre' else 'EMX_REQUEST.json')
    _require(command.count(str(argument)) == 1, 'Stage command does not bind exact candidate input')
    if name == 'cadence':
        _flags(command, {'--candidate-csv': argument, '--out-dir': root/output})
    elif name == 'calibre':
        wrapper = c.check(release_document['calibre_wrapper'])
        _require(len(command) == 7 and command[1:] == ['-B', '-c', CALIBRE_LOADER,
            wrapper['path'], str(argument), '<inherited-fd>'], 'Calibre command differs from original lease-inheriting wrapper')
    elif name in ('gds_audit', 'emx'):
        _flags(command, {'--request': argument, '--out' if name == 'gds_audit' else '--output': root/output})
        module = 'research.broadband56_nn.frequency_research_' + ('gds_audit' if name == 'gds_audit' else 'emx')
        _flags(command, {'-m': module})
        if name == 'emx':
            _require(command[command.index('-m')+2] == 'run', 'Owner stage must cover original solve then extract')
    _require(type(process['returncode']) is int, 'Exact integer wrapper returncode required')
    log = c.check(process['log'])
    _require(Path(log['path']).parent == root and Path(log['path']).name.startswith(name+'_')
             and Path(log['path']).suffix == '.log', 'Cross-candidate process log')
    lines = c.raw(log).decode('utf-8').rstrip().splitlines()
    tail = lines[-1] if lines else ''
    _require(not any(marker.lower() in tail.lower() for marker in NON_FAILURE),
             'Resource, partial, interruption or integrity error is NO_GO, not a physical failure')
    terminal = process['completion']
    if terminal is not None:
        _require(c.check(terminal)['path'] == str(root/completion), 'Wrong stage completion')
    if error in GATES:
        _require(process['returncode'] == 0 and terminal is not None, 'Gate failure requires completed audit')
    else:
        # A zero-return child with missing output is a publication/integrity gap,
        # not evidence of a failed native execution.
        _require(process['returncode'] > 0, 'No positive closed process exit; signal interruption is NO_GO')
        _require(terminal is not None or not c.has(root/completion),
                 'Published completion contradicts failed-process missing completion')
        if error.startswith('STAGE_EXECUTION_FAILED: '):
            _require(error == 'STAGE_EXECUTION_FAILED: '+name+'; '+log['path'], 'Failure names another stage log')
    return process, pin, tail, state


def _geometry(c, root, b, binding_pin, config, *, passed):
    audit_pin = c.known(root/'gds_audit'/'REQUEST_GDS_AUDIT.json')
    audit = c.document(audit_pin)
    _fields(audit, dict(schema='eucap15_final_candidate_gds_audit.v1', final_binding=binding_pin,
        physical_selection='FROZEN_MAIN_AUDIT_UNION', candidate_id=b['candidate_id'],
        N_audit_attempted=1, N_audit_pass=int(passed)), 'candidate GDS audit')
    _exact(audit['source_pins'], {**b['source_pins'], 'final_binding': binding_pin, 'private_config': config}, 'GDS sources')
    _require(isinstance(audit['records'], list) and len(audit['records']) == 1, 'One candidate per GDS audit')
    row = audit['records'][0]
    cid_sha = hashlib.sha256(b['candidate_id'].encode()).hexdigest()
    _fields(row, dict(status='PASS' if passed else 'FAIL', candidate_id=b['candidate_id'],
        candidate_id_sha256=cid_sha, candidate_geometry_identity_sha256=b['candidate_geometry_identity_sha256']), 'GDS row')
    for key in ('gds', 'port_manifest', 'geometry_audit'):
        _require(Path(c.check(row[key])['path']).is_relative_to(root), 'Foreign GDS evidence')
    geometry_pin = row['geometry_audit']
    _require(Path(geometry_pin['path']).is_relative_to(root/'gds_audit'), 'Foreign actual geometry audit')
    geom = c.document(geometry_pin)
    normalized = gds_timestamp_normalized_sha256(Path(c.mirror.check(row['gds'])['path']))
    c.check(row['gds'])
    _fields(geom, dict(schema='independent_research_candidate_gds_geometry_audit.v1',
        overall_status='PASS' if passed else 'FAIL', candidate_id=b['candidate_id'],
        candidate_id_sha256=cid_sha, candidate_geometry_identity_sha256=b['candidate_geometry_identity_sha256'],
        gds_path=row['gds']['path'], gds_sha256=row['gds']['sha256'],
        gds_timestamp_normalized_sha256=normalized, process_token='/TSMC65_05_12_26/',
        original_artifacts_unchanged=True, production_campaign_membership=False), 'actual geometry audit')
    checks = geom['checks']
    _require(isinstance(checks, dict) and all(k in checks for k in GEOMETRY_CHECKS)
             and all(type(v) is bool for v in checks.values()), 'Missing/nonboolean geometry checks')
    failed = [k for k, value in checks.items() if not value]
    _require(bool(failed) is (not passed), 'GDS status contradicts actual checks')
    if not passed:
        _require(isinstance(row['failed_checks'], list) and len(row['failed_checks']) == len(set(row['failed_checks']))
                 and set(row['failed_checks']) == set(failed), 'Failed checks differ from actual geometry audit')
    originals = [c.check(p) for p in geom['original_artifacts']]
    _require(len({p['path'] for p in originals}) == len(originals)
             and row['gds'] in originals and row['port_manifest'] in originals, 'Original GDS/ports missing')
    for pin in geom['evidence']:
        c.check(pin)
    return audit, row, normalized


def _calibre_request(c, root, binding_pin, audit, row, normalized):
    pin = c.known(root/'CALIBRE_REQUEST.json')
    request = c.document(pin)
    _fields(request, dict(schema='frequency_research_calibre_request.v1', final_binding=binding_pin,
        input_index=audit['calibre_input'], out=str(root/'calibre')), 'Calibre request')
    inputs = c.rows(c.check(request['input_index']))
    _require(len(inputs) == 1, 'Exactly one original candidate in DRC input')
    _fields(inputs[0], dict(candidate_id_sha256=row['candidate_id_sha256'],
        candidate_geometry_identity_sha256=row['candidate_geometry_identity_sha256'],
        gds_path=row['gds']['path'], gds_sha256=row['gds']['sha256'],
        geometry_audit_path=row['geometry_audit']['path'],
        gds_timestamp_normalized_sha256=normalized, top_cell='TRANSFORMER'), 'DRC original input')
    for key in ('script', 'gds_hash_source'):
        c.check(request[key])
    for value in request['runtime_sources']:
        c.check(value)
    return pin


def _drc(c, root, row, normalized, *, passed):
    index_pin = c.known(root/'calibre'/'drc_index.csv')
    rows = c.rows(index_pin)
    _require(len(rows) == 1 and rows[0]['candidate_id_sha256'] == row['candidate_id_sha256'],
             'DRC index must have exactly this candidate')
    index_row = rows[0]
    summary_pin = c.known(index_row['drc_summary_path'])
    _require(summary_pin['sha256'] == index_row['drc_summary_sha256']
             and Path(summary_pin['path']).is_relative_to(root/'calibre'), 'Foreign DRC summary')
    summary = c.document(summary_pin)
    _fields(summary, dict(overall_status='PASS' if passed else 'FAIL',
        drc_scope='foundry_macro_ip_back_end', candidate_id_sha256=row['candidate_id_sha256'],
        candidate_geometry_identity_sha256=row['candidate_geometry_identity_sha256'],
        gds_path=row['gds']['path'], gds_sha256=row['gds']['sha256'],
        geometry_audit_sha256=row['geometry_audit']['sha256'],
        gds_timestamp_normalized_sha256=normalized, process_token='/TSMC65_05_12_26/',
        gds_top_cell='TRANSFORMER', drc_source_rule_deck_sha256=FOUNDRY_DECK_SHA256), 'same-GDS DRC')
    _require(index_row['overall_status'] == summary['overall_status'], 'DRC index contradicts summary')
    checks = summary['checks']
    required = (*GEOMETRY_CHECKS, 'foundry_drc_pass', 'no_blocking_drc_violations', 'calibre_result_accounting_complete')
    _require(isinstance(checks, dict) and all(k in checks for k in required)
             and all(type(v) is bool for v in checks.values()), 'Incomplete DRC checks')
    blocking = summary['blocking_drc_violation_count']
    _require(type(blocking) is int and blocking >= 0, 'Invalid blocking violation count')
    if passed:
        _require(blocking == 0 and all(checks.values()), 'Failed DRC before solver')
    else:
        _require(blocking > 0 or not all(checks.values()), 'No actual DRC rejection')
    for path_key, sha_key in (('drc_report_path', 'drc_report_sha256'),
                             ('drc_source_rule_deck_path', 'drc_source_rule_deck_sha256')):
        _require(c.known(summary[path_key])['sha256'] == summary[sha_key], 'DRC report/deck mismatch')
    return index_pin, summary_pin


def _preflight(c, root, b, binding_pin, config, common, release):
    audit, row, normalized = _geometry(c, root, b, binding_pin, config, passed=True)
    _calibre_request(c, root, binding_pin, audit, row, normalized)
    index, drc = _drc(c, root, row, normalized, passed=True)
    request_pin = c.known(root/'EMX_REQUEST.json')
    request = c.document(request_pin)
    _fields(request, {**{k:v for k,v in common.items() if k != 'q_emx'},
        'schema':'eucap15_final_emx_request.v1', 'private_config':config,
        'target_source':b['original_record']['target_source'], 'production_campaign_membership':False,
        'gds_audit':c.known(root/'gds_audit'/'REQUEST_GDS_AUDIT.json'), 'calibre_index':index}, 'EMX request')
    proof_pin = c.known(root/'emx_selected'/'PREFLIGHT.json')
    proof = c.document(proof_pin)
    _fields(proof, {**common, 'schema':'eucap15_final_emx_preflight.v1', 'status':'PASS',
        'request':request_pin, 'candidate_id_sha256':row['candidate_id_sha256'],
        'geometry_sha256':b['candidate_geometry_identity_sha256'], 'gds':row['gds'],
        'port_manifest':row['port_manifest'], 'calibre':drc, 'frequency_grid_hz':FREQUENCIES,
        'port_order':['P001','P002','P003','P004'], 'port_permutation':[0,1,3,2], 'reference_ohm':50,
        'output':str(root/'emx_selected'), 'production_membership':False,
        'full11_physical_optimum':'NOT_EVALUATED_BY_NATIVE_OWNER',
        'executed_hit_tolerances':b['absolute_tolerances'],
        'protocol':dict(q_values=list(range(10,21)), q_scalar='min(Qp,Qs)',
            score_scale=b['score_scale'], absolute_tolerances=b['absolute_tolerances'])}, 'FINAL preflight')
    _exact(proof['original_record'], b['original_record'], 'Preflight original record')
    sources = [c.check(p) for p in proof['source_pins']]
    _require(len({p['path'] for p in sources}) == len(sources), 'Duplicate preflight sources')
    runtime = request['runtime']
    runtime_sources = [c.check(p) for p in runtime['source_pins']]
    _require(len({p['path'] for p in runtime_sources}) == len(runtime_sources), 'Duplicate runtime source')
    for name, relative in FAILURE_SOURCE_PATH.items():
        source = release['failure_sources'][name]
        _require(source in runtime_sources and source in sources
            and source['path'] == str(_path(runtime['repo'])/relative),
            'Reviewed failure source is not the bound executed runtime: '+name)
    required = [binding_pin, request_pin, config, *b['source_pins'].values(), request['gds_audit'],
        row['gds'], row['port_manifest'], row['geometry_audit'], index, drc]
    _require(all(p in sources for p in required), 'Preflight original sources missing')
    return proof_pin, proof


def _emx_failure(c, root, b, binding_pin, config, common, process, tail, release):
    out = root/'emx_selected'
    _require(process['returncode'] > 0 and process['completion'] is None, 'EMX pipeline is not a closed failed stage')
    solver_failed, feature_failed = c.has(out/'SOLVER_FAILURE.json'), c.has(out/'FEATURE_FAILURE.json')
    _require(solver_failed != feature_failed, 'Exactly one specific published failure required; generic pipeline is NO_GO')
    _require(not c.has(out/'features'/'MANIFEST.json') and not c.has(out/'features'/'FEATURE_RECEIPT.json'),
             'Completed/partially published features require separate reconciliation, not guessed failure')
    proof_pin, proof = _preflight(c, root, b, binding_pin, config, common, release)
    failure = c.document(c.known(out/('SOLVER_FAILURE.json' if solver_failed else 'FEATURE_FAILURE.json')))
    _fields(failure, dict(status='FAIL_NO_AUTOMATIC_RETRY'), 'original substage failure')
    error = failure['error']
    _require(isinstance(error, str) and error and isinstance(failure['ended_utc'], str)
             and tail == error, 'Closed owner log does not match original substage exception')
    _require(not any(marker.lower() in error.lower() for marker in NON_FAILURE), 'Non-physical substage stop')
    if solver_failed:
        _require(not c.has(out/'SOLVER_RECEIPT.json'), 'Contradictory solver success')
        native_dir = out/'solve'/'emx'
        stderr = c.known(native_dir/'emx_stderr.log')
        c.known(native_dir/'emx_stdout.log')
        command = c.document(c.known(native_dir/'emx_command.json'))
        _exact(command, proof['command'], 'Original native argv')
        match = re.fullmatch(r'RuntimeError: EMX failed with exit code ([1-9][0-9]*)\. See '
            + re.escape(stderr['path']) + r' for details\.', error)
        _require(match is not None, 'No exact native nonzero-exit evidence; unknown solver failure is NO_GO')
        # The saved wrapper PID does not establish a native attempt identity.
        return 'SOLVER_FAIL', None, 'NATIVE_EMX_NONZERO_EXIT', dict(native_returncode=int(match[1]))
    solver_pin = c.known(out/'SOLVER_RECEIPT.json')
    solver = c.document(solver_pin)
    _fields(solver, dict(schema='frequency_research_fresh_solver.v1', status='PASS',
        candidate_id=b['candidate_id'], preflight=proof_pin, source_gds_before=proof['gds'],
        source_gds_after=proof['gds'], real_emx=True, production_modified=False, q_emx=None), 'successful solver before extraction failure')
    artifacts = [c.check(p) for p in solver['artifacts']]
    _require(len({p['path'] for p in artifacts}) == len(artifacts)
             and all(Path(p['path']).is_relative_to(out/'solve') for p in artifacts), 'Invalid solver artifact closure')
    s4p = c.check(solver['touchstone'])
    _require(s4p in artifacts and s4p['bytes'] > 0 and Path(s4p['path']).suffix == '.s4p', 'Original own S4P missing')
    commands = [p for p in artifacts if Path(p['path']).name == 'emx_command.json']
    _require(len(commands) == 1, 'Exactly one original native command required')
    _exact(c.document(commands[0]), proof['command'], 'Successful solver argv')
    prefix = 'Broadband56S4pQaError: '
    message = error[len(prefix):] if error.startswith(prefix) else ''
    _require(message in FEATURE_EXACT or any(message.startswith(p) and len(message) > len(p) for p in FEATURE_PREFIXES),
             'No recognized original extractor error; identity/I/O/unknown exception is NO_GO')
    return 'FEATURE_FAIL', s4p['sha256'], 'ORIGINAL56_EXTRACTOR_REJECTED', dict(native_returncode=None)


def inspect_failure(ctx, candidate_id, result_pin, mirror, *, expected_release, expected_private_config):
    """Consume one frozen export; all expected pins come from caller's release.

    ctx is load_context's result, reused across the campaign. No native/model
    action or feature extraction occurs. A caller must preserve any NO_GO slot
    unresolved and retain the original denominator; never omit or replace it.
    Ordinary GDS/DRC execution failures are pipeline failures, not proof that a
    tool started or that geometry is intrinsically infeasible. Native counts
    remain unknown; MAIN/AUDIT membership is not an execution-count multiplier.
    """
    try:
        return _inspect(ctx, candidate_id, result_pin, mirror, expected_release, expected_private_config)
    except SelectedEvidenceError:
        raise
    except (KeyError, ValueError, OSError, TypeError, IndexError, OverflowError, csv.Error, GdsIdentityError) as error:
        raise SelectedEvidenceError('Invalid FINAL failed evidence: '+str(error)) from error


def _inspect(ctx, candidate_id, result_pin, mirror, expected_release, expected_private_config):
    b = candidate_binding(ctx, candidate_id)
    _require(b['analytic_grid'] is True and b['candidate_geometry_identity_sha256'] is not None,
             'Original analytic failure cannot acquire a native failure')
    c = _Closure(mirror)
    result_pin = c.check(result_pin)
    root = _path(result_pin['path']).parent
    _require(Path(result_pin['path']).name == 'RESULT.json' and root.name == candidate_id
             and root.parent.name == b['request_id'], 'Candidate-Q independent result root required')
    binding_pin = c.known(root/'FINAL_BINDING.json')
    _exact(c.document(binding_pin), b, 'Frozen FINAL binding')
    for pin in b['source_pins'].values():
        c.check(pin)
    config = c.check(expected_private_config)
    release_pin = c.check(expected_release)
    _require(release_pin['path'] == str(root.parent.parent/'RELEASE.json'), 'Candidate belongs to another owner release root')
    release = c.document(release_pin)
    _fields(release, dict(schema='eucap15_final_native_release.v1', private_config=config), 'Frozen owner release')
    _exact(release['source_pins'], {k:b['source_pins'][k] for k in ('frame','model_freeze','inference_complete')}, 'Owner FINAL source pins')
    for name, sha in FAILURE_SOURCE_SHA.items():
        _require(c.check(release['failure_sources'][name])['sha256'] == sha, 'Unreviewed failure runtime semantics: '+name)
    common = _common(b, binding_pin)
    result = c.document(result_pin)
    _fields(result, {**common, 'schema':'eucap15_final_candidate_failure.v1',
        'status':'CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION',
        'candidate_geometry_identity_sha256':b['candidate_geometry_identity_sha256']}, 'FINAL terminal failure')
    error = result['error']
    _require(isinstance(error, str) and not any(p.lower() in error.lower() for p in NON_FAILURE), 'Nonterminal/unknown failure')
    names = [GATES[error]] if error in GATES else [name for name in STAGES if
        error == 'PRIOR_STAGE_FAILED: '+name or error.startswith('STAGE_EXECUTION_FAILED: '+name+'; ')]
    _require(len(names) == 1, 'Unrecognized failure; preserve NO_GO')
    name = names[0]
    if name != 'emx':
        later = list(STAGES)[list(STAGES).index(name)+1:]
        conflicting = [root/(stage+'_PROCESS.json') for stage in later]
        conflicting += [root/STAGES[stage][1] for stage in later]
        conflicting += [root/'emx_selected'/file for file in (
            'PREFLIGHT.json', 'SOLVER_RECEIPT.json', 'SOLVER_FAILURE.json',
            'FEATURE_FAILURE.json', 'features/MANIFEST.json')]
        _require(not any(c.has(path) for path in conflicting),
                 'Published downstream execution contradicts the claimed earlier-stage failure')
    request = c.document(c.known(root/'GDS_REQUEST.json'))
    _fields(request, {**{k:v for k,v in common.items() if k!='q_emx'},
        'schema':'eucap15_final_gds_audit_request.v1', 'production_campaign_membership':False,
        'cadence':dict(root=str(root/'cadence_only'), routes={candidate_id:'parallel_shards/shard_000'})}, 'FINAL GDS request')
    _exact(request['source_pins'], {**b['source_pins'], 'final_binding':binding_pin, 'private_config':config}, 'GDS request source pins')
    input_rows = c.rows(c.known(root/'cadence_candidates.csv'))
    _require(len(input_rows) == 1, 'One frozen geometry per cadence input')
    row = input_rows[0]
    _fields(row, dict(candidate_id=candidate_id, candidate_id_sha256=hashlib.sha256(candidate_id.encode()).hexdigest()), 'Cadence candidate identity')
    values = [float(row[k]) for k in b['geometry_fields']]
    _require(all(math.isfinite(v) for v in values) and values == b['grid_geometry'], 'Cadence geometry differs from frozen record')
    process, process_pin, tail, state = _stage(c, root, name, error, release_pin, release)
    reason, touchstone, detail = name.upper()+'_PROCESS_FAILED', None, dict(native_returncode=None)
    if name == 'gds_audit' and error in GATES:
        _geometry(c, root, b, binding_pin, config, passed=False)
        reason = 'ACTUAL_GDS_AUDIT_REJECTED'
    elif name == 'calibre':
        audit, geometry, normalized = _geometry(c, root, b, binding_pin, config, passed=True)
        request_pin = _calibre_request(c, root, binding_pin, audit, geometry, normalized)
        if error in GATES:
            index, _ = _drc(c, root, geometry, normalized, passed=False)
            wrapper = c.document(process['completion'])
            _fields(wrapper, dict(status='PROCESS_COMPLETE', input_request=request_pin, index=index,
                N_candidates=1, N_pass=0, production_modified=False, solver_started=False), 'DRC gate rejection')
            c.check(wrapper['summary'])
            reason = 'CALIBRE_ZERO_BLOCKING_NOT_PASS'
    elif name == 'emx':
        state, touchstone, reason, detail = _emx_failure(c, root, b, binding_pin, config, common, process, tail, release)
    if error not in GATES and name != 'emx' and process['completion'] is not None:
        raise SelectedEvidenceError('Nonzero process with a completed stage needs separate reconciliation')
    publication = dict(frame_sha256=ctx['context']['frame_sha256'], model_freeze_sha256=ctx['context']['model_freeze_sha256'],
        model_id=b['model_id'], request_id=b['request_id'], candidate_id=candidate_id,
        frozen_record_sha256=b['frozen_record_sha256'], state=state, actual=None,
        evidence_ref=result_pin, touchstone_sha=touchstone,
        candidate_geometry_identity_sha256=b['candidate_geometry_identity_sha256'], reason_code=reason)
    _publication(publication, ctx['slots'][candidate_id], ctx['context'])
    c.recheck()
    return dict(schema='eucap15_final_failed_evidence_check.v1', publication=publication,
        failed_stage=name, process=process_pin, original_error=error, **detail,
        evidence_pins=[c.used[k]['original'] for k in sorted(c.used)],
        resolution_evidence=[c.used[k] for k in sorted(c.used)],
        verified_scope='CLOSED_FINAL_CANDIDATE_FAILURE_NOT_NATIVE_ATTEMPT_COUNT',
        native_attempts=None, native_dispatch_authorized=False, native_executions_by_reader=0,
        final_model_selection_qa=False, q_emx=None)
