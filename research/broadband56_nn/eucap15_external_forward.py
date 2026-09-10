"""Fixed S15 forward on prior original64 strict survivors; no new native evidence.

prepare_inputs(spec, documents) accepts decoded JSON objects / CSV-row lists
whose source pins the caller has verified. It performs no file or model reads.
run is restricted to the actual pre-prediction spec, not an alternative FINAL
evaluation. Nontrain source labels/geometry are never numerically converted.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import traceback

from .eucap15_author_sources import load_json, no_symlinks, put_csv, put_json
from .io import canonical_sha

FEATURES = ('Lp_nH', 'Ls_nH', 'Qmin', 'K_abs')
UNITS = ('nH', 'nH', 'dimensionless', 'dimensionless')
SPANS = (2.5, 2.5, 20.0, .8)
MODEL = dict(role='forward', seed=17, step=14300, widths=[10, 256, 256, 256, 4])
SPEC_SHA = 'b2c1807007514cc2680531e783738ebbe26358256795a89db756432ec839eed2'
DATA_SHA = '4dc6e91a644db2aca0a580958ac8b31e1e26ed73497b935145202b07227ef580'
CSV_SOURCES = {'source_rows', 'exclusions', 'physical_rows', 'selected_geometry'}
SOURCES = CSV_SOURCES | {'data_receipt', 'data_manifest', 'splits', 'physical_summary',
    'selected_manifest', 'training_receipt', 'normalizer', 'contract', 'checkpoint'}
EVIDENCE = 'PRIOR_REAL_EM_EXTERNAL_DEVELOPMENT_DIAGNOSTIC'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def number(value):
    require(not isinstance(value, bool), 'boolean is not a physical number')
    result = float(value)
    require(math.isfinite(result), 'nonfinite physical value')
    return result


def vector(value, width):
    if isinstance(value, str):
        value = load_json(value)
    require(isinstance(value, (list, tuple)) and len(value) == width, 'wrong vector width')
    return [number(v) for v in value]


def flag(value):
    require(value in ('true', 'false', 'True', 'False'), 'unrecognized CSV boolean')
    return value.lower() == 'true'


def same_pin(a, b, *, same_path=True):
    require(a['sha256'] == b['sha256'], 'source SHA linkage differs')
    if same_path:
        require(a['path'] == b['path'], 'source path linkage differs')
    size_a, size_b = a.get('bytes', a.get('size_bytes')), b.get('bytes', b.get('size_bytes'))
    if size_a is not None and size_b is not None:
        require(size_a == size_b, 'source size linkage differs')


def unique(rows, key):
    result = {}
    for row in rows:
        identity = row[key]
        require(isinstance(identity, str) and identity and identity not in result, 'duplicate/missing '+key)
        result[identity] = row
    return result


def prepare_inputs(spec, documents):
    """Pure metadata/row check; small synthetic population sizes follow the spec.

    documents has all source aliases except checkpoint, JSON already decoded,
    CSV sources as lists of dictionaries. Caller owns byte verification. Return
    original64 accounting, the strict rows/unaltered geometries/truth, and an
    exclusion audit. No incoming validation/test numeric values are touched.
    """
    from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
        GEOMETRY_FIELDS, canonical_geometry_sha256)
    require(spec['schema'] == 'eucap15_external_forward_spec.v1' and
            spec['status'] == 'FROZEN_BEFORE_PREDICTIONS', 'frozen external spec required')
    require(spec['model'] == MODEL and spec['score_spans'] == list(SPANS), 'model/scoring contract differs')
    require(set(spec['sources']) == SOURCES and set(documents) == SOURCES-{'checkpoint'}, 'source aliases differ')
    n, ns = spec['N_original'], spec['N_strict']
    require(type(n) is int and type(ns) is int and 0 < ns <= n, 'invalid original/strict denominator')
    pins = spec['sources']; d = documents
    dr, dm, splits, contract = (d[k] for k in ('data_receipt', 'data_manifest', 'splits', 'contract'))
    require(dr['schema'] == 'eucap15_formal_development_view_receipt.v1' and
            dr['status'] == 'PASS_PREPARED_BUNDLE_BB00' and dr['scope'] == 'DEVELOPMENT_CURRENT_SNAPSHOT' and
            dr['FINAL_status'] == 'NOT_FINAL', 'wrong development data receipt')
    require(dm['schema'] == 'bb_data_manifest.v1' and dm['status'] == 'PASS', 'unclosed data manifest')
    require(dr['dataset']['sha256'] == dm['artifacts']['dataset.npz']['sha256'] == spec['expected_data_sha'], 'data identity differs')
    same_pin(dr['data_manifest'], pins['data_manifest'])
    for alias, name in (('source_rows', 'SOURCE_ROWS.csv'), ('splits', 'splits.json'),
                        ('exclusions', 'EXCLUSIONS.csv'), ('contract', 'contract.json')):
        same_pin(dm['artifacts'][name], pins[alias], same_path=False)
    fields = contract['field_names']
    require(fields == list(GEOMETRY_FIELDS) == dm['geometry_fields'], 'exact original 10D order required')
    lower, upper = vector(contract['lower'], 10), vector(contract['upper'], 10)
    require(all(a < b for a,b in zip(lower,upper)), 'invalid geometry bounds')
    require(dm['geometry_units'] == 'um' and dm['frequency_hz'] == [15000000000] and
            dm['target_columns'] == ['lp_nh', 'ls_nh', 'qmin', 'k_abs'], 'data frequency/units/order differs')
    expected_counts = dict(train=spec['source_counts']['train'], validation=spec['source_counts']['validation_metadata'],
                           test=spec['source_counts']['test_metadata'])
    require(sum(expected_counts.values()) == spec['source_counts']['snapshot'] and
            dr['split_counts'] == dm['split_counts'] == splits['counts'] == expected_counts, 'source counts differ')
    by_hash, id_map = splits['by_geometry_sha256'], splits['geometry_id_to_sha256']
    id_sets = {k: set(splits['ids'][k]) for k in expected_counts}
    require(all(len(id_sets[k]) == len(splits['ids'][k]) == expected_counts[k] for k in id_sets) and
            all(not id_sets[a] & id_sets[b] for a, b in (('train','validation'), ('train','test'), ('validation','test'))),
            'duplicate or overlapping split')
    source_map = unique(d['source_rows'], 'geometry_sha256')
    require(len(source_map) == spec['source_counts']['snapshot'] and set(source_map) == set(by_hash), 'full source/split coverage differs')
    train_tuples, seen_ids, seen_view = set(), set(), set()
    for identity, row in source_map.items():
        role, gid, view = row['assigned_development_split'], row['geometry_id'], int(row['view_row'])
        require(role in id_sets and by_hash[identity] == role and gid in id_sets[role] and id_map.get(gid) == identity,
                'source/split row identity differs')
        require(gid not in seen_ids and view not in seen_view, 'duplicate source row/geometry ID')
        seen_ids.add(gid); seen_view.add(view)
        if role == 'train':
            train_tuples.add(tuple(round(number(row['geom__'+f]), 12) for f in fields))
    require(seen_view == set(range(len(source_map))) and set(id_map) == seen_ids, 'source view/map incomplete')
    ps, selected = d['physical_summary'], d['selected_manifest']
    require(ps['N_original_requests'] == selected['N_requests'] == n and ps['N_strict_valid'] == ns and
            ps['completion_status'] == 'COMPLETE_ACCOUNTING' and ps['N_pending_requests'] == 0,
            'original physical account is not closed')
    require(selected['schema'] == 'eucap15_selected_candidate_handoff.v1' and
            selected['frequency_ghz'] == 15 and selected['label_mode'] == 'STRICT_LUMPED' and
            ps['dataset_scope'] == selected['dataset_scope'] == 'FORMAL_10K' and
            ps['research_phase'] == selected['research_phase'] == 'DEVELOPMENT_PILOT_NOT_FINAL10K' and
            ps['model_id'] == selected['model_id'], 'historical reference identity differs')
    same_pin(ps['selected_manifest'], pins['selected_manifest'])
    same_pin(selected['selected_candidate_csv'], pins['selected_geometry'])
    require(ps['feature_order'] == list(FEATURES) and ps['score_spans'] == list(SPANS) and
            ps['q_emx'] is None and ps['complete11_status'] == 'NOT_EVALUATED', 'physical semantics changed')
    reqs = unique(selected['requests'], 'request_id'); physical = unique(d['physical_rows'], 'request_id')
    require(len(reqs) == len(physical) == n and set(reqs) == set(physical), 'original request frame differs')
    require(len(unique(d['physical_rows'], 'candidate_id')) == n, 'duplicate physical candidate')
    states = Counter(r['state'] for r in physical.values())
    require(states.get('STRICT_VALID', 0) == ns and
            all(states.get(k, 0) == v for k, v in ps['state_counts'].items()) and
            set(states) <= set(ps['state_counts']), 'physical state accounting differs')
    geo = unique(d['selected_geometry'], 'candidate_id')
    pass_ids = {r['candidate_id'] for r in reqs.values() if r['selected_analytic_pass'] is True}
    require(set(geo) == pass_ids and len(geo) == selected['N_selected_analytic_pass'], 'selected geometry set differs')
    excluded = [r for r in d['exclusions'] if r['source_group'] == 'ORIGINAL64_VALIDATION']
    ex = unique(excluded, 'candidate_id')
    strict_ids = {r['candidate_id'] for r in physical.values() if r['state'] == 'STRICT_VALID'}
    require(len(ex) == ns and set(ex) == strict_ids and dr['excluded_source_counts']['ORIGINAL64_VALIDATION'] == ns,
            'exclusions are not the complete original64 strict set')
    accounting, strict, canonical_ids = [], [], set()
    for order, item in enumerate(selected['requests']):
        rid, cid, q = item['request_id'], item['candidate_id'], item['q_proxy']; row = physical[rid]
        require(item['request_order'] == order and type(q) is int and 10 <= q <= 20 and
                cid == f'{rid}-q{q:02d}' and item['candidate_id_sha256'] == hashlib.sha256(cid.encode()).hexdigest(),
                'frozen selected candidate/Q/order differs')
        require(row['candidate_id'] == cid and int(row['q_proxy']) == q and row['q_emx'] == '' and
                row['candidate_geometry_identity_sha256'] == item['candidate_geometry_identity_sha256'], 'physical selected identity differs')
        target, proxy = vector(row['target'], 4), vector(row['grid_proxy'], 4)
        require(target == item['selected_target'] and target[2] == q and proxy == item['selected_grid_proxy'], 'frozen target/proxy changed')
        account = dict(request_id=rid, candidate_id=cid, q_proxy=q, original_state=row['state'],
            candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256'],
            target=target, historical_grid_proxy=proxy, prior_actual=row['actual'], prior_strict_joint_hit=row['strict_joint_hit'],
            forward_prediction=None, forward_status='NOT_SELECTED_NONSTRICT_ORIGINAL_RESULT',
            N_original=n, N_strict=ns, evidence=EVIDENCE, q_emx=None)
        if row['state'] == 'STRICT_VALID':
            er, gr = ex[cid], geo[cid]; truth = vector(row['actual'], 4)
            require(item['selected_analytic_pass'] is True and row['touchstone_sha'] and
                    er['exclusion_reason'] == 'ORIGINAL64_VALIDATION_NOT_TRAINING_DATA' and
                    er['frequency_hz'] == '15000000000' and flag(er['strict_lumped_valid']) and flag(er['below_half_srf']),
                    'wrong excluded physical cohort')
            require([number(er[k]) for k in ('lp_nh','ls_nh','qmin','k_abs')] == truth and
                    truth[2] == min(number(er['qp']), number(er['qs'])) and truth[3] == abs(number(er['signed_k'])),
                    'exact prior EM labels differ')
            geometry = [number(gr['geom__'+f]) for f in fields]
            excluded_geometry = [number(er['geom__'+f]) for f in fields]
            require(geometry == excluded_geometry and tuple(round(v,12) for v in geometry) ==
                    tuple(round(v,12) for v in excluded_geometry), 'original geometry changed; no rounding/repair permitted')
            require(gr['candidate_id_sha256'] == item['candidate_id_sha256'] and
                    gr['candidate_geometry_identity_sha256'] == gr['geometry_sha256'] == item['candidate_geometry_identity_sha256'],
                    'selected geometry candidate namespace differs')
            canonical = canonical_geometry_sha256(dict(zip(fields, geometry)))
            require(canonical == er['geometry_sha256'] == gr['geometry_sha256'] and
                    canonical not in by_hash and canonical not in canonical_ids,
                    'canonical identity mismatch/overlap with source population')
            require(tuple(round(v,12) for v in geometry) not in train_tuples, 'round12 geometry overlaps training')
            canonical_ids.add(canonical)
            require(all(lo <= g <= hi for lo,g,hi in zip(lower,geometry,upper)), 'geometry outside unchanged contract')
            strict.append(dict(request_id=rid, candidate_id=cid, q_proxy=q, canonical_geometry_sha256=canonical,
                candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256'], geometry_um=geometry,
                truth=truth, touchstone_sha256=row['touchstone_sha'], label_source_path=er['label_source_path'],
                label_source_sha256=er['label_source_sha256'],
                label_source_row_reference=er['label_source_row_1based_including_header']))
            account['forward_status'] = 'AWAITING_FIXED_FORWARD_PREDICTION'
        accounting.append(account)
    tr, norm = d['training_receipt'], d['normalizer']
    require(tr['schema'] == 'bb00_training_receipt.v1' and tr['role'] == 'forward' and tr['kind'] == 'BB00' and
            tr['status'] == 'PARTIAL' and tr['validation_selected_checkpoint'] is True and
            tr['best_checkpoint'] == pins['checkpoint']['path'] and tr['best_sha256'] == pins['checkpoint']['sha256'] and
            tr['data_sha'] == spec['expected_data_sha'] and tr['normalizer_sha'] == canonical_sha(norm), 'trained forward/best identity differs')
    require(tr['architecture']['widths'] == MODEL['widths'] and tr['frequency_ghz'] == 15 and
            tr['label_mode'] == 'STRICT_LUMPED' and tr['test_access'] is False and
            all(tr['eligible_rows'][k]['eligible_geometries'] == v for k,v in expected_counts.items()), 'forward training scope differs')
    require(norm['field_names'] == fields and norm['physical_features'] == list(FEATURES) and
            norm['response_spans'] == list(SPANS) and norm['frequency_ghz'] == 15 and norm['label_mode'] == 'STRICT_LUMPED',
            'forward normalization metadata differs')
    return dict(accounting=accounting, strict_rows=strict, geometry_fields=fields,
        geometries=[r['geometry_um'] for r in strict], truth=[r['truth'] for r in strict],
        expected_checkpoint_splits={h:('train','validation','test').index(role) for h,role in by_hash.items()},
        audit=dict(N_original=n, N_strict=ns, source_counts=expected_counts, canonical_excluded_from_all_source=True,
            round12_excluded_from_train=True, geometry_rounded_for_prediction=False, test_numeric_values_converted=0,
            validation_numeric_values_converted=0, parent_family_independence='UNKNOWN_NOT_CERTIFIED'))


def file_pin(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'exact absolute path required')
    no_symlinks(path); digest = hashlib.sha256(); size = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block); size += len(block)
    return dict(path=str(path), sha256=digest.hexdigest(), bytes=size)


def read_sources(spec):
    documents, pins = {}, {}
    for alias, original in spec['sources'].items():
        actual = file_pin(original['path']); same_pin(original, actual); pins[alias] = actual
        if alias == 'checkpoint':
            continue
        raw = Path(actual['path']).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == actual['sha256'], 'source changed during read')
        if alias in CSV_SOURCES:
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8')))
            require(reader.fieldnames and len(set(reader.fieldnames)) == len(reader.fieldnames), 'bad CSV header')
            rows = list(reader)
            require(all(None not in r and None not in r.values() for r in rows), 'ragged CSV')
            documents[alias] = rows
        else:
            documents[alias] = load_json(raw)
    return documents, pins


def write_rows(path, rows):
    encoded = [{k:json.dumps(v, allow_nan=False) if isinstance(v,(list,dict)) else v for k,v in r.items()} for r in rows]
    put_csv(path, encoded, list(encoded[0]))


def run(spec_path, out):
    """One no-clobber forward-only diagnostic; never dispatches native work."""
    out = Path(out); require(out.is_absolute() and '..' not in out.parts, 'absolute new output required')
    no_symlinks(out); out.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat(); loaded = 0
    try:
        code = file_pin(Path(__file__).resolve())
        spec_pin = file_pin(spec_path)
        require(spec_pin['sha256'] == SPEC_SHA, 'this entry accepts only the exact pre-prediction spec')
        spec = load_json(Path(spec_path).read_bytes())
        require(spec['expected_data_sha'] == DATA_SHA and spec['N_original'] == 64 and spec['N_strict'] == 23 and
                spec['source_counts'] == dict(snapshot=6329,train=3801,validation_metadata=1269,test_metadata=1259), 'not fixed S15/external23 frame')
        put_json(out/'INTENT.json', dict(spec=spec_pin, status='STARTED_NOT_COMPLETION', started_utc=started,
            model_loads_authorized=1, native_calls_authorized=0, inverse_loads_authorized=0))
        documents, pins = read_sources(spec); prepared = prepare_inputs(spec, documents)
        put_json(out/'INPUT_AUDIT.json', prepared['audit'])
        # Deferred imports: prepare_inputs never loads a torch checkpoint/model.
        import numpy as np
        import torch
        from .bb00 import load_bb00
        from .frequency_evaluation import _predict, regression_metrics
        from .frequency_physical_statistics import error_metrics, percentile, percentage
        torch.set_num_threads(2)
        model, state = load_bb00(pins['checkpoint']['path'], device='cpu', expected_sha256=pins['checkpoint']['sha256']); loaded = 1
        require(state['role'] == 'forward' and state['step'] == MODEL['step'] and state['train_config']['seed'] == 17 and
                state['train_config']['role'] == 'forward' and tuple(state['train_config']['hidden_layers']) == (256,256,256) and
                state['architecture'] == documents['training_receipt']['architecture'] and
                state['data_sha'] == DATA_SHA and state['data_manifest_sha'] == pins['data_manifest']['sha256'], 'loaded forward configuration differs')
        require(state['normalizer_sha'] == canonical_sha(documents['normalizer']) == canonical_sha(state['normalizer']) and
                state['contract_sha'] == canonical_sha(documents['contract']) == canonical_sha(state['contract']) and
                state['split_by_geometry_sha256'] == prepared['expected_checkpoint_splits'] and
                state['forward_checkpoint'] is None, 'loaded normalization/contract/split or role differs')
        model.eval().requires_grad_(False)
        predictions, failures = _predict(model, np.asarray(prepared['geometries'],float), 4, 'cpu', 32)
        put_json(out/'PREDICTION_FAILURES.json', failures)
        truth = np.asarray(prepared['truth'],float); parity = []; by_request = {}
        for source, pred in zip(prepared['strict_rows'], predictions):
            values = [float(v) if math.isfinite(float(v)) else None for v in pred]
            by_request[source['request_id']] = values
            record = {k:source[k] for k in ('request_id','candidate_id','q_proxy','canonical_geometry_sha256','candidate_geometry_identity_sha256','touchstone_sha256')}
            record.update(N_original=64,N_strict=23,evidence=EVIDENCE,forward_status='PASS' if None not in values else 'PREDICTION_FAILED')
            record.update({'geometry__'+f:g for f,g in zip(prepared['geometry_fields'],source['geometry_um'])})
            for feature, actual, value in zip(FEATURES, source['truth'], values):
                record['prior_em__'+feature]=actual; record['forward__'+feature]=value
                record['error__'+feature]=None if value is None else value-actual
            parity.append(record)
        for row in prepared['accounting']:
            if row['request_id'] in by_request:
                row['forward_prediction']=by_request[row['request_id']]
                row['forward_status']='PASS' if None not in row['forward_prediction'] else 'PREDICTION_FAILED'
        write_rows(out/'ACCOUNTING64.csv',prepared['accounting']); put_json(out/'ACCOUNTING64.json',prepared['accounting'])
        write_rows(out/'FORWARD_PARITY23.csv',parity)
        inherited = regression_metrics(truth,predictions); metrics=[]
        for j,(feature,unit,span) in enumerate(zip(FEATURES,UNITS,SPANS)):
            ok=np.isfinite(predictions[:,j]); errors=(predictions[ok,j]-truth[ok,j]).tolist()
            em=error_metrics(errors); absolute=[abs(v) for v in errors]
            base={**em,'abs_error_p99':percentile(absolute,.99),'abs_error_max':max(absolute) if absolute else None}
            record=dict(feature=feature,unit=unit,N_original=64,N_strict=23,n_finite=len(errors),score_span=span,
                evidence=EVIDENCE,ci_status='NOT_ESTIMATED',quantile_method='linear',population='ORIGINAL64_STRICT23_CONDITIONAL',
                signed_error_definition='evaluated_forward_prediction_minus_prior_actual_EM',
                relative_error_denominator='absolute_prior_actual_EM_not_original_design_target')
            for key,value in base.items():
                if key=='n': continue
                name=key+'_linear' if key.startswith('abs_error_p') else key
                record[name]=value if len(errors)==23 else None
                record['conditional_finite_'+name]=value
                record['fixed_span_'+name]=None if len(errors)!=23 or value is None else value/span
            pct=[percentage(float(predictions[i,j]),float(truth[i,j]),span) for i in np.flatnonzero(ok)]
            pv=[v for v,_ in pct if v is not None]
            record.update(em_truth_relative_absolute_percent_mean=sum(pv)/len(pv) if len(pv)==23 else None,
                conditional_em_truth_relative_absolute_percent_mean=sum(pv)/len(pv) if pv else None,
                em_truth_relative_absolute_percent_n=len(pv),
                em_truth_relative_absolute_percent_status_counts=dict(Counter(status for _,status in pct)),
                regression_absolute_error_p95_higher=inherited[feature]['conditional_evaluable']['absolute_error_p95'])
            metrics.append(record)
        write_rows(out/'FORWARD_METRICS.csv',metrics)
        complete=bool(np.isfinite(predictions).all()) and not failures
        summary=dict(schema='eucap15_external_forward_result.v1',status='COMPLETE_CONDITIONAL_DIAGNOSTIC' if complete else 'PARTIAL_PREDICTION_FAILURES_PRESERVED',
            evidence=EVIDENCE,N_original=64,N_strict=23,N_forward_complete=int(np.isfinite(predictions).all(1).sum()),
            evaluated_forward_model=MODEL,checkpoint=pins['checkpoint'],data_sha=DATA_SHA,
            candidate_generator_model=dict(model_id=documents['physical_summary']['model_id'],
                dataset_scope=documents['physical_summary']['dataset_scope'],role='HISTORICAL_INVERSE_PRESELECTION'),
            source_state_counts=documents['physical_summary']['state_counts'],score_spans=list(SPANS),metrics=metrics,
            regression_metrics=inherited,regression_metrics_p95_method='higher; distinct from *_linear metric fields',
            q_proxy_modified=False,q_emx=None,complete11_status='NOT_EVALUATED',ci_status='NOT_ESTIMATED',
            inverse_loads=0,model_loads=1,training_updates=0,native_calls=0,test_numeric_values_used=False,
            source_csv_contains_test_bytes=True,FINAL=False,limitations=spec['limitations'])
        put_json(out/'SUMMARY.json',summary)
        for original in [code,spec_pin,*pins.values()]: same_pin(original,file_pin(original['path']))
        put_json(out/'RECEIPT.json',dict(schema='eucap15_external_forward_receipt.v1',status=summary['status'],spec=spec_pin,
            sources=pins,implementation=code,started_utc=started,ended_utc=datetime.now(timezone.utc).isoformat(),
            argv=sys.argv,python=sys.version,torch=torch.__version__,numpy=np.__version__,scope=EVIDENCE,
            model_loads=loaded,native_calls=0,physical_chain_revalidated=False))
        with (out/'SHA256SUMS').open('x') as stream:
            for p in sorted(out.iterdir()):
                if p.is_file() and p.name!='SHA256SUMS': stream.write(file_pin(p)['sha256']+'  '+p.name+'\n')
        return summary
    except BaseException as error:
        put_json(out/'FAILURE_RECEIPT.json',dict(status='FAIL_PRESERVED_NOT_RETRIED',error=type(error).__name__+': '+str(error),
            traceback=traceback.format_exc(),model_loads=loaded,native_calls=0,created_utc=datetime.now(timezone.utc).isoformat()))
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',type=Path,required=True); parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(); result=run(args.spec,args.out)
    print(json.dumps({k:result[k] for k in ('status','N_original','N_strict','N_forward_complete')}))


if __name__ == '__main__':
    main()
