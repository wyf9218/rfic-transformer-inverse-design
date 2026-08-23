from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import json
import sys


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "compare_uncertainty_reliability_methods.py"
    spec = importlib.util.spec_from_file_location("compare_uncertainty_reliability_methods_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _summary(path: Path, *, ensemble: bool, target_sha: str = "target") -> None:
    metrics = {
        "aggregate_spearman_uncertainty_vs_error": 0.45 if ensemble else 0.30,
        "high_vs_low_uncertainty_error_ratio": 1.8 if ensemble else 1.7,
        "low_minus_high_uncertainty_mean_1d_bin_accuracy": 0.15 if ensemble else 0.12,
        "mean_range_normalized_absolute_error": 0.050 if ensemble else 0.052,
        "scaled_interval_empirical_coverage": 0.90,
    }
    payload = {
        "overall_status": "PASS",
        "eligible_for_acquisition_ablation": True,
        "feature_columns": ["lp", "ls", "q", "k"],
        "feature_ranges": {"lp": [0.5, 3.0], "ls": [0.5, 3.0], "q": [5.0, 25.0], "k": [0.0, 0.8]},
        "split": {
            "train_geometry_count": 120,
            "train_group_sha256": "train_geometry",
            "train_real_target_sha256": "train_target",
            "holdout_geometry_count": 80,
            "holdout_group_sha256": "geometry",
            "holdout_real_target_sha256": target_sha,
        },
        "holdout_metrics": metrics,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_approves_only_equal_real_holdout_with_better_ensemble(tmp_path):
    module = _load()
    knn = tmp_path / "knn.json"
    ensemble = tmp_path / "ensemble.json"
    _summary(knn, ensemble=False)
    _summary(ensemble, ensemble=True)
    out_dir = tmp_path / "out"
    assert module.main(["--knn-summary", str(knn), "--ensemble-summary", str(ensemble), "--out-dir", str(out_dir)]) == 0
    result = json.loads((out_dir / "uncertainty_method_comparison_summary.json").read_text())
    assert result["overall_status"] == "PASS"
    assert result["eligible_for_equal_budget_real_emx_ablation"] is True
    assert result["decision"] == "ENSEMBLE_READY_FOR_EQUAL_BUDGET_REAL_EMX_ABLATION_ONLY"


def test_waits_when_real_holdout_targets_differ(tmp_path):
    module = _load()
    knn = tmp_path / "knn.json"
    ensemble = tmp_path / "ensemble.json"
    _summary(knn, ensemble=False, target_sha="target_a")
    _summary(ensemble, ensemble=True, target_sha="target_b")
    out_dir = tmp_path / "out"
    assert module.main(["--knn-summary", str(knn), "--ensemble-summary", str(ensemble), "--out-dir", str(out_dir)]) == 2
    result = json.loads((out_dir / "uncertainty_method_comparison_summary.json").read_text())
    assert result["overall_status"] == "WAITING"
    assert result["checks"]["holdout_real_target_match"] is False
    assert result["eligible_for_equal_budget_real_emx_ablation"] is False
