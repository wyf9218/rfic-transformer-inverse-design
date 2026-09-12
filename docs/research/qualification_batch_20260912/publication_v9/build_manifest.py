"""Adapt this new bounded native GDS handoff to the existing necessary-check runner."""
import json, hashlib, os
from pathlib import Path
R=Path("/Users/wyf/Documents/模拟变压器AI反向建模")
W=Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z/batch_qualification_resume_20260912_v1")
H=Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15_native_owner_20260909T062500Z/resume15_20260912/samebatch_hotpath_v1/training_source_gds_20260912T073738283479Z")
OUT=Path(__file__).parent
def read(path, expected=None):
    with path.open("rb") as f:
        a=os.fstat(f.fileno()); raw=f.read(); b=os.fstat(f.fileno())
    ident=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    assert ident(a)==ident(b)==ident(path.stat())
    assert not any(p.is_symlink() for p in (path,*path.parents))
    if expected:assert hashlib.sha256(raw).hexdigest()==expected
    return json.loads(raw)
obs=read(H/"OBSERVATION.json","81734ed700c23e2165fcd46bd78a7e63b9ea42df43681f94b2092f1a4f810633")
transport=read(H/"TRANSPORT_RECEIPT.json","f015dab611df3065e3faaa7629c1c05a72ef4af7408f7796e4688aa21bd85f1e")
prior=read(W/"p215_gds_compatibility_v1/INPUT_MANIFEST.json","83d98a2040056ad9c9b9d34fbc76ff0dedd2c30227c8f56ea945bf3f78f7b128")
files={};rows=[]
for row in obs["rows"]:
    assert row["status"]=="COMMAND_GDS_BOUND_CURRENT_BYTES"
    assert len(row["indexed_identity"])==1
    hit=row["indexed_identity"][0];gds=row["gds"];cmd=row["actual_recorded_command"]
    assert hit["evaluation"]==row["evaluation"]
    assert hit["touchstone_sha256"]==row["historical_touchstone_sha256"]
    assert cmd[1]==gds["path"] and cmd[2]==row["top_cell"]
    assert cmd[cmd.index("-s")+1]==row["touchstone_path"]
    local=transport["path_map"][gds["path"]]
    assert local["sha256"]==gds["sha256"] and local["bytes"]==gds["bytes"]
    files[gds["path"]]=dict(remote_path=gds["path"],local_path=local["path"],
                            sha256=gds["sha256"],bytes=gds["bytes"])
    rows.append(dict(source_row_index=hit["source_row_index"],source_prefix_ordinal=row["source_prefix_ordinal"],
        evaluation=row["evaluation"],top_cell=row["top_cell"],gds_remote_path=gds["path"],
        gds_sha256=gds["sha256"],raw_geometry_identity_sha256=hit["raw_geometry_sha256"],
        production_geometry_fingerprint_sha256=hit["production_geometry_sha256"],
        current_gds_historical_execution_sha_verified=False,historical_s4p_sha256=hit["touchstone_sha256"]))
assert len(rows)==100 and len({r["source_row_index"] for r in rows})==100
manifest=dict(schema="p215_new_training_source100_gds_current_bindings.v1",
    current_configuration=prior["current_configuration"],
    transport=dict(path=str(H/"TRANSPORT_RECEIPT.json"),
        sha256="f015dab611df3065e3faaa7629c1c05a72ef4af7408f7796e4688aa21bd85f1e",
        observation=dict(path=str(H/"OBSERVATION.json"),
            sha256="81734ed700c23e2165fcd46bd78a7e63b9ea42df43681f94b2092f1a4f810633")),
    boundary="Current observed archived GDS/command bytes only; no historical execution-time GDS SHA proof. No S4P/re-extraction/Calibre/EMX.",
    files=list(files.values()),rows=rows)
with (OUT/"INPUT_MANIFEST.json").open("x") as f:
    json.dump(manifest,f,indent=2);f.write("\n")
print(json.dumps(dict(rows=len(rows),gds=len(files),native=0,original_physical_files_edited=0)))
