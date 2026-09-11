"""Synthetic controlled64 metadata/arithmetics only; no real model/native data.

Pure comparison fixtures are explicitly caller-verified synthetic rows, NOT
evidence that a native chain or a real controlled experiment has completed.
"""
from copy import deepcopy
import csv
import hashlib
import io
import json
import socket
import subprocess

import numpy as np
import pytest

from research.broadband56_nn import eucap15_controlled_results as m
from research.broadband56_nn.eucap15_acquisition import Q_COUNTS
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    GEOMETRY_FIELDS, canonical_geometry_sha256,
)


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    import torch
    def forbidden(*args,**kwargs): raise AssertionError('Synthetic suite forbids network/native/model calls')
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(torch,'load',forbidden)


def coverage():
    edges=[np.linspace(lo,hi,9).tolist() for lo,hi in ((.5,2.),(.5,2.),(.2,.85))]
    rows=[]
    for i in range(8):
        for j in range(8):
            for k in range(8):
                pos=len(rows); n=23+(1 if pos<144 else 0) if pos<159 else 0
                row=dict(grid_n=8,split='train',frequency_hz=15000000000,lp_bin=i,ls_bin=j,k_bin=k,
                    lp_low_nh=edges[0][i],lp_high_nh=edges[0][i+1],
                    ls_low_nh=edges[1][j],ls_high_nh=edges[1][j+1],
                    k_low=edges[2][k],k_high=edges[2][k+1],N_strict_core_all_q=n,N_strict_core_q10_20=n)
                row.update({q:0 for q in Q_COUNTS}); row['N_q12_14']=n; rows.append(row)
    assert sum(r['N_strict_core_all_q'] for r in rows)==3801
    return rows


def intent():
    return dict(schema='eucap15_controlled_acquisition_intent.v1',status='FROZEN_BEFORE_TARGETS_AND_INFERENCE',
        model_id='SYNTHETIC_MODEL_NOT_FINAL',counts=dict(proposals_per_arm=32,sparse_targets=25,
        exploration=7,doe=32,logical_q_candidates=275),q_selection=dict(spans=m.SCALE,values=list(range(10,21))),
        budget=dict(proposals_per_arm_max=32,solver_starts_per_arm_max=16,total_solver_starts_max=32,
            native_wall_seconds_max=21600,incremental_storage_bytes_max=2147483648),
        comparisons=dict(equal_qualified_training=dict(primary_K=4),equal_simulation=dict(primary_m=16)))


def frame(*, failures=False):
    rows=[]
    for order in range(1,33):
        for a,arm in enumerate(m.ARMS):
            source='GEOMETRY_DOE' if a else 'SPARSE_TARGETED' if order<=25 else 'EXPLORATION'
            # Synthetic geometries selected only to exercise original hash split.
            # This is not a physical-feasibility or sampling-policy experiment.
            nonce=0
            while True:
                g=[10.*order+a+nonce/1000,2.,3.,4.,5.,6.,7.,8.,9.,10.]
                h=canonical_geometry_sha256(dict(zip(GEOMETRY_FIELDS,g)))
                if m._split_for_hash(h,17)==0: break
                nonce+=1
            targeted=source=='SPARSE_TARGETED'; rid=f'SYNTHETIC-{a}-{order:03d}'
            analytic=not (failures and order<=(4 if a else 10))
            rows.append(dict(request_id=rid,candidate_id=rid+'-q13' if targeted else rid,
                arm=arm,arm_order=order,global_order=len(rows)+1,source=source,q_proxy=13 if targeted else None,
                q_emx=None,frequency_hz=15000000000,recipe_sha256=m.INTENT_SHA,
                geometry_fields=list(GEOMETRY_FIELDS),geometry=g,canonical_geometry_sha256=h,
                assigned_development_split='train',analytic_pass=analytic,duplicate_reasons=[],
                local_dispatch_eligible=analytic,model_id='SYNTHETIC_MODEL_NOT_FINAL' if targeted else None,
                target=[1.,1.,13.,.5] if targeted else None,proxy=[1.,1.,13.,.5] if targeted else None,
                target_cell=list(m.actual_landing([1.,1.,.5])) if targeted else None,
                predicted_cell=list(m.actual_landing([1.,1.,.5])) if targeted else None))
    return rows


def context(rows=None):
    rows=frame() if rows is None else rows
    return dict(rows=m.validate_proposals(intent(),rows),before=coverage(),excluded_hashes={'f'*64})


def published(ctx,n_each=4):
    rows=m.initial_rows(ctx)
    ordinal=0
    for r in rows:
        if r['arm_order']<=n_each:
            ordinal+=1
            second=2*ordinal
            r.update(state='STRICT_VALID',actual=[1.9,1.9,13.,.8],actual_cell=list(m.actual_landing([1.9,1.9,.8])),
                strict_valid=True,core_eligible=True,strict_joint_hit=False if r['source']=='SPARSE_TARGETED' else None,
                physical_chain_verified=True,evidence_class='SYNTHETIC_CALLER_VERIFIED_NOT_EMX',
                solver_start_verified=True,solver_start_order=ordinal,
                solver_started_utc=f'2026-01-01T00:{second//60:02d}:{second%60:02d}Z',
                closed_utc=f'2026-01-01T00:{(second+1)//60:02d}:{(second+1)%60:02d}Z',
                solver_wall_seconds=1.,solver_cpu_seconds=2.,total_stage_cost_seconds=3.,storage_bytes=100)
    return rows


def test_all64_original_failures_and_no_publication_not_run():
    ctx=context(frame(failures=True)); rows=m.initial_rows(ctx); original=deepcopy(rows)
    report=m.compare(ctx,rows)
    assert report['original_denominator']==64
    assert report['state_counts']=={'ANALYTIC_FAIL':14,'PENDING':50}
    assert report['solver_starts'] is report['equal_m'] is report['equal_K'] is report['actual_coverage'] is None
    assert rows==original and all(r['actual'] is None and r['q_emx'] is None for r in rows)


@pytest.mark.parametrize('kind',['duplicate','missing','order','Q','model','geometry','split','target_cell','proxy_cell','DOEtarget'])
def test_frozen_identity_rejections(kind):
    rows=frame()
    if kind=='duplicate': rows[-1]=deepcopy(rows[0])
    elif kind=='missing': rows.pop()
    elif kind=='order': rows[0]['global_order']=2
    elif kind=='Q': rows[0]['q_proxy']=14
    elif kind=='model': rows[0]['model_id']='OTHER_MODEL'
    elif kind=='geometry': rows[0]['geometry'][0]+=.005
    elif kind=='split': rows[0]['assigned_development_split']='test'
    elif kind=='target_cell': rows[0]['target_cell']=[0,0,0]
    elif kind=='proxy_cell': rows[0]['predicted_cell']=[0,0,0]
    else: rows[1]['target']=[1.,1.,13.,.5]
    with pytest.raises(ValueError): m.validate_proposals(intent(),rows)


def test_primary_K4_and_maximum_common_m_keep_full_denominator():
    ctx=context(); rows=published(ctx,4)
    report=m.compare(ctx,rows,publication_verified=True)
    assert report['original_denominator']==64 and report['solver_starts']==8
    assert report['equal_m']['primary']['status']=='NOT_REACHED_OR_PENDING'
    assert report['equal_m']['maximum_common']['m']==4
    k=report['equal_K']['primary']; assert k['K']==4 and k['status']=='MATCHED_QUALIFIED_TRAIN_PREFIX'
    for arm in m.ARMS:
        p=k['arms'][arm]
        assert p['solver_starts']==4 and p['unique_qualified_train']==4
        assert p['solver_wall_seconds']==4 and p['solver_cpu_seconds']==8
        assert p['total_stage_cost_seconds']==12 and p['storage_bytes']==400
        assert p['coverage']['newly_occupied_cells']==1
    assert rows[1]['target'] is rows[1]['strict_joint_hit'] is None


@pytest.mark.parametrize('kind',['wrapper','reservation','gapped','duplicate','reversed','cap'])
def test_no_wrapper_or_incomplete_start_budget(kind):
    ctx=context(); rows=published(ctx,17 if kind=='cap' else 4)
    if kind in ('wrapper','reservation'):
        rows[0].update(solver_start_verified=False,solver_start_order=None,solver_started_utc=None)
        report=m.compare(ctx,rows,publication_verified=True)
        assert report['solver_starts'] is report['equal_m'] is report['equal_K'] is None
        assert report['status']=='PHYSICAL_PUBLICATIONS_WITH_UNRESOLVED_NATIVE_START_ORDER'
        return
    if kind=='gapped': rows[0]['solver_start_order']=50
    elif kind=='duplicate': rows[1]['solver_start_order']=1
    elif kind=='reversed': rows[0]['solver_started_utc']='2026-01-01T00:00:04Z'
    with pytest.raises(ValueError): m.compare(ctx,rows,publication_verified=True)


def test_unclosed_earlier_start_blocks_later_equal_K_and_m():
    ctx=context(); rows=published(ctx,4)
    rows[0].update(state='SOLVER_PENDING',actual=None,actual_cell=None,core_eligible=None,strict_valid=None,
        strict_joint_hit=None,closed_utc=None,solver_wall_seconds=None,solver_cpu_seconds=None)
    report=m.compare(ctx,rows,publication_verified=True)
    assert report['equal_m']['maximum_common'] is None
    assert report['equal_K']['primary']['status']=='NOT_REACHED'


def test_pre_native_failures_retained_in_K_prefix_and_missing_cost_null():
    ctx=context(); rows=published(ctx,5)
    r=rows[0]; r.update(state='GDS_FAIL',actual=None,actual_cell=None,core_eligible=None,strict_valid=None,
        strict_joint_hit=None,solver_start_verified=False,solver_start_order=None,solver_started_utc=None,
        solver_wall_seconds=None,solver_cpu_seconds=None,total_stage_cost_seconds=None)
    n=0
    for row in rows:
        if row['solver_start_verified']: n+=1; row['solver_start_order']=n
    report=m.compare(ctx,rows,publication_verified=True)
    p=report['equal_K']['primary']['arms'][m.ARMS[0]]
    assert p['proposal_count']==5 and p['solver_starts']==4 and p['state_counts']['GDS_FAIL']==1
    assert p['total_stage_cost_seconds'] is None and p['solver_wall_seconds']==4


@pytest.mark.parametrize('kind',['self_reported','failure_numbers','DOEhit','unknown','removed_analytic','negative_cost'])
def test_states_nulls_and_no_self_reported_physics(kind):
    ctx=context(); rows=published(ctx,4)
    if kind=='self_reported': rows[0]['physical_chain_verified']=False
    elif kind=='failure_numbers': rows[-1]['actual']=[1.,1.,13.,.5]
    elif kind=='DOEhit': rows[1]['strict_joint_hit']=True
    elif kind=='unknown': rows[-1]['state']='MAYBE_SUCCESS'
    elif kind=='removed_analytic': ctx['rows'][rows[0]['candidate_id']]['analytic_pass']=False
    else: rows[0]['solver_cpu_seconds']=-1
    with pytest.raises(ValueError): m.compare(ctx,rows,publication_verified=True)


def test_invalid_and_solver_feature_failures_consume_budget_not_K():
    ctx=context(); rows=published(ctx,4)
    rows[0].update(state='EMX_INVALID',strict_valid=False,core_eligible=False,strict_joint_hit=False)
    for row,state in zip(rows[2:6:2],['SOLVER_FAIL','FEATURE_FAIL']):
        row.update(state=state,actual=None,actual_cell=None,strict_valid=None,core_eligible=None,strict_joint_hit=None)
    result=m.compare(ctx,rows,publication_verified=True)
    assert result['solver_starts']==8 and result['equal_m']['maximum_common']['m']==4
    assert result['equal_K']['primary']['status']=='NOT_REACHED'
    assert result['equal_K']['maximum_common']['K']==1


def test_test_split_and_known_geometry_do_not_increment_train_K():
    ctx=context(); rows=published(ctx,4)
    first=rows[0]; first['split']='test'; ctx['rows'][first['candidate_id']]['assigned_development_split']='test'
    ctx['excluded_hashes'].add(rows[2]['geometry_sha256'])
    result=m.compare(ctx,rows,publication_verified=True)
    assert result['equal_K']['maximum_common']['K']==2
    assert result['equal_K']['primary']['status']=='NOT_REACHED'


def write_preflight_fixture(tmp_path,monkeypatch):
    """Small synthetic files with the actual64/512 schema, no model fixtures."""
    mirror=tmp_path/'mirror'; mirror.mkdir()
    original=tmp_path/'SYNTHETIC_ORIGINAL_NOT_CREATED'; mapping={}
    def raw(name,value):
        local=mirror/name; local.write_bytes(value)
        p=dict(path=str(original/name),sha256=hashlib.sha256(value).hexdigest(),bytes=len(value))
        mapping[p['path']]=str(local); return p
    def doc(name,value): return raw(name,(json.dumps(value,allow_nan=False)+'\n').encode())
    csv_stream=io.StringIO(); w=csv.DictWriter(csv_stream,fieldnames=list(coverage()[0]));w.writeheader();w.writerows(coverage())
    cp=raw('DECISION_COVERAGE.csv',csv_stream.getvalue().encode())
    i=intent(); i['inputs']={'decision_coverage':cp}; ip=doc('INTENT.json',i)
    monkeypatch.setattr(m,'INTENT_SHA',ip['sha256'])
    proposals=frame(failures=True)
    pp=raw('SELECTED_CANDIDATES.jsonl',''.join(json.dumps(r)+'\n' for r in proposals).encode())
    monkeypatch.setattr(m,'PROPOSALS_SHA',pp['sha256'])
    ep=doc('EXCLUSION_METADATA.json',dict(canonical_hashes={'SYNTHETIC':['f'*64]},nominal_grid_hashes={},numeric_response_values_used=0))
    rp=doc('PREPARATION_RECEIPT.json',dict(status='PREPARED_NOT_NATIVE_RELEASE',intent=ip,N_total_proposals=64,
        N_logical_proxy_candidates=275,N_selected_q=25,source_bytes_unchanged=True))
    mp=doc('MANIFEST.json',dict(intent=ip,files={n:p for n,p in [('DECISION_COVERAGE.csv',cp),
        ('SELECTED_CANDIDATES.jsonl',pp),('EXCLUSION_METADATA.json',ep),('PREPARATION_RECEIPT.json',rp)]}))
    monkeypatch.setattr(m,'MANIFEST_SHA',mp['sha256'])
    spec=dict(schema='eucap15_controlled_context_preflight.v1',intent=ip,preparation_manifest=mp,
        path_map=mapping,implementation=[m.pin(m.Path(m.__file__).resolve())])
    sp=tmp_path/'SPEC.json'; sp.write_text(json.dumps(spec)+'\n')
    return sp,m.pin(sp),spec


def test_actual_synthetic_preflight_read_chain_and_no_clobber(tmp_path,monkeypatch):
    sp,p,spec=write_preflight_fixture(tmp_path,monkeypatch); out=tmp_path/'out'
    result=m.run_preflight(sp,p['sha256'],out)
    assert result['status']=='PASS_METADATA_ONLY_PHYSICAL_NOT_RUN'
    summary=json.loads((out/'SUMMARY.json').read_text())
    assert summary['state_counts']=={'ANALYTIC_FAIL':14,'PENDING':50}
    assert summary['native_result_consumer']=='NOT_INSTALLED_PENDING_OWNER_SCHEMA'
    assert len(summary['original_failure_ids'])==14 and summary['model_loads']==0
    pins={f.name:m.pin(f) for f in out.iterdir()}
    with pytest.raises(FileExistsError): m.run_preflight(sp,p['sha256'],out)
    assert {f.name:m.pin(f) for f in out.iterdir()}==pins
    assert not (out/'FAILURE_RECEIPT.json').exists()


@pytest.mark.parametrize('kind',['missing_map','changed_proposals','unknown_native_assertion'])
def test_preflight_input_failure_no_physical_promotion(tmp_path,monkeypatch,kind):
    sp,p,spec=write_preflight_fixture(tmp_path,monkeypatch); out=tmp_path/'out'
    if kind=='missing_map': spec['path_map'].pop(spec['intent']['path'])
    elif kind=='changed_proposals':
        local=next(v for k,v in spec['path_map'].items() if k.endswith('SELECTED_CANDIDATES.jsonl'))
        m.Path(local).write_text('changed\n')
    else: spec['publication_verified']=True
    sp.write_text(json.dumps(spec)+'\n'); p=m.pin(sp)
    with pytest.raises(ValueError): m.run_preflight(sp,p['sha256'],out)
    assert not (out/'RECEIPT.json').exists()
    if kind!='unknown_native_assertion': assert (out/'FAILURE_RECEIPT.json').is_file()
