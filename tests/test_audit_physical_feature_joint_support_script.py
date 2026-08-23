from __future__ import annotations

import csv
import importlib.util
import itertools
import json
import sys
from pathlib import Path


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
LOWER = (0.5, 0.5, 5.0, 0.0)
UPPER = (3.0, 3.0, 25.0, 0.8)


def _load_audit():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_feature_joint_support.py"
    spec = importlib.util.spec_from_file_location("joint_support_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _point(index: tuple[int, int, int, int]) -> dict[str, float]:
    return {
        feature: low + (value + 0.5) * (high - low) / 2.0
        for feature, low, high, value in zip(FEATURES, LOWER, UPPER, index)
    }


def _write_rows(path: Path, indices: list[tuple[int, int, int, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FEATURES))
        writer.writeheader()
        writer.writerows(_point(index) for index in indices)


def _run(
    audit,
    tmp_path: Path,
    indices: list[tuple[int, int, int, int]],
    *,
    no_plots: bool = True,
):
    training_csv = tmp_path / "accepted.csv"
    out_dir = tmp_path / "out"
    _write_rows(training_csv, indices)
    argv = [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(out_dir),
            "--bins",
            "2",
        ]
    if no_plots:
        argv.append("--no-plots")
    status = audit.main(argv)
    summary = json.loads((out_dir / "physical_feature_joint_support_summary.json").read_text())
    return status, summary


def test_even_parity_design_finds_pairwise_supported_4d_empty_cells(tmp_path):
    audit = _load_audit()
    indices = [index for index in itertools.product(range(2), repeat=4) if sum(index) % 2 == 0]
    status, summary = _run(audit, tmp_path, indices, no_plots=False)

    assert status == 0
    assert summary["overall_status"] == "PASS"
    counts = summary["analysis"]["classification_counts"]
    assert counts[audit.OCCUPIED] == 8
    assert counts[audit.PAIRWISE_SUPPORTED] == 8
    assert counts.get(audit.PAIRWISE_UNSUPPORTED, 0) == 0
    assert summary["uniformity_contract"]["denominator_changed"] is False
    assert summary["uniformity_contract"]["all_4d_cells_remain_in_final_denominator"] is True
    assert summary["analysis"]["nearest_distance_by_empty_class"][audit.PAIRWISE_SUPPORTED]["min"] > 0
    assert Path(summary["outputs"]["figure"]["path"]).is_file()
    assert summary["outputs"]["figure"]["sha256"]


def test_sparse_diagonal_design_distinguishes_pairwise_from_marginal_gaps(tmp_path):
    audit = _load_audit()
    status, summary = _run(audit, tmp_path, [(0, 0, 0, 0), (1, 1, 1, 1)])
    assert status == 0
    counts = summary["analysis"]["classification_counts"]
    assert counts[audit.PAIRWISE_UNSUPPORTED] == 14
    assert counts.get(audit.MARGINAL_UNSUPPORTED, 0) == 0

    other = tmp_path / "marginal"
    indices = [(0, a, b, c) for a, b, c in itertools.product(range(2), repeat=3)]
    status, summary = _run(audit, other, indices)
    assert status == 0
    counts = summary["analysis"]["classification_counts"]
    assert counts[audit.MARGINAL_UNSUPPORTED] == 8


def test_out_of_range_row_fails_real_accepted_input_contract(tmp_path):
    audit = _load_audit()
    training_csv = tmp_path / "accepted.csv"
    _write_rows(training_csv, [(0, 0, 0, 0)])
    with training_csv.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FEATURES))
        writer.writerow(
            {
                "lp_nh_center": 99.0,
                "ls_nh_center": 1.0,
                "q_center": 10.0,
                "k_abs_center": 0.2,
            }
        )
    out_dir = tmp_path / "out"
    assert audit.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(out_dir),
            "--no-plots",
        ]
    ) == 2
    summary = json.loads((out_dir / "physical_feature_joint_support_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["input_stats"]["out_of_range_rows"] == 1
    assert summary["checks"]["all_rows_valid_and_in_range"] is False


def test_scientific_boundary_never_claims_empty_cells_are_impossible(tmp_path):
    audit = _load_audit()
    _, summary = _run(audit, tmp_path, [(0, 0, 0, 0), (1, 1, 1, 1)])
    boundary = summary["scientific_boundary"].lower()
    assert "do not prove physical impossibility" in boundary
    assert "does not prove that a four-feature combination is physically reachable" in boundary
    assert summary["uniformity_contract"]["production_queue_or_labels_modified"] is False
