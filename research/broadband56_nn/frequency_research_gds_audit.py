"""Research-only audit of one frozen Q10..20 request's *existing* Cadence GDS.

This is not a launcher: no layout generation, repairs, Calibre, EMX, models,
production campaign membership, or source-tree writes.  The physical checks and
low-level calls are those of the successful first15 ``research_gds_audit_v2``.
Only request identity, routing, no-clobber publication and accounting are new.
Run with ``python -B -m research.broadband56_nn.frequency_research_gds_audit``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    GEOMETRY_FIELDS, canonical_geometry_sha256,
)
from .io import canonical_sha, read_json, save_json, sha256, utc_now

REQUEST_SCHEMA = "frequency_research_gds_audit_request.v1"
RECEIPT_SCHEMA = "frequency_research_request_gds_audit.v1"
CONTEXT_FIELDS = ("request_id", "frequency_ghz", "model_id", "dataset_scope",
                  "q_proxy", "target_source")
CORE_MODULES = {
    "canonical_geometry": "rfic_transformer_inverse_design.campaigns.broadband56_balanced200k",
    "gds_identity": "rfic_transformer_inverse_design.campaigns.broadband56_gds_identity",
    "foundry_audit": "rfic_transformer_inverse_design.layout.foundry_audit",
    "port_ground_metrics": "rfic_transformer_inverse_design.layout.port_ground_metrics",
    "api": "rfic_transformer_inverse_design.api",
}
# Only paths/request identities may vary. These are the first15-v2 physical
# reference bytes, not a new or relaxed foundry contract.
CONFIG_SHA256 = "431ad59c22df2471746ab5a2eb73a3a7a6484fa511b71c9d6e6c93fb4b3d04d7"
FOUNDRY_CONTRACT_SHA256 = "8398134bac76ffc4d17b89a2d4a6a2605f29f129c0be82442279ce7046e06a45"
PROVEN_CORE_SHA256 = {
    "canonical_geometry": "bd82390aa822a6426ad505e99675694be2fc49e017b25c76b7c93fc78121d424",
    "gds_identity": "172a59c825ae01fd8b25ed033cec32864943f427a35986af48d872ca319c876e",
    "foundry_audit": "18345730dab23fbdf4a88dfab227622a889ca043122089652f68db6a38eb04c3",
    "port_ground_metrics": "53c9fc68aaeaf784d9071b6fb53a0d02d54e7ffeb9be8b894dd894f983c3489d",
    "api": "44fb94d8b2600c97cebe0a7e744955fc570bd27f25eefe44d325e414622ef275",
}
CALIBRE_FIELDS = ("candidate_id_sha256", "candidate_geometry_identity_sha256",
                  "gds_path", "gds_sha256", "gds_timestamp_normalized_sha256",
                  "gds_timestamp_normalization_algorithm", "geometry_audit_path", "top_cell")


class AuditInputError(ValueError):
    """A frozen input, identity or routing gate failed (never repaired)."""


def require(condition, message):
    if not condition:
        raise AuditInputError(message)


def pin(path):
    path = Path(path).resolve(strict=True)
    require(path.is_file(), "pin is not a file: " + str(path))
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def verify_pin(expected):
    require(isinstance(expected, dict), "file pin must be an object")
    require(set(("path", "sha256", "bytes")) <= set(expected), "pin requires path/sha256/bytes")
    require(Path(expected["path"]).is_absolute(), "pin path must be absolute")
    actual = pin(expected["path"])
    require(actual["sha256"] == expected["sha256"] and actual["bytes"] == expected["bytes"],
            "file pin mismatch: " + str(expected["path"]))
    return actual


def _inside(root, relative):
    relative = Path(relative)
    require(not relative.is_absolute() and ".." not in relative.parts, "unsafe relative route")
    path = (root / relative).resolve()
    require(path.is_relative_to(root), "route escapes Cadence root")
    return path


def _parameter_hash(record):
    values = np.asarray(record["grid_geometry"], dtype=float)
    if not np.isfinite(values).all():
        return None
    # Exact source-Qscan parameter-vector hash, not actual GDS identity.
    return canonical_sha({"fields": record["geometry_fields"], "units": "um",
                          "values": np.round(values, 12).tolist(),
                          "identity_scope": "parameter_vector_only_not_actual_GDS"})


def load_request(request_path):
    """Validate only frozen metadata; never load a model or read/audit GDS."""
    require(__debug__ and sys.flags.optimize == 0, "Python optimization is forbidden for legacy audit cores")
    sys.dont_write_bytecode = True
    request_pin = pin(request_path)
    value = read_json(request_path)
    require(value.get("schema") == REQUEST_SCHEMA, "wrong request schema")
    require(value.get("production_campaign_membership") is False, "research membership must be false")
    context = value["request"]
    require(all(k in context for k in CONTEXT_FIELDS), "incomplete request context")
    require(isinstance(context["request_id"], str) and context["request_id"], "empty request_id")
    require(isinstance(context["model_id"], str) and context["model_id"], "empty model_id")
    require(type(context["frequency_ghz"]) in (int, float) and context["frequency_ghz"] in range(5, 21),
            "frequency must be an integer GHz in 5..20")
    require(context["dataset_scope"] in ("DEVELOPMENT_5K_NOT_FORMAL_10K", "FORMAL_10K"),
            "unsupported dataset scope; cannot infer a research tier")
    require(context["target_source"] in ("HELDOUT_TRIPLE_AUDIT", "RANDOM_LHS_TRIPLE"),
            "unknown target source")
    require(context["q_proxy"] is None or type(context["q_proxy"]) is int and 10 <= context["q_proxy"] <= 20,
            "invalid frozen q_proxy")
    sources = {k: verify_pin(value["source_pins"][k]) for k in ("eleven_records", "qscan_freeze")}
    freeze = read_json(sources["qscan_freeze"]["path"])
    require(freeze.get("schema") == "frequency_qscan_freeze.v1" and
            freeze.get("status") == "FROZEN_BEFORE_QSCAN_AND_NEW_EMX", "Qscan is not frozen")
    for actual, expected, name in (
        (freeze["frequency_ghz"], context["frequency_ghz"], "frequency"),
        (freeze["model_id"], context["model_id"], "model"),
        (freeze["config"]["dataset_scope"], context["dataset_scope"], "scope"),
        (freeze["config"]["frequency_ghz"], context["frequency_ghz"], "config frequency"),
        (freeze["protocol"]["q_values"], list(range(10, 21)), "Q grid"),
        (freeze["config"]["label_mode"], "STRICT_LUMPED", "label mode"),
        (freeze["protocol"]["q_scalar"], "min(Qp,Qs)", "Q scalar"),
    ):
        require(actual == expected, "freeze/request mismatch: " + name)
    require(context["request_id"].startswith(freeze["config"]["study_id"] + "-"),
            "request is outside pinned study")
    records = [json.loads(line) for line in Path(sources["eleven_records"]["path"]).read_text().splitlines()]
    require(len(records) == 11 and [r["q_target"] for r in records] == list(range(10, 21)),
            "exact ordered eleven Q10..20 records required")
    candidates = []
    for line_number, record in enumerate(records, 1):
        require(all(record[k] == context[k] for k in CONTEXT_FIELDS), "record/context mismatch")
        require(type(record["q_target"]) is int, "q_target must be an integer")
        require(record["candidate_id"] == f'{context["request_id"]}-q{record["q_target"]:02d}',
                "candidate identity does not match request/Q")
        require(record.get("evidence_source") == "SELF_PROXY", "original record is not SELF_PROXY")
        require(record.get("emx_status") == "NOT_RUN" and record.get("actual_response") is None,
                "original record must predate fresh EMX")
        require(record.get("parameter_identity_not_actual_gds") is True, "parameter/GDS identity conflated")
        require(type(record["analytic_grid"]) is bool, "analytic_grid must be boolean")
        require(record["geometry_fields"] == list(GEOMETRY_FIELDS), "geometry field order mismatch")
        require(len(record["grid_geometry"]) == len(GEOMETRY_FIELDS), "geometry dimension mismatch")
        require(record["parameter_geometry_hash"] == _parameter_hash(record), "parameter geometry hash mismatch")
        require(len(record["target"]) == 4 and all(v is not None and math.isfinite(v) for v in record["target"]),
                "invalid four-feature target")
        require(record["target"][2] == record["q_target"], "target Q mismatch")
        require([record["target"][j] for j in (0, 1, 3)] == [records[0]["target"][j] for j in (0, 1, 3)],
                "request target triple changes with Q")
        require(len(record["grid_proxy"]) == 4, "proxy dimension mismatch")
        if record["analytic_grid"]:
            require(record["parameter_geometry_hash"] is not None, "analytic pass with nonfinite geometry")
        geometry = dict(zip(GEOMETRY_FIELDS, record["grid_geometry"]))
        geometry_sha = canonical_geometry_sha256(geometry) if record["parameter_geometry_hash"] is not None else None
        candidates.append({**{k: record[k] for k in CONTEXT_FIELDS},
            "q_target": record["q_target"], "candidate_id": record["candidate_id"],
            "candidate_id_sha256": hashlib.sha256(record["candidate_id"].encode()).hexdigest(),
            "candidate_geometry_identity_sha256": geometry_sha,
            "analytic_grid": record["analytic_grid"], "original_record": record,
            "source_record": {"eleven_records": sources["eleven_records"], "line_number": line_number,
                              "record_canonical_sha256": canonical_sha(record)}})
    eligible = {r["candidate_id"] for r in candidates if r["analytic_grid"]}
    cadence = value["cadence"]
    require(Path(cadence["root"]).is_absolute(), "Cadence root must be absolute")
    root = Path(cadence["root"]).resolve()
    routes = cadence["routes"]
    require(set(routes) == eligible, "routes must cover exactly analytic-pass candidate IDs")
    resolved = {key: _inside(root, relative) for key, relative in routes.items()}
    require(len(set(resolved.values())) == len(resolved), "duplicate candidate shard route")
    runtime = value["runtime"]
    runtime_pins = {k: verify_pin(runtime[k]) for k in ("configuration", "foundry_contract")}
    require(set(runtime["core_sources"]) == set(CORE_MODULES), "exact low-level core source pins required")
    runtime_pins["core_sources"] = {k: verify_pin(p) for k, p in runtime["core_sources"].items()}
    require(runtime_pins["configuration"]["sha256"] == CONFIG_SHA256, "configuration differs from proven physical contract")
    require(runtime_pins["foundry_contract"]["sha256"] == FOUNDRY_CONTRACT_SHA256,
            "foundry contract differs from proven reference")
    require(all(runtime_pins["core_sources"][k]["sha256"] == v for k, v in PROVEN_CORE_SHA256.items()),
            "low-level core differs from proven reference")
    sources["private_config"] = runtime_pins["configuration"]
    return SimpleNamespace(value=value, request_pin=request_pin, context=context, sources=sources,
                           candidates=candidates, routes=resolved, runtime=runtime_pins)


def _load_backend(runtime):
    modules = {role: importlib.import_module(name) for role, name in CORE_MODULES.items()}
    for role, module in modules.items():
        require(pin(module.__file__) == runtime["core_sources"][role], "loaded core differs from pin: " + role)
    cfg = modules["api"].load_run_config(runtime["configuration"]["path"])
    contract = read_json(runtime["foundry_contract"]["path"])
    foundry = modules["foundry_audit"]
    foundry._validate_contract(contract, run_config=cfg)
    require("/TSMC65_05_12_26/" in str(cfg.emx.emx_process_file), "unexpected physical process token")
    return SimpleNamespace(cfg=cfg, contract=contract, foundry=foundry,
        adapter=modules["api"].TransformerOptimizationAdapter(cfg.bounds),
        identity=modules["gds_identity"], ports=modules["port_ground_metrics"])


def _candidate_inputs(candidate, shard):
    report_path = shard / "candidate_queue_dataset_summary.json"
    rows_path = shard / "dataset_rows.csv"
    routing_before = {"cadence_report": pin(report_path), "cadence_rows": pin(rows_path)}
    report = read_json(report_path)
    require(report["overall_status"] == "PASS" and report["cadence_streamout_only"] is True
            and report["run_emx"] is False, "not a successful Cadence-only shard")
    with rows_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == 1, "one candidate per shard required")
    row = rows[0]
    for field in ("candidate_id", "candidate_id_sha256", "candidate_geometry_identity_sha256"):
        require(row["queue__" + field] == candidate[field], "queue identity mismatch: " + field)
    evaluation = _inside(shard / "evaluations", row["evaluation"])
    summary_path = evaluation / "summary.json"
    summary_before = pin(summary_path)
    summary = read_json(summary_path)
    geometry = summary["geometry"]
    wanted = dict(zip(GEOMETRY_FIELDS, candidate["original_record"]["grid_geometry"]))
    require(canonical_geometry_sha256(wanted) == candidate["candidate_geometry_identity_sha256"] ==
            canonical_geometry_sha256(geometry), "summary geometry identity mismatch")
    require(canonical_geometry_sha256({k: row["geom__" + k] for k in GEOMETRY_FIELDS}) ==
            candidate["candidate_geometry_identity_sha256"], "queue geometry identity mismatch")
    require(summary["ok"] is True and summary["error"] is None and summary["touchstone_path"] is None,
            "summary is not successful Cadence-only output")
    paths = {"gds": evaluation / "streamout/transformer_layout_cadpins.gds",
             "direct": evaluation / "layout/transformer_layout.gds",
             "summary": summary_path, "source": evaluation / "layout/foundry_layout_source_audit.json",
             "power": evaluation / "layout/power_line_8port_geometry.json",
             "port_manifest": evaluation / "layout/transformer_layout.layout.json",
             "cadence_report": report_path, "cadence_rows": rows_path}
    # Pin every original input, including the actual port manifest used downstream.
    original = {name: pin(path) for name, path in paths.items()}
    require(original["summary"] == summary_before and all(original[k] == v for k, v in routing_before.items()),
            "candidate metadata changed while reading")
    return paths, original, summary, wanted


def _physical_checks(backend, summary, wanted, source, power, actual, port, physical):
    """Unchanged first15-v2 physical requirements, independent of request/Q."""
    close = lambda a, b, t=1e-9: math.isclose(float(a), float(b), rel_tol=0, abs_tol=t)
    geometry = summary["geometry"]
    checks = summary["geometry_check"]
    metrics = checks["metrics"]
    width = wanted["line_width_um"]
    geom = backend.adapter.from_vector([geometry[k] for k in backend.adapter.field_order()])
    range_errors = backend.adapter.search_space.validate(geom)
    angles = all(close(metrics[f"{w}_winding_centerline_{a}_deg"], v, 1e-6)
        for w in ("primary", "secondary") for a, v in
        (("min_internal_angle", 135), ("max_internal_angle", 135),
         ("min_terminal_angle", 90), ("max_terminal_angle", 90)))
    shared = all(close(geometry[k], width) for k in ("primary_width_um", "secondary_width_um",
        "primary_vdd_bar_width_um", "secondary_vdd_bar_width_um"))
    shared = shared and all(close(metrics[k], width) for k in ("power_line_8port_bridge_width_um",
        "power_line_8port_primary_bridge_width_um", "power_line_8port_secondary_bridge_width_um"))
    return {
        "geometry_range_pass": not range_errors,
        "topology_pass": checks["ok"] is True and geometry["primary_turns"] == 1 and geometry["secondary_turns"] == 1 and physical,
        "line_width_sync_pass": shared,
        "angle_45_135_pass": angles and actual["checks"]["all_polygon_edges_horizontal_vertical_or_45_degree"],
        "ground_clearance_pass": port["power_line_check"] == "PASS" and port["metrics"]["power_line_8port_port_ground_overlap_verified_port_count"] == 8,
        "foundry_layout_audit_pass": actual["overall_status"] == "PASS",
        "manufacturing_grid_canonicalization_pass": actual["checks"]["all_polygon_vertices_on_manufacturing_grid"] and source["grid_canonicalization"]["overall_status"] == "PASS" and source["grid_canonicalization"]["max_relative_area_change"] <= .005,
        "foundry_slotted_ground_frame_pass": actual["ground_frame"]["overall_status"] == "PASS",
        "foundry_power_line_contract_pass": port["power_line_check"] == "PASS" and power["touchstone_mode"] == "signal_4_grounded_aux",
        "foundry_via_stack_and_landing_pad_pass": actual["via_and_landing"]["overall_status"] == "PASS" and port["via_stack_check"]["overall_status"] == "PASS",
        "foundry_bridge_connection_pass": actual["power_line_bridge_connections"]["overall_status"] == "PASS",
    }


def _audit_candidate(candidate, shard, target, backend, request):
    paths, original, summary, wanted = _candidate_inputs(candidate, shard)
    save_json(target / "INPUT_BINDING.json", dict(schema="frequency_research_candidate_gds_inputs.v1",
        status="INPUTS_PINNED_NOT_YET_PHYSICALLY_AUDITED", candidate_id=candidate["candidate_id"],
        candidate_id_sha256=candidate["candidate_id_sha256"],
        candidate_geometry_identity_sha256=candidate["candidate_geometry_identity_sha256"],
        request=request.context, source_pins=request.sources, original_artifacts=list(original.values()),
        production_campaign_membership=False))
    source, power = read_json(paths["source"]), read_json(paths["power"])
    foundry = backend.foundry
    foundry._validate_source_audit(source, run_config=backend.cfg)
    foundry._validate_power_line_audit_shape(power)
    actual = foundry._audit_actual_gds(gds_path=paths["gds"], source_audit=source,
        power_line_audit=power, run_config=backend.cfg, contract=backend.contract, expected_top_cell="TRANSFORMER")
    require(bool(actual["checks"]), "empty actual-GDS check set")
    actual.update(schema="independent_research_actual_gds_foundry.v1",
        gds_sha256=original["gds"]["sha256"],
        overall_status="PASS" if all(v is True for v in actual["checks"].values()) else "FAIL",
        candidate_id=candidate["candidate_id"], candidate_id_sha256=candidate["candidate_id_sha256"],
        geometry_sha256=candidate["candidate_geometry_identity_sha256"], production_campaign_membership=False,
        configuration=request.runtime["configuration"], physical_contract_reference=request.runtime["foundry_contract"])
    save_json(target / "ACTUAL_FOUNDRY_AUDIT.json", actual)
    port = backend.ports.measure_port_ground_metrics(gds_path=paths["gds"], power_line_audit=power, foundry_audit=actual)
    save_json(target / "ACTUAL_PORT_GROUND_AUDIT.json", port)
    identities = {name: backend.identity.gds_structural_identity(paths[name]) for name in ("direct", "gds")}
    identities["cadence"] = identities.pop("gds")
    physical = all(x["overall_status"] == "PASS" for x in identities.values()) and all(
        identities["direct"][k] == identities["cadence"][k]
        for k in ("layer_union_sha256", "label_pin_set_sha256", "structural_sha256"))
    save_json(target / "STRUCTURAL_IDENTITY.json", {**identities, "physical_structure_matches": physical})
    required = _physical_checks(backend, summary, wanted, source, power, actual, port, physical)
    normalized = backend.identity.gds_timestamp_normalized_sha256(paths["gds"])
    require(original == {name: pin(path) for name, path in paths.items()}, "original artifacts changed during audit")
    audit = dict(schema="independent_research_candidate_gds_geometry_audit.v1",
        overall_status="PASS" if all(v is True for v in required.values()) else "FAIL",
        candidate_id=candidate["candidate_id"], candidate_id_sha256=candidate["candidate_id_sha256"],
        candidate_geometry_identity_sha256=candidate["candidate_geometry_identity_sha256"],
        gds_path=original["gds"]["path"], gds_sha256=original["gds"]["sha256"],
        gds_timestamp_normalized_sha256=normalized,
        gds_timestamp_normalization_algorithm=backend.identity.GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM,
        process_token="/TSMC65_05_12_26/", checks=required, original_artifacts=list(original.values()),
        original_artifacts_unchanged=True, production_campaign_membership=False,
        foundry_drc_executed=False, emx_executed=False, request=request.context, source_pins=request.sources,
        configuration=request.runtime["configuration"], physical_contract_reference=request.runtime["foundry_contract"],
        evidence=[pin(p) for p in sorted(target.iterdir())])
    audit_path = target / "GEOMETRY_AUDIT.json"
    save_json(audit_path, audit)
    result = dict(status=audit["overall_status"], geometry_audit=pin(audit_path), gds=original["gds"],
        port_manifest=original["port_manifest"], failed_checks=[k for k, v in required.items() if v is not True],
        actual_foundry_audit=pin(target / "ACTUAL_FOUNDRY_AUDIT.json"),
        actual_port_ground_audit=pin(target / "ACTUAL_PORT_GROUND_AUDIT.json"),
        structural_identity=pin(target / "STRUCTURAL_IDENTITY.json"))
    row = {k: audit[k] for k in CALIBRE_FIELDS if k in audit}
    row.update(geometry_audit_path=str(audit_path), top_cell="TRANSFORMER")
    return result, row


def audit_request(request_path, out):
    """Audit each eligible existing candidate once; preserve all eleven statuses."""
    out = Path(out).resolve()
    # Reject source/output overlap before the first write, including on failure.
    require(not out.is_relative_to(Path(__file__).resolve().parents[2]), "output must not be in source repository")
    preliminary = read_json(request_path)
    cadence_root = Path(preliminary["cadence"]["root"]).resolve()
    require(not out.is_relative_to(cadence_root), "output must not be inside Cadence source artifacts")
    out.mkdir(parents=True, exist_ok=False)
    try:
        request = load_request(request_path)
        protected = [Path(request.request_pin["path"]), *[Path(p["path"]) for p in request.sources.values()],
                     Path(request.value["cadence"]["root"]).resolve()]
        require(not any(p == out or out.is_relative_to(p) for p in protected), "output overlaps input artifacts")
        backend = _load_backend(request.runtime) if request.routes else None
        records, index = [], []
        for candidate in request.candidates:
            record = dict(candidate)
            if not candidate["analytic_grid"]:
                record.update(status="ANALYTIC_FAIL", audit_attempted=False, cadence_routed=False,
                              calibre_eligible=False)
            else:
                target = out / "candidates" / candidate["candidate_id_sha256"]
                target.mkdir(parents=True, exist_ok=False)
                record.update(audit_attempted=True, cadence_routed=True, calibre_eligible=False,
                              cadence_shard=str(request.routes[candidate["candidate_id"]]))
                try:
                    result, row = _audit_candidate(candidate, request.routes[candidate["candidate_id"]], target, backend, request)
                    record.update(result)
                    if record["status"] == "PASS":
                        record["calibre_eligible"] = True
                        index.append(row)
                except Exception as error:
                    record.update(status="FAIL", error=type(error).__name__ + ": " + str(error),
                                  partial_evidence=[pin(p) for p in sorted(target.iterdir())])
                    save_json(target / "AUDIT_FAILURE.json", record)
                    record["failure_receipt"] = pin(target / "AUDIT_FAILURE.json")
            records.append(record)
        # Do not publish a usable index if the request or pinned metadata changed.
        require(pin(request_path) == request.request_pin, "request manifest changed during audit")
        for source in request.sources.values():
            verify_pin(source)
        for key in ("configuration", "foundry_contract"):
            verify_pin(request.runtime[key])
        for source in request.runtime["core_sources"].values():
            verify_pin(source)
        status = "NO_ELIGIBLE_CANDIDATES" if not request.routes else (
            "PASS" if all(r["status"] != "FAIL" for r in records) else "PARTIAL_FAIL")
        result = dict(schema=RECEIPT_SCHEMA, status=status,
            request=request.context, input_request=request.request_pin, source_pins=request.sources,
            runtime=request.runtime, records=records, N_logical=11,
            N_analytic_fail=sum(r["status"] == "ANALYTIC_FAIL" for r in records),
            N_audit_attempted=len(request.routes), N_audit_pass=len(index),
            N_audit_fail=sum(r["status"] == "FAIL" for r in records), audited_utc=utc_now(),
            production_campaign_membership=False, cadence_started=False, calibre_started=False, emx_started=False,
            REAL_EMX_VALIDATION="NOT_RUN", q_emx=None, repairs_performed=False)
        if index:
            path = out / "CALIBRE_INPUT.csv"
            with path.open("x", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=CALIBRE_FIELDS)
                writer.writeheader()
                writer.writerows(index)
            result["calibre_input"] = pin(path)
        save_json(out / "REQUEST_GDS_AUDIT.json", result)
        return result
    except Exception as error:
        save_json(out / "REQUEST_FAILURE.json", dict(schema=RECEIPT_SCHEMA, status="FAIL",
            error=type(error).__name__ + ": " + str(error), input_request_path=str(request_path),
            audited_utc=utc_now(), calibre_started=False, emx_started=False,
            production_campaign_membership=False, partial_artifacts_preserved=True))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path, help="Frozen research request JSON")
    parser.add_argument("--out", type=Path, help="Fresh no-clobber output directory; required for audit")
    parser.add_argument("--validate-only", action="store_true", help="Metadata and pins only; no GDS audit")
    args = parser.parse_args(argv)
    if args.validate_only:
        if args.out is not None:
            parser.error("--validate-only does not write an output directory")
        request = load_request(args.request)
        print(json.dumps(dict(status="METADATA_VALIDATED_GDS_NOT_AUDITED", request=request.context,
            N_logical=11, N_analytic_pass=len(request.routes), REAL_EMX_VALIDATION="NOT_RUN"), sort_keys=True))
    else:
        if args.out is None:
            parser.error("--out is required for an audit")
        result = audit_request(args.request, args.out)
        print(json.dumps({k: result[k] for k in ("status", "N_logical", "N_analytic_fail", "N_audit_pass", "N_audit_fail")}))


if __name__ == "__main__":
    main()
