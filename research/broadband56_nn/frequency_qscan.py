"""Frozen three-input Q10..20 scans for current Torch frequency pairs.

Reuses the original Q-sweep grid/score and current frequency model loader.
This is inference and candidate preparation, never a physical solver or trainer.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import fcntl
import json
from pathlib import Path

import numpy as np

from rfic_transformer_inverse_design.synthesis.q_sweep import Q_SWEEP_VALUES, DECLARED_FEATURE_SPANS
from .io import read_json, save_json, sha256, utc_now, canonical_sha
from .frequency_evaluation import pin, verify_pin, _predict, FEATURES
from .frequency_large_eval import (_load_context, _csv, _read_csv, _jsonl,
    _resumable_json, _geometry_hash, _geometry_status, _float)
from .evaluation import _grid_from_contract, _grid_geometry

SCALE = np.asarray(DECLARED_FEATURE_SPANS, dtype=float)
TAU = .05 * SCALE
Q_VALUES = tuple(Q_SWEEP_VALUES)


def q_targets(triple):
    triple = np.asarray(triple, dtype=float)
    if triple.shape != (3,) or not np.isfinite(triple).all():
        raise ValueError('finite [Lp_nH,Ls_nH,K_abs] required')
    return np.column_stack((np.repeat(triple[0], 11), np.repeat(triple[1], 11),
                            np.asarray(Q_VALUES), np.repeat(triple[2], 11)))


def score(observed, target):
    observed, target = np.asarray(observed, float), np.asarray(target, float)
    if observed.shape != target.shape or observed.shape[-1] != 4:
        raise ValueError('paired four-feature responses required')
    return np.sqrt(np.mean(((observed-target)/SCALE)**2, axis=-1))


def select_q(scores):
    scores = np.asarray(scores, float)
    if scores.shape != (11,):
        raise ValueError('exact eleven scores required')
    finite = np.flatnonzero(np.isfinite(scores))
    best = int(Q_VALUES[min(finite, key=lambda j: (scores[j], Q_VALUES[j]))]) if len(finite) else None
    return {'q_proxy': best if len(finite) == 11 else None,
            'best_available_proxy': best, 'N_proxy_finite': int(len(finite)),
            'full_proxy_scan': len(finite) == 11}


def lhs3(low, high, n, seed):
    rng = np.random.default_rng(seed)
    unit = np.column_stack([(rng.permutation(n)+rng.random(n))/n for _ in range(3)])
    return np.asarray(low)+(np.asarray(high)-np.asarray(low))*unit


def prepare(config, out):
    config = read_json(config) if not isinstance(config, dict) else dict(config)
    if config.get('schema') != 'frequency_qscan_request.v1':
        raise ValueError('explicit Q-scan request required')
    if config.get('allow_extrapolation') not in (True, False):
        raise ValueError('explicit research extrapolation policy required')
    if config['random_count'] != 10000 or not 1 <= config['holdout_count'] <= 100:
        raise ValueError('10000 random requests and 1..100 holdout audit requests required')
    if config['batch_requests'] < 1 or config['batch_requests'] > 32:
        raise ValueError('bounded batch of 1..32 requests required')
    if not 5 <= config['frequency_ghz'] <= 20 or config['label_mode'] != 'STRICT_LUMPED':
        raise ValueError('current scope is exact integer 5..20 GHz STRICT_LUMPED')
    out = Path(out)
    if out.exists():
        raise FileExistsError(out)
    bundle, fs, ins, norm, train, test, at, exposure = _load_context(config)
    if config['dataset_scope']=='FORMAL_10K' and len(bundle.arrays['geometry'])!=10000:
        raise ValueError('FORMAL_10K requires actual 10000 unique geometries')
    ytrain = np.asarray(bundle.arrays['y'][train, at], float)
    low, high = np.quantile(ytrain[:, [0, 1, 3]], [.01, .99], axis=0, method='linear')
    rng = np.random.default_rng(config['seed'])
    chosen = rng.permutation(test)[:min(config['holdout_count'], len(test))]
    random_values = lhs3(low, high, config['random_count'], config['seed']+1)
    random_audit = set(np.random.default_rng(config['seed']+2).choice(10000, 20, replace=False).tolist())
    requests = []
    for source, values in (('HELDOUT_TRIPLE_AUDIT', bundle.arrays['y'][chosen, at][:, [0, 1, 3]]),
                           ('RANDOM_LHS_TRIPLE', random_values)):
        for i, triple in enumerate(values):
            heldout = source == 'HELDOUT_TRIPLE_AUDIT'
            requests.append(dict(request_id=f"{config['study_id']}-{source}-{i:06d}",
                target_source=source, source_geometry_id=str(bundle.arrays['geometry_ids'][chosen[i]]) if heldout else '',
                source_geometry_sha256=str(bundle.arrays['geometry_sha256'][chosen[i]]) if heldout else '',
                lp_nh=float(triple[0]), ls_nh=float(triple[1]), k_abs=float(triple[2]),
                seed=config['seed'] if heldout else config['seed']+1, request_order=i,
                preselected_emx=(i < 20 if heldout else i in random_audit),
                target_feasibility='UNKNOWN_FOR_INTEGER_Q_SCAN', frequency_ghz=config['frequency_ghz']))
    if len({r['request_id'] for r in requests}) != len(requests):
        raise ValueError('duplicate request IDs')
    identity = {name: pin(Path(config['data_root'])/filename) for name, filename in
                (('dataset','dataset.npz'), ('data_manifest','data_manifest.json'), ('split','splits.json'))}
    identity.update(forward=pin(config['forward_checkpoint']), inverse=pin(config['inverse_checkpoint']),
                    normalizer_sha256=canonical_sha(norm), contract_sha256=canonical_sha(fs['contract']))
    out.mkdir(parents=True, exist_ok=False)
    _csv(out/'requests.csv', requests)
    save_json(out/'normalizer.json', norm)
    save_json(out/'geometry_contract.json', fs['contract'])
    freeze = dict(schema='frequency_qscan_freeze.v1', status='FROZEN_BEFORE_QSCAN_AND_NEW_EMX',
        created_utc=utc_now(), config=config, identity=identity, source_geometries=len(bundle.arrays['geometry']),
        frequency_exposure=exposure, frequency_ghz=config['frequency_ghz'],
        train_seed=ins['train_config']['seed'], model_id=f"f{config['frequency_ghz']}-{identity['inverse']['sha256'][:12]}",
        train_window=dict(source='ELIGIBLE_TRAIN_ONLY', n_train=len(train), p01=low.tolist(), p99=high.tolist(),
                          quantile_method='linear', dimensions=['Lp_nH','Ls_nH','K_abs'], joint_feasibility='UNKNOWN'),
        protocol=dict(q_values=list(Q_VALUES), score_scale=SCALE.tolist(), absolute_tolerances=TAU.tolist(),
            score='sqrt(mean(((observed-target(q))/score_scale)**2))', tie_break='exact tie selects smaller q',
            tolerance_definition='0.05_X_FIXED_DECLARED_SPAN_NOT_TARGET_RELATIVE_PERCENT',
            q_scalar='min(Qp,Qs)', selection_geometry='grid_only_not_layout_audited',
            no_target_clipping=True, physical_optimum_requires='11/11 valid exact fresh EMX candidates',
            random_sampling='single randomized LHS; no IID confidence interval',
            hypothesis='three-input Q scan, not previous four-target one-shot study'),
        artifacts={name:pin(out/name) for name in ('requests.csv','normalizer.json','geometry_contract.json')},
        implementation={name:pin(Path(__file__).with_name(name)) for name in
                        ('frequency_qscan.py','frequency_tandem.py','bb00.py','evaluation.py','seven_evaluation.py')},
        original_q_sweep_source=pin(Path(__file__).resolve().parents[2]/'rfic_transformer_inverse_design/synthesis/q_sweep.py'),
        original_reference_AB='previously evaluated; no Q-scan or fresh EMX outcome used for selection',
        model_prediction_calls=0, REAL_EMX_VALIDATION='NOT_RUN')
    save_json(out/'QSCAN_FREEZE.json', freeze)
    return freeze


def _verify(out):
    freeze = read_json(out/'QSCAN_FREEZE.json')
    for group in ('artifacts','implementation'):
        for value in freeze[group].values(): verify_pin(value)
    for value in freeze['identity'].values():
        if isinstance(value, dict): verify_pin(value)
    verify_pin(freeze['original_q_sweep_source'])
    return freeze


def _batch(requests, freeze, forward, inverse):
    norm = read_json(freeze['artifacts']['normalizer.json']['path'])
    contract = read_json(freeze['artifacts']['geometry_contract.json']['path'])
    targets = np.vstack([q_targets([r['lp_nh'], r['ls_nh'], r['k_abs']]) for r in requests])
    outside = ((targets < norm['train_support_min']) | (targets > norm['train_support_max'])).any(1)
    allowed = ~outside | freeze['config']['allow_extrapolation']
    raw = np.full((len(targets),len(contract['field_names'])),np.nan)
    failures = {}
    if allowed.any(): raw[allowed], failures['inverse'] = _predict(inverse,targets[allowed],raw.shape[1],'cpu',352)
    grid = np.full_like(raw,np.nan)
    finite_g = np.isfinite(raw).all(1)
    grid[finite_g] = _grid_geometry(raw[finite_g],_grid_from_contract(contract))
    raw_pred, failures['raw_forward'] = _predict(forward,raw,4,'cpu',352)
    grid_pred, failures['grid_forward'] = _predict(forward,grid,4,'cpu',352)
    _, raw_analytic = _geometry_status(raw,contract)
    _, grid_analytic = _geometry_status(grid,contract)
    candidates, summaries = [], []
    for i, request in enumerate(requests):
        indices = list(range(i*11,(i+1)*11))
        scores = score(grid_pred[indices], targets[indices])
        selected = select_q(scores)
        candidates_request = []
        for j, q in zip(indices,Q_VALUES):
            geometry_hash = _geometry_hash(grid[j],contract['field_names'])
            record = dict(request_id=request['request_id'], target_source=request['target_source'],
                frequency_ghz=freeze['frequency_ghz'], dataset_scope=freeze['config']['dataset_scope'],
                model_id=freeze['model_id'], q_target=q, candidate_id=f"{request['request_id']}-q{q:02d}",
                parameter_geometry_hash=geometry_hash, parameter_identity_not_actual_gds=True,
                target=[_float(v) for v in targets[j]], continuous_geometry=[_float(v) for v in raw[j]],
                grid_geometry=[_float(v) for v in grid[j]], geometry_fields=contract['field_names'],
                raw_proxy=[_float(v) for v in raw_pred[j]], grid_proxy=[_float(v) for v in grid_pred[j]],
                grid_proxy_score=_float(scores[q-10]), q_proxy=selected['q_proxy'],
                proxy_preselected=q==selected['q_proxy'], support_status='EXTRAPOLATION' if outside[j] else 'IN_TRAIN_MARGINAL_SUPPORT_JOINT_UNKNOWN',
                inference_status='REJECTED_OUTSIDE_SUPPORT' if not allowed[j] else 'FINITE' if np.isfinite(grid_pred[j]).all() else 'NONFINITE',
                analytic_raw=bool(raw_analytic[j]), analytic_grid=bool(grid_analytic[j]),
                within_tolerance=[bool(v) for v in (np.abs(grid_pred[j]-targets[j])<=TAU)],
                evidence_source='SELF_PROXY', actual_gds_geometry=None, gds_sha256=None,
                cadence_status='NOT_RUN', calibre_blocking_count=None, emx_status='NOT_RUN', s4p_sha256=None,
                actual_response=None, emx_minus_target=None, emx_minus_proxy=None,
                unique_emx_solve=False, preselected_emx=str(request['preselected_emx']) in ('True','true'))
            candidates_request.append(record)
        chosen = next((r for r in candidates_request if r['proxy_preselected']), None)
        summaries.append(dict(**request, **selected, model_id=freeze['model_id'],
            q_emx=None, best_available_emx=None, selection_gap=None, REAL_EMX_VALIDATION='NOT_RUN',
            complete_fresh_emx_candidates=0, independent_solves=0, physical_status='FULL_SWEEP_UNVERIFIED',
            selected_error=[_float(v) for v in (np.asarray(chosen['grid_proxy'])-np.asarray(chosen['target']))] if chosen else None,
            selected_support=chosen['support_status'] if chosen else 'NOT_SELECTED',
            selected_joint_response_hit=all(chosen['within_tolerance']) if chosen else False,
            selected_analytic_pass=chosen['analytic_grid'] if chosen else False,
            existence_response_hit=any(all(r['within_tolerance']) for r in candidates_request),
            logical_candidate_count=11))
        candidates.extend(candidates_request)
    return candidates,summaries,failures


def _physical_queue(out, candidates):
    groups = {}
    for row in candidates:
        if row['preselected_emx']: groups.setdefault(row['request_id'],[]).append(row)
    for request_id, rows in groups.items():
        if sorted(r['q_target'] for r in rows) != list(Q_VALUES): raise ValueError('incomplete logical Q request')
        dest = out/'physical_pending'/request_id
        dest.mkdir(parents=True,exist_ok=True)
        _jsonl(dest/'eleven_candidates.jsonl',rows,allow_identical=True)
        csv_rows=[]
        for row in rows:
            item={'candidate_id':row['candidate_id'], 'request_id':request_id, 'q_target':row['q_target'],
                  'parameter_geometry_hash':row['parameter_geometry_hash']}
            item.update({'geom__'+field:value for field,value in zip(row['geometry_fields'],row['grid_geometry'])})
            csv_rows.append(item)
        _csv(dest/'candidate_geometry.csv',csv_rows,allow_identical=True)
        _resumable_json(dest/'PENDING_REQUEST.json',dict(schema='frequency_qscan_physical_pending.v1',
            request_id=request_id,q_proxy=rows[0]['q_proxy'],N_logical_candidates=11,N_independent_solves=0,
            status='NOT_DISPATCHED',REAL_EMX_VALIDATION='NOT_RUN',
            candidate_records=pin(dest/'eleven_candidates.jsonl'),candidate_csv=pin(dest/'candidate_geometry.csv'),
            q_proxy_frozen_before_any_new_emx=True, output_geometry='GRID_PARAMETERS_NOT_ACTUAL_GDS',
            required='actual GDS identity/foundry audit/Calibre zero blocking/resource admission before exact56 EMX'))


def run(out, *, max_new_batches=None):
    out=Path(out).resolve(strict=True)
    with (out/'qscan.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        freeze=_verify(out)
        if (out/'QSCAN_SUMMARY.json').exists():
            final=read_json(out/'QSCAN_SUMMARY.json')
            for value in final['shards']: verify_pin(value)
            return final
        requests=_read_csv(out/'requests.csv'); receipts=[]; todo=[]
        for source in ('HELDOUT_TRIPLE_AUDIT','RANDOM_LHS_TRIPLE'):
            selected=[r for r in requests if r['target_source']==source]
            for start in range(0,len(selected),freeze['config']['batch_requests']):
                batch=selected[start:start+freeze['config']['batch_requests']]
                root=out/'shards'/source/f'batch_{start:06d}'
                committed=list(root.glob('attempt_*/SHARD_RECEIPT.json'))
                if len(committed)>1: raise ValueError('duplicate committed Q-scan shard')
                if committed:
                    r=read_json(committed[0])
                    if r['request_ids']!=[b['request_id'] for b in batch] or r['freeze']['sha256']!=sha256(out/'QSCAN_FREEZE.json'):
                        raise ValueError('Q-scan shard identity differs')
                    for value in r['artifacts'].values(): verify_pin(value)
                    receipts.append(committed[0])
                else: todo.append((root,batch))
        if todo:
            import torch
            from .frequency_tandem import load_frequency_pair
            torch.set_num_threads(2)
            forward,inverse,_,_=load_frequency_pair(freeze['identity']['forward']['path'],freeze['identity']['inverse']['path'],
                frequency_ghz=freeze['frequency_ghz'],label_mode='STRICT_LUMPED',device='cpu')
            for number,(root,batch) in enumerate(todo):
                if max_new_batches is not None and number>=max_new_batches:
                    return dict(status='PARTIAL_SHARDS_COMMITTED',completed_shards=len(receipts),remaining_shards=len(todo)-number)
                root.mkdir(parents=True,exist_ok=True)
                attempt=root/f"attempt_{len(list(root.glob('attempt_*')))+1:04d}"
                attempt.mkdir(exist_ok=False)
                save_json(attempt/'INTENT.json',dict(created_utc=utc_now(),request_ids=[r['request_id'] for r in batch],freeze=pin(out/'QSCAN_FREEZE.json')))
                candidates,summaries,failures=_batch(batch,freeze,forward,inverse)
                _jsonl(attempt/'candidates.jsonl',candidates)
                _jsonl(attempt/'requests.jsonl',summaries)
                save_json(attempt/'INFERENCE_FAILURES.json',failures)
                _physical_queue(out,candidates)
                save_json(attempt/'SHARD_RECEIPT.json',dict(schema='frequency_qscan_shard.v1',status='COMPLETE',created_utc=utc_now(),
                    request_ids=[r['request_id'] for r in batch],freeze=pin(out/'QSCAN_FREEZE.json'),
                    artifacts={n:pin(attempt/n) for n in ('INTENT.json','candidates.jsonl','requests.jsonl','INFERENCE_FAILURES.json')}))
                receipts.append(attempt/'SHARD_RECEIPT.json')
        summaries=[]
        for path in sorted(receipts):
            p=read_json(path)['artifacts']['requests.jsonl']['path']
            with Path(p).open() as stream: summaries.extend(json.loads(line) for line in stream)
        groups=[]
        for source in ('HELDOUT_TRIPLE_AUDIT','RANDOM_LHS_TRIPLE'):
            rows=[r for r in summaries if r['target_source']==source]
            by_support=sorted(set(r['selected_support'] for r in rows))
            for support in ['ALL_REQUESTS']+by_support:
                group=rows if support=='ALL_REQUESTS' else [r for r in rows if r['selected_support']==support]
                errors=np.asarray([r['selected_error'] for r in group if r['selected_error'] is not None],float)
                metrics={}
                for j,f in enumerate(FEATURES):
                    e=errors[:,j] if errors.size else np.asarray([])
                    metrics[f]=dict(N_finite=len(e),Bias=float(e.mean()) if len(e) else None,
                        MAE=float(np.abs(e).mean()) if len(e) else None,RMSE=float(np.sqrt((e*e).mean())) if len(e) else None,
                        **{f'P{int(q*100)}':float(np.quantile(np.abs(e),q,method='linear')) if len(e) else None for q in (.5,.9,.95)})
                groups.append(dict(target_source=source,selected_support=support,N_requested=len(group),
                    N_complete_proxy=sum(r['full_proxy_scan'] for r in group),N_failed_proxy=sum(not r['full_proxy_scan'] for r in group),
                    joint_response_hit=sum(r['selected_joint_response_hit'] for r in group),
                    analytic_pass=sum(r['selected_analytic_pass'] for r in group),
                    end_to_end_proxy_hit=sum(r['selected_joint_response_hit'] and r['selected_analytic_pass'] for r in group),
                    q_proxy_distribution=dict(Counter(str(r['q_proxy']) for r in group)),features=metrics,
                    N_complete_emx_requests=0,N_independent_solves=0,physical_accuracy='NOT_MEASURED'))
        result=dict(schema='frequency_qscan_summary.v1',status='PROXY_COMPLETE_EMX_NOT_RUN',created_utc=utc_now(),
            frequency_ghz=freeze['frequency_ghz'],dataset_scope=freeze['config']['dataset_scope'],
            model_id=freeze['model_id'],groups=groups,N_requests=len(requests),N_logical_candidates=len(requests)*11,
            REAL_EMX_VALIDATION='NOT_RUN',confidence_intervals='NOT_ESTIMATED_SINGLE_RANDOMIZED_LHS_AND_FIXED_REQUEST_FRAME',
            freeze=pin(out/'QSCAN_FREEZE.json'),shards=[pin(p) for p in sorted(receipts)])
        save_json(out/'QSCAN_SUMMARY.json',result)
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='action',required=True)
    p=subs.add_parser('prepare');p.add_argument('--config',required=True);p.add_argument('--out',required=True)
    p=subs.add_parser('run');p.add_argument('--out',required=True);p.add_argument('--max-new-batches',type=int)
    args=parser.parse_args()
    result=prepare(args.config,args.out) if args.action=='prepare' else run(args.out,max_new_batches=args.max_new_batches)
    print(json.dumps({k:result[k] for k in ('status','N_requests','N_logical_candidates','completed_shards','remaining_shards') if k in result}))


if __name__=='__main__':main()
