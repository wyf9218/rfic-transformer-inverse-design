from tests.rfic_transformer_inverse_design.shared import *

import argparse
import csv
import importlib.util
import json
import sys

import pytest


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
RANGES = ((0.5, 3.0), (0.5, 3.0), (5.0, 25.0), (0.0, 0.8))


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", "_test_module"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _paired_rows(root: Path, *, count: int, mode: str, offset: int = 0) -> Path:
    rng = np.random.default_rng(47)
    rows = []
    for index in range(count):
        real = np.asarray([rng.uniform(lower, upper) for lower, upper in RANGES], dtype=float)
        if mode == "biased":
            predicted = np.asarray(
                [
                    0.72 * real[0] + 0.30,
                    1.18 * real[1] - 0.20,
                    0.70 * real[2] + 3.00,
                    0.76 * real[3] + 0.08,
                ]
            )
        elif mode == "already_calibrated":
            predicted = np.array(real, copy=True)
        else:
            raise ValueError(mode)
        sample_index = offset + index
        touchstone = root / f"sample_{sample_index:04d}.s4p"
        touchstone.write_text("! nonempty synthetic fixture, not scientific evidence\n", encoding="ascii")
        row: dict[str, object] = {
            "candidate_id": f"candidate_{sample_index:04d}",
            "ok": "true",
            "touchstone_path": str(touchstone),
            "qp_center": real[2],
            "qs_center": min(25.0, real[2] + 0.1),
        }
        for geometry_index in range(10):
            row[f"geom__g{geometry_index}"] = sample_index + geometry_index * 0.001
        for feature_index, feature in enumerate(FEATURES):
            row[feature] = real[feature_index]
            row[f"pred_{feature}"] = predicted[feature_index]
        rows.append(row)
    path = root / f"pairs_{mode}.csv"
    _write_csv(path, rows)
    return path


def test_systematic_monotonic_bias_is_approved_only_after_geometry_holdout(tmp_path):
    audit = _load("audit_proxy_to_real_physical_feature_calibration.py")
    paired = _paired_rows(tmp_path, count=240, mode="biased")
    out_dir = tmp_path / "audit"

    assert audit.main(["--paired-csv", str(paired), "--out-dir", str(out_dir)]) == 0
    summary = json.loads((out_dir / "proxy_to_real_physical_calibration_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "USE_CALIBRATION_FOR_ACQUISITION_ONLY"
    assert summary["eligible_for_selector"] is True
    assert summary["split"]["train_group_sha256"] != summary["split"]["holdout_group_sha256"]
    assert summary["improvements"]["range_normalized_mae_relative_improvement"] > 0.5
    assert summary["improvements"]["mean_one_d_bin_accuracy_delta"] > 0.1
    assert summary["deployment_mapping"]
    assert (out_dir / "proxy_to_real_physical_calibration_holdout.png").is_file()


def test_already_calibrated_proxy_retains_raw_predictions(tmp_path):
    audit = _load("audit_proxy_to_real_physical_feature_calibration.py")
    paired = _paired_rows(tmp_path, count=160, mode="already_calibrated")
    out_dir = tmp_path / "audit"

    assert audit.main(["--paired-csv", str(paired), "--out-dir", str(out_dir)]) == 0
    summary = json.loads((out_dir / "proxy_to_real_physical_calibration_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "KEEP_RAW_PREDICTIONS"
    assert summary["eligible_for_selector"] is False
    assert summary["deployment_mapping"] is None


def test_distribution_shift_on_holdout_rejects_train_only_bias_correction(tmp_path):
    audit = _load("audit_proxy_to_real_physical_feature_calibration.py")
    rng = np.random.default_rng(71)
    rows = []
    group_keys = []
    for index in range(200):
        predicted = np.asarray([rng.uniform(lower, upper) for lower, upper in RANGES], dtype=float)
        row: dict[str, object] = {"candidate_id": f"shift_{index:04d}", "ok": "true"}
        geometry_fields = []
        for geometry_index in range(10):
            field = f"geom__g{geometry_index}"
            row[field] = index + geometry_index * 0.001
            geometry_fields.append(field)
        for feature_index, feature in enumerate(FEATURES):
            row[f"pred_{feature}"] = predicted[feature_index]
        rows.append(row)
        group_keys.append(audit._geometry_group(row, sorted(geometry_fields)))
    holdout = audit._holdout_groups(sorted(group_keys), 2026071101, 0.25, 20)
    for index, (row, group_key) in enumerate(zip(rows, group_keys)):
        predicted = np.asarray([float(row[f"pred_{feature}"]) for feature in FEATURES])
        if group_key in holdout:
            real = predicted
        else:
            real = np.asarray(
                [
                    0.70 * predicted[0] + 0.25,
                    0.72 * predicted[1] + 0.22,
                    0.70 * predicted[2] + 2.0,
                    0.70 * predicted[3] + 0.08,
                ]
            )
        real = np.clip(real, [item[0] for item in RANGES], [item[1] for item in RANGES])
        touchstone = tmp_path / f"shift_{index:04d}.s4p"
        touchstone.write_text("! nonempty synthetic fixture, not scientific evidence\n", encoding="ascii")
        row["touchstone_path"] = str(touchstone)
        row["qp_center"] = real[2]
        row["qs_center"] = min(25.0, real[2] + 0.1)
        for feature_index, feature in enumerate(FEATURES):
            row[feature] = real[feature_index]
    paired = tmp_path / "shifted_pairs.csv"
    _write_csv(paired, rows)
    out_dir = tmp_path / "audit"

    assert audit.main(["--paired-csv", str(paired), "--out-dir", str(out_dir)]) == 0
    summary = json.loads((out_dir / "proxy_to_real_physical_calibration_summary.json").read_text())
    assert summary["decision"] == "KEEP_RAW_PREDICTIONS"
    assert summary["eligible_for_selector"] is False
    assert summary["improvements"]["range_normalized_mae_relative_improvement"] < 0.0


def test_latest_real_emx_round_is_a_distribution_shift_holdout(tmp_path):
    audit = _load("audit_proxy_to_real_physical_feature_calibration.py")
    history_dir = tmp_path / "history"
    latest_dir = tmp_path / "latest"
    history_dir.mkdir()
    latest_dir.mkdir()
    history = _paired_rows(history_dir, count=120, mode="biased")
    latest = _paired_rows(latest_dir, count=80, mode="already_calibrated", offset=1000)
    out_dir = tmp_path / "audit"

    assert audit.main(
        [
            "--paired-csv",
            str(history),
            "--paired-csv",
            str(latest),
            "--out-dir",
            str(out_dir),
            "--holdout-mode",
            "latest-source",
        ]
    ) == 0
    summary = json.loads((out_dir / "proxy_to_real_physical_calibration_summary.json").read_text())
    assert summary["split"]["holdout_mode"] == "latest-source"
    assert summary["split"]["train_geometry_count"] == 120
    assert summary["split"]["holdout_geometry_count"] == 80
    assert summary["decision"] == "KEEP_RAW_PREDICTIONS"
    assert summary["eligible_for_selector"] is False


def test_insufficient_real_emx_pairs_waits_without_mapping(tmp_path):
    audit = _load("audit_proxy_to_real_physical_feature_calibration.py")
    paired = _paired_rows(tmp_path, count=12, mode="biased")
    out_dir = tmp_path / "audit"

    assert audit.main(["--paired-csv", str(paired), "--out-dir", str(out_dir)]) == 2
    summary = json.loads((out_dir / "proxy_to_real_physical_calibration_summary.json").read_text())
    assert summary["overall_status"] == "WAITING"
    assert summary["decision"] == "WAIT_FOR_MORE_REAL_EMX_PAIRS"
    assert summary["eligible_for_selector"] is False


def _write_selection_plan(path: Path) -> None:
    target: dict[str, object] = {"rank": 1, "bin_key": "target", "recommended_new_samples": 1}
    bounds = {
        "lp_nh_center": (0.5, 1.5),
        "ls_nh_center": (0.5, 1.5),
        "q_center": (15.0, 25.0),
        "k_abs_center": (0.0, 0.4),
    }
    for feature, (lower, upper) in bounds.items():
        target[f"{feature}__min"] = lower
        target[f"{feature}__max"] = upper
        target[f"{feature}__target"] = 0.5 * (lower + upper)
    _write_csv(path / "physical_feature_acquisition_targets.csv", [target])


def _approved_mapping(path: Path, *, approved: bool = True) -> None:
    mapping = {}
    for feature in FEATURES:
        if feature == "q_center":
            mapping[feature] = {"input_knots": [5.0, 25.0], "output_knots": [15.0, 25.0]}
        else:
            lower, upper = RANGES[FEATURES.index(feature)]
            mapping[feature] = {"input_knots": [lower, upper], "output_knots": [lower, upper]}
    payload = {
        "schema": "rfic_physical_feature_prediction_calibration_v1",
        "overall_status": "PASS",
        "decision": "USE_CALIBRATION_FOR_ACQUISITION_ONLY" if approved else "KEEP_RAW_PREDICTIONS",
        "eligible_for_selector": approved,
        "feature_columns": list(FEATURES),
        "deployment_mapping": mapping if approved else None,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_selector_uses_only_approved_mapping_and_preserves_raw_proxy_values(tmp_path):
    selector = _load("select_physical_feature_targeted_candidate_geometries.py")
    plan = tmp_path / "plan"
    _write_selection_plan(plan)
    candidates = tmp_path / "candidates.csv"
    row = {
        "candidate_id": "candidate-1",
        "pred_lp_nh_center": 1.0,
        "pred_ls_nh_center": 1.0,
        "pred_q_center": 10.0,
        "pred_k_abs_center": 0.2,
        "geom__line_width_um": 6.0,
    }
    _write_csv(candidates, [row])
    calibration = tmp_path / "calibration.json"
    _approved_mapping(calibration)
    out_dir = tmp_path / "selection"

    assert selector.main(
        [
            "--plan-dir",
            str(plan),
            "--candidate-csv",
            str(candidates),
            "--out-dir",
            str(out_dir),
            "--feature-columns",
            ",".join(FEATURES),
            "--max-total",
            "1",
            "--prediction-calibration-json",
            str(calibration),
        ]
    ) == 0
    summary = json.loads((out_dir / "physical_feature_targeted_candidate_selection_summary.json").read_text())
    assert summary["prediction_calibration"]["mode"] == "approved_holdout_isotonic_calibration"
    rows = list(csv.DictReader((out_dir / "physical_feature_targeted_candidate_selection.csv").open()))
    assert len(rows) == 1
    assert float(rows[0]["pred_q_center"]) == pytest.approx(17.5)
    assert float(rows[0]["candidate__pred_q_center"]) == pytest.approx(10.0)
    assert float(rows[0]["candidate__calibrated_pred_q_center"]) == pytest.approx(17.5)


def test_selector_rejects_unapproved_mapping_instead_of_silently_using_it(tmp_path):
    selector = _load("select_physical_feature_targeted_candidate_geometries.py")
    plan = tmp_path / "plan"
    _write_selection_plan(plan)
    candidates = tmp_path / "candidates.csv"
    _write_csv(
        candidates,
        [
            {
                "candidate_id": "candidate-1",
                "pred_lp_nh_center": 1.0,
                "pred_ls_nh_center": 1.0,
                "pred_q_center": 10.0,
                "pred_k_abs_center": 0.2,
            }
        ],
    )
    calibration = tmp_path / "calibration.json"
    _approved_mapping(calibration, approved=False)
    out_dir = tmp_path / "selection"

    assert selector.main(
        [
            "--plan-dir",
            str(plan),
            "--candidate-csv",
            str(candidates),
            "--out-dir",
            str(out_dir),
            "--feature-columns",
            ",".join(FEATURES),
            "--max-total",
            "1",
            "--prediction-calibration-json",
            str(calibration),
        ]
    ) == 2
    summary = json.loads((out_dir / "physical_feature_targeted_candidate_selection_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["prediction_calibration"]["eligible_for_selector"] is False


def test_prediction_provenance_survives_geometry_queue_and_real_emx_row_metadata():
    materializer = _load("materialize_physical_feature_targeted_s4p_queue.py")
    runner = _load("run_candidate_queue_dataset.py")
    selected: dict[str, object] = {
        "candidate_id": "original-candidate",
        "selection_rank": 3,
        "selection_source": "four_d_target_bin",
        "prediction_value_source": "approved_holdout_isotonic_calibration",
        "prediction_calibration_sha256": "a" * 64,
    }
    for index, field in enumerate(materializer.GEOMETRY_FIELDS):
        selected[f"candidate__geom__{field}"] = 10.0 + index
    selected["candidate__geom__primary_width_um"] = selected["candidate__geom__line_width_um"]
    selected["candidate__geom__secondary_width_um"] = selected["candidate__geom__line_width_um"]
    for index, feature in enumerate(FEATURES):
        selected[f"pred_{feature}"] = 1.0 + index
        selected[f"raw_pred_{feature}"] = 0.9 + index
        selected[f"calibrated_pred_{feature}"] = 1.0 + index
        selected[f"candidate__pred_uncertainty_{feature}"] = 0.01 + 0.01 * index
    selected["candidate__pred_neighbor_mean_distance"] = 0.125
    selected["candidate__pred_k_neighbors"] = 8

    queue, errors = materializer._materialize_rows(
        [selected], argparse.Namespace(candidate_id_prefix="queue", sync_widths=True)
    )
    assert errors == []
    assert queue[0]["source_candidate_id"] == "original-candidate"
    assert queue[0]["raw_pred_lp_nh_center"] == pytest.approx(0.9)
    assert queue[0]["pred_uncertainty_lp_nh_center"] == pytest.approx(0.01)
    assert queue[0]["pred_neighbor_mean_distance"] == pytest.approx(0.125)
    metadata = runner._queue_metadata(queue[0], 0)
    assert metadata["queue__raw_pred_lp_nh_center"] == pytest.approx(0.9)
    assert metadata["queue__pred_uncertainty_lp_nh_center"] == pytest.approx(0.01)
    assert metadata["queue__pred_neighbor_mean_distance"] == pytest.approx(0.125)
    assert metadata["queue__prediction_calibration_sha256"] == "a" * 64
    assert metadata["queue__geometry_fingerprint_sha256"] == queue[0]["geometry_fingerprint_sha256"]
    assert metadata["queue__geometry_fingerprint_schema"] == materializer.GEOMETRY_FINGERPRINT_SCHEMA
    assert metadata["queue__geometry_fingerprint_quantization_um"] == pytest.approx(1.0e-6)


def test_calibration_loader_derives_q_and_k_abs_from_real_emx_dataset_columns(tmp_path):
    audit = _load("audit_proxy_to_real_physical_feature_calibration.py")
    touchstone = tmp_path / "real.s4p"
    touchstone.write_text("! nonempty synthetic fixture, not scientific evidence\n", encoding="ascii")
    row: dict[str, object] = {
        "ok": "true",
        "touchstone_path": str(touchstone),
        "lp_nh_center": 1.2,
        "ls_nh_center": 1.4,
        "qp_center": 12.0,
        "qs_center": 10.0,
        "k_center": -0.45,
        "queue__raw_pred_lp_nh_center": 1.1,
        "queue__raw_pred_ls_nh_center": 1.3,
        "queue__raw_pred_q_center": 9.5,
        "queue__raw_pred_k_abs_center": 0.4,
    }
    for index in range(10):
        row[f"geom__g{index}"] = 10.0 + index
    paired = tmp_path / "returned_dataset_rows.csv"
    _write_csv(paired, [row])

    loaded = audit._load_pairs(
        [paired], FEATURES, "pred_", dict(zip(FEATURES, RANGES)), True
    )
    assert loaded["stats"]["accepted_pair_row_count"] == 1
    group = next(iter(loaded["groups"].values()))
    assert group["real"].tolist() == pytest.approx([1.2, 1.4, 10.0, 0.45])
    assert group["predicted"].tolist() == pytest.approx([1.1, 1.3, 9.5, 0.4])
