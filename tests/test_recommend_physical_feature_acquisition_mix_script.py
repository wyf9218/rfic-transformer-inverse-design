from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "recommend_physical_feature_acquisition_mix.py"
    spec = importlib.util.spec_from_file_location("recommend_physical_feature_acquisition_mix_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _summary(*, concentrated: bool = True, explicit: bool = True) -> dict:
    one_d = {}
    for name in ("lp", "ls", "q", "k"):
        severe = concentrated and name in {"q", "k"}
        one_d[name] = {
            "occupied_fraction": 1.0,
            "normalized_entropy": 0.78 if severe else 0.95,
            "max_to_min_nonzero_ratio": 4000.0 if severe else 2.0,
        }
    pairwise = {}
    for name in ("lp_ls", "lp_q", "lp_k", "ls_q", "ls_k", "q_k"):
        severe = concentrated and name in {"lp_q", "ls_q", "q_k"}
        pairwise[name] = {
            "occupied_fraction": 0.50 if severe else 0.90,
            "normalized_entropy": 0.70 if severe else 0.90,
        }
    return {
        "overall_status": "FAIL" if concentrated else "PASS",
        "valid_feature_count": 100000,
        "ranges": {
            name: {"min": bounds[0], "max": bounds[1], "explicit": explicit}
            for name, bounds in {
                "lp": (0.5, 3.0),
                "ls": (0.5, 3.0),
                "q": (5.0, 25.0),
                "k": (0.0, 0.8),
            }.items()
        },
        "one_dimensional_uniformity": one_d,
        "pairwise_uniformity": pairwise,
        "four_dimensional_uniformity": {
            "occupied_fraction": 0.46 if concentrated else 0.75,
            "normalized_entropy": 0.74 if concentrated else 0.90,
            "max_to_min_nonzero_ratio": 3755.0 if concentrated else 2.0,
        },
        "distribution_thresholds": {
            "min_1d_occupied_fraction": 0.90,
            "min_1d_normalized_entropy": 0.90,
            "max_1d_nonzero_bin_imbalance": 2.50,
            "min_pair_occupied_fraction": 0.65,
            "min_pair_normalized_entropy": 0.80,
            "require_four_d_gate": True,
            "min_four_d_occupied_fraction": 0.50,
            "min_four_d_normalized_entropy": 0.80,
            "max_four_d_nonzero_bin_imbalance": 4.0,
        },
    }


def _run(module, root: Path, data: dict, *, count: int = 101) -> tuple[int, dict]:
    source = root / "uniformity.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    out = root / "out"
    status = module.main(
        [
            "--uniformity-summary",
            str(source),
            "--out-dir",
            str(out),
            "--queue-count",
            str(count),
            "--no-fail-exit",
        ]
    )
    payload = json.loads((out / "physical_feature_acquisition_mix_proposal_summary.json").read_text())
    return status, payload


def test_concentrated_q_k_and_pair_gaps_receive_targeted_budget(tmp_path):
    module = _load_module()
    status, payload = _run(module, tmp_path, _summary(concentrated=True), count=101)

    assert status == 0
    assert payload["overall_status"] == "PASS"
    assert payload["decision"] == "STAGE_GAP_DRIVEN_EQUAL_BUDGET_REMEDIATION"
    assert all(
        isinstance(payload["severity"][name], float)
        for name in ("marginal", "pairwise", "four_d")
    )
    assert payload["severity"]["worst_marginal"] in {"q", "k"}
    assert payload["severity"]["worst_pair"] in {"lp_q", "ls_q", "q_k"}
    mix = payload["recommended_mix"]
    assert mix["count_sum"] == 101
    assert sum(mix["counts"].values()) == 101
    assert mix["fractions"]["random_exploration"] == pytest.approx(0.10)
    assert mix["fractions"]["geometry_diversity"] == pytest.approx(0.10)
    assert mix["fractions"]["rare_marginal"] > 0.15
    assert mix["fractions"]["pairwise_gap"] > 0.15
    assert payload["production_mapping"]["automatic_command_authorized"] is False
    assert Path(payload["artifacts"]["plot"]).is_file()


def test_uniform_source_keeps_exploration_and_conserves_budget(tmp_path):
    module = _load_module()
    _, payload = _run(module, tmp_path, _summary(concentrated=False), count=100)

    assert payload["decision"] == "NO_STRICT_GAP_SIGNAL_KEEP_EXPLORATION"
    assert payload["recommended_mix"]["count_sum"] == 100
    targeted = [payload["recommended_mix"]["fractions"][name] for name in module.TARGETED_ARMS]
    assert targeted[0] == pytest.approx(targeted[1])
    assert targeted[1] == pytest.approx(targeted[2])


def test_missing_explicit_ranges_rejects_proposal(tmp_path):
    module = _load_module()
    _, payload = _run(module, tmp_path, _summary(explicit=False))

    assert payload["overall_status"] == "FAIL"
    assert payload["checks"]["declared_ranges_exact_and_explicit"] is False
    assert payload["recommended_mix"] == {}


def test_weakened_thresholds_reject_proposal(tmp_path):
    module = _load_module()
    data = _summary()
    data["distribution_thresholds"]["min_four_d_normalized_entropy"] = 0.70
    _, payload = _run(module, tmp_path, data)

    assert payload["overall_status"] == "FAIL"
    assert payload["checks"]["strict_thresholds_not_weakened"] is False
