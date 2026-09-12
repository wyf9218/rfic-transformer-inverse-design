"""One bounded actual P215 partition. Reuse formula identity once; no native action."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from datetime import datetime, timezone
import extract_historical111 as h

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--repo",type=Path,required=True)
    p.add_argument("--transport",type=Path,required=True)
    p.add_argument("--transport-sha256",required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    tp=h.pin(a.transport)
    if tp["sha256"]!=a.transport_sha256:raise ValueError("transport pin mismatch")
    original=json.loads(a.transport.read_text())
    if original["sample_count"]!=100 or len(original["rows"])!=100:raise ValueError("not original first100")
    sources=h.configure_repository(a.repo)
    a.out.mkdir(parents=True,exist_ok=False)
    start=datetime.now(timezone.utc).isoformat()
    metadata,targets,failures=[],[],[]
    csv_path=a.out/"HISTORICAL_ALL111.csv"
    writer=None
    with csv_path.open("x",newline="",encoding="utf-8") as stream:
        for row in original["rows"]:
            identity={k:row[k] for k in ("source_row_index","evaluation","merge_source","top_cell")}
            try:
                source=h.pin(row["local_s4p"])
                if row["transfer_status"]!="EXACT_BYTES_VERIFIED" or source["sha256"]!=row["s4p"]["sha256"]:
                    raise ValueError("actual source identity mismatch")
                result=h.extract111_under_current_mapping(Path(row["local_s4p"]))
                target=h.target15_summary(result)
                if h.pin(row["local_s4p"])!=source:raise ValueError("source changed during extraction")
                physical=target["row"]
                in_range=bool(0.5<=physical["lp_nh"]<=2.0 and 0.5<=physical["ls_nh"]<=2.0 and 0.2<=physical["k_abs"]<=0.85)
                t=dict(identity,**target,core_range=in_range,
                       conditional_strict_and_range=target["current_definition_strict_lumped_valid"] and in_range,
                       q10_to20_support=10<=physical["qmin"]<=20,
                       current_formal_qualification="NOT_ESTABLISHED_BY_NUMERICAL_EXTRACTION",
                       formal_added=0,source=source)
                targets.append(t)
                for band_row in result.rows:
                    combined=dict(identity,**band_row)
                    if writer is None:
                        writer=csv.DictWriter(stream,fieldnames=list(combined));writer.writeheader()
                    writer.writerow(combined)
                metadata.append(dict(identity,source=source,summary=result.summary))
            except Exception as exc:
                failures.append(dict(identity,error_type=type(exc).__name__,error=str(exc),formal_added=0))
    # Recheck only these reused formula identities, once per bounded run.
    for pin in sources:
        if h.pin(pin["path"])!=pin:raise ValueError("formula source drift")
    summary=dict(schema="p215_first100_actual_historical111_conditional_reextraction.v1",
        started_utc=start,finished_utc=datetime.now(timezone.utc).isoformat(),
        scope="ORIGINAL_P215_ROWS_0_TO_99_ONLY",evidence_class="HISTORICAL_EM_RESPONSE_CURRENT_MAPPING_DIAGNOSTIC_NOT_FRESH",
        requested=len(original["rows"]),extracted=len(targets),failed=len(failures),
        retained_frequency_rows=len(targets)*111,target_index_zero_based=20,frequency_resampling=False,
        conditional_descriptor_valid=sum(t["row"]["broadband_descriptor_valid"]=="true" for t in targets),
        conditional_strict=sum(t["current_definition_strict_lumped_valid"] for t in targets),
        conditional_core_range=sum(t["core_range"] for t in targets),
        conditional_strict_and_range=sum(t["conditional_strict_and_range"] for t in targets),
        conditional_strict_range_q10to20=sum(t["conditional_strict_and_range"] and t["q10_to20_support"] for t in targets),
        strict_failure_reason_counts=dict(Counter(reason for t in targets for reason in t["strict_failure_reasons"])),
        transport=tp,current_formula_sources=sources,extractor=h.pin(h.__file__),runner=h.pin(__file__),
        native_actions=0,formally_added=0,
        qualification_caveat="All100 actual unchanged GDS fail current required grid checks in the separately bound GDS receipt. These numbers describe historical responses, not new current-qualified samples.")
    h.save_new(a.out/"TARGET15_ROWS.json",targets)
    h.save_new(a.out/"PER_SOURCE_EXTRACTION.json",metadata)
    h.save_new(a.out/"FAILURES.json",failures)
    summary["outputs"]=[h.pin(a.out/name) for name in ("HISTORICAL_ALL111.csv","TARGET15_ROWS.json","PER_SOURCE_EXTRACTION.json","FAILURES.json")]
    h.save_new(a.out/"RECEIPT.json",summary)
    print(json.dumps(dict(receipt=h.pin(a.out/"RECEIPT.json"),summary={k:summary[k] for k in ("started_utc","finished_utc","requested","extracted","failed","retained_frequency_rows","conditional_descriptor_valid","conditional_strict","conditional_core_range","conditional_strict_and_range","conditional_strict_range_q10to20","strict_failure_reason_counts","native_actions","formally_added")})))

if __name__=="__main__":main()
