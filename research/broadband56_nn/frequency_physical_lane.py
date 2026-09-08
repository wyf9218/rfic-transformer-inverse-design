"""Prepare a research-only physical handoff; never execute a simulator.

This is a thin, local preflight/command adapter, not a campaign or scheduler.
It accepts a hash-pinned, already selected candidate queue and preserves the
target mapping. The generic Cadence command MUST stop at streamout: its default
EMX mode has no intervening Calibre gate. Existing layout-audit, Calibre and
exact-GDS solver implementations remain unchanged.

RUNTIME_NOT_VERIFIED is intentional. Even successful evidence checks here are
not resource admission, license availability, a launch receipt or physical QA.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat

from .io import canonical_sha, save_json, sha256, utc_now
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    GEOMETRY_FIELDS, canonical_geometry_sha256,
)


REQUEST_SCHEMA = "frequency_physical_preflight_request.v1"
MODES = ("STRICT_LUMPED", "POINTWISE_DESCRIPTOR_EXPERIMENTAL")
RUNTIME_ROLES = (
    "python", "cadence_parallel", "cadence_single", "evaluator", "zeus_cadence",
    "private_config", "foundry_contract", "foundry_audit", "gds_identity",
    "calibre_runner", "calibre_archive", "emx_wrapper", "emx_process_file",
    "cadence_pdk_cds_lib", "cadence_layer_map",
)
GEOMETRY_CHECKS = (
    "geometry_range_pass", "topology_pass", "line_width_sync_pass",
    "angle_45_135_pass", "ground_clearance_pass", "foundry_layout_audit_pass",
    "manufacturing_grid_canonicalization_pass", "foundry_slotted_ground_frame_pass",
    "foundry_power_line_contract_pass", "foundry_via_stack_and_landing_pad_pass",
    "foundry_bridge_connection_pass",
)
CALIBRE_CHECKS = GEOMETRY_CHECKS + (
    "calibre_result_accounting_complete", "foundry_drc_pass", "no_blocking_drc_violations",
)


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("exact lowercase SHA-256 required")
    return value


def _pin_bytes(record):
    """Read the exact regular file, refusing symlinks and changed bytes."""
    path = Path(record["path"])
    if not path.is_absolute():
        raise ValueError("absolute private input path required")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("input symlink forbidden: " + str(parent))
    expected = _digest(record["sha256"])
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("regular input file required")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        content = stream.read()
        after_fd = os.fstat(stream.fileno())
    after = path.stat()
    fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if not fields(before) == fields(opened) == fields(after_fd) == fields(after):
        raise ValueError("input changed during preflight")
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("input SHA mismatch: " + str(path))
    if "size_bytes" in record and (type(record["size_bytes"]) is not int or record["size_bytes"] != len(content)):
        raise ValueError("input size mismatch")
    return content


def _json_pin(record):
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(_pin_bytes(record), object_pairs_hook=no_duplicates,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def _csv_pin(record):
    return list(csv.DictReader(io.StringIO(_pin_bytes(record).decode("utf-8-sig"))))


def _unique(rows, key):
    index = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value or value in index:
            raise ValueError("missing or duplicate " + key)
        index[value] = row
    return index


def _evidence(request):
    bindings = request["bindings"]
    freeze = _json_pin(bindings["evaluation_freeze"])
    identity = freeze["identity"]
    frequency = request["frequency_ghz"]
    if type(frequency) is not int or not 5 <= frequency <= 60:
        raise ValueError("exact integer frequency required")
    if request["label_mode"] not in MODES:
        raise ValueError("explicit label mode required")
    if (frequency != identity["frequency_ghz"] or request["label_mode"] != identity["label_mode"]
            or request["model_id"] != freeze["model_id"]
            or request["study_id"] != freeze["config"]["study_id"]):
        raise ValueError("research/model/frequency identity mismatch")
    # These are existing model/data bytes only. Never load a model or test labels.
    for key in ("dataset", "data_manifest", "splits", "forward_checkpoint", "inverse_checkpoint"):
        _pin_bytes(identity[key])
    for key in ("target_manifest.csv", "emx_preselected.json", "normalizer.json", "geometry_contract.json"):
        _pin_bytes(freeze["artifacts"][key])
    if freeze["artifacts"]["target_manifest.csv"]["sha256"] != bindings["target_manifest"]["sha256"]:
        raise ValueError("target manifest differs from frozen pre-scoring inputs")
    if freeze["artifacts"]["emx_preselected.json"]["sha256"] != bindings["preselection"]["sha256"]:
        raise ValueError("physical selection differs from frozen preselection")
    norm = _json_pin(freeze["artifacts"]["normalizer.json"])
    contract = _json_pin(freeze["artifacts"]["geometry_contract.json"])
    if canonical_sha(norm) != identity["normalizer_sha256"] or canonical_sha(contract) != identity["contract_sha256"]:
        raise ValueError("normalizer/geometry contract identity mismatch")
    if tuple(contract["field_names"]) != tuple(GEOMETRY_FIELDS) or contract["units"] != "um":
        raise ValueError("unsupported actual geometry field order or units")
    if contract["grid_um"] != .005:
        raise ValueError("exact existing manufacturing grid reference required")
    targets = _unique(_csv_pin(bindings["target_manifest"]), "target_id")
    selected = _unique(_json_pin(bindings["preselection"])["selected"], "target_id")
    mapping = _unique(_json_pin(bindings["target_candidate_map"])["rows"], "target_id")
    if not selected or set(selected) != set(mapping) or not set(selected) <= set(targets):
        raise ValueError("preselected target mapping must preserve the exact full selected set")
    queue = _unique(_csv_pin(bindings["candidate_csv"]), "candidate_id_sha256")
    referenced = set()
    for target_id, record in mapping.items():
        if str(targets[target_id].get("frequency_ghz")) != str(frequency):
            raise ValueError("target belongs to another frequency")
        if record.get("selection_group") != selected[target_id].get("selection_group"):
            raise ValueError("selection group changed")
        if record.get("status") == "FAIL_ANALYTIC_PRECHECK":
            if record.get("candidate_id_sha256") not in (None, ""):
                raise ValueError("failed precheck must not enter the executable candidate queue")
            continue
        if record.get("status") != "PENDING":
            raise ValueError("mapping must distinguish PENDING from analytical failure")
        digest = _digest(record.get("candidate_id_sha256"))
        if digest not in queue:
            raise ValueError("selected candidate missing from queue")
        referenced.add(digest)
    if set(queue) != referenced:
        raise ValueError("candidate queue contains unselected or replaced candidates")
    for digest, row in queue.items():
        vector = {name: float(row["geom__" + name]) for name in GEOMETRY_FIELDS}
        if not all(math.isfinite(value) for value in vector.values()):
            raise ValueError("nonfinite queued geometry")
        if (canonical_geometry_sha256(vector) != digest or row.get("geometry_sha256") != digest
                or row.get("candidate_geometry_identity_sha256") != digest or not row.get("candidate_id")):
            raise ValueError("canonical candidate geometry identity mismatch")
        for i, name in enumerate(GEOMETRY_FIELDS):
            value = vector[name]
            if not contract["lower"][i] <= value <= contract["upper"][i]:
                raise ValueError("queued geometry outside frozen bounds")
            if abs(value / contract["grid_um"] - round(value / contract["grid_um"])) > 1e-7:
                raise ValueError("queued geometry is not on the export grid")
    return freeze, list(mapping.values()), list(queue.values())


def cadence_only_command(request, output):
    """Source-supported command shape only; never default generic EMX mode."""
    runtime = request["runtime_pins"]
    return [runtime["python"]["path"], runtime["cadence_parallel"]["path"],
        "--candidate-csv", request["bindings"]["candidate_csv"]["path"],
        "--out-dir", str(output), "--config", runtime["private_config"]["path"],
        "--jobs", "1", "--expected-jobs", "1", "--chunk-size", "1", "--batch-size", "1",
        "--expected-count", str(request["candidate_count"]), "--cadence-streamout-only",
        "--allow-outside-target-bin", "--force-port-mode", "single_ended_shield_grounded",
        "--force-cadence-pin-purpose", "51", "--force-wideband-5-60-1p0",
        "--expected-port-mode", "single_ended_shield_grounded", "--expected-pin-purpose", "51",
        "--expected-frequency-start-ghz", "5", "--expected-frequency-stop-ghz", "60",
        "--expected-frequency-step-ghz", "1", "--expected-frequency-points", "56",
        "--expected-touchstone-extension", ".s4p", "--expected-ports", "4"]


def prepare(request_path, out):
    """Write a no-clobber handoff and honest runtime gaps, with zero launches."""
    request_path, out = Path(request_path).absolute(), Path(out).absolute()
    if out.exists() or any(p.is_symlink() for p in (out, *out.parents)):
        raise FileExistsError("new no-symlink preflight output required")
    request_pin = {"path": str(request_path), "sha256": sha256(request_path)}
    request = _json_pin(request_pin)
    if request.get("schema") != REQUEST_SCHEMA:
        raise ValueError("explicit physical preflight request schema required")
    freeze, mapping, queue = _evidence(request)
    if type(request.get("candidate_count")) is not int or request["candidate_count"] != len(queue):
        raise ValueError("actual unique candidate count mismatch")
    runtime = request.get("runtime_pins", {})
    runtime_checks = {}
    for role in RUNTIME_ROLES:
        try:
            _pin_bytes(runtime[role])
        except (OSError, ValueError, KeyError) as exc:
            runtime_checks[role] = {"status": "NOT_VERIFIED", "reason": str(exc)}
        else:
            runtime_checks[role] = {"status": "HASH_VERIFIED_ONLY", "pin": runtime[role]}
    command = None
    if queue and all(role in runtime for role in ("python", "cadence_parallel", "private_config")):
        command = cadence_only_command(request, out / "future_cadence_only")
    result = {"schema": "frequency_physical_preflight.v1", "created_utc": utc_now(),
        "status": "PREPARED_RUNTIME_NOT_VERIFIED", "request": request_pin,
        "study_id": request["study_id"], "model_id": request["model_id"],
        "frequency_ghz": request["frequency_ghz"], "label_mode": request["label_mode"],
        "identity": freeze["identity"], "bindings": request["bindings"],
        "N_selected": len(mapping), "N_unique_candidates": len(queue),
        "N_analytic_precheck_failed": sum(x["status"] == "FAIL_ANALYTIC_PRECHECK" for x in mapping),
        "target_mapping": mapping, "runtime_checks": runtime_checks,
        "cadence_only_command_template": command, "command_executed": False,
        "downstream_existing_cores": ["layout.foundry_audit.produce_foundry_layout_audit",
            "campaigns.broadband56_gds_identity.audit_gds_physical_identity",
            "run_tsmc65_calibre_macro_drc.py", "execution.zeus_cadence._run_emx"],
        "missing_integration": ["actual Cadence export/port and foundry audit on a selected candidate",
            "candidate-bound Calibre index and zero-blocking receipt from exact same GDS",
            "verified exact-GDS solver input/config/port/runtime binding immediately before invocation",
            "existing authorized independent resource and license admission; not tested here"],
        "resource_admission": "NOT_CHECKED", "license_availability": "NOT_CHECKED",
        "physical_chain_runtime": "NOT_VERIFIED", "REAL_EMX_VALIDATION": "NOT_RUN",
        "simulator_launch_allowed_by_this_receipt": False, "production_changed": False,
        "geometry_mapping_scope": "grid values only; bounds/grid checks do not prove actual layout pass",
        "no_new_approval_chain": True, "no_controller_installed": True,
        "implementation": {"path": str(Path(__file__).absolute()), "sha256": sha256(__file__)}}
    # Recheck all research inputs before publication, then never replace them.
    _evidence(request)
    out.mkdir(parents=True, exist_ok=False)
    save_json(out / "PHYSICAL_PREFLIGHT.json", result)
    return result


def verify_same_gds_zero_blocking(*, candidate_id_sha256, geometry_identity_sha256,
                                 gds_pin, geometry_audit_pin, calibre_receipt_pin):
    """Read-only narrow Calibre/raw-GDS check, NOT a solver launch authority.

    This consumes the existing independent macro/IP Calibre receipt schema.
    No claim is made about proc/ports/resources until a real integrated lane
    verifies those separately. The original GDS bytes are rehashed, not rebuilt.
    """
    candidate, geometry = _digest(candidate_id_sha256), _digest(geometry_identity_sha256)
    _pin_bytes(gds_pin)
    audit, receipt = _json_pin(geometry_audit_pin), _json_pin(calibre_receipt_pin)
    if receipt.get("schema") != "candidate_bound_tsmc65_calibre_macro_ip_back_end_drc_v1":
        raise ValueError("actual independent Calibre candidate receipt required")
    for record in (audit, receipt):
        if (record.get("overall_status") != "PASS" or record.get("candidate_id_sha256") != candidate
                or record.get("candidate_geometry_identity_sha256") != geometry
                or record.get("gds_sha256") != gds_pin["sha256"]):
            raise ValueError("candidate/geometry/raw audited GDS binding mismatch")
    if (receipt.get("gds_path") != gds_pin["path"]
            or receipt.get("geometry_audit_path") != geometry_audit_pin["path"]
            or receipt.get("geometry_audit_sha256") != geometry_audit_pin["sha256"]):
        raise ValueError("Calibre references different source artifacts")
    if any(audit.get("checks", {}).get(name) is not True for name in GEOMETRY_CHECKS):
        raise ValueError("incomplete actual geometry/layout checks")
    if any(receipt.get("checks", {}).get(name) is not True for name in CALIBRE_CHECKS):
        raise ValueError("incomplete Calibre check set")
    if type(receipt.get("blocking_drc_violation_count")) is not int or receipt["blocking_drc_violation_count"] != 0:
        raise ValueError("Calibre must have exact zero blocking violations")
    if (receipt.get("drc_engine") != "Calibre" or receipt.get("drc_scope") != "foundry_macro_ip_back_end"
            or receipt.get("gds_top_cell") != "TRANSFORMER"):
        raise ValueError("Calibre engine/scope/top-cell mismatch")
    for label in ("drc_report", "drc_rule_deck", "drc_source_rule_deck"):
        _pin_bytes({"path": receipt[label + "_path"], "sha256": receipt[label + "_sha256"]})
    _pin_bytes(gds_pin)
    return {"status": "SAME_RAW_GDS_AND_ZERO_BLOCKING_BINDING_VERIFIED_ONLY",
        "candidate_id_sha256": candidate, "geometry_identity_sha256": geometry,
        "gds": gds_pin, "geometry_audit": geometry_audit_pin, "calibre_receipt": calibre_receipt_pin,
        "emx_invoked": False, "ready_for_emx": False,
        "remaining": "proc/ports/config/runtime and real resource admission are not verified by this narrow check"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.request, args.out), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
