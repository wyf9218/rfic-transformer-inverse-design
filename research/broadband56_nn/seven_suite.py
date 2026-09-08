"""One foreground, resumable BB00--BB06 10K study; no automatic scheduler.

Use check-and-run-once from an explicitly installed scheduler or manually.
Importing/running this module never changes the producer or its NN flags.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys

from .io import canonical_sha, load_checkpoint, read_json, sha256, utc_now
from .study_once import (BusyStudy, Journal, atomic_json, lease, pin,
                         resource_snapshot, run_child, study_key, verify_pin)

SUITE = "seven-model-10k-v3"
MAPPING = {"BB01": ("F1", "I1"), "BB02": ("F2", "I1"),
           "BB03": ("F3", "I1"), "BB04": ("F2", "I2"),
           "BB05": ("F2", "I3"), "BB06": ("F2", "I4")}
ORDER = ("BB00_FORWARD", "F1", "F2", "F3", "FREF", "BB00", *MAPPING)


def software_identity():
    directory = Path(__file__).resolve().parent
    return {p.name: pin(p) for p in sorted(directory.glob("*.py"))}


def environment_identity():
    return {"python": str(Path(sys.executable).absolute()), "python_version": sys.version,
            "packages": {name: importlib.metadata.version(name) for name in ("torch", "numpy", "scipy")}}


def create_request(path, campaign_id, contract, legacy_replay, spec,
                   *, forward_steps=256, inverse_steps=128, device="mps",
                   wall_budget_seconds=1800, control_root=None):
    if forward_steps < 32 or inverse_steps < 32 or forward_steps % 32 or inverse_steps % 32:
        raise ValueError("formal update budgets must be positive multiples of validation interval 32")
    if not 0 < wall_budget_seconds <= 10800:
        raise ValueError("explicit wall budget must be within the authorized three-hour ceiling")
    request = {"schema": "bb_seven_study_request.v1", "suite_version": SUITE,
               "campaign_id": campaign_id, "study_key": study_key(campaign_id, SUITE),
               "control_root": str(Path(control_root or Path(path).resolve().parent / "studies").resolve()),
               "milestone_geometries": 10000, "seed": 17, "reference_seed": 29,
               "initialization": "FROM_SCRATCH_ALL_TRAINED_FORWARDS_AND_INVERSES",
               "selection": "first 10000 authoritative accepted_sequence; never quality-ranked",
               "split": "bb56-split-v1 seed17 geometry hash 60/20/20; preserve existing equivalent geometry groups",
               "common_task": "PHYSICAL_15GHZ_FOUR_TARGETS_ONLY",
               "broadband_supported": list(MAPPING), "BB00_broadband": "NOT_SUPPORTED",
               "forward_steps": forward_steps, "inverse_steps": inverse_steps,
               "validation_interval": 32, "effective_batch": 32, "micro_batch": 8,
               "threads": 2, "device": device, "wall_budget_seconds": wall_budget_seconds,
               "resource_policy": {"min_available_bytes": 8 * 1024**3,
                                   "min_disk_bytes": 10 * 1024**3},
               "runtime_contract": pin(contract), "legacy_replay": pin(legacy_replay),
               "execution_spec": pin(spec), "software": software_identity(),
               "environment": environment_identity(), "created_utc": utc_now(),
               "automatic_trigger": "NOT_INSTALLED", "production_modified": False,
               "real_emx_validation": "NOT_RUN"}
    atomic_json(path, request, immutable=True)
    return request


def _validate_request_identity(request):
    """Validate directory identity before any mkdir/lease, independent of sources."""
    if request.get("schema") != "bb_seven_study_request.v1" or request.get("suite_version") != SUITE:
        raise ValueError("unknown seven-model study contract")
    if request.get("study_key") != study_key(request["campaign_id"], SUITE):
        raise ValueError("study identity is not stable campaign + 10K + suite version")
    if request.get("milestone_geometries") != 10000:
        raise ValueError("wrong formal milestone")


def validate_request(request):
    _validate_request_identity(request)
    if request["milestone_geometries"] != 10000 or request["seed"] != 17:
        raise ValueError("wrong formal milestone or main seed")
    if request["initialization"] != "FROM_SCRATCH_ALL_TRAINED_FORWARDS_AND_INVERSES":
        raise ValueError("pretrained weights are forbidden in the primary 10K ranking")
    if (request.get("reference_seed") != 29 or request.get("effective_batch") != 32 or
            request.get("micro_batch") != 8 or request.get("threads") != 2 or
            request.get("validation_interval") != 32 or request.get("device") not in ("cpu", "mps")):
        raise ValueError("frozen shared training settings differ from implemented execution")
    if environment_identity() != request["environment"]:
        raise ValueError("research environment changed; do not silently resume")
    if request["software"] != software_identity():
        raise ValueError("executing research source paths or bytes changed after request freeze")
    for value in request["software"].values():
        verify_pin(value)
    for key in ("runtime_contract", "legacy_replay", "execution_spec"):
        verify_pin(request[key])


def committed_count(source_manifest):
    """Read a small immutable receipt, not a growing status/feature table."""
    path = Path(source_manifest).resolve(strict=True)
    source = read_json(path)
    if source.get("schema") == "bb_committed_increment_source.v1":
        from .snapshot_10k import inspect_source_manifest
        result = inspect_source_manifest(path)
        return result["evidence"]["formally_accepted_geometries"], pin(path), source["boundary_progress_receipt"]
    if source.get("schema") != "bb_source_manifest.v1":
        raise ValueError("requires an explicit transferred/local source manifest")
    value = dict(source["files"]["checkpoint_receipt"])
    value["path"] = str((path.parent / value["path"]).resolve())
    receipt = read_json(verify_pin(value))
    if receipt.get("overall_status") != "PASS" or receipt.get("decision") != "USE_CHECKPOINT":
        raise ValueError("not a terminal accepted checkpoint")
    if receipt.get("contract_fingerprint_sha256") != source["contract_fingerprint_sha256"]:
        raise ValueError("checkpoint/manifest scientific identity differs")
    count = receipt.get("expected_accepted")
    if type(count) is not int or count < 0:
        raise ValueError("invalid formal accepted geometry count")
    return count, pin(path), value


def _copy_once(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = sha256(source)
    if destination.exists():
        if sha256(destination) != expected:
            raise ValueError("immutable registry copy differs")
        return
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if sha256(destination) != expected:
        raise ValueError("copy identity mismatch")


def _guard_runtime_binding(root, binding, request=None):
    """Verify the active lineage, without re-freezing a mutable study journal."""
    root = Path(root)
    if binding is None:
        marker = root / "ACTIVE_RUNTIME_REVISION.json"
        if marker.exists() or marker.is_symlink():
            raise ValueError("active runtime revision requires an exact runtime binding")
        return
    from .runtime_revision import resolve_active_request
    path, current = resolve_active_request(root, verify_pin(binding["active_request"]))
    if current != binding:
        raise ValueError("active runtime identity differs from this stage or package")
    if request is not None and canonical_sha(read_json(path)) != canonical_sha(request):
        raise ValueError("in-memory request differs from the active runtime request")


def _require_worker_runtime(attempt, binding):
    if binding is None:
        return
    value = read_json(Path(attempt) / "WORKER_RUNTIME_IDENTITY.json")
    if (value.get("runtime_binding") != binding or
            value.get("training_receipt") != pin(Path(attempt) / "TRAINING_RECEIPT.json")):
        raise ValueError("completed worker lacks the exact active runtime identity")


def _require_attempt_runtime(attempt, binding, spec=None):
    """Partial checkpoints need the original parent request, not a terminal receipt."""
    if binding is None:
        return
    attempt = Path(attempt)
    stage, root = attempt.parent, attempt.parent.parent.parent
    request_path = stage / (attempt.name + "_REQUEST.json")
    if (attempt.is_symlink() or re.fullmatch(r"attempt_[0-9]{4}", attempt.name) is None or
            stage.parent.name != "stages" or request_path.is_symlink() or not request_path.is_file()):
        raise ValueError("attempt lacks an original stage-bound runtime request")
    saved = read_json(request_path)
    if saved.get("runtime_binding") != binding or (spec is not None and saved.get("spec") != spec):
        raise ValueError("attempt runtime or scientific stage identity differs")
    argv = saved.get("argv", [])
    if argv[:4] != [sys.executable, "-m", "research.broadband56_nn.seven_suite", "_train-worker"]:
        raise ValueError("attempt worker executable or entry differs")
    for flag, expected in {"--request": binding["active_request"]["path"],
                           "--request-sha256": binding["active_request"]["sha256"],
                           "--study-root": str(root), "--out": str(attempt),
                           "--label": stage.name}.items():
        if argv.count(flag) != 1 or argv.index(flag) + 1 >= len(argv) or argv[argv.index(flag) + 1] != expected:
            raise ValueError("attempt argv lacks exact parent-bound runtime identity")
    return saved


def _stage_spec(label, request, forward=None):
    forward_role = label in ("BB00_FORWARD", "F1", "F2", "F3", "FREF")
    return {"label": label, "role": "forward" if forward_role else "inverse",
            "kind": "BB00" if label.startswith("BB00") else
                    ("F1" if label == "FREF" else label if forward_role else MAPPING[label][1]),
            "budget": request["forward_steps" if forward_role else "inverse_steps"],
            "seed": 29 if label == "FREF" else 17, "forward": forward}


def _qualified_receipt(path, spec, data_sha):
    receipt = read_json(path)
    if receipt.get("data_sha") != data_sha or receipt.get("trainable_weights_changed") is not True:
        raise ValueError("stage receipt does not prove new-snapshot training")
    if receipt.get("research_comparison_eligible") is False or not math.isfinite(receipt.get("best_validation", math.inf)):
        raise ValueError("diagnostic-only or nonfinite validation cannot qualify a ranked stage")
    step = receipt.get("completed_step", -1)
    if not 0 < step <= spec["budget"]:
        raise ValueError("stage updates outside frozen total budget")
    for key in ("best", "last"):
        candidate = Path(receipt[f"{key}_checkpoint"])
        if sha256(candidate) != receipt[f"{key}_sha256"]:
            raise ValueError("stage weight SHA changed")
        state = load_checkpoint(candidate)
        if state.get("data_sha") != data_sha or state.get("role") != spec["role"]:
            raise ValueError("stage checkpoint data or role mismatch")
        if not label_matches(spec, state):
            raise ValueError("stage architecture/package identity mismatch")
        if canonical_sha(state["normalizer"]) != state["normalizer_sha"]:
            raise ValueError("checkpoint normalizer identity mismatch")
        if canonical_sha(state["contract"]) != state["contract_sha"]:
            raise ValueError("checkpoint scientific contract identity mismatch")
        config = state["train_config"]
        if (config.get("seed") != spec["seed"] or config.get("effective_batch") != 32 or
                config.get("micro_batch") != 8 or config.get("validation_interval") != 32 or
                config.get("resume_probe") is True or state.get("research_comparison_eligible") is False):
            raise ValueError("checkpoint training contract/probe eligibility mismatch")
        if spec["forward"] is not None and sha256(state["forward_checkpoint"]) != spec["forward"]["sha256"]:
            raise ValueError("inverse checkpoint is not bound to the exact shared forward")
    stop = receipt.get("stop_reason")
    complete = (step == spec["budget"] and stop == "UPDATE_BUDGET_COMPLETE") or (
        stop == "VALIDATION_EARLY_STOP" and state.get("stale_validations", 0) >= state["train_config"]["patience"])
    return receipt, complete


def label_matches(spec, state):
    return (spec["kind"] == "BB00" and state.get("kind") in ("BB00", "BB00_TANDEM_MLP")) or state.get("kind") == spec["kind"]


def _resume_candidate(stage_root, spec, data_sha, *, runtime_binding=None):
    """Only complete sidecar-verified checkpoints from this exact stage."""
    candidates = []
    for file in sorted(stage_root.glob("attempt_*/checkpoint_step_*.pt")):
        if not Path(str(file) + ".identity.json").is_file():
            continue  # incomplete publication is preserved, never loaded
        if runtime_binding is not None:
            if file.is_symlink() or Path(str(file) + ".identity.json").is_symlink():
                raise ValueError("resume checkpoint must remain inside its original attempt")
            _require_attempt_runtime(file.parent, runtime_binding, spec)
        state = load_checkpoint(file)
        if state.get("data_sha") != data_sha or state.get("role") != spec["role"] or not label_matches(spec, state):
            raise ValueError("foreign checkpoint within study stage")
        config = state["train_config"]
        if config.get("seed") != spec["seed"] or config.get("micro_batch") != 8 or config.get("effective_batch") != 32:
            raise ValueError("resume configuration drift")
        best = Path(state["best_checkpoint"])
        if runtime_binding is not None:
            if best.is_symlink() or best.resolve().parent.parent != stage_root.resolve():
                raise ValueError("best resume checkpoint belongs to another study stage")
            _require_attempt_runtime(best.parent, runtime_binding, spec)
        load_checkpoint(best)
        if state["step"] > spec["budget"]:
            raise ValueError("resume step exceeds frozen budget")
        candidates.append((state["step"], file, state))
    if not candidates:
        raise ValueError("interrupted stage has no committed checkpoint; explicit recovery decision required")
    candidates.sort(key=lambda row: (row[0], str(row[1])))
    # Equal-step divergent states are not silently arbitrated by timestamp.
    top = [row for row in candidates if row[0] == candidates[-1][0]]
    if len({sha256(row[1]) for row in top}) != 1:
        raise ValueError("ambiguous divergent resume checkpoints")
    return candidates[-1]


def _train_stage(root, label, request, data, parity, deadline, forward, lock_fds, *, runtime_binding=None):
    _guard_runtime_binding(root, runtime_binding, request)
    stage_root = root / "stages" / label
    stage_root.mkdir(parents=True, exist_ok=True)
    data_sha = read_json(data / "data_manifest.json")["artifacts"]["dataset.npz"]["sha256"]
    spec = _stage_spec(label, request, forward)
    done = stage_root / "STAGE_COMPLETE.json"
    if done.exists():
        completion = read_json(done)
        if completion["stage_spec_sha256"] != canonical_sha(spec):
            raise ValueError("completed stage contract changed")
        if runtime_binding is not None and completion.get("runtime_binding") != runtime_binding:
            raise ValueError("completed stage runtime identity differs")
        receipt_path = verify_pin(completion["receipt"])
        if runtime_binding is not None and receipt_path.parent.parent.resolve() != stage_root.resolve():
            raise ValueError("completed receipt belongs to another stage")
        _require_attempt_runtime(receipt_path.parent, runtime_binding, spec)
        _require_worker_runtime(receipt_path.parent, runtime_binding)
        receipt, complete = _qualified_receipt(receipt_path, spec, data_sha)
        if not complete:
            raise ValueError("completed marker lacks qualified terminal training")
        return receipt_path, receipt
    attempts = sorted(p for p in stage_root.glob("attempt_*")
                      if p.is_dir() and re.fullmatch(r"attempt_[0-9]{4}", p.name))
    resume = None
    remaining = spec["budget"]
    if attempts:
        for prior in attempts:
            _require_attempt_runtime(prior, runtime_binding, spec)
        # Crash after terminal receipt but before journal commit: adopt exact evidence.
        prior_receipt = attempts[-1] / "TRAINING_RECEIPT.json"
        if prior_receipt.is_file():
            receipt, complete = _qualified_receipt(prior_receipt, spec, data_sha)
            if complete:
                _require_worker_runtime(attempts[-1], runtime_binding)
                atomic_json(done, {"stage_spec_sha256": canonical_sha(spec), "receipt": pin(prior_receipt),
                                   "runtime_binding": runtime_binding}, immutable=True)
                return prior_receipt, receipt
        step, resume, _ = _resume_candidate(stage_root, spec, data_sha, runtime_binding=runtime_binding)
        remaining -= step
        if remaining <= 0:
            raise ValueError("budget checkpoint exists without terminal receipt; reconcile, do not retrain")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(deadline):
        raise TimeoutError("frozen study wall budget exhausted")
    attempt = stage_root / f"attempt_{len(attempts) + 1:04d}"
    # Trainer owns create-once attempt directory. Request/log stay outside it.
    active_request = verify_pin(runtime_binding["active_request"]) if runtime_binding else root / "experiment_plan.json"
    command = [sys.executable, "-m", "research.broadband56_nn.seven_suite", "_train-worker",
               "--request", str(active_request), "--label", label,
               "--data", str(data), "--out", str(attempt), "--parity", str(parity),
               "--deadline", deadline, "--steps", str(remaining)]
    if runtime_binding is not None:
        command += ["--study-root", str(root), "--request-sha256", runtime_binding["active_request"]["sha256"]]
    if resume:
        command += ["--checkpoint", str(resume)]
    if forward:
        command += ["--forward", forward["path"]]
    atomic_json(stage_root / f"attempt_{len(attempts) + 1:04d}_REQUEST.json",
                {"spec": spec, "argv": command, "resume": pin(resume) if resume else None,
                 "initialization": "EXACT_NEW_STUDY_RESUME" if resume else "FROM_SCRATCH",
                 "runtime_binding": runtime_binding}, immutable=True)
    execution = run_child(command, Path(__file__).resolve().parents[2],
                          stage_root / f"attempt_{len(attempts) + 1:04d}.log", lock_fds)
    atomic_json(stage_root / f"attempt_{len(attempts) + 1:04d}_PROCESS.json", execution, immutable=True)
    if execution["returncode"] != 0:
        raise RuntimeError(f"{label} training child failed; evidence preserved, no automatic retry")
    receipt_path = attempt / "TRAINING_RECEIPT.json"
    _require_worker_runtime(attempt, runtime_binding)
    receipt, complete = _qualified_receipt(receipt_path, spec, data_sha)
    if not complete:
        raise TimeoutError(f"{label} is partial; do not advance dependent stages")
    atomic_json(done, {"stage_spec_sha256": canonical_sha(spec), "receipt": pin(receipt_path),
                       "runtime_binding": runtime_binding}, immutable=True)
    return receipt_path, receipt


def train_worker(args):
    request = read_json(args.request)
    _validate_request_identity(request)
    inferred_root = Path(request["control_root"]) / request["study_key"]
    explicit_root = getattr(args, "study_root", None)
    runtime_binding = None
    if explicit_root is not None and Path(explicit_root).resolve() != inferred_root.resolve():
        raise ValueError("worker study root differs from its frozen request")
    formal = explicit_root is not None or any(
        (inferred_root / name).exists() or (inferred_root / name).is_symlink()
        for name in ("experiment_plan.json", "ACTIVE_RUNTIME_REVISION.json"))
    if formal:
        from .runtime_revision import resolve_active_request
        active, runtime_binding = resolve_active_request(inferred_root, args.request)
        expected_sha = getattr(args, "request_sha256", None)
        if expected_sha is None:
            raise ValueError("formal worker requires the parent-bound request SHA")
        if expected_sha is not None and pin(active)["sha256"] != expected_sha:
            raise ValueError("worker active request SHA differs from parent")
        request = read_json(active)
        if (Path(args.out).is_symlink() or
                Path(args.out).resolve().parent != inferred_root.resolve() / "stages" / args.label or
                re.fullmatch(r"attempt_[0-9]{4}", Path(args.out).name) is None):
            raise ValueError("formal worker output must be its existing study stage attempt")
        saved = _require_attempt_runtime(Path(args.out), runtime_binding)
        saved_spec = saved.get("spec", {})
        if saved_spec != _stage_spec(args.label, request, saved_spec.get("forward")):
            raise ValueError("formal worker stage spec differs from frozen request")
        expected_forward = saved_spec.get("forward")
        if ((args.forward is None) != (expected_forward is None) or
                (expected_forward is not None and str(verify_pin(expected_forward)) != args.forward)):
            raise ValueError("formal worker forward differs from immutable parent stage")
        # A valid runtime identity alone must not authorize different live data,
        # budget, forward weights or resume state from the parent submission.
        argv = saved["argv"]
        for name in ("data", "parity", "deadline", "steps", "checkpoint", "forward"):
            flag, expected = "--" + name, getattr(args, name)
            if expected is None:
                if flag in argv:
                    raise ValueError("formal worker arguments differ from immutable parent request")
            elif (argv.count(flag) != 1 or argv.index(flag) + 1 >= len(argv) or
                  argv[argv.index(flag) + 1] != str(expected)):
                raise ValueError("formal worker arguments differ from immutable parent request")
    validate_request(request)
    spec = _stage_spec(args.label, request)
    if args.label.startswith("BB00"):
        from .bb00 import BB00Config, train_bb00
        config = BB00Config(role=spec["role"], steps=args.steps,
                           schedule_total_steps=spec["budget"], seed=spec["seed"],
                           device=request["device"], micro_batch=8, effective_batch=32,
                           forward_checkpoint=args.forward, deadline_utc=args.deadline)
        result = train_bb00(args.data, args.out, config, request["runtime_contract"]["path"],
                          request["legacy_replay"]["path"], request["legacy_replay"]["sha256"],
                          resume_checkpoint=args.checkpoint)
    else:
        from .training import TrainConfig, train
        config = TrainConfig(role=spec["role"], kind=spec["kind"], steps=args.steps,
                         seed=spec["seed"], device=request["device"], micro_batch=8,
                         effective_batch=32, threads=2, validation_interval=32,
                         deadline_utc=args.deadline, package_id=args.label,
                         physical_ready=spec["role"] == "inverse",
                         physical_parity_receipt=args.parity if spec["role"] == "inverse" else None,
                         forward_checkpoint=args.forward)
        result = train(args.data, args.out, config, request["runtime_contract"]["path"],
                 resume_checkpoint=args.checkpoint)
    if runtime_binding is not None:
        _guard_runtime_binding(inferred_root, runtime_binding, request)
        atomic_json(Path(args.out) / "WORKER_RUNTIME_IDENTITY.json", {
            "schema": "bb_seven_worker_runtime.v1", "runtime_binding": runtime_binding,
            "training_receipt": pin(Path(args.out) / "TRAINING_RECEIPT.json"),
            "worker_pid": os.getpid(), "created_utc": utc_now()}, immutable=True)
    return result


def build_registries(root, data, stages, *, runtime_binding=None):
    _guard_runtime_binding(root, runtime_binding)
    from .training import Bundle
    bundle = Bundle(data)
    records = {}
    six = root / "six_registry"
    results = {}
    for label, (receipt_path, receipt) in stages.items():
        records[label] = {"checkpoint": {"path": receipt["best_checkpoint"], "sha256": receipt["best_sha256"]},
                          "receipt": pin(receipt_path)}
        if label.startswith("BB00"):
            continue
        destination = six / "shared_forward" / label if label.startswith("F") else six / label
        for name in ("TRAINING_RECEIPT.json", "config.json", "contract.json", "normalizer.json", "history.json"):
            _copy_once(receipt_path.parent / name, destination / name)
        results[label] = receipt
    campaign = six / "CAMPAIGN_RECEIPT.json"
    if not campaign.exists():
        atomic_json(campaign, {"created_utc": utc_now(), "results": results,
                              "all_six_trained": True, "source": "seven-suite exact completed stage registry",
                              "real_emx_validation": "NOT_RUN"}, immutable=True)
    registry = root / "STUDY_MODELS.json"
    if not registry.exists():
        atomic_json(registry, {"schema": "bb_seven_registry.v1", "data_sha": bundle.data_sha,
                              "normalizer_sha": bundle.norm_sha, "records": records,
                              "study_plan": pin(root / "experiment_plan.json"),
                              "runtime_binding": runtime_binding}, immutable=True)
    if runtime_binding is not None and read_json(registry).get("runtime_binding") != runtime_binding:
        raise ValueError("model registry is bound to another runtime")
    return registry


def _finished_evaluation(out):
    out = Path(out)
    summary = out / "EVALUATION_SUMMARY.json"
    if not out.exists():
        return False
    if not summary.is_file() or read_json(summary).get("status") != "COMPLETE_PROXY_EVALUATION":
        raise ValueError(f"incomplete evaluation directory retained; needs explicit reconciliation: {out}")
    from .reuse_evaluation import verify_existing_evaluation
    panel, split = out.name.rsplit("_", 1)
    if panel not in ("common15", "broadband") or split not in ("validation", "test"):
        raise ValueError("unknown frozen evaluation directory")
    root = out.parent
    verify_existing_evaluation(out, root / "data" / "data_manifest.json",
        root / "STUDY_MODELS.json", split, panel,
        root / "target_plans" / f"physical15_{split}.json" if panel == "common15" else None)
    return True


def evaluate_completed(root, request, *, runtime_binding=None):
    """Both validation panels, then exact freezes, then each sealed test once."""
    from . import seven_evaluation as seven
    from . import evaluation as six
    root = Path(root)
    _guard_runtime_binding(root, runtime_binding, request)
    data, registry = root / "data", root / "STUDY_MODELS.json"
    if runtime_binding is not None and read_json(registry).get("runtime_binding") != runtime_binding:
        raise ValueError("evaluation registry runtime identity differs")
    records = read_json(registry)["records"]
    targets = root / "target_plans"
    targets.mkdir(exist_ok=True)
    for split in ("validation", "test"):
        target = targets / f"physical15_{split}.json"
        if not target.exists():
            seven.freeze_common_15ghz_targets(data, target, split=split)
    kwargs = {"device": request["device"], "micro_batch": 8, "threads": 2}
    common_val, broadband_val = root / "common15_validation", root / "broadband_validation"
    if not _finished_evaluation(common_val):
        seven.evaluate_seven(data, registry, targets / "physical15_validation.json", common_val, split="validation", **kwargs)
    if not _finished_evaluation(broadband_val):
        six.evaluate(data, root / "six_registry", records["FREF"]["checkpoint"]["path"], broadband_val, split="validation", **kwargs)
    common_freeze, six_freeze = root / "COMMON15_CONFIGURATION_FREEZE.json", root / "BROADBAND_CONFIGURATION_FREEZE.json"
    if not common_freeze.exists():
        seven.freeze_seven_configuration(registry, data, common_val / "EVALUATION_SUMMARY.json",
                                         targets / "physical15_test.json", common_freeze)
    if not six_freeze.exists():
        validation = read_json(broadband_val / "EVALUATION_SUMMARY.json")
        atomic_json(six_freeze, {"schema": "bb_evaluation_configuration_freeze.v1", "status": "FROZEN",
            "created_utc": utc_now(), "data_sha": validation["data_sha"],
            "normalizer_sha": validation["normalizer_sha"],
            "checkpoints": {name: record["checkpoint"]["sha256"] for name, record in records.items() if not name.startswith("BB00")},
            "evaluation_protocol_sha256": six.evaluation_protocol_identity()["sha256"],
            "validation_summary_sha256": sha256(broadband_val / "EVALUATION_SUMMARY.json"),
            "test_use": "report only; never selection or tuning"}, immutable=True)
    common_test, broadband_test = root / "common15_test", root / "broadband_test"
    if not _finished_evaluation(common_test):
        seven.evaluate_seven(data, registry, targets / "physical15_test.json", common_test,
                             split="test", configuration_freeze=common_freeze, **kwargs)
    if not _finished_evaluation(broadband_test):
        six.evaluate(data, root / "six_registry", records["FREF"]["checkpoint"]["path"], broadband_test,
                     split="test", configuration_freeze=six_freeze, **kwargs)
    _copy_once(common_test / "comparison_15ghz.csv", root / "comparison_15ghz.csv")
    _copy_once(common_test / "comparison_forward_15ghz.csv", root / "comparison_forward_15ghz.csv")
    if not (root / "comparison_broadband.csv").exists():
        seven.export_broadband_comparison(broadband_test, root / "comparison_broadband.csv")
    report = root / "report.md"
    if not report.exists():
        report.write_text("# Seven-model 10K first trial\n\n"
            "The common table evaluates PHYSICAL_15GHZ with four identical inputs only. "
            "The separate broadband table covers BB01–BB06; BB00 is NOT_SUPPORTED.\n\n"
            "All ranked weights were initialized from scratch on the new frozen train split. "
            "The legacy replay is a separate reference, not a ranked pretrained arm. "
            "This is a descriptive method/system comparison: BB00 uses 15GHz direct labels; "
            "the six broadband systems use the v2 spectral/spec supervision. Raw training losses "
            "are not interchangeable accuracy scores.\n\n"
            "REAL_EMX_VALIDATION=NOT_RUN. An independent forward proxy is not EMX truth. "
            "Invalid/unavailable outputs remain failures in the frozen target denominator; "
            "undefined full-panel RMSE is not replaced by zero. No physical winner is established.\n\n"
            "See common15_validation, common15_test, broadband_validation and broadband_test "
            "for source-bound definitions, denominators, units, selected steps, own/common proxy "
            "diagnostics, grids, candidate files, manifests and SHA256SUMS. All failure artifacts "
            "and stage-attempt logs are retained.\n", encoding="utf-8")
    return {"common": pin(common_test / "EVALUATION_SUMMARY.json"),
            "broadband": pin(broadband_test / "EVALUATION_SUMMARY.json")}


def _packaging_budget(root, request):
    """Read the original clock; packaging must never create or renew it."""
    from .delivery import validate_resume_deadline
    path = Path(root) / "TRAINING_BUDGET.json"
    budget = read_json(path)
    seconds = budget.get("seconds")
    if type(seconds) is not int or seconds != request.get("wall_budget_seconds") or seconds <= 0:
        raise ValueError("packaging budget seconds differ from the frozen request")
    start = validate_resume_deadline(budget.get("started_utc"), allow_expired=True)
    deadline = validate_resume_deadline(budget.get("deadline_utc"), allow_expired=True)
    if start is None or deadline is None or (deadline - start).total_seconds() != seconds:
        raise ValueError("packaging deadline/start interval differs from original budget")
    return budget["deadline_utc"], pin(path)


def _require_packaging_proof(path, deadline, budget_pin, labels):
    """Old/unbound or differently budgeted proofs do not authorize this study."""
    proof = read_json(path)
    if (proof.get("status") != "PASS" or proof.get("effective_deadline_utc") != deadline or
            proof.get("training_budget_sha256") != budget_pin["sha256"] or
            set(proof.get("results", {})) != set(labels)):
        raise ValueError("packaging resume proof lacks the exact original deadline/budget binding")
    for row in proof["results"].values():
        if (row.get("status") != "PASS" or row.get("effective_deadline_utc") != deadline or
                row.get("training_budget_sha256") != budget_pin["sha256"] or
                row.get("checks", {}).get("effective_deadline_bound") is not True):
            raise ValueError("component resume proof is not bound to the original deadline")


def package_completed(root, request, lock_fds=(), *, runtime_binding=None):
    """Existing six-package machinery plus separately proved BB00 new states."""
    from . import delivery
    from .baseline_package import build_package
    from .bb00_delivery import verify_bb00_load_resume
    root = Path(root)
    _guard_runtime_binding(root, runtime_binding, request)
    deadline, budget_pin = _packaging_budget(root, request)
    proof = root / "load_resume_six" / "LOAD_RESUME_RECEIPT.json"
    bb00_proof = root / "load_resume_bb00" / "BB00_LOAD_RESUME_RECEIPT.json"
    # Reject incompatible saved evidence before starting any new probe. Expiry
    # does not prevent reuse of exact, already completed proofs or hash copying.
    for path, labels in ((proof, delivery.RUN_LABELS), (bb00_proof, ("forward", "inverse"))):
        if path.exists():
            _require_packaging_proof(path, deadline, budget_pin, labels)
    if not proof.exists() or not bb00_proof.exists():
        delivery.validate_resume_deadline(deadline)
    data, records = root / "data", read_json(root / "STUDY_MODELS.json")["records"]
    if runtime_binding is not None and read_json(root / "STUDY_MODELS.json").get("runtime_binding") != runtime_binding:
        raise ValueError("package registry runtime identity differs")
    for subdir in ("common15_test", "broadband_test"):
        if not _finished_evaluation(root / subdir):
            raise ValueError("completed comparisons required before final package")
    if not proof.exists():
        delivery.verify_load_resume(data, root / "six_registry", proof.parent, request["device"], lock_fds=lock_fds,
                                    deadline_utc=deadline, training_budget_sha256=budget_pin["sha256"])
    _require_packaging_proof(proof, deadline, budget_pin, delivery.RUN_LABELS)
    if not bb00_proof.exists():
        delivery.validate_resume_deadline(deadline)
        verify_bb00_load_resume(data, records["BB00_FORWARD"]["receipt"]["path"],
            records["BB00"]["receipt"]["path"], bb00_proof.parent,
            request["runtime_contract"]["path"], request["legacy_replay"]["path"],
            request["legacy_replay"]["sha256"], request["device"], lock_fds=lock_fds,
            deadline_utc=deadline, training_budget_sha256=budget_pin["sha256"])
    _require_packaging_proof(bb00_proof, deadline, budget_pin, ("forward", "inverse"))
    output = root / "packages"
    six_output = output / "broadband_six"
    if not six_output.exists():
        delivery.package(data, root / "six_registry", proof, six_output)
    six_receipt = read_json(six_output / "PACKAGE_RECEIPT.json")
    if set(six_receipt["results"]) != set(ORDER) - {"BB00", "BB00_FORWARD"} or any(
            row.get("status") != "PRETRAINED" for row in six_receipt["results"].values()):
        raise ValueError("six-component packaging incomplete; no seven-package success marker")
    reference = output / "BB00_legacy_tandem" / "reference"
    if not reference.exists():
        build_package(request["legacy_replay"]["path"], reference,
                      expected_replay_sha256=request["legacy_replay"]["sha256"])
    retrained = output / "BB00_legacy_tandem" / "retrain_new10k_15ghz"
    for label in ("BB00_FORWARD", "BB00"):
        receipt_path = verify_pin(records[label]["receipt"])
        receipt = read_json(receipt_path)
        folder = retrained / label
        for name in ("TRAINING_RECEIPT.json", "config.json", "normalizer.json", "contract.json", "history.json"):
            _copy_once(receipt_path.parent / name, folder / name)
        for kind in ("best", "last"):
            weight = Path(receipt[f"{kind}_checkpoint"])
            _copy_once(weight, folder / (kind + ".pt"))
            _copy_once(str(weight) + ".identity.json", folder / (kind + ".pt.identity.json"))
    if not (output / "SEVEN_PACKAGE.json").exists():
        atomic_json(output / "SEVEN_PACKAGE.json", {"schema": "bb_seven_package.v1",
            "status": "TRAINED_AND_EVALUATED", "created_utc": utc_now(), "study": str(root),
            "study_plan": pin(root / "experiment_plan.json"), "models": pin(root / "STUDY_MODELS.json"),
            "six_resume_proof": pin(proof), "bb00_resume_proof": pin(bb00_proof),
            "training_budget": budget_pin, "effective_resume_deadline_utc": deadline,
            "runtime_binding": runtime_binding,
            "shared_data": "broadband_six/shared_data", "BB00_broadband": "NOT_SUPPORTED",
            "normalizers": "BB00 uses valid15GHz train only; six broadband share the frozen train-only normalizer",
            "resume_entry": "seven_suite resume-seven with exact active request and original study root; original plan remains immutable provenance",
            "real_emx_validation": "NOT_RUN", "production_modified": False}, immutable=True)
    for name in ("comparison_15ghz.csv", "comparison_forward_15ghz.csv", "comparison_broadband.csv", "report.md", "experiment_plan.json", "STUDY_MODELS.json"):
        _copy_once(root / name, output / name)
    from .io import manifest_tree
    manifest = output / "SEVEN_MANIFEST.json"
    if not manifest.exists():
        atomic_json(manifest, {"schema": "bb_seven_artifacts.v1", "files": manifest_tree(output)}, immutable=True)
    if not (output / "SHA256SUMS.txt").exists():
        with (output / "SHA256SUMS.txt").open("x") as handle:
            for item in _package_files(output, exclude_root=("SHA256SUMS.txt",)):
                handle.write(item["sha256"] + "  " + item["path"] + "\n")
    _verify_seven_package(output, runtime_binding=runtime_binding)
    return pin(output / "SEVEN_PACKAGE.json")


def _package_files(output, exclude_root=()):
    from .io import manifest_tree
    return [item for item in manifest_tree(output) if item["path"] not in exclude_root]


def _verify_seven_package(output, *, runtime_binding=None):
    output = Path(output)
    recorded = read_json(output / "SEVEN_MANIFEST.json")["files"]
    actual = _package_files(output, exclude_root=("SEVEN_MANIFEST.json", "SHA256SUMS.txt"))
    if recorded != actual:
        raise ValueError("seven-package exact artifact set differs")
    for item in recorded:
        verify_pin({**item, "path": str(output / item["path"])})
    expected_index = "".join(item["sha256"] + "  " + item["path"] + "\n"
                            for item in _package_files(output, exclude_root=("SHA256SUMS.txt",)))
    if (output / "SHA256SUMS.txt").read_text() != expected_index:
        raise ValueError("seven-package SHA index differs")
    summary = read_json(output / "SEVEN_PACKAGE.json")
    if summary.get("status") != "TRAINED_AND_EVALUATED":
        raise ValueError("no complete seven-group package")
    root = output.parent
    recorded_runtime = summary.get("runtime_binding")
    if runtime_binding is not None and recorded_runtime != runtime_binding:
        raise ValueError("completed package runtime identity differs")
    _guard_runtime_binding(root, recorded_runtime)
    if (Path(summary["study"]).resolve() != root.resolve() or
            summary["study_plan"] != pin(root / "experiment_plan.json") or
            summary["models"] != pin(root / "STUDY_MODELS.json")):
        raise ValueError("package belongs to another study identity")
    deadline, budget_pin = _packaging_budget(root, read_json(root / "experiment_plan.json"))
    if summary.get("training_budget") != budget_pin or summary.get("effective_resume_deadline_utc") != deadline:
        raise ValueError("seven-package original training budget binding differs")
    for name, labels in (("six_resume_proof", ("F1", "F2", "F3", "FREF", *MAPPING)),
                         ("bb00_resume_proof", ("forward", "inverse"))):
        _require_packaging_proof(verify_pin(summary[name]), deadline, budget_pin, labels)


def check_once(request_path, control_root, source_manifest=None, *, phase="all", access=None):
    request = read_json(request_path)
    _validate_request_identity(request)
    if str(Path(control_root).resolve()) != request.get("control_root"):
        raise ValueError("control root differs from frozen request; do not create a duplicate study directory")
    root = Path(control_root).resolve() / request["study_key"]
    if Path(control_root).is_symlink() or root.is_symlink():
        raise ValueError("study/control root must not be a symlink")
    marker = root / "ACTIVE_RUNTIME_REVISION.json"
    if not (marker.exists() or marker.is_symlink()):
        validate_request(request)
    root.mkdir(parents=True, exist_ok=True)
    try:
        with lease(root / "study.lock") as study_fd:
            if not (marker.exists() or marker.is_symlink()):
                _copy_once(request_path, root / "experiment_plan.json")
            from .runtime_revision import resolve_active_request
            active_request, runtime_binding = resolve_active_request(root, request_path)
            request = read_json(active_request)
            validate_request(request)
            journal = Journal(root)
            if all((root / "packages" / name).is_file()
                   for name in ("SEVEN_PACKAGE.json", "SEVEN_MANIFEST.json", "SHA256SUMS.txt")):
                _verify_seven_package(root / "packages", runtime_binding=runtime_binding)
                return {"status": "ALREADY_COMPLETE", "package": pin(root / "packages" / "SEVEN_PACKAGE.json")}
            frozen = root / "selection" / "SELECTION_MANIFEST.json"
            if phase in ("evaluate", "package") and not (root / "STUDY_MODELS.json").is_file():
                raise ValueError("evaluation/packaging requires completed seven-model training; no training will be launched")
            if phase == "resume" and not (root / "TRAINING_BUDGET.json").is_file():
                raise ValueError("resume-seven requires an existing started study; use check-and-run-once for initial submission")
            if not frozen.exists():
                if access is not None:
                    from .research_data_access import probe_and_localize
                    observed = probe_and_localize(**access)
                    if observed["status"] in ("WAITING_FOR_10K", "WAITING_RESOURCE"):
                        return journal.append(observed["status"], observed)
                    source_manifest = observed["source_manifest"]["path"]
                if source_manifest is None:
                    return journal.append("WAITING_FOR_10K", {"reason": "No explicit readable committed source manifest supplied", "automatic_trigger": "NOT_INSTALLED"})
                count, source_pin, checkpoint_pin = committed_count(source_manifest)
                source_identity = read_json(source_manifest)
                source_campaign = (source_identity.get("campaign_id") or
                                   read_json(verify_pin(checkpoint_pin)).get("campaign_id"))
                if source_campaign != request.get("campaign_id"):
                    raise ValueError("source campaign differs from stable study identity")
                if count < 10000:
                    return journal.append("WAITING_FOR_10K", {"committed_accepted": count,
                        "source_manifest": source_pin, "checkpoint": checkpoint_pin,
                        "snapshot_created": False, "training_started": False,
                        "automatic_trigger": "NOT_INSTALLED"})
                from .snapshot_10k import freeze_selection
                result = freeze_selection(source_manifest, root / "selection", seed=17)
                if result["status"] != "READY_FOR_10K":
                    raise ValueError("threshold met but immutable exact10K selection did not qualify")
            if phase == "select":
                return journal.append("READY_FOR_10K", {"selection": pin(frozen), "training_started": False})
            try:
                with lease(Path(control_root).resolve() / "research_device.lock") as device_fd:
                    resources = resource_snapshot(root, device=request["device"], **request["resource_policy"])
                    if resources["status"] != "PASS":
                        return journal.append("WAITING_RESOURCE", resources)
                    data = root / "data"
                    if not (data / "data_manifest.json").is_file():
                        from .snapshot_10k import prepare_selection
                        prepare_selection(frozen, data, sha256(frozen))
                    if phase == "prepare":
                        return journal.append("READY_FOR_10K", {"data": pin(data / "data_manifest.json"), "training_started": False})
                    clock = root / "TRAINING_BUDGET.json"
                    if not clock.exists():
                        now = datetime.now(timezone.utc)
                        atomic_json(clock, {"started_utc": now.isoformat(),
                            "deadline_utc": (now + timedelta(seconds=request["wall_budget_seconds"])).isoformat(),
                            "seconds": request["wall_budget_seconds"]}, immutable=True)
                    deadline = read_json(clock)["deadline_utc"]
                    parity = root / "PHYSICAL_PARITY.json"
                    if not parity.exists():
                        from .snapshot_parity import verify
                        verify(data, request["runtime_contract"]["path"], parity, count=64)
                    stages = {}
                    for label in ORDER:
                        resources = resource_snapshot(root, device=request["device"], **request["resource_policy"])
                        if resources["status"] != "PASS":
                            return journal.append("WAITING_RESOURCE", {"next_stage": label, **resources})
                        if phase in ("evaluate", "package") and not (root / "stages" / label / "STAGE_COMPLETE.json").is_file():
                            raise ValueError("incomplete training stage; evaluation/packaging does not start training")
                        owner = "BB00_FORWARD" if label == "BB00" else MAPPING[label][0] if label in MAPPING else None
                        forward = None if owner is None else {"path": stages[owner][1]["best_checkpoint"], "sha256": stages[owner][1]["best_sha256"]}
                        stages[label] = _train_stage(root, label, request, data, parity, deadline, forward,
                                                     (study_fd, device_fd), runtime_binding=runtime_binding)
                    registry = build_registries(root, data, stages, runtime_binding=runtime_binding)
                    journal.append("TRAINED_PENDING_EVALUATION", {"registry": pin(registry),
                        "group_count": 7, "stage_count": 12, "real_emx_validation": "NOT_RUN"})
                    if phase == "train":
                        return journal.load()
                    evaluation = evaluate_completed(root, request, runtime_binding=runtime_binding)
                    journal.append("EVALUATED_PENDING_PACKAGE", evaluation)
                    if phase == "evaluate":
                        return journal.load()
                    package = package_completed(root, request, (study_fd, device_fd), runtime_binding=runtime_binding)
                    return journal.append("TRAINED_AND_EVALUATED", {"package": package})
            except BusyStudy:
                return journal.append("WAITING_RESOURCE", {"reason": "Another local research worker holds the device lock"})
            except TimeoutError as error:
                return journal.append("PARTIAL", {"reason": str(error), "no_automatic_retry": True})
            except Exception as error:
                journal.append("FAILED", {"reason": str(error), "evidence_preserved": True})
                raise
    except BusyStudy:
        return {"status": "ALREADY_RUNNING", "study_key": request["study_key"], "root": str(root)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-request")
    for name in ("out", "campaign-id", "contract", "legacy-replay", "spec"):
        create.add_argument("--" + name, required=True)
    create.add_argument("--device", choices=("mps", "cpu"), default="mps")
    create.add_argument("--forward-steps", type=int, default=256)
    create.add_argument("--inverse-steps", type=int, default=128)
    create.add_argument("--wall-budget-seconds", type=int, default=1800)
    create.add_argument("--control-root", help="Fixed study-control root; default is a studies sibling of the request")
    prepare_revision_cli = sub.add_parser("prepare-runtime-revision", help="Prepare an immutable pre-training source-only revision; never trains")
    for name in ("base-request", "base-sha256", "predecessor-index", "index-sha256", "out"):
        prepare_revision_cli.add_argument("--" + name, required=True)
    activate_revision_cli = sub.add_parser("activate-runtime-revision", help="Activate only an exact independently approved pre-training revision; never trains")
    for name in ("proposal", "proposal-sha256", "qa-receipt", "qa-sha256", "out"):
        activate_revision_cli.add_argument("--" + name, required=True)
    for command in ("check-and-run-once", "prepare-10k", "train-seven", "resume-seven", "evaluate-seven", "package-seven"):
        cli = sub.add_parser(command)
        cli.add_argument("--request", required=True)
        cli.add_argument("--root", required=True)
        cli.add_argument("--source-manifest")
        cli.add_argument("--access-config", help="Explicit pinned read-only MARS transport configuration")
    worker = sub.add_parser("_train-worker", help=argparse.SUPPRESS)
    for name in ("request", "label", "data", "out", "parity", "deadline"):
        worker.add_argument("--" + name, required=True)
    worker.add_argument("--steps", type=int, required=True)
    worker.add_argument("--checkpoint")
    worker.add_argument("--forward")
    worker.add_argument("--study-root")
    worker.add_argument("--request-sha256")
    args = parser.parse_args(argv)
    if args.command == "create-request":
        result = create_request(args.out, args.campaign_id, args.contract, args.legacy_replay, args.spec,
                                forward_steps=args.forward_steps, inverse_steps=args.inverse_steps,
                                device=args.device, wall_budget_seconds=args.wall_budget_seconds,
                                control_root=args.control_root)
    elif args.command == "prepare-runtime-revision":
        from .runtime_revision import prepare_revision
        result = prepare_revision(args.base_request, args.base_sha256, args.predecessor_index, args.index_sha256, args.out)
    elif args.command == "activate-runtime-revision":
        from .runtime_revision import activate_revision
        result = activate_revision(args.proposal, args.proposal_sha256, args.qa_receipt, args.qa_sha256, out=args.out)
    elif args.command == "_train-worker":
        result = train_worker(args)
    else:
        result = check_once(args.request, args.root, args.source_manifest,
                            phase={"prepare-10k": "prepare", "train-seven": "train",
                                   "resume-seven": "resume", "evaluate-seven": "evaluate",
                                   "package-seven": "package"}.get(args.command, "all"),
                            access=read_json(args.access_config) if args.access_config else None)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return result


if __name__ == "__main__":
    main()
