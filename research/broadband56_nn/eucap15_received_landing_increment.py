"""Project new received terminals onto the unchanged 512-cell train reference.
Read-only inputs. No admission, sampling, model, simulator, or cumulative union update.
"""
import argparse, csv, hashlib, io, json, math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from research.broadband56_nn import eucap15_acquisition as c

PIN_BASE = "8774136cb90164549b465135d41d206371b8616a86d0b3cf5595848971a948e9"
PIN_RECEIVED = "0efdb72f1dd544f97112a2b54ef90449ce3a6f88a8992de45774c7594eb7bf00"
PIN_CORE = "d55eedd5955a6654966fd3dd0ad32074cae4376ef2709ffdbbcfe8edee431f0a"

def read(path, sha):
    raw = Path(path).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == sha, str(path)
    return raw

def run(base_path, received_path):
    read(c.__file__, PIN_CORE)
    base = c.validate_coverage(list(csv.DictReader(io.StringIO(read(base_path, PIN_BASE).decode()))))
    assert sum(r[c.COUNTS[0]] for r in base) == 3801
    doc = json.loads(read(received_path, PIN_RECEIVED))
    assert doc["feature_order"] == ["Lp_nH", "Ls_nH", "Qmin", "K_abs"]
    rows = doc["rows"]
    assert len(rows) == 24 == doc["new_terminal"]
    assert len({r["request_id"] for r in rows}) == len({r["geometry_sha256"] for r in rows}) == len(rows)
    output, train_cells, groups = [], [], {}
    for r in rows:
        assert r["assigned_development_split"] in ("train", "validation", "test")
        actual = r["actual_response"]
        fresh = r["status"] == "FRESH_EMX_EXTRACTED"
        assert fresh == (actual is not None)
        cell = None
        if fresh:
            assert len(actual) == 4 and all(type(x) in (int, float) and math.isfinite(x) for x in actual)
            cell = c.actual_landing([actual[j] for j in (0, 1, 3)])
        eligible = r["strict"] is True and cell is not None
        assert r["core15_eligible"] is eligible
        formal = r["formal_records"]
        assert (len(formal) == 1) if eligible else (len(formal) == 0)
        train = eligible and r["assigned_development_split"] == "train"
        nbase = base[cell[0]*64 + cell[1]*8 + cell[2]][c.COUNTS[0]] if train else None
        if train:
            train_cells.append(tuple(cell))
        item = dict(r, actual_cell=list(cell) if cell is not None else None,
                    train_reference_comparison_eligible=train,
                    frozen3801_train_cell_count=nbase,
                    train_cell_was_empty=(nbase == 0) if train else None,
                    target_cell=None, predicted_cell=None,
                    target_prediction_availability="NOT_IN_RECEIVED_CUT_NOT_INFERRED",
                    requested_target_error=None)
        output.append(item)
        g = groups.setdefault(r["source"], Counter())
        g["received_terminal"] += 1
        g["emx_completed"] += int(fresh)
        g["strict_in_range"] += int(eligible)
        g["formal_train"] += int(train)
        g["formal_validation"] += int(eligible and r["assigned_development_split"] == "validation")
        g["formal_test"] += int(eligible and r["assigned_development_split"] == "test")
    assert sum(r["core15_eligible"] for r in rows) == 7
    assert sum(r["status"] == "FRESH_EMX_EXTRACTED" for r in rows) == 20
    train_rows = [r for r in output if r["train_reference_comparison_eligible"]]
    assert len(train_rows) == 5
    empty = sorted({tuple(r["actual_cell"]) for r in train_rows if r["train_cell_was_empty"]})
    low = [r for r in train_rows if r["frozen3801_train_cell_count"] < 5]
    return dict(schema="eucap15_actual_landing_increment_m13.v1",
        generated_utc=datetime.now(timezone.utc).isoformat(),
        observation_utc=doc["observed_utc"], baseline_observation_utc=doc["baseline_utc"],
        input_pins=[dict(path=str(base_path),sha256=PIN_BASE),
                    dict(path=str(received_path),sha256=PIN_RECEIVED),
                    dict(path=c.__file__,sha256=PIN_CORE)],
        scope="DESCRIPTIVE_INCREMENT_ONLY_NOT_CUMULATIVE_POOL_OR_ACQUISITION_SUPERIORITY",
        baseline_train_rows=3801, baseline_cells=512,
        baseline_occupied_cells=sum(r[c.COUNTS[0]] > 0 for r in base),
        by_source={k:dict(v) for k,v in groups.items()},
        eligible_new_train_rows=len(train_rows), distinct_train_landing_cells=len(set(train_cells)),
        train_landings_in_baseline_empty_cells=sum(r["train_cell_was_empty"] for r in train_rows),
        distinct_baseline_empty_cells_observed=empty,
        train_landings_in_baseline_underfilled_cells=len(low),
        train_k_above_point8=sum(r["actual_response"][3] > .8 for r in train_rows),
        train_q10_20=sum(10 <= r["actual_response"][2] <= 20 for r in train_rows),
        train_max_k=max((r["actual_response"][3] for r in train_rows),default=None),
        train_max_q=max((r["actual_response"][2] for r in train_rows),default=None),
        cumulative_coverage_gain=None, cumulative_union_geometry_count=None,
        scope_caveats=[
            "Five train rows are compared only with unchanged 3801 reference, not the full current admitted pool.",
            "Original source/split/RESULT flags/formal pins and all failures retained.",
            "No re-extraction, fresh EMX, native admission, target generation, model update or sampling decision.",
            "Validation/test never contributes to train occupancy; no fabricated DOE target error.",
            "No same-budget superiority or final100K support inference; missing target/predicted cells are not reconstructed."
        ], rows=output)

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--baseline",required=True);p.add_argument("--received",required=True)
    a=p.parse_args()
    print(json.dumps(run(a.baseline,a.received),ensure_ascii=False,allow_nan=False,indent=2))
