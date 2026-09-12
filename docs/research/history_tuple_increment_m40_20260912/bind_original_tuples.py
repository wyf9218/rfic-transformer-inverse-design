"""Bind two already checked historical members to original source tuples once.
No EMX, new labels, GDS checks, train assignment or formal ledger writes.
"""
import ast, csv, hashlib, importlib.util, io, json, math, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
R=Path("/Users/wyf/Documents/模拟变压器AI反向建模")
W=Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1")
sys.path.insert(0,"/Users/wyf/Documents/模拟变压器AI反向建模/github_worktrees/eucap15-mlp-capacity-20260909")
from research.broadband56_nn.eucap15_received_landing_increment import emit_output
HERE=Path(__file__).resolve().parent
def pin(p):
    b=p.read_bytes()
    return dict(path=str(p),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))
def checked(p,sha):
    b=p.read_bytes()
    if hashlib.sha256(b).hexdigest()!=sha: raise ValueError("Input identity conflict: "+str(p))
    return b
source=R/"reports/tandem_112_real_emx_closure_20260719/real_emx/dataset_rows.csv"
raw=checked(source,"1bb5ce9ff06cfa3feb89c21270cfb08a93ca9f18e953741b4d6530504a77d256")
reader=csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
source_rows=list(reader)
if len(source_rows)!=112 or len(reader.fieldnames)!=192: raise ValueError("Source shape conflict")
index=W/"history_strict112_actual_index_m31_v1/ROW_BINDINGS.json"
bindings=json.loads(checked(index,"84a41da1563b1641832a4f6c5e9e6a9548b54b4c9254360b5d60be97fd1dc833"))["rows"]
helper=R/"reports/eucap15_native_owner_20260909T062500Z/qualified15_holdout_partition_v3/geometry_helpers.py"
checked(helper,"b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98")
spec=importlib.util.spec_from_file_location("geometry_helpers",helper)
gh=importlib.util.module_from_spec(spec);spec.loader.exec_module(gh)
historical=R/"rfic-transformer-inverse-design/scripts/plan_tandem_portfolio_fresh_emx_closure.py"
tree=ast.parse(historical.read_text())
funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_vector_digest"]
if len(funcs)!=1: raise ValueError("Ambiguous historical digest")
namespace={"np":np,"hashlib":hashlib}
exec(compile(ast.Module(body=funcs,type_ignores=[]),str(historical),"exec"),namespace)
if sys.byteorder!="little": raise ValueError("Original native float byte order not verified here")
outputs=[]
for ordinal in (1,2):
    b=next(x for x in bindings if x["authority_ordinal_zero_based"]==ordinal)
    meta=b["original_authority_metadata"]
    matches=[(i,x) for i,x in enumerate(source_rows) if x["touchstone_path"]==meta["touchstone_path"]]
    if len(matches)!=1: raise ValueError("Nonunique exact S4P source row")
    source_ordinal,row=matches[0]
    previous=W/f"history_strict112_next5_artifacts_m35_v1/actual_verification_v1/authority_{ordinal:03d}/RESULT.json"
    component=W/f"history_strict112_three_contract_m36_v1/actual_geometry_closure_v1/authority_{ordinal:03d}.json"
    prior=json.loads(previous.read_text());comp=json.loads(component.read_text())
    if comp["prior_checks_reused"]!=pin(previous): raise ValueError("Prior component binding changed")
    if prior["candidate_id_sha256"]!=b["candidate_id_sha256"] or comp["candidate_id_sha256"]!=b["candidate_id_sha256"]: raise ValueError("Candidate identity mismatch")
    g={k:float(row["geom__"+k]) for k in gh.GEOMETRY_FIELDS}
    if not all(math.isfinite(v) for v in g.values()): raise ValueError("Nonfinite tuple")
    checks={"source_tuple_equals_bound_audit":g==prior["geometry_parameter_check"]["geometry_um"],
            "source_row_original_success":row["ok"].lower()=="true",
            "original_geometry_id_preserved":prior["candidate_geometry_identity_sha256"]==b["candidate_geometry_identity_sha256"],
            "previous_actual_components_pass":all(comp["checks"].values()),
            "equal_shared_winding_width":float(row["geom__primary_width_um"])==float(row["geom__secondary_width_um"])==g["line_width_um"]}
    vec=np.asarray([g[k] for k in gh.GEOMETRY_FIELDS],dtype=np.float64)
    legacy=namespace["_vector_digest"](vec)
    checks["historical_round12_float64_hash_exact"]=legacy==b["candidate_geometry_identity_sha256"]
    checks["canonical9_prior_exact"]=gh.canonical_geometry_sha256(g)==prior["geometry_parameter_check"]["current_canonical9_sha256"]
    record=dict(ordinal=ordinal,candidate_id_sha256=b["candidate_id_sha256"],source_csv=pin(source),
                source_ordinal_zero_based=source_ordinal,touchstone_path=row["touchstone_path"],
                bound_touchstone_sha256=meta["touchstone_sha256"],source_raw_geometry={k:row["geom__"+k] for k in gh.GEOMETRY_FIELDS},
                geometry_um=g,geometry_order=list(gh.GEOMETRY_FIELDS),checks=checks,
                hashes=dict(original_declared=b["candidate_geometry_identity_sha256"],historical_round12_float64=legacy,
                            current_canonical9=gh.canonical_geometry_sha256(g),raw17=gh._raw_geometry_identity(vec),
                            production6=gh._production_geometry_fingerprint(vec)),
                original_split="UNKNOWN",benchmark_arm=b["benchmark_arm"],pair_id_sha256=b["pair_id_sha256"],
                evaluation_reservation=b["evaluation_reservation"],prior_artifact=pin(previous),component_receipt=pin(component),
                status="TUPLE_IDENTITY_BOUND_NOT_FORMAL" if all(checks.values()) else "IDENTITY_CONFLICT",
                physical_process_compatibility="STILL_REQUIRES_SOURCE_EVIDENCE",formal_unique_check="NOT_RUN",
                historical_executed_source_bytes="NOT_ATTESTED_BY_THIS_LOCAL_SOURCE_HELPER",
                formal_added=0,new_gradient_training=0)
    outputs.append(record)
result=dict(schema="eucap15_strict112_two_tuple_crosswalk.m40.v1",utc=datetime.now(timezone.utc).isoformat(),
            status="COMPLETE_IDENTITY_ONLY" if all(all(x["checks"].values()) for x in outputs) else "CONFLICT_RETAINED",
            inputs=[pin(source),pin(index),pin(helper),pin(historical)],implementation=pin(Path(__file__)),
            previous_turn="PROGRESS_M39_ONCE_ONLY_INTAKE",source_rows=112,selected_members=2,
            exact_tuple_members=sum(x["checks"]["source_tuple_equals_bound_audit"] for x in outputs),
            exact_original_digest_members=sum(x["checks"]["historical_round12_float64_hash_exact"] for x in outputs),
            namespace_note="Different declared serializers are retained as aliases of the exact same bound tuple. No hash equality across different namespaces is required.",
            old_physical_checks_reused=True,native_calls=0,source_labels_reextracted=0,formal_added=0,train_promotions=0,
            remaining=["CURRENT_COMPATIBLE_EM_PROCESS_AND_PORT_EXECUTION_EVIDENCE","HISTORICAL111_FORMAL_ADAPTER","CROSS_SOURCE_UNIQUE_FORMAL_MERGE"],
            members=outputs)
emit_output(result,HERE/"RESULT.json")
print(json.dumps({k:result[k] for k in ("status","selected_members","exact_tuple_members","exact_original_digest_members","formal_added")}))
