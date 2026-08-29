from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_acquisition_ensemble import (
    CONTINUOUS_FEATURES,
    DEFAULT_MEMBER_SEEDS,
    PREDICTED_FEATURES,
    deterministic_geometry_split,
    ensemble_mean_and_uncertainty,
    evaluate_ensemble,
    fit_random_feature_member,
    fit_uncertainty_calibration,
    load_member,
    predict_member,
    save_member,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    GEOMETRY_FIELDS,
    canonical_geometry_sha256,
    contract_fingerprint,
)
from rfic_transformer_inverse_design.campaigns.broadband56_geometry_coverage import (
    geometry_bounds_payload,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _RoundSpecFixture:
    def __init__(self, accepted_start: int) -> None:
        self.accepted_start = accepted_start

    def as_dict(self) -> dict[str, int]:
        return {"accepted_start": self.accepted_start, "accepted_target": self.accepted_start + 5}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    assert rows
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _file_evidence(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_checkpoint_fixture(
    root: Path,
    *,
    accepted_count: int,
) -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = str(contract.get("contract_fingerprint_sha256") or contract_fingerprint(contract))
    bounds = {name: [0.0, 1.0] for name in GEOMETRY_FIELDS}
    bounds_path = root / "GEOMETRY_BOUNDS_FROZEN.json"
    _write_json(
        bounds_path,
        geometry_bounds_payload(
            bounds=bounds,
            contract_fingerprint_sha256=fingerprint,
        ),
    )

    rng = np.random.default_rng(2026082801)
    normalized = rng.uniform(0.02, 0.98, size=(accepted_count, len(GEOMETRY_FIELDS)))
    accepted_rows: list[dict[str, object]] = []
    feature_rows: list[dict[str, object]] = []
    for index, values in enumerate(normalized):
        geometry = {name: float(value) for name, value in zip(GEOMETRY_FIELDS, values)}
        geometry_id = f"synthetic_geometry_{index:06d}"
        geometry_sha = canonical_geometry_sha256(geometry)
        accepted_rows.append(
            {
                "geometry_id": geometry_id,
                "geometry_sha256": geometry_sha,
                "campaign_contract_fingerprint": fingerprint,
                "campaign_phase": "PHASE_A",
                **{f"geom__{name}": geometry[name] for name in GEOMETRY_FIELDS},
            }
        )
        for anchor in ANCHOR_FREQUENCIES_GHZ:
            xp = 10.0 + 160.0 * values[0] + 0.20 * anchor
            xs = 12.0 + 150.0 * values[1] + 0.15 * anchor
            qp = 3.0 + 25.0 * values[2] + 0.01 * anchor
            qs = 4.0 + 24.0 * values[3] + 0.01 * anchor
            feature_rows.append(
                {
                    "geometry_id": geometry_id,
                    "geometry_sha256": geometry_sha,
                    "campaign_contract_fingerprint": fingerprint,
                    "frequency_hz": int(anchor * 1_000_000_000),
                    "xp_ohm": xp,
                    "xs_ohm": xs,
                    "qp": qp,
                    "qs": qs,
                    "qmin": min(qp, qs),
                    "k_abs": 0.05 + 0.75 * values[4],
                    "broadband_descriptor_valid": "true",
                }
            )

    accepted_path = root / "accepted_geometry.csv"
    feature_path = root / "broadband_features_long.csv"
    _write_csv(accepted_path, accepted_rows)
    _write_csv(feature_path, feature_rows)

    audit_dir = root / "checkpoint_audit"
    audit_dir.mkdir()
    status_path = audit_dir / "CHECKPOINT_STATUS.json"
    _write_json(
        status_path,
        {
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "checkpoint_status": f"ROUND_{accepted_count}_COMPLETE",
            "accepted_geometries": accepted_count,
            "s4p_artifacts": accepted_count,
            "geometry_frequency_rows": accepted_count * 56,
        },
    )
    receipt_path = audit_dir / "CHECKPOINT_RECEIPT.json"
    _write_json(
        receipt_path,
        {
            "overall_status": "PASS",
            "decision": "USE_CHECKPOINT",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "expected_accepted": accepted_count,
            "checks": [{"name": "synthetic_checkpoint_fixture", "pass": True}],
            "inputs": {
                "accepted_geometries": _file_evidence(accepted_path),
                "long_features": _file_evidence(feature_path),
                "geometry_bounds": _file_evidence(bounds_path),
            },
            "outputs": {"checkpoint_status": _file_evidence(status_path)},
        },
    )
    return {
        "fingerprint": fingerprint,
        "bounds": bounds,
        "bounds_path": bounds_path,
        "accepted_rows": accepted_rows,
        "accepted_path": accepted_path,
        "feature_path": feature_path,
        "audit_dir": audit_dir,
    }


def _synthetic_data(count: int = 120) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    rng = np.random.default_rng(20260828)
    geometry = rng.uniform(0.0, 1.0, size=(count, len(GEOMETRY_FIELDS)))
    anchors = np.asarray(ANCHOR_FREQUENCIES_GHZ, dtype=float)[None, :]
    xp = 10.0 + 180.0 * geometry[:, 0, None] + 0.20 * anchors
    xs = 12.0 + 170.0 * geometry[:, 1, None] + 0.15 * anchors
    qp = 3.0 + 25.0 * geometry[:, 2, None] + 0.01 * anchors
    qs = 4.0 + 24.0 * geometry[:, 3, None] + 0.01 * anchors
    qmin = np.minimum(qp, qs)
    k_abs = 0.05 + 0.75 * geometry[:, 4, None] + np.zeros_like(anchors)
    continuous = np.stack((xp, xs, qp, qs, qmin, k_abs), axis=2)
    validity = (geometry[:, 5, None] + 0.002 * anchors) > 0.08
    continuous[~validity] = np.nan
    hashes = [hashlib.sha256(f"geometry-{index}".encode("ascii")).hexdigest() for index in range(count)]
    return geometry, continuous, validity, hashes


def test_geometry_split_is_exact_disjoint_deterministic_and_identity_bound() -> None:
    _, _, _, hashes = _synthetic_data()

    first = deterministic_geometry_split(hashes, split_seed=20260828)
    second = deterministic_geometry_split(hashes, split_seed=20260828)

    assert first.train_indices.tolist() == second.train_indices.tolist()
    assert first.calibration_indices.tolist() == second.calibration_indices.tolist()
    assert first.validation_indices.tolist() == second.validation_indices.tolist()
    combined = np.concatenate(
        (first.train_indices, first.calibration_indices, first.validation_indices)
    )
    assert sorted(combined.tolist()) == list(range(len(hashes)))
    assert len(set(combined.tolist())) == len(hashes)
    assert len(first.train_hash_sha256) == 64
    assert len(first.calibration_hash_sha256) == 64
    assert len(first.validation_hash_sha256) == 64


def test_geometry_split_rejects_duplicate_identity() -> None:
    _, _, _, hashes = _synthetic_data()
    hashes[-1] = hashes[0]

    with pytest.raises(ValueError, match="unique SHA-256"):
        deterministic_geometry_split(hashes, split_seed=20260828)


def test_five_member_ensemble_calibrates_evaluates_and_round_trips(tmp_path: Path) -> None:
    geometry, continuous, validity, hashes = _synthetic_data()
    split = deterministic_geometry_split(hashes, split_seed=20260828)
    members = [
        fit_random_feature_member(
            geometry_normalized=geometry,
            continuous_targets=continuous,
            validity_targets=validity,
            train_indices=split.train_indices,
            seed=seed,
            hidden_features=16,
            ridge=1.0e-3,
        )
        for seed in DEFAULT_MEMBER_SEEDS
    ]

    calibration_predictions = np.stack(
        [predict_member(member, geometry[split.calibration_indices]) for member in members]
    )
    calibration = fit_uncertainty_calibration(
        member_predictions=calibration_predictions,
        continuous_targets=continuous[split.calibration_indices],
        validity_targets=validity[split.calibration_indices],
    )
    validation_predictions = np.stack(
        [predict_member(member, geometry[split.validation_indices]) for member in members]
    )
    evaluation = evaluate_ensemble(
        member_predictions=validation_predictions,
        continuous_targets=continuous[split.validation_indices],
        validity_targets=validity[split.validation_indices],
        calibration=calibration,
        validation_limits={
            "mean_range_normalized_mae_max": 1.0,
            "worst_feature_range_normalized_mae_max": 1.0,
            "minimum_scaled_interval_coverage": 0.0,
            "validity_brier_max": 1.0,
            "validity_accuracy_min": 0.0,
            "minimum_nonzero_disagreement_fraction": 0.0,
        },
    )
    mean, uncertainty = ensemble_mean_and_uncertainty(validation_predictions, calibration)

    assert evaluation["overall_status"] == "PASS"
    assert mean.shape == (
        len(split.validation_indices),
        len(ANCHOR_FREQUENCIES_GHZ),
        len(PREDICTED_FEATURES),
    )
    assert uncertainty.shape == mean.shape
    assert np.all(uncertainty >= 0.0)
    qp_index = CONTINUOUS_FEATURES.index("qp")
    qs_index = CONTINUOUS_FEATURES.index("qs")
    qmin_index = CONTINUOUS_FEATURES.index("qmin")
    assert np.allclose(mean[:, :, qmin_index], np.minimum(mean[:, :, qp_index], mean[:, :, qs_index]))

    member_path = tmp_path / "member_0.npz"
    save_member(member_path, members[0])
    restored = load_member(member_path)
    assert restored.seed == members[0].seed
    assert np.array_equal(
        predict_member(restored, geometry[split.validation_indices]),
        predict_member(members[0], geometry[split.validation_indices]),
    )


def test_uncertainty_application_rejects_missing_feature_scale() -> None:
    geometry, continuous, validity, hashes = _synthetic_data()
    split = deterministic_geometry_split(hashes, split_seed=20260828)
    predictions = np.zeros(
        (
            5,
            len(split.validation_indices),
            len(ANCHOR_FREQUENCIES_GHZ),
            len(PREDICTED_FEATURES),
        )
    )

    with pytest.raises(ValueError, match="scales are missing"):
        ensemble_mean_and_uncertainty(predictions, {"feature_scales": {}})


def test_checkpoint_bound_trainer_writes_five_member_pass_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_checkpoint_fixture(tmp_path, accepted_count=40)
    trainer = _load_script("train_broadband56_acquisition_ensemble")
    real_evaluate = evaluate_ensemble

    def evaluate_with_small_fixture_limits(**kwargs):
        return real_evaluate(
            **kwargs,
            validation_limits={
                "mean_range_normalized_mae_max": 1.0,
                "worst_feature_range_normalized_mae_max": 1.0,
                "minimum_scaled_interval_coverage": 0.0,
                "validity_brier_max": 1.0,
                "validity_accuracy_min": 0.0,
                "minimum_nonzero_disagreement_fraction": 0.0,
            },
        )

    monkeypatch.setattr(trainer, "MINIMUM_ACQUISITION_TRAINING_COUNT", 10)
    monkeypatch.setattr(
        trainer,
        "adaptive_round_spec",
        lambda count: _RoundSpecFixture(count),
    )
    monkeypatch.setattr(trainer, "evaluate_ensemble", evaluate_with_small_fixture_limits)
    output_dir = tmp_path / "ensemble_output"

    exit_code = trainer.main(
        [
            "--contract",
            str(CONTRACT),
            "--checkpoint-audit-dir",
            str(fixture["audit_dir"]),
            "--out-dir",
            str(output_dir),
            "--hidden-features",
            "8",
        ]
    )

    assert exit_code == 0
    receipt_path = output_dir / "ENSEMBLE_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["overall_status"] == "PASS"
    assert receipt["decision"] == "USE_FOR_CANDIDATE_PRIORITY_ONLY"
    assert receipt["source_accepted_count"] == 40
    assert receipt["member_count"] == 5
    assert receipt["training_geometry_count"] == 32
    assert receipt["calibration_geometry_count"] == 4
    assert receipt["validation_geometry_count"] == 4
    assert receipt["predictions_are_final_labels"] is False
    assert receipt["source_accepted_geometries"]["sha256"] == _sha256(
        fixture["accepted_path"]
    )
    assert receipt["source_long_features"]["sha256"] == _sha256(fixture["feature_path"])
    assert len({member["seed"] for member in receipt["members"]}) == 5
    assert len({member["model_sha256"] for member in receipt["members"]}) == 5
    assert all(Path(member["model_file"]["path"]).is_file() for member in receipt["members"])


def test_checkpoint_ensemble_predictor_writes_candidate_priority_only_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_checkpoint_fixture(tmp_path, accepted_count=40)
    trainer = _load_script("train_broadband56_acquisition_ensemble")
    real_evaluate = evaluate_ensemble

    def evaluate_with_small_fixture_limits(**kwargs):
        return real_evaluate(
            **kwargs,
            validation_limits={
                "mean_range_normalized_mae_max": 1.0,
                "worst_feature_range_normalized_mae_max": 1.0,
                "minimum_scaled_interval_coverage": 0.0,
                "validity_brier_max": 1.0,
                "validity_accuracy_min": 0.0,
                "minimum_nonzero_disagreement_fraction": 0.0,
            },
        )

    monkeypatch.setattr(trainer, "MINIMUM_ACQUISITION_TRAINING_COUNT", 10)
    monkeypatch.setattr(
        trainer,
        "adaptive_round_spec",
        lambda count: _RoundSpecFixture(count),
    )
    monkeypatch.setattr(trainer, "evaluate_ensemble", evaluate_with_small_fixture_limits)
    ensemble_dir = tmp_path / "ensemble_output"
    assert (
        trainer.main(
            [
                "--contract",
                str(CONTRACT),
                "--checkpoint-audit-dir",
                str(fixture["audit_dir"]),
                "--out-dir",
                str(ensemble_dir),
                "--hidden-features",
                "8",
            ]
        )
        == 0
    )
    ensemble_path = ensemble_dir / "ENSEMBLE_RECEIPT.json"

    predictor = _load_script("predict_broadband56_acquisition_candidates")
    monkeypatch.setattr(predictor, "ADAPTIVE_BATCH_SIZE", 5)
    monkeypatch.setattr(predictor, "MINIMUM_CANDIDATE_POOL_FACTOR", 2)
    round_dir = tmp_path / "round"
    round_dir.mkdir()
    round_contract_path = round_dir / "ADAPTIVE_ROUND_CONTRACT.json"
    _write_json(
        round_contract_path,
        {
            "overall_status": "PASS",
            "decision": "USE_ENSEMBLE_ACQUISITION",
            "campaign_id": CAMPAIGN_ID,
            "campaign_contract_fingerprint": fixture["fingerprint"],
            "acquisition_mode": "ENSEMBLE_ACQUISITION",
            "candidate_selection_policy": predictor.selection_policy_contract(),
            "round": {"accepted_start": 40, "accepted_target": 45},
            "preceding_real_emx_audit": {
                "geometry_bounds_path": str(fixture["bounds_path"]),
                "geometry_bounds_sha256": _sha256(fixture["bounds_path"]),
                "accepted_geometries_path": str(fixture["accepted_path"]),
                "accepted_geometries_sha256": _sha256(fixture["accepted_path"]),
            },
            "ensemble_gate": {"receipt": _file_evidence(ensemble_path)},
        },
    )
    _write_json(
        round_dir / "ADAPTIVE_ROUND_RECEIPT.json",
        {
            "overall_status": "PASS",
            "decision": "STAGE_ADAPTIVE_CANDIDATES",
            "campaign_id": CAMPAIGN_ID,
            "campaign_contract_fingerprint": fixture["fingerprint"],
            "outputs": {"adaptive_round_contract": _file_evidence(round_contract_path)},
        },
    )

    accepted_hashes = {
        str(row["geometry_sha256"]) for row in fixture["accepted_rows"]
    }
    candidate_rows: list[dict[str, object]] = []
    rng = np.random.default_rng(2026082802)
    while len(candidate_rows) < 10:
        values = rng.uniform(0.01, 0.99, size=len(GEOMETRY_FIELDS))
        geometry = {name: float(value) for name, value in zip(GEOMETRY_FIELDS, values)}
        geometry_sha = canonical_geometry_sha256(geometry)
        if geometry_sha in accepted_hashes:
            continue
        index = len(candidate_rows)
        candidate_rows.append(
            {
                "candidate_id": f"candidate_{index:06d}",
                "campaign_id": CAMPAIGN_ID,
                "campaign_contract_fingerprint": fixture["fingerprint"],
                "geometry_sha256": geometry_sha,
                "candidate_generation_mode": "synthetic_test_only",
                "candidate_generation_seed": 2026082802,
                "analytical_status": "PASS",
                "topology_status": "PASS",
                "top_metal_drc_status": "PASS",
                **{f"geom__{name}": geometry[name] for name in GEOMETRY_FIELDS},
            }
        )
    candidate_path = tmp_path / "candidate_pool.csv"
    _write_csv(candidate_path, candidate_rows)
    prediction_dir = tmp_path / "prediction_output"

    exit_code = predictor.main(
        [
            "--contract",
            str(CONTRACT),
            "--round-dir",
            str(round_dir),
            "--ensemble-receipt",
            str(ensemble_path),
            "--candidate-csv",
            str(candidate_path),
            "--out-dir",
            str(prediction_dir),
            "--batch-size",
            "3",
        ]
    )

    assert exit_code == 0
    prediction_path = prediction_dir / "broadband56_candidate_pool_with_ensemble_predictions.csv"
    with prediction_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 10
    assert all(row["predictions_are_labels"] == "false" for row in rows)
    assert all(row["label_status"] == "UNEVALUATED_AWAITING_FRESH_REAL_EMX" for row in rows)
    assert all(
        column in rows[0]
        for column in predictor.required_prediction_columns()
    )
    for row in rows:
        for anchor in ANCHOR_FREQUENCIES_GHZ:
            qp = float(row[predictor.prediction_column("qp", anchor)])
            qs = float(row[predictor.prediction_column("qs", anchor)])
            qmin = float(row[predictor.prediction_column("qmin", anchor)])
            assert qmin == pytest.approx(min(qp, qs), abs=1.0e-9)
    receipt = json.loads(
        (prediction_dir / "CANDIDATE_PREDICTION_RECEIPT.json").read_text(encoding="utf-8")
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["decision"] == "USE_FOR_ADAPTIVE_CANDIDATE_SELECTION_ONLY"

    tampered_ensemble = json.loads(ensemble_path.read_text(encoding="utf-8"))
    tampered_ensemble["members"][0]["seed"] = 999_999
    _write_json(ensemble_path, tampered_ensemble)
    round_contract = json.loads(round_contract_path.read_text(encoding="utf-8"))
    round_contract["ensemble_gate"]["receipt"] = _file_evidence(ensemble_path)
    _write_json(round_contract_path, round_contract)
    round_receipt_path = round_dir / "ADAPTIVE_ROUND_RECEIPT.json"
    round_receipt = json.loads(round_receipt_path.read_text(encoding="utf-8"))
    round_receipt["outputs"]["adaptive_round_contract"] = _file_evidence(round_contract_path)
    _write_json(round_receipt_path, round_receipt)
    rejected_dir = tmp_path / "prediction_metadata_mismatch"
    assert (
        predictor.main(
            [
                "--contract",
                str(CONTRACT),
                "--round-dir",
                str(round_dir),
                "--ensemble-receipt",
                str(ensemble_path),
                "--candidate-csv",
                str(candidate_path),
                "--out-dir",
                str(rejected_dir),
            ]
        )
        == 2
    )
    rejected = json.loads(
        (rejected_dir / "CANDIDATE_PREDICTION_RECEIPT.json").read_text(encoding="utf-8")
    )
    assert rejected["overall_status"] == "FAIL"
    assert not (rejected_dir / "broadband56_candidate_pool_with_ensemble_predictions.csv").exists()
    assert any(
        check["name"] == "ensemble_members_load"
        and "metadata differs" in str(check["detail"])
        for check in rejected["checks"]
    )
