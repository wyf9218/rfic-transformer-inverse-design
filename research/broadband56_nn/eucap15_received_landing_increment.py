"""Project new terminals and separate prior formal backfills onto a train reference.
Read-only inputs. No admission, sampling, model, simulator, or cumulative union update.

Pins bind the received cut; they do not verify its underlying physical evidence.
For another cut the caller must already have verified the RESULT/physical chain
and the formally committed record joins. This module checks that cut's internal
identities and counts, and never performs or grants formal admission.
"""
import argparse, csv, hashlib, io, json, math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from research.broadband56_nn import eucap15_acquisition as c

PIN_BASE = "8774136cb90164549b465135d41d206371b8616a86d0b3cf5595848971a948e9"
PIN_RECEIVED = "0efdb72f1dd544f97112a2b54ef90449ce3a6f88a8992de45774c7594eb7bf00"
PIN_CORE = "d55eedd5955a6654966fd3dd0ad32074cae4376ef2709ffdbbcfe8edee431f0a"
# Backward-compatible envelope for the already verified, frozen M12 cut only.
M12_COUNTS = dict(received_terminal=24, emx_completed=20, strict_in_range=7,
                  formal_admitted=7, pending_formal=0, formal_train=5)
SPLITS = ("train", "validation", "test")
COUNT_KEYS = (*M12_COUNTS, "formal_validation", "formal_test",
              *("pending_formal_" + split for split in SPLITS))
BACKFILL_COUNT_KEYS = ("formal_admitted", *("formal_" + split for split in SPLITS))
DOC_COUNTS = dict(new_terminal="received_terminal", new_emx_completed="emx_completed",
                  new_strict_range_unique="strict_in_range",
                  fresh_formal_this_increment="formal_admitted",
                  new_core_without_formal="pending_formal")

def require(condition, message):
    if not condition:
        raise ValueError(message)

def unique_pin(pin, seen, label):
    """Check a supplied pin's shape/uniqueness, without claiming its authenticity."""
    require(isinstance(pin, dict), label + ": missing pin")
    path, sha = pin.get("path"), pin.get("sha256")
    require(isinstance(path, str) and bool(path.strip()), label + ": invalid path")
    c._hash(sha)
    require(type(pin.get("bytes")) is int and pin["bytes"] > 0,
            label + ": positive byte count required")
    for identity in (("path", path), ("sha256", sha)):
        require(identity not in seen, label + ": duplicate record pin " + identity[0])
        seen.add(identity)

def check_counts(expected, actual, label, *, required=()):
    require(isinstance(expected, dict) and set(required) <= expected.keys(),
            label + ": incomplete count envelope")
    for key, value in expected.items():
        require(key in actual and type(value) is int and value >= 0,
                label + ": invalid count " + key)
        require(actual[key] == value,
                f"{label}: {key} derived {actual[key]} != supplied {value}")

def read(path, sha):
    raw = Path(path).read_bytes()
    c._hash(sha)
    require(hashlib.sha256(raw).hexdigest() == sha, "SHA-256 mismatch: " + str(path))
    return raw

def train_landing_diagnostics(train_rows):
    """Describe one cohort against the frozen baseline, without updating coverage."""
    return dict(
        distinct_train_landing_cells=len({tuple(r["actual_cell"]) for r in train_rows}),
        train_landings_in_baseline_empty_cells=sum(r["train_cell_was_empty"] for r in train_rows),
        distinct_baseline_empty_cells_observed=sorted({tuple(r["actual_cell"]) for r in train_rows
                                                     if r["train_cell_was_empty"]}),
        train_landings_in_baseline_underfilled_cells=sum(r["frozen3801_train_cell_count"] < 5
                                                       for r in train_rows),
        train_k_above_point8=sum(r["actual_response"][3] > .8 for r in train_rows),
        train_q10_20=sum(10 <= r["actual_response"][2] <= 20 for r in train_rows),
        train_max_k=max((r["actual_response"][3] for r in train_rows), default=None),
        train_max_q=max((r["actual_response"][2] for r in train_rows), default=None))

def run(base_path, received_path, *, received_sha256=PIN_RECEIVED,
        expected_counts=None, expected_backfill_counts=None, caller_verified_sources=False):
    legacy = received_sha256 == PIN_RECEIVED and expected_counts is None
    require(legacy or caller_verified_sources is True,
            "Caller must have verified physical sources and formal record joins")
    require(legacy or expected_counts is not None, "An explicit count envelope is required")
    expected_counts = dict(M12_COUNTS) if legacy else expected_counts
    read(c.__file__, PIN_CORE)
    base = c.validate_coverage(list(csv.DictReader(io.StringIO(read(base_path, PIN_BASE).decode()))))
    require(sum(r[c.COUNTS[0]] for r in base) == 3801, "Frozen baseline must contain 3801 train rows")
    doc = json.loads(read(received_path, received_sha256))
    require(doc["feature_order"] == ["Lp_nH", "Ls_nH", "Qmin", "K_abs"], "Feature order mismatch")
    rows = doc["rows"]
    require(isinstance(rows, list), "Received rows must be a list")
    backfill_rows = doc.get("backfilled_prior_rows", [])
    require(isinstance(backfill_rows, list), "Backfilled prior rows must be a list")
    require(not backfill_rows or expected_backfill_counts is not None,
            "Backfilled prior rows require an explicit backfill count envelope")
    output, backfill_output, groups = [], [], {}
    identities, result_pins, formal_pins = set(), set(), set()
    totals = Counter({key: 0 for key in COUNT_KEYS})
    backfill_totals = Counter({key: 0 for key in BACKFILL_COUNT_KEYS})
    for index, r in enumerate(rows + backfill_rows):
        is_backfill = index >= len(rows)
        require(isinstance(r, dict), "Received row must be an object")
        for key in ("request_id", "source", "status"):
            require(isinstance(r.get(key), str) and bool(r[key].strip()), "Invalid " + key)
        c._hash(r["geometry_sha256"])
        for key in ("request_id", "geometry_sha256"):
            identity = (key, r[key])
            require(identity not in identities, "Duplicate source row " + key)
            identities.add(identity)
        unique_pin(r.get("result_pin"), result_pins, "RESULT")
        split = r["assigned_development_split"]
        require(split in SPLITS, "Original split must be train, validation or test")
        actual = r["actual_response"]
        fresh = r["status"] == "FRESH_EMX_EXTRACTED"
        require(fresh == (actual is not None), "Status/actual response mismatch")
        cell = None
        if fresh:
            require(isinstance(actual, list) and len(actual) == 4 and
                    all(type(x) in (int, float) and math.isfinite(x) for x in actual),
                    "Four finite actual response values required")
            require(type(r["strict"]) is bool, "Fresh response requires explicit strict flag")
            cell = c.actual_landing([actual[j] for j in (0, 1, 3)])
        eligible = r["strict"] is True and cell is not None
        require(r["core15_eligible"] is eligible or
                (not fresh and r["core15_eligible"] is None and r["strict"] in (False, None)),
                "Source core eligibility disagrees with strict/range values")
        formal = r["formal_records"]
        require(isinstance(formal, list) and len(formal) <= int(eligible),
                "Formal records require an eligible row and at most one exact join")
        for record in formal:
            require(isinstance(record, dict) and record.get("request_id") == r["request_id"],
                    "Formal request_id does not exactly match received row")
            unique_pin(record.get("pin"), formal_pins, "Formal")
            for key in ("geometry_sha256", "candidate_geometry_identity_sha256", "source",
                        "assigned_development_split"):
                if key in record:
                    require(key in r and record[key] == r[key], "Formal source row mismatch: " + key)
        admitted = eligible and len(formal) == 1
        require(not is_backfill or (fresh and admitted),
                "Backfilled prior row must have a strict in-range actual response and one exact formal join")
        pending = eligible and not admitted
        train = admitted and split == "train"
        nbase = base[cell[0]*64 + cell[1]*8 + cell[2]][c.COUNTS[0]] if train else None
        item = dict(r, actual_cell=list(cell) if cell is not None else None,
                    train_reference_comparison_eligible=train,
                    frozen3801_train_cell_count=nbase,
                    train_cell_was_empty=(nbase == 0) if train else None,
                    formally_admitted_in_received_source=admitted,
                    qualified_pending_formal=pending)
        item.setdefault("target_cell", None)
        item.setdefault("predicted_cell", None)
        item.setdefault("target_prediction_availability",
                        "SOURCE_FIELDS_PRESERVED_NOT_RECONSTRUCTED" if
                        any(item[key] is not None for key in ("target_cell", "predicted_cell")) else
                        "NOT_IN_RECEIVED_CUT_NOT_INFERRED")
        item.setdefault("requested_target_error", None)
        if is_backfill:
            backfill_output.append(item)
            backfill_totals.update(dict(formal_admitted=1,
                                       **{"formal_" + name: int(split == name) for name in SPLITS}))
            continue
        output.append(item)
        counts = dict(received_terminal=1, emx_completed=int(fresh), strict_in_range=int(eligible),
                      formal_admitted=int(admitted), pending_formal=int(pending))
        for name in SPLITS:
            counts["formal_" + name] = int(admitted and split == name)
            counts["pending_formal_" + name] = int(pending and split == name)
        groups.setdefault(r["source"], Counter({key: 0 for key in COUNT_KEYS})).update(counts)
        totals.update(counts)
    check_counts(expected_counts, totals, "Caller envelope", required=M12_COUNTS)
    check_counts({key: doc[name] for name, key in DOC_COUNTS.items() if name in doc},
                 totals, "Received envelope", required=("received_terminal",))
    if expected_backfill_counts is not None:
        check_counts(expected_backfill_counts, backfill_totals, "Caller backfill envelope",
                     required=BACKFILL_COUNT_KEYS)
    check_counts({key: doc[key] for key in ("prior_pending_formal_backfill", "ledger_window_fresh_formal")
                  if key in doc},
                 dict(prior_pending_formal_backfill=backfill_totals["formal_admitted"],
                      ledger_window_fresh_formal=totals["formal_admitted"] + backfill_totals["formal_admitted"]),
                 "Received formal window envelope")
    train_rows = [r for r in output if r["train_reference_comparison_eligible"]]
    backfill_train_rows = [r for r in backfill_output if r["train_reference_comparison_eligible"]]
    return dict(schema="eucap15_actual_landing_increment_m13.v1" if legacy else
                       "eucap15_actual_landing_increment.v3",
        generated_utc=datetime.now(timezone.utc).isoformat(),
        observation_utc=doc["observed_utc"], baseline_observation_utc=doc["baseline_utc"],
        input_pins=[dict(path=str(base_path),sha256=PIN_BASE),
                    dict(path=str(received_path),sha256=received_sha256),
                    dict(path=c.__file__,sha256=PIN_CORE)],
        scope="DESCRIPTIVE_INCREMENT_ONLY_NOT_CUMULATIVE_POOL_OR_ACQUISITION_SUPERIORITY",
        baseline_train_rows=3801, baseline_cells=512,
        baseline_occupied_cells=sum(r[c.COUNTS[0]] > 0 for r in base),
        source_counts=dict(totals), expected_source_counts=expected_counts,
        source_reliance="CALLER_VERIFIED_PHYSICAL_SOURCES_AND_FORMAL_JOINS_NOT_REVERIFIED_HERE",
        by_source={k:dict(v) for k,v in groups.items()},
        eligible_new_train_rows=len(train_rows), **train_landing_diagnostics(train_rows),
        backfill_counts=dict(backfill_totals), expected_backfill_counts=expected_backfill_counts,
        backfilled_prior_rows=backfill_output,
        backfill_train_landing_diagnostics=dict(
            scope="PRIOR_FORMAL_BACKFILLS_ONLY_NOT_NEW_TERMINALS_EMX_ELIGIBILITY_OR_CUMULATIVE_COVERAGE",
            baseline_train_rows=3801, baseline_cells=512,
            eligible_backfilled_train_rows=len(backfill_train_rows),
            **train_landing_diagnostics(backfill_train_rows)),
        cumulative_coverage_gain=None, cumulative_union_geometry_count=None,
        scope_caveats=[
            ("Five" if legacy else str(len(train_rows))) +
            " train rows are compared only with unchanged 3801 reference, not the full current admitted pool.",
            "Original source/split/RESULT flags/formal pins and all failures retained.",
            "Prior formal backfills are separate: they add no new terminal, EMX or eligible geometry counts.",
            "No re-extraction, fresh EMX, native admission, target generation, model update or sampling decision.",
            "Validation/test/pending-formal never contributes to train occupancy; no fabricated DOE target error.",
            "Received pin and status strings do not prove physical truth or admission; caller verification is required.",
            "No same-budget superiority or final100K support inference; missing target/predicted cells are not reconstructed."
        ], rows=output)

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--baseline",required=True);p.add_argument("--received",required=True)
    p.add_argument("--received-sha256", default=PIN_RECEIVED)
    p.add_argument("--expected-counts", type=json.loads,
                   help="JSON count envelope: received_terminal, emx_completed, strict_in_range, "
                        "formal_admitted, pending_formal, formal_train; other source_counts optional")
    p.add_argument("--expected-backfill-counts", type=json.loads,
                   help="Required for nonempty backfilled_prior_rows: formal_admitted, formal_train, "
                        "formal_validation, formal_test; separate from new-terminal counts")
    p.add_argument("--caller-verified-sources", action="store_true",
                   help="Acknowledge existing caller verification of physical sources and formal joins")
    a=p.parse_args()
    print(json.dumps(run(a.baseline,a.received,received_sha256=a.received_sha256,
                         expected_counts=a.expected_counts,
                         expected_backfill_counts=a.expected_backfill_counts,
                         caller_verified_sources=a.caller_verified_sources),
                     ensure_ascii=False,allow_nan=False,indent=2))
