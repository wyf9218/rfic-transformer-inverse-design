#!/usr/bin/env bash
# Audit realized EMX labels during the urgent 100k run and launch the first
# inverse-model checkpoint only after 100,000 real, in-range rows exist.

set -u

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
PY="${PY:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python}"
CONFIG="${CONFIG:-$PROJECT/configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml}"
PRODUCTION_DIR="${PRODUCTION_DIR:-$BASE/production_chunks/chunk_001_100k_urgent_targeted_20260709}"
SOURCE_POOL="${SOURCE_POOL:-$BASE/status/accepted_inrange_pool_after_chunk08_20260706}"
STATUS_DIR="${STATUS_DIR:-$BASE/status/first100k_urgent_targeted_20260709/accepted_checkpoint_watcher}"
POLL_SECONDS="${POLL_SECONDS:-300}"
TARGET_ACCEPTED="${TARGET_ACCEPTED:-100000}"
MILESTONES_CSV="${MILESTONES_CSV:-1000,10000,25000,50000,73590,77000,80000,85000,90000,95000,100000}"

GEOMETRY_COLUMNS="geom__primary_outer_width_um,geom__primary_outer_height_um,geom__secondary_outer_width_um,geom__secondary_outer_height_um,geom__line_width_um,geom__primary_terminal_y_span_um,geom__secondary_terminal_y_span_um,geom__offset_um,geom__primary_feed_extension_um,geom__secondary_feed_extension_um"
INPUT_COLUMNS="input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center"
IFS=',' read -r -a MILESTONES <<< "$MILESTONES_CSV"

mkdir -p "$STATUS_DIR"
LOG="$STATUS_DIR/accepted_checkpoint_watcher.log"
LATEST="$STATUS_DIR/latest_acceptance_status.json"
TRAINED_MARKER="$STATUS_DIR/first100k_model_checkpoint.complete"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" | tee -a "$LOG"
}

run_logged() {
  local label="$1"
  shift
  {
    printf '\n===== %s =====\n' "$label"
    date '+%Y-%m-%d %H:%M:%S %Z'
    printf 'COMMAND'
    printf ' %q' "$@"
    printf '\n'
  } >> "$LOG"
  nice -n 15 "$@" >> "$LOG" 2>&1
  local rc=$?
  log "$label returncode=$rc"
  return "$rc"
}

require_json_gate() {
  local label="$1"
  local path="$2"
  local count_field="${3:-}"
  local minimum="${4:-0}"
  "$PY" - "$label" "$path" "$count_field" "$minimum" <<'PY'
import json
import pathlib
import sys

label, path_raw, count_field, minimum_raw = sys.argv[1:]
path = pathlib.Path(path_raw)
if not path.is_file():
    raise SystemExit(f"{label}: missing JSON {path}")
payload = json.loads(path.read_text(encoding="utf-8"))
if payload.get("overall_status") != "PASS":
    raise SystemExit(f"{label}: overall_status={payload.get('overall_status')!r}")
if count_field:
    value = int(payload.get(count_field) or 0)
    minimum = int(minimum_raw)
    if value < minimum:
        raise SystemExit(f"{label}: {count_field}={value}, minimum={minimum}")
print(f"{label}=PASS")
PY
  local rc=$?
  log "$label JSON gate returncode=$rc"
  return "$rc"
}

require_json_contract() {
  local label="$1"
  local path="$2"
  local status_field="$3"
  local expected_status="$4"
  local count_path="${5:-}"
  local minimum="${6:-0}"
  "$PY" - "$label" "$path" "$status_field" "$expected_status" "$count_path" "$minimum" <<'PY'
import json
import pathlib
import sys

label, path_raw, status_field, expected_status, count_path, minimum_raw = sys.argv[1:]
path = pathlib.Path(path_raw)
if not path.is_file():
    raise SystemExit(f"{label}: missing JSON {path}")
payload = json.loads(path.read_text(encoding="utf-8"))
status = payload.get(status_field)
if status != expected_status:
    raise SystemExit(f"{label}: {status_field}={status!r}, expected={expected_status!r}")
if count_path:
    value = payload
    for part in count_path.split("."):
        value = value[part]
    value = int(value)
    minimum = int(minimum_raw)
    if value < minimum:
        raise SystemExit(f"{label}: {count_path}={value}, minimum={minimum}")
print(f"{label}=PASS")
PY
  local rc=$?
  log "$label JSON contract returncode=$rc"
  return "$rc"
}

write_latest_status() {
  local milestone="$1"
  local out="$2"
  "$PY" - "$LATEST.tmp" "$milestone" "$TARGET_ACCEPTED" "$out" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

target, milestone, target_accepted, out_raw = sys.argv[1:]
out = pathlib.Path(out_raw)
merge_path = out / "accepted_pool" / "accepted_pool_merge_summary.json"
uniformity_path = out / "accepted_pool" / "physical_feature_uniformity" / "physical_feature_uniformity_summary.json"
merge = json.loads(merge_path.read_text(encoding="utf-8")) if merge_path.is_file() else {}
uniformity = json.loads(uniformity_path.read_text(encoding="utf-8")) if uniformity_path.is_file() else {}
payload = {
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "raw_new_s4p_milestone": int(milestone),
    "target_accepted_count": int(target_accepted),
    "accepted_combined_count": int(merge.get("row_count") or 0),
    "accepted_target_reached": int(merge.get("row_count") or 0) >= int(target_accepted),
    "merge_status": merge.get("overall_status", "MISSING"),
    "uniformity_status": uniformity.get("overall_status", "MISSING"),
    "merge_summary": str(merge_path),
    "uniformity_summary": str(uniformity_path),
    "scientific_caveat": "Only rows with real EMX-derived Lp/Ls/Q/|K| inside the explicit ranges are counted. Uniformity is reported independently and is never inferred from queue predictions.",
}
pathlib.Path(target).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  mv "$LATEST.tmp" "$LATEST"
}

build_model_checkpoint() {
  local milestone="$1"
  local out="$2"
  local checkpoint="$STATUS_DIR/first100k_accepted_model_checkpoint"
  local dataset="$checkpoint/dataset"
  local uniformity="$checkpoint/physical_feature_uniformity"
  local train="$checkpoint/physical_feature_inverse_training_table"
  local baseline="$checkpoint/ridge_baseline"
  local nn_plan="$checkpoint/nn_architecture_plan"
  local nn_train="$checkpoint/nn_architecture_training"
  local nn_figures="$checkpoint/nn_report_figures"
  local tandem="$checkpoint/physical_feature_tandem_inverse"

  if [[ -f "$TRAINED_MARKER" ]]; then
    return 0
  fi

  mkdir -p "$dataset" "$checkpoint"
  run_logged select_first100k_balanced_real_rows \
    "$PY" "$PROJECT/scripts/select_balanced_physical_feature_checkpoint.py" \
    --input-csv "$out/accepted_pool/dataset_rows.csv" --out-dir "$dataset" \
    --target-count "$TARGET_ACCEPTED" --seed 20260709 \
    --four-d-bins 4 --min-four-d-occupied-fraction 0.50 \
    --lp-min-nh 0.5 --lp-max-nh 3.0 --ls-min-nh 0.5 --ls-max-nh 3.0 \
    --q-min 5 --q-max 25 --k-min 0 --k-max 0.8 \
    --check-touchstone-exists --no-fail-exit || return 1
  "$PY" - "$dataset/balanced_physical_feature_checkpoint_summary.json" "$TARGET_ACCEPTED" <<'PY'
import json,pathlib,sys
path=pathlib.Path(sys.argv[1]); expected=int(sys.argv[2])
data=json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
if int(data.get("selected_count") or 0) != expected:
    raise SystemExit("balanced selected_count mismatch")
checks=data.get("checks") or {}
if checks.get("selected_geometry_unique") is not True:
    raise SystemExit("balanced checkpoint contains duplicate independent geometries")
PY
  if [[ $? -ne 0 ]]; then
    log "first100k balanced selection failed at milestone=$milestone"
    return 1
  fi

  run_logged first100k_strict_uniformity \
    "$PY" "$PROJECT/scripts/audit_physical_feature_uniformity.py" \
    --training-csv "$dataset/dataset_rows.csv" --out-dir "$uniformity" \
    --min-valid-count "$TARGET_ACCEPTED" --bins 10 --pair-bins 10 --four-d-bins 4 \
    --min-1d-occupied-frac 0.90 --min-1d-entropy-frac 0.90 \
    --max-1d-bin-imbalance 2.50 --min-pair-occupied-frac 0.65 \
    --min-pair-entropy-frac 0.80 --min-four-d-occupied-frac 0.50 \
    --k-mode magnitude --lp-min-nh 0.5 --lp-max-nh 3.0 \
    --ls-min-nh 0.5 --ls-max-nh 3.0 --q-min 5 --q-max 25 \
    --k-min 0 --k-max 0.8 --require-explicit-ranges \
    --require-four-d-gate --require-plots --no-fail-exit || return 1

  run_logged build_first100k_training_table \
    "$PY" "$PROJECT/scripts/build_physical_feature_inverse_training_table.py" "$dataset" \
    --out-dir "$train" \
    --feature-columns lp_nh_center,ls_nh_center,q_center,k_abs_center \
    --geometry-columns "$GEOMETRY_COLUMNS" \
    --input-prefix input__ \
    --check-touchstone-exists \
    --no-fail-exit || return 1
  require_json_gate first100k_training_table \
    "$train/physical_feature_inverse_training_manifest.json" training_count "$TARGET_ACCEPTED" || return 1

  run_logged first100k_ridge_baseline \
    "$PY" "$PROJECT/scripts/run_physical_feature_inverse_checkpoint_test.py" \
    --training-csv "$train/physical_feature_inverse_training_table.csv" \
    --out-dir "$baseline" \
    --input-columns "$INPUT_COLUMNS" \
    --geometry-columns "$GEOMETRY_COLUMNS" \
    --min-training-rows "$TARGET_ACCEPTED" \
    --max-train-rows 80000 \
    --max-test-rows 20000 \
    --no-fail-exit || return 1
  require_json_gate first100k_ridge_baseline \
    "$baseline/physical_feature_inverse_checkpoint_test_summary.json" usable_row_count "$TARGET_ACCEPTED" || return 1

  run_logged first100k_nn_plan \
    "$PY" "$PROJECT/scripts/plan_physical_feature_inverse_nn_architecture_search.py" \
    --training-csv "$train/physical_feature_inverse_training_table.csv" \
    --out-dir "$nn_plan" \
    --input-columns "$INPUT_COLUMNS" \
    --geometry-columns "$GEOMETRY_COLUMNS" \
    --min-training-rows "$TARGET_ACCEPTED" \
    --seeds 20260709 \
    --hidden-widths 128,256 \
    --depths 2,3 \
    --dropouts 0,0.05 \
    --learning-rates 0.001 \
    --weight-decays 0 \
    --batch-sizes 1024 \
    --max-candidates 8 \
    --no-fail-exit || return 1
  require_json_gate first100k_nn_plan \
    "$nn_plan/physical_feature_inverse_nn_architecture_search_summary.json" training_count "$TARGET_ACCEPTED" || return 1

  OPENBLAS_NUM_THREADS=8 OMP_NUM_THREADS=8 run_logged first100k_nn_train \
    "$PY" "$PROJECT/scripts/train_physical_feature_inverse_nn_architecture_search.py" \
    --training-csv "$train/physical_feature_inverse_training_table.csv" \
    --candidate-csv "$nn_plan/physical_feature_inverse_nn_architecture_candidates.csv" \
    --out-dir "$nn_train" \
    --input-columns "$INPUT_COLUMNS" \
    --geometry-columns "$GEOMETRY_COLUMNS" \
    --min-training-rows "$TARGET_ACCEPTED" \
    --max-candidates 8 \
    --max-epochs-cap 120 \
    --patience-cap 15 \
    --resume-completed-candidates \
    --no-fail-exit || return 1
  require_json_gate first100k_nn_train \
    "$nn_train/physical_feature_inverse_nn_architecture_search_training_summary.json" training_count "$TARGET_ACCEPTED" || return 1

  run_logged first100k_nn_report_figures \
    "$PY" "$PROJECT/scripts/build_physical_feature_inverse_nn_report_figures.py" \
    --training-summary "$nn_train/physical_feature_inverse_nn_architecture_search_training_summary.json" \
    --out-dir "$nn_figures" \
    --no-fail-exit || return 1
  require_json_contract first100k_nn_report_figures \
    "$nn_figures/physical_feature_inverse_nn_report_figures_summary.json" overall_status PASS || return 1

  OPENBLAS_NUM_THREADS=8 OMP_NUM_THREADS=8 run_logged first100k_tandem_inverse_ablation \
    "$PY" "$PROJECT/scripts/train_physical_feature_tandem_inverse.py" \
    --training-csv "$train/physical_feature_inverse_training_table.csv" \
    --out-dir "$tandem" \
    --input-columns "$INPUT_COLUMNS" \
    --geometry-columns "$GEOMETRY_COLUMNS" \
    --min-training-rows "$TARGET_ACCEPTED" \
    --forward-depth 3 --forward-width 256 \
    --inverse-depth 3 --inverse-width 256 \
    --batch-size 1024 --forward-epochs 160 --inverse-epochs 180 \
    --patience 20 --geometry-anchor-weight 0.01 \
    --no-fail-exit || true

  "$PY" - "$checkpoint/first100k_checkpoint_manifest.json" "$milestone" "$TARGET_ACCEPTED" "$dataset/dataset_rows.csv" "$dataset/balanced_physical_feature_checkpoint_summary.json" "$uniformity/physical_feature_uniformity_summary.json" "$train/physical_feature_inverse_training_manifest.json" "$baseline/physical_feature_inverse_checkpoint_test_summary.json" "$nn_train/physical_feature_inverse_nn_architecture_search_training_summary.json" "$nn_figures/physical_feature_inverse_nn_report_figures_summary.json" "$tandem/physical_feature_tandem_inverse_summary.json" <<'PY'
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

target, milestone, count, dataset, selection, uniformity, training, baseline, nn, nn_figures, tandem = sys.argv[1:]
def record(raw):
    path = pathlib.Path(raw)
    item = {"path": str(path), "exists": path.is_file()}
    if path.is_file():
        item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return item
uniformity_payload = json.loads(pathlib.Path(uniformity).read_text(encoding="utf-8")) if pathlib.Path(uniformity).is_file() else {}
uniformity_status = uniformity_payload.get("overall_status", "MISSING")
tandem_payload = json.loads(pathlib.Path(tandem).read_text(encoding="utf-8")) if pathlib.Path(tandem).is_file() else {}
tandem_status = tandem_payload.get("overall_status", "MISSING")
payload = {
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "overall_status": "COMPLETE" if uniformity_status == "PASS" else "PROVISIONAL_UNIFORMITY_FAIL",
    "model_test_status": "PASS",
    "tandem_ablation_status": tandem_status,
    "uniformity_status": uniformity_status,
    "trigger_raw_new_s4p_milestone": int(milestone),
    "accepted_training_row_count": int(count),
    "input_contract": ["Lp", "Ls", "Q=min(Qp,Qs)", "|K|"],
    "output_contract": "10 independent geometry variables",
    "artifacts": {
        "dataset": record(dataset),
        "balanced_selection": record(selection),
        "uniformity": record(uniformity),
        "training_manifest": record(training),
        "ridge_summary": record(baseline),
        "nn_summary": record(nn),
        "nn_report_figures_summary": record(nn_figures),
        "tandem_inverse_summary": record(tandem),
    },
    "limitations": [
        "Model metrics are reportable only from the referenced real-label artifacts.",
        "The physical-feature uniformity audit remains a separate gate from successful model execution.",
        "Inverse predictions still require DRC, EMX, and sampled HFSS validation.",
    ],
}
pathlib.Path(target).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  touch "$TRAINED_MARKER"
  log "first100k accepted model checkpoint complete at raw milestone=$milestone"
}

audit_milestone() {
  local milestone="$1"
  local out="$STATUS_DIR/milestone_${milestone}"
  local done="$out/milestone.complete"
  if [[ -f "$done" ]]; then
    local existing_accepted
    existing_accepted="$($PY - "$out/accepted_pool/accepted_pool_merge_summary.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
print(int(payload.get("row_count") or 0))
PY
)"
    if [[ "$existing_accepted" -ge "$TARGET_ACCEPTED" && ! -f "$TRAINED_MARKER" ]]; then
      build_model_checkpoint "$milestone" "$out"
    fi
    return 0
  fi
  mkdir -p "$out"
  log "milestone=$milestone audit start"

  run_logged "stable_index_${milestone}" \
    "$PY" "$PROJECT/scripts/build_stable_touchstone_index.py" "$PRODUCTION_DIR/dataset" \
    --out-dir "$out/stable_index" --max-count "$milestone" --min-count "$milestone" --clean --no-fail-exit || return 1
  require_json_contract "stable_index_${milestone}" \
    "$out/stable_index/stable_touchstone_index_summary.json" status PASS indexed_count "$milestone" || return 1

  run_logged "response_features_${milestone}" \
    "$PY" "$PROJECT/scripts/extract_touchstone_response_features.py" "$out/stable_index" \
    --out-dir "$out/response_features" \
    --expected-ports 4 \
    --expected-frequency-start-ghz 5 \
    --expected-frequency-stop-ghz 60 \
    --expected-frequency-step-ghz 0.5 \
    --expected-frequency-points 111 \
    --target-frequency-ghz 15 \
    --no-fail-exit || return 1
  require_json_contract "response_features_${milestone}" \
    "$out/response_features/response_feature_extraction_summary.json" overall_status PASS counts.ok_rows "$milestone" || return 1

  run_logged "enrich_geometry_${milestone}" \
    "$PY" "$PROJECT/scripts/enrich_response_features_with_geometry.py" \
    --features-csv "$out/response_features/response_features.csv" \
    --out-dir "$out/enriched_geometry" \
    --q-definition min \
    --no-fail-exit || return 1
  require_json_contract "enrich_geometry_${milestone}" \
    "$out/enriched_geometry/geometry_enrichment_manifest.json" overall_status PASS || return 1

  run_logged "new_training_table_${milestone}" \
    "$PY" "$PROJECT/scripts/build_physical_feature_inverse_training_table.py" "$out/enriched_geometry" \
    --out-dir "$out/new_training_table" \
    --geometry-columns "$GEOMETRY_COLUMNS" \
    --input-prefix input__ \
    --check-touchstone-exists \
    --no-fail-exit || return 1
  require_json_contract "new_training_table_${milestone}" \
    "$out/new_training_table/physical_feature_inverse_training_manifest.json" overall_status PASS || return 1

  run_logged "accepted_pool_${milestone}" \
    "$PY" "$PROJECT/scripts/merge_physical_feature_accepted_pool.py" \
    --base-pool-dir "$SOURCE_POOL" \
    --training-csv "$out/new_training_table/physical_feature_inverse_training_table.csv" \
    --out-dir "$out/accepted_pool" \
    --min-row-count 1 \
    --lp-min-nh 0.5 --lp-max-nh 3.0 \
    --ls-min-nh 0.5 --ls-max-nh 3.0 \
    --q-min 5 --q-max 25 \
    --k-min 0 --k-max 0.8 \
    --bins 10 --pair-bins 10 --four-d-bins 4 \
    --min-four-d-occupied-frac 0.50 \
    --run-uniformity --require-plots --no-fail-exit || return 1
  require_json_contract "accepted_pool_${milestone}" \
    "$out/accepted_pool/accepted_pool_merge_summary.json" overall_status PASS || return 1

  write_latest_status "$milestone" "$out"
  touch "$done"
  log "milestone=$milestone audit complete latest=$LATEST"

  local accepted
  accepted="$($PY - "$LATEST" <<'PY'
import json, pathlib, sys
print(int(json.loads(pathlib.Path(sys.argv[1]).read_text())["accepted_combined_count"]))
PY
)"
  if [[ "$accepted" -ge "$TARGET_ACCEPTED" ]]; then
    build_model_checkpoint "$milestone" "$out"
  fi
}

log "watcher start production=$PRODUCTION_DIR target_accepted=$TARGET_ACCEPTED"
while :; do
  raw_count="$( { find "$PRODUCTION_DIR/dataset" -type f -name '*.s4p' -size +0c 2>/dev/null || true; } | wc -l | tr -d ' ')"
  for milestone in "${MILESTONES[@]}"; do
    if [[ "$raw_count" -ge "$milestone" ]]; then
      audit_milestone "$milestone" || log "milestone=$milestone audit failed; will retry"
    fi
  done
  if [[ "$raw_count" -ge 100000 ]]; then
    log "raw production reached 100000; watcher complete"
    break
  fi
  sleep "$POLL_SECONDS"
done
