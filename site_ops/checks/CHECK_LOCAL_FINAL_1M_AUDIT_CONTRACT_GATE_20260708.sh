#!/usr/bin/env bash
set -euo pipefail

# Local guard for the final 1M completion audit. It verifies that the final
# PASS condition requires the production-plan contract audit, accepted-campaign
# strict completion audit (including every physical-cell tail check), and the
# remote production-rate artifact. This does not connect to MARS.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
FINAL_AUDIT="$ROOT_DIR/CHECK_MARS56_1M_GOAL_COMPLETION_AFTER_DUO_20260707.sh"

if [ ! -f "$FINAL_AUDIT" ]; then
  echo "FINAL_1M_AUDIT_CONTRACT_GATE_STATUS=FAIL missing final audit: $FINAL_AUDIT" >&2
  exit 2
fi

bash -n "$FINAL_AUDIT"

python3 - "$FINAL_AUDIT" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
required_tokens = [
    "CONTRACT_BUILD",
    "CONTRACT_AUDIT",
    "build_mars56_1m_production_plan_contract.py",
    "audit_mars56_1m_production_plan_contract.py",
    "production_plan_contract_audit_result",
    "production_plan_contract_audit_summary",
    "ACCEPTED_FINAL_AUDIT_SUMMARY",
    "ACCEPTED_FINAL_AUDIT_MARKER",
    "ACCEPTED_CAMPAIGN_COMPLETE_MARKER",
    "accepted_campaign_completion_proof",
    "ACCEPTED_1M_REAL_EMX_CAMPAIGN_COMPLETE",
    "manifest_physical_cell_tail_error",
    "accepted_checkpoint_physical_cell_tail_not_pass",
    "manifest_balanced_mse_bni_status",
    "manifest_balanced_mse_bni_decision_rule",
    "manifest_balanced_mse_bni_p90_tail_ci_lower",
    "balanced_mse_bni_artifact_sha256_matches",
    "accepted_checkpoint_200k_bni_status",
    "accepted_checkpoint_200k_bni_nonfinite",
    "accepted_checkpoint_200k_bni_artifact",
    "manifest_mondrian_conformal",
    "manifest_mondrian_conformal_decision",
    "mondrian_conformal_artifact_sha256_matches",
    "accepted_checkpoint_600k_mondrian_status",
    "accepted_checkpoint_600k_mondrian_support",
    "accepted_checkpoint_600k_mondrian_artifact",
    "fixed_common_test_panel_contract_pass",
    "production_rate_artifact",
    "expected_parallel_jobs = 48",
    "expected_target_seconds_per_row = 4.0",
    "expected_target_days_per_100k = 5.0",
    "max_seconds_per_accepted_row = 4.5",
    "max_days_per_100k = 5.5",
    "production_rate_artifact.json_exists",
    "production_rate_artifact.json_bytes",
    "production_rate_artifact.md_exists",
    "production_rate_artifact.md_bytes",
    'production_rate_artifact.audit_mode',
    'REMOTE_READ_ONLY_AUDIT',
    "production_rate_artifact.production_rate_audit_status",
    "production_rate_artifact.production_rate_target_status",
    "production_rate_artifact.latest_parallel_jobs",
    "production_rate_artifact.expected_parallel_jobs",
    "production_rate_artifact.measured_seconds_per_accepted_row",
    "production_rate_artifact.eta_days_per_100k",
    "production_rate_artifact_audit_result",
    "production_rate_artifact_audit_reasons",
    "global_training_evaluation_proof",
    "global_training_evaluation_proof.status",
    "global_training_evaluation_proof.training_row_count",
    "global_training_evaluation_proof.unique_training_evaluation_count",
    "global_training_evaluation_proof.duplicate_evaluation_count",
    "global_training_evaluation_proof.missing_or_bad_training_csv_count",
    "global_training_evaluation_status",
    "global_training_evaluation_unique_count",
    "global_training_evaluation_duplicate_count",
    "formal_100k_tag_status",
    "missing_expected_formal_100k_tags",
    "unexpected_formal_100k_tags",
    "evidence_formal_100k_tag_status",
    "evidence_missing_expected_formal_100k_tags",
    "evidence_unexpected_formal_100k_tags",
    "cumulative_checkpoint_tag_status",
    "missing_expected_cumulative_checkpoint_tags",
    "unexpected_cumulative_checkpoint_tags",
    "duplicate_cumulative_checkpoint_tags",
    "evidence_cumulative_checkpoint_tag_status",
    "evidence_missing_expected_cumulative_checkpoint_tags",
    "evidence_unexpected_cumulative_checkpoint_tags",
    "evidence_duplicate_cumulative_checkpoint_tags",
    "expected_min_four_d_occupied_fraction = 0.50",
    "expected_uniformity_thresholds",
    "min_1d_occupied_fraction",
    "min_1d_entropy_fraction",
    "max_1d_bin_imbalance",
    "min_pair_occupied_fraction",
    "min_pair_entropy_fraction",
    "physical_uniformity_gate.require_four_d_gate",
    "physical_uniformity_gate.min_four_d_occupied_fraction",
    "min_four_d_normalized_entropy",
    "max_four_d_nonzero_bin_imbalance",
    "uniformity.one_dimensional_uniformity=MISSING",
    "uniformity.one_dimensional_uniformity.{feature_name}=MISSING",
    "uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}",
    "uniformity.pairwise_uniformity=MISSING",
    "uniformity.pairwise_uniformity.{pair_name}",
    "uniformity.pairwise_uniformity.{pair_name}.{metric_name}",
    "uniformity.four_dimensional_uniformity=MISSING",
    "uniformity.four_dimensional_uniformity.occupied_fraction",
    "uniformity.four_dimensional_uniformity.{metric_name}",
    "model.test_row_count",
    "model.metrics=MISSING",
    "model.metrics.test_count",
    "model.metrics.geometry_count",
    "model.metrics.{metric_key}",
    "max_normalized_mae",
    "max_normalized_rmse",
    "mean_normalized_mae",
    "mean_normalized_rmse",
]
missing = [token for token in required_tokens if token not in text]
if missing:
    print("FINAL_1M_AUDIT_CONTRACT_GATE_STATUS=FAIL")
    print("missing_tokens=" + ",".join(missing))
    raise SystemExit(1)

match = re.search(
    r'if \[ "\$total_nonempty" -ge "\$EXPECTED_TOTAL" \].*?then\s*\n\s*echo "ONE_MILLION_GOAL_STATUS=PASS"',
    text,
    flags=re.S,
)
if not match:
    print("FINAL_1M_AUDIT_CONTRACT_GATE_STATUS=FAIL")
    print("reason=final_pass_condition_not_found")
    raise SystemExit(1)
condition = match.group(0)
needed = '[ "$contract_audit_result" = "PASS" ]'
if needed not in condition:
    print("FINAL_1M_AUDIT_CONTRACT_GATE_STATUS=FAIL")
    print("reason=contract_audit_result_not_required_in_final_pass_condition")
    raise SystemExit(1)

needed_accepted = '[ "$accepted_campaign_proof" = "PASS" ]'
if needed_accepted not in condition:
    print("FINAL_1M_AUDIT_CONTRACT_GATE_STATUS=FAIL")
    print("reason=accepted_campaign_completion_not_required_in_final_pass_condition")
    raise SystemExit(1)

if '[ "$failures" -eq 0 ]' not in condition:
    print("FINAL_1M_AUDIT_CONTRACT_GATE_STATUS=FAIL")
    print("reason=failure_counter_not_required_in_final_pass_condition")
    raise SystemExit(1)

print("FINAL_1M_AUDIT_CONTRACT_GATE_CASE=contract_required_in_final_pass status=PASS")
print("FINAL_1M_AUDIT_CONTRACT_GATE_CASE=accepted_campaign_required_in_final_pass status=PASS")
print("FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_artifact_required_in_evidence_index status=PASS")
PY

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_final_1m_gate.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

python3 - "$FINAL_AUDIT" "$TMP_ROOT/evidence_index_audit.py" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1])
out = Path(sys.argv[2])
lines = source.read_text(encoding="utf-8").splitlines()
start = None
for index, line in enumerate(lines):
    if "evidence_tmp=$(" in line and "$EVIDENCE_INDEX_JSON" in line and "$EVIDENCE_INDEX_MD" in line:
        start = index + 1
        break
if start is None:
    raise SystemExit("final audit evidence-index heredoc not found")
end = None
for index in range(start, len(lines)):
    if lines[index] == "PY":
        end = index
        break
if end is None:
    raise SystemExit("final audit evidence-index heredoc end not found")
out.write_text("\n".join(lines[start:end]) + "\n", encoding="utf-8")
PY

python3 - "$FINAL_AUDIT" "$TMP_ROOT/final_checkpoint_proof.py" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1])
out = Path(sys.argv[2])
lines = source.read_text(encoding="utf-8").splitlines()
start = None
for index, line in enumerate(lines):
    if '"$PY" - "$path" "$expected" <<' in line:
        start = index + 1
        break
if start is None:
    raise SystemExit("final audit checkpoint_proof heredoc not found")
end = None
for index in range(start, len(lines)):
    if lines[index] == "PY":
        end = index
        break
if end is None:
    raise SystemExit("final audit checkpoint_proof heredoc end not found")
out.write_text("\n".join(lines[start:end]) + "\n", encoding="utf-8")
PY

python3 - "$FINAL_AUDIT" "$TMP_ROOT/accepted_campaign_completion_proof.py" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1])
out = Path(sys.argv[2])
lines = source.read_text(encoding="utf-8").splitlines()
start = None
for index, line in enumerate(lines):
    if '"$PY" - "$summary_path" "$audit_marker" "$campaign_marker" "$expected_checkpoints" <<' in line:
        start = index + 1
        break
if start is None:
    raise SystemExit("accepted campaign completion proof heredoc not found")
end = None
for index in range(start, len(lines)):
    if lines[index] == "PY":
        end = index
        break
if end is None:
    raise SystemExit("accepted campaign completion proof heredoc end not found")
out.write_text("\n".join(lines[start:end]) + "\n", encoding="utf-8")
PY

python3 - "$TMP_ROOT" <<'PY'
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

tmp_root = Path(sys.argv[1])
audit = tmp_root / "evidence_index_audit.py"
checkpoint_proof = tmp_root / "final_checkpoint_proof.py"
accepted_campaign_proof = tmp_root / "accepted_campaign_completion_proof.py"


def base_checkpoint_summary() -> dict:
    expected = 3
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
    return {
        "overall_status": "PASS",
        "expected_count": expected,
        "min_valid": expected,
        "physical_uniformity_gate": {
            "require_four_d_gate": True,
            "min_1d_occupied_fraction": 0.90,
            "min_1d_entropy_fraction": 0.90,
            "max_1d_bin_imbalance": 2.50,
            "min_pair_occupied_fraction": 0.65,
            "min_pair_entropy_fraction": 0.80,
            "min_four_d_occupied_fraction": 0.50,
            "min_four_d_normalized_entropy": 0.80,
            "max_four_d_nonzero_bin_imbalance": 4.0,
        },
        "statuses": {step: "PASS" for step in required_steps},
        "details": {
            "uniformity": {
                "valid_feature_count": expected,
                "k_mode": "magnitude",
                "ranges": {
                    "lp": {"min": 0.5, "max": 3.0, "source": "explicit", "explicit": True},
                    "ls": {"min": 0.5, "max": 3.0, "source": "explicit", "explicit": True},
                    "q": {"min": 5.0, "max": 25.0, "source": "explicit", "explicit": True},
                    "k": {"min": 0.0, "max": 0.8, "source": "explicit", "explicit": True},
                },
                "one_dimensional_uniformity": {
                    name: {
                        "occupied_fraction": 1.0,
                        "normalized_entropy": 0.98,
                        "max_to_min_nonzero_ratio": 1.5,
                    }
                    for name in ("lp", "ls", "q", "k")
                },
                "pairwise_uniformity": {
                    name: {"occupied_fraction": 0.70, "normalized_entropy": 0.85}
                    for name in ("lp_ls", "lp_q", "lp_k", "ls_q", "ls_k", "q_k")
                },
                "four_dimensional_uniformity": {
                    "occupied_fraction": 0.50,
                    "occupied_bins": 128,
                    "total_bins": 256,
                    "normalized_entropy": 0.90,
                    "max_to_min_nonzero_ratio": 2.0,
                },
                "k_sign_diagnostics": {
                    "uniformity_k_axis": "|K|",
                    "signed_k_count": expected,
                },
            },
            "uniformity_manifest": {
                "visual_artifact_count": 3,
                "require_plots": True,
            },
            "training": {"training_count": expected},
            "model": {
                "usable_row_count": expected,
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
                "stable_manifest_rows": expected,
                "stable_unique_evaluations": expected,
                "response_feature_rows": expected,
                "response_unique_evaluations": expected,
                "response_dataset_rows": expected,
                "response_dataset_unique_evaluations": expected,
                "enriched_rows": expected,
                "enriched_unique_evaluations": expected,
                "training_rows": expected,
                "training_unique_evaluations": expected,
            },
        },
    }


def checkpoint_summary_variant(variant: str) -> dict:
    data = copy.deepcopy(base_checkpoint_summary())
    if variant == "complete":
        return data
    if variant == "missing_one_d_uniformity":
        data["details"]["uniformity"].pop("one_dimensional_uniformity")
        return data
    if variant == "low_one_d_entropy":
        data["details"]["uniformity"]["one_dimensional_uniformity"]["q"]["normalized_entropy"] = 0.85
        return data
    if variant == "high_one_d_imbalance":
        data["details"]["uniformity"]["one_dimensional_uniformity"]["k"]["max_to_min_nonzero_ratio"] = 3.0
        return data
    if variant == "low_pair_occupancy":
        data["details"]["uniformity"]["pairwise_uniformity"]["lp_k"]["occupied_fraction"] = 0.60
        return data
    if variant == "low_pair_entropy":
        data["details"]["uniformity"]["pairwise_uniformity"]["ls_q"]["normalized_entropy"] = 0.75
        return data
    if variant == "low_four_d_occupancy":
        data["details"]["uniformity"]["four_dimensional_uniformity"]["occupied_fraction"] = 0.25
        return data
    if variant == "low_four_d_entropy":
        data["details"]["uniformity"]["four_dimensional_uniformity"]["normalized_entropy"] = 0.79
        return data
    if variant == "high_four_d_imbalance":
        data["details"]["uniformity"]["four_dimensional_uniformity"]["max_to_min_nonzero_ratio"] = 5.0
        return data
    if variant == "missing_model_metrics":
        data["details"]["model"].pop("metrics")
        return data
    if variant == "zero_model_test_rows":
        data["details"]["model"]["test_row_count"] = 0
        data["details"]["model"]["metrics"]["test_count"] = 0
        return data
    raise ValueError(variant)


def run_checkpoint_case(case_name: str, variant: str, expected_status: str, needles: list[str]) -> None:
    case_dir = tmp_root / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    summary_path = case_dir / "checkpoint_summary.json"
    summary_path.write_text(
        json.dumps(checkpoint_summary_variant(variant), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(checkpoint_proof), str(summary_path), "3"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{case_name} checkpoint proof rc={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    output = completed.stdout.strip()
    if expected_status == "PASS":
        if output != "PASS":
            raise AssertionError(f"{case_name} expected PASS, got {output!r}")
    elif not output.startswith("FAIL:"):
        raise AssertionError(f"{case_name} expected FAIL, got {output!r}")
    missing = [needle for needle in needles if needle not in output]
    if missing:
        raise AssertionError(f"{case_name} missing {missing}\n{output}")
    print(f"FINAL_1M_AUDIT_CONTRACT_GATE_CASE={case_name} status=PASS")


def base_index() -> dict:
    return {
        "overall_status": "PASS",
        "formal_100k_dataset_count": 10,
        "all_formal_100k_dataset_count": 10,
        "formal_100k_evidence_pass_count": 10,
        "cumulative_evidence_count": 10,
        "all_cumulative_evidence_count": 10,
        "cumulative_evidence_pass_count": 10,
        "expected_per_chunk": 100000,
        "expected_total": 1000000,
        "formal_100k_tag_status": "PASS",
        "expected_formal_100k_tags": [
            f"chunk_{idx:03d}_100k_after_chunk08_pass"
            for idx in range(1, 11)
        ],
        "observed_formal_100k_tags": [
            f"chunk_{idx:03d}_100k_after_chunk08_pass"
            for idx in range(1, 11)
        ],
        "missing_expected_formal_100k_tags": [],
        "unexpected_formal_100k_tags": [],
        "cumulative_checkpoint_tag_status": "PASS",
        "expected_cumulative_checkpoint_tags": [
            f"cumulative_{idx * 100:04d}k_after_chunk08_pass"
            for idx in range(1, 11)
        ],
        "observed_cumulative_checkpoint_tags": [
            f"cumulative_{idx * 100:04d}k_after_chunk08_pass"
            for idx in range(1, 11)
        ],
        "missing_expected_cumulative_checkpoint_tags": [],
        "unexpected_cumulative_checkpoint_tags": [],
        "duplicate_cumulative_checkpoint_tags": [],
        "global_training_evaluation_proof": {
            "status": "PASS",
            "expected_total": 1000000,
            "expected_chunks": 10,
            "expected_per_chunk": 100000,
            "formal_pass_chunk_count": 10,
            "training_row_count": 1000000,
            "unique_training_evaluation_count": 1000000,
            "duplicate_evaluation_count": 0,
            "missing_or_bad_training_csv_count": 0,
        },
        "production_rate_artifact": {
            "json_exists": True,
            "json_bytes": 2048,
            "md_exists": True,
            "md_bytes": 1024,
            "audit_mode": "REMOTE_READ_ONLY_AUDIT",
            "return_code": 0,
            "production_rate_audit_status": "PASS",
            "production_rate_target_status": "PASS",
            "latest_parallel_jobs": 48,
            "expected_parallel_jobs": 48,
            "target_seconds_per_accepted_row": 4.0,
            "target_days_per_100k": 5.0,
            "measured_seconds_per_accepted_row": 4.0,
            "eta_days_per_100k": 5.0,
        },
    }


def run_case(case_name: str, mutation: str, expected: str, expected_rate: str, expected_needles: list[str]) -> None:
    case_dir = tmp_root / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    data = base_index()
    rate = data["production_rate_artifact"]
    if mutation == "missing_rate":
        data.pop("production_rate_artifact")
    elif mutation == "missing_expected_formal_tag":
        data["overall_status"] = "IN_PROGRESS"
        data["formal_100k_tag_status"] = "IN_PROGRESS"
        data["formal_100k_dataset_count"] = 9
        data["formal_100k_evidence_pass_count"] = 9
        data["missing_expected_formal_100k_tags"] = ["chunk_001_100k_after_chunk08_pass"]
        data["unexpected_formal_100k_tags"] = ["chunk_011_100k_after_chunk08_pass"]
    elif mutation == "unexpected_formal_tag":
        data["overall_status"] = "IN_PROGRESS"
        data["formal_100k_tag_status"] = "IN_PROGRESS"
        data["all_formal_100k_dataset_count"] = 11
        data["observed_formal_100k_tags"].append("chunk_011_100k_after_chunk08_pass")
        data["unexpected_formal_100k_tags"] = ["chunk_011_100k_after_chunk08_pass"]
    elif mutation == "missing_expected_cumulative_tag":
        data["overall_status"] = "IN_PROGRESS"
        data["cumulative_checkpoint_tag_status"] = "IN_PROGRESS"
        data["cumulative_evidence_count"] = 9
        data["cumulative_evidence_pass_count"] = 9
        data["missing_expected_cumulative_checkpoint_tags"] = ["cumulative_0100k_after_chunk08_pass"]
        data["unexpected_cumulative_checkpoint_tags"] = ["cumulative_1100k_after_chunk08_pass"]
    elif mutation == "unexpected_cumulative_tag":
        data["overall_status"] = "IN_PROGRESS"
        data["cumulative_checkpoint_tag_status"] = "IN_PROGRESS"
        data["all_cumulative_evidence_count"] = 11
        data["observed_cumulative_checkpoint_tags"].append("cumulative_1100k_after_chunk08_pass")
        data["unexpected_cumulative_checkpoint_tags"] = ["cumulative_1100k_after_chunk08_pass"]
    elif mutation == "duplicate_cumulative_summary":
        data["overall_status"] = "IN_PROGRESS"
        data["cumulative_checkpoint_tag_status"] = "IN_PROGRESS"
        data["all_cumulative_evidence_count"] = 11
        data["duplicate_cumulative_checkpoint_tags"] = ["cumulative_0100k_after_chunk08_pass"]
    elif mutation == "missing_global_training_proof":
        data.pop("global_training_evaluation_proof")
    elif mutation == "low_global_unique":
        data["global_training_evaluation_proof"]["status"] = "FAIL"
        data["global_training_evaluation_proof"]["unique_training_evaluation_count"] = 999999
    elif mutation == "global_duplicate":
        data["global_training_evaluation_proof"]["status"] = "FAIL"
        data["global_training_evaluation_proof"]["unique_training_evaluation_count"] = 999999
        data["global_training_evaluation_proof"]["duplicate_evaluation_count"] = 1
    elif mutation == "local_dry_run":
        rate["audit_mode"] = "LOCAL_DRY_RUN"
    elif mutation == "wrong_parallel":
        rate["latest_parallel_jobs"] = 24
    elif mutation == "wrong_expected_parallel":
        rate["expected_parallel_jobs"] = 24
    elif mutation == "slow_seconds":
        rate["measured_seconds_per_accepted_row"] = 4.6
    elif mutation == "slow_eta":
        rate["eta_days_per_100k"] = 5.6
    elif mutation == "target_fail":
        rate["production_rate_audit_status"] = "FAIL"
    elif mutation == "missing_rate_md_flag":
        rate["md_exists"] = False
    elif mutation == "empty_rate_json":
        rate["json_bytes"] = 0
    elif mutation == "empty_rate_md":
        rate["md_bytes"] = 0
    elif mutation == "complete":
        pass
    else:
        raise ValueError(mutation)

    json_path = case_dir / "evidence.json"
    md_path = case_dir / "evidence.md"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text("# evidence\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(audit), str(json_path), str(md_path), "10"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{case_name} audit rc={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    output = completed.stdout
    actual = None
    rate_actual = None
    for line in output.splitlines():
        if line.startswith("evidence_index_audit_result="):
            actual = line.split("=", 1)[1]
        if line.startswith("production_rate_artifact_audit_result="):
            rate_actual = line.split("=", 1)[1]
    if actual != expected:
        raise AssertionError(f"{case_name} expected {expected}, got {actual}\n{output}")
    if rate_actual != expected_rate:
        raise AssertionError(f"{case_name} expected rate {expected_rate}, got {rate_actual}\n{output}")
    missing = [needle for needle in expected_needles if needle not in output]
    if missing:
        raise AssertionError(f"{case_name} missing {missing}\n{output}")
    print(f"FINAL_1M_AUDIT_CONTRACT_GATE_CASE={case_name} status=PASS")


cases = [
    ("rate_behavior_complete", "complete", "PASS", "PASS", ["production_rate_artifact_audit_result=PASS", "global_training_evaluation_status=PASS"]),
    ("formal_tag_behavior_missing_expected", "missing_expected_formal_tag", "FAIL", "PASS", ["formal_100k_tag_status='IN_PROGRESS'", "missing_expected_formal_100k_tags=chunk_001_100k_after_chunk08_pass"]),
    ("formal_tag_behavior_unexpected_extra", "unexpected_formal_tag", "FAIL", "PASS", ["unexpected_formal_100k_tags=chunk_011_100k_after_chunk08_pass"]),
    ("cumulative_tag_behavior_missing_expected", "missing_expected_cumulative_tag", "FAIL", "PASS", ["cumulative_checkpoint_tag_status='IN_PROGRESS'", "missing_expected_cumulative_checkpoint_tags=cumulative_0100k_after_chunk08_pass"]),
    ("cumulative_tag_behavior_unexpected_extra", "unexpected_cumulative_tag", "FAIL", "PASS", ["unexpected_cumulative_checkpoint_tags=cumulative_1100k_after_chunk08_pass"]),
    ("cumulative_tag_behavior_duplicate_summary", "duplicate_cumulative_summary", "FAIL", "PASS", ["duplicate_cumulative_checkpoint_tags=cumulative_0100k_after_chunk08_pass"]),
    ("global_behavior_missing_proof", "missing_global_training_proof", "FAIL", "PASS", ["global_training_evaluation_proof=MISSING"]),
    ("global_behavior_low_unique", "low_global_unique", "FAIL", "PASS", ["global_training_evaluation_proof.unique_training_evaluation_count=999999"]),
    ("global_behavior_duplicate", "global_duplicate", "FAIL", "PASS", ["global_training_evaluation_proof.duplicate_evaluation_count=1"]),
    ("rate_behavior_missing_rate", "missing_rate", "FAIL", "FAIL", ["production_rate_artifact=MISSING"]),
    ("rate_behavior_local_dry_run", "local_dry_run", "FAIL", "FAIL", ["production_rate_artifact.audit_mode='LOCAL_DRY_RUN'"]),
    ("rate_behavior_wrong_parallel", "wrong_parallel", "FAIL", "FAIL", ["production_rate_artifact.latest_parallel_jobs=24"]),
    ("rate_behavior_wrong_expected_parallel", "wrong_expected_parallel", "FAIL", "FAIL", ["production_rate_artifact.expected_parallel_jobs=24"]),
    ("rate_behavior_slow_seconds", "slow_seconds", "FAIL", "FAIL", ["production_rate_artifact.measured_seconds_per_accepted_row=4.6"]),
    ("rate_behavior_slow_eta", "slow_eta", "FAIL", "FAIL", ["production_rate_artifact.eta_days_per_100k=5.6"]),
    ("rate_behavior_target_fail", "target_fail", "FAIL", "FAIL", ["production_rate_artifact.production_rate_audit_status='FAIL'"]),
    ("rate_behavior_missing_rate_md_flag", "missing_rate_md_flag", "FAIL", "FAIL", ["production_rate_artifact.md_exists=False"]),
    ("rate_behavior_empty_rate_json", "empty_rate_json", "FAIL", "FAIL", ["production_rate_artifact.json_bytes=0"]),
    ("rate_behavior_empty_rate_md", "empty_rate_md", "FAIL", "FAIL", ["production_rate_artifact.md_bytes=0"]),
]

for case in cases:
    run_case(*case)

checkpoint_cases = [
    ("checkpoint_proof_complete", "complete", "PASS", []),
    ("checkpoint_proof_missing_one_d_uniformity", "missing_one_d_uniformity", "FAIL", ["uniformity.one_dimensional_uniformity=MISSING"]),
    ("checkpoint_proof_low_one_d_entropy", "low_one_d_entropy", "FAIL", ["uniformity.one_dimensional_uniformity.q.normalized_entropy=0.85"]),
    ("checkpoint_proof_high_one_d_imbalance", "high_one_d_imbalance", "FAIL", ["uniformity.one_dimensional_uniformity.k.max_to_min_nonzero_ratio=3"]),
    ("checkpoint_proof_low_pair_occupancy", "low_pair_occupancy", "FAIL", ["uniformity.pairwise_uniformity.lp_k.occupied_fraction=0.6"]),
    ("checkpoint_proof_low_pair_entropy", "low_pair_entropy", "FAIL", ["uniformity.pairwise_uniformity.ls_q.normalized_entropy=0.75"]),
    ("checkpoint_proof_low_four_d_occupancy", "low_four_d_occupancy", "FAIL", ["uniformity.four_dimensional_uniformity.occupied_fraction=0.25"]),
    ("checkpoint_proof_low_four_d_entropy", "low_four_d_entropy", "FAIL", ["uniformity.four_dimensional_uniformity.normalized_entropy=0.79"]),
    ("checkpoint_proof_high_four_d_imbalance", "high_four_d_imbalance", "FAIL", ["uniformity.four_dimensional_uniformity.max_to_min_nonzero_ratio=5"]),
    ("checkpoint_proof_missing_model_metrics", "missing_model_metrics", "FAIL", ["model.metrics=MISSING"]),
    ("checkpoint_proof_zero_model_test_rows", "zero_model_test_rows", "FAIL", ["model.test_row_count=0"]),
]
for case in checkpoint_cases:
    run_checkpoint_case(*case)


def base_accepted_campaign_summary() -> dict:
    records = [
        {
            "checkpoint_index": index,
            "manifest_physical_cell_tail_error": "PASS",
        }
        for index in range(1, 11)
    ]
    records[1].update(
        {
            "manifest_balanced_mse_bni_status": "PASS",
            "manifest_balanced_mse_bni_decision_rule": (
                "row_equal_cell_and_p90_tail_cluster_bootstrap_ci_lower_ge_material_improvement"
            ),
            "manifest_balanced_mse_bni_row_ci_lower": -0.04,
            "manifest_balanced_mse_bni_equal_cell_ci_lower": -0.03,
            "manifest_balanced_mse_bni_p90_tail_ci_lower": -0.08,
            "balanced_mse_bni_artifact_exists_flag": True,
            "balanced_mse_bni_artifact_exists": True,
            "balanced_mse_bni_artifact_sha256_recorded": "a" * 64,
            "balanced_mse_bni_artifact_sha256_matches": True,
            "balanced_mse_bni_artifact_status": "PASS",
            "balanced_mse_bni_artifact_decision_rule": (
                "row_equal_cell_and_p90_tail_cluster_bootstrap_ci_lower_ge_material_improvement"
            ),
            "balanced_mse_bni_artifact_bootstrap_status": "PASS",
        }
    )
    records[5].update(
        {
            "manifest_mondrian_conformal": "PASS",
            "manifest_mondrian_conformal_decision": (
                "RETAIN_GLOBAL_INTERVALS_AND_REPORT_GROUP_DIAGNOSTICS"
            ),
            "manifest_mondrian_conformal_recommendation": (
                "RETAIN_GLOBAL_INTERVALS_AND_REPORT_GROUP_DIAGNOSTICS"
            ),
            "manifest_mondrian_supported_cell_fraction": 0.9,
            "manifest_mondrian_supported_row_fraction": 0.95,
            "mondrian_conformal_artifact_exists_flag": True,
            "mondrian_conformal_artifact_exists": True,
            "mondrian_conformal_artifact_sha256_recorded": "b" * 64,
            "mondrian_conformal_artifact_sha256_matches": True,
            "mondrian_conformal_artifact_status": "PASS",
            "mondrian_conformal_artifact_decision": (
                "RETAIN_GLOBAL_INTERVALS_AND_REPORT_GROUP_DIAGNOSTICS"
            ),
            "mondrian_conformal_artifact_checks_all_pass": True,
            "mondrian_conformal_artifact_supported_cell_fraction": 0.9,
            "mondrian_conformal_artifact_supported_row_fraction": 0.95,
        }
    )
    return {
        "overall_status": "PASS",
        "decision": "ACCEPTED_1M_REAL_EMX_CAMPAIGN_COMPLETE",
        "checks": {
            "accepted_count_at_least_expected": True,
            "independent_geometry_unique": True,
            "checkpoint_contract_pass": True,
            "learning_curve_has_ten_comparable_checkpoints": True,
            "fixed_common_test_panel_contract_pass": True,
            "final_uniformity_contract_pass": True,
            "final_model_manifest_pass": True,
        },
        "checkpoint_audit": {
            "overall_status": "PASS",
            "records": records,
        },
    }


def run_accepted_campaign_case(case_name: str, variant: str, expected: str, needle: str = "") -> None:
    case_dir = tmp_root / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    summary_path = case_dir / "accepted_1m_campaign_completion_audit_summary.json"
    audit_marker = case_dir / "accepted_1m_campaign_completion.pass"
    campaign_marker = case_dir / "accepted_1m_campaign.complete"
    data = base_accepted_campaign_summary()
    audit_marker.touch()
    campaign_marker.touch()
    if variant == "tail_fail":
        data["checkpoint_audit"]["records"][3]["manifest_physical_cell_tail_error"] = "FAIL"
    elif variant == "bni_status_missing":
        data["checkpoint_audit"]["records"][1].pop("manifest_balanced_mse_bni_status")
    elif variant == "bni_nonfinite_ci":
        data["checkpoint_audit"]["records"][1]["manifest_balanced_mse_bni_p90_tail_ci_lower"] = None
    elif variant == "bni_bad_artifact_sha":
        data["checkpoint_audit"]["records"][1]["balanced_mse_bni_artifact_sha256_matches"] = False
    elif variant == "mondrian_status_missing":
        data["checkpoint_audit"]["records"][5].pop("manifest_mondrian_conformal")
    elif variant == "mondrian_low_support":
        data["checkpoint_audit"]["records"][5]["manifest_mondrian_supported_cell_fraction"] = 0.79
    elif variant == "mondrian_bad_artifact_sha":
        data["checkpoint_audit"]["records"][5]["mondrian_conformal_artifact_sha256_matches"] = False
    elif variant == "common_panel_fail":
        data["checks"]["fixed_common_test_panel_contract_pass"] = False
    elif variant == "record_count_nine":
        data["checkpoint_audit"]["records"].pop()
    elif variant == "summary_fail":
        data["overall_status"] = "FAIL"
        data["decision"] = "DO_NOT_CLAIM_ONE_MILLION_COMPLETE"
    elif variant == "missing_audit_marker":
        audit_marker.unlink()
    elif variant == "complete":
        pass
    else:
        raise ValueError(variant)
    summary_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(accepted_campaign_proof),
            str(summary_path),
            str(audit_marker),
            str(campaign_marker),
            "10",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{case_name} accepted proof rc={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    output = completed.stdout.strip()
    if expected == "PASS":
        if output != "PASS":
            raise AssertionError(f"{case_name} expected PASS, got {output!r}")
    elif not output.startswith("FAIL:"):
        raise AssertionError(f"{case_name} expected FAIL, got {output!r}")
    if needle and needle not in output:
        raise AssertionError(f"{case_name} missing {needle!r}\n{output}")
    print(f"FINAL_1M_AUDIT_CONTRACT_GATE_CASE={case_name} status=PASS")


accepted_campaign_cases = [
    ("accepted_campaign_complete", "complete", "PASS", ""),
    (
        "accepted_campaign_tail_fail",
        "tail_fail",
        "FAIL",
        "accepted_checkpoint_physical_cell_tail_not_pass=4",
    ),
    (
        "accepted_campaign_record_count_nine",
        "record_count_nine",
        "FAIL",
        "accepted_checkpoint_record_count=9,expected=10",
    ),
    (
        "accepted_campaign_bni_status_missing",
        "bni_status_missing",
        "FAIL",
        "accepted_checkpoint_200k_bni_status=None",
    ),
    (
        "accepted_campaign_bni_nonfinite_ci",
        "bni_nonfinite_ci",
        "FAIL",
        "accepted_checkpoint_200k_bni_nonfinite.manifest_balanced_mse_bni_p90_tail_ci_lower=None",
    ),
    (
        "accepted_campaign_bni_bad_artifact_sha",
        "bni_bad_artifact_sha",
        "FAIL",
        "accepted_checkpoint_200k_bni_artifact.balanced_mse_bni_artifact_sha256_matches=False",
    ),
    (
        "accepted_campaign_mondrian_status_missing",
        "mondrian_status_missing",
        "FAIL",
        "accepted_checkpoint_600k_mondrian_status=None",
    ),
    (
        "accepted_campaign_mondrian_low_support",
        "mondrian_low_support",
        "FAIL",
        "accepted_checkpoint_600k_mondrian_support.manifest_mondrian_supported_cell_fraction=0.79",
    ),
    (
        "accepted_campaign_mondrian_bad_artifact_sha",
        "mondrian_bad_artifact_sha",
        "FAIL",
        "accepted_checkpoint_600k_mondrian_artifact.mondrian_conformal_artifact_sha256_matches=False",
    ),
    (
        "accepted_campaign_common_panel_fail",
        "common_panel_fail",
        "FAIL",
        "accepted_final_audit_check.fixed_common_test_panel_contract_pass=False",
    ),
    (
        "accepted_campaign_summary_fail",
        "summary_fail",
        "FAIL",
        "accepted_final_audit_overall_status='FAIL'",
    ),
    (
        "accepted_campaign_missing_audit_marker",
        "missing_audit_marker",
        "FAIL",
        "accepted_final_audit_marker_missing",
    ),
]
for case in accepted_campaign_cases:
    run_accepted_campaign_case(*case)

print("FINAL_1M_AUDIT_CONTRACT_GATE_STATUS=PASS")
PY
