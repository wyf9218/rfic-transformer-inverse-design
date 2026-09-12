"""Read-only necessary grid/edge checks on existing, identity-bound historical cells.
Not a complete foundry audit, Calibre receipt, EMX result, or admission command.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
from datetime import datetime, timezone

import gdstk
from rfic_transformer_inverse_design.layout import foundry_audit as audit

AUDIT_SHA = "18345730dab23fbdf4a88dfab227622a889ca043122089652f68db6a38eb04c3"
GRID_UM = 0.005

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def inspect(manifest):
    module = Path(audit.__file__).resolve()
    if sha(module) != AUDIT_SHA:
        raise ValueError("shared audit implementation identity mismatch")
    policy = manifest["current_configuration"]
    if policy["sha256"] != "431ad59c22df2471746ab5a2eb73a3a7a6484fa511b71c9d6e6c93fb4b3d04d7" or sha(policy["path"]) != policy["sha256"]:
        raise ValueError("frozen current configuration identity mismatch")
    config = audit.load_run_config(Path(policy["path"]))
    if not config.emx.foundry_layout.enabled or config.emx.foundry_layout.manufacturing_grid_um != GRID_UM:
        raise ValueError("frozen foundry/grid policy mismatch")
    files, cache, rows = {}, {}, []
    for item in manifest["files"]:
        local = Path(item["local_path"])
        if not local.is_absolute() or local.is_symlink() or not local.is_file():
            raise ValueError("not an absolute, regular, non-symlink input")
        before = local.stat()
        if sha(local) != item["sha256"] or before.st_size != item["bytes"]:
            raise ValueError("transported GDS identity mismatch")
        after = local.stat()
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise ValueError("input changed during identity read")
        key = (item["remote_path"], item["sha256"])
        if key in files:
            raise ValueError("duplicate GDS map key")
        files[key] = item
        lib = gdstk.read_gds(str(local))
        parsed = local.stat()
        if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) != (parsed.st_dev,parsed.st_ino,parsed.st_size,parsed.st_mtime_ns,parsed.st_ctime_ns):
            raise ValueError("GDS changed during parse")
        if not math.isclose(lib.unit, 1e-6, rel_tol=0.0, abs_tol=1e-18):
            raise ValueError("GDS unit is not frozen micrometer unit")
        cache[key] = (lib, {cell.name:cell for cell in lib.cells})
    seen = set()
    for item in manifest["rows"]:
        ordinal = item["source_row_index"]
        if ordinal in seen:
            raise ValueError("duplicate source ordinal")
        seen.add(ordinal)
        key = (item["gds_remote_path"], item["gds_sha256"])
        if key not in cache:
            raise ValueError("row GDS binding missing")
        _, cells = cache[key]
        cell = cells.get(item["top_cell"])
        row = dict(item)
        if cell is None:
            row.update(status="FAIL_TOPCELL_BINDING", grid=None, required_grid_edges_pass=False)
        else:
            polygons = list(cell.get_polygons(apply_repetitions=True, include_paths=True, depth=None))
            labels = list(cell.get_labels(apply_repetitions=True, depth=None))
            result = audit._actual_grid_audit(polygons, labels=labels, grid_um=GRID_UM)
            necessary_pass = bool(polygons) and result["off_grid_vertex_count"] == 0 and result["noncanonical_edge_count"] == 0
            row.update(status="NECESSARY_GRID_EDGES_PASS_NOT_FULL_QUALIFICATION" if necessary_pass else "FAIL_CURRENT_REQUIRED_GRID_OR_EDGES",
                       grid=result, required_grid_edges_pass=necessary_pass)
        row.update(calibre_executed=False, emx_executed=False, current_formal_qualified=None, formally_added=0)
        rows.append(row)
    return {
        "schema":"p215_actual_historical_gds_current_necessary_checks.v1",
        "utc":datetime.now(timezone.utc).isoformat(),
        "shared_audit":{"path":str(module),"sha256":AUDIT_SHA,"function":"_actual_grid_audit","grid_um":GRID_UM},
        "runtime":{"python":sys.version,"gdstk":gdstk.__version__},
        "current_configuration":policy,
        "files":list(files.values()),"rows":rows,"counts":dict(Counter(r["status"] for r in rows)),
        "limits":["Uses each original declared topcell; does not rename, repair, flatten/write, or substitute geometry.",
                  "A required grid/edge failure disproves direct current-contract reuse of this unchanged GDS.",
                  "A pass proves only these necessary checks, not source-layout equivalence, all foundry rules, process identity, Calibre, SRF or qualification.",
                  "Off-grid labels are recorded separately; required_grid_edges_pass uses the original foundry check predicates.",
                  "Historical S4P validity as evidence of its original GDS is not erased by a current-contract failure."],
        "native_actions":0,"formal_added":0
    }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    output=Path(args.output)
    if output.exists():
        raise FileExistsError("no-clobber output")
    source=Path(args.manifest)
    data=json.loads(source.read_text())
    result=inspect(data)
    result["input_manifest"]={"path":str(source.resolve()),"sha256":sha(source)}
    result["implementation"]={"path":str(Path(__file__).resolve()),"sha256":sha(__file__)}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("x",encoding="utf-8") as stream:
        json.dump(result,stream,indent=2,allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output":str(output),"sha256":sha(output),"rows":len(result["rows"]),"counts":result["counts"],"native_actions":0,"formal_added":0}))

if __name__=="__main__":
    main()
