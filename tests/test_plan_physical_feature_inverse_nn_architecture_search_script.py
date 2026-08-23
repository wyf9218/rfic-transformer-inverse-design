from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plan_physical_feature_inverse_nn_architecture_search.py"
    spec = importlib.util.spec_from_file_location("plan_physical_feature_inverse_nn_architecture_search_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_training_csv(path: Path, *, use_zin: bool = False, rows: int = 12) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if use_zin:
        fieldnames = ["input__zin_real_center_ohm", "input__zin_imag_center_ohm", "geom__w_um", "geom__s_um"]
    else:
        fieldnames = [
            "input__lp_nh_center",
            "input__ls_nh_center",
            "input__q_center",
            "input__k_center",
            "geom__w_um",
            "geom__s_um",
            "geom__din_um",
        ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for idx in range(rows):
            if use_zin:
                writer.writerow(
                    {
                        "input__zin_real_center_ohm": 10 + idx,
                        "input__zin_imag_center_ohm": 100 + idx,
                        "geom__w_um": 2.0 + idx * 0.1,
                        "geom__s_um": 1.0 + idx * 0.1,
                    }
                )
            else:
                writer.writerow(
                    {
                        "input__lp_nh_center": 0.7 + idx * 0.01,
                        "input__ls_nh_center": 0.8 + idx * 0.01,
                        "input__q_center": 8.0 + idx * 0.1,
                        "input__k_center": 0.4 + idx * 0.005,
                        "geom__w_um": 2.0 + idx * 0.1,
                        "geom__s_um": 1.0 + idx * 0.05,
                        "geom__din_um": 80.0 + idx,
                    }
                )


def test_waits_when_100k_training_table_is_missing(tmp_path):
    module = _load_module()

    status = module.main(
        [
            "--training-csv",
            str(tmp_path / "missing.csv"),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_inverse_nn_architecture_search_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_TRAINING_CSV"
    assert summary["decision"] == "WAIT_FOR_100K_CHUNK_PHYSICAL_FEATURE_TRAINING_TABLE"
    assert summary["architecture_candidate_count"] > 0


def test_generates_mlp_architecture_candidates_for_physical_feature_training_table(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv, rows=16)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "16",
            "--max-candidates",
            "5",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_inverse_nn_architecture_search_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "READY_TO_RUN_NN_ARCHITECTURE_SEARCH_FOR_THIS_CHUNK"
    assert summary["input_columns"] == [
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__k_center",
    ]
    assert summary["architecture_candidate_count"] == 5
    with (tmp_path / "out" / "physical_feature_inverse_nn_architecture_candidates.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 5
    assert rows[0]["model_family"] == "mlp_residual"
    assert rows[0]["activation"] == "gelu"


def test_rejects_zin_inputs_for_nn_architecture_search(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv, use_zin=True, rows=8)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--input-columns",
            "input__zin_real_center_ohm,input__zin_imag_center_ohm",
            "--min-training-rows",
            "8",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_inverse_nn_architecture_search_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    checks = {item["name"]: item for item in summary["checks"]}
    assert checks["inverse NN inputs do not use Zin"]["status"] == "FAIL"
    assert checks["inverse NN inputs include Lp/Ls/Q/K"]["status"] == "FAIL"
