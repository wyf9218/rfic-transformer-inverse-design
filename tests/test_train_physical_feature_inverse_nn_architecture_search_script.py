from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "train_physical_feature_inverse_nn_architecture_search.py"
    spec = importlib.util.spec_from_file_location("train_physical_feature_inverse_nn_architecture_search_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_training_csv(path: Path, *, rows: int = 64, use_zin: bool = False) -> None:
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
                        "input__zin_real_center_ohm": 10.0 + idx,
                        "input__zin_imag_center_ohm": 100.0 + idx,
                        "geom__w_um": 2.0 + idx * 0.1,
                        "geom__s_um": 1.0 + idx * 0.1,
                    }
                )
            else:
                lp = 0.7 + idx * 0.004
                ls = 0.85 + idx * 0.003
                q = 8.0 + idx * 0.05
                k = 0.42 + idx * 0.001
                writer.writerow(
                    {
                        "input__lp_nh_center": lp,
                        "input__ls_nh_center": ls,
                        "input__q_center": q,
                        "input__k_center": k,
                        "geom__w_um": 1.5 + 0.5 * lp + 0.02 * q,
                        "geom__s_um": 0.8 + 0.4 * ls - 0.1 * k,
                        "geom__din_um": 70.0 + 10.0 * lp + 5.0 * ls,
                    }
                )


def _write_candidate_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "candidate_id": "mlp_small",
            "hidden_depth": 1,
            "hidden_width": 8,
            "dropout": 0.0,
            "learning_rate": 0.01,
            "weight_decay": 0.0,
            "batch_size": 16,
            "seed": 123,
            "max_epochs": 80,
            "early_stopping_patience": 20,
        },
        {
            "candidate_id": "mlp_medium",
            "hidden_depth": 2,
            "hidden_width": 12,
            "dropout": 0.0,
            "learning_rate": 0.008,
            "weight_decay": 0.0001,
            "batch_size": 16,
            "seed": 456,
            "max_epochs": 80,
            "early_stopping_patience": 20,
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_waits_when_training_csv_is_missing(tmp_path):
    module = _load_module()
    candidate_csv = tmp_path / "candidates.csv"
    _write_candidate_csv(candidate_csv)

    status = module.main(
        [
            "--training-csv",
            str(tmp_path / "missing.csv"),
            "--candidate-csv",
            str(candidate_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_inverse_nn_architecture_search_training_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_TRAINING_CSV"
    assert summary["trained_candidate_count"] == 0


def test_trains_and_selects_numpy_mlp_candidate(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    candidate_csv = tmp_path / "candidates.csv"
    _write_training_csv(training_csv, rows=72)
    _write_candidate_csv(candidate_csv)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--candidate-csv",
            str(candidate_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "32",
            "--max-candidates",
            "2",
            "--max-epochs-cap",
            "80",
            "--patience-cap",
            "20",
            "--max-validation-normalized-rmse",
            "5",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_inverse_nn_architecture_search_training_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "SELECT_NN_ARCHITECTURE_FOR_THIS_100K_CHUNK"
    assert summary["trained_candidate_count"] == 2
    assert summary["selected_candidate"]["candidate_id"] in {"mlp_small", "mlp_medium"}
    assert Path(summary["best_model_json"]).is_file()
    assert Path(summary["best_weights_npz"]).is_file()
    assert Path(summary["best_history_csv"]).is_file()
    assert Path(summary["best_test_predictions_csv"]).is_file()
    assert Path(summary["best_geometry_errors_csv"]).is_file()
    assert summary["best_test_evidence"]["test_row_count"] > 0
    with Path(summary["results_csv"]).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    with Path(summary["best_history_csv"]).open(newline="", encoding="utf-8") as handle:
        history = list(csv.DictReader(handle))
    assert history
    with Path(summary["best_geometry_errors_csv"]).open(newline="", encoding="utf-8") as handle:
        geometry_errors = list(csv.DictReader(handle))
    assert len(geometry_errors) == 3


def test_rejects_zin_training_inputs(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    candidate_csv = tmp_path / "candidates.csv"
    _write_training_csv(training_csv, rows=16, use_zin=True)
    _write_candidate_csv(candidate_csv)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--candidate-csv",
            str(candidate_csv),
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
    summary = json.loads((tmp_path / "out" / "physical_feature_inverse_nn_architecture_search_training_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "FAIL"
    checks = {item["name"]: item for item in summary["checks"]}
    assert checks["inverse NN train inputs do not use Zin"]["status"] == "FAIL"
    assert checks["inverse NN train inputs include Lp/Ls/Q/K"]["status"] == "FAIL"


def test_geometry_envelope_violation_rate_is_computed_not_hardcoded():
    module = _load_module()
    x = np.asarray([[0.0], [1.0], [2.0], [3.0]], dtype=float)
    y = np.asarray([[0.0], [1.0], [0.5], [0.2]], dtype=float)
    split = {
        "train": np.asarray([0, 1], dtype=int),
        "validation": np.asarray([2], dtype=int),
        "test": np.asarray([3], dtype=int),
    }
    weights = [np.asarray([[0.0]], dtype=float)]
    biases = [np.asarray([2.0], dtype=float)]

    metrics = module._geometry_envelope_violation_metrics(y, split, weights, biases, x)

    assert metrics["geometry_bound_violation_rate"] == 1.0
    assert metrics["geometry_bound_violation_element_rate"] == 1.0
    assert metrics["geometry_bound_definition"].endswith("_not_drc")


def test_direct_mlp_can_use_complete_physical_cell_ood_split(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    candidate_csv = tmp_path / "candidates.csv"
    _write_training_csv(training_csv, rows=96)
    _write_candidate_csv(candidate_csv)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--candidate-csv",
            str(candidate_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "64",
            "--max-candidates",
            "1",
            "--max-epochs-cap",
            "4",
            "--patience-cap",
            "2",
            "--split-seed",
            "20260711",
            "--split-mode",
            "physical_cell_grouped",
            "--physical-cell-bins",
            "4",
            "--physical-cell-lower",
            "0.5,0.5,5,0.3",
            "--physical-cell-upper",
            "1.5,1.5,20,0.8",
            "--max-validation-normalized-rmse",
            "100",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_inverse_nn_architecture_search_training_summary.json").read_text(
            encoding="utf-8"
        )
    )
    audit = summary["split_audit"]
    assert summary["overall_status"] == "PASS"
    assert audit["split_mode"] == "physical_cell_grouped"
    assert audit["physical_cell_overlap_count"] == 0
    assert audit["all_rows_assigned_once"] is True
    assert audit["out_of_range_row_count_before_clipping"] == 0
    assert len(audit["split_fingerprint_sha256"]) == 64


def _resume_test_args(training_csv: Path, candidate_csv: Path, out_dir: Path) -> list[str]:
    return [
        "--training-csv",
        str(training_csv),
        "--candidate-csv",
        str(candidate_csv),
        "--out-dir",
        str(out_dir),
        "--min-training-rows",
        "32",
        "--max-candidates",
        "2",
        "--max-epochs-cap",
        "4",
        "--patience-cap",
        "2",
        "--max-validation-normalized-rmse",
        "100",
        "--resume-completed-candidates",
    ]


def test_candidate_checkpoints_resume_without_retraining(tmp_path, monkeypatch):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    candidate_csv = tmp_path / "candidates.csv"
    out_dir = tmp_path / "out"
    _write_training_csv(training_csv, rows=72)
    _write_candidate_csv(candidate_csv)
    args = _resume_test_args(training_csv, candidate_csv, out_dir)

    assert module.main(args) == 0
    first_summary = json.loads(
        (out_dir / "physical_feature_inverse_nn_architecture_search_training_summary.json").read_text(
            encoding="utf-8"
        )
    )
    markers = sorted((out_dir / "candidate_checkpoints").glob("*/candidate_checkpoint.complete.json"))
    progress = json.loads(
        (out_dir / "physical_feature_inverse_nn_architecture_search_progress.json").read_text(encoding="utf-8")
    )
    assert first_summary["trained_this_run_count"] == 2
    assert first_summary["resumed_candidate_count"] == 0
    assert len(markers) == 2
    assert progress["status"] == "COMPLETE"
    assert progress["completed_candidate_count"] == 2
    assert progress["expected_candidate_count"] == 2
    assert progress["persisted_candidate_checkpoint_count"] == 2

    stale_checkpoint = out_dir / "candidate_checkpoints" / "stale_old_candidate" / "candidate_checkpoint.complete.json"
    stale_checkpoint.parent.mkdir(parents=True)
    stale_checkpoint.write_text("{}\n", encoding="utf-8")

    def fail_if_trained(*_args, **_kwargs):
        raise AssertionError("a matching complete candidate checkpoint must be resumed")

    monkeypatch.setattr(module, "_train_candidate", fail_if_trained)
    assert module.main(args) == 0
    resumed_summary = json.loads(
        (out_dir / "physical_feature_inverse_nn_architecture_search_training_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert resumed_summary["trained_this_run_count"] == 0
    assert resumed_summary["resumed_candidate_count"] == 2
    assert resumed_summary["overall_status"] == "PASS"


def test_tampered_candidate_checkpoint_retrains_only_invalid_candidate(tmp_path, monkeypatch):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    candidate_csv = tmp_path / "candidates.csv"
    out_dir = tmp_path / "out"
    _write_training_csv(training_csv, rows=72)
    _write_candidate_csv(candidate_csv)
    args = _resume_test_args(training_csv, candidate_csv, out_dir)

    assert module.main(args) == 0
    weights = sorted((out_dir / "candidate_checkpoints").glob("*/candidate_model_weights.npz"))
    assert len(weights) == 2
    weights[0].write_bytes(weights[0].read_bytes() + b"tampered")

    original_train = module._train_candidate
    trained_ids: list[str] = []

    def count_training(candidate, data, parsed_args):
        trained_ids.append(str(candidate["candidate_id"]))
        return original_train(candidate, data, parsed_args)

    monkeypatch.setattr(module, "_train_candidate", count_training)
    assert module.main(args) == 0
    summary = json.loads(
        (out_dir / "physical_feature_inverse_nn_architecture_search_training_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(trained_ids) == 1
    assert summary["trained_this_run_count"] == 1
    assert summary["resumed_candidate_count"] == 1


def test_duplicate_candidate_ids_fail_before_training(tmp_path, monkeypatch):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    candidate_csv = tmp_path / "candidates.csv"
    out_dir = tmp_path / "out"
    _write_training_csv(training_csv, rows=72)
    _write_candidate_csv(candidate_csv)
    with candidate_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows[1]["candidate_id"] = rows[0]["candidate_id"]
    with candidate_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    def fail_if_trained(*_args, **_kwargs):
        raise AssertionError("duplicate candidate IDs must fail before training")

    monkeypatch.setattr(module, "_train_candidate", fail_if_trained)
    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--candidate-csv",
            str(candidate_csv),
            "--out-dir",
            str(out_dir),
            "--min-training-rows",
            "32",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (out_dir / "physical_feature_inverse_nn_architecture_search_training_summary.json").read_text(
            encoding="utf-8"
        )
    )
    checks = {item["name"]: item for item in summary["checks"]}
    assert summary["overall_status"] == "FAIL"
    assert summary["trained_candidate_count"] == 0
    assert checks["candidate IDs unique"]["status"] == "FAIL"
