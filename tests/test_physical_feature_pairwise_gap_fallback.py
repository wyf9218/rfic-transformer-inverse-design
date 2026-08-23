import argparse
import csv
import importlib.util
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
GEOMETRY = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "line_width_um",
    "primary_width_um",
    "secondary_width_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
)


def _load(name: str):
    script = Path(__file__).resolve().parents[1] / "scripts" / name
    module_name = name.replace(".py", "_test_module")
    spec = importlib.util.spec_from_file_location(module_name, script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_plan(plan: Path) -> None:
    bounds = {
        "lp_nh_center": ((0.5, 1.75), (1.75, 3.0)),
        "ls_nh_center": ((0.5, 1.75), (1.75, 3.0)),
        "q_center": ((5.0, 15.0), (15.0, 25.0)),
        "k_abs_center": ((0.0, 0.4), (0.4, 0.8)),
    }
    bins = []
    for index in itertools.product(range(2), repeat=4):
        # High-Q cells are empty, while low-Q cells are already at target.
        row: dict[str, object] = {
            "bin_key": "|".join(map(str, index)),
            "current_count": 0 if index[2] == 1 else 10,
            "target_count": 10,
        }
        for axis, feature in enumerate(FEATURES):
            lower, upper = bounds[feature][index[axis]]
            row[f"{feature}__bin"] = index[axis]
            row[f"{feature}__min"] = lower
            row[f"{feature}__max"] = upper
            row[f"{feature}__target"] = 0.5 * (lower + upper)
        bins.append(row)
    _write_csv(plan / "physical_feature_acquisition_bins.csv", bins)
    target = dict(bins[0])
    target.update({"rank": 1, "recommended_new_samples": 4, "deficit": 10})
    _write_csv(plan / "physical_feature_acquisition_targets.csv", [target])


def _write_candidates(path: Path) -> None:
    rows = []
    for index in range(12):
        row: dict[str, object] = {
            "candidate_id": f"candidate-{index}",
            "pred_lp_nh_center": 0.8 if index % 2 == 0 else 2.4,
            "pred_ls_nh_center": 0.9 if index % 3 == 0 else 2.3,
            "pred_q_center": 20.0,
            "pred_k_abs_center": 0.6 if index % 2 else 0.2,
        }
        for geometry_index, field in enumerate(GEOMETRY):
            row[f"geom__{field}"] = 10.0 + geometry_index + 0.01 * index
        rows.append(row)
    _write_csv(path, rows)


def _write_dataset(path: Path) -> None:
    rows = []
    for index in range(16):
        rows.append(
            {
                "evaluation": f"real-{index}",
                "ok": "true",
                "lp_nh_center": 0.7 if index % 2 == 0 else 2.7,
                "ls_nh_center": 0.8 if (index // 2) % 2 == 0 else 2.6,
                "q_center": 7.0 if index < 14 else 23.0,
                "k_abs_center": 0.1 if (index // 4) % 2 == 0 else 0.7,
            }
        )
    _write_csv(path / "dataset_rows.csv", rows)


def test_pairwise_fallback_fills_q_related_pair_gaps_without_claiming_full_4d_membership(tmp_path):
    selector = _load("select_physical_feature_targeted_candidate_geometries.py")
    plan = tmp_path / "plan"
    candidates = tmp_path / "candidates.csv"
    _write_plan(plan)
    _write_candidates(candidates)

    status = selector.main(
        [
            "--plan-dir",
            str(plan),
            "--candidate-csv",
            str(candidates),
            "--out-dir",
            str(tmp_path / "selection"),
            "--feature-columns",
            ",".join(FEATURES),
            "--max-total",
            "4",
            "--pairwise-fallback-max-total",
            "4",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "selection" / "physical_feature_targeted_candidate_selection_summary.json").read_text()
    )
    assert summary["overall_status"] == "PASS"
    assert summary["selected_candidate_count"] == 4
    assert summary["selected_inside_target_bin_count"] == 0
    assert summary["selected_pairwise_gap_count"] == 4
    diagnostics = summary["selection_diagnostics"]["pairwise_gap_fallback"]
    assert diagnostics["status"] == "PASS"
    assert diagnostics["groups"] >= 2
    with Path(summary["selected_csv"]).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert all(row["selection_source"] == "pairwise_gap_fallback" for row in rows)
    assert all(row["inside_target_bin"].lower() == "false" for row in rows)
    assert all(row["inside_pairwise_target_bin"].lower() == "true" for row in rows)


def test_materializer_requires_explicit_pairwise_fallback_authorization(tmp_path):
    selector = _load("select_physical_feature_targeted_candidate_geometries.py")
    materializer = _load("materialize_physical_feature_targeted_s4p_queue.py")
    plan = tmp_path / "plan"
    candidates = tmp_path / "candidates.csv"
    selection = tmp_path / "selection"
    _write_plan(plan)
    _write_candidates(candidates)
    assert selector.main(
        [
            "--plan-dir",
            str(plan),
            "--candidate-csv",
            str(candidates),
            "--out-dir",
            str(selection),
            "--feature-columns",
            ",".join(FEATURES),
            "--max-total",
            "4",
            "--pairwise-fallback-max-total",
            "4",
        ]
    ) == 0
    rows = materializer._read_csv(selection / "physical_feature_targeted_candidate_selection.csv")
    strict = argparse.Namespace(require_inside_target_bin=True, allow_pairwise_fallback=False, max_count=None)
    authorized = argparse.Namespace(require_inside_target_bin=True, allow_pairwise_fallback=True, max_count=None)

    assert materializer._select_rows(rows, strict) == []
    accepted = materializer._select_rows(rows, authorized)
    assert len(accepted) == 4
    queue, errors = materializer._materialize_rows(
        accepted,
        argparse.Namespace(candidate_id_prefix="pair", sync_widths=True),
    )
    assert len(queue) == 4
    assert errors == []
    assert all(row["line_width_um"] == row["primary_width_um"] == row["secondary_width_um"] for row in queue)

    provenance = _load("audit_mars56_s4p_candidate_queue_provenance.py")
    assert provenance._target_gap_evidence(rows) is True
    selection_summary = json.loads(
        (selection / "physical_feature_targeted_candidate_selection_summary.json").read_text()
    )
    checks = provenance._selection_summary_checks(
        selection_summary,
        selection / "physical_feature_targeted_candidate_selection.csv",
        4,
    )
    assert all(item["pass"] for item in checks)


def test_adaptive_wrapper_exposes_pairwise_fallback_as_explicit_contract():
    wrapper = Path(__file__).resolve().parents[1] / "scripts" / "run_mars56_s4p_adaptive_physical_acquisition_round.sh"
    source = wrapper.read_text(encoding="utf-8")
    required = (
        "--pairwise-fallback-fraction",
        "--pairwise-target-fraction",
        "--pairwise-bins-csv",
        "--pairwise-fallback-max-total",
        "--pairwise-feature-pairs",
        "--pairwise-marginal-features",
        "--allow-pairwise-fallback",
        "selected_inside_or_pairwise_target_count",
    )
    assert all(item in source for item in required)


def test_adaptive_wrapper_materializes_pairwise_fallback_end_to_end(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    wrapper = repo / "scripts" / "run_mars56_s4p_adaptive_physical_acquisition_round.sh"
    dataset = tmp_path / "dataset"
    candidates = tmp_path / "candidates.csv"
    out_dir = tmp_path / "round"
    _write_dataset(dataset)
    _write_candidates(candidates)
    env = dict(os.environ)
    env["PYTHON_BIN"] = sys.executable

    result = subprocess.run(
        [
            "bash",
            str(wrapper),
            "--dataset-dir",
            str(dataset),
            "--out-dir",
            str(out_dir),
            "--queue-count",
            "4",
            "--candidate-predictions-csv",
            str(candidates),
            "--bins",
            "2",
            "--target-count-per-bin",
            "2",
            "--rare-marginal-fraction",
            "0",
            "--pairwise-fallback-fraction",
            "1",
            "--no-reachable-targets-only",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        check=False,
    )

    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    summary = json.loads((out_dir / "adaptive_physical_acquisition_round_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["selected_inside_target_bin_count"] == 0
    assert summary["selected_pairwise_gap_count"] == 4
    assert summary["selected_inside_or_pairwise_target_count"] == 4
    assert summary["queue_count"] == 4
    identity = summary["queue_identity_evidence"]
    assert identity["require_unique_geometry"] is True
    assert identity["require_unique_source_id"] is True
    assert identity["identity_audit"]["unique_geometry_fingerprint_count"] == 4
    assert identity["identity_audit"]["duplicate_geometry_extra_row_count"] == 0
    provenance = json.loads(
        (out_dir / "provenance" / "mars56_s4p_candidate_queue_provenance_summary.json").read_text()
    )
    assert provenance["overall_status"] == "PASS"


def test_adaptive_wrapper_accepts_only_explicitly_authorized_mix_contract(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    wrapper = repo / "scripts" / "run_mars56_s4p_adaptive_physical_acquisition_round.sh"
    dataset = tmp_path / "dataset"
    candidates = tmp_path / "candidates.csv"
    _write_dataset(dataset)
    _write_candidates(candidates)
    contract = tmp_path / "mix.json"
    contract.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "automatic_command_authorized": True,
                "proxy_values_are_acquisition_only": True,
                "production_acquisition_mix": {
                    "queue_count": 4,
                    "counts": {
                        "coarse_4d": 0,
                        "rare_marginal": 0,
                        "pairwise_gap": 4,
                        "random_exploration": 0,
                        "geometry_diversity": 0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHON_BIN"] = sys.executable
    command = [
        "bash",
        str(wrapper),
        "--dataset-dir",
        str(dataset),
        "--out-dir",
        str(tmp_path / "authorized"),
        "--queue-count",
        "4",
        "--candidate-predictions-csv",
        str(candidates),
        "--bins",
        "2",
        "--target-count-per-bin",
        "2",
        "--acquisition-mix-json",
        str(contract),
        "--no-reachable-targets-only",
    ]

    result = subprocess.run(command, cwd=repo, env=env, capture_output=True, check=False)

    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    summary = json.loads(
        (tmp_path / "authorized" / "adaptive_physical_acquisition_round_summary.json").read_text()
    )
    assert summary["overall_status"] == "PASS"
    assert summary["selected_acquisition_policy_count"] == 4
    assert summary["acquisition_mix_contract"]["selected_counts"]["pairwise_gap"] == 4
    assert summary["acquisition_mix_contract_source"]["path"] == str(contract.resolve())

    contract_data = json.loads(contract.read_text())
    contract_data["automatic_command_authorized"] = False
    contract.write_text(json.dumps(contract_data), encoding="utf-8")
    command[command.index(str(tmp_path / "authorized"))] = str(tmp_path / "denied")
    denied = subprocess.run(command, cwd=repo, env=env, capture_output=True, check=False)
    assert denied.returncode != 0
    assert not (tmp_path / "denied" / "queue" / "mars56_grounded_s4p_candidate_queue.csv").exists()
