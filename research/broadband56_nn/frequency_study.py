"""One ordinary, idempotent frequency-pair job; production is strictly read-only.

Use run for initial work and resume for interrupted work. A scheduler may call
run periodically: data/resource waits are harmless; completed jobs are no-ops.
Existing incomplete outputs require explicit resume, never a second submission.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .io import read_json, sha256, utc_now, load_checkpoint, canonical_sha
from .study_once import lease, BusyStudy, atomic_json, resource_snapshot, run_child, pin, verify_pin


def _state(root, status, **details):
    value = {"schema": "frequency_study_state.v1", "status": status,
             "observed_utc": utc_now(), "pid": os.getpid(), **details}
    atomic_json(root / "RUN_STATE.json", value)
    print(json.dumps(value), flush=True)
    return value


def _data(request, root):
    if request.get("data_root"):
        from .training import Bundle
        bundle = Bundle(request["data_root"])
        if bundle.data_sha != request["dataset_sha256"]:
            raise ValueError("prepared dataset identity changed")
        return str(bundle.root)
    from .research_data_access import probe_and_localize
    from .snapshot_10k import freeze_selection, prepare_selection
    prepared = root / "data" / "data_manifest.json"
    if prepared.is_file():
        from .training import Bundle
        bundle = Bundle(prepared.parent)
        if len(bundle.arrays["geometry"]) != 10000:
            raise ValueError("formal snapshot is not 10000 geometries")
        selection = root / "selection" / "SELECTION_MANIFEST.json"
        source = bundle.manifest.get("selection_manifest", {})
        if source.get("sha256") != sha256(selection):
            raise ValueError("prepared formal dataset does not match this study selection")
        return str(bundle.root)
    selection = root / "selection" / "SELECTION_MANIFEST.json"
    if not selection.exists():
        probe = probe_and_localize(**request["source_access"])
        if probe["status"] != "SOURCE_TRANSPORT_VERIFIED_PENDING_SELECTION":
            _state(root, probe["status"], source_probe=probe.get("receipt"),
                   committed_accepted=probe.get("committed_accepted"))
            return None
        freeze_selection(probe["source_manifest"]["path"], selection.parent,
                         previous_splits=request.get("previous_splits"), seed=17)
    prepare_selection(selection, prepared.parent, sha256(selection))
    return str(prepared.parent)


def _attempts(role_root):
    attempts = sorted(p for p in role_root.glob("attempt_*")
                      if p.is_dir() and re.fullmatch(r"attempt_\d{4}", p.name))
    if [p.name for p in attempts] != [f"attempt_{i:04d}" for i in range(1, len(attempts)+1)]:
        raise ValueError("attempt sequence has a gap; preserve for reconciliation")
    return attempts


def _receipt(path, request, role, data, data_sha, forward=None):
    from .bb00 import BB00Config
    path = Path(path)
    result = read_json(path)
    expected = {"schema": "bb00_training_receipt.v1", "kind": "BB00", "role": role,
        "frequency_ghz": request["train"]["frequency_ghz"], "label_mode": request["train"]["label_mode"],
        "data_sha": data_sha, "resume_probe": False, "research_comparison_eligible": True,
        "trainable_weights_changed": True, "frozen_forward_unchanged": True}
    if any(result.get(k) != value for k, value in expected.items()):
        raise ValueError("terminal receipt role/frequency/data/eligibility differs")
    if result.get("updates_this_run", 0) < 1:
        raise ValueError("no actual optimizer update in terminal receipt")
    config_path = path.parent.parent / ("config_"+path.parent.name.split("_")[-1]+".json")
    config = read_json(config_path)
    desired = dict(request["train"], role=role, log_progress=True)
    if role == "inverse":
        desired["forward_checkpoint"] = forward
    if canonical_sha(config["train"]) != canonical_sha(asdict(BB00Config(**desired))):
        raise ValueError("terminal training configuration differs from study request")
    if str(Path(config["data_root"]).resolve()) != str(Path(data).resolve()):
        raise ValueError("terminal data path differs")
    for key in ("contract_path", "legacy_replay_receipt", "legacy_replay_sha256"):
        if config.get(key) != request[key]:
            raise ValueError("terminal recipe/contract identity differs")
    for key in ("best", "last"):
        verify_pin({"path": result[key+"_checkpoint"], "sha256": result[key+"_sha256"]})
    return result


def _verify_pair(pair, request, identity):
    if (pair.get("schema") != "frequency_pair_receipt.v1" or set(pair.get("roles", {})) != {"forward", "inverse"}
            or pair.get("request") != identity or pair.get("frequency_ghz") != request["train"]["frequency_ghz"]
            or pair.get("label_mode") != request["train"]["label_mode"]
            or pair.get("experiment_class") != request["experiment_class"]):
        raise ValueError("completed pair does not exactly match this study")
    data = pair["data_root"]
    data_sha = request.get("dataset_sha256") or read_json(Path(data)/"data_manifest.json")["artifacts"]["dataset.npz"]["sha256"]
    inverse_forward = _inverse_forward(request, pair["roles"]["forward"], data_sha)
    if request.get("shared_forward_receipt") is not None and "inverse_forward" not in pair:
        raise ValueError("shared-forward pair requires explicit inverse_forward identity")
    if pair.get("inverse_forward", inverse_forward) != inverse_forward:
        raise ValueError("completed pair shared-forward identity differs")
    for role in ("forward", "inverse"):
        entry = pair["roles"][role]
        for item in ("receipt", "best", "last"):
            verify_pin(entry[item])
        path = Path(entry["receipt"]["path"])
        if _attempts(path.parent.parent)[-1] != path.parent:
            raise ValueError("a newer incomplete attempt exists after the completed pair")
        result = _receipt(path, request, role, data, data_sha,
                          inverse_forward["best"]["path"] if role == "inverse" else None)
        for key in ("best", "last"):
            if result[key+"_sha256"] != entry[key]["sha256"] or result[key+"_checkpoint"] != entry[key]["path"]:
                raise ValueError("completed pair checkpoint and receipt differ")


def _inverse_forward(request, own_forward, data_sha):
    """Optional common frozen ruler for the five-capacity development study.

    The arm still trains its own forward for the independent forward comparison.
    Never substitute that forward's metrics for the shared inverse-training ruler.
    """
    source = request.get("shared_forward_receipt")
    if source is None:
        return {"receipt": own_forward["receipt"], "best": own_forward["best"], "source": "OWN_FORWARD"}
    if request.get("experiment_class") != "DEVELOPMENT_CURRENT_SNAPSHOT":
        raise ValueError("shared-forward extension is scoped to the development snapshot")
    result = read_json(verify_pin(source))
    expected = {"schema": "bb00_training_receipt.v1", "role": "forward", "kind": "BB00",
        "data_sha": data_sha, "frequency_ghz": request["train"]["frequency_ghz"],
        "label_mode": request["train"]["label_mode"], "resume_probe": False,
        "research_comparison_eligible": True, "validation_selected_checkpoint": True}
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("shared forward receipt qualification mismatch")
    best = pin(result["best_checkpoint"])
    if best["sha256"] != result["best_sha256"]:
        raise ValueError("shared forward best checkpoint changed")
    return {"receipt": source, "best": best, "source": "SHARED_FROZEN_FORWARD"}


def _finish(request, root, lock_fds):
    if not request.get("posttrain"):
        return None
    from .frequency_posttrain import finalize_pair
    try:
        result = finalize_pair(request, root, lock_fds=lock_fds)
        return _state(root, result["status"], posttrain=pin(root/"posttrain"/"POSTTRAIN_RECEIPT.json"),
                      REAL_EMX_VALIDATION="NOT_RUN", automatic_visual_qa="NOT_RUN")
    except Exception as error:
        return _state(root, "BLOCKED", phase="posttrain", reason=str(error),
                      next_action="Inspect preserved evidence; no automatic duplicate training or stage retry")


def run(request_path, *, resume=False):
    from .bb00 import BB00Config
    raw = Path(request_path).read_bytes()
    request = json.loads(raw)
    if request.get("schema") != "frequency_study_request.v1":
        raise ValueError("unsupported frequency study request")
    root = Path(request["out"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with lease(root / "study.lock") as study_fd, lease(request["device_lock"]) as device_fd:
            binding = root / "REQUEST_IDENTITY.json"
            identity = pin(request_path)
            if identity["sha256"] != hashlib.sha256(raw).hexdigest():
                raise ValueError("request changed while admitting study")
            if binding.exists():
                if read_json(binding)["sha256"] != identity["sha256"]:
                    raise ValueError("study request changed; never overwrite an existing experiment")
            else:
                atomic_json(binding, identity, immutable=True)
            if (root/"ADMISSION_FAILED.json").exists() and not resume:
                return _state(root, "BLOCKED", failure=pin(root/"ADMISSION_FAILED.json"),
                    reason="Preserved admission failure; explicit inspection/resume required")
            if (root / "PAIR_RECEIPT.json").exists():
                result = read_json(root / "PAIR_RECEIPT.json")
                _verify_pair(result, request, identity)
                finished = _finish(request, root, (study_fd, device_fd))
                if finished is not None:
                    return finished
                return _state(root, "ALREADY_TRAINED", pair=pin(root / "PAIR_RECEIPT.json"),
                              evaluation="SEPARATE_REQUIRED_STEP")
            from datetime import datetime, timezone
            deadline = datetime.fromisoformat(request["train"]["deadline_utc"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= deadline:
                return _state(root, "PARTIAL_BUDGET_EXHAUSTED", reason="No new optimizer update authorized after deadline")
            resources = resource_snapshot(root, device=request["train"]["device"],
                min_available_bytes=request["resources"]["min_available_bytes"],
                min_disk_bytes=request["resources"]["min_disk_bytes"])
            if resources["status"] != "PASS":
                return _state(root, "WAITING_RESOURCE", resources=resources)
            data = _data(request, root)
            if data is None:
                return read_json(root / "RUN_STATE.json")
            data_sha = request.get("dataset_sha256") or read_json(Path(data)/"data_manifest.json")["artifacts"]["dataset.npz"]["sha256"]
            records = {}
            for role in ("forward", "inverse"):
                inverse_forward = _inverse_forward(request, records["forward"], data_sha) if role == "inverse" else None
                role_root = root / role
                attempts = _attempts(role_root)
                receipt_path = attempts[-1]/"TRAINING_RECEIPT.json" if attempts else None
                if receipt_path is not None and receipt_path.is_file():
                    receipt = _receipt(receipt_path, request, role, data, data_sha,
                        inverse_forward["best"]["path"] if role == "inverse" else None)
                    if receipt["stop_reason"] in ("VALIDATION_EARLY_STOP", "UPDATE_BUDGET_COMPLETE"):
                        records[role] = {"receipt": pin(receipt_path), "best": pin(receipt["best_checkpoint"]),
                                         "last": pin(receipt["last_checkpoint"]), "status": receipt["status"]}
                        continue
                resume_path = None
                if attempts:
                    if not resume:
                        return _state(root, "NEEDS_RESUME", role=role, reason="Existing incomplete attempt; never duplicate")
                    checkpoints = sorted(attempts[-1].glob("checkpoint_step_*.pt"))
                    if not checkpoints:
                        return _state(root, "BLOCKED", role=role, reason="Interrupted attempt has no checkpoint; retain evidence")
                    resume_path = checkpoints[-1]
                    load_checkpoint(resume_path)
                attempt = role_root / f"attempt_{len(attempts)+1:04d}"
                config = {k: request[k] for k in ("contract_path", "legacy_replay_receipt", "legacy_replay_sha256")}
                config.update(schema="frequency_tandem_train.v1", data_root=data, resource_admission=resources)
                train = dict(request["train"], role=role, log_progress=True)
                if role == "inverse":
                    train["forward_checkpoint"] = inverse_forward["best"]["path"]
                config["train"] = asdict(BB00Config(**train))
                config_path = role_root / f"config_{len(attempts)+1:04d}.json"
                atomic_json(config_path, config, immutable=True)
                command = [sys.executable, "-B", "-m", "research.broadband56_nn.frequency_tandem", "train",
                           "--config", str(config_path), "--out", str(attempt)]
                if resume_path:
                    command += ["--resume", str(resume_path)]
                _state(root, "RUNNING", role=role, resources=resources, command=command,
                       attempt=str(attempt), experiment_class=request["experiment_class"])
                child = run_child(command, Path(__file__).resolve().parents[2],
                                  role_root / f"attempt_{len(attempts)+1:04d}.log", (study_fd, device_fd))
                atomic_json(role_root / f"completion_{len(attempts)+1:04d}.json", child, immutable=True)
                receipt_path = attempt / "TRAINING_RECEIPT.json"
                if child["returncode"] != 0 or not receipt_path.exists():
                    return _state(root, "FAILED", role=role, child=child, reason="Preserved failure; explicit inspection/resume only")
                receipt = _receipt(receipt_path, request, role, data, data_sha,
                    inverse_forward["best"]["path"] if role == "inverse" else None)
                if receipt["stop_reason"] not in ("VALIDATION_EARLY_STOP", "UPDATE_BUDGET_COMPLETE"):
                    return _state(root, "PARTIAL", role=role, receipt=pin(receipt_path))
                records[role] = {"receipt": pin(receipt_path), "best": pin(receipt["best_checkpoint"]),
                                 "last": pin(receipt["last_checkpoint"]), "status": receipt["status"]}
            result = {"schema": "frequency_pair_receipt.v1", "status": "TRAINED_BUDGET_OR_EARLY_STOP",
                "convergence_claim": False, "roles": records, "request": identity,
                "frequency_ghz": request["train"]["frequency_ghz"], "label_mode": request["train"]["label_mode"],
                "experiment_class": request["experiment_class"], "data_root": data,
                "inverse_forward": _inverse_forward(request, records["forward"], data_sha),
                "REAL_EMX_VALIDATION": "NOT_RUN", "created_utc": utc_now()}
            atomic_json(root / "PAIR_RECEIPT.json", result, immutable=True)
            finished = _finish(request, root, (study_fd, device_fd))
            if finished is not None:
                return finished
            return _state(root, "TRAINED", pair=pin(root / "PAIR_RECEIPT.json"),
                          evaluation="SEPARATE_REQUIRED_STEP")
    except BusyStudy:
        return {"status": "BUSY", "training_submitted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "resume"))
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    try:
        result = run(args.request, resume=args.action == "resume")
    except Exception as error:
        request = read_json(args.request)
        root = Path(request["out"]).resolve()
        failure = root/"ADMISSION_FAILED.json"
        if not failure.exists():
            atomic_json(failure, {"status":"NO_GO", "created_utc":utc_now(),
                "request":pin(args.request), "error":type(error).__name__+": "+str(error)}, immutable=True)
        result = _state(root, "BLOCKED", failure=pin(failure), reason=str(error),
                        next_action="Inspect and explicitly resume; no automatic retry")
    print(json.dumps(result), flush=True)
    return 1 if result["status"] in ("FAILED", "BLOCKED", "NEEDS_RESUME") else 0


if __name__ == "__main__":
    raise SystemExit(main())
