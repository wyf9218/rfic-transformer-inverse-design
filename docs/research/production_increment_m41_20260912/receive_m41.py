"""One incremental intake from the sole owner's M41 observation; no simulator."""
import copy, hashlib, json
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0, "/Users/wyf/Documents/模拟变压器AI反向建模/github_worktrees/eucap15-mlp-capacity-20260909")
from research.broadband56_nn import eucap15_received_landing_increment as landing

HERE=Path(__file__).resolve().parent
OBS=Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/storage_claim_wait_v1/budget_walk_v1/increment_20260912T163128066082Z/OBSERVATION.json")
SHA='ede5f7995d0f4d5c0b6100ef52076b22dc9e70c414b120bd0c1a81675bd5a0bd'
raw=OBS.read_bytes()
landing.require(hashlib.sha256(raw).hexdigest()==SHA,'OBS identity')
d=json.loads(raw);s=d['old_scope'];headers=d['formal_increment']
landing.require(d['baseline_utc']=='2026-09-12T16:06:22.089693+00:00','exact M39 baseline')
landing.require(d['new_scope'] is None,'no unconsumed successor scope')
landing.require(d['formal_count']==7232 and d['formal_added']==35 and len(headers)==35,'formal window')
landing.require([int(Path(h['pin']['path']).stem) for h in headers]==list(range(7198,7233)),'formal sequence')
landing.require(headers[-1]['pin']==d['formal_head'],'formal head')
by_id={h['request_id']:h for h in headers}
landing.require(len(by_id)==35,'duplicate formal request')
expected_status={'FRESH_EMX_EXTRACTED':76,'ANALYTIC_FAIL_NOT_DISPATCHED':6}
landing.require(s['new_counts']==expected_status,'status counts')
rows=[]
for item in s['new_results']:
    r=copy.deepcopy(item['value']);p=r['original_proposal']
    for key in ('request_id','candidate_id','source','arm'):
        landing.require(r[key]==p[key],'RESULT/proposal '+key)
    landing.require(r['candidate_geometry_identity_sha256']==p['canonical_geometry_sha256'],'geometry')
    fresh=r['status']=='FRESH_EMX_EXTRACTED'
    for key in ('release','execution_release'):
        landing.require(key not in r or r[key]==s['release'],'release conflict')
        landing.require(not fresh or key in r,'fresh missing release')
    split=p['assigned_development_split'];landing.require(split in landing.SPLITS,'split')
    header=by_id.get(r['request_id']);formal=[] if header is None else [header]
    if header is not None:
        landing.require(header['new_result_release']==r['release'],'formal exact release')
        landing.require(header['assigned_split_from_frozen_request']==split,'formal original split')
        landing.require(r['valid_for_strict_comparison'] is True and r['core15_eligible'] is True,'formal eligibility')
    r.update(geometry_sha256=p['canonical_geometry_sha256'],assigned_development_split=split,
             strict=r.get('valid_for_strict_comparison'),core15_eligible=r.get('core15_eligible'),
             result_pin=item['pin'],formal_records=formal,original_result_accepted_flag=r['production_accepted'])
    missing=[k for k in ('valid_for_strict_comparison','core15_eligible') if k not in item['value']]
    if missing:r['source_missing_flag_fields']=missing
    for key in ('geometry','geometry_fields','geometry_units','seed','recipe_sha256','target_cell','predicted_cell'):
        if key in p:r[key]=copy.deepcopy(p[key])
    rows.append(r)
landing.require(Counter(r['status'] for r in rows)==expected_status,'derived status')
landing.require(len(rows)==len({r['request_id'] for r in rows})==len({r['geometry_sha256'] for r in rows})==82,'unique window')
landing.require(set(by_id)<={r['request_id'] for r in rows},'unmatched formal header')
core=[r for r in rows if r['core15_eligible'] is True]
pending=[r for r in core if not r['formal_records']]
landing.require(len(core)==37 and len(pending)==2 and {r['request_id'].rsplit('-',1)[1] for r in pending}=={'053','054'} and all('TRAIN_NEIGHBORHOOD' in r['request_id'] for r in pending),'exact core pending')
expected=dict(received_terminal=82,emx_completed=76,strict_in_range=37,formal_admitted=35,
              pending_formal=2,formal_train=23,formal_validation=4,formal_test=8,
              pending_formal_train=1,pending_formal_validation=1,pending_formal_test=0)
doc=dict(schema='eucap15_received_successor_new_window_view.v1',window_id='M41_new82',
         feature_order=['Lp_nH','Ls_nH','Qmin','K_abs'],observed_utc=d['utc'],baseline_utc=d['baseline_utc'],
         source=dict(path=str(OBS),sha256=SHA,bytes=len(raw)),
         source_reliance='SOLE_OWNER_VERIFIED_PHYSICAL_RESULTS_EXACT_FORMAL_RELEASE_SPLIT_SEQUENCE_JOINS',
         new_terminal=82,new_emx_completed=76,new_strict_valid=sum(r['strict'] is True for r in rows),
         new_strict_range_unique=37,fresh_formal_this_increment=35,new_core_without_formal=2,
         prior_pending_formal_backfill=0,ledger_window_fresh_formal=35,rows=rows,backfilled_prior_rows=[])
view=HERE/'RECEIVED_NEW82_VIEW.json';landing.emit_output(doc,view)
view_sha=hashlib.sha256(view.read_bytes()).hexdigest()
actual=landing.run("/Users/wyf/Documents/模拟变压器AI反向建模/github_worktrees/eucap15-mlp-capacity-20260909/docs/research/eucap15_abc_increment_20260910/COVERAGE_BEFORE.csv",
                   view,received_sha256=view_sha,expected_counts=expected,caller_verified_sources=True)
out=HERE/'ACTUAL_LANDINGS.json';landing.emit_output(actual,out)
# Reuse the already supported compact schema, preserving source fields.
previous=json.loads(Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/landing_increment_m37_new20_v1/TRAIN_SOURCE_ROWS.json").read_text())
keys=previous['current_formal_train_rows'][0].keys()
compact=lambda r:{k:v for k,v in r.items() if k in keys}
train=dict(schema=previous['schema'],window_id='M41_new82',current_new_terminal_denominator=82,
           schema_format_note='Existing compact schema; counts declared separately, not inferred from name.',
           source_output=dict(path=str(out),sha256=hashlib.sha256(out.read_bytes()).hexdigest(),bytes=out.stat().st_size),
           scope='CURRENT_NEW82_ONLY_RECEIVED_SOURCE_UNION_NOT_FULL_PRODUCTION',
           feature_order=doc['feature_order'],generated_utc=actual['generated_utc'],
           current_formal_train_rows=[compact(r) for r in actual['rows'] if r['train_reference_comparison_eligible']],
           pending_formal_rows=[compact(r) for r in actual['rows'] if r['qualified_pending_formal']],
           prior_formal_backfill_rows=[])
landing.emit_output(train,HERE/'TRAIN_SOURCE_ROWS.json')
def pin(path):
    b=path.read_bytes();return dict(path=str(path),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))
receipt=dict(schema='eucap15_received_increment.m41.v1',status='PASS_INCREMENT_RECEIVED_NOT_NATIVE_ADMISSION',
             observed_utc=d['utc'],baseline_utc=d['baseline_utc'],completed_utc=actual['generated_utc'],
             source=doc['source'],counts=dict(expected,strict_valid=doc['new_strict_valid'],
             analytic_not_dispatched=6,candidate_failure_retained=0,prior_formal_backfill=0,
             history_formal=0,ledger_window_fresh_formal=35),formal_union_lower_bound=7232,
             formal_head=d['formal_head'],pending_request_ids=[r['request_id'] for r in pending],
             source_script=pin(Path(__file__)),by_source=actual['by_source'],
             train_descriptive={k:v for k,v in actual.items() if k.startswith('train_') or k=='eligible_new_train_rows'},
             artifacts=[pin(view),pin(out),pin(HERE/'TRAIN_SOURCE_ROWS.json')],native_calls=0,training_calls=0,
             physical_sources_reextracted=False,formal_writes=0,existing_completed_windows_rerun=0,
             note='Formal certification is read from sole owner ledger headers; only exact new joins here. 37 core candidates are unique within this window; only35 have formal unique admission,2 remain pending.')
landing.emit_output(receipt,HERE/'RECEIPT.json')
