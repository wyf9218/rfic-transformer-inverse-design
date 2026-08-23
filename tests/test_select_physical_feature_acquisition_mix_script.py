import argparse
import csv
import importlib.util
import itertools
import json
import sys
from pathlib import Path


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "select_physical_feature_acquisition_mix.py"
    spec = importlib.util.spec_from_file_location("physical_feature_acquisition_mix_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_plan(path: Path) -> None:
    bounds = {
        "lp_nh_center": ((0.5, 1.75), (1.75, 3.0)),
        "ls_nh_center": ((0.5, 1.75), (1.75, 3.0)),
        "q_center": ((5.0, 15.0), (15.0, 25.0)),
        "k_abs_center": ((0.0, 0.4), (0.4, 0.8)),
    }
    bins = []
    for index in itertools.product(range(2), repeat=4):
        row: dict[str, object] = {
            "bin_key": "|".join(map(str, index)),
            "current_count": 10 if index[2] == 0 else 0,
            "target_count": 10,
        }
        for axis, feature in enumerate(FEATURES):
            lower, upper = bounds[feature][index[axis]]
            row[f"{feature}__bin"] = index[axis]
            row[f"{feature}__min"] = lower
            row[f"{feature}__max"] = upper
            row[f"{feature}__target"] = 0.5 * (lower + upper)
        bins.append(row)
    _write_csv(path / "physical_feature_acquisition_bins.csv", bins)
    target = dict(bins[0])
    target.update({"rank": 1, "recommended_new_samples": 2, "deficit": 2})
    _write_csv(path / "physical_feature_acquisition_targets.csv", [target])


def _candidate(index: int, mode: str, values: tuple[float, float, float, float]) -> dict[str, object]:
    row: dict[str, object] = {
        "candidate_id": f"candidate-{index:03d}",
        "candidate_generation_mode": mode,
        "geom__g1": 0.03 * index,
        "geom__g2": 1.0 - 0.021 * index,
    }
    for feature, value in zip(FEATURES, values):
        row[f"pred_{feature}"] = value
    return row


def _write_candidates(path: Path) -> None:
    rows = [
        _candidate(0, "local_sparse_target_perturbation", (0.8, 0.9, 10.0, 0.2)),
        _candidate(1, "local_sparse_target_perturbation", (0.9, 1.0, 11.0, 0.25)),
    ]
    for index in (2, 3):
        row = _candidate(index, "local_rare_marginal_perturbation", (1.0, 1.1, 10.0, 0.75))
        row.update(
            {
                "candidate_marginal_feature": "k_abs_center",
                "candidate_marginal_bin": 9,
                "candidate_marginal_min": 0.72,
                "candidate_marginal_max": 0.8,
                "candidate_marginal_target": 0.76,
                "candidate_marginal_seed_count": 1,
                "candidate_marginal_priority_weight": 3.0,
                "candidate_seed_anchor_weight": 0.8,
            }
        )
        rows.append(row)
    rows.extend(
        [
            _candidate(4, "local_pairwise_gap_perturbation", (0.9, 2.2, 20.0, 0.2)),
            _candidate(5, "local_pairwise_gap_perturbation", (2.2, 0.9, 21.0, 0.6)),
        ]
    )
    for index in range(6, 30):
        rows.append(
            _candidate(
                index,
                "global_latin_hypercube",
                (2.0 + 0.01 * (index % 5), 2.1 + 0.01 * (index % 7), 12.0, 0.2 + 0.01 * (index % 8)),
            )
        )
    _write_csv(path, rows)


def _write_accepted(path: Path) -> None:
    rows = [
        {"evaluation": f"accepted-{index}", "ok": "true", "geom__g1": 0.01 * index, "geom__g2": 0.1 + 0.015 * index}
        for index in range(20)
    ]
    _write_csv(path / "dataset_rows.csv", rows)


def _args(root: Path, out: Path) -> list[str]:
    return [
        "--plan-dir", str(root / "plan"),
        "--candidate-csv", str(root / "candidates.csv"),
        "--accepted-dataset-dir", str(root / "accepted"),
        "--out-dir", str(out),
        "--feature-columns", ",".join(FEATURES),
        "--geometry-columns", "geom__g1,geom__g2",
        "--max-total", "10",
        "--coarse-4d-max-total", "2",
        "--rare-marginal-max-total", "2",
        "--pairwise-gap-max-total", "2",
        "--random-exploration-max-total", "2",
        "--geometry-diversity-max-total", "2",
        "--seed", "17",
    ]


def test_five_arm_mix_is_exact_disjoint_and_deterministic(tmp_path):
    module = _load_module()
    _write_plan(tmp_path / "plan")
    _write_candidates(tmp_path / "candidates.csv")
    _write_accepted(tmp_path / "accepted")

    assert module.main(_args(tmp_path, tmp_path / "out-a")) == 0
    assert module.main(_args(tmp_path, tmp_path / "out-b")) == 0

    summary = json.loads(
        (tmp_path / "out-a" / "physical_feature_targeted_candidate_selection_summary.json").read_text()
    )
    assert summary["overall_status"] == "PASS"
    assert summary["outcome_status"] == "CANDIDATE_QUEUE_ONLY_AWAITING_REAL_EMX"
    contract = summary["acquisition_mix_contract"]
    assert contract["selected_counts"] == contract["requested_counts"]
    assert contract["count_sum"] == 10
    assert contract["arms_are_disjoint"] is True
    assert contract["proxy_values_are_acquisition_only"] is True
    assert summary["selected_policy_eligible_count"] == 10
    rows_a = (tmp_path / "out-a" / "physical_feature_targeted_candidate_selection.csv").read_text()
    rows_b = (tmp_path / "out-b" / "physical_feature_targeted_candidate_selection.csv").read_text()
    assert rows_a == rows_b


def test_quota_sum_mismatch_fails_closed(tmp_path):
    module = _load_module()
    _write_plan(tmp_path / "plan")
    _write_candidates(tmp_path / "candidates.csv")
    _write_accepted(tmp_path / "accepted")
    args = _args(tmp_path, tmp_path / "bad")
    args[args.index("--random-exploration-max-total") + 1] = "1"
    args.append("--no-fail-exit")

    assert module.main(args) == 0
    summary = json.loads(
        (tmp_path / "bad" / "physical_feature_targeted_candidate_selection_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["quota_sum_matches_max_total"] is False
    assert summary["selected_candidate_count"] == 0


def test_materializer_requires_explicit_authorization_for_exploration_arms():
    materializer_path = Path(__file__).resolve().parents[1] / "scripts" / "materialize_physical_feature_targeted_s4p_queue.py"
    spec = importlib.util.spec_from_file_location("mix_materializer", materializer_path)
    assert spec is not None and spec.loader is not None
    materializer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = materializer
    spec.loader.exec_module(materializer)
    rows = [
        {
            "candidate_id": "random-1",
            "selection_source": "random_exploration",
            "inside_target_bin": "false",
            "acquisition_policy_authorized": "true",
        },
        {
            "candidate_id": "diversity-1",
            "selection_source": "geometry_diversity",
            "inside_target_bin": "false",
            "acquisition_policy_authorized": "true",
        },
    ]
    denied = argparse.Namespace(
        require_inside_target_bin=True,
        allow_pairwise_fallback=False,
        allow_random_exploration=False,
        allow_geometry_diversity=False,
        max_count=None,
    )
    allowed = argparse.Namespace(
        require_inside_target_bin=True,
        allow_pairwise_fallback=False,
        allow_random_exploration=True,
        allow_geometry_diversity=True,
        max_count=None,
    )

    assert materializer._select_rows(rows, denied) == []
    assert materializer._select_rows(rows, allowed) == rows


def test_provenance_accepts_exact_five_arm_contract_and_rejects_count_drift():
    provenance_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_mars56_s4p_candidate_queue_provenance.py"
    spec = importlib.util.spec_from_file_location("mix_provenance", provenance_path)
    assert spec is not None and spec.loader is not None
    provenance = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = provenance
    spec.loader.exec_module(provenance)
    counts = {
        "coarse_4d": 2,
        "rare_marginal": 2,
        "pairwise_gap": 2,
        "random_exploration": 2,
        "geometry_diversity": 2,
    }
    summary = {
        "overall_status": "PASS",
        "decision": "USE_SELECTED_CANDIDATES_FOR_NEXT_REAL_EMX",
        "selected_candidate_count": 10,
        "selected_inside_target_bin_count": 4,
        "selected_pairwise_gap_count": 2,
        "selected_inside_or_pairwise_target_count": 6,
        "selected_policy_eligible_count": 10,
        "acquisition_mix_contract": {
            "requested_counts": counts,
            "selected_counts": dict(counts),
            "arms_are_disjoint": True,
            "proxy_values_are_acquisition_only": True,
        },
    }

    checks = provenance._selection_summary_checks(summary, None, 10)
    assert all(item["pass"] for item in checks)
    summary["acquisition_mix_contract"]["selected_counts"]["random_exploration"] = 1
    checks = provenance._selection_summary_checks(summary, None, 10)
    exact = next(item for item in checks if item["name"] == "selection_summary_five_arm_exact_quotas")
    assert exact["pass"] is False
