"""Replay the historical R0 NumPy model against its saved validation export.

No training, fixed-target generation, EM simulation, or test-set evaluation is
performed. This proves numerical runtime parity, not original trainer identity
or physical accuracy. Output directories are create-once.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
import warnings

import numpy as np

from rfic_transformer_inverse_design.synthesis import frozen_mlp
from rfic_transformer_inverse_design.synthesis.frozen_mlp import FrozenTandemMLP
from .data import sha256


VALIDATION_NAME = "physical_feature_tandem_inverse_validation_predictions.csv"
VALIDATION_SHA = "49038cf8b91cd215dfe4a62df8ad88b9de3ebfcf404dc3e8a675842cf7343678"
HISTORY_NAME = "physical_feature_tandem_inverse_history.csv"


def _pin(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def _write(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")


def replay_baseline(
    run_dir: str | Path,
    out_dir: str | Path,
    *,
    contract_path: str | Path | None = None,
    expected_validation_sha256: str = VALIDATION_SHA,
    absolute_tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Run both forward and inverse paths using the existing frozen runtime."""
    root, output = Path(run_dir).resolve(), Path(out_dir)
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    try:
        with warnings.catch_warnings(record=True) as observed_warnings:
            warnings.simplefilter("always")
            receipt = _replay(root, output, contract_path, expected_validation_sha256, absolute_tolerance)
        receipt["runtime_warnings"] = [{"category": item.category.__name__, "message": str(item.message), "filename": item.filename, "lineno": item.lineno} for item in observed_warnings]
    except Exception as exc:
        receipt = {"schema": "bb_r0_replay.v1", "status": "FAIL", "error": str(exc), "run_dir": str(root), "retraining_performed": False, "test_set_accessed": False, "fixed_target_generation": False}
        receipt["generated_utc"] = datetime.now(timezone.utc).isoformat()
        receipt["elapsed_seconds"] = time.monotonic() - start
        _write(output / "REPLAY_RECEIPT.json", receipt)
        raise
    receipt["generated_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["elapsed_seconds"] = time.monotonic() - start
    _write(output / "REPLAY_RECEIPT.json", receipt)
    with (output / "SHA256SUMS.txt").open("x", encoding="utf-8") as handle:
        for filename in ("replay_arrays.npz", "validation_row_identities.json", "REPLAY_RECEIPT.json"):
            handle.write(f"{sha256(output / filename)}  {filename}\n")
    return receipt


def _replay(root: Path, output: Path, contract_path: str | Path | None, expected_validation_sha256: str, tolerance: float) -> dict[str, Any]:
    if not np.isfinite(tolerance) or not 0 < tolerance <= 1e-9:
        raise ValueError("replay tolerance must be positive and no looser than 1e-9")
    contract_file = Path(contract_path).resolve() if contract_path else Path(frozen_mlp.__file__).with_name("real10k_model_contract.json")
    model = FrozenTandemMLP.load(root, contract_path=contract_file)
    validation_file = root / VALIDATION_NAME
    if sha256(validation_file) != expected_validation_sha256:
        raise ValueError("original validation export SHA mismatch")
    summary = model.summary
    summary_method = summary.get("method", {})
    if summary_method.get("geometry_output_constraint") != "sigmoid_projection_to_observed_training_envelope":
        raise ValueError("historical decoder is not the frozen runtime's independent sigmoid")
    if summary_method.get("local_refinement") != "disabled":
        raise ValueError("replay does not silently add or remove historical refinement")
    with validation_file.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    expected_count = summary["evaluation_isolation"]["row_counts"]["validation"]
    if len(rows) != expected_count or any(row["evaluation_split"] != "validation" for row in rows):
        raise ValueError("original export is not the complete original validation group")
    identities = [{name: row[name] for name in ("validation_index", "evaluation_split", "matrix_index", "source_row_index", "source_evaluation", "source_geometry_identity_sha256")} for row in rows]
    if len({row["source_geometry_identity_sha256"] for row in rows}) != len(rows):
        raise ValueError("validation source geometry identities are not unique")
    features = [name.removeprefix("input__") for name in model.contract["input_columns"]]
    geometries = [name.removeprefix("geom__") for name in model.contract["geometry_columns"]]
    def matrix(prefix: str, names: list[str]) -> np.ndarray:
        array = np.asarray([[float(row[f"{prefix}__{name}"]) for name in names] for row in rows], dtype=np.float64)
        if not np.isfinite(array).all():
            raise ValueError(f"nonfinite original {prefix} values")
        return array
    targets = matrix("target", features)
    paired_geometry = matrix("paired_geometry", geometries)
    expected_geometry = matrix("predicted_geometry", geometries)
    expected_reconstructed = matrix("reconstructed", features)
    expected_forward = matrix("forward", features)
    prediction = model.predict(targets)
    paired_normalized = (paired_geometry - model.arrays["y_mean"]) / model.arrays["y_scale"]
    forward = frozen_mlp._predict(paired_normalized, model.arrays["forward_weights"], model.arrays["forward_biases"])
    forward = forward * model.arrays["x_scale"] + model.arrays["x_mean"]
    if not all(np.isfinite(array).all() for array in (prediction.geometry, prediction.proxy_features, forward)):
        raise ValueError("replay produced nonfinite values")
    differences = {"geometry_max_absolute_difference": float(np.max(np.abs(prediction.geometry - expected_geometry))), "inverse_reconstruction_max_absolute_difference": float(np.max(np.abs(prediction.proxy_features - expected_reconstructed))), "paired_forward_max_absolute_difference": float(np.max(np.abs(forward - expected_forward)))}
    passed = all(value <= tolerance for value in differences.values())
    np.savez_compressed(output / "replay_arrays.npz", physical_targets=targets, paired_geometry=paired_geometry, inverse_geometry=prediction.geometry, inverse_proxy_features=prediction.proxy_features, paired_forward_features=forward, source_geometry_ids=np.asarray([row["source_geometry_identity_sha256"] for row in rows]))
    _write(output / "validation_row_identities.json", {"rows": identities})
    runtime_file = Path(frozen_mlp.__file__).resolve()
    repository = runtime_file.parents[2]
    current_trainer = repository / "scripts/train_physical_feature_tandem_inverse.py"
    expected_trainer = summary["model_comparison_contract"]["trainer_implementation_sha256"]
    observed_trainer = sha256(current_trainer) if current_trainer.is_file() else None
    sources = {"contract": _pin(contract_file), "runtime": _pin(runtime_file), "validation_export": _pin(validation_file)}
    for name, artifact in model.contract["artifacts"].items():
        if "filename" in artifact:
            sources[name] = _pin(root / artifact["filename"])
    history_by_stage = {}
    history_path = root / HISTORY_NAME
    if history_path.is_file():
        sources["training_history"] = _pin(history_path)
        with history_path.open(newline="", encoding="utf-8") as handle:
            for record in csv.DictReader(handle):
                history_by_stage[record["stage"]] = record
    command_path = root / "runner_command.json"
    if command_path.is_file():
        sources["historical_command"] = _pin(command_path)
    spans = model.support_upper - model.support_lower
    residual = prediction.proxy_features - targets
    metrics = {"boundary": "Original validation-only own-forward proxy; not comparable to new Broadband56 task or fresh EMX", "inverse_response_range_normalized_rmse": float(np.sqrt(np.mean((residual / spans) ** 2))), "inverse_response_feature_mae": np.mean(np.abs(residual), axis=0).tolist(), "forward_response_range_normalized_rmse": float(np.sqrt(np.mean(((forward - targets) / spans) ** 2)))}
    normalizer = {name: model.arrays[name].tolist() for name in ("x_mean", "x_scale", "y_mean", "y_scale", "geometry_lower", "geometry_upper")}
    return {"schema": "bb_r0_replay.v1", "status": "PASS" if passed else "FAIL", "model_id": model.model_id, "run_dir": str(root), "architecture": model.contract["architecture"], "hidden_activation_implementation": "NumPy tanh-approximation GELU from existing frozen_mlp runtime", "q_definition": model.contract.get("q_training_definition"), "target_frequency_ghz": model.target_frequency_ghz, "source_rows": summary["training_count"], "split_rows": summary["evaluation_isolation"]["row_counts"], "validation_rows_replayed": len(rows), "absolute_tolerance": tolerance, "numerical_parity": differences, "normalizer_from_original_weights": normalizer, "sources": sources, "trainer_identity": {"summary_expected_sha256": expected_trainer, "current_checkout_sha256": observed_trainer, "exact_original_trainer_identity_proven": observed_trainer == expected_trainer, "boundary": "Current trainer was not executed; numerical parity is not byte identity of the original trainer."}, "historical_loss_contract": summary.get("model_comparison_contract", {}).get("loss"), "historical_best_optimizer_updates": summary.get("best_optimizer_updates"), "historical_last_logged_records": history_by_stage, "replayed_validation_metrics": metrics, "artifacts": {name: _pin(output / name) for name in ("replay_arrays.npz", "validation_row_identities.json")}, "retraining_performed": False, "test_set_accessed": False, "fixed_target_generation": False, "real_emx_validation": "NOT_RUN", "research_boundary": "Historical R0 only; excluded from six Broadband56 packages and any controlled architecture ranking."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--contract")
    parser.add_argument("--expected-validation-sha256", default=VALIDATION_SHA)
    args = parser.parse_args()
    receipt = replay_baseline(args.run, args.out, contract_path=args.contract, expected_validation_sha256=args.expected_validation_sha256)
    print(json.dumps({"status": receipt["status"], "model_id": receipt["model_id"], "rows": receipt["validation_rows_replayed"], "parity": receipt["numerical_parity"]}))
    if receipt["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
