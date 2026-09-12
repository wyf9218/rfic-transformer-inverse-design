"""One bounded read-only consumption of93 saved historical111 candidates."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

def pin(path):
    path=Path(path)
    data=path.read_bytes()
    return dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))

def save(path,value):
    data=(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
    with path.open("xb") as f:f.write(data)
    return dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--workspace",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    r=args.workspace.absolute()
    w=r/"reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1"
    h=r/"reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/samebatch_hotpath_v1"
    g=w/"p215_training_source100_gds_v1"
    module_path=w/"historical111_qualification_v1/qualification.py"
    module_pin=pin(module_path)
    if module_pin["sha256"]!="4a88a302973030606962cb3c97c8568253ba2d19f21537a067f0418ce3ceaa26":
        raise ValueError("new readonly API source changed; do not silently retry")
    spec=importlib.util.spec_from_file_location("historical111_readiness",module_path)
    api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
    source_manifest_pin=pin(g/"INPUT_MANIFEST.json")
    manifest=json.loads(Path(source_manifest_pin["path"]).read_bytes())
    extraction_pin=pin(g/"actual111_v1/RECEIPT.json")
    extraction=json.loads(Path(extraction_pin["path"]).read_bytes())
    outputs={Path(p["path"]).name:p for p in extraction["outputs"]}
    readiness_pin=pin(h/"training_source93_readiness_20260912T075008875033Z/READINESS_RECEIPT.json")
    csv_path=h/"small_handoff_20260912T073341449914Z/new_training_first100.csv"
    csv_pin=dict(path=str(csv_path),sha256="24b75adcb75cf48b2f06fbe12c7f1d0c4f55bdef97b8983167f57f075ab7a8b3",bytes=65076)
    config_pin=pin(manifest["current_configuration"]["path"])
    ctx=api.load_context(contract_pin=config_pin,source_manifest_pin=source_manifest_pin,
        extraction_receipt_pin=extraction_pin,target_rows_pin=outputs["TARGET15_ROWS.json"],
        per_source_pin=outputs["PER_SOURCE_EXTRACTION.json"],all111_pin=outputs["HISTORICAL_ALL111.csv"],
        readiness_pin=readiness_pin,geometry_helpers_path=h/"runtime/geometry_helpers.py",
        qualification_path=w/"history_queue_delta_v2/history_qualification_v2.py")
    selected=sorted(ctx.readiness)
    expected=sorted(k for k,t in ctx.targets.items() if t["conditional_strict_and_range"] is True)
    if selected!=expected or len(selected)!=93:
        raise ValueError("actual readiness is not the exact93 conditional-range source members")
    # Reuse API byte/parse cache; no legacy labels, full-frequency rows or raw S4P read.
    original_rows=ctx.csv(csv_pin)
    fields=[key[6:] for key in original_rows[0] if key.startswith("geom__")]
    args.out.mkdir(parents=True,exist_ok=False)
    started=datetime.now(timezone.utc).isoformat()
    results=[]
    for key in selected:
        original=original_rows[ctx.members[key]["source_prefix_ordinal"]]
        results.append(api.assess_member(ctx,key,
            geometry=[float(original["geom__"+f]) for f in fields],
            geometry_fields=fields,geometry_evidence_pin=csv_pin))
    result_pin=save(args.out/"MEMBERS.json",results)
    dispositions=Counter(x["status"] for x in results)
    missing=Counter(k for row in results for k in row["missing_evidence"])
    receipt=dict(schema="eucap15_training_source93_readonly_binding_consumption.v1",
        started_utc=started,completed_utc=datetime.now(timezone.utc).isoformat(),
        source_pin=pin(__file__),qualification_source=module_pin,
        source_manifest=source_manifest_pin,original_geometry_csv=csv_pin,
        saved_extraction=extraction_pin,readiness=readiness_pin,
        processed=len(results),classifications=dict(dispositions),missing_evidence=dict(missing),
        original_geometry_bound=sum("identities" in row for row in results),
        original_split_unknown=sum(row["preserved_split"] is None for row in results),
        formal_added=0,new_emx=0,calibre_started=0,physical_qa_reexecuted=False,
        numerical_extraction_reexecuted=False,all111_body_read=False,all111_sha_recomputed=False,
        full_union_dedup_run=False,ledger_modified=False,outputs=[result_pin],
        boundary="Read-only source/label/geometry binding. Not physical qualification or new production.")
    saved=save(args.out/"RECEIPT.json",receipt)
    print(json.dumps(dict(receipt=saved,processed=receipt["processed"],
        classifications=receipt["classifications"],missing=receipt["missing_evidence"],
        original_geometry_bound=receipt["original_geometry_bound"],formal_added=0)))
if __name__=="__main__":main()
