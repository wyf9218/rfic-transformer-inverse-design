"""Versioned model loading for the Q-sweep GUI; private artifacts stay external.

A replacement package supplies its own public-contract JSON, summary and NPZ.
Both networks, normalization and decoder travel together. Unsupported activations,
decoders, feature definitions or frequency contracts are rejected explicitly.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np

from .frozen_mlp import FrozenTandemMLP, MLPBatchPrediction


MODEL_PROFILES = ("deployed100k", "real10k", "custom")
_PROFILE_CONTRACTS = {
    "deployed100k": "deployed100k_model_contract.json",
    "real10k": "real10k_model_contract.json",
}
_INPUT_UNITS = ["nH", "nH", "dimensionless", "dimensionless"]


def load_model(
    model_dir: str | Path,
    *,
    profile: str = "deployed100k",
    contract_path: str | Path | None = None,
) -> FrozenTandemMLP:
    """Load one complete model before job creation; no training or inference.

    An explicit contract selects a replacement package. ``profile='custom'`` without
    an explicit path reads ``model_contract.json`` inside the supplied model directory.
    A GUI can replace its default model only after this complete-package validation
    returns. Existing jobs retain their original model object and identity. Every
    inference also validates against that model's own support, including when a
    replacement supports a narrower domain than the form's initial field limits.
    """
    if profile not in MODEL_PROFILES:
        raise ValueError(f"unknown model profile: {profile}")
    root = Path(model_dir).expanduser().resolve()
    if contract_path is not None:
        selected = Path(contract_path).expanduser().resolve()
    elif profile == "custom":
        selected = root / "model_contract.json"
    else:
        selected = Path(__file__).with_name(_PROFILE_CONTRACTS[profile])
    model = FrozenTandemMLP.load(root, contract_path=selected)
    selected_profile = next((
        name for name, filename in _PROFILE_CONTRACTS.items()
        if model.contract == json.loads(Path(__file__).with_name(filename).read_text())
    ), "custom")
    _validate_application_contract(model, custom=(selected_profile == "custom"))
    model.adapter_profile = selected_profile
    return model


def _validate_application_contract(model: FrozenTandemMLP, *, custom: bool) -> None:
    contract = model.contract
    summary = model.summary
    arguments = summary.get("arguments") or {}
    if model.target_frequency_ghz != 15.0:
        raise ValueError("Q-sweep requires a 15 GHz model; frequency adaptation is required")
    if not model.model_id:
        raise ValueError("model contract is missing model_id")
    if model.support_lower[2] > 10.0 or model.support_upper[2] < 20.0:
        raise ValueError("model support must cover the fixed Q=10..20 sweep")
    if custom:
        required = ("input_units", "geometry_unit", "q_target_semantics", "row_counts")
        if any(name not in contract for name in required):
            raise ValueError("custom model package must declare units, Q semantics and row_counts")
        architecture = contract.get("architecture") or {}
        if not architecture.get("hidden_activation") or not architecture.get("geometry_projection"):
            raise ValueError("custom model package must declare activation and geometry projection")
    if contract.get("input_units", _INPUT_UNITS) != _INPUT_UNITS:
        raise ValueError("model input units require an explicit adapter")
    if contract.get("geometry_unit", "um") != "um":
        raise ValueError("model geometry units require an explicit adapter")
    semantics = contract.get("q_target_semantics", arguments.get("q_target_semantics", "exact"))
    if semantics not in {"exact", "minimum"}:
        raise ValueError("unsupported model Q target semantics")
    if arguments.get("q_target_semantics", semantics) != semantics:
        raise ValueError("model contract Q semantics differ from the checkpoint summary")
    summary_projection = arguments.get("inverse_geometry_projection")
    if summary_projection and summary_projection != model.arrays["projection_mode"]:
        raise ValueError("checkpoint summary geometry projection differs from the weights")
    if "q_training_margin_physical" in contract and (
        float(contract["q_training_margin_physical"])
        != float(arguments.get("q_minimum_margin_physical", 0.0))
    ):
        raise ValueError("model contract Q training margin differs from the checkpoint summary")
    if "seed" in arguments and int(arguments["seed"]) != model.model_seed:
        raise ValueError("model contract seed differs from checkpoint summary")
    for field, declared in (("physical_cell_lower", model.support_lower), ("physical_cell_upper", model.support_upper)):
        if field in arguments:
            raw = arguments[field]
            observed = np.asarray(raw.split(",") if isinstance(raw, str) else raw, dtype=float)
            if not np.array_equal(observed, declared):
                raise ValueError("model support differs from the checkpoint summary")
    artifacts = contract.get("artifacts") or {}
    if summary.get("weights_npz_sha256") and (
        summary["weights_npz_sha256"] != artifacts["weights"]["sha256"]
    ):
        raise ValueError("checkpoint summary refers to different weights")
    expected_training_sha = (artifacts.get("training_table") or {}).get("sha256")
    if expected_training_sha and summary.get("training_csv_sha256") != expected_training_sha:
        raise ValueError("model contract training source differs from the checkpoint summary")
    declared_rows = contract.get("row_counts")
    if declared_rows is not None:
        if not isinstance(declared_rows, dict) or set(declared_rows) != {"source", "train", "validation", "test"}:
            raise ValueError("model row_counts must contain source, train, validation and test")
        split_rows = (summary.get("split_audit") or {}).get("row_counts") or (
            summary.get("evaluation_isolation") or {}
        ).get("row_counts") or {}
        actual_rows = {"source": summary.get("training_count"), **split_rows}
        if declared_rows != actual_rows or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in declared_rows.values()
        ):
            raise ValueError("model contract row counts differ from checkpoint summary")
        if sum(declared_rows[name] for name in ("train", "validation", "test")) != declared_rows["source"]:
            raise ValueError("model source/train/validation/test row counts do not reconcile")


def describe_model(model: FrozenTandemMLP) -> dict[str, Any]:
    """Return public metadata for the UI and per-job history, without local paths."""
    contract = model.contract
    arguments = model.summary.get("arguments") or {}
    attestation = model.runtime_checkpoint_attestation or {}
    split = (model.summary.get("split_audit") or {}).get("row_counts") or (
        model.summary.get("evaluation_isolation") or {}
    ).get("row_counts") or {}
    rows = contract.get("row_counts") or {
        "source": model.summary.get("training_count", contract.get("training_label_count")),
        "train": split.get("train"), "validation": split.get("validation"), "test": split.get("test"),
    }
    return {
        "schema": "rfic_q_sweep_model_description.v1",
        "model_id": model.model_id,
        "display_name": contract.get("display_name", model.model_id),
        "profile": getattr(model, "adapter_profile", "custom"),
        "model_seed": model.model_seed,
        "target_frequency_ghz": model.target_frequency_ghz,
        "contract_sha256": attestation.get("contract_sha256"),
        "summary_sha256": attestation.get("summary_sha256"),
        "weights_sha256": attestation.get("weights_sha256"),
        "runtime_checkpoint_status": attestation.get("status", "UNKNOWN"),
        "architecture": copy.deepcopy(contract.get("architecture") or {}),
        "geometry_projection": model.arrays.get("projection_mode", "independent_sigmoid"),
        "declared_support_lower": model.support_lower.tolist(),
        "declared_support_upper": model.support_upper.tolist(),
        "input_units": list(contract.get("input_units", _INPUT_UNITS)),
        "geometry_unit": contract.get("geometry_unit", "um"),
        "row_counts": copy.deepcopy(rows),
        "q_training_semantics": contract.get("q_target_semantics", arguments.get("q_target_semantics", "exact")),
        "q_training_margin": float(contract.get("q_training_margin_physical", arguments.get("q_minimum_margin_physical", 0.0))),
        "inference_q_input_transform": "identity_no_margin_no_clipping",
        "selection_q_semantics": "exact_symmetric",
        "model_acceptance_status": model.summary.get("quality_status", "UNKNOWN"),
        "scientific_boundary": contract.get("scientific_boundary", "Forward output is proxy evidence only."),
    }


def infer(model: FrozenTandemMLP, physical_targets: np.ndarray) -> MLPBatchPrediction:
    """Batch interface shared by approved models; preserves original target values."""
    return model.predict(physical_targets)
