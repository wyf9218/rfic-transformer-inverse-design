#!/usr/bin/env python3
"""Build candidate-geometry Zin predictions for sparse-bin acquisition.

This script trains a small, auditable KNN inverse-acquisition surrogate from
existing real dataset rows. It only predicts which new candidate geometries are
worth simulating next; it does not create simulator labels. The output CSV is
intended for ``select_zin_targeted_candidate_geometries.py``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import qmc


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_csv = dataset_dir / "dataset_rows.csv"
    rows = _read_rows(dataset_csv)
    source = _file_source(dataset_csv)
    manifest = _read_manifest(args, dataset_dir)
    geom_columns = _infer_geometry_columns(rows, args.geom_prefix, args.min_geometry_span)
    training = _training_matrix(rows, geom_columns, args.real_column, args.imag_column)
    bounds = _resolve_bounds(training, geom_columns, manifest, args)

    checks = [
        _check("dataset_rows_csv_exists", dataset_csv.is_file(), str(dataset_csv)),
        _check("dataset_rows_present", bool(rows), f"rows={len(rows)}"),
        _check("geometry_columns_present", bool(geom_columns), f"columns={len(geom_columns)}"),
        _check("training_rows_present", training["count"] > 0, f"rows={training['count']}"),
        _check("bounds_valid", bool(bounds), f"bounds={len(bounds)}"),
        _check("candidate_count_positive", int(args.candidate_count) > 0, args.candidate_count),
        _check("prediction_batch_size_positive", int(args.prediction_batch_size) > 0, args.prediction_batch_size),
    ]

    candidate_rows: list[dict[str, Any]] = []
    validation = {"status": "NOT_RUN"}
    if all(item["pass"] for item in checks):
        validation = _cross_validate(training, bounds, args)
        candidate_rows = _build_candidates(training, bounds, args)
        checks.append(_check("candidate_predictions_present", bool(candidate_rows), f"rows={len(candidate_rows)}"))

    candidate_csv = out_dir / "candidate_zin_predictions.csv"
    summary_path = out_dir / "candidate_zin_prediction_summary.json"
    report_path = out_dir / "candidate_zin_prediction_report.md"
    _write_csv(candidate_csv, candidate_rows)
    figures = [] if args.no_plots or not candidate_rows else _write_plots(candidate_rows, out_dir)

    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_AS_CANDIDATE_PREDICTIONS_ONLY" if status == "PASS" else "DO_NOT_USE_CANDIDATE_PREDICTIONS",
        "dataset_dir": str(dataset_dir),
        "dataset_source": source,
        "out_dir": str(out_dir),
        "candidate_csv": str(candidate_csv),
        "figures": figures,
        "row_count": len(rows),
        "training_count": training["count"],
        "candidate_count": len(candidate_rows),
        "geometry_columns": geom_columns,
        "bounds": bounds,
        "validation": validation,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "Predicted Zin values are not labels and must not be used as EMX/ADS ground truth.",
            "Use the candidate CSV only to prioritize the next Cadence/EMX acquisition batch.",
            "The selected candidates still require geometry checks, EMX S4P generation, ADS/Python metric extraction, and post-run Zin uniformity audit.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"candidate_csv={candidate_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--candidate-count", type=int, default=5000)
    parser.add_argument("--prediction-batch-size", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=20260614)
    parser.add_argument("--k-neighbors", type=int, default=8)
    parser.add_argument("--distance-power", type=float, default=2.0)
    parser.add_argument("--real-column", default="zin_center_real_ohm")
    parser.add_argument("--imag-column", default="zin_center_imag_ohm")
    parser.add_argument("--geom-prefix", default="geom__")
    parser.add_argument("--min-geometry-span", type=float, default=1e-12)
    parser.add_argument("--manifest")
    parser.add_argument("--bounds-source", choices=["manifest_then_observed", "observed"], default="manifest_then_observed")
    parser.add_argument("--max-validation-rows", type=int, default=1000)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_manifest(args: argparse.Namespace, dataset_dir: Path) -> dict[str, Any]:
    candidates = [Path(args.manifest).expanduser().resolve()] if args.manifest else [dataset_dir / "dataset_manifest.json"]
    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            data["_manifest_path"] = str(path)
            return data
    return {}


def _infer_geometry_columns(rows: list[dict[str, str]], prefix: str, min_span: float) -> list[str]:
    if not rows:
        return []
    candidates = sorted(key for key in rows[0] if key.startswith(prefix))
    selected: list[str] = []
    for key in candidates:
        values = [_as_float(row.get(key)) for row in rows]
        finite = [value for value in values if value is not None]
        if len(finite) < 2:
            continue
        if max(finite) - min(finite) <= float(min_span):
            continue
        selected.append(key)
    return selected


def _training_matrix(
    rows: list[dict[str, str]],
    geom_columns: list[str],
    real_column: str,
    imag_column: str,
) -> dict[str, Any]:
    vectors: list[list[float]] = []
    labels: list[tuple[float, float]] = []
    source_indices: list[int] = []
    for idx, row in enumerate(rows):
        if not _truthy(row.get("ok", "true")):
            continue
        real = _as_float(row.get(real_column))
        imag = _as_float(row.get(imag_column))
        if real is None or imag is None:
            continue
        vector = [_as_float(row.get(column)) for column in geom_columns]
        if any(value is None for value in vector):
            continue
        vectors.append([float(value) for value in vector if value is not None])
        labels.append((real, imag))
        source_indices.append(idx)
    if not vectors:
        return {"count": 0, "x": np.empty((0, 0)), "y": np.empty((0, 2)), "source_indices": []}
    return {
        "count": len(vectors),
        "x": np.asarray(vectors, dtype=float),
        "y": np.asarray(labels, dtype=float),
        "source_indices": source_indices,
    }


def _resolve_bounds(
    training: dict[str, Any],
    geom_columns: list[str],
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, dict[str, float | str]]:
    if training["count"] == 0:
        return {}
    x = training["x"]
    manifest_bounds = manifest.get("bounds") if isinstance(manifest, dict) else {}
    manifest_bounds = manifest_bounds if isinstance(manifest_bounds, dict) else {}
    bounds: dict[str, dict[str, float | str]] = {}
    for col_idx, column in enumerate(geom_columns):
        observed_min = float(np.min(x[:, col_idx]))
        observed_max = float(np.max(x[:, col_idx]))
        lo = observed_min
        hi = observed_max
        source = "observed_training_range"
        raw_key = column.removeprefix(args.geom_prefix)
        if args.bounds_source == "manifest_then_observed" and raw_key in manifest_bounds:
            maybe = manifest_bounds.get(raw_key)
            if isinstance(maybe, (list, tuple)) and len(maybe) == 2:
                lo_candidate = _as_float(maybe[0])
                hi_candidate = _as_float(maybe[1])
                if lo_candidate is not None and hi_candidate is not None and hi_candidate > lo_candidate:
                    lo = lo_candidate
                    hi = hi_candidate
                    source = "dataset_manifest_bounds"
        if hi <= lo:
            continue
        bounds[column] = {
            "min": float(lo),
            "max": float(hi),
            "observed_min": observed_min,
            "observed_max": observed_max,
            "source": source,
        }
    return bounds


def _cross_validate(training: dict[str, Any], bounds: dict[str, dict[str, float | str]], args: argparse.Namespace) -> dict[str, Any]:
    x = _normalize(training["x"], bounds)
    y = training["y"]
    count = x.shape[0]
    if count < 3:
        return {"status": "SKIPPED_TOO_FEW_ROWS", "row_count": int(count)}
    rng = np.random.default_rng(int(args.seed))
    indices = np.arange(count)
    if count > int(args.max_validation_rows):
        indices = np.sort(rng.choice(indices, size=int(args.max_validation_rows), replace=False))
    errors: list[dict[str, float]] = []
    k = max(1, min(int(args.k_neighbors), count - 1))
    for idx in indices:
        distances = np.linalg.norm(x - x[idx], axis=1)
        order = np.argsort(distances)
        neighbors = [item for item in order if item != idx][:k]
        pred, _unc, _mean_distance = _predict_from_neighbors(y, distances, neighbors, args.distance_power)
        err_real = abs(float(pred[0] - y[idx, 0]))
        err_imag = abs(float(pred[1] - y[idx, 1]))
        errors.append({"real_abs_error_ohm": err_real, "imag_abs_error_ohm": err_imag, "abs_error_ohm": float(math.hypot(err_real, err_imag))})
    return {
        "status": "PASS",
        "method": "leave_one_out_knn_idw",
        "validated_rows": len(errors),
        "k_neighbors": k,
        "real_abs_error_ohm": _error_summary([row["real_abs_error_ohm"] for row in errors]),
        "imag_abs_error_ohm": _error_summary([row["imag_abs_error_ohm"] for row in errors]),
        "complex_abs_error_ohm": _error_summary([row["abs_error_ohm"] for row in errors]),
    }


def _build_candidates(training: dict[str, Any], bounds: dict[str, dict[str, float | str]], args: argparse.Namespace) -> list[dict[str, Any]]:
    field_order = list(bounds)
    dims = len(field_order)
    count = max(0, int(args.candidate_count))
    if dims == 0 or count <= 0:
        return []
    sampler = qmc.LatinHypercube(d=dims, seed=int(args.seed), optimization="random-cd")
    unit = sampler.random(n=count)
    lows = np.asarray([float(bounds[key]["min"]) for key in field_order], dtype=float)
    highs = np.asarray([float(bounds[key]["max"]) for key in field_order], dtype=float)
    candidates_x = qmc.scale(unit, lows, highs)
    train_x = _normalize(training["x"], bounds)
    train_y = training["y"]
    k = max(1, min(int(args.k_neighbors), train_x.shape[0]))

    rows: list[dict[str, Any]] = []
    batch_size = max(1, int(args.prediction_batch_size))
    denom = np.maximum(highs - lows, 1e-12)
    for start in range(0, count, batch_size):
        stop = min(count, start + batch_size)
        batch_vectors = candidates_x[start:stop]
        batch_norm = (batch_vectors - lows) / denom
        distances = np.linalg.norm(train_x[None, :, :] - batch_norm[:, None, :], axis=2)
        neighbors = np.argpartition(distances, kth=k - 1, axis=1)[:, :k]
        neighbor_distances = np.take_along_axis(distances, neighbors, axis=1)
        neighbor_y = train_y[neighbors]

        zero_rows = np.any(neighbor_distances < 1e-12, axis=1)
        weights = 1.0 / np.maximum(neighbor_distances, 1e-12) ** float(args.distance_power)
        weights = weights / np.maximum(np.sum(weights, axis=1, keepdims=True), 1e-12)
        preds = np.sum(neighbor_y * weights[:, :, None], axis=1)
        uncertainty = np.sqrt(np.sum(((neighbor_y - preds[:, None, :]) ** 2) * weights[:, :, None], axis=1))
        if np.any(zero_rows):
            zero_choice = np.argmax(neighbor_distances[zero_rows] < 1e-12, axis=1)
            preds[zero_rows] = neighbor_y[zero_rows, zero_choice, :]
            uncertainty[zero_rows] = 0.0
        mean_distances = np.mean(neighbor_distances, axis=1)

        for offset, vector in enumerate(batch_vectors):
            idx = start + offset
            pred = preds[offset]
            unc = uncertainty[offset]
            row: dict[str, Any] = {
                "candidate_id": f"surrogate_candidate_{idx:06d}",
                "pred_zin_center_real_ohm": float(pred[0]),
                "pred_zin_center_imag_ohm": float(pred[1]),
                "pred_zin_center_abs_ohm": float(math.hypot(float(pred[0]), float(pred[1]))),
                "pred_uncertainty_real_ohm": float(unc[0]),
                "pred_uncertainty_imag_ohm": float(unc[1]),
                "pred_neighbor_mean_distance": float(mean_distances[offset]),
                "pred_source": "knn_idw_surrogate_for_candidate_priority_only",
                "pred_k_neighbors": k,
            }
            for col_idx, column in enumerate(field_order):
                row[column] = float(vector[col_idx])
            rows.append(row)
    return rows


def _normalize(x: np.ndarray, bounds: dict[str, dict[str, float | str]]) -> np.ndarray:
    field_order = list(bounds)
    lows = np.asarray([float(bounds[key]["min"]) for key in field_order], dtype=float)
    highs = np.asarray([float(bounds[key]["max"]) for key in field_order], dtype=float)
    return (np.asarray(x, dtype=float) - lows) / np.maximum(highs - lows, 1e-12)


def _predict_from_neighbors(
    y: np.ndarray,
    distances: np.ndarray,
    neighbors: list[int],
    power: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    neighbor_distances = np.asarray([float(distances[idx]) for idx in neighbors], dtype=float)
    neighbor_y = y[np.asarray(neighbors, dtype=int), :]
    if np.any(neighbor_distances < 1e-12):
        pred = neighbor_y[int(np.argmin(neighbor_distances))]
        uncertainty = np.zeros(2, dtype=float)
    else:
        weights = 1.0 / np.maximum(neighbor_distances, 1e-12) ** float(power)
        weights = weights / np.sum(weights)
        pred = np.sum(neighbor_y * weights[:, None], axis=0)
        uncertainty = np.sqrt(np.sum(((neighbor_y - pred) ** 2) * weights[:, None], axis=0))
    return pred, uncertainty, float(np.mean(neighbor_distances))


def _write_plots(rows: list[dict[str, Any]], out_dir: Path) -> list[dict[str, str]]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []
    real = np.asarray([float(row["pred_zin_center_real_ohm"]) for row in rows], dtype=float)
    imag = np.asarray([float(row["pred_zin_center_imag_ohm"]) for row in rows], dtype=float)
    figures: list[dict[str, str]] = []

    fig, ax = plt.subplots(figsize=(6.5, 5.2))
    ax.scatter(real, imag, s=9, alpha=0.45)
    ax.set_xlabel("predicted Re(Zin) [ohm]")
    ax.set_ylabel("predicted Im(Zin) [ohm]")
    ax.set_title("Candidate predicted Zin scatter")
    ax.grid(True, alpha=0.25)
    path = out_dir / "candidate_predicted_zin_scatter.png"
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    figures.append({"title": "candidate predicted Zin scatter", "path": str(path)})

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8))
    axes[0].hist(real, bins=30, color="#2f63d7", alpha=0.75)
    axes[0].set_title("Predicted Re(Zin)")
    axes[0].set_xlabel("ohm")
    axes[1].hist(imag, bins=30, color="#b88a00", alpha=0.75)
    axes[1].set_title("Predicted Im(Zin)")
    axes[1].set_xlabel("ohm")
    for ax in axes:
        ax.grid(True, alpha=0.25)
    path = out_dir / "candidate_predicted_zin_histograms.png"
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    figures.append({"title": "candidate predicted Zin histograms", "path": str(path)})
    return figures


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "none", "no", "nan"}


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _error_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "p90": None, "max": None}
    arr = np.asarray(values, dtype=float)
    return {"median": float(np.median(arr)), "p90": float(np.percentile(arr, 90)), "max": float(np.max(arr))}


def _file_source(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    digest = hashlib.sha256()
    line_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            line_count += chunk.count(b"\n")
    out.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest(), "line_count": line_count})
    return out


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Zin Surrogate Candidate Predictions",
        "",
        f"Status: **{summary['overall_status']}**",
        f"Decision: **{summary['decision']}**",
        f"Training rows: `{summary['training_count']}`",
        f"Candidate rows: `{summary['candidate_count']}`",
        f"Candidate CSV: `{summary['candidate_csv']}`",
        "",
        "## Cross-validation",
        "",
        f"Validation status: `{summary['validation'].get('status')}`",
    ]
    validation = summary.get("validation") or {}
    if validation.get("complex_abs_error_ohm"):
        lines.append(f"Complex |error| ohm: `{validation['complex_abs_error_ohm']}`")
    lines.extend(["", "## Checks", "", "| Check | Pass | Detail |", "| --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
