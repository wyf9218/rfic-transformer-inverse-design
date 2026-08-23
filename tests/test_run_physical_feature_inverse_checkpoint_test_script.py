from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


INPUTS = [
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
]
GEOMETRIES = [f"geom__g{index}" for index in range(10)]


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_physical_feature_inverse_checkpoint_test.py"
    spec = importlib.util.spec_from_file_location("run_physical_feature_inverse_checkpoint_test_script", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_rows(path: Path, *, count: int = 120, noisy: bool = False) -> None:
    rng = np.random.default_rng(44)
    rows = []
    for index in range(count):
        inputs = rng.uniform(0.0, 1.0, size=4)
        if noisy:
            geometry = rng.uniform(0.0, 1.0, size=10)
        else:
            geometry = np.asarray(
                [
                    inputs[0],
                    inputs[1],
                    inputs[2],
                    inputs[3],
                    inputs[0] + inputs[1],
                    inputs[2] + inputs[3],
                    inputs[0] * inputs[1],
                    inputs[2] * inputs[3],
                    inputs[0] ** 2,
                    inputs[3] ** 2,
                ]
            )
        rows.append(
            {
                "evaluation": f"row_{index:04d}",
                **{column: float(inputs[col]) for col, column in enumerate(INPUTS)},
                **{column: float(geometry[col]) for col, column in enumerate(GEOMETRIES)},
            }
        )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _args(training: Path, out_dir: Path) -> list[str]:
    return [
        "--training-csv",
        str(training),
        "--out-dir",
        str(out_dir),
        "--input-columns",
        ",".join(INPUTS),
        "--geometry-columns",
        ",".join(GEOMETRIES),
        "--min-training-rows",
        "100",
        "--max-train-rows",
        "90",
        "--max-test-rows",
        "30",
        "--max-prediction-rows",
        "30",
    ]


def test_random_holdout_pass_is_explicitly_ineligible_for_model_success_claim(tmp_path):
    module = _load_module()
    training = tmp_path / "training.csv"
    _write_rows(training)
    out_dir = tmp_path / "out"

    args = _args(training, out_dir) + [
        "--warn-max-normalized-mae",
        "0.05",
        "--warn-max-normalized-rmse",
        "0.08",
    ]
    assert module.main(args) == 0
    summary = json.loads((out_dir / "physical_feature_inverse_checkpoint_test_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["execution_status"] == "PASS"
    assert summary["quality_status"] == "PASS"
    assert summary["evidence_scope"] == "DETERMINISTIC_RANDOM_HOLDOUT_RIDGE_BASELINE"
    assert summary["eligible_for_model_success_claim"] is False
    assert summary["formal_model_gate_status"].startswith("NOT_EVALUATED")
    assert summary["decision"] == "BASELINE_CHECKPOINT_EXECUTED_WARNING_LIMITS_PASS_NOT_MODEL_SUCCESS_GATE"


def test_quality_warning_does_not_turn_execution_into_model_success(tmp_path):
    module = _load_module()
    training = tmp_path / "training.csv"
    _write_rows(training, noisy=True)
    out_dir = tmp_path / "out"

    args = _args(training, out_dir) + [
        "--warn-max-normalized-mae",
        "0.05",
        "--warn-max-normalized-rmse",
        "0.08",
    ]
    assert module.main(args) == 0
    summary = json.loads((out_dir / "physical_feature_inverse_checkpoint_test_summary.json").read_text())
    assert summary["execution_status"] == "PASS"
    assert summary["quality_status"] == "WARN"
    assert summary["eligible_for_model_success_claim"] is False
    assert summary["decision"] == "BASELINE_CHECKPOINT_EXECUTED_WITH_QUALITY_WARNING_NOT_MODEL_SUCCESS_GATE"


def test_missing_training_evidence_fails_execution(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "out"

    assert module.main(_args(tmp_path / "missing.csv", out_dir)) == 2
    summary = json.loads((out_dir / "physical_feature_inverse_checkpoint_test_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["execution_status"] == "FAIL"
    assert summary["eligible_for_model_success_claim"] is False
    assert summary["decision"] == "DO_NOT_USE_MODEL_CHECKPOINT"
