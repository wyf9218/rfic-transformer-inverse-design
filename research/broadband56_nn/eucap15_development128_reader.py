"""Read an independently QA-frozen development128 pilot into an initial ledger.

This is not a native-result consumer or live monitor. It does not load models,
datasets, split members, test targets, owner returns, or physics. The inherited
candidate QA is checked, not rerun. All 128 original requests remain present.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

from .eucap15_selected_evidence import _strict_json, _same
from .eucap15_selected_metrics import summarize, SCORE_SPANS
from .frequency_physical_statistics import canonical, csv_write, save, require
from .io import utc_now

SCOPE = 'DEVELOPMENT_CURRENT_SNAPSHOT'
MODEL_ID = 'f15-development6329-3x256-seed17-9d69d7ebfc66'
MODEL_ROLE = 'DEVELOPMENT_BASELINE_NOT_FINAL'
WEIGHT_SHAS = {
    'forward': '23034fcd736608f7e81277d4dc555bcd6ebc89cfb36f5bdc7fde72e3bdeb1b9d',
    'inverse': '8106f2f8d0274fc7985def30dbf6c515b94cb70daa3e72b362f5655767c07ab9'}
DATA_SHA = '4dc6e91a644db2aca0a580958ac8b31e1e26ed73497b935145202b07227ef580'
SPLIT_SHA = '92ba524fd139dfb6f9ab93bd745aa26bc7fdcde68687ae132ac1cc0b63064433'
TAU = [.125, .125, 1., .04000000000000001]
ECDF_FIELDS = ['comparison','feature','unit','request_id','candidate_id','q_proxy',
    'rank','n','N_original','absolute_error','normalized_absolute_error','cdf','ci_status','definition']


def path(value):
    value = Path(value)
    require(value.is_absolute() and '..' not in value.parts, 'absolute nontraversing path required')
    require(not any(p.is_symlink() for p in (value, *value.parents)), 'symlink source/output forbidden')
    return value


def pin(source):
    source = path(source)
    require(source.is_file(), 'regular source required')
    with source.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path':str(source),'sha256':sha,'bytes':source.stat().st_size}


def pin_shape(item):
    require(isinstance(item,dict) and {'path','sha256','bytes'} <= item.keys(), 'exact pin required')
    path(item['path'])
    require(type(item['bytes']) is int and item['bytes']>=0 and
        isinstance(item['sha256'],str) and re.fullmatch('[0-9a-f]{64}',item['sha256']), 'invalid pin')
    return {key:item[key] for key in ('path','sha256','bytes')}


def fields(document, expected, label):
    require(isinstance(document,dict), label+' object required')
    for key,value in expected.items():
        require(key in document and _same(document[key],value), label+' mismatch: '+key)


def load_context(manifest_pin, qa_pin):
    """Only revalidate consumed source pins and frozen selected identities.

    No random-frame regeneration, proxy-score recomputation, grid/analytic gate
    rerun or model access. The independently signed-off candidate QA supplies
    that prior evidence; this bounded reader prevents later identity drift.
    """
    consumed = {}

    def read(item, *, bound=None, jsonl=False):
        item = pin_shape(item)
        target = path(item['path'])
        require(target.suffix in {'.json','.jsonl'}, 'metadata JSON/JSONL only')
        require(target.name not in {'splits.json','dataset.npz'}, 'dataset/split members forbidden')
        if bound is not None:
            require(bound.get(item['path'])==item, 'source not exactly bound by independent QA')
        require(pin(target)==item, 'source SHA/bytes changed')
        raw = target.read_bytes()
        require(len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256'], 'source changed during read')
        value = [_strict_json(line) for line in raw.splitlines()] if jsonl else _strict_json(raw)
        require(pin(target)==item, 'source changed after parse')
        consumed[item['path']] = item
        return value

    qa = read(qa_pin)
    fields(qa,{'schema':'eucap15_development128_independent_candidate_qa.v1','status':'GO',
        'scope':'CANDIDATE_LIST_METADATA_ARITHMETIC_ONLY','FINAL':False,
        'REAL_EMX_VALIDATION':'NOT_RUN','native_admission':'NOT_APPROVED_BY_THIS_QA',
        'physical_correctness':'NOT_VALIDATED'},'independent QA')
    bound = {}
    for item in qa['source_pins']:
        item = pin_shape(item)
        require(item['path'] not in bound, 'duplicate QA source pin')
        bound[item['path']] = item
    manifest = read(manifest_pin,bound=bound)
    fields(manifest,{'schema':'eucap15_current_development_selected_handoff.v1',
        'status':'NOT_DISPATCHED_REQUIRES_OWNER_DEVELOPMENT_SCOPE_ADAPTER',
        'dataset_scope':SCOPE,'model_role':MODEL_ROLE,'model_id':MODEL_ID,
        'frequency_ghz':15,'label_mode':'STRICT_LUMPED','N_original_requests':128,
        'N_proxy_slots':1408,'N_q_proxy_selected':128,'N_selected_analytic_pass':93,
        'N_native_started':0,'FINAL':False,'REAL_EMX_VALIDATION':'NOT_RUN',
        'no_failure_replacement':True,'production_campaign_membership':False},'pilot')
    identity = read(manifest['model_identity'],bound=bound)
    fields(identity,{'schema':'eucap15_current_development_model_identity.v1',
        'experiment_class':SCOPE,'model_role':MODEL_ROLE,'model_id':MODEL_ID,
        'source_rows':6329,'gradient_train_rows':3801,'validation_rows':1269,
        'test_rows_count_only':1259,'FINAL':False,'REAL_EMX_VALIDATION':'NOT_RUN',
        'selection_basis':'PREDECLARED_FIRST_COMPLETED_3X256_SEED17_NOT_ABLATION_WINNER'},'model identity')
    # These are inherited metadata identities, deliberately not file reads.
    for role,sha in WEIGHT_SHAS.items():
        require(pin_shape(identity['best_weights'][role])['sha256']==sha,'predeclared baseline weights differ')
    require(identity['source_dataset_inherited_pin']['sha256']==DATA_SHA and
        identity['source_split_inherited_pin']['sha256']==SPLIT_SHA,'inherited data/split identity differs')
    freeze = read(manifest['freeze'],bound=bound)
    fields(freeze,{'schema':'eucap15_current_development_pilot_freeze.v1',
        'status':'FROZEN_BEFORE_ANY_PREDICTION_AND_NATIVE','model_id':MODEL_ID,
        'model_identity':manifest['model_identity'],'frequency_ghz':15,
        'N_original_requests':128,'N_proxy_slots':1408,
        'config':{'dataset_scope':SCOPE,'allow_extrapolation':True},
        'score_scale':list(SCORE_SPANS),'absolute_tolerances':TAU,
        'selection':'ALL_ELEVEN_FINITE_SCORES_THEN_MINIMUM_NO_ANALYTICAL_RERANK',
        'optimizer_updates':0,'REAL_EMX_VALIDATION':'NOT_RUN'},'freeze')
    require(freeze['artifacts']['normalizer.json']==identity['normalizer'] and
        freeze['artifacts']['geometry_contract.json']==identity['geometry_contract'], 'normalizer/geometry pin mismatch')
    require(len(manifest['requests'])==128, 'original128 required')
    rows, ids, candidate_ids = [], set(), set()
    for index,item in enumerate(manifest['requests']):
        rid = item['request_id']; q = item['q_proxy']
        require(rid not in ids and item['candidate_id'] not in candidate_ids,'duplicate selected identity')
        ids.add(rid);candidate_ids.add(item['candidate_id'])
        fields(item,{'request_order':index,'N_original_requests':128,'N_original_proxy_candidates':11,
            'native_started':False,'no_failure_replacement':True,'production_campaign_membership':False,
            'q_emx':None,'physical_stages':{'gds':'NOT_RUN','calibre':'NOT_RUN','emx':'NOT_RUN'}},'selected')
        require(type(q) is int and 10<=q<=20 and item['record_line_number']==q-9,'selected Q/line mismatch')
        records = read(item['source_records'],bound=bound,jsonl=True)
        require(len(records)==11 and [r['q_target'] for r in records]==list(range(10,21)), 'original11 slots changed')
        for record in records:
            fields(record,{'request_id':rid,'model_id':MODEL_ID,'dataset_scope':SCOPE,
                'frequency_ghz':15,'target_source':'DEVELOPMENT_CURRENT_SNAPSHOT_UNIFORM_TRIPLE',
                'q_proxy':q,'proxy_preselected':record['q_target']==q,
                'candidate_id':f"{rid}-q{record['q_target']:02d}"},'candidate identity')
        chosen = records[q-10]
        require(canonical(chosen)==item['source_record_canonical_sha256'],'selected original record SHA differs')
        fields(chosen,{'candidate_id':item['candidate_id'],
            'candidate_geometry_identity_sha256':item['candidate_geometry_identity_sha256'],
            'target':item['selected_target'],'grid_proxy':item['selected_grid_proxy'],
            'analytic_grid':item['selected_analytic_pass'],'evidence_source':'SELF_PROXY',
            'emx_status':'NOT_RUN','actual_response':None},'selected binding')
        eligible = item['selected_analytic_pass']
        require(type(eligible) is bool, 'analytic state must be boolean')
        require(item['state']==('PENDING_OWNER_ACCEPTANCE_NOT_DISPATCHED' if eligible else
            'NOT_SUBMITTED_SELECTED_ANALYTIC_FAIL'),'frozen selected status differs')
        rows.append(dict(request_id=rid,candidate_id=item['candidate_id'],
            candidate_geometry_identity_sha256=item['candidate_geometry_identity_sha256'],
            q_proxy=q,q_emx=None,target=item['selected_target'],grid_proxy=item['selected_grid_proxy'],
            state='PENDING' if eligible else 'ANALYTIC_FAIL',actual=None,strict_joint_hit=None,touchstone_sha=None,
            status_detail='NO_NATIVE_EVIDENCE_CONSUMED_NOT_A_LIVE_STATUS' if eligible else 'FROZEN_ANALYTIC_FAIL_NO_REPLACEMENT',
            selected_support_status=item['selected_support_status'],
            selected_outside_support_by_feature=item['selected_outside_support_by_feature'],
            original_source_record=item['source_records'],full11_status='NOT_EVALUATED'))
    require(Counter(r['state'] for r in rows)=={'ANALYTIC_FAIL':35,'PENDING':93}, 'frozen128 analytical accounting differs')
    for item in consumed.values():require(pin(item['path'])==item,'context source changed')
    return SimpleNamespace(manifest=manifest,freeze=freeze,identity=identity,qa=qa,rows=rows,
        sources=list(consumed.values()),manifest_pin=manifest_pin,qa_pin=qa_pin)


def build(manifest_pin, qa_pin, out, *, publication_index=None):
    # Deliberately reject even a supplied empty index: no native schema installed.
    require(publication_index is None, 'NATIVE_RESULT_CONSUMER_NOT_INSTALLED; no native publication input accepted')
    out = path(out)
    require(out.parent.is_dir(), 'existing output parent required')
    require(not out.is_relative_to(Path(__file__).resolve().parents[2]), 'outputs must be outside source worktree')
    out.mkdir(exist_ok=False)
    try:
        save(out/'INTENT.json',dict(created_utc=utc_now(),manifest=manifest_pin,independent_qa=qa_pin,
            native_results_consumer='NOT_INSTALLED',mode='FROZEN_INITIAL_LEDGER_NOT_CURRENT_LIVE_STATUS'))
        ctx = load_context(manifest_pin,qa_pin)
        result = summarize(ctx.rows,score_spans=ctx.freeze['score_scale'],tolerances=ctx.freeze['absolute_tolerances'])
        result['summary'].update(dataset_scope=SCOPE,model_role=MODEL_ROLE,model_id=MODEL_ID,
            frequency_ghz=15,source_rows=6329,gradient_train_rows=3801,validation_rows=1269,test_rows_count_only=1259,
            REAL_EMX_VALIDATION='NOT_RUN',FINAL=False,mode='FROZEN_INITIAL_LEDGER_NOT_CURRENT_LIVE_STATUS',
            native_result_consumer='NOT_INSTALLED_AWAIT_OWNER_SCHEMA',owner_acceptance_not_physical_success=True,
            owner_intake_read=False,publication_index=None,model_loads=0,model_inferences=0,native_calls=0,
            source_data_arrays_read=False,original_q_proxy_unchanged=True,figures_created=0)
        csv_write(out/'REQUEST_RESULTS.csv',ctx.rows)
        csv_write(out/'PHYSICAL_METRICS.csv',result['metric_rows'])
        csv_write(out/'ERROR_ECDF.csv',result['ecdf_rows'],fields=ECDF_FIELDS)
        save(out/'SUMMARY.json',result['summary'])
        for item in ctx.sources:require(pin(item['path'])==item,'source changed during initial ledger build')
        artifacts = [pin(p) for p in sorted(out.iterdir()) if p.is_file()]
        implementations = [pin(Path(__file__)),pin(Path(summarize.__code__.co_filename)),
            pin(Path(_strict_json.__code__.co_filename)),pin(Path(csv_write.__code__.co_filename)),
            pin(Path(utc_now.__code__.co_filename))]
        save(out/'RECEIPT.json',dict(schema='eucap15_development128_initial_ledger_receipt.v1',
            status='PASS_INITIAL_128_ACCOUNTING_NOT_NATIVE_RESULTS',created_utc=utc_now(),
            sources=ctx.sources,implementation=implementations,artifacts=artifacts,
            N_original_requests=128,N_analytic_fail=35,N_pending_no_evidence=93,
            model_loads=0,model_inferences=0,new_training_updates=0,native_calls=0,
            source_data_arrays_read=False,REAL_EMX_VALIDATION='NOT_RUN',FINAL=False,
            native_result_consumer='NOT_INSTALLED_AWAIT_OWNER_SCHEMA'))
        with (out/'SHA256SUMS').open('x') as stream:
            for item in artifacts+[pin(out/'RECEIPT.json')]:
                stream.write(item['sha256']+'  '+Path(item['path']).name+'\n')
        return result['summary']
    except Exception as error:
        save(out/'FAILURE_RECEIPT.json',dict(status='NO_GO_PRESERVED',error=repr(error),created_utc=utc_now(),
            native_calls=0,model_inferences=0))
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--qa-receipt',type=Path,required=True)
    parser.add_argument('--qa-sha256',required=True)
    parser.add_argument('--publication-index',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    require(args.publication_index is None, 'NATIVE_RESULT_CONSUMER_NOT_INSTALLED')
    manifest=pin(args.manifest);qa=pin(args.qa_receipt)
    require(manifest['sha256']==args.manifest_sha256 and qa['sha256']==args.qa_sha256,'explicit input SHA mismatch')
    result=build(manifest,qa,args.out)
    print(json.dumps({'status':'INITIAL_LEDGER_WRITTEN','output':str(args.out),
        'N_original_requests':result['N_original_requests'],'REAL_EMX_VALIDATION':'NOT_RUN',
        'native_result_consumer':'NOT_INSTALLED_AWAIT_OWNER_SCHEMA'}))


if __name__=='__main__':main()
