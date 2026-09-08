"""Synthetic-only large evaluation contracts; no real model/data/EMX calls."""
from types import SimpleNamespace
import csv
import json
import numpy as np
import pytest

from research.broadband56_nn import frequency_large_eval as ev
from research.broadband56_nn.io import save_json, canonical_sha


def context(tmp_path):
    data = tmp_path/"synthetic_data"; data.mkdir()
    for name in ("dataset.npz", "data_manifest.json", "splits.json", "forward.pt", "inverse.pt"):
        (data/name).write_text("SYNTHETIC_ONLY "+name)
    y = np.arange(24, dtype=float).reshape(6, 1, 4)/10+1
    bundle = SimpleNamespace(arrays={"y": y, "geometry": np.ones((6, 10)),
        "geometry_ids": np.asarray([f"geom{i}" for i in range(6)]),
        "geometry_sha256": np.asarray([str(i)*64 for i in range(6)]),
        "broadband_descriptor_valid": np.ones((6, 1), dtype=bool),
        "strict_lumped_valid": np.ones((6, 1), dtype=bool)})
    state = {"normalizer": {"response_spans": [2.5, 2.5, 20, .8]},
             "contract": {"field_names": [f"g{i}" for i in range(10)]},
             "train_config": {"seed":17}, "step":9}
    request = {"schema":"frequency_large_eval_request.v1", "study_id":"synthetic",
        "data_root":str(data), "forward_checkpoint":str(data/"forward.pt"),
        "inverse_checkpoint":str(data/"inverse.pt"), "frequency_ghz":15,
        "label_mode":"STRICT_LUMPED", "dataset_scope":"SYNTHETIC_TEST_ONLY",
        "random_count":20,"emx_per_panel":2}
    return request, (bundle, state, state, state["normalizer"], np.array([0,1,2]), np.array([4,5]), 0, {})


def test_prepare_zero_predictions_and_selection_frozen(tmp_path, monkeypatch):
    request, state = context(tmp_path)
    monkeypatch.setattr(ev, "_load_context", lambda config:state)
    frozen = ev.prepare(request, tmp_path/"out")
    assert frozen["prepare_model_prediction_calls"] == 0
    assert frozen["source_unique_geometries"] == 6
    targets = ev._read_csv(tmp_path/"out/target_manifest.csv")
    assert len(targets) == 24
    assert len({r["target_id"] for r in targets}) == 24
    assert sum(ev._bool(r["emx_preselected"]) for r in targets) == 4
    for row in targets:
        if row["panel"] == "C":
            assert row["target_feasibility"] == "UNKNOWN" and row["reference_geometry_id"] == ""
    before = ev.pin(tmp_path/"out/target_manifest.csv")
    with pytest.raises(FileExistsError): ev.prepare(request, tmp_path/"out")
    assert ev.pin(tmp_path/"out/target_manifest.csv") == before


def test_formal10k_cannot_be_declared_for_smaller_frame(tmp_path, monkeypatch):
    request, state = context(tmp_path)
    request["dataset_scope"]="FORMAL_10K"
    monkeypatch.setattr(ev,"_load_context",lambda config:state)
    with pytest.raises(ValueError,match="exactly10000"): ev.prepare(request,tmp_path/"out")
    assert not (tmp_path/"out").exists()


def test_lhs_reproducible_unique_strata_and_constant_dimension():
    train = np.column_stack([np.arange(101)]*3+[np.ones(101)*7.])
    window = ev.evaluation_window(train,[2.5,2.5,20,.8])
    a = ev.randomized_lhs(window,100,18); b=ev.randomized_lhs(window,100,18)
    assert np.array_equal(a,b) and np.all(a[:,3]==7)
    assert window["actual_p99_minus_p01"][3] == 0
    assert window["evaluation_scale"][3] == .8
    assert window["degenerate_dimensions"][3] is True
    assert "FALLBACK_CONSTANT" in window["scale_source_per_feature"][3]
    for j in range(3):
        strata = np.floor((a[:,j]-window["p01"][j])/(window["p99"][j]-window["p01"][j])*100).astype(int)
        assert set(strata)==set(range(100))


def test_train_quantiles_and_positive_tolerance():
    values=np.arange(80,dtype=float).reshape(20,4)
    window=ev.evaluation_window(values,[1,2,3,4])
    assert window["p01"]==np.quantile(values,.01,axis=0,method="linear").tolist()
    assert np.all(np.asarray(window["tolerance_abs"])>0)
    for bad in ([0,1,1,1], [np.nan,1,1,1]):
        with pytest.raises(ValueError):ev.evaluation_window(values,bad)


def test_full_heldout_tuple_target_not_independently_recombined(tmp_path):
    request, state = context(tmp_path)
    request.update(sampling_seed=22,emx_selection_seed=33,random_count=10,emx_per_panel=2)
    bundle=state[0]; test=state[5]
    window=ev.evaluation_window(bundle.arrays["y"][:3,0],[1,1,1,1])
    rows, selected=ev.make_targets(request,bundle,test,0,window)
    for panel in ("A","B"):
        for row,index in zip([x for x in rows if x["panel"]==panel],test):
            assert [row[k] for k in ev.TARGET_COLUMNS]==bundle.arrays["y"][index,0].tolist()
    assert len({row["target_id"] for row in selected}) == 4


def test_mask_preserves_strict_and_descriptor_contract():
    arrays={"frequency_hz":np.array([15e9]), "y":np.ones((4,1,4)),
        "y_valid":np.ones((4,1,4),dtype=bool),
        "strict_lumped_valid":np.array([[True],[False],[True],[True]]),
        "broadband_descriptor_valid":np.ones((4,1),dtype=bool)}
    arrays["y_valid"][2,0,1]=False
    arrays["y"][3,0,2]=np.nan
    bundle=SimpleNamespace(arrays=arrays)
    assert ev.frequency_mask(bundle,15,"STRICT_LUMPED").tolist()==[True,False,False,False]
    assert ev.frequency_mask(bundle,15,"POINTWISE_DESCRIPTOR_EXPERIMENTAL").tolist()==[True,True,True,False]


def records(n=3, panel="C", stage="raw"):
    freeze={"config":{"study_id":"synthetic"},"identity":{"dataset":{"sha256":"d"},
        "inverse_checkpoint":{"sha256":"i"},"forward_checkpoint":{"sha256":"f"},"normalizer_sha256":"n"},
        "training_seed":17,"model_id":"synthetic","train_window":{"evaluation_scale":[2.,4.,8.,.5],"tolerance_abs":[.1,.2,.4,.025]}}
    targets=[{"panel":panel,"target_id":f"t{i}","reference_geometry_id":f"g{i}" if panel=="A" else "",
        "frequency_ghz":15,"label_mode":"STRICT_LUMPED","support_tag":"INSIDE_TRAIN_MARGINAL_RANGE_JOINT_NOT_PROVEN",
        **dict(zip(ev.TARGET_COLUMNS,[1.,1.,10.,.3]))} for i in range(n)]
    prediction=np.asarray([[1.,1.,10.,.3]]*n)
    return freeze,targets,prediction


def test_four_way_AND_not_rms_and_geometry_independent():
    frozen,targets,prediction=records(2)
    prediction[0,0]+=.11  # only one dimension fails, RMS would understate it
    rows=ev.prediction_rows(targets,prediction,frozen,"raw",np.array([True,False]))
    group=ev.summarize_records(rows)[0]
    assert group["ALL_FOUR_HIT_count"]==1
    assert group["geometry_pass_count"]==1
    assert rows[4]["geometry_status"]=="ANALYTIC_FAIL"
    assert group["features"]["Lp_nH"]["MAE_available"]==pytest.approx(.055)


def test_failed_nonfinite_predictions_keep_full_denominator():
    frozen,targets,prediction=records(4)
    prediction[1,:]=np.nan
    prediction[2,1]=np.nan
    rows=ev.prediction_rows(targets,prediction,frozen,"raw",np.ones(4,dtype=bool))
    group=ev.summarize_records(rows,{"C":5})[0]
    assert group["N_requested"]==5 and group["N_completed"]==4
    assert group["N_finite"]==2 and group["N_failed"]==2 and group["N_pending"]==1
    assert group["ALL_FOUR_HIT_rate"]==.4
    assert group["features"]["Ls_nH"]["N_finite"]==2
    assert group["features"]["Lp_nH"]["N_finite"]==3
    assert group["features"]["Lp_nH"]["MAE_full_population_status"].startswith("NOT_DEFINED")
    assert all(row["error_abs"] is None for row in rows[4:8])


def test_source_mask_and_physical_validity_are_not_hidden():
    frozen,targets,prediction=records(2)
    prediction[0,0]=-1
    rows=ev.prediction_rows(targets,prediction,frozen,"raw",np.ones(2,dtype=bool))
    rows[4]["source_label_valid"]=False
    group=ev.summarize_records(rows)[0]
    assert group["features"]["Lp_nH"]["N_finite"]==1
    assert group["features"]["Lp_nH"]["MAE_available"]==2 # negative finite prediction stays in error distribution
    assert group["N_finite"]==1


def test_units_duplicate_ids_and_missing_feature_rejected():
    frozen,targets,prediction=records()
    rows=ev.prediction_rows(targets,prediction,frozen,"raw",np.ones(3,dtype=bool))
    with pytest.raises(ValueError,match="duplicate"):ev.summarize_records(rows+[rows[0]])
    with pytest.raises(ValueError,match="all four"):ev.summarize_records(rows[:-1])
    rows[0]["unit"]="H"
    with pytest.raises(ValueError,match="unit"):ev.summarize_records(rows)


def test_panel_and_evaluator_separation():
    frozen,targets,prediction=records()
    rows=ev.prediction_rows(targets,prediction,frozen,"raw",np.ones(3,dtype=bool))
    rows2=[dict(row,panel="B") for row in rows]
    rows3=[dict(row,evaluation_source="INDEPENDENT_PROXY") for row in rows]
    assert len(ev.summarize_records(rows+rows2+rows3))==3


def test_micro_macro_and_band_quantile_recomputed_from_records():
    frozen,targets,prediction=records(4)
    prediction[:,0]=[1,1,1,11]
    rows=ev.prediction_rows(targets,prediction,frozen,"raw",np.ones(4,dtype=bool))
    for row in rows:
        if row["target_id"]=="t3":row["frequency_ghz"]=16
    band=next(x for x in ev.band_statistics(rows) if x["feature"]=="Lp_nH")
    assert band["micro_MAE"]==2.5
    assert band["macro_MAE"]==5
    assert band["micro_P95_abs"]==pytest.approx(8.5)
    assert band["micro_P95_abs"]!=5 # averaging per-frequency P95 would give5


def test_prediction_normalization_not_target_relative():
    frozen,targets,prediction=records(1)
    prediction[0,3]+=.01
    row=ev.prediction_rows(targets,prediction,frozen,"raw",np.ones(1,dtype=bool))[3]
    assert row["error_normalized"]==pytest.approx(.02)
    assert row["error_over_tolerance"]==pytest.approx(.4)
    assert row["unit"]=="dimensionless"


def test_committed_shard_is_reused_and_identity_mismatch_rejected(tmp_path):
    root=tmp_path/"batch_0";attempt=root/"attempt_0001";attempt.mkdir(parents=True)
    save_json(attempt/"artifact.json",{"synthetic":True})
    save_json(attempt/"SHARD_RECEIPT.json",{"status":"COMPLETE","target_ids":["t1"],"protocol_sha256":"p",
        "artifacts":{"x":ev.pin(attempt/"artifact.json")}})
    assert ev._existing_shard(root,["t1"],"p")==attempt/"SHARD_RECEIPT.json"
    with pytest.raises(ValueError):ev._existing_shard(root,["t2"],"p")
    (attempt/"artifact.json").write_text("changed")
    with pytest.raises(ValueError):ev._existing_shard(root,["t1"],"p")


def test_prepare_source_change_is_provenance_but_core_change_rejected(tmp_path,monkeypatch):
    request,state=context(tmp_path)
    monkeypatch.setattr(ev,"_load_context",lambda config:state)
    ev.prepare(request,tmp_path/"out")
    path=tmp_path/"out/CONFIGURATION_FREEZE.json"
    frozen=json.loads(path.read_text())
    frozen["implementation"]["frequency_large_eval.py"]["sha256"]="historical_prepare_source"
    path.write_text(json.dumps(frozen))
    assert ev._verify_freeze(tmp_path/"out")["geometry_fields"]==state[1]["contract"]["field_names"]
    frozen["implementation"]["bb00.py"]["sha256"]="wrong"
    path.write_text(json.dumps(frozen))
    with pytest.raises(ValueError):ev._verify_freeze(tmp_path/"out")


def _mock_execution(tmp_path,monkeypatch):
    from research.broadband56_nn import frequency_tandem,training
    request,state=context(tmp_path)
    request["batch_size"]=4
    monkeypatch.setattr(ev,"_load_context",lambda config:state)
    ev.prepare(request,tmp_path/"out")
    monkeypatch.setattr(frequency_tandem,"load_frequency_pair",lambda *a,**kw:(None,None,None,None))
    monkeypatch.setattr(training,"Bundle",lambda *a:state[0])
    calls=[]
    def batch(targets,freeze,*unused):
        calls.append([row["target_id"] for row in targets])
        prediction=np.asarray([[float(row[key]) for key in ev.TARGET_COLUMNS] for row in targets])
        candidates=[]
        if targets[0]["panel"]!="A":
            candidates=[ev._candidate(row,np.ones(10),np.ones(10),(True,True),(True,True),freeze) for row in targets]
        rows=[]
        for stage in (("reference",) if targets[0]["panel"]=="A" else ("raw","grid")):
            rows.extend(ev.prediction_rows(targets,prediction,freeze,stage,np.ones(len(targets),dtype=bool),candidates))
        return rows,candidates,{},{}
    monkeypatch.setattr(ev,"_batch",batch)
    return tmp_path/"out",calls


def test_real_shard_protocol_small_C_then_resume_no_repeat(tmp_path,monkeypatch):
    out,calls=_mock_execution(tmp_path,monkeypatch)
    partial=ev.run(out,panels=["C"],max_new_batches=1)
    assert partial["status"]=="PARTIAL_SHARDS_SAVED"
    assert len(calls)==1 and len(calls[0])==4 and ":C:" in calls[0][0]
    first=ev.pin(next((out/"panel_C").glob("shards/*/attempt_*/SHARD_RECEIPT.json")))
    complete=ev.run(out)
    assert complete["status"]=="ABC_COMPLETE_D_PENDING"
    assert len(calls)==7
    assert len({x for call in calls for x in call})==24
    assert ev.pin(first["path"])==first
    assert complete["N_by_panel"]=={"A":2,"B":2,"C":20}
    assert complete["N_D_selected_by_source"]=={"B":2,"C":2}
    assert len(complete["groups"])==7
    assert len(complete["frequency_status"])==112
    assert len(ev._read_csv(out/"prediction_records.csv"))==184
    before=len(calls);ev.run(out);assert len(calls)==before


def test_final_publication_resume_preserves_bytes_without_model_rerun(tmp_path,monkeypatch):
    out,calls=_mock_execution(tmp_path,monkeypatch)
    original=ev._resumable_json
    def fail_once(path,value):
        if path.name=="prediction_manifest.json":raise RuntimeError("synthetic interrupted publication")
        return original(path,value)
    monkeypatch.setattr(ev,"_resumable_json",fail_once)
    with pytest.raises(RuntimeError,match="interrupted"):ev.run(out)
    assert len(calls)==7
    before=ev.pin(out/"summary.json")
    monkeypatch.setattr(ev,"_resumable_json",original)
    assert ev.run(out)["status"]=="ABC_COMPLETE_D_PENDING"
    assert len(calls)==7 and ev.pin(out/"summary.json")==before


def test_atomic_publication_never_overwrites_different_existing_result(tmp_path):
    path=tmp_path/"rows.csv"
    ev._csv(path,[{"value":1}])
    first=ev.pin(path)
    ev._csv(path,[{"value":1}],allow_identical=True)
    with pytest.raises(FileExistsError):ev._csv(path,[{"value":2}],allow_identical=True)
    assert ev.pin(path)==first


def test_incomplete_attempt_preserved_without_counting_it(tmp_path,monkeypatch):
    out,calls=_mock_execution(tmp_path,monkeypatch)
    bad=out/"panel_C/shards/batch_000000/attempt_0001";bad.mkdir(parents=True)
    (bad/"partial.txt").write_text("SYNTHETIC interrupted uncommitted attempt")
    before=ev.pin(bad/"partial.txt")
    result=ev.run(out,panels=["C"],max_new_batches=1)
    assert result["status"]=="PARTIAL_SHARDS_SAVED" and len(calls)==1
    assert ev.pin(bad/"partial.txt")==before
    assert (bad.parent/"attempt_0002/SHARD_RECEIPT.json").exists()


def test_batch_is_one_inverse_two_forward_no_repair_and_timing(tmp_path,monkeypatch):
    import torch
    from research.broadband56_nn import frequency_evaluation
    request,state=context(tmp_path)
    monkeypatch.setattr(ev,"_load_context",lambda config:state)
    ev.prepare(request,tmp_path/"out")
    freeze=ev._verify_freeze(tmp_path/"out")
    contract=freeze["geometry_contract"]
    contract.update(grid_um=.005,grid_source_sha256="a"*64,
        grid_status="SOURCE_VERIFIED_EXPORT_ONLY_NO_STRAIGHT_THROUGH_ESTIMATOR",
        lower=[0]*10,upper=[2]*10)
    targets=[row for row in ev._read_csv(tmp_path/"out/target_manifest.csv") if row["panel"]=="C"][:4]
    calls=[]
    def predict(model,values,dim,device,batch):
        calls.append(model)
        return np.ones((len(values),dim))*.333333,[]
    monkeypatch.setattr(frequency_evaluation,"_predict",predict)
    monkeypatch.setattr(ev,"_geometry_status",lambda g,c:(np.ones(len(g),dtype=bool),np.ones(len(g),dtype=bool)))
    bundle=SimpleNamespace(dim=10)
    rows,candidates,failures,timing=ev._batch(targets,freeze,bundle,"F","I",None)
    assert calls==["I","F","F"]
    assert len(rows)==32 and len(candidates)==4
    assert candidates[0]["generated_geometry_raw"][0]==.333333
    assert candidates[0]["generated_geometry_grid"][0]==.335
    assert candidates[0]["actual_gds_geometry"] is None
    assert candidates[0]["canonical_mapping"]=="grid_only_not_layout_audited"
    assert timing["inverse_batch_ms"]>=0
    assert all(row["evaluation_source"]=="SELF_PROXY" for row in rows)


def test_reuse_requires_exact_heldout_population_tuple_and_no_duplicates(tmp_path):
    frozen,targets,prediction=records(2,panel="A",stage="reference")
    for i,row in enumerate(targets):row["reference_geometry_id"]=f"g{i}"
    other=[dict(row,panel="B",target_id="B"+row["target_id"]) for row in targets]
    forward=[];inverse=[]
    for row in targets:
        item={"target_id":row["reference_geometry_id"]}
        item.update({"truth__"+f:row[ev.TARGET_COLUMNS[j]] for j,f in enumerate(ev.FEATURES)})
        forward.append(item)
        for mode in ("continuous","grid"):
            item={"target_id":row["reference_geometry_id"],"mode":mode}
            item.update({"target__"+f:row[ev.TARGET_COLUMNS[j]] for j,f in enumerate(ev.FEATURES)})
            inverse.append(item)
    ev._csv(tmp_path/"forward.csv",forward);ev._csv(tmp_path/"inverse.csv",inverse)
    inputs={"sources":{"forward_predictions.csv":ev.pin(tmp_path/"forward.csv"),"inverse_predictions.csv":ev.pin(tmp_path/"inverse.csv")}}
    assert len(ev._load_reuse(inputs,targets+other)["B"])==4
    other[0]["target_q_scalar"]=12
    with pytest.raises(ValueError,match="tuple changed"):ev._load_reuse(inputs,targets+other)
