#!/usr/bin/env bash
set -euo pipefail

# Local-only guardrail for the MARS56 1M campaign.
#
# It checks that every 20260707 entrypoint that can classify a 100k/model
# checkpoint uses the same strict proof ingredients and behavior:
#   - overall_status=PASS
#   - expected_count matches the requested row count
#   - min_valid matches the requested row count
#   - stable_index / response_features / enrichment / uniformity / uniformity_manifest / training / model / traceability all PASS
#   - uniformity_manifest proves required Lp/Ls/Q/|K| visual artifacts exist
#   - uniformity details prove |K| is the uniformity axis and signed-K diagnostics exist
#   - traceability row-count evidence exists and is not below the expected row count
#
# This script does not connect to MARS and does not modify files.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"

python3 - "$ROOT_DIR" <<'PY'
from pathlib import Path
import json
import shlex
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])

proof_scripts = [
    "CHECK_MARS56_1M_GOAL_COMPLETION_AFTER_DUO_20260707.sh",
    "CHECK_MARS56_MILLION_CAMPAIGN_STATUS_20260707.sh",
    "RUN_MARS56_AUTO_CHECKPOINT_COMPLETED_100K_CHUNKS_AFTER_DUO_20260707.sh",
    "RUN_MARS56_CUMULATIVE_100K_CHECKPOINTS_AFTER_DUO_20260707.sh",
    "RUN_MARS56_FIRST100K_MODEL_TEST_AFTER_DUO_20260707.sh",
    "RUN_MARS56_100K_MODEL_TEST_FOR_DATASET_AFTER_DUO_20260707.sh",
]

required_tokens = [
    "checkpoint_proof()",
    "overall_status",
    "expected_count",
    "min_valid",
    '"stable_index"',
    '"response_features"',
    '"enrichment"',
    '"uniformity"',
    '"uniformity_manifest"',
    '"training"',
    '"model"',
    '"traceability"',
    '"valid_feature_count"',
    '"training_count"',
    '"usable_row_count"',
    '"stable_manifest_rows"',
    '"response_feature_rows"',
    '"enriched_rows"',
    "traceability.details_missing",
    "traceability.{key}=MISSING",
    '"visual_artifact_count"',
    '"require_plots"',
    "uniformity_manifest.visual_artifact_count",
    "uniformity_manifest.require_plots",
    "uniformity.k_mode",
    "uniformity.k_sign_diagnostics",
    "uniformity.k_sign_diagnostics.uniformity_k_axis",
    "uniformity.k_sign_diagnostics.signed_k_count",
    'print("PASS" if not reasons else "FAIL:"',
]

usage_checks = {
    "CHECK_MARS56_1M_GOAL_COMPLETION_AFTER_DUO_20260707.sh": [
        "cp_proof=$(checkpoint_proof",
        "proof=$(checkpoint_proof",
        "ONE_MILLION_GOAL_STATUS=PASS",
    ],
    "CHECK_MARS56_MILLION_CAMPAIGN_STATUS_20260707.sh": [
        "cp_proof=$(checkpoint_proof",
        'checkpoint_proof=%s',
        'prod_state="CHECKPOINT_PASS"',
    ],
    "RUN_MARS56_AUTO_CHECKPOINT_COMPLETED_100K_CHUNKS_AFTER_DUO_20260707.sh": [
        "existing_proof=$(checkpoint_proof",
        "new_proof=$(checkpoint_proof",
        "SKIP_ALREADY_CHECKPOINTED_PASS",
    ],
    "RUN_MARS56_CUMULATIVE_100K_CHECKPOINTS_AFTER_DUO_20260707.sh": [
        "cp_proof=$(checkpoint_proof",
        "existing_proof=$(checkpoint_proof",
        "CUM_SOURCE",
    ],
    "RUN_MARS56_FIRST100K_MODEL_TEST_AFTER_DUO_20260707.sh": [
        "proof=$(checkpoint_proof",
        "STATUS=FIRST100K_CHECKPOINT_PROOF_PASS",
        "STATUS=FIRST100K_CHECKPOINT_PROOF_FAIL",
    ],
    "RUN_MARS56_100K_MODEL_TEST_FOR_DATASET_AFTER_DUO_20260707.sh": [
        "proof=$(checkpoint_proof",
        "STATUS=100K_CHECKPOINT_PROOF_PASS",
        "STATUS=100K_CHECKPOINT_PROOF_FAIL",
    ],
}

failures = []

def extract_checkpoint_proof(text):
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == "checkpoint_proof() {":
            start = index
            break
    if start is None:
        return None
    block = []
    in_heredoc = False
    for line in lines[start:]:
        block.append(line)
        stripped = line.strip()
        if not in_heredoc and "<<'PY'" in line:
            in_heredoc = True
            continue
        if in_heredoc and stripped == "PY":
            in_heredoc = False
            continue
        if not in_heredoc and stripped == "}" and len(block) > 1:
            return "\n".join(block) + "\n"
    return None

def valid_summary():
    statuses = {
        "stable_index": "PASS",
        "response_features": "PASS",
        "enrichment": "PASS",
        "uniformity": "PASS",
        "uniformity_manifest": "PASS",
        "training": "PASS",
        "model": "PASS",
        "traceability": "PASS",
    }
    return {
        "overall_status": "PASS",
        "expected_count": 100000,
        "min_valid": 100000,
        "statuses": statuses,
        "details": {
            "uniformity": {
                "valid_feature_count": 100000,
                "k_mode": "magnitude",
                "k_sign_diagnostics": {
                    "uniformity_k_axis": "|K|",
                    "signed_k_count": 100000,
                    "positive_k_count": 60000,
                    "zero_k_count": 0,
                    "negative_k_count": 40000,
                },
            },
            "uniformity_manifest": {"visual_artifact_count": 3, "require_plots": True},
            "training": {"training_count": 100000},
            "model": {"usable_row_count": 100000},
            "traceability": {
                "stable_manifest_rows": 100000,
                "response_feature_rows": 100000,
                "enriched_rows": 100000,
                "training_rows": 100000,
            },
        },
    }

def proof_behavior_failures(rel, function_text):
    cases = []
    good = valid_summary()
    cases.append(("valid_full_pass", good, "PASS"))

    weak_missing_expected = valid_summary()
    del weak_missing_expected["expected_count"]
    cases.append(("weak_missing_expected_count", weak_missing_expected, "FAIL:"))

    wrong_min_valid = valid_summary()
    wrong_min_valid["min_valid"] = 99999
    cases.append(("wrong_min_valid", wrong_min_valid, "FAIL:"))

    bad_step = valid_summary()
    bad_step["statuses"]["uniformity"] = "FAIL"
    cases.append(("bad_uniformity_step", bad_step, "FAIL:"))

    low_detail = valid_summary()
    low_detail["details"]["model"]["usable_row_count"] = 99999
    cases.append(("low_model_usable_row_count", low_detail, "FAIL:"))

    missing_manifest = valid_summary()
    del missing_manifest["statuses"]["uniformity_manifest"]
    cases.append(("missing_uniformity_manifest_step", missing_manifest, "FAIL:"))

    missing_traceability = valid_summary()
    del missing_traceability["statuses"]["traceability"]
    cases.append(("missing_traceability_step", missing_traceability, "FAIL:"))

    low_traceability = valid_summary()
    low_traceability["details"]["traceability"]["training_rows"] = 99999
    cases.append(("low_traceability_training_rows", low_traceability, "FAIL:"))

    missing_traceability_details = valid_summary()
    del missing_traceability_details["details"]["traceability"]
    cases.append(("missing_traceability_details", missing_traceability_details, "FAIL:"))

    missing_traceability_row_field = valid_summary()
    del missing_traceability_row_field["details"]["traceability"]["response_feature_rows"]
    cases.append(("missing_traceability_row_field", missing_traceability_row_field, "FAIL:"))

    low_visual_count = valid_summary()
    low_visual_count["details"]["uniformity_manifest"]["visual_artifact_count"] = 2
    cases.append(("low_uniformity_visual_artifact_count", low_visual_count, "FAIL:"))

    plots_not_required = valid_summary()
    plots_not_required["details"]["uniformity_manifest"]["require_plots"] = False
    cases.append(("uniformity_manifest_plots_not_required", plots_not_required, "FAIL:"))

    missing_k_diag = valid_summary()
    del missing_k_diag["details"]["uniformity"]["k_sign_diagnostics"]
    cases.append(("missing_k_sign_diagnostics", missing_k_diag, "FAIL:"))

    wrong_k_axis = valid_summary()
    wrong_k_axis["details"]["uniformity"]["k_sign_diagnostics"]["uniformity_k_axis"] = "signed K"
    cases.append(("wrong_k_uniformity_axis", wrong_k_axis, "FAIL:"))

    low_signed_k_count = valid_summary()
    low_signed_k_count["details"]["uniformity"]["k_sign_diagnostics"]["signed_k_count"] = 99999
    cases.append(("low_signed_k_count", low_signed_k_count, "FAIL:"))

    result_failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for case_name, data, expected_prefix in cases:
            summary = tmpdir / f"{case_name}.json"
            summary.write_text(json.dumps(data), encoding="utf-8")
            shell = (
                "set -euo pipefail\n"
                f"PY={shlex.quote(sys.executable)}\n"
                f"{function_text}\n"
                f"checkpoint_proof {shlex.quote(str(summary))} 100000\n"
            )
            completed = subprocess.run(
                ["bash", "-c", shell],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            output = completed.stdout.strip()
            if completed.returncode != 0:
                result_failures.append(
                    f"{case_name}: rc={completed.returncode} stderr={completed.stderr.strip()!r}"
                )
            elif expected_prefix == "PASS" and output != "PASS":
                result_failures.append(f"{case_name}: expected PASS got {output!r}")
            elif expected_prefix != "PASS" and not output.startswith(expected_prefix):
                result_failures.append(f"{case_name}: expected {expected_prefix!r} prefix got {output!r}")
    return result_failures

for rel in proof_scripts:
    path = root / rel
    if not path.exists():
        failures.append(f"{rel}: missing file")
        continue
    text = path.read_text(errors="replace")
    function_text = extract_checkpoint_proof(text)
    if function_text is None:
        failures.append(f"{rel}: could not extract checkpoint_proof function")
        continue
    missing = [token for token in required_tokens if token not in text]
    missing_usage = [token for token in usage_checks.get(rel, []) if token not in text]
    behavior_failures = proof_behavior_failures(rel, function_text)
    if missing or missing_usage or behavior_failures:
        details = []
        if missing:
            details.append("missing proof tokens: " + ", ".join(missing))
        if missing_usage:
            details.append("missing usage tokens: " + ", ".join(missing_usage))
        if behavior_failures:
            details.append("behavior failures: " + " ; ".join(behavior_failures))
        failures.append(f"{rel}: " + " | ".join(details))
    else:
        print(f"CHECKPOINT_PROOF_CONSISTENCY file={rel} status=PASS behavior=PASS")

if failures:
    print("CHECKPOINT_PROOF_CONSISTENCY_STATUS=FAIL")
    for failure in failures:
        print("FAIL:", failure)
    raise SystemExit(1)

print("CHECKPOINT_PROOF_CONSISTENCY_STATUS=PASS")
PY
