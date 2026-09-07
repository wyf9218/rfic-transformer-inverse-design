"""Portable, immutable packaging of the already-replayed historical 10K model.

This is NOT a new BB00 architecture and does not accept broadband S requests.
Build copies evidence and original checkpoint bytes; it performs no prediction,
training, test-set evaluation, optimization, or physical simulation.
Only Python and NumPy are needed by the exported standalone loader.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import numpy as np


NORMALIZER_KEYS = ("x_mean", "x_scale", "y_mean", "y_scale", "geometry_lower", "geometry_upper")
PARITY_KEYS = ("geometry_max_absolute_difference", "inverse_reconstruction_max_absolute_difference", "paired_forward_max_absolute_difference")
REQUIRED_SOURCES = ("contract", "runtime", "weights", "summary", "validation_export")
PACKAGE_SCHEMA = "bb_historical_10k_portable_package.v1"
BOUNDARY = {
    "native_task": "single_frequency_15ghz_four_physical_features_to_10d_geometry",
    "native_full56_s_supported": False,
    "native_broadband_physical_supported": False,
    "native_masked_or_partial_physical_supported": False,
    "bb00_broadband_architecture_identity": "NOT_ESTABLISHED_BY_THIS_HISTORICAL_PACKAGE",
    "comparison": "Historical descriptive reference only; not a common-spec BB01-BB06 architecture arm",
    "real_emx_validation": "NOT_RUN",
    "manufacturability": "NOT_PROVEN",
    "historical_retraining": False,
}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def _verified(path, expected):
    path = Path(path).resolve()
    if not isinstance(expected, str) or len(expected) != 64 or sha256(path) != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")
    return path


def _within(root, relative):
    p = Path(relative)
    path = root/p
    if p.is_absolute() or ".." in p.parts or not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError("package path must stay inside the portable root without symlinks")
    return path


def _normalizer(weights):
    with np.load(weights, allow_pickle=False) as arrays:
        normalizer = {key: np.asarray(arrays["normalization__"+key], dtype=float) for key in NORMALIZER_KEYS}
    for key, value in normalizer.items():
        size = 4 if key.startswith("x_") else 10
        if value.shape != (size,) or not np.isfinite(value).all():
            raise ValueError("invalid historical normalizer: "+key)
    if any(np.any(normalizer[key] <= 0) for key in ("x_scale", "y_scale")):
        raise ValueError("historical normalizer scales must be positive")
    if np.any(normalizer["geometry_upper"] <= normalizer["geometry_lower"]):
        raise ValueError("invalid historical normalized geometry envelope")
    return {key: value.tolist() for key, value in normalizer.items()}


def build_package(replay_receipt, out, *, expected_replay_sha256):
    """Copy one hash-anchored existing PASS replay, without re-running it."""
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    try:
        result = _build(replay_receipt, out, expected_replay_sha256)
    except Exception as exc:
        _write(out/"PACKAGE_BUILD_FAILED.json", {"status": "FAIL", "error": str(exc),
               "generated_utc": datetime.now(timezone.utc).isoformat(), "historical_retraining": False})
        raise
    return result


def _build(replay_path, out, replay_sha):
    replay_path = _verified(replay_path, replay_sha)
    replay = _json(replay_path)
    if replay.get("schema") != "bb_r0_replay.v1" or replay.get("status") != "PASS":
        raise ValueError("existing PASS R0 replay required")
    tolerance = replay.get("absolute_tolerance")
    if not isinstance(tolerance, (int, float)) or not np.isfinite(tolerance) or not 0 < tolerance <= 1e-9:
        raise ValueError("qualified replay tolerance required")
    parity = replay.get("numerical_parity", {})
    if any(not isinstance(parity.get(k), (int, float)) or not np.isfinite(parity[k]) or not 0 <= parity[k] <= tolerance for k in PARITY_KEYS):
        raise ValueError("all three finite original parity checks are required")
    if any(replay.get(k) is not False for k in ("retraining_performed", "test_set_accessed", "fixed_target_generation")):
        raise ValueError("existing replay must preserve no-retraining and validation-only boundaries")
    source_records = replay["sources"]
    source_paths = {name: _verified(row["path"], row["sha256"]) for name, row in source_records.items()}
    if not all(key in source_paths for key in REQUIRED_SOURCES):
        raise ValueError("incomplete historical runtime/model source identities")
    contract, summary = (_json(source_paths[key]) for key in ("contract", "summary"))
    if (contract["model_id"] != replay["model_id"] or contract["architecture"] != replay["architecture"] or
            contract["target_frequency_ghz"] != replay["target_frequency_ghz"] or float(contract["target_frequency_ghz"]) != 15):
        raise ValueError("model/frequency/architecture identity mismatch")
    architecture = contract["architecture"]
    if (architecture.get("hidden_activation") != "gelu" or
            architecture.get("geometry_projection") != "sigmoid_to_training_envelope" or
            architecture["inverse_mlp"][0] != 4 or architecture["inverse_mlp"][-1] != 10 or
            architecture["forward_surrogate"][0] != 10 or architecture["forward_surrogate"][-1] != 4):
        raise ValueError("unsupported historical task or decoder")
    if (summary["method"].get("geometry_output_constraint") != "sigmoid_projection_to_observed_training_envelope" or
            summary["method"].get("local_refinement") != "disabled"):
        raise ValueError("historical decoder/refinement identity mismatch")
    if (summary["input_columns"] != contract["input_columns"] or summary["geometry_columns"] != contract["geometry_columns"] or
            summary["evaluation_isolation"]["row_counts"] != replay["split_rows"] or
            summary["training_count"] != replay["source_rows"] or replay["validation_rows_replayed"] != replay["split_rows"]["validation"]):
        raise ValueError("historical feature/split identity mismatch")
    if sum(replay["split_rows"].values()) != replay["source_rows"]:
        raise ValueError("historical source/split counts do not reconcile")
    normalizer = _normalizer(source_paths["weights"])
    if normalizer != replay["normalizer_from_original_weights"]:
        raise ValueError("normalizer identity differs from original replay")
    for key in ("weights", "summary"):
        artifact = contract["artifacts"][key]
        if artifact["sha256"] != source_records[key]["sha256"] or Path(artifact["filename"]).name != artifact["filename"]:
            raise ValueError("contract original model artifact mismatch")
    files = {}

    def copy(src, relative, expected=None):
        src = Path(src).resolve()
        expected = expected or sha256(src)
        _verified(src, expected)
        destination = _within(out, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with src.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        _verified(destination, expected)
        files[relative] = {"sha256": expected, "bytes": destination.stat().st_size}
        return relative

    layout = {"contract": copy(source_paths["contract"], "model/contract.json", source_records["contract"]["sha256"]),
              "runtime": copy(source_paths["runtime"], "runtime/frozen_mlp.py", source_records["runtime"]["sha256"]),
              "replay": copy(replay_path, "evidence/REPLAY_RECEIPT.json", replay_sha)}
    for key in ("weights", "summary"):
        layout[key] = copy(source_paths[key], "model/"+contract["artifacts"][key]["filename"], source_records[key]["sha256"])
    for key in set(source_paths)-set(layout):
        layout[key] = copy(source_paths[key], "evidence/source_"+key+source_paths[key].suffix, source_records[key]["sha256"])
    for name, record in replay.get("artifacts", {}).items():
        if Path(name).name != name:
            raise ValueError("invalid replay artifact filename")
        copy(_verified(record["path"], record["sha256"]), "evidence/"+name, record["sha256"])
    warning_path = replay_path.parent/"RUNTIME_WARNING_OBSERVATION.json"
    warning_status = "recorded_in_original_replay" if replay.get("runtime_warnings") else "not_separately_recorded"
    if warning_path.exists():
        warning = _json(warning_path)
        if warning["observed_replay_receipt_sha256"] != replay_sha:
            raise ValueError("warning evidence refers to a different replay")
        copy(warning_path, "evidence/RUNTIME_WARNING_OBSERVATION.json")
        warning_status = "original_warning_sidecar_preserved"
    config = {"schema": "bb_historical_10k_config.v1", "model_id": contract["model_id"],
              "architecture": architecture, "model_seed": contract["model_seed"],
              "target_frequency_ghz": contract["target_frequency_ghz"],
              "input_columns": contract["input_columns"], "geometry_columns": contract["geometry_columns"],
              "declared_support_lower": contract["declared_support_lower"], "declared_support_upper": contract["declared_support_upper"],
              "q_definition": contract.get("q_training_definition"), "source_rows": replay["source_rows"], "split_rows": replay["split_rows"],
              "trainer_identity": replay["trainer_identity"], "runtime_warning_evidence": warning_status,
              "boundary": BOUNDARY, "normalizer_space": "geometry lower/upper are standardized coordinates, not physical micrometres"}
    _write(out/"config.json", config)
    _write(out/"normalizer.json", normalizer)
    for name in ("config.json", "normalizer.json"):
        files[name] = {"sha256": sha256(out/name), "bytes": (out/name).stat().st_size}
    copy(__file__, "load_baseline.py")
    result = {"schema": PACKAGE_SCHEMA, "status": "PACKAGED_EXISTING_REPLAY_NOT_REPLAYED",
              "created_utc": datetime.now(timezone.utc).isoformat(), "model_id": contract["model_id"],
              "existing_replay_sha256": replay_sha, "layout": layout, "files": files,
              "boundary": BOUNDARY, "fresh_process_load": "NOT_RUN_BY_BUILDER",
              "dependencies": ["Python>=3.10", "NumPy"], "private_artifact": True}
    _write(out/"PACKAGE_MANIFEST.json", result)
    with (out/"SHA256SUMS").open("x", encoding="utf-8") as stream:
        for name in sorted([*files, "PACKAGE_MANIFEST.json"]):
            stream.write(f"{sha256(out/name)}  {name}\n")
    return {"status": result["status"], "package": str(out), "manifest_sha256": sha256(out/"PACKAGE_MANIFEST.json"), "model_id": contract["model_id"]}


class HistoricalBaseline:
    """Native single-frequency adapter, deliberately incompatible with BB tokens."""
    def __init__(self, model, config):
        self.model, self.config = model, config

    def predict(self, physical_targets, *, frequency_ghz=15.0):
        frequency = np.asarray(frequency_ghz)
        if frequency.ndim != 0 or float(frequency) != self.model.target_frequency_ghz:
            raise ValueError("historical baseline supports only its fixed 15 GHz physical task")
        targets = np.asarray(physical_targets, dtype=float)
        if targets.ndim != 2 or targets.shape[1] != 4 or not len(targets):
            raise ValueError("native historical input must be nonempty [N,4], not broadband/masked/S tokens")
        result = self.model.predict(targets)
        if not np.isfinite(result.geometry).all() or not np.isfinite(result.proxy_features).all():
            raise ValueError("historical runtime produced nonfinite output")
        return result


def load_package(package, *, expected_manifest_sha256):
    """Load using package-relative files only, with externally anchored manifest."""
    root = Path(package).resolve()
    manifest = _json(_verified(root/"PACKAGE_MANIFEST.json", expected_manifest_sha256))
    if manifest.get("schema") != PACKAGE_SCHEMA or manifest.get("boundary") != BOUNDARY:
        raise ValueError("unsupported historical package contract")
    for name, record in manifest["files"].items():
        _verified(_within(root, name), record["sha256"])
    layout = manifest["layout"]
    if any(name not in manifest["files"] for name in layout.values()) or not all(x in manifest["files"] for x in ("config.json", "normalizer.json", "load_baseline.py")):
        raise ValueError("unmanifested package dependency")
    config = _json(root/"config.json")
    if config["boundary"] != BOUNDARY or config["model_id"] != manifest["model_id"]:
        raise ValueError("historical config identity mismatch")
    if _normalizer(_within(root, layout["weights"])) != _json(root/"normalizer.json"):
        raise ValueError("packaged normalizer does not match original weights")
    runtime = _within(root, layout["runtime"])
    module_name = "_bb_historical_frozen_"+manifest["files"][layout["runtime"]]["sha256"]
    spec = importlib.util.spec_from_file_location(module_name, runtime)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    # Do not write runtime caches into an immutable copied delivery tree.
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    model = module.FrozenTandemMLP.load(_within(root, layout["weights"]).parent, contract_path=_within(root, layout["contract"]))
    if model.model_id != config["model_id"] or model.contract["architecture"] != config["architecture"]:
        raise ValueError("loaded model differs from packaged config")
    return HistoricalBaseline(model, config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--replay-receipt", required=True)
    build.add_argument("--expected-replay-sha256", required=True)
    build.add_argument("--out", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--package", required=True)
    inspect.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    if args.command == "build":
        result = build_package(args.replay_receipt, args.out, expected_replay_sha256=args.expected_replay_sha256)
    else:
        model = load_package(args.package, expected_manifest_sha256=args.expected_manifest_sha256)
        result = {"status": "LOADED_NO_PREDICTION", "config": model.config}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
