#!/usr/bin/env python3
"""Evaluate one controlled inverse arm on fixed targets with one shared F_ref.

This evaluator is deliberately narrower than the historical replay utility:
it permits one-shot inference only, requires the arm archive to contain the
byte-identical canonical forward component as the separately supplied F_ref,
and reports proxy finite-frame diagnostics rather than EMX accuracy.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

import evaluate_historical_tandem_fixed_targets as legacy


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
NORMALIZATION_ARRAY_KEYS = (
    "x_mean",
    "x_scale",
    "y_mean",
    "y_scale",
    "geometry_lower",
    "geometry_upper",
    "dimension_weights",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-weights", required=True)
    parser.add_argument("--arm-summary", required=True)
    parser.add_argument("--fref-weights", required=True)
    parser.add_argument("--fref-summary", required=True)
    parser.add_argument("--targets-json", required=True)
    parser.add_argument("--trainer-source", required=True)
    parser.add_argument("--preregistration-json", required=True)
    parser.add_argument("--clarification-addendum-json", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--arm", choices=("small", "large"), required=True)
    parser.add_argument("--replicate", type=int, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--expected-arm-training-rows", type=int, required=True)
    parser.add_argument("--expected-arm-seed", type=int, required=True)
    parser.add_argument("--expected-fref-seed", type=int, required=True)
    parser.add_argument("--expected-arm-weights-sha256", required=True)
    parser.add_argument("--expected-arm-summary-sha256", required=True)
    parser.add_argument("--expected-fref-weights-sha256", required=True)
    parser.add_argument("--expected-fref-summary-sha256", required=True)
    parser.add_argument("--expected-fref-forward-component-sha256", required=True)
    parser.add_argument("--expected-targets-sha256", required=True)
    parser.add_argument("--expected-trainer-sha256", required=True)
    parser.add_argument("--expected-preregistration-sha256", required=True)
    parser.add_argument("--expected-clarification-addendum-sha256", required=True)
    parser.add_argument("--expected-legacy-evaluator-sha256", required=True)
    return parser.parse_args()


def _canonical_forward_component_sha256(model: dict[str, Any]) -> str:
    digest = hashlib.sha256(b"controlled_forward_component_v1\0")
    for family in ("forward_weights", "forward_biases"):
        for index, value in enumerate(model[family]):
            array = np.asarray(value, dtype="<f8", order="C")
            digest.update(f"{family}_{index}\0{array.shape}\0".encode("ascii"))
            digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def _load_forward_only_fref(path: Path) -> dict[str, Any]:
    """Load the portable shared F_ref without accepting any inverse arrays."""
    with np.load(path, allow_pickle=False) as archive:
        inverse_keys = [
            key
            for key in archive.files
            if key.startswith("inverse_weight_") or key.startswith("inverse_bias_")
        ]
        if inverse_keys:
            raise ValueError(f"forward-only F_ref unexpectedly contains inverse arrays: {inverse_keys}")
        weight_keys = sorted(
            (key for key in archive.files if key.startswith("forward_weight_")),
            key=lambda key: int(key.removeprefix("forward_weight_")),
        )
        bias_keys = sorted(
            (key for key in archive.files if key.startswith("forward_bias_")),
            key=lambda key: int(key.removeprefix("forward_bias_")),
        )
        if weight_keys != ["forward_weight_0", "forward_weight_1", "forward_weight_2"]:
            raise ValueError("F_ref forward weight array order is incomplete")
        if bias_keys != ["forward_bias_0", "forward_bias_1", "forward_bias_2"]:
            raise ValueError("F_ref forward bias array order is incomplete")
        model = {
            "forward_weights": [np.asarray(archive[key], dtype=float) for key in weight_keys],
            "forward_biases": [np.asarray(archive[key], dtype=float) for key in bias_keys],
        }
        for key in NORMALIZATION_ARRAY_KEYS:
            archive_key = f"normalization__{key}"
            if archive_key not in archive.files:
                raise ValueError(f"F_ref lacks shared normalization array: {archive_key}")
            model[key] = np.asarray(archive[archive_key], dtype=float)
    if legacy._layer_widths(model["forward_weights"]) != [10, 128, 128, 4]:
        raise ValueError("F_ref forward architecture is not 10-128-128-4")
    if any(
        not np.all(np.isfinite(value))
        for value in [*model["forward_weights"], *model["forward_biases"]]
        + [model[key] for key in NORMALIZATION_ARRAY_KEYS]
    ):
        raise ValueError("F_ref contains non-finite arrays")
    return model


def _require_sha(path: Path, expected: str, label: str) -> str:
    actual = legacy._sha256(path)
    if actual != expected.strip().lower():
        raise ValueError(f"{label} SHA-256 mismatch: {actual} != {expected}")
    return actual


def _summary_contract_checks(
    summary: dict[str, Any],
    *,
    expected_seed: int,
    expected_train_rows: int,
    role: str,
) -> dict[str, bool]:
    arguments = summary.get("arguments") or {}
    split = summary.get("split_audit") or {}
    test_access = summary.get("test_access_contract") or {}
    normalization = summary.get("normalization_contract") or {}
    checks = {
        f"{role}_execution_pass": summary.get("execution_status") == "PASS",
        f"{role}_seed_exact": int(arguments.get("seed") or -1) == expected_seed,
        f"{role}_train_rows_exact": int((split.get("row_counts") or {}).get("train") or 0)
        == expected_train_rows,
        f"{role}_common_validation_rows_exact": int(
            (split.get("row_counts") or {}).get("validation") or 0
        )
        == 9096,
        f"{role}_common_test_rows_exact": int(
            (split.get("row_counts") or {}).get("test") or 0
        )
        == 9096,
        f"{role}_holdout_manifest_exact": (
            (split.get("fixed_common_holdout_manifest") or {}).get("sha256")
            == "4cd2e1f584c2cf7c14ef64a89508bd30d92aec4eb4b1c377effe1d561b9b8ebe"
        ),
        f"{role}_normalization_exact": normalization.get("sha256")
        == "9b29ac93f3eb0735964492497ec2032157c5ae290ce3ad2b97216a4bc4b34d47",
        f"{role}_validation_only": arguments.get("evaluation_mode") == "validation_only",
        f"{role}_test_never_accessed": int(test_access.get("test_access_event_count") or 0) == 0,
        f"{role}_exact_q_historical_semantics": arguments.get("q_target_semantics") == "exact",
    }
    if role == "arm":
        checks.update(
            {
                "arm_one_shot_contract": int(arguments.get("local_refinement_steps") or 0) == 0,
                "arm_independent_sigmoid": arguments.get("inverse_geometry_projection")
                == "independent_sigmoid",
                "arm_forward_frozen": arguments.get("freeze_transported_forward") is True,
                "arm_forward_updates_zero": int(
                    arguments.get("forward_max_optimizer_updates") or -1
                )
                == 0,
                "arm_inverse_updates_4800": int(
                    arguments.get("inverse_max_optimizer_updates") or 0
                )
                == 4800,
            }
        )
    return checks


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write empty predictions")
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = _parse_args()
    self_path = Path(__file__).resolve()
    legacy_path = Path(legacy.__file__).resolve()
    paths = {
        "arm_weights": Path(args.arm_weights).expanduser().resolve(),
        "arm_summary": Path(args.arm_summary).expanduser().resolve(),
        "fref_weights": Path(args.fref_weights).expanduser().resolve(),
        "fref_summary": Path(args.fref_summary).expanduser().resolve(),
        "targets": Path(args.targets_json).expanduser().resolve(),
        "trainer": Path(args.trainer_source).expanduser().resolve(),
        "preregistration": Path(args.preregistration_json).expanduser().resolve(),
        "clarification_addendum": Path(args.clarification_addendum_json).expanduser().resolve(),
    }
    expected = {
        "arm_weights": args.expected_arm_weights_sha256,
        "arm_summary": args.expected_arm_summary_sha256,
        "fref_weights": args.expected_fref_weights_sha256,
        "fref_summary": args.expected_fref_summary_sha256,
        "targets": args.expected_targets_sha256,
        "trainer": args.expected_trainer_sha256,
        "preregistration": args.expected_preregistration_sha256,
        "clarification_addendum": args.expected_clarification_addendum_sha256,
    }
    source_records: dict[str, dict[str, str]] = {}
    for label, path in paths.items():
        source_records[label] = {
            "path": str(path),
            "sha256": _require_sha(path, expected[label], label),
        }
    _require_sha(legacy_path, args.expected_legacy_evaluator_sha256, "legacy evaluator dependency")

    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise FileExistsError(f"no-clobber output exists: {out_dir}")
    if args.replicate not in range(1, 6):
        raise ValueError("replicate must be in 1..5")
    expected_rows_for_arm = 100000 if args.arm == "small" else 200000
    if args.expected_arm_training_rows != expected_rows_for_arm:
        raise ValueError("arm label and expected training rows disagree")

    arm_summary = legacy._read_json(paths["arm_summary"])
    fref_summary = legacy._read_json(paths["fref_summary"])
    preregistration = legacy._read_json(paths["preregistration"])
    clarification = legacy._read_json(paths["clarification_addendum"])
    targets_payload = legacy._read_json(paths["targets"])
    arm_model = legacy._load_weights(paths["arm_weights"])
    fref_model = _load_forward_only_fref(paths["fref_weights"])
    target_ids, target_matrix = legacy._targets(targets_payload)

    checks: dict[str, bool] = {}
    checks.update(
        _summary_contract_checks(
            arm_summary,
            expected_seed=args.expected_arm_seed,
            expected_train_rows=args.expected_arm_training_rows,
            role="arm",
        )
    )
    checks.update(
        _summary_contract_checks(
            fref_summary,
            expected_seed=args.expected_fref_seed,
            expected_train_rows=200000,
            role="fref",
        )
    )
    arm_forward_digest = _canonical_forward_component_sha256(arm_model)
    fref_forward_digest = _canonical_forward_component_sha256(fref_model)
    checks.update(
        {
            "preregistration_schema_exact": preregistration.get("schema")
            == "controlled_historical_data_scaling_preregistration_v1",
            "clarification_parent_exact": clarification.get("parent_preregistration_sha256")
            == args.expected_preregistration_sha256,
            "target_frame_schema_exact": targets_payload.get("schema")
            == "direct_mlp_one_shot_targets_v1",
            "target_frame_rows_exact": len(target_ids) == 10000,
            "arm_input_columns_exact": tuple(arm_summary.get("input_columns") or ())
            == EXPECTED_INPUT_COLUMNS,
            "arm_geometry_columns_exact": tuple(arm_summary.get("geometry_columns") or ())
            == EXPECTED_GEOMETRY_COLUMNS,
            "arm_inverse_architecture_exact": legacy._layer_widths(arm_model["inverse_weights"])
            == [4, 128, 128, 10],
            "fref_forward_architecture_exact": legacy._layer_widths(fref_model["forward_weights"])
            == [10, 128, 128, 4],
            "fref_summary_is_forward_only": fref_summary.get("schema")
            == "controlled_forward_only_fref_summary_v1"
            and not (fref_summary.get("array_contract") or {}).get("inverse_array_keys_present"),
            "fref_forward_digest_expected": fref_forward_digest
            == args.expected_fref_forward_component_sha256,
            "arm_contains_same_fref_forward": arm_forward_digest == fref_forward_digest,
            "shared_normalization_arrays_exact": all(
                np.array_equal(arm_model[key], fref_model[key])
                for key in NORMALIZATION_ARRAY_KEYS
            ),
        }
    )
    if not all(checks.values()):
        raise ValueError(f"contract checks failed: {[key for key,value in checks.items() if not value]}")

    trainer = legacy._load_trainer(paths["trainer"])
    standardized_targets = (
        target_matrix - arm_model["x_mean"][None, :]
    ) / arm_model["x_scale"][None, :]
    normalization = {
        "x_mean": arm_model["x_mean"],
        "x_scale": arm_model["x_scale"],
        "y_mean": arm_model["y_mean"],
        "y_scale": arm_model["y_scale"],
        "geometry_lower": arm_model["geometry_lower"],
        "geometry_upper": arm_model["geometry_upper"],
        "response_loss_dimension_weights": arm_model["dimension_weights"],
    }
    geometry_normalized = trainer._predict_inverse(
        standardized_targets,
        arm_model["inverse_weights"],
        arm_model["inverse_biases"],
        arm_model["geometry_lower"],
        arm_model["geometry_upper"],
        projection_mode="independent_sigmoid",
        normalization=normalization,
        topology_contract=arm_model["topology_contract"],
    )
    proxy_standardized = trainer._predict(
        geometry_normalized,
        fref_model["forward_weights"],
        fref_model["forward_biases"],
    )
    proxy_physical = proxy_standardized * fref_model["x_scale"][None, :] + fref_model[
        "x_mean"
    ][None, :]
    geometry_physical = geometry_normalized * arm_model["y_scale"][None, :] + arm_model[
        "y_mean"
    ][None, :]
    numerical_checks = {
        "geometry_normalized_finite": bool(np.all(np.isfinite(geometry_normalized))),
        "proxy_physical_finite": bool(np.all(np.isfinite(proxy_physical))),
        "geometry_physical_finite": bool(np.all(np.isfinite(geometry_physical))),
        "geometry_inside_declared_normalized_bounds": bool(
            np.all(geometry_normalized >= arm_model["geometry_lower"][None, :] - 1e-12)
            and np.all(geometry_normalized <= arm_model["geometry_upper"][None, :] + 1e-12)
        ),
    }
    checks.update(numerical_checks)
    if not all(numerical_checks.values()):
        raise ValueError(f"numerical checks failed: {numerical_checks}")

    legacy_mask = target_matrix[:, 3] <= 0.8
    high_k_mask = ~legacy_mask
    if int(np.sum(legacy_mask)) != 8000 or int(np.sum(high_k_mask)) != 2000:
        raise ValueError("fixed frame panel counts are not 8000/2000")
    rows: list[dict[str, Any]] = []
    for index, target_id in enumerate(target_ids):
        signed = proxy_physical[index] - target_matrix[index]
        fixed_range = signed / legacy.FIXED_FRAME_SPANS
        historical_range = signed / legacy.LEGACY_SPANS
        row: dict[str, Any] = {
            "row_index": index,
            "target_id": target_id,
            "panel": "legacy_k_le_0p8" if legacy_mask[index] else "high_k_gt_0p8",
            "arm": args.arm,
            "replicate": args.replicate,
            "model_id": args.model_id,
            "model_seed": args.expected_arm_seed,
            "evaluation_forward_component_sha256": fref_forward_digest,
            "inference_mode": "one_shot_shared_fref",
        }
        for column, value in zip(legacy.TARGET_OUTPUT_COLUMNS, target_matrix[index]):
            row[column] = float(value)
        for column, value in zip(legacy.PROXY_OUTPUT_COLUMNS, proxy_physical[index]):
            row[column] = float(value)
        for feature_index, key in enumerate(("lp_nh", "ls_nh", "q", "k_abs")):
            row[f"signed_error__{key}"] = float(signed[feature_index])
            row[f"absolute_error__{key}"] = float(abs(signed[feature_index]))
            row[f"fixed_frame_range_error__{key}"] = float(fixed_range[feature_index])
            row[f"historical_range_error__{key}"] = float(historical_range[feature_index])
        row["q_shortfall"] = float(max(target_matrix[index, 2] - proxy_physical[index, 2], 0.0))
        row["q_lower_bound_met"] = bool(proxy_physical[index, 2] >= target_matrix[index, 2])
        row["joint_fixed_frame_range_rmse"] = float(np.sqrt(np.mean(fixed_range**2)))
        for column, value in zip(EXPECTED_GEOMETRY_COLUMNS, geometry_physical[index]):
            row[column] = float(value)
        row["selected_geometry_sha256"] = legacy._vector_digest(geometry_physical[index])
        rows.append(row)

    metrics = {
        "all_10000_coverage_stress": legacy._panel_metrics(
            target_matrix, proxy_physical, np.ones(10000, dtype=bool)
        ),
        "legacy_k_le_0p8_8000_primary": legacy._panel_metrics(
            target_matrix, proxy_physical, legacy_mask
        ),
        "high_k_gt_0p8_2000_extrapolation": legacy._panel_metrics(
            target_matrix, proxy_physical, high_k_mask
        ),
    }
    out_dir.mkdir(parents=True)
    stem = f"controlled_{args.arm}_rep{args.replicate}_shared_fref_fixed10k"
    predictions_path = out_dir / f"{stem}_predictions.csv"
    _write_csv(predictions_path, rows)
    summary_payload = {
        "schema": "controlled_tandem_shared_fref_fixed_target_evaluation_v1",
        "overall_status": "PASS_PROXY_FINITE_FRAME_NOT_EMX",
        "scientific_boundary": (
            "Every inverse arm is scored by the same hash-bound F_ref forward component. These are exact "
            "finite-frame proxy quantities on a deterministic centered-LHS frame, not iid population estimates "
            "and not real-EMX accuracy. The single F_ref seed is conditioned upon and its random variation is "
            "not included in the five paired inverse-replicate interval."
        ),
        "historical_exact_q_boundary": (
            "Training uses exact-Q semantics to estimate the historical-method data-size contrast. Q lower-bound "
            "satisfaction is reported as an engineering diagnostic; this is not a current Q-floor-optimized arm."
        ),
        "sources": {
            **source_records,
            "evaluator": {"path": str(self_path), "sha256": legacy._sha256(self_path)},
            "legacy_evaluator_dependency": {
                "path": str(legacy_path),
                "sha256": legacy._sha256(legacy_path),
            },
        },
        "arm": args.arm,
        "replicate": args.replicate,
        "model_id": args.model_id,
        "model_seed": args.expected_arm_seed,
        "training_rows": args.expected_arm_training_rows,
        "f_ref_seed": args.expected_fref_seed,
        "evaluation_forward_weights_archive_sha256": source_records["fref_weights"]["sha256"],
        "evaluation_forward_component_sha256": fref_forward_digest,
        "arm_forward_component_sha256": arm_forward_digest,
        "checks": checks,
        "target_frame": {
            "row_count": 10000,
            "legacy_primary_count": 8000,
            "high_k_stress_count": 2000,
            "sampling_interpretation": "deterministic finite coverage frame",
        },
        "metrics": metrics,
        "outputs": {
            "predictions_csv": str(predictions_path),
            "predictions_csv_sha256": legacy._sha256(predictions_path),
            "row_count": len(rows),
        },
    }
    summary_path = out_dir / f"{stem}_summary.json"
    summary_path.write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"overall_status={summary_payload['overall_status']}")
    print(f"evaluation_forward_component_sha256={fref_forward_digest}")
    print(f"predictions={predictions_path}")
    print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
