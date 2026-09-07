"""Synthetic functional checks of new BB00 training, never historical retraining."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import torch

from research.broadband56_nn.bb00 import BB00Config, BB00MLP, prepare_bb00, train_bb00, load_bb00
from research.broadband56_nn.io import sha256, save_json, load_checkpoint
from research.broadband56_nn.training import Bundle
from tests.test_bb_training import fixture_data


def synthetic_recipe(tmp_path):
    loss = {"response_weight":1.,"geometry_anchor_weight":.01,"topology_feasibility_weight":0.,
        "response_loss_scaling":"declared_range","response_loss_family":"mse","response_weight_schedule":"warmup_ramp_adaptive_ema",
        "response_schedule_domain":"optimizer_update","response_warmup_fraction":.05,"response_ramp_fraction":.25,
        "response_adaptive_ema_decay":.95,"response_adaptive_min_multiplier":.25,"response_adaptive_max_multiplier":4.}
    summary = tmp_path/"synthetic_legacy_summary.json"
    save_json(summary, {"model_comparison_contract":{"loss":loss,"optimization":{"evidence":"SYNTHETIC"}}})
    receipt = tmp_path/"synthetic_legacy_receipt.json"
    save_json(receipt, {"schema":"bb_r0_replay.v1","status":"PASS","model_id":"SYNTHETIC_RECIPE_ONLY",
        "architecture":{"forward_surrogate":[10,256,256,256,4],"inverse_mlp":[4,256,256,256,10],"hidden_activation":"gelu","geometry_projection":"sigmoid_to_training_envelope"},
        "target_frequency_ghz":15,"historical_loss_contract":loss,"sources":{"summary":{"path":str(summary),"sha256":sha256(summary)}},
        "trainer_identity":{"exact_original_trainer_identity_proven":False}})
    return receipt


def inputs(tmp_path):
    data, contract = fixture_data(tmp_path)
    # Keep a nondegenerate synthetic valid15GHz source. Rewrite only test fixture.
    with np.load(data/"dataset.npz") as a:
        values = {k:a[k] for k in a.files}
    values["y"] = np.broadcast_to(np.arange(8)[:,None,None]*.02+np.array([1.,2.,10.,.4]),(8,56,4)).copy()
    np.savez(data/"dataset.npz",**values)
    manifest = json.loads((data/"data_manifest.json").read_text())
    manifest["artifacts"]["dataset.npz"]["sha256"] = sha256(data/"dataset.npz")
    (data/"data_manifest.json").write_text(json.dumps(manifest))
    return data, contract, synthetic_recipe(tmp_path)


def test_actual_new_optimizer_and_exact_continuation_in_fresh_process(tmp_path):
    torch.set_num_threads(2)
    data, contract, recipe = inputs(tmp_path)
    cfg = BB00Config("forward",steps=1,schedule_total_steps=2,device="cpu",validation_interval=1,allow_io_adaptation=True)
    first = train_bb00(data,tmp_path/"first",cfg,contract,recipe,sha256(recipe))
    whole = train_bb00(data,tmp_path/"whole",replace(cfg,steps=2),contract,recipe,sha256(recipe))
    code = "from research.broadband56_nn.bb00 import BB00Config,train_bb00; import sys,json; c=BB00Config(**json.loads(sys.argv[1])); r=train_bb00(sys.argv[2],sys.argv[3],c,sys.argv[4],sys.argv[5],sys.argv[6],resume_checkpoint=sys.argv[7]); print(json.dumps(r))"
    from dataclasses import asdict
    command = [sys.executable,"-c",code,json.dumps(asdict(cfg)),str(data),str(tmp_path/"resumed"),str(contract),str(recipe),sha256(recipe),first["last_checkpoint"]]
    result = subprocess.run(command,env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]),OMP_NUM_THREADS="2"),capture_output=True,text=True,check=True)
    resumed = json.loads(result.stdout)
    state = load_checkpoint(resumed["last_checkpoint"])
    complete = load_checkpoint(whole["last_checkpoint"])
    assert resumed["started_step"]==1 and resumed["completed_step"]==2
    assert state["model_sha"]==complete["model_sha"]
    assert state["rng_state"]["sampler"]==complete["rng_state"]["sampler"]
    assert state["optimizer_state"]["state"] and state["scheduler_state"]==complete["scheduler_state"]
    assert state["historical_weights_loaded"] is False and state["normalizer"]["io_status"]=="IO_ADAPTED"
    model,_=load_bb00(resumed["last_checkpoint"])
    assert model(torch.ones(3,2)).shape==(3,4)
    with pytest.raises(ValueError,match="resume mismatch"):
        train_bb00(data,tmp_path/"wrong",replace(cfg,lr=.01),contract,recipe,sha256(recipe),resume_checkpoint=first["last_checkpoint"])


def test_inverse_tandem_frozen_forward_and_geometry_units(tmp_path):
    torch.set_num_threads(2)
    data,contract,recipe=inputs(tmp_path)
    f=train_bb00(data,tmp_path/"forward",BB00Config("forward",steps=2,schedule_total_steps=2,device="cpu",validation_interval=1,allow_io_adaptation=True),contract,recipe,sha256(recipe))
    cfg=BB00Config("inverse",steps=2,schedule_total_steps=3,device="cpu",validation_interval=1,allow_io_adaptation=True,forward_checkpoint=f["best_checkpoint"])
    result=train_bb00(data,tmp_path/"inverse",cfg,contract,recipe,sha256(recipe))
    model,state=load_bb00(result["best_checkpoint"])
    forward,_=load_bb00(f["best_checkpoint"])
    forward.requires_grad_(False)
    targets=torch.tensor([[1.,2.,10.,.4]],requires_grad=True)
    geometry=model(targets)
    forward(geometry).sum().backward()
    assert targets.grad is not None and torch.isfinite(targets.grad).all()
    assert all(p.grad is None for p in forward.parameters())
    physical_low=model.g_lower*model.g_scale+model.g_mean
    physical_high=model.g_upper*model.g_scale+model.g_mean
    assert torch.all(geometry>=physical_low) and torch.all(geometry<=physical_high)
    resumed=train_bb00(data,tmp_path/"inverse_resume",replace(cfg,steps=1),contract,recipe,sha256(recipe),resume_checkpoint=result["last_checkpoint"])
    assert resumed["completed_step"]==3 and resumed["frozen_forward_unchanged"]
    with pytest.raises(FileExistsError):
        train_bb00(data,tmp_path/"inverse",cfg,contract,recipe,sha256(recipe))


def test_only_new_valid_train_rows_fit_normalizer_and_adapter_opt_in(tmp_path):
    data,contract,_=inputs(tmp_path)
    bundle=Bundle(data)
    c=json.loads(contract.read_text())
    with pytest.raises(ValueError,match="IO adaptation"):
        prepare_bb00(bundle,c,[2.5,2.5,20,.8])
    normalizer,train,val,frequency,counts=prepare_bb00(bundle,c,[2.5,2.5,20,.8],True)
    bundle.arrays["y"][bundle.val]=1e8
    changed,*_=prepare_bb00(bundle,c,[2.5,2.5,20,.8],True)
    assert normalizer==changed and frequency==10
    assert counts["train"]["eligible15ghz_geometries"]==6
    assert set(train).isdisjoint(val)


def test_both_roles_fresh_process_delivery_one_update_proof(tmp_path):
    from research.broadband56_nn.bb00_delivery import verify_bb00_load_resume
    torch.set_num_threads(2)
    data,contract,recipe=inputs(tmp_path)
    config=BB00Config("forward",steps=2,schedule_total_steps=2,device="cpu",validation_interval=1,allow_io_adaptation=True)
    forward=train_bb00(data,tmp_path/"forward",config,contract,recipe,sha256(recipe))
    inverse=train_bb00(data,tmp_path/"inverse",replace(config,role="inverse",forward_checkpoint=forward["best_checkpoint"]),contract,recipe,sha256(recipe))
    proof=verify_bb00_load_resume(data,forward,inverse,tmp_path/"proof",contract,recipe,sha256(recipe),"cpu")
    assert proof["status"]=="PASS" and proof["original_checkpoint_bytes_unchanged"]
    assert all(row["last_step"]==2 and row["resumed_step"]==3 for row in proof["results"].values())
    assert all(all(row["checks"].values()) for row in proof["results"].values())
    assert all(row["load_pid"]!=os.getpid() and row["resume_pid"]!=os.getpid() for row in proof["results"].values())
    probe=tmp_path/"proof/forward/one_update_only/checkpoint_step_000003.pt"
    with pytest.raises(ValueError,match="diagnostic resume descendants"):
        train_bb00(data,tmp_path/"forbidden_promotion",replace(config,steps=1),contract,recipe,sha256(recipe),resume_checkpoint=probe)
