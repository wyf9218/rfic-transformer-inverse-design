"""Thin fixed-order loop over the existing historical111 extraction entry."""
import csv
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path('/Users/wyf/Documents/模拟变压器AI反向建模')
V=ROOT/'github_worktrees/eucap15-mlp-capacity-20260909'
W=ROOT/'reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1'
Q=ROOT/'reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/storage_claim_wait_v1/budget_walk_v1'
IN=Q/'strict112_next16_20260912T144508027742Z'
OUT=Path(__file__).parent
ENTRY=W/'historical111_extraction_v1/extract_historical111.py'


def pin(path):
    path=Path(path).absolute()
    assert not any(p.is_symlink() for p in (path,*path.parents))
    before=path.stat();raw=path.read_bytes();after=path.stat()
    assert (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns)
    return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


def load(path,sha):
    record=pin(path);assert record['sha256']==sha
    return json.loads(Path(path).read_text()),record


def write(name,value):
    with (OUT/name).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2,ensure_ascii=False,allow_nan=False);stream.write('\n')


request,request_pin=load(OUT/'MINIMUM_OWNER_READ_REQUEST.json','574cff6930bcab49fd90e2f24774f5a455c95e53675d0ebc73c4326b5074123c')
received,received_pin=load(IN/'RECEIPT.json','92085459112bd100f3e13fc51092a1e820f69eb557745e86b0ff8869ba988fa4')
assert received['request']==request_pin and len(received['files'])==len(request['files'])==16
assert [r['authority_ordinal_zero_based'] for r in request['files']]==list(range(1,17))
assert pin(ENTRY)['sha256']=='d53d4549477f34091bda2faf6e92b1844100eca84f2a87a260c44d4684db6410'
spec=importlib.util.spec_from_file_location('single_current111_entry',ENTRY)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
(OUT/'extracted').mkdir(exist_ok=False)
rows=[];inputs=[request_pin,received_pin,pin(ENTRY)];receipts=[];calls=[]
for expected,actual in zip(request['files'],received['files']):
    assert actual['requested']==expected and actual['status']=='MATCH'
    source=actual['source'];local=actual['local'];path=Path(local['path'])
    assert path.is_relative_to(IN) and pin(path)==local
    assert source['path']==expected['path'] and source['sha256']==local['sha256']==expected['expected_sha256']
    assert local['bytes']<=expected['max_bytes'] and expected['original_split']=='UNKNOWN'
    ordinal=expected['authority_ordinal_zero_based'];inputs.append(local)
    row={key:expected[key] for key in ('authority_ordinal_zero_based','candidate_id_sha256','candidate_geometry_identity_sha256','benchmark_arm','pair_id_sha256','original_split')}
    row.update(s4p_sha256=local['sha256'],source_s4p_path=source['path'],local_s4p_path=local['path'],
        qualification_scope='CONDITIONAL_NUMERICAL_SCREEN',formal_added=0,
        lp_nh=None,ls_nh=None,qp=None,qs=None,qmin=None,signed_k=None,k_abs=None,
        descriptor_valid=False,strict_valid=False,below_half_srf=False,core_range=False,q10_20_intersection=False,
        conditional_strict_and_core=False,strict_reasons='',core_range_reasons='',retained_frequency_points=0,
        extraction_status='NOT_STARTED',failure='',extraction_receipt_path='',extraction_receipt_sha256='')
    dest=OUT/'extracted'/f'authority_{ordinal:03d}'
    args=SimpleNamespace(repo=V,s4p=path,s4p_sha256=local['sha256'],out=dest)
    calls.append(dict(function='extract_historical111.run',repo=str(V),s4p=str(path),s4p_sha256=local['sha256'],out=str(dest)))
    try:
        receipt_pin=module.run(args);receipts.append(receipt_pin)
        extraction=json.loads(Path(receipt_pin['path']).read_text())
        target_path=dest/'TARGET15.json';assert pin(target_path)==extraction['target15']
        target=json.loads(target_path.read_text());physical=target['row']
        assert extraction['retained_rows']==111 and target['original_frequency_index_zero_based']==20
        assert target['frequency_hz']==15000000000 and extraction['source']==local
        reasons=[f'{name}_NONFINITE_OR_OUTSIDE_{lo}_{hi}' for name,lo,hi in [('lp_nh',.5,2),('ls_nh',.5,2),('k_abs',.2,.85)]
            if physical[name] is None or not math.isfinite(physical[name]) or not lo<=physical[name]<=hi]
        for name in ('lp_nh','ls_nh','qp','qs','qmin','signed_k','k_abs'):row[name]=physical[name]
        row.update(descriptor_valid=physical['broadband_descriptor_valid']=='true',strict_valid=target['current_definition_strict_lumped_valid'],
            below_half_srf=physical['below_half_srf']=='true',core_range=not reasons,
            q10_20_intersection=physical['qmin'] is not None and 10<=physical['qmin']<=20,
            strict_reasons=';'.join(target['strict_failure_reasons']),core_range_reasons=';'.join(reasons),
            conditional_strict_and_core=target['current_definition_strict_lumped_valid'] and not reasons,
            retained_frequency_points=111,extraction_status='COMPLETE',
            extraction_receipt_path=receipt_pin['path'],extraction_receipt_sha256=receipt_pin['sha256'])
    except Exception as exc:
        row.update(extraction_status='FAIL',failure=f'{type(exc).__name__}: {exc}')
        if (dest/'FAILURE.json').exists():receipts.append(pin(dest/'FAILURE.json'))
    rows.append(row)
    print(json.dumps({k:row[k] for k in ('authority_ordinal_zero_based','extraction_status','strict_valid','core_range','q10_20_intersection','conditional_strict_and_core')}),flush=True)
assert len(rows)==16
counts={key:sum(bool(r[key]) for r in rows) for key in ('descriptor_valid','strict_valid','below_half_srf','core_range','q10_20_intersection','conditional_strict_and_core')}
counts.update(received=16,extraction_complete=sum(r['extraction_status']=='COMPLETE' for r in rows),
    extraction_fail=sum(r['extraction_status']=='FAIL' for r in rows),retained_frequency_rows=sum(r['retained_frequency_points'] for r in rows),formal_added=0)
strict_reasons=Counter(reason for r in rows for reason in r['strict_reasons'].split(';') if reason)
range_reasons=Counter(reason for r in rows for reason in r['core_range_reasons'].split(';') if reason)
passing=[dict(r,required_next_evidence='ACTUAL_GEOMETRY_GDS_DRC_CURRENT_PROCESS_AND_PORT_BINDING_PLUS_ORIGINAL_RESERVATION_AND_UNIQUENESS; no automatic train promotion') for r in rows if r['conditional_strict_and_core']]
write('BATCH_ROWS.json',dict(scope='CONDITIONAL_NUMERICAL_SCREEN_NOT_PHYSICAL_CERTIFICATION',source=received_pin,rows=rows))
with (OUT/'BATCH_ROWS.csv').open('x',newline='',encoding='utf-8') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
write('NEXT_EVIDENCE_CANDIDATES.json',dict(status='ORDERED_CONDITIONAL_SCREEN_PASS_NOT_CERTIFIED',
    original_order_retained=True,numerical_screen_count=len(passing),formal_added=0,rows=passing,
    q_intersection_is_not_a_required_full100k_gate=True,benchmark_reservations_unchanged=True))
summary=dict(schema='eucap15_strict112_next16_current111_screen.v1',completed_utc=datetime.now(timezone.utc).isoformat(),
    scope='CONDITIONAL_NUMERICAL_SCREEN_NOT_CURRENT_PROCESS_PORT_DRC_OR_UNIQUENESS_CERTIFICATION',counts=counts,
    strict_failure_reason_counts=dict(strict_reasons),core_range_failure_reason_counts=dict(range_reasons),
    conditional_strict_core_and_q_intersection=sum(r['conditional_strict_and_core'] and r['q10_20_intersection'] for r in rows),
    conditional_pass_original_ordinals=[r['authority_ordinal_zero_based'] for r in passing],
    original_ordinals=list(range(1,17)),ordinal0_reexecuted=False,original_split='UNKNOWN',
    original_benchmark_arm_counts=dict(Counter(r['benchmark_arm'] for r in rows)),
    q_definition='min(Qp,Qs)',q_intersection_is_not_full100k_gate=True,
    frequency_points_per_member=111,original15ghz_index=20,frequency_resampling=False,
    current_mapping='CONDITIONAL_ON_CURRENT_PORT_MAPPING; [0,1,3,2]; actual compatibility not yet certified',
    new_emx=0,formal_added=0,uses_for_training_or_sampling=False,old112_full_profile_run=False,
    implementation=pin(__file__),inputs=inputs,actual_calls=calls,extraction_receipts=receipts,argv=sys.argv,
    artifacts=[pin(OUT/name) for name in ('BATCH_ROWS.json','BATCH_ROWS.csv','NEXT_EVIDENCE_CANDIDATES.json')])
for item in inputs:assert pin(item['path'])==item
write('BATCH_SUMMARY.json',summary)
with (OUT/'BATCH_SHA256SUMS').open('x') as stream:
    for name in ('EXTRACT_RECEIVED16.py','BATCH_ROWS.json','BATCH_ROWS.csv','NEXT_EVIDENCE_CANDIDATES.json','BATCH_SUMMARY.json'):
        stream.write(pin(OUT/name)['sha256']+'  '+name+'\n')
print(json.dumps(dict(counts=counts,pass_ordinals=summary['conditional_pass_original_ordinals'],summary=pin(OUT/'BATCH_SUMMARY.json'))))
