"""Bind the new source100 verified original responses; do not resample or substitute."""
from pathlib import Path
import json
import extract_historical111 as h
D=Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1/p215_training_source100_gds_v1")
H=Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/samebatch_hotpath_v1/training_source_s4p_20260912T074156881679Z")
tp=h.pin(H/"TRANSPORT_RECEIPT.json")
assert tp["sha256"]=="5351ed0b91ac29add9cafe177622e3de37f2fc1ca1c47416f25682ee988b848b"
transport=json.loads((H/"TRANSPORT_RECEIPT.json").read_text())
gds=json.loads((D/"INPUT_MANIFEST.json").read_text())
gds_by={r["source_row_index"]:r for r in gds["rows"]}
rows=[]
for item in transport["records"]:
    expected=item["expected"];matched=gds_by[expected["source_row_index"]]
    assert item["status"]=="EXACT_HISTORICAL_SHA_VERIFIED"
    assert expected["evaluation"]==matched["evaluation"]
    assert expected["sha256"]==matched["historical_s4p_sha256"]==item["actual"]["sha256"]==item["local"]["sha256"]
    assert expected["ordinal"]==matched["source_prefix_ordinal"]
    rows.append(dict(source_row_index=expected["source_row_index"],evaluation=expected["evaluation"],
        source_prefix_ordinal=expected["ordinal"],merge_source="training_csv:new_training_table",
        top_cell=matched["top_cell"],local_s4p=item["local"]["path"],s4p=item["actual"],
        transfer_status="EXACT_BYTES_VERIFIED"))
assert len(rows)==100 and len({r["source_row_index"] for r in rows})==100
value=dict(sample_count=100,rows=rows,source_transport=tp,
    scope_label="ORIGINAL_NEW_TRAINING_SOURCE_PREFIX_0_TO_99; ACTUAL_P215_INDEX97567_TO97666",
    qualification_caveat="Current archived GDS necessary grid/edges PASS100. Execution-time GDS/proc identity, current Calibre/port evidence and full-history/split eligibility remain unclosed. Numerical re-extraction is conditional, not formal qualification or fresh EMX.")
h.save_new(D/"EXTRACTION_INPUT.json",value)
print(json.dumps(h.pin(D/"EXTRACTION_INPUT.json")))
