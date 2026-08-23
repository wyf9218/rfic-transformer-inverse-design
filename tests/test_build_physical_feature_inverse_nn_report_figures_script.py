import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_physical_feature_inverse_nn_report_figures.py"


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_report_figure_builder_writes_three_hashed_real_artifacts(tmp_path: Path):
    history = tmp_path / "history.csv"
    predictions = tmp_path / "predictions.csv"
    errors = tmp_path / "errors.csv"
    summary = tmp_path / "training_summary.json"
    out_dir = tmp_path / "figures"

    _write_csv(
        history,
        [
            {"epoch": 1, "train_probe_normalized_rmse": 0.30, "validation_normalized_rmse": 0.34},
            {"epoch": 2, "train_probe_normalized_rmse": 0.20, "validation_normalized_rmse": 0.24},
        ],
    )
    suffixes = [f"geometry_{index}_um" for index in range(10)]
    prediction_rows = []
    for row_index in range(8):
        row: dict[str, object] = {}
        for column_index, suffix in enumerate(suffixes):
            truth = 10.0 + row_index + column_index
            row[f"true__{suffix}"] = truth
            row[f"pred__{suffix}"] = truth + 0.1 * (column_index + 1)
        prediction_rows.append(row)
    _write_csv(predictions, prediction_rows)
    _write_csv(
        errors,
        [
            {
                "geometry_column": f"geom__{suffix}",
                "normalized_mae": 0.01 + index * 0.001,
                "normalized_rmse": 0.02 + index * 0.001,
            }
            for index, suffix in enumerate(suffixes)
        ],
    )
    summary.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "best_history_csv": str(history),
                "best_test_predictions_csv": str(predictions),
                "best_geometry_errors_csv": str(errors),
                "selected_candidate": {"candidate_id": "candidate_001"},
                "best_test_evidence": {"test_row_count": 8},
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--training-summary", str(summary), "--out-dir", str(out_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(
        (out_dir / "physical_feature_inverse_nn_report_figures_summary.json").read_text(encoding="utf-8")
    )
    assert evidence["overall_status"] == "PASS"
    assert set(evidence["figures"]) == {
        "training_validation_curve",
        "geometry_error_bars",
        "predicted_vs_true_geometry",
    }
    for record in evidence["figures"].values():
        path = Path(record["path"])
        assert record["exists"] is True
        assert path.is_file() and path.stat().st_size > 0
        assert record["size_bytes"] == path.stat().st_size
        assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
