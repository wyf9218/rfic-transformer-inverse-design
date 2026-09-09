"""Small synthetic-only frame/CLI/resume tests, never a FINAL10K experiment.

Public constants are monkeypatched only in tests; no actual checkpoint is loaded.
"""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from research.broadband56_nn import eucap15_final_frame as m
from research.broadband56_nn.frequency_qscan import q_targets
from tests.test_eucap15_final_routing import fixture as routing_fixture, refresh, FIELDS, MODEL

REAL_LOAD_PAIR = m.load_pair

@pytest.fixture
def small(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'N_REQUESTS', 35)
    monkeypatch.setattr(m, 'N_AUDIT', 3)
    pins = {}
    for name in m.PIN_NAMES:
        path = tmp_path / ('SYNTHETIC_' + name + '.json')
        m.save_json(path, dict(SYNTHETIC_NOT_A_MODEL=True, field_names=FIELDS))
        pins[name] = m.pin(path)
    model = dict(schema='eucap15_final_model_freeze.v1', status='FINAL_FROZEN',
        model_role='FINAL', frequency_ghz=15, label_mode='STRICT_LUMPED',
        model_id=MODEL, selection_basis='VALIDATION_ONLY', allow_extrapolation=True,
        pins=pins, synthetic_unit_test_only=True)
    source = tmp_path / 'SYNTHETIC_DECLARATION_NOT_FINAL_MODEL.json'
    m.save_json(source, model)
    calls = []
    monkeypatch.setattr(m, 'load_pair', lambda value: (calls.append('SYNTHETIC_LOAD') or object(), object(), FIELDS))
    monkeypatch.setattr(m, '_resources', lambda path: dict(status='SYNTHETIC_RESOURCE_STUB_NOT_REAL_ADMISSION'))

    def batch(requests, context, forward, inverse):
        calls.append(('SYNTHETIC_INFERENCE', len(requests)))
        result = []
        for request in requests:
            _, rows = routing_fixture(request['full11_audit'])
            targets = q_targets([request[k] for k in ('lp_nh', 'ls_nh', 'k_abs')])
            for row, target in zip(rows, targets):
                row.update(request_id=request['request_id'], candidate_id=f"{request['request_id']}-q{row['q_target']:02d}",
                    target_source=request['target_source'], dataset_scope='SYNTHETIC', target=target.tolist())
                row['grid_proxy'] = row['target'].copy()
                row['grid_proxy'][0] += abs(row['q_target']-14)/100
            refresh(rows)
            result.extend(rows)
        return result, [], {}

    monkeypatch.setattr(m, '_batch', batch)
    return dict(root=tmp_path, model=model, source=source, out=tmp_path/'SYNTHETIC_FRAME', calls=calls)


def prepare(small):
    m.prepare(small['source'], small['out'], study_id='SYNTHETIC', seed=11, audit_seed=19)
    return small['out'] / 'FRAME.json'


def test_small_draw_reproducible_and_audit_independent_of_target_rng():
    rows, ids = m._draw('SYNTHETIC', 2, 9, 9, 3)
    assert (rows, ids) == m._draw('SYNTHETIC', 2, 9, 9, 3)
    other, other_ids = m._draw('SYNTHETIC', 2, 11, 9, 3)
    assert ids != other_ids
    assert [(r['lp_nh'],r['ls_nh'],r['k_abs']) for r in rows] == [(r['lp_nh'],r['ls_nh'],r['k_abs']) for r in other]
    assert len(set(ids)) == sum(r['full11_audit'] for r in rows) == 3
    assert all(.5 <= r['lp_nh'] <= 2 and .5 <= r['ls_nh'] <= 2 and .2 <= r['k_abs'] <= .85 for r in rows)


@pytest.mark.parametrize('args', [('bad/path',1,2,4,2), ('',1,2,4,2), ('ok',1,1,4,2),
    ('ok',True,2,4,2), ('ok',-1,2,4,2), ('ok',1,2,4,5), ('ok',1,2,4,0)])
def test_invalid_sampling_declaration(args):
    with pytest.raises(ValueError):
        m._draw(*args)


def test_nonfinal_fails_before_any_draw_load_or_output(small, monkeypatch):
    value=deepcopy(small['model']); value['status']='REFERENCE_LOADED_NOT_FINAL'
    source=small['root']/'REFERENCE.json'; m.save_json(source,value)
    monkeypatch.setattr(m,'_draw',lambda *args: pytest.fail('must not draw'))
    with pytest.raises(ValueError, match='Explicit FINAL'):
        m.prepare(source,small['out'],study_id='NO',seed=1,audit_seed=2)
    assert not small['out'].exists() and not small['calls']


def test_unloadable_pair_does_not_draw_or_create_frame(small, monkeypatch):
    def fail(*args): raise ValueError('SYNTHETIC load error')
    monkeypatch.setattr(m,'load_pair',fail)
    monkeypatch.setattr(m,'_draw',lambda *args: pytest.fail('must not draw'))
    with pytest.raises(ValueError, match='load error'):
        prepare(small)
    assert not small['out'].exists()


def test_small_prepare_freezes_bytes_audit_before_inference_no_repeat(small, monkeypatch):
    path=prepare(small)
    assert small['calls']==['SYNTHETIC_LOAD']
    _, frame, _, requests=m.read_frame(path)
    assert len(requests)==35 and sum(r['full11_audit'] for r in requests)==3
    assert frame['protocol']['absolute_tolerances'][3]==0.04000000000000001
    assert frame['REAL_EMX_VALIDATION']=='NOT_RUN'
    monkeypatch.setattr(m,'_draw',lambda *args: pytest.fail('RNG must not regenerate identity'))
    m.read_frame(path)
    with pytest.raises(ValueError, match='no-clobber'):
        prepare(small)


def test_small_resume_skips_committed_prediction_and_keeps_shared_audit(small):
    path=prepare(small)
    r=m.run(path,max_new_batches=1)
    assert r['status']=='INFERENCE_PARTIAL_RESUMABLE' and r['committed_batches']==1
    first=small['out']/'inference/batch_000000/records.jsonl'; digest=m.sha256(first)
    r=m.run(path)
    assert r['status']=='INFERENCE_COMPLETE_NOT_NATIVE_RELEASE' and r['new_batches']==1
    assert m.sha256(first)==digest
    calls=small['calls'].copy()
    r=m.run(path)
    assert r['new_batches']==0 and small['calls']==calls
    assert [c[1] for c in calls if isinstance(c,tuple)]==[32,3]
    routes=[]
    for rec in r['shards']:
        receipt=m.read_json(rec['path'])
        routes.extend(m.read_json(receipt['artifacts']['ROUTES.json']['path']))
    assert len(routes)==35 and sum(len(x['audit_slots']) for x in routes)==33
    assert sum(len(x['unique_candidates']) for x in routes)==65 #35+3*10, not35+3*11
    assert all(not x['native_dispatch_authorized'] and x['q_emx'] is None for x in routes)


def test_zero_batch_observation_never_loads_model(small):
    path=prepare(small); before=small['calls'].copy()
    assert m.run(path,max_new_batches=0)['new_batches']==0
    assert small['calls']==before


def test_partial_shard_preserved_no_implicit_rerun(small):
    path=prepare(small)
    dest=small['out']/'inference/batch_000000';dest.mkdir(parents=True)
    m.save_json(dest/'INTENT.json',{'SYNTHETIC_PARTIAL':True})
    before=small['calls'].copy()
    with pytest.raises(ValueError,match='Partial shard preserved'):
        m.run(path)
    assert small['calls']==before and (dest/'INTENT.json').exists()


def test_corrupt_committed_shard_not_recomputed(small):
    path=prepare(small);m.run(path,max_new_batches=1)
    with (small['out']/'inference/batch_000000/records.jsonl').open('a') as f:f.write('synthetic corrupt\n')
    before=small['calls'].copy()
    with pytest.raises(ValueError,match='identity changed'):
        m.run(path)
    assert small['calls']==before


def test_tampered_target_file_rejected_without_rng(small):
    path=prepare(small)
    with (small['out']/'requests.jsonl').open('a') as f:f.write('{}\n')
    with pytest.raises(ValueError,match='identity changed'):
        m.read_frame(path)


def test_symlink_output_rejected_before_draw(small):
    link=small['root']/'link';link.symlink_to(small['root'],target_is_directory=True)
    with pytest.raises(ValueError,match='nonsymlink'):
        m.prepare(small['source'],link/'new',study_id='NO',seed=1,audit_seed=2)


@pytest.mark.parametrize('target', ['directory', 'lock'])
def test_symlink_inference_write_path_rejected(small, target):
    path=prepare(small); elsewhere=small['root']/'elsewhere';elsewhere.mkdir()
    root=small['out']/'inference'
    if target=='directory':root.symlink_to(elsewhere,target_is_directory=True)
    else:
        root.mkdir();(root/'inference.lock').symlink_to(elsewhere/'do_not_write')
    before=small['calls'].copy()
    with pytest.raises(ValueError,match='Symlink'):
        m.run(path)
    assert small['calls']==before and list(elsewhere.iterdir())==[]


@pytest.mark.parametrize('change', ['empty_artifacts', 'count', 'native', 'status'])
def test_forged_completion_metadata_is_not_counted(small, change):
    path=prepare(small);m.run(path,max_new_batches=1)
    receipt=small['out']/'inference/batch_000000/RECEIPT.json'
    value=m.read_json(receipt)
    if change=='empty_artifacts':value['artifacts']={}
    elif change=='count':value['logical_proxy_candidates']=0
    elif change=='native':value['native_started']=1
    else:value['status']='PENDING'
    # Intentional synthetic corruption, never modifying a real research receipt.
    receipt.write_text(json.dumps(value))
    before=small['calls'].copy()
    with pytest.raises(ValueError,match='Committed shard scope'):
        m.run(path)
    assert small['calls']==before


def test_cli_help_is_nonexecuting(capsys):
    with pytest.raises(SystemExit) as result:m.main(['--help'])
    assert result.value.code==0 and 'prepare' in capsys.readouterr().out


def test_public_defaults_are_10000_and100_not_a_pilot():
    assert m.N_REQUESTS==10000 and m.N_AUDIT==100


def test_loaded_manifest_identity_mismatch_is_rejected(small, monkeypatch):
    from research.broadband56_nn import frequency_tandem
    norm=m.read_json(small['model']['pins']['normalizer']['path'])
    state=dict(data_sha=small['model']['pins']['dataset']['sha256'],
        data_manifest_sha='0'*64,normalizer_sha=m.canonical_sha(norm),
        contract_sha=m.canonical_sha(norm),model_sha='model',best_model_sha='model',step=1)
    monkeypatch.setattr(frequency_tandem,'load_frequency_pair',lambda *a,**kw:(object(),object(),state,state))
    with pytest.raises(ValueError,match='Loaded validation-best pair differs'):
        REAL_LOAD_PAIR(small['model'])


def test_corrupt_completed_frame_counts_not_reused(small):
    path=prepare(small);m.run(path)
    receipt=small['out']/'inference/INFERENCE_COMPLETE.json'
    value=m.read_json(receipt);value['logical_counts']['main_requests']=9999
    receipt.write_text(json.dumps(value)) # Intentional synthetic-only corruption.
    before=small['calls'].copy()
    with pytest.raises(ValueError,match='Completed inference receipt changed'):
        m.run(path)
    assert before==small['calls']
