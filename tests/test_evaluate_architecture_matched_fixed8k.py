from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "evaluate_architecture_matched_fixed8k.py"
SPEC = importlib.util.spec_from_file_location(
    "evaluate_architecture_matched_fixed8k_test_module", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_trainer(path: Path) -> None:
    path.write_text(
        """import numpy as np

def _predict(x, weights, biases):
    value = np.asarray(x, dtype=float)
    for weight, bias in zip(weights, biases):
        value = value @ weight + bias
    return value

def _predict_inverse(x, weights, biases, lower, upper, **kwargs):
    raw = _predict(x, weights, biases)
    sigmoid = 1.0 / (1.0 + np.exp(-np.clip(raw, -40.0, 40.0)))
    return lower[None, :] + (upper - lower)[None, :] * sigmoid
""",
        encoding="utf-8",
    )


def _write_weights(path: Path, forward_offset: float) -> None:
    topology = {
        "available": True,
        "power_line_port_ground_overlap": {
            "enabled": True,
            "bar_offset_um": 12.0,
            "shield_opening_clearance_um": 10.0,
            "expected_overlap_um": 10.0,
            "training_safety_margin_um": 0.0,
        },
    }
    np.savez(
        path,
        forward_weight_0=np.zeros((10, 2), dtype=float),
        forward_bias_0=np.zeros(2, dtype=float),
        forward_weight_1=np.zeros((2, 4), dtype=float),
        forward_bias_1=np.full(4, forward_offset, dtype=float),
        inverse_weight_0=np.zeros((4, 2), dtype=float),
        inverse_bias_0=np.zeros(2, dtype=float),
        inverse_weight_1=np.zeros((2, 10), dtype=float),
        inverse_bias_1=np.zeros(10, dtype=float),
        normalization__x_mean=np.asarray([1.0, 1.5, 10.0, 0.4]),
        normalization__x_scale=np.asarray([0.5, 0.5, 2.0, 0.2]),
        normalization__y_mean=np.zeros(10, dtype=float),
        normalization__y_scale=np.ones(10, dtype=float),
        normalization__geometry_lower=np.full(10, -1.0, dtype=float),
        normalization__geometry_upper=np.full(10, 1.0, dtype=float),
        normalization__response_loss_dimension_weights=np.ones(4, dtype=float),
        inverse_geometry_projection__mode=np.asarray(
            ["hard_feasible_topology_v1"]
        ),
        inverse_geometry_projection__topology_contract_json=np.asarray(
            [json.dumps(topology, sort_keys=True)]
        ),
    )


def _contract(trainer_sha: str) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "physical_feature_tandem_cross_checkpoint_v1",
        "trainer_implementation_sha256": trainer_sha,
        "input_columns": list(MODULE.INPUT_COLUMNS),
        "geometry_columns": list(MODULE.GEOMETRY_COLUMNS),
        "architecture": {
            "forward_hidden_widths": [2],
            "inverse_hidden_widths": [2],
            "inverse_geometry_projection": "hard_feasible_topology_v1",
        },
        "loss": {
            "q_target_semantics": "minimum",
            "q_minimum_margin_physical": 2.0,
        },
    }
    value["fingerprint_sha256"] = MODULE._canonical_sha256(value)
    return value


def _summary(
    *,
    model_id: str,
    training_count: int,
    weights_sha: str,
    trainer_sha: str,
) -> dict[str, object]:
    if training_count == 100_000:
        counts = {"train": 80_000, "validation": 10_000, "test": 10_000}
    else:
        counts = {"train": 160_000, "validation": 20_000, "test": 20_000}
    return {
        "execution_status": "PASS",
        "overall_status": "COMPLETE_REVIEW_REQUIRED",
        "quality_status": "REVIEW_REQUIRED",
        "eligible_for_checkpoint_model_acceptance": False,
        "eligible_for_model_success_claim": False,
        "model_id": model_id,
        "training_count": training_count,
        "weights_npz_sha256": weights_sha,
        "out_dir": f"/private/run/{model_id}",
        "input_columns": list(MODULE.INPUT_COLUMNS),
        "geometry_columns": list(MODULE.GEOMETRY_COLUMNS),
        "arguments": {
            "training_csv": f"/private/data/{training_count}.csv",
            "out_dir": f"/private/run/{model_id}",
            "min_training_rows": training_count,
            "max_prediction_rows": 100_000,
            "forward_hidden_widths": "2",
            "inverse_hidden_widths": "2",
            "inverse_geometry_projection": "hard_feasible_topology_v1",
            "q_target_semantics": "minimum",
            "q_minimum_margin_physical": 2.0,
            "local_refinement_steps": 0,
            "local_refinement_starts": 1,
            "seed": 20260713,
            "batch_size": 4096,
        },
        "method": {
            "geometry_output_constraint": "hard_feasible_topology_v1",
            "geometry_output_constraint_is_single_pass": True,
            "geometry_output_constraint_is_posthoc_repair": False,
        },
        "model_comparison_contract": _contract(trainer_sha),
        "split_audit": {"row_counts": counts},
    }


def _fixture(tmp_path: Path) -> tuple[list[str], Path]:
    trainer = tmp_path / "exact_trainer.py"
    _write_trainer(trainer)
    trainer_sha = _sha256(trainer)

    weights_100k = tmp_path / "weights_100k.npz"
    weights_200k = tmp_path / "weights_200k.npz"
    _write_weights(weights_100k, 0.0)
    _write_weights(weights_200k, 0.1)

    summary_100k = tmp_path / "summary_100k.json"
    summary_200k = tmp_path / "summary_200k.json"
    _write_json(
        summary_100k,
        _summary(
            model_id="synthetic_100k",
            training_count=100_000,
            weights_sha=_sha256(weights_100k),
            trainer_sha=trainer_sha,
        ),
    )
    _write_json(
        summary_200k,
        _summary(
            model_id="synthetic_200k",
            training_count=200_000,
            weights_sha=_sha256(weights_200k),
            trainer_sha=trainer_sha,
        ),
    )

    targets = tmp_path / "fixed_targets.json"
    _write_json(
        targets,
        {
            "schema": "direct_mlp_one_shot_targets_v1",
            "target_role": "nonadvisor_fixed_proxy_frame",
            "q_target_semantics": "minimum",
            "row_count": 3,
            "targets": [
                {
                    "target_id": "target_0002",
                    "Lp_nH": 1.0,
                    "Ls_nH": 1.5,
                    "Q_min": 10.0,
                    "K_abs": 0.2,
                },
                {
                    "target_id": "target_0001",
                    "Lp_nH": 1.2,
                    "Ls_nH": 1.4,
                    "Q_min": 11.0,
                    "K_abs": 0.8,
                },
                {
                    "target_id": "high_k_must_not_appear",
                    "Lp_nH": 2.0,
                    "Ls_nH": 2.0,
                    "Q_min": 20.0,
                    "K_abs": 0.9,
                },
            ],
        },
    )
    reference_contract = tmp_path / "reference_contract.json"
    _write_json(
        reference_contract,
        {
            "schema": "architecture_matched_reference_selection_contract_v1",
            "comparison_eligibility": {
                "reference_model_id": "synthetic_100k",
                "candidate_model_id": "synthetic_200k",
                "reference_role": (
                    "deployed_and_presented_seed20260713_not_final_global_winner"
                ),
                "reference_selection_status": "MISMATCH_FINAL_GLOBAL_WINNER",
                "advisor_comparison_eligible": False,
                "engineering_evaluation_allowed": True,
            },
        },
    )
    out_dir = tmp_path / "evaluation"
    argv = [
        "--model-100k-id",
        "synthetic_100k",
        "--model-100k-summary",
        str(summary_100k),
        "--model-100k-weights",
        str(weights_100k),
        "--model-100k-trainer-source",
        str(trainer),
        "--expected-model-100k-summary-sha256",
        _sha256(summary_100k),
        "--expected-model-100k-weights-sha256",
        _sha256(weights_100k),
        "--expected-model-100k-trainer-sha256",
        trainer_sha,
        "--model-200k-id",
        "synthetic_200k",
        "--model-200k-summary",
        str(summary_200k),
        "--model-200k-weights",
        str(weights_200k),
        "--model-200k-trainer-source",
        str(trainer),
        "--expected-model-200k-summary-sha256",
        _sha256(summary_200k),
        "--expected-model-200k-weights-sha256",
        _sha256(weights_200k),
        "--expected-model-200k-trainer-sha256",
        trainer_sha,
        "--targets-json",
        str(targets),
        "--expected-targets-sha256",
        _sha256(targets),
        "--reference-contract",
        str(reference_contract),
        "--expected-reference-contract-sha256",
        _sha256(reference_contract),
        "--out-dir",
        str(out_dir),
    ]
    return argv, out_dir


def test_metrics_include_required_feature_q_and_joint_statistics() -> None:
    target = np.asarray(
        [[1.0, 1.0, 10.0, 0.2], [2.0, 2.0, 20.0, 0.6]], dtype=float
    )
    prediction = target + np.asarray(
        [[1.0, -1.0, -2.0, 0.08], [-1.0, 1.0, 2.0, -0.08]],
        dtype=float,
    )
    metrics = MODULE._calculate_metrics(target, prediction)

    assert metrics["per_feature"]["Lp"]["bias"] == pytest.approx(0.0)
    assert metrics["per_feature"]["Lp"]["mae"] == pytest.approx(1.0)
    assert metrics["per_feature"]["K_abs"]["normalized_mae"] == pytest.approx(
        0.1
    )
    assert metrics["q_one_sided_shortfall"]["mean"] == pytest.approx(1.0)
    assert metrics["q_one_sided_shortfall"]["target_met_rate"] == pytest.approx(
        0.5
    )
    assert metrics["joint_normalized_error"]["mean"] > 0.0
    assert metrics["joint_q_shortfall_normalized_error"]["mean"] > 0.0


def test_end_to_end_synthetic_is_legacy_only_hash_bound_and_no_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_FRAME_ROWS", 3)
    monkeypatch.setattr(MODULE, "EXPECTED_LEGACY_ROWS", 2)
    monkeypatch.setattr(MODULE, "EXPECTED_INVERSE_ARCHITECTURE", (4, 2, 10))
    monkeypatch.setattr(MODULE, "EXPECTED_FORWARD_ARCHITECTURE", (10, 2, 4))
    monkeypatch.setattr(MODULE, "EXPECTED_PARAMETER_COUNT", 74)
    argv, out_dir = _fixture(tmp_path)
    target_hash_index = argv.index("--expected-targets-sha256") + 1
    monkeypatch.setattr(MODULE, "FROZEN_FIXED10K_SHA256", argv[target_hash_index])

    assert MODULE.main(argv) == 0
    assert {path.name for path in out_dir.iterdir()} == {
        "per_target_100k_predictions.csv",
        "per_target_200k_predictions.csv",
        "architecture_matched_comparison.csv",
        "evaluation_summary.json",
        "SHA256SUMS.txt",
    }

    for role in ("100k", "200k"):
        with (out_dir / f"per_target_{role}_predictions.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            rows = list(csv.DictReader(handle))
        assert [row["target_id"] for row in rows] == ["target_0002", "target_0001"]
        assert [row["fixed10k_original_row_index"] for row in rows] == ["0", "1"]
        assert all(row["panel"] == "legacy_k_le_0p8" for row in rows)
        assert all("high_k_must_not_appear" not in json.dumps(row) for row in rows)
        assert all(f"proxy_prediction__{name}" in rows[0] for name in ("lp", "ls", "qmin", "k_abs"))
        assert all(column in rows[0] for column in MODULE.GEOMETRY_COLUMNS)

    summary = json.loads((out_dir / "evaluation_summary.json").read_text())
    assert summary["comparison_design"]["selected_row_count"] == 2
    assert summary["comparison_design"]["high_k_extension_included"] is False
    assert summary["comparison_design"]["targets_regenerated"] is False
    assert summary["comparison_design"]["emx_run"] is False
    assert summary["evaluation_execution_status"] == "PASS"
    assert "overall_status" not in summary
    assert summary["advisor_comparison_eligible"] is False
    assert summary["engineering_evaluation_allowed"] is True
    assert (
        summary["reference_selection_status"]
        == "MISMATCH_FINAL_GLOBAL_WINNER"
    )
    assert summary["comparison_eligibility"]["reference_model_id"] == "synthetic_100k"
    assert summary["models"]["100k"]["summary_status"] == {
        "overall_status": "COMPLETE_REVIEW_REQUIRED",
        "execution_status": "PASS",
        "quality_status": "REVIEW_REQUIRED",
        "eligible_for_checkpoint_model_acceptance": False,
        "eligible_for_model_success_claim": False,
    }
    assert summary["metrics"]["100k"]["row_count"] == 2
    assert "extension" not in json.dumps(summary["metrics"]).lower()
    assert all("path" not in record for record in summary["sources"].values())
    assert "evaluation-only" in summary["scientific_boundary"]

    sha_lines = (out_dir / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
    assert len(sha_lines) == 4
    for line in sha_lines:
        digest, filename = line.split("  ", 1)
        assert digest == _sha256(out_dir / filename)

    with pytest.raises(FileExistsError, match="no-clobber"):
        MODULE.main(argv)


def test_hash_mismatch_fails_before_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_FRAME_ROWS", 3)
    monkeypatch.setattr(MODULE, "EXPECTED_LEGACY_ROWS", 2)
    monkeypatch.setattr(MODULE, "EXPECTED_INVERSE_ARCHITECTURE", (4, 2, 10))
    monkeypatch.setattr(MODULE, "EXPECTED_FORWARD_ARCHITECTURE", (10, 2, 4))
    monkeypatch.setattr(MODULE, "EXPECTED_PARAMETER_COUNT", 74)
    argv, out_dir = _fixture(tmp_path)
    target_hash_index = argv.index("--expected-targets-sha256") + 1
    monkeypatch.setattr(MODULE, "FROZEN_FIXED10K_SHA256", argv[target_hash_index])
    argv[target_hash_index] = "0" * 64

    with pytest.raises(ValueError, match="targets SHA-256 mismatch"):
        MODULE.main(argv)
    assert not out_dir.exists()


def test_max_prediction_rows_contract_drift_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_FRAME_ROWS", 3)
    monkeypatch.setattr(MODULE, "EXPECTED_LEGACY_ROWS", 2)
    monkeypatch.setattr(MODULE, "EXPECTED_INVERSE_ARCHITECTURE", (4, 2, 10))
    monkeypatch.setattr(MODULE, "EXPECTED_FORWARD_ARCHITECTURE", (10, 2, 4))
    monkeypatch.setattr(MODULE, "EXPECTED_PARAMETER_COUNT", 74)
    argv, out_dir = _fixture(tmp_path)
    target_hash_index = argv.index("--expected-targets-sha256") + 1
    monkeypatch.setattr(MODULE, "FROZEN_FIXED10K_SHA256", argv[target_hash_index])

    summary_index = argv.index("--model-200k-summary") + 1
    summary_path = Path(argv[summary_index])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["arguments"]["max_prediction_rows"] = 200_000
    _write_json(summary_path, summary)
    expected_index = argv.index("--expected-model-200k-summary-sha256") + 1
    argv[expected_index] = _sha256(summary_path)

    with pytest.raises(
        ValueError, match="trainer arguments differ beyond the training-data population"
    ):
        MODULE.main(argv)
    assert not out_dir.exists()


def test_reference_contract_model_identity_mismatch_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_FRAME_ROWS", 3)
    monkeypatch.setattr(MODULE, "EXPECTED_LEGACY_ROWS", 2)
    monkeypatch.setattr(MODULE, "EXPECTED_INVERSE_ARCHITECTURE", (4, 2, 10))
    monkeypatch.setattr(MODULE, "EXPECTED_FORWARD_ARCHITECTURE", (10, 2, 4))
    monkeypatch.setattr(MODULE, "EXPECTED_PARAMETER_COUNT", 74)
    argv, out_dir = _fixture(tmp_path)
    target_hash_index = argv.index("--expected-targets-sha256") + 1
    monkeypatch.setattr(MODULE, "FROZEN_FIXED10K_SHA256", argv[target_hash_index])

    contract_index = argv.index("--reference-contract") + 1
    contract_path = Path(argv[contract_index])
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["comparison_eligibility"]["candidate_model_id"] = "wrong_candidate"
    _write_json(contract_path, contract)
    expected_index = argv.index("--expected-reference-contract-sha256") + 1
    argv[expected_index] = _sha256(contract_path)

    with pytest.raises(ValueError, match="identity or comparison-eligibility"):
        MODULE.main(argv)
    assert not out_dir.exists()


def test_reference_contract_rejects_numeric_boolean_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MODULE, "EXPECTED_TARGET_FRAME_ROWS", 3)
    monkeypatch.setattr(MODULE, "EXPECTED_LEGACY_ROWS", 2)
    monkeypatch.setattr(MODULE, "EXPECTED_INVERSE_ARCHITECTURE", (4, 2, 10))
    monkeypatch.setattr(MODULE, "EXPECTED_FORWARD_ARCHITECTURE", (10, 2, 4))
    monkeypatch.setattr(MODULE, "EXPECTED_PARAMETER_COUNT", 74)
    argv, out_dir = _fixture(tmp_path)
    target_hash_index = argv.index("--expected-targets-sha256") + 1
    monkeypatch.setattr(MODULE, "FROZEN_FIXED10K_SHA256", argv[target_hash_index])

    contract_index = argv.index("--reference-contract") + 1
    contract_path = Path(argv[contract_index])
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["comparison_eligibility"]["advisor_comparison_eligible"] = 0
    _write_json(contract_path, contract)
    expected_index = argv.index("--expected-reference-contract-sha256") + 1
    argv[expected_index] = _sha256(contract_path)

    with pytest.raises(ValueError, match="must be an exact boolean"):
        MODULE.main(argv)
    assert not out_dir.exists()
