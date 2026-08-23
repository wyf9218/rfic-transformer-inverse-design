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
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_feature_target_support.py"
    spec = importlib.util.spec_from_file_location("target_support_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _point(index: tuple[int, int, int, int], jitter: float = 0.0) -> dict[str, float]:
    return {
        feature: low + (value + 0.5 + jitter) * (high - low) / 2.0
        for feature, low, high, value in zip(FEATURES, LOWER, UPPER, index)
    }


def _write_training(path: Path, indices: list[tuple[int, int, int, int]], copies: int = 12) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["sample_id", *FEATURES]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index in indices:
            for copy in range(copies):
                jitter = (copy - (copies - 1) / 2.0) * 0.002
                writer.writerow(
                    {
                        "sample_id": "sample-" + "-".join(map(str, index)) + f"-{copy}",
                        **_point(index, jitter),
                    }
                )


def _run(audit, tmp_path: Path, indices: list[tuple[int, int, int, int]], *, plot: bool = False):
    training = tmp_path / "accepted.csv"
    out_dir = tmp_path / "out"
    _write_training(training, indices)
    argv = [
        "--training-csv",
        str(training),
        "--out-dir",
        str(out_dir),
        "--id-column",
        "sample_id",
        "--bins",
        "2",
        "--min-training-rows",
        "20",
        "--min-reference-rows",
        "10",
        "--min-calibration-rows",
        "5",
    ]
    if not plot:
        argv.append("--no-plots")
    status = audit.main(argv)
    summary = json.loads((out_dir / "physical_feature_target_support_summary.json").read_text())
    return status, summary, out_dir


def test_grid_support_gate_flags_empty_cells_without_claiming_impossibility(tmp_path):
    audit = _load_audit()
    even_parity = [
        index for index in itertools.product(range(2), repeat=4) if sum(index) % 2 == 0
    ]
    status, summary, out_dir = _run(audit, tmp_path, even_parity, plot=True)
    assert status == 0
    counts = summary["analysis"]["support_status_counts"]
    assert counts[audit.SUPPORTED] == 8
    assert counts[audit.EMPTY_PAIRWISE_SUPPORTED] == 8
    assert summary["production_contract"]["production_runtime_modified"] is False
    assert "do not prove physical impossibility" in summary["scientific_boundary"]
    assert (out_dir / "physical_feature_target_support.png").is_file()


def test_target_csv_separates_supported_empty_and_out_of_range(tmp_path):
    audit = _load_audit()
    training = tmp_path / "accepted.csv"
    _write_training(training, [(0, 0, 0, 0), (1, 1, 1, 1)], copies=30)
    target_csv = tmp_path / "targets.csv"
    with target_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["target_id", *FEATURES])
        writer.writeheader()
        writer.writerow({"target_id": "supported", **_point((0, 0, 0, 0))})
        writer.writerow({"target_id": "empty", **_point((0, 1, 0, 0))})
        writer.writerow(
            {
                "target_id": "outside",
                "lp_nh_center": 9.0,
                "ls_nh_center": 1.0,
                "q_center": 10.0,
                "k_abs_center": 0.2,
            }
        )
    out_dir = tmp_path / "out"
    assert audit.main(
        [
            "--training-csv",
            str(training),
            "--target-csv",
            str(target_csv),
            "--out-dir",
            str(out_dir),
            "--id-column",
            "sample_id",
            "--bins",
            "2",
            "--min-training-rows",
            "20",
            "--min-reference-rows",
            "10",
            "--min-calibration-rows",
            "5",
            "--no-plots",
        ]
    ) == 0
    with (out_dir / "physical_feature_target_support_targets.csv").open(newline="") as handle:
        by_id = {row["target_id"]: row for row in csv.DictReader(handle)}
    assert by_id["supported"]["support_status"] == audit.SUPPORTED
    assert by_id["empty"]["support_status"] == audit.EMPTY_PAIRWISE_UNSUPPORTED
    assert by_id["outside"]["support_status"] == audit.OUT_OF_RANGE


def test_duplicate_training_ids_fail_traceability_gate(tmp_path):
    audit = _load_audit()
    training = tmp_path / "accepted.csv"
    _write_training(training, [(0, 0, 0, 0), (1, 1, 1, 1)], copies=20)
    rows = list(csv.DictReader(training.open(newline="", encoding="utf-8")))
    rows[1]["sample_id"] = rows[0]["sample_id"]
    with training.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", *FEATURES])
        writer.writeheader()
        writer.writerows(rows)
    out_dir = tmp_path / "out"
    assert audit.main(
        [
            "--training-csv",
            str(training),
            "--out-dir",
            str(out_dir),
            "--id-column",
            "sample_id",
            "--min-training-rows",
            "20",
            "--min-reference-rows",
            "10",
            "--min-calibration-rows",
            "5",
            "--no-plots",
        ]
    ) == 2
    summary = json.loads((out_dir / "physical_feature_target_support_summary.json").read_text())
    assert summary["checks"]["stable_training_ids_unique"] is False
    assert summary["input_stats"]["duplicate_id_count"] == 1


def test_mixed_schema_ids_are_coalesced_per_row_without_row_number_fallback(tmp_path):
    audit = _load_audit()
    training = tmp_path / "accepted.csv"
    fields = ["touchstone_sha256", "evaluation", *FEATURES]
    with training.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index in range(60):
            writer.writerow(
                {
                    "touchstone_sha256": f"sha-{index}" if index % 2 == 0 else "",
                    "evaluation": f"eval-{index}",
                    **_point((index % 2,) * 4, (index % 5 - 2) * 0.001),
                }
            )
    out_dir = tmp_path / "out"
    assert audit.main(
        [
            "--training-csv",
            str(training),
            "--out-dir",
            str(out_dir),
            "--min-training-rows",
            "20",
            "--min-reference-rows",
            "10",
            "--min-calibration-rows",
            "5",
            "--no-plots",
        ]
    ) == 0
    summary = json.loads((out_dir / "physical_feature_target_support_summary.json").read_text())
    assert summary["input_stats"]["id_source_counts"] == {
        "touchstone_sha256": 30,
        "evaluation": 30,
    }
    assert summary["input_stats"]["missing_id_rows"] == 0
    assert summary["input_stats"]["duplicate_id_count"] == 0
