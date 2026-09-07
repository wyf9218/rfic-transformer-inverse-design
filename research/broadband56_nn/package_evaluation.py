"""Evaluate a relocated private package without relying on historical paths.

Creates a fresh, derived registry view. Original training evidence/checkpoint
bytes are never edited, and no previously computed metric is copied as a new
result. The frozen evaluator itself remains unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch

from .delivery import RUN_LABELS
from .evaluation import evaluate
from .io import canonical_sha, load_checkpoint, read_json, save_json, sha256, utc_now
from .models import PACKAGE_MAPPING


def _inside(root: Path, base: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("operational package references must be nonempty relative paths")
    path = (base / relative).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("operational package reference escapes the relocated package root")
    return path


def _pin(path: Path, expected: str | None = None) -> dict:
    if not path.is_file():
        raise ValueError(f"expected a regular package artifact: {path}")
    digest = sha256(path)
    if expected is not None and digest != expected:
        raise ValueError(f"packaged artifact SHA mismatch: {path}")
    return {"path": str(path), "sha256": digest, "size_bytes": path.stat().st_size}


def _model_state_digest(state):
    digest = hashlib.sha256()
    for name, tensor in sorted(state["model_state"].items()):
        if not isinstance(tensor, torch.Tensor):
            raise ValueError("model state must contain tensors")
        digest.update(name.encode())
        digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def _checkpoint_metadata(path, expected_sha, artifact):
    _pin(path, expected_sha)
    state = load_checkpoint(path)
    if (state.get("role"), state.get("kind")) != (artifact["role"], artifact["kind"]):
        raise ValueError("packaged checkpoint role/kind differs from its manifest")
    for state_key, artifact_key in (("data_sha", "data_sha"), ("normalizer_sha", "normalizer_sha")):
        if state.get(state_key) != artifact[artifact_key]:
            raise ValueError(f"packaged checkpoint {state_key} differs")
    if state.get("step", 0) <= 0:
        raise ValueError("zero-update weights cannot enter a research evaluation registry")
    if canonical_sha(state["contract"]) != state["contract_sha"]:
        raise ValueError("checkpoint contract identity does not match its contents")
    if _model_state_digest(state) != state["model_sha"]:
        raise ValueError("checkpoint tensor digest differs from saved model identity")
    return {key: state.get(key) for key in ("role", "kind", "data_sha", "normalizer_sha", "geometry_dim",
                                          "step", "model_sha", "contract_sha", "forward_model_sha")}


def build_registry(package_root, out):
    """Hash-only/metadata verification and a no-clobber relocated registry view."""
    root, output = Path(package_root).resolve(strict=True), Path(out).resolve()
    output.mkdir(parents=True, exist_ok=False)
    try:
        receipt_path = root / "PACKAGE_RECEIPT.json"
        package_receipt = read_json(receipt_path)
        if package_receipt.get("schema") != "bb_delivery_package.v1":
            raise ValueError("not a Broadband56 delivery package root")
        if set(package_receipt.get("results", {})) != set(RUN_LABELS):
            raise ValueError("package must identify exactly four forward and six inverse runs")
        records, common_data, common_norm, common_contract = {}, None, None, None
        for label in RUN_LABELS:
            result = package_receipt["results"][label]
            if result.get("status") not in {"PRETRAINED", "PRETRAINED_PARTIAL"}:
                raise ValueError(f"{label}: missing trained research package")
            directory = _inside(root, root, result["package_path"])
            manifest_pin = _pin(directory / "PACKAGE.json", result["package_sha256"])
            artifact = read_json(manifest_pin["path"])
            expected_kind = ("F1" if label == "FREF" else label) if label.startswith("F") else PACKAGE_MAPPING[label][1]
            expected_role = "forward" if label.startswith("F") else "inverse"
            if (artifact.get("schema"), artifact.get("label"), artifact.get("role"), artifact.get("kind")) != (
                    "bb_portable_package.v1", label, expected_role, expected_kind):
                raise ValueError(f"{label}: package role/architecture mapping differs")
            data = _inside(root, directory, artifact["data_root"])
            _pin(data / "dataset.npz", artifact["data_sha"])
            _pin(data / "data_manifest.json", artifact["data_manifest_sha"])
            normalizer = read_json(data / "normalizer.json")
            if canonical_sha(normalizer) != artifact["normalizer_sha"]:
                raise ValueError("relocated package normalizer differs")
            if artifact["data_sha"] != package_receipt["data_sha"]:
                raise ValueError("package does not use the root-declared shared snapshot")
            contract_path = _inside(root, directory, artifact["contract_path"])
            _pin(contract_path, artifact["contract_sha256"])
            contract_sha = canonical_sha(read_json(contract_path))
            if common_data is None:
                common_data, common_norm, common_contract = data, artifact["normalizer_sha"], contract_sha
            if artifact["normalizer_sha"] != common_norm or contract_sha != common_contract:
                raise ValueError("packages disagree on normalizer or geometry/port contract")
            original_path = directory / "original_evidence" / "TRAINING_RECEIPT.json"
            original_pin = _pin(original_path, artifact["training_receipt_sha256"])
            original = read_json(original_path)
            if original.get("data_sha") != artifact["data_sha"] or original.get("updates_this_run", 0) <= 0:
                raise ValueError("original training receipt does not prove positive same-snapshot updates")
            paths, states = {}, {}
            for selection in ("best", "last"):
                paths[selection] = _inside(root, directory, artifact[f"{selection}_checkpoint"])
                if original.get(f"{selection}_sha256") != artifact[f"{selection}_sha256"]:
                    raise ValueError("package-selected checkpoint differs from original training evidence")
                states[selection] = _checkpoint_metadata(paths[selection], artifact[f"{selection}_sha256"], artifact)
                if states[selection]["contract_sha"] != common_contract:
                    raise ValueError("checkpoint contract differs from the shared frozen contract")
            destination = output / "shared_forward" / label if label.startswith("F") else output / label
            destination.mkdir(parents=True)
            # Preserve exact historical evidence, including its now-stale paths.
            original_copy = destination / "ORIGINAL_TRAINING_RECEIPT.json"
            with original_path.open("rb") as source, original_copy.open("xb") as target:
                target.write(source.read())
            _pin(original_copy, original_pin["sha256"])
            derived = dict(original)
            derived.update(best_checkpoint=str(paths["best"]), last_checkpoint=str(paths["last"]),
                           registry_relocation_only=True, original_receipt=original_pin,
                           original_receipt_copy_sha256=sha256(original_copy),
                           package_manifest=manifest_pin, no_new_training_or_evaluation=True)
            save_json(destination / "TRAINING_RECEIPT.json", derived)
            records[label] = {"directory": directory, "artifact": artifact, "states": states,
                              "paths": paths, "registry_directory": destination,
                              "package_manifest": manifest_pin, "original_receipt": original_pin}
        for label, (forward_label, _) in PACKAGE_MAPPING.items():
            record, own = records[label], records[forward_label]
            reference = record["artifact"].get("forward_reference")
            if not reference:
                raise ValueError(f"{label}: missing portable forward-reference evidence")
            path = _inside(root, record["directory"], reference["checkpoint_path"])
            _pin(path, reference["sha256"])
            if reference["sha256"] != own["artifact"]["best_sha256"]:
                raise ValueError("inverse forward reference is not the exact selected shared forward checkpoint")
            model_sha = own["states"]["best"]["model_sha"]
            if reference.get("model_sha") != model_sha:
                raise ValueError("portable forward model digest differs")
            if any(state["forward_model_sha"] != model_sha for state in record["states"].values()):
                raise ValueError("inverse checkpoint was not trained with the declared exact shared forward")
            if model_sha == records["FREF"]["states"]["best"]["model_sha"]:
                raise ValueError("common evaluation forward was used by inverse training")
            # The unchanged evaluator validates this pinned reference when the
            # original checkpoint's absolute path is no longer available.
            save_json(record["registry_directory"] / "forward_reference.json",
                      {**reference, "checkpoint_path": str(path), "model_sha": model_sha,
                       "package_manifest_sha256": record["package_manifest"]["sha256"],
                       "relocation_only": True, "original_evidence_unchanged": True})
        result = {"schema": "bb_packaged_evaluation_registry.v1", "status": "PASS", "created_utc": utc_now(),
                  "package_receipt": _pin(receipt_path), "data_root": str(common_data),
                  "data_sha": package_receipt["data_sha"], "normalizer_sha": common_norm,
                  "contract_sha": common_contract, "registry_root": str(output),
                  "forward_reference": str(records["FREF"]["paths"]["best"]),
                  "checkpoint_identities": {label: {name: record["artifact"][f"{name}_sha256"] for name in ("best", "last")}
                                            for label, record in records.items()},
                  "source_package_manifests": {label: record["package_manifest"] for label, record in records.items()},
                  "original_receipt_evidence": {label: record["original_receipt"] for label, record in records.items()},
                  "original_files_modified": False, "numerical_evaluation_performed": False,
                  "statistics_copied": False, "test_access": False}
        save_json(output / "REGISTRY_RECEIPT.json", result)
        return result
    except Exception as error:
        save_json(output / "REGISTRY_FAILED.json", {"status": "FAIL", "created_utc": utc_now(),
                  "error": f"{type(error).__name__}: {error}", "partial_outputs_preserved": True,
                  "original_files_modified": False, "numerical_evaluation_performed": False})
        raise


def evaluate_package(package_root, out, *, split="validation", configuration_freeze=None,
                     check_only=False, device="mps", micro_batch=8, threads=2):
    output = Path(out).resolve()
    output.mkdir(parents=True, exist_ok=False)
    registry = build_registry(package_root, output / "registry")
    if check_only:
        result = {"status": "PASS_IDENTITY_CHECK_ONLY", "registry_receipt": str(output / "registry" / "REGISTRY_RECEIPT.json"),
                  "registry_sha256": sha256(output / "registry" / "REGISTRY_RECEIPT.json"),
                  "numerical_evaluation_performed": False, "statistics_copied": False, "test_access": False}
        save_json(output / "CHECK_ONLY_RECEIPT.json", result)
        return result
    result = evaluate(registry["data_root"], registry["registry_root"], registry["forward_reference"],
                      output / "evaluation", split=split, configuration_freeze=configuration_freeze,
                      device=device, micro_batch=micro_batch, threads=threads)
    save_json(output / "PACKAGE_EVALUATION_RECEIPT.json", {"status": result["status"], "split": split,
              "registry_receipt_sha256": sha256(output / "registry" / "REGISTRY_RECEIPT.json"),
              "evaluation_summary": str(output / "evaluation" / "EVALUATION_SUMMARY.json"),
              "evaluation_summary_sha256": sha256(output / "evaluation" / "EVALUATION_SUMMARY.json"),
              "new_statistics_recomputed": True, "original_files_modified": False,
              "real_emx_validation": "NOT_RUN", "created_utc": utc_now()})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--configuration-freeze")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--device", choices=("cpu", "mps"), default="mps")
    parser.add_argument("--micro-batch", type=int, default=8)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    result = evaluate_package(args.package, args.out, split=args.split,
                              configuration_freeze=args.configuration_freeze, check_only=args.check_only,
                              device=args.device, micro_batch=args.micro_batch, threads=args.threads)
    print(json.dumps({"status": result["status"], "out": str(Path(args.out).resolve())}), flush=True)
    return result


if __name__ == "__main__":
    main()
