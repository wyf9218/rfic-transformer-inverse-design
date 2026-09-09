"""Verify a closed single-q_proxy native feature chain without running physics.

Physical artifacts can be read at their recorded paths or through an optional
exact physical_path_map. Recorded identities/structure never use mirror paths.
Only frozen pilot-source relocations use ctx.path_map. S4P is hashed, not parsed
or re-extracted; the exact56 CSV and original fifteen-GHz receipt are reconciled.
There are no writes, subprocesses, network, model or dataset imports.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path

from .frequency_physical_statistics import clean, pin, verified_read
from .io import canonical_sha

FREQUENCIES = [n * 10**9 for n in range(5, 61)]
PHYSICAL_FIELDS = ("lp_nh", "ls_nh", "qmin", "k_abs")
FLAG_FIELDS = ("finite_values", "positive_primary_resistance", "positive_secondary_resistance",
               "positive_primary_inductive_reactance", "positive_secondary_inductive_reactance")
FEATURE_SCHEMA = "eucap15_selected_fresh_features.v1"
PROOF_SCHEMA = "eucap15_selected_emx_preflight.v1"


class SelectedEvidenceError(ValueError):
    """A claimed completed feature chain is incomplete, inconsistent or changed."""


def _require(condition, message):
    if not condition:
        raise SelectedEvidenceError(message)


def _strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "duplicate JSON receipt key: " + key)
            result[key] = value
        return result

    def invalid_constant(value):
        raise SelectedEvidenceError("nonstandard JSON constant: " + value)

    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    # Also rejects numeric overflow such as 1e999, not only NaN/Infinity tokens.
    _require(_same(value, clean(value)), "nonfinite JSON numbers must be explicit null")
    return value


def _same(actual, expected):
    if isinstance(expected, bool):
        return type(actual) is bool and actual is expected
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(
            _same(actual[k], v) for k, v in expected.items())
    if isinstance(expected, (list, tuple)):
        return isinstance(actual, (list, tuple)) and len(actual) == len(expected) and all(
            _same(a, b) for a, b in zip(actual, expected))
    if expected is None:
        return actual is None
    if isinstance(actual, bool):
        return False
    return actual == expected


def _fields(value, expected, label):
    _require(isinstance(value, dict), label + " must be an object")
    for name, wanted in expected.items():
        _require(name in value and _same(value[name], wanted), label + " mismatch: " + name)


def _path(value):
    _require(isinstance(value, str), "pin path must be text")
    path = Path(value)
    _require(path.is_absolute() and ".." not in path.parts and str(path) == value,
             "absolute canonical artifact path required")
    _require(not any(p.is_symlink() for p in (path, *path.parents)), "symlink artifact forbidden")
    return path


def _boolean_text(value, label):
    _require(isinstance(value, str) and value.lower() in ("true", "false"), label + " must be a saved boolean")
    return value.lower() == "true"


def _numeric_text(value, label):
    _require(isinstance(value, str) and value != "", label + " must retain its numeric CSV value")
    try:
        return float(value)
    except ValueError as error:
        raise SelectedEvidenceError(label + " is not numeric") from error


def _original_row_matches_csv(original, csv_row):
    _require(isinstance(original, dict) and set(original) == set(csv_row),
             "original15 row/CSV columns differ")
    for key, value in original.items():
        text = csv_row[key]
        if value is None:
            # Native JSON cleans NaN/Inf to null; CSV retains nonfinite tokens.
            _require(text == "" or not math.isfinite(_numeric_text(text, key)),
                     "null original field differs from retained nonfinite CSV: " + key)
        elif isinstance(value, bool):
            _require(_boolean_text(text, key) == value, "original boolean differs: " + key)
        elif isinstance(value, (int, float)):
            _require(math.isfinite(value) and _numeric_text(text, key) == value,
                     "original numeric field differs: " + key)
        else:
            _require(isinstance(value, str) and text == value, "original text field differs: " + key)


def inspect_features(entry, item, ctx, *, physical_path_map=None):
    """Return one verified completed selected candidate, or fail closed.

    Caller owns PENDING status for requests with no closed feature manifest.
    Merely supplying a missing or incomplete manifest never creates a terminal.
    ctx is one previously validated complete pilot64 context; it may have been
    loaded for another request. This function never uses ctx.selected to choose.

    physical_path_map, when supplied, is an exact recorded absolute path ->
    mirror absolute path mapping. Every needed path must have an explicit key;
    there is no local fallback, prefix replacement, or mutation of native pins.
    Frozen-source ctx.path_map relocation still happens first. Resolution only
    chooses where bytes are read; native path/identity comparisons stay original.
    resolution_evidence records both pins (identical in the no-map case).
    """
    try:
        return _inspect_features(entry, item, ctx, physical_path_map=physical_path_map)
    except SelectedEvidenceError:
        raise
    except (ValueError, OSError, KeyError, TypeError, IndexError, OverflowError) as error:
        raise SelectedEvidenceError("Invalid completed selected evidence: " + str(error)) from error


def _inspect_features(entry, item, ctx, *, physical_path_map=None):
    evidence, resolutions = {}, {}
    _require(physical_path_map is None or isinstance(physical_path_map, dict),
             "physical_path_map must be an exact path mapping or None")
    # Snapshot the supplied lookup; a caller cannot redirect a later read by
    # mutating its mapping after the first artifact has been checked.
    physical_paths = None if physical_path_map is None else dict(physical_path_map)
    # Validate only consumed keys. A campaign-wide mirror can have thousands of
    # unrelated entries; rechecking their paths per candidate adds no evidence.

    def resolved_path(original_path):
        original = _path(original_path)
        if physical_paths is None:
            return original
        _require(original_path in physical_paths,
                 "physical_path_map missing exact original path: " + original_path)
        return _path(physical_paths[original_path])

    def resolved_pin(original):
        # checked() has already validated these exact bytes and recorded the
        # resolution; do not replace the caller's original historical pin.
        return resolutions[original["path"]]["resolved"]

    def checked(expected):
        _require(isinstance(expected, dict) and {"path", "sha256", "bytes"} <= set(expected),
                 "exact artifact pin required")
        path = _path(expected["path"])
        _require(type(expected["bytes"]) is int and expected["bytes"] >= 0 and
                 isinstance(expected["sha256"], str) and len(expected["sha256"]) == 64 and
                 set(expected["sha256"]) <= set("0123456789abcdef"), "invalid artifact SHA/bytes")
        original = {k: expected[k] for k in ("path", "sha256", "bytes")}
        resolved = resolved_path(str(path))
        actual = pin(resolved)
        _require(_same(actual, {"path": str(resolved), "sha256": original["sha256"],
                               "bytes": original["bytes"]}), "artifact SHA/size mismatch")
        previous = evidence.get(original["path"])
        _require(previous is None or previous == original, "conflicting pin for one artifact path")
        resolution = {"original": original, "resolved": actual}
        _require(original["path"] not in resolutions or resolutions[original["path"]] == resolution,
                 "conflicting physical resolution for one artifact path")
        evidence[original["path"]] = original
        resolutions[original["path"]] = resolution
        return original

    def document(expected):
        actual = checked(expected)
        raw = resolved_path(actual["path"]).read_bytes()
        _require(len(raw) == actual["bytes"] and hashlib.sha256(raw).hexdigest() == actual["sha256"],
                 "JSON receipt changed during read")
        value = _strict_json(raw)
        checked(actual)
        return value

    def frozen(expected):
        # Explicit mappings already validated by load_selected_context; no prefix guessing.
        actual = dict(expected)
        actual["path"] = ctx.path_map.get(expected["path"], expected["path"])
        return checked(actual)

    _require(isinstance(entry, dict) and set(entry) == {"request_id", "feature_manifest"},
             "entry requires exactly request_id and closed feature_manifest")
    _require(entry["request_id"] == item["request_id"], "publication entry/request mismatch")
    _require(document(ctx.manifest_pin) == ctx.manifest and document(ctx.freeze_pin) == ctx.freeze,
             "previously validated pilot context changed")
    _fields(ctx.manifest, {"schema": "eucap15_selected_candidate_handoff.v1", "N_requests": 64,
        "N_proxy_candidates": 704, "research_phase": "DEVELOPMENT_PILOT_NOT_FINAL10K",
        "frequency_ghz": 15, "dataset_scope": "FORMAL_10K", "label_mode": "STRICT_LUMPED"}, "pilot")
    _require(len(ctx.manifest["requests"]) == 64 and
             sum(candidate == item for candidate in ctx.manifest["requests"]) == 1,
             "item is not a unique unchanged member of original64")
    reference = document(ctx.reference_pin)
    _fields(reference, {"schema": "eucap15_reference_identity.v1", "reference_label": "FORMAL10K_REFERENCE",
        "status": "REFERENCE_LOADED_NOT_FINAL", "source_snapshot_geometries": 10000,
        "frequency_ghz": 15, "model_id": ctx.manifest["model_id"], "label_mode": "STRICT_LUMPED"}, "reference")
    records_pin = frozen(item["source_records"])
    records = verified_read(resolved_pin(records_pin), jsonl=True)
    _require(len(records) == 11 and [r["q_target"] for r in records] == list(range(10, 21)),
             "original eleven source slots changed")
    chosen = [r for r in records if r["candidate_id"] == item["candidate_id"]]
    _require(len(chosen) == 1, "preselected original candidate missing or duplicated")
    chosen = chosen[0]
    _fields(chosen, {"request_id": item["request_id"], "q_target": item["q_proxy"],
        "q_proxy": item["q_proxy"], "proxy_preselected": True, "analytic_grid": True,
        "model_id": ctx.manifest["model_id"], "frequency_ghz": 15, "dataset_scope": "FORMAL_10K",
        "target_source": "DEVELOPMENT_UNIFORM_TRIPLE", "evidence_source": "SELF_PROXY",
        "emx_status": "NOT_RUN", "actual_response": None}, "frozen selected")
    _require(item["selected_analytic_pass"] is True and
             records[item["record_line_number"]-1] == chosen and
             canonical_sha(chosen) == item["source_record_canonical_sha256"],
             "selected source line/hash/analytic eligibility differs")
    wanted, proxy = chosen["target"], chosen["grid_proxy"]
    _require(len(wanted) == len(proxy) == 4 and
             all(type(v) in (int, float) and math.isfinite(v) for v in wanted + proxy)
             and all(v > 0 for v in wanted), "original selected target/proxy must be finite")
    _require(item["selected_target"] == wanted and item["selected_grid_proxy"] == proxy and
             item["candidate_geometry_identity_sha256"] == chosen["candidate_geometry_identity_sha256"],
             "handoff selected target/proxy/geometry differs")
    candidate_id = chosen["candidate_id"]
    geometry_sha = chosen["candidate_geometry_identity_sha256"]
    candidate_sha = hashlib.sha256(candidate_id.encode()).hexdigest()
    scale = ctx.freeze["protocol"]["score_scale"]
    tau = ctx.freeze["executed_legacy_tolerance_float64"]
    _require(len(scale) == len(tau) == 4 and all(math.isfinite(v) and v > 0 for v in scale + tau),
             "frozen executed scale/tolerance invalid")

    manifest_pin = checked(entry["feature_manifest"])
    feature_dir = _path(manifest_pin["path"]).parent
    solver_root = feature_dir.parent
    _require(Path(manifest_pin["path"]).name == "MANIFEST.json" and feature_dir.name == "features",
             "native closed features/MANIFEST.json required")
    feature_manifest = document(manifest_pin)
    _fields(feature_manifest, {"inputs_unchanged": True}, "feature manifest")
    artifacts = feature_manifest["artifacts"]
    _require(len(artifacts) == 2 and
             {Path(p["path"]).name for p in artifacts} == {"FEATURE_RECEIPT.json", "features_56.csv"},
             "native feature manifest must contain receipt and exact56 CSV")
    by_name = {}
    for artifact in artifacts:
        actual = checked(artifact)
        _require(Path(actual["path"]).parent == feature_dir, "feature artifact escaped native feature directory")
        by_name[Path(actual["path"]).name] = actual
    feature = document(by_name["FEATURE_RECEIPT.json"])
    context_fields = {"candidate_id": candidate_id, "model_id": ctx.manifest["model_id"],
        "dataset_scope": "FORMAL_10K", "frequency_ghz": 15, "q_requested": item["q_proxy"],
        "q_proxy": item["q_proxy"], "q_emx": None}
    selected_fields = {"physical_selection": "Q_PROXY_ONLY", "selected_manifest": ctx.manifest_pin,
        "reference": ctx.reference_pin, "original_request_denominator": 64,
        "unselected_physical_status": "NOT_REQUESTED_MAIN_PILOT"}
    _fields(feature, {**context_fields, **selected_fields, "schema": FEATURE_SCHEMA,
        "status": "PASS_EXTRACTION", "target": wanted, "proxy_self": proxy, "score_scale": scale,
        "absolute_hit_tolerances": tau, "q_optimum_status": "NOT_EVALUATED_MAIN_PRESELECTED_CANDIDATE",
        "production_membership": False}, "feature")
    proof_pin = checked(feature["preflight"])
    solver_pin = checked(feature["solver_receipt"])
    _require(Path(proof_pin["path"]) == solver_root/"PREFLIGHT.json" and
             Path(solver_pin["path"]) == solver_root/"SOLVER_RECEIPT.json",
             "feature refers to another native output tree")
    proof = document(proof_pin)
    _fields(proof, {**context_fields, **selected_fields, "schema": PROOF_SCHEMA, "status": "PASS",
        "request_id": item["request_id"], "candidate_id_sha256": candidate_sha, "geometry_sha256": geometry_sha,
        "original_record": chosen, "protocol": ctx.freeze["protocol"], "executed_hit_tolerances": tau,
        "frequency_grid_hz": FREQUENCIES, "port_order": ["P001", "P002", "P003", "P004"],
        "port_permutation": [0, 1, 3, 2], "reference_ohm": 50,
        "full11_physical_optimum": "NOT_EVALUATED_SINGLE_PRESELECTED_CANDIDATE",
        "production_membership": False, "output": str(solver_root)}, "preflight")
    source_pins = [checked(p) for p in proof["source_pins"]]
    _require(len({p["path"] for p in source_pins}) == len(source_pins), "duplicate preflight source path")
    request = document(proof["request"])
    _fields(request, {**{k: v for k, v in context_fields.items() if k != "q_emx"},
        "schema": "eucap15_selected_emx_request.v1", "request_id": item["request_id"],
        "target_source": "DEVELOPMENT_UNIFORM_TRIPLE", "production_campaign_membership": False,
        "records": records_pin, "qscan_freeze": ctx.freeze_pin}, "native selected request")
    _require(frozen(request["selected_manifest"]) == ctx.manifest_pin,
             "native request selected manifest relocation differs")
    required_sources = [proof["request"], request["records"], request["qscan_freeze"],
        request["gds_audit"], request["calibre_index"], request["private_config"],
        ctx.manifest_pin, ctx.reference_pin, proof["gds"], proof["port_manifest"], proof["calibre"]]
    _require(all(checked(p) in source_pins for p in required_sources),
             "preflight omitted required original/native source pin")
    audit = document(request["gds_audit"])
    _fields(audit, {"schema": "eucap15_selected_request_gds_audit.v1", "physical_selection": "Q_PROXY_ONLY",
        "selected_candidate_id": candidate_id, "N_selected": 1, "N_audit_attempted": 1}, "selected GDS audit")
    _fields(audit["source_pins"], {"eleven_records": records_pin, "qscan_freeze": ctx.freeze_pin,
        "selected_manifest": ctx.manifest_pin, "reference": ctx.reference_pin,
        "private_config": request["private_config"]}, "GDS source chain")
    audited = audit["records"]
    _require(len(audited) == 11 and {r["candidate_id"] for r in audited} == {r["candidate_id"] for r in records},
             "GDS original eleven accounting changed")
    for row in audited:
        if row["candidate_id"] == candidate_id:
            _fields(row, {"status": "PASS", "candidate_id_sha256": candidate_sha,
                "candidate_geometry_identity_sha256": geometry_sha, "gds": proof["gds"],
                "port_manifest": proof["port_manifest"]}, "selected GDS identity")
        else:
            _fields(row, {"status": "NOT_REQUESTED_MAIN_PILOT", "audit_attempted": False,
                "cadence_routed": False, "calibre_eligible": False}, "unselected physical status")
    expected_statuses = [{"candidate_id": r["candidate_id"], "status": r["status"]} for r in audited]
    _require(proof["original_candidate_statuses"] == feature["original_candidate_statuses"] == expected_statuses,
             "feature/preflight original candidate statuses differ")
    drc = document(proof["calibre"])
    _fields(drc, {"overall_status": "PASS", "blocking_drc_violation_count": 0,
        "drc_scope": "foundry_macro_ip_back_end",
        "candidate_id_sha256": candidate_sha, "candidate_geometry_identity_sha256": geometry_sha,
        "gds_path": proof["gds"]["path"], "gds_sha256": proof["gds"]["sha256"]}, "same-GDS Calibre")
    _require(drc["checks"] and all(v is True for v in drc["checks"].values()), "Calibre checks not all PASS")
    solver = document(solver_pin)
    _fields(solver, {"schema": "frequency_research_fresh_solver.v1", "status": "PASS",
        "candidate_id": candidate_id, "preflight": proof_pin, "source_gds_before": proof["gds"],
        "source_gds_after": proof["gds"], "real_emx": True, "production_modified": False, "q_emx": None}, "fresh solver")
    solver_artifacts = [checked(p) for p in solver["artifacts"]]
    _require(len({p["path"] for p in solver_artifacts}) == len(solver_artifacts), "duplicate solver artifact path")
    solve_dir = solver_root/"solve"
    _require(all(Path(p["path"]).is_relative_to(solve_dir) for p in solver_artifacts), "solver artifact escaped original solve directory")
    touchstone = checked(solver["touchstone"])
    _require(touchstone in solver_artifacts and Path(touchstone["path"]).is_relative_to(solve_dir)
             and Path(touchstone["path"]).suffix == ".s4p" and touchstone["bytes"] > 0,
             "own original S4P missing from solver artifact closure")
    commands = [p for p in solver_artifacts if Path(p["path"]).name == "emx_command.json"]
    _require(len(commands) == 1 and document(commands[0]) == proof["command"],
             "completed solver command differs from preflight")

    csv_pin = by_name["features_56.csv"]
    raw_csv = resolved_path(csv_pin["path"]).read_bytes()
    _require(hashlib.sha256(raw_csv).hexdigest() == csv_pin["sha256"] and len(raw_csv) == csv_pin["bytes"],
             "CSV changed during read")
    reader = csv.DictReader(io.StringIO(raw_csv.decode("utf-8"), newline=""))
    rows = list(reader)
    _require(reader.fieldnames and len(set(reader.fieldnames)) == len(reader.fieldnames) and len(rows) == 56,
             "exact56 feature CSV/columns required")
    _require([int(r["frequency_hz"]) for r in rows] == FREQUENCIES, "feature CSV is not exact5..60GHz step1")
    _fields(feature["original_56_summary"], {"port_count": 4, "frequency_points": 56,
        "frequency_start_hz": FREQUENCIES[0], "frequency_stop_hz": FREQUENCIES[-1],
        "frequency_step_hz": 10**9}, "original56 summary")
    row = rows[10]
    _original_row_matches_csv(feature["original_frequency_row"], row)
    actual_raw = [_numeric_text(row[name], name) for name in PHYSICAL_FIELDS]
    # Reconcile saved scalar labels with the original extractor's explicit
    # qp/qs/signed_k contract, without deriving anything again from S-parameters.
    qp, qs = (_numeric_text(row[name], name) for name in ("qp", "qs"))
    expected_qmin = min(qp, qs) if math.isfinite(qp) and math.isfinite(qs) else math.nan
    signed_k = _numeric_text(row["signed_k"], "signed_k")
    _require(_same(clean(actual_raw[2]), clean(expected_qmin)),
             "stored qmin differs from finite qp/qs minimum contract")
    _require(_same(clean(actual_raw[3]), clean(abs(signed_k))),
             "stored k_abs differs from absolute signed_k contract")
    actual = clean(actual_raw)
    descriptor = _boolean_text(row["broadband_descriptor_valid"], "descriptor")
    strict = _boolean_text(row["strict_lumped_valid"], "strict")
    _require(descriptor == all(_boolean_text(row[name], name) for name in FLAG_FIELDS),
             "stored descriptor disagrees with its original predicates")
    _require(strict == (descriptor and _boolean_text(row["below_half_srf"], "below_half_srf")),
             "stored strict flag disagrees with descriptor/SRF predicate")
    physics = row["passivity_status"] == row["reciprocity_status"] == "PASS"
    errors = [a-t for a, t in zip(actual_raw, wanted)]
    proxy_errors = [a-p for a, p in zip(actual_raw, proxy)]
    hits = [math.isfinite(e) and abs(e) <= t for e, t in zip(errors, tau)]
    finite = all(math.isfinite(a) for a in actual_raw)
    valid = bool(finite and descriptor and strict and physics)
    score = math.sqrt(sum((e/s)**2 for e, s in zip(errors, scale))/4) if finite else None
    _fields(feature, {"actual_fresh_emx": actual, "emx_minus_target": clean(errors),
        "emx_minus_proxy": clean(proxy_errors), "normalized_response_score": score,
        "within_tolerance": hits, "joint_response_hit": all(hits),
        "descriptor_valid": descriptor, "strict_lumped_valid": strict, "physics_qa_pass": physics,
        "valid_for_strict_comparison": valid, "strict_joint_hit": bool(all(hits) and valid),
        "target_relative_signed_percent": clean([100*e/t for e,t in zip(errors,wanted)]),
        "target_relative_absolute_percent": clean([100*abs(e)/t for e,t in zip(errors,wanted)])},
        "recomputed original15 feature")
    # Reverify the closure after all parsing. Never turn a changed source into a
    # successful statistic or silently fetch a replacement.
    for artifact in list(evidence.values()):
        checked(artifact)
    return {"actual": actual, "valid_for_strict_comparison": valid,
        "strict_joint_hit": bool(all(hits) and valid), "evidence_pins": list(evidence.values()),
        "resolution_evidence": list(resolutions.values()),
        "geometry_sha": geometry_sha, "touchstone_sha": touchstone["sha256"],
        "status": "STRICT_VALID" if valid else "EMX_INVALID"}
