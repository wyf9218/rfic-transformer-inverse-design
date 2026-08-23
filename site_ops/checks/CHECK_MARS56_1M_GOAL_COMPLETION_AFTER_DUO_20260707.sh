#!/usr/bin/env bash
set -euo pipefail

# Read-only final-goal audit after Duo is available.
#
# This script answers whether the current MARS campaign has actually satisfied:
#   - 1,000,000 formal production .s4p rows,
#   - 10 formal 100k chunks,
#   - every 100k chunk has dataset summary PASS and physical/model checkpoint PASS,
#   - cumulative 100k, 200k, ..., 1000k checkpoints all PASS.
# A checkpoint PASS also requires the Lp/Ls/Q/|K| visual artifact manifest and
# at least three distribution plots from the uniformity audit. Lp/Ls/Q/|K|
# uniformity is not accepted unless the 1D marginal, pairwise, and 4D
# occupied-bin gates reach the configured thresholds.
# The final PASS also requires the report-facing 100k evidence index JSON and
# Markdown to exist and to prove every formal and cumulative checkpoint, plus
# the remote production-rate artifact proving the 48-parallel, ~4 s/row, ~5
# days/100k operating point.
#
# It does not write persistent remote files and does not start EMX or model
# tests. It may use a temporary directory for contract/evidence auditing.

JUMP_HOST="login.example.edu"
MARS_HOST="mars.example.edu"
USER_NAME="researcher"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-}"
SSH_PERSIST="${SSH_PERSIST:-20m}"

if [[ "$SSH_CONTROL_PATH" == *$'\n'* || "$SSH_CONTROL_PATH" == *$'\r'* ]]; then
  echo "ERROR: SSH_CONTROL_PATH contains unsupported newline characters." >&2
  exit 2
fi

SSH_ARGS=(-tt)
if [ -n "$SSH_CONTROL_PATH" ]; then
  SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
fi

read -r -d '' REMOTE_AUDIT <<'REMOTE' || true
set -euo pipefail

BASE=/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256
PROJECT=/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705
PY=/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python
CONTRACT_BUILD=$PROJECT/scripts/build_mars56_1m_production_plan_contract.py
CONTRACT_AUDIT=$PROJECT/scripts/audit_mars56_1m_production_plan_contract.py
ACCEPTED_CAMPAIGN_ROOT=$BASE/status/accepted_1m_campaign_controller_20260710
ACCEPTED_FINAL_AUDIT_SUMMARY=$ACCEPTED_CAMPAIGN_ROOT/final_completion_audit/accepted_1m_campaign_completion_audit_summary.json
ACCEPTED_FINAL_AUDIT_MARKER=$ACCEPTED_CAMPAIGN_ROOT/final_completion_audit/accepted_1m_campaign_completion.pass
ACCEPTED_CAMPAIGN_COMPLETE_MARKER=$ACCEPTED_CAMPAIGN_ROOT/accepted_1m_campaign.complete
EXPECTED_CHUNKS=10
EXPECTED_PER_CHUNK=100000
EXPECTED_TOTAL=1000000

printf 'remote_time='; date '+%Y-%m-%d %H:%M:%S %Z'
printf 'base=%s\n' "$BASE"
printf 'expected_chunks=%s\n' "$EXPECTED_CHUNKS"
printf 'expected_per_chunk=%s\n' "$EXPECTED_PER_CHUNK"
printf 'expected_total=%s\n' "$EXPECTED_TOTAL"

json_status() {
  local path="$1"
  if [ ! -f "$path" ]; then
    echo "MISSING"
    return
  fi
  "$PY" - "$path" <<'PY'
import json, sys
try:
    print(str(json.load(open(sys.argv[1])).get("overall_status", "")))
except Exception:
    print("PARSE_ERROR")
PY
}

checkpoint_proof() {
  local path="$1"
  local expected="$2"
  if [ ! -f "$path" ]; then
    echo "MISSING"
    return
  fi
  "$PY" - "$path" "$expected" <<'PY'
import json, math, sys
path = sys.argv[1]
expected = int(sys.argv[2])
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
expected_physical_ranges = {
    "lp": (0.5, 3.0),
    "ls": (0.5, 3.0),
    "q": (5.0, 25.0),
    "k": (0.0, 0.8),
}
expected_min_four_d_occupied_fraction = 0.50
expected_uniformity_thresholds = {
    "min_1d_occupied_fraction": 0.90,
    "min_1d_entropy_fraction": 0.90,
    "max_1d_bin_imbalance": 2.50,
    "min_pair_occupied_fraction": 0.65,
    "min_pair_entropy_fraction": 0.80,
    "min_four_d_normalized_entropy": 0.80,
    "max_four_d_nonzero_bin_imbalance": 4.0,
}
try:
    data = json.load(open(path))
except Exception as exc:
    print(f"PARSE_ERROR:{type(exc).__name__}")
    raise SystemExit(0)
reasons = []
if data.get("overall_status") != "PASS":
    reasons.append(f"overall_status={data.get('overall_status')!r}")
try:
    actual_expected = int(data.get("expected_count"))
except Exception:
    actual_expected = None
if actual_expected != expected:
    reasons.append(f"expected_count={actual_expected!r}")
try:
    actual_min_valid = int(data.get("min_valid"))
except Exception:
    actual_min_valid = None
if actual_min_valid != expected:
    reasons.append(f"min_valid={actual_min_valid!r}")
statuses = data.get("statuses") if isinstance(data.get("statuses"), dict) else {}
missing_steps = sorted(required_steps.difference(statuses))
bad_steps = {k: statuses.get(k) for k in sorted(required_steps.intersection(statuses)) if statuses.get(k) != "PASS"}
if missing_steps:
    reasons.append("missing_steps=" + ",".join(missing_steps))
if bad_steps:
    reasons.append("bad_steps=" + ",".join(f"{k}:{v}" for k, v in bad_steps.items()))
details = data.get("details") if isinstance(data.get("details"), dict) else {}
physical_uniformity_gate = data.get("physical_uniformity_gate") if isinstance(data.get("physical_uniformity_gate"), dict) else {}
if physical_uniformity_gate.get("require_four_d_gate") is not True:
    reasons.append(f"physical_uniformity_gate.require_four_d_gate={physical_uniformity_gate.get('require_four_d_gate')!r}")
for threshold_name, expected_value in expected_uniformity_thresholds.items():
    try:
        actual_value = float(physical_uniformity_gate.get(threshold_name))
    except Exception:
        reasons.append(f"physical_uniformity_gate.{threshold_name}={physical_uniformity_gate.get(threshold_name)!r}")
        continue
    if not math.isclose(actual_value, expected_value, rel_tol=0.0, abs_tol=1e-12):
        reasons.append(f"physical_uniformity_gate.{threshold_name}={actual_value},expected={expected_value}")
try:
    gate_min_four_d = float(physical_uniformity_gate.get("min_four_d_occupied_fraction"))
except Exception:
    reasons.append(f"physical_uniformity_gate.min_four_d_occupied_fraction={physical_uniformity_gate.get('min_four_d_occupied_fraction')!r}")
else:
    if not math.isclose(gate_min_four_d, expected_min_four_d_occupied_fraction, rel_tol=0.0, abs_tol=1e-12):
        reasons.append(
            f"physical_uniformity_gate.min_four_d_occupied_fraction={gate_min_four_d},expected={expected_min_four_d_occupied_fraction}"
        )
for step in ("uniformity", "training", "model"):
    step_details = details.get(step) if isinstance(details.get(step), dict) else {}
    for key in ("valid_feature_count", "training_count", "usable_row_count"):
        if key in step_details:
            try:
                value = int(step_details[key])
            except Exception:
                reasons.append(f"{step}.{key}={step_details[key]!r}")
            else:
                if value < expected:
                    reasons.append(f"{step}.{key}={value}")
model_details = details.get("model") if isinstance(details.get("model"), dict) else {}
try:
    model_test_rows = int(model_details.get("test_row_count"))
except Exception:
    reasons.append(f"model.test_row_count={model_details.get('test_row_count')!r}")
    model_test_rows = None
else:
    if model_test_rows <= 0:
        reasons.append(f"model.test_row_count={model_test_rows}")
model_metrics = model_details.get("metrics") if isinstance(model_details.get("metrics"), dict) else {}
if not model_metrics:
    reasons.append("model.metrics=MISSING")
else:
    try:
        metric_test_count = int(model_metrics.get("test_count"))
    except Exception:
        reasons.append(f"model.metrics.test_count={model_metrics.get('test_count')!r}")
    else:
        if metric_test_count <= 0:
            reasons.append(f"model.metrics.test_count={metric_test_count}")
        if model_test_rows is not None and metric_test_count != model_test_rows:
            reasons.append(f"model.metrics.test_count={metric_test_count},expected={model_test_rows}")
    try:
        geometry_count = int(model_metrics.get("geometry_count"))
    except Exception:
        reasons.append(f"model.metrics.geometry_count={model_metrics.get('geometry_count')!r}")
    else:
        if geometry_count <= 0:
            reasons.append(f"model.metrics.geometry_count={geometry_count}")
    for metric_key in ("max_normalized_mae", "max_normalized_rmse", "mean_normalized_mae", "mean_normalized_rmse"):
        try:
            metric_value = float(model_metrics.get(metric_key))
        except Exception:
            reasons.append(f"model.metrics.{metric_key}={model_metrics.get(metric_key)!r}")
        else:
            if not math.isfinite(metric_value):
                reasons.append(f"model.metrics.{metric_key}={metric_value!r}")
trace_details = details.get("traceability") if isinstance(details.get("traceability"), dict) else {}
if not trace_details:
    reasons.append("traceability.details_missing")
for key in ("stable_manifest_rows", "stable_unique_evaluations", "response_feature_rows", "response_unique_evaluations", "response_dataset_rows", "response_dataset_unique_evaluations", "enriched_rows", "enriched_unique_evaluations", "training_rows", "training_unique_evaluations"):
    if key not in trace_details:
        reasons.append(f"traceability.{key}=MISSING")
        continue
    try:
        value = int(trace_details[key])
    except Exception:
        reasons.append(f"traceability.{key}={trace_details[key]!r}")
    else:
        if value < expected:
            reasons.append(f"traceability.{key}={value}")
manifest_details = details.get("uniformity_manifest") if isinstance(details.get("uniformity_manifest"), dict) else {}
try:
    visual_artifact_count = int(manifest_details.get("visual_artifact_count"))
except Exception:
    reasons.append(f"uniformity_manifest.visual_artifact_count={manifest_details.get('visual_artifact_count')!r}")
else:
    if visual_artifact_count < 3:
        reasons.append(f"uniformity_manifest.visual_artifact_count={visual_artifact_count}")
if manifest_details.get("require_plots") is not True:
    reasons.append(f"uniformity_manifest.require_plots={manifest_details.get('require_plots')!r}")
uniformity_details = details.get("uniformity") if isinstance(details.get("uniformity"), dict) else {}
k_diag = uniformity_details.get("k_sign_diagnostics") if isinstance(uniformity_details.get("k_sign_diagnostics"), dict) else None
if uniformity_details.get("k_mode") != "magnitude":
    reasons.append(f"uniformity.k_mode={uniformity_details.get('k_mode')!r}")
range_details = uniformity_details.get("ranges") if isinstance(uniformity_details.get("ranges"), dict) else {}
if not range_details:
    reasons.append("uniformity.ranges=MISSING")
for feature_name, (target_min, target_max) in expected_physical_ranges.items():
    item = range_details.get(feature_name) if isinstance(range_details.get(feature_name), dict) else {}
    if not item:
        reasons.append(f"uniformity.ranges.{feature_name}=MISSING")
        continue
    if item.get("explicit") is not True or item.get("source") != "explicit":
        reasons.append(f"uniformity.ranges.{feature_name}.explicit={item.get('explicit')!r},source={item.get('source')!r}")
    try:
        actual_min = float(item.get("min"))
        actual_max = float(item.get("max"))
    except Exception:
        reasons.append(f"uniformity.ranges.{feature_name}.bounds={item!r}")
    else:
        if not (
            math.isclose(actual_min, target_min, rel_tol=0.0, abs_tol=1e-12)
            and math.isclose(actual_max, target_max, rel_tol=0.0, abs_tol=1e-12)
        ):
            reasons.append(
                f"uniformity.ranges.{feature_name}=({actual_min},{actual_max}),expected=({target_min},{target_max})"
            )
one_d = uniformity_details.get("one_dimensional_uniformity") if isinstance(uniformity_details.get("one_dimensional_uniformity"), dict) else {}
if not one_d:
    reasons.append("uniformity.one_dimensional_uniformity=MISSING")
for feature_name in expected_physical_ranges:
    item = one_d.get(feature_name) if isinstance(one_d.get(feature_name), dict) else {}
    if not item:
        reasons.append(f"uniformity.one_dimensional_uniformity.{feature_name}=MISSING")
        continue
    for metric_name, threshold_name, comparator in (
        ("occupied_fraction", "min_1d_occupied_fraction", "min"),
        ("normalized_entropy", "min_1d_entropy_fraction", "min"),
        ("max_to_min_nonzero_ratio", "max_1d_bin_imbalance", "max"),
    ):
        try:
            metric_value = float(item.get(metric_name))
        except Exception:
            reasons.append(f"uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}={item.get(metric_name)!r}")
            continue
        threshold_value = expected_uniformity_thresholds[threshold_name]
        if comparator == "min" and (not math.isfinite(metric_value) or metric_value < threshold_value):
            reasons.append(
                f"uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}={metric_value:.6g},required={threshold_value}"
            )
        elif comparator == "max" and (not math.isfinite(metric_value) or metric_value > threshold_value):
            reasons.append(
                f"uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}={metric_value:.6g},limit={threshold_value}"
            )
pairwise = uniformity_details.get("pairwise_uniformity") if isinstance(uniformity_details.get("pairwise_uniformity"), dict) else {}
if not pairwise:
    reasons.append("uniformity.pairwise_uniformity=MISSING")
for pair_name, item in pairwise.items():
    if not isinstance(item, dict):
        reasons.append(f"uniformity.pairwise_uniformity.{pair_name}={item!r}")
        continue
    for metric_name, threshold_name in (
        ("occupied_fraction", "min_pair_occupied_fraction"),
        ("normalized_entropy", "min_pair_entropy_fraction"),
    ):
        try:
            metric_value = float(item.get(metric_name))
        except Exception:
            reasons.append(f"uniformity.pairwise_uniformity.{pair_name}.{metric_name}={item.get(metric_name)!r}")
            continue
        threshold_value = expected_uniformity_thresholds[threshold_name]
        if not math.isfinite(metric_value) or metric_value < threshold_value:
            reasons.append(
                f"uniformity.pairwise_uniformity.{pair_name}.{metric_name}={metric_value:.6g},required={threshold_value}"
            )
four_d = uniformity_details.get("four_dimensional_uniformity") if isinstance(uniformity_details.get("four_dimensional_uniformity"), dict) else {}
if not four_d:
    reasons.append("uniformity.four_dimensional_uniformity=MISSING")
else:
    try:
        occupied_fraction = float(four_d.get("occupied_fraction"))
    except Exception:
        reasons.append(f"uniformity.four_dimensional_uniformity.occupied_fraction={four_d.get('occupied_fraction')!r}")
    else:
        if not math.isfinite(occupied_fraction) or occupied_fraction < expected_min_four_d_occupied_fraction:
            reasons.append(
                f"uniformity.four_dimensional_uniformity.occupied_fraction={occupied_fraction:.6g},required={expected_min_four_d_occupied_fraction}"
            )
    for metric_name, threshold_name, comparator in (
        ("normalized_entropy", "min_four_d_normalized_entropy", "min"),
        ("max_to_min_nonzero_ratio", "max_four_d_nonzero_bin_imbalance", "max"),
    ):
        try:
            metric_value = float(four_d.get(metric_name))
        except Exception:
            reasons.append(
                f"uniformity.four_dimensional_uniformity.{metric_name}={four_d.get(metric_name)!r}"
            )
            continue
        threshold_value = expected_uniformity_thresholds[threshold_name]
        if not math.isfinite(metric_value):
            reasons.append(f"uniformity.four_dimensional_uniformity.{metric_name}={metric_value!r}")
        elif comparator == "min" and metric_value < threshold_value:
            reasons.append(
                f"uniformity.four_dimensional_uniformity.{metric_name}={metric_value:.6g},required={threshold_value}"
            )
        elif comparator == "max" and metric_value > threshold_value:
            reasons.append(
                f"uniformity.four_dimensional_uniformity.{metric_name}={metric_value:.6g},limit={threshold_value}"
            )
if not k_diag:
    reasons.append("uniformity.k_sign_diagnostics=MISSING")
else:
    if k_diag.get("uniformity_k_axis") != "|K|":
        reasons.append(f"uniformity.k_sign_diagnostics.uniformity_k_axis={k_diag.get('uniformity_k_axis')!r}")
    try:
        signed_k_count = int(k_diag.get("signed_k_count"))
    except Exception:
        reasons.append(f"uniformity.k_sign_diagnostics.signed_k_count={k_diag.get('signed_k_count')!r}")
    else:
        if signed_k_count < expected:
            reasons.append(f"uniformity.k_sign_diagnostics.signed_k_count={signed_k_count}")
print("PASS" if not reasons else "FAIL:" + ";".join(reasons))
PY
}

accepted_campaign_completion_proof() {
  local summary_path="$1"
  local audit_marker="$2"
  local campaign_marker="$3"
  local expected_checkpoints="$4"
  "$PY" - "$summary_path" "$audit_marker" "$campaign_marker" "$expected_checkpoints" <<'PY'
import json
import math
import pathlib
import sys

summary_path = pathlib.Path(sys.argv[1])
audit_marker = pathlib.Path(sys.argv[2])
campaign_marker = pathlib.Path(sys.argv[3])
expected = int(sys.argv[4])
reasons = []
if not audit_marker.is_file():
    reasons.append("accepted_final_audit_marker_missing")
if not campaign_marker.is_file():
    reasons.append("accepted_campaign_complete_marker_missing")
data = {}
if not summary_path.is_file():
    reasons.append("accepted_final_audit_summary_missing")
else:
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - audit transcript needs parser detail.
        reasons.append(f"accepted_final_audit_summary_parse_error:{type(exc).__name__}")
if data:
    if data.get("overall_status") != "PASS":
        reasons.append(f"accepted_final_audit_overall_status={data.get('overall_status')!r}")
    if data.get("decision") != "ACCEPTED_1M_REAL_EMX_CAMPAIGN_COMPLETE":
        reasons.append(f"accepted_final_audit_decision={data.get('decision')!r}")
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    for key in (
        "accepted_count_at_least_expected",
        "independent_geometry_unique",
        "checkpoint_contract_pass",
        "learning_curve_has_ten_comparable_checkpoints",
        "fixed_common_test_panel_contract_pass",
        "final_uniformity_contract_pass",
        "final_model_manifest_pass",
    ):
        if checks.get(key) is not True:
            reasons.append(f"accepted_final_audit_check.{key}={checks.get(key)!r}")
    checkpoint_audit = data.get("checkpoint_audit") if isinstance(data.get("checkpoint_audit"), dict) else {}
    if checkpoint_audit.get("overall_status") != "PASS":
        reasons.append(f"accepted_checkpoint_audit_status={checkpoint_audit.get('overall_status')!r}")
    records = checkpoint_audit.get("records") if isinstance(checkpoint_audit.get("records"), list) else []
    if len(records) != expected:
        reasons.append(f"accepted_checkpoint_record_count={len(records)},expected={expected}")
    expected_indices = list(range(1, expected + 1))
    actual_indices = [int(item.get("checkpoint_index") or -1) for item in records if isinstance(item, dict)]
    if actual_indices != expected_indices:
        reasons.append(f"accepted_checkpoint_indices={actual_indices},expected={expected_indices}")
    bad_tail = [
        int(item.get("checkpoint_index") or -1)
        for item in records
        if not isinstance(item, dict) or item.get("manifest_physical_cell_tail_error") != "PASS"
    ]
    if bad_tail:
        reasons.append("accepted_checkpoint_physical_cell_tail_not_pass=" + ",".join(map(str, bad_tail)))
    bni_records = [
        item
        for item in records
        if isinstance(item, dict) and int(item.get("checkpoint_index") or -1) == 2
    ]
    if len(bni_records) != 1:
        reasons.append(f"accepted_checkpoint_200k_bni_record_count={len(bni_records)},expected=1")
    else:
        bni = bni_records[0]
        expected_rule = "row_equal_cell_and_p90_tail_cluster_bootstrap_ci_lower_ge_material_improvement"
        if bni.get("manifest_balanced_mse_bni_status") != "PASS":
            reasons.append(
                f"accepted_checkpoint_200k_bni_status={bni.get('manifest_balanced_mse_bni_status')!r}"
            )
        if bni.get("manifest_balanced_mse_bni_decision_rule") != expected_rule:
            reasons.append(
                "accepted_checkpoint_200k_bni_decision_rule="
                f"{bni.get('manifest_balanced_mse_bni_decision_rule')!r}"
            )
        for key in (
            "manifest_balanced_mse_bni_row_ci_lower",
            "manifest_balanced_mse_bni_equal_cell_ci_lower",
            "manifest_balanced_mse_bni_p90_tail_ci_lower",
        ):
            try:
                value = float(bni.get(key))
            except (TypeError, ValueError):
                value = math.nan
            if not math.isfinite(value):
                reasons.append(f"accepted_checkpoint_200k_bni_nonfinite.{key}={bni.get(key)!r}")
        for key in (
            "balanced_mse_bni_artifact_exists_flag",
            "balanced_mse_bni_artifact_exists",
            "balanced_mse_bni_artifact_sha256_matches",
        ):
            if bni.get(key) is not True:
                reasons.append(f"accepted_checkpoint_200k_bni_artifact.{key}={bni.get(key)!r}")
        if len(str(bni.get("balanced_mse_bni_artifact_sha256_recorded") or "")) != 64:
            reasons.append("accepted_checkpoint_200k_bni_artifact.sha256_not_recorded")
        if bni.get("balanced_mse_bni_artifact_status") != "PASS":
            reasons.append(
                f"accepted_checkpoint_200k_bni_artifact.status={bni.get('balanced_mse_bni_artifact_status')!r}"
            )
        if bni.get("balanced_mse_bni_artifact_decision_rule") != expected_rule:
            reasons.append(
                "accepted_checkpoint_200k_bni_artifact.decision_rule="
                f"{bni.get('balanced_mse_bni_artifact_decision_rule')!r}"
            )
        if bni.get("balanced_mse_bni_artifact_bootstrap_status") != "PASS":
            reasons.append(
                "accepted_checkpoint_200k_bni_artifact.bootstrap_status="
                f"{bni.get('balanced_mse_bni_artifact_bootstrap_status')!r}"
            )
    mondrian_records = [
        item
        for item in records
        if isinstance(item, dict) and int(item.get("checkpoint_index") or -1) == 6
    ]
    if len(mondrian_records) != 1:
        reasons.append(f"accepted_checkpoint_600k_mondrian_record_count={len(mondrian_records)},expected=1")
    else:
        mondrian = mondrian_records[0]
        allowed_decisions = {
            "ADOPT_MONDRIAN_FOR_GROUP_REPORTED_INTERVALS",
            "RETAIN_GLOBAL_INTERVALS_AND_REPORT_GROUP_DIAGNOSTICS",
        }
        if mondrian.get("manifest_mondrian_conformal") != "PASS":
            reasons.append(
                f"accepted_checkpoint_600k_mondrian_status={mondrian.get('manifest_mondrian_conformal')!r}"
            )
        if mondrian.get("manifest_mondrian_conformal_decision") not in allowed_decisions:
            reasons.append(
                "accepted_checkpoint_600k_mondrian_decision="
                f"{mondrian.get('manifest_mondrian_conformal_decision')!r}"
            )
        if mondrian.get("manifest_mondrian_conformal_recommendation") != mondrian.get(
            "manifest_mondrian_conformal_decision"
        ):
            reasons.append("accepted_checkpoint_600k_mondrian_manifest_recommendation_mismatch")
        for key in (
            "manifest_mondrian_supported_cell_fraction",
            "manifest_mondrian_supported_row_fraction",
            "mondrian_conformal_artifact_supported_cell_fraction",
            "mondrian_conformal_artifact_supported_row_fraction",
        ):
            try:
                value = float(mondrian.get(key))
            except (TypeError, ValueError):
                value = math.nan
            if not math.isfinite(value) or value < 0.80:
                reasons.append(f"accepted_checkpoint_600k_mondrian_support.{key}={mondrian.get(key)!r}")
        for key in (
            "mondrian_conformal_artifact_exists_flag",
            "mondrian_conformal_artifact_exists",
            "mondrian_conformal_artifact_sha256_matches",
            "mondrian_conformal_artifact_checks_all_pass",
        ):
            if mondrian.get(key) is not True:
                reasons.append(f"accepted_checkpoint_600k_mondrian_artifact.{key}={mondrian.get(key)!r}")
        if len(str(mondrian.get("mondrian_conformal_artifact_sha256_recorded") or "")) != 64:
            reasons.append("accepted_checkpoint_600k_mondrian_artifact.sha256_not_recorded")
        if mondrian.get("mondrian_conformal_artifact_status") != "PASS":
            reasons.append(
                "accepted_checkpoint_600k_mondrian_artifact.status="
                f"{mondrian.get('mondrian_conformal_artifact_status')!r}"
            )
        if mondrian.get("mondrian_conformal_artifact_decision") not in allowed_decisions:
            reasons.append(
                "accepted_checkpoint_600k_mondrian_artifact.decision="
                f"{mondrian.get('mondrian_conformal_artifact_decision')!r}"
            )
print("PASS" if not reasons else "FAIL:" + ";".join(reasons))
PY
}

nonempty_count() {
  "$PY" - "$1" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
count = 0
if root.exists():
    for path in root.rglob("*.s4p"):
        try:
            if path.is_file() and path.stat().st_size > 0:
                count += 1
        except OSError:
            pass
print(count)
PY
}

latest_per_chunk_summary() {
  local tag="$1"
  find "$BASE/model_tests/$tag" -path '*/mars56_s4p_physical_checkpoint_pipeline_summary.json' -type f 2>/dev/null | sort | tail -n 1 || true
}

latest_cumulative_summary() {
  local tag="$1"
  find "$BASE/cumulative_model_tests/$tag" -path '*/mars56_s4p_physical_checkpoint_pipeline_summary.json' -type f 2>/dev/null | sort | tail -n 1 || true
}

total_nonempty=0
formal_chunk_pass=0
cumulative_pass=0
failures=0

echo "-- accepted real-EMX campaign strict completion audit --"
accepted_campaign_proof=$(accepted_campaign_completion_proof \
  "$ACCEPTED_FINAL_AUDIT_SUMMARY" \
  "$ACCEPTED_FINAL_AUDIT_MARKER" \
  "$ACCEPTED_CAMPAIGN_COMPLETE_MARKER" \
  "$EXPECTED_CHUNKS")
printf 'accepted_campaign_completion_proof=%s\n' "$accepted_campaign_proof"
printf 'accepted_campaign_final_audit_summary=%s\n' "$ACCEPTED_FINAL_AUDIT_SUMMARY"
if [ "$accepted_campaign_proof" != "PASS" ]; then
  failures=$((failures + 1))
fi

echo "-- formal 100k chunk audit --"
for idx in $(seq 1 "$EXPECTED_CHUNKS"); do
  tag=$(printf 'chunk_%03d_100k_after_chunk08_pass' "$idx")
  dataset="$BASE/datasets/$tag"
  exists=0
  [ -d "$dataset" ] && exists=1
  n=$(nonempty_count "$dataset")
  total_nonempty=$((total_nonempty + n))
  ds_status=$(json_status "$dataset/parallel_candidate_queue_dataset_summary.json")
  cp_summary=$(latest_per_chunk_summary "$tag")
  cp_status="MISSING"
  cp_proof="MISSING"
  if [ -n "$cp_summary" ]; then
    cp_status=$(json_status "$cp_summary")
    cp_proof=$(checkpoint_proof "$cp_summary" "$EXPECTED_PER_CHUNK")
  fi
  chunk_state="FAIL"
  if [ "$exists" -eq 1 ] && [ "$n" -ge "$EXPECTED_PER_CHUNK" ] && [ "$ds_status" = "PASS" ] && [ "$cp_proof" = "PASS" ]; then
    chunk_state="PASS"
    formal_chunk_pass=$((formal_chunk_pass + 1))
  else
    failures=$((failures + 1))
  fi
  printf 'FORMAL_CHUNK index=%s tag=%s state=%s exists=%s nonempty=%s dataset_status=%s checkpoint_status=%s checkpoint_proof=%s dataset=%s\n' \
    "$idx" "$tag" "$chunk_state" "$exists" "$n" "$ds_status" "$cp_status" "$cp_proof" "$dataset"
  if [ -n "$cp_summary" ]; then
    printf 'FORMAL_CHUNK_CHECKPOINT index=%s summary=%s\n' "$idx" "$cp_summary"
  fi
done

echo "-- cumulative prefix audit --"
for idx in $(seq 1 "$EXPECTED_CHUNKS"); do
  expected=$((idx * EXPECTED_PER_CHUNK))
  tag=$(printf 'cumulative_%04dk_after_chunk08_pass' "$((idx * 100))")
  summary=$(latest_cumulative_summary "$tag")
  status="MISSING"
  proof="MISSING"
  if [ -n "$summary" ]; then
    status=$(json_status "$summary")
    proof=$(checkpoint_proof "$summary" "$expected")
  fi
  state="FAIL"
  if [ "$proof" = "PASS" ]; then
    state="PASS"
    cumulative_pass=$((cumulative_pass + 1))
  else
    failures=$((failures + 1))
  fi
  printf 'CUMULATIVE_PREFIX index=%s tag=%s expected_rows=%s state=%s checkpoint_status=%s checkpoint_proof=%s\n' \
    "$idx" "$tag" "$expected" "$state" "$status" "$proof"
  if [ -n "$summary" ]; then
    printf 'CUMULATIVE_PREFIX_CHECKPOINT index=%s summary=%s\n' "$idx" "$summary"
  fi
done

echo "-- checkpoint evidence index audit --"
EVIDENCE_INDEX_JSON=$BASE/status/100k_checkpoint_evidence_index_20260707/mars56_100k_checkpoint_evidence_index.json
EVIDENCE_INDEX_MD=$BASE/status/100k_checkpoint_evidence_index_20260707/mars56_100k_checkpoint_evidence_index.md
evidence_tmp=$("$PY" - "$EVIDENCE_INDEX_JSON" "$EVIDENCE_INDEX_MD" "$EXPECTED_CHUNKS" <<'PY'
import json
import math
import pathlib
import sys

json_path = pathlib.Path(sys.argv[1])
md_path = pathlib.Path(sys.argv[2])
expected_chunks = int(sys.argv[3])
expected_parallel_jobs = 48
expected_target_seconds_per_row = 4.0
expected_target_days_per_100k = 5.0
max_seconds_per_accepted_row = 4.5
max_days_per_100k = 5.5
reasons = []
rate_reasons = []
data = {}

if not json_path.exists():
    reasons.append("json_missing")
else:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"json_parse_error:{type(exc).__name__}")

if not md_path.exists():
    reasons.append("md_missing")

status = data.get("overall_status") if isinstance(data, dict) else None
formal_pass = data.get("formal_100k_evidence_pass_count") if isinstance(data, dict) else None
cumulative_pass = data.get("cumulative_evidence_pass_count") if isinstance(data, dict) else None
formal_count = data.get("formal_100k_dataset_count") if isinstance(data, dict) else None
all_formal_count = data.get("all_formal_100k_dataset_count") if isinstance(data, dict) else None
cumulative_count = data.get("cumulative_evidence_count") if isinstance(data, dict) else None
expected_per_chunk = data.get("expected_per_chunk") if isinstance(data, dict) else None
expected_total = data.get("expected_total") if isinstance(data, dict) else None
formal_tag_status = data.get("formal_100k_tag_status") if isinstance(data, dict) else None
missing_expected_formal_tags = data.get("missing_expected_formal_100k_tags") if isinstance(data, dict) and isinstance(data.get("missing_expected_formal_100k_tags"), list) else []
unexpected_formal_tags = data.get("unexpected_formal_100k_tags") if isinstance(data, dict) and isinstance(data.get("unexpected_formal_100k_tags"), list) else []
cumulative_tag_status = data.get("cumulative_checkpoint_tag_status") if isinstance(data, dict) else None
missing_expected_cumulative_tags = data.get("missing_expected_cumulative_checkpoint_tags") if isinstance(data, dict) and isinstance(data.get("missing_expected_cumulative_checkpoint_tags"), list) else []
unexpected_cumulative_tags = data.get("unexpected_cumulative_checkpoint_tags") if isinstance(data, dict) and isinstance(data.get("unexpected_cumulative_checkpoint_tags"), list) else []
duplicate_cumulative_tags = data.get("duplicate_cumulative_checkpoint_tags") if isinstance(data, dict) and isinstance(data.get("duplicate_cumulative_checkpoint_tags"), list) else []
all_cumulative_count = data.get("all_cumulative_evidence_count") if isinstance(data, dict) else None
rate = data.get("production_rate_artifact") if isinstance(data, dict) and isinstance(data.get("production_rate_artifact"), dict) else {}
global_training = data.get("global_training_evaluation_proof") if isinstance(data, dict) and isinstance(data.get("global_training_evaluation_proof"), dict) else {}

def as_int(value):
    try:
        return int(value)
    except Exception:
        return None

def as_float(value):
    try:
        return float(value)
    except Exception:
        return None

formal_pass_i = as_int(formal_pass)
cumulative_pass_i = as_int(cumulative_pass)
formal_count_i = as_int(formal_count)
all_formal_count_i = as_int(all_formal_count)
cumulative_count_i = as_int(cumulative_count)
all_cumulative_count_i = as_int(all_cumulative_count)
expected_per_chunk_i = as_int(expected_per_chunk)
expected_total_i = as_int(expected_total)
rate_latest_parallel_i = as_int(rate.get("latest_parallel_jobs"))
rate_expected_parallel_i = as_int(rate.get("expected_parallel_jobs"))
rate_return_code_i = as_int(rate.get("return_code"))
rate_json_bytes_i = as_int(rate.get("json_bytes"))
rate_md_bytes_i = as_int(rate.get("md_bytes"))
rate_measured_seconds_f = as_float(rate.get("measured_seconds_per_accepted_row"))
rate_eta_days_100k_f = as_float(rate.get("eta_days_per_100k"))
rate_target_seconds_f = as_float(rate.get("target_seconds_per_accepted_row"))
rate_target_days_100k_f = as_float(rate.get("target_days_per_100k"))
required_total_i = expected_per_chunk_i * expected_chunks if expected_per_chunk_i is not None else None
global_status = global_training.get("status") if global_training else None
global_rows_i = as_int(global_training.get("training_row_count")) if global_training else None
global_unique_i = as_int(global_training.get("unique_training_evaluation_count")) if global_training else None
global_duplicate_i = as_int(global_training.get("duplicate_evaluation_count")) if global_training else None
global_missing_bad_i = as_int(global_training.get("missing_or_bad_training_csv_count")) if global_training else None
global_pass_chunks_i = as_int(global_training.get("formal_pass_chunk_count")) if global_training else None

if status != "PASS":
    reasons.append(f"overall_status={status!r}")
if formal_tag_status != "PASS":
    reasons.append(f"formal_100k_tag_status={formal_tag_status!r}")
if missing_expected_formal_tags:
    reasons.append("missing_expected_formal_100k_tags=" + ",".join(str(tag) for tag in missing_expected_formal_tags))
if unexpected_formal_tags:
    reasons.append("unexpected_formal_100k_tags=" + ",".join(str(tag) for tag in unexpected_formal_tags))
if cumulative_tag_status != "PASS":
    reasons.append(f"cumulative_checkpoint_tag_status={cumulative_tag_status!r}")
if missing_expected_cumulative_tags:
    reasons.append("missing_expected_cumulative_checkpoint_tags=" + ",".join(str(tag) for tag in missing_expected_cumulative_tags))
if unexpected_cumulative_tags:
    reasons.append("unexpected_cumulative_checkpoint_tags=" + ",".join(str(tag) for tag in unexpected_cumulative_tags))
if duplicate_cumulative_tags:
    reasons.append("duplicate_cumulative_checkpoint_tags=" + ",".join(str(tag) for tag in duplicate_cumulative_tags))
if formal_pass_i is None or formal_pass_i < expected_chunks:
    reasons.append(f"formal_100k_evidence_pass_count={formal_pass!r}")
if cumulative_pass_i is None or cumulative_pass_i < expected_chunks:
    reasons.append(f"cumulative_evidence_pass_count={cumulative_pass!r}")
if formal_count_i is None or formal_count_i < expected_chunks:
    reasons.append(f"formal_100k_dataset_count={formal_count!r}")
if cumulative_count_i is None or cumulative_count_i < expected_chunks:
    reasons.append(f"cumulative_evidence_count={cumulative_count!r}")
if expected_per_chunk_i is None:
    reasons.append(f"expected_per_chunk={expected_per_chunk!r}")
if required_total_i is None:
    reasons.append(f"expected_total={expected_total!r}")
elif expected_total_i != required_total_i:
    reasons.append(f"expected_total={expected_total!r},required={required_total_i}")
if not global_training:
    reasons.append("global_training_evaluation_proof=MISSING")
else:
    if global_status != "PASS":
        reasons.append(f"global_training_evaluation_proof.status={global_status!r}")
    if global_pass_chunks_i is None or global_pass_chunks_i < expected_chunks:
        reasons.append(f"global_training_evaluation_proof.formal_pass_chunk_count={global_training.get('formal_pass_chunk_count')!r}")
    if required_total_i is not None:
        if global_rows_i is None or global_rows_i < required_total_i:
            reasons.append(f"global_training_evaluation_proof.training_row_count={global_training.get('training_row_count')!r},required={required_total_i}")
        if global_unique_i is None or global_unique_i < required_total_i:
            reasons.append(f"global_training_evaluation_proof.unique_training_evaluation_count={global_training.get('unique_training_evaluation_count')!r},required={required_total_i}")
    if global_duplicate_i != 0:
        reasons.append(f"global_training_evaluation_proof.duplicate_evaluation_count={global_training.get('duplicate_evaluation_count')!r}")
    if global_missing_bad_i != 0:
        reasons.append(f"global_training_evaluation_proof.missing_or_bad_training_csv_count={global_training.get('missing_or_bad_training_csv_count')!r}")
if not rate:
    rate_reasons.append("production_rate_artifact=MISSING")
else:
    if rate.get("json_exists") is not True:
        rate_reasons.append(f"production_rate_artifact.json_exists={rate.get('json_exists')!r}")
    if rate_json_bytes_i is None or rate_json_bytes_i <= 0:
        rate_reasons.append(f"production_rate_artifact.json_bytes={rate.get('json_bytes')!r}")
    if rate.get("md_exists") is not True:
        rate_reasons.append(f"production_rate_artifact.md_exists={rate.get('md_exists')!r}")
    if rate_md_bytes_i is None or rate_md_bytes_i <= 0:
        rate_reasons.append(f"production_rate_artifact.md_bytes={rate.get('md_bytes')!r}")
    if rate.get("audit_mode") != "REMOTE_READ_ONLY_AUDIT":
        rate_reasons.append(f"production_rate_artifact.audit_mode={rate.get('audit_mode')!r}")
    if rate_return_code_i != 0:
        rate_reasons.append(f"production_rate_artifact.return_code={rate.get('return_code')!r}")
    if rate.get("production_rate_audit_status") != "PASS":
        rate_reasons.append(f"production_rate_artifact.production_rate_audit_status={rate.get('production_rate_audit_status')!r}")
    if rate.get("production_rate_target_status") != "PASS":
        rate_reasons.append(f"production_rate_artifact.production_rate_target_status={rate.get('production_rate_target_status')!r}")
    if rate_latest_parallel_i != expected_parallel_jobs:
        rate_reasons.append(f"production_rate_artifact.latest_parallel_jobs={rate.get('latest_parallel_jobs')!r}")
    if rate_expected_parallel_i != expected_parallel_jobs:
        rate_reasons.append(f"production_rate_artifact.expected_parallel_jobs={rate.get('expected_parallel_jobs')!r}")
    if rate_target_seconds_f is None or not math.isclose(rate_target_seconds_f, expected_target_seconds_per_row, rel_tol=0.0, abs_tol=1e-12):
        rate_reasons.append(f"production_rate_artifact.target_seconds_per_accepted_row={rate.get('target_seconds_per_accepted_row')!r}")
    if rate_target_days_100k_f is None or not math.isclose(rate_target_days_100k_f, expected_target_days_per_100k, rel_tol=0.0, abs_tol=1e-12):
        rate_reasons.append(f"production_rate_artifact.target_days_per_100k={rate.get('target_days_per_100k')!r}")
    if rate_measured_seconds_f is None or rate_measured_seconds_f > max_seconds_per_accepted_row:
        rate_reasons.append(f"production_rate_artifact.measured_seconds_per_accepted_row={rate.get('measured_seconds_per_accepted_row')!r}")
    if rate_eta_days_100k_f is None or rate_eta_days_100k_f > max_days_per_100k:
        rate_reasons.append(f"production_rate_artifact.eta_days_per_100k={rate.get('eta_days_per_100k')!r}")
reasons.extend(rate_reasons)

audit_result = "PASS" if not reasons else "FAIL"
print(f"evidence_index_json={json_path}")
print(f"evidence_index_md={md_path}")
print(f"evidence_index_status={status if status is not None else 'MISSING'}")
print(f"evidence_formal_100k_dataset_count={formal_count_i if formal_count_i is not None else 'MISSING'}")
print(f"evidence_all_formal_100k_dataset_count={all_formal_count_i if all_formal_count_i is not None else 'MISSING'}")
print(f"evidence_formal_100k_evidence_pass_count={formal_pass_i if formal_pass_i is not None else 'MISSING'}")
print(f"evidence_cumulative_evidence_count={cumulative_count_i if cumulative_count_i is not None else 'MISSING'}")
print(f"evidence_all_cumulative_evidence_count={all_cumulative_count_i if all_cumulative_count_i is not None else 'MISSING'}")
print(f"evidence_cumulative_evidence_pass_count={cumulative_pass_i if cumulative_pass_i is not None else 'MISSING'}")
print(f"evidence_formal_100k_tag_status={formal_tag_status if formal_tag_status is not None else 'MISSING'}")
print("evidence_missing_expected_formal_100k_tags=" + (",".join(str(tag) for tag in missing_expected_formal_tags) if missing_expected_formal_tags else "none"))
print("evidence_unexpected_formal_100k_tags=" + (",".join(str(tag) for tag in unexpected_formal_tags) if unexpected_formal_tags else "none"))
print(f"evidence_cumulative_checkpoint_tag_status={cumulative_tag_status if cumulative_tag_status is not None else 'MISSING'}")
print("evidence_missing_expected_cumulative_checkpoint_tags=" + (",".join(str(tag) for tag in missing_expected_cumulative_tags) if missing_expected_cumulative_tags else "none"))
print("evidence_unexpected_cumulative_checkpoint_tags=" + (",".join(str(tag) for tag in unexpected_cumulative_tags) if unexpected_cumulative_tags else "none"))
print("evidence_duplicate_cumulative_checkpoint_tags=" + (",".join(str(tag) for tag in duplicate_cumulative_tags) if duplicate_cumulative_tags else "none"))
print(f"evidence_expected_per_chunk={expected_per_chunk_i if expected_per_chunk_i is not None else 'MISSING'}")
print(f"evidence_expected_total={expected_total_i if expected_total_i is not None else 'MISSING'}")
print(f"evidence_index_md_exists={str(md_path.exists()).lower()}")
print(f"global_training_evaluation_status={global_status if global_status is not None else 'MISSING'}")
print(f"global_training_evaluation_formal_pass_chunk_count={global_pass_chunks_i if global_pass_chunks_i is not None else 'MISSING'}")
print(f"global_training_evaluation_row_count={global_rows_i if global_rows_i is not None else 'MISSING'}")
print(f"global_training_evaluation_unique_count={global_unique_i if global_unique_i is not None else 'MISSING'}")
print(f"global_training_evaluation_duplicate_count={global_duplicate_i if global_duplicate_i is not None else 'MISSING'}")
print(f"global_training_evaluation_missing_or_bad_csv_count={global_missing_bad_i if global_missing_bad_i is not None else 'MISSING'}")
print(f"production_rate_artifact_json_exists={str(rate.get('json_exists')).lower() if rate else 'missing'}")
print(f"production_rate_artifact_json_bytes={rate_json_bytes_i if rate_json_bytes_i is not None else 'MISSING'}")
print(f"production_rate_artifact_md_exists={str(rate.get('md_exists')).lower() if rate else 'missing'}")
print(f"production_rate_artifact_md_bytes={rate_md_bytes_i if rate_md_bytes_i is not None else 'MISSING'}")
print(f"production_rate_artifact_audit_mode={rate.get('audit_mode') if rate else 'MISSING'}")
print(f"production_rate_artifact_return_code={rate_return_code_i if rate_return_code_i is not None else 'MISSING'}")
print(f"production_rate_artifact_audit_status={rate.get('production_rate_audit_status') if rate else 'MISSING'}")
print(f"production_rate_artifact_target_status={rate.get('production_rate_target_status') if rate else 'MISSING'}")
print(f"production_rate_artifact_latest_parallel_jobs={rate_latest_parallel_i if rate_latest_parallel_i is not None else 'MISSING'}")
print(f"production_rate_artifact_expected_parallel_jobs={rate_expected_parallel_i if rate_expected_parallel_i is not None else 'MISSING'}")
print(f"production_rate_artifact_measured_seconds_per_accepted_row={rate_measured_seconds_f if rate_measured_seconds_f is not None else 'MISSING'}")
print(f"production_rate_artifact_eta_days_per_100k={rate_eta_days_100k_f if rate_eta_days_100k_f is not None else 'MISSING'}")
print(f"production_rate_artifact_target_seconds_per_accepted_row={rate_target_seconds_f if rate_target_seconds_f is not None else 'MISSING'}")
print(f"production_rate_artifact_target_days_per_100k={rate_target_days_100k_f if rate_target_days_100k_f is not None else 'MISSING'}")
print(f"production_rate_artifact_max_seconds_per_accepted_row={max_seconds_per_accepted_row}")
print(f"production_rate_artifact_max_days_per_100k={max_days_per_100k}")
print(f"production_rate_artifact_audit_result={'PASS' if rate and not rate_reasons else 'FAIL'}")
if rate_reasons:
    print("production_rate_artifact_audit_reasons=" + ";".join(rate_reasons))
print(f"evidence_index_audit_result={audit_result}")
if reasons:
    print("evidence_index_audit_reasons=" + ";".join(reasons))
PY
)
printf '%s\n' "$evidence_tmp"
evidence_index_audit_result=$(printf '%s\n' "$evidence_tmp" | awk -F= '$1=="evidence_index_audit_result"{print $2}' | tail -n 1)
if [ "$evidence_index_audit_result" != "PASS" ]; then
  failures=$((failures + 1))
fi

echo "-- production plan contract audit --"
contract_audit_result="FAIL"
if [ ! -f "$CONTRACT_BUILD" ]; then
  echo "production_plan_contract_build_script_missing=$CONTRACT_BUILD"
  failures=$((failures + 1))
elif [ ! -f "$CONTRACT_AUDIT" ]; then
  echo "production_plan_contract_audit_script_missing=$CONTRACT_AUDIT"
  failures=$((failures + 1))
elif [ ! -f "$EVIDENCE_INDEX_JSON" ]; then
  echo "production_plan_contract_audit_result=FAIL"
  echo "production_plan_contract_audit_reason=evidence_index_json_missing"
  failures=$((failures + 1))
else
  contract_tmp=$(mktemp -d "${TMPDIR:-/tmp}/mars56_1m_contract_audit.XXXXXX")
  trap 'rm -rf "$contract_tmp"' EXIT
  if "$PY" "$CONTRACT_BUILD" \
      --out-dir "$contract_tmp/contract" \
      --base "$BASE" \
      --remote-project "$PROJECT" \
      --expected-chunks "$EXPECTED_CHUNKS" \
      --expected-per-chunk "$EXPECTED_PER_CHUNK" >/tmp/mars56_contract_build.$$ 2>&1; then
    cat /tmp/mars56_contract_build.$$ || true
    if "$PY" "$CONTRACT_AUDIT" \
        --contract-json "$contract_tmp/contract/mars56_1m_production_plan_contract.json" \
        --evidence-index-json "$EVIDENCE_INDEX_JSON" \
        --out-dir "$contract_tmp/audit" \
        --no-fail-exit >/tmp/mars56_contract_audit.$$ 2>&1; then
      cat /tmp/mars56_contract_audit.$$ || true
      contract_summary="$contract_tmp/audit/mars56_1m_production_plan_contract_audit_summary.json"
      if [ -f "$contract_summary" ]; then
        contract_audit_result=$("$PY" - "$contract_summary" <<'PY'
import json
import sys
try:
    print(str(json.load(open(sys.argv[1])).get("overall_status", "")))
except Exception:
    print("PARSE_ERROR")
PY
)
        echo "production_plan_contract_audit_summary=$contract_summary"
        echo "production_plan_contract_audit_result=$contract_audit_result"
      else
        echo "production_plan_contract_audit_result=FAIL"
        echo "production_plan_contract_audit_reason=summary_missing_after_audit"
      fi
    else
      cat /tmp/mars56_contract_audit.$$ || true
      echo "production_plan_contract_audit_result=FAIL"
      echo "production_plan_contract_audit_reason=audit_command_failed"
    fi
  else
    cat /tmp/mars56_contract_build.$$ || true
    echo "production_plan_contract_audit_result=FAIL"
    echo "production_plan_contract_audit_reason=build_command_failed"
  fi
  rm -f /tmp/mars56_contract_build.$$ /tmp/mars56_contract_audit.$$ || true
  if [ "$contract_audit_result" != "PASS" ]; then
    failures=$((failures + 1))
  fi
fi

printf 'formal_chunk_pass_count=%s\n' "$formal_chunk_pass"
printf 'cumulative_pass_count=%s\n' "$cumulative_pass"
printf 'total_nonempty_formal_s4p=%s\n' "$total_nonempty"
printf 'production_plan_contract_audit_result=%s\n' "$contract_audit_result"
printf 'accepted_campaign_completion_proof=%s\n' "$accepted_campaign_proof"
printf 'failure_count=%s\n' "$failures"

if [ "$total_nonempty" -ge "$EXPECTED_TOTAL" ] && [ "$formal_chunk_pass" -eq "$EXPECTED_CHUNKS" ] && [ "$cumulative_pass" -eq "$EXPECTED_CHUNKS" ] && [ "$contract_audit_result" = "PASS" ] && [ "$accepted_campaign_proof" = "PASS" ] && [ "$failures" -eq 0 ]; then
  echo "ONE_MILLION_GOAL_STATUS=PASS"
  echo "ONE_MILLION_GOAL_DECISION=GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW"
else
  echo "ONE_MILLION_GOAL_STATUS=FAIL"
  echo "ONE_MILLION_GOAL_DECISION=CONTINUE_GENERATION_OR_CHECKPOINT_REPAIR"
fi
REMOTE

echo "Opening ${USER_NAME}@${JUMP_HOST}; approve Duo when prompted."
echo "This is a read-only final-goal audit. It will not write remote files."
ssh "${SSH_ARGS[@]}" "${USER_NAME}@${JUMP_HOST}" "ssh -tt ${MARS_HOST} 'bash -s'" <<<"$REMOTE_AUDIT"
