"""Bind saved train-only coverage to a received-source union, never production.

No native calls, original label extraction, baseline recount, or sampler update.
The source caller owns physical/formal qualification. Missing geometry is held,
not replaced by a hash from a different serialization or grid namespace.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import eucap15_acquisition as coverage
from rfic_transformer_inverse_design.campaigns import broadband56_balanced200k as campaign

SCOPE = "RECEIVED_SOURCE_UNION_NOT_FULL_PRODUCTION"
FIELDS = list(campaign.GEOMETRY_FIELDS)
PRE_RESUME_IMPLEMENTATION = dict(sha256="53db2077f90a5caac85ebc41620f1129c5000ead8da9e996b0da9761d4f87857",
                                 published_commit="9918a63de716652a146b80caba90315d01ac965e")
PRE_EMBEDDED_IMPLEMENTATION = dict(sha256="ba11ac8af8180fe866365044918017e453e9a1e17b21434a4995d0c883eb5f99",
                                  published_commit="4c6abad0d124eb3db0e013f55f43a787cc8bc453")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pin(path):
    path = Path(path).absolute()
    raw = path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def checked(path, expected):
    path = Path(path).absolute()
    require(not path.is_symlink(), "Input must not be a symlink")
    raw = path.read_bytes()
    actual = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    require(actual["sha256"] == expected, "Input SHA mismatch: " + str(path))
    return raw, actual


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def geometry_identity(row):
    """Return exact canonical9 identity, or an explicit scoped hold reason."""
    missing = [key for key in ("geometry", "geometry_fields", "geometry_units") if key not in row]
    if missing:
        return None, "MISSING_" + "_AND_".join(missing)
    if row["geometry_fields"] != FIELDS or row["geometry_units"] != "um":
        return None, "GEOMETRY_FIELD_ORDER_OR_UNITS_MISMATCH"
    values = row["geometry"]
    if not isinstance(values, list) or len(values) != 10:
        return None, "INCOMPLETE_TEN_DIMENSION_GEOMETRY"
    try:
        identity = campaign.canonical_geometry_sha256(dict(zip(FIELDS, values)))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, "INVALID_GEOMETRY_VALUE"
    if identity != row.get("geometry_sha256"):
        return None, "CANONICAL9_HASH_MISMATCH"
    return identity, None


def resume_state(checkpoint, coverage_pin, received_pin):
    """Accept a frozen prior identity/count binding; reject a repeated source."""
    require(checkpoint["scope"] == SCOPE and checkpoint["full_production_cursor"] is None,
            "Only a received-source-union checkpoint may resume")
    require(checkpoint["original_split_unchanged"] is True, "Prior split identity is not fixed")
    require(checkpoint["coverage"] == coverage_pin, "Checkpoint/coverage pin mismatch")
    identities = [coverage._hash(value) for value in checkpoint["geometry_hashes"]]
    require(len(identities) == len(set(identities)) == checkpoint["unique_train"], "Prior identity count differs")
    consumed = set(checkpoint.get("consumed_source_sha256s", []))
    consumed.add(checkpoint["received_source"]["sha256"])
    consumed = {coverage._hash(value) for value in consumed}
    require(received_pin["sha256"] not in consumed, "RECEIVED_SOURCE_ALREADY_CONSUMED")
    return set(identities), consumed


def compact_groups(received):
    """Select declared formal-train cohorts without consuming held-out labels."""
    old_key, mixed_key = "prior_formal_backfill_train_rows", "prior_formal_backfill_rows"
    require(not (old_key in received and mixed_key in received), "CONFLICTING_BACKFILL_KEYS")
    schema = received["schema"]
    if re.fullmatch(r"eucap15_new[1-9][0-9]*_compact_train_sources\.v1", schema):
        key = old_key
    elif schema == "eucap15_new27_compact_train_and_pending_sources.v1":
        key = old_key
    elif schema == "eucap15_new59_compact_train_and_backfill_sources.v1":
        key = mixed_key
    else:
        raise ValueError("Wrong received source schema")
    require(key in received, "MISSING_DECLARED_BACKFILL_KEY: " + key)
    current, prior = received["current_formal_train_rows"], received[key]
    require(isinstance(current, list) and isinstance(prior, list), "Cohorts must be lists")
    require(all(row["assigned_development_split"] == "train" for row in current),
            "Non-train current formal source prohibited")
    selected, excluded = [], []
    for row in prior:
        split = row["assigned_development_split"]
        require(split in ("train", "validation", "test"), "Unknown backfill split")
        if key == old_key:
            require(split == "train", "Non-train row in declared train-only backfill")
        if split == "train":
            selected.append(row)
        else:
            excluded.append(dict(request_id=row["request_id"], split=split,
                                 reason="NON_TRAIN_BACKFILL_NOT_CONSUMED"))
    return (("CURRENT_NEW_FORMAL_TRAIN", current), ("PRIOR_FORMAL_BACKFILL_TRAIN", selected)), excluded


def require_pin(item, message, with_size=True):
    require(isinstance(item, dict) and isinstance(item.get("path"), str) and
            Path(item["path"]).is_absolute() and isinstance(item.get("sha256"), str) and
            re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) and
            (not with_size or (type(item.get("bytes")) is int and item["bytes"] > 0)), message)


def validate_formal_join(row):
    """Accept original train headers or the explicit same-pin resolved wrapper."""
    formal = row["formal_records"]
    request = row["request_id"]
    require(isinstance(formal, list) and len(formal) == 1 and formal[0]["request_id"] == request,
            "Formal header join differs")
    record = formal[0]
    if "assigned_split_from_frozen_request" in record:
        require("native_header" not in record and "split_resolution" not in record and
                record["assigned_split_from_frozen_request"] == "train", "Formal header join differs")
        return
    require(all(field in record for field in ("native_header", "split_resolution", "split_resolution_source",
                "assigned_development_split")), "MISSING_EXPLICIT_FORMAL_SPLIT_BINDING")
    header, resolution = record["native_header"], record["split_resolution"]
    require(record["assigned_development_split"] == row["assigned_development_split"] == "train" and
            header["request_id"] == resolution["request_id"] == request and
            header["pin"] == resolution["header_pin"] == record["pin"] and
            header["assigned_split_from_frozen_request"] == "UNKNOWN" and
            resolution["assigned_split_from_frozen_request"] == "train", "FORMAL_SPLIT_RESOLUTION_JOIN_DIFFERS")
    require_pin(record["split_resolution_source"], "FORMAL_SPLIT_RESOLUTION_SOURCE_INVALID", with_size=False)
    release = resolution["new_result_release"]
    require_pin(release, "FORMAL_SPLIT_RESOLUTION_RELEASE_INVALID")
    require(Path(row["result_pin"]["path"]).parent.parent == Path(release["path"]).parent,
            "FORMAL_SPLIT_RESOLUTION_RESULT_RELEASE_DIFFERS")


def embedded_metadata(row):
    """Use pinned compact metadata itself; never manufacture a second source."""
    required = ("request_id", "source", "geometry_sha256", "geometry", "geometry_fields", "geometry_units",
                "assigned_development_split", "result_pin", "formal_records", "strict",
                "formally_admitted_in_received_source", "valid_for_strict_comparison", "core15_eligible")
    missing = [field for field in required if field not in row]
    require(not missing, "EMBEDDED_METADATA_MISSING: " + ",".join(missing))
    require(isinstance(row["source"], str) and bool(row["source"].strip()), "EMBEDDED_SOURCE_MISSING")
    require(row["assigned_development_split"] == "train", "Non-train embedded source prohibited")
    require(all(row[field] is True for field in ("strict", "formally_admitted_in_received_source",
                "valid_for_strict_comparison", "core15_eligible")), "EMBEDDED_QUALIFICATION_NOT_TRUE")
    validate_formal_join(row)
    for item in (row["result_pin"], row["formal_records"][0]["pin"]):
        require_pin(item, "EMBEDDED_RESULT_OR_FORMAL_PIN_INVALID")
    return row


def run(args):
    paths = {}
    documents = {}
    resuming = bool(args.checkpoint)
    require(bool(args.geometry) == bool(args.geometry_sha), "Missing input pair: geometry")
    names = ("coverage", "received", "checkpoint") if resuming else ("coverage", "splits", "members", "received")
    if args.geometry:
        names += ("geometry",)
    for name in names:
        require(getattr(args, name) and getattr(args, name + "_sha"), "Missing input pair: " + name)
        raw, paths[name] = checked(getattr(args, name), getattr(args, name + "_sha"))
        documents[name] = raw if name == "coverage" else json.loads(raw)
    before = coverage.validate_coverage(csv.DictReader(io.StringIO(documents["coverage"].decode())))
    received = documents["received"]
    if resuming:
        require(not args.splits and not args.members, "Do not mix resume with original baseline inputs")
        baseline, consumed = resume_state(documents["checkpoint"], paths["coverage"], paths["received"])
    else:
        split_doc, members = (documents[name] for name in ("splits", "members"))
        require(split_doc["seed"] == 17 and split_doc["counts"]["train"] == 3801, "Frozen split differs")
        baseline = {coverage._hash(h) for h, split in split_doc["by_geometry_sha256"].items() if split == "train"}
        require(len(baseline) == 3801, "Frozen train identity count differs")
        require(len(members) == 20, "Existing research increment must have twenty train members")
        member_ids = []
        for row in members:
            require(row["split"] == "train", "Non-train member prohibited")
            identity, reason = geometry_identity(row)
            require(reason is None, "Existing twenty identity binding failed: " + str(reason))
            require(identity not in baseline and identity not in member_ids, "Existing twenty overlap")
            member_ids.append(identity)
        baseline.update(member_ids)
        require(len(baseline) == 3821 and sum(row[coverage.COUNTS[0]] > 0 for row in before) == 163,
                "Saved initial identity/occupancy differs")
        consumed = set()
    require(len(baseline) == sum(row[coverage.COUNTS[0]] for row in before), "Saved coverage/identity count differs")
    groups, excluded = compact_groups(received)
    require(received["feature_order"] == ["Lp_nH", "Ls_nH", "Qmin", "K_abs"], "Feature order differs")
    if args.geometry:
        geometry_doc = documents["geometry"]
        require(geometry_doc["schema"] == "eucap15_train_geometry_metadata_projection.v1", "Wrong geometry source")
        require(geometry_doc["source"] == {key: received["source_output"][key] for key in ("path", "sha256", "bytes")},
                "Geometry and received data do not reference the same saved output")
        geometry_rows = {row["request_id"]: row for row in geometry_doc["rows"]}
        require(len(geometry_rows) == len(geometry_doc["rows"]) == sum(len(rows) for _, rows in groups) > 0,
                "Geometry metadata cohort differs")
    else:
        require(sum(len(rows) for _, rows in groups) > 0, "No formal-train rows")
    accepted, disposition, request_ids = [], [], set()
    for group, rows in groups:
        for row in rows:
            request = row["request_id"]
            require(request not in request_ids, "Repeated received request")
            request_ids.add(request)
            require(row["assigned_development_split"] == "train", "Non-train source prohibited")
            validate_formal_join(row)
            supplement = geometry_rows.get(request) if args.geometry else embedded_metadata(row)
            require(supplement is not None, "Missing train-only geometry join")
            for field in ("request_id", "geometry_sha256", "assigned_development_split", "result_pin", "formal_records"):
                require(supplement[field] == row[field], "Geometry metadata join differs: " + field)
            if resuming:
                require(supplement["source"] == row["source"], "Candidate source differs")
            for field in ("geometry", "geometry_fields", "geometry_units"):
                if field in row:
                    require(supplement[field] == row[field], "Compact geometry differs: " + field)
            require(supplement["formally_admitted_in_received_source"] is True and
                    supplement["valid_for_strict_comparison"] is True and supplement["core15_eligible"] is True,
                    "Received source strict/formal qualification flags differ")
            identity, reason = geometry_identity(supplement)
            result = dict(request_id=request, cohort=group, original=row,
                          geometry_metadata=supplement, status="HOLD" if reason else "BOUND", reason=reason,
                          original_compact_missing_fields=[key for key in ("geometry", "geometry_fields", "geometry_units")
                                                           if key not in row])
            if reason is None:
                actual = [coverage._finite(value, "actual") for value in row["actual_response"]]
                require(len(actual) == 4, "Four actual values required")
                actual_cell = coverage.actual_landing([actual[j] for j in (0, 1, 3)])
                require(actual_cell is not None and list(actual_cell) == row["actual_cell"], "Actual cell differs")
                accepted.append(dict(geometry_hash=identity, split="train", frequency_hz=15000000000,
                    strict_lumped_valid=True, evidence_class="FRESH_REAL_EMX", actual=actual,
                    request_id=request, cohort=group))
            disposition.append(result)
    gain = coverage.coverage_gain(before, accepted, baseline_hashes=baseline)
    next_ids = baseline | {row["geometry_hash"] for row in gain["retained_unique_rows"]
                           if row["accounting_disposition"] == "NEW_IN_DOMAIN_ADDED"}
    out = Path(args.out).absolute()
    require(not out.is_symlink(), "Output symlink prohibited")
    out.mkdir(parents=True, exist_ok=False)
    inputs = dict(paths, implementation=pin(__file__), coverage_implementation=pin(coverage.__file__),
                  geometry_implementation=pin(campaign.__file__))
    baseline_document = dict(schema="eucap15_saved_train_coverage_checkpoint.v1", scope=SCOPE,
        source_pins={key: paths[key] for key in (("coverage", "checkpoint") if resuming else ("coverage", "splits", "members"))},
        original_train=3801, unique_train=len(baseline), occupied_cells=gain["occupied_before"],
        identity_algorithm="campaign.canonical_geometry_sha256 ordered named10D float format .9f compact JSON SHA256",
        geometry_fields=FIELDS, geometry_units="um", geometry_hashes=sorted(baseline),
        original_data_or_model_modified=False, full_production_cursor=None)
    write_json(out / "BASELINE_CHECKPOINT.json", baseline_document)
    write_json(out / "RECEIVED_INCREMENT.json", dict(scope=SCOPE, source_pin=paths["received"],
        original_source_scope=received["scope"], original_source_output=received["source_output"],
        geometry_metadata_projection=paths.get("geometry"),
        geometry_metadata_mode="EXTERNAL_PROJECTION" if args.geometry else "EMBEDDED_PINNED_COMPACT",
        excluded_non_train_backfills=excluded,
        source_generated_utc=received["generated_utc"], rows=disposition, gain=gain))
    with (out / "COVERAGE_AFTER.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(gain["after_coverage"][0]))
        writer.writeheader()
        writer.writerows(gain["after_coverage"])
        stream.flush()
        os.fsync(stream.fileno())
    write_json(out / "CHECKPOINT.json", dict(scope=SCOPE, unique_train=len(next_ids),
        geometry_hashes=sorted(next_ids), coverage=pin(out / "COVERAGE_AFTER.csv"),
        baseline_checkpoint=pin(out / "BASELINE_CHECKPOINT.json"), received_source=paths["received"],
        attempted_request_ids=sorted(request_ids), admitted_request_ids=[r["request_id"] for r in accepted],
        held_request_ids=[r["request_id"] for r in disposition if r["status"] == "HOLD"],
        consumed_source_sha256s=sorted(consumed | {paths["received"]["sha256"]}),
        parent_checkpoint=paths.get("checkpoint"),
        cumulative_received_source_count=len(consumed | {paths["received"]["sha256"]}),
        geometry_identity_algorithm="canonical_geometry_sha256_9dp_named_ordered_fields",
        full_production_cursor=None, original_split_unchanged=True))
    require(len(next_ids) == sum(r[coverage.COUNTS[0]] for r in gain["after_coverage"]), "Output identity/count mismatch")
    summary = dict(schema="eucap15_received_train_coverage_receipt.v1", scope=SCOPE,
        status="COMPLETE_WITH_ROW_HOLDS" if any(r["status"] == "HOLD" for r in disposition) else "COMPLETE",
        completed_utc=datetime.now(timezone.utc).isoformat(), command=sys.argv, inputs=inputs,
        baseline_unique=len(baseline), source_rows=len(disposition), bound_rows=len(accepted),
        held_rows=sum(r["status"] == "HOLD" for r in disposition), counts=gain["counts"],
        occupied_before=gain["occupied_before"], occupied_after=gain["occupied_after"],
        newly_occupied=gain["newly_occupied_cells"], sparse_crossed_5=gain["sparse_crossed_5"],
        deficit_before=sum(max(5-r[coverage.COUNTS[0]], 0) for r in before),
        deficit_after=sum(max(5-r[coverage.COUNTS[0]], 0) for r in gain["after_coverage"]),
        unique_train_after=len(next_ids), native_calls=0, training_calls=0, baseline_labels_recounted=0,
        physical_revalidation="REUSED_SOURCE_CALLER_EVIDENCE_NOT_REEXECUTED", val_test_labels_read=False,
        new_sampling_or_formal_admission=False, source_receipts_modified=False,
        resumed_existing_checkpoint=resuming, previous_implementation=PRE_RESUME_IMPLEMENTATION,
        previous_embedded_implementation=PRE_EMBEDDED_IMPLEMENTATION,
        geometry_metadata_mode="EXTERNAL_PROJECTION" if args.geometry else "EMBEDDED_PINNED_COMPACT",
        received_schema=received["schema"], excluded_non_train_backfills=excluded,
        consumed_source_sha256s=sorted(consumed | {paths["received"]["sha256"]}),
        artifacts=[pin(path) for path in sorted(out.iterdir())])
    write_json(out / "RECEIPT.json", summary)
    with (out / "SHA256SUMS").open("x", encoding="utf-8") as stream:
        for path in sorted(out.iterdir()):
            if path.name != "SHA256SUMS":
                stream.write(pin(path)["sha256"] + "  " + path.name + "\n")
    return {key: summary[key] for key in ("status", "scope", "source_rows", "bound_rows", "held_rows", "counts",
            "occupied_before", "occupied_after", "unique_train_after")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("coverage", "splits", "members", "received", "geometry", "checkpoint"):
        required = name in ("coverage", "received")
        parser.add_argument("--" + name, required=required)
        parser.add_argument("--" + name + "-sha", required=required)
    parser.add_argument("--out", required=True)
    print(json.dumps(run(parser.parse_args()), sort_keys=True))


if __name__ == "__main__":
    main()
