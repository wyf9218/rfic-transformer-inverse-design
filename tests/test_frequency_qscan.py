"""Synthetic Q-scan contract tests, no actual model/data/EMX execution."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from research.broadband56_nn import frequency_qscan as q


def test_all_eleven_targets_keep_three_inputs_and_own_q():
    targets=q.q_targets([1.2,1.4,.3])
    assert targets.shape==(11,4)
    assert targets[:,2].tolist()==list(range(10,21))
    assert np.all(targets[:,[0,1,3]]==[1.2,1.4,.3])


def test_original_score_units_and_exact_tie():
    targets=q.q_targets([1.,1.,.2])
    assert np.allclose(q.score(targets+q.SCALE,targets),1)
    assert q.select_q(np.zeros(11))['q_proxy']==10
    scores=np.ones(11);scores[7]=0
    assert q.select_q(scores)['q_proxy']==17
    assert q.TAU.tolist()==[.125,.125,1.,.04000000000000001]


def test_missing_candidate_does_not_claim_full_scan():
    scores=np.zeros(11);scores[4]=np.nan
    result=q.select_q(scores)
    assert result['q_proxy'] is None and result['best_available_proxy']==10
    assert not result['full_proxy_scan'] and result['N_proxy_finite']==10
    with pytest.raises(ValueError): q.select_q(np.zeros(10))


@pytest.mark.parametrize('bad',([1,2],[1,2,np.nan],[[1,2,3]]))
def test_invalid_triple_rejected(bad):
    with pytest.raises(ValueError):q.q_targets(bad)


def test_lhs3_exact_strata_seed_and_no_feasibility_inference():
    a=q.lhs3([0,2,4],[1,3,5],10000,30)
    assert np.array_equal(a,q.lhs3([0,2,4],[1,3,5],10000,30))
    assert len(np.unique(a,axis=0))==10000
    for j in range(3):
        assert set(np.floor((a[:,j]-[0,2,4][j])*10000).astype(int))==set(range(10000))


def synthetic_context(tmp_path):
    data=tmp_path/'data';data.mkdir()
    for name in ('dataset.npz','data_manifest.json','splits.json','f.pt','i.pt'):
        (data/name).write_text('SYNTHETIC '+name)
    y=np.asarray([[1,1,10,.1],[2,2,12,.2],[3,3,14,.3],[1000,1000,999,.9]],float)[:,None,:]
    bundle=SimpleNamespace(arrays={'y':y,'geometry':np.ones((4,10)),
        'geometry_ids':np.array(['g0','g1','g2','g3']), 'geometry_sha256':np.array(['a'*64,'b'*64,'c'*64,'d'*64])})
    norm={'response_spans':q.SCALE.tolist(),'train_support_min':[0,0,0,0], 'train_support_max':[5,5,25,1]}
    state={'contract':{'field_names':['g'+str(i) for i in range(10)]},'train_config':{'seed':17}}
    config=dict(schema='frequency_qscan_request.v1',study_id='synthetic',dataset_scope='SYNTHETIC',
        data_root=str(data),forward_checkpoint=str(data/'f.pt'),inverse_checkpoint=str(data/'i.pt'),
        frequency_ghz=15,label_mode='STRICT_LUMPED',random_count=10000,holdout_count=100,batch_requests=32,
        allow_extrapolation=True,seed=42)
    return config,(bundle,state,state,norm,np.array([0,1,2]),np.array([3]),0,{})


def test_prepare_train_only_and_immutable_preselection(tmp_path,monkeypatch):
    config,context=synthetic_context(tmp_path)
    monkeypatch.setattr(q,'_load_context',lambda c:context)
    result=q.prepare(config,tmp_path/'out')
    assert result['model_prediction_calls']==0
    assert result['train_window']['p99'][0]<3  # 1000-valued test never fits window
    rows=q._read_csv(tmp_path/'out/requests.csv')
    assert len(rows)==10001 and len({r['request_id'] for r in rows})==10001
    assert sum(r['preselected_emx']=='True' for r in rows)==21
    assert result['protocol']['q_values']==list(range(10,21))
    with pytest.raises(FileExistsError):q.prepare(config,tmp_path/'out')


def test_batch_preserves_raw_grid_support_and_not_emx(tmp_path,monkeypatch):
    norm=tmp_path/'norm.json';norm.write_text(json.dumps({'train_support_min':[0,0,10,0], 'train_support_max':[2,2,18,1]}))
    contract=tmp_path/'contract.json';contract.write_text(json.dumps(dict(field_names=['g'+str(i) for i in range(10)],
        lower=[0]*10,upper=[2]*10,grid_um=.005,grid_source_sha256='a'*64,
        grid_status='SOURCE_VERIFIED_EXPORT_ONLY_NO_STRAIGHT_THROUGH_ESTIMATOR')))
    freeze=dict(artifacts={'normalizer.json':{'path':str(norm)},'geometry_contract.json':{'path':str(contract)}},
                config={'allow_extrapolation':True,'dataset_scope':'SYNTHETIC'},frequency_ghz=15,model_id='synthetic')
    calls=[]
    def predict(model,values,dim,device,batch):
        calls.append((model,len(values)))
        if model=='I':return np.ones((len(values),dim))*.333333,[]
        return np.tile([1,1,15,.3],(len(values),1)),[]
    monkeypatch.setattr(q,'_predict',predict)
    monkeypatch.setattr(q,'_geometry_status',lambda values,c:(np.ones(len(values),bool),np.ones(len(values),bool)))
    requests=[dict(request_id='r0',lp_nh=1,ls_nh=1,k_abs=.3,target_source='HELDOUT_TRIPLE_AUDIT',preselected_emx=True)]
    candidates,summary,failures=q._batch(requests,freeze,'F','I')
    assert calls==[('I',11),('F',11),('F',11)]
    assert len(candidates)==11 and summary[0]['q_proxy']==15
    assert candidates[0]['continuous_geometry'][0]==.333333
    assert candidates[0]['grid_geometry'][0]==.335
    assert [r['q_target'] for r in candidates if r['support_status']=='EXTRAPOLATION']==[19,20]
    assert all(r['actual_response'] is None and r['emx_status']=='NOT_RUN' for r in candidates)
    assert summary[0]['q_emx'] is None and summary[0]['independent_solves']==0
    q._physical_queue(tmp_path,candidates)
    pending=json.loads((tmp_path/'physical_pending/r0/PENDING_REQUEST.json').read_text())
    assert pending['N_logical_candidates']==11 and pending['status']=='NOT_DISPATCHED'
    before=q.pin(tmp_path/'physical_pending/r0/eleven_candidates.jsonl')
    q._physical_queue(tmp_path,candidates)
    assert before==q.pin(tmp_path/'physical_pending/r0/eleven_candidates.jsonl')
