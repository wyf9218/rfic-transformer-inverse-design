from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import json
import sys


INPUTS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_physical_feature_sample_efficiency.py"
    spec = importlib.util.spec_from_file_location("sample_efficiency_benchmark_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_training_csv(path: Path, count: int) -> None:
    rng = np.random.default_rng(20260711)
    rows = []
    for index in range(count):
        features = np.asarray(
            [
                rng.uniform(0.5, 3.0),
                rng.uniform(0.5, 3.0),
                rng.uniform(5.0, 25.0),
                rng.uniform(0.0, 0.8),
            ],
            dtype=float,
        )
        row: dict[str, object] = {
            "evaluation": f"sample_{index:05d}",
            **{column: float(features[axis]) for axis, column in enumerate(INPUTS)},
        }
        normalized = np.asarray(
            [
                (features[0] - 0.5) / 2.5,
                (features[1] - 0.5) / 2.5,
                (features[2] - 5.0) / 20.0,
                features[3] / 0.8,
            ]
        )
        for geometry_index in range(10):
            coefficients = np.roll(np.asarray([0.9, 0.5, 0.3, 0.2]), geometry_index % 4)
            row[f"geom__g{geometry_index}"] = float(
                5.0 + geometry_index + np.dot(coefficients, normalized) + 0.01 * rng.normal()
            )
        rows.append(row)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _args(training_csv: Path, out_dir: Path) -> list[str]:
    return [
        "--training-csv",
        str(training_csv),
        "--out-dir",
        str(out_dir),
        "--minimum-full-rows",
        "240",
        "--training-counts",
        "20,40",
        "--model-seeds",
        "101,102",
        "--forward-depth",
        "1",
        "--forward-width",
        "8",
        "--inverse-depth",
        "1",
        "--inverse-width",
        "8",
        "--batch-size",
        "32",
        "--forward-epochs",
        "3",
        "--inverse-epochs",
        "3",
        "--patience",
        "2",
        "--no-plots",
    ]


def test_nested_multi_seed_sample_efficiency_uses_fixed_ood_rows_and_resumes(tmp_path):
    module = _load()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv, 320)
    out_dir = tmp_path / "benchmark"

    assert module.main(_args(training_csv, out_dir)) == 0
    summary_path = out_dir / "physical_feature_sample_efficiency_summary.json"
    summary = json.loads(summary_path.read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "INTERFACE_SMOKE_ONLY_NO_SAMPLE_SUFFICIENCY_CLAIM"
    assert summary["sufficiency_claim_eligible"] is False
    assert summary["candidate_sufficient_training_count_for_real_emx_review"] is None
    assert summary["effective_training_counts"][:2] == [20, 40]
    assert summary["effective_training_counts"][-1] == summary["full_available_training_count"]
    assert len(summary["records"]) == 2 * len(summary["effective_training_counts"])
    assert all(item["contains_previous_training_prefix"] for item in summary["nested_training_prefixes"])
    assert all(record["run_status"] == "PASS" for record in summary["records"])
    assert len({record["validation_identity_sha256"] for record in summary["records"]}) == 1
    assert len({record["test_identity_sha256"] for record in summary["records"]}) == 1
    assert summary["aggregates"]["by_training_count"][-1]["training_count"] == summary[
        "full_available_training_count"
    ]
    assert all(
        row["total_evidence_rows"]
        == row["training_count"] + summary["fixed_validation_count"] + summary["fixed_test_count"]
        for row in summary["aggregates"]["by_training_count"]
    )

    assert module.main(_args(training_csv, out_dir)) == 0
    resumed = json.loads(summary_path.read_text())
    assert all(record["reused"] is True for record in resumed["records"])


def test_sample_efficiency_waits_for_declared_checkpoint_size(tmp_path):
    module = _load()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv, 80)
    out_dir = tmp_path / "benchmark"

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(out_dir),
            "--minimum-full-rows",
            "100",
            "--no-plots",
        ]
    )
    assert status == 2
    summary = json.loads((out_dir / "physical_feature_sample_efficiency_summary.json").read_text())
    assert summary["overall_status"] == "WAITING"
    assert summary["decision"] == "WAIT_FOR_FIRST_100K_TRAINING_TABLE"
    assert summary["records"] == []


def test_default_counts_include_paper_reference_anchors(tmp_path):
    module = _load()
    args = module._parse_args(
        [
            "--training-csv",
            str(tmp_path / "training.csv"),
            "--out-dir",
            str(tmp_path / "benchmark"),
        ]
    )

    assert module._training_counts(args.training_counts)[:3] == [2400, 3216, 4000]
    anchors = module.SAMPLE_COUNT_REFERENCE_ANCHORS
    assert [item["benchmark_count"] for item in anchors] == [2400, 3216, 4000]
    assert anchors[0]["paper_total_samples"] == 4000
    assert anchors[1]["paper_total_samples"] == 3216
    assert all(item["task_boundary"] for item in anchors)


def test_rejects_duplicate_geometry_or_wrong_source_hash(tmp_path):
    module = _load()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv, 320)
    with training_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    geometry_columns = [name for name in rows[0] if name.startswith("geom__")]
    for column in geometry_columns:
        rows[1][column] = rows[0][column]
    with training_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    out_dir = tmp_path / "duplicate"
    arguments = _args(training_csv, out_dir)
    arguments.append("--no-fail-exit")

    assert module.main(arguments) == 0
    summary = json.loads(
        (out_dir / "physical_feature_sample_efficiency_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["independent_geometry_vectors_unique"] is False
    assert summary["geometry_contract"]["duplicate_vector_count"] == 1

    clean_csv = tmp_path / "clean.csv"
    _write_training_csv(clean_csv, 320)
    hash_out = tmp_path / "hash_mismatch"
    hash_arguments = _args(clean_csv, hash_out)
    hash_arguments.extend(
        ["--require-training-csv-sha256", "0" * 64, "--no-fail-exit"]
    )
    assert module.main(hash_arguments) == 0
    hash_summary = json.loads(
        (hash_out / "physical_feature_sample_efficiency_summary.json").read_text()
    )
    assert hash_summary["checks"]["required_training_csv_sha256_matches"] is False


def test_trainer_arguments_pin_geometry_and_response_ramp_once(tmp_path):
    module = _load()
    args = module._parse_args(
        [
            "--training-csv",
            str(tmp_path / "training.csv"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    geometry_columns = [f"geom__g{index}" for index in range(10)]
    values = module._trainer_argv(
        args,
        tmp_path / "subset.csv",
        tmp_path / "run",
        list(INPUTS),
        list(INPUTS),
        geometry_columns,
        100,
        7,
    )
    ramp_index = values.index("--response-ramp-fraction")
    geometry_index = values.index("--geometry-columns")
    assert values.count("--response-ramp-fraction") == 1
    assert values[ramp_index + 1] == "0.20"
    assert values[geometry_index + 1] == ",".join(geometry_columns)
