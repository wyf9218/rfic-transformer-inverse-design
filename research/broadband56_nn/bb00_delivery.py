"""Fresh-process BB00 acceptance; never rewrites original model state.

Run only after explicit authorization: this performs exactly ONE diagnostic
optimizer update for each NEW-data BB00 role. It never trains historical R0.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import torch

from .bb00 import BB00Config, load_bb00, train_bb00
from .delivery import validate_resume_deadline
from .io import read_json, save_json, sha256, load_checkpoint, utc_now
from .training import Bundle


def _record(value):
    return read_json(value) if isinstance(value, (str, Path)) else value


def verify_historical_package_load(package, expected_manifest_sha256, receipt_path, *, lock_fds=()):
    """Real standalone legacy load only, without repeating saved validation."""
    root=Path(package).resolve()
    if sha256(root/"PACKAGE_MANIFEST.json")!=expected_manifest_sha256:
        raise ValueError("historical manifest SHA mismatch")
    before={str(p.relative_to(root)):sha256(p) for p in root.rglob("*") if p.is_file()}
    command=[sys.executable,"-I","-B",str(root/"load_baseline.py"),"inspect","--package",str(root),
             "--expected-manifest-sha256",expected_manifest_sha256]
    process=subprocess.Popen(command,cwd=root,env=dict(os.environ,PYTHONPATH="",OMP_NUM_THREADS="2"),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,pass_fds=tuple(lock_fds))
    stdout,stderr=process.communicate()
    loaded=json.loads(stdout) if process.returncode==0 else {}
    after={str(p.relative_to(root)):sha256(p) for p in root.rglob("*") if p.is_file()}
    result={"schema":"bb00_historical_standalone_load.v1","status":"PASS" if process.returncode==0 and loaded.get("status")=="LOADED_NO_PREDICTION" and before==after else "FAIL",
            "child_pid":process.pid,"parent_pid":os.getpid(),"command":command,"returncode":process.returncode,
            "model_id":loaded.get("config",{}).get("model_id"),"manifest_sha256":expected_manifest_sha256,
            "package_original_bytes_unchanged":before==after,"input_file_sha256":before,
            "stdout":stdout,"stderr":stderr,"historical_evaluation_repeated":False,"new_training_performed":False,"created_utc":utc_now()}
    save_json(receipt_path,result)
    return result


def _inputs(bundle):
    frequencies=np.flatnonzero(bundle.arrays["frequency_hz"]==15e9)
    if len(frequencies)!=1:
        raise ValueError("exact15GHz frequency missing")
    f=int(frequencies[0])
    valid=bundle.arrays["y_valid"][bundle.train,f].all(-1)
    indices=bundle.train[valid][:8]
    if not len(indices):
        raise ValueError("no fixed train-valid input rows")
    return indices,f


def _worker(request_path):
    request=read_json(request_path)
    if request["action"] != "load":
        try:
            validate_resume_deadline(request.get("effective_deadline_utc"))
        except Exception as exc:
            save_json(request["out_receipt"], {"status":"FAIL", "pid":os.getpid(),
                "action":request["action"], "error":str(exc),
                "effective_deadline_utc":request.get("effective_deadline_utc"),
                "training_budget_sha256":request.get("training_budget_sha256"),
                "optimizer_called":False, "created_utc":utc_now()})
            raise
    torch.set_num_threads(2)
    if request["action"]=="load":
        bundle=Bundle(request["data_root"])
        indices,f=_inputs(bundle)
        outputs={"source_indices":indices}
        checks={}
        for key in ("best","last"):
            path=request[key+"_checkpoint"]
            model,state=load_bb00(path,device=request["device"],expected_sha256=request[key+"_sha256"])
            if state["data_sha"]!=bundle.data_sha or state["role"]!=request["role"]:
                raise ValueError("worker data/role mismatch")
            raw=bundle.arrays["geometry"][indices] if state["role"]=="forward" else bundle.arrays["y"][indices,f]
            with torch.no_grad():
                value=model(torch.as_tensor(raw,dtype=torch.float32,device=request["device"])).cpu().numpy()
            if not np.isfinite(value).all():
                raise ValueError("nonfinite fresh-process native output")
            outputs[key]=value
            checks[key]={"data_sha":state["data_sha"],"normalizer_sha":state["normalizer_sha"],"contract_sha":state["contract_sha"],"model_sha":state["model_sha"],"step":state["step"]}
        with Path(request["out_arrays"]).open("xb") as stream:
            np.savez(stream,**outputs)
        result={"status":"PASS","pid":os.getpid(),"checks":checks,"arrays_sha256":sha256(request["out_arrays"]),"action":"load"}
    else:
        initial=load_checkpoint(request["last_checkpoint"])
        config=BB00Config(**initial["train_config"])
        config.steps=1
        config.deadline_utc=request.get("effective_deadline_utc")
        config.forward_checkpoint=request.get("forward_checkpoint")
        # Device is part of the exact-resume contract: no silent change.
        if config.device!=request["device"]:
            raise ValueError("resume device differs from original training")
        validate_resume_deadline(config.deadline_utc)
        receipt=train_bb00(request["data_root"],request["resume_out"],config,request["contract_path"],
            request["legacy_replay_receipt"],request["expected_legacy_sha"],resume_checkpoint=request["last_checkpoint"],
            resume_best_checkpoint=request["best_checkpoint"],resume_best_checkpoint_sha256=request["best_sha256"],resume_probe=True)
        resumed_model,_=load_bb00(receipt["last_checkpoint"],device=request["device"])
        bundle=Bundle(request["data_root"])
        indices,f=_inputs(bundle)
        raw=bundle.arrays["geometry"][indices] if request["role"]=="forward" else bundle.arrays["y"][indices,f]
        with torch.no_grad():
            output=resumed_model(torch.as_tensor(raw,dtype=torch.float32,device=request["device"])).cpu().numpy()
        if not np.isfinite(output).all():
            raise ValueError("resumed model native output nonfinite")
        result={"status":"PASS","pid":os.getpid(),"action":"resume_one","receipt":receipt,"resumed_native_output_finite":True,
                "effective_deadline_utc":config.deadline_utc,
                "training_budget_sha256":request.get("training_budget_sha256")}
    save_json(request["out_receipt"],result)


def _launch(request_path,out_receipt,lock_fds=()):
    request=read_json(request_path)
    if request["action"] != "load":
        validate_resume_deadline(request.get("effective_deadline_utc"))
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[2]),OMP_NUM_THREADS="2",PYTHONDONTWRITEBYTECODE="1")
    command=[sys.executable,"-B","-m","research.broadband56_nn.bb00_delivery","worker","--request",str(request_path)]
    process=subprocess.run(command,env=env,capture_output=True,text=True,pass_fds=tuple(lock_fds))
    log_path=Path(str(out_receipt)+".process.json")
    save_json(log_path,{"command":command,"returncode":process.returncode,"stdout":process.stdout,"stderr":process.stderr})
    if process.returncode!=0:
        raise RuntimeError("fresh BB00 process failed; see "+str(log_path))
    result=read_json(out_receipt)
    if result.get("status")!="PASS" or result.get("pid")==os.getpid():
        raise ValueError("no fresh-process PASS receipt")
    return result


def verify_bb00_load_resume(data_root,forward_receipt,inverse_receipt,out,contract_path,
                            legacy_replay_receipt,expected_legacy_sha,device="cpu",*,lock_fds=(),
                            deadline_utc=None,training_budget_sha256=None):
    """Isolated proof for both new roles. Never promotes diagnostic descendants."""
    out=Path(out).resolve()
    out.mkdir(parents=True,exist_ok=False)
    original_pins={}
    all_results={}
    binding={"effective_deadline_utc":deadline_utc,"training_budget_sha256":training_budget_sha256,
             "deadline_semantics":"admission and update-start checks; no hard in-flight interruption"}
    try:
        validate_resume_deadline(deadline_utc)
        bundle=Bundle(data_root)
        indices,f=_inputs(bundle)
        receipts={"forward":_record(forward_receipt),"inverse":_record(inverse_receipt)}
        for role,receipt in receipts.items():
            validate_resume_deadline(deadline_utc)
            if receipt.get("role")!=role or receipt.get("kind")!="BB00" or receipt.get("test_access") is not False or receipt.get("updates_this_run",0)<1:
                raise ValueError("qualified original new-data BB00 receipt required")
            states={}
            for selection in ("best","last"):
                path=Path(receipt[selection+"_checkpoint"])
                if sha256(path)!=receipt[selection+"_sha256"]:
                    raise ValueError("original checkpoint receipt SHA mismatch")
                original_pins[str(path)]=sha256(path)
                original_pins[str(path)+".identity.json"]=sha256(str(path)+".identity.json")
                states[selection]=load_checkpoint(path)
            best,last=states["best"],states["last"]
            if any(s["schema"]!="bb00_training_state.v1" or s["role"]!=role or s["data_sha"]!=bundle.data_sha for s in states.values()):
                raise ValueError("original role/snapshot/schema mismatch")
            if best["model_sha"]!=last["best_model_sha"] or best["step"]>last["step"]:
                raise ValueError("best/last pair identity mismatch")
            role_out=out/role
            role_out.mkdir()
            baseline={}
            for selection in ("best","last"):
                model,state=load_bb00(receipt[selection+"_checkpoint"],device=device)
                raw=bundle.arrays["geometry"][indices] if role=="forward" else bundle.arrays["y"][indices,f]
                with torch.no_grad():
                    baseline[selection]=model(torch.as_tensor(raw,dtype=torch.float32,device=device)).cpu().numpy()
            request={"action":"load","data_root":str(Path(data_root).resolve()),"role":role,"device":device,
                **{key:receipt[key] for key in ("best_checkpoint","best_sha256","last_checkpoint","last_sha256")},
                "out_arrays":str(role_out/"fresh_load_outputs.npz"),"out_receipt":str(role_out/"FRESH_LOAD_RECEIPT.json")}
            save_json(role_out/"LOAD_REQUEST.json",request)
            fresh=_launch(role_out/"LOAD_REQUEST.json",request["out_receipt"],lock_fds)
            if sha256(request["out_arrays"])!=fresh["arrays_sha256"]:
                raise ValueError("fresh output artifact SHA mismatch")
            with np.load(request["out_arrays"],allow_pickle=False) as values:
                equal={key:bool(np.isfinite(values[key]).all() and np.allclose(values[key],baseline[key],atol=1e-6,rtol=1e-6)) for key in baseline}
                identity=bool(np.array_equal(values["source_indices"],indices))
            if not identity or not all(equal.values()):
                raise ValueError("fresh BB00 output mismatch")
            request.update(action="resume_one",contract_path=str(Path(contract_path).resolve()),
                legacy_replay_receipt=str(Path(legacy_replay_receipt).resolve()),expected_legacy_sha=expected_legacy_sha,
                forward_checkpoint=receipts["forward"]["best_checkpoint"] if role=="inverse" else None,
                resume_out=str(role_out/"one_update_only"),out_receipt=str(role_out/"FRESH_RESUME_RECEIPT.json"),
                effective_deadline_utc=deadline_utc,training_budget_sha256=training_budget_sha256)
            save_json(role_out/"RESUME_REQUEST.json",request)
            resumed=_launch(role_out/"RESUME_REQUEST.json",request["out_receipt"],lock_fds)
            resumed_receipt=resumed["receipt"]
            updated=load_checkpoint(resumed_receipt["last_checkpoint"])
            if sha256(resumed_receipt["last_checkpoint"])!=resumed_receipt["last_sha256"]:
                raise ValueError("resumed checkpoint SHA mismatch")
            generator=np.random.default_rng()
            generator.bit_generator.state=last["rng_state"]["sampler"]
            generator.choice(np.asarray(last["eligible_train_source_indices"]),32,replace=True)
            optimizer_steps=all(int(updated["optimizer_state"]["state"][key]["step"])==int(value["step"])+1 for key,value in last["optimizer_state"]["state"].items())
            checks={"fresh_best_output_matches":equal["best"],"fresh_last_output_matches":equal["last"],"fixed_train_inputs_match":identity,
                "exactly_one_optimizer_update":updated["step"]==last["step"]+1 and resumed_receipt["updates_this_run"]==1,
                "optimizer_counters_continue":optimizer_steps,
                "scheduler_continues":updated["scheduler_state"]["last_epoch"]==last["scheduler_state"]["last_epoch"]+1,
                "sampler_continues":generator.bit_generator.state==updated["rng_state"]["sampler"],
                "torch_rng_continues":torch.equal(updated["rng_state"]["torch"],last["rng_state"]["torch"]),
                "immutable_data_normalizer_contract":all(updated[k]==last[k] for k in ("data_sha","normalizer_sha","contract_sha","recipe_sha256","fixed_train_config_sha","runtime_source_sha256")),
                "frozen_forward_unchanged":updated["forward_model_sha"]==last["forward_model_sha"] and updated["forward_checkpoint_sha256"]==last["forward_checkpoint_sha256"],
                "model_updated":updated["model_sha"]!=last["model_sha"],
                "effective_deadline_bound":updated["train_config"].get("deadline_utc")==deadline_utc and
                    resumed.get("effective_deadline_utc")==deadline_utc and
                    resumed.get("training_budget_sha256")==training_budget_sha256,
                "resumed_native_output_finite":resumed.get("resumed_native_output_finite") is True,
                "diagnostic_not_ranking_checkpoint":updated["resume_probe"] and not updated["research_comparison_eligible"],
                "original_parent_bound":updated["parent_checkpoint"]["sha256"]==receipt["last_sha256"]}
            all_results[role]={"status":"PASS" if all(checks.values()) else "FAIL","checks":checks,
                "load_pid":fresh["pid"],"resume_pid":resumed["pid"],"original_best_sha256":receipt["best_sha256"],
                "original_last_sha256":receipt["last_sha256"],"resumed_checkpoint_sha256":resumed_receipt["last_sha256"],
                "last_step":last["step"],"resumed_step":updated["step"],"fixed_input_source_indices":indices.tolist(),**binding}
            if not all(checks.values()):
                raise ValueError("BB00 new-process resume check failed: "+str(checks))
        unchanged=all(sha256(path)==digest for path,digest in original_pins.items())
        if not unchanged:
            raise RuntimeError("original BB00 checkpoint bytes changed")
        result={"schema":"bb00_load_resume_proof.v1","status":"PASS","results":all_results,
                "data_sha":bundle.data_sha,"original_checkpoint_bytes_unchanged":unchanged,"original_pins":original_pins,
                "implementation_sha256":sha256(__file__),"exact_scope":"new-adapter state continuation, not legacy optimizer resume",
                "created_utc":utc_now(),"real_emx_validation":"NOT_RUN",**binding}
    except Exception as exc:
        result={"schema":"bb00_load_resume_proof.v1","status":"FAIL","error":str(exc),"original_pins":original_pins,
                "results":all_results,"remaining_probes":"NOT_RUN",**binding,
                "original_checkpoint_bytes_unchanged":all(sha256(path)==digest for path,digest in original_pins.items()),"created_utc":utc_now()}
        save_json(out/"BB00_LOAD_RESUME_RECEIPT.json",result)
        raise
    save_json(out/"BB00_LOAD_RESUME_RECEIPT.json",result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["worker"])
    parser.add_argument("--request",required=True)
    args=parser.parse_args()
    _worker(args.request)


if __name__=="__main__":
    main()
