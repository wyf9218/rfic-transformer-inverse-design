#!/usr/bin/env python3
"""Render the hash-bound architecture-matched fixed8k report figures.

The renderer is intentionally presentation-only.  It accepts an already built
report directory, verifies every source artifact recorded by REPORT_SUMMARY,
checks the tabular values needed by the figures, and then publishes exactly ten
PNG/SVG figure pairs through a hidden no-clobber staging directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Iterable

import numpy as np


EXPECTED_N = 8_000
REFERENCE_NAME = "Previous-presentation 100k reference"
CANDIDATE_NAME = "architecture-matched 200k model"
EXPECTED_FORWARD_ARCHITECTURE = (10, 256, 256, 128, 4)
EXPECTED_INVERSE_ARCHITECTURE = (4, 512, 512, 256, 10)
EXPECTED_DECODER = "hard_feasible_topology_v1"
EXPECTED_PARAMETER_COUNT = 501_134
FEATURES = ("Lp", "Ls", "Qmin", "K_abs")
FEATURE_DISPLAY = {
    "Lp": "Lp",
    "Ls": "Ls",
    "Qmin": "Qmin",
    "K_abs": "|K|",
}
FEATURE_SUFFIX = {
    "Lp": "lp",
    "Ls": "ls",
    "Qmin": "qmin",
    "K_abs": "k_abs",
}
MODEL_COLORS = {"reference": "#355C7D", "candidate": "#D95F59"}
MODEL_NAMES = {"reference": REFERENCE_NAME, "candidate": CANDIDATE_NAME}
FIGURE_BASENAMES = (
    "01_model_architecture_and_data_counts",
    "02_training_curves",
    "03_feature_mae_comparison",
    "04_feature_rmse_comparison",
    "05_target_vs_prediction_four_panel",
    "06_absolute_error_cdf_four_panel",
    "07_p50_p90_p95_tail_error",
    "08_q_target_met_and_shortfall",
    "09_success_rate_vs_tolerance",
    "10_geometry_feasibility_and_runtime",
)
INPUT_FILENAMES = (
    "MODEL_CONTRACT_COMPARISON.json",
    "geometry_feasibility_summary.json",
    "training_runtime_summary.json",
    "feature_metrics_long.csv",
    "joint_metrics.csv",
    "per_target_paired_errors.csv",
    "training_curves_long.csv",
)
EXPECTED_JSON_SCHEMAS = {
    "REPORT_SUMMARY.json": "architecture_matched_fixed8k_report_summary_v1",
    "MODEL_CONTRACT_COMPARISON.json": "architecture_matched_fixed8k_model_contract_comparison_v1",
    "geometry_feasibility_summary.json": "architecture_matched_fixed8k_geometry_feasibility_v1",
    "training_runtime_summary.json": "architecture_matched_fixed8k_runtime_summary_v1",
}
FEATURE_METRIC_COLUMNS = (
    "model_role",
    "model_name",
    "feature",
    "unit",
    "normalization_span",
    "count",
    "bias",
    "mae",
    "rmse",
    "median_absolute_error",
    "p90_absolute_error",
    "p95_absolute_error",
    "p99_absolute_error",
    "maximum_absolute_error",
    "normalized_mae",
    "normalized_rmse",
    "source_prediction_csv_sha256",
)
JOINT_METRIC_COLUMNS = (
    "section",
    "model_role",
    "model_name",
    "metric",
    "value",
    "unit",
    "definition",
    "n",
    "tolerance_normalized",
    "source_csv_sha256",
)
TRAINING_CURVE_COLUMNS = (
    "model_role",
    "model_name",
    "stage",
    "x_value",
    "series",
    "metric",
    "value",
    "unit",
    "n",
    "source_history_csv_sha256",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", required=True)
    return parser.parse_args(argv)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _valid_sha256(value: Any, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{label} is not a lowercase SHA-256 value")
    return normalized


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _check_finite_json(value: Any, label: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"{label} contains a non-finite number")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _check_finite_json(child, f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _check_finite_json(child, f"{label}[{index}]")
        return
    raise ValueError(f"{label} contains an unsupported JSON value")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON input {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON input is not an object: {path.name}")
    if not isinstance(value.get("schema"), str) or not str(value["schema"]).strip():
        raise ValueError(f"JSON input lacks a nonempty schema: {path.name}")
    _check_finite_json(value, path.name)
    expected_schema = EXPECTED_JSON_SCHEMAS.get(path.name)
    if expected_schema is not None and value.get("schema") != expected_schema:
        raise ValueError(
            f"{path.name} schema mismatch: {value.get('schema')!r} != {expected_schema!r}"
        )
    return value


def _read_csv(path: Path, required_columns: Iterable[str]) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            if len(fieldnames) != len(set(fieldnames)):
                raise ValueError(f"CSV has duplicate columns: {path.name}")
            missing = set(required_columns).difference(fieldnames)
            if missing:
                raise ValueError(f"CSV {path.name} lacks columns {sorted(missing)}")
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"cannot read CSV input {path.name}: {exc}") from exc
    if not rows:
        raise ValueError(f"CSV input is empty: {path.name}")
    forbidden = {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}
    for row_index, row in enumerate(rows, 1):
        for column, raw in row.items():
            if raw is None:
                raise ValueError(f"CSV {path.name} row {row_index} has a missing field")
            if raw.strip().lower() in forbidden:
                raise ValueError(
                    f"CSV {path.name} row {row_index} column {column} is non-finite"
                )
    return rows


def _finite_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} is non-finite")
    return result


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} is not an integer")
    number = _finite_float(value, label)
    result = int(number)
    if number != result or result < minimum:
        raise ValueError(f"{label} is not an integer >= {minimum}")
    return result


def _normalized_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _role(value: Any, label: str) -> str:
    token = _normalized_token(value)
    if token in {"reference", "100k", "model_100k", "previous_presentation_100k_reference"}:
        return "reference"
    if token in {"candidate", "200k", "model_200k", "architecture_matched_200k_model"}:
        return "candidate"
    raise ValueError(f"{label} has an unknown model role: {value!r}")


def _feature(value: Any, label: str) -> str:
    token = _normalized_token(value)
    aliases = {
        "lp": "Lp",
        "lp_nh": "Lp",
        "ls": "Ls",
        "ls_nh": "Ls",
        "qmin": "Qmin",
        "q_min": "Qmin",
        "k": "K_abs",
        "k_abs": "K_abs",
        "abs_k": "K_abs",
    }
    if token not in aliases:
        raise ValueError(f"{label} has an unknown feature: {value!r}")
    return aliases[token]


def _model_name(row: dict[str, str], role: str, label: str) -> None:
    actual = str(row.get("model_name") or "")
    if actual != MODEL_NAMES[role]:
        raise ValueError(f"{label} model_name does not match the formal comparison name")


def _verify_manifest(
    report_dir: Path, summary: dict[str, Any]
) -> tuple[dict[str, Path], dict[str, str], dict[str, int]]:
    outputs = summary.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("REPORT_SUMMARY outputs is not an object")
    paths: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    row_counts: dict[str, int] = {}
    for filename in INPUT_FILENAMES:
        record = outputs.get(filename)
        if not isinstance(record, dict):
            raise ValueError(f"REPORT_SUMMARY does not bind {filename}")
        path = report_dir / filename
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"required report input is missing or is a symlink: {filename}")
        expected = _valid_sha256(record.get("sha256"), f"outputs.{filename}.sha256")
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"{filename} SHA-256 mismatch: {actual} != {expected}")
        paths[filename] = path
        hashes[filename] = actual
        if filename.endswith(".csv"):
            row_counts[filename] = _integer(
                record.get("row_count"),
                f"outputs.{filename}.row_count",
                minimum=1,
            )
        elif "row_count" in record:
            if _integer(record["row_count"], f"outputs.{filename}.row_count", minimum=1) != 1:
                raise ValueError(f"JSON output {filename} row_count must be 1 when declared")
    return paths, hashes, row_counts


def _comparison(summary: dict[str, Any]) -> dict[str, Any]:
    comparison = summary.get("comparison")
    if not isinstance(comparison, dict):
        raise ValueError("REPORT_SUMMARY comparison is not an object")
    if comparison.get("reference_name") != REFERENCE_NAME:
        raise ValueError("REPORT_SUMMARY reference_name is not the formal name")
    if comparison.get("candidate_name") != CANDIDATE_NAME:
        raise ValueError("REPORT_SUMMARY candidate_name is not the formal name")
    n = _integer(comparison.get("n"), "REPORT_SUMMARY comparison.n", minimum=1)
    if n != EXPECTED_N:
        raise ValueError(f"REPORT_SUMMARY comparison.n is not exactly {EXPECTED_N}")
    evidence_label = str(comparison.get("evidence_label") or "").strip()
    if "proxy-only evidence" not in evidence_label.lower():
        raise ValueError("REPORT_SUMMARY evidence_label lacks 'proxy-only evidence'")
    return {
        "reference_name": REFERENCE_NAME,
        "candidate_name": CANDIDATE_NAME,
        "n": n,
        "evidence_label": evidence_label,
    }


def _recursive_find(value: Any, names: set[str]) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if _normalized_token(key) in names:
                return child
        for child in value.values():
            found = _recursive_find(child, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _recursive_find(child, names)
            if found is not None:
                return found
    return None


def _model_records(payload: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    container = payload.get("models")
    if not isinstance(container, (dict, list)):
        # MODEL_CONTRACT_COMPARISON.json uses top-level reference/candidate
        # records, while the geometry summary uses a models mapping.
        direct = {
            key: payload[key]
            for key in ("reference", "candidate")
            if key in payload
        }
        if set(direct) == {"reference", "candidate"}:
            container = direct
    if not isinstance(container, (dict, list)):
        raise ValueError(f"{label} lacks reference/candidate model records")
    result: dict[str, dict[str, Any]] = {}
    items: Iterable[tuple[Any, Any]]
    if isinstance(container, dict):
        items = container.items()
    else:
        items = ((index, item) for index, item in enumerate(container))
    for key, item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{label} contains a non-object model record")
        declared_role = item.get("model_role", key)
        role = _role(declared_role, f"{label}.models")
        if role in result:
            raise ValueError(f"{label} contains duplicate {role} records")
        declared_name = item.get("model_name", item.get("display_name"))
        if declared_name is not None and str(declared_name) != MODEL_NAMES[role]:
            raise ValueError(f"{label} {role} model name is not formal")
        result[role] = item
    if set(result) != {"reference", "candidate"}:
        raise ValueError(f"{label} must contain exactly reference and candidate models")
    return result


def _architecture(value: Any, label: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{label} is not a nonempty architecture list")
    return tuple(_integer(item, label, minimum=1) for item in value)


def _validate_model_contract(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = _model_records(payload, "MODEL_CONTRACT_COMPARISON")
    checks = payload.get("contract_checks")
    required_checks = {
        "forward_architecture_exact_and_equal",
        "inverse_architecture_exact_and_equal",
        "decoder_exact_and_equal",
        "parameter_count_exact_and_equal",
        "prediction_summary_weights_binding_complete",
    }
    if not isinstance(checks, dict) or any(checks.get(key) is not True for key in required_checks):
        raise ValueError("MODEL_CONTRACT_COMPARISON contract checks are incomplete or not true")
    result: dict[str, dict[str, Any]] = {}
    for role, record in records.items():
        forward = _recursive_find(
            record, {"forward_architecture", "forward_layer_widths", "forward"}
        )
        inverse = _recursive_find(
            record, {"inverse_architecture", "inverse_layer_widths", "inverse"}
        )
        forward_architecture = _architecture(forward, f"{role} forward_architecture")
        inverse_architecture = _architecture(inverse, f"{role} inverse_architecture")
        if forward_architecture != EXPECTED_FORWARD_ARCHITECTURE:
            raise ValueError(f"{role} forward architecture is not exact")
        if inverse_architecture != EXPECTED_INVERSE_ARCHITECTURE:
            raise ValueError(f"{role} inverse architecture is not exact")
        decoder = str(
            _recursive_find(
                record,
                {"decoder", "projection_mode", "inverse_geometry_projection"},
            )
            or ""
        )
        if decoder != EXPECTED_DECODER:
            raise ValueError(f"{role} decoder is not exact")
        parameter_count = _integer(
            _recursive_find(record, {"total_parameters", "parameter_count"}),
            f"{role} total_parameters",
            minimum=1,
        )
        if parameter_count != EXPECTED_PARAMETER_COUNT:
            raise ValueError(f"{role} parameter count is not exact")
        training_rows = _integer(
            _recursive_find(
                record,
                {"training_rows", "training_count", "source_table_rows", "accepted_rows"},
            ),
            f"{role} training_rows",
            minimum=1,
        )
        expected_rows = 100_000 if role == "reference" else 200_000
        if training_rows != expected_rows:
            raise ValueError(f"{role} training row count is not {expected_rows}")
        result[role] = {
            "forward_architecture": forward_architecture,
            "inverse_architecture": inverse_architecture,
            "decoder": decoder,
            "parameter_count": parameter_count,
            "training_rows": training_rows,
        }
    return result


def _validate_geometry(
    payload: dict[str, Any], n: int
) -> dict[str, dict[str, float | int]]:
    if _integer(payload.get("n"), "geometry_feasibility_summary.n", minimum=1) != n:
        raise ValueError("geometry_feasibility_summary n does not match REPORT_SUMMARY")
    records = _model_records(payload, "geometry_feasibility_summary")
    result: dict[str, dict[str, float | int]] = {}
    for role, record in records.items():
        bound = _integer(
            _recursive_find(record, {"geometry_bound_violation_count", "bound_violation_count"}),
            f"{role} geometry_bound_violation_count",
        )
        topology = _integer(
            _recursive_find(
                record,
                {"topology_violation_count", "topology_violating_row_count"},
            ),
            f"{role} topology_violation_count",
        )
        duplicate = _integer(
            _recursive_find(record, {"duplicate_predicted_geometry_count", "duplicate_count"}),
            f"{role} duplicate_predicted_geometry_count",
        )
        coverage = _finite_float(
            _recursive_find(record, {"prediction_coverage", "prediction_coverage_fraction"}),
            f"{role} prediction_coverage",
        )
        if not 0.0 <= coverage <= 1.0:
            raise ValueError(f"{role} prediction_coverage is outside [0,1]")
        result[role] = {
            "geometry_bound_violation_count": bound,
            "topology_violation_count": topology,
            "duplicate_predicted_geometry_count": duplicate,
            "prediction_coverage": coverage,
        }
    return result


def _validate_runtime(payload: dict[str, Any], n: int) -> dict[str, Any]:
    if _integer(payload.get("n"), "training_runtime_summary.n", minimum=1) != n:
        raise ValueError("training_runtime_summary n does not match REPORT_SUMMARY")
    training_record = payload.get("training_runtime")
    if not isinstance(training_record, dict):
        raise ValueError("training_runtime_summary.training_runtime is not an object")
    training = _finite_float(training_record.get("seconds"), "training_runtime.seconds")
    if training < 0.0:
        raise ValueError("training_runtime.seconds is negative")

    inference_record = payload.get("inference_runtime")
    if not isinstance(inference_record, dict):
        raise ValueError("training_runtime_summary.inference_runtime is not an object")
    per_model = inference_record.get("per_model_seconds")
    if not isinstance(per_model, dict):
        raise ValueError("inference_runtime.per_model_seconds is not an object")
    inference: dict[str, float] = {}
    for declared_role, value in per_model.items():
        role = _role(declared_role, "inference_runtime.per_model_seconds")
        if role in inference:
            raise ValueError(f"duplicate {role} inference runtime")
        seconds = _finite_float(value, f"{role} inference runtime seconds")
        if seconds < 0.0:
            raise ValueError(f"{role} inference runtime is negative")
        inference[role] = seconds
    if set(inference) != {"reference", "candidate"}:
        raise ValueError("inference_runtime.per_model_seconds must contain 100k and 200k")

    pipeline_record = payload.get("evaluation_pipeline_wall_runtime")
    if not isinstance(pipeline_record, dict):
        raise ValueError("evaluation_pipeline_wall_runtime is not an object")
    pipeline = _finite_float(
        pipeline_record.get("seconds"),
        "evaluation_pipeline_wall_runtime.seconds",
    )
    if pipeline < 0.0:
        raise ValueError("evaluation_pipeline_wall_runtime.seconds is negative")
    return {
        "training_runtime_seconds": training,
        "inference_runtime_seconds": inference,
        "evaluation_pipeline_wall_runtime_seconds": pipeline,
    }


def _validate_feature_metrics(
    rows: list[dict[str, str]], n: int
) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {"reference": {}, "candidate": {}}
    numeric_columns = FEATURE_METRIC_COLUMNS[4:-1]
    for row_index, row in enumerate(rows, 1):
        role = _role(row["model_role"], f"feature_metrics row {row_index}")
        _model_name(row, role, f"feature_metrics row {row_index}")
        feature = _feature(row["feature"], f"feature_metrics row {row_index}")
        if feature in result[role]:
            raise ValueError(f"duplicate feature_metrics row for {role}/{feature}")
        if not row["unit"].strip():
            raise ValueError("feature_metrics contains an empty unit")
        values = {
            column: _finite_float(row[column], f"feature_metrics row {row_index}.{column}")
            for column in numeric_columns
        }
        count = _integer(row["count"], f"feature_metrics row {row_index}.count", minimum=1)
        if count != n:
            raise ValueError(f"feature_metrics {role}/{feature} count is not {n}")
        span = values["normalization_span"]
        if span <= 0.0:
            raise ValueError("feature_metrics normalization span is not positive")
        expected_span = {"Lp": 2.5, "Ls": 2.5, "Qmin": 20.0, "K_abs": 0.8}[feature]
        if not math.isclose(span, expected_span, rel_tol=0.0, abs_tol=1.0e-12):
            raise ValueError(f"feature_metrics {feature} normalization span is not exact")
        if not math.isclose(
            values["normalized_mae"], values["mae"] / span, rel_tol=1.0e-9, abs_tol=1.0e-12
        ) or not math.isclose(
            values["normalized_rmse"], values["rmse"] / span, rel_tol=1.0e-9, abs_tol=1.0e-12
        ):
            raise ValueError(f"feature_metrics {role}/{feature} normalization is inconsistent")
        source_sha = _valid_sha256(
            row["source_prediction_csv_sha256"],
            f"feature_metrics row {row_index}.source_prediction_csv_sha256",
        )
        result[role][feature] = {
            **values,
            "count": count,
            "unit": row["unit"].strip(),
            "source_sha256": source_sha,
        }
    if any(set(result[role]) != set(FEATURES) for role in result):
        raise ValueError("feature_metrics must contain each model x feature exactly once")
    return result


def _paired_required_columns() -> tuple[str, ...]:
    columns = ["target_id"]
    for feature in FEATURES:
        suffix = FEATURE_SUFFIX[feature]
        columns.append(f"target__{suffix}")
        for role in ("reference", "candidate"):
            columns.extend(
                (
                    f"{role}_prediction__{suffix}",
                    f"{role}_absolute_error__{suffix}",
                )
            )
    return tuple(columns)


def _validate_paired_rows(
    rows: list[dict[str, str]], n: int
) -> dict[str, dict[str, np.ndarray]]:
    if len(rows) != n:
        raise ValueError(f"per_target_paired_errors row count is not {n}")
    target_ids: set[str] = set()
    result: dict[str, dict[str, np.ndarray]] = {}
    for feature in FEATURES:
        suffix = FEATURE_SUFFIX[feature]
        target_values: list[float] = []
        reference_values: list[float] = []
        candidate_values: list[float] = []
        reference_errors: list[float] = []
        candidate_errors: list[float] = []
        for row_index, row in enumerate(rows, 1):
            target_id = row["target_id"].strip()
            if not target_id:
                raise ValueError("per_target_paired_errors contains an empty target_id")
            if feature == FEATURES[0]:
                if target_id in target_ids:
                    raise ValueError("per_target_paired_errors target_id is not unique")
                target_ids.add(target_id)
            target = _finite_float(row[f"target__{suffix}"], f"paired row {row_index} target {feature}")
            reference = _finite_float(
                row[f"reference_prediction__{suffix}"],
                f"paired row {row_index} reference prediction {feature}",
            )
            candidate = _finite_float(
                row[f"candidate_prediction__{suffix}"],
                f"paired row {row_index} candidate prediction {feature}",
            )
            reference_error = _finite_float(
                row[f"reference_absolute_error__{suffix}"],
                f"paired row {row_index} reference error {feature}",
            )
            candidate_error = _finite_float(
                row[f"candidate_absolute_error__{suffix}"],
                f"paired row {row_index} candidate error {feature}",
            )
            if not math.isclose(reference_error, abs(reference - target), rel_tol=1.0e-9, abs_tol=1.0e-12):
                raise ValueError(f"paired reference absolute error is inconsistent at row {row_index}/{feature}")
            if not math.isclose(candidate_error, abs(candidate - target), rel_tol=1.0e-9, abs_tol=1.0e-12):
                raise ValueError(f"paired candidate absolute error is inconsistent at row {row_index}/{feature}")
            target_values.append(target)
            reference_values.append(reference)
            candidate_values.append(candidate)
            reference_errors.append(reference_error)
            candidate_errors.append(candidate_error)
        result[feature] = {
            "target": np.asarray(target_values, dtype=float),
            "reference_prediction": np.asarray(reference_values, dtype=float),
            "candidate_prediction": np.asarray(candidate_values, dtype=float),
            "reference_absolute_error": np.asarray(reference_errors, dtype=float),
            "candidate_absolute_error": np.asarray(candidate_errors, dtype=float),
        }
    k_targets = result["K_abs"]["target"]
    if np.any(k_targets < 0.0) or np.any(k_targets > 0.8):
        raise ValueError("per_target_paired_errors contains a target outside 0 <= |K| <= 0.8")
    return result


def _crosscheck_feature_metrics(
    metrics: dict[str, dict[str, dict[str, Any]]],
    paired: dict[str, dict[str, np.ndarray]],
) -> None:
    for role in ("reference", "candidate"):
        for feature in FEATURES:
            values = paired[feature]
            target = values["target"]
            prediction = values[f"{role}_prediction"]
            absolute = values[f"{role}_absolute_error"]
            signed = prediction - target
            expected = {
                "bias": float(np.mean(signed)),
                "mae": float(np.mean(absolute)),
                "rmse": float(np.sqrt(np.mean(signed**2))),
                "median_absolute_error": float(np.median(absolute)),
                "p90_absolute_error": float(np.percentile(absolute, 90.0)),
                "p95_absolute_error": float(np.percentile(absolute, 95.0)),
                "p99_absolute_error": float(np.percentile(absolute, 99.0)),
                "maximum_absolute_error": float(np.max(absolute)),
            }
            for key, expected_value in expected.items():
                actual = float(metrics[role][feature][key])
                if not math.isclose(actual, expected_value, rel_tol=1.0e-8, abs_tol=1.0e-11):
                    raise ValueError(f"feature_metrics {role}/{feature}/{key} disagrees with paired rows")


def _validate_joint_metrics(
    rows: list[dict[str, str]], n: int
) -> dict[str, Any]:
    success: dict[str, list[tuple[float, float, str]]] = {"reference": [], "candidate": []}
    metric_values: dict[str, dict[str, float]] = {
        "reference": {},
        "candidate": {},
        "both": {},
    }
    joint_found = {
        "reference": {"mae": False, "rmse": False},
        "candidate": {"mae": False, "rmse": False},
    }
    for row_index, row in enumerate(rows, 1):
        value = _finite_float(row["value"], f"joint_metrics row {row_index}.value")
        if _integer(row["n"], f"joint_metrics row {row_index}.n", minimum=1) != n:
            raise ValueError(f"joint_metrics row {row_index} n is not {n}")
        if not row["unit"].strip() or not row["definition"].strip():
            raise ValueError("joint_metrics contains an empty unit or definition")
        source_sha = _valid_sha256(
            row["source_csv_sha256"], f"joint_metrics row {row_index}.source_csv_sha256"
        )
        combined = _normalized_token(f"{row['section']} {row['metric']}")
        if _normalized_token(row["model_role"]) == "both":
            if (
                _normalized_token(row["section"]) != "runtime"
                or _normalized_token(row["metric"])
                != "evaluation_pipeline_wall_runtime_seconds"
                or row["model_name"] != "two-model evaluation pipeline"
                or row["tolerance_normalized"].strip()
            ):
                raise ValueError("joint_metrics 'both' row is not the evaluation pipeline runtime")
            if value < 0.0:
                raise ValueError("joint_metrics evaluation pipeline runtime is negative")
            if "evaluation_pipeline_wall_runtime_seconds" in metric_values["both"]:
                raise ValueError("joint_metrics duplicates the evaluation pipeline runtime")
            metric_values["both"]["evaluation_pipeline_wall_runtime_seconds"] = value
            continue
        role = _role(row["model_role"], f"joint_metrics row {row_index}")
        _model_name(row, role, f"joint_metrics row {row_index}")
        if "joint" in combined and "normalized" in combined:
            if "rmse" in combined:
                joint_found[role]["rmse"] = True
            elif "mae" in combined:
                joint_found[role]["mae"] = True
        tolerance_raw = row["tolerance_normalized"].strip()
        if tolerance_raw:
            tolerance = _finite_float(tolerance_raw, f"joint_metrics row {row_index}.tolerance")
            if "success" not in combined:
                raise ValueError("joint_metrics tolerance row is not labeled as success rate")
            if not 0.0 <= tolerance <= 0.25:
                raise ValueError("success-rate tolerance is outside [0,0.25]")
            if not 0.0 <= value <= 1.0:
                raise ValueError("success-rate value is outside [0,1]")
            success[role].append((tolerance, value, source_sha))
        else:
            metric = _normalized_token(row["metric"])
            if metric in metric_values[role]:
                raise ValueError(f"joint_metrics duplicates {role}/{metric}")
            metric_values[role][metric] = value
    if not all(all(values.values()) for values in joint_found.values()):
        raise ValueError("joint_metrics lacks joint normalized MAE/RMSE for both models")
    for role in success:
        success[role].sort()
        if len(success[role]) < 2:
            raise ValueError(f"joint_metrics lacks a success-rate sweep for {role}")
        tolerances = [item[0] for item in success[role]]
        rates = [item[1] for item in success[role]]
        if len(tolerances) != len(set(tolerances)):
            raise ValueError(f"joint_metrics success tolerances are duplicated for {role}")
        if not math.isclose(tolerances[0], 0.0, abs_tol=1.0e-12) or not math.isclose(
            tolerances[-1], 0.25, abs_tol=1.0e-12
        ):
            raise ValueError("success-rate sweep must span exactly 0 through 0.25")
        if any(later + 1.0e-12 < earlier for earlier, later in zip(rates, rates[1:])):
            raise ValueError("success-rate sweep is not nondecreasing")
    reference_tolerances = np.asarray([item[0] for item in success["reference"]])
    candidate_tolerances = np.asarray([item[0] for item in success["candidate"]])
    if reference_tolerances.shape != candidate_tolerances.shape or not np.allclose(
        reference_tolerances, candidate_tolerances, rtol=0.0, atol=1.0e-12
    ):
        raise ValueError("success-rate sweeps do not share the same tolerance frame")
    return {"success": success, "metric_values": metric_values}


def _crosscheck_joint_summaries(
    joint: dict[str, Any],
    model_contract: dict[str, dict[str, Any]],
    geometry: dict[str, dict[str, float | int]],
    runtime: dict[str, Any],
) -> None:
    values = joint["metric_values"]

    def check(role: str, metric: str, expected: float) -> None:
        if metric not in values[role]:
            raise ValueError(f"joint_metrics lacks {role}/{metric}")
        if not math.isclose(
            float(values[role][metric]),
            float(expected),
            rel_tol=1.0e-9,
            abs_tol=1.0e-11,
        ):
            raise ValueError(f"joint_metrics {role}/{metric} disagrees with its JSON summary")

    for role in ("reference", "candidate"):
        check(role, "source_table_rows", float(model_contract[role]["training_rows"]))
        check(role, "parameter_count", float(model_contract[role]["parameter_count"]))
        for metric in (
            "geometry_bound_violation_count",
            "topology_violation_count",
            "duplicate_predicted_geometry_count",
            "prediction_coverage",
        ):
            check(role, metric, float(geometry[role][metric]))
        check(
            role,
            "inference_runtime_seconds",
            float(runtime["inference_runtime_seconds"][role]),
        )
    check("candidate", "training_runtime_seconds", float(runtime["training_runtime_seconds"]))
    check(
        "both",
        "evaluation_pipeline_wall_runtime_seconds",
        float(runtime["evaluation_pipeline_wall_runtime_seconds"]),
    )


def _validate_curves(rows: list[dict[str, str]], n: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    roles: set[str] = set()
    stages_by_role: dict[str, set[str]] = {"reference": set(), "candidate": set()}
    for row_index, row in enumerate(rows, 1):
        role = _role(row["model_role"], f"training_curves row {row_index}")
        _model_name(row, role, f"training_curves row {row_index}")
        stage = row["stage"].strip()
        if not stage or not row["series"].strip() or not row["metric"].strip() or not row["unit"].strip():
            raise ValueError("training_curves contains an empty categorical field")
        x_value = _finite_float(row["x_value"], f"training_curves row {row_index}.x_value")
        value = _finite_float(row["value"], f"training_curves row {row_index}.value")
        curve_n = _integer(row["n"], f"training_curves row {row_index}.n", minimum=1)
        if curve_n != n:
            raise ValueError(f"training_curves row {row_index} n is not {n}")
        source_sha = _valid_sha256(
            row["source_history_csv_sha256"],
            f"training_curves row {row_index}.source_history_csv_sha256",
        )
        roles.add(role)
        stages_by_role[role].add(_normalized_token(stage))
        result.append(
            {
                "role": role,
                "stage": stage,
                "x_value": x_value,
                "series": row["series"].strip(),
                "metric": row["metric"].strip(),
                "value": value,
                "unit": row["unit"].strip(),
                "n": curve_n,
                "source_sha256": source_sha,
            }
        )
    if roles != {"reference", "candidate"}:
        raise ValueError("training_curves must contain both models")
    for role, stages in stages_by_role.items():
        if not any("forward" in stage for stage in stages) or not any(
            "inverse" in stage for stage in stages
        ):
            raise ValueError(f"training_curves must contain forward and inverse stages for {role}")
    return result


def _collect_csv_hashes(value: Any) -> list[str]:
    hashes: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            token = _normalized_token(key)
            if "csv" in token and "sha256" in token and isinstance(child, str):
                try:
                    digest = _valid_sha256(child, key)
                except ValueError:
                    continue
                if digest not in hashes:
                    hashes.append(digest)
            else:
                for digest in _collect_csv_hashes(child):
                    if digest not in hashes:
                        hashes.append(digest)
    elif isinstance(value, list):
        for child in value:
            for digest in _collect_csv_hashes(child):
                if digest not in hashes:
                    hashes.append(digest)
    return hashes


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _configure_matplotlib() -> Any:
    import matplotlib

    matplotlib.use("Agg", force=True)
    matplotlib.rcParams.update(
        {
            "svg.fonttype": "none",
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "legend.fontsize": 7,
            "figure.facecolor": "white",
            "axes.facecolor": "#FAFAFA",
            "axes.grid": True,
            "grid.alpha": 0.22,
        }
    )
    # Register a CJK-capable font when the host provides one, while keeping a
    # portable sans-serif fallback for CI images.
    from matplotlib import font_manager

    candidates = (
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    )
    for candidate in candidates:
        if candidate.is_file():
            try:
                font_manager.fontManager.addfont(str(candidate))
                family = font_manager.FontProperties(fname=str(candidate)).get_name()
                matplotlib.rcParams["font.family"] = [family, "DejaVu Sans", "sans-serif"]
                break
            except (OSError, RuntimeError, ValueError):
                continue
    import matplotlib.pyplot as plt

    return plt


def _footnote(
    comparison: dict[str, Any], definition: str, units: str, source_hashes: Iterable[str]
) -> str:
    hashes = _unique(source_hashes)
    if not hashes:
        raise ValueError("a figure has no source CSV SHA256")
    return (
        f"{comparison['reference_name']} vs. {comparison['candidate_name']} | "
        f"n={comparison['n']} | proxy-only evidence | 指标定义={definition} | "
        f"单位={units} | 源CSV SHA256={' ; '.join(hashes)}"
    )


def _save_figure(
    plt: Any,
    fig: Any,
    stage_dir: Path,
    basename: str,
    title: str,
    footnote: str,
) -> None:
    if basename not in FIGURE_BASENAMES:
        raise ValueError(f"unknown formal figure basename: {basename}")
    wrapped = "\n".join(
        textwrap.wrap(
            footnote,
            width=155,
            break_long_words=False,
            break_on_hyphens=False,
        )
    )
    fig.suptitle(title, fontsize=13, fontweight="bold", y=0.985)
    fig.text(0.01, 0.012, wrapped, ha="left", va="bottom", fontsize=5.7, color="#303030")
    line_count = max(1, wrapped.count("\n") + 1)
    bottom = min(0.29, 0.075 + 0.031 * line_count)
    fig.tight_layout(rect=(0.0, bottom, 1.0, 0.955))
    png = stage_dir / f"{basename}.png"
    svg = stage_dir / f"{basename}.svg"
    creator = "render_architecture_matched_fixed8k_report.py"
    fig.savefig(
        png,
        dpi=300,
        facecolor="white",
        metadata={"Title": title, "Description": footnote, "Creator": creator},
    )
    fig.savefig(
        svg,
        facecolor="white",
        metadata={
            "Title": title,
            "Description": footnote,
            "Creator": creator,
            "Date": None,
        },
    )
    plt.close(fig)


def _plot_architecture(
    plt: Any,
    stage_dir: Path,
    comparison: dict[str, Any],
    model_contract: dict[str, dict[str, Any]],
    sources: list[str],
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 7.2))
    axes[0].axis("off")
    axes[0].set_title("Exact model contract")
    y = 0.92
    for role in ("reference", "candidate"):
        record = model_contract[role]
        axes[0].text(
            0.02,
            y,
            MODEL_NAMES[role],
            color=MODEL_COLORS[role],
            fontsize=11,
            fontweight="bold",
            transform=axes[0].transAxes,
        )
        y -= 0.09
        lines = (
            "Forward: " + " → ".join(str(value) for value in record["forward_architecture"]),
            "Inverse: " + " → ".join(str(value) for value in record["inverse_architecture"]),
            f"Decoder: {record['decoder']}",
            f"Total parameters: {record['parameter_count']:,}",
        )
        axes[0].text(
            0.05,
            y,
            "\n".join(lines),
            va="top",
            linespacing=1.5,
            transform=axes[0].transAxes,
        )
        y -= 0.32
    roles = ("reference", "candidate")
    counts = [model_contract[role]["training_rows"] for role in roles]
    bars = axes[1].bar(
        [0, 1],
        np.asarray(counts, dtype=float) / 1000.0,
        color=[MODEL_COLORS[role] for role in roles],
        width=0.58,
    )
    axes[1].set_xticks([0, 1], ["100k reference", "200k model"])
    axes[1].set_ylabel("Training source rows (thousands)")
    axes[1].set_title("Data counts")
    for bar, count in zip(bars, counts):
        axes[1].text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{count:,}",
            ha="center",
            va="bottom",
            fontweight="bold",
        )
    _save_figure(
        plt,
        fig,
        stage_dir,
        FIGURE_BASENAMES[0],
        "Model architecture and data counts",
        _footnote(
            comparison,
            "Declared layer widths, hard-feasible decoder, parameter count, and accepted training rows",
            "layer widths; parameters; rows",
            sources,
        ),
    )


def _plot_training_curves(
    plt: Any,
    stage_dir: Path,
    comparison: dict[str, Any],
    curves: list[dict[str, Any]],
    source_sha: str,
) -> None:
    stage_groups = {
        "Forward proxy": [row for row in curves if "forward" in _normalized_token(row["stage"])],
        "Tandem inverse": [row for row in curves if "inverse" in _normalized_token(row["stage"])],
    }
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 7.2))
    for axis, (stage_label, rows) in zip(axes, stage_groups.items()):
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault((row["role"], row["series"], row["metric"]), []).append(row)
        for (role, series, metric), group in sorted(groups.items()):
            group.sort(key=lambda item: item["x_value"])
            linestyle = "--" if "val" in series.lower() else "-"
            axis.plot(
                [item["x_value"] for item in group],
                [item["value"] for item in group],
                color=MODEL_COLORS[role],
                linestyle=linestyle,
                marker="o",
                markersize=3,
                linewidth=1.5,
                label=f"{role}: {series} / {metric}",
            )
        axis.set_title(stage_label)
        axis.set_xlabel("Recorded optimizer update / epoch index")
        axis.set_ylabel("Recorded metric value")
        axis.legend(loc="best")
    _save_figure(
        plt,
        fig,
        stage_dir,
        FIGURE_BASENAMES[1],
        "Training curves",
        _footnote(
            comparison,
            "Recorded train/validation series versus the stored training progress coordinate; unavailable series are not inferred",
            "; ".join(sorted({row["unit"] for row in curves})),
            [source_sha],
        ),
    )


def _plot_feature_metric(
    plt: Any,
    stage_dir: Path,
    comparison: dict[str, Any],
    metrics: dict[str, dict[str, dict[str, Any]]],
    field: str,
    basename: str,
    title: str,
    definition: str,
    source_sha: str,
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(11.8, 8.3))
    for axis, feature in zip(axes.flat, FEATURES):
        values = [metrics[role][feature][field] for role in ("reference", "candidate")]
        bars = axis.bar(
            [0, 1],
            values,
            color=[MODEL_COLORS[role] for role in ("reference", "candidate")],
            width=0.58,
        )
        axis.set_xticks([0, 1], ["100k ref.", "200k model"])
        axis.set_title(FEATURE_DISPLAY[feature])
        axis.set_ylabel(metrics["reference"][feature]["unit"])
        for bar, value in zip(bars, values):
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f"{value:.4g}",
                ha="center",
                va="bottom",
                fontsize=8,
            )
    _save_figure(
        plt,
        fig,
        stage_dir,
        basename,
        title,
        _footnote(
            comparison,
            definition,
            "; ".join(
                f"{FEATURE_DISPLAY[feature]}={metrics['reference'][feature]['unit']}"
                for feature in FEATURES
            ),
            [source_sha],
        ),
    )


def _plot_target_vs_prediction(
    plt: Any,
    stage_dir: Path,
    comparison: dict[str, Any],
    paired: dict[str, dict[str, np.ndarray]],
    per_target_sha: str,
    metrics: dict[str, dict[str, dict[str, Any]]],
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(11.8, 8.5))
    for axis, feature in zip(axes.flat, FEATURES):
        values = paired[feature]
        target = values["target"]
        for role in ("reference", "candidate"):
            axis.scatter(
                target,
                values[f"{role}_prediction"],
                s=10,
                alpha=0.42,
                color=MODEL_COLORS[role],
                edgecolors="none",
                label="100k reference" if role == "reference" else "200k model",
            )
        low = float(min(np.min(target), np.min(values["reference_prediction"]), np.min(values["candidate_prediction"])))
        high = float(max(np.max(target), np.max(values["reference_prediction"]), np.max(values["candidate_prediction"])))
        axis.plot([low, high], [low, high], color="#222222", linewidth=1.0, linestyle=":")
        unit = metrics["reference"][feature]["unit"]
        axis.set_title(FEATURE_DISPLAY[feature])
        axis.set_xlabel(f"Target ({unit})")
        axis.set_ylabel(f"Proxy prediction ({unit})")
        axis.legend(loc="best")
    _save_figure(
        plt,
        fig,
        stage_dir,
        FIGURE_BASENAMES[4],
        "Target vs. prediction",
        _footnote(
            comparison,
            "Frozen target value versus each model's own-forward-proxy prediction; dotted line is prediction=target",
            "; ".join(
                f"{FEATURE_DISPLAY[feature]}={metrics['reference'][feature]['unit']}"
                for feature in FEATURES
            ),
            [per_target_sha],
        ),
    )


def _plot_error_cdf(
    plt: Any,
    stage_dir: Path,
    comparison: dict[str, Any],
    paired: dict[str, dict[str, np.ndarray]],
    per_target_sha: str,
    metrics: dict[str, dict[str, dict[str, Any]]],
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(11.8, 8.5))
    for axis, feature in zip(axes.flat, FEATURES):
        for role in ("reference", "candidate"):
            errors = np.sort(paired[feature][f"{role}_absolute_error"])
            cdf = np.arange(1, len(errors) + 1, dtype=float) / float(len(errors))
            axis.plot(
                errors,
                cdf,
                color=MODEL_COLORS[role],
                linewidth=1.7,
                label="100k reference" if role == "reference" else "200k model",
            )
        axis.set_title(FEATURE_DISPLAY[feature])
        axis.set_xlabel(f"Absolute error ({metrics['reference'][feature]['unit']})")
        axis.set_ylabel("Empirical cumulative fraction")
        axis.set_ylim(0.0, 1.01)
        axis.legend(loc="lower right")
    _save_figure(
        plt,
        fig,
        stage_dir,
        FIGURE_BASENAMES[5],
        "Absolute-error empirical CDF",
        _footnote(
            comparison,
            "Empirical CDF F(e)=count(absolute error <= e)/n on the same paired legacy target frame",
            "; ".join(
                f"{FEATURE_DISPLAY[feature]}={metrics['reference'][feature]['unit']}; CDF=fraction"
                for feature in FEATURES
            ),
            [per_target_sha],
        ),
    )


def _plot_tail_error(
    plt: Any,
    stage_dir: Path,
    comparison: dict[str, Any],
    metrics: dict[str, dict[str, dict[str, Any]]],
    source_sha: str,
) -> None:
    quantiles = (
        ("P50", "median_absolute_error"),
        ("P90", "p90_absolute_error"),
        ("P95", "p95_absolute_error"),
    )
    fig, axes = plt.subplots(2, 2, figsize=(11.8, 8.5))
    for axis, feature in zip(axes.flat, FEATURES):
        x = np.arange(len(quantiles), dtype=float)
        width = 0.36
        for offset, role in ((-width / 2, "reference"), (width / 2, "candidate")):
            axis.bar(
                x + offset,
                [metrics[role][feature][field] for _, field in quantiles],
                width=width,
                color=MODEL_COLORS[role],
                label="100k reference" if role == "reference" else "200k model",
            )
        axis.set_xticks(x, [label for label, _ in quantiles])
        axis.set_title(FEATURE_DISPLAY[feature])
        axis.set_ylabel(metrics["reference"][feature]["unit"])
        axis.legend(loc="best")
    _save_figure(
        plt,
        fig,
        stage_dir,
        FIGURE_BASENAMES[6],
        "P50 / P90 / P95 absolute tail error",
        _footnote(
            comparison,
            "Linear-interpolated percentiles of per-target absolute proxy error",
            "; ".join(
                f"{FEATURE_DISPLAY[feature]}={metrics['reference'][feature]['unit']}"
                for feature in FEATURES
            ),
            [source_sha],
        ),
    )


def _plot_q_shortfall(
    plt: Any,
    stage_dir: Path,
    comparison: dict[str, Any],
    paired: dict[str, dict[str, np.ndarray]],
    per_target_sha: str,
    q_unit: str,
) -> None:
    target = paired["Qmin"]["target"]
    met: dict[str, float] = {}
    shortfalls: dict[str, np.ndarray] = {}
    for role in ("reference", "candidate"):
        prediction = paired["Qmin"][f"{role}_prediction"]
        met[role] = float(np.mean(prediction >= target))
        shortfalls[role] = np.maximum(target - prediction, 0.0)
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 7.2))
    roles = ("reference", "candidate")
    bars = axes[0].bar(
        [0, 1],
        [met[role] for role in roles],
        color=[MODEL_COLORS[role] for role in roles],
        width=0.58,
    )
    axes[0].set_xticks([0, 1], ["100k ref.", "200k model"])
    axes[0].set_ylim(0.0, 1.0)
    axes[0].set_ylabel("Fraction")
    axes[0].set_title("Q target-met fraction")
    for bar, value in zip(bars, [met[role] for role in roles]):
        axes[0].text(bar.get_x() + bar.get_width() / 2, value, f"{value:.1%}", ha="center", va="bottom")
    labels = ("MAE", "RMSE", "P90", "P95")
    x = np.arange(len(labels), dtype=float)
    width = 0.36
    for offset, role in ((-width / 2, "reference"), (width / 2, "candidate")):
        values = shortfalls[role]
        summary = (
            float(np.mean(values)),
            float(np.sqrt(np.mean(values**2))),
            float(np.percentile(values, 90.0)),
            float(np.percentile(values, 95.0)),
        )
        axes[1].bar(
            x + offset,
            summary,
            width=width,
            color=MODEL_COLORS[role],
            label="100k reference" if role == "reference" else "200k model",
        )
    axes[1].set_xticks(x, labels)
    axes[1].set_ylabel(q_unit)
    axes[1].set_title("One-sided Q shortfall")
    axes[1].legend(loc="best")
    _save_figure(
        plt,
        fig,
        stage_dir,
        FIGURE_BASENAMES[7],
        "Q target met and one-sided shortfall",
        _footnote(
            comparison,
            "Q target met iff predicted Qmin >= target Qmin; shortfall=max(target Qmin-predicted Qmin,0)",
            f"target-met=fraction; shortfall={q_unit}",
            [per_target_sha],
        ),
    )


def _plot_success_sweep(
    plt: Any,
    stage_dir: Path,
    comparison: dict[str, Any],
    joint: dict[str, Any],
    source_sha: str,
) -> None:
    fig, axis = plt.subplots(figsize=(11.8, 7.2))
    for role in ("reference", "candidate"):
        rows = joint["success"][role]
        axis.plot(
            [item[0] for item in rows],
            [item[1] for item in rows],
            color=MODEL_COLORS[role],
            linewidth=2.0,
            marker="o",
            markersize=3,
            label=MODEL_NAMES[role],
        )
    axis.set_xlim(0.0, 0.25)
    axis.set_ylim(0.0, 1.0)
    axis.set_xlabel("Normalized tolerance")
    axis.set_ylabel("Success rate (fraction)")
    axis.set_title("Descriptive fixed-frame sensitivity curve")
    axis.legend(loc="lower right")
    _save_figure(
        plt,
        fig,
        stage_dir,
        FIGURE_BASENAMES[8],
        "Success rate vs. normalized tolerance",
        _footnote(
            comparison,
            "Descriptive success-rate sensitivity over the fixed normalized tolerance sweep 0 to 0.25",
            "tolerance=normalized fraction; success rate=fraction",
            [source_sha],
        ),
    )


def _plot_feasibility_runtime(
    plt: Any,
    stage_dir: Path,
    comparison: dict[str, Any],
    geometry: dict[str, dict[str, float | int]],
    runtime: dict[str, Any],
    sources: list[str],
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12.0, 8.4))
    roles = ("reference", "candidate")
    labels = ("Bound", "Topology", "Duplicate")
    fields = (
        "geometry_bound_violation_count",
        "topology_violation_count",
        "duplicate_predicted_geometry_count",
    )
    x = np.arange(len(labels), dtype=float)
    width = 0.36
    for offset, role in ((-width / 2, "reference"), (width / 2, "candidate")):
        axes[0, 0].bar(
            x + offset,
            [geometry[role][field] for field in fields],
            width=width,
            color=MODEL_COLORS[role],
            label="100k reference" if role == "reference" else "200k model",
        )
    axes[0, 0].set_xticks(x, labels)
    axes[0, 0].set_ylabel("Count")
    axes[0, 0].set_title("Geometry feasibility findings")
    axes[0, 0].legend(loc="best")

    coverage = [float(geometry[role]["prediction_coverage"]) for role in roles]
    bars = axes[0, 1].bar(
        [0, 1], coverage, color=[MODEL_COLORS[role] for role in roles], width=0.58
    )
    axes[0, 1].set_xticks([0, 1], ["100k ref.", "200k model"])
    axes[0, 1].set_ylim(0.0, 1.0)
    axes[0, 1].set_ylabel("Fraction")
    axes[0, 1].set_title("Prediction coverage")
    for bar, value in zip(bars, coverage):
        axes[0, 1].text(bar.get_x() + bar.get_width() / 2, value, f"{value:.2%}", ha="center", va="bottom")

    inference = runtime["inference_runtime_seconds"]
    bars = axes[1, 0].bar(
        [0, 1],
        [inference[role] for role in roles],
        color=[MODEL_COLORS[role] for role in roles],
        width=0.58,
    )
    axes[1, 0].set_xticks([0, 1], ["100k ref.", "200k model"])
    axes[1, 0].set_ylabel("Seconds")
    axes[1, 0].set_title("Single-pass inference runtime")
    for bar, value in zip(bars, [inference[role] for role in roles]):
        axes[1, 0].text(bar.get_x() + bar.get_width() / 2, value, f"{value:.4g}s", ha="center", va="bottom")

    training = float(runtime["training_runtime_seconds"])
    evaluation = float(runtime["evaluation_pipeline_wall_runtime_seconds"])
    runtime_values = [training, evaluation]
    axes[1, 1].barh(
        [0, 1],
        runtime_values,
        color=[MODEL_COLORS["candidate"], "#777777"],
        height=0.48,
    )
    axes[1, 1].set_yticks([0, 1], ["200k training", "Evaluation pipeline"])
    axes[1, 1].set_xlabel("Seconds")
    axes[1, 1].set_title("Receipt-bound wall runtimes")
    runtime_scale = max(runtime_values) if max(runtime_values) > 0.0 else 1.0
    axes[1, 1].set_xlim(0.0, runtime_scale * 1.12)
    for position, value in enumerate(runtime_values):
        axes[1, 1].text(
            value + runtime_scale * 0.01,
            position,
            f"{value:.4g}s",
            va="center",
            ha="left",
        )
    _save_figure(
        plt,
        fig,
        stage_dir,
        FIGURE_BASENAMES[9],
        "Geometry feasibility, coverage, and runtime",
        _footnote(
            comparison,
            "Counts use audited predicted geometries; coverage is valid predictions/n; runtimes use declared inference and receipt-bound wall-time definitions",
            "violations=count; coverage=fraction; runtime=seconds",
            sources,
        ),
    )


def _render_all(
    stage_dir: Path,
    comparison: dict[str, Any],
    data: dict[str, Any],
) -> None:
    plt = _configure_matplotlib()
    try:
        _plot_architecture(
            plt,
            stage_dir,
            comparison,
            data["model_contract"],
            [data["hashes"]["joint_metrics.csv"]],
        )
        _plot_training_curves(
            plt,
            stage_dir,
            comparison,
            data["curves"],
            data["hashes"]["training_curves_long.csv"],
        )
        _plot_feature_metric(
            plt,
            stage_dir,
            comparison,
            data["feature_metrics"],
            "mae",
            FIGURE_BASENAMES[2],
            "Feature MAE comparison",
            "MAE=mean absolute proxy prediction error for each physical target feature",
            data["hashes"]["feature_metrics_long.csv"],
        )
        _plot_feature_metric(
            plt,
            stage_dir,
            comparison,
            data["feature_metrics"],
            "rmse",
            FIGURE_BASENAMES[3],
            "Feature RMSE comparison",
            "RMSE=square root of the mean squared proxy prediction error for each target feature",
            data["hashes"]["feature_metrics_long.csv"],
        )
        _plot_target_vs_prediction(
            plt,
            stage_dir,
            comparison,
            data["paired"],
            data["hashes"]["per_target_paired_errors.csv"],
            data["feature_metrics"],
        )
        _plot_error_cdf(
            plt,
            stage_dir,
            comparison,
            data["paired"],
            data["hashes"]["per_target_paired_errors.csv"],
            data["feature_metrics"],
        )
        _plot_tail_error(
            plt,
            stage_dir,
            comparison,
            data["feature_metrics"],
            data["hashes"]["feature_metrics_long.csv"],
        )
        _plot_q_shortfall(
            plt,
            stage_dir,
            comparison,
            data["paired"],
            data["hashes"]["per_target_paired_errors.csv"],
            data["feature_metrics"]["reference"]["Qmin"]["unit"],
        )
        _plot_success_sweep(
            plt,
            stage_dir,
            comparison,
            data["joint"],
            data["hashes"]["joint_metrics.csv"],
        )
        _plot_feasibility_runtime(
            plt,
            stage_dir,
            comparison,
            data["geometry"],
            data["runtime"],
            [data["hashes"]["joint_metrics.csv"]],
        )
    except Exception:
        plt.close("all")
        raise


def _load_and_validate(report_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    summary_path = report_dir / "REPORT_SUMMARY.json"
    if not summary_path.is_file() or summary_path.is_symlink():
        raise ValueError("REPORT_SUMMARY.json is missing or is a symlink")
    summary = _read_json(summary_path)
    comparison = _comparison(summary)
    paths, hashes, declared_rows = _verify_manifest(report_dir, summary)

    model_payload = _read_json(paths["MODEL_CONTRACT_COMPARISON.json"])
    geometry_payload = _read_json(paths["geometry_feasibility_summary.json"])
    runtime_payload = _read_json(paths["training_runtime_summary.json"])
    feature_rows = _read_csv(paths["feature_metrics_long.csv"], FEATURE_METRIC_COLUMNS)
    joint_rows = _read_csv(paths["joint_metrics.csv"], JOINT_METRIC_COLUMNS)
    paired_rows = _read_csv(paths["per_target_paired_errors.csv"], _paired_required_columns())
    curve_rows = _read_csv(paths["training_curves_long.csv"], TRAINING_CURVE_COLUMNS)
    loaded_csv = {
        "feature_metrics_long.csv": feature_rows,
        "joint_metrics.csv": joint_rows,
        "per_target_paired_errors.csv": paired_rows,
        "training_curves_long.csv": curve_rows,
    }
    for filename, rows in loaded_csv.items():
        if len(rows) != declared_rows[filename]:
            raise ValueError(
                f"{filename} row count mismatch: {len(rows)} != {declared_rows[filename]}"
            )

    feature_metrics = _validate_feature_metrics(feature_rows, comparison["n"])
    paired = _validate_paired_rows(paired_rows, comparison["n"])
    _crosscheck_feature_metrics(feature_metrics, paired)
    joint = _validate_joint_metrics(joint_rows, comparison["n"])
    curves = _validate_curves(curve_rows, comparison["n"])
    model_contract = _validate_model_contract(model_payload)
    geometry = _validate_geometry(geometry_payload, comparison["n"])
    runtime = _validate_runtime(runtime_payload, comparison["n"])
    _crosscheck_joint_summaries(joint, model_contract, geometry, runtime)
    data = {
        "hashes": hashes,
        "model_contract": model_contract,
        "geometry": geometry,
        "runtime": runtime,
        "feature_metrics": feature_metrics,
        "paired": paired,
        "joint": joint,
        "curves": curves,
    }
    return comparison, data


def render_report(report_dir: Path) -> dict[str, Any]:
    """Validate a statistics report and atomically add its formal figures."""

    report_dir = Path(report_dir).expanduser().resolve()
    if not report_dir.is_dir() or report_dir.is_symlink():
        raise ValueError(f"report-dir is not a regular directory: {report_dir}")
    figures_dir = report_dir / "figures"
    if figures_dir.exists() or figures_dir.is_symlink():
        raise FileExistsError(f"no-clobber figures directory already exists: {figures_dir}")

    # No directory is created until all input hashes, schemas, counts, and
    # finite-value checks have passed.
    comparison, data = _load_and_validate(report_dir)
    publish_lock = report_dir / ".figures.publish.lock"
    try:
        with publish_lock.open("x", encoding="ascii", newline="\n") as handle:
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise FileExistsError(
            f"no-clobber figure publication lock already exists: {publish_lock}"
        ) from exc
    stage_path: Path | None = None
    try:
        if figures_dir.exists() or figures_dir.is_symlink():
            raise FileExistsError(f"no-clobber figures directory appeared: {figures_dir}")
        stage_path = Path(tempfile.mkdtemp(prefix=".figures.staging.", dir=report_dir))
        _render_all(stage_path, comparison, data)
        expected = {
            f"{basename}.{suffix}"
            for basename in FIGURE_BASENAMES
            for suffix in ("png", "svg")
        }
        actual = {path.name for path in stage_path.iterdir() if path.is_file()}
        if actual != expected:
            raise RuntimeError(
                f"rendered figure set is not exact: missing={sorted(expected-actual)} extra={sorted(actual-expected)}"
            )
        if any((stage_path / name).stat().st_size <= 0 for name in expected):
            raise RuntimeError("one or more rendered figures are empty")
        if figures_dir.exists() or figures_dir.is_symlink():
            raise FileExistsError(f"no-clobber figures directory appeared: {figures_dir}")
        stage_path.rename(figures_dir)
    except Exception:
        if stage_path is not None:
            shutil.rmtree(stage_path, ignore_errors=True)
        raise
    finally:
        if publish_lock.exists() and publish_lock.parent == report_dir:
            publish_lock.unlink()
    return {
        "status": "PASS",
        "figure_count": len(expected),
        "figures_dir": str(figures_dir),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = render_report(Path(args.report_dir))
    figures_dir = result["figures_dir"]
    print(f"figures={figures_dir}")
    print(f"figure_count={result['figure_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
