#!/usr/bin/env bash
set -euo pipefail

# Usage after Duo is available:
#   bash RUN_MARS56_AUTO_CHECKPOINT_COMPLETED_100K_CHUNKS_AFTER_DUO_20260707.sh
#
# Optional:
#   DRY_RUN=1 bash RUN_MARS56_AUTO_CHECKPOINT_COMPLETED_100K_CHUNKS_AFTER_DUO_20260707.sh
#   RERUN_FAILED_CHECKPOINTS=1 bash RUN_MARS56_AUTO_CHECKPOINT_COMPLETED_100K_CHUNKS_AFTER_DUO_20260707.sh
#
# The remote side scans only U8-gated 100k production dataset names and skips
# the old geometry-only chunk_01_n100000 path. A completed model test is treated
# as durable evidence via its checkpoint summary JSON and will not be rerun.

JUMP_HOST="login.example.edu"
MARS_HOST="mars.example.edu"
USER_NAME="researcher"
DRY_RUN="${DRY_RUN:-0}"
RERUN_FAILED_CHECKPOINTS="${RERUN_FAILED_CHECKPOINTS:-0}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-}"
SSH_PERSIST="${SSH_PERSIST:-20m}"

case "$DRY_RUN" in 0|1) ;;
  *) echo "ERROR: DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
case "$RERUN_FAILED_CHECKPOINTS" in 0|1) ;;
  *) echo "ERROR: RERUN_FAILED_CHECKPOINTS must be 0 or 1." >&2; exit 2 ;;
esac
if [[ "$SSH_CONTROL_PATH" == *$'\n'* || "$SSH_CONTROL_PATH" == *$'\r'* ]]; then
  echo "ERROR: SSH_CONTROL_PATH contains unsupported newline characters." >&2
  exit 2
fi

SSH_ARGS=(-tt)
if [ -n "$SSH_CONTROL_PATH" ]; then
  SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
fi

read -r -d '' REMOTE_RUN <<'REMOTE' || true
set -euo pipefail

BASE=/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256
PROJECT=/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705
PY=/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python
CONFIG=$PROJECT/configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml
U8=$BASE/status/accepted_inrange_pool_after_chunk08_20260706/physical_feature_uniformity/physical_feature_uniformity_summary.json
EXPECTED=100000
DRY_RUN="${DRY_RUN:-0}"
RERUN_FAILED_CHECKPOINTS="${RERUN_FAILED_CHECKPOINTS:-0}"

printf 'remote_time='; date '+%Y-%m-%d %H:%M:%S %Z'
printf 'base=%s\n' "$BASE"
printf 'dry_run=%s\n' "$DRY_RUN"
printf 'rerun_failed_checkpoints=%s\n' "$RERUN_FAILED_CHECKPOINTS"

if [ ! -f "$U8" ]; then
  echo "STATUS=WAIT_U8_SUMMARY_MISSING"
  exit 0
fi
u8_status=$("$PY" - "$U8" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("overall_status", ""))
except Exception:
    print("PARSE_ERROR")
PY
)
printf 'U8_summary=%s\n' "$U8"
printf 'U8_overall_status=%s\n' "$u8_status"
if [ "$u8_status" != "PASS" ]; then
  echo "STATUS=WAIT_U8_NOT_PASS"
  echo "No 100k production checkpoint should run before U8 physical-feature uniformity passes."
  exit 0
fi

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
        if comparator == "min" and metric_value < threshold_value:
            reasons.append(
                f"uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}={metric_value:.6g},required={threshold_value}"
            )
        elif comparator == "max" and metric_value > threshold_value:
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
        if metric_value < threshold_value:
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
        if occupied_fraction < expected_min_four_d_occupied_fraction:
            reasons.append(
                f"uniformity.four_dimensional_uniformity.occupied_fraction={occupied_fraction:.6g},required={expected_min_four_d_occupied_fraction}"
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

echo "STATUS=SCANNING_COMPLETED_100K_DATASETS"
shopt -s nullglob
datasets=(
  "$BASE"/datasets/chunk_0*_100k_after_chunk08_pass
  "$BASE"/datasets/chunk_*_100k_after_chunk08_pass
)

if [ "${#datasets[@]}" -eq 0 ]; then
  echo "completed_100k_dataset_candidates=0"
  exit 0
fi

seen_file=$(mktemp)
trap 'rm -f "$seen_file"' EXIT
printf '%s\n' "${datasets[@]}" | sort -u > "$seen_file"

completed=0
launched=0
checkpoint_pass=0
checkpoint_fail=0
skipped=0
waiting=0
command_failed=0

while IFS= read -r DATASET; do
  [ -n "$DATASET" ] || continue
  tag=$(basename "$DATASET")

  case "$tag" in
    chunk_01_n100000|chunk_01_n100000*)
      echo "SKIP_GEOMETRY_ONLY tag=$tag dataset=$DATASET"
      skipped=$((skipped + 1))
      continue
      ;;
  esac
  case "$DATASET" in
    "$BASE"/datasets/*_100k_after_chunk08_pass) ;;
    *)
      echo "SKIP_NON_PRODUCTION_100K_NAME tag=$tag dataset=$DATASET"
      skipped=$((skipped + 1))
      continue
      ;;
  esac

  nonempty=$(find "$DATASET" -type f -name '*.s4p' -size +0c 2>/dev/null | wc -l | tr -d ' ')
  summary="$DATASET/parallel_candidate_queue_dataset_summary.json"
  summary_status="MISSING"
  if [ -f "$summary" ]; then
    summary_status=$("$PY" - "$summary" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("overall_status", ""))
except Exception:
    print("PARSE_ERROR")
PY
)
  fi
  printf 'DATASET tag=%s nonempty=%s summary_status=%s path=%s\n' "$tag" "$nonempty" "$summary_status" "$DATASET"

  if [ "$nonempty" -lt "$EXPECTED" ] || [ "$summary_status" != "PASS" ]; then
    echo "WAIT_DATASET_NOT_READY tag=$tag"
    waiting=$((waiting + 1))
    continue
  fi

  completed=$((completed + 1))
  existing=$(find "$BASE/model_tests/$tag" -path '*/mars56_s4p_physical_checkpoint_pipeline_summary.json' -type f 2>/dev/null | sort | tail -n 1 || true)
  if [ -n "$existing" ]; then
    existing_status=$("$PY" - "$existing" <<'PY'
import json, sys
try:
    d=json.load(open(sys.argv[1]))
    print(str(d.get("overall_status", "")))
except Exception:
    print("PARSE_ERROR")
PY
)
    existing_proof=$(checkpoint_proof "$existing" "$EXPECTED")
    if [ "$existing_proof" = "PASS" ]; then
      echo "SKIP_ALREADY_CHECKPOINTED_PASS tag=$tag existing_summary=$existing existing_status=$existing_status existing_proof=$existing_proof"
      checkpoint_pass=$((checkpoint_pass + 1))
      skipped=$((skipped + 1))
      continue
    fi
    echo "EXISTING_CHECKPOINT_NOT_PASS tag=$tag existing_summary=$existing existing_status=$existing_status existing_proof=$existing_proof"
    if [ "$RERUN_FAILED_CHECKPOINTS" != "1" ]; then
      checkpoint_fail=$((checkpoint_fail + 1))
      skipped=$((skipped + 1))
      continue
    fi
    echo "RERUN_FAILED_CHECKPOINT_REQUESTED tag=$tag"
  fi

  OUT="$BASE/model_tests/${tag}/post100k_physical_checkpoint_$(date +%Y%m%d_%H%M%S)"
  LOG="$BASE/logs/${tag}_post100k_physical_checkpoint_$(date +%Y%m%d_%H%M%S).log"
  echo "READY_TO_CHECKPOINT tag=$tag out=$OUT log=$LOG"
  if [ "$DRY_RUN" = "1" ]; then
    continue
  fi

  mkdir -p "$OUT" "$(dirname "$LOG")"
  if bash "$PROJECT/scripts/run_mars56_s4p_physical_checkpoint_pipeline.sh" \
    --dataset-dir "$DATASET" \
    --out-dir "$OUT" \
    --config "$CONFIG" \
    --count "$EXPECTED" \
    --min-valid "$EXPECTED" \
    --target-ghz 15 \
    --bins 10 \
    --pair-bins 10 \
    --lp-min 0.5 --lp-max 3 \
    --ls-min 0.5 --ls-max 3 \
    --q-min 5 --q-max 25 \
    --k-min 0 --k-max 0.8 \
    --require-four-d-gate \
    --four-d-bins 4 \
    --min-four-d-occupied-frac 0.50 \
    --require-plots \
    --python "$PY" 2>&1 | tee "$LOG"; then
    launched=$((launched + 1))
    summary_path="$OUT/mars56_s4p_physical_checkpoint_pipeline_summary.json"
    if [ -f "$summary_path" ]; then
      new_status=$("$PY" - "$summary_path" <<'PY'
import json, sys
try:
    print(str(json.load(open(sys.argv[1])).get("overall_status", "")))
except Exception:
    print("PARSE_ERROR")
PY
)
      new_proof=$(checkpoint_proof "$summary_path" "$EXPECTED")
      echo "CHECKPOINT_RESULT tag=$tag summary=$summary_path status=$new_status proof=$new_proof"
      if [ "$new_proof" = "PASS" ]; then
        checkpoint_pass=$((checkpoint_pass + 1))
      else
        checkpoint_fail=$((checkpoint_fail + 1))
      fi
    else
      echo "CHECKPOINT_SUMMARY_MISSING_AFTER_RUN tag=$tag out=$OUT"
      checkpoint_fail=$((checkpoint_fail + 1))
    fi
  else
    command_failed=$((command_failed + 1))
    echo "CHECKPOINT_COMMAND_FAILED tag=$tag out=$OUT log=$LOG"
  fi
done < "$seen_file"

printf 'completed_ready_datasets=%s\n' "$completed"
printf 'checkpoint_commands_launched=%s\n' "$launched"
printf 'checkpoint_pass=%s\n' "$checkpoint_pass"
printf 'checkpoint_fail=%s\n' "$checkpoint_fail"
printf 'datasets_waiting=%s\n' "$waiting"
printf 'datasets_skipped=%s\n' "$skipped"
printf 'checkpoint_command_failures=%s\n' "$command_failed"
REMOTE

echo "Opening ${USER_NAME}@${JUMP_HOST}; approve Duo when prompted."
echo "Dry run: ${DRY_RUN}. This scans U8-gated 100k production datasets and checkpoint-tests completed chunks once."
ssh "${SSH_ARGS[@]}" "${USER_NAME}@${JUMP_HOST}" \
  "DRY_RUN='${DRY_RUN}' RERUN_FAILED_CHECKPOINTS='${RERUN_FAILED_CHECKPOINTS}' ssh -tt ${MARS_HOST} 'DRY_RUN='\\''${DRY_RUN}'\\'' RERUN_FAILED_CHECKPOINTS='\\''${RERUN_FAILED_CHECKPOINTS}'\\'' bash -s'" \
  <<<"$REMOTE_RUN"
