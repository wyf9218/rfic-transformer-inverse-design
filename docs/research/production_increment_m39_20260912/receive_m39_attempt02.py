"""One incremental intake from the sole owner's M39 observation; no simulator."""
import copy, hashlib, json
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0, "/Users/wyf/Documents/模拟变压器AI反向建模/github_worktrees/eucap15-mlp-capacity-20260909")
from research.broadband56_nn import eucap15_received_landing_increment as landing

HERE=Path(__file__).resolve().parent
OBS=Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/storage_claim_wait_v1/budget_walk_v1/increment_20260912T160621518552Z/OBSERVATION.json")
SHA='8420d7e14b709933f918d907dad9fc6f903480a0129d387684a666cf831fcc19'
raw=OBS.read_bytes()
landing.require(hashlib.sha256(raw).hexdigest()==SHA,'OBS identity')
d=json.loads(raw);s=d['old_scope'];headers=d['formal_increment']
landing.require(d['baseline_utc']=='2026-09-12T15:56:29.716024+00:00','exact M38 baseline')
landing.require(d['new_scope'] is None,'no unconsumed successor scope')
landing.require(d['formal_count']==7197 and d['formal_added']==14 and len(headers)==14,'formal window')
landing.require([int(Path(h['pin']['path']).stem) for h in headers]==list(range(7184,7198)),'formal sequence')
landing.require(headers[-1]['pin']==d['formal_head'],'formal head')
by_id={h['request_id']:h for h in headers}
landing.require(len(by_id)==14,'duplicate formal request')
expected_status={'FRESH_EMX_EXTRACTED':27,'ANALYTIC_FAIL_NOT_DISPATCHED':2}
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
landing.require(len(rows)==len({r['request_id'] for r in rows})==len({r['geometry_sha256'] for r in rows})==29,'unique window')
prior_path=Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/landing_increment_m38_new107_v1/RECEIVED_NEW107_VIEW.json")
prior_raw=prior_path.read_bytes()
landing.require(hashlib.sha256(prior_raw).hexdigest()=='df4bd60614efa17503c7f1342fae4db66a3f515735dcc23e893e0b039d214e9c','prior view identity')
prior=json.loads(prior_raw)
landing.require(prior['observed_utc']==d['baseline_utc'],'prior cutoff')
old_headers=[h for h in headers if h['request_id'] not in {r['request_id'] for r in rows}]
landing.require(len(old_headers)==1 and old_headers[0]['request_id'].endswith('GEOMETRY_DOE-070'),'exact prior backfill')
old_header=old_headers[0]
old_rows=[r for r in prior['rows'] if r['request_id']==old_header['request_id']]
landing.require(len(old_rows)==1,'exact prior member')
back=copy.deepcopy(old_rows[0])
landing.require(back['formal_records']==[] and back['strict'] is True and back['core15_eligible'] is True,'prior qualified pending')
landing.require(back['result_pin'] in s['current_result_pins'],'prior RESULT pin changed')
landing.require(old_header['assigned_split_from_frozen_request']==back['assigned_development_split']=='train','prior split')
landing.require(old_header['new_result_release'] is None or old_header['new_result_release']==back['release'],'prior release')
back['original_formal_records']=[];back['formal_records']=[old_header]
back['backfill_binding_note']='M38 DOE070 pinned RESULT and original train split; missing header release preserved, no new solve.'
backfill=[back]
core=[r for r in rows if r['core15_eligible'] is True]
pending=[r for r in core if not r['formal_records']]
landing.require(len(core)==13 and len(pending)==0,'exact core and pending')
expected=dict(received_terminal=29,emx_completed=27,strict_in_range=13,formal_admitted=13,
              pending_formal=0,formal_train=6,formal_validation=3,formal_test=4,
              pending_formal_train=0,pending_formal_validation=0,pending_formal_test=0)
backfill_expected=dict(formal_admitted=1,formal_train=1,formal_validation=0,formal_test=0)
doc=dict(schema='eucap15_received_successor_new_window_view.v1',window_id='M39_new29',
         feature_order=['Lp_nH','Ls_nH','Qmin','K_abs'],observed_utc=d['utc'],baseline_utc=d['baseline_utc'],
         source=dict(path=str(OBS),sha256=SHA,bytes=len(raw)),
         source_reliance='SOLE_OWNER_VERIFIED_PHYSICAL_RESULTS_EXACT_FORMAL_RELEASE_SPLIT_SEQUENCE_JOINS',
         new_terminal=29,new_emx_completed=27,new_strict_valid=sum(r['strict'] is True for r in rows),
         new_strict_range_unique=13,fresh_formal_this_increment=13,new_core_without_formal=0,
         prior_pending_formal_backfill=1,ledger_window_fresh_formal=14,rows=rows,backfilled_prior_rows=backfill)
view=HERE/'RECEIVED_NEW29_VIEW.json';landing.emit_output(doc,view)
view_sha=hashlib.sha256(view.read_bytes()).hexdigest()
actual=landing.run("/Users/wyf/Documents/模拟变压器AI反向建模/github_worktrees/eucap15-mlp-capacity-20260909/docs/research/eucap15_abc_increment_20260910/COVERAGE_BEFORE.csv",
                   view,received_sha256=view_sha,expected_counts=expected,expected_backfill_counts=backfill_expected,caller_verified_sources=True)
out=HERE/'ACTUAL_LANDINGS.json';landing.emit_output(actual,out)
# Reuse the already supported compact schema, preserving source fields.
previous=json.loads(Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/landing_increment_m37_new20_v1/TRAIN_SOURCE_ROWS.json").read_text())
keys=previous['current_formal_train_rows'][0].keys()
compact=lambda r:{k:v for k,v in r.items() if k in keys}
train=dict(schema=previous['schema'],window_id='M39_new29',current_new_terminal_denominator=29,
           schema_format_note='Existing compact schema; counts declared separately, not inferred from name.',
           source_output=dict(path=str(out),sha256=hashlib.sha256(out.read_bytes()).hexdigest(),bytes=out.stat().st_size),
           scope='CURRENT_NEW29_ONLY_RECEIVED_SOURCE_UNION_NOT_FULL_PRODUCTION',
           feature_order=doc['feature_order'],generated_utc=actual['generated_utc'],
           current_formal_train_rows=[compact(r) for r in actual['rows'] if r['train_reference_comparison_eligible']],
           pending_formal_rows=[compact(r) for r in actual['rows'] if r['qualified_pending_formal']],
           prior_formal_backfill_rows=[compact(r) for r in actual['backfilled_prior_rows']])
landing.emit_output(train,HERE/'TRAIN_SOURCE_ROWS.json')
def pin(path):
    b=path.read_bytes();return dict(path=str(path),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))
receipt=dict(schema='eucap15_received_increment.m39.v1',status='PASS_INCREMENT_RECEIVED_NOT_NATIVE_ADMISSION',
             observed_utc=d['utc'],baseline_utc=d['baseline_utc'],completed_utc=actual['generated_utc'],
             source=doc['source'],counts=dict(expected,strict_valid=doc['new_strict_valid'],
             analytic_not_dispatched=2,candidate_failure_retained=0,prior_formal_backfill=1,
             prior_formal_backfill_train=1,history_formal=0,ledger_window_fresh_formal=14),formal_union_lower_bound=7197,
             formal_head=d['formal_head'],pending_request_ids=[r['request_id'] for r in pending],
             source_script=pin(Path(__file__)),by_source=actual['by_source'],
             train_descriptive={k:v for k,v in actual.items() if k.startswith('train_') or k=='eligible_new_train_rows'},
             artifacts=[pin(view),pin(out),pin(HERE/'TRAIN_SOURCE_ROWS.json')],native_calls=0,training_calls=0,
             physical_sources_reextracted=False,formal_writes=0,existing_completed_windows_rerun=0,
             note='Formal certification is read from sole owner ledger headers; only exact new joins here. 13 new formal and1 prior backfill are separated; no repeated EMX.')
landing.emit_output(receipt,HERE/'RECEIPT.json')
