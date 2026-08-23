from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "compare_physical_feature_q_input_ablation.py"
    spec = importlib.util.spec_from_file_location("compare_physical_feature_q_input_ablation_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_summary(path: Path, *, error: float, representation: str, split: str = "shared") -> None:
    path.write_text(
        json.dumps(
            {
                "training_count": 200000,
                "split_audit": {
                    "split_fingerprint_sha256": split,
                    "physical_cell_partition_fingerprint_sha256": "cells",
                },
                "metrics": {
                    "common_lp_ls_qmin_absk_contract": {
                        "status": "PASS",
                        "q_representation": representation,
                        "test_range_normalized_rmse": error,
                        "per_feature_physical_mae": {"Lp_nH": error},
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _write_broadband_summary(
    path: Path,
    *,
    error: float,
    predictor_count: int,
    row_fingerprint: str = "same-real-s4p-rows",
    content_fingerprint: str | None = None,
    split: str = "shared",
    target_error: float | None = None,
    target_frequency_ghz: float = 15.0,
    input_quality_pass: bool = True,
) -> None:
    if target_error is None:
        target_error = error
    path.write_text(
        json.dumps(
            {
                "overall_status": "COMPLETE_REVIEW_REQUIRED",
                "training_count": 10000,
                "predictor_role": "physical_spec",
                "predictor_columns": [f"input__p{index}" for index in range(predictor_count)],
                "row_identity_sha256": row_fingerprint,
                "touchstone_content_sha256": content_fingerprint or f"content-{row_fingerprint}",
                "reciprocal_training_content_sha256": f"reciprocal-{row_fingerprint}",
                "frequency_grid_sha256": "same-grid",
                "acceptance_thresholds": {"input_quality_configured": True},
                "input_s4p_quality": {
                    "audit_stage": "raw complex S4P before reciprocal symmetrization",
                    "reciprocity": {"hard_threshold_pass": input_quality_pass},
                    "passivity": {"hard_threshold_pass": input_quality_pass},
                },
                "split_audit": {
                    "split_fingerprint_sha256": split,
                    "physical_cell_partition_fingerprint_sha256": "cells",
                },
                "metrics": {
                    "test_raw_complex_rmse": error,
                    "target_test_raw_complex_rmse": target_error,
                    "target_frequency_used_ghz": target_frequency_ghz,
                    "test_pca_floor_complex_rmse": 0.01,
                },
            }
        ),
        encoding="utf-8",
    )


def test_recommends_review_when_qp_qs_has_material_common_metric_gain(tmp_path):
    module = _load_module()
    q = tmp_path / "q.json"
    qp_qs = tmp_path / "qp_qs.json"
    _write_summary(q, error=0.10, representation="Q_scalar")
    _write_summary(qp_qs, error=0.08, representation="min_Qp_Qs")

    status = module.main(
        [
            "--q-summary",
            str(q),
            "--qp-qs-summary",
            str(qp_qs),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_q_input_ablation_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["qp_qs_relative_improvement"] == pytest.approx(0.20)
    assert summary["decision"] == "REVIEW_QP_QS_FOR_FUTURE_INPUT_CONTRACT_WITH_REAL_EMX_CLOSURE"


def test_rejects_mismatched_ood_split(tmp_path):
    module = _load_module()
    q = tmp_path / "q.json"
    qp_qs = tmp_path / "qp_qs.json"
    _write_summary(q, error=0.10, representation="Q_scalar", split="a")
    _write_summary(qp_qs, error=0.08, representation="min_Qp_Qs", split="b")

    status = module.main(
        [
            "--q-summary",
            str(q),
            "--qp-qs-summary",
            str(qp_qs),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_q_input_ablation_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["same_split_fingerprint"] is False
    assert summary["decision"] == "FIX_ABLATION_COMPARISON_CONTRACT"


def test_requires_center_and_broadband_gain_before_recommending_qp_qs(tmp_path):
    module = _load_module()
    q = tmp_path / "q.json"
    qp_qs = tmp_path / "qp_qs.json"
    q_broadband = tmp_path / "q_broadband.json"
    qp_qs_broadband = tmp_path / "qp_qs_broadband.json"
    _write_summary(q, error=0.10, representation="Q_scalar")
    _write_summary(qp_qs, error=0.08, representation="min_Qp_Qs")
    _write_broadband_summary(q_broadband, error=0.05, predictor_count=4)
    _write_broadband_summary(qp_qs_broadband, error=0.04, predictor_count=5)

    status = module.main(
        [
            "--q-summary",
            str(q),
            "--qp-qs-summary",
            str(qp_qs),
            "--q-broadband-summary",
            str(q_broadband),
            "--qp-qs-broadband-summary",
            str(qp_qs_broadband),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_q_input_ablation_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["broadband_qp_qs_relative_improvement"] == pytest.approx(0.20)
    assert summary["target_frequency_qp_qs_relative_improvement"] == pytest.approx(0.20)
    assert summary["decision"] == (
        "REVIEW_QP_QS_FOR_FUTURE_INPUT_CONTRACT_WITH_BROADBAND_AND_REAL_EMX_CLOSURE"
    )


def test_rejects_broadband_ablation_with_different_real_s4p_rows(tmp_path):
    module = _load_module()
    q = tmp_path / "q.json"
    qp_qs = tmp_path / "qp_qs.json"
    q_broadband = tmp_path / "q_broadband.json"
    qp_qs_broadband = tmp_path / "qp_qs_broadband.json"
    _write_summary(q, error=0.10, representation="Q_scalar")
    _write_summary(qp_qs, error=0.08, representation="min_Qp_Qs")
    _write_broadband_summary(q_broadband, error=0.05, predictor_count=4, row_fingerprint="a")
    _write_broadband_summary(qp_qs_broadband, error=0.04, predictor_count=5, row_fingerprint="b")

    status = module.main(
        [
            "--q-summary",
            str(q),
            "--qp-qs-summary",
            str(qp_qs),
            "--q-broadband-summary",
            str(q_broadband),
            "--qp-qs-broadband-summary",
            str(qp_qs_broadband),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_q_input_ablation_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["broadband_same_real_s4p_rows"] is False


def test_retains_qmin_when_target_frequency_and_full_band_evidence_disagree(tmp_path):
    module = _load_module()
    q = tmp_path / "q.json"
    qp_qs = tmp_path / "qp_qs.json"
    q_broadband = tmp_path / "q_broadband.json"
    qp_qs_broadband = tmp_path / "qp_qs_broadband.json"
    _write_summary(q, error=0.10, representation="Q_scalar")
    _write_summary(qp_qs, error=0.08, representation="min_Qp_Qs")
    _write_broadband_summary(q_broadband, error=0.05, target_error=0.02, predictor_count=4)
    _write_broadband_summary(qp_qs_broadband, error=0.04, target_error=0.03, predictor_count=5)

    status = module.main(
        [
            "--q-summary",
            str(q),
            "--qp-qs-summary",
            str(qp_qs),
            "--q-broadband-summary",
            str(q_broadband),
            "--qp-qs-broadband-summary",
            str(qp_qs_broadband),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_q_input_ablation_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["target_frequency_qp_qs_relative_improvement"] == pytest.approx(-0.5)
    assert summary["decision"] == "RETAIN_QMIN_MIXED_PHYSICAL_TARGET_AND_BROADBAND_EVIDENCE"


def test_rejects_same_paths_when_parsed_touchstone_content_differs(tmp_path):
    module = _load_module()
    q = tmp_path / "q.json"
    qp_qs = tmp_path / "qp_qs.json"
    q_broadband = tmp_path / "q_broadband.json"
    qp_qs_broadband = tmp_path / "qp_qs_broadband.json"
    _write_summary(q, error=0.10, representation="Q_scalar")
    _write_summary(qp_qs, error=0.08, representation="min_Qp_Qs")
    _write_broadband_summary(
        q_broadband,
        error=0.05,
        predictor_count=4,
        content_fingerprint="content-a",
    )
    _write_broadband_summary(
        qp_qs_broadband,
        error=0.04,
        predictor_count=5,
        content_fingerprint="content-b",
    )

    status = module.main(
        [
            "--q-summary",
            str(q),
            "--qp-qs-summary",
            str(qp_qs),
            "--q-broadband-summary",
            str(q_broadband),
            "--qp-qs-broadband-summary",
            str(qp_qs_broadband),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_q_input_ablation_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["broadband_same_real_s4p_rows"] is True
    assert summary["checks"]["broadband_same_real_s4p_content"] is False


def test_rejects_broadband_ablation_when_raw_input_quality_failed(tmp_path):
    module = _load_module()
    q = tmp_path / "q.json"
    qp_qs = tmp_path / "qp_qs.json"
    q_broadband = tmp_path / "q_broadband.json"
    qp_qs_broadband = tmp_path / "qp_qs_broadband.json"
    _write_summary(q, error=0.10, representation="Q_scalar")
    _write_summary(qp_qs, error=0.08, representation="min_Qp_Qs")
    _write_broadband_summary(q_broadband, error=0.05, predictor_count=4)
    _write_broadband_summary(
        qp_qs_broadband,
        error=0.04,
        predictor_count=5,
        input_quality_pass=False,
    )

    assert module.main(
        [
            "--q-summary",
            str(q),
            "--qp-qs-summary",
            str(qp_qs),
            "--q-broadband-summary",
            str(q_broadband),
            "--qp-qs-broadband-summary",
            str(qp_qs_broadband),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    ) == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_q_input_ablation_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["broadband_input_quality_thresholds_pass"] is False
