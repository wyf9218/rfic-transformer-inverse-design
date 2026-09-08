"""Portable frequency-pair delivery and exact-route loading, never simulation.

Inference needs only the delivered weights/metadata and compatible repo runtime.
Resume additionally requires the separately retained, exact prepared dataset.
The 56 index slots describe evidence, not 56 trained models or directories.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

from .io import read_json, save_json, sha256, utc_now

MODES = ("STRICT_LUMPED", "POINTWISE_DESCRIPTOR_EXPERIMENTAL")
ACCEPTANCE_CHECKS = {"fresh_best_output_matches", "fresh_last_output_matches", "fixed_train_inputs_match",
    "exactly_one_optimizer_update", "optimizer_counters_continue", "scheduler_continues", "sampler_continues",
    "torch_rng_continues", "immutable_data_normalizer_contract", "frozen_forward_unchanged", "model_updated",
    "effective_deadline_bound", "resumed_native_output_finite", "diagnostic_not_ranking_checkpoint", "original_parent_bound"}


def _pin(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def _verify(pin):
    path = Path(pin["path"])
    if path.is_symlink() or not path.is_file() or sha256(path) != pin["sha256"]:
        raise ValueError("source identity mismatch: " + str(path))
    return path


def _copy(source, target, expected=None):
    source, target = Path(source), Path(target)
    digest = sha256(source)
    if source.is_symlink() or (expected is not None and digest != expected):
        raise ValueError("source file changed or is a symlink")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush(); os.fsync(writer.fileno())
    if sha256(target) != digest or sha256(source) != digest:
        raise ValueError("source/transport hash changed during copy")
    return digest


def _tree(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink():
        raise ValueError("evidence tree may not be a symlink")
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("evidence tree contains a symlink")
        if path.is_file():
            _copy(path, destination / path.relative_to(source))


def _relative_pin(root, path):
    return {"path": str(Path(path).relative_to(root)), "sha256": sha256(path)}


def _route(index, frequency_ghz, label_mode):
    if type(frequency_ghz) is not int or not 5 <= frequency_ghz <= 60 or label_mode not in MODES:
        raise ValueError("exact integer-frequency and explicit label mode required")
    rows = [row for row in index["frequencies"] if row["frequency_ghz"] == frequency_ghz]
    if len(rows) != 1:
        raise ValueError("frequency missing or ambiguous; no nearest-frequency fallback")
    slot = rows[0]["label_modes"][label_mode]
    if not slot["model_available"]:
        raise ValueError(slot["model_status"] + ": no trained model for this exact route")
    return index["models"][slot["model_id"]]


def _validate_inputs(pair_path, profile_path, evaluation_root, acceptance_path):
    pair, profile, acceptance = map(read_json, (pair_path, profile_path, acceptance_path))
    if (pair.get("schema") != "frequency_pair_receipt.v1" or
            pair.get("status") != "TRAINED_BUDGET_OR_EARLY_STOP" or set(pair["roles"]) != {"forward", "inverse"}):
        raise ValueError("completed bounded frequency pair required")
    if (profile.get("schema") != "bb_frequency_data_profile.v1" or profile.get("frequency_slots") != 56 or
            [r["frequency_ghz"] for r in profile["rows"]] != list(range(5, 61))):
        raise ValueError("complete genuine 56-frequency data profile required")
    if (acceptance.get("schema") != "bb00_load_resume_proof.v1" or acceptance.get("status") != "PASS" or
            acceptance.get("original_checkpoint_bytes_unchanged") is not True):
        raise ValueError("actual load and resume acceptance required")
    if (type(pair["frequency_ghz"]) is not int or not 5 <= pair["frequency_ghz"] <= 20 or
            pair["label_mode"] != "STRICT_LUMPED"):
        raise ValueError("acceptance adapter requires strict integer frequency 5..20 GHz")
    # Missing route fields in preserved historical receipts mean 15 GHz only.
    if (acceptance.get("frequency_ghz", 15) != pair["frequency_ghz"] or
            acceptance.get("label_mode", "STRICT_LUMPED") != pair["label_mode"]):
        raise ValueError("acceptance frequency/label route differs from trained pair")
    for role, entry in pair["roles"].items():
        receipt = read_json(_verify(entry["receipt"]))
        result = acceptance["results"][role]
        if (result["status"] != "PASS" or not ACCEPTANCE_CHECKS.issubset(result["checks"]) or
                not all(value is True for value in result["checks"].values())):
            raise ValueError("role load/resume checks are incomplete")
        if receipt.get("role") != role or receipt.get("updates_this_run", 0) < 1:
            raise ValueError("role has no actual updates")
        if (receipt.get("frequency_ghz", 15) != pair["frequency_ghz"] or
                receipt.get("label_mode", "STRICT_LUMPED") != pair["label_mode"] or
                result.get("frequency_ghz", 15) != pair["frequency_ghz"] or
                result.get("label_mode", "STRICT_LUMPED") != pair["label_mode"]):
            raise ValueError("role training/acceptance frequency/label route differs")
        for selection in ("best", "last"):
            checkpoint = _verify(entry[selection])
            side = read_json(str(checkpoint) + ".identity.json")
            digest = entry[selection]["sha256"]
            if (side["sha256"] != digest or receipt[selection+"_sha256"] != digest or
                    result["original_"+selection+"_sha256"] != digest):
                raise ValueError("pair/sidecar/training/acceptance checkpoint identity mismatch")
    data_root = Path(pair["data_root"])
    manifest = read_json(data_root / "data_manifest.json")
    data_sha = manifest["artifacts"]["dataset.npz"]["sha256"]
    if (acceptance["data_sha"] != data_sha or profile["source_identity"]["dataset"]["sha256"] != data_sha or
            profile["source_identity"]["data_manifest_sha256"] != sha256(data_root / "data_manifest.json")):
        raise ValueError("profile/acceptance/prepared data identities differ")
    summaries = {}
    for path in sorted(Path(evaluation_root).rglob("EVALUATION_SUMMARY.json")):
        value = read_json(path)
        if (value.get("schema") != "frequency_evaluation_summary.v1" or
                value.get("status") != "COMPLETE_DESCRIPTIVE_EVALUATION" or
                value.get("split") not in ("validation", "test") or value["split"] in summaries):
            raise ValueError("exact completed validation/test evaluation pair required")
        identity = value["identity"]
        if (identity["dataset"]["sha256"] != data_sha or identity["frequency_ghz"] != pair["frequency_ghz"] or
                identity["label_mode"] != pair["label_mode"] or
                any(identity[role+"_checkpoint"]["sha256"] != pair["roles"][role]["best"]["sha256"]
                    for role in ("forward", "inverse"))):
            raise ValueError("evaluation does not belong to selected pair/data/route")
        for item in value["artifacts"].values():
            _verify(item)
        summaries[value["split"]] = path
    if set(summaries) != {"validation", "test"}:
        raise ValueError("both completed validation and frozen-test evidence required")
    test_summary = read_json(summaries["test"])
    freeze = read_json(_verify(test_summary["configuration_freeze"]))
    if (freeze.get("schema") != "frequency_evaluation_freeze.v1" or freeze.get("status") != "FROZEN" or
            freeze["identity"] != test_summary["identity"]):
        raise ValueError("test result lacks its exact configuration freeze")
    if list(Path(evaluation_root).rglob("EVALUATION_FAILED.json")):
        raise ValueError("failed evaluation cannot be packaged as completed")
    return pair, profile, acceptance, manifest, summaries


def _reload_copies(root, model):
    """Actual four-checkpoint reload plus one synthetic train-mean proxy probe."""
    import torch
    from .bb00 import load_bb00
    torch.set_num_threads(2)
    loaded, checks = {}, {}
    for role in ("forward", "inverse"):
        loaded[role] = {}
        for selection in ("best", "last"):
            item = model["roles"][role][selection]
            network, state = load_bb00(root / item["path"], expected_sha256=item["sha256"])
            if (state["role"] != role or state["normalizer"]["frequency_ghz"] != model["frequency_ghz"] or
                    state["normalizer"]["label_mode"] != model["label_mode"] or state["data_sha"] != model["dataset_sha256"]):
                raise ValueError("copied checkpoint metadata differs")
            loaded[role][selection] = (network, state)
            checks[role+"_"+selection] = {"loaded": True, "model_sha256": state["model_sha"], "step": state["step"]}
        if loaded[role]["best"][1]["model_sha"] != loaded[role]["last"][1]["best_model_sha"]:
            raise ValueError("copied best/last checkpoint selection differs")
    forward, fs = loaded["forward"]["best"]
    inverse, ins = loaded["inverse"]["best"]
    if (ins["forward_checkpoint_sha256"] != model["roles"]["forward"]["best"]["sha256"] or
            ins["normalizer_sha"] != fs["normalizer_sha"]):
        raise ValueError("copied pair is not the same Tandem")
    with torch.no_grad():
        target = torch.tensor([ins["normalizer"]["y_mean"]], dtype=torch.float32)
        geometry = inverse(target); predicted = forward(geometry)
    if not torch.isfinite(geometry).all() or not torch.isfinite(predicted).all():
        raise ValueError("copied model synthetic inference is nonfinite")
    return {"schema": "frequency_package_reload.v1", "status": "PASS", "roles": checks,
            "input": "one synthetic target equal to train normalizer mean; no held-out data read",
            "geometry": geometry.tolist(), "predicted_response": predicted.tolist(),
            "optimizer_updates": 0, "data_file_loaded": False, "validation_source": "SELF_PROXY",
            "REAL_EMX_VALIDATION": "NOT_RUN", "created_utc": utc_now()}


def package_model(pair_receipt, profile_json, evalroot, acceptance_receipt, out):
    root = Path(out).resolve()
    if root.exists():
        raise FileExistsError(root)
    # Receipt/profile are copied as individual files, not their parent trees.
    # A study/posttrain/package directory is therefore legitimate. Prevent
    # recursion into the two copied trees and mutation of prepared data only.
    for source in (Path(evalroot).resolve(), Path(acceptance_receipt).resolve().parent,
                   Path(read_json(pair_receipt)["data_root"]).resolve()):
        if root == source or source in root.parents:
            raise ValueError("package output must remain outside existing input evidence trees")
    root.mkdir(parents=True, exist_ok=False)
    try:
        pair, profile, acceptance, data_manifest, summaries = _validate_inputs(
            pair_receipt, profile_json, evalroot, acceptance_receipt)
        inputs = {name: _pin(path) for name, path in (("pair", pair_receipt), ("profile", profile_json),
                                                     ("acceptance", acceptance_receipt))}
        for name, pin in inputs.items():
            _copy(pin["path"], root / "evidence" / (name + ".json"), pin["sha256"])
        _tree(evalroot, root / "evaluation")
        _tree(Path(acceptance_receipt).parent, root / "acceptance")
        data_root = Path(pair["data_root"])
        for name in ("data_manifest.json", "normalizer.json", "splits.json", "geometry_provenance.json"):
            _copy(data_root / name, root / "data_references" / name)
        request = read_json(_verify(pair["request"]))
        _copy(pair["request"]["path"], root / "evidence" / "STUDY_REQUEST.json")
        _copy(request["legacy_replay_receipt"], root / "resume" / "legacy_replay_receipt.json",
              request["legacy_replay_sha256"])
        source_count = profile["snapshot_unique_geometries"]
        model_id = f"f{pair['frequency_ghz']:02d}-{pair['label_mode'].lower()}-{inputs['pair']['sha256'][:12]}"
        model = {"model_id": model_id, "frequency_ghz": pair["frequency_ghz"], "label_mode": pair["label_mode"],
            "training_status": "PROVISIONAL_PARTIAL", "convergence_established": False,
            "experiment_class": pair["experiment_class"], "source_snapshot_geometries": source_count,
            "formal_10k": source_count == 10000 and "DEVELOPMENT" not in pair["experiment_class"].upper(),
            "dataset_sha256": acceptance["data_sha"], "validation_source": {
                "forward": "HELDOUT_EM_LABELS", "inverse": "SELF_PROXY"},
            "REAL_EMX_VALIDATION": "NOT_RUN", "roles": {}}
        for role, entry in pair["roles"].items():
            role_root = root / "models" / model_id / role
            model["roles"][role] = {}
            for selection in ("best", "last"):
                source = Path(entry[selection]["path"])
                dest = role_root / selection / source.name
                _copy(source, dest, entry[selection]["sha256"])
                _copy(str(source)+".identity.json", str(dest)+".identity.json")
                model["roles"][role][selection] = _relative_pin(root, dest)
            attempt = Path(entry["receipt"]["path"]).parent
            for name in ("config.json", "normalizer.json", "contract.json", "history.json", "TRAINING_RECEIPT.json"):
                _copy(attempt / name, role_root / name)
            log = attempt.parent / (attempt.name + ".log")
            _copy(log, role_root / "training.log")
            train_receipt = read_json(attempt / "TRAINING_RECEIPT.json")
            model["roles"][role].update({key: train_receipt[key] for key in
                ("started_step", "completed_step", "updates_this_run", "elapsed_seconds", "eligible_rows", "stop_reason")})
        norm = read_json(root / "models" / model_id / "inverse" / "normalizer.json")
        model["support"] = {"train_min": norm["train_support_min"], "train_max": norm["train_support_max"],
                            "definition": "train marginal envelope; joint realizability NOT established"}
        frequencies = []
        for row in profile["rows"]:
            slots = {}
            for mode in MODES:
                data = row["label_modes"][mode]
                present = row["frequency_ghz"] == pair["frequency_ghz"] and mode == pair["label_mode"]
                slots[mode] = {"data_status": data["status"], "eligible_count": data["eligible_count"],
                    "splits": data["splits"], "model_available": present, "model_id": model_id if present else None,
                    "model_status": "PROVISIONAL_PARTIAL" if present else
                        "NO_STRICT_LABELS" if data["status"] == "NO_STRICT_LABELS" else "NOT_TRAINED"}
            frequencies.append({"frequency_ghz": row["frequency_ghz"], "label_modes": slots})
        index = {"schema": "frequency_model_index.v1", "frequency_slots": 56, "frequencies": frequencies,
                 "models": {model_id: model}, "routing": "exact integer GHz and exact label mode, never nearest",
                 "private_delivery": True, "dataset_included": False, "created_utc": utc_now()}
        save_json(root / "MODEL_INDEX.json", index)
        save_json(root / "COPIED_MODEL_RELOAD.json", _reload_copies(root, model))
        save_json(root / "RESUME_REQUIREMENTS.json", {"dataset_sha256": acceptance["data_sha"],
            "data_manifest_sha256": sha256(data_root / "data_manifest.json"),
            "dataset_included": False, "original_data_root_reference_only": str(data_root),
            "source_pin_policy": "BB00 exact runtime-source/normalizer/contract/RNG/optimizer checks remain unchanged",
            "deadline_policy": "resume requires an explicit new deadline; it never silently renews the prior budget",
            "legacy_recipe": "resume uses persisted verified recipe, not historical source paths inside the copied receipt"})
        artifacts = {str(p.relative_to(root)): _relative_pin(root, p) for p in sorted(root.rglob("*")) if p.is_file()}
        save_json(root / "PACKAGE_MANIFEST.json", {"schema": "frequency_package_manifest.v1", "artifacts": artifacts})
        receipt = {"schema": "frequency_package_receipt.v1", "status": "PORTABLE_INFERENCE_PACKAGE_READY",
            "inputs": inputs, "model_index": _relative_pin(root, root / "MODEL_INDEX.json"),
            "manifest": _relative_pin(root, root / "PACKAGE_MANIFEST.json"),
            "trained_model_count": 1, "frequency_slots": 56, "source_snapshot_geometries": source_count,
            "model_status": "PROVISIONAL_PARTIAL", "original_load_resume_acceptance": "PASS",
            "copied_model_reload": "PASS", "relocated_optimizer_resume": "NOT_RUN",
            "REAL_EMX_VALIDATION": "NOT_RUN", "created_utc": utc_now()}
        save_json(root / "PACKAGE_RECEIPT.json", receipt)
        with (root / "SHA256SUMS.txt").open("x") as stream:
            for p in sorted(root.rglob("*")):
                if p.is_file() and p != root / "SHA256SUMS.txt":
                    stream.write(sha256(p) + "  " + str(p.relative_to(root)) + "\n")
        return receipt
    except Exception as error:
        save_json(root / "PACKAGE_FAILED.json", {"status": "FAIL", "error": str(error), "created_utc": utc_now()})
        raise


def load_index(index_path):
    path = Path(index_path).resolve(); root = path.parent
    receipt = read_json(root / "PACKAGE_RECEIPT.json")
    if (receipt.get("status") != "PORTABLE_INFERENCE_PACKAGE_READY" or (root / "PACKAGE_FAILED.json").exists() or
            receipt["manifest"]["sha256"] != sha256(root / "PACKAGE_MANIFEST.json") or
            receipt["model_index"]["sha256"] != sha256(path)):
        raise ValueError("qualified final package receipt required")
    manifest = read_json(root / "PACKAGE_MANIFEST.json")
    if manifest.get("schema") != "frequency_package_manifest.v1":
        raise ValueError("package manifest required")
    for key, item in manifest["artifacts"].items():
        rel = Path(item["path"])
        if rel.is_absolute() or ".." in rel.parts or key != str(rel):
            raise ValueError("portable paths must stay inside package")
        _verify({"path": str(root / rel), "sha256": item["sha256"]})
    index = read_json(path)
    if index.get("schema") != "frequency_model_index.v1" or path.name not in manifest["artifacts"]:
        raise ValueError("qualified model index required")
    for model in index["models"].values():
        for entry in model["roles"].values():
            for selection in ("best", "last"):
                item = entry[selection]
                if manifest["artifacts"].get(item["path"]) != item:
                    raise ValueError("model route points outside exact portable artifact manifest")
    return root, index


def infer_index(index_path, targets, *, frequency_ghz, label_mode, allow_extrapolation=False):
    from .frequency_tandem import infer
    root, index = load_index(index_path)
    model = _route(index, frequency_ghz, label_mode)
    return infer(root / model["roles"]["forward"]["best"]["path"],
        root / model["roles"]["inverse"]["best"]["path"], targets,
        frequency_ghz=frequency_ghz, label_mode=label_mode, allow_extrapolation=allow_extrapolation)


def resume_package(index_path, data_root, out, *, frequency_ghz, label_mode, role, steps,
                   deadline_utc, device_lock, resume_probe=False):
    from .bb00 import BB00Config, load_bb00, train_bb00
    from .study_once import lease, resource_snapshot
    root, index = load_index(index_path)
    model = _route(index, frequency_ghz, label_mode)
    if role not in ("forward", "inverse") or type(steps) is not int or steps < 1:
        raise ValueError("explicit role and positive continuation updates required")
    deadline = datetime.fromisoformat(deadline_utc.replace("Z", "+00:00"))
    if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
        raise ValueError("explicit future UTC resume deadline required")
    if sha256(Path(data_root) / "dataset.npz") != model["dataset_sha256"]:
        raise ValueError("external prepared dataset is not the exact trained snapshot")
    last = root / model["roles"][role]["last"]["path"]
    best = root / model["roles"][role]["best"]["path"]
    _, state = load_bb00(last)
    config = BB00Config(**state["train_config"])
    config.steps, config.deadline_utc = steps, deadline_utc
    config.forward_checkpoint = str(root / model["roles"]["forward"]["best"]["path"]) if role == "inverse" else None
    output = Path(out).resolve()
    lock = Path(device_lock).resolve()
    if output == root or root in output.parents or lock == root or root in lock.parents:
        raise ValueError("resume outputs and shared device lock must stay outside immutable package")
    request = read_json(root / "evidence" / "STUDY_REQUEST.json")
    recipe = root / "resume" / "legacy_replay_receipt.json"
    contract = root / "models" / model["model_id"] / role / "contract.json"
    with lease(lock):
        resources = resource_snapshot(output.parent, device=config.device,
            min_available_bytes=request["resources"]["min_available_bytes"],
            min_disk_bytes=request["resources"]["min_disk_bytes"])
        if resources["status"] != "PASS":
            return {"status": "WAITING_RESOURCE", "resources": resources, "optimizer_updates": 0}
        output.mkdir(parents=True, exist_ok=False)
        save_json(output / "RESUME_CONFIG.json", {"train": asdict(config), "source_index": str(index_path),
            "data_root": str(Path(data_root).resolve()), "resume_probe": resume_probe,
            "device_lock": str(lock), "resources": resources, "original_checkpoint_bytes_modified": False})
        return train_bb00(data_root, output / "attempt_0001", config, contract, recipe, sha256(recipe),
            resume_checkpoint=last, resume_best_checkpoint=best,
            resume_best_checkpoint_sha256=model["roles"][role]["best"]["sha256"], resume_probe=resume_probe)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("build")
    for key in ("pair", "profile", "evaluation", "acceptance", "out"):
        build.add_argument("--"+key, required=True)
    for action in ("infer", "resume"):
        cli = sub.add_parser(action)
        cli.add_argument("--index", required=True); cli.add_argument("--frequency-ghz", type=int, required=True)
        cli.add_argument("--label-mode", choices=MODES, required=True)
        if action == "infer":
            cli.add_argument("--targets", required=True); cli.add_argument("--allow-extrapolation", action="store_true")
        else:
            cli.add_argument("--data-root", required=True); cli.add_argument("--out", required=True)
            cli.add_argument("--role", choices=("forward", "inverse"), required=True)
            cli.add_argument("--steps", type=int, required=True); cli.add_argument("--deadline-utc", required=True)
            cli.add_argument("--device-lock", required=True, help="existing shared research device lock, outside package")
            cli.add_argument("--resume-probe", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "build":
        result = package_model(args.pair, args.profile, args.evaluation, args.acceptance, args.out)
    elif args.action == "infer":
        result = infer_index(args.index, json.loads(args.targets), frequency_ghz=args.frequency_ghz,
            label_mode=args.label_mode, allow_extrapolation=args.allow_extrapolation)
    else:
        result = resume_package(args.index, args.data_root, args.out, frequency_ghz=args.frequency_ghz,
            label_mode=args.label_mode, role=args.role, steps=args.steps, deadline_utc=args.deadline_utc,
            device_lock=args.device_lock, resume_probe=args.resume_probe)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
