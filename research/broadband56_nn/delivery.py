"""Private, no-clobber research packaging and separate-process resume evidence.

This module never contacts MARS, invokes an EM solver, or changes production.
Checkpoint bytes remain unchanged; relocation is through explicit overrides.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np
import torch

from .io import canonical_sha, load_checkpoint, read_json, save_json, sha256, utc_now
from .models import PACKAGE_MAPPING, build_forward, build_inverse
from .specs import make_spec, tokenize
from .training import Bundle, decoder_from_contract, load_forward, model_digest


RUN_LABELS = ("F1", "F2", "F3", "FREF", *PACKAGE_MAPPING)
REQUIRED_RESUME_CHECKS = (
    "optimizer_step_increment", "scheduler_restored", "sampler_rng_exact_resume",
    "one_real_update", "same_data", "same_normalizer", "trainable_weights_changed",
    "frozen_forward_unchanged", "parent_identity", "new_process",
    "original_best_unchanged", "original_last_unchanged", "fresh_worker_identity",
    "finite_outputs", "same_contract", "same_architecture",
)


class ResumeDeadlineError(ValueError):
    """A supplied research deadline is not a timezone-aware timestamp."""


def validate_resume_deadline(deadline_utc, *, allow_expired=False):
    """Validate without normalizing/renewing the exact caller-supplied deadline.

    None preserves the standalone API, not an authorization or budget exemption.
    Trainers check before updates; an in-flight update is not forcibly killed.
    """
    if deadline_utc is None:
        return None
    try:
        if not isinstance(deadline_utc, str) or not deadline_utc:
            raise ValueError("timestamp string required")
        parsed = datetime.fromisoformat(deadline_utc.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone required")
    except (ValueError, TypeError) as error:
        raise ResumeDeadlineError("invalid timezone-aware resume deadline") from error
    if not allow_expired and datetime.now(timezone.utc) >= parsed:
        raise TimeoutError("original research resume deadline exhausted")
    return parsed


def _resume_command(argv, log_path, *, deadline_utc=None, lock_fds=()):
    """Last parent-side gate; existing trainer also checks before each update."""
    validate_resume_deadline(deadline_utc)
    command = list(argv)
    if deadline_utc is not None:
        command += ["--deadline-utc", deadline_utc]
    return _command(command, log_path, lock_fds=lock_fds)


def _run_directory(root: Path, label: str) -> Path:
    return root / "shared_forward" / label if label.startswith("F") else root / label


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)


def _copy_pin(source: Path, destination: Path) -> dict:
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"source is not a regular file: {source}")
    expected = sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Hardlinks would let a user overwrite the sole historical checkpoint through
    # its packaged alias. Independent copies keep historical evidence isolated.
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
    actual = sha256(destination)
    if actual != expected:
        raise ValueError(f"copied artifact hash differs: {source}")
    return {"path": str(destination), "sha256": actual, "size_bytes": destination.stat().st_size}


def _checkpoint_pair(directory: Path, data_sha: str, label: str | None = None) -> tuple[dict, dict]:
    receipt = read_json(directory / "TRAINING_RECEIPT.json")
    if (receipt.get("status") not in ("PRETRAINED", "PRETRAINED_PARTIAL") or
            receipt.get("data_sha") != data_sha or receipt.get("updates_this_run", 0) <= 0 or
            receipt.get("trainable_weights_changed") is not True):
        raise ValueError(f"run is not trained on the supplied snapshot: {directory}")
    states = {}
    for name in ("best", "last"):
        path = Path(receipt[f"{name}_checkpoint"])
        if sha256(path) != receipt[f"{name}_sha256"]:
            raise ValueError(f"training receipt {name} checkpoint SHA differs")
        state = load_checkpoint(path)
        if state["data_sha"] != data_sha or state["step"] <= 0:
            raise ValueError("checkpoint is not a trained state for the supplied snapshot")
        if label:
            expected_role = "forward" if label.startswith("F") else "inverse"
            expected_kind = None if label == "FREF" else label if expected_role == "forward" else PACKAGE_MAPPING[label][1]
            if state["role"] != expected_role or (expected_kind and state["kind"] != expected_kind):
                raise ValueError(f"{label}: fixed package role/architecture mapping mismatch")
            if label == "FREF" and state["kind"] not in ("F1", "F2", "F3"):
                raise ValueError("FREF must be a declared forward architecture")
        if canonical_sha(state["contract"]) != state["contract_sha"]:
            raise ValueError("checkpoint contract digest mismatch")
        states[name] = state
    best, last = states["best"], states["last"]
    for key in ("role", "kind", "geometry_dim", "normalizer_sha", "contract_sha", "forward_model_sha", "architecture"):
        if best[key] != last[key]:
            raise ValueError(f"best/last checkpoint {key} mismatch")
    if best["step"] > last["step"] or last["step"] != receipt["completed_step"]:
        raise ValueError("best/last/receipt training step mismatch")
    if sha256(last["best_checkpoint"]) != receipt["best_sha256"]:
        raise ValueError("last checkpoint selected-best reference differs from receipt")
    if best["best_validation"] != last["best_validation"] or last["best_validation"] != receipt["best_validation"]:
        raise ValueError("selected-best validation evidence differs")
    return receipt, states


def _campaign_terminal(root: Path) -> dict:
    path = root / "CAMPAIGN_RECEIPT.json"
    if not path.is_file():
        raise ValueError("campaign terminal receipt required before any resume verification")
    receipt = read_json(path)
    results = receipt.get("results")
    if not isinstance(results, dict) or set(results) != set(RUN_LABELS):
        raise ValueError("campaign terminal receipt lacks the exact ten stage results")
    if any(row.get("status") not in ("PRETRAINED", "PRETRAINED_PARTIAL", "FAILED", "BLOCKED") for row in results.values()):
        raise ValueError("campaign receipt contains a nonterminal stage")
    return receipt


def _require_forward_reference(states: dict, label: str, root: Path) -> None:
    if label not in PACKAGE_MAPPING:
        return
    expected = read_json(_run_directory(root, PACKAGE_MAPPING[label][0]) / "TRAINING_RECEIPT.json")
    reference = read_json(_run_directory(root, "FREF") / "TRAINING_RECEIPT.json")
    if sha256(expected["best_checkpoint"]) != expected["best_sha256"] or sha256(reference["best_checkpoint"]) != reference["best_sha256"]:
        raise ValueError("shared forward/reference receipt checkpoint SHA mismatch")
    expected_model = load_checkpoint(expected["best_checkpoint"])["model_sha"]
    reference_model = load_checkpoint(reference["best_checkpoint"])["model_sha"]
    for state in states.values():
        if sha256(state["forward_checkpoint"]) != expected["best_sha256"] or state["forward_model_sha"] != expected_model:
            raise ValueError(f"{label}: inverse does not use the exact package-mapped shared forward")
        if state["forward_model_sha"] == reference_model:
            raise ValueError("independent FREF was used for inverse gradient training")


def _output_arrays(bundle: Bundle, state: dict, device: str, forward_path: str | None) -> dict:
    torch.set_num_threads(2)
    if device == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS requested but unavailable")
        torch.mps.set_per_process_memory_fraction(.25)
    indices = bundle.train[:8]
    if not len(indices):
        raise ValueError("no training geometries for fixed-input load verification")
    batch = bundle.batch(indices, device)
    factory = build_forward if state["role"] == "forward" else build_inverse
    model = factory(state["kind"], bundle.dim).to(device)
    model.load_state_dict(state["model_state"])
    if model_digest(model) != state["model_sha"]:
        raise ValueError("loaded model state digest differs from recorded digest")
    model.eval()
    with torch.no_grad():
        if state["role"] == "forward":
            values = {"normalized_s": model(bundle.g_normalize(batch["geometry"]), bundle.frequency)}
        else:
            spec = make_spec(batch["s"], batch["y"], batch["y_valid"], bundle.frequency,
                             np.random.default_rng(170043), task="SPECTRUM", mode="full")
            tokens, mask = tokenize(spec, bundle.norm)
            logits = model(tokens, mask)
            geometry = decoder_from_contract(state["contract"], device)(logits)
            forward, _ = load_forward(forward_path or state["forward_checkpoint"], bundle, device)
            if model_digest(forward) != state["forward_model_sha"]:
                raise ValueError("load proof forward identity differs")
            values = {"logits": logits, "geometry_um": geometry,
                      "normalized_s": forward(bundle.g_normalize(geometry), bundle.frequency)}
    result = {key: value.detach().cpu().numpy() for key, value in values.items()}
    if any(not np.isfinite(value).all() for value in result.values()):
        raise ValueError("loaded model produced nonfinite fixed-input outputs")
    result["training_indices"] = indices.copy()
    del model, values, batch
    gc.collect()
    if device == "mps":
        torch.mps.empty_cache()
    return result


def _fresh_worker(data: str, best: str, last: str, out: str, device: str,
                  forward_checkpoint: str | None = None) -> None:
    destination = Path(out)
    destination.mkdir(parents=True, exist_ok=False)
    bundle = Bundle(data)
    pins = {}
    for name, path in (("best", best), ("last", last)):
        state = load_checkpoint(path)
        if state["data_sha"] != bundle.data_sha or state["normalizer_sha"] != bundle.norm_sha:
            raise ValueError("fresh worker snapshot/normalizer mismatch")
        arrays = _output_arrays(bundle, state, device, forward_checkpoint)
        with (destination / f"{name}_outputs.npz").open("xb") as stream:
            np.savez(stream, **arrays)
        pins[name] = {"checkpoint_sha256": sha256(path),
                      "output_sha256": sha256(destination / f"{name}_outputs.npz")}
    save_json(destination / "FRESH_PROCESS_RECEIPT.json", {"status": "PASS", "pid": os.getpid(),
              "created_utc": utc_now(), "data_sha": bundle.data_sha, "normalizer_sha": bundle.norm_sha,
              "input_rule": "first eight training geometries; inverse uses fixed full-S specification",
              "test_evaluated": False, "device": device, "artifacts": pins})


def _command(command: list[str], log: Path, software_root: Path | None = None, *, lock_fds=()) -> dict:
    started = time.monotonic()
    env = os.environ.copy()
    root = str((software_root or Path(__file__).resolve().parents[2]).resolve())
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with log.open("x", encoding="utf-8") as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, env=env, cwd=root,
                                   pass_fds=tuple(lock_fds))
        result = {"pid": process.pid, "command": command, "started_utc": utc_now()}
        result["returncode"] = process.wait()
    result.update(elapsed_seconds=time.monotonic() - started, log_sha256=sha256(log), log_path=str(log))
    if result["returncode"]:
        save_json(log.with_suffix(".failure.json"), result)
        raise RuntimeError(f"fresh process exited {result['returncode']}; evidence: {log}")
    return result


def _resume_contract(state: dict, directory: Path) -> tuple[Path, Path | None]:
    parity_path = state["train_config"].get("physical_parity_receipt") if state["train_config"].get("physical_ready") else None
    if parity_path:
        parity_path = Path(parity_path)
        parity = read_json(parity_path)
        path = Path(parity["contract_path"])
        if sha256(path) != parity["contract_sha"]:
            raise ValueError("original parity-bound contract bytes changed")
    else:
        path = directory / "contract.json"
    if canonical_sha(read_json(path)) != state["contract_sha"]:
        raise ValueError("resume contract is not canonically identical to checkpoint")
    return path, parity_path


def _optimizer_steps(state: dict) -> dict[str, float]:
    return {str(key): float(value["step"]) for key, value in state["optimizer_state"]["state"].items() if "step" in value}


def _expected_sampler_state(state: dict, bundle: Bundle, micro_batch: int) -> dict:
    generator = np.random.default_rng()
    generator.bit_generator.state = copy.deepcopy(state["rng_state"]["sampler"])
    indices = generator.choice(bundle.train, size=32, replace=True)
    if state["role"] == "inverse":
        for offset in range(0, 32, micro_batch):
            batch = bundle.batch(indices[offset:offset + micro_batch], "cpu")
            make_spec(batch["s"], batch["y"], batch["y_valid"], bundle.frequency, generator,
                      task=None if state["train_config"]["physical_ready"] else "SPECTRUM")
    return generator.bit_generator.state


def verify_load_resume(data: str | Path, runs_root: str | Path, out: str | Path,
                       device: str = "mps", *, lock_fds=(), deadline_utc=None,
                       training_budget_sha256=None) -> dict:
    """Run only after the main training campaign is terminal, never alongside it."""
    data, root, output = Path(data).resolve(), Path(runs_root).resolve(), Path(out).resolve()
    terminal = _campaign_terminal(root)
    output.mkdir(parents=True, exist_ok=False)
    binding = {"effective_deadline_utc": deadline_utc,
               "training_budget_sha256": training_budget_sha256,
               "deadline_semantics": "admission and update-start checks; no hard in-flight interruption"}
    try:
        validate_resume_deadline(deadline_utc)
    except (ResumeDeadlineError, TimeoutError) as error:
        receipt = {"schema": "bb_load_resume_receipt.v1", "status": "FAIL", **binding,
                   "created_utc": utc_now(), "error": f"{type(error).__name__}: {error}",
                   "results": {}, "remaining_probes": "NOT_RUN", "test_evaluated": False,
                   "production_modified": False}
        save_json(output / "LOAD_RESUME_RECEIPT.json", receipt)
        return receipt
    bundle = Bundle(data)
    results = {}
    for label in RUN_LABELS:
        destination = output / label
        destination.mkdir()
        directory = _run_directory(root, label)
        stop_remaining = False
        try:
            validate_resume_deadline(deadline_utc)
            original, states = _checkpoint_pair(directory, bundle.data_sha, label)
            _require_forward_reference(states, label, root)
            if canonical_sha(original) != canonical_sha(terminal["results"][label]):
                raise ValueError("campaign result differs from the immutable run receipt")
            started = time.monotonic()
            forward = states["last"].get("forward_checkpoint")
            comparison = {}
            parent_outputs = {name: _output_arrays(bundle, state, device, forward) for name, state in states.items()}
            worker = [sys.executable, "-m", "research.broadband56_nn.delivery", "_load-worker",
                      "--data", str(data), "--best", original["best_checkpoint"],
                      "--last", original["last_checkpoint"], "--out", str(destination / "fresh_load"),
                      "--device", device]
            if forward:
                worker += ["--forward-checkpoint", forward]
            worker_process = _command(worker, destination / "fresh_load.log", lock_fds=lock_fds)
            worker_receipt = read_json(destination / "fresh_load" / "FRESH_PROCESS_RECEIPT.json")
            worker_identity = (worker_receipt.get("status") == "PASS" and
                               worker_receipt.get("pid") == worker_process["pid"] and
                               worker_receipt.get("data_sha") == bundle.data_sha and
                               worker_receipt.get("normalizer_sha") == bundle.norm_sha)
            if not worker_identity:
                raise ValueError("fresh worker receipt process/data identity differs")
            tolerance = 1e-5 if device == "mps" else 1e-8
            for name, expected in parent_outputs.items():
                comparison[name] = {}
                output_path = destination / "fresh_load" / f"{name}_outputs.npz"
                if (worker_receipt["artifacts"][name]["checkpoint_sha256"] != original[f"{name}_sha256"] or
                        worker_receipt["artifacts"][name]["output_sha256"] != sha256(output_path)):
                    raise ValueError("fresh worker checkpoint/output hash binding differs")
                with np.load(destination / "fresh_load" / f"{name}_outputs.npz", allow_pickle=False) as actual:
                    if set(actual.files) != set(expected):
                        raise ValueError("fresh output fields differ")
                    for key, values in expected.items():
                        if not np.isfinite(actual[key]).all() or not np.isfinite(values).all():
                            raise ValueError("nonfinite fresh-load outputs cannot pass equality verification")
                        if key == "training_indices":
                            np.testing.assert_array_equal(actual[key], values)
                        else:
                            np.testing.assert_allclose(actual[key], values, rtol=tolerance, atol=tolerance,
                                                       equal_nan=False, err_msg=f"{label}/{name}/{key}")
                        comparison[name][key] = float(np.max(np.abs(actual[key] - values)))
            contract, parity = _resume_contract(states["last"], directory)
            resume = [sys.executable, "-m", "research.broadband56_nn", "resume", "--data", str(data),
                      "--checkpoint", original["last_checkpoint"], "--contract", str(contract),
                      "--best-checkpoint", original["best_checkpoint"],
                      "--best-checkpoint-sha256", original["best_sha256"],
                      "--out", str(destination / "resume_branch"), "--steps", "1",
                      "--validation-interval", "1", "--micro-batch", "8", "--device", device]
            if forward:
                resume += ["--forward-checkpoint", forward]
            if parity:
                resume += ["--physical-parity-receipt", str(parity)]
            # Any failed resume child is terminal for this verification attempt;
            # do not retry the next role after a timeout/zero-update failure.
            stop_remaining = True
            resume_process = _resume_command(resume, destination / "resume.log",
                                              deadline_utc=deadline_utc, lock_fds=lock_fds)
            proof = read_json(destination / "resume_branch" / "TRAINING_RECEIPT.json")
            if sha256(proof["last_checkpoint"]) != proof["last_sha256"]:
                raise ValueError("resume receipt checkpoint SHA mismatch")
            after = load_checkpoint(proof["last_checkpoint"])
            before = states["last"]
            old_steps, new_steps = _optimizer_steps(before), _optimizer_steps(after)
            checks = {
                "optimizer_step_increment": bool(old_steps) and old_steps.keys() == new_steps.keys() and all(new_steps[k] == v + 1 for k, v in old_steps.items()),
                "scheduler_restored": after["scheduler_state"]["last_epoch"] == before["scheduler_state"]["last_epoch"] + 1,
                "sampler_rng_exact_resume": canonical_sha(after["rng_state"]["sampler"]) == canonical_sha(_expected_sampler_state(before, bundle, 8)),
                "one_real_update": after["step"] == before["step"] + 1 and proof["updates_this_run"] == 1,
                "same_data": after["data_sha"] == before["data_sha"] == bundle.data_sha,
                "same_normalizer": after["normalizer_sha"] == before["normalizer_sha"] == bundle.norm_sha,
                "trainable_weights_changed": after["model_sha"] != before["model_sha"] and proof["trainable_weights_changed"],
                "frozen_forward_unchanged": after["forward_model_sha"] == before["forward_model_sha"] and proof["frozen_forward_unchanged"],
                "parent_identity": after["parent_checkpoint"]["sha256"] == original["last_sha256"],
                "new_process": worker_process["pid"] != os.getpid() and resume_process["pid"] != os.getpid(),
                "fresh_worker_identity": worker_identity,
                "finite_outputs": True,
                "same_contract": after["contract_sha"] == before["contract_sha"],
                "same_architecture": after["architecture"] == before["architecture"],
                "original_best_unchanged": sha256(original["best_checkpoint"]) == original["best_sha256"],
                "original_last_unchanged": sha256(original["last_checkpoint"]) == original["last_sha256"],
                "effective_deadline_bound": after["train_config"].get("deadline_utc") == deadline_utc,
            }
            if not all(checks.values()):
                raise ValueError(f"resume checks failed: {[name for name, passed in checks.items() if not passed]}")
            result = {"status": "PASS", "label": label, "original_best_sha256": original["best_sha256"],
                      "original_last_sha256": original["last_sha256"], "data_sha": bundle.data_sha,
                      "normalizer_sha": bundle.norm_sha, "checks": checks, "output_max_abs_difference": comparison,
                      "rtol": tolerance, "atol": tolerance, "fresh_load_process": worker_process,
                      "fresh_worker_receipt": str(destination / "fresh_load" / "FRESH_PROCESS_RECEIPT.json"),
                      "fresh_worker_receipt_sha256": sha256(destination / "fresh_load" / "FRESH_PROCESS_RECEIPT.json"),
                      "resume_process": resume_process, "resume_receipt": str(destination / "resume_branch" / "TRAINING_RECEIPT.json"),
                      "resume_receipt_sha256": sha256(destination / "resume_branch" / "TRAINING_RECEIPT.json"),
                      "resume_weights_are_verification_only": True, "reported_best_is_original": True,
                      "elapsed_seconds": time.monotonic() - started, "created_utc": utc_now()}
            stop_remaining = False
        except Exception as error:
            result = {"status": "FAIL", "label": label, "error": f"{type(error).__name__}: {error}", "created_utc": utc_now()}
            # Every proof gate is terminal, including fresh-load/identity checks
            # before resume. A failed role must not trigger other optimizers.
            stop_remaining = True
        result.update(binding)
        save_json(destination / "LOAD_RESUME_CHECK.json", result)
        results[label] = result
        print(json.dumps({"event": "load_resume_check", "label": label, "status": result["status"]}), flush=True)
        if stop_remaining:
            break
    receipt = {"schema": "bb_load_resume_receipt.v1", "status": "PASS" if all(r["status"] == "PASS" for r in results.values()) else "FAIL",
               "created_utc": utc_now(), "data_sha": bundle.data_sha, "normalizer_sha": bundle.norm_sha,
               "runs_root": str(root), "campaign_receipt_sha256": sha256(root / "CAMPAIGN_RECEIPT.json"),
               "results": results, "not_run_labels": [label for label in RUN_LABELS if label not in results],
               **binding, "test_evaluated": False, "production_modified": False}
    save_json(output / "LOAD_RESUME_RECEIPT.json", receipt)
    return receipt


def _package_status(training: dict, proof: dict | None) -> str:
    if training.get("updates_this_run", 0) <= 0:
        return "FAILED"
    exact_proof = (proof and proof.get("status") == "PASS" and
                   all(proof.get("checks", {}).get(name) is True for name in REQUIRED_RESUME_CHECKS) and
                   all(proof.get(f"original_{name}_sha256") == training.get(f"{name}_sha256") for name in ("best", "last")))
    if exact_proof and training.get("stop_reason") in ("UPDATE_BUDGET_COMPLETE", "VALIDATION_EARLY_STOP"):
        return "PRETRAINED"
    return "PRETRAINED_PARTIAL"


def _copy_checkpoint(path: Path, directory: Path) -> str:
    destination = directory / path.name
    if not destination.exists():
        _copy_pin(path, destination)
        _copy_pin(Path(str(path) + ".identity.json"), Path(str(destination) + ".identity.json"))
    elif sha256(destination) != sha256(path):
        raise ValueError("checkpoint filename collision has different bytes")
    return str(destination)


def _relative(path: Path, directory: Path) -> str:
    return os.path.relpath(path, directory)


def package(data: str | Path, runs_root: str | Path, resume_receipt: str | Path,
            out: str | Path, self_contained: bool = False) -> dict:
    data, root, proof_path, output = map(lambda p: Path(p).resolve(), (data, runs_root, resume_receipt, out))
    proof = read_json(proof_path)
    bundle = Bundle(data)
    if (proof.get("schema") != "bb_load_resume_receipt.v1" or proof.get("data_sha") != bundle.data_sha or
            proof.get("normalizer_sha") != bundle.norm_sha or Path(proof["runs_root"]).resolve() != root or
            proof.get("campaign_receipt_sha256") != sha256(root / "CAMPAIGN_RECEIPT.json")):
        raise ValueError("resume evidence does not belong to this data/run root")
    output.mkdir(parents=True, exist_ok=False)
    shared_data = output / "shared_data"
    shared_data.mkdir()
    for name in ("dataset.npz", "normalizer.json", "splits.json", "geometry_provenance.json", "data_manifest.json"):
        _copy_pin(data / name, shared_data / name)
    software = output / "software" / "research" / "broadband56_nn"
    for source in sorted(Path(__file__).parent.glob("*.py")):
        _copy_pin(source, software / source.name)
    _copy_pin(Path(__file__).with_name("requirements.txt"), software / "requirements.txt")
    software_identity = [{"path": str(path.relative_to(output / "software")), "sha256": sha256(path)}
                         for path in sorted(software.iterdir()) if path.is_file()]
    save_json(output / "software" / "SOFTWARE_IDENTITY.json", {"files": software_identity})
    _copy_pin(proof_path, output / "evidence" / "LOAD_RESUME_RECEIPT.json")
    results = {}
    locations = {}
    for label in RUN_LABELS:
        source = _run_directory(root, label)
        destination = _run_directory(output, label)
        destination.mkdir(parents=True)
        try:
            trained, states = _checkpoint_pair(source, bundle.data_sha, label)
            _require_forward_reference(states, label, root)
            checks = proof.get("results", {}).get(label)
            state = states["last"]
            if checks and checks.get("status") == "PASS":
                if checks.get("label") != label or checks.get("data_sha") != bundle.data_sha or checks.get("normalizer_sha") != bundle.norm_sha:
                    raise ValueError("per-package resume proof identity mismatch")
                for role in ("resume_receipt", "fresh_worker_receipt"):
                    if sha256(checks[role]) != checks[f"{role}_sha256"]:
                        raise ValueError(f"resume proof {role} changed")
                    _copy_pin(Path(checks[role]), destination / "verification_evidence" / Path(checks[role]).name)
                for role in ("fresh_load_process", "resume_process"):
                    process = checks[role]
                    if process["returncode"] != 0 or sha256(process["log_path"]) != process["log_sha256"]:
                        raise ValueError("resume proof subprocess/log evidence differs")
                    _copy_pin(Path(process["log_path"]), destination / "verification_evidence" / Path(process["log_path"]).name)
            files = {name: Path(_copy_checkpoint(Path(trained[f"{name}_checkpoint"]), destination / "checkpoints")) for name in ("best", "last")}
            for name in ("config.json", "normalizer.json", "contract.json", "history.json", "TRAINING_RECEIPT.json"):
                _copy_pin(source / name, destination / "original_evidence" / name)
            raw_contract, parity = _resume_contract(state, source)
            _copy_pin(raw_contract, destination / "runtime_contract.json")
            parity_bindings = None
            if parity:
                prior = read_json(parity)
                _copy_pin(parity, destination / "original_evidence" / "PHYSICAL_PARITY.json")
                source_refs = []
                for index, pin in enumerate(prior["implementation_sources"]):
                    name = f"{index:02d}_{Path(pin['path']).name}"
                    copy_path = destination / "repro_sources" / name
                    actual = _copy_pin(Path(pin["path"]), copy_path)
                    if actual["sha256"] != pin["sha256"]:
                        raise ValueError("parity implementation source changed before packaging")
                    source_refs.append({"path": _relative(copy_path, destination), "sha256": actual["sha256"]})
                parity_bindings = {"receipt_path": "original_evidence/PHYSICAL_PARITY.json", "parent_parity_sha256": sha256(parity),
                                   "implementation_sources": source_refs}
            local_data = shared_data
            forward_binding = None
            if state["role"] == "inverse":
                f_label = PACKAGE_MAPPING[label][0]
                forward_location = locations[f_label]
                if self_contained:
                    forward_location = destination / "forward"
                    shutil.copytree(locations[f_label], forward_location, dirs_exist_ok=False)
                forward_manifest = read_json(forward_location / "PACKAGE.json")
                forward_binding = {"package_path": _relative(forward_location, destination),
                                   "checkpoint_path": _relative(forward_location / forward_manifest["best_checkpoint"], destination),
                                   "sha256": forward_manifest["best_sha256"], "model_sha": state["forward_model_sha"],
                                   "shared_identity": f_label}
                if self_contained:
                    local_data = destination / "data"
                    shutil.copytree(shared_data, local_data, dirs_exist_ok=False)
                    shutil.copytree(output / "software", destination / "software", dirs_exist_ok=False)
            artifact = {"schema": "bb_portable_package.v1", "label": label, "status": _package_status(trained, checks),
                        "role": state["role"], "kind": state["kind"], "architecture": state["architecture"],
                        "data_root": _relative(local_data, destination), "data_sha": bundle.data_sha,
                        "normalizer_sha": state["normalizer_sha"], "data_manifest_sha": bundle.manifest_sha,
                        "best_checkpoint": _relative(files["best"], destination), "best_sha256": trained["best_sha256"],
                        "last_checkpoint": _relative(files["last"], destination), "last_sha256": trained["last_sha256"],
                        "contract_path": "runtime_contract.json", "contract_sha256": sha256(destination / "runtime_contract.json"),
                        "forward_reference": forward_binding, "parity_relocation": parity_bindings,
                        "software_root": "software" if self_contained and state["role"] == "inverse" else _relative(output / "software", destination),
                        "software_identity_sha256": sha256(output / "software" / "SOFTWARE_IDENTITY.json"),
                        "completed_updates": trained["updates_this_run"], "parameter_counts": trained["parameter_counts"],
                        "training_receipt_sha256": sha256(source / "TRAINING_RECEIPT.json"),
                        "load_resume_status": checks.get("status") if checks else "MISSING",
                        "test_evaluation": "SEPARATE_FROZEN_EVALUATION_REPORT", "real_emx_validation": "NOT_RUN",
                        "physical_winner": "NOT_ESTABLISHED", "self_contained": bool(self_contained and state["role"] == "inverse")}
            if checks:
                save_json(destination / "LOAD_RESUME_CHECK.json", checks)
            if forward_binding:
                save_json(destination / "forward_reference.json", forward_binding)
            save_json(destination / "PACKAGE.json", artifact)
            _write_text(destination / "model_card.md", f"# {label}\n\nStatus: {artifact['status']}.\n\n"
                        f"Role: {state['role']}; architecture: {state['kind']}; updates: {trained['updates_this_run']}.\n\n"
                        "Snapshot source/train/validation/test counts are in shared_data/data_manifest.json. "
                        "The original best-validation checkpoint is used for reporting; one-step resume branches are verification only.\n\n"
                        "Forward consistency is a proxy, not real-EMX accuracy. REAL_EMX_VALIDATION=NOT_RUN. "
                        "Manufacturability and a physical winner are not established.\n\n"
                        "Operational paths in PACKAGE.json are relative to this directory. Historical evidence and embedded checkpoint "
                        "paths remain unchanged; use delivery resume-package to resolve current paths and generate a hash-checked "
                        "relocation-only parity receipt.\n")
            locations[label] = destination
            results[label] = {"status": artifact["status"], "package_path": _relative(destination, output),
                              "package_sha256": sha256(destination / "PACKAGE.json")}
        except Exception as error:
            results[label] = {"status": "FAILED", "error": f"{type(error).__name__}: {error}"}
            save_json(destination / "PACKAGE_FAILED.json", results[label])
    _write_text(output / "START_HERE.md", "# Broadband56 private research packages\n\n"
                "This tree contains the unchanged original best/last checkpoints, one shared frozen dataset and shared forward models. "
                "It is private and must not be uploaded to public GitHub.\n\n"
                "Set PYTHONPATH to the software directory, then use `python -m research.broadband56_nn.delivery resume-package "
                "--package /current/path/BB05 --out /new/private/resume --steps 1 --device mps`. "
                "Do not reuse an output directory. No simulator is invoked.\n\n"
                "Resume proof does not select its new weights for research reporting. A PRETRAINED label means the declared "
                "budget/early-stop and fresh-process verification completed, not convergence or physical accuracy.\n")
    receipt = {"schema": "bb_delivery_package.v1", "created_utc": utc_now(), "data_sha": bundle.data_sha,
               "resume_receipt_sha256": sha256(proof_path), "self_contained_requested": self_contained,
               "results": results, "real_emx_validation": "NOT_RUN", "production_modified": False}
    save_json(output / "PACKAGE_RECEIPT.json", receipt)
    manifest = [{"path": str(path.relative_to(output)), "sha256": sha256(path), "size_bytes": path.stat().st_size}
                for path in sorted(output.rglob("*")) if path.is_file()]
    save_json(output / "MANIFEST.json", {"schema": "bb_delivery_manifest.v1", "artifacts": manifest})
    manifest.append({"path": "MANIFEST.json", "sha256": sha256(output / "MANIFEST.json")})
    _write_text(output / "SHA256SUMS", "".join(f"{pin['sha256']}  {pin['path']}\n" for pin in manifest))
    return receipt


def resume_package(package_root: str | Path, out: str | Path, steps: int = 1,
                   device: str = "mps") -> dict:
    """Relocate paths, verify unchanged identities, then call the actual resume CLI."""
    root, output = Path(package_root).resolve(), Path(out).resolve()
    artifact = read_json(root / "PACKAGE.json")
    checkpoint, contract, data = (root / artifact[key] for key in ("last_checkpoint", "contract_path", "data_root"))
    if sha256(checkpoint) != artifact["last_sha256"] or sha256(contract) != artifact["contract_sha256"]:
        raise ValueError("packaged checkpoint/contract SHA mismatch")
    if sha256(data / "dataset.npz") != artifact["data_sha"]:
        raise ValueError("packaged data SHA mismatch")
    best = root / artifact["best_checkpoint"]
    if sha256(best) != artifact["best_sha256"]:
        raise ValueError("packaged original best checkpoint SHA mismatch")
    before = load_checkpoint(checkpoint)
    bundle = Bundle(data)
    if before["data_sha"] != bundle.data_sha or before["normalizer_sha"] != bundle.norm_sha or bundle.norm_sha != artifact["normalizer_sha"]:
        raise ValueError("packaged checkpoint snapshot/normalizer identity mismatch")
    if steps <= 0:
        raise ValueError("resume requires a positive update budget")
    software = (root / artifact["software_root"]).resolve()
    identity_path = software / "SOFTWARE_IDENTITY.json"
    if sha256(identity_path) != artifact["software_identity_sha256"]:
        raise ValueError("packaged software identity changed")
    for pin in read_json(identity_path)["files"]:
        if sha256(software / pin["path"]) != pin["sha256"]:
            raise ValueError("packaged software source changed")
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-m", "research.broadband56_nn", "resume", "--checkpoint", str(checkpoint),
               "--best-checkpoint", str(best), "--best-checkpoint-sha256", artifact["best_sha256"],
               "--data", str(data), "--contract", str(contract), "--out", str(output / "training"),
               "--steps", str(steps), "--validation-interval", "1", "--device", device]
    forward = artifact.get("forward_reference")
    if forward:
        path = root / forward["checkpoint_path"]
        if sha256(path) != forward["sha256"]:
            raise ValueError("packaged forward SHA mismatch")
        command += ["--forward-checkpoint", str(path)]
    binding = artifact.get("parity_relocation")
    if binding:
        original = root / binding["receipt_path"]
        if sha256(original) != binding["parent_parity_sha256"]:
            raise ValueError("packaged parity receipt changed")
        relocated = read_json(original)
        sources = []
        for pin in binding["implementation_sources"]:
            path = root / pin["path"]
            if sha256(path) != pin["sha256"]:
                raise ValueError("packaged parity implementation source changed")
            sources.append({"path": str(path.resolve()), "sha256": pin["sha256"]})
        relocated.update(implementation_sources=sources, contract_path=str(contract.resolve()),
                         parent_parity_sha256=binding["parent_parity_sha256"],
                         relocation_only=True, no_new_numeric_run=True, relocated_utc=utc_now())
        if relocated["data_sha"] != artifact["data_sha"] or relocated["contract_sha"] != sha256(contract):
            raise ValueError("relocation cannot change data or contract identity")
        parity = output / "RELOCATED_PHYSICAL_PARITY.json"
        save_json(parity, relocated)
        command += ["--physical-parity-receipt", str(parity)]
    process = _command(command, output / "resume.log", software)
    receipt_path = output / "training" / "TRAINING_RECEIPT.json"
    receipt = read_json(receipt_path)
    if sha256(receipt["last_checkpoint"]) != receipt["last_sha256"]:
        raise ValueError("portable resume produced a mismatched checkpoint receipt")
    after = load_checkpoint(receipt["last_checkpoint"])
    completed = after["step"] - before["step"]
    old_steps, new_steps = _optimizer_steps(before), _optimizer_steps(after)
    acceptance = {
        "positive_bounded_updates": 0 < completed <= steps and receipt["updates_this_run"] == completed,
        "same_data": after["data_sha"] == before["data_sha"] == bundle.data_sha,
        "same_normalizer": after["normalizer_sha"] == before["normalizer_sha"] == bundle.norm_sha,
        "same_contract": after["contract_sha"] == before["contract_sha"],
        "same_architecture": after["architecture"] == before["architecture"],
        "trainable_weights_changed": after["model_sha"] != before["model_sha"] and receipt.get("trainable_weights_changed") is True,
        "frozen_forward_unchanged": after["forward_model_sha"] == before["forward_model_sha"] and receipt.get("frozen_forward_unchanged") is True,
        "optimizer_progress": bool(old_steps) and old_steps.keys() == new_steps.keys() and all(new_steps[k] == value + completed for k, value in old_steps.items()),
        "scheduler_progress": after["scheduler_state"]["last_epoch"] == before["scheduler_state"]["last_epoch"] + completed,
        "parent_checkpoint": after["parent_checkpoint"]["sha256"] == artifact["last_sha256"],
        "original_last_unchanged": sha256(checkpoint) == artifact["last_sha256"],
        "original_best_unchanged": sha256(best) == artifact["best_sha256"],
        "new_process": process["pid"] != os.getpid(),
    }
    if not all(acceptance.values()):
        save_json(output / "RESUME_PACKAGE_FAILED.json", {"status": "FAIL", "checks": acceptance, "process": process,
                  "created_utc": utc_now(), "training_receipt_sha256": sha256(receipt_path)})
        raise ValueError(f"portable resume acceptance failed: {[key for key, value in acceptance.items() if not value]}")
    result = {"status": "PASS", "created_utc": utc_now(), "process": process,
              "checks": acceptance, "completed_updates": completed,
              "package_manifest_sha256": sha256(root / "PACKAGE.json"),
              "training_receipt": str(receipt_path), "training_receipt_sha256": sha256(receipt_path)}
    save_json(output / "RESUME_PACKAGE_RECEIPT.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("load-resume", "package"):
        item = sub.add_parser(name)
        item.add_argument("--data", required=True)
        item.add_argument("--runs-root", required=True)
        item.add_argument("--out", required=True)
        if name == "load-resume":
            item.add_argument("--device", choices=("cpu", "mps"), default="mps")
        else:
            item.add_argument("--resume-receipt", required=True)
            item.add_argument("--self-contained", action="store_true")
    item = sub.add_parser("resume-package")
    item.add_argument("--package", required=True)
    item.add_argument("--out", required=True)
    item.add_argument("--steps", type=int, default=1)
    item.add_argument("--device", choices=("cpu", "mps"), default="mps")
    item = sub.add_parser("_load-worker", help=argparse.SUPPRESS)
    for name in ("data", "best", "last", "out"):
        item.add_argument(f"--{name}", required=True)
    item.add_argument("--device", choices=("cpu", "mps"), default="mps")
    item.add_argument("--forward-checkpoint")
    args = parser.parse_args(argv)
    if args.command == "load-resume":
        return verify_load_resume(args.data, args.runs_root, args.out, args.device)
    if args.command == "package":
        return package(args.data, args.runs_root, args.resume_receipt, args.out, args.self_contained)
    if args.command == "resume-package":
        return resume_package(args.package, args.out, args.steps, args.device)
    return _fresh_worker(args.data, args.best, args.last, args.out, args.device, args.forward_checkpoint)


if __name__ == "__main__":
    main()
