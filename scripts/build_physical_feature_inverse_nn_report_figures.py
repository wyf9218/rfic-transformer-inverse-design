#!/usr/bin/env python3
"""Build report-ready figures from a completed inverse-NN training checkpoint.

All plotted points come from the saved history, fixed-test prediction, and
per-geometry error CSVs. The script does not retrain, smooth, or synthesize
model results.
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary_path = Path(args.training_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    training = _read_json(summary_path)
    history_path = _referenced_path(training.get("best_history_csv"), summary_path.parent)
    predictions_path = _referenced_path(training.get("best_test_predictions_csv"), summary_path.parent)
    errors_path = _referenced_path(training.get("best_geometry_errors_csv"), summary_path.parent)
    history = _read_csv(history_path)
    predictions = _read_csv(predictions_path)
    errors = _read_csv(errors_path)

    checks = [
        _check("training_summary_exists", summary_path.is_file(), summary_path),
        _check("training_status_pass", training.get("overall_status") == "PASS", training.get("overall_status")),
        _check("history_rows_present", bool(history), len(history)),
        _check("test_prediction_rows_present", bool(predictions), len(predictions)),
        _check("geometry_error_rows_present", bool(errors), len(errors)),
    ]

    figures: dict[str, str] = {}
    plot_errors: list[str] = []
    if all(item["pass"] for item in checks):
        try:
            figures = _build_figures(out_dir, history, predictions, errors)
        except Exception as exc:  # pragma: no cover - backend failures are recorded as evidence
            plot_errors.append(f"{type(exc).__name__}: {exc}")
    checks.append(_check("all_report_figures_written", len(figures) == 3 and not plot_errors, plot_errors or figures))

    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_AS_FIRST100K_TRAINING_REPORT_EVIDENCE" if status == "PASS" else "DO_NOT_REPORT_MODEL_FIGURES_YET",
        "training_summary": _file_record(summary_path),
        "history_csv": _file_record(history_path),
        "test_predictions_csv": _file_record(predictions_path),
        "geometry_errors_csv": _file_record(errors_path),
        "selected_candidate": training.get("selected_candidate") or {},
        "test_evidence": training.get("best_test_evidence") or {},
        "figures": {name: _file_record(Path(path)) for name, path in figures.items()},
        "checks": checks,
        "plot_errors": plot_errors,
        "limitations": [
            "These figures visualize the saved fixed-test evidence; they do not prove EMX-to-HFSS agreement.",
            "Physical-feature distribution uniformity is audited separately and must not be inferred from model error plots.",
            "Predicted geometries still require DRC, EMX, and sampled HFSS validation.",
        ],
    }
    output_summary = out_dir / "physical_feature_inverse_nn_report_figures_summary.json"
    output_report = out_dir / "physical_feature_inverse_nn_report_figures.md"
    output_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_report.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"summary={output_summary}")
    print(f"report={output_report}")
    for name, path in figures.items():
        print(f"figure_{name}={path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-summary", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _build_figures(
    out_dir: Path,
    history: list[dict[str, str]],
    predictions: list[dict[str, str]],
    errors: list[dict[str, str]],
) -> dict[str, str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )
    figures: dict[str, str] = {}

    epochs = np.asarray([_float(row.get("epoch")) for row in history], dtype=float)
    train_rmse = np.asarray([_float(row.get("train_probe_normalized_rmse")) for row in history], dtype=float)
    validation_rmse = np.asarray([_float(row.get("validation_normalized_rmse")) for row in history], dtype=float)
    fig, ax = plt.subplots(figsize=(8.4, 4.8), constrained_layout=True)
    ax.plot(epochs, train_rmse, color="#1565C0", linewidth=2.0, label="Train probe")
    ax.plot(epochs, validation_rmse, color="#D84315", linewidth=2.0, label="Validation")
    ax.set_xlabel("Epoch")
    ax.set_ylabel("Normalized RMSE")
    ax.set_title("Inverse MLP training history")
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False)
    path = out_dir / "nn_training_validation_curve.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    figures["training_validation_curve"] = str(path)

    labels = [_short_geometry_name(row.get("geometry_column", "")) for row in errors]
    mae = np.asarray([100.0 * _float(row.get("normalized_mae")) for row in errors], dtype=float)
    rmse = np.asarray([100.0 * _float(row.get("normalized_rmse")) for row in errors], dtype=float)
    positions = np.arange(len(labels), dtype=float)
    width = 0.38
    fig, ax = plt.subplots(figsize=(11.2, 5.2), constrained_layout=True)
    ax.bar(positions - width / 2.0, mae, width, color="#1976D2", label="Normalized MAE")
    ax.bar(positions + width / 2.0, rmse, width, color="#EF6C00", label="Normalized RMSE")
    ax.set_xticks(positions, labels, rotation=35, ha="right")
    ax.set_ylabel("Error (% of full geometry span)")
    ax.set_title("Fixed-test error by geometry output")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(frameon=False)
    path = out_dir / "nn_geometry_error_bars.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    figures["geometry_error_bars"] = str(path)

    geometry_suffixes = [
        str(row.get("geometry_column") or "").removeprefix("geom__")
        for row in errors
    ]
    fig, axes = plt.subplots(2, 5, figsize=(15.5, 6.8), constrained_layout=True)
    for axis, suffix in zip(axes.flat, geometry_suffixes):
        truth = np.asarray([_float(row.get(f"true__{suffix}")) for row in predictions], dtype=float)
        pred = np.asarray([_float(row.get(f"pred__{suffix}")) for row in predictions], dtype=float)
        low = float(min(np.min(truth), np.min(pred)))
        high = float(max(np.max(truth), np.max(pred)))
        pad = max((high - low) * 0.04, 1.0e-9)
        axis.scatter(truth, pred, s=10, alpha=0.45, color="#00695C", edgecolors="none")
        axis.plot([low - pad, high + pad], [low - pad, high + pad], color="#424242", linestyle="--", linewidth=1.0)
        axis.set_xlim(low - pad, high + pad)
        axis.set_ylim(low - pad, high + pad)
        axis.set_title(_short_geometry_name(suffix))
        axis.set_xlabel("True (um)")
        axis.set_ylabel("Predicted (um)")
        axis.grid(True, alpha=0.2)
    for axis in axes.flat[len(geometry_suffixes) :]:
        axis.set_visible(False)
    fig.suptitle("Inverse MLP fixed-test predictions", fontsize=14)
    path = out_dir / "nn_predicted_vs_true_geometry.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    figures["predicted_vs_true_geometry"] = str(path)
    return figures


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _referenced_path(raw: Any, parent: Path) -> Path:
    if not raw:
        return parent / "__missing__"
    path = Path(str(raw)).expanduser()
    return path.resolve() if path.is_absolute() else (parent / path).resolve()


def _float(raw: Any) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"non-finite numeric value: {raw!r}")
    return value


def _short_geometry_name(raw: str) -> str:
    value = str(raw).removeprefix("geom__").removesuffix("_um")
    replacements = {
        "primary": "P",
        "secondary": "S",
        "outer": "out",
        "width": "W",
        "height": "H",
        "terminal": "term",
        "span": "span",
        "extension": "ext",
        "line": "line",
        "offset": "offset",
    }
    return " ".join(replacements.get(part, part) for part in value.split("_"))


def _file_record(path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if path.is_file():
        data = path.read_bytes()
        record.update({"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return record


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


def _render_report(summary: dict[str, Any]) -> str:
    selected = summary.get("selected_candidate") or {}
    evidence = summary.get("test_evidence") or {}
    lines = [
        "# Physical-Feature Inverse NN Report Figures",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Selected candidate: `{selected.get('candidate_id', '')}`",
        f"- Test rows: `{evidence.get('test_row_count', '')}`",
        f"- Mean normalized MAE: `{evidence.get('mean_normalized_mae', '')}`",
        f"- Max normalized MAE: `{evidence.get('max_normalized_mae', '')}`",
        "",
        "## Figures",
        "",
    ]
    lines.extend(f"- {name}: `{record.get('path', '')}`" for name, record in summary.get("figures", {}).items())
    lines.extend(["", "## Checks", ""])
    lines.extend(f"- {'PASS' if item['pass'] else 'FAIL'}: {item['name']} - {item['detail']}" for item in summary["checks"])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
