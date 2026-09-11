"""Read-only results for the exact frozen 32+32 controlled acquisition.

No model import, target generation, native dispatch or training admission. The
pure comparison consumes verified rows; it is not a native authenticity test.
The original 64 proposals, including analytic failures and unstarted members,
are always retained. DOE has no target error. Wrapper birth is not solver birth.
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
import traceback

from .eucap15_acquisition_evidence import (
    MirrorReader, _require, _fields, _same, _strict_json, pin, SCALE, TAU,
)
from .eucap15_acquisition import actual_landing, validate_coverage, coverage_gain
from .eucap15_acquisition_budget import _time
from .eucap15_author_sources import no_symlinks, put_json, put_csv
from .data import _split_for_hash

INTENT_SHA = 'ea8cb05f228215932a47ee6620267ca299818e8ea76299f9862f920b362c2abb'
MANIFEST_SHA = '007f0c42e597089029111cbb005e4679fc5dad55efa735a94fe7baaba916de46'
PROPOSALS_SHA = '17de43afa74182674d6391c3b84bc8dca729d6c3f6d1b711e8c5728fab642fa3'
ARMS = ('COVERAGE_DIRECTED', 'GEOMETRY_DOE_CONTROL')
COUNTS = {'SPARSE_TARGETED': 25, 'EXPLORATION': 7, 'GEOMETRY_DOE': 32}
IDENTITY = ('candidate_id', 'request_id', 'arm', 'arm_order', 'global_order', 'q_proxy')
PRE_FAILURE = ('ANALYTIC_FAIL', 'DUPLICATE_HOLD', 'GDS_FAIL', 'DRC_FAIL')
# Prefix blockers; NO_CLOSED/UNCLASSIFIED do NOT assert that a solver never started.
UNSTARTED = ('PENDING', 'RESOURCE_PENDING', 'BUDGET_NOT_DISPATCHED',
             'NO_CLOSED_RESULT_IN_CAPTURE', 'CANDIDATE_FAILURE_UNCLASSIFIED')
SOLVER_CLOSED = ('STRICT_VALID', 'EMX_INVALID', 'SOLVER_FAIL', 'FEATURE_FAIL')
STATES = (*PRE_FAILURE, *UNSTARTED, *SOLVER_CLOSED, 'SOLVER_PENDING')
COST_FIELDS = ('solver_wall_seconds', 'solver_cpu_seconds',
               'total_stage_cost_seconds', 'storage_bytes')


def _finite4(values, name, *, positive=False):
    _require(isinstance(values, list) and len(values) == 4 and
             all(type(v) in (int, float) and math.isfinite(v) and
                 (not positive or v > 0) for v in values), name + ': finite four-vector required')


def validate_proposals(intent, proposals):
    """Validate frozen identities without generating targets or loading a model."""
    from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
        GEOMETRY_FIELDS, canonical_geometry_sha256,
    )
    _fields(intent, dict(schema='eucap15_controlled_acquisition_intent.v1',
        status='FROZEN_BEFORE_TARGETS_AND_INFERENCE'), 'Controlled intent')
    _require(intent['counts'] == dict(proposals_per_arm=32, sparse_targets=25,
        exploration=7, doe=32, logical_q_candidates=275), 'Frozen64 count contract differs')
    _require(intent['q_selection']['spans'] == SCALE and
             intent['q_selection']['values'] == list(range(10,21)), 'Symmetric Q contract differs')
    _fields(intent['budget'], dict(proposals_per_arm_max=32, solver_starts_per_arm_max=16,
        total_solver_starts_max=32, native_wall_seconds_max=21600,
        incremental_storage_bytes_max=2147483648), 'Controlled budget')
    _require(intent['comparisons']['equal_qualified_training']['primary_K'] == 4 and
             intent['comparisons']['equal_simulation']['primary_m'] == 16, 'Frozen comparison differs')
    _require(len(proposals) == 64, 'All64 original proposals required')
    seen, arm_counts, sources = set(), Counter(), Counter()
    for order, row in enumerate(proposals, 1):
        cid = row['candidate_id']
        _require(isinstance(cid,str) and cid and cid not in seen, 'Duplicate/invalid candidate')
        seen.add(cid)
        _require(row['arm'] in ARMS and row['source'] in COUNTS, 'Unknown arm/source')
        arm_counts[row['arm']] += 1; sources[row['source']] += 1
        _require(type(row['global_order']) is int and row['global_order'] == order and
                 type(row['arm_order']) is int and row['arm_order'] == arm_counts[row['arm']],
                 'Frozen global/per-arm order differs')
        _require(row['frequency_hz'] == 15000000000 and row['q_emx'] is None and
                 row['recipe_sha256'] == INTENT_SHA, 'Frequency/Q/intent identity differs')
        _require(row['geometry_fields'] == list(GEOMETRY_FIELDS), 'Geometry field order differs')
        g = row['geometry']
        _require(isinstance(g,list) and len(g)==10 and
                 all(type(x) in (int,float) and math.isfinite(x) for x in g), 'Finite original geometry required')
        h = canonical_geometry_sha256(dict(zip(GEOMETRY_FIELDS,g)))
        _require(h == row['canonical_geometry_sha256'], 'Geometry digest differs')
        _require(row['assigned_development_split'] == ('train','validation','test')[_split_for_hash(h,17)],
                 'Original geometry split differs')
        _require(type(row['analytic_pass']) is bool and isinstance(row['duplicate_reasons'],list) and
                 type(row['local_dispatch_eligible']) is bool and
                 row['local_dispatch_eligible'] == (row['analytic_pass'] and not row['duplicate_reasons']),
                 'Original analytic/hold eligibility differs')
        targeted = row['source']=='SPARSE_TARGETED'
        _require(row['arm'] == (ARMS[1] if row['source']=='GEOMETRY_DOE' else ARMS[0]), 'Source arm differs')
        if targeted:
            _finite4(row['target'],'target',positive=True); _finite4(row['proxy'],'proxy')
            _require(type(row['q_proxy']) is int and 10<=row['q_proxy']<=20 and
                     row['target'][2] == row['q_proxy'] and row['model_id'] == intent['model_id'], 'Frozen selected Q/model differs')
            _require(cid == row['request_id']+f"-q{row['q_proxy']:02d}", 'Candidate-Q identity differs')
            wanted=actual_landing([row['target'][j] for j in (0,1,3)])
            predicted=actual_landing([row['proxy'][j] for j in (0,1,3)])
            _require(wanted is not None and row['target_cell'] == list(wanted) and
                     row['predicted_cell'] == (list(predicted) if predicted is not None else None), 'Frozen target/proxy cell differs')
        else:
            _require(all(row[k] is None for k in ('target','proxy','q_proxy','model_id','target_cell','predicted_cell')),
                     'DOE/exploration must not acquire proxy target/errors')
    _require(dict(sources)==COUNTS and all(arm_counts[a]==32 for a in ARMS), 'Source/arm denominators differ')
    _require(len({r['request_id'] for r in proposals})==64, 'Duplicate original request')
    return {r['candidate_id']:r for r in proposals}


def load_context(reader, intent_pin, manifest_pin):
    """Read this frozen frame only. Pins of models remain identities, not loads."""
    _require(intent_pin['sha256']==INTENT_SHA and manifest_pin['sha256']==MANIFEST_SHA,
             'Different controlled experiment')
    intent, manifest = reader.document(intent_pin), reader.document(manifest_pin)
    _require(manifest['intent']==intent_pin, 'Preparation manifest/intent mismatch')
    files=manifest['files']
    for name,p in files.items():
        _require(Path(name).name==name and Path(p['path'])==Path(manifest_pin['path']).parent/name,
                 'Preparation artifact path differs')
    pp=files['SELECTED_CANDIDATES.jsonl']
    _require(pp['sha256']==PROPOSALS_SHA, 'Frozen64 proposal bytes differ')
    rows=[_strict_json(x) for x in reader.read(pp).splitlines()]
    by_id=validate_proposals(intent,rows)
    before=validate_coverage(list(csv.DictReader(io.StringIO(reader.read(files['DECISION_COVERAGE.csv']).decode()))))
    _require(sum(r['N_strict_core_all_q'] for r in before)==3801 and
             sum(r['N_strict_core_all_q']>0 for r in before)==159, 'Original3801/159 baseline differs')
    _require(files['DECISION_COVERAGE.csv']['sha256']==intent['inputs']['decision_coverage']['sha256'],
             'Decision coverage not original byte copy')
    exclusions=reader.document(files['EXCLUSION_METADATA.json'])
    excluded=set()
    for key in ('canonical_hashes','nominal_grid_hashes'):
        for values in exclusions[key].values(): excluded.update(values)
    _require(excluded and exclusions['numeric_response_values_used']==0, 'Frozen exclusions unavailable')
    receipt=reader.document(files['PREPARATION_RECEIPT.json'])
    _fields(receipt,dict(status='PREPARED_NOT_NATIVE_RELEASE',intent=intent_pin,N_total_proposals=64,
        N_logical_proxy_candidates=275,N_selected_q=25,source_bytes_unchanged=True), 'Preparation receipt')
    reader.recheck()
    return dict(intent=intent_pin,manifest=manifest_pin,proposals=pp,intent_value=intent,
        rows=by_id,before=before,excluded_hashes=excluded,model_id=intent['model_id'])


def initial_rows(ctx):
    """Absent publication is NOT_RUN, not proof of native absence or completion."""
    result=[]
    for p in ctx['rows'].values():
        state='ANALYTIC_FAIL' if not p['analytic_pass'] else 'DUPLICATE_HOLD' if p['duplicate_reasons'] else 'PENDING'
        result.append(dict({k:p[k] for k in IDENTITY}, source=p['source'],
            geometry_sha256=p['canonical_geometry_sha256'],split=p['assigned_development_split'],
            target=p['target'],frozen_proxy=p['proxy'],target_cell=p['target_cell'],predicted_cell=p['predicted_cell'],
            state=state,actual=None,actual_cell=None,strict_valid=None,core_eligible=None,strict_joint_hit=None,
            q_emx=None,evidence_class='FROZEN_PROPOSAL_ONLY',physical_chain_verified=False,
            native_observation='NOT_PROVIDED',solver_start_order=None,solver_started_utc=None,
            solver_start_verified=False,closed_utc=None,solver_wall_seconds=None,solver_cpu_seconds=None,
            total_stage_cost_seconds=None,storage_bytes=None))
    return result


def _sum_known(rows, field):
    values=[r[field] for r in rows]
    for x in values:
        _require(x is None or (type(x) in (int,float) and math.isfinite(x) and x>=0), 'Invalid cost')
    return math.fsum(values) if all(x is not None for x in values) else None


def _prefix(ctx, rows, endpoint):
    """All prior proposals and costs, not just successful or solver-only rows."""
    original=[r for r in rows if r['arm_order']<=endpoint]
    actual=[]; seen=set()
    for r in original:
        if r['state']=='STRICT_VALID' and r['core_eligible'] and r['split']=='train':
            h=r['geometry_sha256']
            if h not in seen and h not in ctx['excluded_hashes']:
                seen.add(h)
                actual.append(dict(geometry_hash=h,split='train',frequency_hz=15000000000,
                    strict_lumped_valid=True,evidence_class='FRESH_REAL_EMX',actual=r['actual']))
    gain=coverage_gain(ctx['before'],actual,baseline_hashes=ctx['excluded_hashes'])
    started=[r for r in original if r['solver_start_verified']]
    return dict(candidate_ids=[r['candidate_id'] for r in original],proposal_prefix_end=endpoint,
        proposal_count=len(original),solver_starts=len(started),unique_qualified_train=len(actual),
        state_counts=dict(Counter(r['state'] for r in original)),
        solver_wall_seconds=_sum_known(started,'solver_wall_seconds'),
        solver_cpu_seconds=_sum_known(started,'solver_cpu_seconds'),
        total_stage_cost_seconds=_sum_known(original,'total_stage_cost_seconds'),
        storage_bytes=_sum_known(original,'storage_bytes'),coverage=gain)


def compare(ctx, rows, *, publication_verified=False):
    """Pure arithmetic after source verification; tags alone do not prove physics.

    Production run must obtain rows from this module's native-chain consumer.
    This function itself does not inspect a native source or grant training use.
    """
    _require(type(publication_verified) is bool and len(rows)==64, 'All64 result rows required')
    ids=set(); by_arm={a:[] for a in ARMS}; starts=[]
    for r in rows:
        cid=r['candidate_id']; _require(cid in ctx['rows'] and cid not in ids,'Foreign/duplicate result')
        ids.add(cid); p=ctx['rows'][cid]
        _fields(r,{k:p[k] for k in IDENTITY},'Result identity')
        _fields(r,dict(geometry_sha256=p['canonical_geometry_sha256'],split=p['assigned_development_split'],
            source=p['source'],target=p['target'],frozen_proxy=p['proxy'],target_cell=p['target_cell'],
            predicted_cell=p['predicted_cell'],q_emx=None),'Frozen result fields')
        _require(r['state'] in STATES and type(r['solver_start_verified']) is bool,'Unknown result state/start')
        if not p['analytic_pass']: _require(r['state']=='ANALYTIC_FAIL','Original analytic failure replaced')
        if p['duplicate_reasons']: _require(r['state'] in ('ANALYTIC_FAIL','DUPLICATE_HOLD'),'Frozen duplicate replaced')
        if r['state'] in ('STRICT_VALID','EMX_INVALID'):
            _require(publication_verified and r['physical_chain_verified'] is True,'Physical status is not evidence')
            _require(type(r['strict_valid']) is bool and type(r['core_eligible']) is bool and
                     r['strict_valid']==(r['state']=='STRICT_VALID'),'Strict classification differs')
            _require(isinstance(r['actual'],list) and len(r['actual'])==4 and all(v is None or
                     (type(v) in (int,float) and math.isfinite(v)) for v in r['actual']), 'Actual descriptor vector malformed')
            if r['strict_valid']: _finite4(r['actual'],'actual')
            landed=actual_landing([r['actual'][j] for j in (0,1,3)])
            _require(r['actual_cell']==(list(landed) if landed is not None else None),'Actual EM landing differs')
            core=r['strict_valid'] and actual_landing([r['actual'][j] for j in (0,1,3)]) is not None
            _require(r['core_eligible']==core,'Invalid core flag')
            if p['source']!='SPARSE_TARGETED': _require(r['strict_joint_hit'] is None,'DOE target hit fabricated')
        else:
            _require(r['actual'] is None and r['actual_cell'] is None and r['strict_valid'] is None and
                     r['core_eligible'] is None and r['strict_joint_hit'] is None,'Failure/pending physical numbers fabricated')
        if r['solver_start_verified']:
            _require(publication_verified and r['state'] in (*SOLVER_CLOSED,'SOLVER_PENDING') and
                     type(r['solver_start_order']) is int and r['solver_start_order']>0,
                     'Wrapper/unverified start cannot consume budget')
            _time(r['solver_started_utc'],'actual solver birth')
            if r['state'] in SOLVER_CLOSED:
                _require(_time(r['closed_utc'],'closure')>=_time(r['solver_started_utc'],'birth'),'Closure precedes solver')
            else: _require(r['closed_utc'] is None,'Pending solver marked closed')
            starts.append(r)
        else:
            _require(r['solver_start_order'] is None and r['solver_started_utc'] is None,'Unverified start identity')
        for key in COST_FIELDS: _sum_known([r],key)
        by_arm[r['arm']].append(r)
    _require(ids==set(ctx['rows']),'Missing original denominator')
    for a in ARMS: by_arm[a].sort(key=lambda r:r['arm_order'])
    common=dict(original_denominator=64,original_proposals_per_arm=32,
        scope='DEVELOPMENT_SINGLE_FROZEN_POLICY_PAIR_NOT_FINAL',CI='NOT_ESTIMATED',
        native_actions_performed=False,training_admission=False,q_emx=None,
        source_validation='NOT_PERFORMED_BY_PURE_COMPARE',
        baseline_train=3801,baseline_occupied=159,prior20_not_in_decision=True,
        state_counts=dict(Counter(r['state'] for r in rows)),
        cost_definition='Per-solver elapsed and CPU separate; neither is campaign parallel wall time. All preceding proposal costs retained; missing cost is null.')
    if not publication_verified:
        return dict(common,status='NOT_RUN_NO_VERIFIED_NATIVE_PUBLICATION',solver_starts=None,
            equal_m=None,equal_K=None,actual_coverage=None)
    if any(r['state'] in ('NO_CLOSED_RESULT_IN_CAPTURE','CANDIDATE_FAILURE_UNCLASSIFIED') for r in rows):
        return dict(common,status='CLOSED_CAPTURE_WITH_UNRESOLVED_NATIVE_LEDGER',solver_starts=None,
            equal_m=None,equal_K=None,actual_coverage=None,
            reason='Unobserved or unclassified candidates may have started; closed-result membership is not a complete start ledger.')
    if any(r['state'] in (*SOLVER_CLOSED,'SOLVER_PENDING') and not r['solver_start_verified'] for r in rows):
        return dict(common,status='PHYSICAL_PUBLICATIONS_WITH_UNRESOLVED_NATIVE_START_ORDER',
            solver_starts=None,equal_m=None,equal_K=None,actual_coverage=None,
            reason='Keep verified physical rows, but a reservation/wrapper or unknown native birth is not a confirmed solver-start ledger.')
    starts.sort(key=lambda r:r['solver_start_order'])
    _require([r['solver_start_order'] for r in starts]==list(range(1,len(starts)+1)), 'Duplicate/gapped actual starts')
    for x,y in zip(starts,starts[1:]):
        _require(_time(x['solver_started_utc'],'birth')<=_time(y['solver_started_utc'],'birth'),'Start order reverses birth')
    arm_starts={a:[r for r in starts if r['arm']==a] for a in ARMS}
    for a in ARMS:
        seq=arm_starts[a]
        _require(len(seq)<=16 and [r['arm_order'] for r in seq]==sorted(r['arm_order'] for r in seq),'Start cap/frozen order violated')
    def closed_prefix(a):
        result=[]
        for r in arm_starts[a]:
            preceding=by_arm[a][:r['arm_order']]
            if r['state'] not in SOLVER_CLOSED or any(x['state'] in (*UNSTARTED,'SOLVER_PENDING') for x in preceding): break
            result.append(r)
        return result
    closed={a:closed_prefix(a) for a in ARMS}
    m_common=min(map(len,closed.values()))
    def at_m(m):
        reached=all(len(closed[a])>=m for a in ARMS)
        return dict(m=m,status='MATCHED_CLOSED' if reached else 'NOT_REACHED_OR_PENDING',
            arms={a:_prefix(ctx,by_arm[a],closed[a][m-1]['arm_order']) for a in ARMS} if reached else None)
    kval={}
    for a in ARMS:
        ends=[]; seen=set()
        end=closed[a][-1]['arm_order'] if closed[a] else 0
        for r in by_arm[a][:end]:
            h=r['geometry_sha256']
            if r['state']=='STRICT_VALID' and r['core_eligible'] and r['split']=='train' and h not in ctx['excluded_hashes'] and h not in seen:
                ends.append(r['arm_order']); seen.add(h)
        kval[a]=ends
    k_common=min(map(len,kval.values()))
    def at_k(k):
        reached=all(len(kval[a])>=k for a in ARMS)
        return dict(K=k,status='MATCHED_QUALIFIED_TRAIN_PREFIX' if reached else 'NOT_REACHED',
            not_equal_cost=True,arms={a:_prefix(ctx,by_arm[a],kval[a][k-1]) for a in ARMS} if reached else None)
    return dict(common,status='DESCRIPTIVE_VERIFIED_CUTOFF_NOT_TRAINING_ADMISSION',solver_starts=len(starts),
        equal_m=dict(primary=at_m(16),maximum_common=at_m(m_common) if m_common else None),
        equal_K=dict(primary=at_k(4),maximum_common=at_k(k_common) if k_common else None),
        actual_coverage={a:_prefix(ctx,by_arm[a],closed[a][-1]['arm_order']) if closed[a] else None for a in ARMS})


def consume_closed_capture(reader, capture_pin, ctx, *, expected_release, expected_owner_config):
    """Read the existing owner capture, retaining all64 and no invented start rank.

    The caller-frozen release/config are required independently of result content.
    The current capture explicitly does not reconstruct the complete start ledger.
    """
    from .eucap15_controlled_evidence import inspect_closed_result
    capture=reader.document(capture_pin)
    _fields(capture,dict(schema='eucap15_controlled64_closed_metadata_capture.v1',
        controlled_manifest=ctx['manifest'],controlled_intent=ctx['intent'],original_proposal_denominator=64,
        actual_native_start_order='NOT_RECONSTRUCTED',native_started_utc=None,
        raw_artifacts_copied=False,physical_qa_rerun=False,production_accepted_added=0), 'Owner closed capture')
    start=_time(capture['capture_started_utc'],'capture start')
    _require(start<=_time(capture['capture_completed_utc'],'capture end'), 'Capture clock reversed')
    records=capture['records']
    _require(isinstance(records,list) and len(records)==64, 'Closed capture must retain all64')
    rows=initial_rows(ctx); sources=[]; statuses=Counter(); unknown=[]; observed=0
    for row,record in zip(rows,records):
        cid=row['candidate_id']; proposal=ctx['rows'][cid]
        _fields(record,dict(request_id=row['request_id'],candidate_id=cid,arm=row['arm'],
            original_global_order=row['global_order']), 'Capture order/identity')
        row.update(capture_state=record['capture_state'],source_result=record['source_result'],
            terminal_publication_verified=False,native_birth_identity_verified=False,
            native_count_in_this_result=None)
        if record['capture_state']=='NO_CLOSED_RESULT_IN_THIS_CAPTURE':
            _fields(record,dict(source_result=None,result=None,actual_native_starts=None), 'Missing capture item')
            if proposal['local_dispatch_eligible']: row['state']='NO_CLOSED_RESULT_IN_CAPTURE'
            unknown.append(row['request_id']); continue
        _require(record['capture_state']=='PINNED_CLOSED_RESULT' and isinstance(record['source_result'],dict),
                 'Unknown capture state')
        value=reader.document(record['source_result'])
        _require(_same(record['result'],value), 'Embedded RESULT differs from its exact source pin')
        checked=inspect_closed_result(reader,cid,record['source_result'],ctx,
            owner_root=capture['owner_root'],capture_completed_utc=capture['capture_completed_utc'],
            expected_release=expected_release,expected_owner_config=expected_owner_config)
        _require(_same(record['actual_native_starts'],checked['native_count_in_this_result']),
                 'Capture native count differs from checked result')
        row.update(checked)
        if checked['state'] in ('STRICT_VALID','EMX_INVALID'):
            row['physical_chain_verified']=True
            landing=actual_landing([row['actual'][j] for j in (0,1,3)])
            row['actual_cell']=list(landing) if landing is not None else None
        sources.append(record['source_result']); statuses[value['status']]+=1
        n=checked['native_count_in_this_result']
        if n is None: unknown.append(row['request_id'])
        else: observed+=n
    _fields(capture,dict(N_closed=len(sources),N_not_observed_closed=64-len(sources),
        closed_status_counts=dict(statuses),source_result_pins=sources,
        requests_with_unknown_native_count=unknown,observed_native_births_in_closed_results=observed,
        native_total_known=not unknown,actual_native_starts=None if unknown else observed,
        status='COMPLETE_CLOSED_METADATA_CAPTURE' if len(sources)==64 else 'PARTIAL_CLOSED_METADATA_CAPTURE'),
        'Capture accounting does not match all original rows')
    reader.recheck()
    summary=compare(ctx,rows,publication_verified=bool(sources))
    summary.update(native_result_consumer='CLOSED_CAPTURE_AND_PHYSICAL_CHAIN_INSTALLED',
        capture=capture_pin,N_closed_result_sources=len(sources),N_unobserved_closed=64-len(sources),
        observed_native_births_in_closed_results=observed,
        actual_native_start_ledger='NOT_RECONSTRUCTED_BY_THIS_CAPTURE',
        physical_rows=sum(r['physical_chain_verified'] for r in rows),
        capture_completed_utc=capture['capture_completed_utc'],
        capture_time_is_not_native_birth_time=True,model_loads=0,target_generation=0)
    return dict(rows=rows,summary=summary)


def run_closed_capture(spec_path, spec_sha, output):
    """Finite no-clobber read of a pinned capture and exact original-to-mirror map."""
    from . import eucap15_controlled_evidence as evidence
    spec_path,output=Path(spec_path).absolute(),Path(output).absolute()
    no_symlinks(spec_path); no_symlinks(output)
    sp=pin(spec_path); _require(sp['sha256']==spec_sha,'Spec SHA differs')
    spec=_strict_json(spec_path.read_bytes()); _require(pin(spec_path)==sp,'Spec changed during read')
    _require(set(spec)=={'schema','intent','preparation_manifest','path_map','implementation',
                         'capture','expected_release','expected_owner_config'} and
             spec['schema']=='eucap15_controlled_closed_capture_consumer.v1','Exact closed capture spec required')
    output.mkdir(parents=False,exist_ok=False)
    reader=MirrorReader(spec['path_map'])
    try:
        implementation=spec['implementation']
        _require(isinstance(implementation,list) and pin(Path(__file__).resolve()) in implementation and
                 pin(Path(evidence.__file__).resolve()) in implementation,'Both active reader implementations must be pinned')
        for p in implementation: _require(pin(Path(p['path']))==p,'Implementation changed')
        # Bind external authority even for a capture that currently has only holds.
        release=reader.document(spec['expected_release'])
        _require(release['config']==spec['expected_owner_config'],'Frozen release/config differ')
        config=reader.document(spec['expected_owner_config'])
        _require(config['original_manifest']==spec['preparation_manifest'],'Owner belongs to a different frozen frame')
        ctx=evidence.load_context(reader,spec['intent'],spec['preparation_manifest'])
        result=consume_closed_capture(reader,spec['capture'],ctx,
            expected_release=spec['expected_release'],expected_owner_config=spec['expected_owner_config'])
        put_json(output/'REQUEST_RESULTS.json',dict(schema='eucap15_controlled64_request_rows.v1',rows=result['rows']))
        fields=sorted({k for row in result['rows'] for k in row})
        flat=[{k:json.dumps(r.get(k),allow_nan=False,sort_keys=True) if isinstance(r.get(k),(dict,list))
               else r.get(k) for k in fields} for r in result['rows']]
        put_csv(output/'REQUEST_RESULTS.csv',flat,fields)
        put_json(output/'SUMMARY.json',result['summary'])
        reader.recheck(); _require(pin(spec_path)==sp,'Spec changed during consumption')
        for p in implementation: _require(pin(Path(p['path']))==p,'Implementation changed during consumption')
        put_json(output/'READ_SOURCE_PINS.json',list(reader.evidence.values()))
        artifacts={p.name:pin(p) for p in output.iterdir() if p.is_file()}
        receipt=dict(schema='eucap15_controlled64_closed_capture_consumer_receipt.v1',
            status='PASS_SCOPED_CLOSED_CAPTURE_NOT_FULL_START_LEDGER',spec=sp,implementation=implementation,
            artifacts=artifacts,source_bytes_unchanged=True,native_actions_performed=False,
            model_loads=0,target_generation=0,training_admission=False)
        put_json(output/'RECEIPT.json',receipt)
        with (output/'SHA256SUMS').open('x') as stream:
            for p in sorted(output.iterdir()):
                if p.is_file() and p.name!='SHA256SUMS':stream.write(pin(p)['sha256']+'  '+p.name+'\n')
        return receipt
    except Exception as error:
        put_json(output/'FAILURE_RECEIPT.json',dict(status='FAIL_PRESERVED_NO_RETRY',spec=sp,
            error=repr(error),traceback=traceback.format_exc(),native_actions_performed=False))
        raise


def run_preflight(spec_path, spec_sha, output):
    """Only the frozen64 metadata CLI; native-result ingestion is not invoked.

    No CLI flag accepts publication_verified or supplied physical results. A
    future owner-schema adapter must verify complete64 observations and the real
    GDS/DRC/56-point chain before calling compare(publication_verified=True).
    """
    spec_path, output=Path(spec_path).absolute(),Path(output).absolute()
    no_symlinks(spec_path); no_symlinks(output)
    sp=pin(spec_path)
    _require(sp['sha256']==spec_sha,'Spec SHA differs')
    spec=_strict_json(spec_path.read_bytes())
    _require(pin(spec_path)==sp,'Spec changed during read')
    _require(set(spec)=={'schema','intent','preparation_manifest','path_map','implementation'} and
             spec['schema']=='eucap15_controlled_context_preflight.v1',
             'Only metadata preflight spec accepted; native result assertions are not an installed interface')
    output.mkdir(parents=False,exist_ok=False)
    reader=MirrorReader(spec['path_map'])
    try:
        _require(isinstance(spec['implementation'],list) and spec['implementation'],'Pinned implementation required')
        _require(pin(Path(__file__).resolve()) in spec['implementation'],'Running implementation not bound')
        for p in spec['implementation']:
            _require(pin(Path(p['path']))==p,'Implementation changed')
        ctx=load_context(reader,spec['intent'],spec['preparation_manifest'])
        rows=initial_rows(ctx); summary=compare(ctx,rows)
        summary.update(metadata_preflight='PASS_FROZEN64_ONLY',native_result_consumer='NOT_INVOKED_METADATA_ONLY_ENTRYPOINT',
            actual_native_result_reads=0,model_loads=0,target_generation=0,
            original_failure_ids=[r['candidate_id'] for r in rows if r['state']=='ANALYTIC_FAIL'])
        put_json(output/'REQUEST_RESULTS.json',dict(schema='eucap15_controlled64_request_rows.v1',rows=rows))
        put_json(output/'SUMMARY.json',summary)
        reader.recheck()
        _require(pin(spec_path)==sp,'Spec changed during execution')
        for p in spec['implementation']:
            _require(pin(Path(p['path']))==p,'Implementation changed during execution')
        put_json(output/'READ_SOURCE_PINS.json',list(reader.evidence.values()))
        artifacts={p.name:pin(p) for p in output.iterdir() if p.is_file()}
        receipt=dict(schema='eucap15_controlled64_context_preflight_receipt.v1',status='PASS_METADATA_ONLY_PHYSICAL_NOT_RUN',
            created_utc=datetime.now(timezone.utc).isoformat(),spec=sp,implementation=spec['implementation'],artifacts=artifacts,
            source_bytes_unchanged=True,native_result_consumer='NOT_INVOKED_METADATA_ONLY_ENTRYPOINT',
            source_validation='FROZEN_PREPARATION_METADATA_ONLY',source_files_read=len(reader.evidence),
            native_actions_performed=False,model_loads=0,target_generation=0,training_admission=False)
        put_json(output/'RECEIPT.json',receipt)
        with (output/'SHA256SUMS').open('x') as stream:
            for p in sorted(output.iterdir()):
                if p.is_file() and p.name!='SHA256SUMS': stream.write(pin(p)['sha256']+'  '+p.name+'\n')
        return receipt
    except Exception as error:
        put_json(output/'FAILURE_RECEIPT.json',dict(status='FAIL_PRESERVED_NO_RETRY',spec=sp,error=repr(error),
            traceback=traceback.format_exc(),physical_result_consumer='NOT_INSTALLED',
            native_actions_performed=False))
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['validate-context','consume-closed'])
    parser.add_argument('--spec',required=True)
    parser.add_argument('--spec-sha',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    receipt=(run_preflight if args.command=='validate-context' else run_closed_capture)(args.spec,args.spec_sha,args.out)
    print(json.dumps(dict(status=receipt['status'],output=args.out),allow_nan=False))


if __name__=='__main__':
    main()
