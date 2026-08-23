#!/usr/bin/env bash
# Build one cumulative accepted-data model checkpoint from real EMX labels.
#
# A model test is always produced when an exact geometry-unique checkpoint can
# be selected.  Formal checkpoint PASS additionally requires the strict
# Lp/Ls/Q/|K| uniformity gate; model completion never hides a uniformity FAIL.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
ACCEPTED_POOL_DIR=""
OUT_DIR=""
GEOMETRY_CONFIG="${GEOMETRY_CONFIG:-$REPO_ROOT/configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml}"
CHECKPOINT_COUNT=""
CHECKPOINT_INDEX=""
SEED="${SEED:-20260710}"
MODEL_SEED="${MODEL_SEED:-20260711}"
SPLIT_SEED="${MODEL_SPLIT_SEED:-20260711}"
MAX_CANDIDATES="${MAX_CANDIDATES:-8}"
MAX_EPOCHS="${MAX_EPOCHS:-120}"
PATIENCE="${PATIENCE:-15}"
NN_THREADS="${NN_THREADS:-8}"
RIDGE_TEST_ROWS="${RIDGE_TEST_ROWS:-20000}"
TANDEM_DEPTH="${TANDEM_DEPTH:-2}"
TANDEM_WIDTH="${TANDEM_WIDTH:-128}"
TANDEM_BATCH_SIZE="${TANDEM_BATCH_SIZE:-4096}"
TOPOLOGY_FEASIBILITY_WEIGHT="${TOPOLOGY_FEASIBILITY_WEIGHT:-0.02}"
LOCAL_REFINEMENT_STEPS="${LOCAL_REFINEMENT_STEPS:-40}"
LOCAL_REFINEMENT_STARTS="${LOCAL_REFINEMENT_STARTS:-4}"
LOCAL_REFINEMENT_LEARNING_RATE="${LOCAL_REFINEMENT_LEARNING_RATE:-0.05}"
LOCAL_REFINEMENT_JITTER="${LOCAL_REFINEMENT_JITTER:-0.05}"
REQUIRE_UNIFORMITY_PASS=1
NO_FAIL_EXIT=0

GEOMETRY_COLUMNS="geom__primary_outer_width_um,geom__primary_outer_height_um,geom__secondary_outer_width_um,geom__secondary_outer_height_um,geom__line_width_um,geom__primary_terminal_y_span_um,geom__secondary_terminal_y_span_um,geom__offset_um,geom__primary_feed_extension_um,geom__secondary_feed_extension_um"
INPUT_COLUMNS="input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center"

usage() {
  cat <<'USAGE'
Usage:
  run_accepted_physical_feature_model_checkpoint.sh \
    --accepted-pool-dir DIR --out-dir DIR --geometry-config CONFIG.yaml \
    --checkpoint-count 100000 --checkpoint-index 1 \
    [--topology-feasibility-weight 0.02] [options]

The accepted pool must contain dataset_rows.csv with real EMX-derived labels.
The script writes an exact balanced dataset, strict uniformity evidence, Ridge,
direct-MLP, and physical-cell-held-out tandem tests, figures, and a manifest
that distinguishes PASS from PROVISIONAL_UNIFORMITY_FAIL.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --accepted-pool-dir) ACCEPTED_POOL_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --geometry-config) GEOMETRY_CONFIG="$2"; shift 2 ;;
    --checkpoint-count) CHECKPOINT_COUNT="$2"; shift 2 ;;
    --checkpoint-index) CHECKPOINT_INDEX="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    --model-seed) MODEL_SEED="$2"; shift 2 ;;
    --split-seed) SPLIT_SEED="$2"; shift 2 ;;
    --max-candidates) MAX_CANDIDATES="$2"; shift 2 ;;
    --max-epochs) MAX_EPOCHS="$2"; shift 2 ;;
    --patience) PATIENCE="$2"; shift 2 ;;
    --nn-threads|--model-threads) NN_THREADS="$2"; shift 2 ;;
    --ridge-test-rows) RIDGE_TEST_ROWS="$2"; shift 2 ;;
    --topology-feasibility-weight) TOPOLOGY_FEASIBILITY_WEIGHT="$2"; shift 2 ;;
    --allow-provisional-uniformity) REQUIRE_UNIFORMITY_PASS=0; shift ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --no-fail-exit) NO_FAIL_EXIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$ACCEPTED_POOL_DIR" || -z "$OUT_DIR" || -z "$CHECKPOINT_COUNT" || -z "$CHECKPOINT_INDEX" ]]; then
  echo "ERROR: accepted pool, output, checkpoint count, and checkpoint index are required." >&2
  usage >&2
  exit 2
fi
if ! [[ "$CHECKPOINT_COUNT" =~ ^[0-9]+$ ]] || [[ "$CHECKPOINT_COUNT" -lt 10 ]]; then
  echo "ERROR: --checkpoint-count must be an integer of at least 10." >&2
  exit 2
fi
if ! [[ "$CHECKPOINT_INDEX" =~ ^[0-9]+$ ]] || [[ "$CHECKPOINT_INDEX" -lt 1 ]] || [[ "$CHECKPOINT_INDEX" -gt 10 ]]; then
  echo "ERROR: --checkpoint-index must be 1..10." >&2
  exit 2
fi
# Every 100k checkpoint needs the complete held-out prediction set for the
# physical-cell tail audit.  The 100k anchor ablation uses the same complete
# rows for its paired cluster bootstrap.
TANDEM_MAX_PREDICTION_ROWS="$CHECKPOINT_COUNT"

ACCEPTED_POOL_DIR="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$ACCEPTED_POOL_DIR")"
OUT_DIR="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$OUT_DIR")"
GEOMETRY_CONFIG="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$GEOMETRY_CONFIG")"
SOURCE_CSV="$ACCEPTED_POOL_DIR/dataset_rows.csv"
if [[ ! -f "$SOURCE_CSV" ]]; then
  echo "ERROR: missing accepted-pool dataset_rows.csv: $SOURCE_CSV" >&2
  exit 2
fi
if [[ ! -f "$GEOMETRY_CONFIG" ]]; then
  echo "ERROR: missing production geometry config: $GEOMETRY_CONFIG" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
LOCK_DIR="$OUT_DIR/active.lock"
MODEL_MARKER="$OUT_DIR/model_test.complete"
FORMAL_MARKER="$OUT_DIR/formal_checkpoint.pass"
MANIFEST="$OUT_DIR/accepted_physical_feature_model_checkpoint_manifest.json"
LOG="$OUT_DIR/accepted_physical_feature_model_checkpoint.log"
if [[ -f "$MODEL_MARKER" && -f "$MANIFEST" ]]; then
  exit 0
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  old_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "ERROR: checkpoint lock is active: $LOCK_DIR pid=$old_pid" >&2
    exit 2
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR" || exit 2
fi
echo "$$" > "$LOCK_DIR/pid"
cleanup() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

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

json_contract() {
  "$PYTHON_BIN" - "$1" "$2" "$3" "$4" <<'PY'
import json,math,pathlib,sys
path=pathlib.Path(sys.argv[1]); field=sys.argv[2]; expected=sys.argv[3]; minimum=int(sys.argv[4])
if not path.is_file(): raise SystemExit("missing JSON: {}".format(path))
data=json.loads(path.read_text(encoding="utf-8"))
value=data
for part in field.split("."):
    value=value[part]
if expected and str(value) != expected:
    raise SystemExit("{}={!r}, expected={!r}".format(field,value,expected))
if minimum and int(value) < minimum:
    raise SystemExit("{}={}, minimum={}".format(field,value,minimum))
PY
}

BALANCED="$OUT_DIR/balanced_dataset"
UNIFORMITY="$OUT_DIR/uniformity"
TRAINING="$OUT_DIR/training_table"
GROUND_RING_SPACING="$OUT_DIR/ground_ring_spacing_audit"
ABLATION_READINESS="$OUT_DIR/input_ablation_readiness"
BROADBAND_READINESS="$OUT_DIR/broadband_sparameter_readiness"
BROADBAND_BASELINE="$OUT_DIR/broadband_sparameter_pca_baseline"
RIDGE="$OUT_DIR/ridge_baseline"
NN_PLAN="$OUT_DIR/nn_plan"
NN_TRAIN="$OUT_DIR/nn_training"
NN_FIGURES="$OUT_DIR/nn_figures"
TANDEM_OOD="$OUT_DIR/tandem_physical_cell_ood"
PHYSICAL_CELL_TAIL="$OUT_DIR/physical_cell_model_tail_error"
TANDEM_FEASIBILITY="$OUT_DIR/tandem_predicted_geometry_feasibility"
TANDEM_RESPONSE_ONLY="$OUT_DIR/tandem_response_only_physical_cell_ood"
TANDEM_ANCHOR_ABLATION="$OUT_DIR/tandem_geometry_anchor_ablation"
BNI_TEMPERATURE_SELECTION="$OUT_DIR/balanced_mse_bni_temperature_selection"
TANDEM_BNI="$OUT_DIR/tandem_balanced_mse_bni_physical_cell_ood"
BNI_ABLATION="$OUT_DIR/balanced_mse_bni_ablation"
QP_QS_TRAINING="$OUT_DIR/training_table_qp_qs_ablation"
QP_QS_TANDEM="$OUT_DIR/tandem_qp_qs_physical_cell_ood"
Q_INPUT_ABLATION="$OUT_DIR/q_input_ablation"
BROADBAND_QMIN_EXPANDER="$OUT_DIR/broadband_physical_spec_qmin_pca"
BROADBAND_QP_QS_EXPANDER="$OUT_DIR/broadband_physical_spec_qp_qs_pca"
FREQUENCY_STABILITY="$OUT_DIR/physical_feature_frequency_stability"
GEOMETRY_SENSITIVITY="$OUT_DIR/geometry_response_effective_dimension"
FREQUENCY_SELF_TRANSFER="$OUT_DIR/frequency_domain_self_transfer"
FREQUENCY_SEQUENCE_ARCHITECTURES="$OUT_DIR/frequency_sequence_architecture_benchmark"
FREQUENCY_RESOLUTION_ABLATION="$OUT_DIR/cross_frequency_resolution"
GEOMETRY_MULTIPLICITY="$OUT_DIR/inverse_geometry_multiplicity"
CONFORMAL_CALIBRATION="$OUT_DIR/physical_feature_conformal_calibration"
MONDRIAN_CONFORMAL="$OUT_DIR/physical_feature_mondrian_conformal_comparison"
LOW_FREQUENCY_PHYSICS="$OUT_DIR/low_frequency_coupled_rl_consistency"
LOCAL_REFINEMENT="$OUT_DIR/tandem_local_refinement"
BOUNDARY_OOD_STRESS="$OUT_DIR/physical_feature_boundary_ood_stress"
PHYSICAL_SPEC_SPECTRAL_EXPANDER="$OUT_DIR/physical_spec_spectral_expander_pca"

log "checkpoint start index=$CHECKPOINT_INDEX count=$CHECKPOINT_COUNT source=$SOURCE_CSV"

run_logged select_balanced_real_checkpoint \
  "$PYTHON_BIN" "$SCRIPT_DIR/select_balanced_physical_feature_checkpoint.py" \
  --input-csv "$SOURCE_CSV" --out-dir "$BALANCED" \
  --target-count "$CHECKPOINT_COUNT" --seed "$SEED" \
  --four-d-bins 4 --min-four-d-occupied-fraction 0.50 \
  --lp-min-nh 0.5 --lp-max-nh 3.0 \
  --ls-min-nh 0.5 --ls-max-nh 3.0 \
  --q-min 5 --q-max 25 --k-min 0 --k-max 0.8 \
  --check-touchstone-exists --compute-input-sha256 --no-fail-exit || exit 2
json_contract "$BALANCED/balanced_physical_feature_checkpoint_summary.json" selected_count "" "$CHECKPOINT_COUNT" || exit 2
json_contract "$BALANCED/balanced_physical_feature_checkpoint_summary.json" checks.selected_count_exact True 0 || exit 2
json_contract "$BALANCED/balanced_physical_feature_checkpoint_summary.json" checks.selected_geometry_unique True 0 || exit 2

run_logged strict_physical_feature_uniformity \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_physical_feature_uniformity.py" \
  --training-csv "$BALANCED/dataset_rows.csv" --out-dir "$UNIFORMITY" \
  --min-valid-count "$CHECKPOINT_COUNT" --bins 10 --pair-bins 10 --four-d-bins 4 \
  --min-1d-occupied-frac 0.90 --min-1d-entropy-frac 0.90 \
  --max-1d-bin-imbalance 2.50 --min-pair-occupied-frac 0.65 \
  --min-pair-entropy-frac 0.80 --min-four-d-occupied-frac 0.50 \
  --min-four-d-entropy-frac 0.80 --max-four-d-bin-imbalance 4.0 \
  --k-mode magnitude --lp-min-nh 0.5 --lp-max-nh 3.0 \
  --ls-min-nh 0.5 --ls-max-nh 3.0 --q-min 5 --q-max 25 \
  --k-min 0 --k-max 0.8 --require-explicit-ranges \
  --require-four-d-gate --require-plots --no-fail-exit || exit 2
json_contract "$UNIFORMITY/physical_feature_uniformity_manifest.json" overall_status PASS 0 || exit 2

run_logged build_inverse_training_table \
  "$PYTHON_BIN" "$SCRIPT_DIR/build_physical_feature_inverse_training_table.py" "$BALANCED" \
  --out-dir "$TRAINING" --feature-columns lp_nh_center,ls_nh_center,q_center,k_abs_center \
  --geometry-columns "$GEOMETRY_COLUMNS" --input-prefix input__ \
  --check-touchstone-exists --no-fail-exit || exit 2
json_contract "$TRAINING/physical_feature_inverse_training_manifest.json" training_count "" "$CHECKPOINT_COUNT" || exit 2

run_logged audit_size_normalized_ground_ring_spacing \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_transformer_ground_ring_spacing.py" \
  --training-csv "$BALANCED/dataset_rows.csv" --out-dir "$GROUND_RING_SPACING" \
  --min-rows "$CHECKPOINT_COUNT" --recommended-margin-to-diameter-ratio 0.3333333333333333 \
  --recover-margin-from-evaluation-metadata \
  --max-below-recommended-fraction 0.0 --max-row-artifact 20000 \
  --max-plot-points 20000 --no-fail-exit || exit 2
json_contract "$GROUND_RING_SPACING/ground_ring_spacing_audit_summary.json" overall_status PASS 0 || exit 2
json_contract "$GROUND_RING_SPACING/ground_ring_spacing_audit_summary.json" artifacts.ratio_histogram_status PASS 0 || exit 2
json_contract "$GROUND_RING_SPACING/ground_ring_spacing_audit_summary.json" artifacts.response_scatter_status PASS 0 || exit 2

run_logged audit_broadband_complex_s_readiness \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_broadband_sparameter_surrogate_readiness.py" \
  --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
  --out-dir "$BROADBAND_READINESS" --min-files 64 --max-files 256 \
  --expected-ports 4 --expected-frequency-start-ghz 5 \
  --expected-frequency-stop-ghz 60 --expected-frequency-step-ghz 0.5 \
  --expected-frequency-points 111 --max-passivity-excess 0.05 \
  --max-reciprocity-error 0.02 --no-fail-exit || exit 2
json_contract "$BROADBAND_READINESS/broadband_sparameter_surrogate_readiness_summary.json" overall_status PASS 0 || exit 2

run_logged audit_qp_qs_input_ablation_readiness \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_physical_feature_input_ablation_readiness.py" \
  --dataset-csv "$BALANCED/dataset_rows.csv" --out-dir "$ABLATION_READINESS" \
  --min-rows 200000 --expected-touchstone-extension .s4p \
  --expected-frequency-start-ghz 5 --expected-frequency-stop-ghz 60 \
  --expected-frequency-step-ghz 0.5 --expected-frequency-points 111 \
  --no-fail-exit || exit 2

"$PYTHON_BIN" - "$ABLATION_READINESS/physical_feature_input_ablation_readiness_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
path=pathlib.Path(sys.argv[1]); index=int(sys.argv[2])
if not path.is_file(): raise SystemExit("missing input-ablation readiness summary")
data=json.loads(path.read_text(encoding="utf-8"))
expected="WAITING_FOR_200K" if index==1 else "PASS"
if data.get("overall_status")!=expected:
    raise SystemExit("input-ablation readiness status={!r}, expected={!r}".format(data.get("overall_status"),expected))
PY
if [[ $? -ne 0 ]]; then
  log "Qp/Qs input-ablation readiness contract failed"
  exit 2
fi

TEST_ROWS="$RIDGE_TEST_ROWS"
MAX_TEST=$((CHECKPOINT_COUNT / 5))
if [[ "$TEST_ROWS" -gt "$MAX_TEST" ]]; then TEST_ROWS="$MAX_TEST"; fi
if [[ "$TEST_ROWS" -lt 1 ]]; then TEST_ROWS=1; fi
TRAIN_ROWS=$((CHECKPOINT_COUNT - TEST_ROWS))

run_logged ridge_checkpoint \
  "$PYTHON_BIN" "$SCRIPT_DIR/run_physical_feature_inverse_checkpoint_test.py" \
  --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
  --out-dir "$RIDGE" --input-columns "$INPUT_COLUMNS" \
  --geometry-columns "$GEOMETRY_COLUMNS" --min-training-rows "$CHECKPOINT_COUNT" \
  --max-train-rows "$TRAIN_ROWS" --max-test-rows "$TEST_ROWS" \
  --seed "$SPLIT_SEED" --no-fail-exit || exit 2
json_contract "$RIDGE/physical_feature_inverse_checkpoint_test_summary.json" execution_status PASS 0 || exit 2
json_contract "$RIDGE/physical_feature_inverse_checkpoint_test_summary.json" eligible_for_model_success_claim False 0 || exit 2
json_contract "$RIDGE/physical_feature_inverse_checkpoint_test_summary.json" usable_row_count "" "$CHECKPOINT_COUNT" || exit 2

run_logged plan_nn_architecture_search \
  "$PYTHON_BIN" "$SCRIPT_DIR/plan_physical_feature_inverse_nn_architecture_search.py" \
  --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
  --out-dir "$NN_PLAN" --input-columns "$INPUT_COLUMNS" \
  --geometry-columns "$GEOMETRY_COLUMNS" --min-training-rows "$CHECKPOINT_COUNT" \
  --seeds "$MODEL_SEED" --hidden-widths 128,256 --depths 2,3 \
  --dropouts 0,0.05 --learning-rates 0.001 --weight-decays 0 \
  --batch-sizes 1024 --max-candidates "$MAX_CANDIDATES" --no-fail-exit || exit 2
json_contract "$NN_PLAN/physical_feature_inverse_nn_architecture_search_summary.json" training_count "" "$CHECKPOINT_COUNT" || exit 2

run_logged train_nn_architecture_search \
  env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" \
  "$PYTHON_BIN" "$SCRIPT_DIR/train_physical_feature_inverse_nn_architecture_search.py" \
  --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
  --candidate-csv "$NN_PLAN/physical_feature_inverse_nn_architecture_candidates.csv" \
  --out-dir "$NN_TRAIN" --input-columns "$INPUT_COLUMNS" \
  --geometry-columns "$GEOMETRY_COLUMNS" --min-training-rows "$CHECKPOINT_COUNT" \
  --max-candidates "$MAX_CANDIDATES" --max-epochs-cap "$MAX_EPOCHS" \
  --patience-cap "$PATIENCE" --split-seed "$SPLIT_SEED" \
  --split-mode physical_cell_grouped --physical-cell-bins 4 \
  --physical-cell-lower 0.5,0.5,5,0 --physical-cell-upper 3,3,25,0.8 \
  --resume-completed-candidates \
  --no-fail-exit || exit 2
json_contract "$NN_TRAIN/physical_feature_inverse_nn_architecture_search_training_summary.json" training_count "" "$CHECKPOINT_COUNT" || exit 2

run_logged build_nn_report_figures \
  "$PYTHON_BIN" "$SCRIPT_DIR/build_physical_feature_inverse_nn_report_figures.py" \
  --training-summary "$NN_TRAIN/physical_feature_inverse_nn_architecture_search_training_summary.json" \
  --out-dir "$NN_FIGURES" --no-fail-exit || exit 2
json_contract "$NN_FIGURES/physical_feature_inverse_nn_report_figures_summary.json" overall_status PASS 0 || exit 2

# The direct baselines above retain the published random-row comparison.  The
# tandem model is evaluated more strictly: complete 4-D Lp/Ls/Q/|K| cells are
# absent from training, so its test metric measures interpolation into unseen
# joint physical targets instead of memorization of neighboring rows.
run_logged train_tandem_physical_cell_ood \
  env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" MKL_NUM_THREADS="$NN_THREADS" \
  "$PYTHON_BIN" "$SCRIPT_DIR/train_physical_feature_tandem_inverse.py" \
  --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
  --out-dir "$TANDEM_OOD" --input-columns "$INPUT_COLUMNS" \
  --geometry-columns "$GEOMETRY_COLUMNS" --min-training-rows "$CHECKPOINT_COUNT" \
  --split-mode physical_cell_grouped --physical-cell-bins 4 \
  --physical-cell-lower 0.5,0.5,5,0 --physical-cell-upper 3,3,25,0.8 \
  --forward-depth "$TANDEM_DEPTH" --forward-width "$TANDEM_WIDTH" \
  --inverse-depth "$TANDEM_DEPTH" --inverse-width "$TANDEM_WIDTH" \
  --batch-size "$TANDEM_BATCH_SIZE" --forward-epochs "$MAX_EPOCHS" \
  --inverse-epochs "$MAX_EPOCHS" --patience "$PATIENCE" --seed "$MODEL_SEED" \
  --max-prediction-rows "$TANDEM_MAX_PREDICTION_ROWS" \
  --split-seed "$SPLIT_SEED" \
  --response-loss-scaling declared_range \
  --topology-feasibility-weight "$TOPOLOGY_FEASIBILITY_WEIGHT" \
  --response-weight-schedule warmup_ramp_adaptive_ema \
  --response-warmup-fraction 0.05 --response-ramp-fraction 0.20 \
  --local-refinement-steps "$LOCAL_REFINEMENT_STEPS" \
  --local-refinement-starts "$LOCAL_REFINEMENT_STARTS" \
  --local-refinement-learning-rate "$LOCAL_REFINEMENT_LEARNING_RATE" \
  --local-refinement-jitter "$LOCAL_REFINEMENT_JITTER" \
  --no-fail-exit || exit 2

"$PYTHON_BIN" - "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" "$CHECKPOINT_COUNT" <<'PY'
import json,math,pathlib,sys
path=pathlib.Path(sys.argv[1]); minimum=int(sys.argv[2])
if not path.is_file(): raise SystemExit("missing tandem summary: {}".format(path))
data=json.loads(path.read_text(encoding="utf-8")); audit=data.get("split_audit") or {}; metrics=data.get("metrics") or {}
loss_contract=data.get("response_loss_contract") or {}; method=data.get("method") or {}
range_rmse=((metrics.get("tandem_inverse") or {}).get("test_response_range_normalized_rmse"))
checks={
    "training_count":int(data.get("training_count") or 0)>=minimum,
    "review_status_is_truthful":data.get("overall_status")=="COMPLETE_REVIEW_REQUIRED",
    "split_mode":audit.get("split_mode")=="physical_cell_grouped",
    "no_cell_overlap":int(audit.get("physical_cell_overlap_count") or 0)==0,
    "all_rows_assigned_once":audit.get("all_rows_assigned_once") is True,
    "no_out_of_range_rows":int(audit.get("out_of_range_row_count_before_clipping") or 0)==0,
    "stable_cell_partition":audit.get("physical_cell_partition_stable_for_existing_cells") is True,
    "fixed_range_metric":range_rmse is not None and math.isfinite(float(range_rmse)),
    "declared_range_balanced_loss":loss_contract.get("scaling")=="declared_range",
    "adaptive_picc_schedule":method.get("response_weight_schedule")=="warmup_ramp_adaptive_ema",
    "label_free_topology_feasibility":method.get("topology_feasibility_is_label_free") is True,
    "topology_feasibility_enabled":float(method.get("topology_feasibility_weight") or 0.0)>0.0,
    "topology_feasibility_columns_available":((method.get("topology_feasibility_contract") or {}).get("available") is True),
}
if not all(checks.values()):
    raise SystemExit("tandem OOD contract failed: {}".format(checks))
PY
if [[ $? -ne 0 ]]; then
  log "tandem physical-cell OOD contract failed"
  exit 2
fi

# A checkpoint-wide mean can hide a sparse held-out physical cell with much
# larger error.  Recompute metrics from every held-out row and publish both
# row-weighted and equal-cell tail evidence without inventing an accuracy gate.
run_logged audit_physical_cell_model_tail_error \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_physical_cell_model_tail_error.py" \
  --model-summary "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" \
  --predictions-csv "$TANDEM_OOD/physical_feature_tandem_inverse_test_predictions.csv" \
  --out-dir "$PHYSICAL_CELL_TAIL" --minimum-test-rows 1000 --minimum-test-cells 8 \
  --no-fail-exit || exit 2
json_contract "$PHYSICAL_CELL_TAIL/physical_cell_model_tail_error_summary.json" overall_status PASS 0 || exit 2
json_contract "$PHYSICAL_CELL_TAIL/physical_cell_model_tail_error_summary.json" test_row_count "" 1000 || exit 2
json_contract "$PHYSICAL_CELL_TAIL/physical_cell_model_tail_error_summary.json" test_physical_cell_count "" 8 || exit 2
json_contract "$PHYSICAL_CELL_TAIL/physical_cell_model_tail_error_summary.json" artifacts.plot_status PASS 0 || exit 2

# Per-dimension sigmoid bounds do not prove that the combined geometry is
# buildable. Rebuild the anchored tandem predictions with the exact production
# config and require the analytical topology plus TSMC65 top-metal gates before
# recording a model checkpoint.
run_logged audit_tandem_predicted_geometry_feasibility \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_tandem_predicted_geometry_feasibility.py" \
  --predictions-csv "$TANDEM_OOD/physical_feature_tandem_inverse_test_predictions.csv" \
  --config "$GEOMETRY_CONFIG" --out-dir "$TANDEM_FEASIBILITY" \
  --min-rows 1000 --no-fail-exit || exit 2
json_contract "$TANDEM_FEASIBILITY/tandem_predicted_geometry_feasibility_summary.json" overall_status PASS 0 || exit 2
json_contract "$TANDEM_FEASIBILITY/tandem_predicted_geometry_feasibility_summary.json" valid_fraction 1.0 0 || exit 2
json_contract "$TANDEM_FEASIBILITY/tandem_predicted_geometry_feasibility_summary.json" valid_count "" 1000 || exit 2

mkdir -p "$TANDEM_ANCHOR_ABLATION"
if [[ "$CHECKPOINT_INDEX" -eq 1 ]]; then
  # The original tandem paper uses no paired-geometry label in the inverse
  # objective. This same-split arm removes geometry-label anchoring while both
  # arms retain the same label-free topology constraint; a comparison artifact
  # decides whether real-EM closure is worth running before any future change.
  run_logged train_tandem_response_only_physical_cell_ood_at_100k \
    env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" MKL_NUM_THREADS="$NN_THREADS" \
    "$PYTHON_BIN" "$SCRIPT_DIR/train_physical_feature_tandem_inverse.py" \
    --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
    --out-dir "$TANDEM_RESPONSE_ONLY" --input-columns "$INPUT_COLUMNS" \
    --geometry-columns "$GEOMETRY_COLUMNS" --min-training-rows "$CHECKPOINT_COUNT" \
    --split-mode physical_cell_grouped --physical-cell-bins 4 \
    --physical-cell-lower 0.5,0.5,5,0 --physical-cell-upper 3,3,25,0.8 \
    --forward-depth "$TANDEM_DEPTH" --forward-width "$TANDEM_WIDTH" \
    --inverse-depth "$TANDEM_DEPTH" --inverse-width "$TANDEM_WIDTH" \
    --batch-size "$TANDEM_BATCH_SIZE" --forward-epochs "$MAX_EPOCHS" \
    --inverse-epochs "$MAX_EPOCHS" --patience "$PATIENCE" --seed "$MODEL_SEED" \
    --max-prediction-rows "$TANDEM_MAX_PREDICTION_ROWS" --split-seed "$SPLIT_SEED" \
    --geometry-anchor-weight 0 \
    --response-loss-scaling declared_range \
    --topology-feasibility-weight "$TOPOLOGY_FEASIBILITY_WEIGHT" \
    --response-weight-schedule warmup_ramp_adaptive_ema \
    --response-warmup-fraction 0 --response-ramp-fraction 0.20 \
    --local-refinement-steps "$LOCAL_REFINEMENT_STEPS" \
    --local-refinement-starts "$LOCAL_REFINEMENT_STARTS" \
    --local-refinement-learning-rate "$LOCAL_REFINEMENT_LEARNING_RATE" \
    --local-refinement-jitter "$LOCAL_REFINEMENT_JITTER" \
    --no-fail-exit || exit 2

  run_logged compare_tandem_geometry_anchor_ablation_at_100k \
    "$PYTHON_BIN" "$SCRIPT_DIR/compare_tandem_geometry_anchor_ablation.py" \
    --anchored-summary "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" \
    --response-only-summary "$TANDEM_RESPONSE_ONLY/physical_feature_tandem_inverse_summary.json" \
    --anchored-predictions "$TANDEM_OOD/physical_feature_tandem_inverse_test_predictions.csv" \
    --response-only-predictions "$TANDEM_RESPONSE_ONLY/physical_feature_tandem_inverse_test_predictions.csv" \
    --require-paired-bootstrap --bootstrap-replicates 2000 --bootstrap-confidence 0.95 \
    --bootstrap-seed "$SPLIT_SEED" --physical-cell-bins 4 \
    --physical-cell-lower 0.5,0.5,5,0 --physical-cell-upper 3,3,25,0.8 \
    --minimum-paired-test-rows 1000 --minimum-paired-test-cells 8 \
    --out-dir "$TANDEM_ANCHOR_ABLATION" --minimum-material-improvement 0.05 \
    --no-fail-exit || exit 2
  json_contract "$TANDEM_ANCHOR_ABLATION/tandem_geometry_anchor_ablation_summary.json" overall_status PASS 0 || exit 2
  json_contract "$TANDEM_ANCHOR_ABLATION/tandem_geometry_anchor_ablation_summary.json" paired_cluster_bootstrap.status PASS 0 || exit 2
  json_contract "$TANDEM_ANCHOR_ABLATION/tandem_geometry_anchor_ablation_summary.json" decision_rule paired_cluster_bootstrap_row_and_cell_balanced_ci_lower_ge_material_improvement 0 || exit 2
else
  "$PYTHON_BIN" - "$TANDEM_ANCHOR_ABLATION/tandem_geometry_anchor_ablation_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"NOT_REPEATED_AFTER_100K",
 "decision":"USE_RECORDED_100K_GEOMETRY_ANCHOR_ABLATION",
 "checkpoint_index":index,
 "scientific_boundary":"The response-only tandem ablation is run once at 100k on the shared physical-cell OOD split. It never changes the active model without DRC and real-EM closure.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

mkdir -p "$Q_INPUT_ABLATION"
mkdir -p "$GEOMETRY_SENSITIVITY"
if [[ "$CHECKPOINT_INDEX" -eq 3 ]]; then
  run_logged audit_geometry_response_effective_dimension_at_300k \
    "$PYTHON_BIN" "$SCRIPT_DIR/audit_geometry_response_effective_dimension.py" \
    --tandem-summary "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" \
    --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
    --out-dir "$GEOMETRY_SENSITIVITY" --min-source-rows "$CHECKPOINT_COUNT" \
    --min-sample-rows 2048 --max-sample-rows 8192 \
    --permutation-rows 2048 --permutation-repeats 3 \
    --jacobian-batch-size 512 --seed "$MODEL_SEED" \
    --max-forward-test-normalized-rmse 1.0 --no-fail-exit || exit 2
  json_contract "$GEOMETRY_SENSITIVITY/geometry_response_effective_dimension_summary.json" overall_status PASS 0 || exit 2
else
  "$PYTHON_BIN" - "$GEOMETRY_SENSITIVITY/geometry_response_effective_dimension_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_300K" if index<3 else "NOT_REPEATED_AFTER_300K",
 "decision":"RUN_GEOMETRY_SENSITIVITY_AT_300K" if index<3 else "USE_RECORDED_300K_GEOMETRY_SENSITIVITY_EVIDENCE",
 "checkpoint_index":index,
 "scientific_boundary":"Geometry sensitivity and effective dimension are audited once at 300k. No production variable is removed without a shared-split retraining and real-EMX closure ablation.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

mkdir -p "$FREQUENCY_SELF_TRANSFER"
if [[ "$CHECKPOINT_INDEX" -eq 3 ]]; then
  run_logged benchmark_frequency_domain_self_transfer_at_300k \
    env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" MKL_NUM_THREADS="$NN_THREADS" \
    "$PYTHON_BIN" "$SCRIPT_DIR/benchmark_frequency_domain_self_transfer.py" \
    --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
    --training-manifest "$TRAINING/physical_feature_inverse_training_manifest.json" \
    --out-dir "$FREQUENCY_SELF_TRANSFER" --geometry-columns "$GEOMETRY_COLUMNS" \
    --split-reference-columns "$INPUT_COLUMNS" \
    --min-rows 5000 --max-rows 5000 --band-count 10 \
    --transfer-iterations 2 --epochs-per-session 2 \
    --hidden-depth 2 --hidden-width 64 --batch-size 512 \
    --learning-rate 0.001 --weight-decay 0.000001 \
    --seed "$MODEL_SEED" --split-seed "$SPLIT_SEED" --physical-cell-bins 4 \
    --expected-ports 4 --expected-frequency-start-ghz 5 \
    --expected-frequency-stop-ghz 60 --expected-frequency-step-ghz 0.5 \
    --expected-frequency-points 111 --target-frequency-ghz 15 \
    --input-reciprocity-audit-tolerance 0.001 --input-passivity-audit-tolerance 0.001 \
    --max-input-reciprocity-error 0.02 --max-input-passivity-excess 0.05 \
    --minimum-material-improvement 0.05 --minimum-physical-improvement 0.0 \
    --max-frequency-regression-fraction 0.20 --frequency-regression-tolerance 0.05 \
    --max-passivity-correction-increase 0.01 \
    --max-candidate-test-complex-rmse 0.05 --max-candidate-raw-passivity-excess 0.05 \
    --no-fail-exit || exit 2
  json_contract "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" overall_status COMPLETE_REVIEW_REQUIRED 0 || exit 2
  json_contract "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" equal_budget_contract.equal_updates_per_band True 0 || exit 2
  json_contract "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" checks.raw_input_reciprocity_threshold_pass True 0 || exit 2
  json_contract "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" checks.raw_input_passivity_threshold_pass True 0 || exit 2
  json_contract "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" checks.frequency_bands_are_monotonic_contiguous True 0 || exit 2
  json_contract "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" checks.same_initial_weights_per_band True 0 || exit 2
  json_contract "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" checks.physical_metric_extraction_available True 0 || exit 2
  json_contract "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" equal_budget_contract.test_set_used_for_training False 0 || exit 2
  json_contract "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" equal_budget_contract.test_set_used_for_checkpoint_selection False 0 || exit 2
else
  "$PYTHON_BIN" - "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_300K" if index<3 else "NOT_REPEATED_AFTER_300K",
 "decision":"RUN_EQUAL_BUDGET_FREQUENCY_SELF_TRANSFER_AT_300K" if index<3 else "USE_RECORDED_300K_SELF_TRANSFER_EVIDENCE",
 "checkpoint_index":index,
 "scientific_boundary":"Frequency-domain self-transfer is compared once at 300k against an equal-budget independent-band model. No favorable proxy result changes the inverse model or EMX queue without real-EMX inverse closure.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

mkdir -p "$FREQUENCY_SEQUENCE_ARCHITECTURES"
if [[ "$CHECKPOINT_INDEX" -eq 3 ]]; then
  run_logged benchmark_frequency_sequence_architectures_at_300k \
    env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" MKL_NUM_THREADS="$NN_THREADS" \
    "$PYTHON_BIN" "$SCRIPT_DIR/benchmark_frequency_sequence_architectures.py" \
    --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
    --training-manifest "$TRAINING/physical_feature_inverse_training_manifest.json" \
    --out-dir "$FREQUENCY_SEQUENCE_ARCHITECTURES" --geometry-columns "$GEOMETRY_COLUMNS" \
    --split-reference-columns "$INPUT_COLUMNS" \
    --min-rows 10000 --max-rows 10000 --epochs 12 --batch-size 256 \
    --gru-hidden-width 32 --mlp-hidden-depth 2 \
    --learning-rate 0.001 --weight-decay 0.000001 --gradient-clip 5 \
    --model-seeds "$MODEL_SEED,$((MODEL_SEED + 1)),$((MODEL_SEED + 2))" \
    --split-seed "$SPLIT_SEED" --physical-cell-bins 4 \
    --expected-ports 4 --expected-frequency-start-ghz 5 \
    --expected-frequency-stop-ghz 60 --expected-frequency-step-ghz 0.5 \
    --expected-frequency-points 111 --target-frequency-ghz 15 \
    --input-reciprocity-audit-tolerance 0.001 --input-passivity-audit-tolerance 0.001 \
    --max-input-reciprocity-error 0.02 --max-input-passivity-excess 0.05 \
    --max-parameter-count-ratio 1.10 --minimum-material-improvement 0.03 \
    --max-frequency-regression-fraction 0.20 --frequency-regression-tolerance 0.05 \
    --max-passivity-correction-increase 0.01 \
    --max-candidate-test-complex-rmse 0.05 --max-candidate-raw-passivity-excess 0.05 \
    --no-fail-exit || exit 2
  json_contract "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" overall_status COMPLETE_REVIEW_REQUIRED 0 || exit 2
  json_contract "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" checks.physical_cell_overlap_zero True 0 || exit 2
  json_contract "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" checks.equal_optimizer_updates_per_seed True 0 || exit 2
  json_contract "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" checks.parameter_budget_ratio_within_limit True 0 || exit 2
  json_contract "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" checks.raw_input_reciprocity_threshold_pass True 0 || exit 2
  json_contract "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" checks.raw_input_passivity_threshold_pass True 0 || exit 2
  json_contract "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" checks.full_frequency_sequence_used True 0 || exit 2
  json_contract "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" checks.physical_metric_extraction_available True 0 || exit 2
  json_contract "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" equal_budget_contract.test_set_used_for_selection False 0 || exit 2
  json_contract "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" artifacts.plot_status PASS 0 || exit 2
else
  "$PYTHON_BIN" - "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_300K" if index<3 else "NOT_REPEATED_AFTER_300K",
 "decision":"RUN_EQUAL_BUDGET_MLP_VS_GRU_AT_300K" if index<3 else "USE_RECORDED_300K_SEQUENCE_ARCHITECTURE_EVIDENCE",
 "checkpoint_index":index,
 "scientific_boundary":"The pointwise-MLP versus GRU benchmark is run once at 300k on the same real S4P rows and physical-cell OOD split with matched optimizer updates and parameter budget. It can nominate only a frozen-forward inverse ablation and cannot alter the EMX queue without DRC, real-EMX closure, and sampled HFSS correlation.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

mkdir -p "$FREQUENCY_RESOLUTION_ABLATION"
if [[ "$CHECKPOINT_INDEX" -eq 3 ]]; then
  run_logged benchmark_cross_frequency_resolution_at_300k \
    env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" MKL_NUM_THREADS="$NN_THREADS" \
    "$PYTHON_BIN" "$SCRIPT_DIR/benchmark_cross_frequency_resolution.py" \
    --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
    --training-manifest "$TRAINING/physical_feature_inverse_training_manifest.json" \
    --out-dir "$FREQUENCY_RESOLUTION_ABLATION" --geometry-columns "$GEOMETRY_COLUMNS" \
    --split-reference-columns "$INPUT_COLUMNS" \
    --min-rows 5000 --max-rows 5000 --sparse-frequency-stride 2 \
    --optimizer-updates 512 --hidden-depth 2 --hidden-width 64 --batch-size 512 \
    --learning-rate 0.001 --weight-decay 0.000001 \
    --seed "$MODEL_SEED" --split-seed "$SPLIT_SEED" --physical-cell-bins 4 \
    --expected-ports 4 --expected-frequency-start-ghz 5 \
    --expected-frequency-stop-ghz 60 --expected-frequency-step-ghz 0.5 \
    --expected-frequency-points 111 --target-frequency-ghz 15 \
    --max-input-reciprocity-error 0.02 --max-input-passivity-excess 0.05 \
    --maximum-held-out-relative-degradation 0.15 \
    --max-held-out-regression-fraction 0.25 --frequency-regression-tolerance 0.05 \
    --no-fail-exit || exit 2
  json_contract "$FREQUENCY_RESOLUTION_ABLATION/cross_frequency_resolution_summary.json" overall_status COMPLETE_REVIEW_REQUIRED 0 || exit 2
  json_contract "$FREQUENCY_RESOLUTION_ABLATION/cross_frequency_resolution_summary.json" equal_budget_contract.equal_optimizer_updates True 0 || exit 2
  json_contract "$FREQUENCY_RESOLUTION_ABLATION/cross_frequency_resolution_summary.json" checks.sparse_training_excludes_all_held_out_frequencies True 0 || exit 2
  json_contract "$FREQUENCY_RESOLUTION_ABLATION/cross_frequency_resolution_summary.json" checks.raw_input_reciprocity_threshold_pass True 0 || exit 2
  json_contract "$FREQUENCY_RESOLUTION_ABLATION/cross_frequency_resolution_summary.json" checks.raw_input_passivity_threshold_pass True 0 || exit 2
else
  "$PYTHON_BIN" - "$FREQUENCY_RESOLUTION_ABLATION/cross_frequency_resolution_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_300K" if index<3 else "NOT_REPEATED_AFTER_300K",
 "decision":"RUN_EQUAL_UPDATE_CROSS_FREQUENCY_RESOLUTION_AT_300K" if index<3 else "USE_RECORDED_300K_CROSS_RESOLUTION_EVIDENCE",
 "checkpoint_index":index,
 "scientific_boundary":"The sparse-frequency arm is evaluated once at 300k on held-out frequency coordinates with the same real rows, physical-cell split, initialization, architecture, batches, and optimizer-update count as the full-grid arm. It is not a neural-operator claim and cannot change the 0.5 GHz EMX grid without inverse and real-EM closure.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

mkdir -p "$GEOMETRY_MULTIPLICITY"
if [[ "$CHECKPOINT_INDEX" -eq 1 || "$CHECKPOINT_INDEX" -eq 5 ]]; then
  if [[ "$CHECKPOINT_INDEX" -eq 1 ]]; then
    MULTIPLICITY_LABEL="audit_inverse_one_to_many_geometry_multiplicity_at_100k_coarse"
    MULTIPLICITY_STAGE="exploratory_coarse"
    MULTIPLICITY_BINS=4
    MULTIPLICITY_MIN_CELL_ROWS=32
    MULTIPLICITY_MIN_CELLS=64
    MULTIPLICITY_MAX_CELLS=256
  else
    MULTIPLICITY_LABEL="audit_inverse_one_to_many_geometry_multiplicity_at_500k"
    MULTIPLICITY_STAGE="confirmatory_fine"
    MULTIPLICITY_BINS=10
    MULTIPLICITY_MIN_CELL_ROWS=12
    MULTIPLICITY_MIN_CELLS=128
    MULTIPLICITY_MAX_CELLS=2048
  fi
  run_logged "$MULTIPLICITY_LABEL" \
    "$PYTHON_BIN" "$SCRIPT_DIR/audit_inverse_one_to_many_geometry_multiplicity.py" \
    --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
    --out-dir "$GEOMETRY_MULTIPLICITY" --min-source-rows "$CHECKPOINT_COUNT" \
    --evidence-stage "$MULTIPLICITY_STAGE" \
    --physical-cell-bins "$MULTIPLICITY_BINS" \
    --min-cell-rows "$MULTIPLICITY_MIN_CELL_ROWS" --max-rows-per-cell 256 \
    --min-analyzed-cells "$MULTIPLICITY_MIN_CELLS" --max-analyzed-cells "$MULTIPLICITY_MAX_CELLS" \
    --seed "$MODEL_SEED" --no-fail-exit || exit 2
  json_contract "$GEOMETRY_MULTIPLICITY/inverse_geometry_multiplicity_summary.json" overall_status PASS 0 || exit 2
else
  "$PYTHON_BIN" - "$GEOMETRY_MULTIPLICITY/inverse_geometry_multiplicity_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"COARSE_100K_COMPLETE_FINE_WAITING_FOR_500K" if index<5 else "NOT_REPEATED_AFTER_500K",
 "decision":"USE_100K_COARSE_AND_RUN_FINE_AT_500K" if index<5 else "USE_RECORDED_500K_GEOMETRY_MULTIPLICITY_EVIDENCE",
 "checkpoint_index":index,
 "scientific_boundary":"A coarse one-to-many diagnostic runs at 100k and cannot authorize a generative model. The confirmatory fine-cell audit runs at 500k; neither result can replace fixed-split top-k and real-EMX ablations.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

mkdir -p "$CONFORMAL_CALIBRATION"
mkdir -p "$MONDRIAN_CONFORMAL"
if [[ "$CHECKPOINT_INDEX" -eq 6 ]]; then
  run_logged audit_physical_feature_conformal_calibration_at_600k \
    "$PYTHON_BIN" "$SCRIPT_DIR/audit_physical_feature_conformal_calibration.py" \
    --tandem-summary "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" \
    --predictions-csv "$TANDEM_OOD/physical_feature_tandem_inverse_test_predictions.csv" \
    --out-dir "$CONFORMAL_CALIBRATION" --min-source-rows "$CHECKPOINT_COUNT" \
    --min-prediction-rows 5000 --min-calibration-rows 2000 --min-evaluation-rows 2000 \
    --coverage-levels 0.90,0.95 --coverage-tolerance 0.03 \
    --seed "$MODEL_SEED" --no-fail-exit || exit 2
  json_contract "$CONFORMAL_CALIBRATION/physical_feature_conformal_calibration_summary.json" overall_status PASS 0 || exit 2
  run_logged compare_global_vs_mondrian_conformal_calibration_at_600k \
    "$PYTHON_BIN" "$SCRIPT_DIR/compare_global_vs_mondrian_conformal_calibration.py" \
    --tandem-summary "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" \
    --global-summary "$CONFORMAL_CALIBRATION/physical_feature_conformal_calibration_summary.json" \
    --predictions-csv "$TANDEM_OOD/physical_feature_tandem_inverse_test_predictions.csv" \
    --out-dir "$MONDRIAN_CONFORMAL" --min-source-rows "$CHECKPOINT_COUNT" \
    --min-prediction-rows 5000 --min-calibration-rows 2000 --min-evaluation-rows 2000 \
    --physical-cell-bins 4 --physical-cell-lower 0.5,0.5,5,0 \
    --physical-cell-upper 3,3,25,0.8 \
    --min-cell-calibration-rows 30 --min-cell-evaluation-rows 30 \
    --min-supported-cells 8 --min-supported-cell-fraction 0.80 \
    --min-supported-row-fraction 0.80 \
    --coverage-levels 0.90,0.95 --coverage-tolerance 0.03 \
    --max-mean-width-inflation 0.20 \
    --seed "$MODEL_SEED" --no-fail-exit || exit 2
  json_contract "$MONDRIAN_CONFORMAL/physical_feature_mondrian_conformal_comparison_summary.json" overall_status PASS 0 || exit 2
else
  "$PYTHON_BIN" - "$CONFORMAL_CALIBRATION/physical_feature_conformal_calibration_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_600K" if index<6 else "NOT_REPEATED_AFTER_600K",
 "decision":"RUN_SPLIT_CONFORMAL_CALIBRATION_AT_600K" if index<6 else "USE_RECORDED_600K_CONFORMAL_CALIBRATION_EVIDENCE",
 "checkpoint_index":index,
 "scientific_boundary":"Global conformal intervals are calibrated once at 600k. They are not sample-wise epistemic uncertainty and cannot directly drive active acquisition.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
  "$PYTHON_BIN" - "$MONDRIAN_CONFORMAL/physical_feature_mondrian_conformal_comparison_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_600K" if index<6 else "NOT_REPEATED_AFTER_600K",
 "decision":"RUN_GLOBAL_VS_MONDRIAN_COMPARISON_AT_600K" if index<6 else "USE_RECORDED_600K_MONDRIAN_COMPARISON_EVIDENCE",
 "checkpoint_index":index,
 "scientific_boundary":"The fixed-cell Mondrian comparison is run once at 600k. PASS means auditable same-split evidence, not that Mondrian won or that exact pointwise conditional coverage was achieved.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

mkdir -p "$LOW_FREQUENCY_PHYSICS"
if [[ "$CHECKPOINT_INDEX" -eq 7 ]]; then
  run_logged audit_low_frequency_coupled_rl_consistency_at_700k \
    "$PYTHON_BIN" "$SCRIPT_DIR/audit_low_frequency_coupled_rl_consistency.py" \
    --dataset-dir "$BALANCED" --out-dir "$LOW_FREQUENCY_PHYSICS" \
    --fit-start-ghz 5 --fit-stop-ghz 10 \
    --reference-frequency-ghz 5 --comparison-frequency-ghz 15 \
    --port-pairs 1,2:3,4 --expected-ports 4 \
    --expected-start-ghz 5 --expected-stop-ghz 60 \
    --expected-step-ghz 0.5 --expected-points 111 \
    --min-files 128 --max-files 512 --min-success-fraction 0.98 \
    --min-physical-fraction 0.95 --no-fail-exit || exit 2
  json_contract "$LOW_FREQUENCY_PHYSICS/low_frequency_coupled_rl_consistency_summary.json" overall_status PASS 0 || exit 2
else
  "$PYTHON_BIN" - "$LOW_FREQUENCY_PHYSICS/low_frequency_coupled_rl_consistency_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_700K" if index<7 else "NOT_REPEATED_AFTER_700K",
 "decision":"RUN_LOW_FREQUENCY_EQUIVALENT_CIRCUIT_AUDIT_AT_700K" if index<7 else "USE_RECORDED_700K_LOW_FREQUENCY_PHYSICS_EVIDENCE",
 "checkpoint_index":index,
 "scientific_boundary":"The coupled-RL audit is run once at 700k. It can authorize only an auxiliary-loss ablation and never replaces broadband complex-S PICC or real EM validation.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

mkdir -p "$LOCAL_REFINEMENT"
if [[ "$CHECKPOINT_INDEX" -eq 8 ]]; then
  run_logged plan_tandem_local_refinement_benchmark_at_800k \
    "$PYTHON_BIN" "$SCRIPT_DIR/plan_tandem_local_refinement_benchmark.py" \
    --tandem-summary "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" \
    --weights-npz "$TANDEM_OOD/physical_feature_tandem_inverse_weights.npz" \
    --predictions-csv "$TANDEM_OOD/physical_feature_tandem_inverse_test_predictions.csv" \
    --out-dir "$LOCAL_REFINEMENT" --candidate-count 256 \
    --min-source-rows "$CHECKPOINT_COUNT" --min-target-rows 512 \
    --max-target-scan 10000 --trust-weight 0.01 --max-iterations 100 \
    --seed "$MODEL_SEED" --no-fail-exit || exit 2
  json_contract "$LOCAL_REFINEMENT/tandem_local_refinement_plan_summary.json" overall_status PASS 0 || exit 2
  json_contract "$LOCAL_REFINEMENT/tandem_local_refinement_plan_summary.json" outcome_status AWAITING_REAL_EMX 0 || exit 2
else
  "$PYTHON_BIN" - "$LOCAL_REFINEMENT/tandem_local_refinement_plan_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_800K" if index<8 else "NOT_REPEATED_AFTER_800K",
 "decision":"RUN_TANDEM_LOCAL_REFINEMENT_BENCHMARK_AT_800K" if index<8 else "USE_RECORDED_800K_LOCAL_REFINEMENT_PLAN",
 "checkpoint_index":index,
 "outcome_status":"AWAITING_800K_PLAN" if index<8 else "USE_RECORDED_800K_EVIDENCE",
 "scientific_boundary":"The 800k checkpoint plans equal-budget inverse-only and bounded L-BFGS-B candidates. Proxy improvement is never reported as real-EMX improvement.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

mkdir -p "$BOUNDARY_OOD_STRESS"
if [[ "$CHECKPOINT_INDEX" -eq 9 ]]; then
  run_logged audit_physical_feature_boundary_ood_stress_at_900k \
    "$PYTHON_BIN" "$SCRIPT_DIR/audit_physical_feature_boundary_ood_stress.py" \
    --tandem-summary "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" \
    --predictions-csv "$TANDEM_OOD/physical_feature_tandem_inverse_test_predictions.csv" \
    --weights-npz "$TANDEM_OOD/physical_feature_tandem_inverse_weights.npz" \
    --out-dir "$BOUNDARY_OOD_STRESS" --min-source-rows "$CHECKPOINT_COUNT" \
    --min-prediction-rows 5000 --min-boundary-rows 256 --min-interior-rows 256 \
    --min-group-evaluation-rows 128 --boundary-fraction 0.10 --interior-fraction 0.20 \
    --coverage-levels 0.90,0.95 --stress-levels 0.01,0.03,0.05,0.10 \
    --max-stress-rows 2048 --seed "$MODEL_SEED" --no-fail-exit || exit 2
  json_contract "$BOUNDARY_OOD_STRESS/boundary_ood_stress_summary.json" overall_status PASS 0 || exit 2
else
  "$PYTHON_BIN" - "$BOUNDARY_OOD_STRESS/boundary_ood_stress_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_900K" if index<9 else "NOT_REPEATED_AFTER_900K",
 "decision":"RUN_BOUNDARY_OOD_STRESS_AT_900K" if index<9 else "USE_RECORDED_900K_BOUNDARY_OOD_STRESS_EVIDENCE",
 "checkpoint_index":index,
 "scientific_boundary":"The boundary OOD and outward-specification stress audit runs once at 900k. Frozen-proxy robustness never replaces DRC, real EMX, HFSS, or final uniformity evidence.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

mkdir -p "$PHYSICAL_SPEC_SPECTRAL_EXPANDER"
run_logged train_physical_spec_to_broadband_s4p_baseline_at_checkpoint \
  "$PYTHON_BIN" "$SCRIPT_DIR/train_broadband_sparameter_pca_surrogate.py" \
  --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
  --out-dir "$PHYSICAL_SPEC_SPECTRAL_EXPANDER" \
  --geometry-columns "$INPUT_COLUMNS" \
  --split-reference-columns "$INPUT_COLUMNS" \
  --predictor-role physical_spec --expected-predictor-count 4 \
  --min-rows 5000 --max-rows 10000 --pca-rank 32 \
  --pca-oversample 8 --pca-power-iterations 1 \
  --ridge-alpha 0.001 --seed "$MODEL_SEED" --split-seed "$SPLIT_SEED" \
  --physical-cell-bins 4 --expected-ports 4 \
  --expected-frequency-start-ghz 5 --expected-frequency-stop-ghz 60 \
  --expected-frequency-step-ghz 0.5 --expected-frequency-points 111 \
  --target-frequency-ghz 15 \
  --max-input-reciprocity-error 0.02 --max-input-passivity-excess 0.05 \
  --no-fail-exit || exit 2
json_contract "$PHYSICAL_SPEC_SPECTRAL_EXPANDER/broadband_sparameter_pca_surrogate_summary.json" overall_status COMPLETE_REVIEW_REQUIRED 0 || exit 2
json_contract "$PHYSICAL_SPEC_SPECTRAL_EXPANDER/broadband_sparameter_pca_surrogate_summary.json" predictor_role physical_spec 0 || exit 2

if [[ "$CHECKPOINT_INDEX" -eq 2 ]]; then
  run_logged audit_5ghz_vs_15ghz_feature_stability \
    "$PYTHON_BIN" "$SCRIPT_DIR/audit_physical_feature_extraction_frequency_stability.py" \
    --dataset-dir "$BALANCED" --out-dir "$FREQUENCY_STABILITY" \
    --low-frequency-ghz 5 --current-frequency-ghz 15 --forward-step-ghz 0.5 \
    --port-pairs 1,2:3,4 --expected-ports 4 \
    --expected-start-ghz 5 --expected-stop-ghz 60 \
    --expected-step-ghz 0.5 --expected-points 111 \
    --min-files 128 --max-files 512 --min-success-fraction 0.98 \
    --min-plausible-fraction 0.95 --no-fail-exit || exit 2
  json_contract "$FREQUENCY_STABILITY/physical_feature_frequency_stability_summary.json" overall_status PASS 0 || exit 2

  run_logged build_qp_qs_ablation_training_table \
    "$PYTHON_BIN" "$SCRIPT_DIR/build_physical_feature_inverse_training_table.py" "$BALANCED" \
    --out-dir "$QP_QS_TRAINING" \
    --feature-columns lp_nh_center,ls_nh_center,q_center,qp_center,qs_center,k_abs_center \
    --geometry-columns "$GEOMETRY_COLUMNS" --input-prefix input__ \
    --check-touchstone-exists --no-fail-exit || exit 2
  json_contract "$QP_QS_TRAINING/physical_feature_inverse_training_manifest.json" training_count "" "$CHECKPOINT_COUNT" || exit 2

  run_logged train_tandem_qp_qs_shared_ood \
    env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" MKL_NUM_THREADS="$NN_THREADS" \
    "$PYTHON_BIN" "$SCRIPT_DIR/train_physical_feature_tandem_inverse.py" \
    --training-csv "$QP_QS_TRAINING/physical_feature_inverse_training_table.csv" \
    --out-dir "$QP_QS_TANDEM" \
    --input-columns input__lp_nh_center,input__ls_nh_center,input__qp_center,input__qs_center,input__k_abs_center \
    --split-reference-columns input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center \
    --geometry-columns "$GEOMETRY_COLUMNS" --min-training-rows "$CHECKPOINT_COUNT" \
    --split-mode physical_cell_grouped --physical-cell-bins 4 \
    --physical-cell-lower 0.5,0.5,5,0 --physical-cell-upper 3,3,25,0.8 \
    --forward-depth "$TANDEM_DEPTH" --forward-width "$TANDEM_WIDTH" \
    --inverse-depth "$TANDEM_DEPTH" --inverse-width "$TANDEM_WIDTH" \
    --batch-size "$TANDEM_BATCH_SIZE" --forward-epochs "$MAX_EPOCHS" \
    --inverse-epochs "$MAX_EPOCHS" --patience "$PATIENCE" --seed "$MODEL_SEED" \
    --split-seed "$SPLIT_SEED" \
    --response-loss-scaling declared_range \
    --topology-feasibility-weight "$TOPOLOGY_FEASIBILITY_WEIGHT" \
    --response-weight-schedule warmup_ramp_adaptive_ema \
    --response-warmup-fraction 0.05 --response-ramp-fraction 0.20 \
    --local-refinement-steps "$LOCAL_REFINEMENT_STEPS" \
    --local-refinement-starts "$LOCAL_REFINEMENT_STARTS" \
    --local-refinement-learning-rate "$LOCAL_REFINEMENT_LEARNING_RATE" \
    --local-refinement-jitter "$LOCAL_REFINEMENT_JITTER" \
    --no-fail-exit || exit 2

  run_logged train_broadband_qmin_physical_spec_baseline_at_200k \
    env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" MKL_NUM_THREADS="$NN_THREADS" \
    "$PYTHON_BIN" "$SCRIPT_DIR/train_broadband_sparameter_pca_surrogate.py" \
    --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
    --out-dir "$BROADBAND_QMIN_EXPANDER" \
    --geometry-columns "$INPUT_COLUMNS" --split-reference-columns "$INPUT_COLUMNS" \
    --predictor-role physical_spec --expected-predictor-count 4 \
    --min-rows 5000 --max-rows 10000 --pca-rank 32 --pca-oversample 8 \
    --pca-power-iterations 1 --ridge-alpha 0.001 --seed "$MODEL_SEED" \
    --split-seed "$SPLIT_SEED" --physical-cell-bins 4 --expected-ports 4 \
    --expected-frequency-start-ghz 5 --expected-frequency-stop-ghz 60 \
    --expected-frequency-step-ghz 0.5 --expected-frequency-points 111 \
    --target-frequency-ghz 15 \
    --max-input-reciprocity-error 0.02 --max-input-passivity-excess 0.05 \
    --no-fail-exit || exit 2
  json_contract "$BROADBAND_QMIN_EXPANDER/broadband_sparameter_pca_surrogate_summary.json" overall_status COMPLETE_REVIEW_REQUIRED 0 || exit 2

  run_logged train_broadband_qp_qs_physical_spec_baseline_at_200k \
    env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" MKL_NUM_THREADS="$NN_THREADS" \
    "$PYTHON_BIN" "$SCRIPT_DIR/train_broadband_sparameter_pca_surrogate.py" \
    --training-csv "$QP_QS_TRAINING/physical_feature_inverse_training_table.csv" \
    --out-dir "$BROADBAND_QP_QS_EXPANDER" \
    --geometry-columns input__lp_nh_center,input__ls_nh_center,input__qp_center,input__qs_center,input__k_abs_center \
    --split-reference-columns "$INPUT_COLUMNS" \
    --predictor-role physical_spec --expected-predictor-count 5 \
    --min-rows 5000 --max-rows 10000 --pca-rank 32 --pca-oversample 8 \
    --pca-power-iterations 1 --ridge-alpha 0.001 --seed "$MODEL_SEED" \
    --split-seed "$SPLIT_SEED" --physical-cell-bins 4 --expected-ports 4 \
    --expected-frequency-start-ghz 5 --expected-frequency-stop-ghz 60 \
    --expected-frequency-step-ghz 0.5 --expected-frequency-points 111 \
    --target-frequency-ghz 15 \
    --max-input-reciprocity-error 0.02 --max-input-passivity-excess 0.05 \
    --no-fail-exit || exit 2
  json_contract "$BROADBAND_QP_QS_EXPANDER/broadband_sparameter_pca_surrogate_summary.json" overall_status COMPLETE_REVIEW_REQUIRED 0 || exit 2

  run_logged compare_q_vs_qp_qs_input_ablation \
    "$PYTHON_BIN" "$SCRIPT_DIR/compare_physical_feature_q_input_ablation.py" \
    --q-summary "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" \
    --qp-qs-summary "$QP_QS_TANDEM/physical_feature_tandem_inverse_summary.json" \
    --q-broadband-summary "$BROADBAND_QMIN_EXPANDER/broadband_sparameter_pca_surrogate_summary.json" \
    --qp-qs-broadband-summary "$BROADBAND_QP_QS_EXPANDER/broadband_sparameter_pca_surrogate_summary.json" \
    --out-dir "$Q_INPUT_ABLATION" --minimum-material-improvement 0.05 \
    --no-fail-exit || exit 2
  json_contract "$Q_INPUT_ABLATION/physical_feature_q_input_ablation_summary.json" overall_status PASS 0 || exit 2

  run_logged select_balanced_mse_bni_temperature_at_200k \
    "$PYTHON_BIN" "$SCRIPT_DIR/select_balanced_mse_bni_temperature.py" \
    --mse-summary "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" \
    --mse-history "$TANDEM_OOD/physical_feature_tandem_inverse_history.csv" \
    --out-dir "$BNI_TEMPERATURE_SELECTION" --no-fail-exit || exit 2
  json_contract "$BNI_TEMPERATURE_SELECTION/balanced_mse_bni_temperature_selection.json" overall_status PASS 0 || exit 2

  BNI_TAU="$("$PYTHON_BIN" - "$BNI_TEMPERATURE_SELECTION/balanced_mse_bni_temperature_selection.json" <<'PY'
import json,pathlib,sys
payload=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
value=float(payload["selected_temperature_tau"])
if not value>0: raise SystemExit("selected BNI temperature is not positive")
print(repr(value))
PY
)"
  if [[ -z "$BNI_TAU" ]]; then
    log "BNI temperature selection did not return a positive value"
    exit 2
  fi

  run_logged train_balanced_mse_bni_same_budget_at_200k \
    env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" MKL_NUM_THREADS="$NN_THREADS" \
    "$PYTHON_BIN" "$SCRIPT_DIR/train_physical_feature_tandem_inverse.py" \
    --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
    --out-dir "$TANDEM_BNI" --input-columns "$INPUT_COLUMNS" \
    --geometry-columns "$GEOMETRY_COLUMNS" --min-training-rows "$CHECKPOINT_COUNT" \
    --split-mode physical_cell_grouped --physical-cell-bins 4 \
    --physical-cell-lower 0.5,0.5,5,0 --physical-cell-upper 3,3,25,0.8 \
    --forward-depth "$TANDEM_DEPTH" --forward-width "$TANDEM_WIDTH" \
    --inverse-depth "$TANDEM_DEPTH" --inverse-width "$TANDEM_WIDTH" \
    --batch-size "$TANDEM_BATCH_SIZE" --forward-epochs "$MAX_EPOCHS" \
    --inverse-epochs "$MAX_EPOCHS" --patience "$PATIENCE" --seed "$MODEL_SEED" \
    --max-prediction-rows "$TANDEM_MAX_PREDICTION_ROWS" --split-seed "$SPLIT_SEED" \
    --response-loss-scaling declared_range \
    --response-loss-family balanced_mse_bni --balanced-mse-temperature "$BNI_TAU" \
    --topology-feasibility-weight "$TOPOLOGY_FEASIBILITY_WEIGHT" \
    --response-weight-schedule warmup_ramp_adaptive_ema \
    --response-warmup-fraction 0.05 --response-ramp-fraction 0.20 \
    --local-refinement-steps "$LOCAL_REFINEMENT_STEPS" \
    --local-refinement-starts "$LOCAL_REFINEMENT_STARTS" \
    --local-refinement-learning-rate "$LOCAL_REFINEMENT_LEARNING_RATE" \
    --local-refinement-jitter "$LOCAL_REFINEMENT_JITTER" \
    --no-fail-exit || exit 2
  json_contract "$TANDEM_BNI/physical_feature_tandem_inverse_summary.json" overall_status COMPLETE_REVIEW_REQUIRED 0 || exit 2

  run_logged compare_mse_vs_balanced_mse_bni_at_200k \
    "$PYTHON_BIN" "$SCRIPT_DIR/compare_balanced_mse_bni_ablation.py" \
    --mse-summary "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" \
    --bni-summary "$TANDEM_BNI/physical_feature_tandem_inverse_summary.json" \
    --mse-predictions "$TANDEM_OOD/physical_feature_tandem_inverse_test_predictions.csv" \
    --bni-predictions "$TANDEM_BNI/physical_feature_tandem_inverse_test_predictions.csv" \
    --temperature-selection "$BNI_TEMPERATURE_SELECTION/balanced_mse_bni_temperature_selection.json" \
    --bootstrap-replicates 2000 --bootstrap-confidence 0.95 --bootstrap-seed "$SPLIT_SEED" \
    --physical-cell-bins 4 --physical-cell-lower 0.5,0.5,5,0 \
    --physical-cell-upper 3,3,25,0.8 --minimum-paired-test-rows 1000 \
    --minimum-paired-test-cells 8 --minimum-material-improvement 0.05 \
    --out-dir "$BNI_ABLATION" --no-fail-exit || exit 2
  json_contract "$BNI_ABLATION/balanced_mse_bni_ablation_summary.json" overall_status PASS 0 || exit 2

  run_logged train_broadband_sparameter_pca_baseline \
    env OPENBLAS_NUM_THREADS="$NN_THREADS" OMP_NUM_THREADS="$NN_THREADS" MKL_NUM_THREADS="$NN_THREADS" \
    "$PYTHON_BIN" "$SCRIPT_DIR/train_broadband_sparameter_pca_surrogate.py" \
    --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
    --out-dir "$BROADBAND_BASELINE" --geometry-columns "$GEOMETRY_COLUMNS" \
    --min-rows 5000 --max-rows 10000 --pca-rank 32 --pca-oversample 8 \
    --pca-power-iterations 1 --ridge-alpha 0.001 --seed "$MODEL_SEED" \
    --split-seed "$SPLIT_SEED" --physical-cell-bins 4 --expected-ports 4 \
    --expected-frequency-start-ghz 5 --expected-frequency-stop-ghz 60 \
    --expected-frequency-step-ghz 0.5 --expected-frequency-points 111 \
    --target-frequency-ghz 15 \
    --max-input-reciprocity-error 0.02 --max-input-passivity-excess 0.05 \
    --no-fail-exit || exit 2
  json_contract "$BROADBAND_BASELINE/broadband_sparameter_pca_surrogate_summary.json" overall_status COMPLETE_REVIEW_REQUIRED 0 || exit 2
else
  mkdir -p "$FREQUENCY_STABILITY"
  "$PYTHON_BIN" - "$FREQUENCY_STABILITY/physical_feature_frequency_stability_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_200K" if index==1 else "NOT_REPEATED_AFTER_200K",
 "decision":"RUN_5_VS_15_GHZ_STABILITY_AT_200K" if index==1 else "USE_200K_FREQUENCY_STABILITY_EVIDENCE",
 "checkpoint_index":index,
 "scientific_boundary":"Frequency stability is audited once at 200k from the same real S4P labels and never changes the production input contract automatically.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
  "$PYTHON_BIN" - "$Q_INPUT_ABLATION/physical_feature_q_input_ablation_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_200K" if index==1 else "NOT_REPEATED_AFTER_200K",
 "decision":"RUN_AT_200K" if index==1 else "USE_RECORDED_200K_ABLATION_FOR_CONTRACT_DECISION",
 "checkpoint_index":index,
 "scientific_boundary":"The Q versus Qp/Qs ablation is scheduled once at the cumulative 200k checkpoint and does not alter the active EMX production contract.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
  mkdir -p "$BNI_TEMPERATURE_SELECTION" "$BNI_ABLATION"
  "$PYTHON_BIN" - "$BNI_ABLATION/balanced_mse_bni_ablation_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_200K" if index==1 else "NOT_REPEATED_AFTER_200K",
 "decision":"RUN_MSE_VS_BNI_AT_200K" if index==1 else "USE_RECORDED_200K_BNI_ABLATION",
 "checkpoint_index":index,
 "scientific_boundary":"The BNI arm is trained once at 200k with tau frozen from MSE validation history. It cannot replace MSE without paired row, equal-cell, p90-tail, DRC, and real-EM evidence.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
  mkdir -p "$BROADBAND_BASELINE"
  "$PYTHON_BIN" - "$BROADBAND_BASELINE/broadband_sparameter_pca_surrogate_summary.json" "$CHECKPOINT_INDEX" <<'PY'
import json,pathlib,sys
from datetime import datetime,timezone
target,index=sys.argv[1:]; index=int(index)
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"WAITING_FOR_200K" if index==1 else "NOT_REPEATED_AFTER_200K",
 "decision":"RUN_BASELINE_AT_200K" if index==1 else "USE_200K_BROADBAND_BASELINE_FOR_LATER_ARCHITECTURE_COMPARISON",
 "checkpoint_index":index,
 "scientific_boundary":"The low-rank broadband complex-S baseline is scheduled once at 200k and is not a substitute for later neural or real-EM validation.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
fi

"$PYTHON_BIN" - "$MANIFEST" "$CHECKPOINT_INDEX" "$CHECKPOINT_COUNT" "$BALANCED/balanced_physical_feature_checkpoint_summary.json" "$UNIFORMITY/physical_feature_uniformity_summary.json" "$TRAINING/physical_feature_inverse_training_manifest.json" "$GROUND_RING_SPACING/ground_ring_spacing_audit_summary.json" "$ABLATION_READINESS/physical_feature_input_ablation_readiness_summary.json" "$BROADBAND_READINESS/broadband_sparameter_surrogate_readiness_summary.json" "$RIDGE/physical_feature_inverse_checkpoint_test_summary.json" "$NN_TRAIN/physical_feature_inverse_nn_architecture_search_training_summary.json" "$NN_FIGURES/physical_feature_inverse_nn_report_figures_summary.json" "$TANDEM_OOD/physical_feature_tandem_inverse_summary.json" "$PHYSICAL_CELL_TAIL/physical_cell_model_tail_error_summary.json" "$TANDEM_FEASIBILITY/tandem_predicted_geometry_feasibility_summary.json" "$TANDEM_ANCHOR_ABLATION/tandem_geometry_anchor_ablation_summary.json" "$Q_INPUT_ABLATION/physical_feature_q_input_ablation_summary.json" "$BNI_ABLATION/balanced_mse_bni_ablation_summary.json" "$BROADBAND_BASELINE/broadband_sparameter_pca_surrogate_summary.json" "$FREQUENCY_STABILITY/physical_feature_frequency_stability_summary.json" "$GEOMETRY_SENSITIVITY/geometry_response_effective_dimension_summary.json" "$FREQUENCY_SELF_TRANSFER/frequency_self_transfer_benchmark_summary.json" "$FREQUENCY_SEQUENCE_ARCHITECTURES/frequency_sequence_architecture_summary.json" "$FREQUENCY_RESOLUTION_ABLATION/cross_frequency_resolution_summary.json" "$GEOMETRY_MULTIPLICITY/inverse_geometry_multiplicity_summary.json" "$CONFORMAL_CALIBRATION/physical_feature_conformal_calibration_summary.json" "$MONDRIAN_CONFORMAL/physical_feature_mondrian_conformal_comparison_summary.json" "$LOW_FREQUENCY_PHYSICS/low_frequency_coupled_rl_consistency_summary.json" "$LOCAL_REFINEMENT/tandem_local_refinement_plan_summary.json" "$BOUNDARY_OOD_STRESS/boundary_ood_stress_summary.json" "$PHYSICAL_SPEC_SPECTRAL_EXPANDER/broadband_sparameter_pca_surrogate_summary.json" "$SEED" "$MODEL_SEED" "$SPLIT_SEED" <<'PY'
import hashlib,json,pathlib,sys
from datetime import datetime,timezone
(target,index,count,balanced_raw,uniform_raw,training_raw,ground_ring_spacing_raw,ablation_raw,broadband_raw,ridge_raw,nn_raw,fig_raw,tandem_raw,physical_cell_tail_raw,tandem_feasibility_raw,anchor_ablation_raw,q_ablation_raw,bni_ablation_raw,broadband_baseline_raw,frequency_stability_raw,geometry_sensitivity_raw,frequency_self_transfer_raw,frequency_sequence_raw,frequency_resolution_raw,geometry_multiplicity_raw,conformal_calibration_raw,mondrian_conformal_raw,low_frequency_physics_raw,local_refinement_raw,boundary_ood_stress_raw,physical_spec_spectral_expander_raw,selection_seed,model_seed,split_seed)=sys.argv[1:]
def read(raw):
    p=pathlib.Path(raw)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
def record(raw):
    p=pathlib.Path(raw); item={"path":str(p),"exists":p.is_file()}
    if p.is_file(): item["sha256"]=hashlib.sha256(p.read_bytes()).hexdigest()
    return item
def raw_s4p_quality_pass(data):
    quality=data.get("input_s4p_quality") or {}
    thresholds=data.get("acceptance_thresholds") or {}
    return bool(
        thresholds.get("input_quality_configured")
        and quality.get("audit_stage")=="raw complex S4P before reciprocal symmetrization"
        and (quality.get("reciprocity") or {}).get("hard_threshold_pass") is True
        and (quality.get("passivity") or {}).get("hard_threshold_pass") is True
        and int(quality.get("row_count") or 0)>=5000
    )
def is_sha256(value):
    value=str(value or "").strip().lower()
    return len(value)==64 and all(character in "0123456789abcdef" for character in value)
balanced=read(balanced_raw); uniform=read(uniform_raw); training=read(training_raw); ground_ring_spacing=read(ground_ring_spacing_raw); ablation=read(ablation_raw); broadband=read(broadband_raw); ridge=read(ridge_raw); nn=read(nn_raw); figures=read(fig_raw); tandem=read(tandem_raw); physical_cell_tail=read(physical_cell_tail_raw); tandem_feasibility=read(tandem_feasibility_raw); anchor_ablation=read(anchor_ablation_raw); q_ablation=read(q_ablation_raw); bni_ablation=read(bni_ablation_raw); broadband_baseline=read(broadband_baseline_raw); frequency_stability=read(frequency_stability_raw); geometry_sensitivity=read(geometry_sensitivity_raw); frequency_self_transfer=read(frequency_self_transfer_raw); frequency_sequence=read(frequency_sequence_raw); frequency_resolution=read(frequency_resolution_raw); geometry_multiplicity=read(geometry_multiplicity_raw); conformal_calibration=read(conformal_calibration_raw); mondrian_conformal=read(mondrian_conformal_raw); low_frequency_physics=read(low_frequency_physics_raw); local_refinement=read(local_refinement_raw); boundary_ood_stress=read(boundary_ood_stress_raw); physical_spec_spectral_expander=read(physical_spec_spectral_expander_raw)
uniform_manifest_raw=str(pathlib.Path(uniform_raw).with_name("physical_feature_uniformity_manifest.json"))
uniform_manifest=read(uniform_manifest_raw)
tandem_audit=tandem.get("split_audit") or {}
tandem_isolation=tandem.get("evaluation_isolation") or {}
tandem_model_contract=tandem.get("model_comparison_contract") or {}
nn_audit=nn.get("split_audit") or {}
report_figure_artifacts=figures.get("figures") or {}
report_figures_complete=(
    len(report_figure_artifacts)==3
    and all(
        item.get("exists") is True
        and int(item.get("size_bytes") or 0)>0
        and bool(item.get("sha256"))
        for item in report_figure_artifacts.values()
    )
)
expected_ablation_status="WAITING_FOR_200K" if int(index)==1 else "PASS"
shared_split_fingerprint=(
    nn_audit.get("split_fingerprint_sha256")
    and nn_audit.get("split_fingerprint_sha256")==tandem_audit.get("split_fingerprint_sha256")
)
model_pass=(
    int(balanced.get("selected_count") or 0)==int(count)
    and int(training.get("training_count") or 0)>=int(count)
    and uniform_manifest.get("overall_status")=="PASS"
    and ground_ring_spacing.get("overall_status")=="PASS"
    and int(ground_ring_spacing.get("valid_row_count") or 0)>=int(count)
    and (ground_ring_spacing.get("artifacts") or {}).get("ratio_histogram_status")=="PASS"
    and (ground_ring_spacing.get("artifacts") or {}).get("response_scatter_status")=="PASS"
    and ablation.get("overall_status")==expected_ablation_status
    and broadband.get("overall_status")=="PASS"
    and ridge.get("execution_status")=="PASS"
    and ridge.get("eligible_for_model_success_claim") is False
    and int(ridge.get("usable_row_count") or 0)>=int(count)
    and int(nn.get("training_count") or 0)>=int(count)
    and figures.get("overall_status")=="PASS"
    and report_figures_complete
    and nn_audit.get("split_mode")=="physical_cell_grouped"
    and int(nn_audit.get("physical_cell_overlap_count") or 0)==0
    and int(tandem.get("training_count") or 0)>=int(count)
    and tandem.get("overall_status")=="COMPLETE_REVIEW_REQUIRED"
    and tandem_audit.get("split_mode")=="physical_cell_grouped"
    and int(tandem_audit.get("physical_cell_overlap_count") or 0)==0
    and tandem_audit.get("all_rows_assigned_once") is True
    and tandem_audit.get("physical_cell_partition_stable_for_existing_cells") is True
    and tandem_isolation.get("overall_status")=="PASS"
    and bool(tandem_isolation.get("checks"))
    and all(value is True for value in (tandem_isolation.get("checks") or {}).values())
    and tandem_isolation.get("test_set_used_for_gradient_updates") is False
    and tandem_isolation.get("test_set_used_for_early_stopping") is False
    and tandem_isolation.get("test_set_used_for_model_or_hyperparameter_selection") is False
    and tandem_isolation.get("test_set_used_for_acceptance_threshold_tuning") is False
    and tandem_isolation.get("test_set_used_only_for_post_training_evaluation") is True
    and is_sha256(tandem_model_contract.get("fingerprint_sha256"))
    and is_sha256(tandem_model_contract.get("trainer_implementation_sha256"))
    and len(tandem_model_contract.get("input_columns") or [])==4
    and len(tandem_model_contract.get("geometry_columns") or [])==10
    and (tandem.get("metrics") or {}).get("range_normalization",{}).get("source")=="declared_physical_cell_range"
    and physical_cell_tail.get("overall_status")=="PASS"
    and int(physical_cell_tail.get("test_row_count") or 0)>=1000
    and int(physical_cell_tail.get("test_physical_cell_count") or 0)>=8
    and (physical_cell_tail.get("artifacts") or {}).get("plot_status")=="PASS"
    and tandem_feasibility.get("overall_status")=="PASS"
    and float(tandem_feasibility.get("valid_fraction") or 0.0)==1.0
    and int(tandem_feasibility.get("valid_count") or 0)>=1000
    and bool(shared_split_fingerprint)
    and (int(index)!=1 or (
        anchor_ablation.get("overall_status")=="PASS"
        and anchor_ablation.get("decision_rule")=="paired_cluster_bootstrap_row_and_cell_balanced_ci_lower_ge_material_improvement"
        and (anchor_ablation.get("paired_cluster_bootstrap") or {}).get("status")=="PASS"
        and int((anchor_ablation.get("paired_cluster_bootstrap") or {}).get("paired_test_row_count") or 0)>=1000
        and int((anchor_ablation.get("paired_cluster_bootstrap") or {}).get("paired_physical_cell_count") or 0)>=8
    ))
    and (int(index)!=2 or q_ablation.get("overall_status")=="PASS")
    and (int(index)!=2 or (
        bni_ablation.get("overall_status")=="PASS"
        and bni_ablation.get("decision_rule")=="row_equal_cell_and_p90_tail_cluster_bootstrap_ci_lower_ge_material_improvement"
        and (bni_ablation.get("paired_cluster_bootstrap") or {}).get("status")=="PASS"
        and int((bni_ablation.get("paired_cluster_bootstrap") or {}).get("paired_test_row_count") or 0)>=1000
        and int((bni_ablation.get("paired_cluster_bootstrap") or {}).get("paired_physical_cell_count") or 0)>=8
    ))
    and (int(index)!=2 or broadband_baseline.get("overall_status")=="COMPLETE_REVIEW_REQUIRED")
    and (int(index)!=2 or raw_s4p_quality_pass(broadband_baseline))
    and (int(index)!=2 or frequency_stability.get("overall_status")=="PASS")
    and (int(index)!=3 or geometry_sensitivity.get("overall_status")=="PASS")
    and (int(index)!=3 or (
        frequency_self_transfer.get("overall_status")=="COMPLETE_REVIEW_REQUIRED"
        and ((frequency_self_transfer.get("equal_budget_contract") or {}).get("equal_updates_per_band") is True)
        and ((frequency_self_transfer.get("equal_budget_contract") or {}).get("test_set_used_for_training") is False)
        and ((frequency_self_transfer.get("equal_budget_contract") or {}).get("test_set_used_for_checkpoint_selection") is False)
        and ((frequency_self_transfer.get("checks") or {}).get("raw_input_reciprocity_threshold_pass") is True)
        and ((frequency_self_transfer.get("checks") or {}).get("raw_input_passivity_threshold_pass") is True)
        and ((frequency_self_transfer.get("checks") or {}).get("frequency_bands_are_monotonic_contiguous") is True)
        and ((frequency_self_transfer.get("checks") or {}).get("frequency_band_boundaries_are_contiguous") is True)
        and ((frequency_self_transfer.get("checks") or {}).get("same_initial_weights_per_band") is True)
        and ((frequency_self_transfer.get("checks") or {}).get("physical_metric_extraction_available") is True)
        and int(((frequency_self_transfer.get("split_audit") or {}).get("physical_cell_overlap_count") or 0))==0
    ))
    and (int(index)!=3 or (
        frequency_sequence.get("overall_status")=="COMPLETE_REVIEW_REQUIRED"
        and ((frequency_sequence.get("checks") or {}).get("physical_cell_overlap_zero") is True)
        and ((frequency_sequence.get("checks") or {}).get("equal_optimizer_updates_per_seed") is True)
        and ((frequency_sequence.get("checks") or {}).get("parameter_budget_ratio_within_limit") is True)
        and ((frequency_sequence.get("checks") or {}).get("raw_input_reciprocity_threshold_pass") is True)
        and ((frequency_sequence.get("checks") or {}).get("raw_input_passivity_threshold_pass") is True)
        and ((frequency_sequence.get("checks") or {}).get("full_frequency_sequence_used") is True)
        and ((frequency_sequence.get("checks") or {}).get("physical_metric_extraction_available") is True)
        and ((frequency_sequence.get("equal_budget_contract") or {}).get("test_set_used_for_selection") is False)
        and ((frequency_sequence.get("artifacts") or {}).get("plot_status")=="PASS")
        and int(((frequency_sequence.get("split_audit") or {}).get("physical_cell_overlap_count") or 0))==0
    ))
    and (int(index)!=3 or (
        frequency_resolution.get("overall_status")=="COMPLETE_REVIEW_REQUIRED"
        and ((frequency_resolution.get("equal_budget_contract") or {}).get("equal_optimizer_updates") is True)
        and ((frequency_resolution.get("checks") or {}).get("sparse_training_excludes_all_held_out_frequencies") is True)
        and ((frequency_resolution.get("architecture") or {}).get("is_neural_operator") is False)
        and int(((frequency_resolution.get("split_audit") or {}).get("physical_cell_overlap_count") or 0))==0
    ))
    and (int(index) not in {1,5} or geometry_multiplicity.get("overall_status")=="PASS")
    and (int(index)!=6 or conformal_calibration.get("overall_status")=="PASS")
    and (int(index)!=6 or mondrian_conformal.get("overall_status")=="PASS")
    and (int(index)!=7 or low_frequency_physics.get("overall_status")=="PASS")
    and (int(index)!=8 or (
        local_refinement.get("overall_status")=="PASS"
        and local_refinement.get("outcome_status")=="AWAITING_REAL_EMX"
    ))
    and (int(index)!=9 or boundary_ood_stress.get("overall_status")=="PASS")
    and (
        physical_spec_spectral_expander.get("overall_status")=="COMPLETE_REVIEW_REQUIRED"
        and physical_spec_spectral_expander.get("predictor_role")=="physical_spec"
        and int(((physical_spec_spectral_expander.get("split_audit") or {}).get("physical_cell_overlap_count") or 0))==0
        and raw_s4p_quality_pass(physical_spec_spectral_expander)
    )
)
uniform_pass=uniform.get("overall_status")=="PASS"
overall="PASS" if model_pass and uniform_pass else ("PROVISIONAL_UNIFORMITY_FAIL" if model_pass else "FAIL")
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":overall,
 "decision":"FORMAL_CHECKPOINT_AND_MODEL_PASS" if overall=="PASS" else ("MODEL_TEST_COMPLETE_CONTINUE_ADAPTIVE_ACQUISITION" if model_pass else "DO_NOT_USE_MODEL_CHECKPOINT"),
 "checkpoint_index":int(index),
 "accepted_checkpoint_count":int(count),
 "input_contract":["Lp","Ls","Q=min(Qp,Qs)","|K|"],
 "output_contract":"10 independent geometry variables",
 "ground_ring_spacing_audit_status":ground_ring_spacing.get("overall_status","MISSING"),
 "ground_ring_spacing_audit_decision":ground_ring_spacing.get("decision","MISSING"),
 "ground_ring_spacing_below_advisory_fraction":(ground_ring_spacing.get("analysis") or {}).get("below_recommended_fraction"),
 "ground_ring_spacing_boundary":"The one-third-diameter value is an RF rule of thumb, not DRC or an automatic row-rejection gate. The audit is complete only when row-level shield margin evidence and plots exist.",
 "seed_contract":{
   "selection_seed":int(selection_seed),
   "model_initialization_seed":int(model_seed),
   "cross_checkpoint_split_seed":int(split_seed),
   "boundary":"Selection may vary by checkpoint, while model initialization and physical-cell assignment remain fixed so cumulative learning curves are comparable.",
 },
 "model_test_status":"PASS" if model_pass else "FAIL",
 "model_test_semantics":"PASS means the declared model-test suite completed with valid artifacts; it is not a claim that the inverse model met an undeclared scientific accuracy threshold.",
 "direct_ood_split_audit":nn_audit,
 "tandem_ood_status":tandem.get("overall_status","MISSING"),
 "tandem_ood_split_audit":tandem_audit,
 "tandem_evaluation_isolation":tandem_isolation,
 "tandem_model_comparison_contract":tandem_model_contract,
 "tandem_test_set_used_for_gradient_updates":tandem_isolation.get("test_set_used_for_gradient_updates"),
 "tandem_test_set_used_for_early_stopping":tandem_isolation.get("test_set_used_for_early_stopping"),
 "tandem_test_set_used_for_model_or_hyperparameter_selection":tandem_isolation.get("test_set_used_for_model_or_hyperparameter_selection"),
 "tandem_test_set_used_for_acceptance_threshold_tuning":tandem_isolation.get("test_set_used_for_acceptance_threshold_tuning"),
 "tandem_test_set_used_only_for_post_training_evaluation":tandem_isolation.get("test_set_used_only_for_post_training_evaluation"),
 "tandem_ood_metrics":tandem.get("metrics",{}),
 "physical_cell_tail_error_status":physical_cell_tail.get("overall_status","MISSING"),
 "physical_cell_tail_error_test_rows":physical_cell_tail.get("test_row_count"),
 "physical_cell_tail_error_test_cells":physical_cell_tail.get("test_physical_cell_count"),
 "physical_cell_tail_error_row_weighted_rmse":(physical_cell_tail.get("metrics") or {}).get("row_weighted_response_range_normalized_rmse"),
 "physical_cell_tail_error_equal_cell_rmse":(physical_cell_tail.get("metrics") or {}).get("equal_cell_response_range_normalized_rmse"),
 "physical_cell_tail_error_p95_rmse":(physical_cell_tail.get("metrics") or {}).get("cell_response_range_normalized_rmse_p95"),
 "physical_cell_tail_error_max_rmse":(physical_cell_tail.get("metrics") or {}).get("cell_response_range_normalized_rmse_max"),
 "physical_cell_tail_error_worst_to_median_ratio":(physical_cell_tail.get("metrics") or {}).get("worst_to_median_cell_rmse_ratio"),
 "physical_cell_tail_error_worst_cell":physical_cell_tail.get("worst_cell",{}),
 "physical_cell_tail_error_contract_fingerprint":(physical_cell_tail.get("contract") or {}).get("fingerprint_sha256"),
 "physical_cell_tail_error_boundary":"This complete held-out-cell audit exposes row-weighted, equal-cell, p95, and worst-cell errors at every 100k checkpoint. It records no undeclared accuracy threshold and cannot replace DRC, real EMX closure, HFSS correlation, process corners, or measurement.",
 "tandem_topology_feasibility_weight":(tandem.get("method") or {}).get("topology_feasibility_weight"),
 "tandem_topology_feasibility_is_label_free":(tandem.get("method") or {}).get("topology_feasibility_is_label_free"),
 "tandem_predicted_geometry_feasibility_status":tandem_feasibility.get("overall_status","MISSING"),
 "tandem_predicted_geometry_valid_fraction":tandem_feasibility.get("valid_fraction"),
 "tandem_predicted_geometry_feasibility_boundary":tandem_feasibility.get("scientific_boundary","MISSING"),
 "direct_tandem_shared_split_fingerprint":shared_split_fingerprint,
 "tandem_geometry_anchor_ablation_status":anchor_ablation.get("overall_status","MISSING"),
 "tandem_geometry_anchor_ablation_decision":anchor_ablation.get("decision","MISSING"),
 "tandem_response_only_relative_improvement":anchor_ablation.get("response_only_relative_improvement"),
 "tandem_response_only_improvement_ci_lower":(anchor_ablation.get("paired_cluster_bootstrap") or {}).get("relative_improvement_ci_lower"),
 "tandem_response_only_improvement_ci_upper":(anchor_ablation.get("paired_cluster_bootstrap") or {}).get("relative_improvement_ci_upper"),
 "tandem_response_only_cell_balanced_improvement_ci_lower":(anchor_ablation.get("paired_cluster_bootstrap") or {}).get("cell_balanced_relative_improvement_ci_lower"),
 "tandem_response_only_cell_balanced_improvement_ci_upper":(anchor_ablation.get("paired_cluster_bootstrap") or {}).get("cell_balanced_relative_improvement_ci_upper"),
 "tandem_anchor_ablation_bootstrap_status":(anchor_ablation.get("paired_cluster_bootstrap") or {}).get("status","MISSING"),
 "tandem_anchor_ablation_paired_test_rows":(anchor_ablation.get("paired_cluster_bootstrap") or {}).get("paired_test_row_count"),
 "tandem_anchor_ablation_paired_physical_cells":(anchor_ablation.get("paired_cluster_bootstrap") or {}).get("paired_physical_cell_count"),
 "tandem_anchor_ablation_bootstrap_fingerprint":(anchor_ablation.get("paired_cluster_bootstrap") or {}).get("bootstrap_contract_fingerprint_sha256"),
 "tandem_geometry_anchor_ablation_boundary":"The 100k response-only arm removes paired-geometry labels on the same physical-cell OOD split while keeping the same label-free topology penalty. Its review decision requires both the row-weighted and equal-cell lower 95% paired cluster-bootstrap bounds to exceed the predeclared 5% material-gain threshold. This fixed-model interval does not cover training-seed or simulator uncertainty and cannot replace hard geometry audit or real-EM closure.",
 "input_ablation_readiness_status":ablation.get("overall_status","MISSING"),
 "input_ablation_readiness_decision":ablation.get("decision","MISSING"),
 "broadband_sparameter_readiness_status":broadband.get("overall_status","MISSING"),
 "broadband_sparameter_readiness_decision":broadband.get("decision","MISSING"),
 "q_input_ablation_status":q_ablation.get("overall_status","MISSING"),
 "q_input_ablation_decision":q_ablation.get("decision","MISSING"),
 "q_input_ablation_target_frequency_ghz":q_ablation.get("target_frequency_ghz"),
 "q_input_ablation_target_frequency_relative_improvement":q_ablation.get("target_frequency_qp_qs_relative_improvement"),
 "q_input_ablation_full_band_relative_improvement":q_ablation.get("broadband_qp_qs_relative_improvement"),
 "balanced_mse_bni_ablation_status":bni_ablation.get("overall_status","MISSING"),
 "balanced_mse_bni_ablation_decision":bni_ablation.get("decision","MISSING"),
 "balanced_mse_bni_ablation_decision_rule":bni_ablation.get("decision_rule","MISSING"),
 "balanced_mse_bni_selected_temperature_tau":bni_ablation.get("selected_temperature_tau"),
 "balanced_mse_bni_row_improvement_ci_lower":(bni_ablation.get("paired_cluster_bootstrap") or {}).get("relative_improvement_ci_lower"),
 "balanced_mse_bni_equal_cell_improvement_ci_lower":(bni_ablation.get("paired_cluster_bootstrap") or {}).get("cell_balanced_relative_improvement_ci_lower"),
 "balanced_mse_bni_p90_tail_improvement_ci_lower":(bni_ablation.get("paired_cluster_bootstrap") or {}).get("p90_tail_relative_improvement_ci_lower"),
 "balanced_mse_bni_boundary":"The one-time 200k BNI arm uses the same real EMX rows, physical-cell split, architecture, seed, and optimizer budget as MSE. Tau is frozen from MSE validation history without a test sweep. PASS records a valid comparison, not a BNI win; only the predeclared three-CI decision can request real-EM closure.",
 "broadband_baseline_status":broadband_baseline.get("overall_status","MISSING"),
 "broadband_baseline_decision":broadband_baseline.get("decision","MISSING"),
 "broadband_baseline_raw_input_s4p_quality":broadband_baseline.get("input_s4p_quality",{}),
 "physical_feature_frequency_stability_status":frequency_stability.get("overall_status","MISSING"),
 "physical_feature_frequency_stability_decision":frequency_stability.get("decision","MISSING"),
 "physical_feature_frequency_stability_recommendation":(frequency_stability.get("recommendation") or {}).get("decision","MISSING"),
 "geometry_response_effective_dimension_status":geometry_sensitivity.get("overall_status","MISSING"),
 "geometry_response_effective_dimension_decision":geometry_sensitivity.get("decision","MISSING"),
 "geometry_effective_dimension_90pct":((geometry_sensitivity.get("analysis") or {}).get("active_subspace") or {}).get("dimension_for_90_percent_energy"),
 "frequency_self_transfer_status":frequency_self_transfer.get("overall_status","MISSING"),
 "frequency_self_transfer_decision":frequency_self_transfer.get("decision","MISSING"),
 "frequency_self_transfer_test_full_band_relative_improvement":(frequency_self_transfer.get("comparison") or {}).get("test_full_band_relative_improvement"),
 "frequency_self_transfer_test_target_relative_improvement":(frequency_self_transfer.get("comparison") or {}).get("test_target_relative_improvement"),
 "frequency_self_transfer_test_frequency_regression_fraction":(frequency_self_transfer.get("comparison") or {}).get("test_frequency_regression_fraction"),
 "frequency_self_transfer_boundary":"The 300k benchmark is same-data and equal-budget. A favorable broadband proxy result still requires inverse retraining, DRC, real EMX closure, and sampled HFSS correlation.",
 "frequency_sequence_architecture_status":frequency_sequence.get("overall_status","MISSING"),
 "frequency_sequence_architecture_decision":frequency_sequence.get("decision","MISSING"),
 "frequency_sequence_architecture_test_full_band_relative_improvement":(frequency_sequence.get("comparison") or {}).get("test_full_band_relative_improvement"),
 "frequency_sequence_architecture_test_target_relative_improvement":(frequency_sequence.get("comparison") or {}).get("test_target_relative_improvement"),
 "frequency_sequence_architecture_test_resonance_relative_improvement":(frequency_sequence.get("comparison") or {}).get("test_resonance_relative_improvement"),
 "frequency_sequence_architecture_test_frequency_regression_fraction":(frequency_sequence.get("comparison") or {}).get("test_frequency_regression_fraction"),
 "frequency_sequence_architecture_parameter_count_ratio":(frequency_sequence.get("architecture") or {}).get("parameter_count_ratio"),
 "frequency_sequence_architecture_boundary":"The 300k pointwise-MLP versus GRU benchmark uses the same real S4P rows, physical-cell OOD split, optimizer updates, and near-equal parameter budget. A favorable result can only nominate frozen-forward inverse retraining; DRC, real EMX closure, and sampled HFSS correlation remain mandatory.",
 "cross_frequency_resolution_status":frequency_resolution.get("overall_status","MISSING"),
 "cross_frequency_resolution_decision":frequency_resolution.get("decision","MISSING"),
 "cross_frequency_resolution_test_full_grid_relative_degradation":(frequency_resolution.get("comparison") or {}).get("test_full_grid_relative_degradation"),
 "cross_frequency_resolution_test_held_out_relative_degradation":(frequency_resolution.get("comparison") or {}).get("test_held_out_relative_degradation"),
 "cross_frequency_resolution_test_held_out_regression_fraction":(frequency_resolution.get("comparison") or {}).get("test_held_out_frequency_regression_fraction"),
 "cross_frequency_resolution_observed_point_count":(frequency_resolution.get("frequency_partition") or {}).get("observed_point_count"),
 "cross_frequency_resolution_held_out_point_count":(frequency_resolution.get("frequency_partition") or {}).get("held_out_point_count"),
 "cross_frequency_resolution_is_neural_operator":(frequency_resolution.get("architecture") or {}).get("is_neural_operator"),
 "cross_frequency_resolution_boundary":"The 300k sparse-frequency arm is evaluated on unseen frequency coordinates with the same real rows, physical-cell split, initialization, architecture, physical-row batches, batch size, and optimizer-update count as the full-grid arm. This is not a neural-operator claim and does not change the production 0.5 GHz EMX grid.",
 "inverse_geometry_multiplicity_status":geometry_multiplicity.get("overall_status","MISSING"),
 "inverse_geometry_multiplicity_decision":geometry_multiplicity.get("decision","MISSING"),
 "inverse_geometry_multiplicity_recommendation":(geometry_multiplicity.get("recommendation") or {}).get("decision","MISSING"),
 "inverse_geometry_multiplicity_evidence_stage":geometry_multiplicity.get("evidence_stage","MISSING"),
 "inverse_geometry_multiplicity_top_k_eligible":(geometry_multiplicity.get("recommendation") or {}).get("eligible_for_top_k_ablation",False),
 "physical_feature_conformal_calibration_status":conformal_calibration.get("overall_status","MISSING"),
 "physical_feature_conformal_calibration_decision":conformal_calibration.get("decision","MISSING"),
 "physical_feature_conformal_boundary":"Global intervals only; not sample-wise epistemic uncertainty for acquisition.",
 "physical_feature_mondrian_conformal_status":mondrian_conformal.get("overall_status","MISSING"),
 "physical_feature_mondrian_conformal_decision":mondrian_conformal.get("decision","MISSING"),
 "physical_feature_mondrian_conformal_recommendation":(mondrian_conformal.get("recommendation") or {}).get("decision","MISSING"),
 "physical_feature_mondrian_supported_cell_fraction":((mondrian_conformal.get("analysis") or {}).get("support") or {}).get("supported_evaluation_cell_fraction"),
 "physical_feature_mondrian_supported_row_fraction":((mondrian_conformal.get("analysis") or {}).get("support") or {}).get("supported_evaluation_row_fraction"),
 "physical_feature_mondrian_boundary":"PASS means an auditable same-split group-coverage comparison, not a Mondrian win or exact pointwise conditional coverage.",
 "low_frequency_coupled_rl_consistency_status":low_frequency_physics.get("overall_status","MISSING"),
 "low_frequency_coupled_rl_consistency_decision":low_frequency_physics.get("decision","MISSING"),
 "low_frequency_coupled_rl_recommendation":(low_frequency_physics.get("recommendation") or {}).get("decision","MISSING"),
 "low_frequency_coupled_rl_boundary":"Low-frequency equivalent-circuit evidence can support only an auxiliary-loss ablation; broadband complex-S and real EM remain mandatory.",
 "tandem_local_refinement_plan_status":local_refinement.get("overall_status","MISSING"),
 "tandem_local_refinement_plan_decision":local_refinement.get("decision","MISSING"),
 "tandem_local_refinement_outcome_status":local_refinement.get("outcome_status","MISSING"),
 "tandem_local_refinement_boundary":"The 800k artifact is an equal-budget candidate plan. A separate paired evaluator with nonempty real EMX S4P returns is required before claiming improvement.",
 "physical_feature_boundary_ood_stress_status":boundary_ood_stress.get("overall_status","MISSING"),
 "physical_feature_boundary_ood_stress_decision":boundary_ood_stress.get("decision","MISSING"),
 "physical_feature_boundary_ood_stress_recommendation":(boundary_ood_stress.get("recommendation") or {}).get("decision","MISSING"),
 "physical_feature_boundary_ood_stress_boundary":"The 900k audit is a conditional OOD and frozen-proxy stress diagnostic. It cannot replace real EMX edge-target validation.",
 "physical_spec_spectral_expander_status":physical_spec_spectral_expander.get("overall_status","MISSING"),
 "physical_spec_spectral_expander_decision":physical_spec_spectral_expander.get("decision","MISSING"),
 "physical_spec_spectral_expander_test_complex_rmse":(physical_spec_spectral_expander.get("metrics") or {}).get("test_raw_complex_rmse"),
 "physical_spec_spectral_expander_target_frequency_ghz":(physical_spec_spectral_expander.get("metrics") or {}).get("target_frequency_used_ghz"),
 "physical_spec_spectral_expander_target_complex_rmse":(physical_spec_spectral_expander.get("metrics") or {}).get("target_test_raw_complex_rmse"),
 "physical_spec_spectral_expander_raw_input_s4p_quality":physical_spec_spectral_expander.get("input_s4p_quality",{}),
 "physical_spec_spectral_expander_boundary":"The per-100k PCA/ridge spectral expander is a paper-aligned early information-sufficiency baseline, not the final neural architecture. Its proxy error cannot replace DRC, real EMX inverse closure, or HFSS correlation.",
 "uniformity_status":uniform.get("overall_status","MISSING"),
 "uniformity_artifact_manifest_status":uniform_manifest.get("overall_status","MISSING"),
 "uniformity_visual_artifacts":{
   name:item for name,item in (uniform_manifest.get("artifacts") or {}).items()
   if name.startswith("plot_")
 },
 "model_report_figure_artifacts":report_figure_artifacts,
 "four_d_occupied_fraction":(uniform.get("four_dimensional_uniformity") or {}).get("occupied_fraction"),
 "four_d_normalized_entropy":(uniform.get("four_dimensional_uniformity") or {}).get("normalized_entropy"),
 "four_d_nonzero_bin_imbalance":(uniform.get("four_dimensional_uniformity") or {}).get("max_to_min_nonzero_ratio"),
 "artifacts":{
   "balanced_selection":record(balanced_raw),"uniformity":record(uniform_raw),
   "uniformity_artifact_manifest":record(uniform_manifest_raw),
   "training":record(training_raw),"ground_ring_spacing_audit":record(ground_ring_spacing_raw),
   "input_ablation_readiness":record(ablation_raw),
   "broadband_sparameter_readiness":record(broadband_raw),
   "ridge":record(ridge_raw),"nn":record(nn_raw),"figures":record(fig_raw),
   "tandem_physical_cell_ood":record(tandem_raw),
   "physical_cell_model_tail_error":record(physical_cell_tail_raw),
   "tandem_predicted_geometry_feasibility":record(tandem_feasibility_raw),
   "tandem_geometry_anchor_ablation":record(anchor_ablation_raw),
   "q_input_ablation":record(q_ablation_raw),
   "balanced_mse_bni_ablation":record(bni_ablation_raw),
   "broadband_sparameter_pca_baseline":record(broadband_baseline_raw),
   "physical_feature_frequency_stability":record(frequency_stability_raw),
   "geometry_response_effective_dimension":record(geometry_sensitivity_raw),
   "frequency_domain_self_transfer":record(frequency_self_transfer_raw),
   "frequency_sequence_architecture_benchmark":record(frequency_sequence_raw),
   "cross_frequency_resolution":record(frequency_resolution_raw),
   "inverse_geometry_multiplicity":record(geometry_multiplicity_raw),
   "physical_feature_conformal_calibration":record(conformal_calibration_raw),
   "physical_feature_mondrian_conformal_comparison":record(mondrian_conformal_raw),
   "low_frequency_coupled_rl_consistency":record(low_frequency_physics_raw),
   "tandem_local_refinement_plan":record(local_refinement_raw),
   "physical_feature_boundary_ood_stress":record(boundary_ood_stress_raw),
   "physical_spec_spectral_expander":record(physical_spec_spectral_expander_raw),
 },
 "scientific_boundary":"Model-suite completion, physical-feature uniformity, and inverse-model accuracy are separate gates. The tandem result is held out by complete Lp/Ls/Q/|K| cells and remains COMPLETE_REVIEW_REQUIRED until accuracy thresholds are declared from the first checkpoint. Only overall_status=PASS is a formal cumulative campaign checkpoint.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
if not model_pass: raise SystemExit(2)
PY
manifest_rc=$?
if [[ "$manifest_rc" -ne 0 ]]; then
  log "model manifest failed returncode=$manifest_rc"
  exit 2
fi
touch "$MODEL_MARKER"

UNIFORM_STATUS="$("$PYTHON_BIN" - "$MANIFEST" <<'PY'
import json,pathlib,sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["overall_status"])
PY
)"
if [[ "$UNIFORM_STATUS" == "PASS" ]]; then
  touch "$FORMAL_MARKER"
  log "formal checkpoint PASS index=$CHECKPOINT_INDEX count=$CHECKPOINT_COUNT"
  exit 0
fi

log "model test complete but strict uniformity did not pass status=$UNIFORM_STATUS"
if [[ "$REQUIRE_UNIFORMITY_PASS" -eq 1 && "$NO_FAIL_EXIT" -eq 0 ]]; then
  exit 3
fi
exit 0
