#!/usr/bin/env python3
"""One-time sealed common-real-label forward evaluation for all paired arms."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

import evaluate_historical_tandem_fixed_targets as legacy


INPUT_COLUMNS = list(legacy.INPUT_COLUMNS)
GEOMETRY_COLUMNS = [
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
]
DECLARED_SPANS = np.asarray([2.5, 2.5, 20.0, 0.8], dtype=float)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rq-f-phase-summary", required=True)
    parser.add_argument("--expected-rq-f-phase-summary-sha256", required=True)
    parser.add_argument("--large-data-csv", required=True)
    parser.add_argument("--expected-large-data-csv-sha256", required=True)
    parser.add_argument("--common-holdout-manifest", required=True)
    parser.add_argument("--expected-common-holdout-manifest-sha256", required=True)
    parser.add_argument("--fixed-normalization-json", required=True)
    parser.add_argument("--expected-fixed-normalization-sha256", required=True)
    parser.add_argument("--trainer-source", required=True)
    parser.add_argument("--expected-trainer-source-sha256", required=True)
    parser.add_argument("--preregistration-json", required=True)
    parser.add_argument("--expected-preregistration-sha256", required=True)
    parser.add_argument("--clarification-addendum-json", required=True)
    parser.add_argument("--expected-clarification-addendum-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_sha(path: Path, expected: str, label: str) -> str:
    actual = _sha256(path)
    if actual != expected.strip().lower():
        raise ValueError(f"{label} SHA mismatch: {actual} != {expected}")
    return actual


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _metrics(truth: np.ndarray, prediction: np.ndarray) -> dict[str, Any]:
    error = prediction - truth
    normalized = error / DECLARED_SPANS[None, :]
    names = ("lp_nh", "ls_nh", "q", "k_abs")
    return {
        "row_count": int(truth.shape[0]),
        "joint_declared_range_normalized_rmse": float(np.sqrt(np.mean(normalized**2))),
        "per_feature": {
            name: {
                "mae_physical": float(np.mean(np.abs(error[:, index]))),
                "rmse_physical": float(np.sqrt(np.mean(error[:, index] ** 2))),
                "declared_range_normalized_mae": float(
                    np.mean(np.abs(normalized[:, index]))
                ),
                "declared_range_normalized_rmse": float(
                    np.sqrt(np.mean(normalized[:, index] ** 2))
                ),
            }
            for index, name in enumerate(names)
        },
    }


def main() -> int:
    args = _parse_args()
    source_paths = {
        "rq_f_phase_summary": Path(args.rq_f_phase_summary).resolve(),
        "large_data_csv": Path(args.large_data_csv).resolve(),
        "common_holdout_manifest": Path(args.common_holdout_manifest).resolve(),
        "fixed_normalization": Path(args.fixed_normalization_json).resolve(),
        "trainer_source": Path(args.trainer_source).resolve(),
        "preregistration": Path(args.preregistration_json).resolve(),
        "clarification_addendum": Path(args.clarification_addendum_json).resolve(),
    }
    expected_hashes = {
        "rq_f_phase_summary": args.expected_rq_f_phase_summary_sha256,
        "large_data_csv": args.expected_large_data_csv_sha256,
        "common_holdout_manifest": args.expected_common_holdout_manifest_sha256,
        "fixed_normalization": args.expected_fixed_normalization_sha256,
        "trainer_source": args.expected_trainer_source_sha256,
        "preregistration": args.expected_preregistration_sha256,
        "clarification_addendum": args.expected_clarification_addendum_sha256,
    }
    sources = {
        label: {"path": str(path), "sha256": _require_sha(path, expected_hashes[label], label)}
        for label, path in source_paths.items()
    }
    phase = _read_json(source_paths["rq_f_phase_summary"])
    if phase.get("overall_status") != "PASS_ALL_10_RUNS_VALIDATION_ONLY_TEST_SEALED":
        raise ValueError("RQ-F phase is not terminal PASS with sealed test")
    if phase.get("phase") != "rq_f_own_forward" or int(phase.get("run_count") or 0) != 10:
        raise ValueError("wrong RQ-F phase contract")
    runs = phase.get("runs") or []
    if len(runs) != 10:
        raise ValueError("RQ-F phase does not bind ten run records")
    run_keys = {(int(run["replicate"]), str(run["arm"])) for run in runs}
    if run_keys != {(replicate, arm) for replicate in range(1, 6) for arm in ("small", "large")}:
        raise ValueError("RQ-F run identities are incomplete")

    out_dir = Path(args.out_dir).resolve()
    if out_dir.exists():
        raise FileExistsError(f"no-clobber output exists: {out_dir}")
    trainer = legacy._load_trainer(source_paths["trainer_source"])
    rows = trainer._read_rows(source_paths["large_data_csv"])
    geometry_columns = trainer._resolve_geometry_columns(
        rows, ",".join(GEOMETRY_COLUMNS), "geom__"
    )
    matrix = trainer._build_matrix(rows, INPUT_COLUMNS, geometry_columns, INPUT_COLUMNS)
    split, split_audit = trainer._split_indices_from_common_holdout_manifest(
        matrix,
        SimpleNamespace(
            fixed_common_holdout_manifest_json=str(source_paths["common_holdout_manifest"]),
            fixed_common_holdout_manifest_sha256=sources["common_holdout_manifest"]["sha256"],
        ),
    )
    data = trainer._normalize(
        matrix,
        split,
        1.0e-12,
        fixed_contract_path=str(source_paths["fixed_normalization"]),
        expected_fixed_contract_sha256=sources["fixed_normalization"]["sha256"],
        input_columns=INPUT_COLUMNS,
        geometry_columns=GEOMETRY_COLUMNS,
    )
    test_indices = np.asarray(split["test"], dtype=int)
    if test_indices.shape != (9096,):
        raise ValueError(f"common test count is {len(test_indices)}, expected 9096")
    truth_standardized = data["x"][test_indices]
    truth_physical = truth_standardized * data["normalization"]["x_scale"][None, :] + data[
        "normalization"
    ]["x_mean"][None, :]
    geometry_standardized = data["y"][test_indices]
    test_identities = [matrix["source_geometry_identities"][int(index)] for index in test_indices]

    combined_rows: list[dict[str, Any]] = []
    model_results: list[dict[str, Any]] = []
    for run in sorted(runs, key=lambda item: (int(item["replicate"]), str(item["arm"]))):
        replicate = int(run["replicate"])
        arm = str(run["arm"])
        seed = int(run["seed"])
        summary_path = Path(run["summary"]["path"])
        weights_path = Path(run["weights"]["path"])
        _require_sha(summary_path, run["summary"]["sha256"], "run summary")
        _require_sha(weights_path, run["weights"]["sha256"], "run weights")
        summary = _read_json(summary_path)
        model = legacy._load_weights(weights_path)
        checks = {
            "training_summary_test_access_zero": int(
                (summary.get("test_access_contract") or {}).get("test_access_event_count") or 0
            )
            == 0,
            "training_summary_validation_only": summary.get("evaluation_mode") == "validation_only",
            "seed_exact": int((summary.get("arguments") or {}).get("seed") or -1) == seed,
            "train_rows_exact": int(
                ((summary.get("split_audit") or {}).get("row_counts") or {}).get("train") or 0
            )
            == (100000 if arm == "small" else 200000),
            "forward_architecture_exact": legacy._layer_widths(model["forward_weights"])
            == [10, 128, 128, 4],
            "normalization_x_mean_exact": np.array_equal(
                model["x_mean"], data["normalization"]["x_mean"]
            ),
            "normalization_x_scale_exact": np.array_equal(
                model["x_scale"], data["normalization"]["x_scale"]
            ),
            "normalization_y_mean_exact": np.array_equal(
                model["y_mean"], data["normalization"]["y_mean"]
            ),
            "normalization_y_scale_exact": np.array_equal(
                model["y_scale"], data["normalization"]["y_scale"]
            ),
        }
        if not all(checks.values()):
            raise ValueError(
                f"model contract failed rep{replicate} {arm}: {[key for key,value in checks.items() if not value]}"
            )
        prediction_standardized = trainer._predict(
            geometry_standardized, model["forward_weights"], model["forward_biases"]
        )
        prediction_physical = prediction_standardized * model["x_scale"][None, :] + model[
            "x_mean"
        ][None, :]
        if not np.all(np.isfinite(prediction_physical)):
            raise ValueError(f"nonfinite forward predictions rep{replicate} {arm}")
        model_metrics = _metrics(truth_physical, prediction_physical)
        model_results.append(
            {
                "replicate": replicate,
                "arm": arm,
                "seed": seed,
                "training_rows": 100000 if arm == "small" else 200000,
                "summary_sha256": run["summary"]["sha256"],
                "weights_sha256": run["weights"]["sha256"],
                "checks": checks,
                "metrics": model_metrics,
            }
        )
        for row_index, source_index in enumerate(test_indices):
            error = prediction_physical[row_index] - truth_physical[row_index]
            normalized = error / DECLARED_SPANS
            combined_rows.append(
                {
                    "replicate": replicate,
                    "arm": arm,
                    "seed": seed,
                    "training_rows": 100000 if arm == "small" else 200000,
                    "common_test_row_index": row_index,
                    "source_row_index": int(source_index),
                    "canonical_geometry_identity_sha256": test_identities[row_index],
                    "target__lp_nh": float(truth_physical[row_index, 0]),
                    "target__ls_nh": float(truth_physical[row_index, 1]),
                    "target__q_exact": float(truth_physical[row_index, 2]),
                    "target__k_abs": float(truth_physical[row_index, 3]),
                    "prediction__lp_nh": float(prediction_physical[row_index, 0]),
                    "prediction__ls_nh": float(prediction_physical[row_index, 1]),
                    "prediction__q_exact": float(prediction_physical[row_index, 2]),
                    "prediction__k_abs": float(prediction_physical[row_index, 3]),
                    "error__lp_nh": float(error[0]),
                    "error__ls_nh": float(error[1]),
                    "error__q": float(error[2]),
                    "error__k_abs": float(error[3]),
                    "row_joint_declared_range_rmse": float(np.sqrt(np.mean(normalized**2))),
                }
            )

    out_dir.mkdir(parents=True)
    predictions_path = out_dir / "controlled_forward_common_real_emx_test_predictions.csv"
    with predictions_path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(combined_rows[0]))
        writer.writeheader()
        writer.writerows(combined_rows)
    summary_payload = {
        "schema": "controlled_forward_common_real_label_evaluation_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS_ONE_TIME_SEALED_COMMON_TEST_EVALUATION",
        "scientific_boundary": (
            "Forward models are evaluated on the same 9096 identity-disjoint real-EMX labels after all "
            "paired runs were validation-selected and frozen. The holdout spans 120/123 occupied source cells "
            "and is same-source interpolation/generalization evidence, not whole-cell or deployment OOD."
        ),
        "historical_exact_q_boundary": "Q is evaluated as an exact historical-contract response, not a current Q-floor objective.",
        "sources": sources,
        "split_audit": split_audit,
        "test_access_event": {
            "count": 1,
            "timing": "after_all_10_rq_f_runs_frozen",
            "used_for_training": False,
            "used_for_checkpoint_selection": False,
            "used_for_hyperparameter_selection": False,
        },
        "model_results": model_results,
        "outputs": {
            "predictions_csv": str(predictions_path),
            "predictions_csv_sha256": _sha256(predictions_path),
            "row_count": len(combined_rows),
        },
    }
    summary_path = out_dir / "controlled_forward_common_real_emx_test_summary.json"
    summary_path.write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"overall_status={summary_payload['overall_status']}")
    print(f"model_count={len(model_results)}")
    print(f"prediction_rows={len(combined_rows)}")
    print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
