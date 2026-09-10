"""Read-only cost/invalid-reason supplement to an already accepted acquisition delta.

Consumes saved result rows and exact exported metadata, never CSV/S4P labels,
models, geometry generation or native tools. Recorded solver wall time, reported
CPU time and wrapper intervals are different quantities. Missing costs stay null.
This does not repeat the consumer's native authenticity or numerical-error QA.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys


SOURCES = ("SPARSE_TARGETED", "GEOMETRY_DOE", "EXPLORATION")
STAGES = ("cadence", "gds_audit", "calibre", "emx")
BOOLEAN_FLAGS = (
    "finite_values", "positive_primary_resistance", "positive_secondary_resistance",
    "positive_primary_inductive_reactance", "positive_secondary_inductive_reactance",
    "below_half_srf", "broadband_descriptor_valid", "strict_lumped_valid",
)
COSTS = (
    "cadence_elapsed_seconds", "cadence_active_worker_elapsed_seconds_sum",
    "gds_audit_elapsed_seconds", "calibre_elapsed_seconds",
    "emx_native_wallclock_seconds", "emx_reported_cpu_seconds",
    "solver_wrapper_interval_seconds", "all_stages_end_to_end_seconds",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def no_symlinks(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "absolute canonical path required")
    require(all(not p.is_symlink() for p in (path, *path.parents)), "symlink prohibited")


def pin(path):
    path = Path(path); no_symlinks(path)
    raw = path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def strict_json(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, "duplicate JSON key")
            result[k] = v
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


class MetadataReader:
    def __init__(self):
        self.sources = {}

    def raw(self, original, resolved=None):
        resolved = resolved or original
        require(set(original) == set(resolved) == {"path", "sha256", "bytes"}, "exact pin required")
        require((original["sha256"], original["bytes"]) ==
                (resolved["sha256"], resolved["bytes"]), "mirror pin differs")
        path = Path(resolved["path"]); no_symlinks(path)
        require(path.suffix in (".json", ".jsonl", ".log", ".py"), "non-metadata payload prohibited")
        raw = path.read_bytes()
        require(len(raw) == original["bytes"] and hashlib.sha256(raw).hexdigest() == original["sha256"],
                "source SHA/size changed: " + str(path))
        entry = dict(original=original, resolved=resolved)
        require(original["path"] not in self.sources or self.sources[original["path"]] == entry,
                "conflicting source resolution")
        self.sources[original["path"]] = entry
        return raw

    def document(self, original, resolved=None):
        return strict_json(self.raw(original, resolved))

    def recheck(self):
        for entry in self.sources.values():
            require(pin(entry["resolved"]["path"]) == entry["resolved"], "read-period source mutation")


def flag(value):
    if type(value) is bool:
        return value
    require(value in ("true", "false"), "unknown original boolean flag")
    return value == "true"


def recorded_seconds(value):
    if value is None:
        return None
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            "invalid recorded duration")
    return value


def native_footer(text):
    result = {}
    for label, key in (("Wall-clock time", "emx_native_wallclock_seconds"),
                       ("CPU time", "emx_reported_cpu_seconds")):
        matches = re.findall(r"^" + re.escape(label) + r" ([0-9]+(?:\.[0-9]+)?) sec\s*$", text, re.M)
        require(len(matches) <= 1, "ambiguous repeated native footer")
        result[key] = recorded_seconds(float(matches[0])) if matches else None
    return result


def wrapper_interval(started, ended):
    if started is None or ended is None:
        return None
    a, b = datetime.fromisoformat(started), datetime.fromisoformat(ended)
    require(a.tzinfo is not None and b.tzinfo is not None and b >= a, "invalid wrapper timestamp interval")
    return (b-a).total_seconds()


def invalid_reasons(row, feature):
    """Classify recorded predicates; do not reconstruct response or SRF values."""
    require(row["state"] == "EMX_INVALID", "only invalid subset requires detail")
    require(feature["candidate_id"] == row["candidate_id"] and feature["frequency_ghz"] == 15,
            "foreign feature metadata")
    original = feature["original_frequency_row"]
    require(original["frequency_hz"] == 15000000000, "wrong original frequency")
    flags = {k: flag(original[k]) for k in BOOLEAN_FLAGS}
    require(flags["below_half_srf"] == row["below_half_srf"] and
            flags["broadband_descriptor_valid"] == row["descriptor_valid"] and
            feature["physics_qa_pass"] == row["physics_qa_pass"] and
            feature["valid_for_strict_comparison"] == row["strict_valid"] is False,
            "saved accepted predicate drift")
    # A failed derived strict flag is not a distinct physical root cause.
    reasons = [k.upper() + "_FALSE" for k in BOOLEAN_FLAGS[:-1] if flags[k] is False]
    for name in ("passivity_status", "reciprocity_status"):
        require(isinstance(original[name], str), "missing original QA status")
        if original[name] != "PASS":
            reasons.append(name.upper() + "_NOT_PASS")
    if feature["physics_qa_pass"] is False:
        reasons.append("PHYSICS_QA_PASS_FALSE")
    if not reasons:
        reasons = ["STRICT_INVALID_REASON_NOT_RESOLVED_BY_RECORDED_PREDICATES"]
    return dict(reason_codes=sorted(reasons), original_predicates=flags,
                passivity_status=original["passivity_status"],
                reciprocity_status=original["reciprocity_status"],
                extraction_continuity_status=original.get("extraction_continuity_status"),
                srf_status=original.get("srf_status"),
                primary_srf=feature["original_56_summary"].get("primary_srf"),
                secondary_srf=feature["original_56_summary"].get("secondary_srf"),
                feature_pin=row["feature"], independent_reextraction=False)


def aggregate(rows):
    result = []
    for source in (*SOURCES, "ALL_NEW120"):
        group = rows if source == "ALL_NEW120" else [r for r in rows if r["source"] == source]
        costs = {}
        for name in COSTS:
            values = [r[name] for r in group if r[name] is not None]
            costs[name] = dict(recorded_n=len(values), missing_n=len(group)-len(values),
                               observed_sum=math.fsum(values) if values else None,
                               observed_mean=math.fsum(values)/len(values) if values else None,
                               full_original_group_sum=math.fsum(values) if len(values) == len(group) and values else None)
        invalid = [r for r in group if r["state"] == "EMX_INVALID"]
        result.append(dict(source=source, original_delta_n=len(group),
            states=dict(Counter(r["state"] for r in group)), costs=costs,
            invalid_n=len(invalid),
            overlapping_invalid_reason_counts=dict(Counter(k for r in invalid for k in r["invalid_detail"]["reason_codes"])),
            exclusive_invalid_reason_combinations=dict(Counter(" + ".join(r["invalid_detail"]["reason_codes"]) for r in invalid)),
            gds_rejection_check_counts=dict(Counter(k for r in group for k in r["failed_checks"]))))
    return result


def put_json(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False); f.write("\n")


def run(receipt_pin, numeric_qa_pin, output):
    reader = MetadataReader()
    receipt = reader.document(receipt_pin)
    qa = reader.document(numeric_qa_pin)
    require(qa["status"] == "GO_SCOPED_SAVED_VALUES_AND_IDENTITIES" and
            qa["inputs"]["run_receipt"] == receipt_pin, "exact upstream numerical GO required")
    artifacts = {Path(p["path"]).name: p for p in receipt["artifacts"]}
    config = reader.document(receipt["config"])
    rows = reader.document(artifacts["REQUEST_RESULTS.json"])
    summary = reader.document(artifacts["SUMMARY.json"])
    closure = reader.document(artifacts["SOURCE_CLOSURE.json"])
    require(len(rows) == 120 and Counter(r["state"] for r in rows) ==
            {"STRICT_VALID":43, "EMX_INVALID":61, "GDS_FAIL":16}, "not frozen accepted new120")
    require(Counter(r["source"] for r in rows) == dict(zip(SOURCES, (42,66,12))), "source denominator drift")
    require(len({r["candidate_id"] for r in rows}) == len({r["request_id"] for r in rows}) == 120,
            "duplicate original identity")
    mapped = {}
    for entry in closure:
        key = entry["original"]["path"]
        require(key not in mapped, "duplicate closure key")
        mapped[key] = entry
    closed = reader.document(config["inputs"]["closed"])
    index = reader.document(config["inputs"]["index"])
    export = reader.document(config["inputs"]["export"])
    root = Path(config["inputs"]["export"]["path"]).parent
    for entry in index:
        p = entry["source"]; local = Path(entry["local_path"]); rel = Path(entry["relative_path"])
        require(not rel.is_absolute() and ".." not in rel.parts and root/rel == local and local.is_relative_to(root),
                "export metadata escapes frozen root")
        resolution = dict(original=p, resolved=dict(p, path=str(local)))
        require(p["path"] not in mapped or mapped[p["path"]] == resolution, "closure/index conflict")
        mapped[p["path"]] = resolution

    def native(p):
        require(p["path"] in mapped and mapped[p["path"]]["original"] == p, "missing exact mirror metadata")
        return reader.document(p, mapped[p["path"]]["resolved"])

    def named(suffix):
        values = [e["original"] for e in closure if e["original"]["path"].endswith("/"+suffix)]
        require(len(values) == 1, "ambiguous frozen proposal source")
        return values[0]

    proposal_pin = named("SELECTED_CANDIDATES.jsonl")
    raw = reader.raw(proposal_pin, mapped[proposal_pin["path"]]["resolved"])
    proposals = {}
    for line_no, line in enumerate(raw.splitlines(), 1):
        p = strict_json(line); cid = p["candidate_id"]
        require(cid not in proposals, "duplicate original proposal")
        proposals[cid] = (p, line_no, hashlib.sha256(line).hexdigest())
    require(len(proposals) == 256, "original 256 proposal denominator")
    recipe_pin, model_pin = named("RECIPE_FREEZE.json"), named("MODEL_LOAD_RECEIPT.json")
    recipe, model = native(recipe_pin), native(model_pin)
    require(recipe["source_dataset_sha256"] == model["source_data_sha256"] and
            all(recipe["inputs"][k] == model[k] for k in ("forward","inverse","normalizer")), "recipe/model metadata mismatch")
    items = {r["candidate_id"]: r for r in closed["items"]}
    require(len(items) == 120 and set(items) == {r["candidate_id"] for r in rows}, "closed cohort differs")
    output = Path(output); no_symlinks(output); output.mkdir(parents=True, exist_ok=False)
    completed = []
    try:
        put_json(output/"INTENT.json", dict(schema="eucap15_acquisition_cost_reasons_intent.v1",
            receipt=receipt_pin, numeric_qa=numeric_qa_pin, implementation=pin(Path(__file__).absolute()),
            command=sys.argv, scope="SAVED_METADATA_ONLY_NO_PHYSICAL_RECOMPUTATION"))
        results = []
        for row in rows:
            cid = row["candidate_id"]; p, line_no, line_sha = proposals[cid]; item = items[cid]
            require(all(p[k] == row[k] for k in ("request_id","source","arm","global_order","arm_order","q_proxy")) and
                    p["canonical_geometry_sha256"] == row["geometry_sha256"] and p["target"] == row["target"] and
                    p["proxy"] == row["frozen_proxy"] and p["recipe_sha256"] == recipe_pin["sha256"] and
                    item["original_result"] == row["original_result"], "accepted proposal identity drift")
            base = str(Path(row["original_result"]["path"]).parent)
            result = {k:row[k] for k in ("request_id","candidate_id","source","arm","global_order","arm_order",
                "state","strict_valid","core_eligible","q_proxy","geometry_sha256","failed_checks")}
            result.update({k:None for k in COSTS})
            result.update(geometry=p["geometry"], geometry_fields=p["geometry_fields"],
                parameter_geometry_hash=p["parameter_geometry_hash"],
                original_proposal=dict(pin=proposal_pin,line_number_one_based=line_no,line_sha256=line_sha),
                recipe_sha256=p["recipe_sha256"], source_dataset_sha256=recipe["source_dataset_sha256"],
                model_pair_receipt=recipe["inputs"]["pair_receipt"],
                forward_checkpoint_pin=recipe["inputs"]["forward"], inverse_checkpoint_pin=recipe["inputs"]["inverse"],
                model_used_for_candidate=p["source"] == "SPARSE_TARGETED",
                model_scope=model["role"],
                target_cell_recorded=p["sparse_cell"], predicted_cell_recorded=None,
                actual_cell_recorded=p["actual_landing"], actual_cell_status="NOT_RECORDED_NO_BIN_COMPUTATION",
                original_result=row["original_result"], original_feature=row["feature"],
                physical_flags={k:row[k] for k in ("descriptor_valid","physics_qa_pass","below_half_srf","q10_to20_supported")},
                invalid_detail=None, stages={}, stage_cost_status="INCOMPLETE_NO_ALL_STAGE_TOTAL",
                source_pins=[])
            used_before=set(reader.sources)
            for stage in STAGES:
                key=base+"/"+stage+"_PROCESS.json"
                if key not in mapped:
                    result["stages"][stage]=dict(status="NOT_IN_FROZEN_EXPORT_UNKNOWN_COST",elapsed_seconds=None)
                    continue
                process_pin=mapped[key]["original"]; process=native(process_pin)
                require(process["intent"]["output"].startswith(base+"/"), "foreign stage output")
                result["stages"][stage]=dict(status="RECORDED_CLOSED_PROCESS",process_pin=process_pin,
                    ended_utc=process.get("utc"), returncode=process["returncode"],
                    release_pin=process["intent"]["release"],elapsed_seconds=None,
                    started_utc=None,missing_reason="No explicit start/elapsed; no log-name timestamp inference")
                if stage == "cadence":
                    cp=process["completion"]
                    require(cp["path"]==base+"/cadence_only/parallel_candidate_queue_dataset_summary.json", "foreign cadence cost")
                    cost=native(cp)
                    require(cost["input_row_count"] == 1 and cost["cadence_streamout_only"] is True, "non-single cadence cost")
                    result["cadence_elapsed_seconds"]=recorded_seconds(cost.get("elapsed_seconds"))
                    result["cadence_active_worker_elapsed_seconds_sum"]=recorded_seconds(cost.get("active_worker_elapsed_seconds_sum"))
                    result["stages"][stage].update(elapsed_seconds=result["cadence_elapsed_seconds"],cost_pin=cp,
                        missing_reason=None, definition="Original single-candidate Cadence wrapper reported elapsed")
            if row["state"] != "GDS_FAIL":
                solver=native(item["solver"])
                require(solver["candidate_id"] == cid and solver["status"] == "PASS", "foreign cost solver")
                logs=[p for p in solver["artifacts"] if p["path"] == base+"/emx_selected/solve/emx/emx_stderr.log"]
                starts=[p for p in solver["artifacts"] if p["path"] == base+"/emx_selected/solve/RUNNING.json"]
                require(len(logs) == len(starts) == 1, "ambiguous solver timing metadata")
                logpin=logs[0]; require(mapped[logpin["path"]]["original"]==logpin, "solver log binding")
                result.update(native_footer(reader.raw(logpin,mapped[logpin["path"]]["resolved"]).decode("utf-8")))
                start=native(starts[0]); result["solver_wrapper_interval_seconds"]=wrapper_interval(start.get("started_utc"),solver.get("ended_utc"))
                result["solver_timing"]=dict(solver_pin=item["solver"],log_pin=logpin,running_pin=starts[0],
                    wrapper_started_utc=start.get("started_utc"),solver_receipt_ended_utc=solver.get("ended_utc"),
                    wrapper_pid=start.get("pid"),native_birth_utc=None,
                    scope="RUNNING is wrapper metadata, not exact native solver process birth")
            else:
                result["solver_timing"]=dict(status="GDS_REJECTED_NO_SOLVER_COST_IN_EXPORTED_CHAIN",cost=None)
            if row["state"] == "EMX_INVALID":
                result["invalid_detail"]=invalid_reasons(row,native(row["feature"]))
            result["source_pins"]=[reader.sources[k] for k in sorted(set(reader.sources)-used_before)]
            results.append(result); completed.append(cid)
        groups=aggregate(results)
        reader.recheck()
        summary_out=dict(schema="eucap15_acquisition_delta_cost_reasons.v1",status="COMPLETE_METADATA_SUPPLEMENT_PENDING_INDEPENDENT_REVIEW",
            original_proposals=256,original_proposals_per_arm=128,new_delta_count=120,
            state_counts=summary["state_counts"],groups=groups,
            complete_end_to_end_cost=None,cpu_cost_all_stages=None,campaign_parallel_wall_time=None,
            recipe_pin=recipe_pin,model_load_pin=model_pin,model_selection=recipe["model_selection"],
            source_dataset_sha256=recipe["source_dataset_sha256"],
            recorded_bin_fields=dict(target="original proposal.sparse_cell",predicted="NOT_RECORDED",actual="original proposal.actual_landing remains null"),
            missing_cost_policy="Unknown/not exported/not started are never imputed as numeric zero; known-only sums have explicit recorded/missing denominators.",
            cost_definition="Sum of recorded EMX Wall-clock time is cumulative per-solver elapsed, not CPU cost or campaign parallel wall time. CPU time footer reported separately. Wrapper intervals overlap solver elapsed and must not be added to it.",
            reason_definition="Recorded failure predicates; overlapping reasons and mutually exclusive full reason combinations both reported; no assumption that all invalid rows are SRF failures.",
            scope="UNMATCHED_NEW120_SAVED_METADATA_NO_NEW_PHYSICS_OR_TRAINING_ADMISSION",
            raw_csv_reads=0,s4p_reads=0,model_loads=0,native_actions=0,residual_recalculations=0,
            warnings=["Geometry/model/data pins identify the frozen old3018 acquisition recipe, not current6329 model accuracy or FINAL.",
                      "No new targets, proxy/actual bin landing, coverage gain, residue or actual solver-attempt recount computed.",
                      "GDS/Calibre explicit elapsed is absent from the inspected process schema; full-stage/end-to-end cost remains unknown."])
        put_json(output/"REQUEST_COST_REASONS.json",results)
        with (output/"REQUEST_COST_REASONS.csv").open("x",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(results[0]));w.writeheader()
            for row in results:
                w.writerow({k:json.dumps(v,ensure_ascii=False,separators=(",",":"),allow_nan=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
        put_json(output/"SUMMARY.json",summary_out)
        put_json(output/"READ_SOURCE_PINS.json",list(reader.sources.values()))
        artifacts=[pin(p) for p in sorted(output.iterdir()) if p.is_file()]
        put_json(output/"RECEIPT.json",dict(status="COMPLETE_METADATA_SUPPLEMENT_NOT_NEW_NATIVE_QA",implementation=pin(Path(__file__).absolute()),
            upstream=receipt_pin,numeric_qa=numeric_qa_pin,artifacts=artifacts,completed=len(completed),
            read_metadata_files=len(reader.sources),source_sha_recheck="PASS",ended_utc=datetime.now(timezone.utc).isoformat()))
        with (output/"SHA256SUMS").open("x",encoding="utf-8") as f:
            for p in sorted(output.iterdir()):
                if p.is_file() and p.name != "SHA256SUMS": f.write(pin(p)["sha256"]+"  "+p.name+"\n")
        return summary_out
    except BaseException as exc:
        put_json(output/"FAILURE.json",dict(status="FAIL_PRESERVED_NO_RETRY",error=type(exc).__name__+": "+str(exc),
            completed_candidate_ids=completed,read_sources=list(reader.sources.values())))
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt",required=True);parser.add_argument("--receipt-sha256",required=True)
    parser.add_argument("--numeric-qa",required=True);parser.add_argument("--numeric-qa-sha256",required=True)
    parser.add_argument("--out",required=True)
    args=parser.parse_args(); rp=pin(args.receipt); qp=pin(args.numeric_qa)
    require(rp["sha256"]==args.receipt_sha256 and qp["sha256"]==args.numeric_qa_sha256,"caller pin mismatch")
    result=run(rp,qp,args.out)
    print(json.dumps(dict(status=result["status"],new_delta_count=result["new_delta_count"],
                         groups=[dict(source=g["source"],n=g["original_delta_n"],invalid_n=g["invalid_n"]) for g in result["groups"]])))


if __name__ == "__main__":
    main()
