"""Synthetic process/resource fixtures: no production lease, task or solver."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_isolation_identity as identity
from rfic_transformer_inverse_design.campaigns import broadband56_scheduling as scheduling
from rfic_transformer_inverse_design.campaigns.broadband56_dispatch import StageAdmission
from tests.test_broadband56_fixed48_scheduling import fixed_samples
from tests.test_broadband56_isolation_identity import _process, _lease, _evaluate
from tests.test_broadband56_scheduling import write


def fixture(root):
    root = root.resolve()
    paths, now = fixed_samples(root)
    initial = json.loads(paths[0].read_text())
    backend_path = root / "fixed_backend.json"
    backend = json.loads(backend_path.read_text())
    scripts = {}
    names = {"stage_launcher": "run_broadband56_v2_stage_launcher.py",
             "production_stage_backend": "run_broadband56_v2_production_stage_backend.py",
             "exact_audited_gds_emx_runner": "run_broadband56_v2_exact_gds_emx_batch.py",
             "cadence_streamout_delegate": "run_candidate_queue_dataset_parallel.py"}
    for role, name in names.items():
        p = root / "runtime" / name
        p.parent.mkdir(exist_ok=True)
        p.write_text("# TEST FIXTURE ONLY; NEVER EXECUTED\n")
        scripts[role] = identity.file_record(p)
    profile_path = write(root / "profile.json", {"stages": {"PILOT_1000": {
        "commands": [{"role": "exact_audited_gds_emx_runner"}]}}})
    backend.update(script_identities=scripts, runtime_identities={
        "stage_execution_profile": identity.file_record(profile_path)})
    write(backend_path, backend)
    backend_pin = identity.file_record(backend_path)
    owner = _process(100)
    lease = _lease(owner)
    lease.update(backend_identity_manifest=backend_pin,
                 contract_fingerprint_sha256=backend["contract_fingerprint_sha256"])
    lease_path = Path(initial["supervisor_lease"]["path"])
    write(lease_path, lease)
    overlay_path = Path(initial["operational_overlay_manifest"]["path"])
    overlay = json.loads(overlay_path.read_text())
    overlay.update(corrected_backend_manifest=backend_pin,
                   queue_id=identity.QUEUE_ID, supervisor_id=identity.SUPERVISOR_ID)
    write(overlay_path, overlay)
    for path in paths:
        item = json.loads(path.read_text())
        item.update(supervisor_lease=identity.file_record(lease_path),
                    operational_overlay_manifest=identity.file_record(overlay_path))
        proof_path = Path(item["per_tool_capacity_evidence"]["path"])
        proof = json.loads(proof_path.read_text())
        proof["supervisor_lease"] = item["supervisor_lease"]
        write(proof_path, proof)
        item["per_tool_capacity_evidence"] = identity.file_record(proof_path)
        write(path, item)
    initial_pin = identity.file_record(paths[0])
    initial = json.loads(paths[0].read_text())
    history = root / "scheduling_history/stage_000046/LATEST.json"
    state_path = history.with_name("STATE_000001.json")
    state = {"schema": "rfic_transformer.broadband56_stage_resource_history.v1",
             "overall_status": "OBSERVED", "error": None, "sampler_pid": owner["pid"],
             "stage": "PILOT_1000", "stage_check_index": 46, "initial_snapshot": initial_pin,
             "latest_snapshot": identity.file_record(paths[-1]),
             "bindings": {k: initial[k] for k in ("campaign_id", "contract_fingerprint_sha256",
                 "supervisor_lease", "operational_overlay_manifest", "owner_swap_override_receipt")}}
    write(state_path, state)
    write(history, identity.file_record(state_path))
    stage = root / "stages/000046_pilot_1000_TEST"
    stage.mkdir(parents=True)
    auth = identity.file_record(write(root / "authorization.json", {"fixture": True}))

    def process(pid, parent, role, args):
        argv = ["/usr/bin/python3", scripts[role]["path"], *args]
        result = _process(pid, parent_pid=parent, command=" ".join(argv))
        result["command_argv"] = argv
        return result

    common = ["--stage", "PILOT_1000", "--campaign-root", str(root),
              "--backend-identity-manifest", str(backend_path), "--max-concurrency", "48",
              "--full-campaign-receipt", auth["path"], "--resource-snapshot", initial_pin["path"]]
    launcher = process(200, 100, "stage_launcher", [*common, "--out-dir", str(stage)])
    worker = process(300, 200, "production_stage_backend",
                     [*common, "--backend-out-dir", str(stage / "backend")])
    audit = {"schema": "rfic_transformer.broadband56_v2_stage_launch_audit.v1",
             "overall_status": "PASS", "stage": "PILOT_1000",
             "campaign_id": identity.CAMPAIGN_ID,
             "contract_fingerprint_sha256": backend["contract_fingerprint_sha256"],
             "backend_identity_manifest": backend_pin,
             "command_argv_sha256": hashlib.sha256(json.dumps(
                 worker["command_argv"][1:], separators=(",", ":")).encode()).hexdigest(),
             "resource_snapshot": initial_pin, "authorization_receipt": auth}
    write(stage / "STAGE_LAUNCH_AUDIT.json", audit)
    context = {"schema": "rfic_transformer.broadband56_v2_stage_context.v2",
               "stage": "PILOT_1000", "campaign_id": identity.CAMPAIGN_ID,
               "contract_fingerprint_sha256": backend["contract_fingerprint_sha256"],
               "campaign_root": str(root), "backend_identity_manifest": backend_pin,
               "stage_execution_profile": identity.file_record(profile_path),
               "initial_resource_snapshot": initial_pin, "resource_snapshot": initial_pin,
               "stage_resource_history": str(history),
               "full_campaign_authorization_receipt": auth, "max_concurrency": 48,
               "current_accepted": 861}
    context_path = write(stage / "backend/STAGE_CONTEXT.json", context)
    role = process(400, 300, "exact_audited_gds_emx_runner",
        ["--stage", "PILOT_1000", "--backend-identity-manifest", str(backend_path),
         "--full-campaign-receipt", auth["path"],
         "--input-role-receipt", str(stage / "backend/roles/00_calibre_input/receipt.json"),
         "--out-dir", str(stage / "backend/roles/01_exact_audited_gds_emx_runner")])
    return {"root": root, "paths": paths, "now": now, "history": history,
            "state_path": state_path, "stage": stage, "context_path": context_path,
            "backend_pin": backend_pin, "lease": lease, "processes": [owner, launcher, worker, role],
            "scripts": scripts}


def evaluate(f, *, scoped=True):
    return _evaluate(f["lease"], f["processes"],
        backend_manifest_sha256=f["backend_pin"]["sha256"], now=f["now"],
        running_stage_history=f["history"] if scoped else None)


def test_real_shape_startup_gate_reproduces_then_scoped_gate_passes(tmp_path):
    f = fixture(tmp_path / identity.CAMPAIGN_ID)
    old = evaluate(f, scoped=False)
    assert old["duplicate_runner_count"] == 2 and not old["isolation_gate_pass"]
    assert old["project_owned_calibre_children"] == 1
    result = evaluate(f)
    assert result["running_stage_error"] is None
    assert result["isolation_gate_pass"] and result["duplicate_runner_count"] == 0
    assert result["bound_stage_process_pids"] == [200, 300, 400]
    assert result["active_simulator_jobs"] == 0  # Python + calibre input path is not native.


@pytest.mark.parametrize("mutation", [
    "owner_uid", "owner_boot", "owner_start", "child_uid", "child_boot", "parent_reuse",
    "wrong_parent", "duplicate_stage", "duplicate_backend", "duplicate_role", "foreign_runner",
    "different_backend_arg", "different_output", "script_bytes", "history_sampler",
    "history_stage", "history_backend", "lease_fingerprint", "context_backend",
    "context_initial", "context_campaign", "context_contract", "context_resource",
    "audit_command", "role_unknown", "role_wrong_stage",
])
def test_running_scope_does_not_hide_identity_conflicts(tmp_path, mutation):
    f = fixture(tmp_path / identity.CAMPAIGN_ID)
    p = f["processes"]
    if mutation == "owner_uid": p[0]["uid"] += 1
    elif mutation == "owner_boot": p[0]["boot_id"] = "changed"
    elif mutation == "owner_start": p[0]["start_ticks"] += 1
    elif mutation == "child_uid": p[1]["uid"] += 1
    elif mutation == "child_boot": p[1]["boot_id"] = "changed"
    elif mutation == "parent_reuse": p[1]["start_ticks"] = p[0]["start_ticks"] - 1
    elif mutation == "wrong_parent": p[1]["parent_pid"] = 999
    elif mutation.startswith("duplicate_"):
        i = {"duplicate_stage": 1, "duplicate_backend": 2, "duplicate_role": 3}[mutation]
        copy = deepcopy(p[i]); copy["pid"] += 1000; p.append(copy)
    elif mutation == "foreign_runner":
        p.append(_process(900, command=f"python run_candidate_queue_dataset_parallel.py {identity.CAMPAIGN_ID}"))
    elif mutation == "different_backend_arg":
        p[1]["command_argv"][p[1]["command_argv"].index("--backend-identity-manifest") + 1] = "/wrong"
    elif mutation == "different_output":
        p[1]["command_argv"][-1] = str(tmp_path / "another_campaign/stages/000046_pilot_1000_TEST")
    elif mutation == "script_bytes":
        Path(f["scripts"]["production_stage_backend"]["path"]).write_text("# changed")
    elif mutation in {"history_sampler", "history_stage", "history_backend"}:
        state = json.loads(f["state_path"].read_text())
        if mutation == "history_sampler": state["sampler_pid"] = 999
        elif mutation == "history_stage": state["stage"] = "GOLDEN"
        else: state["bindings"]["supervisor_lease"] = {"sha256": "0" * 64}
        write(f["state_path"], state); write(f["history"], identity.file_record(f["state_path"]))
    elif mutation == "lease_fingerprint": f["lease"]["contract_fingerprint_sha256"] = "0" * 64
    elif mutation.startswith("context_"):
        context = json.loads(f["context_path"].read_text())
        key = {"context_backend": "backend_identity_manifest",
               "context_initial": "initial_resource_snapshot",
               "context_campaign": "campaign_id", "context_contract": "contract_fingerprint_sha256",
               "context_resource": "resource_snapshot"}[mutation]
        context[key] = {"sha256": "0" * 64}; write(f["context_path"], context)
    elif mutation == "audit_command":
        path = f["stage"] / "STAGE_LAUNCH_AUDIT.json"; audit = json.loads(path.read_text())
        audit["command_argv_sha256"] = "0" * 64; write(path, audit)
    elif mutation == "role_unknown": p[3]["command_argv"][1] = "/unbound.py"
    elif mutation == "role_wrong_stage": p[3]["command_argv"][3] = "PHASE_C"
    result = evaluate(f)
    assert not result["isolation_gate_pass"], (mutation, result)


def test_native_children_are_counted_without_becoming_duplicate_runners(tmp_path):
    f = fixture(tmp_path / identity.CAMPAIGN_ID)
    executable = tmp_path / "emx"
    executable.write_bytes(b"\x7fELF" + b"TEST FIXTURE NOT EXECUTABLE")
    native = _process(500, parent_pid=400, command=f"{executable} {identity.CAMPAIGN_ID}")
    native.update(executable_path=str(executable), executable_sha256=identity.sha256(executable))
    f["processes"].append(native)
    result = evaluate(f)
    assert result["isolation_gate_pass"]
    assert result["project_owned_emx_children"] == result["active_simulator_jobs"] == 1
    assert result["duplicate_runner_count"] == 0


def test_complete_history_to_real_stage_admission_no_counter_mock(tmp_path):
    f = fixture(tmp_path / identity.CAMPAIGN_ID)
    result = evaluate(f)
    assert result["isolation_gate_pass"], result
    for path in f["paths"][1:]:
        item = json.loads(path.read_text())
        item["isolation"] = result
        write(path, item)
    state = json.loads(f["state_path"].read_text())
    state["latest_snapshot"] = identity.file_record(f["paths"][-1])
    write(f["state_path"], state); write(f["history"], identity.file_record(f["state_path"]))
    admission = StageAdmission(f["context_path"], f["history"], 48)
    assert admission() == 48
    assert admission.last_decision["healthy_check_streak"] == 5
    assert admission() == 48  # Same snapshot is cached, not an extra check.
    assert admission.last_decision["healthy_check_streak"] == 5


@pytest.mark.parametrize("transit_uid", [0, 1001])
def test_protected_container_parent_is_ancestry_not_a_solver(tmp_path, transit_uid):
    f = fixture(tmp_path / identity.CAMPAIGN_ID)
    transit = _process(450, parent_pid=400, command="Singularity runtime parent")
    transit.update(uid=transit_uid, protected_container_transit=True, counted_as_native_solver=False)
    executable = tmp_path / "emx"
    executable.write_bytes(b"\x7fELF" + b"TEST FIXTURE NOT EXECUTABLE")
    native = _process(500, parent_pid=450, command=f"{executable} {identity.CAMPAIGN_ID}")
    native.update(executable_path=str(executable), executable_sha256=identity.sha256(executable))
    f["processes"].extend([transit, native])
    result = evaluate(f)
    assert result["isolation_gate_pass"]
    assert result["active_simulator_jobs"] == 1 and result["bound_stage_native_counts"]["emx"] == 1


@pytest.mark.parametrize("mutation", ["uid", "boot", "parent_ticks", "detached", "exe_hash"])
def test_native_outside_verified_tree_remains_rejected(tmp_path, mutation):
    f = fixture(tmp_path / identity.CAMPAIGN_ID)
    executable = tmp_path / "emx"
    executable.write_bytes(b"\x7fELF" + b"TEST FIXTURE NOT EXECUTABLE")
    native = _process(500, parent_pid=400, command=f"{executable} {identity.CAMPAIGN_ID}")
    native.update(executable_path=str(executable), executable_sha256=identity.sha256(executable))
    if mutation == "uid": native["uid"] += 1
    elif mutation == "boot": native["boot_id"] = "another-boot"
    elif mutation == "parent_ticks": native["start_ticks"] = 1
    elif mutation == "detached": native["parent_pid"] = 999
    else: native["executable_sha256"] = "0" * 64
    f["processes"].append(native)
    assert not evaluate(f)["isolation_gate_pass"]


@pytest.mark.parametrize("running", [False, True])
def test_probe_constructs_explicit_isolation_scope_only_for_running_stage(tmp_path, monkeypatch, running):
    from types import SimpleNamespace
    from tests.test_broadband56_swap_override_control_scripts import CONTROLLER

    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        directory = Path(command[command.index("--out-dir") + 1])
        directory.mkdir()
        write(directory / "ISOLATION_IDENTITY_AUDIT.json", {"fixture": True})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(CONTROLLER.subprocess, "run", fake_run)
    history = tmp_path / "history/LATEST.json" if running else None
    CONTROLLER._run_isolation_identity_audit(
        controller=SimpleNamespace(ControllerError=RuntimeError),
        python_bin=Path("/TEST_ONLY/python"), auditor_path=tmp_path / "audit.py",
        lease_path=tmp_path / "lease.json", backend_manifest_path=tmp_path / "backend.json",
        campaign_lock=tmp_path / "campaign.lock", out_dir=tmp_path, check_index=46,
        running_stage_history=history)
    assert len(calls) == 1
    command = calls[0]
    assert ("--running-stage-history" in command) == running
    if running:
        assert command[command.index("--running-stage-history") + 1] == str(history)
