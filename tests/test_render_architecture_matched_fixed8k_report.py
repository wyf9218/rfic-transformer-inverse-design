from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import struct
import sys
import uuid
from pathlib import Path

import numpy as np
import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "render_architecture_matched_fixed8k_report.py"
REFERENCE_NAME = "Previous-presentation 100k reference"
CANDIDATE_NAME = "architecture-matched 200k model"
FEATURES = ("Lp", "Ls", "Qmin", "K_abs")
SUFFIX = {"Lp": "lp", "Ls": "ls", "Qmin": "qmin", "K_abs": "k_abs"}
SPANS = {"Lp": 2.5, "Ls": 2.5, "Qmin": 20.0, "K_abs": 0.8}
UNITS = {"Lp": "nH", "Ls": "nH", "Qmin": "1", "K_abs": "1"}


def _load_module():
    name = f"render_architecture_matched_fixed8k_report_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    assert rows
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _paired_fixture(n: int) -> tuple[list[dict[str, object]], dict[str, dict[str, np.ndarray]]]:
    rows: list[dict[str, object]] = []
    arrays: dict[str, dict[str, list[float]]] = {
        feature: {
            "target": [],
            "reference_prediction": [],
            "candidate_prediction": [],
        }
        for feature in FEATURES
    }
    reference_scale = {"Lp": 0.11, "Ls": 0.13, "Qmin": 1.25, "K_abs": 0.045}
    candidate_scale = {"Lp": 0.065, "Ls": 0.075, "Qmin": 0.70, "K_abs": 0.025}
    for index in range(n):
        targets = {
            "Lp": 0.8 + 0.18 * index,
            "Ls": 1.0 + 0.15 * index,
            "Qmin": 8.0 + 1.2 * index,
            "K_abs": 0.10 + 0.08 * index,
        }
        row: dict[str, object] = {"target_id": f"synthetic_target_{index:04d}"}
        for feature_index, feature in enumerate(FEATURES):
            sign_reference = -1.0 if (index + feature_index) % 2 == 0 else 1.0
            sign_candidate = -1.0 if (index + 2 * feature_index) % 3 == 0 else 1.0
            reference = targets[feature] + sign_reference * reference_scale[feature] * (
                1.0 + 0.05 * index
            )
            candidate = targets[feature] + sign_candidate * candidate_scale[feature] * (
                1.0 + 0.03 * index
            )
            suffix = SUFFIX[feature]
            row[f"target__{suffix}"] = targets[feature]
            row[f"reference_prediction__{suffix}"] = reference
            row[f"reference_absolute_error__{suffix}"] = abs(reference - targets[feature])
            row[f"candidate_prediction__{suffix}"] = candidate
            row[f"candidate_absolute_error__{suffix}"] = abs(candidate - targets[feature])
            arrays[feature]["target"].append(targets[feature])
            arrays[feature]["reference_prediction"].append(reference)
            arrays[feature]["candidate_prediction"].append(candidate)
        rows.append(row)
    numeric_arrays = {
        feature: {name: np.asarray(values, dtype=float) for name, values in records.items()}
        for feature, records in arrays.items()
    }
    return rows, numeric_arrays


def _feature_metric_rows(
    n: int, arrays: dict[str, dict[str, np.ndarray]]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    source = {"reference": "a" * 64, "candidate": "b" * 64}
    for role, model_name in (("reference", REFERENCE_NAME), ("candidate", CANDIDATE_NAME)):
        for feature in FEATURES:
            target = arrays[feature]["target"]
            prediction = arrays[feature][f"{role}_prediction"]
            signed = prediction - target
            absolute = np.abs(signed)
            span = SPANS[feature]
            mae = float(np.mean(absolute))
            rmse = float(np.sqrt(np.mean(signed**2)))
            rows.append(
                {
                    "model_role": role,
                    "model_name": model_name,
                    "feature": feature,
                    "unit": UNITS[feature],
                    "normalization_span": span,
                    "count": n,
                    "bias": float(np.mean(signed)),
                    "mae": mae,
                    "rmse": rmse,
                    "median_absolute_error": float(np.median(absolute)),
                    "p90_absolute_error": float(np.percentile(absolute, 90.0)),
                    "p95_absolute_error": float(np.percentile(absolute, 95.0)),
                    "p99_absolute_error": float(np.percentile(absolute, 99.0)),
                    "maximum_absolute_error": float(np.max(absolute)),
                    "normalized_mae": mae / span,
                    "normalized_rmse": rmse / span,
                    "source_prediction_csv_sha256": source[role],
                }
            )
    return rows


def _joint_rows(
    n: int,
    arrays: dict[str, dict[str, np.ndarray]],
    per_target_sha: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    tolerances = np.linspace(0.0, 0.25, 6)
    for role, model_name in (("reference", REFERENCE_NAME), ("candidate", CANDIDATE_NAME)):
        normalized_columns = []
        engineering_columns = []
        for feature in FEATURES:
            target = arrays[feature]["target"]
            prediction = arrays[feature][f"{role}_prediction"]
            normalized = np.abs(prediction - target) / SPANS[feature]
            normalized_columns.append(normalized)
            if feature == "Qmin":
                engineering_columns.append(np.maximum(target - prediction, 0.0) / SPANS[feature])
            else:
                engineering_columns.append(normalized)
        normalized_matrix = np.column_stack(normalized_columns)
        engineering_matrix = np.column_stack(engineering_columns)
        for metric, value, definition in (
            (
                "joint_normalized_mae",
                float(np.mean(engineering_matrix)),
                "mean absolute normalized error across rows and four features",
            ),
            (
                "joint_normalized_rmse",
                float(np.sqrt(np.mean(engineering_matrix**2))),
                "root mean squared normalized error across rows and four features",
            ),
        ):
            rows.append(
                {
                    "section": "joint_normalized_error",
                    "model_role": role,
                    "model_name": model_name,
                    "metric": metric,
                    "value": value,
                    "unit": "normalized fraction",
                    "definition": definition,
                    "n": n,
                    "tolerance_normalized": "",
                    "source_csv_sha256": per_target_sha,
                }
            )
        for tolerance in tolerances:
            rows.append(
                {
                    "section": "descriptive_tolerance_sweep",
                    "model_role": role,
                    "model_name": model_name,
                    "metric": "success_rate",
                    "value": float(np.mean(np.all(engineering_matrix <= tolerance, axis=1))),
                    "unit": "fraction",
                    "definition": "all four normalized errors within tolerance; Q is one-sided shortfall",
                    "n": n,
                    "tolerance_normalized": float(tolerance),
                    "source_csv_sha256": per_target_sha,
                }
            )
        summary_metrics = (
            ("model_contract", "source_table_rows", 100000 if role == "reference" else 200000, "count"),
            ("model_contract", "parameter_count", 501134, "count"),
            ("feasibility", "geometry_bound_violation_count", 0, "rows"),
            ("feasibility", "topology_violation_count", 0, "rows"),
            (
                "feasibility",
                "duplicate_predicted_geometry_count",
                1 if role == "reference" else 0,
                "rows",
            ),
            ("feasibility", "prediction_coverage", 1.0, "fraction"),
            ("runtime", "inference_runtime_seconds", 0.021 if role == "reference" else 0.023, "seconds"),
        )
        if role == "candidate":
            summary_metrics = (*summary_metrics, ("runtime", "training_runtime_seconds", 4321.5, "seconds"))
        for section, metric, value, unit in summary_metrics:
            rows.append(
                {
                    "section": section,
                    "model_role": role,
                    "model_name": model_name,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "definition": f"synthetic fixture definition for {metric}",
                    "n": n,
                    "tolerance_normalized": "",
                    "source_csv_sha256": per_target_sha,
                }
            )
    rows.append(
        {
            "section": "runtime",
            "model_role": "both",
            "model_name": "two-model evaluation pipeline",
            "metric": "evaluation_pipeline_wall_runtime_seconds",
            "value": 12.5,
            "unit": "seconds",
            "definition": "synthetic evaluation pipeline wall runtime",
            "n": n,
            "tolerance_normalized": "",
            "source_csv_sha256": per_target_sha,
        }
    )
    return rows


def _curve_rows(n: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    source = {"reference": "c" * 64, "candidate": "d" * 64}
    for role, model_name, multiplier in (
        ("reference", REFERENCE_NAME, 1.0),
        ("candidate", CANDIDATE_NAME, 0.82),
    ):
        for stage, series_values in (
            (
                "forward_proxy",
                {
                    "train": [0.35, 0.24, 0.19],
                    "validation": [0.39, 0.28, 0.23],
                },
            ),
            ("tandem_inverse", {"validation": [0.44, 0.32, 0.27]}),
        ):
            for series, values in series_values.items():
                for index, value in enumerate(values, 1):
                    rows.append(
                        {
                            "model_role": role,
                            "model_name": model_name,
                            "stage": stage,
                            "x_value": index * 100,
                            "series": series,
                            "metric": "normalized_rmse" if stage == "forward_proxy" else "validation_objective",
                            "value": multiplier * value,
                            "unit": "normalized fraction",
                            "n": n,
                            "source_history_csv_sha256": source[role],
                        }
                    )
    return rows


def _build_report_fixture(root: Path, n: int = 8) -> Path:
    report_dir = root / "fixture_report"
    report_dir.mkdir()
    paired_rows, arrays = _paired_fixture(n)
    per_target = report_dir / "per_target_paired_errors.csv"
    _write_csv(per_target, paired_rows)
    feature_rows = _feature_metric_rows(n, arrays)
    _write_csv(report_dir / "feature_metrics_long.csv", feature_rows)
    joint_rows = _joint_rows(n, arrays, _sha256(per_target))
    _write_csv(report_dir / "joint_metrics.csv", joint_rows)
    curves = _curve_rows(n)
    curve_path = report_dir / "training_curves_long.csv"
    _write_csv(curve_path, curves)

    _write_json(
        report_dir / "MODEL_CONTRACT_COMPARISON.json",
        {
            "schema": "architecture_matched_fixed8k_model_contract_comparison_v1",
            "reference": {
                "display_name": REFERENCE_NAME,
                "forward_architecture": [10, 256, 256, 128, 4],
                "inverse_architecture": [4, 512, 512, 256, 10],
                "decoder": "hard_feasible_topology_v1",
                "total_parameters": 501134,
                "source_table_rows": 100000,
                "gradient_training_rows": 80000,
            },
            "candidate": {
                "display_name": CANDIDATE_NAME,
                "forward_architecture": [10, 256, 256, 128, 4],
                "inverse_architecture": [4, 512, 512, 256, 10],
                "decoder": "hard_feasible_topology_v1",
                "total_parameters": 501134,
                "source_table_rows": 200000,
                "gradient_training_rows": 160000,
            },
            "contract_checks": {
                "forward_architecture_exact_and_equal": True,
                "inverse_architecture_exact_and_equal": True,
                "decoder_exact_and_equal": True,
                "parameter_count_exact_and_equal": True,
                "prediction_summary_weights_binding_complete": True,
            },
        },
    )
    _write_json(
        report_dir / "geometry_feasibility_summary.json",
        {
            "schema": "architecture_matched_fixed8k_geometry_feasibility_v1",
            "n": n,
            "models": {
                "100k": {
                    "geometry_bound_violation_count": 0,
                    "topology_violation_count": 0,
                    "duplicate_predicted_geometry_count": 1,
                    "prediction_coverage": 1.0,
                },
                "200k": {
                    "geometry_bound_violation_count": 0,
                    "topology_violation_count": 0,
                    "duplicate_predicted_geometry_count": 0,
                    "prediction_coverage": 1.0,
                },
            },
        },
    )
    _write_json(
        report_dir / "training_runtime_summary.json",
        {
            "schema": "architecture_matched_fixed8k_runtime_summary_v1",
            "n": n,
            "training_runtime": {"seconds": 4321.5},
            "inference_runtime": {
                "per_model_seconds": {"100k": 0.021, "200k": 0.023},
            },
            "evaluation_pipeline_wall_runtime": {"seconds": 12.5},
        },
    )

    filenames = (
        "MODEL_CONTRACT_COMPARISON.json",
        "geometry_feasibility_summary.json",
        "training_runtime_summary.json",
        "feature_metrics_long.csv",
        "joint_metrics.csv",
        "per_target_paired_errors.csv",
        "training_curves_long.csv",
    )
    csv_counts = {
        "feature_metrics_long.csv": len(feature_rows),
        "joint_metrics.csv": len(joint_rows),
        "per_target_paired_errors.csv": len(paired_rows),
        "training_curves_long.csv": len(curves),
    }
    outputs = {}
    for filename in filenames:
        record: dict[str, object] = {"sha256": _sha256(report_dir / filename)}
        if filename in csv_counts:
            record["row_count"] = csv_counts[filename]
        outputs[filename] = record
    _write_json(
        report_dir / "REPORT_SUMMARY.json",
        {
            "schema": "architecture_matched_fixed8k_report_summary_v1",
            "comparison": {
                "reference_name": REFERENCE_NAME,
                "candidate_name": CANDIDATE_NAME,
                "n": n,
                "evidence_label": "proxy-only evidence",
            },
            "outputs": outputs,
        },
    )
    return report_dir


def _png_dpi(path: Path) -> tuple[float, float]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        if chunk_type == b"pHYs":
            x_pixels_per_meter, y_pixels_per_meter, unit = struct.unpack(">IIB", chunk)
            assert unit == 1
            return x_pixels_per_meter * 0.0254, y_pixels_per_meter * 0.0254
        offset += 12 + length
    raise AssertionError("PNG lacks physical-resolution metadata")


def _refresh_output_hash(report_dir: Path, filename: str) -> None:
    summary_path = report_dir / "REPORT_SUMMARY.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["outputs"][filename]["sha256"] = _sha256(report_dir / filename)
    _write_json(summary_path, summary)


def _assert_no_staging(report_dir: Path) -> None:
    assert not any(path.name.startswith(".figures.staging.") for path in report_dir.iterdir())


def test_renderer_writes_exact_300dpi_png_svg_set_with_visible_metadata_and_no_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "EXPECTED_N", 8)
    report_dir = _build_report_fixture(tmp_path)

    result = module.render_report(report_dir)
    assert result["status"] == "PASS"
    assert result["figure_count"] == 20
    figures = report_dir / "figures"
    expected = {
        f"{basename}.{suffix}"
        for basename in module.FIGURE_BASENAMES
        for suffix in ("png", "svg")
    }
    assert {path.name for path in figures.iterdir()} == expected
    assert len(expected) == 20
    expected_source = {
        module.FIGURE_BASENAMES[0]: "joint_metrics.csv",
        module.FIGURE_BASENAMES[1]: "training_curves_long.csv",
        module.FIGURE_BASENAMES[2]: "feature_metrics_long.csv",
        module.FIGURE_BASENAMES[3]: "feature_metrics_long.csv",
        module.FIGURE_BASENAMES[4]: "per_target_paired_errors.csv",
        module.FIGURE_BASENAMES[5]: "per_target_paired_errors.csv",
        module.FIGURE_BASENAMES[6]: "feature_metrics_long.csv",
        module.FIGURE_BASENAMES[7]: "per_target_paired_errors.csv",
        module.FIGURE_BASENAMES[8]: "joint_metrics.csv",
        module.FIGURE_BASENAMES[9]: "joint_metrics.csv",
    }
    for basename in module.FIGURE_BASENAMES:
        png = figures / f"{basename}.png"
        svg = figures / f"{basename}.svg"
        assert png.stat().st_size > 1_000
        assert svg.stat().st_size > 1_000
        dpi_x, dpi_y = _png_dpi(png)
        assert dpi_x == pytest.approx(300.0, abs=0.2)
        assert dpi_y == pytest.approx(300.0, abs=0.2)
        svg_text = svg.read_text(encoding="utf-8")
        assert "<metadata>" in svg_text and "<dc:title>" in svg_text
        assert "<text" in svg_text
        assert REFERENCE_NAME in svg_text
        assert CANDIDATE_NAME in svg_text
        assert "n=8" in svg_text
        assert "proxy-only evidence" in svg_text
        assert "指标定义=" in svg_text
        assert "单位=" in svg_text
        assert "源CSV SHA256=" in svg_text
        assert _sha256(report_dir / expected_source[basename]) in svg_text
    _assert_no_staging(report_dir)

    before = {path.name: _sha256(path) for path in figures.iterdir()}
    with pytest.raises(FileExistsError, match="no-clobber"):
        module.render_report(report_dir)
    assert {path.name: _sha256(path) for path in figures.iterdir()} == before


def test_renderer_rejects_nonfinite_csv_before_creating_figures_or_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "EXPECTED_N", 8)
    report_dir = _build_report_fixture(tmp_path)
    path = report_dir / "feature_metrics_long.csv"
    rows = _read_csv(path)
    rows[0]["mae"] = "NaN"
    _write_csv(path, rows)
    _refresh_output_hash(report_dir, path.name)

    with pytest.raises(ValueError, match="non-finite"):
        module.render_report(report_dir)
    assert not (report_dir / "figures").exists()
    _assert_no_staging(report_dir)


def test_renderer_rejects_hash_mismatch_before_creating_figures_or_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "EXPECTED_N", 8)
    report_dir = _build_report_fixture(tmp_path)
    path = report_dir / "joint_metrics.csv"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        module.render_report(report_dir)
    assert not (report_dir / "figures").exists()
    _assert_no_staging(report_dir)
