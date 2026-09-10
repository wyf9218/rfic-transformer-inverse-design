"""Read saved acquisition GDS rejections; no execution, relabeling or admission.

The caller supplies an already frozen subset and the original 256-proposal
batch. MirrorReader verifies consumed bytes and performs the caller's final
read-period check. This adapter neither infers server absence from a partial
mirror nor classifies DRC, solver, resource or unknown failures as GDS failures.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .eucap15_acquisition_evidence import CONFIG_SHA, GEOMETRY_CHECKS, _fields, _require, _same
from .eucap15_selected_evidence import SelectedEvidenceError


def _failed_checks(value, label):
    _require(isinstance(value, list) and value and
             all(isinstance(k, str) and k for k in value), label + " must be a nonempty check list")
    _require(len(set(value)) == len(value), label + " contains duplicate checks")
    return set(value)


def _pin(value, label):
    _require(isinstance(value, dict) and {"path", "sha256", "bytes"} <= value.keys(), label + " pin required")
    path = value["path"]
    _require(isinstance(path, str) and Path(path).is_absolute() and
             ".." not in Path(path).parts and str(Path(path)) == path, label + " canonical path required")
    _require(type(value["bytes"]) is int and value["bytes"] >= 0 and
             isinstance(value["sha256"], str) and len(value["sha256"]) == 64 and
             set(value["sha256"]) <= set("0123456789abcdef"), label + " invalid SHA/size")
    return value


def _normalized_entry(item, proposal):
    _fields(item, {k: proposal[k] for k in ("candidate_id", "request_id", "q_proxy")}, "Frozen candidate")
    _require(proposal["local_dispatch_eligible"] is True and proposal["analytic_pass"] is True,
             "Held candidate is not a native input")
    _require(item["evidence_status"] in ("CANDIDATE_PHYSICAL_CHAIN_BOUND", "FAILURE_EVIDENCE_BOUND"),
             "Unknown terminal")
    if item["evidence_status"] == "FAILURE_EVIDENCE_BOUND":
        _fields(item, dict(original_status="CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION",
                          failure_stage="ACTUAL_GDS_AUDIT", original_error="ACTUAL_GDS_AUDIT_REJECTED"),
                "GDS rejection identity")
        _failed_checks(item["failed_checks"], "Export failed_checks")
        _require(not any(k in item for k in ("feature", "s4p", "solver")),
                 "GDS failure carries contradictory physical output")
        if "geometry_sha256" in item:
            _fields(item, {"geometry_sha256": proposal["canonical_geometry_sha256"]}, "Failure geometry")
        return None
    _fields(item, dict(original_status="FRESH_EMX_EXTRACTED",
                      geometry_sha256=proposal["canonical_geometry_sha256"], production_accepted=False),
            "Closed physical identity")
    _require(type(item["strict_valid_source_flag"]) is bool and
             type(item["core15_eligible_source_flag"]) is bool, "Exact validity flags required")
    return dict(item, result=item["original_result"], arm=proposal["arm"],
                strict_valid=item["strict_valid_source_flag"], core_eligible=item["core15_eligible_source_flag"])


def normalized_entry(item, proposal):
    """Return the unchanged success-entry mapping, or None for a named GDS rejection."""
    try:
        return _normalized_entry(item, proposal)
    except (KeyError, TypeError, AttributeError) as exc:
        raise SelectedEvidenceError("Malformed acquisition terminal: " + str(exc)) from exc


def _failure_row(reader, item, proposal, batch, index):
    _require(_normalized_entry(item, proposal) is None, "Failure branch requires GDS rejection")
    cid = proposal["candidate_id"]
    candidate_sha = hashlib.sha256(cid.encode()).hexdigest()
    geometry_sha = proposal["canonical_geometry_sha256"]
    _require(cid in batch["rows"] and _same(batch["rows"][cid], proposal), "Proposal is not the frozen batch row")
    result_pin = _pin(item["original_result"], "RESULT")
    terminal = reader.document(result_pin)
    _fields(terminal, dict(status=item["original_status"], request_id=proposal["request_id"],
                          candidate_id=cid, q_proxy=proposal["q_proxy"], error=item["original_error"]), "Rejected RESULT")
    _require(not any(k in terminal and terminal[k] is not None for k in ("feature", "s4p", "solver", "actual")),
             "Rejected RESULT carries contradictory physical output")
    base = Path(result_pin["path"]).parent
    _require(base.name == proposal["request_id"] and Path(result_pin["path"]).name == "RESULT.json",
             "Failure path identity")
    audit_path = str(base / "gds_audit/REQUEST_GDS_AUDIT.json")
    _require(audit_path in index, "Unpinned failure audit")
    audit_pin = _pin(index[audit_path], "Request audit")
    _require(audit_pin["path"] == audit_path, "Audit index path differs")
    audit = reader.document(audit_pin)
    context = dict(request_id=proposal["request_id"], frequency_ghz=15, q_proxy=proposal["q_proxy"],
                   model_id="ACQUISITION_RECIPE_BOUND_NOT_MODEL_INFERRED_HERE", dataset_scope="DEVELOPMENT_ACQUISITION_PAIR",
                   target_source=proposal["source"], arm=proposal["arm"], arm_order=proposal["arm_order"],
                   global_order=proposal["global_order"])
    _fields(audit, dict(schema="eucap15_acquisition_gds_audit.v1", request=context,
                       N_logical=1, N_audit_attempted=1), "Failure GDS audit")
    sources = audit["source_pins"]
    _require(isinstance(sources, dict) and set(sources) == {"manifest", "recipe", "proposals", "private_config"},
             "Failure audit source closure differs")
    _fields(sources, {k: batch[k] for k in ("manifest", "recipe", "proposals")}, "Frozen failure source")
    _require(_pin(sources["private_config"], "Config")["sha256"] == CONFIG_SHA, "Failure config changed")
    for source in sources.values():
        reader.read(source)
    _require(isinstance(audit["records"], list) and len(audit["records"]) == 1, "Failure must bind one candidate")
    record = audit["records"][0]
    _fields(record, dict(candidate_id=cid, candidate_id_sha256=candidate_sha,
                        candidate_geometry_identity_sha256=geometry_sha, analytic_grid=True, status="FAIL",
                        audit_attempted=True, cadence_routed=True, calibre_eligible=False), "Rejected candidate")
    expected_failed = _failed_checks(item["failed_checks"], "Export failed_checks")
    _require(_failed_checks(record["failed_checks"], "Record failed_checks") == expected_failed,
             "Record failed checks differ")
    original_record = record["original_record"]
    _fields(original_record, proposal, "Original failure proposal")
    if set(original_record) != set(proposal):
        # The original acquisition candidate_context adds exactly these five
        # aliases/identities; never accept arbitrary or partially derived extras.
        expected_record = dict(proposal, grid_geometry=proposal["geometry"], grid_proxy=proposal["proxy"],
                               analytic_grid=proposal["analytic_pass"], candidate_id_sha256=candidate_sha,
                               candidate_geometry_identity_sha256=geometry_sha)
        _require(_same(original_record, expected_record), "Derived failure proposal differs")
    gds, port, geometry_pin = (_pin(record[k], k) for k in ("gds", "port_manifest", "geometry_audit"))
    _require(len({p["path"] for p in (gds, port, geometry_pin)}) == 3, "GDS/port/audit paths alias")
    for source in (gds, port, geometry_pin):
        _require(source["path"] in index and _same(index[source["path"]], source),
                 "Failure artifact not bound by source index")
    geometry = reader.document(geometry_pin)
    _fields(geometry, dict(overall_status="FAIL", candidate_id_sha256=candidate_sha,
                           candidate_geometry_identity_sha256=geometry_sha,
                           gds_path=gds["path"], gds_sha256=gds["sha256"]), "Actual GDS rejection")
    checks = geometry["checks"]
    _require(isinstance(checks, dict) and set(GEOMETRY_CHECKS) <= set(checks), "Mandatory geometry check missing")
    _require(all(isinstance(k, str) and k and type(v) is bool for k, v in checks.items()),
             "Geometry checks must be native booleans")
    _require({k for k, v in checks.items() if v is False} == expected_failed, "Actual False checks differ")
    artifacts = geometry["original_artifacts"]
    _require(isinstance(artifacts, list) and artifacts, "Geometry original_artifacts missing")
    for source in artifacts:
        _pin(source, "Geometry source")
    _require(len({p["path"] for p in artifacts}) == len(artifacts), "Duplicate geometry source path")
    _require(any(_same(gds, p) for p in artifacts) and any(_same(port, p) for p in artifacts),
             "Same GDS and port manifest must be geometry-audited")
    for source in artifacts:
        reader.read(source)
    return dict(candidate_id=cid, request_id=proposal["request_id"], arm=proposal["arm"],
                arm_order=proposal["arm_order"], global_order=proposal["global_order"], source=proposal["source"],
                geometry_sha256=geometry_sha, q_proxy=proposal["q_proxy"], state="GDS_FAIL", actual=None,
                strict_valid=None, core_eligible=None, descriptor_valid=None, physics_qa_pass=None,
                below_half_srf=None, q10_to20_supported=None, strict_joint_hit=None,
                target_errors_defined=False, emx_minus_target=None, emx_minus_proxy=None,
                target=proposal["target"], frozen_proxy=proposal["proxy"], feature=None, s4p=None,
                original_result=result_pin, failure_stage=item["failure_stage"], failed_checks=list(item["failed_checks"]),
                error=item["original_error"], gds_audit=audit_pin, evidence_class="PRIOR_PUBLISHED_GDS_REJECTION",
                physical_numbers_available=False)


def failure_row(reader, item, proposal, batch, index):
    """Verify one saved rejection; caller retains denominator and final read-period recheck."""
    try:
        return _failure_row(reader, item, proposal, batch, index)
    except (KeyError, TypeError, AttributeError) as exc:
        raise SelectedEvidenceError("Malformed acquisition GDS rejection: " + str(exc)) from exc
