"""Pure successor-window adapter; supplied pins are not physical certification.

Only the caller-verified OBS results and its formal increment are consumed.
Prior pending rows require explicitly supplied, SHA-verified received bytes.
No remote access, ledger scan, admission, sampling or physical re-extraction.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math

from research.broadband56_nn.eucap15_received_landing_increment import (
    SPLITS, require, unique_pin,
)

FEATURES = ["Lp_nH", "Ls_nH", "Qmin", "K_abs"]
RESULT_SCHEMA = "eucap15_production256_result.v1"
FORMAL_SCHEMA = "eucap15_production256_qualified_increment.v1"


def _time(value):
    require(isinstance(value, str), "Missing observation time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Observation time needs timezone")
    return parsed


def _feature_order(value):
    require(value in (FEATURES, ["Lp_nH", "Ls_nH", "Q_min", "K_abs"]),
            "Feature column/order conflict")


def _count(value, label):
    require(type(value) is int and value >= 0, "Invalid window count: " + label)
    return value


def _row(entry, geometry_order):
    require(isinstance(entry, dict) and isinstance(entry.get("value"), dict),
            "RESULT entry requires pin and value")
    value = entry["value"]
    require(value.get("schema") == RESULT_SCHEMA, "Unsupported RESULT schema")
    proposal = value.get("original_proposal")
    require(isinstance(proposal, dict), "Missing original proposal")
    for key in ("request_id", "source", "candidate_id", "arm"):
        require(isinstance(value.get(key), str) and value[key] == proposal.get(key),
                "RESULT/proposal identity conflict: " + key)
    require(value.get("candidate_geometry_identity_sha256") ==
            proposal.get("canonical_geometry_sha256"), "RESULT/proposal geometry conflict")
    fields, geometry = proposal.get("geometry_fields"), proposal.get("geometry")
    require(isinstance(fields, list) and fields and len(set(fields)) == len(fields) and
            all(isinstance(key, str) and key for key in fields) and
            isinstance(geometry, list) and len(fields) == len(geometry) and
            all(type(x) in (int, float) and math.isfinite(x) for x in geometry),
            "Geometry column/value conflict")
    require(geometry_order is None or fields == geometry_order, "Geometry field order conflict")
    require(proposal.get("assigned_development_split") in SPLITS, "Missing original split")
    require(type(proposal.get("seed")) is int, "Missing original sampling seed")
    for owner in (value, proposal):
        if "feature_order" in owner:
            _feature_order(owner["feature_order"])
    actual = value.get("actual_response")
    fresh = value.get("status") == "FRESH_EMX_EXTRACTED"
    require(isinstance(value.get("status"), str) and value["status"], "Missing RESULT status")
    require(fresh == (actual is not None), "Status/actual response conflict")
    if fresh:
        require(isinstance(actual, list) and len(actual) == 4 and
                all(type(x) in (int, float) and math.isfinite(x) for x in actual),
                "Actual response column/value conflict")
        require(type(value.get("valid_for_strict_comparison")) is bool and
                type(value.get("core15_eligible")) is bool, "Missing strict/core flags")
    else:
        require(value.get("valid_for_strict_comparison") in (False, None) and
                value.get("core15_eligible") in (False, None), "Non-EMX strict/core conflict")
    require(type(value.get("production_accepted")) is bool, "Missing original accepted flag")
    row = deepcopy(value)  # Keep every native RESULT field, including the whole proposal.
    additions = dict(geometry_sha256=proposal["canonical_geometry_sha256"],
                     assigned_development_split=proposal["assigned_development_split"],
                     strict=value.get("valid_for_strict_comparison"),
                     core15_eligible=value.get("core15_eligible"), result_pin=entry.get("pin"),
                     original_result_accepted_flag=value["production_accepted"], formal_records=[])
    missing_flags = [key for key in ("valid_for_strict_comparison", "core15_eligible") if key not in value]
    if missing_flags:
        additions["source_missing_flag_fields"] = missing_flags
    for key in ("geometry_fields", "geometry", "geometry_units", "seed", "recipe_sha256",
                "target_cell", "predicted_cell"):
        if key in proposal:
            additions[key] = proposal[key]
    for key, item in additions.items():
        require(key not in row or row[key] == item, "Normalized field conflict: " + key)
        row[key] = deepcopy(item)
    return row, fields


def convert_observation(observation, *, source_pin, prior_received=(), caller_verified_sources=False):
    """Return unified received data without modifying any argument or source.

    prior_received items are {pin, raw: bytes}; raw SHA/size is checked here.
    Missing formal headers stay pending unless the declared window total disagrees.
    """
    require(caller_verified_sources is True,
            "Caller must verify physical sources and exact formal joins, not just hashes")
    unique_pin(source_pin, set(), "OBS")
    observed, baseline = observation.get("utc"), observation.get("baseline_utc")
    require(_time(observed) >= _time(baseline), "Observation precedes baseline")
    entries, headers = observation.get("results"), observation.get("formal_increment")
    require(isinstance(entries, list) and isinstance(headers, list), "Missing window arrays")
    declared = observation.get("new_result_counts")
    require(isinstance(declared, dict), "Missing new_result_counts; whole-batch counts forbidden")
    require(all(isinstance(key, str) and key for key in declared), "Invalid window status")
    declared = {key: _count(value, key) for key, value in declared.items()}
    rows, by_request, seen_geometry, result_pins = [], {}, set(), set()
    geometry_order = None
    for entry in entries:
        row, geometry_order = _row(entry, geometry_order)
        request = row["request_id"]
        require(request not in by_request, "Duplicate new request_id")
        require(row["geometry_sha256"] not in seen_geometry, "Duplicate geometry identity")
        unique_pin(row["result_pin"], result_pins, "RESULT")
        seen_geometry.add(row["geometry_sha256"])
        by_request[request] = row
        rows.append(row)
    require(Counter(declared) == Counter(row["status"] for row in rows),
            "new_result_counts disagrees with new RESULT rows")
    core = sum(row["core15_eligible"] is True for row in rows)
    require(_count(observation.get("new_core"), "new_core") == core, "new_core mismatch")
    require(_count(observation.get("formal_added"), "formal_added") == len(headers),
            "formal_added/header count mismatch")
    # Only explicitly supplied prior cuts may close headers outside this new window.
    prior_rows, prior_pins, seen_prior_requests = {}, [], set()
    prior_source_pins = set()
    for supplied in prior_received:
        pin, raw = supplied.get("pin"), supplied.get("raw")
        unique_pin(pin, prior_source_pins, "Prior received")
        require(isinstance(raw, bytes) and len(raw) == pin["bytes"] and
                hashlib.sha256(raw).hexdigest() == pin["sha256"], "Prior received SHA/size mismatch")
        prior = json.loads(raw)
        _feature_order(prior.get("feature_order"))
        require(_time(prior.get("observed_utc")) <= _time(baseline), "Prior cut is not prior")
        require(isinstance(prior.get("rows"), list), "Prior received rows missing")
        prior_pins.append(deepcopy(pin))
        for row in prior["rows"]:
            request = row.get("request_id")
            require(isinstance(request, str) and request and request not in seen_prior_requests,
                    "Duplicate/missing prior request_id")
            require(request not in by_request, "New RESULT overlaps supplied prior window")
            seen_prior_requests.add(request)
            prior_rows[request] = row
    backfills, formal_pins, formal_requests = [], set(), set()
    for header in headers:
        require(isinstance(header, dict) and header.get("schema") == FORMAL_SCHEMA,
                "Unsupported formal header schema")
        request = header.get("request_id")
        require(isinstance(request, str) and request not in formal_requests,
                "Duplicate/missing formal request_id")
        unique_pin(header.get("pin"), formal_pins, "Formal")
        formal_requests.add(request)
        if request in by_request:
            row = by_request[request]
        else:
            require(request in prior_rows, "Unmatched formal header requires explicit pinned prior received: " + request)
            row = deepcopy(prior_rows[request])
            require(row.get("formal_records") == [] and row.get("core15_eligible") is True and
                    row.get("strict") is True and row.get("status") == "FRESH_EMX_EXTRACTED",
                    "Prior row is not qualified pending-formal")
            require(row.get("geometry_sha256") not in seen_geometry, "Duplicate geometry identity")
            unique_pin(row.get("result_pin"), result_pins, "RESULT")
            seen_geometry.add(row["geometry_sha256"])
            row["original_formal_records"] = deepcopy(row["formal_records"])
            backfills.append(row)
        require(row.get("core15_eligible") is True, "Formal header joins ineligible row")
        for key in ("geometry_sha256", "candidate_geometry_identity_sha256", "source",
                    "assigned_development_split"):
            if key in header:
                require(header[key] == row.get(key), "Formal header identity conflict: " + key)
        row["formal_records"] = [deepcopy(header)]
    admitted = sum(bool(row["formal_records"]) for row in rows)
    require(admitted + len(backfills) == observation["formal_added"], "Formal window join mismatch")
    return dict(schema="eucap15_received_successor_observation.v1", feature_order=FEATURES.copy(),
                observed_utc=observed, baseline_utc=baseline, source=deepcopy(source_pin),
                prior_received_pins=prior_pins,
                source_reliance="CALLER_VERIFIED_PHYSICAL_SOURCES_AND_FORMAL_JOINS_NOT_REVERIFIED_HERE",
                adaptation="Q_min/Qmin display synonym only; exact request joins; native fields preserved",
                new_terminal=len(rows), new_emx_completed=sum(row["status"] == "FRESH_EMX_EXTRACTED" for row in rows),
                new_strict_range_unique=core, fresh_formal_this_increment=admitted,
                new_core_without_formal=core-admitted, prior_pending_formal_backfill=len(backfills),
                ledger_window_fresh_formal=admitted+len(backfills), rows=rows,
                backfilled_prior_rows=backfills)
