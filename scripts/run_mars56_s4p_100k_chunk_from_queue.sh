#!/usr/bin/env bash
# Execute one gated MARS56 grounded-S4P production chunk from a prepared queue.
#
# This runner is intentionally queue-driven: the queue must already come from
# a physical-feature acquisition step, not blind geometry sampling.  The chunk
# is accepted only after EMX S4P generation, Lp/Ls/Q/|K| uniformity, inverse
# training-table construction, model checkpoint, and chunk audit all pass.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG=""
CANDIDATE_CSV=""
CANDIDATE_DIR=""
OUT_DIR=""
CHUNK_INDEX=1
COUNT=100000
MIN_VALID=100000
JOBS=48
SHARD_CHUNK_SIZE=250
BATCH_SIZE=1
TARGET_GHZ=15
BINS=10
PAIR_BINS=10
MIN_1D_OCCUPIED_FRAC=0.90
MIN_1D_ENTROPY_FRAC=0.90
MAX_1D_BIN_IMBALANCE=2.50
MIN_PAIR_OCCUPIED_FRAC=0.65
MIN_PAIR_ENTROPY_FRAC=0.80
FOUR_D_BINS=4
MIN_FOUR_D_OCCUPIED_FRAC=0.50
MIN_FOUR_D_ENTROPY_FRAC=0.80
MAX_FOUR_D_BIN_IMBALANCE=4.0
LP_MIN=0.5
LP_MAX=3
LS_MIN=0.5
LS_MAX=3
Q_MIN=5
Q_MAX=25
K_MIN=0
K_MAX=0.8
TOUCHSTONE_CHECKS=100000
RESUME_COMPLETED=1

usage() {
  cat <<'USAGE'
Usage:
  run_mars56_s4p_100k_chunk_from_queue.sh \
    --candidate-csv QUEUE.csv \
    --config CONFIG.yaml \
    --out-dir CHUNK_OUT_DIR \
    [--chunk-index 1] [--count 100000] [--jobs 48]

Required input:
  The candidate CSV must be a grounded-S4P geometry queue, normally produced by
  materialize_physical_feature_targeted_s4p_queue.py from a physical-feature
  targeted selection.  This script does not invent labels or sample blindly.

Outputs:
  dataset/
  checkpoint/
  chunk_audit/
  mars56_s4p_100k_chunk_run_summary.json
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --candidate-csv) CANDIDATE_CSV="$2"; shift 2 ;;
    --candidate-dir) CANDIDATE_DIR="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --chunk-index) CHUNK_INDEX="$2"; shift 2 ;;
    --count) COUNT="$2"; TOUCHSTONE_CHECKS="$2"; shift 2 ;;
    --min-valid) MIN_VALID="$2"; shift 2 ;;
    --jobs) JOBS="$2"; shift 2 ;;
    --shard-chunk-size) SHARD_CHUNK_SIZE="$2"; shift 2 ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    --target-ghz) TARGET_GHZ="$2"; shift 2 ;;
    --bins) BINS="$2"; shift 2 ;;
    --pair-bins) PAIR_BINS="$2"; shift 2 ;;
    --min-1d-occupied-frac) MIN_1D_OCCUPIED_FRAC="$2"; shift 2 ;;
    --min-1d-entropy-frac) MIN_1D_ENTROPY_FRAC="$2"; shift 2 ;;
    --max-1d-bin-imbalance) MAX_1D_BIN_IMBALANCE="$2"; shift 2 ;;
    --min-pair-occupied-frac) MIN_PAIR_OCCUPIED_FRAC="$2"; shift 2 ;;
    --min-pair-entropy-frac) MIN_PAIR_ENTROPY_FRAC="$2"; shift 2 ;;
    --four-d-bins) FOUR_D_BINS="$2"; shift 2 ;;
    --min-four-d-occupied-frac) MIN_FOUR_D_OCCUPIED_FRAC="$2"; shift 2 ;;
    --min-four-d-entropy-frac) MIN_FOUR_D_ENTROPY_FRAC="$2"; shift 2 ;;
    --max-four-d-bin-imbalance) MAX_FOUR_D_BIN_IMBALANCE="$2"; shift 2 ;;
    --lp-min) LP_MIN="$2"; shift 2 ;;
    --lp-max) LP_MAX="$2"; shift 2 ;;
    --ls-min) LS_MIN="$2"; shift 2 ;;
    --ls-max) LS_MAX="$2"; shift 2 ;;
    --q-min) Q_MIN="$2"; shift 2 ;;
    --q-max) Q_MAX="$2"; shift 2 ;;
    --k-min) K_MIN="$2"; shift 2 ;;
    --k-max) K_MAX="$2"; shift 2 ;;
    --touchstone-checks) TOUCHSTONE_CHECKS="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --no-resume-completed) RESUME_COMPLETED=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$CANDIDATE_CSV" || -z "$CONFIG" || -z "$OUT_DIR" ]]; then
  echo "ERROR: --candidate-csv, --config, and --out-dir are required." >&2
  usage >&2
  exit 2
fi

CANDIDATE_CSV="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$CANDIDATE_CSV")"
CONFIG="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$CONFIG")"
OUT_DIR="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$OUT_DIR")"
if [[ -z "$CANDIDATE_DIR" ]]; then
  CANDIDATE_DIR="$(dirname "$CANDIDATE_CSV")"
else
  CANDIDATE_DIR="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$CANDIDATE_DIR")"
fi

mkdir -p "$OUT_DIR"
DATASET_DIR="$OUT_DIR/dataset"
CHECKPOINT_DIR="$OUT_DIR/checkpoint"
AUDIT_DIR="$OUT_DIR/chunk_audit"
QUEUE_PREFLIGHT_DIR="$OUT_DIR/candidate_queue_preflight"
LOG="$OUT_DIR/mars56_s4p_100k_chunk_commands.log"

run_step() {
  local name="$1"
  shift
  {
    echo
    echo "===== ${name} ====="
    date '+%Y-%m-%d %H:%M:%S %Z'
    printf 'COMMAND'
    printf ' %q' "$@"
    echo
  } | tee -a "$LOG"
  "$@" 2>&1 | tee -a "$LOG"
}

RUN_FLAGS=()
if [[ "$RESUME_COMPLETED" == "1" ]]; then
  RUN_FLAGS+=(--resume-completed)
fi

run_step candidate_queue_provenance_preflight \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_mars56_s4p_candidate_queue_provenance.py" \
  --candidate-csv "$CANDIDATE_CSV" \
  --expected-count "$COUNT" \
  --out-dir "$QUEUE_PREFLIGHT_DIR"

# Preserve every independent sample attempt; failed rows are retried from the
# merged audit instead of aborting the rest of their dynamic shard.
run_step emx_parallel_s4p \
  env RFIC_SKIP_LAYOUT_PREVIEWS=1 RFIC_SKIP_LUMPED_COMPARE=1 \
  "$PYTHON_BIN" "$SCRIPT_DIR/run_candidate_queue_dataset_parallel.py" \
  --candidate-csv "$CANDIDATE_CSV" \
  --config "$CONFIG" \
  --out-dir "$DATASET_DIR" \
  --jobs "$JOBS" \
  --expected-jobs "$JOBS" \
  --chunk-size "$SHARD_CHUNK_SIZE" \
  --max-count "$COUNT" \
  --expected-count "$COUNT" \
  --batch-size "$BATCH_SIZE" \
  --force-wideband-5-60-0p5 \
  --expected-frequency-start-ghz 5 \
  --expected-frequency-stop-ghz 60 \
  --expected-frequency-step-ghz 0.5 \
  --expected-frequency-points 111 \
  --expected-touchstone-extension .s4p \
  --expected-ports 4 \
  --max-touchstone-checks "$TOUCHSTONE_CHECKS" \
  --expected-port-mode single_ended_shield_grounded \
  --expected-pin-purpose 51 \
  --no-fail-exit \
  "${RUN_FLAGS[@]}"

run_step physical_checkpoint \
  bash "$SCRIPT_DIR/run_mars56_s4p_physical_checkpoint_pipeline.sh" \
  --dataset-dir "$DATASET_DIR" \
  --out-dir "$CHECKPOINT_DIR" \
  --config "$CONFIG" \
  --count "$COUNT" \
  --min-valid "$MIN_VALID" \
  --target-ghz "$TARGET_GHZ" \
  --bins "$BINS" \
  --pair-bins "$PAIR_BINS" \
  --min-1d-occupied-frac "$MIN_1D_OCCUPIED_FRAC" \
  --min-1d-entropy-frac "$MIN_1D_ENTROPY_FRAC" \
  --max-1d-bin-imbalance "$MAX_1D_BIN_IMBALANCE" \
  --min-pair-occupied-frac "$MIN_PAIR_OCCUPIED_FRAC" \
  --min-pair-entropy-frac "$MIN_PAIR_ENTROPY_FRAC" \
  --four-d-bins "$FOUR_D_BINS" \
  --min-four-d-occupied-frac "$MIN_FOUR_D_OCCUPIED_FRAC" \
  --min-four-d-entropy-frac "$MIN_FOUR_D_ENTROPY_FRAC" \
  --max-four-d-bin-imbalance "$MAX_FOUR_D_BIN_IMBALANCE" \
  --lp-min "$LP_MIN" --lp-max "$LP_MAX" \
  --ls-min "$LS_MIN" --ls-max "$LS_MAX" \
  --q-min "$Q_MIN" --q-max "$Q_MAX" \
  --k-min "$K_MIN" --k-max "$K_MAX" \
  --require-four-d-gate \
  --require-plots \
  --python "$PYTHON_BIN"

run_step chunk_audit \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_mars56_s4p_million_chunk_checkpoint.py" \
  --chunk-index "$CHUNK_INDEX" \
  --expected-sample-count "$COUNT" \
  --candidate-dir "$CANDIDATE_DIR" \
  --dataset-dir "$DATASET_DIR" \
  --checkpoint-dir "$CHECKPOINT_DIR" \
  --out-dir "$AUDIT_DIR" \
  --min-training-rows "$MIN_VALID"

python3 - "$OUT_DIR" "$CHUNK_INDEX" "$COUNT" "$CANDIDATE_CSV" "$DATASET_DIR" "$CHECKPOINT_DIR" "$AUDIT_DIR" "$MIN_FOUR_D_OCCUPIED_FRAC" "$LP_MIN" "$LP_MAX" "$LS_MIN" "$LS_MAX" "$Q_MIN" "$Q_MAX" "$K_MIN" "$K_MAX" "$MIN_1D_OCCUPIED_FRAC" "$MIN_1D_ENTROPY_FRAC" "$MAX_1D_BIN_IMBALANCE" "$MIN_PAIR_OCCUPIED_FRAC" "$MIN_PAIR_ENTROPY_FRAC" "$MIN_FOUR_D_ENTROPY_FRAC" "$MAX_FOUR_D_BIN_IMBALANCE" <<'PY'
import json
import math
import pathlib
import sys
from datetime import datetime, timezone

out_dir = pathlib.Path(sys.argv[1])
chunk_index = int(sys.argv[2])
count = int(sys.argv[3])
candidate_csv = sys.argv[4]
dataset_dir = pathlib.Path(sys.argv[5])
checkpoint_dir = pathlib.Path(sys.argv[6])
audit_dir = pathlib.Path(sys.argv[7])
expected_min_four_d_occupied_frac = float(sys.argv[8])
expected_target_ranges = {
    "lp": (float(sys.argv[9]), float(sys.argv[10])),
    "ls": (float(sys.argv[11]), float(sys.argv[12])),
    "q": (float(sys.argv[13]), float(sys.argv[14])),
    "k": (float(sys.argv[15]), float(sys.argv[16])),
}
expected_uniformity_thresholds = {
    "min_1d_occupied_fraction": float(sys.argv[17]),
    "min_1d_entropy_fraction": float(sys.argv[18]),
    "max_1d_bin_imbalance": float(sys.argv[19]),
    "min_pair_occupied_fraction": float(sys.argv[20]),
    "min_pair_entropy_fraction": float(sys.argv[21]),
    "min_four_d_normalized_entropy": float(sys.argv[22]),
    "max_four_d_nonzero_bin_imbalance": float(sys.argv[23]),
}

def read_json(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

parallel = read_json(dataset_dir / "parallel_candidate_queue_dataset_summary.json")
checkpoint = read_json(checkpoint_dir / "mars56_s4p_physical_checkpoint_pipeline_summary.json")
audit = read_json(audit_dir / "mars56_s4p_million_chunk_checkpoint_summary.json")
queue_preflight = read_json(out_dir / "candidate_queue_preflight" / "mars56_s4p_candidate_queue_provenance_summary.json")

def checkpoint_proof(data, expected):
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
    gate = data.get("physical_uniformity_gate") if isinstance(data.get("physical_uniformity_gate"), dict) else {}
    if not gate:
        reasons.append("physical_uniformity_gate=MISSING")
    else:
        if gate.get("require_four_d_gate") is not True:
            reasons.append(f"physical_uniformity_gate.require_four_d_gate={gate.get('require_four_d_gate')!r}")
        for threshold_name, expected_value in expected_uniformity_thresholds.items():
            try:
                actual_value = float(gate.get(threshold_name))
            except Exception:
                reasons.append(f"physical_uniformity_gate.{threshold_name}={gate.get(threshold_name)!r}")
                continue
            if not math.isclose(actual_value, expected_value, rel_tol=0.0, abs_tol=1e-12):
                reasons.append(f"physical_uniformity_gate.{threshold_name}={actual_value},expected={expected_value}")
        try:
            gate_min_four_d = float(gate.get("min_four_d_occupied_fraction"))
        except Exception:
            reasons.append(f"physical_uniformity_gate.min_four_d_occupied_fraction={gate.get('min_four_d_occupied_fraction')!r}")
        else:
            if not math.isclose(gate_min_four_d, expected_min_four_d_occupied_frac, rel_tol=0.0, abs_tol=1e-12):
                reasons.append(
                    f"physical_uniformity_gate.min_four_d_occupied_fraction={gate_min_four_d},expected={expected_min_four_d_occupied_frac}"
                )
        gate_ranges = gate.get("target_ranges") if isinstance(gate.get("target_ranges"), dict) else {}
        if not gate_ranges:
            reasons.append("physical_uniformity_gate.target_ranges=MISSING")
        for feature_name, (target_min, target_max) in expected_target_ranges.items():
            item = gate_ranges.get(feature_name) if isinstance(gate_ranges.get(feature_name), dict) else {}
            if not item:
                reasons.append(f"physical_uniformity_gate.target_ranges.{feature_name}=MISSING")
                continue
            try:
                actual_min = float(item.get("min"))
                actual_max = float(item.get("max"))
            except Exception:
                reasons.append(f"physical_uniformity_gate.target_ranges.{feature_name}={item!r}")
            else:
                if not (math.isclose(actual_min, target_min, rel_tol=0.0, abs_tol=1e-12) and math.isclose(actual_max, target_max, rel_tol=0.0, abs_tol=1e-12)):
                    reasons.append(
                        f"physical_uniformity_gate.target_ranges.{feature_name}=({actual_min},{actual_max}),expected=({target_min},{target_max})"
                    )
    statuses = data.get("statuses") if isinstance(data.get("statuses"), dict) else {}
    missing_steps = sorted(required_steps.difference(statuses))
    bad_steps = {
        key: statuses.get(key)
        for key in sorted(required_steps.intersection(statuses))
        if statuses.get(key) != "PASS"
    }
    if missing_steps:
        reasons.append("missing_steps=" + ",".join(missing_steps))
    if bad_steps:
        reasons.append("bad_steps=" + ",".join(f"{key}:{value}" for key, value in bad_steps.items()))
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
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
    range_details = uniformity_details.get("ranges") if isinstance(uniformity_details.get("ranges"), dict) else {}
    if not range_details:
        reasons.append("uniformity.ranges=MISSING")
    for feature_name, (target_min, target_max) in expected_target_ranges.items():
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
            if not (math.isclose(actual_min, target_min, rel_tol=0.0, abs_tol=1e-12) and math.isclose(actual_max, target_max, rel_tol=0.0, abs_tol=1e-12)):
                reasons.append(
                    f"uniformity.ranges.{feature_name}=({actual_min},{actual_max}),expected=({target_min},{target_max})"
                )
    one_d = uniformity_details.get("one_dimensional_uniformity") if isinstance(uniformity_details.get("one_dimensional_uniformity"), dict) else {}
    if not one_d:
        reasons.append("uniformity.one_dimensional_uniformity=MISSING")
    for feature_name in expected_target_ranges:
        item = one_d.get(feature_name) if isinstance(one_d.get(feature_name), dict) else {}
        if not item:
            reasons.append(f"uniformity.one_dimensional_uniformity.{feature_name}=MISSING")
            continue
        for metric_name, threshold_name, direction in (
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
            if direction == "min" and (not math.isfinite(metric_value) or metric_value < threshold_value):
                reasons.append(
                    f"uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}={metric_value:.6g},required={threshold_value}"
                )
            if direction == "max" and (not math.isfinite(metric_value) or metric_value > threshold_value):
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
            if not math.isfinite(occupied_fraction) or occupied_fraction < expected_min_four_d_occupied_frac:
                reasons.append(
                    f"uniformity.four_dimensional_uniformity.occupied_fraction={occupied_fraction:.6g},required={expected_min_four_d_occupied_frac}"
                )
        for metric_name, threshold_name, direction in (
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
            if direction == "min" and (not math.isfinite(metric_value) or metric_value < threshold_value):
                reasons.append(
                    f"uniformity.four_dimensional_uniformity.{metric_name}={metric_value:.6g},required={threshold_value}"
                )
            if direction == "max" and (not math.isfinite(metric_value) or metric_value > threshold_value):
                reasons.append(
                    f"uniformity.four_dimensional_uniformity.{metric_name}={metric_value:.6g},limit={threshold_value}"
                )
    k_diag = uniformity_details.get("k_sign_diagnostics") if isinstance(uniformity_details.get("k_sign_diagnostics"), dict) else None
    if uniformity_details.get("k_mode") != "magnitude":
        reasons.append(f"uniformity.k_mode={uniformity_details.get('k_mode')!r}")
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
    return ("PASS" if not reasons else "FAIL", reasons)

checkpoint_proof_status, checkpoint_proof_reasons = checkpoint_proof(checkpoint, count)
status = "PASS" if (
    queue_preflight.get("overall_status") == "PASS"
    and parallel.get("overall_status") == "PASS"
    and checkpoint_proof_status == "PASS"
    and audit.get("overall_status") == "PASS"
) else "FAIL"
summary = {
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "overall_status": status,
    "decision": "ACCEPT_AND_PROCEED_TO_NEXT_100K_CHUNK" if status == "PASS" else "STOP_BEFORE_NEXT_100K_CHUNK",
    "chunk_index": chunk_index,
    "expected_count": count,
    "strict_acceptance": {
        "candidate_queue_provenance_status": queue_preflight.get("overall_status"),
        "parallel_status": parallel.get("overall_status"),
        "checkpoint_proof": checkpoint_proof_status,
        "checkpoint_proof_reasons": checkpoint_proof_reasons,
        "audit_status": audit.get("overall_status"),
    },
    "candidate_csv": candidate_csv,
    "candidate_queue_provenance_summary": str(out_dir / "candidate_queue_preflight" / "mars56_s4p_candidate_queue_provenance_summary.json"),
    "dataset_summary": str(dataset_dir / "parallel_candidate_queue_dataset_summary.json"),
    "checkpoint_summary": str(checkpoint_dir / "mars56_s4p_physical_checkpoint_pipeline_summary.json"),
    "audit_summary": str(audit_dir / "mars56_s4p_million_chunk_checkpoint_summary.json"),
}
target = out_dir / "mars56_s4p_100k_chunk_run_summary.json"
target.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"summary={target}")
print(f"overall_status={status}")
PY
