"""Consume only a new frozen original256 closed delta; never run native jobs.

Reuse the original acquisition chain parser. Prior45 contribute identities only,
not re-read physics. Each new chain has its own reader and source recheck, then
the union is checked once. This is descriptive, not a matched-cost experiment.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

from .eucap15_acquisition_evidence import (
    MirrorReader, load_frozen_batch, inspect_chain, MANIFEST_SHA, _require,
    _fields, _strict_json, pin, SCALE, TAU)
from .eucap15_acquisition_delta_failure import normalized_entry, failure_row
from .eucap15_author_sources import no_symlinks, put_json, put_csv

OLD_PREFIX_SHA = "885040c4459510f59c029be8a9f41e1c4f0948dded6f2d13fc1522a2973a0b9b"
OLD_FIXED_SHA = "ce9f04e10bbc00908a19d6e28a07556d89ec081fa56efbd22b7005a2d43bea7d"
OLD_IDS_SHA = "ee1c2a9423a28359ac2bd7c3cc2a56d3150a3d50c055b5ca72cd25bc59f26500"
RULE = "ALL_NEW_CLOSED_EXECUTED_TERMINALS_IN_ORIGINAL_GLOBAL_ORDER_NO_OUTCOME_FILTER"
SOURCES = ("SPARSE_TARGETED", "GEOMETRY_DOE", "EXPLORATION")


def checked(p):
    _require(isinstance(p, dict) and set(("path", "sha256", "bytes")) <= set(p), "exact pin required")
    path = Path(p["path"]); no_symlinks(path)
    _require(path.is_absolute() and ".." not in path.parts, "exact absolute path required")
    _require(pin(path) == p, "input SHA/size changed: " + str(path))
    raw = path.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == p["sha256"], "input changed during read")
    return _strict_json(raw)


def unique_pairs(rows):
    pairs = [(r["request_id"], r["candidate_id"]) for r in rows]
    _require(all(isinstance(x, str) and x for pair in pairs for x in pair), "exact IDs required")
    _require(len(set(x[0] for x in pairs)) == len(set(x[1] for x in pairs)) == len(pairs), "duplicate ID")
    return pairs


def freeze_delta(items, old_prefix, old_fixed, frozen_requests, exclusions, batch):
    old = unique_pairs(old_prefix["rows"] + old_fixed["items"])
    _require(len(old_prefix["rows"]) == 32 and len(old_fixed["items"]) == 13 and len(old) == 45, "prior45 changed")
    digest = hashlib.sha256(("".join(x+"\n" for x in sorted(r for r,c in old))).encode()).hexdigest()
    _require(digest == OLD_IDS_SHA, "prior45 request identity changed")
    pairs = unique_pairs(items)
    # Exact external whitelist keys are supplied by the caller after inspecting
    # the frozen owner's request; no target/Q suffix inference is used.
    _require(pairs == frozen_requests, "closed items differ from frozen whitelist")
    _require(set(old) == set(exclusions), "owner exclusion set differs from prior45")
    _require(not ({r for r,c in old} & {r for r,c in pairs}) and
             not ({c for r,c in old} & {c for r,c in pairs}), "old physics repeated")
    order=[]
    for request,cid in pairs:
        _require(cid in batch["rows"], "foreign proposal")
        p=batch["rows"][cid]
        _require(p["request_id"] == request, "candidate/request mismatch")
        order.append(p["global_order"])
    _require(order == sorted(order) and len(set(order)) == len(order), "not original global order")
    return old


def bind_owner_snapshot(docs, batch):
    """Prove that the frozen whitelist contains every new closed snapshot row."""
    old_rows=docs["old_prefix"]["rows"]+docs["old_fixed"]["items"]
    old_pairs=unique_pairs(old_rows)
    expected_old={r["request_id"]:r.get("result", r.get("original_result")) for r in old_rows}
    excluded=docs["exclusions"]
    _fields(excluded,dict(original_denominator=256,excluded_count=45),"exclusions")
    _require(set(excluded["excluded"]) == set(expected_old), "excluded request mismatch")
    for request,p in expected_old.items():
        _require(excluded["excluded"][request]["result"] == p, "old result pin changed")
    context=docs["export"]["context"]
    request=docs["request"]["payload"]
    _require(request["context"] == context, "frozen owner context differs")
    items=docs["closed"]["items"]; frozen=request["items"]
    _require(len(frozen) == len(items), "owner whitelist length differs")
    for expected,item in zip(frozen,items):
        _fields(item,expected,"frozen whitelist entry")
    rows=docs["observation"]["groups"]["original256"]["rows"]
    _require(len(rows) == 256 and len({r["job"]["request_id"] for r in rows}) == 256, "incomplete original frame")
    _require([r["job"]["global_order"] for r in rows] == list(range(1,257)), "snapshot not original order")
    selected=[]; counts=Counter()
    for row in rows:
        job=row["job"]; proposal=batch["rows"].get(job["candidate_id"])
        _require(proposal is not None, "foreign observed candidate")
        _fields(job,{k:proposal[k] for k in job},"snapshot job")
        result=row["result"]
        if job["request_id"] in expected_old:
            _require(result["status"] == "STABLE_JSON" and result["pin"] == expected_old[job["request_id"]],
                     "previously delivered result changed in snapshot")
        if result["status"] == "STABLE_JSON":
            document=result["document"]
            _fields(document,{k:job[k] for k in ("request_id","candidate_id","q_proxy")},"snapshot result")
            status=document["status"]
            _require(status in ("FRESH_EMX_EXTRACTED", "CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION",
                                "FROZEN_PROPOSAL_HELD_NO_REPLACEMENT"), "unknown stable terminal")
            if status == "FROZEN_PROPOSAL_HELD_NO_REPLACEMENT":
                _require(proposal["local_dispatch_eligible"] is False and job["request_id"] not in expected_old,
                         "held state conflicts with frozen eligibility or previous result")
            counts[status]+=1
            if status in ("FRESH_EMX_EXTRACTED","CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION") and job["request_id"] not in expected_old:
                selected.append(dict(request_id=job["request_id"],candidate_id=job["candidate_id"],
                    q_proxy=job["q_proxy"],original_result=result["pin"],original_status=status))
        else:
            _require(result["status"] == "ABSENT_AT_READ", "unknown snapshot state cannot become pending")
            counts["ABSENT_AT_READ"]+=1
    _require(selected == frozen, "not all and only new closed snapshot rows")
    _require(dict(counts) == docs["observation"]["groups"]["original256"]["counts"], "snapshot count mismatch")
    _require(counts["FROZEN_PROPOSAL_HELD_NO_REPLACEMENT"] == context["retained_original_holds"] and
             counts["ABSENT_AT_READ"] == context["pending_at_snapshot"], "snapshot denominator mismatch")
    return freeze_delta(items,docs["old_prefix"],docs["old_fixed"],unique_pairs(frozen),old_pairs,batch)


def bind_source_index_entry(entry, paths, export_root):
    """Reject absolute/parent relative paths before joining to the export root."""
    relative=entry["relative_path"]; local=entry["local_path"]
    _require(isinstance(relative,str) and isinstance(local,str), "index paths must be strings")
    rp,lp=Path(relative),Path(local)
    _require(not rp.is_absolute() and ".." not in rp.parts and str(rp) == relative,
             "noncanonical or absolute relative path")
    _require(lp.is_absolute() and ".." not in lp.parts and str(lp) == local and lp.is_relative_to(export_root),
             "local mirror escapes export root")
    source=entry["source"]; key=source["path"]
    _require(export_root/rp == lp and paths.get(key) == local, "source index/map conflict")
    return key,source


def merge_paths(paths, external, old_map, supplements):
    result=dict(paths); expected={}
    for entry in external:
        _require(entry["status"] == "EXTERNAL_CONTRACT_OR_METADATA_PIN_REFERENCED_NOT_REREAD", "external status")
        p=entry["source"]; key=p["path"]
        _require(key not in expected and key not in result and key in old_map, "external path conflict")
        result[key]=old_map[key]; expected[key]=p
    for item in supplements:
        p,resolved=item["original"],item["resolved"]; key=p["path"]
        _require(p["sha256"] == resolved["sha256"] and p["bytes"] == resolved["bytes"], "supplement differs")
        _require(key not in expected and (key not in result or result[key] == resolved["path"]), "supplement conflict")
        result[key]=resolved["path"]; expected[key]=p
    return result,expected


def combine_evidence(union, entries):
    for key, entry in entries.items():
        _require(key not in union or union[key] == entry, "conflicting evidence pin")
        union[key]=entry


def summarize_rows(rows, original_context):
    states=Counter(r["state"] for r in rows)
    groups=[]
    for source in SOURCES:
        selected=[r for r in rows if r["source"] == source]
        groups.append(dict(source=source,original_delta_requests=len(selected),
            states=dict(Counter(r["state"] for r in selected)),
            strict_core_eligible=sum(r["core_eligible"] is True for r in selected)))
    targeted=[r for r in rows if r["source"] == "SPARSE_TARGETED"]
    strict=[r for r in targeted if r["strict_valid"] is True]
    metrics=[]
    for j,name in enumerate(("Lp_nH","Ls_nH","Qmin","K_abs")):
        errors=[r["emx_minus_target"][j] for r in strict]
        n=len(errors)
        metrics.append(dict(feature=name,evidence="FRESH_EMX_STRICT_TARGETED_SURVIVORS",
            original_targeted_delta_denominator=len(targeted),strict_numeric_denominator=n,
            mae=math.fsum(abs(e) for e in errors)/n if n else None,
            rmse=math.sqrt(math.fsum(e*e for e in errors)/n) if n else None,
            target_relative_mape_percent=math.fsum(100*abs(r["emx_minus_target"][j])/r["target"][j] for r in strict)/n if n else None))
    return dict(schema="eucap15_acquisition_closed_delta_statistics.v1",
        status="COMPLETE_NEW_DELTA_PENDING_INDEPENDENT_NUMERIC_QA",
        original_proposals=256,original_proposals_per_arm=128,
        previous_research_accounted=45,new_closed_delta=len(rows),
        previous_plus_new_closed=45+len(rows),
        original_holds_at_owner_snapshot=original_context["retained_original_holds"],
        pending_at_owner_snapshot=original_context["pending_at_snapshot"],
        snapshot_utc=original_context["snapshot_utc"],state_counts=dict(states),groups=groups,
        targeted_original_delta_denominator=len(targeted),targeted_strict_valid=len(strict),
        targeted_strict_joint_hits=sum(r["strict_joint_hit"] is True for r in targeted),
        targeted_strict_joint_hit_rate=sum(r["strict_joint_hit"] is True for r in targeted)/len(targeted) if targeted else None,
        physical_feature_metrics=metrics,score_spans=SCALE,absolute_tolerances=TAU,
        coverage_gain=None,equal_budget_comparison=None,train_admission="NOT_PERFORMED",
        q_emx=None,complete11_status="NOT_EVALUATED_NO_Q_REPLACEMENT",
        scope="UNMATCHED_CLOSED_DELTA_NOT_IID_MODEL_ACCURACY_NOT_FINAL",
        new_native_actions=0,model_loads=0,optimizer_updates=0,old_physical_rows_reprocessed=0,
        caveats=["Owner snapshot counts are not current liveness.",
                 "Unmatched completion subset cannot establish same-budget superiority.",
                 "DOE/exploration have no response target; do not invent residuals.",
                 "Numeric errors are conditional on strict targeted survivors; original failures remain in the joint-hit denominator."])


def run(config_pin, output):
    config=checked(config_pin)
    output=Path(output); no_symlinks(output)
    _require(output.is_absolute() and ".." not in output.parts, "absolute output required")
    output.mkdir(parents=True,exist_ok=False)
    completed=[]
    try:
        docs={name:checked(p) for name,p in config["inputs"].items()}
        runtime=[pin(Path(__file__).absolute())]
        for p in config["runtime"]:
            _require(pin(p["path"]) == p, "runtime changed")
            runtime.append(p)
        _require(config["inputs"]["old_prefix"]["sha256"] == OLD_PREFIX_SHA and
                 config["inputs"]["old_fixed"]["sha256"] == OLD_FIXED_SHA, "prior publications changed")
        export=docs["export"]; context=export["context"]
        _fields(export,dict(schema="eucap15_fixed_terminal_export.v1",status="FIXED_LIST_ACCOUNTED",
            native_actions=0,original_candidates_modified_by_export=False,training=False,production_modified=False),"export")
        _fields(context,dict(selection_rule=RULE,original_denominator=256,excluded_already_delivered=45,
            retained_original_holds=53,no_replacement=True,no_new_accepted_or_training_members=True),"context")
        items=docs["closed"]["items"]; n=len(items)
        _require(n > 0 and docs["closed"]["count"] == export["item_count"] == context["new_terminal_count"] == n, "delta count")
        _require(45+n+53+context["pending_at_snapshot"] == 256, "original denominator closure")
        _require(dict(Counter(i["evidence_status"] for i in items)) == export["counts"], "export counts")
        paths=docs["map"]; index={}
        export_root=Path(config["inputs"]["export"]["path"]).parent
        for entry in docs["index"]:
            key,p=bind_source_index_entry(entry,paths,export_root)
            _require(key not in index, "duplicate source index")
            index[key]=p
        _require(len(index) == export["source_count"] and set(index) == set(paths), "source index closure")
        _require(len(docs["external"]) == export["external_reference_count"], "external closure")
        merged,external=merge_paths(paths,docs["external"],docs["old_map"],docs["supplement"])
        initial=MirrorReader(merged)
        for p in external.values(): initial.read(p)
        manifests=[p for p in external.values() if p["sha256"] == MANIFEST_SHA]
        _require(len(manifests) == 1, "frozen manifest absent")
        batch=load_frozen_batch(initial,manifests[0])
        bind_owner_snapshot(docs,batch)
        _require(context["observation"] == config["inputs"]["observation"] and
                 context["exclusions"] == config["inputs"]["exclusions"], "owner context differs")
        put_json(output/"INTENT.json",dict(config=config_pin,implementation=runtime,started_utc=datetime.now(timezone.utc).isoformat()))
        union=dict(initial.evidence); rows=[]
        for item in items:
            proposal=batch["rows"][item["candidate_id"]]
            reader=MirrorReader(merged)
            entry=normalized_entry(item,proposal)
            if entry is None:
                row=failure_row(reader,item,proposal,batch,index)
                reader.recheck()
            else:
                row=inspect_chain(reader,entry,batch)
                row.update(state="STRICT_VALID" if row["strict_valid"] else "EMX_INVALID",
                    original_result=item["original_result"],physical_numbers_available=True,
                    target=proposal["target"],frozen_proxy=proposal["proxy"],failure_stage=None,failed_checks=[],error=None)
            row.update(q_emx=None,complete11_status="NOT_EVALUATED_NO_Q_REPLACEMENT",
                fixed_delta_denominator=n,original_proposal_denominator=256,original_arm_denominator=128,
                training_admission="NOT_PERFORMED")
            combine_evidence(union,reader.evidence)
            rows.append(row); completed.append(item["candidate_id"])
            print(json.dumps(dict(new_closed=len(rows),of=n,state=row["state"])),flush=True)
        final_reader=MirrorReader(merged)
        for entry in union.values(): final_reader.read(entry["original"])
        for p in [config_pin,*config["inputs"].values(),*runtime]:
            _require(pin(p["path"]) == p, "input changed at final check")
        summary=summarize_rows(rows,context)
        summary["source_bindings_consumed"]=len(union)
        put_json(output/"REQUEST_RESULTS.json",rows)
        keys=list(dict.fromkeys(k for r in rows for k in r))
        put_csv(output/"REQUEST_RESULTS.csv",[{k:json.dumps(r.get(k),allow_nan=False) if isinstance(r.get(k),(dict,list)) else r.get(k) for k in keys} for r in rows],keys)
        put_json(output/"SUMMARY.json",summary)
        put_json(output/"SOURCE_CLOSURE.json",list(union.values()))
        artifacts=[pin(p) for p in sorted(output.iterdir()) if p.is_file()]
        put_json(output/"RECEIPT.json",dict(status="COMPLETE_READONLY_ACCEPTANCE_PENDING_INDEPENDENT_NUMERIC_QA",
            config=config_pin,implementation=runtime,artifacts=artifacts,command=sys.argv,
            ended_utc=datetime.now(timezone.utc).isoformat(),new_native_actions=0,new_training=0))
        with (output/"SHA256SUMS").open("x") as stream:
            for p in sorted(output.iterdir()):
                if p.is_file() and p.name != "SHA256SUMS": stream.write(pin(p)["sha256"]+"  "+p.name+"\n")
        return summary
    except BaseException as error:
        put_json(output/"FAILURE_RECEIPT.json",dict(status="FAIL_PRESERVED_NO_RETRY",error=str(error),
            traceback=traceback.format_exc(),completed_candidate_ids=completed,new_native_actions=0,new_training=0))
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs",required=True); p.add_argument("--inputs-sha256",required=True); p.add_argument("--out",required=True)
    a=p.parse_args(); cp=pin(a.inputs)
    _require(cp["sha256"] == a.inputs_sha256, "frozen config changed")
    print(json.dumps(run(cp,a.out)),flush=True)


if __name__ == "__main__":
    main()
