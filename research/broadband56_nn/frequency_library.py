"""Exact registry routing to existing frequency packages; no training or EMX.

Resolve reads only metadata. Infer delegates full package validation and target
handling to frequency_package.infer_index (and its existing load_index).
"""
import argparse
import hashlib
import json
from pathlib import Path

from . import frequency_package as package


def _read(path, expected=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("regular metadata file required: " + str(path))
    raw = path.read_bytes()
    pin = {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    if expected is not None and (pin["sha256"] != expected.get("sha256") or
            type(expected.get("bytes")) is not int or pin["bytes"] != expected["bytes"]):
        raise ValueError("metadata SHA/bytes mismatch: " + str(path))
    return json.loads(raw), pin


def _path(pin, base):
    path = Path(pin["path"])
    return path if path.is_absolute() else Path(base) / path


def resolve(registry_path, *, frequency_ghz, label_mode="STRICT_LUMPED"):
    """Verify exact metadata identity without reading weights or running a model."""
    if type(frequency_ghz) is not int or not 5 <= frequency_ghz <= 20 or label_mode not in package.MODES:
        raise ValueError("exact integer frequency 5..20 and supported label mode required")
    registry_path = Path(registry_path)
    registry, registry_pin = _read(registry_path)
    root = registry_path.resolve().parent
    receipt, receipt_pin = _read(root / "ROLLUP_RECEIPT.json")
    if receipt.get("status") != "PASS_SAVED_ARTIFACT_RECONCILIATION":
        raise ValueError("terminal qualified rollup receipt required")
    bindings = [p for p in receipt["artifacts"] if _path(p, root).resolve() == registry_path.resolve()]
    if len(bindings) != 1 or any(bindings[0].get(k) != registry_pin[k] for k in ("sha256", "bytes")):
        raise ValueError("registry SHA/bytes or receipt binding mismatch")
    rows = registry.get("models", [])
    frequencies = [r.get("frequency_ghz") for r in rows]
    if (registry.get("frequency_is_input") is not False or not rows or
            any(type(f) is not int or not 5 <= f <= 20 for f in frequencies) or
            len(set(frequencies)) != len(frequencies) or
            any(r.get("nearest_frequency_fallback") is not False for r in rows)):
        raise ValueError("registry requires unique integer routes; no frequency input or nearest fallback")
    selected = next((r for r in rows if r["frequency_ghz"] == frequency_ghz), None)
    if selected is None:
        raise ValueError("frequency unavailable; no nearest-frequency fallback")
    package_path = _path(selected["package_receipt"], root)
    index_path = _path(selected["model_index"], root)
    pack, pack_pin = _read(package_path, selected["package_receipt"])
    index, index_pin = _read(index_path, selected["model_index"])
    if (package_path.name != "PACKAGE_RECEIPT.json" or index_path.name != "MODEL_INDEX.json" or
            package_path.resolve().parent != index_path.resolve().parent or
            (package_path.parent / "PACKAGE_FAILED.json").exists() or
            pack.get("schema") != "frequency_package_receipt.v1" or
            pack.get("status") != "PORTABLE_INFERENCE_PACKAGE_READY"):
        raise ValueError("qualified same-directory package/index receipt required")
    bound = pack["model_index"]
    if (_path(bound, package_path.parent).resolve() != index_path.resolve() or
            bound.get("sha256") != index_pin["sha256"] or
            ("bytes" in bound and bound["bytes"] != index_pin["bytes"])):
        raise ValueError("package/index binding mismatch")
    pair = pack["inputs"]["pair"]
    if (_path(pair, package_path.parent).resolve() != _path(selected["pair"], root).resolve() or
            any(pair.get(k) != selected["pair"].get(k) for k in ("sha256", "bytes"))):
        raise ValueError("package/registry pair binding mismatch")
    if index.get("schema") != "frequency_model_index.v1" or index.get("frequency_is_input", False) is not False:
        raise ValueError("qualified exact-frequency model index required")
    slots = index["frequencies"]
    slot_freqs = [r["frequency_ghz"] for r in slots]
    if (any(type(f) is not int or not 5 <= f <= 60 for f in slot_freqs) or
            len(set(slot_freqs)) != len(slot_freqs)):
        raise ValueError("ambiguous index frequency route")
    model = package._route(index, frequency_ghz, label_mode)
    slot = next(r for r in slots if r["frequency_ghz"] == frequency_ghz)["label_modes"][label_mode]
    model_id = slot["model_id"]
    if (slot["model_available"] is not True or model.get("model_id") != model_id or
            type(model.get("frequency_ghz")) is not int or model["frequency_ghz"] != frequency_ghz or
            model.get("label_mode") != label_mode or selected.get("model_id", model_id) != model_id or
            selected.get("status") != model.get("training_status") or
            pack.get("model_status") != model.get("training_status") or
            slot.get("model_status") != model.get("training_status")):
        raise ValueError("selected model ID/frequency/mode/status route mismatch")
    return {"schema": "frequency_library_resolution.v1", "status": "RESOLVED",
        "frequency_ghz": frequency_ghz, "label_mode": label_mode, "model_id": model_id,
        "model_status": model["training_status"], "convergence_established": model.get("convergence_established", False),
        "formal_10k": model.get("formal_10k"), "experiment_class": model.get("experiment_class"),
        "source_snapshot_geometries": model.get("source_snapshot_geometries"),
        "dataset_sha256": model.get("dataset_sha256"), "frequency_is_input": False,
        "nearest_frequency_fallback": False, "weights_verified": False, "models_loaded": 0,
        "validation_source": "SELF_PROXY", "REAL_EMX_VALIDATION": "NOT_RUN",
        "physical_validation_scope": "THIS_NEW_CALL_ONLY_NO_RESEARCH_EMX_BORROWING",
        "source": {"registry": registry_pin, "rollup_receipt": receipt_pin,
                   "package_receipt": pack_pin, "model_index": index_pin}}


def infer(registry_path, targets, *, frequency_ghz, label_mode="STRICT_LUMPED", allow_extrapolation=False):
    """Pass original targets unchanged; full loading, support checks and inference stay upstream."""
    if type(allow_extrapolation) is not bool:
        raise ValueError("allow_extrapolation must be an explicit boolean")
    route = resolve(registry_path, frequency_ghz=frequency_ghz, label_mode=label_mode)
    result = package.infer_index(route["source"]["model_index"]["path"], targets,
        frequency_ghz=frequency_ghz, label_mode=label_mode, allow_extrapolation=allow_extrapolation)
    return {"schema": "frequency_library_inference.v1", "status": "SELF_PROXY_INFERENCE_COMPLETE",
        "route": route, "result": result, "allow_extrapolation": allow_extrapolation,
        "validation_source": "SELF_PROXY", "REAL_EMX_VALIDATION": "NOT_RUN",
        "physical_validation_scope": "THIS_NEW_CALL_ONLY_NO_RESEARCH_EMX_BORROWING"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("resolve", "infer"):
        cli = sub.add_parser(action)
        cli.add_argument("--registry", required=True)
        cli.add_argument("--frequency-ghz", type=int, required=True)
        cli.add_argument("--label-mode", choices=package.MODES, required=True)
        if action == "infer":
            cli.add_argument("--targets", required=True, help="JSON four-vector or array; passed unchanged")
            cli.add_argument("--allow-extrapolation", action="store_true")
    args = parser.parse_args(argv)
    kwargs = {"frequency_ghz": args.frequency_ghz, "label_mode": args.label_mode}
    result = (resolve(args.registry, **kwargs) if args.action == "resolve" else
              infer(args.registry, json.loads(args.targets), **kwargs, allow_extrapolation=args.allow_extrapolation))
    print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
    return result


if __name__ == "__main__":
    main()
