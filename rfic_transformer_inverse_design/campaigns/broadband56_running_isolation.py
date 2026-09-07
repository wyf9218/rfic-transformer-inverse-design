"""Bind running-stage children to the existing lease, history and backend.

This is process-isolation evidence only. It neither grants resource capacity
nor dispatches work. Startup without this explicit history remains idle-only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import broadband56_isolation_identity as identity


def _bound(record):
    if not isinstance(record, dict) or not record.get("path"):
        raise identity.IsolationIdentityError("running-stage identity is missing")
    path = Path(record["path"])
    if not path.is_absolute() or not identity.file_record_matches(path, record):
        raise identity.IsolationIdentityError("running-stage file identity mismatch")
    return path, identity.read_json(path, "running-stage input")


def _option(process, flag):
    argv = process.get("command_argv", [])
    if argv.count(flag) != 1 or argv.index(flag) + 1 >= len(argv):
        raise identity.IsolationIdentityError(f"running-stage argument missing/duplicated: {flag}")
    return argv[argv.index(flag) + 1]


def _script(process):
    argv = process.get("command_argv", [])
    # All public stage/role entrypoints use the approved Python interpreter.
    index = 1
    while index < len(argv) and argv[index] in {"-B", "-u"}:
        index += 1
    return argv[index] if index < len(argv) and not argv[index].startswith("-") else None


def _same_python(process, owner):
    return all(process.get(k) == owner.get(k) for k in
               ("uid", "boot_id", "executable_path", "executable_sha256"))


def _child(child, parent, owner=None):
    uid_matches = child.get("uid") == parent.get("uid")
    if owner is not None:
        uid_matches = all(p.get("uid") == owner["uid"] or
                          (p.get("protected_container_transit") is True and p.get("uid") == 0)
                          for p in (child, parent))
    return (child.get("parent_pid") == parent["pid"]
            and uid_matches
            and child.get("boot_id") == parent.get("boot_id")
            and type(child.get("start_ticks")) is int
            and child["start_ticks"] >= parent["start_ticks"])


def _matches_script(process, record, owner):
    return (_same_python(process, owner) and _script(process) == record.get("path")
            and identity.file_record_matches(Path(record["path"]), record))


def running_stage_children(*, history_path, lease, processes):
    """Return only children belonging to one hash-bound active stage tree."""
    owner = next((p for p in processes
                  if identity.process_identity_matches(p, lease["physical_process"])), None)
    if owner is None:
        raise identity.IsolationIdentityError("running-stage owner is not live")
    history_path = Path(history_path).resolve(strict=True)
    state_path, state = _bound(identity.read_json(history_path, "history pointer"))
    campaign_root = history_path.parents[2]
    stage = state.get("stage")
    _, initial = _bound(state.get("initial_snapshot"))
    _, bound_lease = _bound(initial.get("supervisor_lease"))
    _, backend = _bound(lease.get("backend_identity_manifest"))
    expected_bindings = {key: initial.get(key) for key in (
        "campaign_id", "contract_fingerprint_sha256", "supervisor_lease",
        "operational_overlay_manifest", "owner_swap_override_receipt")}
    if (state_path.parent != history_path.parent
            or history_path.parent.parent != campaign_root / "scheduling_history"
            or history_path.name != "LATEST.json"
            or state.get("schema") != "rfic_transformer.broadband56_stage_resource_history.v1"
            or state.get("overall_status") != "OBSERVED" or state.get("error") is not None
            or state.get("sampler_pid") != owner["pid"]
            or state.get("bindings") != expected_bindings
            or bound_lease != lease
            or stage not in {"PILOT_1000", "PHASE_A", "PHASE_B", "PHASE_C"}
            or backend.get("campaign_id") != identity.CAMPAIGN_ID
            or initial.get("campaign_id") != identity.CAMPAIGN_ID
            or initial.get("contract_fingerprint_sha256") != backend.get("contract_fingerprint_sha256")
            or lease.get("contract_fingerprint_sha256") != backend.get("contract_fingerprint_sha256")):
        raise identity.IsolationIdentityError("running-stage history/owner/contract mismatch")
    from .broadband56_scheduling import fixed_generation_policy
    fixed = fixed_generation_policy(initial)
    if fixed is None:
        raise identity.IsolationIdentityError("running-stage isolation requires fixed48 history")
    scripts = backend["script_identities"]
    launchers = [p for p in processes if _script(p) == scripts["stage_launcher"]["path"]]
    if not launchers:
        return {"owned_pids": [], "native_counts": {}, "evidence": [identity.file_record(state_path)]}
    if len(launchers) != 1:
        raise identity.IsolationIdentityError("multiple live stage launchers")
    launcher = launchers[0]
    stage_dir = Path(_option(launcher, "--out-dir"))
    if (not _child(launcher, owner)
            or not _matches_script(launcher, scripts["stage_launcher"], owner)
            or _option(launcher, "--campaign-root") != str(campaign_root)
            or _option(launcher, "--stage") != stage
            or _option(launcher, "--backend-identity-manifest") != lease["backend_identity_manifest"]["path"]
            or _option(launcher, "--max-concurrency") != str(fixed["requested_concurrency"])
            or not stage_dir.is_absolute()
            or stage_dir.resolve().parent != campaign_root / "stages"
            or not stage_dir.name.startswith(f'{state["stage_check_index"]:06d}_{stage.lower()}_')):
        raise identity.IsolationIdentityError("running-stage launcher identity/arguments mismatch")
    owned = {launcher["pid"]}
    evidence = [identity.file_record(state_path)]
    backends = [p for p in processes if _script(p) == scripts["production_stage_backend"]["path"]]
    if not backends:
        return {"owned_pids": sorted(owned), "native_counts": {}, "evidence": evidence}
    if len(backends) != 1:
        raise identity.IsolationIdentityError("multiple live production backends")
    worker = backends[0]
    audit_path = stage_dir / "STAGE_LAUNCH_AUDIT.json"
    audit = identity.read_json(audit_path, "stage launch audit")
    argv = worker["command_argv"]
    script_index = argv.index(scripts["production_stage_backend"]["path"])
    command_sha = hashlib.sha256(json.dumps(argv[script_index:], separators=(",", ":")).encode()).hexdigest()
    if (not _child(worker, launcher)
            or not _matches_script(worker, scripts["production_stage_backend"], owner)
            or audit.get("schema") != "rfic_transformer.broadband56_v2_stage_launch_audit.v1"
            or audit.get("overall_status") != "PASS"
            or audit.get("stage") != stage
            or audit.get("campaign_id") != identity.CAMPAIGN_ID
            or audit.get("contract_fingerprint_sha256") != backend["contract_fingerprint_sha256"]
            or audit.get("backend_identity_manifest") != lease["backend_identity_manifest"]
            or audit.get("command_argv_sha256") != command_sha
            or _option(worker, "--backend-out-dir") != str(stage_dir / "backend")
            or _option(worker, "--campaign-root") != str(campaign_root)
            or _option(worker, "--stage") != stage
            or _option(worker, "--backend-identity-manifest") != lease["backend_identity_manifest"]["path"]
            or _option(worker, "--resource-snapshot") != audit["resource_snapshot"]["path"]
            or _option(worker, "--full-campaign-receipt") != audit["authorization_receipt"]["path"]
            or _option(worker, "--max-concurrency") != str(fixed["requested_concurrency"])):
        raise identity.IsolationIdentityError("running-stage backend/launch audit mismatch")
    _bound(audit["authorization_receipt"])
    _bound(audit["resource_snapshot"])
    owned.add(worker["pid"])
    evidence.append(identity.file_record(audit_path))
    children = [p for p in processes if p.get("parent_pid") == worker["pid"]]
    if not children:
        return {"owned_pids": sorted(owned), "native_counts": {}, "evidence": evidence}
    context_path = stage_dir / "backend/STAGE_CONTEXT.json"
    context = identity.read_json(context_path, "active stage context")
    profile_pin = backend["runtime_identities"]["stage_execution_profile"]
    _, profile = _bound(profile_pin)
    if (context.get("schema") != "rfic_transformer.broadband56_v2_stage_context.v2"
            or context.get("stage") != stage
            or context.get("campaign_id") != identity.CAMPAIGN_ID
            or context.get("contract_fingerprint_sha256") != backend["contract_fingerprint_sha256"]
            or context.get("campaign_root") != str(campaign_root)
            or context.get("backend_identity_manifest") != lease["backend_identity_manifest"]
            or context.get("stage_execution_profile") != profile_pin
            or context.get("initial_resource_snapshot") != state["initial_snapshot"]
            or context.get("resource_snapshot") != audit["resource_snapshot"]
            or context.get("stage_resource_history") != str(history_path)
            or context.get("full_campaign_authorization_receipt") != audit["authorization_receipt"]
            or context.get("max_concurrency") != fixed["requested_concurrency"]):
        raise identity.IsolationIdentityError("running-stage context identity mismatch")
    commands = profile["stages"][stage]["commands"]
    roles = {scripts[c["role"]]["path"]: (i, c["role"]) for i, c in enumerate(commands, 1)}
    if len(children) != 1:
        raise identity.IsolationIdentityError("overlapping role executors")
    role_process = children[0]
    entry = roles.get(_script(role_process))
    if entry is None:
        raise identity.IsolationIdentityError("role script is not in the bound stage profile")
    index, role = entry
    role_dir = stage_dir / "backend/roles" / f"{index:02d}_{role}"
    if (not _child(role_process, worker)
            or not _matches_script(role_process, scripts[role], owner)
            or not any(str(role_dir) == a or a.startswith(str(role_dir) + "/")
                       for a in role_process["command_argv"])):
        raise identity.IsolationIdentityError("running-stage role identity/output mismatch")
    for flag, expected in (("--backend-identity-manifest", lease["backend_identity_manifest"]["path"]),
                           ("--full-campaign-receipt", audit["authorization_receipt"]["path"]),
                           ("--stage", stage)):
        if flag in role_process["command_argv"] and _option(role_process, flag) != expected:
            raise identity.IsolationIdentityError("running-stage role contract argument mismatch")
    owned.add(role_process["pid"])
    evidence.append(identity.file_record(context_path))
    # A verified, hash-bound role owns its solver/delegate descendants, not a
    # second stage/owner. Verify each parent edge; detached jobs never inherit it.
    by_pid = {p["pid"]: p for p in processes}
    descendants = {role_process["pid"]}
    while True:
        added = {p["pid"] for p in processes
                 if p["pid"] not in descendants and p.get("parent_pid") in descendants
                 and _child(p, by_pid[p["parent_pid"]], owner)}
        if not added:
            break
        descendants.update(added)
    forbidden = {scripts["stage_launcher"]["path"], scripts["production_stage_backend"]["path"]}
    for pid in descendants - {role_process["pid"]}:
        process = by_pid[pid]
        if (_script(process) in forbidden
                or identity._contains_marker(str(process.get("command_text", "")), identity.SUPERVISOR_MARKERS)):
            raise identity.IsolationIdentityError("nested duplicate stage or supervisor")
        # Runner delegates must be exact backend scripts, not name-only matches.
        if identity._contains_marker(str(process.get("command_text", "")), identity.RUNNER_MARKERS):
            record = next((r for r in scripts.values() if r.get("path") == _script(process)), None)
            if record is None or not _matches_script(process, record, owner):
                raise identity.IsolationIdentityError("unbound nested runner")
            if ("--out-dir" not in process.get("command_argv", [])
                    or not Path(_option(process, "--out-dir")).resolve().is_relative_to(role_dir)):
                raise identity.IsolationIdentityError("nested runner output escapes its bound role")
    owned.update(descendants)
    from .broadband56_native_telemetry import NATIVE_NAMES
    native_counts = dict.fromkeys(("cadence", "calibre", "emx"), 0)
    for pid in descendants:
        process = by_pid[pid]
        if process.get("protected_container_transit"):
            continue
        executable = Path(process["executable_path"])
        tool = NATIVE_NAMES.get(executable.name)
        if tool is not None:
            with executable.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    raise identity.IsolationIdentityError("native solver executable is not ELF")
            if identity.sha256(executable) != process["executable_sha256"]:
                raise identity.IsolationIdentityError("native solver executable changed")
            native_counts[tool] += 1
    return {"owned_pids": sorted(owned), "native_counts": native_counts, "evidence": evidence}
