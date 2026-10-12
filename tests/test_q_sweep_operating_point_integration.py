"""Packaging regressions: preserve main's operating-point gate in the GUI merge.

All receipts here are explicitly synthetic; no native tools are invoked.
"""
from copy import deepcopy

import numpy as np
import pytest

from rfic_transformer_inverse_design.analysis.operating_point15 import LABEL_POLICY
from rfic_transformer_inverse_design.synthesis.q_sweep import (
    PhysicalTarget3,
    _operating_point_backend_gate,
    _write_backend_request,
    run_q_sweep,
)
from rfic_transformer_inverse_design.synthesis.frozen_mlp import MLPBatchPrediction


class DummyModel:
    model_id = "synthetic-policy-integration"
    model_seed = 7
    target_frequency_ghz = 15.0

    def predict(self, targets):
        rows = np.asarray(targets, dtype=float)
        geometry = np.column_stack([rows[:, 2] + float(index) for index in range(10)])
        return MLPBatchPrediction(geometry=geometry, proxy_features=rows.copy())


def evidence():
    artifacts = {"gds_sha256": "synthetic-gds", "s4p_sha256": "synthetic-s4p"}
    physical = np.asarray([3.0, 0.3, 7.0, 0.76])
    row = {
        "candidate_id": "synthetic-q10",
        "geometry_sha256": "synthetic-geometry",
        "operating_point_evidence": {
            "candidate_id": "synthetic-q10",
            "frequency_hz": 15e9,
            "physical15": dict(zip(("lp_nh", "ls_nh", "qmin", "k_abs"), physical.tolist())),
            "descriptor_valid": True,
            "strict_valid": False,
            "below_half_srf": False,
            "s_z_roundtrip_abs_max": 1e-12,
            "passivity_sigma_max": 0.99,
            "reciprocity_abs_max": 0.0,
        },
        "physical_gate_evidence": {
            "geometry_sha256": "synthetic-geometry",
            **artifacts,
            "current_structure_compatible": True,
            "drc_zero_blocking": True,
            "original_GDS_DRC_EMX_binding": True,
        },
    }
    return row, physical, artifacts


@pytest.mark.parametrize("remove_srf", [False, True])
def test_half_srf_is_not_a_gate_and_no_inductance_or_q_window(remove_srf):
    row, physical, artifacts = evidence()
    if remove_srf:
        row["operating_point_evidence"].pop("below_half_srf")
        row["operating_point_evidence"].pop("strict_valid")
    verdict = _operating_point_backend_gate(row, physical, artifacts)
    assert verdict["operating_point_valid"]
    assert verdict["label_policy"] == LABEL_POLICY


@pytest.mark.parametrize("bad_lp", [-1.0, float("nan")])
def test_invalid_inductance_still_rejected(bad_lp):
    row, physical, artifacts = evidence()
    row["operating_point_evidence"]["physical15"]["lp_nh"] = bad_lp
    physical[0] = bad_lp
    with pytest.raises(ValueError, match="OPERATING_POINT_NUMERICAL"):
        _operating_point_backend_gate(row, physical, artifacts)


def test_physical_identity_and_drc_are_not_bypassed():
    row, physical, artifacts = evidence()
    for name in ("current_structure_compatible", "drc_zero_blocking", "original_GDS_DRC_EMX_binding"):
        invalid = deepcopy(row)
        invalid["physical_gate_evidence"][name] = False
        with pytest.raises(ValueError, match="MISSING_OR_INCOMPATIBLE"):
            _operating_point_backend_gate(invalid, physical, artifacts)


def test_missing_policy_evidence_fails_closed():
    row, physical, artifacts = evidence()
    row.pop("operating_point_evidence")
    with pytest.raises(ValueError, match="MISSING_OPERATING_POINT_QA"):
        _operating_point_backend_gate(row, physical, artifacts)


def test_result_and_request_keep_policy_metadata(tmp_path):
    import json

    model = DummyModel()
    result = run_q_sweep(model, PhysicalTarget3("demo", 1.15, 1.4, 0.76))
    assert result.record()["label_policy"] == LABEL_POLICY
    assert result.record()["srf_role"] == "OPTIONAL_DIAGNOSTIC_NOT_A_GATE"
    path = tmp_path / "request.json"
    _write_backend_request(path, model, result)
    request = json.loads(path.read_text())
    assert request["label_policy"] == LABEL_POLICY
    assert request["srf_required"] is False
    assert request["q_proxy"] == result.selected_q
    assert request["required_evidence"] == ["operating_point_evidence", "physical_gate_evidence"]
    assert request["selection_scope"] == "FULL11Q_DIAGNOSTIC_NOT_FINAL_PRESELECTED_Q_PROXY_VALIDATION"
