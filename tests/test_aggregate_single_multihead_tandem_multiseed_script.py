import importlib.util
import json
import sys
from pathlib import Path


FEATURES = [
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
]


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "aggregate_single_multihead_tandem_multiseed.py"
    )
    spec = importlib.util.spec_from_file_location(
        "aggregate_single_multihead_tandem_multiseed_script", path
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _record(seed: int, improvement: float = 0.10) -> dict:
    return {
        "overall_status": "PASS",
        "formal_evidence": True,
        "checks": {"contract": True, "paired": True},
        "comparison_contract": {
            "training_csv_sha256": "a" * 64,
            "split_fingerprint_sha256": "b" * 64,
            "physical_cell_partition_fingerprint_sha256": "c" * 64,
            "input_columns": FEATURES,
            "geometry_columns": [f"geom__g{index}" for index in range(10)],
            "shared_arguments": {
                "seed": seed,
                "split_seed": 20260711,
                "forward_depth": 2,
                "inverse_depth": 2,
            },
            "model_seed": seed,
            "split_seed": 20260711,
            "head_count": 4,
        },
        "metrics": {
            "multihead_relative_improvement": improvement,
            "per_feature": {
                feature: {"multihead_relative_regression": -0.05} for feature in FEATURES
            },
        },
        "head_utilization": {
            "all_heads_selected_at_least_once": True,
            "entropy": 0.95,
        },
        "weight_contract": {
            "forward_exact_match": True,
            "multihead_parameter_overhead_fraction": 0.03,
        },
    }


def _write_records(tmp_path: Path, values: list[dict]) -> list[Path]:
    paths = []
    for index, value in enumerate(values):
        path = tmp_path / f"comparison_{index}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)
    return paths


def _args(paths: list[Path], out_dir: Path) -> list[str]:
    result = ["--out-dir", str(out_dir), "--bootstrap-replicates", "200"]
    for path in paths:
        result.extend(["--comparison-summary", str(path)])
    return result


def test_reviews_strong_five_seed_result_for_real_emx_closure(tmp_path):
    module = _load_module()
    paths = _write_records(
        tmp_path, [_record(20260720 + index, 0.08 + 0.01 * index) for index in range(5)]
    )
    out_dir = tmp_path / "out"
    status = module.main(_args(paths, out_dir))

    assert status == 0
    summary = json.loads(
        (out_dir / "single_multihead_tandem_multiseed_summary.json").read_text()
    )
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "REVIEW_MULTIHEAD_FOR_FIXED_BUDGET_REAL_EMX_CLOSURE"
    assert all(summary["checks"].values())
    assert all(summary["review_gates"].values())
    assert summary["seed_count"] == 5
    assert summary["statistics"]["median_relative_improvement"] == 0.10


def test_weak_seed_consistency_retains_singlehead(tmp_path):
    module = _load_module()
    improvements = [0.10, 0.08, 0.01, -0.02, 0.00]
    paths = _write_records(
        tmp_path, [_record(20260720 + index, value) for index, value in enumerate(improvements)]
    )
    out_dir = tmp_path / "out"
    status = module.main(_args(paths, out_dir))

    assert status == 0
    summary = json.loads(
        (out_dir / "single_multihead_tandem_multiseed_summary.json").read_text()
    )
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "RETAIN_SINGLEHEAD_BASELINE_MULTI_SEED_GATES_NOT_MET"
    assert summary["review_gates"]["seed_win_fraction_meets_threshold"] is False


def test_rejects_duplicate_model_seeds(tmp_path):
    module = _load_module()
    records = [_record(20260720 + index) for index in range(5)]
    records[-1] = _record(20260720)
    paths = _write_records(tmp_path, records)
    out_dir = tmp_path / "out"
    status = module.main(_args(paths, out_dir) + ["--no-fail-exit"])

    assert status == 0
    summary = json.loads(
        (out_dir / "single_multihead_tandem_multiseed_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["model_seeds_valid_and_unique"] is False


def test_rejects_changed_split_contract(tmp_path):
    module = _load_module()
    records = [_record(20260720 + index) for index in range(5)]
    records[-1]["comparison_contract"]["split_fingerprint_sha256"] = "d" * 64
    paths = _write_records(tmp_path, records)
    out_dir = tmp_path / "out"
    status = module.main(_args(paths, out_dir) + ["--no-fail-exit"])

    assert status == 0
    summary = json.loads(
        (out_dir / "single_multihead_tandem_multiseed_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["all_contract_fingerprints_match_except_model_seed"] is False


def test_rejects_nonformal_per_seed_comparison(tmp_path):
    module = _load_module()
    records = [_record(20260720 + index) for index in range(5)]
    records[2]["formal_evidence"] = False
    records[2]["decision"] = "INTERFACE_ONLY_NO_MODEL_PROMOTION"
    paths = _write_records(tmp_path, records)
    out_dir = tmp_path / "out"
    status = module.main(_args(paths, out_dir) + ["--no-fail-exit"])

    assert status == 0
    summary = json.loads(
        (out_dir / "single_multihead_tandem_multiseed_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["all_evidence_is_formal"] is False
