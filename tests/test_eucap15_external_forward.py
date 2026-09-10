"""Synthetic-only external-forward preparation and pre-inference file gates.

No real checkpoint, research CSV, model, NPZ, or native process is accessed.
"""
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_external_forward as m


FIELDS = ["primary_outer_width_um", "primary_outer_height_um", "secondary_outer_width_um",
    "secondary_outer_height_um", "line_width_um", "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um", "offset_um", "primary_feed_extension_um",
    "secondary_feed_extension_um"]


def canonical_geometry(g):
    ordered = [(f, f"{v:.9f}") for f, v in zip(FIELDS, g)]
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def geometry(i):
    return [200.0+i, 210.0+i, 220.0+i, 230.0+i, 5.0, 40.0, 41.0, 0.0, 150.0, 160.0]


def geom_columns(g):
    return {"geom__"+f:str(v) for f,v in zip(FIELDS,g)}


@pytest.fixture
def frame():
    pins = {a:{"path":"/synthetic/"+a,"sha256":hashlib.sha256(a.encode()).hexdigest(),"bytes":1}
            for a in m.SOURCES}
    spec = {"schema":"eucap15_external_forward_spec.v1","status":"FROZEN_BEFORE_PREDICTIONS",
        "model":deepcopy(m.MODEL),"score_spans":list(m.SPANS),"sources":pins,
        "N_original":4,"N_strict":2,"expected_data_sha":"d"*64,
        "source_counts":{"snapshot":3,"train":1,"validation_metadata":1,"test_metadata":1}}
    counts = {"train":1,"validation":1,"test":1}
    contract = {"field_names":FIELDS[:],"lower":[160,160,160,160,3,20,20,-90,100,100],
        "upper":[520,520,520,520,12,90,90,90,320,320],"units":"um",
        "port_contract":{"ports":4,"port_order":["P001","P002","P003","P004"],"reference_impedance_ohm":50}}
    norm = {"field_names":FIELDS[:],"physical_features":list(m.FEATURES),"response_spans":list(m.SPANS),
        "frequency_ghz":15,"label_mode":"STRICT_LUMPED","g_mean":[0]*10,"g_scale":[1]*10,
        "y_mean":[0]*4,"y_scale":[1]*4}
    dm = {"schema":"bb_data_manifest.v1","status":"PASS","artifacts":{"dataset.npz":{"sha256":"d"*64}},
        "geometry_fields":FIELDS[:],"geometry_units":"um","frequency_hz":[15000000000],
        "target_columns":["lp_nh","ls_nh","qmin","k_abs"],"split_counts":counts.copy()}
    for a,n in (("source_rows","SOURCE_ROWS.csv"),("splits","splits.json"),("exclusions","EXCLUSIONS.csv"),("contract","contract.json")):
        dm["artifacts"][n] = {**pins[a],"path":n}
    dr = {"schema":"eucap15_formal_development_view_receipt.v1","status":"PASS_PREPARED_BUNDLE_BB00",
        "scope":"DEVELOPMENT_CURRENT_SNAPSHOT","FINAL_status":"NOT_FINAL","dataset":{"sha256":"d"*64},
        "data_manifest":deepcopy(pins["data_manifest"]),"split_counts":counts.copy(),
        "excluded_source_counts":{"ORIGINAL64_VALIDATION":2}}
    splits = {"counts":counts.copy(),"by_geometry_sha256":{},"geometry_id_to_sha256":{},"ids":{k:[] for k in counts}}
    sources=[]
    for i,role in enumerate(counts):
        g=geometry(i); h=canonical_geometry(g); gid="synthetic-g"+str(i)
        r={"geometry_sha256":h,"geometry_id":gid,"assigned_development_split":role,"view_row":str(i)}
        if role=="train": r.update(geom_columns(g))
        else: r.update({"geom__"+f:"DO_NOT_CONVERT_HELDOUT_GEOMETRY" for f in FIELDS})
        r.update({f:"DO_NOT_CONVERT_ANY_SOURCE_LABEL" for f in ("lp_nh","ls_nh","qmin","k_abs")})
        sources.append(r); splits["by_geometry_sha256"][h]=role
        splits["geometry_id_to_sha256"][gid]=h; splits["ids"][role].append(gid)
    selected={"schema":"eucap15_selected_candidate_handoff.v1","N_requests":4,"N_selected_analytic_pass":3,
        "frequency_ghz":15,"label_mode":"STRICT_LUMPED","dataset_scope":"FORMAL_10K",
        "research_phase":"DEVELOPMENT_PILOT_NOT_FINAL10K","model_id":"synthetic-historical-reference",
        "selected_candidate_csv":deepcopy(pins["selected_geometry"]),"requests":[]}
    physical=[]; geometries=[]; exclusions=[]
    states=["STRICT_VALID","STRICT_VALID","EMX_INVALID","ANALYTIC_FAIL"]
    for i,state in enumerate(states):
        q=13+i; rid=f"synthetic-request-{i:03d}"; cid=f"{rid}-q{q:02d}"
        g=geometry(10+i); h=canonical_geometry(g); cidsha=hashlib.sha256(cid.encode()).hexdigest()
        target=[1.0,1.1,float(q),.3]; actual=[1.01,1.11,float(q)+.2,.31]
        selected["requests"].append({"request_id":rid,"request_order":i,"candidate_id":cid,"q_proxy":q,
            "candidate_id_sha256":cidsha,"candidate_geometry_identity_sha256":h,
            "selected_target":target,"selected_grid_proxy":target[:],"selected_analytic_pass":state!="ANALYTIC_FAIL"})
        physical.append({"request_id":rid,"candidate_id":cid,"q_proxy":str(q),"q_emx":"",
            "candidate_geometry_identity_sha256":h,"target":json.dumps(target),"grid_proxy":json.dumps(target),
            "actual":json.dumps(None if state=="ANALYTIC_FAIL" else actual),"strict_joint_hit":"null" if state!="STRICT_VALID" else "true",
            "state":state,"touchstone_sha":"e"*64 if state!="ANALYTIC_FAIL" else ""})
        if state!="ANALYTIC_FAIL":
            geometries.append({"candidate_id":cid,"candidate_id_sha256":cidsha,
                "candidate_geometry_identity_sha256":h,"geometry_sha256":h,**geom_columns(g)})
        if state=="STRICT_VALID":
            exclusions.append({"source_group":"ORIGINAL64_VALIDATION","candidate_id":cid,
                "geometry_sha256":h,"frequency_hz":"15000000000","strict_lumped_valid":"true","below_half_srf":"true",
                "exclusion_reason":"ORIGINAL64_VALIDATION_NOT_TRAINING_DATA","lp_nh":str(actual[0]),"ls_nh":str(actual[1]),
                "qmin":str(actual[2]),"qp":str(actual[2]),"qs":str(actual[2]+1),"k_abs":str(actual[3]),"signed_k":str(-actual[3]),
                "label_source_path":"/synthetic/features.csv","label_source_sha256":"f"*64,
                "label_source_row_1based_including_header":"original_frequency_row",**geom_columns(g)})
    ps={"N_original_requests":4,"N_strict_valid":2,"completion_status":"COMPLETE_ACCOUNTING","N_pending_requests":0,
        "dataset_scope":"FORMAL_10K","research_phase":"DEVELOPMENT_PILOT_NOT_FINAL10K","model_id":selected["model_id"],
        "selected_manifest":deepcopy(pins["selected_manifest"]),"feature_order":list(m.FEATURES),"score_spans":list(m.SPANS),
        "q_emx":None,"complete11_status":"NOT_EVALUATED","state_counts":{"STRICT_VALID":2,"EMX_INVALID":1,"ANALYTIC_FAIL":1}}
    tr={"schema":"bb00_training_receipt.v1","role":"forward","kind":"BB00","status":"PARTIAL",
        "validation_selected_checkpoint":True,"best_checkpoint":pins["checkpoint"]["path"],"best_sha256":pins["checkpoint"]["sha256"],
        "data_sha":"d"*64,"normalizer_sha":m.canonical_sha(norm),"architecture":{"widths":deepcopy(m.MODEL["widths"])},
        "frequency_ghz":15,"label_mode":"STRICT_LUMPED","test_access":False,
        "eligible_rows":{k:{"eligible_geometries":v} for k,v in counts.items()}}
    d=dict(data_receipt=dr,data_manifest=dm,splits=splits,contract=contract,normalizer=norm,training_receipt=tr,
        source_rows=sources,physical_summary=ps,selected_manifest=selected,physical_rows=physical,
        selected_geometry=geometries,exclusions=exclusions)
    return spec,d


def test_minimal_frame_preserves_original_accounting_and_never_converts_heldout(frame):
    spec,d=frame; before=deepcopy(frame)
    result=m.prepare_inputs(spec,d)
    assert len(result["accounting"])==4 and len(result["strict_rows"])==2
    assert result["audit"]["test_numeric_values_converted"]==result["audit"]["validation_numeric_values_converted"]==0
    assert result["geometries"]==[geometry(10),geometry(11)]
    assert result["accounting"][2]["prior_actual"]==d["physical_rows"][2]["actual"]
    assert result["accounting"][3]["prior_actual"]=="null"
    assert all(r["forward_prediction"] is None for r in result["accounting"])
    assert result["strict_rows"][0]["label_source_row_reference"]=="original_frequency_row"
    assert result["audit"]["parent_family_independence"]=="UNKNOWN_NOT_CERTIFIED"
    assert frame==before


@pytest.mark.parametrize("case",["denominator","source_sha","split_overlap","model","score_spans","target_q","changed_proxy",
    "missing_strict","bad_actual","changed_geometry","nonfinite_geometry","missing_s4p","normalizer","training_test_access","synchronized_identity_drift"])
def test_pure_input_guards_reject_drift(frame,case):
    spec,d=frame
    if case=="denominator": spec["N_original"]=3
    elif case=="source_sha": d["data_manifest"]["artifacts"]["SOURCE_ROWS.csv"]["sha256"]="0"*64
    elif case=="split_overlap": d["splits"]["ids"]["test"]=d["splits"]["ids"]["train"][:]
    elif case=="model": spec["model"]["seed"]=29
    elif case=="score_spans": spec["score_spans"][3]=1
    elif case=="target_q": d["selected_manifest"]["requests"][0]["q_proxy"]=20
    elif case=="changed_proxy": d["physical_rows"][0]["grid_proxy"]="[1,1,1,1]"
    elif case=="missing_strict": d["exclusions"].pop()
    elif case=="bad_actual": d["physical_rows"][0]["actual"]="[1,2,3,4]"
    elif case=="changed_geometry": d["selected_geometry"][0]["geom__"+FIELDS[0]]="333"
    elif case=="nonfinite_geometry": d["selected_geometry"][0]["geom__"+FIELDS[0]]="nan"
    elif case=="missing_s4p": d["physical_rows"][0]["touchstone_sha"]=""
    elif case=="normalizer": d["normalizer"]["y_mean"][0]=1
    elif case=="training_test_access": d["training_receipt"]["test_access"]=True
    elif case=="synchronized_identity_drift":
        d["selected_manifest"]["requests"][0]["candidate_geometry_identity_sha256"]="0"*64
        d["physical_rows"][0]["candidate_geometry_identity_sha256"]="0"*64
        d["selected_geometry"][0]["candidate_geometry_identity_sha256"]="0"*64
        d["selected_geometry"][0]["geometry_sha256"]="0"*64
    with pytest.raises(ValueError): m.prepare_inputs(spec,d)


def test_geometry_duplicate_in_training_rejected_without_reading_labels(frame):
    spec,d=frame
    d["source_rows"][0].update(geom_columns(geometry(10)))
    with pytest.raises(ValueError,match="round12 geometry overlaps training"):
        m.prepare_inputs(spec,d)


def test_file_pin_hash_size_and_symlink_gate(tmp_path):
    p=tmp_path/"source.bin"; p.write_bytes(b"synthetic-only")
    correct=m.file_pin(p)
    assert correct["sha256"]==hashlib.sha256(b"synthetic-only").hexdigest() and correct["bytes"]==14
    with pytest.raises(ValueError,match="size"):
        m.same_pin(correct,{**correct,"bytes":15})
    with pytest.raises(ValueError,match="SHA"):
        m.same_pin(correct,{**correct,"sha256":"f"*64})
    link=tmp_path/"alias"; link.symlink_to(p)
    with pytest.raises((ValueError,AssertionError)): m.file_pin(link)
    with pytest.raises(ValueError): m.file_pin(Path("relative-input"))


def test_read_sources_detects_byte_change_without_checkpoint_loading(tmp_path):
    p=tmp_path/"source.json"; p.write_text("{}")
    expected=m.file_pin(p); p.write_text('{"changed":true}')
    with pytest.raises(ValueError,match="SHA"):
        m.read_sources({"sources":{"contract":expected}})
    p.write_bytes(b"not a checkpoint; opaque bytes only")
    documents,pins=m.read_sources({"sources":{"checkpoint":m.file_pin(p)}})
    assert documents=={} and pins["checkpoint"]["bytes"]==len(p.read_bytes())


def test_wrong_spec_fails_before_sources_and_preserves_failure(tmp_path,monkeypatch):
    p=tmp_path/"unapproved.json"; p.write_text("{}")
    def forbidden(*args,**kwargs): raise AssertionError("must not read source/model")
    monkeypatch.setattr(m,"read_sources",forbidden)
    out=tmp_path/"out"
    with pytest.raises(ValueError,match="exact pre-prediction spec"):
        m.run(p,out)
    receipt=json.loads((out/"FAILURE_RECEIPT.json").read_text())
    assert receipt["status"]=="FAIL_PRESERVED_NOT_RETRIED" and receipt["model_loads"]==receipt["native_calls"]==0
    assert not (out/"SUMMARY.json").exists()


def test_existing_output_is_not_clobbered(tmp_path):
    out=tmp_path/"existing"; out.mkdir(); sentinel=out/"keep"; sentinel.write_text("immutable")
    with pytest.raises(FileExistsError): m.run(tmp_path/"does-not-exist.json",out)
    assert sentinel.read_text()=="immutable" and list(out.iterdir())==[sentinel]
