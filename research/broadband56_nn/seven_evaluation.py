"""Seven-model v3 common 15-GHz evaluation, separate from six-model broadband.

No training, simulator, scan, refinement, target substitution, or historical
replay ranking. All numerical results are proxy evidence, not generated EMX.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import time

import numpy as np
import torch

from . import evaluation as broadband
from .io import canonical_sha, load_checkpoint, manifest_tree, read_json, save_json, sha256, utc_now
from .metrics import inverse_metrics
from .models import PACKAGE_MAPPING
from .physics import geometry_feasibility
from .specs import tokenize
from .training import Bundle, TrainConfig, configure_device, decoder_from_contract


PACKAGES = ("BB00", *PACKAGE_MAPPING)
FORWARDS = ("F1", "F2", "F3", "FREF", "BB00_FORWARD")
RECORDS = (*FORWARDS, *PACKAGES)
FEATURES = ("Lp_nH", "Ls_nH", "Qmin", "K_abs")
SELECTION = ("lowest common FREF continuous-geometry full-panel scaled RMSE among "
             "models with no unevaluable requested predictions; otherwise NOT_DETERMINED")


def _pin(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha256(path)}


def seven_protocol_identity():
    root = Path(__file__).parent
    sources = {name: sha256(root / name) for name in
               ("seven_evaluation.py", "training.py", "io.py")}
    # BB00 is independently implemented; its exact adapter is part of the gate.
    for name in ("bb00.py", "baseline_training.py"):
        sources[name] = sha256(root / name) if (root / name).is_file() else "NOT_IMPLEMENTED"
    protocol = {"schema": "bb_seven_common_protocol.v1", "task": "PHYSICAL_15GHZ",
                "frequency_hz": 15_000_000_000, "features": list(FEATURES),
                "relation": "EQ for all four requested physical targets",
                "source_domain": "all four source-strict-valid, finite 15GHz labels",
                "order": "original frozen dataset geometry order; no sampling/reranking",
                "scale_policy": "shared broadband normalizer y_scale, fitted on train only",
                "invalid_policy": "fixed target denominator; invalid geometry or response is failure",
                "selection": SELECTION, "inference": "one geometry, no scan/search/refinement",
                "comparison": "historical-method versus broadband-system common task; unequal supervision, not pure architecture causality",
                "broadband_protocol": broadband.evaluation_protocol_identity(), "sources": sources}
    return {"sha256": canonical_sha(protocol), "protocol": protocol}


def _data_identity(root):
    root = Path(root).resolve()
    manifest = read_json(root / "data_manifest.json")
    if manifest.get("status") != "PASS" or manifest.get("schema") != "bb_data_manifest.v1":
        raise ValueError("common evaluation requires a PASS data manifest")
    norm = read_json(root / "normalizer.json")
    if manifest.get("normalizer_fit_split") != "train":
        raise ValueError("shared scaling must be fitted on train only")
    for name in ("dataset.npz", "normalizer.json", "splits.json"):
        entry = manifest["artifacts"][name]
        if sha256(root / entry["path"]) != entry["sha256"]:
            raise ValueError("data artifact changed: " + name)
    assignments = read_json(root / "splits.json")["by_geometry_sha256"]
    codes = {"train": 0, "validation": 1, "test": 2}
    split_map = {key: codes[value] for key, value in assignments.items()}
    return {"data_sha": sha256(root / "dataset.npz"), "normalizer_sha": canonical_sha(norm),
            "split_sha256": sha256(root / "splits.json"),
            "split_assignments_sha256": canonical_sha(split_map),
            "data_manifest_sha256": sha256(root / "data_manifest.json")}


def _target_document(bundle, split, tolerance_fraction):
    if split not in ("validation", "test"):
        raise ValueError("common panel split must be validation or test")
    if isinstance(tolerance_fraction, bool) or not np.isfinite(tolerance_fraction) or tolerance_fraction < 0:
        raise ValueError("tolerance must be finite nonnegative fixed-scale fraction")
    frequency = np.asarray(bundle.arrays["frequency_hz"])
    if not np.array_equal(frequency, np.arange(5, 61) * 1_000_000_000):
        raise ValueError("common panel requires the exact 56-point production frequency axis")
    at = 10
    population = np.flatnonzero(bundle.arrays["split"] == {"validation": 1, "test": 2}[split])
    valid = np.asarray(bundle.arrays["y_valid"])
    if valid.dtype != np.bool_:
        raise ValueError("source physical validity must be boolean")
    eligible = valid[population, at].all(-1) & np.isfinite(bundle.arrays["y"][population, at]).all(-1)
    if "strict_lumped_valid" in bundle.arrays:
        eligible &= bundle.arrays["strict_lumped_valid"][population, at]
    indices = population[eligible]
    if not len(indices):
        raise ValueError("NO_VALID_SOURCE_15GHZ_TARGETS; do not invent targets")
    scale = np.asarray(bundle.norm["y_scale"], dtype=np.float64)
    if scale.shape != (4,) or not np.isfinite(scale).all() or (scale <= 0).any():
        raise ValueError("shared train-only physical scale must be positive shape4")
    ids = bundle.arrays["geometry_ids"][indices].astype(str).tolist()
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate common target IDs")
    return {"schema": "bb_seven_common_15ghz_targets.v1", "status": "FROZEN",
            **_data_identity(bundle.root), "split": split, "task": "PHYSICAL_15GHZ",
            "frequency_hz": 15_000_000_000, "frequency_index": at,
            "feature_order": list(FEATURES), "source_indices": indices.tolist(), "target_ids": ids,
            "target_id_order_sha256": canonical_sha(ids),
            "targets": np.asarray(bundle.arrays["y"][indices, at], dtype=np.float64).tolist(),
            "condition_mask": [[True] * 4 for _ in indices], "relation": [0, 0, 0, 0],
            "channel_scale": scale.tolist(), "tolerance_fraction": float(tolerance_fraction),
            "tolerance_physical_units": (scale * tolerance_fraction).tolist(),
            "source_split_geometries": int(len(population)), "target_count": int(len(indices)),
            "source_label_ineligible_geometries": int((~eligible).sum()),
            "input_policy": "only four requested physical values at15GHz; no S, other55 physical values, geometry or hidden validity",
            "source_domain": "strict_lumped_valid AND all four finite physical labels at15GHz",
            "evaluation_protocol_sha256": seven_protocol_identity()["sha256"],
            "numerical_model_inference_performed": False, "real_emx_validation": "NOT_RUN"}


def freeze_common_15ghz_targets(data_root, out, *, split="validation", tolerance_fraction=0.05):
    """Create a target-only, no-clobber plan before inspecting model responses."""
    path = Path(out).resolve()
    if path.exists():
        raise FileExistsError(path)
    document = _target_document(Bundle(data_root), split, tolerance_fraction)
    save_json(path, {**document, "created_utc": utc_now()})
    return _pin(path)


def _verify_targets(path, bundle, split):
    document = read_json(path)
    expected = _target_document(bundle, split, document["tolerance_fraction"])
    if {key: document.get(key) for key in expected} != expected:
        raise ValueError("common target plan differs from exact snapshot/source-valid IDs/order/scales/protocol")
    return document


def physical15_spec(targets, frequency_hz, *, tolerance_fraction=0.05, device="cpu"):
    """Construct inputs from four values only; never accept full source labels."""
    targets = torch.as_tensor(targets, dtype=torch.float32, device=device)
    if targets.ndim != 2 or targets.shape[1] != 4 or not bool(torch.isfinite(targets).all()):
        raise ValueError("PHYSICAL_15GHZ accepts finite [N,4] requests only")
    frequency = torch.as_tensor(frequency_hz, dtype=torch.float32, device=device)
    if frequency.shape != (56,) or not bool(frequency[10] == 15_000_000_000):
        raise ValueError("missing exact15GHz frequency position")
    n = len(targets)
    s = torch.zeros((n, 56, 32), device=device)
    y = torch.zeros((n, 56, 4), device=device)
    ym = torch.zeros_like(y, dtype=torch.bool)
    y[:, 10], ym[:, 10] = targets, True
    return {"s_target": s, "s_mask": torch.zeros_like(s, dtype=torch.bool),
            "y_target": y, "y_mask": ym, "frequency_hz": frequency,
            "s_tolerance": torch.zeros_like(s), "y_tolerance": ym.to(y.dtype) * tolerance_fraction,
            "y_relation": torch.zeros_like(y, dtype=torch.long)}


def _registry(path, identity):
    path = Path(path).resolve()
    registry = read_json(path)
    if registry.get("schema") != "bb_seven_registry.v1" or set(registry.get("records", {})) != set(RECORDS):
        raise ValueError("registry must contain exactly twelve named new-study checkpoints")
    if any(registry.get(key) != identity[key] for key in ("data_sha", "normalizer_sha")):
        raise ValueError("seven registry snapshot/shared normalizer mismatch")
    records = {}
    for name, original in registry["records"].items():
        record = {**original, "name": name}
        for kind in ("checkpoint", "receipt"):
            ref = Path(record[kind]["path"])
            ref = ref if ref.is_absolute() else path.parent / ref
            if sha256(ref) != record[kind]["sha256"]:
                raise ValueError(f"{name} {kind} SHA mismatch")
            record[kind] = _pin(ref)
        receipt = read_json(record["receipt"]["path"])
        if (receipt.get("data_sha") != identity["data_sha"] or
                receipt.get("status") not in ("PRETRAINED", "PRETRAINED_PARTIAL", "SMOKE_TRAINED", "PARTIAL", "TRAINED_AND_EVALUATED") or
                receipt.get("best_sha256") != record["checkpoint"]["sha256"] or
                int(receipt.get("updates_this_run", 0)) <= 0 or
                receipt.get("test_access") is not False or
                receipt.get("resume_probe") is True or receipt.get("research_comparison_eligible") is False or
                not receipt.get("trainable_weights_changed")):
            raise ValueError(f"{name} receipt does not prove eligible new-study training")
        _verify_new_training_lineage(record, identity)
        record["completed_updates"] = receipt["completed_step"]
        records[name] = record
    return records


def _verify_new_training_lineage(record, identity):
    """Reject older-data warm starts and historical replay before any inference."""
    name = record["name"]
    current = Path(record["checkpoint"]["path"])
    seen, baseline = set(), None
    while True:
        digest = sha256(current)
        if digest in seen or len(seen) >= 1000:
            raise ValueError("invalid/cyclic checkpoint parent lineage")
        seen.add(digest)
        state = load_checkpoint(current)
        expected_schema = "bb00_training_state.v1" if name.startswith("BB00") else "bb_training_state.v1"
        if state.get("schema") != expected_schema or state.get("data_sha") != identity["data_sha"]:
            raise ValueError(name + " is not new-snapshot training; old/replay weights are ineligible")
        if state.get("data_manifest_sha") != identity["data_manifest_sha256"]:
            raise ValueError(name + " source manifest identity differs")
        if canonical_sha(state.get("split_by_geometry_sha256")) != identity["split_assignments_sha256"]:
            raise ValueError(name + " checkpoint splits differ from the frozen common split")
        expected_role = "forward" if name in FORWARDS else "inverse"
        expected_kind = ("BB00" if name.startswith("BB00") else
                         ("F1" if name == "FREF" else name) if expected_role == "forward" else PACKAGE_MAPPING[name][1])
        if state.get("role") != expected_role or state.get("kind") != expected_kind or state.get("step", 0) <= 0:
            raise ValueError(name + " checkpoint role/kind/positive-step mismatch")
        if state.get("historical_weights_loaded") or state.get("historical_replay"):
            raise ValueError("historical weights cannot enter from-scratch primary comparison")
        if state.get("resume_probe") is True or state.get("research_comparison_eligible") is False:
            raise ValueError("resume diagnostic branch is ineligible for research ranking")
        config = state.get("train_config", {})
        if (config.get("seed") != (29 if name == "FREF" else 17) or config.get("effective_batch") != 32 or
                config.get("micro_batch") != 8 or config.get("validation_interval") != 32):
            raise ValueError("checkpoint differs from v3 seed/batch/validation profile; diagnostic branches are ineligible")
        if canonical_sha(state["normalizer"]) != state["normalizer_sha"]:
            raise ValueError("checkpoint embedded normalizer digest mismatch")
        if not name.startswith("BB00") and state["normalizer_sha"] != identity["normalizer_sha"]:
            raise ValueError("broadband checkpoint normalizer differs from shared snapshot")
        invariant = {key: state[key] for key in ("role", "kind", "normalizer_sha", "contract_sha", "geometry_dim", "split_by_geometry_sha256")}
        if baseline is None:
            baseline = invariant
        elif invariant != baseline:
            raise ValueError("resume lineage scientific/split identity changed")
        parent = state.get("parent_checkpoint")
        if parent is None:
            if name.startswith("BB00") and state.get("initialization") != "RANDOM_FROM_SCRATCH_NO_LEGACY_WEIGHTS":
                raise ValueError("BB00 root must prove random initialization without legacy weights")
            break
        prior = Path(parent["path"])
        current = prior if prior.is_absolute() else current.parent / prior
        if sha256(current) != parent["sha256"]:
            raise ValueError("resume parent checkpoint SHA mismatch")


def _checkpoint_shas(records):
    return {name: record["checkpoint"]["sha256"] for name, record in records.items()}


def freeze_seven_configuration(registry_path, data_root, validation_summary, target_plan_test, out):
    """Freeze selection after validation, before any sealed-test model inference."""
    if Path(out).exists():
        raise FileExistsError(out)
    identity = _data_identity(data_root)
    records = _registry(registry_path, identity)
    summary = read_json(validation_summary)
    target = _verify_targets(target_plan_test, Bundle(data_root), "test")
    if (summary.get("status") != "COMPLETE_PROXY_EVALUATION" or summary.get("split") != "validation" or
            summary.get("checkpoints") != _checkpoint_shas(records) or
            summary.get("evaluation_protocol_sha256") != seven_protocol_identity()["sha256"] or
            summary.get("test_used_to_select_model") is not False or
            any(summary.get(key) != identity[key] for key in identity)):
        raise ValueError("configuration freeze requires complete exact seven-model validation evidence")
    for field in ("target_plan", "evaluation_request"):
        evidence = summary[field]
        if sha256(evidence["path"]) != evidence["sha256"]:
            raise ValueError("validation evidence changed: " + field)
    validation_target = _verify_targets(summary["target_plan"]["path"], Bundle(data_root), "validation")
    validation_request = read_json(summary["evaluation_request"]["path"])
    if (datetime.fromisoformat(target["created_utc"]) >= datetime.fromisoformat(validation_request["created_utc"]) or
            any(target[key] != validation_target[key] for key in
                ("channel_scale", "tolerance_fraction", "tolerance_physical_units", "relation", "evaluation_protocol_sha256"))):
        raise ValueError("test target protocol must be predeclared before validation inference and match its scales/tolerances")
    if set(summary.get("packages", {})) != set(PACKAGES) or summary.get("selection_criterion") != SELECTION:
        raise ValueError("validation summary lacks exact seven-package selection protocol")
    for name, entry in summary["packages"].items():
        if sha256(entry["metrics"]["path"]) != entry["metrics"]["sha256"]:
            raise ValueError("validation package metrics changed: " + name)
        report = read_json(entry["metrics"]["path"])
        if report["selected_checkpoint"]["sha256"] != records[name]["checkpoint"]["sha256"]:
            raise ValueError("validation package used different selected weights")
    document = {"schema": "bb_seven_configuration_freeze.v1", "status": "FROZEN", **identity,
                "created_utc": utc_now(), "registry": _pin(registry_path),
                "checkpoints": _checkpoint_shas(records), "target_plan": _pin(target_plan_test),
                "target_id_order_sha256": target["target_id_order_sha256"],
                "evaluation_protocol_sha256": seven_protocol_identity()["sha256"],
                "validation_summary": _pin(validation_summary),
                "best_validation_candidate": summary["best_validation_candidate"],
                "selection_criterion": SELECTION, "test_use": "report only; no selection or tuning"}
    save_json(out, document)
    return _pin(out)


def _verify_freeze(path, records, identity, target_plan_path, split):
    if split != "test":
        return {"status": "VALIDATION_ONLY", "test_access": False}
    if path is None:
        raise ValueError("sealed common15 test requires exact seven-model configuration freeze")
    freeze = read_json(path)
    if (freeze.get("schema") != "bb_seven_configuration_freeze.v1" or freeze.get("status") != "FROZEN" or
            freeze.get("checkpoints") != _checkpoint_shas(records) or
            freeze.get("evaluation_protocol_sha256") != seven_protocol_identity()["sha256"] or
            freeze.get("target_plan", {}).get("sha256") != sha256(target_plan_path) or
            any(freeze.get(key) != identity[key] for key in identity)):
        raise ValueError("sealed common15 configuration/checkpoint/target/protocol mismatch")
    validation_pin = freeze["validation_summary"]
    if sha256(validation_pin["path"]) != validation_pin["sha256"]:
        raise ValueError("frozen validation selection evidence changed")
    return {"status": "EXACT_SEVEN_CONFIGURATION_FROZEN", "receipt": _pin(path), "test_access": True}


def physical_response_metrics(predicted, targets, scale, *, valid=None, feasible=None, tolerance_fraction=0.05):
    """Every original target remains in failure/hit denominators, including NaN."""
    targets = np.asarray(targets, dtype=np.float64)
    n = len(targets)
    predicted = np.full_like(targets, np.nan) if predicted is None else np.asarray(predicted, dtype=np.float64)
    if predicted.shape != targets.shape or targets.shape != (n, 4) or n == 0:
        raise ValueError("physical responses require matching nonempty [N,4] arrays")
    validity = np.isfinite(predicted) if valid is None else np.asarray(valid)
    feasibility = np.ones(n, dtype=bool) if feasible is None else np.asarray(feasible)
    if validity.dtype != np.bool_ or validity.shape != (n, 4) or feasibility.dtype != np.bool_ or feasibility.shape != (n,):
        raise ValueError("prediction validity/feasibility must be exact boolean shapes")
    validity = validity & feasibility[:, None]
    mask = np.ones((n, 1, 4), dtype=bool)
    result = inverse_metrics(predicted[:, None], targets[:, None], mask, scale,
                             prediction_valid=validity[:, None], tolerance_fraction=tolerance_fraction)
    features = {}
    for channel, name in enumerate(FEATURES):
        detail = inverse_metrics(predicted[:, None, channel:channel + 1], targets[:, None, channel:channel + 1],
                    mask[:, :, channel:channel + 1], np.asarray(scale)[channel:channel + 1],
                    prediction_valid=validity[:, None, channel:channel + 1], tolerance_fraction=tolerance_fraction)
        numeric = detail["valid_requested_numeric"]
        features[name] = {"unit": "nH" if channel < 2 else "dimensionless",
                          "mae": None if detail["normalized_mae"] is None else detail["normalized_mae"] * scale[channel],
                          "conditional_evaluable_mae": None if numeric["normalized_mae"] is None else numeric["normalized_mae"] * scale[channel],
                          "conditional_denominator": numeric["condition_denominator"],
                          "fixed_denominator": n, "invalid_count": detail["invalid_requested_predictions"]}
    result.update(physical_unit_features=features, geometry_infeasible_count=int((~feasibility).sum()),
                  target_failure_count=result["geometries_with_any_condition_violation"],
                  joint_hit_count=n - result["geometries_with_any_condition_violation"],
                  joint_hit_denominator=n, joint_hit_rate=1.0 - result["geometry_any_violation_rate"],
                  real_emx_validation="NOT_RUN", physical_accuracy="NOT_ESTABLISHED")
    return result


def _load_component(record, bundle, device):
    name = record["name"]
    if name not in ("BB00", "BB00_FORWARD"):
        role = "forward" if name in FORWARDS else "inverse"
        kind = ("F1" if name == "FREF" else name) if role == "forward" else PACKAGE_MAPPING[name][1]
        return broadband._load_model(record, bundle, device, role, kind)
    from .bb00 import load_bb00
    model, state = load_bb00(record["checkpoint"]["path"], device=str(device),
                             expected_sha256=record["checkpoint"]["sha256"])
    if (state["data_sha"] != bundle.data_sha or state["geometry_dim"] != bundle.dim or
            state["role"] != ("forward" if name == "BB00_FORWARD" else "inverse") or
            canonical_sha(state["contract"]) != state["contract_sha"]):
        raise ValueError("BB00 new-training snapshot/role/contract mismatch")
    if state.get("historical_replay") or state.get("mode") == "historical_replay":
        raise ValueError("historical replay cannot enter new-study ranking")
    model.eval().requires_grad_(False)
    meta = {key: state.get(key) for key in ("role", "kind", "step", "contract", "contract_sha", "architecture",
             "model_sha", "forward_model_sha", "forward_checkpoint", "forward_checkpoint_sha256", "normalizer_sha")}
    meta["parameter_count"] = sum(p.numel() for p in model.parameters())
    meta["seed"] = state.get("train_config", {}).get("seed")
    return model, meta


def _geometry_flags(geometry, contract):
    check = geometry_feasibility(torch.as_tensor(geometry, dtype=torch.float64), contract["field_names"],
                                contract["lower"], contract["upper"], contract.get("topology_contract"))
    return check["analytical_pass"].cpu().numpy()


def _query_physical(model, name, geometry, bundle, contract, device, micro_batch):
    n = len(geometry)
    values, valid = np.full((n, 4), np.nan), np.zeros((n, 4), dtype=bool)
    errors = []
    for start in range(0, n, micro_batch):
        end = min(n, start + micro_batch)
        finite = np.isfinite(geometry[start:end]).all(-1)
        ix = np.arange(start, end)[finite]
        if not len(ix):
            continue
        try:
            tensor = torch.as_tensor(geometry[ix], dtype=torch.float32, device=device)
            with torch.no_grad():
                if name == "BB00_FORWARD":
                    out = model(tensor).cpu().numpy().astype(np.float64)
                    if out.shape != (len(ix), 4):
                        raise ValueError("BB00 direct forward must return [N,4]")
                    flags = np.isfinite(out) & np.column_stack((out[:, 0] > 0, out[:, 1] > 0,
                                                               out[:, 2] > 0, out[:, 3] >= 0))
                else:
                    raw = broadband._raw_forward(model, tensor, bundle).cpu().numpy()
                    physical, physical_valid = broadband._physical_arrays(raw, bundle, contract, micro_batch, "cpu")
                    out, flags = physical[:, 10], physical_valid[:, 10]
            values[ix], valid[ix] = out, flags
        except (RuntimeError, ValueError, FloatingPointError) as exc:
            errors.append({"rows": ix.tolist(), "error_type": type(exc).__name__, "error": str(exc)})
    return values, valid, errors


def _inverse_geometry(model, name, targets, bundle, contract, device, micro_batch, tolerance):
    geometry = np.full((len(targets), bundle.dim), np.nan)
    errors = []
    decoder = None if name == "BB00" else decoder_from_contract(contract, device)
    for start in range(0, len(targets), micro_batch):
        end = min(len(targets), start + micro_batch)
        try:
            with torch.no_grad():
                if name == "BB00":
                    predicted = model(torch.as_tensor(targets[start:end], dtype=torch.float32, device=device))
                else:
                    spec = physical15_spec(targets[start:end], bundle.arrays["frequency_hz"],
                                           tolerance_fraction=tolerance, device=device)
                    tokens, mask = tokenize(spec, bundle.norm)
                    predicted = decoder(model(tokens, mask))
                if predicted.shape != (end - start, bundle.dim):
                    raise ValueError("inverse geometry shape differs")
                geometry[start:end] = predicted.cpu().numpy().astype(np.float64)
        except (RuntimeError, ValueError, FloatingPointError) as exc:
            errors.append({"rows": list(range(start, end)), "error_type": type(exc).__name__, "error": str(exc)})
    return geometry, errors


def _write_index(out):
    save_json(out / "ARTIFACT_MANIFEST.json", {"files": manifest_tree(out)})
    with (out / "SHA256SUMS.txt").open("x") as stream:
        for item in manifest_tree(out, exclude=("SHA256SUMS.txt",)):
            stream.write(f"{item['sha256']}  {item['path']}\n")


def evaluate_seven(data_root, registry_path, target_plan_path, out_dir, *, split="validation",
                   configuration_freeze=None, device="mps", micro_batch=8, threads=2):
    """Evaluate all seven on identical four-input one-shot physical15 targets."""
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        if split not in ("validation", "test") or micro_batch <= 0 or threads <= 0:
            raise ValueError("invalid evaluation split/batch/threads")
        identity = _data_identity(data_root)
        records = _registry(registry_path, identity)
        gate = _verify_freeze(configuration_freeze, records, identity, target_plan_path, split)
        # Test authorization is checked BEFORE Bundle loads test response arrays.
        bundle = Bundle(data_root)
        plan = _verify_targets(target_plan_path, bundle, split)
        if datetime.fromisoformat(plan["created_utc"]) >= datetime.fromisoformat(utc_now()):
            raise ValueError("target plan must predate model evaluation")
        torch_device = configure_device(TrainConfig("forward", "F1", device=device, threads=threads))
        targets, indices = np.asarray(plan["targets"]), np.asarray(plan["source_indices"])
        scales, tolerance = np.asarray(plan["channel_scale"]), plan["tolerance_fraction"]
        common, common_meta = _load_component(records["FREF"], bundle, torch_device)
        contract, contract_sha = common_meta["contract"], common_meta["contract_sha"]
        grid_um = broadband._grid_from_contract(contract)
        request = {"schema": "bb_seven_evaluation_request.v1", "created_utc": utc_now(), **identity,
                   "split": split, "registry": _pin(registry_path), "checkpoints": _checkpoint_shas(records),
                   "target_plan": _pin(target_plan_path), "configuration_freeze": gate,
                   "evaluation_protocol": seven_protocol_identity(), "device": device,
                   "micro_batch": micro_batch, "threads": threads, "geometry_grid_um": grid_um}
        save_json(out / "EVALUATION_REQUEST.json", request)
        save_json(out / "common_15ghz_specs.json", plan)
        source_geometry = np.asarray(bundle.arrays["geometry"][indices])
        forward_reports, reports = {}, {}
        # Each required forward is scored on exactly the same valid source15 IDs.
        for name in FORWARDS:
            model, meta = (common, common_meta) if name == "FREF" else _load_component(records[name], bundle, torch_device)
            if meta["contract_sha"] != contract_sha:
                raise ValueError("forward geometry/port contracts differ")
            begin = time.monotonic()
            values, flags, errors = _query_physical(model, name, source_geometry, bundle, contract, torch_device, micro_batch)
            forward_reports[name] = {"metrics": physical_response_metrics(values, targets, scales, valid=flags,
                  tolerance_fraction=tolerance), "inference_seconds": time.monotonic() - begin,
                  "parameter_count": meta["parameter_count"], "selected_step": meta["step"],
                  "completed_updates": records[name]["completed_updates"], "checkpoint": records[name]["checkpoint"],
                  "errors": errors, "source_target_ids_sha256": plan["target_id_order_sha256"],
                  "prediction_domain": ("direct4 finite positive Lp/Ls/Q, nonnegative K; strict SRF unobservable, not claimed"
                     if name == "BB00_FORWARD" else "exact S-derived strict_lumped_valid at15GHz"),
                  "broadband_spectrum": "NOT_SUPPORTED" if name == "BB00_FORWARD" else "SEPARATE_SIX_MODEL_REPORT"}
            if name != "FREF":
                del model
        for name in PACKAGES:
            model, meta = _load_component(records[name], bundle, torch_device)
            own_name = "BB00_FORWARD" if name == "BB00" else PACKAGE_MAPPING[name][0]
            own, own_meta = _load_component(records[own_name], bundle, torch_device)
            if meta["contract_sha"] != contract_sha or own_meta["contract_sha"] != contract_sha:
                raise ValueError("inverse geometry/port contracts differ")
            broadband._verify_own_forward_binding(meta, records[name], records[own_name], own_meta, common_meta)
            begin = time.monotonic()
            continuous, inverse_errors = _inverse_geometry(model, name, targets, bundle, contract,
                              torch_device, micro_batch, tolerance)
            inference_seconds = time.monotonic() - begin
            grid = np.full_like(continuous, np.nan)
            finite = np.isfinite(continuous).all(-1)
            if finite.any():
                grid[finite] = broadband._grid_geometry(continuous[finite], grid_um)
            report = {"package": name, "task": "PHYSICAL_15GHZ", "status": "EVALUATED_PROXY_ONLY",
                      "target_plan": _pin(target_plan_path), "target_id_order_sha256": plan["target_id_order_sha256"],
                      "target_count": len(targets), "selected_step": meta["step"],
                      "selected_checkpoint": records[name]["checkpoint"],
                      "completed_updates": records[name]["completed_updates"], "parameter_count": meta["parameter_count"],
                      "inverse_inference_seconds": inference_seconds, "inverse_errors": inverse_errors,
                      "own_forward": records[own_name]["checkpoint"], "common_forward": records["FREF"]["checkpoint"],
                      "own_proxy_is_not_primary_ranking": True, "broadband": "NOT_SUPPORTED" if name == "BB00" else "SEPARATE_SIX_MODEL_REPORT",
                      "own_prediction_domain": forward_reports[own_name]["prediction_domain"],
                      "manufacturability": "NOT_PROVEN", "real_emx_validation": "NOT_RUN", "modes": {}}
            arrays = {"geometry_continuous": continuous, "geometry_grid": grid, "target": targets}
            for mode, geometry in (("continuous", continuous), ("grid", grid)):
                feasible = _geometry_flags(geometry, contract)
                arrays[mode + "_analytical_pass"] = feasible
                mode_report = {"analytical_pass": int(feasible.sum()), "analytical_denominator": len(targets),
                               "nonfinite_geometry_count": int((~np.isfinite(geometry).all(-1)).sum()),
                               "geometry_check_dtype": "float64", "grid_um": grid_um if mode == "grid" else None}
                for category, forward_model, forward_name in (("common", common, "FREF"), ("own", own, own_name)):
                    begin = time.monotonic()
                    values, flags, errors = _query_physical(forward_model, forward_name, geometry, bundle, contract, torch_device, micro_batch)
                    mode_report[category] = physical_response_metrics(values, targets, scales, valid=flags,
                                                       feasible=feasible, tolerance_fraction=tolerance)
                    mode_report[category + "_query_seconds"] = time.monotonic() - begin
                    mode_report[category + "_errors"] = errors
                    arrays[mode + "_" + category + "_physical"] = values
                    arrays[mode + "_" + category + "_valid"] = flags & feasible[:, None]
                report["modes"][mode] = mode_report
            cc, gc = (report["modes"][mode]["common"] for mode in ("continuous", "grid"))
            report["grid_effect"] = {"fixed_target_denominator": len(targets),
                    "common_joint_hit_rate_delta_grid_minus_continuous": gc["joint_hit_rate"] - cc["joint_hit_rate"],
                    "invalid_request_delta_grid_minus_continuous": gc["invalid_requested_predictions"] - cc["invalid_requested_predictions"],
                    "analytical_pass_delta_grid_minus_continuous": report["modes"]["grid"]["analytical_pass"] - report["modes"]["continuous"]["analytical_pass"],
                    "finite_geometry_count": int(finite.sum()),
                    "max_absolute_coordinate_change_um_on_finite": float(np.max(np.abs(grid[finite] - continuous[finite]))) if finite.any() else None,
                    "repair_or_search_used": False}
            folder = out / name
            folder.mkdir()
            np.savez_compressed(folder / "predictions.npz", **arrays)
            with (folder / "candidates.csv").open("x", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["target_id", "candidate_status", "real_emx_validation",
                                 "continuous_analytical_pass", "grid_analytical_pass",
                                 *("continuous__" + k for k in contract["field_names"]),
                                 *("grid__" + k for k in contract["field_names"])])
                for idx, target_id in enumerate(plan["target_ids"]):
                    writer.writerow([target_id, "UNVALIDATED_MODEL_CANDIDATE", "NOT_RUN",
                                     bool(arrays["continuous_analytical_pass"][idx]),
                                     bool(arrays["grid_analytical_pass"][idx]), *continuous[idx], *grid[idx]])
            save_json(folder / "metrics.json", report)
            reports[name] = {"metrics": _pin(folder / "metrics.json"),
                             "common_continuous_scaled_rmse": report["modes"]["continuous"]["common"]["normalized_rmse"],
                             "common_continuous_joint_hit_rate": report["modes"]["continuous"]["common"]["joint_hit_rate"]}
            del model, own
        eligible = {name: item["common_continuous_scaled_rmse"] for name, item in reports.items()
                    if item["common_continuous_scaled_rmse"] is not None}
        best = min(eligible, key=eligible.get) if split == "validation" and eligible else "NOT_DETERMINED"
        summary = {"schema": "bb_seven_evaluation_summary.v1", "status": "COMPLETE_PROXY_EVALUATION", **identity,
                   "split": split, "target_count": len(targets), "target_plan": _pin(target_plan_path),
                   "source_split_geometries": plan["source_split_geometries"],
                   "source_label_ineligible_geometries": plan["source_label_ineligible_geometries"],
                   "evaluation_request": _pin(out / "EVALUATION_REQUEST.json"),
                   "checkpoints": _checkpoint_shas(records), "configuration_freeze": gate,
                   "evaluation_protocol_sha256": seven_protocol_identity()["sha256"],
                   "forward_models": forward_reports, "packages": reports,
                   "forward_by_package": {name: "BB00_FORWARD" if name == "BB00" else PACKAGE_MAPPING[name][0] for name in PACKAGES},
                   "best_validation_candidate": best, "selection_criterion": SELECTION,
                   "test_used_to_select_model": False, "physical_winner": "NOT_ESTABLISHED",
                   "real_emx_validation": "NOT_RUN", "production_modified": False,
                   "supervision_difference": "BB00 trains15GHz direct4; BB01-06 train broadband spec tasks; descriptive system comparison, not equal-supervision causal ablation",
                   "historical_replay_ranked": False, "completed_utc": utc_now(), "elapsed_seconds": time.monotonic() - started}
        with (out / "comparison_15ghz.csv").open("x", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["package", "mode", "target_count", "completed_updates", "selected_step", "scaled_rmse",
                             "p95_target_scaled_rmse", "joint_hit_rate", "failed_targets", "analytical_pass", *FEATURES])
            for name in PACKAGES:
                report = read_json(out / name / "metrics.json")
                for mode, result in report["modes"].items():
                    metric = result["common"]
                    writer.writerow([name, mode, len(targets), report["completed_updates"], report["selected_step"],
                       metric["normalized_rmse"], metric["p95_per_geometry_normalized_rmse"], metric["joint_hit_rate"],
                       metric["target_failure_count"], result["analytical_pass"],
                       *(metric["physical_unit_features"][k]["mae"] for k in FEATURES)])
        with (out / "comparison_forward_15ghz.csv").open("x", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["package", "forward_name", "checkpoint_sha256", "target_count", "completed_updates", "selected_step",
                             "parameter_count", "inference_seconds", "scaled_rmse", "p95_target_scaled_rmse", "joint_hit_rate", *FEATURES])
            for package, name in summary["forward_by_package"].items():
                report = forward_reports[name]
                metric = report["metrics"]
                writer.writerow([package, name, report["checkpoint"]["sha256"], len(targets), report["completed_updates"], report["selected_step"],
                                 report["parameter_count"], report["inference_seconds"], metric["normalized_rmse"],
                                 metric["p95_per_geometry_normalized_rmse"], metric["joint_hit_rate"],
                                 *(metric["physical_unit_features"][k]["mae"] for k in FEATURES)])
        save_json(out / "EVALUATION_SUMMARY.json", summary)
        _write_index(out)
        return summary
    except Exception as exc:
        save_json(out / "EVALUATION_FAILED.json", {"status": "FAIL", "error_type": type(exc).__name__,
                  "error": str(exc), "created_utc": utc_now(), "split": split,
                  "partial_outputs_preserved": True, "real_emx_validation": "NOT_RUN"})
        raise


def evaluate_six_broadband(data_root, runs_root, forward_reference, out_dir, **kwargs):
    """Unchanged six-group extended evaluator; BB00 is not assigned a fake score."""
    return broadband.evaluate(data_root, runs_root, forward_reference, out_dir, **kwargs)


def export_broadband_comparison(evaluation_root, out_csv):
    """Copy hash-verified saved metrics, without invoking any model or metric.

    The frozen v2 evaluator did not compute own/grid. Those requested cells
    remain explicitly NOT_RECORDED, never filled with common/grid or zero.
    """
    root, target = Path(evaluation_root).resolve(), Path(out_csv).resolve()
    receipt_path = Path(str(target) + ".receipt.json")
    if target.exists() or receipt_path.exists():
        raise FileExistsError("broadband CSV/receipt already exists")
    summary = read_json(root / "EVALUATION_SUMMARY.json")
    if summary.get("status") != "COMPLETE_PROXY_EVALUATION" or set(summary.get("packages", {})) != set(PACKAGE_MAPPING):
        raise ValueError("requires a complete saved six-model broadband summary")
    rows, pins = [], {}
    names = [task.lower() + "_" + mode for task, mode in broadband.PANEL_DEFINITIONS]
    for package in PACKAGE_MAPPING:
        metric_pin = summary["packages"][package]["metrics"]
        source = root / package / "metrics.json"
        if sha256(source) != metric_pin["sha256"]:
            raise ValueError("saved broadband metrics SHA mismatch: " + package)
        pins[package] = _pin(source)
        report = read_json(source)
        if set(report["panels"]) != set(names):
            raise ValueError("saved broadband panels differ from frozen eight-panel protocol")
        for panel_name in names:
            panel = report["panels"][panel_name]
            for mode in ("continuous", "grid"):
                for proxy in ("own", "common"):
                    metric = panel.get(f"{proxy}_forward_{mode}")
                    status = ("EVALUATED_PROXY_ONLY" if metric is not None else
                              "NOT_RECORDED" if panel.get("status") == "EVALUATED_PROXY_ONLY" else panel["status"])
                    row = {"package": package, "panel": panel_name, "geometry_mode": mode, "proxy": proxy,
                           "status": status, "holdout_geometry_count": panel.get("holdout_geometry_count"),
                           "source_geometry_count": panel.get("source_geometry_count", 0),
                           "source_label_ineligible_geometry_count": panel.get("source_label_ineligible_geometry_count"),
                           "panel_manifest_sha256": panel.get("panel_manifest_sha256"),
                           "metrics_sha256": pins[package]["sha256"], "real_emx_validation": "NOT_RUN"}
                    for field in ("normalized_rmse", "normalized_mae", "p95_per_geometry_normalized_rmse",
                                  "requested_conditions", "evaluable_requested_predictions", "invalid_requested_predictions",
                                  "condition_violation_count", "condition_violation_denominator", "condition_violation_rate",
                                  "geometries_with_any_condition_violation", "geometry_any_violation_rate"):
                        row[field] = None if metric is None else metric.get(field)
                    row["actual_request_support_json"] = json.dumps(panel.get("actual_request_support"), sort_keys=True)
                    rows.append(row)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    receipt = {"schema": "bb_broadband_saved_metric_export.v1", "status": "PASS", "created_utc": utc_now(),
               "source_summary": _pin(root / "EVALUATION_SUMMARY.json"), "source_metrics": pins,
               "output": _pin(target), "rows": len(rows), "bb00_broadband": "NOT_SUPPORTED",
               "own_grid": "NOT_RECORDED by frozen v2 evaluator; empty numeric cells, not zero",
               "new_model_calls": 0, "new_metrics_computed": False, "real_emx_validation": "NOT_RUN"}
    save_json(receipt_path, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    targets = sub.add_parser("freeze-targets")
    targets.add_argument("--data", required=True)
    targets.add_argument("--out", required=True)
    targets.add_argument("--split", choices=("validation", "test"), required=True)
    targets.add_argument("--tolerance-fraction", type=float, default=0.05)
    freeze = sub.add_parser("freeze-configuration")
    for key in ("data", "registry", "validation-summary", "target-plan", "out"):
        freeze.add_argument("--" + key, required=True)
    run = sub.add_parser("evaluate-seven")
    for key in ("data", "registry", "target-plan", "out"):
        run.add_argument("--" + key, required=True)
    run.add_argument("--split", choices=("validation", "test"), default="validation")
    run.add_argument("--configuration-freeze")
    run.add_argument("--device", choices=("cpu", "mps"), default="mps")
    run.add_argument("--micro-batch", type=int, default=8)
    run.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    if args.command == "freeze-targets":
        return freeze_common_15ghz_targets(args.data, args.out, split=args.split, tolerance_fraction=args.tolerance_fraction)
    if args.command == "freeze-configuration":
        return freeze_seven_configuration(args.registry, args.data, args.validation_summary, args.target_plan, args.out)
    return evaluate_seven(args.data, args.registry, args.target_plan, args.out, split=args.split,
                         configuration_freeze=args.configuration_freeze, device=args.device,
                         micro_batch=args.micro_batch, threads=args.threads)


if __name__ == "__main__":
    main()
