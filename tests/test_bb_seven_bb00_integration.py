"""Synthetic10D integration through actual seven-suite BB00 child workers.

No research snapshot selection, formal10K admission, original-weight replay,
production process, physical simulator, or real dataset is involved.
Set BB00_INTEGRATION_EVIDENCE_ROOT to preserve generated synthetic artifacts.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from research.broadband56_nn import seven_suite
from research.broadband56_nn.io import canonical_sha, load_checkpoint, save_json, sha256
from rfic_transformer_inverse_design.synthesis.frozen_mlp import GEOMETRY_COLUMNS
from tests.test_bb00 import synthetic_recipe


def fixture_10d(root):
    data=root/"synthetic_data"
    data.mkdir()
    rng=np.random.default_rng(170033)
    count=24
    fields=[name.removeprefix("geom__") for name in GEOMETRY_COLUMNS]
    # Deliberately generous clearances: all independently sampled terminals,
    # offsets and feed lengths stay far inside these synthetic geometry bounds.
    # This is not a foundry/DRC/EMX validity claim.
    lower=np.array([300,300,300,300,4,20,20,-10,80,80],float)
    upper=np.array([350,350,350,350,8,40,40,10,120,120],float)
    unit=rng.uniform(.1,.9,size=(count,10))
    geometry=lower+unit*(upper-lower)
    frequencies=np.arange(5,61,dtype=np.int64)*10**9
    physical=np.stack([1.+unit[:,0],1.5+unit[:,1],10.+5*unit[:,4],.85+.04*unit[:,5]],axis=-1)
    y=np.broadcast_to(physical[:,None,:],(count,56,4)).copy()
    y_valid=np.ones_like(y,dtype=bool)
    # Explicit strict-invalid rows remain missing; no zero-valued replacement.
    for index in (0,16,20):
        y_valid[index,10]=False
        y[index,10]=np.nan
    split=np.array([0]*16+[1]*4+[2]*4,dtype=np.int8)
    s=rng.normal(0,.02,(count,56,32))
    with (data/"dataset.npz").open("xb") as stream:
        np.savez(stream,geometry_ids=np.array([f"SYNTHETIC_{i:03d}" for i in range(count)]),
            geometry_sha256=np.array([f"{i:064x}" for i in range(count)]),geometry=geometry,
            frequency_hz=frequencies,s=s,s_valid=np.ones_like(s,dtype=bool),y=y,y_valid=y_valid,split=split)
    train_y=y[split==0]
    norm={"field_names":fields,"g_min":geometry[split==0].min(0).tolist(),"g_max":geometry[split==0].max(0).tolist(),
        "s_mean":s[split==0].mean((0,1)).tolist(),"s_scale":s[split==0].std((0,1)).tolist(),
        "y_mean":np.nanmean(train_y,(0,1)).tolist(),"y_scale":np.nanstd(train_y,(0,1)).tolist(),
        "contract_bounds_um":{"lower":lower.tolist(),"upper":upper.tolist()}}
    save_json(data/"normalizer.json",norm)
    save_json(data/"splits.json",{"evidence":"SYNTHETIC_TEST_ONLY","rows":split.tolist()})
    port={"port_order":["P001","P002","P003","P004"],"reference_impedance_ohm":50.,
          "mode":"single_ended_shield_grounded","internal_permutation":[0,1,3,2]}
    contract=root/"synthetic_contract.json"
    save_json(contract,{"field_names":fields,"lower":lower.tolist(),"upper":upper.tolist(),"port_contract":port,
                        "evidence":"SYNTHETIC_ONLY_NOT_PRODUCTION_CONTRACT"})
    save_json(data/"data_manifest.json",{"schema":"bb_data_manifest.v1","status":"PASS","evidence":"SYNTHETIC_TEST_ONLY",
        "port_contract":port,"unique_geometries":count,"geometry_dim":10,"frequency_rows":count*56,
        "split_counts":{"train":16,"validation":4,"test":4},"contract_fingerprint_sha256":canonical_sha(port),
        "artifacts":{name:{"path":name,"sha256":sha256(data/name)} for name in ("dataset.npz","normalizer.json","splits.json")}})
    return data,contract,physical,geometry


def test_actual_seven_worker_bb00_forward_inverse_32_steps(tmp_path):
    evidence=os.environ.get("BB00_INTEGRATION_EVIDENCE_ROOT")
    root=Path(evidence).resolve() if evidence else tmp_path/"integration"
    root.mkdir(parents=True,exist_ok=False)
    data,contract,physical,geometry=fixture_10d(root)
    recipe=synthetic_recipe(root)
    spec_file=root/"SYNTHETIC_EXECUTION_SPEC.json"
    save_json(spec_file,{"scope":"SYNTHETIC_SOFTWARE_TEST_ONLY","geometry_count":24,"not_formal10k":True})
    request_file=root/"request.json"
    request=seven_suite.create_request(request_file,"SYNTHETIC_BB00_WORKER_INTEGRATION",contract,recipe,spec_file,
        forward_steps=32,inverse_steps=32,device="cpu",wall_budget_seconds=120)
    deadline=(datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat()
    repository=Path(__file__).resolve().parents[1]
    env=dict(os.environ,OMP_NUM_THREADS="2",PYTHONPATH=str(repository),PYTHONDONTWRITEBYTECODE="1")
    stages={}

    def execute(label,request_path,out,forward=None):
        command=[sys.executable,"-B","-m","research.broadband56_nn.seven_suite","_train-worker",
            "--request",str(request_path),"--label",label,"--data",str(data),"--out",str(out),
            "--parity",str(root/"UNUSED_NO_PHYSICS_EVALUATION.json"),"--deadline",deadline,"--steps","32"]
        if forward:
            command += ["--forward",forward]
        process=subprocess.run(command,cwd=repository,env=env,capture_output=True,text=True,timeout=90)
        log=root/(out.name+".log")
        with log.open("x",encoding="utf-8") as stream:
            stream.write(process.stdout+"\n"+process.stderr)
        save_json(root/(out.name+"_PROCESS.json"),{"argv":command,"returncode":process.returncode,
                    "stdout_stderr_log":str(log),"log_sha256":sha256(log),"scope":"SYNTHETIC_ONLY"})
        return process

    # The actual child must reject frozen-environment drift before making a run.
    wrong_environment=copy.deepcopy(request)
    wrong_environment["environment"]["python_version"]="SYNTHETIC_DRIFT"
    bad_env=root/"bad_environment_request.json"
    save_json(bad_env,wrong_environment)
    result=execute("BB00_FORWARD",bad_env,root/"rejected_environment")
    assert result.returncode!=0 and "research environment changed" in result.stderr
    assert not (root/"rejected_environment").exists()
    # Mutate only the synthetic request's expected SHA, never actual source code.
    wrong_source=copy.deepcopy(request)
    wrong_source["software"]["bb00.py"]["sha256"]="0"*64
    bad_source=root/"bad_source_request.json"
    save_json(bad_source,wrong_source)
    result=execute("BB00_FORWARD",bad_source,root/"rejected_source")
    assert result.returncode!=0 and ("artifact identity changed" in result.stderr or
           "executing research source paths or bytes changed after request freeze" in result.stderr)
    assert not (root/"rejected_source").exists()

    data_sha=sha256(data/"dataset.npz")
    for label in ("BB00_FORWARD","BB00"):
        forward=stages.get("BB00_FORWARD",{}).get("best_checkpoint") if label=="BB00" else None
        result=execute(label,request_file,root/label,forward)
        assert result.returncode==0, result.stderr
        forward_pin={"path":forward,"sha256":sha256(forward)} if forward else None
        stage_spec=seven_suite._stage_spec(label,request,forward_pin)
        receipt,complete=seven_suite._qualified_receipt(root/label/"TRAINING_RECEIPT.json",stage_spec,data_sha)
        assert complete and receipt["completed_step"]==32 and receipt["updates_this_run"]==32
        assert receipt["status"]=="SMOKE_TRAINED" and receipt["stop_reason"]=="UPDATE_BUDGET_COMPLETE"
        assert receipt["test_access"] is False and receipt["historical_weights_loaded"] is False
        state=load_checkpoint(receipt["last_checkpoint"])
        assert state["kind"]=="BB00" and state["geometry_dim"]==10
        assert state["initialization"]=="RANDOM_FROM_SCRATCH_NO_LEGACY_WEIGHTS"
        assert state["normalizer"]["io_status"]=="MATCHED_10D_FIELD_ORDER"
        assert state["normalizer"]["field_names"]==[x.removeprefix("geom__") for x in GEOMETRY_COLUMNS]
        assert state["eligible_rows"]=={
            "train":{"split_geometries":16,"eligible15ghz_geometries":15},
            "validation":{"split_geometries":4,"eligible15ghz_geometries":3},
            "test":{"split_geometries":4,"eligible15ghz_geometries":3}}
        np.testing.assert_allclose(state["normalizer"]["y_mean"],physical[1:16].mean(0),rtol=0,atol=1e-14)
        np.testing.assert_allclose(state["normalizer"]["g_mean"],geometry[1:16].mean(0),rtol=0,atol=1e-12)
        # Every synthetic K target exceeds old support0.8; all15 valid train rows
        # still contribute, proving the old support is not a hidden row filter.
        assert state["normalizer"]["y_mean"][3]>.8
        if label=="BB00":
            assert state["forward_checkpoint_sha256"]==forward_pin["sha256"]
            forward_state=load_checkpoint(forward)
            assert state["forward_model_sha"]==forward_state["model_sha"]
            assert state["normalizer_sha"]==forward_state["normalizer_sha"]
            bad_spec=copy.deepcopy(stage_spec)
            bad_spec["forward"]["sha256"]="1"*64
            with pytest.raises(ValueError,match="exact shared forward"):
                seven_suite._qualified_receipt(root/label/"TRAINING_RECEIPT.json",bad_spec,data_sha)
        stages[label]=receipt
    save_json(root/"INTEGRATION_RECEIPT.json",{"status":"PASS","scope":"SYNTHETIC_24_GEOMETRIES_10D_ONLY_NOT_FORMAL10K",
        "steps_per_role":32,"actual_child_workers":2,"rejected_pretraining_workers":2,
        "request_sha256":sha256(request_file),"dataset_sha256":data_sha,"test_module_sha256":sha256(__file__),
        "frozen_software":request["software"],"environment":request["environment"],
        "source_and_environment_drift_rejected_before_out_creation":True,
        "qualified_receipt_pass":True,"exact_forward_binding_verified":True,"native_10d_io_verified":True,
        "train_only15ghz_normalizer_verified":True,"original_support_not_used_as_filter":True,
        "stage_receipts":{label:{"path":str(root/label/"TRAINING_RECEIPT.json"),"sha256":sha256(root/label/"TRAINING_RECEIPT.json")} for label in stages},
        "no_historical_replay":True,"no_remote_or_production_action":True,"real_emx_validation":"NOT_RUN"})
