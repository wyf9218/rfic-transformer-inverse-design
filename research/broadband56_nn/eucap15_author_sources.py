"""Export already-frozen EuCAP figure data for author-made figures; no plotting or inference."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

def digest(data):
    return hashlib.sha256(data).hexdigest()

def load_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))

def no_symlinks(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"symlink not allowed: {path}")

def put_json(path, obj):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")

def put_csv(path, rows, fields):
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)

def history_rows(history, role):
    """Reshape every recorded update without interpolation/smoothing/loss conversion."""
    result = []
    previous = 0
    for entry in history:
        step = entry["step"]
        if type(step) is not int or step <= previous:
            raise ValueError("non-increasing recorded update")
        previous = step
        result.append({"role": role, "seed": 17, "step": step,
                       **{key: entry.get(key) for key in
                          ("train_loss", "validation_loss", "response_weight", "elapsed_seconds")},
                       "scope": "DEVELOPMENT6329_VALIDATION_NOT_FINAL",
                       "loss_comparability": "WITHIN_ROLE_ONLY_NOT_PHYSICAL_UNITS"})
    return result

def request_rows(rows):
    """Retain all original requests; flatten only values already in the frozen table."""
    result = []
    seen = set()
    for row in rows:
        rid = row["request_id"]
        if not rid or rid in seen:
            raise ValueError("duplicate/missing request")
        seen.add(rid)
        item = {k: row[k] for k in ("request_id", "candidate_id", "q_proxy", "state",
                                     "strict_joint_hit", "failure_detail", "touchstone_sha")}
        item.update(N_original=64, model_scope="HISTORICAL_FORMAL10K_PILOT64_NOT_NEW6329")
        item.update(strict_plot_eligible=row["state"] == "STRICT_VALID",
                    actual_interpretation="STRICT_LUMPED" if row["state"] == "STRICT_VALID"
                    else "DESCRIPTOR_OR_MISSING_NOT_STRICT")
        for field, prefix in (("target", "target"), ("grid_proxy", "proxy"), ("actual", "emx")):
            values = load_json(row[field]) if row[field] else [None] * 4
            if not isinstance(values, list) or len(values) != 4:
                raise ValueError("four physical fields required")
            item.update({f"{prefix}_{name}": value for name, value in
                         zip(("Lp_nH", "Ls_nH", "Qmin", "K_abs"), values)})
        result.append(item)
    return result

def export(spec_path, out):
    spec_path = spec_path.absolute()
    out = out.absolute()
    no_symlinks(spec_path)
    no_symlinks(out)
    spec_bytes = spec_path.read_bytes()
    spec = load_json(spec_bytes)
    if spec["schema"] != "eucap15_author_source_spec.v1" or not spec["private_author_package"]:
        raise ValueError("private figure-source spec required")
    # A new run directory is the exclusive no-clobber claim. No source tree traversal.
    out.mkdir(parents=True, exist_ok=False)
    source_dir = out / "source_data"
    source_dir.mkdir()
    entries, objects, bytepins = [], {}, {}
    started = datetime.now(timezone.utc).isoformat()
    for source in spec["sources"]:
        sid = source["id"]
        if not re.fullmatch(r"[a-z0-9_]+", sid) or sid in objects:
            raise ValueError("bad/duplicate source id")
        path = Path(source["path"])
        if not path.is_absolute() or path.suffix not in (".csv", ".json"):
            raise ValueError("only explicit CSV/JSON sources, no weights or native raw")
        no_symlinks(path)
        data = path.read_bytes()
        if digest(data) != source["sha256"]:
            raise ValueError(f"source identity changed: {sid}")
        target = source_dir / (sid + path.suffix)
        with target.open("xb") as stream:
            stream.write(data)
        fields = []
        if path.suffix == ".csv":
            reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
            fields = reader.fieldnames
            if len(fields) != len(set(fields)):
                raise ValueError("duplicate CSV column")
            obj = list(reader)
            if any(None in row for row in obj):
                raise ValueError("CSV width mismatch")
            n = len(obj)
        else:
            obj = load_json(data)
            n = len(obj) if isinstance(obj, list) else None
        objects[sid] = obj
        bytepins[sid] = (path, data)
        entries.append({**source, "local_path": str(target), "relative_path": str(target.relative_to(out)),
                        "bytes": len(data), "rows": n, "columns": fields})
    # Packaging QA, not repeated model evaluation or fresh physical verification.
    parity = objects["fig3_forward_parity"]
    if len(parity) != 1269 or len({r["target_id"] for r in parity}) != 1269:
        raise ValueError("wrong validation population")
    if any(r["split"] != "validation" or r["fixed_target_denominator"] != "1269" for r in parity):
        raise ValueError("validation/test mix")
    requests = objects["fig4_requests"]
    states = Counter(r["state"] for r in requests)
    summary = objects["fig4_summary"]
    if len(requests) != 64 or dict(states) != {k: v for k, v in summary["state_counts"].items() if v}:
        raise ValueError("original64 accounting mismatch")
    current = objects["fig4_new128_status"]
    if (current["N_original_requests"], current["N_analytic_fail"], current["N_pending_requests"],
        current["N_strict_valid"]) != (128, 35, 93, 0):
        raise ValueError("not the declared historical new128 snapshot")
    if len(objects["fig5_capacity"]) != 5:
        raise ValueError("five shape table required")
    roles = objects["fig5_training_roles"]
    stop = Counter(r["stop_reason"] for r in roles)
    updates = sum(r["updates_last_attempt"] for r in roles)
    if len(roles) != 30 or updates != 538099 or stop != {"VALIDATION_EARLY_STOP": 24, "UPDATE_BUDGET_COMPLETE": 6}:
        raise ValueError("training status source differs")
    history = history_rows(objects["fig3_forward_history"], "forward") + history_rows(objects["fig3_inverse_history"], "inverse")
    put_csv(source_dir / "fig3_learning_curves.csv", history, list(history[0]))
    flat = request_rows(requests)
    put_csv(source_dir / "fig4_request_realization.csv", flat, list(flat[0]))
    put_csv(source_dir / "fig5_training_roles.csv", roles, list(roles[0]))
    # Re-read derived exports: every numeric/string value must round-trip to its source value.
    checks = []
    for name, rows in (("fig3_learning_curves.csv", history), ("fig4_request_realization.csv", flat),
                       ("fig5_training_roles.csv", roles)):
        with (source_dir / name).open(newline="") as stream:
            readback = list(csv.DictReader(stream))
        if len(readback) != len(rows) or any(
            actual != {k: "" if v is None else str(v) for k, v in original.items()}
            for actual, original in zip(readback, rows)):
            raise ValueError("derived CSV not source-equivalent")
        checks.append({"file": name, "rows": len(rows), "result": "EXACT_FIELD_RESHAPE_PASS"})
    for sid, (path, data) in bytepins.items():
        if path.read_bytes() != data:
            raise ValueError(f"source changed during packaging: {sid}")
    put_json(out / "SOURCE_INDEX.json", {"schema": "eucap15_author_source_index.v1", "sources": entries,
        "private": True, "copies": "BYTE_IDENTICAL", "external_references_inside_JSON": "NOT_MATERIALIZED_OR_REVALIDATED"})
    put_json(out / "PACKAGING_QA.json", {"status": "PASS_SOURCE_COPY_AND_RESHAPE_ONLY",
        "source_count": len(entries), "derived_checks": checks, "original64_states": dict(states),
        "training_stop_reasons": dict(stop), "primary_updates": updates,
        "validation_rows": len(parity), "new128_snapshot": {"analytic_fail": 35, "pending": 93, "strict": 0},
        "not_claimed": ["new EMX QA", "new model evaluation", "convergence", "FINAL", "author visual approval"]})
    put_json(out / "RECEIPT.json", {"schema": "eucap15_author_source_export.v1", "status": "COMPLETE_DATA_EXPORT_NOT_FINAL_FIGURES",
        "started_utc": started, "ended_utc": datetime.now(timezone.utc).isoformat(),
        "spec": {"path": str(spec_path), "sha256": digest(spec_bytes)},
        "code": {"path": str(Path(__file__).resolve()), "sha256": digest(Path(__file__).read_bytes())},
        "argv": sys.argv, "source_count": len(entries), "figures_created": 0, "model_loads": 0,
        "optimizer_updates": 0, "native_calls": 0, "test_predictions": 0, "scientific_metrics_recomputed": False})
    files = sorted(p for p in out.rglob("*") if p.is_file())
    with (out / "SHA256SUMS").open("x") as stream:
        for path in files:
            stream.write(f"{digest(path.read_bytes())}  {path.relative_to(out)}\n")
    return {"output": str(out), "source_count": len(entries), "derived_rows": [len(history), len(flat), len(roles)]}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(export(args.spec, args.out)))
if __name__ == "__main__":
    main()
