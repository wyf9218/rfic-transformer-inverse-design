#!/usr/bin/env python3
"""Freeze a completed forward-stage checkpoint as the shared controlled F_ref.

The source trainer always starts an inverse stage after the forward stage.  The
inverse stage is irrelevant to F_ref.  This binder therefore accepts only the
atomic PASS marker and its hash-bound forward arrays, verifies the preregistered
training/split/normalization/update contract, and creates a portable archive
that intentionally contains no inverse arrays.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


TRAINER_SHA = "92988524b08b15a2388f655f6239070889098024e49ee184832f69876f7db3be"
TRAINING_CSV_SHA = "61d93c5489081f41bb8878c1ef847c61972408c4135aca06828ce8d09bf5d61c"
HOLDOUT_SHA = "4cd2e1f584c2cf7c14ef64a89508bd30d92aec4eb4b1c377effe1d561b9b8ebe"
NORMALIZATION_SHA = "9b29ac93f3eb0735964492497ec2032157c5ae290ce3ad2b97216a4bc4b34d47"
EXPECTED_INPUT_COLUMNS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
EXPECTED_GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-contract", required=True)
    parser.add_argument("--stage-marker", required=True)
    parser.add_argument("--stage-metadata", required=True)
    parser.add_argument("--stage-weights", required=True)
    parser.add_argument("--trainer-source", required=True)
    parser.add_argument("--preregistration-json", required=True)
    parser.add_argument("--clarification-addendum-json", required=True)
    parser.add_argument("--expected-stage-contract-sha256", required=True)
    parser.add_argument("--expected-stage-marker-sha256", required=True)
    parser.add_argument("--expected-stage-metadata-sha256", required=True)
    parser.add_argument("--expected-stage-weights-sha256", required=True)
    parser.add_argument("--expected-preregistration-sha256", required=True)
    parser.add_argument("--expected-clarification-addendum-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _require_sha(path: Path, expected: str, label: str) -> str:
    actual = _sha256(path)
    if actual != expected.strip().lower():
        raise ValueError(f"{label} SHA-256 mismatch: {actual} != {expected}")
    return actual


def _json_fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()


def _canonical_forward_component_sha256(
    weights: list[np.ndarray], biases: list[np.ndarray]
) -> str:
    digest = hashlib.sha256(b"controlled_forward_component_v1\0")
    for family, values in (("forward_weights", weights), ("forward_biases", biases)):
        for index, value in enumerate(values):
            array = np.asarray(value, dtype="<f8", order="C")
            digest.update(f"{family}_{index}\0{array.shape}\0".encode("ascii"))
            digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def _load_forward_stage(path: Path) -> tuple[list[np.ndarray], list[np.ndarray]]:
    with np.load(path, allow_pickle=False) as archive:
        weight_keys = sorted(
            (key for key in archive.files if key.startswith("weight_")),
            key=lambda key: int(key.removeprefix("weight_")),
        )
        bias_keys = sorted(
            (key for key in archive.files if key.startswith("bias_")),
            key=lambda key: int(key.removeprefix("bias_")),
        )
        if weight_keys != ["weight_0", "weight_1", "weight_2"]:
            raise ValueError(f"unexpected forward stage weight arrays: {weight_keys}")
        if bias_keys != ["bias_0", "bias_1", "bias_2"]:
            raise ValueError(f"unexpected forward stage bias arrays: {bias_keys}")
        if sorted(archive.files) != sorted(weight_keys + bias_keys):
            raise ValueError("forward stage archive contains undeclared arrays")
        weights = [np.asarray(archive[key], dtype=float).copy() for key in weight_keys]
        biases = [np.asarray(archive[key], dtype=float).copy() for key in bias_keys]
    expected_weight_shapes = [(10, 128), (128, 128), (128, 4)]
    expected_bias_shapes = [(128,), (128,), (4,)]
    if [value.shape for value in weights] != expected_weight_shapes:
        raise ValueError("forward stage weights do not implement 10-128-128-4")
    if [value.shape for value in biases] != expected_bias_shapes:
        raise ValueError("forward stage biases do not implement 10-128-128-4")
    if any(not np.all(np.isfinite(value)) for value in weights + biases):
        raise ValueError("forward stage arrays contain non-finite values")
    return weights, biases


def _write_json_no_clobber(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _validation_history_checks(metadata: dict[str, Any]) -> dict[str, bool]:
    history = metadata.get("history") or []
    expected_updates = list(range(40, 4801, 40))
    updates = [int(item.get("optimizer_updates") or 0) for item in history]
    draws = [int(item.get("real_row_draws") or 0) for item in history]
    events = [int(item.get("validation_event") or 0) for item in history]
    validation_values = [
        float(item.get("validation_response_objective_rmse", math.nan)) for item in history
    ]
    best = metadata.get("best") or {}
    best_update = int(best.get("optimizer_updates") or 0)
    best_loss = float(best.get("loss", math.nan))
    loss_by_update = dict(zip(updates, validation_values))
    return {
        "validation_history_has_120_fixed_checkpoints": len(history) == 120,
        "validation_updates_are_exact_40_to_4800": updates == expected_updates,
        "row_draws_equal_updates_times_4096": draws
        == [update * 4096 for update in expected_updates],
        "validation_events_are_exact_1_to_120": events == list(range(1, 121)),
        "validation_metrics_all_finite": bool(validation_values)
        and all(math.isfinite(value) for value in validation_values),
        "best_checkpoint_is_one_of_common_validation_checkpoints": best_update in expected_updates,
        "best_loss_is_finite": math.isfinite(best_loss),
        "best_loss_matches_selected_validation_checkpoint": best_update in loss_by_update
        and math.isclose(best_loss, loss_by_update[best_update], rel_tol=0.0, abs_tol=1e-15),
        "best_loss_is_global_minimum_over_common_validation_checkpoints": bool(validation_values)
        and math.isclose(best_loss, min(validation_values), rel_tol=0.0, abs_tol=1e-15),
    }


def main() -> int:
    args = _parse_args()
    paths = {
        "stage_contract": Path(args.stage_contract).expanduser().resolve(),
        "stage_marker": Path(args.stage_marker).expanduser().resolve(),
        "stage_metadata": Path(args.stage_metadata).expanduser().resolve(),
        "stage_weights": Path(args.stage_weights).expanduser().resolve(),
        "trainer": Path(args.trainer_source).expanduser().resolve(),
        "preregistration": Path(args.preregistration_json).expanduser().resolve(),
        "clarification_addendum": Path(args.clarification_addendum_json).expanduser().resolve(),
    }
    expected = {
        "stage_contract": args.expected_stage_contract_sha256,
        "stage_marker": args.expected_stage_marker_sha256,
        "stage_metadata": args.expected_stage_metadata_sha256,
        "stage_weights": args.expected_stage_weights_sha256,
        "trainer": TRAINER_SHA,
        "preregistration": args.expected_preregistration_sha256,
        "clarification_addendum": args.expected_clarification_addendum_sha256,
    }
    source_records = {
        label: {"path": str(path), "sha256": _require_sha(path, expected[label], label)}
        for label, path in paths.items()
    }
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise FileExistsError(f"no-clobber output directory exists: {out_dir}")

    contract = _read_json(paths["stage_contract"])
    marker = _read_json(paths["stage_marker"])
    metadata = _read_json(paths["stage_metadata"])
    preregistration = _read_json(paths["preregistration"])
    clarification = _read_json(paths["clarification_addendum"])
    weights, biases = _load_forward_stage(paths["stage_weights"])

    fingerprint = str(contract.get("fingerprint_sha256") or "")
    contract_without_fingerprint = dict(contract)
    contract_without_fingerprint.pop("fingerprint_sha256", None)
    split = contract.get("split_audit") or {}
    budget = contract.get("optimizer_budget_contract") or {}
    sampler = contract.get("training_batch_sampler_contract") or {}
    arguments = contract.get("arguments") or {}
    history_checks = _validation_history_checks(metadata)
    checks = {
        "contract_schema_exact": contract.get("schema")
        == "physical_feature_tandem_stage_checkpoint_contract_v1",
        "contract_fingerprint_recalculates": _json_fingerprint(contract_without_fingerprint)
        == fingerprint,
        "marker_schema_exact": marker.get("schema")
        == "physical_feature_tandem_stage_checkpoint_marker_v1",
        "marker_pass_forward_only": marker.get("status") == "PASS"
        and marker.get("stage") == "forward_proxy",
        "marker_contract_exact": marker.get("contract_fingerprint_sha256") == fingerprint,
        "marker_has_no_dependency": marker.get("dependency_weights_sha256") == "",
        "marker_binds_metadata": marker.get("metadata_sha256")
        == source_records["stage_metadata"]["sha256"],
        "marker_binds_weights": marker.get("weights_sha256")
        == source_records["stage_weights"]["sha256"],
        "metadata_schema_exact": metadata.get("schema")
        == "physical_feature_tandem_stage_checkpoint_v1",
        "metadata_contract_exact": metadata.get("contract_fingerprint_sha256") == fingerprint,
        "metadata_binds_weights": metadata.get("weights_sha256")
        == source_records["stage_weights"]["sha256"],
        "metadata_forward_dimensions_exact": metadata.get("stage") == "forward_proxy"
        and int(metadata.get("expected_input_dim") or 0) == 10
        and int(metadata.get("expected_output_dim") or 0) == 4,
        "trainer_exact": contract.get("trainer_implementation_sha256") == TRAINER_SHA,
        "training_csv_exact": contract.get("training_csv_sha256") == TRAINING_CSV_SHA,
        "seed_exact": int(arguments.get("seed") or -1) == 2026082201,
        "fixed_common_holdout_exact": (
            (split.get("fixed_common_holdout_manifest") or {}).get("sha256") == HOLDOUT_SHA
        ),
        "split_rows_exact": (split.get("row_counts") or {})
        == {"train": 200000, "validation": 9096, "test": 9096},
        "same_distribution_holdout_not_cell_ood": split.get("physical_cell_grouped") is False,
        "fixed_normalization_exact": (contract.get("normalization_contract") or {}).get(
            "sha256"
        )
        == NORMALIZATION_SHA,
        "validation_only_test_sealed": arguments.get("evaluation_mode") == "validation_only",
        "forward_budget_exact": int(
            ((budget.get("forward") or {}).get("target_optimizer_updates") or 0)
        )
        == 4800,
        "forward_row_draw_budget_exact": int(
            ((budget.get("forward") or {}).get("target_real_row_draws") or 0)
        )
        == 19660800,
        "early_stopping_disabled": budget.get("early_stopping_enabled") is False,
        "validation_cadence_40_updates": int(
            budget.get("validation_every_optimizer_updates") or 0
        )
        == 40,
        "continuous_full_batch_4096": budget.get("exact_update_batch_mode")
        == "continuous_permutation_full_batch"
        and int(sampler.get("batch_size") or 0) == 4096
        and sampler.get("all_exact_update_batches_have_configured_size") is True,
        "declared_range_mse_exact_q": arguments.get("response_loss_scaling")
        == "declared_range"
        and arguments.get("response_loss_family") == "mse"
        and arguments.get("q_target_semantics") == "exact",
        "preregistration_schema_exact": preregistration.get("schema")
        == "controlled_historical_data_scaling_preregistration_v1",
        "clarification_parent_exact": clarification.get("parent_preregistration_sha256")
        == source_records["preregistration"]["sha256"],
        **history_checks,
    }
    if not all(checks.values()):
        raise ValueError(
            "F_ref forward-stage checks failed: "
            + ", ".join(key for key, value in checks.items() if not value)
        )

    normalization = contract.get("normalization") or {}
    portable_arrays: dict[str, np.ndarray] = {}
    for index, value in enumerate(weights):
        portable_arrays[f"forward_weight_{index}"] = value
    for index, value in enumerate(biases):
        portable_arrays[f"forward_bias_{index}"] = value
    for key in ("x_mean", "x_scale", "y_mean", "y_scale", "geometry_lower", "geometry_upper"):
        portable_arrays[f"normalization__{key}"] = np.asarray(normalization[key], dtype=float)
    portable_arrays["normalization__dimension_weights"] = np.asarray(
        normalization["response_loss_dimension_weights"], dtype=float
    )
    for key, value in portable_arrays.items():
        if not np.all(np.isfinite(value)):
            raise ValueError(f"portable F_ref array is non-finite: {key}")

    out_dir.mkdir(parents=True)
    portable_weights_path = out_dir / "controlled_shared_fref_forward_only_weights.npz"
    np.savez_compressed(portable_weights_path, **portable_arrays)
    portable_weights_sha = _sha256(portable_weights_path)
    forward_component_sha = _canonical_forward_component_sha256(weights, biases)

    best = metadata.get("best") or {}
    portable_summary = {
        "schema": "controlled_forward_only_fref_summary_v1",
        "execution_status": "PASS",
        "overall_status": "PASS_FROZEN_FORWARD_ONLY_INVERSE_ARRAYS_ABSENT",
        "weights_npz": str(portable_weights_path),
        "weights_npz_sha256": portable_weights_sha,
        "input_columns": list(EXPECTED_INPUT_COLUMNS),
        "geometry_columns": list(EXPECTED_GEOMETRY_COLUMNS),
        "arguments": {
            "seed": 2026082201,
            "evaluation_mode": "validation_only",
            "q_target_semantics": "exact",
        },
        "evaluation_mode": "validation_only",
        "test_access_contract": {
            "test_access_event_count": 0,
            "test_evaluator_called": False,
            "evidence": (
                "The atomic forward marker is written immediately after common-validation checkpoint "
                "selection and before inverse training or any post-training evaluator in trainer SHA "
                f"{TRAINER_SHA}. The run contract is validation_only."
            ),
        },
        "split_audit": split,
        "normalization_contract": contract.get("normalization_contract"),
        "optimizer_budget_contract": {
            **budget,
            "realized": {
                "forward_optimizer_updates": 4800,
                "forward_real_row_draws": 19660800,
                "validation_checkpoint_count": 120,
            },
        },
        "model_comparison_contract": {
            "trainer_implementation_sha256": TRAINER_SHA,
            "architecture": {
                "forward_layer_sizes": [10, 128, 128, 4],
                "forward_hidden_widths": [128, 128],
                "forward_parameter_count": 18436,
                "activation": "GELU_hidden_linear_output",
            },
        },
        "best_optimizer_updates": {"forward_proxy": int(best["optimizer_updates"])},
        "best_validation_response_objective_rmse": float(best["loss"]),
        "canonical_forward_component_sha256": forward_component_sha,
        "source_stage": {
            **source_records,
            "contract_fingerprint_sha256": fingerprint,
            "stage_generated_utc": marker.get("generated_utc"),
        },
        "array_contract": {
            "ordered_forward_weight_keys": [f"forward_weight_{index}" for index in range(3)],
            "ordered_forward_bias_keys": [f"forward_bias_{index}" for index in range(3)],
            "inverse_array_keys_present": [],
            "inverse_arrays_eligible": False,
        },
        "scientific_boundary": (
            "This artifact binds one shared forward surrogate trained on the common 200,000-row train "
            "pool and selected only on the 9,096-row common validation split. RQ-I is conditional on this "
            "single F_ref seed/weight. Its randomness is outside the five paired inverse-replicate interval."
        ),
    }
    portable_summary_path = out_dir / "controlled_shared_fref_forward_only_summary.json"
    _write_json_no_clobber(portable_summary_path, portable_summary)

    binding = {
        "schema": "controlled_shared_fref_binding_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS_FREF_FROZEN_RQ_I_ARMS_MAY_USE_THIS_FORWARD_ONLY",
        "parent_preregistration_sha256": source_records["preregistration"]["sha256"],
        "clarification_addendum_sha256": source_records["clarification_addendum"]["sha256"],
        "f_ref_seed": 2026082201,
        "f_ref_training_rows": 200000,
        "f_ref_weights": {
            "path": str(portable_weights_path),
            "sha256": portable_weights_sha,
            "content": "ordered forward arrays plus shared declared normalization; no inverse arrays",
        },
        "f_ref_summary": {
            "path": str(portable_summary_path),
            "sha256": _sha256(portable_summary_path),
        },
        "canonical_forward_component_sha256": forward_component_sha,
        "forward_stage_checkpoint": {
            "contract": source_records["stage_contract"],
            "marker": source_records["stage_marker"],
            "metadata": source_records["stage_metadata"],
            "weights": source_records["stage_weights"],
            "contract_fingerprint_sha256": fingerprint,
            "best_forward_optimizer_update": int(best["optimizer_updates"]),
            "best_validation_response_objective_rmse": float(best["loss"]),
        },
        "checks": checks,
        "test_access_event_count_at_binding": 0,
        "estimand_boundary": (
            "RQ-I estimates the inverse-data-size effect under this one frozen shared F_ref. The 100k "
            "inverse arm therefore indirectly uses the large-pool forward labels through the scorer. The "
            "effect is conditional on F_ref seed/weight and is not a complete end-to-end deployment effect."
        ),
        "unused_inverse_byproduct": {
            "required_by_trainer": True,
            "eligible_for_any_arm_or_claim": False,
            "included_in_portable_artifact": False,
            "may_be_terminated_after_forward_marker": True,
        },
    }
    binding_path = out_dir / "controlled_shared_fref_binding.json"
    _write_json_no_clobber(binding_path, binding)
    index_path = out_dir / "SHA256SUMS.txt"
    with index_path.open("x", encoding="ascii") as handle:
        for path in (portable_weights_path, portable_summary_path, binding_path):
            handle.write(f"{_sha256(path)}  {path.name}\n")

    print(f"overall_status={binding['overall_status']}")
    print(f"canonical_forward_component_sha256={forward_component_sha}")
    print(f"f_ref_weights_sha256={portable_weights_sha}")
    print(f"f_ref_summary_sha256={_sha256(portable_summary_path)}")
    print(f"binding_sha256={_sha256(binding_path)}")
    print(f"index_sha256={_sha256(index_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
