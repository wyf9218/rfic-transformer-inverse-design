"""Fixed-panel, all-holdout proxy evaluation and private candidate export.

No optimizer, simulator, production controller or model selection on sealed test
is invoked. All emitted geometry is an unvalidated candidate, not an EMX result.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import resource
import sys
import time
from typing import Any

import numpy as np
import torch

from .io import canonical_sha, load_checkpoint, manifest_tree, read_json, save_json, sha256, utc_now
from .metrics import forward_metrics, inverse_metrics
from .models import PACKAGE_MAPPING, build_forward, build_inverse
from .physics import extract_physical, geometry_feasibility
from .specs import make_spec, tokenize
from .training import Bundle, TrainConfig, configure_device, decoder_from_contract, model_digest


PANEL_SEED = 170029
FORWARD_IDS = ("F1", "F2", "F3", "FREF")
PACKAGE_IDS = tuple(PACKAGE_MAPPING)
PANEL_DEFINITIONS = tuple((task, mode) for task in ("SPECTRUM", "PHYSICAL")
                          for mode in ("full", "band", "multi", "single"))


def evaluation_protocol_identity():
    """Freeze methodology/code as well as selected weights before sealed test."""
    source = Path(__file__).parent
    protocol = {"schema": "bb_evaluation_protocol.v1", "panel_seed": PANEL_SEED,
                "panel_definitions": [list(item) for item in PANEL_DEFINITIONS],
                "geometry_population": "ALL declared split geometries",
                "physical_domain": "source strict_lumped_valid; invalid requested predictions remain failures",
                "tolerance_fraction_of_train_channel_scale": 0.05,
                "grid_policy": "source-verified contract grid; float64 nearest ties-to-even; no repair",
                "physical_arithmetic": "CPU complex128, float64 returned metrics",
                "sources": {name: sha256(source / name) for name in
                            ("evaluation.py", "metrics.py", "physics.py", "specs.py", "models.py")}}
    return {"sha256": canonical_sha(protocol), "protocol": protocol}


def _grid_from_contract(contract):
    value = contract.get("grid_um")
    source = contract.get("grid_source_sha256", "")
    if (not isinstance(value, (int, float)) or isinstance(value, bool) or
            not np.isfinite(value) or value <= 0 or not isinstance(source, str) or len(source) != 64 or
            any(character not in "0123456789abcdef" for character in source) or
            contract.get("grid_status") != "SOURCE_VERIFIED_EXPORT_ONLY_NO_STRAIGHT_THROUGH_ESTIMATOR"):
        raise ValueError("grid export requires the positive, source-SHA-verified manufacturing grid contract")
    return float(value)


def _grid_geometry(geometry, grid_um):
    """Export grid rounding is float64, never a straight-through training step."""
    values = np.asarray(geometry, dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("cannot export nonfinite model geometry")
    return np.rint(values / grid_um) * grid_um


def _memory_observation(device):
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {"process_lifetime_peak_rss_bytes": int(peak if sys.platform == "darwin" else peak * 1024),
              "scope": "process-lifetime RSS high-water includes dataset and other loaded models; MPS values are point samples, not isolated-model peaks"}
    if str(device).startswith("mps"):
        torch.mps.synchronize()
        report.update(mps_current_allocated_bytes=int(torch.mps.current_allocated_memory()),
                      mps_driver_allocated_bytes=int(torch.mps.driver_allocated_memory()))
    return report


def _pin(path: str | Path) -> dict[str, Any]:
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def discover_runs(runs_root: str | Path, forward_reference: str | Path) -> dict[str, dict[str, Any]]:
    """Read exactly the declared ten terminal receipts; do not guess latest runs."""
    root = Path(runs_root).resolve()
    records = {}
    for name in (*FORWARD_IDS, *PACKAGE_IDS):
        folder = root / "shared_forward" / name if name in FORWARD_IDS else root / name
        receipt_path = folder / "TRAINING_RECEIPT.json"
        receipt = read_json(receipt_path)
        if receipt.get("status") not in {"PRETRAINED", "PRETRAINED_PARTIAL"}:
            raise ValueError(f"{name}: no qualifying real training terminal receipt")
        if int(receipt.get("updates_this_run", 0)) <= 0 or not receipt.get("trainable_weights_changed"):
            raise ValueError(f"{name}: receipt does not prove positive training updates")
        checkpoint = Path(receipt["best_checkpoint"])
        if not checkpoint.is_absolute():
            checkpoint = folder / checkpoint
        checkpoint = checkpoint.resolve()
        pin = _pin(checkpoint)
        if pin["sha256"] != receipt["best_sha256"]:
            raise ValueError(f"{name}: selected checkpoint differs from training receipt SHA")
        if name == "FREF" and checkpoint != Path(forward_reference).resolve():
            raise ValueError("--forward-reference must be the FREF receipt-selected best checkpoint")
        records[name] = {"name": name, "checkpoint": pin, "receipt": _pin(receipt_path),
                         "training_status": receipt["status"], "training_updates": receipt["completed_step"],
                         "data_sha": receipt["data_sha"], "training_elapsed_seconds": receipt["elapsed_seconds"],
                         "parameter_counts": receipt.get("parameter_counts")}
        reference_file = folder / "forward_reference.json"
        if name in PACKAGE_IDS and reference_file.is_file():
            records[name]["forward_reference"] = read_json(reference_file)
            records[name]["forward_reference_evidence"] = _pin(reference_file)
    return records


def verify_configuration_freeze(path, records, bundle, split):
    """Require all ten exact selected checkpoint SHAs before sealed-test scoring."""
    if split not in {"validation", "test"}:
        raise ValueError("split must be validation or test")
    if path is None:
        if split == "test":
            raise ValueError("sealed test requires an exact configuration-freeze file")
        return {"status": "VALIDATION_ONLY_NO_TEST_RELEASE", "test_access": False}
    freeze = read_json(path)
    expected = {name: record["checkpoint"]["sha256"] for name, record in records.items()}
    if freeze.get("schema") != "bb_evaluation_configuration_freeze.v1" or freeze.get("status") != "FROZEN":
        raise ValueError("configuration freeze schema/status mismatch")
    if freeze.get("checkpoints") != expected:
        raise ValueError("configuration freeze must bind exactly the ten selected checkpoint SHAs")
    if freeze.get("data_sha") != bundle.data_sha or freeze.get("normalizer_sha") != bundle.norm_sha:
        raise ValueError("configuration freeze snapshot/normalizer mismatch")
    if split == "test" and freeze.get("evaluation_protocol_sha256") != evaluation_protocol_identity()["sha256"]:
        raise ValueError("sealed test requires the exact current evaluation protocol/source fingerprint")
    return {"status": "EXACT_CONFIGURATION_FROZEN", "file": _pin(path), "test_access": split == "test"}


def build_panels(bundle: Bundle, split: str) -> tuple[np.ndarray, dict[str, dict[str, Any]]]:
    """Create one identical target panel per mode from every eligible holdout row."""
    split_value = {"validation": 1, "test": 2}[split]
    indices = np.flatnonzero(bundle.arrays["split"] == split_value)
    if not len(indices):
        raise ValueError(f"empty {split} split")
    panels = {}
    for task, mode in PANEL_DEFINITIONS:
        eligible = indices
        if task == "PHYSICAL":
            eligible = indices[bundle.arrays["y_valid"][indices].any(axis=(1, 2))]
        name = f"{task.lower()}_{mode}"
        if not len(eligible):
            panels[name] = {"name": name, "task": task, "mode": mode, "indices": eligible,
                            "status": "NOT_EVALUABLE_NO_VALID_SOURCE_PHYSICAL_TARGET", "spec": None}
            continue
        batch = bundle.batch(eligible, torch.device("cpu"))
        spec = make_spec(batch["s"], batch["y"], batch["y_valid"], bundle.frequency,
                         np.random.default_rng(PANEL_SEED), task=task, mode=mode)
        panels[name] = {"name": name, "task": task, "mode": mode, "indices": eligible,
                        "status": "FROZEN", "spec": spec}
    return indices, panels


def _save_npz(path: Path, **arrays):
    with path.open("xb") as handle:
        np.savez_compressed(handle, **arrays)
    return _pin(path)


def _panel_support(panel):
    if panel["spec"] is None:
        return {"actual_frequency_counts": [], "rows_requesting_all_56_frequencies": 0}
    spec = panel["spec"]
    mask = (spec["s_mask"].any(-1) | spec["y_mask"].any(-1)).numpy()
    counts = mask.sum(-1)
    contiguous = [bool(len(ix) <= 1 or np.all(np.diff(ix) == 1)) for ix in (np.flatnonzero(row) for row in mask)]
    return {"actual_frequency_counts": counts.tolist(),
            "rows_requesting_all_56_frequencies": int((counts == 56).sum()),
            "rows_requesting_one_frequency": int((counts == 1).sum()),
            "rows_with_contiguous_requested_frequencies": int(sum(contiguous)),
            "requested_condition_counts": (spec["s_mask"].sum((1, 2)) + spec["y_mask"].sum((1, 2))).tolist(),
            "requested_mode": panel["mode"],
            "band_reduced_to_single_rows": int((counts == 1).sum()) if panel["mode"] == "band" else 0,
            "physical_full_semantics": "all source-strict-valid conditions; NOT a full-56 guarantee" if panel["task"] == "PHYSICAL" else None,
            "band_semantics": "reported actual source-valid frequencies in sampled interval; single-point fallbacks are counted, not called a successful broadband design"}


def freeze_panels(out: Path, bundle: Bundle, indices: np.ndarray, panels, split):
    root = out / "fixed_specs"
    root.mkdir()
    definitions = []
    for name, panel in panels.items():
        ix = panel["indices"]
        entry = {"name": name, "task": panel["task"], "mode": panel["mode"], "status": panel["status"],
                 "source_geometry_count": int(len(ix)), "holdout_geometry_count": int(len(indices)),
                 "excluded_no_valid_source_physical_target": int(len(indices) - len(ix)),
                 "source_indices": ix.tolist(), "geometry_ids": bundle.arrays["geometry_ids"][ix].tolist(),
                 "geometry_sha256": bundle.arrays["geometry_sha256"][ix].tolist(),
                 "actual_request_support": _panel_support(panel)}
        if panel["spec"] is not None:
            spec = panel["spec"]
            arrays = {key: spec[key].numpy() for key in ("s_mask", "y_mask", "y_relation", "s_tolerance", "y_tolerance")}
            entry["mask_artifact"] = _save_npz(root / f"{name}.npz", source_indices=ix, **arrays)
            entry["requested_s_conditions"] = int(arrays["s_mask"].sum())
            entry["requested_physical_conditions"] = int(arrays["y_mask"].sum())
            entry["sampled_tasks"] = spec["description"]
        definitions.append(entry)
    document = {"schema": "bb_fixed_evaluation_specs.v1", "seed": PANEL_SEED, "split": split,
                "data_sha": bundle.data_sha, "data_manifest_sha": bundle.manifest_sha,
                "normalizer_sha": bundle.norm_sha, "holdout_geometries": int(len(indices)),
                "frequency_hz": bundle.arrays["frequency_hz"].tolist(),
                "selection": "ALL split geometries; PHYSICAL excludes only source rows with no strict-valid requested label",
                "task_sampling": "same seed reset per declared task/mode; immutable source geometry order; independent of model",
                "target_source": "same source geometry real response; no cross-geometry target splicing",
                "target_fields": {"SPECTRUM": "32 full S real/imaginary channels", "PHYSICAL": ["Lp_nH", "Ls_nH", "Qmin", "K_abs"]},
                "tolerance": "0.05 times fixed channel scale in metrics; not target-relative percent",
                "physical_domain": "strict_lumped_valid", "real_emx_validation": "NOT_RUN", "panels": definitions}
    document["evaluation_protocol"] = evaluation_protocol_identity()
    save_json(root / "PANEL_MANIFEST.json", document)
    return _pin(root / "PANEL_MANIFEST.json")


def _load_model(record, bundle, device, expected_role, expected_kind=None):
    path = record["checkpoint"]["path"]
    if sha256(path) != record["checkpoint"]["sha256"]:
        raise ValueError("checkpoint changed after discovery")
    state = load_checkpoint(path)
    if state["role"] != expected_role or state["data_sha"] != bundle.data_sha or state["normalizer_sha"] != bundle.norm_sha:
        raise ValueError("checkpoint role/snapshot/normalizer mismatch")
    if expected_kind is not None and state["kind"] != expected_kind:
        raise ValueError("checkpoint architecture differs from fixed package mapping")
    if state["geometry_dim"] != bundle.dim or canonical_sha(state["contract"]) != state["contract_sha"]:
        raise ValueError("checkpoint geometry/contract identity mismatch")
    factory = build_forward if expected_role == "forward" else build_inverse
    model = factory(state["kind"], bundle.dim).to(device).eval().requires_grad_(False)
    model.load_state_dict(state["model_state"])
    if model_digest(model) != state["model_sha"]:
        raise ValueError("loaded weight tensor digest differs from checkpoint identity")
    metadata = {key: state[key] for key in ("role", "kind", "step", "model_sha", "contract", "contract_sha", "architecture")}
    metadata["forward_checkpoint"] = state.get("forward_checkpoint")
    metadata["forward_model_sha"] = state.get("forward_model_sha")
    metadata["forward_checkpoint_sha256"] = state.get("forward_checkpoint_sha256")
    metadata["parameter_count"] = sum(parameter.numel() for parameter in model.parameters())
    metadata["seed"] = state.get("train_config", {}).get("seed")
    return model, metadata


def _verify_own_forward_binding(metadata, package_record, own_record, own_metadata, common_metadata):
    if metadata["forward_model_sha"] != own_metadata["model_sha"]:
        raise ValueError("inverse own-forward weights differ from the selected shared checkpoint")
    if metadata["forward_model_sha"] == common_metadata["model_sha"]:
        raise ValueError("FREF was used by an inverse gradient path and is not independent")
    expected = own_record["checkpoint"]["sha256"]
    bound_sha = metadata.get("forward_checkpoint_sha256")
    reference = package_record.get("forward_reference")
    if reference is not None:
        pin = package_record["forward_reference_evidence"]
        if sha256(pin["path"]) != pin["sha256"]:
            raise ValueError("portable forward-reference evidence changed")
        if reference.get("sha256") != expected or reference.get("model_sha") != metadata["forward_model_sha"]:
            raise ValueError("portable forward-reference identity differs from frozen model/selected checkpoint")
        if bound_sha is not None and bound_sha != reference["sha256"]:
            raise ValueError("checkpoint and portable forward-reference bindings disagree")
        bound_sha = reference["sha256"]
    original = metadata.get("forward_checkpoint")
    if original and Path(original).is_file():
        actual = sha256(original)
        if bound_sha is not None and bound_sha != actual:
            raise ValueError("recorded original and portable forward checkpoint bytes disagree")
        bound_sha = actual
    if bound_sha != expected:
        raise ValueError("exact frozen forward checkpoint bytes are unproven; an old path alone is not a portable identity")


def _raw_forward(model, geometry, bundle):
    return bundle.denormalize_s(model(bundle.g_normalize(geometry), bundle.frequency))


def _physical_arrays(raw_s, bundle, contract, micro_batch=8, device="cpu"):
    values, validity = [], []
    exact_frequency = torch.as_tensor(bundle.arrays["frequency_hz"], dtype=torch.float64)
    with torch.no_grad():
        for start in range(0, len(raw_s), micro_batch):
            # Official reporting retains complex128 extraction precision rather
            # than routing derived physical values back through MPS float32.
            prediction = extract_physical(torch.as_tensor(raw_s[start:start + micro_batch], dtype=torch.float64, device="cpu"),
                                          exact_frequency, contract["port_contract"])
            values.append(prediction["y"].cpu().numpy())
            validity.append(prediction["valid_strict"].cpu().numpy())
    return np.concatenate(values), np.concatenate(validity)


def _physical_forward_metrics(raw_s, bundle, indices, contract, micro_batch, device):
    predicted, valid = _physical_arrays(raw_s, bundle, contract, micro_batch, device)
    targets, mask = bundle.arrays["y"][indices], bundle.arrays["y_valid"][indices]
    result = {"source_domain": "strict_lumped_valid", "prediction_domain": "strict_lumped_valid",
              "geometry_count": int(len(indices)), "features": {}}
    for channel, name in enumerate(("Lp_nH", "Ls_nH", "Qmin", "K_abs")):
        requested = mask[:, :, channel:channel + 1]
        rows = requested.any(axis=(1, 2))
        count = int(requested.sum())
        if not count:
            result["features"][name] = {"status": "NO_VALID_SOURCE_LABELS", "requested_conditions": 0}
            continue
        metric = inverse_metrics(predicted[rows, :, channel:channel + 1], targets[rows, :, channel:channel + 1],
                                 requested[rows], np.asarray(bundle.norm["y_scale"])[channel:channel + 1],
                                 prediction_valid=valid[rows, :, channel:channel + 1])
        scale = float(bundle.norm["y_scale"][channel])
        metric["raw_mae"] = None if metric["normalized_mae"] is None else metric["normalized_mae"] * scale
        metric["raw_rmse"] = None if metric["normalized_rmse"] is None else metric["normalized_rmse"] * scale
        conditional = metric["valid_requested_numeric"]
        conditional["raw_mae"] = None if conditional["normalized_mae"] is None else conditional["normalized_mae"] * scale
        conditional["raw_rmse"] = None if conditional["normalized_rmse"] is None else conditional["normalized_rmse"] * scale
        result["features"][name] = metric
    return result


def _slice_spec(spec, start, end, device):
    return {key: (value[start:end].to(device) if isinstance(value, torch.Tensor) and key != "frequency_hz"
                  else value.to(device) if key == "frequency_hz" else value[start:end])
            for key, value in spec.items()}


def _feasibility_flags(geometry, decoder):
    """Check the actual exported float64 grid coordinates, including on MPS."""
    with torch.no_grad():
        return geometry_feasibility(torch.as_tensor(geometry, dtype=torch.float64, device="cpu"),
                                    decoder.field_names, decoder.lower.cpu().double(),
                                    decoder.upper.cpu().double(), decoder.topology_contract)


def _feasibility_summary(geometry: np.ndarray, decoder, bundle, device):
    with torch.no_grad():
        checks = _feasibility_flags(geometry, decoder)
    finite = np.isfinite(geometry).all(axis=1)
    outside = ~finite | (geometry < np.asarray(bundle.norm["g_min"])).any(axis=1) | (geometry > np.asarray(bundle.norm["g_max"])).any(axis=1)
    return {"geometries": int(len(geometry)), "analytical_pass": int(checks["analytical_pass"].sum().cpu()),
            "envelope_pass": int(checks["envelope_pass"].sum().cpu()), "nonfinite_geometries": int((~finite).sum()),
            "outside_train_coordinate_envelope": int(outside.sum()),
            "support_check_scope": "coordinate envelope only, not density or nearest-neighbor/OOD proof",
            "geometry_check_dtype": "float64",
            "power_line_topology_checked": checks["power_line_topology_checked"],
            "manufacturability": "NOT_PROVEN", "calibre": "NOT_RUN", "real_emx_validation": "NOT_RUN"}


def _panel_response_metrics(raw_s, panel, bundle, contract, micro_batch, device):
    spec = panel["spec"]
    if panel["task"] == "SPECTRUM":
        return inverse_metrics(raw_s, spec["s_target"].numpy(), spec["s_mask"].numpy(), np.asarray(bundle.norm["s_scale"]))
    values, valid = _physical_arrays(raw_s, bundle, contract, micro_batch, device)
    return inverse_metrics(values, spec["y_target"].numpy(), spec["y_mask"].numpy(), np.asarray(bundle.norm["y_scale"]),
                           prediction_valid=valid, relation=spec["y_relation"].numpy())


def _candidate_rows(writer, package, panel, bundle, continuous, grid, grid_checks):
    for position, source_index in enumerate(panel["indices"]):
        writer.writerow([package, panel["name"], position, str(bundle.arrays["geometry_ids"][source_index]),
                         "UNVALIDATED_MODEL_CANDIDATE", "NOT_RUN", *continuous[position].tolist(),
                         *grid[position].tolist(), bool(grid_checks[position])])


def evaluate(data_root, runs_root, forward_reference, out_dir, *, split="validation",
             configuration_freeze=None, device="mps", micro_batch=8, threads=2):
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        if micro_batch <= 0 or threads <= 0:
            raise ValueError("micro_batch and threads must be positive")
        records = discover_runs(runs_root, forward_reference)
        bundle = Bundle(data_root)
        if any(record["data_sha"] != bundle.data_sha for record in records.values()):
            raise ValueError("training receipts do not share the requested snapshot")
        freeze = verify_configuration_freeze(configuration_freeze, records, bundle, split)
        torch_device = configure_device(TrainConfig(role="forward", kind="F1", device=device, threads=threads))
        indices, panels = build_panels(bundle, split)
        panel_identity = freeze_panels(out, bundle, indices, panels, split)
        evaluation_request = {"created_utc": utc_now(), "split": split,
                  "data_manifest": _pin(bundle.root / "data_manifest.json"), "data_sha": bundle.data_sha,
                  "normalizer_sha": bundle.norm_sha, "source_runs": records, "configuration_freeze": freeze,
                  "panel_manifest": panel_identity, "micro_batch": micro_batch, "device": device,
                  "threads": threads, "evaluation_protocol": evaluation_protocol_identity(),
                  "holdout_scope": "ALL split geometries, no first-128 subsampling", "production_modified": False}
        forwards, forward_meta, forward_reports = {}, {}, {}
        common_contract_sha = None
        grid_um = None
        for name in FORWARD_IDS:
            model, metadata = _load_model(records[name], bundle, torch_device, "forward", "F1" if name == "FREF" else name)
            common_contract_sha = common_contract_sha or metadata["contract_sha"]
            if metadata["contract_sha"] != common_contract_sha:
                raise ValueError("shared forward geometry/port contracts differ")
            current_grid = _grid_from_contract(metadata["contract"])
            if grid_um is None:
                grid_um = current_grid
                evaluation_request.update(geometry_grid_um=grid_um,
                                          geometry_grid_source_sha256=metadata["contract"]["grid_source_sha256"])
                save_json(out / "EVALUATION_REQUEST.json", evaluation_request)
            elif current_grid != grid_um:
                raise ValueError("shared forward manufacturing-grid contracts differ")
            forwards[name], forward_meta[name] = model, metadata
            predicted = []
            inference_started = time.monotonic()
            with torch.no_grad():
                for start in range(0, len(indices), micro_batch):
                    batch = bundle.batch(indices[start:start + micro_batch], torch_device)
                    predicted.append(_raw_forward(model, batch["geometry"], bundle).cpu().numpy())
            predicted = np.concatenate(predicted)
            inference_elapsed = time.monotonic() - inference_started
            metrics = forward_metrics(predicted, bundle.arrays["s"][indices], np.asarray(bundle.norm["s_scale"]), bundle.arrays["frequency_hz"])
            metrics["physical"] = _physical_forward_metrics(predicted, bundle, indices, metadata["contract"], micro_batch, torch_device)
            metrics["checkpoint"] = records[name]["checkpoint"]
            metrics["selected_step"] = metadata["step"]
            metrics["architecture"] = metadata["architecture"]
            metrics["parameter_count"] = metadata.get("parameter_count")
            metrics["seed"] = metadata.get("seed")
            metrics["training_elapsed_seconds"] = records[name].get("training_elapsed_seconds")
            metrics["inference_elapsed_seconds"] = inference_elapsed
            metrics["inference_time_scope"] = "all selected holdout geometries, full frequency grid, model call plus input/host transfers; excludes physical extraction"
            metrics["physical_arithmetic"] = "CPU complex128; float64 physical descriptors"
            metrics["memory_observation"] = _memory_observation(torch_device)
            folder = out / "shared_forward" / name
            folder.mkdir(parents=True)
            metrics["prediction_artifact"] = _save_npz(folder / "predictions.npz", source_indices=indices, predicted_s=predicted)
            save_json(folder / "metrics.json", metrics)
            forward_reports[name] = {"metrics": _pin(folder / "metrics.json"), "shared_scale_nrmse": metrics["shared_scale_nrmse"]}
            print(json.dumps({"event": "forward_evaluation_complete", "name": name, "geometries": len(indices)}), flush=True)
        package_reports = {}
        for package, (own_name, inverse_kind) in PACKAGE_MAPPING.items():
            model, metadata = _load_model(records[package], bundle, torch_device, "inverse", inverse_kind)
            if metadata["contract_sha"] != common_contract_sha:
                raise ValueError(f"{package}: inverse contract differs from forward contract")
            _verify_own_forward_binding(metadata, records[package], records[own_name],
                                        forward_meta[own_name], forward_meta["FREF"])
            decoder = decoder_from_contract(metadata["contract"], torch_device)
            folder = out / package
            folder.mkdir()
            report = {"package": package, "selected_checkpoint": records[package]["checkpoint"],
                      "selected_step": metadata["step"], "architecture": metadata["architecture"],
                      "own_forward": records[own_name]["checkpoint"], "common_forward": records["FREF"]["checkpoint"],
                      "panel_manifest": panel_identity, "evaluation_protocol": evaluation_protocol_identity(),
                      "parameter_count": metadata.get("parameter_count"),
                      "training_elapsed_seconds": records[package].get("training_elapsed_seconds"),
                      "common_forward_not_used_for_inverse_gradient": True, "panels": {},
                      "real_emx_validation": "NOT_RUN", "physical_winner": "NOT_ESTABLISHED"}
            fields = metadata["contract"]["field_names"]
            with (folder / "candidates.csv").open("x", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["package", "panel", "panel_row", "target_source_geometry_id", "candidate_status", "real_emx_validation",
                                 *["continuous__" + field for field in fields], *["grid__" + field for field in fields], "grid_analytical_pass"])
                for name, panel in panels.items():
                    if panel["spec"] is None:
                        report["panels"][name] = {"status": panel["status"], "source_geometry_count": 0}
                        continue
                    continuous, grid, own_s, common_s, common_grid_s = [], [], [], [], []
                    inference_started = time.monotonic()
                    with torch.no_grad():
                        for start in range(0, len(panel["indices"]), micro_batch):
                            spec = _slice_spec(panel["spec"], start, start + micro_batch, torch_device)
                            tokens, condition = tokenize(spec, bundle.norm)
                            geometry = decoder(model(tokens, condition))
                            continuous_values = geometry.cpu().numpy()
                            grid_values = _grid_geometry(continuous_values, grid_um)
                            grid_geometry = torch.as_tensor(grid_values, dtype=geometry.dtype, device=torch_device)
                            continuous.append(continuous_values)
                            grid.append(grid_values)
                            own_s.append(_raw_forward(forwards[own_name], geometry, bundle).cpu().numpy())
                            common_s.append(_raw_forward(forwards["FREF"], geometry, bundle).cpu().numpy())
                            common_grid_s.append(_raw_forward(forwards["FREF"], grid_geometry, bundle).cpu().numpy())
                    geometry, gridded, own, common, grid_response = [np.concatenate(values) for values in (continuous, grid, own_s, common_s, common_grid_s)]
                    inference_elapsed = time.monotonic() - inference_started
                    artifact = _save_npz(folder / f"{name}_predictions.npz", source_indices=panel["indices"],
                                         continuous_geometry_um=geometry, grid_geometry_um=gridded,
                                         own_forward_continuous_s=own, common_forward_continuous_s=common,
                                         common_forward_grid_s=grid_response)
                    own_metrics = _panel_response_metrics(own, panel, bundle, metadata["contract"], micro_batch, torch_device)
                    common_metrics = _panel_response_metrics(common, panel, bundle, metadata["contract"], micro_batch, torch_device)
                    grid_metrics = _panel_response_metrics(grid_response, panel, bundle, metadata["contract"], micro_batch, torch_device)
                    panel_result = {"status": "EVALUATED_PROXY_ONLY", "source_geometry_count": int(len(panel["indices"])),
                                    "holdout_geometry_count": int(len(indices)),
                                    "source_label_ineligible_geometry_count": int(len(indices) - len(panel["indices"])),
                                    "actual_request_support": _panel_support(panel),
                                    "panel_manifest_sha256": panel_identity["sha256"],
                                    "prediction_artifact": artifact, "own_forward_continuous": own_metrics,
                                    "common_forward_continuous": common_metrics, "common_forward_grid": grid_metrics,
                                    "continuous_feasibility": _feasibility_summary(geometry, decoder, bundle, torch_device),
                                    "grid_feasibility": _feasibility_summary(gridded, decoder, bundle, torch_device),
                                    "grid_um": grid_um, "grid_source_sha256": metadata["contract"]["grid_source_sha256"],
                                    "grid_rounding": "float64 nearest with ties to even; no topology repair",
                                    "grid_forward_query_dtype": "float32 network query; export and analytical checks retain float64 grid coordinates",
                                    "inference_elapsed_seconds": inference_elapsed,
                                    "inference_time_scope": "inverse/decoder plus own/shared/grid-forward queries and host transfers; excludes physical metrics",
                                    "memory_observation": _memory_observation(torch_device),
                                    "grid_max_absolute_coordinate_change_um": float(np.max(np.abs(gridded - geometry))),
                                    "own_common_full_s_disagreement_shared_scale_rms": float(np.sqrt(np.mean(((own - common) / np.asarray(bundle.norm["s_scale"])) ** 2))),
                                    "real_emx_validation": "NOT_RUN"}
                    report["panels"][name] = panel_result
                    with torch.no_grad():
                        grid_checks = _feasibility_flags(gridded, decoder)["analytical_pass"].cpu().numpy()
                    _candidate_rows(writer, package, panel, bundle, geometry, gridded, grid_checks)
                    print(json.dumps({"event": "inverse_panel_complete", "package": package, "panel": name,
                                      "geometries": len(panel["indices"])}), flush=True)
            report["candidate_export"] = _pin(folder / "candidates.csv")
            save_json(folder / "metrics.json", report)
            package_reports[package] = {"metrics": _pin(folder / "metrics.json"),
                                        "full_s_common_nrmse": report["panels"]["spectrum_full"]["common_forward_continuous"]["normalized_rmse"],
                                        "full_s_common_violation_rate": report["panels"]["spectrum_full"]["common_forward_continuous"]["condition_violation_rate"]}
            del model
            if device == "mps":
                torch.mps.empty_cache()
        eligible = {name: values["full_s_common_nrmse"] for name, values in package_reports.items()
                    if values["full_s_common_nrmse"] is not None}
        best = min(eligible, key=eligible.get) if eligible and split == "validation" else "NOT_DETERMINED"
        summary = {"schema": "bb_evaluation_summary.v1", "status": "COMPLETE_PROXY_EVALUATION",
                   "split": split, "geometry_count": int(len(indices)), "data_sha": bundle.data_sha,
                   "normalizer_sha": bundle.norm_sha, "configuration_freeze": freeze,
                   "evaluation_protocol": evaluation_protocol_identity(),
                   "panel_manifest": panel_identity, "forward_models": forward_reports, "packages": package_reports,
                   "best_validation_candidate": best, "selection_scope": "lowest common FREF full-S continuous-geometry NRMSE only; not a multiobjective or physical winner",
                   "test_used_to_select_model": False, "physical_winner": "NOT_ESTABLISHED",
                   "real_emx_validation": "NOT_RUN", "production_modified": False,
                   "elapsed_seconds": time.monotonic() - started, "completed_utc": utc_now()}
        save_json(out / "EVALUATION_SUMMARY.json", summary)
        manifest = manifest_tree(out)
        save_json(out / "ARTIFACT_MANIFEST.json", {"schema": "bb_evaluation_artifacts.v1", "files": manifest})
        with (out / "SHA256SUMS.txt").open("x", encoding="utf-8") as handle:
            for pin in manifest_tree(out, exclude=("SHA256SUMS.txt",)):
                handle.write(f"{pin['sha256']}  {pin['path']}\n")
        return summary
    except Exception as exc:
        save_json(out / "EVALUATION_FAILED.json", {"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc),
                  "split": split, "elapsed_seconds": time.monotonic() - started,
                  "created_utc": utc_now(), "partial_outputs_preserved": True, "real_emx_validation": "NOT_RUN"})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--runs-root", required=True)
    parser.add_argument("--forward-reference", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--configuration-freeze")
    parser.add_argument("--device", choices=("cpu", "mps"), default="mps")
    parser.add_argument("--micro-batch", type=int, default=8)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    summary = evaluate(args.data, args.runs_root, args.forward_reference, args.out, split=args.split,
                       configuration_freeze=args.configuration_freeze, device=args.device,
                       micro_batch=args.micro_batch, threads=args.threads)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
