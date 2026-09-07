"""Research-only exact-10,000 intake from one committed Broadband56 snapshot.

No remote access, producer changes, solver work, or training. ``freeze`` writes
identities only; ``prepare`` is a separate, explicit materialization command.
Source/raw counts, selected geometries, and gradient-training exposure are
different quantities. Invalid physical rows remain present with their masks.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from .data import (FREQUENCY_HZ, PHYSICAL_COLUMNS, S_COLUMNS, SPLIT_NAMES,
                   Y_COLUMNS, _check_pin, _fit_scale, _json, _split_for_hash,
                   _true, _write_json, sha256)

EXACT_COUNT = 10_000
GATES = ("duplicate_status", "geometry_bounds_status", "analytical_status",
         "topology_status", "cadence_gds_status", "calibre_status", "emx_status",
         "s4p_status", "s_to_z_status", "feature_extraction_status")
SOURCE_FLAGS = ("finite_values", "positive_primary_resistance", "positive_secondary_resistance",
                "positive_primary_inductive_reactance", "positive_secondary_inductive_reactance",
                "extraction_continuity_status", "below_half_srf", "srf_status", "passivity_status",
                "reciprocity_status", "inside_broad_response_envelope", "inside_literature_practical_panel",
                "outside_envelope_reason")
Z_COLUMNS = tuple(name.replace("s", "z", 1) for name in S_COLUMNS)
EXTRA_PHYSICAL_COLUMNS = ("lp_h", "ls_h", "mutual_inductance_h", "ls_over_lp", "xp_ohm", "xs_ohm")


def _pin(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def _canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _digest_string(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _summary_files(output):
    with (output / "SHA256SUMS.txt").open("x", encoding="utf-8") as handle:
        for path in sorted(output.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS.txt":
                handle.write(f"{sha256(path)}  {path.name}\n")


def _source(source_path, validate_small=False):
    source_pin = _pin(source_path)
    source = _json(source_path)
    if source.get("schema") == "bb_committed_increment_source.v1":
        return _chain_source(source_path, source)
    if source.get("schema") != "bb_source_manifest.v1":
        raise ValueError("unsupported source manifest")
    pins = source["files"]
    required = {"checkpoint_receipt", "raw_products_receipt"}
    if not required.issubset(pins):
        raise ValueError("missing formal source acceptance receipts")
    receipt_paths = {name: _check_pin(pins[name], source_path.parent) for name in required}
    checkpoint, raw = (_json(receipt_paths[name]) for name in ("checkpoint_receipt", "raw_products_receipt"))
    if checkpoint.get("overall_status") != "PASS" or checkpoint.get("decision") != "USE_CHECKPOINT":
        raise ValueError("checkpoint is not formally accepted")
    checks = checkpoint.get("checks")
    if not isinstance(checks, list) or not checks or not all(isinstance(c, dict) and c.get("pass") is True for c in checks):
        raise ValueError("checkpoint lacks complete passing named checks")
    if raw.get("overall_status") != "PASS" or raw.get("decision") != "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS":
        raise ValueError("raw products are not formally accepted")
    if not raw.get("checks") or not all(v is True for v in raw["checks"].values()) or raw["checks"].get("output_is_no_clobber") is not True:
        raise ValueError("raw products are not a no-clobber committed publication")
    contract = checkpoint["contract_fingerprint_sha256"]
    if not _digest_string(contract) or raw.get("contract_fingerprint_sha256") != contract or source.get("contract_fingerprint_sha256") != contract:
        raise ValueError("scientific contract mismatch")
    count = checkpoint["expected_accepted"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("invalid accepted checkpoint count")
    if raw["counts"].get("accepted_geometries") != count or raw["counts"].get("geometry_frequency_rows") != count * 56:
        raise ValueError("formal accepted counts disagree")
    evidence = {"source_manifest": source_pin, "checkpoint_receipt": _pin(receipt_paths["checkpoint_receipt"]),
                "raw_products_receipt": _pin(receipt_paths["raw_products_receipt"]),
                "formally_accepted_geometries": count, "contract_fingerprint_sha256": contract}
    if count < EXACT_COUNT and not validate_small:
        return source, None, evidence
    required |= {"accepted_geometries", "long_features", "sparameter_index", "geometry_bounds", "checkpoint_status"}
    if not required.issubset(pins):
        raise ValueError(f"missing committed snapshot inputs: {sorted(required - pins.keys())}")
    files = {name: _check_pin(pin, source_path.parent) for name, pin in pins.items()}
    status = _json(files["checkpoint_status"])
    mode = checkpoint.get("audit_mode")
    allowed_state = {"golden": "GOLDEN_COMPLETE", "pilot": f"PILOT_{count}_COMPLETE",
                     "checkpoint": "COMPLETE_200K" if count == 200000 else "CHECKPOINT_COMPLETE",
                     "round": f"ROUND_{count}_COMPLETE"}.get(mode)
    if (allowed_state is None or status.get("checkpoint_status") != allowed_state or
            status.get("audit_mode") != mode or status.get("contract_fingerprint_sha256") != contract or
            any(status.get(key) != expected for key, expected in
                (("accepted_geometries", count), ("s4p_artifacts", count), ("geometry_frequency_rows", count * 56)))):
        raise ValueError("checkpoint status is not the exact terminal committed snapshot")
    if checkpoint["outputs"]["checkpoint_status"]["sha256"] != pins["checkpoint_status"]["sha256"]:
        raise ValueError("checkpoint terminal status SHA is not receipt-bound")
    for role, producer_role in (("accepted_geometries", "accepted_geometries"), ("long_features", "long_features"),
                                ("sparameter_index", "artifact_index"), ("geometry_bounds", "geometry_bounds")):
        if checkpoint["inputs"][producer_role]["sha256"] != pins[role]["sha256"]:
            raise ValueError(f"checkpoint does not bind {role}")
        if role != "geometry_bounds" and raw["outputs"][producer_role]["sha256"] != pins[role]["sha256"]:
            raise ValueError(f"raw publication does not bind {role}")
    if "long_features_manifest" in files and raw["outputs"]["long_features_manifest"]["sha256"] != pins["long_features_manifest"]["sha256"]:
        raise ValueError("raw publication does not bind long feature manifest")
    if "materializer_receipt" in files:
        materializer = _json(files["materializer_receipt"])
        if (materializer.get("overall_status") != "PASS" or materializer.get("checkpoint_accepted") != count or
                materializer.get("contract_fingerprint_sha256") != contract or
                materializer.get("checkpoint_receipt", {}).get("sha256") != pins["checkpoint_receipt"]["sha256"]):
            raise ValueError("materializer does not bind the exact committed checkpoint")
    port = source.get("port_contract", {})
    if port.get("port_order") != ["P001", "P002", "P003", "P004"] or port.get("reference_impedance_ohm") != 50.0:
        raise ValueError("explicit verified four-port 50-ohm contract required")
    evidence["checkpoint_status"] = _pin(files["checkpoint_status"])
    evidence["source_files"] = {name: _pin(path) for name, path in files.items()}
    return source, files, evidence


def _chain_metadata(source_path, source):
    """Validate original JSON bytes/chain first; never hash a growing raw CSV."""
    from rfic_transformer_inverse_design.campaigns.broadband56_stage_progress import validate_stage_progress_receipt
    def local(record):
        return _check_pin(record, source_path.parent)
    base_path = local(source["base_source_manifest"])
    base = _json(base_path)
    if base.get("schema") != "bb_source_manifest.v1":
        raise ValueError("increment source requires a full committed checkpoint as base")
    base_cp = _json(_check_pin(base["files"]["checkpoint_receipt"], base_path.parent))
    handoff_path = local(source["source_handoff"])
    handoff = _json(handoff_path)
    if handoff.get("schema") != "b56_private_read_only_data_handoff.v1":
        raise ValueError("unsupported producer handoff schema")
    boundary_path = local(source["boundary_progress_receipt"])
    boundary = _json(boundary_path)
    status_path = local(source["checkpoint_status"])
    status = _json(status_path)
    count = base_cp["expected_accepted"]
    contract = base_cp["contract_fingerprint_sha256"]
    expected_status = {"golden": "GOLDEN_COMPLETE", "pilot": f"PILOT_{count}_COMPLETE",
                       "round": f"ROUND_{count}_COMPLETE",
                       "checkpoint": "COMPLETE_200K" if count == 200000 else "CHECKPOINT_COMPLETE"}.get(base_cp.get("audit_mode"))
    if (base_cp.get("overall_status") != "PASS" or base_cp.get("decision") != "USE_CHECKPOINT" or
            not base_cp.get("checks") or any(c.get("pass") is not True for c in base_cp["checks"]) or
            expected_status is None or status.get("checkpoint_status") != expected_status or
            status.get("audit_mode") != base_cp.get("audit_mode") or
            source["contract_fingerprint_sha256"] != contract or handoff["contract_fingerprint_sha256"] != contract or
            source["campaign_id"] != handoff["campaign_id"] or base_cp.get("campaign_id") != source["campaign_id"] or
            base_cp["outputs"]["checkpoint_status"]["sha256"] != source["checkpoint_status"]["sha256"] or
            status.get("accepted_geometries") != count or status.get("geometry_frequency_rows") != count * 56 or
            status.get("s4p_artifacts") != count or status.get("contract_fingerprint_sha256") != contract or
            handoff["checkpoint"]["receipt"]["sha256"] != base["files"]["checkpoint_receipt"]["sha256"] or
            handoff["checkpoint"]["count"] != count or boundary.get("accepted_after") != count or
            boundary.get("contract_fingerprint_sha256") != contract):
        raise ValueError("handoff/base/checkpoint/boundary identities or counts disagree")
    items = source["increments"]
    if not items or not handoff.get("increments"):
        raise ValueError("missing committed post-base progress chain")
    if items[0]["receipt"]["sha256"] != handoff["increments"][0]["pin"]["sha256"]:
        raise ValueError("first increment is not the handoff-pinned post-base commit")
    anchor = handoff["increments"][0]["receipt"]
    backend, auth, stage = anchor["backend_identity_manifest_sha256"], anchor["full_campaign_authorization_receipt_sha256"], anchor["stage"]
    prior_sha, attempt = sha256(boundary_path), boundary["attempt_index"]
    boundary_errors = validate_stage_progress_receipt(boundary, stage=stage, attempt_index=attempt,
        accepted_before=boundary["accepted_before"], prior_progress_receipt_sha256=boundary["prior_progress_receipt_sha256"],
        backend_manifest_sha256=backend, authorization_receipt_sha256=auth, verify_artifacts=False)
    if boundary_errors:
        raise ValueError("base boundary progress invalid: " + "; ".join(boundary_errors))
    receipts, receipt_pins = [], []
    for item in items:
        path = local(item["receipt"])
        receipt = _json(path)
        if not receipts and receipt != anchor:
            raise ValueError("handoff embedded first increment differs from original receipt")
        attempt += 1
        errors = validate_stage_progress_receipt(receipt, stage=stage, attempt_index=attempt, accepted_before=count,
            prior_progress_receipt_sha256=prior_sha, backend_manifest_sha256=backend,
            authorization_receipt_sha256=auth, verify_artifacts=False)
        if errors:
            raise ValueError("committed increment invalid: " + "; ".join(errors))
        receipts.append(receipt); receipt_pins.append(_pin(path))
        count, prior_sha = receipt["accepted_after"], sha256(path)
    if source["committed_accepted"] != count:
        raise ValueError("declared committed watermark differs from actual receipt chain")
    evidence = {"source_manifest": _pin(source_path), "base_source_manifest": _pin(base_path),
                "checkpoint_receipt": _pin(_check_pin(base["files"]["checkpoint_receipt"], base_path.parent)),
                "checkpoint_status": _pin(status_path), "source_handoff": _pin(handoff_path),
                "boundary_progress_receipt": _pin(boundary_path), "increment_receipts": receipt_pins,
                "base_geometries": base_cp["expected_accepted"], "formally_accepted_geometries": count,
                "contract_fingerprint_sha256": contract, "backend_manifest_sha256": backend,
                "authorization_receipt_sha256": auth, "chain_stage": stage,
                "producer_validator": _pin(Path(__file__).parents[2] / "rfic_transformer_inverse_design/campaigns/broadband56_stage_progress.py")}
    return base_path, base, receipts, evidence


def inspect_source_manifest(source_manifest):
    """Metadata-only trigger evidence; READY count still requires full freeze."""
    path = Path(source_manifest).resolve()
    source = _json(path)
    if source.get("schema") == "bb_committed_increment_source.v1":
        _, _, _, evidence = _chain_metadata(path, source)
    elif source.get("schema") == "bb_source_manifest.v1":
        cp_path = _check_pin(source["files"]["checkpoint_receipt"], path.parent)
        cp = _json(cp_path)
        if cp.get("overall_status") != "PASS" or cp.get("decision") != "USE_CHECKPOINT":
            raise ValueError("no formal checkpoint acceptance")
        evidence = {"source_manifest": _pin(path), "checkpoint_receipt": _pin(cp_path),
                    "formally_accepted_geometries": cp["expected_accepted"]}
    else:
        raise ValueError("unsupported source manifest")
    return {"schema": "bb_source_inspection.v1", "status": "WAITING_FOR_10K" if evidence["formally_accepted_geometries"] < EXACT_COUNT else "COMMITTED_COUNT_READY_FULL_FREEZE_REQUIRED",
            "evidence": evidence, "data_materialized": False, "training_allowed": False}


def _chain_source(source_path, source):
    base_path, base, receipts, evidence = _chain_metadata(source_path, source)
    if evidence["formally_accepted_geometries"] < EXACT_COUNT:
        return source, None, evidence
    # Adapt only the local source descriptor; all producer receipt bytes remain
    # unchanged. The base status copy is the same receipt-bound file identity.
    base_status = source["checkpoint_status"]
    if "checkpoint_status" not in base["files"]:
        raise ValueError("base source manifest must include copied checkpoint_status for full freeze")
    if base["files"]["checkpoint_status"]["sha256"] != base_status["sha256"]:
        raise ValueError("base checkpoint status differs")
    _, base_files, base_evidence = _source(base_path, validate_small=True)
    if source.get("port_contract") != base.get("port_contract"):
        raise ValueError("base and increment port contracts differ")
    parts = {role: [base_files[role]] for role in ("accepted_geometries", "long_features", "sparameter_index")}
    all_pins = {"base/" + role: pin for role, pin in base_evidence["source_files"].items()}
    for index, (item, receipt) in enumerate(zip(source["increments"], receipts)):
        for role, original in receipt["artifacts"].items():
            copied = item["files"][role]
            if copied["sha256"] != original["sha256"] or copied.get("size_bytes") != original["size_bytes"]:
                raise ValueError("localized increment does not match original published artifact")
            path = _check_pin(copied, source_path.parent)
            all_pins[f"increment/{index}/{role}"] = _pin(path)
            destination = {"accepted_geometry_increment": "accepted_geometries", "long_features": "long_features",
                           "s4p_artifact_index": "sparameter_index"}.get(role)
            if destination:
                parts[destination].append(path)
        # Boundary-only cumulative records are duplicate views, not added rows.
        # Verify supplied copies, but never count them twice.
        if receipt.get("round_cumulative_inputs"):
            for role, original in receipt["round_cumulative_inputs"].items():
                copied = item.get("round_cumulative_inputs", {}).get(role)
                if not copied or copied["sha256"] != original["sha256"] or copied.get("size_bytes") != original["size_bytes"]:
                    raise ValueError("missing or mismatched boundary cumulative artifact pin")
                path = _check_pin(copied, source_path.parent)
                all_pins[f"increment/{index}/round/{role}"] = _pin(path)
    evidence["source_files"] = all_pins
    files = {**base_files, **parts, "_part_counts": [evidence["base_geometries"], *[r["accepted_this_attempt"] for r in receipts]]}
    return source, files, evidence


def _csv_rows(paths, part_counts=None, frequencies=False):
    before = 0
    for part, path in enumerate(paths if isinstance(paths, list) else [paths]):
        count = 0
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if part_counts is not None:
                    if "accepted_sequence" in row:
                        if not before < int(row["accepted_sequence"]) <= before + part_counts[part]:
                            raise ValueError("CSV rows escape their exact committed increment range")
                    elif part > 0:
                        raise ValueError("increment row lacks authoritative accepted_sequence")
                count += 1
                yield row
        if part_counts is not None:
            if count != part_counts[part] * (56 if frequencies else 1):
                raise ValueError("CSV part count differs from its committed increment receipt")
            before += part_counts[part]


def _geometries(files, count, contract):
    from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import canonical_geometry_sha256
    bounds = _json(files["geometry_bounds"])
    fields = bounds["geometry_coverage_contract"]["field_order"]
    if (bounds.get("contract_fingerprint_sha256") != contract or len(fields) != 10 or
            len(set(fields)) != 10 or set(fields) != set(bounds["field_bounds_um"])):
        raise ValueError("geometry field/science contract mismatch")
    lower = np.array([bounds["field_bounds_um"][f][0] for f in fields])
    upper = np.array([bounds["field_bounds_um"][f][1] for f in fields])
    rows, ids, hashes, coordinates = [], set(), set(), set()
    with nullcontext(_csv_rows(files["accepted_geometries"], files.get("_part_counts"))) as reader:
        for source_index, row in enumerate(reader):
            if (row["campaign_contract_fingerprint"] != contract or any(row[k] != "PASS" for k in GATES) or
                    int(row["accepted_sequence"]) != source_index + 1 or int(row["calibre_blocking_violations"]) != 0):
                raise ValueError("invalid accepted geometry gates/sequence/science")
            gid, digest = row["geometry_id"], row["geometry_sha256"]
            xyz = tuple(float(row[f"geom__{f}"]) for f in fields)
            if canonical_geometry_sha256(dict(zip(fields, xyz)), fields=fields) != digest:
                raise ValueError("geometry SHA differs from exact producer nine-decimal identity")
            if not gid or not _digest_string(digest) or gid in ids or digest in hashes or xyz in coordinates:
                raise ValueError("duplicate geometry ID/hash/coordinates or invalid identity")
            if not np.isfinite(xyz).all() or (np.asarray(xyz) < lower).any() or (np.asarray(xyz) > upper).any():
                raise ValueError("nonfinite or out-of-contract geometry")
            ids.add(gid); hashes.add(digest); coordinates.add(xyz)
            rows.append({"geometry_id": gid, "geometry_sha256": digest, "source_index": source_index,
                         "source_accepted_sequence": source_index + 1, "geometry": list(xyz),
                         "accepted_csv_row_sha256": _canonical(row), "campaign_phase": row["campaign_phase"],
                         "acquisition_source": row["acquisition_source"]})
    if len(rows) != count:
        raise ValueError("accepted geometry table count differs from formal checkpoint")
    return rows, bounds, fields


def _scan_features(files, rows, selected, contract, collect=False):
    index = {r["geometry_id"]: i for i, r in enumerate(rows)}
    selected_index = {r["geometry_id"]: i for i, r in enumerate(selected)}
    seen = np.zeros((len(rows), 56), dtype=bool)
    touchstones = {}
    row_pins = [[None] * 56 for _ in selected]
    flag_fields = None
    categories = {}
    flag_codes = z = extra_physical = None
    if collect:
        s = np.empty((len(selected), 56, 32), dtype=np.float64)
        physical = np.empty((len(selected), 56, len(PHYSICAL_COLUMNS)), dtype=np.float64)
        broad = np.empty((len(selected), 56), dtype=bool)
        strict = np.empty_like(broad)
    with nullcontext(_csv_rows(files["long_features"], files.get("_part_counts"), frequencies=True)) as reader:
        for row in reader:
            present_flags = [key for key in SOURCE_FLAGS if key in row]
            if flag_fields is None:
                flag_fields = present_flags
                categories = {key: [] for key in flag_fields}
                extra_fields = [key for key in EXTRA_PHYSICAL_COLUMNS if key in row]
                has_z = all(key in row for key in Z_COLUMNS)
                if collect and flag_fields:
                    flag_codes = np.empty((len(selected), 56, len(flag_fields)), dtype=np.int16)
                if collect and has_z:
                    z = np.empty((len(selected), 56, 32), dtype=np.float64)
                if collect and extra_fields:
                    extra_physical = np.empty((len(selected), 56, len(extra_fields)), dtype=np.float64)
            if present_flags != flag_fields or all(key in row for key in Z_COLUMNS) != has_z:
                raise ValueError("base/increment validity or full-Z schema differs")
            if [key for key in EXTRA_PHYSICAL_COLUMNS if key in row] != extra_fields:
                raise ValueError("base/increment extra physical feature schema differs")
            i = index.get(row["geometry_id"])
            if (i is None or row["geometry_sha256"] != rows[i]["geometry_sha256"] or
                    row["campaign_contract_fingerprint"] != contract or int(row["accepted_sequence"]) != i + 1):
                raise ValueError("long row geometry identity/science/sequence mismatch")
            f = int(row["frequency_hz"])
            j = (f - int(FREQUENCY_HZ[0])) // 1_000_000_000
            if not 0 <= j < 56 or f != FREQUENCY_HZ[j] or seen[i, j]:
                raise ValueError("duplicate/different or off-grid geometry-frequency row")
            seen[i, j] = True
            digest = row["s4p_sha256"]
            if not _digest_string(digest) or touchstones.setdefault(row["geometry_id"], digest) != digest:
                raise ValueError("conflicting S4P identity within one geometry")
            bv, sv = _true(row["broadband_descriptor_valid"]), _true(row["strict_lumped_valid"])
            response = np.asarray([float(row[k]) for k in S_COLUMNS])
            features = np.asarray([float(row[k]) for k in PHYSICAL_COLUMNS])
            if not np.isfinite(response).all() or (bv and not np.isfinite(features).all()) or (sv and not bv):
                raise ValueError("nonfinite S or inconsistent physical validity")
            k = selected_index.get(row["geometry_id"])
            if k is not None:
                row_pins[k][j] = _canonical(row)
                if collect:
                    s[k, j], physical[k, j], broad[k, j], strict[k, j] = response, features, bv, sv
                    if z is not None:
                        z[k, j] = [float(row[key]) for key in Z_COLUMNS]
                    if extra_physical is not None:
                        extra_physical[k, j] = [float(row[key]) for key in extra_fields]
                    for column, key in enumerate(flag_fields):
                        value = row[key]
                        if value not in categories[key]:
                            categories[key].append(value)
                        code = categories[key].index(value)
                        if code >= 32767:
                            raise ValueError("source validity category count exceeds lossless encoding")
                        flag_codes[k, j, column] = code
    if not seen.all() or len(set(touchstones.values())) != len(rows):
        raise ValueError("incomplete 56-row geometry or duplicate S4P under different geometries")
    artifact_seen = set()
    with nullcontext(_csv_rows(files["sparameter_index"], files.get("_part_counts"))) as reader:
        for row in reader:
            gid = row["geometry_id"]
            i = index.get(gid)
            if (i is None or gid in artifact_seen or row["geometry_sha256"] != rows[i]["geometry_sha256"] or
                    row["s4p_sha256"] != touchstones[gid] or row["campaign_contract_fingerprint"] != contract):
                raise ValueError("S-parameter artifact index does not exactly bind geometry/S4P")
            for keys, expected in ((("port_count",), 4), (("frequency_points",), 56),
                                  (("first_frequency_hz", "frequency_start_hz"), 5_000_000_000),
                                  (("last_frequency_hz", "frequency_stop_hz"), 60_000_000_000),
                                  (("frequency_step_hz",), 1_000_000_000)):
                values = [int(row[key]) for key in keys if key in row]
                if not values or any(value != expected for value in values):
                    raise ValueError("artifact port/frequency/DRC contract mismatch")
            if "qa_status" in row:
                accepted = row["qa_status"] == "PASS"
            else:
                accepted = row["emx_status"] == "PASS" and row["calibre_status"] == "PASS" and int(row["calibre_blocking_violations"]) == 0
            if not accepted:
                raise ValueError("artifact lacks EMX/Calibre acceptance")
            artifact_seen.add(gid)
    if artifact_seen != set(index):
        raise ValueError("incomplete S-parameter artifact index")
    evidence = [{"s4p_sha256": touchstones[r["geometry_id"]], "frequency_row_sha256": row_pins[k],
                 "all_56_rows_sha256": _canonical(row_pins[k])} for k, r in enumerate(selected)]
    arrays = {"s": s, "physical_features": physical, "broadband_descriptor_valid": broad,
              "strict_lumped_valid": strict} if collect else None
    if collect and z is not None:
        arrays["z"] = z
    if collect and extra_physical is not None:
        arrays["source_extra_physical"] = extra_physical
        arrays["source_extra_physical_columns"] = np.asarray(extra_fields)
    if collect and flag_codes is not None:
        arrays["source_validity_codes"] = flag_codes
        arrays["source_validity_schema_json"] = np.asarray(json.dumps({"fields": flag_fields, "categories": categories}, sort_keys=True))
    return evidence, arrays


def _splits(selected, contract, seed, previous):
    groups = [_split_for_hash(r["geometry_sha256"], seed) for r in selected]
    previous_pin = None
    if previous is not None:
        previous = Path(previous).resolve()
        old = _json(previous)
        if old.get("seed") != seed or old.get("contract_fingerprint_sha256") != contract:
            raise ValueError("previous split seed/science mismatch")
        for i, row in enumerate(selected):
            gid, digest = row["geometry_id"], row["geometry_sha256"]
            if gid in old.get("geometry_id_to_sha256", {}) and old["geometry_id_to_sha256"][gid] != digest:
                raise ValueError("previous geometry ID changed canonical identity")
            if digest in old["by_geometry_sha256"]:
                prior = SPLIT_NAMES.index(old["by_geometry_sha256"][digest])
                if prior != groups[i]:
                    raise ValueError("previous split remaps the frozen geometry hash rule")
                groups[i] = prior
        previous_pin = _pin(previous)
    counts = {name: groups.count(i) for i, name in enumerate(SPLIT_NAMES)}
    if any(v == 0 for v in counts.values()):
        raise ValueError("empty geometry-grouped split")
    return groups, counts, previous_pin


def freeze_selection(source_manifest, out_dir, previous_splits=None, seed=17):
    """Write an immutable exact-10K selection or a WAITING receipt; no NPZ yet."""
    source_path, output = Path(source_manifest).resolve(), Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    try:
        source, files, evidence = _source(source_path)
        base = {"schema": "bb_exact10k_selection.v1", "requested_geometries": EXACT_COUNT,
                "seed": seed, "evidence": evidence, "training_performed": False,
                "evaluation_performed": False, "producer_modified": False,
                "real_emx_generated": False, "model_test_metrics_accessed": False}
        if files is None:
            result = {**base, "status": "WAITING_FOR_10K",
                      "selected_geometries": 0, "data_materialized": False,
                      "blocker": "Formal checkpoint accepted count is below 10000; no selection fabricated."}
            _write_json(output / "WAITING_RECEIPT.json", result)
            _summary_files(output)
            return result
        rows, bounds, fields = _geometries(files, evidence["formally_accepted_geometries"], evidence["contract_fingerprint_sha256"])
        # v3 section 3 fixes the producer's accepted order, not random ranking.
        selected = rows[:EXACT_COUNT]
        grouped, counts, old_pin = _splits(selected, evidence["contract_fingerprint_sha256"], seed, previous_splits)
        labels, _ = _scan_features(files, rows, selected, evidence["contract_fingerprint_sha256"])
        for row, group, label in zip(selected, grouped, labels):
            row.update(label, split=SPLIT_NAMES[group])
        for pin in evidence["source_files"].values():
            _check_pin(pin, source_path.parent)
        _check_pin(evidence["source_manifest"], source_path.parent)
        result = {**base, "status": "READY_FOR_10K", "selected_geometries": len(selected),
                  "selected_frequency_rows": len(selected) * 56, "data_materialized": False,
                  "selection_method": "FIRST_10000_AUTHORITATIVE_ACCEPTED_SEQUENCE; validated contiguous producer order, frozen once, no label selection",
                  "geometry_field_order": fields, "geometry_bounds": bounds["field_bounds_um"],
                  "frequency_hz": FREQUENCY_HZ.tolist(), "port_contract": source["port_contract"],
                  "split_method": "existing bb56-split-v1 hash thresholds 60/20/20; previous identities cannot remap",
                  "split_counts": counts, "previous_splits": old_pin, "rows": selected,
                  "selected_geometry_set_sha256": _canonical(sorted(r["geometry_sha256"] for r in selected)),
                  "physical_validity": "Preserve every raw feature and both original masks; no invalid-row clipping or label-based subsampling",
                  "implementation": {"snapshot_10k.py": _pin(__file__), "data.py": _pin(Path(__file__).with_name("data.py")),
                      "producer_geometry.py": _pin(Path(__file__).parents[2] / "rfic_transformer_inverse_design/campaigns/broadband56_balanced200k.py"),
                      "producer_progress.py": _pin(Path(__file__).parents[2] / "rfic_transformer_inverse_design/campaigns/broadband56_stage_progress.py")}}
        _write_json(output / "SELECTION_MANIFEST.json", result)
        _summary_files(output)
        return result
    except Exception as exc:
        _write_json(output / "SELECTION_FAILED.json", {"status": "FAIL", "error": str(exc), "source_manifest": str(source_path)})
        _summary_files(output)
        raise


def prepare_selection(selection_manifest, out_dir, expected_selection_sha256):
    """Materialize only a previously frozen selection, preserving all 56 rows."""
    path, output = Path(selection_manifest).resolve(), Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    try:
        _check_pin({"path": str(path), "sha256": expected_selection_sha256}, path.parent)
        selection = _json(path)
        if selection.get("schema") != "bb_exact10k_selection.v1" or selection.get("status") != "READY_FOR_10K" or selection.get("selected_geometries") != EXACT_COUNT:
            raise ValueError("only the exact frozen 10000 selection is eligible")
        for pin in selection["implementation"].values():
            _check_pin(pin, path.parent)
        evidence = selection["evidence"]
        source_path = _check_pin(evidence["source_manifest"], path.parent)
        source, files, observed = _source(source_path)
        if files is None or observed != evidence:
            raise ValueError("original committed source identity changed")
        rows, bounds, fields = _geometries(files, evidence["formally_accepted_geometries"], evidence["contract_fingerprint_sha256"])
        selected = selection["rows"]
        if len(selected) != EXACT_COUNT or len({r["geometry_sha256"] for r in selected}) != EXACT_COUNT:
            raise ValueError("selected geometry count/uniqueness mismatch")
        if [r["source_index"] for r in selected] != list(range(EXACT_COUNT)):
            raise ValueError("selection must use first 10000 authoritative accepted geometries")
        for row in selected:
            original = rows[row["source_index"]]
            if any(row[key] != value for key, value in original.items()):
                raise ValueError("selection row differs from original accepted geometry")
        previous = selection["previous_splits"]
        if previous is not None:
            _check_pin(previous, path.parent)
        grouped, counts, _ = _splits(selected, evidence["contract_fingerprint_sha256"], selection["seed"], previous["path"] if previous else None)
        if counts != selection["split_counts"] or any(r["split"] != SPLIT_NAMES[g] for r, g in zip(selected, grouped)):
            raise ValueError("frozen geometry split changed")
        labels, arrays = _scan_features(files, rows, selected, evidence["contract_fingerprint_sha256"], collect=True)
        if any(any(row[key] != value for key, value in label.items()) for row, label in zip(selected, labels)):
            raise ValueError("selected source frequency rows or flags changed")
        geometry = np.asarray([r["geometry"] for r in selected], dtype=np.float64)
        split = np.asarray(grouped, dtype=np.int8)
        physical = arrays["physical_features"]
        y = physical[..., [PHYSICAL_COLUMNS.index(name) for name in Y_COLUMNS]]
        y_valid = arrays["strict_lumped_valid"][..., None] & np.isfinite(y)
        train = split == 0
        g_min, g_max = geometry[train].min(0), geometry[train].max(0)
        if np.any(g_max <= g_min):
            raise ValueError("constant training geometry dimension")
        s_mean, s_scale = _fit_scale(arrays["s"][train])
        y_mean, y_scale = _fit_scale(y[train], y_valid[train])
        normalizer = {"schema": "bb_normalizer.v1", "fit_split": "train", "training_geometries": counts["train"],
                      "scale_floor": 1e-6, "g_min": g_min.tolist(), "g_max": g_max.tolist(),
                      "s_mean": s_mean, "s_scale": s_scale, "y_mean": y_mean, "y_scale": y_scale,
                      "geometry_fields": fields, "field_names": fields, "s_columns": list(S_COLUMNS), "y_columns": list(Y_COLUMNS),
                      "y_units": ["nH", "nH", "dimensionless", "dimensionless"],
                      "physical_mask_domain": "strict_lumped_valid AND finite_feature",
                      "contract_bounds_um": {"lower": [bounds["field_bounds_um"][f][0] for f in fields],
                                             "upper": [bounds["field_bounds_um"][f][1] for f in fields]}}
        ids, hashes = [r["geometry_id"] for r in selected], [r["geometry_sha256"] for r in selected]
        splits = {"schema": "bb_splits.v1", "seed": selection["seed"], "contract_fingerprint_sha256": evidence["contract_fingerprint_sha256"],
                  "method": selection["split_method"], "counts": counts, "requested_fractions": [0.6, 0.2, 0.2],
                  "by_geometry_sha256": dict(zip(hashes, (SPLIT_NAMES[g] for g in grouped))),
                  "geometry_id_to_sha256": dict(zip(ids, hashes)), "previous_splits": previous,
                  "ids": {name: [ids[i] for i, g in enumerate(grouped) if g == j] for j, name in enumerate(SPLIT_NAMES)}}
        for pin in evidence["source_files"].values():
            _check_pin(pin, path.parent)
        np.savez_compressed(output / "dataset.npz", **arrays, geometry=geometry, geometry_ids=np.asarray(ids),
                            geometry_sha256=np.asarray(hashes), frequency_hz=FREQUENCY_HZ, split=split,
                            s_valid=np.ones_like(arrays["s"], dtype=bool), y=y, y_valid=y_valid,
                            physical_valid=arrays["broadband_descriptor_valid"][..., None] & np.isfinite(physical))
        _write_json(output / "normalizer.json", normalizer)
        _write_json(output / "splits.json", splits)
        _write_json(output / "geometry_provenance.json", {"selection_manifest": _pin(path), "rows": selected})
        artifacts = {name: {"path": name, "sha256": sha256(output / name), "size_bytes": (output / name).stat().st_size}
                     for name in ("dataset.npz", "normalizer.json", "splits.json", "geometry_provenance.json")}
        manifest = {"schema": "bb_data_manifest.v1", "status": "PASS", "source_manifest": evidence["source_manifest"],
                    "selection_manifest": _pin(path), "sources": evidence["source_files"], "producer_evidence": evidence,
                    "contract_fingerprint_sha256": evidence["contract_fingerprint_sha256"],
                    "source_checkpoint_geometries": evidence["formally_accepted_geometries"], "unique_geometries": EXACT_COUNT,
                    "frequency_rows": EXACT_COUNT * 56, "geometry_dim": 10, "geometry_fields": fields,
                    "geometry_field_order": fields, "geometry_bounds": bounds["field_bounds_um"], "geometry_units": "um",
                    "geometry_bounds_source": _pin(files["geometry_bounds"]),
                    "frequency_hz": FREQUENCY_HZ.tolist(), "s_channels": list(S_COLUMNS), "physical_columns": list(PHYSICAL_COLUMNS),
                    "target_columns": list(Y_COLUMNS), "split_counts": counts, "normalizer_fit_split": "train",
                    "s_validity": "All finite full-four-port S rows retained including lumped-invalid frequencies",
                    "physical_validity": selection["physical_validity"], "port_contract": source["port_contract"],
                    "test_usage": "Sealed input preparation only; no model evaluation or test-derived scaling",
                    "gradient_training_performed": False, "artifacts": artifacts}
        _write_json(output / "data_manifest.json", manifest)
        _summary_files(output)
        return manifest
    except Exception as exc:
        _write_json(output / "PREPARATION_FAILED.json", {"status": "FAIL", "error": str(exc), "selection_manifest": str(path)})
        _summary_files(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--source-manifest", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--source-manifest", required=True)
    freeze.add_argument("--out", required=True)
    freeze.add_argument("--previous-splits")
    freeze.add_argument("--seed", type=int, default=17)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--selection-manifest", required=True)
    prepare.add_argument("--expected-selection-sha256", required=True)
    prepare.add_argument("--out", required=True)
    args = parser.parse_args()
    result = (inspect_source_manifest(args.source_manifest) if args.command == "inspect" else
              freeze_selection(args.source_manifest, args.out, args.previous_splits, args.seed) if args.command == "freeze"
              else prepare_selection(args.selection_manifest, args.out, args.expected_selection_sha256))
    display = result if args.command == "inspect" else {k: result[k] for k in ("schema", "status")}
    print(json.dumps(display, sort_keys=True))


if __name__ == "__main__":
    main()
