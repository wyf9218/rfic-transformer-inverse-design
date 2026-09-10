"""Read the exact fixed13 publication; no physics, training or data admission.

The old matched16 analysis is not rerun. Eleven existing chains are reconciled
with its unchanged validator; two original GDS failures retain null labels.
This delta is not a balanced native-attempt prefix or a coverage experiment.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import traceback

R = Path('/Users/wyf/Documents/模拟变压器AI反向建模')
E = R/'reports/eucap15ghz_20260908T220300Z'
V = R/'github_worktrees/eucap15-mlp-capacity-20260909'
P = R/'reports/eucap15_native_owner_20260909T062500Z/fixed13_delta_export_20260910T003834Z'
sys.path.insert(0, str(V))
from research.broadband56_nn.eucap15_acquisition_evidence import (
    MirrorReader, load_frozen_batch, inspect_chain, CONFIG_SHA)
from research.broadband56_nn.eucap15_author_sources import load_json, no_symlinks, put_json, put_csv

PREFIX = 'eucap15_dev_acquisition_pair_20260909_v1-'
FIXED_SUFFIXES = ('EXPLORATION-025','GEOMETRY_DOE-008','GEOMETRY_DOE-017',
    'GEOMETRY_DOE-032','GEOMETRY_DOE-065','GEOMETRY_DOE-069','GEOMETRY_DOE-107',
    'GEOMETRY_DOE-127','SPARSE-006-q11','SPARSE-051-q12','SPARSE-058-q10',
    'SPARSE-088-q10','SPARSE-098-q14')
FIXED_IDS = tuple(PREFIX+s for s in FIXED_SUFFIXES)
FAIL_IDS = {PREFIX+'EXPLORATION-025', PREFIX+'SPARSE-058-q10'}
FAILED_CHECKS = ['ground_clearance_pass','foundry_power_line_contract_pass','foundry_via_stack_and_landing_pad_pass']
TOP = {
    'export': (P/'EXPORT_RECEIPT.json','ca43bfabf6e37ac86339c9edef7089af3ef38a55238105aeff5858556aa43556'),
    'closed': (P/'CLOSED_ITEMS.json','ce9f04e10bbc00908a19d6e28a07556d89ec081fa56efbd22b7005a2d43bea7d'),
    'map': (P/'SOURCE_TO_LOCAL_PATH_MAP.json','290db449e9ceb929e9221f54c0b5f8c54bd1d5c12c2d0fcb7da7de35af2eb8f8'),
    'external': (P/'EXTERNAL_REFERENCES.json','b90d1d7faa4481afd4d23e549a97adc02cc11a8577a6fc4d45571c7a6f94eac9'),
    'index': (P/'SOURCE_INDEX.json','1100df7ef2e5d9f30874ba98ad6d3d71548006ad3e295eeac3566d5f00ac2e52'),
    'old_map': (R/'reports/eucap15_native_owner_20260909T062500Z/acquisition_prefix16_export_v1/SOURCE_TO_LOCAL_PATH_MAP.json',
        '430f9841b54727b8b4ae1c47f0ccc038b1e5dfcbd080719adb71c2dc26ca6a75'),
    'supplement': (E/'acquisition_prefix16_statistics_20260909_v1/run_v2/SOURCE_PATH_SUPPLEMENT.json',
        '835416f1c782bdc0da4e44e9bfee29b007333679dde377b01190a4ef1580c57a')}
RUNTIME = {
    V/'research/broadband56_nn/eucap15_acquisition_evidence.py': '2dc03ec9b10b4f10b22069cdb222a6253ae5026dfbe6d8784212ba1dfacae6a0',
    V/'research/broadband56_nn/eucap15_selected_evidence.py': 'fce24a273753c4ab137b93060c8c3a9adb85666e77d8c9ac36000de33d03be5f'}


def require(ok, message):
    if not ok: raise ValueError(message)


def pin(path):
    path=Path(path); require(path.is_absolute() and '..' not in path.parts,'Exact absolute path required')
    no_symlinks(path); raw=path.read_bytes()
    return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


def fields(actual, expected, title):
    for key,value in expected.items():
        require(key in actual and actual[key]==value, title+': '+key)


def unique(rows, key):
    result={}
    for row in rows:
        name=row[key]; require(isinstance(name,str) and name and name not in result,'Duplicate/missing '+key)
        result[name]=row
    return result


def normalized_entry(item, proposal):
    fields(item,{k:proposal[k] for k in ('candidate_id','request_id','q_proxy')},'Frozen candidate')
    require(proposal['local_dispatch_eligible'] is True and proposal['analytic_pass'] is True,'Held candidate is not a native input')
    require(item['evidence_status'] in ('CANDIDATE_PHYSICAL_CHAIN_BOUND','FAILURE_EVIDENCE_BOUND'),'Unknown terminal')
    if item['evidence_status']=='FAILURE_EVIDENCE_BOUND':
        fields(item,dict(original_status='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION',
            failure_stage='ACTUAL_GDS_AUDIT',original_error='ACTUAL_GDS_AUDIT_REJECTED',failed_checks=FAILED_CHECKS),'Frozen failure')
        require('feature' not in item and 's4p' not in item,'Failed GDS cannot carry claimed physical output')
        return None
    fields(item,dict(original_status='FRESH_EMX_EXTRACTED',geometry_sha256=proposal['canonical_geometry_sha256'],
                    production_accepted=False),'Closed physical identity')
    require(type(item['strict_valid_source_flag']) is bool and type(item['core15_eligible_source_flag']) is bool,'Exact validity flags required')
    return dict(item,result=item['original_result'],arm=proposal['arm'],
                strict_valid=item['strict_valid_source_flag'],core_eligible=item['core15_eligible_source_flag'])


def merge_external(paths, external, old_map, supplements):
    """Only explicit external keys and prior named supplements, never all old data."""
    result=dict(paths); expected={}
    for entry in external:
        require(entry['status']=='EXTERNAL_CONTRACT_OR_METADATA_PIN_REFERENCED_NOT_REREAD','External status changed')
        p=entry['source']; key=p['path']
        require(key not in expected and key not in result and key in old_map,'Duplicate/conflicting/missing external path')
        expected[key]=p; result[key]=old_map[key]
    for item in supplements:
        p,resolved=item['original'],item['resolved']; key=p['path']
        require(p['sha256']==resolved['sha256'] and p['bytes']==resolved['bytes'],'Supplement identity changed')
        require(key not in expected,'Duplicate external supplement')
        require(key not in result or result[key]==resolved['path'],'Conflicting supplement path')
        expected[key]=p; result[key]=resolved['path']
    return result,expected


def failure_row(reader, item, proposal, batch, index):
    """Candidate-bound saved rejection only, not a new GDS audit."""
    require(normalized_entry(item,proposal) is None,'Failure branch requires rejection')
    cid=proposal['candidate_id']; sha=hashlib.sha256(cid.encode()).hexdigest(); geom=proposal['canonical_geometry_sha256']
    terminal=reader.document(item['original_result'])
    fields(terminal,dict(status=item['original_status'],request_id=proposal['request_id'],candidate_id=cid,
        q_proxy=proposal['q_proxy'],error=item['original_error']),'Rejected RESULT')
    base=Path(item['original_result']['path']).parent
    require(base.name==proposal['request_id'] and Path(item['original_result']['path']).name=='RESULT.json','Failure path identity')
    audit_path=str(base/'gds_audit/REQUEST_GDS_AUDIT.json'); require(audit_path in index,'Unpinned failure audit')
    audit=reader.document(index[audit_path])
    context=dict(request_id=proposal['request_id'],frequency_ghz=15,q_proxy=proposal['q_proxy'],
        model_id='ACQUISITION_RECIPE_BOUND_NOT_MODEL_INFERRED_HERE',dataset_scope='DEVELOPMENT_ACQUISITION_PAIR',
        target_source=proposal['source'],arm=proposal['arm'],arm_order=proposal['arm_order'],global_order=proposal['global_order'])
    fields(audit,dict(schema='eucap15_acquisition_gds_audit.v1',request=context,N_logical=1,N_audit_attempted=1),'Failure GDS audit')
    sources=audit['source_pins']; fields(sources,{k:batch[k] for k in ('manifest','recipe')},'Failure source')
    fields(sources,dict(proposals=batch['proposals']),'Failure proposal source')
    require(sources['private_config']['sha256']==CONFIG_SHA,'Failure config changed')
    for p in sources.values(): reader.read(p)
    require(len(audit['records'])==1,'Failure is not candidate-specific'); record=audit['records'][0]
    fields(record,dict(candidate_id=cid,candidate_id_sha256=sha,candidate_geometry_identity_sha256=geom,
        analytic_grid=True,status='FAIL',audit_attempted=True,cadence_routed=True,calibre_eligible=False,
        failed_checks=item['failed_checks']),'Rejected candidate')
    fields(record['original_record'],proposal,'Original failure proposal')
    actual=reader.document(record['geometry_audit'])
    fields(actual,dict(overall_status='FAIL',candidate_id_sha256=sha,candidate_geometry_identity_sha256=geom,
        gds_path=record['gds']['path'],gds_sha256=record['gds']['sha256']),'Actual GDS rejection')
    require(all(type(v) is bool for v in actual['checks'].values()) and
        [k for k,v in actual['checks'].items() if not v]==item['failed_checks'],'Failure checks changed')
    for p in (record['gds'],record['port_manifest']): reader.read(p)
    return dict(candidate_id=cid,request_id=proposal['request_id'],arm=proposal['arm'],
        arm_order=proposal['arm_order'],global_order=proposal['global_order'],source=proposal['source'],
        geometry_sha256=geom,q_proxy=proposal['q_proxy'],state='GDS_FAIL',actual=None,strict_valid=None,
        core_eligible=None,descriptor_valid=None,physics_qa_pass=None,below_half_srf=None,q10_to20_supported=None,
        strict_joint_hit=None,target_errors_defined=False,emx_minus_target=None,emx_minus_proxy=None,
        target=proposal['target'],frozen_proxy=proposal['proxy'],
        feature=None,s4p=None,original_result=item['original_result'],failure_stage=item['failure_stage'],
        failed_checks=item['failed_checks'],error=item['original_error'],gds_audit=index[audit_path],
        evidence_class='PRIOR_PUBLISHED_GDS_REJECTION',physical_numbers_available=False)


def run(output):
    output=Path(output); require(output.is_absolute(),'Absolute output required'); no_symlinks(output)
    output.mkdir(parents=True,exist_ok=False)
    start=datetime.now(timezone.utc).isoformat(); completed=[]
    try:
        implementation=pin(Path(__file__).absolute()); runtime=[]
        for path,sha in RUNTIME.items():
            p=pin(path); require(p['sha256']==sha,'Validator source changed'); runtime.append(p)
        inputs={}; docs={}
        for alias,(path,sha) in TOP.items():
            p=pin(path); require(p['sha256']==sha,'Frozen input changed:'+alias)
            raw=path.read_bytes(); require(hashlib.sha256(raw).hexdigest()==sha,'Input mutation')
            inputs[alias]=p; docs[alias]=load_json(raw)
        put_json(output/'INTENT.json',dict(status='STARTED_NOT_COMPLETION',sources=inputs,implementation=implementation,
            started_utc=start,native_actions=0,model_loads=0,training=0))
        fields(docs['export'],dict(schema='eucap15_fixed_terminal_export.v1',status='FIXED_LIST_ACCOUNTED',item_count=13,
            counts={'FAILURE_EVIDENCE_BOUND':2,'CANDIDATE_PHYSICAL_CHAIN_BOUND':11},source_count=526,external_reference_count=27), 'Export frame')
        items=docs['closed']['items']; require(docs['closed']['count']==len(items)==13,'Wrong denominator')
        require(tuple(i['candidate_id'] for i in items)==FIXED_IDS,'Wrong fixed13 order/identity')
        unique(items,'candidate_id'); unique(items,'request_id')
        require({i['candidate_id'] for i in items if i['evidence_status']=='FAILURE_EVIDENCE_BOUND'}==FAIL_IDS,'Failure set changed')
        paths=docs['map']; source_index={}
        for entry in docs['index']:
            p=entry['source']; key=p['path']; require(key not in source_index,'Duplicate source index')
            require(paths.get(key)==entry['local_path'] and P/entry['relative_path']==Path(entry['local_path']), 'Source index/map differs')
            source_index[key]=p
        require(len(source_index)==526 and set(source_index)==set(paths),'Source index incomplete')
        require(len(docs['external'])==27,'External closure differs')
        merged,external=merge_external(paths,docs['external'],docs['old_map'],docs['supplement'])
        reader=MirrorReader(merged)
        for expected in external.values(): reader.read(expected)
        manifests=[p for p in external.values() if p['sha256']=='68c43a11f580856af08768845ca24875475d2f66741100bd29a82a38019a454e']
        require(len(manifests)==1,'Ambiguous acquisition manifest'); batch=load_frozen_batch(reader,manifests[0])
        rows=[]
        for item in items:
            cid=item['candidate_id']; require(cid in batch['rows'],'Unknown frozen proposal'); proposal=batch['rows'][cid]
            entry=normalized_entry(item,proposal)
            if entry is None: row=failure_row(reader,item,proposal,batch,source_index)
            else:
                row=inspect_chain(reader,entry,batch)
                row.update(state='STRICT_VALID' if row['strict_valid'] else 'EMX_INVALID',
                    original_result=item['original_result'],physical_numbers_available=True,
                    target=proposal['target'],frozen_proxy=proposal['proxy'],failure_stage=None,failed_checks=[],error=None)
            row.update(q_emx=None,complete11_status='NOT_EVALUATED_NO_Q_REPLACEMENT',fixed_delta_denominator=13,
                original_proposal_denominator=256,original_arm_denominator=128,training_admission='NOT_PERFORMED')
            rows.append(row); completed.append(cid)
            print(json.dumps(dict(candidate_id=cid,status=row['state'])),flush=True)
        reader.recheck()
        summary=dict(schema='eucap15_fixed13_saved_physics.v1',status='COMPLETE_FIXED13_ACCOUNTING',
            N_fixed_requests=13,N_saved_physical_chains=11,N_gds_failure=2,N_pending_in_fixed_list=0,
            state_counts=dict(Counter(r['state'] for r in rows)),
            original_proposals=256,original_proposals_per_arm=128,original_eligible=203,original_held=53,
            scope='FIXED_PUBLISHED_DELTA_NOT_MATCHED_PREFIX_NOT_FINAL',q_emx=None,
            complete11_status='NOT_EVALUATED',coverage_gain=None,equal_budget_comparison=None,
            ratios_not_computed=True,train_admission='NOT_PERFORMED',new_native_actions=0,model_loads=0,
            optimizer_updates=0,source_count_consumed=len(reader.evidence),
            limitations=['Eleven extracted chains need not be strict-valid or core-eligible.',
                'DOE/exploration has no target/Q/proxy; preserve null response errors.',
                'This fixed delta is not a same-budget prefix or a source-pool update.',
                'No new inference, label extraction, physical run, final test or training admission.'])
        put_json(output/'REQUEST_RESULTS.json',rows)
        allkeys=list(dict.fromkeys(k for row in rows for k in row))
        csvrows=[{k:json.dumps(row.get(k),allow_nan=False) if isinstance(row.get(k),(list,dict)) else row.get(k) for k in allkeys} for row in rows]
        put_csv(output/'REQUEST_RESULTS.csv',csvrows,allkeys)
        put_json(output/'SUMMARY.json',summary); put_json(output/'SOURCE_CLOSURE.json',list(reader.evidence.values()))
        put_json(output/'SOURCE_RESOLUTION.json',dict(external_sources=external,reused_source_metadata=inputs['old_map'],
            supplements=docs['supplement'],old_physical_rows_reprocessed=0))
        for p in [implementation,*runtime,*inputs.values()]: require(pin(p['path'])==p,'Source changed during run')
        artifacts=[pin(p) for p in sorted(output.iterdir()) if p.is_file()]
        put_json(output/'RECEIPT.json',dict(status='COMPLETE_READONLY_ACCEPTANCE_PENDING_INDEPENDENT_NUMERIC_QA',
            implementation=implementation,runtime=runtime,inputs=inputs,artifacts=artifacts,
            started_utc=start,ended_utc=datetime.now(timezone.utc).isoformat(),command=sys.argv,
            native_actions=0,model_loads=0,training_admission=False))
        with (output/'SHA256SUMS').open('x') as f:
            for p in sorted(output.iterdir()):
                if p.is_file() and p.name!='SHA256SUMS': f.write(pin(p)['sha256']+'  '+p.name+'\n')
        return summary
    except BaseException as error:
        put_json(output/'FAILURE_RECEIPT.json',dict(status='FAIL_PRESERVED_NO_RETRY',error=str(error),
            traceback=traceback.format_exc(),completed_candidate_ids=completed,native_actions=0,model_loads=0))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(run(parser.parse_args().output)),flush=True)
