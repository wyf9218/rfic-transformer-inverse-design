import csv
import json
from pathlib import Path

import numpy as np

from scripts.estimate_physical_feature_uniformity_remediation import _estimate_additions, main


def _write_rows(path: Path, rows: list[dict[str, float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_balanced_counts_need_no_idealized_additions():
    result = _estimate_additions(
        np.asarray([10, 10, 10, 10]),
        min_occupied_fraction=1.0,
        min_normalized_entropy=0.95,
        max_nonzero_imbalance=1.1,
        max_additions=1000,
    )
    assert result["converged"] is True
    assert result["idealized_additions"] == 0


def test_skewed_counts_are_water_filled_to_declared_gate():
    result = _estimate_additions(
        np.asarray([100, 10, 0, 0]),
        min_occupied_fraction=0.75,
        min_normalized_entropy=0.65,
        max_nonzero_imbalance=2.0,
        max_additions=1000,
    )
    assert result["converged"] is True
    assert result["idealized_additions"] > 0
    after = result["idealized_after"]
    assert after["occupied_fraction"] >= 0.75
    assert after["normalized_entropy"] >= 0.65
    assert after["max_to_min_nonzero_ratio"] <= 2.0


def test_script_writes_hashed_plot_and_truthful_boundary(tmp_path: Path):
    csv_path = tmp_path / "dataset_rows.csv"
    rows = []
    for index in range(64):
        fraction = index / 63.0
        rows.append(
            {
                "lp_nh_center": 0.5 + 2.5 * fraction,
                "ls_nh_center": 0.5 + 2.5 * ((index * 7) % 64) / 63.0,
                "q_center": 5.0 + 20.0 * fraction,
                "k_abs_center": 0.8 * ((index * 11) % 64) / 63.0,
            }
        )
    _write_rows(csv_path, rows)

    status = main(
        [
            "--training-csv",
            str(csv_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--max-ideal-additions",
            "100000",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_uniformity_remediation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "PASS"
    assert summary["valid_in_range_row_count"] == 64
    assert summary["plot"]["exists"] is True
    assert summary["plot"]["size_bytes"] > 0
    assert summary["plot"]["sha256"]
    assert "optimistic" in summary["scientific_boundary"]
    assert "Only returned real EMX labels" in summary["scientific_boundary"]
