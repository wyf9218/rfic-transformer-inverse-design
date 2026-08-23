#!/usr/bin/env bash
set -euo pipefail

# Behavior test for checkpoint-runner reuse decisions.
#
# The production runners skip already-tested 100k/cumulative checkpoints only
# when their embedded checkpoint_proof() returns PASS. This local test extracts
# the exact embedded Python proof block from each runner and verifies that an
# older checkpoint missing the Lp/Ls/Q/|K| 1D, pairwise, or 4D uniformity
# gates cannot be reused as a passing checkpoint.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
SCRIPTS=(
  "RUN_MARS56_FIRST100K_MODEL_TEST_AFTER_DUO_20260707.sh"
  "RUN_MARS56_100K_MODEL_TEST_FOR_DATASET_AFTER_DUO_20260707.sh"
  "RUN_MARS56_AUTO_CHECKPOINT_COMPLETED_100K_CHUNKS_AFTER_DUO_20260707.sh"
  "RUN_MARS56_CUMULATIVE_100K_CHECKPOINTS_AFTER_DUO_20260707.sh"
)

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_runner_4d_reuse.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

python3 - "$ROOT_DIR" "$TMP_ROOT" "${SCRIPTS[@]}" <<'PY'
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
tmp_root = Path(sys.argv[2])
scripts = [root / item for item in sys.argv[3:]]
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


def extract_proof_python(script: Path, out: Path) -> None:
    lines = script.read_text(encoding="utf-8").splitlines()
    start = None
    for index, line in enumerate(lines):
        if '"$PY" - "$path" "$expected" <<' in line:
            start = index + 1
            break
    if start is None:
        raise AssertionError(f"{script.name}: checkpoint_proof heredoc not found")
    end = None
    for index in range(start, len(lines)):
        if lines[index] == "PY":
            end = index
            break
    if end is None:
        raise AssertionError(f"{script.name}: checkpoint_proof heredoc end not found")
    out.write_text("\n".join(lines[start:end]) + "\n", encoding="utf-8")


def base_summary() -> dict:
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


def mutated_summary(variant: str) -> dict:
    data = copy.deepcopy(base_summary())
    if variant == "complete":
        return data
    if variant == "missing_four_d_gate":
        data["physical_uniformity_gate"]["require_four_d_gate"] = False
        return data
    if variant == "low_four_d_occupancy":
        data["details"]["uniformity"]["four_dimensional_uniformity"]["occupied_fraction"] = 0.25
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
    if variant == "missing_four_d_summary":
        data["details"]["uniformity"].pop("four_dimensional_uniformity")
        return data
    if variant == "missing_model_metrics":
        data["details"]["model"].pop("metrics")
        return data
    if variant == "zero_model_test_rows":
        data["details"]["model"]["test_row_count"] = 0
        data["details"]["model"]["metrics"]["test_count"] = 0
        return data
    if variant == "low_training_unique_evaluations":
        data["details"]["traceability"]["training_unique_evaluations"] = expected - 1
        return data
    raise ValueError(variant)


def run_case(script: Path, proof_py: Path, variant: str, expected_status: str, needles: list[str]) -> None:
    case_dir = tmp_root / script.stem / variant
    case_dir.mkdir(parents=True, exist_ok=True)
    summary_path = case_dir / "summary.json"
    summary_path.write_text(json.dumps(mutated_summary(variant), indent=2) + "\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(proof_py), str(summary_path), str(expected)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{script.name} {variant} rc={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    output = completed.stdout.strip()
    if expected_status == "PASS":
        if output != "PASS":
            raise AssertionError(f"{script.name} {variant} expected PASS, got {output!r}")
    elif not output.startswith("FAIL:"):
        raise AssertionError(f"{script.name} {variant} expected FAIL, got {output!r}")
    missing = [needle for needle in needles if needle not in output]
    if missing:
        raise AssertionError(f"{script.name} {variant} missing {missing} in {output!r}")
    print(f"CHECKPOINT_RUNNER_4D_REUSE_CASE={script.name}:{variant} status=PASS")


for script in scripts:
    if not script.exists():
        raise AssertionError(f"missing script: {script}")
    proof_py = tmp_root / f"{script.stem}_checkpoint_proof.py"
    extract_proof_python(script, proof_py)
    run_case(script, proof_py, "complete", "PASS", [])
    run_case(script, proof_py, "missing_four_d_gate", "FAIL", ["physical_uniformity_gate.require_four_d_gate=False"])
    run_case(script, proof_py, "low_four_d_occupancy", "FAIL", ["uniformity.four_dimensional_uniformity.occupied_fraction=0.25"])
    run_case(script, proof_py, "missing_one_d_uniformity", "FAIL", ["uniformity.one_dimensional_uniformity=MISSING"])
    run_case(script, proof_py, "low_one_d_entropy", "FAIL", ["uniformity.one_dimensional_uniformity.q.normalized_entropy=0.85"])
    run_case(script, proof_py, "high_one_d_imbalance", "FAIL", ["uniformity.one_dimensional_uniformity.k.max_to_min_nonzero_ratio=3"])
    run_case(script, proof_py, "low_pair_occupancy", "FAIL", ["uniformity.pairwise_uniformity.lp_k.occupied_fraction=0.6"])
    run_case(script, proof_py, "low_pair_entropy", "FAIL", ["uniformity.pairwise_uniformity.ls_q.normalized_entropy=0.75"])
    run_case(script, proof_py, "missing_model_metrics", "FAIL", ["model.metrics=MISSING"])
    run_case(script, proof_py, "zero_model_test_rows", "FAIL", ["model.test_row_count=0"])
    run_case(script, proof_py, "low_training_unique_evaluations", "FAIL", ["traceability.training_unique_evaluations=2"])
    run_case(script, proof_py, "missing_four_d_summary", "FAIL", ["uniformity.four_dimensional_uniformity=MISSING"])

print("CHECKPOINT_RUNNER_4D_REUSE_BEHAVIOR_STATUS=PASS")
PY
