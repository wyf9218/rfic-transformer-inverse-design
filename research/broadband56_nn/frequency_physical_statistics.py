"""Published local physical evidence -> immutable descriptive statistics.

No model imports, inference, network, simulation, production writes or pooling
across model/scope/source. Every original manifest candidate retains its slot.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
from statistics import mean

FEATURES = ('Lp_nH', 'Ls_nH', 'Qmin', 'K_abs')
UNITS = ('nH', 'nH', 'dimensionless', 'dimensionless')
GROUP = ('frequency_ghz', 'model_id', 'dataset_scope', 'target_source', 'label_mode', 'protocol_sha256')
CI_SEED, CI_REPLICATES = 2026090801, 2000
CI_POLICY = dict(method='descriptive percentile request-cluster bootstrap', seed=CI_SEED,
    replicates=CI_REPLICATES, confidence=.95, resampling_unit='whole request_id, never individual Q candidates',
    minimum_requests=2, small_sample_rule='2..9 observed request groups are PROVISIONAL',
    interpretation='finite preselected requests; no deployment-population accuracy or causal inference')
_CI_CACHE = {}


def require(test, message):
    if not test:
        raise ValueError(message)


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def canonical(value):
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def pin(path):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def verified_read(item, *, jsonl=False):
    require(isinstance(item, dict) and set(('path', 'sha256', 'bytes')) <= item.keys(), 'Explicit published path/SHA/bytes required')
    path = Path(item['path'])
    before = pin(path)
    require(all(before[k] == item[k] for k in ('sha256', 'bytes')), 'Input SHA/size differs: ' + str(path))
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == item['sha256'] and len(raw) == item['bytes'], 'Input changed during read')
    value = [json.loads(s) for s in raw.decode().splitlines() if s.strip()] if jsonl else json.loads(raw)
    require(pin(path) == before, 'Input changed after parse')
    return value


def save(path, value, *, terminal=False):
    path = Path(path)
    target = path.with_name('.' + path.name + '.tmp') if terminal else path
    require(not path.exists(), 'No-clobber output exists: ' + str(path))
    with target.open('x') as stream:
        json.dump(clean(value), stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    if terminal:
        os.replace(target, path)


def csv_write(path, rows, fields=None):
    fields = fields or list(rows[0])
    with Path(path).open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(clean(value), sort_keys=True, allow_nan=False) if isinstance(value, (dict, list, tuple)) else clean(value)
                             for key, value in row.items()})


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def vector(value):
    return isinstance(value, (list, tuple)) and len(value) == 4


def percentile(values, probability):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * probability
    low = int(position); high = min(low + 1, len(values) - 1)
    return values[low] + (position - low) * (values[high] - values[low])


def error_metrics(values):
    if not values:
        return dict(n=0, mae=None, rmse=None, bias=None, abs_error_p50=None, abs_error_p90=None, abs_error_p95=None)
    absolute = [abs(x) for x in values]
    return dict(n=len(values), mae=mean(absolute), rmse=math.sqrt(mean([x*x for x in values])), bias=mean(values),
                abs_error_p50=percentile(absolute, .5), abs_error_p90=percentile(absolute, .9), abs_error_p95=percentile(absolute, .95))


def cluster_ci(grouped):
    """Bootstrap entire request groups, preserving variable valid candidate counts."""
    grouped = {key: values for key, values in grouped.items() if values}
    names = ('mae', 'rmse', 'bias', 'abs_error_p50', 'abs_error_p90', 'abs_error_p95')
    result = dict(ci_status='NOT_ESTIMABLE' if len(grouped) < 2 else ('PROVISIONAL_SMALL_REQUEST_N' if len(grouped) < 10 else 'DESCRIPTIVE_REQUEST_BOOTSTRAP'),
                  n_request_groups=len(grouped))
    for name in names:
        result[name+'_ci_low'] = result[name+'_ci_high'] = None
    if len(grouped) < 2:
        return result
    cache_key = canonical(grouped)
    if cache_key in _CI_CACHE:
        return dict(_CI_CACHE[cache_key])
    keys = sorted(grouped)
    rng = random.Random(CI_SEED)
    samples = {name: [] for name in names}
    for _ in range(CI_REPLICATES):
        chosen = [keys[rng.randrange(len(keys))] for _ in keys]
        metrics = error_metrics([value for key in chosen for value in grouped[key]])
        for name in names:
            samples[name].append(metrics[name])
    for name in names:
        result[name+'_ci_low'] = percentile(samples[name], .025)
        result[name+'_ci_high'] = percentile(samples[name], .975)
    _CI_CACHE[cache_key] = dict(result)
    return result


def percentage(actual, reference, span):
    """Secondary diagnostics only. Suppression never changes primary errors/gates."""
    if actual is None:
        return None, 'MISSING_OR_NONFINITE_EMX'
    if not finite(actual) or not finite(reference):
        return None, 'NONFINITE_INPUT'
    if abs(reference) <= max(1e-12, abs(span)*1e-6):
        return None, 'NEAR_ZERO_REFERENCE_NOT_REPORTED'
    return 100*abs(actual-reference)/abs(reference), 'DIAGNOSTIC_ONLY_NOT_HIT_TOLERANCE'


def publication_entries(capture_paths):
    if isinstance(capture_paths, (str, Path)):
        publication_pin = pin(capture_paths)
        document = verified_read(publication_pin)
        publications = [publication_pin]
    elif isinstance(capture_paths, dict):
        document, publications = capture_paths, []
    else:
        raise ValueError('Published capture index required; raw CAPTURE paths are not accepted')
    require(document.get('schema') == 'frequency_physical_capture_publication.v1' and document.get('status') == 'PUBLISHED',
            'Capture publication must be written LAST with PUBLISHED status')
    entries = document['captures']
    require(len({x['path'] for x in entries}) == len(entries), 'Duplicate capture publication entry')
    return entries, publications


def manifest_rows(manifest_path):
    source = pin(manifest_path); manifest = verified_read(source)
    require(manifest['schema'] == 'frequency_physical_roundrobin_manifest.v1', 'Wrong dispatch manifest schema')
    jobs = manifest['jobs']; order = manifest['frequency_order']
    require(len(jobs) == 320 and len(order) == 16 and sorted(order) == list(range(5, 21)), 'Expected fixed16x20=320 request frame')
    require(len({j['request_id'] for j in jobs}) == 320 and [j['dispatch_order'] for j in jobs] == list(range(320)), 'Duplicate/reordered requests')
    freezes, sources, rows, contexts = {}, [source], [], {}
    for f, item in zip(order, manifest['source_freezes']):
        freeze = verified_read(item); sources.append(item)
        require(freeze['frequency_ghz'] == f and freeze['config']['label_mode'] == 'STRICT_LUMPED', 'Wrong frequency/label freeze')
        protocol = freeze['protocol']
        require(protocol['q_values'] == list(range(10, 21)) and vector(protocol['score_scale']) and vector(protocol['absolute_tolerances']) and
                all(finite(v) and v > 0 for v in protocol['score_scale'] + protocol['absolute_tolerances']), 'Invalid frozen scoring protocol')
        freezes[f] = (freeze, item)
    for i, job in enumerate(jobs):
        require(job['frequency_ghz'] == order[i % 16] and job['round_index'] == i // 16 and job['N_logical_candidates'] == 11,
                'Original round-robin request order/denominator changed')
        freeze, freeze_pin = freezes[job['frequency_ghz']]
        require(job['model_id'] == freeze['model_id'] and job['dataset_scope'] == freeze['config']['dataset_scope'],
                'Manifest mixes another model/scope into frozen route')
        original = verified_read(job['candidate_records'], jsonl=True); sources.append(job['candidate_records'])
        require([r['q_target'] for r in original] == list(range(10, 21)) and len({r['candidate_id'] for r in original}) == 11,
                'Original eleven candidate slots missing/duplicated')
        contexts[job['request_id']] = dict(job=job, freeze=freeze, freeze_pin=freeze_pin, originals=original)
        for r in original:
            require(all(r[k] == job[k] for k in ('request_id', 'frequency_ghz', 'model_id', 'dataset_scope', 'q_proxy', 'target_source')) and
                    type(r['analytic_grid']) is bool and vector(r['target']) and all(finite(v) for v in r['target']), 'Original candidate context or target mismatch')
            require(r['proxy_preselected'] == (r['q_target'] == job['q_proxy']), 'q_proxy preselection identity differs')
            require(vector(r['grid_proxy']), 'Frozen grid proxy missing; no new inference allowed')
            protocol = freeze['protocol']
            rows.append(dict(request_id=job['request_id'], dispatch_order=i, round_index=job['round_index'],
                frequency_ghz=job['frequency_ghz'], model_id=job['model_id'], dataset_scope=job['dataset_scope'],
                target_source=job['target_source'], label_mode=freeze['config']['label_mode'], protocol_sha256=canonical(protocol),
                candidate_id=r['candidate_id'], q_target=r['q_target'], q_proxy=job['q_proxy'], selected_before_emx=r['proxy_preselected'],
                analytic_pass=r['analytic_grid'], stage='PENDING' if r['analytic_grid'] else 'ANALYTIC_FAIL', request_accounted=False,
                solved=False, strict_valid=None, joint_hit=None, target=r['target'], proxy=clean(r['grid_proxy']), actual=None,
                emx_minus_target=None, emx_minus_frozen_proxy=None, target_relative_absolute_percent=None,
                proxy_relative_absolute_percent=None, percent_status_target=None, percent_status_proxy=None,
                proxy_score=clean(r['grid_proxy_score']), emx_score=None, score_scale=protocol['score_scale'],
                absolute_tolerances=protocol['absolute_tolerances'], solver_identity=None, s4p_sha256=None,
                source_pins=dict(original_records=job['candidate_records'], qscan_freeze=freeze_pin),
                support_status=r.get('support_status', 'UNKNOWN')))
    return manifest, contexts, rows, sources


def apply_capture(capture_pin, context, rows, sources):
    capture = verified_read(capture_pin); sources.append(capture_pin)
    summary = capture['summary']; job = context['job']; protocol = context['freeze']['protocol']
    require(all(summary[k] == job[k] for k in ('request_id', 'frequency_ghz', 'model_id', 'dataset_scope', 'q_proxy')),
            'Capture identity/scope differs from original dispatch')
    require(summary['N_original'] == 11 and [r['q'] for r in summary['candidates']] == list(range(10, 21)), 'Capture original denominator differs')
    require(summary.get('selected') is None or summary['selected']['q'] == job['q_proxy'], 'Captured selected Q is not pre-frozen q_proxy')
    local_by_remote = {}
    for item in capture['files']:
        local, remote = item['local'], item['remote']
        require(local['sha256'] == remote['sha256'] and local['bytes'] == remote['bytes'], 'Capture relocation bytes differ')
        # Verify all delivered local bytes, but never follow private remote paths.
        require(pin(local['path'])['sha256'] == local['sha256'] and pin(local['path'])['bytes'] == local['bytes'], 'Published local artifact changed')
        if remote['path'] in local_by_remote:
            require(local_by_remote[remote['path']] == local, 'Duplicate remote path has conflicting identity')
        local_by_remote[remote['path']] = local; sources.append(local)
    remote_pins = {(p['path'], p['sha256'], p['bytes']) for p in capture['verified_remote']}

    def local_json(remote_pin):
        require(remote_pin['path'] in local_by_remote, 'Required source not locally copied; no network fallback')
        local = local_by_remote[remote_pin['path']]
        require(local['sha256'] == remote_pin['sha256'] and local['bytes'] == remote_pin['bytes'], 'Wrong copied source pin')
        return verified_read(local), local

    request_receipt, receipt_local = local_json(summary['receipt'])
    require(request_receipt['frozen_job'] == job and request_receipt['N_logical'] == 11 and
            request_receipt['status'] in ('REQUEST_ACCOUNTED', 'REUSED_EXISTING_FIRST15_NO_SIMULATOR'), 'No published complete request receipt')
    by_q = {r['q_target']: r for r in rows}; feature_count = 0
    for item in summary['candidates']:
        row = by_q[item['q']]
        require(item['candidate_id'] == row['candidate_id'], 'Capture candidate identity differs')
        row['request_accounted'] = True
        row['source_pins'].update(capture=capture_pin, request_receipt=receipt_local)
        stage = item['stage']
        if not row['analytic_pass']:
            require(stage == 'ANALYTIC_FAIL' and item.get('feature_pin') is None, 'Analytic failure became a successful replacement')
            continue
        if 'feature_pin' not in item:
            row['stage'] = {'FAIL':'GDS_FAIL', 'GDS_FAIL':'GDS_FAIL', 'CALIBRE_FAIL':'DRC_FAIL', 'NOT_SOLVED':'EMX_NOT_SOLVED'}.get(stage)
            require(row['stage'] is not None, 'Unknown completed failure stage; do not impute pending/failure')
            continue
        feature, feature_local = local_json(item['feature_pin']); feature_count += 1
        legacy = 'research_tier' in feature
        require(feature.get('status', feature.get('overall_status')) == 'PASS_EXTRACTION' and feature['candidate_id'] == row['candidate_id'] and
                feature['q_requested'] == row['q_target'] and feature['frequency_ghz'] == row['frequency_ghz'] and
                feature['model_id'] == row['model_id'] and feature.get('dataset_scope', feature.get('research_tier')) == row['dataset_scope'] and
                feature['q_proxy'] == row['q_proxy'], 'Feature model/scope/Q mismatch')
        require(feature['target'] == row['target'] and clean(feature['proxy_self']) == row['proxy'] and
                feature['score_scale'] == row['score_scale'] and feature['absolute_hit_tolerances'] == row['absolute_tolerances'],
                'Frozen target/proxy/scoring/tolerance differs')
        solver_pin = feature['fresh_solver_receipt'] if legacy else feature['solver_receipt']
        solver, solver_local = local_json(solver_pin)
        require(solver.get('status', solver.get('overall_status')) == 'PASS' and solver['candidate_id']==row['candidate_id'] and solver['source_gds_before'] == solver['source_gds_after'],
                'Solver not PASS or GDS changed')
        for p in (solver['source_gds_after'], solver['touchstone']):
            require((p['path'],p['sha256'],p['bytes']) in remote_pins, 'Physical source pin missing from verified capture closure')
        actual = clean(feature['actual_fresh_emx'])
        require(vector(actual), 'Feature vector must retain all four slots')
        finite_actual = all(finite(v) for v in actual)
        require(all(type(feature[k]) is bool for k in ('descriptor_valid','strict_lumped_valid','physics_qa_pass')),
                'Validity flags must be actual booleans, not strings or imputed values')
        valid = finite_actual and feature['descriptor_valid'] and feature['strict_lumped_valid'] and feature['physics_qa_pass']
        require(valid == feature['valid_for_frozen_strict_comparison' if legacy else 'valid_for_strict_comparison'], 'Strict feature validity differs')
        errors = [a-t if finite(a) else None for a,t in zip(actual,row['target'])]
        proxy_errors = [a-p if finite(a) and finite(p) else None for a,p in zip(actual,row['proxy'])]
        hits = [finite(e) and abs(e)<=tau for e,tau in zip(errors,row['absolute_tolerances'])]
        require(feature['within_tolerance'] == hits and feature['joint_response_hit'] == all(hits) and
                feature['strict_joint_hit'] == (all(hits) and valid), 'Joint hit differs from four frozen tolerances')
        score = math.sqrt(mean([(e/s)**2 for e,s in zip(errors,row['score_scale'])])) if finite_actual else None
        require(score is None and feature['normalized_response_score'] is None or
                score is not None and math.isclose(score,feature['normalized_response_score'],rel_tol=1e-10,abs_tol=1e-12), 'Score differs from frozen range')
        target_pct = [percentage(a,t,s) for a,t,s in zip(actual,row['target'],row['score_scale'])]
        proxy_pct = [percentage(a,p,s) for a,p,s in zip(actual,row['proxy'],row['score_scale'])]
        row.update(stage='SOLVED_STRICT_VALID' if valid else 'SOLVED_INVALID', solved=True, strict_valid=valid,
            joint_hit=all(hits) and valid, actual=actual, emx_minus_target=errors, emx_minus_frozen_proxy=proxy_errors,
            target_relative_absolute_percent=[x[0] for x in target_pct], proxy_relative_absolute_percent=[x[0] for x in proxy_pct],
            percent_status_target=[x[1] for x in target_pct], percent_status_proxy=[x[1] for x in proxy_pct], emx_score=score,
            solver_identity=canonical(solver_pin), s4p_sha256=solver['touchstone']['sha256'])
        row['source_pins'].update(feature=feature_local, solver=solver_local, touchstone=solver['touchstone'], gds=solver['source_gds_after'])
    require(feature_count == summary['N_solved'] == request_receipt['N_solved'] and
            sum(r['strict_valid'] is True for r in rows) == summary['N_strict_valid'], 'Solved/strict request denominator differs')
    winner = min(rows, key=lambda r:(r['emx_score'],r['q_target']))['q_target'] if all(r['strict_valid'] is True for r in rows) else None
    require(winner == summary['q_emx'] == request_receipt['q_emx'], 'q_emx requires all original11 strict-valid')


def counts(rows):
    solved = [r for r in rows if r['solved']]
    result = dict(N_original=len(rows), N_solved=len(solved), N_strict_valid=sum(r['strict_valid'] is True for r in rows),
        N_invalid=sum(r['solved'] and r['strict_valid'] is not True for r in rows),
        N_analytic_fail=sum(r['stage']=='ANALYTIC_FAIL' for r in rows), N_gds_fail=sum(r['stage']=='GDS_FAIL' for r in rows),
        N_drc_fail=sum(r['stage']=='DRC_FAIL' for r in rows), N_emx_not_solved=sum(r['stage']=='EMX_NOT_SOLVED' for r in rows),
        N_pending=sum(r['stage']=='PENDING' for r in rows), N_joint_hit=sum(r['joint_hit'] is True for r in rows),
        N_independent_solves=len({r['solver_identity'] for r in solved}), N_unique_s4p_sha=len({r['s4p_sha256'] for r in solved}))
    require(sum(result[k] for k in ('N_solved','N_analytic_fail','N_gds_fail','N_drc_fail','N_emx_not_solved','N_pending')) == len(rows),
            'Mutually exclusive physical stages do not reconcile')
    fully_accounted=all(r['request_accounted'] for r in rows)
    accounted=[r for r in rows if r['request_accounted']]
    result.update(N_accounted_candidates=len(accounted),
                  joint_hit_fraction_original=None if not rows or not fully_accounted else result['N_joint_hit']/len(rows),
                  joint_hit_fraction_accounted_original=None if not accounted else sum(r['joint_hit'] is True for r in accounted)/len(accounted),
                  confirmed_joint_hit_coverage_original=None if not rows else result['N_joint_hit']/len(rows),
                  joint_hit_fraction_strict=None if not result['N_strict_valid'] else result['N_joint_hit']/result['N_strict_valid'])
    for subset,label in ((rows if fully_accounted else [],'original'),(accounted,'accounted_original'),
                         ([r for r in rows if r['strict_valid'] is True],'strict')):
        groups=defaultdict(list)
        for row in subset:groups[row['request_id']].append(float(row['joint_hit'] is True))
        ci=cluster_ci(groups)
        result['joint_hit_'+label+'_ci_status']=ci['ci_status']
        result['joint_hit_'+label+'_ci_low']=ci['bias_ci_low']
        result['joint_hit_'+label+'_ci_high']=ci['bias_ci_high']
    return result


def grouped_rows(rows):
    result=defaultdict(list)
    for row in rows:result[tuple(row[k] for k in GROUP)].append(row)
    return result


def metrics_table(rows):
    result=[]
    for key, allrows in grouped_rows(rows).items():
        for estimand in ('all_candidates','selected_q_proxy'):
            selected=[r for r in allrows if estimand=='all_candidates' or r['selected_before_emx']]
            denominator=counts(selected)
            for validity in ('strict_valid','all_finite_diagnostic'):
                for comparison in ('emx_minus_target','emx_minus_frozen_proxy'):
                    for index,feature in enumerate(FEATURES):
                        eligible=[r for r in selected if r['solved'] and (validity!='strict_valid' or r['strict_valid'] is True) and
                                  r[comparison] is not None and finite(r[comparison][index])]
                        clustered=defaultdict(list)
                        for r in eligible:clustered[r['request_id']].append(r[comparison][index])
                        result.append(dict(**dict(zip(GROUP,key)),estimand=estimand,validity=validity,comparison=comparison,
                            feature=feature,unit=UNITS[index],**error_metrics([r[comparison][index] for r in eligible]),
                            **cluster_ci(clustered),**denominator,percentage_metric='NOT_PRIMARY; use physical units and frozen spans'))
    return result


def request_status(rows):
    result=[]
    by_request=defaultdict(list)
    for row in rows:by_request[row['request_id']].append(row)
    for rid,group in by_request.items():
        first=group[0]; complete=all(r['strict_valid'] is True for r in group)
        selected=next((r for r in group if r['selected_before_emx']),None)
        best=min(group,key=lambda r:(r['emx_score'],r['q_target'])) if complete else None
        result.append(dict(**{k:first[k] for k in GROUP},request_id=rid,dispatch_order=first['dispatch_order'],
            status='ACCOUNTED' if all(r['request_accounted'] for r in group) else 'PENDING',q_proxy=first['q_proxy'],
            q_emx=None if best is None else best['q_target'],full11_gate='PASS' if complete else 'NOT_AVAILABLE_INCOMPLETE_OR_INVALID',
            selection_loss=None if best is None or selected is None else selected['emx_score']-best['emx_score'],
            selected_joint_hit=None if selected is None else selected['joint_hit'],**counts(group)))
    return result


def selection_comparison(rows):
    """All three strategies share exactly the same complete strict11 requests."""
    result=[]
    for key, allrows in grouped_rows(rows).items():
        requests=defaultdict(list)
        for row in allrows:requests[row['request_id']].append(row)
        complete={rid:group for rid,group in requests.items() if len(group)==11 and all(r['strict_valid'] is True for r in group)}
        for strategy in ('fixed_q15','q_proxy','q_emx'):
            chosen=[]
            for group in complete.values():
                chosen.append(next(r for r in group if r['q_target']==15) if strategy=='fixed_q15' else
                    next(r for r in group if r['selected_before_emx']) if strategy=='q_proxy' else min(group,key=lambda r:(r['emx_score'],r['q_target'])))
            for index,feature in enumerate(FEATURES):
                clusters={r['request_id']:[r['emx_minus_target'][index]] for r in chosen}
                result.append(dict(**dict(zip(GROUP,key)),strategy=strategy,feature=feature,unit=UNITS[index],
                    status='AVAILABLE_DESCRIPTIVE' if chosen else 'NOT_AVAILABLE',N_common_complete_requests=len(complete),
                    N_total_planned_requests=len(requests),N_original_candidates=len(allrows),
                    joint_hit_fraction=None if not chosen else mean([r['joint_hit'] for r in chosen]),
                    score_mean=None if not chosen else mean([r['emx_score'] for r in chosen]),
                    **error_metrics([r['emx_minus_target'][index] for r in chosen]),**cluster_ci(clusters)))
    return result


def build(manifest_path, capture_paths, output_newdir):
    """Build from a LAST-published capture index, never active CAPTURE files."""
    out=Path(output_newdir).resolve();out.mkdir(parents=True,exist_ok=False)
    try:
        _CI_CACHE.clear()
        manifest,contexts,rows,sources=manifest_rows(manifest_path)
        captures,publications=publication_entries(capture_paths);sources+=publications
        by_request=defaultdict(list)
        for row in rows:by_request[row['request_id']].append(row)
        seen=set()
        for capture_pin in captures:
            document=verified_read(capture_pin);rid=document['summary']['request_id']
            require(rid in contexts and rid not in seen,'Unknown/duplicate captured request, including another scope')
            seen.add(rid);apply_capture(capture_pin,contexts[rid],by_request[rid],sources)
        status=request_status(rows);metrics=metrics_table(rows);selection=selection_comparison(rows)
        frequency=[]
        for key,group in grouped_rows(rows).items():
            req=[r for r in status if tuple(r[k] for k in GROUP)==key]
            frequency.append(dict(**dict(zip(GROUP,key)),N_planned_requests=len(req),N_accounted_requests=sum(r['status']=='ACCOUNTED' for r in req),
                N_complete_strict11_requests=sum(r['full11_gate']=='PASS' for r in req),**counts(group)))
        save(out/'CANDIDATE_ROWS.json',dict(schema='frequency_physical_candidate_rows.v1',feature_order=FEATURES,rows=rows))
        csv_write(out/'CANDIDATE_ROWS.csv',rows)
        csv_write(out/'METRICS.csv',metrics)
        csv_write(out/'REQUEST_STATUS.csv',status)
        csv_write(out/'FREQUENCY_STATUS.csv',frequency)
        csv_write(out/'SELECTION_COMPARISON.csv',selection)
        source_index={canonical(p):p for p in sources}
        # Overall values are workflow denominators only: never publish a pooled
        # success fraction or CI across the distinct frequency/model/scope routes.
        operational_counts={k:v for k,v in counts(rows).items() if k.startswith('N_')}
        summary=dict(schema='frequency_physical_statistics.v1',status='COMPLETE_SNAPSHOT' if len(seen)==320 else 'PARTIAL_SNAPSHOT',
            created_utc=datetime.now(timezone.utc).isoformat(),N_planned_requests=320,N_accounted_requests=len(seen),
            N_complete_strict11_requests=sum(r['full11_gate']=='PASS' for r in status),**operational_counts,
            frequency_groups=frequency,feature_order=FEATURES,bootstrap_policy=CI_POLICY,
            quantile_method='linear interpolation on absolute errors; P50/P90/P95 not signed-error quantiles',
            primary_metrics='physical-unit EMX-target and EMX-frozen-grid-proxy residuals, strict-valid only',
            secondary_metrics='all_finite_diagnostic separate; relative percentages are not hit tolerance or primary |k| metric',
            percent_near_zero_rule='abs(reference)<=max(1e-12,1e-6*declared_span):null percentage only; retain physical residual and original gate',
            independent_solve_definition='distinct original solver receipt path+SHA+bytes; physical invocation count, NOT IID statistical samples',
            estimands=['all_candidates','selected_q_proxy'],selection_comparison_population='same original complete strict11 requests only',
            research_scope='16 fixed frequency routes x20 preselected heldout requests; dev5K15GHz never pooled with formal10K',
            interpretation='finite descriptive evidence; no population accuracy, causal superiority, PVT, mesh or full-chip claim',
            source_pins=list(source_index.values()),implementation=pin(__file__),simulations_run=0,inference_calls=0,network_calls=0)
        save(out/'SUMMARY.json',summary)
        files=[pin(p) for p in sorted(out.iterdir()) if p.is_file()]
        save(out/'MANIFEST.json',dict(schema='frequency_physical_statistics_manifest.v1',artifacts=files,
            excludes=['MANIFEST.json','SHA256SUMS','STATS_RECEIPT.json']))
        with (out/'SHA256SUMS').open('x') as stream:
            for p in files+[pin(out/'MANIFEST.json')]:stream.write(p['sha256']+'  '+Path(p['path']).name+'\n')
        save(out/'STATS_RECEIPT.json',dict(schema='frequency_physical_statistics_receipt.v1',status='PUBLISHED',
            summary=pin(out/'SUMMARY.json'),manifest=pin(out/'MANIFEST.json'),sha256sums=pin(out/'SHA256SUMS'),
            N_accounted_requests=len(seen),N_original_candidates=3520,implementation=pin(__file__)),terminal=True)
        return pin(out/'STATS_RECEIPT.json')
    except Exception as error:
        save(out/'FAILURE_RECEIPT.json',dict(status='FAIL_NO_OVERWRITE',error=type(error).__name__+': '+str(error),
            created_utc=datetime.now(timezone.utc).isoformat()),terminal=True)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True);parser.add_argument('--captures',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();print(json.dumps(build(args.manifest,args.captures,args.out),sort_keys=True))


if __name__=='__main__':main()
