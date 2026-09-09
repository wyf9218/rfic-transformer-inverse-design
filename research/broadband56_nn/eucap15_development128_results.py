"""Consume a pinned development128 owner snapshot, never a live work directory.

This is a research-side reader, not a dispatcher/exporter. Absent published
terminals remain pending in this snapshot; unknown or incomplete claimed
terminals fail closed. No model, dataset, simulator, network or plot is used.
The frozen initial-ledger reader remains unchanged as historical evidence.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from . import eucap15_development128_reader as initial
from .eucap15_selected_evidence import inspect_features, _strict_json, _same
from .eucap15_selected_metrics import summarize
from .frequency_physical_statistics import csv_write, save, require
from .io import utc_now

SCHEMA = 'eucap15_development128_result_snapshot.v1'
RELEASE_SHA = '2c24abe9d81e951cc77286499b8976a8c7dfee6d0b6e2a8b051becf8065911bc'
MANIFEST_SHA = 'b3d388571dbbd6f94d1d273d11910cb9518038ac790233ad2bd483bb5f337ff8'
QA_SHA = 'ede9ed845a8c804de16450f5636cab0b9244b66ae03796de3755c12c5b1b4acb'
SOURCE = 'DEVELOPMENT_CURRENT_SNAPSHOT_UNIFORM_TRIPLE'
pin, path, fields = initial.pin, initial.path, initial.fields


class Mirror:
    """Exact source/resolved pins only; no guessed prefix or remote fallback."""
    def __init__(self, entries):
        require(isinstance(entries, list), 'explicit source mirror entries required')
        self.entries, self.used = {}, {}
        for entry in entries:
            require(set(entry) == {'original', 'resolved'}, 'original/resolved pins required')
            self.add(entry['original'], entry['resolved'])

    def add(self, original, resolved):
        original, resolved = initial.pin_shape(original), initial.pin_shape(resolved)
        require(original['sha256'] == resolved['sha256'] and original['bytes'] == resolved['bytes'],
                'mirror must preserve original bytes')
        entry = dict(original=original, resolved=resolved)
        require(original['path'] not in self.entries or self.entries[original['path']] == entry,
                'conflicting mirror source')
        self.entries[original['path']] = entry

    def known(self, value):
        require(str(value) in self.entries, 'unpublished required artifact: ' + str(value))
        return self.entries[str(value)]['original']

    def check(self, item):
        item = initial.pin_shape(item)
        require(self.known(item['path']) == item, 'pin not exactly in snapshot')
        entry = self.entries[item['path']]
        require(pin(entry['resolved']['path']) == entry['resolved'], 'mirror source changed')
        self.used[item['path']] = entry
        return entry['resolved']

    def document(self, item):
        resolved = self.check(item)
        raw = Path(resolved['path']).read_bytes()
        require(len(raw) == item['bytes'] and hashlib.sha256(raw).hexdigest() == item['sha256'],
                'receipt changed while reading')
        value = _strict_json(raw)
        self.check(item)
        return value

    def recheck(self):
        for entry in list(self.used.values()):
            self.check(entry['original'])

    @property
    def paths(self):
        return {p: e['resolved']['path'] for p, e in self.entries.items()}


def remote_context(ctx, config, item, mirror):
    """Construct the exact native identity; original pilot bytes are not edited."""
    mapping = config['path_map']
    qa_sources = {p['path']: initial.pin_shape(p) for p in ctx.qa['source_pins']}
    qa_sources[ctx.qa_pin['path']] = ctx.qa_pin

    def remote(original):
        original = initial.pin_shape(original)
        require(qa_sources.get(original['path']) == original, 'metadata not bound by original candidate QA')
        require(original['path'] in mapping, 'missing exact native source relocation')
        resolved = dict(original, path=mapping[original['path']])
        # Equal-byte local originals replace transport copies only as read locations.
        if resolved['path'] in mirror.entries:
            require(mirror.known(resolved['path']) == resolved, 'native metadata pin differs')
        else:
            mirror.add(resolved, original)
        return resolved

    manifest_pin = remote(ctx.manifest_pin)
    freeze_pin = remote(ctx.manifest['freeze'])
    model_pin = remote(ctx.manifest['model_identity'])
    pair_pin = remote(ctx.identity['pair'])
    pair = mirror.document(pair_pin)
    for role in initial.WEIGHT_SHAS:
        require(pair['roles'][role]['best'] == ctx.identity['best_weights'][role], 'model pair weights mismatch')
    require(pair['inverse_forward']['best'] == ctx.identity['best_weights']['forward'], 'inverse forward binding mismatch')
    specification = mirror.document(remote(ctx.freeze['specification']))
    fields(specification, dict(q_values=list(range(10, 21)), q_scalar='min(Qp,Qs)',
        score_scale=list(initial.SCORE_SPANS), frequency_ghz=15, dataset_scope=initial.SCOPE), 'specification')
    records_pin = remote(item['source_records'])
    qa_pin = remote(ctx.qa_pin)
    binding = dict(schema='eucap15_development128_physical_binding.v1',
        dataset_scope=initial.SCOPE, model_role=initial.MODEL_ROLE, model_id=initial.MODEL_ID,
        model_identity=model_pin, original_model_identity=ctx.manifest['model_identity'],
        model_pair=pair_pin, selected_manifest=manifest_pin, original_selected_manifest=ctx.manifest_pin,
        independent_candidate_qa=qa_pin, original_request_denominator=128,
        request_id=item['request_id'], q_proxy=item['q_proxy'], selected_candidate_id=item['candidate_id'],
        candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256'],
        no_failure_replacement=True, production_campaign_membership=False, FINAL=False)
    # Preflight source lists can include other QA-bound transport metadata. Add
    # their exact location mappings, but hash/read only what the closure consumes.
    for original in qa_sources.values():
        if original['path'] in mapping:
            remote(original)
    protocol = dict(q_values=specification['q_values'], q_scalar=specification['q_scalar'],
        score_scale=ctx.freeze['score_scale'], absolute_tolerances=ctx.freeze['absolute_tolerances'])
    return SimpleNamespace(manifest=ctx.manifest, freeze=ctx.freeze, reference=ctx.identity,
        manifest_pin=manifest_pin, freeze_pin=freeze_pin, reference_pin=model_pin,
        original_manifest_pin=ctx.manifest_pin, original_reference_pin=ctx.manifest['model_identity'],
        reference_receipt_pin=pair_pin, records_pin=records_pin, path_map=dict(mapping),
        development_binding=binding, comparison_protocol=protocol,
        executed_hit_tolerances=ctx.freeze['absolute_tolerances'])


STAGES = {
    'cadence': ('cadence_only', 'cadence_only/parallel_candidate_queue_dataset_summary.json', 'GDS_FAIL'),
    'gds_audit': ('gds_audit', 'gds_audit/REQUEST_GDS_AUDIT.json', 'GDS_FAIL'),
    'calibre': ('calibre', 'calibre/RESEARCH_WRAPPER_RECEIPT.json', 'DRC_FAIL'),
    'emx': ('emx_selected', 'emx_selected/features/FEATURE_RECEIPT.json', 'SOLVER_FAIL'),
}


def check_gds_request(mirror, root, item, ctx):
    request = mirror.document(mirror.known(root/'GDS_REQUEST.json'))
    fields(request, dict(schema='eucap15_selected_gds_audit_request.v1',
        selected_manifest=ctx.original_manifest_pin, development_binding=ctx.development_binding,
        production_campaign_membership=False), 'GDS request')
    fields(request['request'], dict(request_id=item['request_id'], q_proxy=item['q_proxy'],
        model_id=initial.MODEL_ID, dataset_scope=initial.SCOPE, frequency_ghz=15, target_source=SOURCE), 'GDS identity')
    fields(request['source_pins'], dict(eleven_records=ctx.records_pin, qscan_freeze=ctx.freeze_pin,
        selected_manifest=ctx.manifest_pin, reference=ctx.reference_pin), 'GDS original sources')
    for source in request['source_pins'].values():
        mirror.check(source)
    fields(request['cadence'], dict(root=str(root/'cadence_only'),
        routes={item['candidate_id']: 'parallel_shards/shard_000'}), 'single-candidate route')


def failure_stage(result, item, root, ctx, mirror, release_pin):
    """Classify a closed pipeline failure, never infer a native solve count."""
    error = result['error']
    require(isinstance(error, str), 'failure reason required')
    explicit = {'ACTUAL_GDS_AUDIT_REJECTED': 'gds_audit', 'CALIBRE_ZERO_BLOCKING_NOT_PASS': 'calibre'}
    if error in explicit:
        name = explicit[error]
    else:
        matches = [name for name in STAGES if error == 'PRIOR_STAGE_FAILED: '+name or
                   error.startswith('STAGE_EXECUTION_FAILED: '+name+'; ')]
        require(len(matches) == 1, 'unresolved terminal failure; do not mislabel pending or invent stage')
        name = matches[0]
    check_gds_request(mirror, root, item, ctx)
    output, completion, state = STAGES[name]
    process = mirror.document(mirror.known(root/(name+'_PROCESS.json')))
    fields(process['intent'], dict(release=release_pin, output=str(root/output),
        completion=str(root/completion)), 'terminal stage intent')
    command = process['intent']['command']
    require(isinstance(command, list) and all(isinstance(v, str) for v in command), 'saved command required')
    required_arg = str(root/('cadence_candidates.csv' if name == 'cadence' else
        'GDS_REQUEST.json' if name == 'gds_audit' else 'CALIBRE_REQUEST.json' if name == 'calibre' else 'EMX_REQUEST.json'))
    require(command.count(required_arg) == 1, 'stage command refers to another request')
    require(type(process['returncode']) is int, 'real process returncode required')
    mirror.check(process['log'])
    require(Path(process['log']['path']).parent == root and
        Path(process['log']['path']).name.startswith(name+'_'), 'stage log outside this request')
    if process['completion'] is not None:
        require(process['completion']['path'] == str(root/completion), 'wrong stage completion')
        mirror.check(process['completion'])
    if error in explicit:
        require(process['returncode'] == 0 and process['completion'] is not None, 'gate failure lacks completed audit')
        if name == 'gds_audit':
            audit = mirror.document(process['completion'])
            fields(audit, dict(schema='eucap15_selected_request_gds_audit.v1',
                development_binding=ctx.development_binding, selected_candidate_id=item['candidate_id'],
                N_selected=1, N_audit_attempted=1, N_audit_pass=0), 'failed GDS audit')
            selected = [r for r in audit['records'] if r['candidate_id'] == item['candidate_id']]
            require(len(selected) == 1 and len(audit['records']) == 11, 'failed GDS original11 accounting')
            fields(selected[0], dict(status='FAIL', candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256'],
                audit_attempted=True, calibre_eligible=False), 'failed selected geometry')
            require(selected[0]['failed_checks'], 'missing actual failed checks')
        else:
            # Saved native DRC gate must itself report rejection, not only an error string.
            import csv
            index_pin = mirror.known(root/'calibre/drc_index.csv')
            wrapper = mirror.document(process['completion'])
            fields(wrapper, dict(status='PROCESS_COMPLETE',
                input_request=mirror.known(root/'CALIBRE_REQUEST.json'), index=index_pin,
                N_candidates=1, N_pass=0, production_modified=False, solver_started=False), 'DRC wrapper rejection')
            mirror.check(wrapper['input_request'])
            mirror.check(wrapper['summary'])
            index = mirror.check(index_pin)
            with Path(index['path']).open(newline='') as stream:
                drc = list(csv.DictReader(stream))
            require(len(drc) == 1 and drc[0]['overall_status'] != 'PASS', 'DRC rejection not established')
            require(drc[0]['candidate_id_sha256'] == hashlib.sha256(item['candidate_id'].encode()).hexdigest(),
                    'DRC rejection belongs to another candidate')
    else:
        require(process['returncode'] != 0 or process['completion'] is None, 'claimed execution failure was successful')
    if name in ('calibre', 'emx'):
        request = mirror.document(mirror.known(required_arg))
        fields(request, dict(development_binding=ctx.development_binding), 'failed stage binding')
    if name == 'emx':
        # The native command includes both solver and extraction. Do not turn
        # an extraction/preflight error into a claimed failed EMX solve.
        raise ValueError('EMX_PIPELINE_FAILURE_REQUIRES_SPECIFIC_SOLVER_OR_EXTRACTION_EVIDENCE; NO_GO preserved')
    return state, dict(stage=name, process=mirror.known(root/(name+'_PROCESS.json')),
        detail=error, interpretation='PIPELINE_STAGE_FAILURE_NOT_PROOF_OF_NATIVE_SOLVER_START')


def consume(ctx, snapshot, mirror):
    fields(snapshot, dict(schema=SCHEMA, dataset_scope=initial.SCOPE, model_id=initial.MODEL_ID,
        N_original_requests=128, mode='FROZEN_PUBLISHED_TERMINALS_NOT_LIVE_STATUS'), 'snapshot')
    release_pin = initial.pin_shape(snapshot['release'])
    require(release_pin['sha256'] == RELEASE_SHA, 'prepared native release differs')
    release = mirror.document(release_pin)
    fields(release, dict(schema='eucap15_development128_delegated_prepared_release.v1'), 'owner release')
    config = mirror.document(release['config'])
    fields(config, dict(schema='eucap15_development128_successor_owner.v1',
        model_id=initial.MODEL_ID, original_manifest=ctx.manifest_pin), 'owner config')
    root = path(config['out'])
    require(Path(release_pin['path']) == root/'RELEASE.json' and
        Path(release['config']['path']) == root/'CONFIG.json', 'wrong native root')
    entries = snapshot['requests']
    require(len(entries) == 128 and [e['request_id'] for e in entries] ==
        [j['request_id'] for j in ctx.manifest['requests']], 'all original128 requests in frozen order required')
    require(all(set(e) == {'request_id', 'result'} for e in entries), 'request/result entries only')
    rows, closures = deepcopy(ctx.rows), []
    for row, item, entry in zip(rows, ctx.manifest['requests'], entries):
        row.update(native_result_pin=None, status_detail='NO_PUBLISHED_TERMINAL_IN_THIS_SNAPSHOT')
        if entry['result'] is None:
            continue
        result_pin = initial.pin_shape(entry['result'])
        candidate_root = root/item['request_id']
        require(Path(result_pin['path']) == candidate_root/'RESULT.json', 'result belongs to another request')
        result = mirror.document(result_pin)
        fields(result, dict(request_id=item['request_id'], candidate_id=item['candidate_id'],
            q_proxy=item['q_proxy']), 'terminal selected identity')
        row.update(native_result_pin=result_pin, status_detail=result['status'])
        if result['status'] in ('ANALYTIC_FAIL_ORIGINAL_NO_REPLACEMENT', 'CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION'):
            fields(result, dict(model_id=initial.MODEL_ID, dataset_scope=initial.SCOPE,
                original_request_denominator=128, source_manifest=ctx.manifest_pin,
                original_records=item['source_records'],
                candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256']), 'failure original identity')
            if result['status'] == 'ANALYTIC_FAIL_ORIGINAL_NO_REPLACEMENT':
                require(item['selected_analytic_pass'] is False, 'original analytic-pass relabeled failed')
                fields(result, dict(native_started=False), 'original analytic failure')
                require('ORIGINAL_SELECTED_ANALYTIC_FAIL' in result['reasons'], 'wrong analytic failure reason')
                row['state'] = 'ANALYTIC_FAIL'
                continue
            require(item['selected_analytic_pass'] is True, 'failed original selection dispatched')
            native = remote_context(ctx, config, item, mirror)
            row['state'], detail = failure_stage(result, item, candidate_root, native, mirror, release_pin)
            closures.append(dict(request_id=item['request_id'], failure=detail))
        elif result['status'] == 'FRESH_EMX_EXTRACTED':
            require(item['selected_analytic_pass'] is True, 'failed original selection dispatched')
            native = remote_context(ctx, config, item, mirror)
            fields(result, dict(development_binding=native.development_binding, production_accepted=False), 'fresh terminal')
            feature_pin = initial.pin_shape(result['feature'])
            require(Path(feature_pin['path']) == candidate_root/'emx_selected/features/FEATURE_RECEIPT.json', 'wrong feature location')
            feature = mirror.document(feature_pin)
            manifest_pin = mirror.known(Path(feature_pin['path']).parent/'MANIFEST.json')
            manifest = mirror.document(manifest_pin)
            require(feature_pin in manifest['artifacts'], 'terminal feature not in closed manifest')
            outcome = inspect_features(dict(request_id=item['request_id'], feature_manifest=manifest_pin),
                item, native, physical_path_map=mirror.paths)
            fields(result, dict(valid_for_strict_comparison=outcome['valid_for_strict_comparison'],
                strict_joint_hit=outcome['strict_joint_hit'],
                absolute_percent_error=feature['target_relative_absolute_percent']), 'terminal physical result')
            require(outcome['geometry_sha'] == item['candidate_geometry_identity_sha256'], 'wrong physical geometry')
            for original in outcome['evidence_pins']:
                mirror.check(original)
            row.update(state=outcome['status'], actual=outcome['actual'],
                strict_joint_hit=outcome['strict_joint_hit'], touchstone_sha=outcome['touchstone_sha'])
            closures.append(dict(request_id=item['request_id'], **outcome))
        else:
            raise ValueError('unknown terminal status retained as NO_GO: '+str(result['status']))
    if snapshot.get('batch') is not None:
        batch_pin = snapshot['batch']
        require(Path(batch_pin['path']) == root/'BATCH_RECEIPT.json', 'wrong batch path')
        batch = mirror.document(batch_pin)
        fields(batch, dict(status='ALL_ORIGINAL_128_ACCOUNTED', release=release_pin,
            N_original_requests=128, N_proxy_records=1408), 'batch terminal')
        require(all(e['result'] is not None for e in entries) and
            batch['results'] == [mirror.document(e['result']) for e in entries], 'batch differs from individual terminals')
    mirror.recheck()
    return rows, closures


def validate_export(ctx, snapshot, mirror):
    """The research index must match the owner's fixed terminal publication."""
    owner = mirror.document(snapshot['owner_export'])
    fields(owner, dict(schema='eucap15_development128_terminal_export.v1', original_denominator=128), 'owner export')
    require(owner['release']['original'] == snapshot['release'], 'snapshot release differs from owner export')
    release = mirror.document(snapshot['release'])
    require(owner['config']['original'] == release['config'], 'export config differs from release')
    fixed = mirror.document(owner['immutable_snapshot'])
    fields(fixed, dict(schema='eucap15_development128_fixed_terminal_snapshot.v1',
        original_request_denominator=128, selected_manifest_original=ctx.manifest_pin), 'owner fixed snapshot')
    require(snapshot['snapshot_utc'] == fixed['capture_completed_utc'], 'snapshot time changed')
    require(len(fixed['rows']) == len(snapshot['requests']) == 128, 'published128 rows required')
    for index, (row, entry, item) in enumerate(zip(fixed['rows'], snapshot['requests'], ctx.manifest['requests'])):
        fields(row, dict(request_id=item['request_id'], request_order=index, q_proxy=item['q_proxy'],
            candidate_id=item['candidate_id'], candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256']),
            'published row identity')
        require(entry == dict(request_id=row['request_id'], result=row['terminal']), 'terminal publication omitted or substituted')
        require(row['observation'] == ('NO_TERMINAL_AT_CAPTURE' if row['terminal'] is None else 'TERMINAL_AT_CAPTURE'),
                'published observation inconsistent')
    count = sum(r['terminal'] is not None for r in fixed['rows'])
    require(fixed['terminal_count_at_capture'] == owner['results_exported'] == count and
        fixed['no_terminal_count_at_capture'] == owner['no_terminal_at_snapshot'] == 128-count, 'published counts differ')


def prepare_snapshot(export_pin, out):
    """One no-clobber metadata index from an existing byte-pinned owner export.

    This only assembles paths/identities. It does not redo candidate QA, read
    terminal content, calculate errors or inspect a remote/current directory.
    """
    require(pin(export_pin['path']) == export_pin, 'owner export changed')
    owner = _strict_json(Path(export_pin['path']).read_bytes())
    fields(owner, dict(schema='eucap15_development128_terminal_export.v1', original_denominator=128), 'owner export')
    export_root = Path(export_pin['path']).parent
    require(str(export_root) == owner['local_export_dir'], 'export local directory mismatch')
    sources = [dict(original=export_pin, resolved=export_pin)]

    def metadata(key, filename):
        original = initial.pin_shape(owner[key])
        require(Path(original['path']) == path(owner['remote_export_dir'])/filename, 'wrong remote export metadata location')
        resolved = pin(export_root/filename)
        require(resolved['sha256'] == original['sha256'] and resolved['bytes'] == original['bytes'], 'export metadata changed')
        sources.append(dict(original=original, resolved=resolved))
        raw = Path(resolved['path']).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == original['sha256'], 'export metadata changed during parse')
        return _strict_json(raw)

    fixed = metadata('immutable_snapshot', 'FIXED_SNAPSHOT_MANIFEST.json')
    index = metadata('source_index', 'RESULT_SOURCE_INDEX.json')
    mapping = metadata('path_map', 'SOURCE_TO_LOCAL_PATH_MAP.json')
    fields(index, dict(schema='eucap15_development128_terminal_source_index.v1', originals_mirrored_without_change=True), 'source index')
    seen = set()
    for entry in index['results']+index['reused_local_roots']:
        original, resolved = initial.pin_shape(entry['original']), initial.pin_shape(entry['resolved'])
        require(original['path'] not in seen and mapping.get(original['path']) == resolved['path'], 'source index/map conflict')
        seen.add(original['path'])
        sources.append(dict(original=original, resolved=resolved))
    require(len(index['results']) == owner['results_exported'], 'export result index count differs')
    terminals = {r['request_id']: r['original'] for r in index['results']}
    require(len(terminals) == len(index['results']), 'duplicate exported request')
    require(terminals == {r['request_id']: r['terminal'] for r in fixed['rows'] if r['terminal'] is not None},
            'fixed rows and terminal index differ')
    snapshot = dict(schema=SCHEMA, dataset_scope=initial.SCOPE, model_id=initial.MODEL_ID,
        N_original_requests=128, mode='FROZEN_PUBLISHED_TERMINALS_NOT_LIVE_STATUS',
        snapshot_utc=fixed['capture_completed_utc'], owner_export=export_pin,
        release=owner['release']['original'], sources=sources,
        requests=[dict(request_id=r['request_id'], result=r['terminal']) for r in fixed['rows']], batch=None)
    # This operation has no scientific outputs; the full consumer checks every
    # consumed source before any metric is released.
    require(pin(export_pin['path']) == export_pin, 'owner export changed during preparation')
    out = path(out)
    require(out.parent.is_dir() and not out.is_relative_to(Path(__file__).resolve().parents[2]), 'research snapshot path required')
    with out.open('x') as stream:
        json.dump(snapshot, stream, indent=2, allow_nan=False)
        stream.write('\n')
    return pin(out)


def build(manifest_pin, qa_pin, snapshot_pin, out):
    out = path(out)
    require(out.parent.is_dir() and not out.is_relative_to(Path(__file__).resolve().parents[2]),
            'existing research output parent outside code worktree required')
    require(manifest_pin['sha256'] == MANIFEST_SHA and qa_pin['sha256'] == QA_SHA, 'exact development128 identity required')
    out.mkdir(exist_ok=False)
    try:
        require(pin(snapshot_pin['path']) == snapshot_pin, 'snapshot changed')
        snapshot = _strict_json(Path(snapshot_pin['path']).read_bytes())
        save(out/'INTENT.json', dict(created_utc=utc_now(), manifest=manifest_pin, qa=qa_pin,
            snapshot=snapshot_pin, native_calls=0, model_inferences=0))
        ctx = initial.load_context(manifest_pin, qa_pin)
        mirror = Mirror(snapshot['sources'])
        export_pin = snapshot['owner_export']
        validate_export(ctx, snapshot, mirror)
        rows, closures = consume(ctx, snapshot, mirror)
        result = summarize(rows, score_spans=ctx.freeze['score_scale'], tolerances=ctx.freeze['absolute_tolerances'])
        result['summary'].update(dataset_scope=initial.SCOPE, model_role=initial.MODEL_ROLE,
            model_id=initial.MODEL_ID, frequency_ghz=15, FINAL=False,
            source_rows=ctx.identity['source_rows'], gradient_train_rows=ctx.identity['gradient_train_rows'],
            validation_rows=ctx.identity['validation_rows'], test_rows_count_only=ctx.identity['test_rows_count_only'],
            REAL_EMX_VALIDATION='CLOSED_PUBLISHED_SELECTED_RESULTS' if closures and
                any('actual' in c for c in closures) else 'NO_COMPLETED_PHYSICAL_EVIDENCE_CONSUMED',
            mode=snapshot['mode'], snapshot_utc=snapshot['snapshot_utc'],
            N_owner_terminal_receipts_consumed=sum(r['native_result_pin'] is not None for r in rows),
            pending_semantics='No terminal evidence published in this frozen snapshot; not current remote process status',
            original_q_proxy_unchanged=True, model_loads=0, model_inferences=0,
            native_calls=0, new_training_updates=0, source_data_arrays_read=False, figures_created=0)
        csv_write(out/'REQUEST_RESULTS.csv', rows)
        csv_write(out/'PHYSICAL_METRICS.csv', result['metric_rows'])
        csv_write(out/'ERROR_ECDF.csv', result['ecdf_rows'], fields=initial.ECDF_FIELDS)
        save(out/'SUMMARY.json', result['summary'])
        save(out/'SOURCE_CLOSURE.json', dict(owner_export=export_pin, terminal_closures=closures,
            original_context=ctx.sources, native_sources=list(mirror.used.values())))
        mirror.recheck()
        for source in ctx.sources:
            require(pin(source['path']) == source, 'original context changed during results read')
        require(pin(snapshot_pin['path']) == snapshot_pin, 'snapshot changed during results read')
        artifacts = [pin(p) for p in sorted(out.iterdir()) if p.is_file()]
        save(out/'RECEIPT.json', dict(schema='eucap15_development128_result_receipt.v1',
            status='PASS_PUBLISHED_SNAPSHOT_ACCOUNTING_NOT_FINAL', created_utc=utc_now(),
            artifacts=artifacts, implementation=[pin(Path(__file__)), pin(Path(inspect_features.__code__.co_filename)),
                pin(Path(summarize.__code__.co_filename)), pin(Path(initial.__file__))],
            N_original_requests=128, native_calls=0, model_inferences=0, new_training_updates=0,
            source_or_mirror_modifications=0, figures_created=0, FINAL=False))
        with (out/'SHA256SUMS').open('x') as stream:
            for artifact in artifacts+[pin(out/'RECEIPT.json')]:
                stream.write(artifact['sha256']+'  '+Path(artifact['path']).name+'\n')
        return result['summary']
    except Exception as error:
        save(out/'FAILURE_RECEIPT.json', dict(status='NO_GO_PRESERVED', created_utc=utc_now(),
            error=repr(error), native_calls=0, model_inferences=0))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'qa-receipt', 'snapshot', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--snapshot-sha256', required=True)
    args = parser.parse_args(argv)
    snapshot = pin(args.snapshot)
    require(snapshot['sha256'] == args.snapshot_sha256, 'explicit snapshot SHA mismatch')
    result = build(pin(args.manifest), pin(args.qa_receipt), snapshot, args.out)
    print(json.dumps(dict(output=str(args.out), N_original_requests=result['N_original_requests'],
        N_owner_terminals=result['N_owner_terminal_receipts_consumed'], REAL_EMX_VALIDATION=result['REAL_EMX_VALIDATION'])))


if __name__ == '__main__':
    main()
