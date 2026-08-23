#!/usr/bin/env bash
set -euo pipefail

# Local behavior test for the report-facing 100k evidence index builder.
#
# This extracts the production Python builder embedded in
# RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh and runs it
# against small synthetic checkpoint directories. The test proves the index
# accepts a complete strict evidence chain and rejects missing/weak proof for
# training CSV, traceability counts, model counts, and |K| diagnostics.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
SOURCE="$ROOT_DIR/RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh"

if [ ! -f "$SOURCE" ]; then
  echo "EVIDENCE_INDEX_STRICT_BEHAVIOR_STATUS=FAIL missing source: $SOURCE" >&2
  exit 2
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_evidence_index_strict.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

python3 - "$SOURCE" "$TMP_ROOT/evidence_index_builder.py" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
out = Path(sys.argv[2])
lines = source.read_text(encoding="utf-8").splitlines()
start = None
for index, line in enumerate(lines):
    if '"$PY" - "$BASE" "$EXPECTED_PER_CHUNK" "$EXPECTED_CHUNKS" "$JSON_OUT" "$MD_OUT" <<' in line:
        start = index + 1
        break
if start is None:
    raise SystemExit("embedded builder heredoc not found")
end = None
for index in range(start, len(lines)):
    if lines[index] == "PY":
        end = index
        break
if end is None:
    raise SystemExit("embedded builder heredoc end not found")
out.write_text("\n".join(lines[start:end]) + "\n", encoding="utf-8")
PY

python3 - "$TMP_ROOT" <<'PY'
from __future__ import annotations

from pathlib import Path
import copy
import json
import subprocess
import sys

tmp_root = Path(sys.argv[1])
builder = tmp_root / "evidence_index_builder.py"
expected = 3
expected_chunks = 1

required_steps = {
    "stable_index",
    "response_features",
    "enrichment",
    "uniformity",
    "uniformity_manifest",
    "training",
    "model",
    "traceability",
}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def touch(path: Path, content: bytes | str = "ok\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def checkpoint_summary(root: Path, variant: str, count: int) -> dict:
    summary = {
        "overall_status": "PASS",
        "expected_count": count,
        "min_valid": count,
        "physical_uniformity_gate": {
            "require_four_d_gate": True,
            "min_1d_occupied_fraction": 0.90,
            "min_1d_entropy_fraction": 0.90,
            "max_1d_bin_imbalance": 2.50,
            "min_pair_occupied_fraction": 0.65,
            "min_pair_entropy_fraction": 0.80,
            "min_four_d_occupied_fraction": 0.50,
        },
        "statuses": {step: "PASS" for step in required_steps},
        "details": {
            "uniformity": {
                "valid_feature_count": count,
                "k_mode": "magnitude",
                "ranges": {
                    "lp": {"min": 0.5, "max": 3.0, "source": "explicit", "explicit": True, "valid": True},
                    "ls": {"min": 0.5, "max": 3.0, "source": "explicit", "explicit": True, "valid": True},
                    "q": {"min": 5.0, "max": 25.0, "source": "explicit", "explicit": True, "valid": True},
                    "k": {"min": 0.0, "max": 0.8, "source": "explicit", "explicit": True, "valid": True},
                },
                "k_sign_diagnostics": {
                    "uniformity_k_axis": "|K|",
                    "signed_k_count": count,
                },
                "one_dimensional_uniformity": {
                    "lp": {"occupied_fraction": 1.0, "normalized_entropy": 0.95, "max_to_min_nonzero_ratio": 1.2},
                    "ls": {"occupied_fraction": 1.0, "normalized_entropy": 0.95, "max_to_min_nonzero_ratio": 1.2},
                    "q": {"occupied_fraction": 1.0, "normalized_entropy": 0.95, "max_to_min_nonzero_ratio": 1.2},
                    "k": {"occupied_fraction": 1.0, "normalized_entropy": 0.95, "max_to_min_nonzero_ratio": 1.2},
                },
                "pairwise_uniformity": {
                    "lp_ls": {"occupied_fraction": 0.70, "normalized_entropy": 0.85},
                    "lp_q": {"occupied_fraction": 0.70, "normalized_entropy": 0.85},
                    "lp_k": {"occupied_fraction": 0.70, "normalized_entropy": 0.85},
                    "ls_q": {"occupied_fraction": 0.70, "normalized_entropy": 0.85},
                    "ls_k": {"occupied_fraction": 0.70, "normalized_entropy": 0.85},
                    "q_k": {"occupied_fraction": 0.70, "normalized_entropy": 0.85},
                },
                "four_dimensional_uniformity": {
                    "occupied_fraction": 0.50,
                    "occupied_bins": 128,
                    "total_bins": 256,
                },
            },
            "uniformity_manifest": {
                "visual_artifact_count": 3,
                "require_plots": True,
            },
            "training": {
                "training_count": count,
            },
            "model": {
                "usable_row_count": count,
                "test_row_count": 1,
                "metrics": {
                    "test_count": 1,
                    "geometry_count": 11,
                    "max_normalized_mae": 0.10,
                    "max_normalized_rmse": 0.20,
                    "mean_normalized_mae": 0.05,
                    "mean_normalized_rmse": 0.10,
                },
            },
            "traceability": {
                "stable_manifest_rows": count,
                "stable_unique_evaluations": count,
                "response_feature_rows": count,
                "response_unique_evaluations": count,
                "response_dataset_rows": count,
                "response_dataset_unique_evaluations": count,
                "enriched_rows": count,
                "enriched_unique_evaluations": count,
                "training_rows": count,
                "training_unique_evaluations": count,
            },
        },
    }
    if variant == "low_traceability_rows":
        summary["details"]["traceability"]["training_rows"] = count - 1
    elif variant == "low_traceability_unique":
        summary["details"]["traceability"]["training_unique_evaluations"] = count - 1
    elif variant == "wrong_k_axis":
        summary["details"]["uniformity"]["k_sign_diagnostics"]["uniformity_k_axis"] = "K"
    elif variant == "low_model_rows":
        summary["details"]["model"]["usable_row_count"] = count - 1
    elif variant == "missing_model_metrics":
        summary["details"]["model"].pop("metrics")
    elif variant == "zero_model_test_rows":
        summary["details"]["model"]["test_row_count"] = 0
        summary["details"]["model"]["metrics"]["test_count"] = 0
    elif variant == "missing_k_diagnostics":
        summary["details"]["uniformity"].pop("k_sign_diagnostics")
    elif variant == "signed_k_count_low":
        summary["details"]["uniformity"]["k_sign_diagnostics"]["signed_k_count"] = count - 1
    elif variant == "wrong_explicit_range":
        summary["details"]["uniformity"]["ranges"]["lp"]["max"] = 3.5
    elif variant == "observed_range_not_explicit":
        summary["details"]["uniformity"]["ranges"]["q"]["source"] = "observed_with_5pct_padding"
        summary["details"]["uniformity"]["ranges"]["q"]["explicit"] = False
    elif variant == "missing_four_d_gate":
        summary["physical_uniformity_gate"]["require_four_d_gate"] = False
    elif variant == "low_four_d_occupancy":
        summary["details"]["uniformity"]["four_dimensional_uniformity"]["occupied_fraction"] = 0.25
    elif variant == "missing_one_d_uniformity":
        summary["details"]["uniformity"].pop("one_dimensional_uniformity")
    elif variant == "low_one_d_entropy":
        summary["details"]["uniformity"]["one_dimensional_uniformity"]["lp"]["normalized_entropy"] = 0.50
    elif variant == "high_one_d_imbalance":
        summary["details"]["uniformity"]["one_dimensional_uniformity"]["q"]["max_to_min_nonzero_ratio"] = 9.0
    elif variant == "low_pair_occupancy":
        summary["details"]["uniformity"]["pairwise_uniformity"]["lp_ls"]["occupied_fraction"] = 0.20
    elif variant == "low_pair_entropy":
        summary["details"]["uniformity"]["pairwise_uniformity"]["q_k"]["normalized_entropy"] = 0.40
    elif variant in {"complete", "missing_training_csv", "duplicate_global_training", "empty_required_artifact"}:
        pass
    else:
        raise ValueError(f"unknown variant: {variant}")
    return summary


def make_training_csv(path: Path, chunk_index: int, row_count: int, duplicate_global: bool = False) -> None:
    rows = ["evaluation,Lp,Ls,Q,K"]
    for idx in range(row_count):
        if duplicate_global and chunk_index == 2 and idx == 0:
            evaluation = "eval_001_000"
        else:
            evaluation = f"eval_{chunk_index:03d}_{idx:03d}"
        rows.append(f"{evaluation},1,1,10,0.4")
    touch(path, "\n".join(rows) + "\n")


def make_checkpoint_artifacts(summary_root: Path, variant: str, chunk_index: int = 1, row_count: int = expected) -> None:
    touch(summary_root / "mars56_s4p_physical_checkpoint_pipeline_commands.log")
    uniformity = summary_root / "physical_feature_uniformity"
    training = summary_root / "physical_feature_inverse_training_table"
    model = summary_root / "physical_feature_inverse_checkpoint_test"
    traceability = summary_root / "physical_checkpoint_traceability"
    write_json(uniformity / "physical_feature_uniformity_summary.json", {"overall_status": "PASS"})
    touch(uniformity / "physical_feature_uniformity_report.md")
    write_json(uniformity / "physical_feature_uniformity_manifest.json", {"overall_status": "PASS"})
    touch(uniformity / "physical_feature_marginal_histograms.png", b"" if variant == "empty_required_artifact" else b"png")
    touch(uniformity / "physical_feature_pair_scatter.png", b"png")
    touch(uniformity / "physical_feature_pair_occupancy_heatmaps.png", b"png")
    write_json(training / "physical_feature_inverse_training_manifest.json", {"overall_status": "PASS"})
    if variant != "missing_training_csv":
        make_training_csv(
            training / "physical_feature_inverse_training_table.csv",
            chunk_index,
            row_count,
            duplicate_global=(variant == "duplicate_global_training"),
        )
    write_json(model / "physical_feature_inverse_checkpoint_test_summary.json", {"overall_status": "PASS"})
    write_json(traceability / "physical_checkpoint_traceability_summary.json", {"overall_status": "PASS"})
    touch(traceability / "physical_checkpoint_traceability_report.md")


def make_case(
    case_dir: Path,
    variant: str,
    chunks: int = expected_chunks,
    start_chunk: int = 1,
    start_cumulative_chunk: int | None = None,
) -> Path:
    base = case_dir / "base"
    if start_cumulative_chunk is None:
        start_cumulative_chunk = start_chunk
    write_json(
        base / "status" / "mars56_production_rate_eta_latest.json",
        {
            "audit_mode": "REMOTE_READ_ONLY_AUDIT",
            "return_code": 0,
            "contract": {
                "expected_parallel_jobs": 48,
                "target_seconds_per_accepted_row": 4.0,
                "target_days_per_100k": 5.0,
            },
            "interpreted": {
                "latest_parallel_jobs": 48,
                "measured_seconds_per_accepted_row": 3.71,
                "eta_days_per_100k": 4.3,
                "eta_days_for_1m_at_same_rate": 43.0,
                "production_rate_target_status": "PASS",
                "production_rate_audit_status": "PASS",
            },
            "artifact_boundary": "synthetic remote read-only audit",
        },
    )
    touch(base / "status" / "mars56_production_rate_eta_latest_CN.md", "# rate\n")
    for chunk_index in range(start_chunk, start_chunk + chunks):
        tag = f"chunk_{chunk_index:03d}_100k_after_chunk08_pass"
        dataset = base / "datasets" / tag
        for idx in range(expected):
            touch(dataset / "evaluations" / f"eval_{chunk_index:03d}_{idx:03d}" / "emx" / "emx.s4p", "! s4p\n")
        write_json(dataset / "parallel_candidate_queue_dataset_summary.json", {"overall_status": "PASS"})
        summary_root = base / "model_tests" / tag / "physical_checkpoint"
        write_json(
            summary_root / "mars56_s4p_physical_checkpoint_pipeline_summary.json",
            checkpoint_summary(summary_root, variant, expected),
        )
        make_checkpoint_artifacts(summary_root, variant, chunk_index=chunk_index, row_count=expected)

        cumulative_index = start_cumulative_chunk + (chunk_index - start_chunk)
        cum_tag = f"cumulative_{cumulative_index * 100:04d}k_after_chunk08_pass"
        cum_root = base / "cumulative_model_tests" / cum_tag / "physical_checkpoint"
        write_json(
            cum_root / "mars56_s4p_physical_checkpoint_pipeline_summary.json",
            checkpoint_summary(cum_root, "complete", expected * cumulative_index),
        )
        make_checkpoint_artifacts(cum_root, "complete", chunk_index=chunk_index, row_count=expected * cumulative_index)
    return base


def run_builder(
    case_name: str,
    variant: str,
    chunks: int = expected_chunks,
    start_chunk: int = 1,
    start_cumulative_chunk: int | None = None,
) -> dict:
    case_dir = tmp_root / case_name
    base = make_case(
        case_dir,
        variant,
        chunks=chunks,
        start_chunk=start_chunk,
        start_cumulative_chunk=start_cumulative_chunk,
    )
    if case_name == "unexpected_formal_tag":
        chunk_index = start_chunk + chunks
        tag = f"chunk_{chunk_index:03d}_100k_after_chunk08_pass"
        dataset = base / "datasets" / tag
        for idx in range(expected):
            touch(dataset / "evaluations" / f"eval_{chunk_index:03d}_{idx:03d}" / "emx" / "emx.s4p", "! s4p\n")
        write_json(dataset / "parallel_candidate_queue_dataset_summary.json", {"overall_status": "PASS"})
        summary_root = base / "model_tests" / tag / "physical_checkpoint"
        write_json(
            summary_root / "mars56_s4p_physical_checkpoint_pipeline_summary.json",
            checkpoint_summary(summary_root, "complete", expected),
        )
        make_checkpoint_artifacts(summary_root, "complete", chunk_index=chunk_index, row_count=expected)
    if case_name == "unexpected_cumulative_tag":
        cumulative_index = (start_cumulative_chunk if start_cumulative_chunk is not None else start_chunk) + chunks
        cum_tag = f"cumulative_{cumulative_index * 100:04d}k_after_chunk08_pass"
        cum_root = base / "cumulative_model_tests" / cum_tag / "physical_checkpoint"
        write_json(
            cum_root / "mars56_s4p_physical_checkpoint_pipeline_summary.json",
            checkpoint_summary(cum_root, "complete", expected * cumulative_index),
        )
        make_checkpoint_artifacts(cum_root, "complete", chunk_index=cumulative_index, row_count=expected * cumulative_index)
    if case_name == "duplicate_cumulative_tag":
        duplicate_index = start_cumulative_chunk if start_cumulative_chunk is not None else start_chunk
        cum_tag = f"cumulative_{duplicate_index * 100:04d}k_after_chunk08_pass"
        duplicate_root = base / "cumulative_model_tests" / "archive" / cum_tag / "physical_checkpoint"
        write_json(
            duplicate_root / "mars56_s4p_physical_checkpoint_pipeline_summary.json",
            checkpoint_summary(duplicate_root, "complete", expected * duplicate_index),
        )
        make_checkpoint_artifacts(duplicate_root, "complete", chunk_index=duplicate_index, row_count=expected * duplicate_index)
    json_out = case_dir / "out" / "evidence.json"
    md_out = case_dir / "out" / "evidence.md"
    completed = subprocess.run(
        [
            sys.executable,
            str(builder),
            str(base),
            str(expected),
            str(chunks),
            str(json_out),
            str(md_out),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{case_name} builder rc={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert md_out.exists(), case_name
    return data


def assert_contains(haystack: object, needle: str, case_name: str) -> None:
    encoded = json.dumps(haystack, ensure_ascii=False, sort_keys=True)
    if needle not in encoded:
        raise AssertionError(f"{case_name} missing {needle!r} in {encoded}")


cases = [
    ("complete", "complete", "PASS", []),
    ("missing_training_csv", "missing_training_csv", "IN_PROGRESS", ["training_csv"]),
    ("low_traceability_rows", "low_traceability_rows", "IN_PROGRESS", ["traceability.training_rows=2"]),
    ("low_traceability_unique", "low_traceability_unique", "IN_PROGRESS", ["traceability.training_unique_evaluations=2"]),
    ("wrong_k_axis", "wrong_k_axis", "IN_PROGRESS", ["uniformity.k_sign_diagnostics.uniformity_k_axis='K'"]),
    ("low_model_rows", "low_model_rows", "IN_PROGRESS", ["model.usable_row_count=2"]),
    ("missing_model_metrics", "missing_model_metrics", "IN_PROGRESS", ["model.metrics=MISSING"]),
    ("zero_model_test_rows", "zero_model_test_rows", "IN_PROGRESS", ["model.test_row_count=0"]),
    ("missing_k_diagnostics", "missing_k_diagnostics", "IN_PROGRESS", ["uniformity.k_sign_diagnostics=MISSING"]),
    ("signed_k_count_low", "signed_k_count_low", "IN_PROGRESS", ["uniformity.k_sign_diagnostics.signed_k_count=2"]),
    ("wrong_explicit_range", "wrong_explicit_range", "IN_PROGRESS", ["uniformity.ranges.lp=(0.5,3.5),expected=(0.5,3.0)"]),
    ("observed_range_not_explicit", "observed_range_not_explicit", "IN_PROGRESS", ["uniformity.ranges.q.explicit=False,source='observed_with_5pct_padding'"]),
    ("missing_one_d_uniformity", "missing_one_d_uniformity", "IN_PROGRESS", ["uniformity.one_dimensional_uniformity=MISSING"]),
    ("low_one_d_entropy", "low_one_d_entropy", "IN_PROGRESS", ["uniformity.one_dimensional_uniformity.lp.normalized_entropy=0.5"]),
    ("high_one_d_imbalance", "high_one_d_imbalance", "IN_PROGRESS", ["uniformity.one_dimensional_uniformity.q.max_to_min_nonzero_ratio=9"]),
    ("low_pair_occupancy", "low_pair_occupancy", "IN_PROGRESS", ["uniformity.pairwise_uniformity.lp_ls.occupied_fraction=0.2"]),
    ("low_pair_entropy", "low_pair_entropy", "IN_PROGRESS", ["uniformity.pairwise_uniformity.q_k.normalized_entropy=0.4"]),
    ("missing_four_d_gate", "missing_four_d_gate", "IN_PROGRESS", ["physical_uniformity_gate.require_four_d_gate=False"]),
    ("low_four_d_occupancy", "low_four_d_occupancy", "IN_PROGRESS", ["uniformity.four_dimensional_uniformity.occupied_fraction=0.25"]),
    ("empty_required_artifact", "empty_required_artifact", "IN_PROGRESS", ["empty_required_artifacts", "plot_marginal_histograms"]),
    ("duplicate_global_training", "duplicate_global_training", "IN_PROGRESS", ["duplicate_evaluation_count=1"]),
    ("missing_expected_formal_tag", "complete", "IN_PROGRESS", ["missing_expected_formal_100k_tags"]),
    ("unexpected_formal_tag", "complete", "IN_PROGRESS", ["unexpected_formal_100k_tags"]),
    ("missing_expected_cumulative_tag", "complete", "IN_PROGRESS", ["missing_expected_cumulative_checkpoint_tags"]),
    ("unexpected_cumulative_tag", "complete", "IN_PROGRESS", ["unexpected_cumulative_checkpoint_tags"]),
    ("duplicate_cumulative_tag", "complete", "IN_PROGRESS", ["duplicate_cumulative_checkpoint_tags"]),
]

for case_name, variant, expected_status, expected_needles in cases:
    chunks = 2 if case_name == "duplicate_global_training" else expected_chunks
    start_chunk = 2 if case_name == "missing_expected_formal_tag" else 1
    start_cumulative_chunk = 2 if case_name == "missing_expected_cumulative_tag" else None
    data = run_builder(
        case_name,
        variant,
        chunks=chunks,
        start_chunk=start_chunk,
        start_cumulative_chunk=start_cumulative_chunk,
    )
    assert data["overall_status"] == expected_status, data
    contract = data.get("strict_evidence_contract") or {}
    rate = data.get("production_rate_artifact") or {}
    assert rate.get("json_exists") is True, rate
    assert rate.get("md_exists") is True, rate
    assert rate.get("audit_mode") == "REMOTE_READ_ONLY_AUDIT", rate
    assert rate.get("production_rate_audit_status") == "PASS", rate
    assert rate.get("latest_parallel_jobs") == 48, rate
    assert rate.get("measured_seconds_per_accepted_row") == 3.71, rate
    assert contract.get("required_k_contract", {}).get("uniformity_k_axis") == "|K|", contract
    assert contract.get("required_physical_ranges", {}).get("lp", {}).get("max") == 3.0, contract
    assert contract.get("required_physical_ranges", {}).get("q", {}).get("source") == "explicit", contract
    assert "training_csv" in contract.get("required_artifacts", []), contract
    assert "traceability_report" in contract.get("required_artifacts", []), contract
    assert contract.get("required_global_training_evaluation_proof", {}).get("duplicate_evaluation_count") == 0, contract
    assert contract.get("required_formal_100k_tags") == [f"chunk_{idx:03d}_100k_after_chunk08_pass" for idx in range(1, chunks + 1)], contract
    assert contract.get("required_cumulative_checkpoint_tags") == [f"cumulative_{idx * 100:04d}k_after_chunk08_pass" for idx in range(1, chunks + 1)], contract
    if expected_status == "PASS":
        formal = data["formal_100k"][0]
        assert formal["checkpoint_proof_reasons"] == [], formal
        assert data["global_training_evaluation_proof"]["status"] == "PASS", data["global_training_evaluation_proof"]
        assert data["global_training_evaluation_proof"]["unique_training_evaluation_count"] == expected * chunks, data["global_training_evaluation_proof"]
        assert data["global_training_evaluation_proof"]["duplicate_evaluation_count"] == 0, data["global_training_evaluation_proof"]
    if expected_status == "PASS":
        formal = data["formal_100k"][0]
        assert formal["evidence_status"] == "PASS", formal
        assert formal["checkpoint_proof"] == "PASS", formal
        assert data["formal_100k_evidence_pass_count"] == 1, data
        assert data["cumulative_evidence_pass_count"] == 1, data
    elif case_name == "duplicate_global_training":
        assert data["global_training_evaluation_proof"]["status"] == "FAIL", data["global_training_evaluation_proof"]
        assert data["global_training_evaluation_proof"]["duplicate_evaluation_count"] == 1, data["global_training_evaluation_proof"]
        assert data["formal_100k_evidence_pass_count"] == chunks, data
        assert data["cumulative_evidence_pass_count"] == chunks, data
        for item in data["formal_100k"]:
            assert item["evidence_status"] == "PASS", item
        for needle in expected_needles:
            assert_contains(data, needle, case_name)
    elif case_name == "missing_expected_formal_tag":
        assert data["formal_100k_tag_status"] == "IN_PROGRESS", data
        assert data["missing_expected_formal_100k_tags"] == ["chunk_001_100k_after_chunk08_pass"], data
        assert data["unexpected_formal_100k_tags"] == ["chunk_002_100k_after_chunk08_pass"], data
        assert data["formal_100k_evidence_pass_count"] == 0, data
        for needle in expected_needles:
            assert_contains(data, needle, case_name)
    elif case_name == "unexpected_formal_tag":
        assert data["formal_100k_tag_status"] == "IN_PROGRESS", data
        assert data["missing_expected_formal_100k_tags"] == [], data
        assert data["unexpected_formal_100k_tags"] == ["chunk_002_100k_after_chunk08_pass"], data
        assert data["formal_100k_evidence_pass_count"] == expected_chunks, data
        for needle in expected_needles:
            assert_contains(data, needle, case_name)
    elif case_name == "missing_expected_cumulative_tag":
        assert data["cumulative_checkpoint_tag_status"] == "IN_PROGRESS", data
        assert data["missing_expected_cumulative_checkpoint_tags"] == ["cumulative_0100k_after_chunk08_pass"], data
        assert data["unexpected_cumulative_checkpoint_tags"] == ["cumulative_0200k_after_chunk08_pass"], data
        assert data["cumulative_evidence_pass_count"] == 0, data
        for needle in expected_needles:
            assert_contains(data, needle, case_name)
    elif case_name == "unexpected_cumulative_tag":
        assert data["cumulative_checkpoint_tag_status"] == "IN_PROGRESS", data
        assert data["missing_expected_cumulative_checkpoint_tags"] == [], data
        assert data["unexpected_cumulative_checkpoint_tags"] == ["cumulative_0200k_after_chunk08_pass"], data
        assert data["cumulative_evidence_pass_count"] == expected_chunks, data
        for needle in expected_needles:
            assert_contains(data, needle, case_name)
    elif case_name == "duplicate_cumulative_tag":
        assert data["cumulative_checkpoint_tag_status"] == "IN_PROGRESS", data
        assert data["missing_expected_cumulative_checkpoint_tags"] == [], data
        assert data["unexpected_cumulative_checkpoint_tags"] == [], data
        assert data["duplicate_cumulative_checkpoint_tags"] == ["cumulative_0100k_after_chunk08_pass"], data
        assert data["cumulative_evidence_pass_count"] == expected_chunks, data
        for needle in expected_needles:
            assert_contains(data, needle, case_name)
    else:
        formal = data["formal_100k"][0]
        assert formal["evidence_status"] == "NEEDS_CHECKPOINT_OR_ARTIFACT_REPAIR", formal
        if case_name == "empty_required_artifact":
            assert formal["required_artifact_status"] == "FAIL", formal
            assert formal["empty_required_artifacts"] == ["plot_marginal_histograms"], formal
        assert data["formal_100k_evidence_pass_count"] == 0, data
        for needle in expected_needles:
            assert_contains(data, needle, case_name)
    print(f"EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE={case_name} status=PASS")

print("EVIDENCE_INDEX_STRICT_BEHAVIOR_STATUS=PASS")
PY
