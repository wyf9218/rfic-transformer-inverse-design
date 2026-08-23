from __future__ import annotations

import csv
import importlib.util
import itertools
import json
import sys
from pathlib import Path


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
BOUNDS = {
    "lp_nh_center": ((0.5, 1.75), (1.75, 3.0)),
    "ls_nh_center": ((0.5, 1.75), (1.75, 3.0)),
    "q_center": ((5.0, 15.0), (15.0, 25.0)),
    "k_abs_center": ((0.0, 0.4), (0.4, 0.8)),
}


def _load_audit():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_feature_candidate_reachability_consensus.py"
    spec = importlib.util.spec_from_file_location("candidate_reachability_consensus_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_bins(path: Path) -> None:
    rows: list[dict[str, object]] = []
    for index in itertools.product(range(2), repeat=4):
        row: dict[str, object] = {
            "bin_key": "|".join(str(value) for value in index),
            "current_count": 0,
            "target_count": 10,
            "deficit": 10,
            "status": "underfilled",
        }
        for axis, feature in enumerate(FEATURES):
            lower, upper = BOUNDS[feature][index[axis]]
            row[f"{feature}__bin"] = index[axis]
            row[f"{feature}__min"] = lower
            row[f"{feature}__max"] = upper
        rows.append(row)
    _write_csv(path, rows)


def _candidate(
    candidate_id: str,
    index: tuple[int, int, int, int],
    *,
    uncertain_axis: int | None = None,
    include_uncertainty: bool = True,
) -> dict[str, object]:
    row: dict[str, object] = {"candidate_id": candidate_id}
    for axis, feature in enumerate(FEATURES):
        lower, upper = BOUNDS[feature][index[axis]]
        span = upper - lower
        if axis == uncertain_axis:
            center = lower + 0.02 * span
            uncertainty = 0.05 * span
        else:
            center = 0.5 * (lower + upper)
            uncertainty = 0.05 * span
        row[f"pred_{feature}"] = center
        if include_uncertainty:
            row[f"pred_uncertainty_{feature}"] = uncertainty
    return row


def test_multi_source_consensus_distinguishes_robust_uncertain_sparse_and_none(tmp_path):
    audit = _load_audit()
    bins = tmp_path / "bins.csv"
    _write_bins(bins)
    candidates = []
    for batch in range(4):
        rows = [
            _candidate(f"robust-{batch}-{copy}", (0, 0, 0, 0))
            for copy in range(2)
        ]
        rows.extend(
            _candidate(f"uncertain-{batch}-{copy}", (1, 0, 0, 0), uncertain_axis=0)
            for copy in range(2)
        )
        if batch == 0:
            rows.extend(
                _candidate(f"sparse-{batch}-{copy}", (0, 1, 0, 0))
                for copy in range(2)
            )
        path = tmp_path / f"candidate_batch_{batch}.csv"
        _write_csv(path, rows)
        candidates.append(path)

    out_dir = tmp_path / "audit"
    argv = ["--bins-csv", str(bins), "--out-dir", str(out_dir), "--min-candidate-rows", "10", "--no-plots"]
    for path in candidates:
        argv.extend(["--candidate-csv", str(path)])
    assert audit.main(argv) == 0

    summary = json.loads((out_dir / "candidate_reachability_consensus_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "USE_AS_ADVISORY_REACHABILITY_CONSENSUS_NOT_PHYSICAL_FEASIBILITY_PROOF"
    by_key = {record["bin_key"]: record for record in summary["records"]}
    assert by_key["0|0|0|0"]["candidate_evidence_class"] == audit.ROBUST
    assert by_key["1|0|0|0"]["candidate_evidence_class"] == audit.NOMINAL
    assert by_key["0|1|0|0"]["candidate_evidence_class"] == audit.SPARSE
    assert by_key["0|0|1|0"]["candidate_evidence_class"] == audit.NONE
    serialized = json.dumps(summary).lower()
    assert "not physical feasibility" in serialized
    assert "physically impossible" not in serialized


def test_single_candidate_pool_uses_deterministic_nonempty_pseudo_batches(tmp_path):
    audit = _load_audit()
    bins = tmp_path / "bins.csv"
    _write_bins(bins)
    rows: list[dict[str, object]] = []
    found = {batch: 0 for batch in range(4)}
    probe = 0
    while min(found.values()) < 5:
        candidate_id = f"candidate-{probe}"
        batch = audit._stable_batch(candidate_id, 4)
        if found[batch] < 5:
            rows.append(_candidate(candidate_id, (0, 0, 0, 0)))
            found[batch] += 1
        probe += 1
    candidate_csv = tmp_path / "candidate_pool.csv"
    _write_csv(candidate_csv, rows)
    out_dir = tmp_path / "audit"

    assert audit.main(
        [
            "--bins-csv",
            str(bins),
            "--candidate-csv",
            str(candidate_csv),
            "--out-dir",
            str(out_dir),
            "--min-candidate-rows",
            "20",
            "--no-plots",
        ]
    ) == 0
    summary = json.loads((out_dir / "candidate_reachability_consensus_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["candidate_batch_mode"] == "stable_candidate_hash_folds"
    assert summary["batch_row_counts"] == [5, 5, 5, 5]
    robust = next(record for record in summary["records"] if record["bin_key"] == "0|0|0|0")
    assert robust["candidate_evidence_class"] == audit.ROBUST


def test_missing_uncertainty_provenance_waits_without_relabeling_bins(tmp_path):
    audit = _load_audit()
    bins = tmp_path / "bins.csv"
    _write_bins(bins)
    candidate_csv = tmp_path / "candidate_pool.csv"
    _write_csv(
        candidate_csv,
        [
            _candidate(f"candidate-{index}", (0, 0, 0, 0), include_uncertainty=False)
            for index in range(40)
        ],
    )
    out_dir = tmp_path / "audit"

    assert audit.main(
        [
            "--bins-csv",
            str(bins),
            "--candidate-csv",
            str(candidate_csv),
            "--out-dir",
            str(out_dir),
            "--min-candidate-rows",
            "20",
            "--no-plots",
        ]
    ) == 2
    summary = json.loads((out_dir / "candidate_reachability_consensus_summary.json").read_text())
    assert summary["overall_status"] == "WAITING"
    assert summary["decision"] == "WAIT_FOR_COMPLETE_MULTI_BATCH_CANDIDATE_EVIDENCE"
    assert summary["records"] == []
    assert summary["checks"]["uncertainty_provenance_present"] is False


def test_wrapper_and_campaign_preflight_keep_reachability_audit_advisory():
    repo = Path(__file__).resolve().parents[1]
    wrapper = (repo / "scripts" / "run_mars56_s4p_adaptive_physical_acquisition_round.sh").read_text()
    controller = (repo / "scripts" / "run_accepted_1m_campaign_controller.sh").read_text()
    assert "audit_physical_feature_candidate_reachability_consensus.py" in wrapper
    assert "candidate_reachability_consensus_summary.json" in wrapper
    assert "reachability consensus is advisory" in wrapper
    assert "audit_physical_feature_candidate_reachability_consensus.py" in controller
