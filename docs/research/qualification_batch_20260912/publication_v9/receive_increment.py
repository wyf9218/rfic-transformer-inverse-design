"""Receive only the three already-completed interfaces and a new bounded source prefix.
No simulator, physical QA, extraction, model, sampler, or ledger writes are called.
"""
import argparse, csv, hashlib, json, math, os, stat
from datetime import datetime, timezone
from pathlib import Path

GEOMETRY = ["primary_outer_width_um","primary_outer_height_um","secondary_outer_width_um",
"secondary_outer_height_um","line_width_um","primary_terminal_y_span_um",
"secondary_terminal_y_span_um","offset_um","primary_feed_extension_um","secondary_feed_extension_um"]
PINS = []
def read(path, sha=None, size=None):
    path = Path(path)
    assert path.is_absolute() and not any(x.is_symlink() for x in (path,*path.parents))
    with path.open("rb") as f:
        before=os.fstat(f.fileno()); raw=f.read(); after=os.fstat(f.fileno())
    assert stat.S_ISREG(before.st_mode)
    ident=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    assert ident(before)==ident(after)==ident(path.stat())
    digest=hashlib.sha256(raw).hexdigest()
    assert sha is None or sha==digest,(path,"SHA")
    assert size is None or size==len(raw),(path,"LENGTH")
    PINS.append(dict(path=str(path),sha256=digest,bytes=len(raw)))
    return json.loads(raw)
def save(out,name,value):
    with (out/name).open("x") as f:
        json.dump(value,f,indent=2,ensure_ascii=False,allow_nan=False);f.write("\n")
def raw_identity(row):
    values=[float(row["geom__"+k]) for k in GEOMETRY]
    assert all(math.isfinite(x) for x in values)
    return hashlib.sha256("|".join(format(0.0 if x==0 else x,".17g") for x in values).encode()).hexdigest()
def run(handoff,out):
    transport=read(handoff/"TRANSPORT_RECEIPT.json","d86183f5258904f01aa9cb40a094dfabb9fdeaff7d2dd12ebb0f1d57ffed72f2")
    observation=read(handoff/"OBSERVATION.json","7b2c64541c1d6f71a0eff8b65ca3f72538ad884e1038ecdba6d23b84c913375a")
    local={Path(pin["path"]).name:(remote,pin) for remote,pin in transport["path_map"].items()}
    def member(name):
        remote,pin=local[name]
        return read(pin["path"],pin["sha256"],pin["bytes"]),remote,pin
    rows=[]
    for result_name,feature_name,formal_name in (
            ("00_RESULT.json","01_FEATURE_RECEIPT.json","02_002021.json"),
            ("03_RESULT.json","04_FEATURE_RECEIPT.json",None),
            ("05_RESULT.json","06_FEATURE_RECEIPT.json","07_004250.json")):
        result,remote,rpin=member(result_name)
        feature,fremote,fpin=member(feature_name)
        rid=result["request_id"]; proposal=result["original_proposal"]
        assert result["status"]=="FRESH_EMX_EXTRACTED"
        assert result["candidate_id"]==feature["candidate_id"]==proposal["candidate_id"]==rid
        assert result["feature"]==dict(path=fremote,sha256=fpin["sha256"],bytes=fpin["bytes"])
        assert result["actual_response"]==feature["actual_fresh_emx"]
        assert feature["frequency_ghz"]==15 and feature["original_frequency_row"]["frequency_hz"]==15e9
        assert feature["original_proposal"]==proposal
        assert proposal["canonical_geometry_sha256"]==result["candidate_geometry_identity_sha256"]
        assert proposal["final_independent_test_eligible"] is False
        assert feature["target_errors_defined"] is False
        for key in ("target","proxy_self","q_proxy","q_requested","q_emx","emx_minus_proxy","emx_minus_target","strict_joint_hit"):
            assert feature[key] is None,(rid,key)
        actual=result["actual_response"]
        for key in ("core15_eligible","valid_for_strict_comparison","q10_to20_supported"):
            assert result[key]==feature[key]
        original=feature["original_frequency_row"]
        assert actual==[original["lp_nh"],original["ls_nh"],original["qmin"],original["k_abs"]]
        assert original["qmin"]==min(original["qp"],original["qs"])
        formal=None
        if formal_name:
            recorded,fpath,pin=member(formal_name);record=recorded["record"]
            assert recorded["status"]=="PASS_CURRENT_CONTRACT_QUALIFIED_INCREMENT_COMMITTED"
            assert record["eucap15_qualified_accepted"] is True
            assert record["request_id"]==rid and record["geometry"]==proposal["geometry"]
            assert record["geometry_sha256"]==result["candidate_geometry_identity_sha256"]
            assert record["split"]==proposal["assigned_development_split"]
            assert [record["physical15"][k] for k in ("lp_nh","ls_nh","qmin","k_abs")]==actual
            assert record["original56"]["feature_receipt"]["original"]==result["feature"]
            formal=dict(sequence=record["increment_sequence"],split=record["split"],utc=recorded["utc"],
                        original_path=fpath,sha256=pin["sha256"])
        reason=[]
        if not feature["strict_lumped_valid"]:
            reason.append("BELOW_HALF_SRF_FALSE" if original["below_half_srf"]=="false" else "OTHER_STRICT_FAILURE")
        if not .5<=actual[0]<=2:reason.append("LP_OUT_OF_RANGE")
        if not .5<=actual[1]<=2:reason.append("LS_OUT_OF_RANGE")
        if not .2<=actual[3]<=.85:reason.append("K_OUT_OF_RANGE")
        split=proposal["assigned_development_split"]
        eligible_train=feature["core15_eligible"] and formal is not None and split=="train"
        rows.append(dict(request_id=rid,source=proposal["source"],seed=proposal["seed"],
            original_split=split,geometry_sha256=result["candidate_geometry_identity_sha256"],
            actual_response=actual,qp=original["qp"],qs=original["qs"],
            strict=feature["strict_lumped_valid"],core=feature["core15_eligible"],
            q10to20=feature["q10_to20_supported"],failure_reasons=reason,
            srf_status=original["srf_status"],formal= formal,eligible_train=eligible_train,
            target=None,proxy=None,q_proxy=None,target_error=None,
            result_pin=dict(original_path=remote,**rpin),feature_pin=dict(original_path=fremote,**fpin)))
    assert len(rows)==3 and sum(r["core"] for r in rows)==2
    assert sum(r["eligible_train"] for r in rows)==0
    # Validate the NEW source's prefix/SQLite joins; do not re-read prior P215100.
    joined=[]
    for row in observation["joined"]:
        src=row["source_row"]; hits=row["index_hits"]; errors=[]
        raw=raw_identity(src)
        if raw!=row["raw_geometry_sha256"]:errors.append("RAW_IDENTITY_MISMATCH")
        if len(hits)!=1:errors.append("NOT_EXACTLY_ONE_SQLITE_HIT")
        else:
            hit=hits[0]
            if hit["raw_geometry_sha256"]!=raw:errors.append("INDEX_RAW_IDENTITY_MISMATCH")
            if hit["evaluation"]!=src["evaluation"]:errors.append("EVALUATION_MISMATCH")
            if hit["touchstone_sha256"]!=src["touchstone_sha256"]:errors.append("S4P_SHA_MISMATCH")
        joined.append(dict(source_prefix_ordinal=row["source_prefix_ordinal"],evaluation=src["evaluation"],
            raw_geometry_sha256=raw,index_hits=hits,join_errors=errors,join_status="PASS" if not errors else "FAIL",
            declared_s4p_path=src["touchstone_path"],current_qualification="NOT_EVALUATED"))
    assert len(joined)==100 and {r["source_prefix_ordinal"] for r in joined}==set(range(100))
    raw_unique=len({r["raw_geometry_sha256"] for r in joined})
    save(out,"RECEIVED_LAST3.json",rows)
    save(out,"NEW_SOURCE100_JOIN.json",dict(source=observation["new_source"],rows=joined,
        current_prefix_sha256=observation["prefix_sha256"],historical_full_sha_reverified=False,
        current_formal_qualified=None,formal_added=0))
    summary=dict(schema="eucap15_last3_and_next_source_readonly_reception.v1",
        utc=datetime.now(timezone.utc).isoformat(),newly_received_existing_fresh_interfaces=3,
        existing_core_received=2,existing_formal_readback=2,eligible_train_increment=0,
        train_coverage_cells_added=0,train_coverage_reason="DOE008 is original test; TRAIN002 is original validation; DOE009 fails strict and K range.",
        new_solvers=0,new_emx_completions_in_this_reception=0,new_formal_added=0,
        new_model_or_sampler_updates=0,old_qa_or_extraction_runs=0,new_target_errors=0,
        scientific_scope="Original production256 geometry proposals have no physical target or q_proxy; no accuracy/joint-hit score defined. Not FINAL10K.",
        prior_six_fresh_reconsumed=0,new_source100=dict(rows=100,raw_unique=raw_unique,
            pass_joins=sum(not r["join_errors"] for r in joined),fail_joins=sum(bool(r["join_errors"]) for r in joined),
            source="training_csv:new_training_table",current_qualification="NOT_EVALUATED",
            actual_gds_missing="Next bounded actual command/GDS/topcell transport requested from sole owner; no classification from raw geometry floats.",
            full_source_hash_reverified=False,full_pool_or_21135_overlap="UNPROVEN"))
    save(out,"SUMMARY.json",summary);save(out,"INPUT_PINS.json",PINS)
    with (out/"RESULTS.csv").open("x",newline="") as f:
        fields=["request_id","source","seed","original_split","geometry_sha256","Lp_nH","Ls_nH","Qmin","K_abs","strict","core","formal_sequence","eligible_train","failure_reasons"]
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in rows:
            row={k:r[k] for k in fields if k in r}
            row.update(zip(("Lp_nH","Ls_nH","Qmin","K_abs"),r["actual_response"]))
            row["formal_sequence"]=r["formal"]["sequence"] if r["formal"] else ""
            row["failure_reasons"]=";".join(r["failure_reasons"]);writer.writerow(row)
    outputs=[]
    for name in ("RECEIVED_LAST3.json","NEW_SOURCE100_JOIN.json","SUMMARY.json","INPUT_PINS.json","RESULTS.csv"):
        path=out/name;raw=path.read_bytes()
        outputs.append(dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
    save(out,"RECEIPT.json",dict(status="PASS_EXACT_INCREMENT_RECEIVED",outputs=outputs,
        input_scope="10 small JSONs: transport/observation +3RESULT+3FEATURE+2formal; no raw S4P/GDS read",
        no_full_physical_qa_claim=True))
    print(json.dumps(summary,ensure_ascii=False))
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--handoff",type=Path,required=True);p.add_argument("--out",type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False);run(a.handoff,a.out)
