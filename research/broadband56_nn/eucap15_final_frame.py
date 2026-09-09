"""FINAL-only target freeze and resumable Q-scan; never a native dispatcher.

Reuses the tested frequency pair loader and Q-scan kernel. No production data is
extracted or modified. ``prepare`` requires an explicitly FINAL model/data
declaration and verifies the loaded pair before drawing a single target. Its
declaration is not an independent scientific audit or an automatic FINAL choice.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import platform
import re
import shutil
import sys

import numpy as np

from .io import canonical_sha, read_json, save_json, sha256, utc_now
from .frequency_qscan import _batch, SCALE, TAU, Q_VALUES

N_REQUESTS = 10000
N_AUDIT = 100
LOW = [.5, .5, .2]
HIGH = [2., 2., .85]
PIN_NAMES = ('forward', 'inverse', 'normalizer', 'geometry_contract',
             'dataset', 'data_manifest', 'split', 'validation_selection')


def pin(path):
    path = Path(path).absolute()
    if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Regular nonsymlink evidence file required: ' + str(path))
    return dict(path=str(path), sha256=sha256(path), bytes=path.stat().st_size)


def check(value):
    actual = pin(value['path'])
    if any(actual[k] != value[k] for k in ('sha256', 'bytes')):
        raise ValueError('Frozen input identity changed: ' + str(value['path']))
    return actual


def _protocol():
    if SCALE.tolist() != [2.5, 2.5, 20., .8] or list(Q_VALUES) != list(range(10, 21)):
        raise ValueError('Existing Q-scan scientific contract changed')
    return dict(frequency_ghz=15, label_mode='STRICT_LUMPED', triple_low=LOW,
        triple_high=HIGH, sampling='IID_UNIFORM_BOX_NUMPY_PCG64', q_values=list(Q_VALUES),
        q_scalar='min(Qp,Qs)', score='sqrt(mean(((grid_forward-target)/scale)**2))',
        scale=SCALE.tolist(), absolute_tolerances=TAU.tolist(),
        tolerance_semantics='LEGACY_0.05_TIMES_FLOAT64_SPANS_NOT_TARGET_RELATIVE_PERCENT',
        tie_break='EXACT_TIE_SMALLER_Q', no_target_clipping=True,
        selection='ALL_ELEVEN_FINITE_PROXY_SCORES_THEN_MINIMUM_NO_ANALYTIC_RERANK',
        main_requests=N_REQUESTS, audit_requests=N_AUDIT, audit_sampling='WITHOUT_REPLACEMENT_BEFORE_INFERENCE',
        main_checkpoints=[100, 1000, 10000], no_failure_replacement=True,
        q_emx_requires='ALL_ORIGINAL_ELEVEN_STRICT_VALID', native_owner='EXISTING_DATA_OWNER_ONLY',
        additional_audit_budget=1000, maximum_main_plus_audit_candidate_slots=11000)


def final_identity(path):
    identity = pin(path)
    value = read_json(path)
    if (value.get('schema') != 'eucap15_final_model_freeze.v1'
            or value.get('status') != 'FINAL_FROZEN' or value.get('model_role') != 'FINAL'
            or value.get('frequency_ghz') != 15 or value.get('label_mode') != 'STRICT_LUMPED'
            or value.get('selection_basis') != 'VALIDATION_ONLY'
            or type(value.get('allow_extrapolation')) is not bool
            or not isinstance(value.get('model_id'), str) or not value['model_id']):
        raise ValueError('Explicit FINAL data/model declaration required; historical/reference/development is not FINAL')
    if set(value.get('pins', {})) != set(PIN_NAMES):
        raise ValueError('Exact model, normalizer, data/split and validation-selection pins required')
    for name in PIN_NAMES:
        check(value['pins'][name])
    return identity, value


def load_pair(value):
    from .frequency_tandem import load_frequency_pair
    forward, inverse, fs, ins = load_frequency_pair(value['pins']['forward']['path'],
        value['pins']['inverse']['path'], frequency_ghz=15, label_mode='STRICT_LUMPED', device='cpu')
    norm = read_json(value['pins']['normalizer']['path'])
    geom = read_json(value['pins']['geometry_contract']['path'])
    for state in (fs, ins):
        if (state['data_sha'] != value['pins']['dataset']['sha256']
                or state['data_manifest_sha'] != value['pins']['data_manifest']['sha256']
                or state['normalizer_sha'] != canonical_sha(norm)
                or state['contract_sha'] != canonical_sha(geom)
                or state['model_sha'] != state['best_model_sha']
                or state.get('resume_probe') or state.get('research_comparison_eligible') is False
                or state['step'] < 1):
            raise ValueError('Loaded validation-best pair differs from FINAL data/normalizer/geometry identity')
        for filename, expected in state['runtime_source_sha256'].items():
            # This is the existing trainer/runtime source contract, not a new approval chain.
            if sha256(Path(__file__).with_name(filename)) != expected:
                raise ValueError('Frozen training runtime differs: ' + filename)
    if norm['field_names'] != geom['field_names']:
        raise ValueError('Normalizer geometry field order differs')
    return forward, inverse, geom['field_names']


def _draw(study_id, seed, audit_seed, count, audit_count):
    """Small sizes are used only by synthetic unit tests; public prepare fixes10K/100."""
    if not isinstance(study_id, str) or not re.fullmatch('[A-Za-z0-9_-]+', study_id):
        raise ValueError('Safe nonempty study_id required')
    if any(type(x) is not int or x < 0 for x in (seed, audit_seed)) or seed == audit_seed:
        raise ValueError('Two distinct explicit nonnegative integer seeds required')
    if not (type(count) is int and type(audit_count) is int and 0 < audit_count <= count):
        raise ValueError('Positive audit subset within request count required')
    values = np.random.Generator(np.random.PCG64(seed)).uniform(LOW, HIGH, size=(count, 3))
    if len({tuple(row) for row in values}) != count:
        raise ValueError('Duplicate target draw preserved as failure; no resampling')
    audit_indices = np.random.Generator(np.random.PCG64(audit_seed)).choice(count, audit_count, replace=False).tolist()
    order = {index: i for i, index in enumerate(audit_indices)}
    requests = [dict(request_id=f'{study_id}-UNIFORM_TRIPLE-{i:06d}',
        target_source='FINAL_UNIFORM_TRIPLE', request_order=i, frequency_ghz=15,
        lp_nh=float(row[0]), ls_nh=float(row[1]), k_abs=float(row[2]), seed=seed,
        full11_audit=i in order, audit_order=order.get(i), preselected_emx=True,
        target_feasibility='UNKNOWN_FOR_INTEGER_Q_SCAN') for i, row in enumerate(values)]
    return requests, [requests[index]['request_id'] for index in audit_indices]


def _jsonl(path, rows):
    with Path(path).open('x', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n')


def _output(path):
    path = Path(path).absolute()
    if path.exists() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('New nonsymlink no-clobber output required')
    path = path.resolve()
    if path.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError('Research outputs must be outside the source worktree')
    return path


def _source_pins():
    values = {name: pin(Path(__file__).with_name(name)) for name in (
        'eucap15_final_frame.py', 'eucap15_final_routing.py', 'frequency_qscan.py',
        'frequency_tandem.py', 'frequency_evaluation.py', 'frequency_large_eval.py',
        'bb00.py', 'evaluation.py', 'seven_evaluation.py', 'io.py',
        'eucap15_prepare_acquisition.py')}
    for name in ('rfic_transformer_inverse_design/synthesis/q_sweep.py',
                 'rfic_transformer_inverse_design/campaigns/broadband56_balanced200k.py'):
        values[name] = pin(Path(__file__).resolve().parents[2] / name)
    return values


def _resources(path):
    from .eucap15_prepare_acquisition import memory_headroom
    path = Path(path)
    while not path.exists():
        path = path.parent
    disk = shutil.disk_usage(path)
    memory = memory_headroom()
    if disk.free < 2*1024**3 or not memory['passed']:
        raise RuntimeError('Bounded CPU inference requires2GiB disk and existing RAM headroom check')
    return dict(memory=memory, disk_free_bytes=disk.free, device='cpu', threads=2,
                platform=platform.platform(), python=sys.version, numpy=np.__version__)


def prepare(model_freeze, out, *, study_id, seed, audit_seed):
    out = _output(out)
    refpin, model = final_identity(model_freeze)
    resources = _resources(out.parent)
    # No target draws before the final pair is verifiably loadable and bound.
    import torch
    torch.set_num_threads(2)
    forward, inverse, fields = load_pair(model)
    del forward, inverse
    protocol = _protocol()
    sources = _source_pins()
    out.mkdir(parents=True, exist_ok=False)
    save_json(out / 'RESOURCES.json', resources)
    save_json(out / 'PREPARATION_INTENT.json', dict(model_freeze=refpin, study_id=study_id,
        seed=seed, audit_seed=audit_seed, created_utc=utc_now(), protocol=protocol,
        partial_output_policy='PRESERVE_NO_IMPLICIT_REDRAW', native_allowed=False))
    requests, audit_ids = _draw(study_id, seed, audit_seed, N_REQUESTS, N_AUDIT)
    _jsonl(out / 'requests.jsonl', requests)
    save_json(out / 'audit_request_ids.json', audit_ids)
    frame = dict(schema='eucap15_final_target_frame.v1',
        status='FROZEN_BEFORE_INFERENCE_AND_ANY_NATIVE', created_utc=utc_now(),
        model_freeze=refpin, model_id=model['model_id'], study_id=study_id,
        seed=seed, audit_seed=audit_seed, numpy_version=np.__version__,
        protocol=protocol, geometry_fields=fields, sources=sources,
        requests=pin(out / 'requests.jsonl'), audit_ids=pin(out / 'audit_request_ids.json'),
        N_requests=len(requests), N_audit_requests=len(audit_ids), REAL_EMX_VALIDATION='NOT_RUN',
        dataset_labels_loaded=False, native_dispatch_installed=False)
    save_json(out / 'FRAME.json', frame)
    save_json(out / 'PREPARATION_RECEIPT.json', dict(status='FINAL_FRAME_FROZEN_NOT_INFERRED_NOT_DISPATCHED',
        frame=pin(out / 'FRAME.json'), pair_load_verified=True, optimizer_updates=0,
        native_started=0, random_targets_generated=len(requests), audits_frozen=len(audit_ids)))
    return frame


def read_frame(path):
    """Verify frozen bytes once per invocation; never regenerate RNG for identity."""
    frame_pin = pin(path)
    frame = read_json(path)
    if (frame.get('schema') != 'eucap15_final_target_frame.v1'
            or frame.get('status') != 'FROZEN_BEFORE_INFERENCE_AND_ANY_NATIVE'
            or frame.get('protocol') != _protocol()
            or frame.get('N_requests') != N_REQUESTS or frame.get('N_audit_requests') != N_AUDIT):
        raise ValueError('Wrong FINAL frozen frame contract/count')
    for value in frame['sources'].values():
        check(value)
    if frame['sources'] != _source_pins():
        raise ValueError('Use the frozen implementation; do not switch source paths or versions')
    check(frame['model_freeze'])
    _, model = final_identity(frame['model_freeze']['path'])
    if frame['model_id'] != model['model_id']:
        raise ValueError('Frozen model id differs')
    for key in ('requests', 'audit_ids'):
        check(frame[key])
    requests = [json.loads(line) for line in Path(frame['requests']['path']).read_text().splitlines()]
    audit_ids = read_json(frame['audit_ids']['path'])
    if len(requests) != N_REQUESTS or len(audit_ids) != N_AUDIT or len(set(audit_ids)) != N_AUDIT:
        raise ValueError('Frozen request/audit denominator changed')
    ids = [row['request_id'] for row in requests]
    if len(set(ids)) != N_REQUESTS or not set(audit_ids) <= set(ids):
        raise ValueError('Duplicate or foreign frozen audit request')
    audit_order = {value: i for i, value in enumerate(audit_ids)}
    triples = set()
    for index, row in enumerate(requests):
        triple = (row['lp_nh'], row['ls_nh'], row['k_abs'])
        if (row['request_order'] != index or row['frequency_ghz'] != 15
                or row['target_source'] != 'FINAL_UNIFORM_TRIPLE'
                or row['full11_audit'] is not (row['request_id'] in audit_order)
                or row['audit_order'] != audit_order.get(row['request_id'])
                or any(type(v) not in (int, float) or not np.isfinite(v) or not lo <= v <= hi
                       for v, lo, hi in zip(triple, LOW, HIGH))):
            raise ValueError('Frozen row scope/range/order/audit identity differs')
        triples.add(triple)
    if len(triples) != N_REQUESTS:
        raise ValueError('Duplicate frozen target triples')
    return frame_pin, frame, model, requests


def _no_symlink(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink inference output forbidden')


def _route_counts(routes):
    return dict(main_requests=len(routes), selected_main=sum(x['q_proxy'] is not None for x in routes),
        audit_requests=sum(x['full11_audit'] for x in routes),
        audit_slots=sum(x['N_original_audit_slots'] for x in routes),
        unique_candidate_slots=sum(x['N_unique_logical_candidates'] for x in routes),
        additional_audit_slots=sum(x['additional_audit_slots'] for x in routes),
        missing_main_selection=sum(x['q_proxy'] is None for x in routes))


def _committed(path, directory, batch, frame_pin):
    receipt = read_json(path)
    names = ('records.jsonl', 'ROUTES.json', 'INFERENCE_FAILURES.json', 'INTENT.json')
    ids = [r['request_id'] for r in batch]
    if (receipt.get('schema') != 'eucap15_final_inference_shard.v1'
            or receipt.get('status') != 'COMMITTED_PROXY_ONLY'
            or receipt.get('frame') != frame_pin or receipt.get('request_ids') != ids
            or set(receipt.get('artifacts', {})) != set(names)
            or receipt.get('logical_proxy_candidates') != 11*len(batch)
            or type(receipt.get('native_started')) is not int or receipt['native_started'] != 0
            or receipt.get('REAL_EMX_VALIDATION') != 'NOT_RUN'):
        raise ValueError('Committed shard scope/count/artifact set differs')
    for name in names:
        artifact = receipt['artifacts'][name]
        if artifact.get('path') != str(directory / name):
            raise ValueError('Shard artifact must bind its own exact path')
        check(artifact)
    intent = read_json(directory / 'INTENT.json')
    if intent['frame'] != frame_pin or intent['request_ids'] != ids or intent['native_allowed'] is not False:
        raise ValueError('Committed shard intent differs')
    records = [json.loads(line) for line in (directory / 'records.jsonl').read_text().splitlines()]
    routes = read_json(directory / 'ROUTES.json')
    if (len(records) != 11*len(batch) or len(routes) != len(batch)
            or [r['request_id'] for r in routes] != ids):
        raise ValueError('Committed shard logical rows differ')
    for i, req in enumerate(batch):
        rows = records[i*11:(i+1)*11]
        if ([r['q_target'] for r in rows] != list(Q_VALUES)
                or any(r['request_id'] != req['request_id'] or
                       r['candidate_id'] != f"{req['request_id']}-q{r['q_target']:02d}" for r in rows)
                or routes[i]['full11_audit'] is not req['full11_audit']):
            raise ValueError('Committed request-Q or frozen audit identity differs')
        by_id = {r['candidate_id']: r for r in rows}
        for item in routes[i]['unique_candidates']:
            if item['original_record'] != by_id.get(item['candidate_id']):
                raise ValueError('Route does not reference the identical recorded candidate')
    counts = _route_counts(routes)
    if receipt.get('route_counts') != counts:
        raise ValueError('Committed route accounting differs')
    return pin(path), counts


def run(frame_path, *, max_new_batches=None):
    """Resume committed32-request shards; preserve partials, never dispatch native.

    All frozen q_proxy and audit routes are written before this command reports
    INFERENCE_COMPLETE. The existing native owner must explicitly consume that
    receipt; old pilot64 and full11 physical queues are never invoked here.
    """
    if max_new_batches is not None and (type(max_new_batches) is not int or max_new_batches < 0):
        raise ValueError('Nonnegative max_new_batches or null required')
    frame_pin, frame, model, requests = read_frame(frame_path)
    root = Path(frame_path).absolute().parent / 'inference'
    _no_symlink(root)
    _no_symlink(root / 'inference.lock')
    _no_symlink(root / 'INFERENCE_COMPLETE.json')
    root.mkdir(exist_ok=True)
    with (root / 'inference.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipts, counts, todo = [], [], []
        for start in range(0, N_REQUESTS, 32):
            directory = root / f'batch_{start:06d}'
            _no_symlink(directory)
            receipt_path = directory / 'RECEIPT.json'
            batch = requests[start:start+32]
            if receipt_path.exists():
                rp, rc = _committed(receipt_path, directory, batch, frame_pin)
                receipts.append(rp); counts.append(rc)
            elif directory.exists():
                raise ValueError('Partial shard preserved; inspect it before any explicit recovery: ' + str(directory))
            else:
                todo.append((directory, batch))
        if max_new_batches is not None:
            todo = todo[:max_new_batches]
        if todo:
            import torch
            from .eucap15_final_routing import route_request
            torch.set_num_threads(2)
            resources = _resources(root)
            forward, inverse, fields = load_pair(model)
            if fields != frame['geometry_fields']:
                raise ValueError('Frozen geometry field order differs from loaded pair')
            context = dict(model_id=frame['model_id'], frequency_ghz=15,
                config=dict(allow_extrapolation=model['allow_extrapolation'], dataset_scope='FINAL_FROZEN_DATASET'),
                artifacts={'normalizer.json': model['pins']['normalizer'],
                           'geometry_contract.json': model['pins']['geometry_contract']})
            for directory, batch in todo:
                directory.mkdir(exist_ok=False)
                save_json(directory / 'INTENT.json', dict(frame=frame_pin,
                    request_ids=[r['request_id'] for r in batch], started_utc=utc_now(),
                    resources=resources, native_allowed=False))
                records, _, failures = _batch(batch, context, forward, inverse)
                if len(records) != 11*len(batch):
                    raise ValueError('Q-scan changed logical candidate denominator')
                routes = [route_request(req, records[i*11:(i+1)*11], frame['model_id'], fields)
                          for i, req in enumerate(batch)]
                _jsonl(directory / 'records.jsonl', records)
                save_json(directory / 'ROUTES.json', routes)
                save_json(directory / 'INFERENCE_FAILURES.json', failures)
                rc = _route_counts(routes)
                save_json(directory / 'RECEIPT.json', dict(schema='eucap15_final_inference_shard.v1',
                    status='COMMITTED_PROXY_ONLY', frame=frame_pin, route_counts=rc,
                    request_ids=[r['request_id'] for r in batch], completed_utc=utc_now(),
                    artifacts={name: pin(directory / name) for name in
                               ('records.jsonl', 'ROUTES.json', 'INFERENCE_FAILURES.json', 'INTENT.json')},
                    logical_proxy_candidates=11*len(batch), native_started=0, REAL_EMX_VALIDATION='NOT_RUN'))
                receipts.append(pin(directory / 'RECEIPT.json'))
                counts.append(rc)
        complete = len(receipts) == (N_REQUESTS+31)//32
        totals = {key: sum(row[key] for row in counts) for key in _route_counts([])}
        if complete and (totals['main_requests'] != N_REQUESTS or totals['audit_requests'] != N_AUDIT
                         or totals['audit_slots'] != 11*N_AUDIT):
            raise ValueError('Completed frame/audit denominators differ')
        result = dict(status='INFERENCE_COMPLETE_NOT_NATIVE_RELEASE' if complete else 'INFERENCE_PARTIAL_RESUMABLE',
            frame=frame_pin, shards=sorted(receipts, key=lambda row: row['path']),
            committed_batches=len(receipts), new_batches=len(todo), native_started=0,
            logical_counts=totals, additional_audit_budget=1000,
            additional_audit_slots_within_budget=totals['additional_audit_slots'] <= 1000,
            actual_geometry_deduplication='NOT_PERFORMED_NO_CROSS_REQUEST_REUSE_CLAIM',
            native_owner_action='Explicitly bind FINAL main/audit routes; existing pilot64 schemas are not compatible',
            REAL_EMX_VALIDATION='NOT_RUN', physical_error=None)
        final_path = root / 'INFERENCE_COMPLETE.json'
        if complete and not final_path.exists():
            save_json(final_path, result)
        elif complete:
            previous = read_json(final_path)
            # A resumed invocation has zero new batches; all scientific counts,
            # identities and status in the original terminal receipt must agree.
            if ({k: v for k, v in previous.items() if k != 'new_batches'} !=
                    {k: v for k, v in result.items() if k != 'new_batches'}):
                raise ValueError('Completed inference receipt changed')
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='Only after actual FINAL model/data freeze; generates10K/100 once')
    p.add_argument('--model-freeze', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--study-id', required=True)
    p.add_argument('--seed', type=int, required=True)
    p.add_argument('--audit-seed', type=int, required=True)
    p = sub.add_parser('run', help='Resume inference only, no native simulator')
    p.add_argument('--frame', type=Path, required=True)
    p.add_argument('--max-new-batches', type=int)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        result = prepare(args.model_freeze, args.out, study_id=args.study_id,
                         seed=args.seed, audit_seed=args.audit_seed)
    else:
        result = run(args.frame, max_new_batches=args.max_new_batches)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
