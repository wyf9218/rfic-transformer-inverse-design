"""Frozen train-only 1NN response-library control. No model or native tool calls."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import sys
import time
import numpy as np
from .eucap15_author_sources import load_json, no_symlinks, put_json, put_csv
from .frequency_physical_statistics import error_metrics, percentage, percentile
from .eucap15_selected_metrics import FEATURES, UNITS, SCORE_SPANS

SPANS = np.asarray(SCORE_SPANS, dtype=np.float64)
Q_VALUES = tuple(range(10, 21))
TAU = .05 * SPANS

def nearest(values, geometry_ids, targets, block_size=32):
    """Exact float64 fixed-span RMS; ties select lexically lowest geometry identity."""
    x, t = np.asarray(values, dtype=np.float64), np.asarray(targets, dtype=np.float64)
    ids = list(geometry_ids)
    if x.ndim != 2 or x.shape[1] != 4 or not len(x) or t.ndim != 2 or t.shape[1] != 4:
        raise ValueError("nonempty library and four-feature targets required")
    if len(ids) != len(x) or len(set(ids)) != len(ids) or any(not isinstance(s, str) or not s for s in ids):
        raise ValueError("unique nonempty geometry IDs required")
    if not np.isfinite(x).all() or not np.isfinite(t).all():
        raise ValueError("finite inputs required")
    if type(block_size) is not int or not 1 <= block_size <= 32:
        raise ValueError("bounded block_size1..32 required")
    order = np.asarray(sorted(range(len(ids)), key=ids.__getitem__))
    x = x[order]
    selected, scores = [], []
    for begin in range(0, len(t), block_size):
        with np.errstate(over="raise", invalid="raise"):
            distances = np.sqrt(np.mean(((x[None, :, :] - t[begin:begin+block_size, None, :]) / SPANS)**2, axis=2))
        if not np.isfinite(distances).all():
            raise ValueError("nonfinite score")
        pos = np.argmin(distances, axis=1)
        selected.extend(order[pos].tolist())
        scores.extend(distances[np.arange(len(pos)), pos].tolist())
    return np.asarray(selected, dtype=int), np.asarray(scores, dtype=float)

def q_choices(scores):
    scores = np.asarray(scores, dtype=float)
    if scores.ndim != 2 or scores.shape[1] != 11 or not np.isfinite(scores).all():
        raise ValueError("complete eleven finite Q scores required")
    return np.argmin(scores, axis=1)  # columns ordered10..20; exact ties choose lower Q

def train_library(rows, splits, contract):
    by_hash = splits["by_geometry_sha256"]
    id_sets = {name: set(splits["ids"][name]) for name in ("train", "validation", "test")}
    if any(len(id_sets[n]) != len(splits["ids"][n]) for n in id_sets) or any(
        id_sets[a] & id_sets[b] for a, b in (("train","validation"),("train","test"),("validation","test"))):
        raise ValueError("overlapping/duplicate split identities")
    fields = ["geom__" + n for n in contract["field_names"]]
    library, metadata, counts = [], {}, Counter()
    for row in rows:
        identity, role = row["geometry_sha256"], row["assigned_development_split"]
        if identity in metadata or by_hash.get(identity) != role or identity not in id_sets.get(role, set()):
            raise ValueError("source/split identity mismatch")
        metadata[identity] = {"view_row": int(row["view_row"]), "split": role}
        counts[role] += 1
        if role != "train":
            continue  # test/validation physical values are never converted or used here
        values = [float(row[k]) for k in ("lp_nh","ls_nh","qmin","k_abs")]
        geometry = [float(row[k]) for k in fields]
        if row["frequency_hz"] != "15000000000" or row["strict_lumped_valid"].lower() != "true" or row["below_half_srf"].lower() != "true":
            raise ValueError("train row is not exact15 strict")
        if not np.isfinite(values+geometry).all() or values[2] != min(float(row["qp"]),float(row["qs"])):
            raise ValueError("invalid train labels or Q definition")
        if not (.5 <= values[0] <= 2 and .5 <= values[1] <= 2 and .2 <= values[3] <= .85):
            raise ValueError("train row outside frozen core")
        if np.any(np.asarray(geometry) < contract["lower"]) or np.any(np.asarray(geometry) > contract["upper"]):
            raise ValueError("original train geometry outside source bounds")
        library.append({"geometry_sha256":identity,"view_row":int(row["view_row"]),"values":values,"geometry_um":geometry,
            "label_source_path":row["label_source_path"],"label_source_sha256":row["label_source_sha256"],
            "label_source_row":row["label_source_row_1based_including_header"]})
    if set(metadata) != set(by_hash) or any(counts[n] != len(id_sets[n]) for n in id_sets):
        raise ValueError("source/split coverage mismatch")
    return library, metadata, counts

def verified_sources(protocol):
    data, pins = {}, {}
    for name, item in protocol["sources"].items():
        path = Path(item["path"])
        if not path.is_absolute():
            raise ValueError("absolute source required")
        no_symlinks(path)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != item["sha256"]:
            raise ValueError("source changed: " + name)
        data[name] = raw
        pins[name] = {**item, "bytes":len(raw)}
    return data, pins

def csv_rows(raw):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("bad CSV fields")
    for row in reader:
        if None in row:
            raise ValueError("CSV width mismatch")
        yield row

def evaluate(library, panel, query_ids, targets, q_labels):
    t = np.asarray(targets, dtype=float)
    start = time.perf_counter()
    ix, scores = nearest([r["values"] for r in library], [r["geometry_sha256"] for r in library], t)
    elapsed = time.perf_counter()-start
    records=[]
    for j, pos in enumerate(ix):
        chosen=library[int(pos)]
        residual=np.asarray(chosen["values"])-t[j]
        item={"panel":panel,"request_id":query_ids[j],"target":t[j].tolist(),
            "retrieval_q":q_labels[j],"retrieved_geometry_sha256":chosen["geometry_sha256"],
            "retrieved_view_row":chosen["view_row"],"original_geometry_um":chosen["geometry_um"],
            "stored_train_em":chosen["values"],"score":float(scores[j]),"legacy_joint_hit":bool(np.all(np.abs(residual)<=TAU)),
            "source_label_pin":{"path":chosen["label_source_path"],"sha256":chosen["label_source_sha256"],"row":chosen["label_source_row"]},
            "evidence":"STORED_TRAIN_EM_RETRIEVAL_NOT_FRESH_EMX","native_authorized":False}
        records.append(item)
    return records, elapsed

def stats(records, panel):
    n=len(records)
    metrics=[]
    for i,(feature,unit,span) in enumerate(zip(FEATURES,UNITS,SPANS)):
        errors=[r["stored_train_em"][i]-r["target"][i] for r in records]
        absolute=[abs(v) for v in errors]
        percentages=[percentage(r["stored_train_em"][i],r["target"][i],float(span))[0] for r in records]
        percentages=[v for v in percentages if v is not None]
        metrics.append({"panel":panel,"feature":feature,"unit":unit,"N_original":n,
            **error_metrics(errors),"abs_error_p99":percentile(absolute,.99),"abs_error_max":max(absolute),
            "normalized_mae":sum(absolute)/n/float(span),
            "target_relative_absolute_percent_mean":sum(percentages)/len(percentages) if percentages else None,
            "percentage_n":len(percentages),"ci_status":"NOT_ESTIMATED",
            "evidence":"STORED_TRAIN_LIBRARY_RESPONSE_NOT_NEURAL_PROXY_OR_FRESH_EMX"})
    return {"panel":panel,"N_original":n,"N_result":n,"unique_retrieved_geometries":len({r["retrieved_geometry_sha256"] for r in records}),
        "legacy_joint_hits":sum(r["legacy_joint_hit"] for r in records),
        "legacy_joint_hit_fraction":sum(r["legacy_joint_hit"] for r in records)/n,
        "score_mean":sum(r["score"] for r in records)/n,
        "q_counts":dict(Counter(str(r["retrieval_q"]) for r in records)) if panel != "VALIDATION_FIXED4" else None}, metrics

def build(protocol_path, out):
    no_symlinks(protocol_path); no_symlinks(out)
    pbytes=protocol_path.read_bytes(); p=load_json(pbytes)
    if p["schema"] != "eucap15_train_retrieval_protocol.v1" or p["status"] != "FROZEN_BEFORE_RETRIEVAL_RESULTS":
        raise ValueError("frozen protocol required")
    if p["score_spans"] != list(SCORE_SPANS) or p["q_values"] != list(Q_VALUES) or p["tolerances"] != TAU.tolist():
        raise ValueError("scientific contract mismatch")
    out.mkdir(parents=True,exist_ok=False)
    data,pins=verified_sources(p)
    split=load_json(data["splits"]); contract=load_json(data["geometry_contract"])
    manifest=load_json(data["data_manifest"]); receipt=load_json(data["data_receipt"])
    for alias,filename in (("source_rows","SOURCE_ROWS.csv"),("splits","splits.json"),("geometry_contract","contract.json")):
        if manifest["artifacts"][filename]["sha256"] != pins[alias]["sha256"]:
            raise ValueError("manifest linkage mismatch")
    if receipt["dataset"]["sha256"] != p["data_identity"] or manifest["artifacts"]["dataset.npz"]["sha256"] != p["data_identity"]:
        raise ValueError("data identity mismatch")
    if receipt["scope"] != "DEVELOPMENT_CURRENT_SNAPSHOT" or receipt["FINAL_status"] != "NOT_FINAL":
        raise ValueError("wrong dataset scope")
    library,metadata,counts=train_library(csv_rows(data["source_rows"]),split,contract)
    if dict(counts) != {"train":3801,"validation":1269,"test":1259} or len(contract["field_names"]) != 10:
        raise ValueError("wrong frozen6329 population")
    validation=list(csv_rows(data["validation_targets"]))
    if len(validation)!=1269 or len({r["target_id"] for r in validation})!=1269 or {r["target_id"] for r in validation}!=set(split["ids"]["validation"]):
        raise ValueError("wrong validation query frame")
    for row in validation:
        meta=metadata[row["target_id"]]
        if row["split"]!="validation" or meta["split"]!="validation" or int(row["source_index"])!=meta["view_row"]:
            raise ValueError("validation row mapping mismatch")
    targets=[[float(row["truth__"+f]) for f in FEATURES] for row in validation]
    fixed, vt=evaluate(library,"VALIDATION_FIXED4",[r["target_id"] for r in validation],targets,[None]*len(validation))
    requests=load_json(data["requests128"])
    req=requests["requests"]
    if requests["N_original_requests"]!=128 or len(req)!=128 or len({r["request_id"] for r in req})!=128:
        raise ValueError("wrong128 target frame")
    qtargets=[]; qids=[]; qs=[]
    for ordinal,r in enumerate(req):
        if r["request_order"]!=ordinal or r["frequency_ghz"]!=15 or r["final_test_membership"] is not False:
            raise ValueError("wrong development request identity")
        if not (.5<=r["lp_nh"]<=2 and .5<=r["ls_nh"]<=2 and .2<=r["k_abs"]<=.85):
            raise ValueError("target outside frozen core")
        for q in Q_VALUES:
            qtargets.append([r["lp_nh"],r["ls_nh"],q,r["k_abs"]]);qids.append(r["request_id"]);qs.append(q)
    allq, qt=evaluate(library,"DEVELOPMENT128_ALL_Q",qids,qtargets,qs)
    jbest=q_choices(np.asarray([r["score"] for r in allq]).reshape(128,11))
    chosen=[{**allq[i*11+int(j)],"panel":"DEVELOPMENT128_RETRIEVAL_QSCAN"} for i,j in enumerate(jbest)]
    q15=[{**allq[i*11+5],"panel":"DEVELOPMENT128_FIXED_Q15"} for i in range(128)]
    summaries=[];metrics=[]
    for records,panel in ((fixed,"VALIDATION_FIXED4"),(q15,"DEVELOPMENT128_FIXED_Q15"),(chosen,"DEVELOPMENT128_RETRIEVAL_QSCAN")):
        summary, ms=stats(records,panel);summaries.append(summary);metrics.extend(ms)
    put_json(out/"RETRIEVAL_LIBRARY.json",{"scope":"TRAIN3801_ONLY","geometry_fields":contract["field_names"],"rows":library})
    # Nested vectors remain JSON strings; no candidate manifest is produced.
    for name, records in (("VALIDATION_RESULTS.csv",fixed),("QSCAN_ALL1408.csv",allq),("QSCAN_SELECTED128.csv",chosen),("FIXED_Q15_RESULTS.csv",q15)):
        formatted=[{k:json.dumps(v,sort_keys=True) if isinstance(v,(list,dict)) else v for k,v in r.items()} for r in records]
        put_csv(out/name,formatted,list(formatted[0]))
    put_csv(out/"METRICS.csv",metrics,list(metrics[0]))
    summary={"schema":"eucap15_retrieval_summary.v1","status":"COMPLETE_DEVELOPMENT_RETRIEVAL","panels":summaries,
        "source_counts":dict(counts),"library_n":len(library),"score_spans":SPANS.tolist(),"legacy_tolerances":TAU.tolist(),
        "evidence":"PRIOR_TRAIN_EM_LABELS_NOT_FRESH","REAL_EMX_VALIDATION":"NOT_RUN",
        "cost":{"validation_lookup_seconds":vt,"all1408_lookup_seconds":qt,"query_block":32,"threads_requested":1},
        "model_loads":0,"training_updates":0,"native_calls":0,"new_random_targets":0,"original128_q_proxy_modified":False,
        "test_numeric_labels_used":False,"test_predictions":0,"source_csv_contains_test_bytes":True,
        "family_independence":"NOT_CERTIFIED","FINAL":False,
        "limitations":["Finite train-library response matching; not neural accuracy or independent native validation",
                       "Validation frame was already used for neural checkpoint selection",
                       "Retrieval Q optimum only within stored train responses; not q_proxy/q_emx or NN Q benefit",
                       "Source geometry copied verbatim, not newly quantized or manufacturing revalidated",
                       "These records are not native dispatch candidates and do not replace main128"] }
    put_json(out/"SUMMARY.json",summary)
    # Whole input bytes are checked again to detect concurrent alteration; no model/data-array loads.
    for name,item in pins.items():
        if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()!=item["sha256"]:
            raise ValueError("source changed during calculation")
    put_json(out/"RECEIPT.json",{"schema":"eucap15_retrieval_receipt.v1","status":"PASS",
        "protocol":{"path":str(protocol_path),"sha256":hashlib.sha256(pbytes).hexdigest()},
        "inputs":pins,"code":{"path":str(Path(__file__).resolve()),"sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "python":sys.version,"numpy":np.__version__,"platform":platform.platform(),"argv":sys.argv,
        "scope":"DEVELOPMENT_RETRIEVAL_ONLY","test_prediction":False})
    with (out/"SHA256SUMS").open("x") as f:
        for path in sorted(out.iterdir()):
            if path.is_file() and path.name!="SHA256SUMS":
                f.write(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    return summary

def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument("--protocol",type=Path,required=True);a.add_argument("--out",type=Path,required=True)
    args=a.parse_args(); result=build(args.protocol,args.out)
    print(json.dumps({"status":result["status"],"panels":result["panels"]}))
if __name__=="__main__": main()

