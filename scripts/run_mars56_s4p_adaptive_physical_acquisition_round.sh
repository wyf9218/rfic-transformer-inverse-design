#!/usr/bin/env bash
set -euo pipefail

# Build one physical-feature targeted MARS56 S4P acquisition queue.
#
# This is the adaptive response-space step used when the accepted EMX pool is
# not yet uniform enough in Lp/Ls/Q/|K|. It does not run EMX and does not invent
# labels. It only:
#   1. plans sparse physical-feature target bins from real simulator rows;
#   2. builds or accepts predicted candidate geometries for those bins;
#   3. materializes a grounded-S4P geometry queue;
#   4. audits that the queue is traceably physical-feature targeted.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3}"
DATASET_DIR=""
OUT_DIR=""
QUEUE_COUNT=100000
CANDIDATE_COUNT=300000
PREDICTION_BATCH_SIZE=4096
SEED=2026070801
K_NEIGHBORS=8
LOCAL_TARGET_FRACTION=0.50
RARE_MARGINAL_FRACTION=0.20
PAIRWISE_FALLBACK_FRACTION=0.0
PAIRWISE_TARGET_FRACTION=""
PAIRWISE_FEATURE_PAIRS="lp_nh_center:q_center,ls_nh_center:q_center"
PAIRWISE_MARGINAL_FEATURES="q_center,k_abs_center"
RARE_MARGINAL_BINS=10
RARE_MARGINAL_FEATURE_WEIGHTS="0.5,0.5,2.0,1.5"
LOCAL_SEED_COUNT=8
LOCAL_PERTURBATION_SCALES="0.01,0.03,0.08"
LOCAL_SEED_ANCHOR_STRENGTH=0.95
LOCAL_SEED_ANCHOR_RADIUS=0.03
BINS=4
TARGET_COUNT_PER_BIN=""
DESIRED_TOTAL_COUNT=""
MAX_TARGET_BINS=""
FEATURE_COLUMNS="lp_nh_center,ls_nh_center,q_center,k_abs_center"
GEOMETRY_COLUMNS="geom__primary_outer_width_um,geom__primary_outer_height_um,geom__secondary_outer_width_um,geom__secondary_outer_height_um,geom__line_width_um,geom__primary_terminal_y_span_um,geom__secondary_terminal_y_span_um,geom__offset_um,geom__primary_feed_extension_um,geom__secondary_feed_extension_um"
TARGET_ENVELOPE_CONFIG=""
CANDIDATE_PREDICTIONS_CSV=""
PREDICTION_CALIBRATION_JSON="${PREDICTION_CALIBRATION_JSON:-}"
ACQUISITION_MIX_JSON="${ACQUISITION_MIX_JSON:-}"
ALLOW_OUTSIDE_BIN=0
REACHABLE_TARGETS_ONLY=1
REDISTRIBUTE_REACHABLE_QUOTA=1
MIN_CANDIDATES_PER_REACHABLE_TARGET=1
REQUIRE_INSIDE_TARGET_BIN=1
NO_FAIL_EXIT=0

usage() {
  cat <<'USAGE'
Usage:
  run_mars56_s4p_adaptive_physical_acquisition_round.sh \
    --dataset-dir DATASET_DIR \
    --out-dir OUT_DIR \
    --queue-count 100000

Optional:
  --candidate-predictions-csv CSV   reuse precomputed predicted candidate geometries
  --prediction-calibration-json JSON
                                      approved real-EMX holdout calibration for acquisition only
  --acquisition-mix-json JSON          audited five-arm production mix; must explicitly authorize launch
  --feature-columns COLUMNS         default lp_nh_center,ls_nh_center,q_center,k_abs_center
  --geometry-columns COLUMNS        independent 10-variable geometry contract
  --bins N                          feature bins per axis, default 4
  --target-envelope-config JSON
  --candidate-count N               surrogate candidate pool size, default 300000
  --local-target-fraction F         fraction for coarse 4D sparse targets, default 0.50
  --rare-marginal-fraction F        fraction for underfilled 10-bin marginal seeds, default 0.20
  --pairwise-fallback-fraction F     reserve queue share for Lp-Q/Ls-Q and Q/K deficits, default 0
  --pairwise-target-fraction F       candidate-pool share generated near pairwise gaps;
                                      defaults to --pairwise-fallback-fraction
  --pairwise-feature-pairs PAIRS     default lp_nh_center:q_center,ls_nh_center:q_center
  --pairwise-marginal-features CSV   default q_center,k_abs_center
  --rare-marginal-bins N            marginal bins per physical feature, default 10
  --local-seed-count N              nearest real-label seeds per sparse target, default 8
  --local-perturbation-scales CSV   geometry-span scales, default 0.01,0.03,0.08
  --local-seed-anchor-strength F    maximum local real-seed anchor weight, default 0.95
  --local-seed-anchor-radius F      normalized RMS decay radius, default 0.03
  --allow-outside-bin               allow nearest candidates outside target bins
  --no-require-inside-target-bin    materialize selected rows even if outside bins
  --no-fail-exit

Output:
  adaptive_physical_acquisition_round_summary.json
  queue/mars56_grounded_s4p_candidate_queue.csv
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset-dir) DATASET_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --queue-count) QUEUE_COUNT="$2"; shift 2 ;;
    --candidate-count) CANDIDATE_COUNT="$2"; shift 2 ;;
    --prediction-batch-size) PREDICTION_BATCH_SIZE="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    --k-neighbors) K_NEIGHBORS="$2"; shift 2 ;;
    --local-target-fraction) LOCAL_TARGET_FRACTION="$2"; shift 2 ;;
    --rare-marginal-fraction) RARE_MARGINAL_FRACTION="$2"; shift 2 ;;
    --pairwise-fallback-fraction) PAIRWISE_FALLBACK_FRACTION="$2"; shift 2 ;;
    --pairwise-target-fraction) PAIRWISE_TARGET_FRACTION="$2"; shift 2 ;;
    --pairwise-feature-pairs) PAIRWISE_FEATURE_PAIRS="$2"; shift 2 ;;
    --pairwise-marginal-features) PAIRWISE_MARGINAL_FEATURES="$2"; shift 2 ;;
    --rare-marginal-bins) RARE_MARGINAL_BINS="$2"; shift 2 ;;
    --rare-marginal-feature-weights) RARE_MARGINAL_FEATURE_WEIGHTS="$2"; shift 2 ;;
    --local-seed-count) LOCAL_SEED_COUNT="$2"; shift 2 ;;
    --local-perturbation-scales) LOCAL_PERTURBATION_SCALES="$2"; shift 2 ;;
    --local-seed-anchor-strength) LOCAL_SEED_ANCHOR_STRENGTH="$2"; shift 2 ;;
    --local-seed-anchor-radius) LOCAL_SEED_ANCHOR_RADIUS="$2"; shift 2 ;;
    --bins) BINS="$2"; shift 2 ;;
    --target-count-per-bin) TARGET_COUNT_PER_BIN="$2"; shift 2 ;;
    --desired-total-count) DESIRED_TOTAL_COUNT="$2"; shift 2 ;;
    --max-target-bins) MAX_TARGET_BINS="$2"; shift 2 ;;
    --feature-columns) FEATURE_COLUMNS="$2"; shift 2 ;;
    --geometry-columns) GEOMETRY_COLUMNS="$2"; shift 2 ;;
    --target-envelope-config) TARGET_ENVELOPE_CONFIG="$2"; shift 2 ;;
    --candidate-predictions-csv) CANDIDATE_PREDICTIONS_CSV="$2"; shift 2 ;;
    --prediction-calibration-json) PREDICTION_CALIBRATION_JSON="$2"; shift 2 ;;
    --acquisition-mix-json) ACQUISITION_MIX_JSON="$2"; shift 2 ;;
    --allow-outside-bin) ALLOW_OUTSIDE_BIN=1; shift ;;
    --reachable-targets-only) REACHABLE_TARGETS_ONLY=1; shift ;;
    --no-reachable-targets-only) REACHABLE_TARGETS_ONLY=0; shift ;;
    --redistribute-reachable-quota) REDISTRIBUTE_REACHABLE_QUOTA=1; shift ;;
    --no-redistribute-reachable-quota) REDISTRIBUTE_REACHABLE_QUOTA=0; shift ;;
    --min-candidates-per-reachable-target) MIN_CANDIDATES_PER_REACHABLE_TARGET="$2"; shift 2 ;;
    --require-inside-target-bin) REQUIRE_INSIDE_TARGET_BIN=1; shift ;;
    --no-require-inside-target-bin) REQUIRE_INSIDE_TARGET_BIN=0; shift ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --no-fail-exit) NO_FAIL_EXIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$PAIRWISE_TARGET_FRACTION" ]]; then
  PAIRWISE_TARGET_FRACTION="$PAIRWISE_FALLBACK_FRACTION"
fi

if [[ -z "$DATASET_DIR" || -z "$OUT_DIR" ]]; then
  echo "ERROR: --dataset-dir and --out-dir are required." >&2
  usage >&2
  exit 2
fi

DATASET_DIR="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$DATASET_DIR")"
OUT_DIR="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$OUT_DIR")"
mkdir -p "$OUT_DIR"

PLAN_DIR="$OUT_DIR/plan"
PRED_DIR="$OUT_DIR/predictions"
REACHABILITY_DIR="$OUT_DIR/reachability_consensus"
SELECTION_DIR="$OUT_DIR/selection"
QUEUE_DIR="$OUT_DIR/queue"
PROVENANCE_DIR="$OUT_DIR/provenance"
LOG="$OUT_DIR/adaptive_physical_acquisition_round_commands.log"

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

PLAN_CMD=(
  "$PYTHON_BIN" "$SCRIPT_DIR/plan_physical_feature_balanced_acquisition.py"
  "$DATASET_DIR"
  --out-dir "$PLAN_DIR"
  --feature-columns "$FEATURE_COLUMNS"
  --bins "$BINS"
  --next-count "$QUEUE_COUNT"
  --no-fail-exit
)
if [[ -n "$TARGET_ENVELOPE_CONFIG" ]]; then
  PLAN_CMD+=(--target-envelope-config "$TARGET_ENVELOPE_CONFIG")
fi
if [[ -n "$TARGET_COUNT_PER_BIN" ]]; then
  PLAN_CMD+=(--target-count-per-bin "$TARGET_COUNT_PER_BIN")
fi
if [[ -n "$DESIRED_TOTAL_COUNT" ]]; then
  PLAN_CMD+=(--desired-total-count "$DESIRED_TOTAL_COUNT")
fi
if [[ -n "$MAX_TARGET_BINS" ]]; then
  PLAN_CMD+=(--max-target-bins "$MAX_TARGET_BINS")
fi
run_step plan_sparse_physical_feature_bins "${PLAN_CMD[@]}"

if [[ -z "$CANDIDATE_PREDICTIONS_CSV" ]]; then
  run_step build_surrogate_candidate_predictions \
    "$PYTHON_BIN" "$SCRIPT_DIR/build_physical_feature_surrogate_candidate_predictions.py" \
    "$DATASET_DIR" \
    --out-dir "$PRED_DIR" \
    --candidate-count "$CANDIDATE_COUNT" \
    --prediction-batch-size "$PREDICTION_BATCH_SIZE" \
    --seed "$SEED" \
    --k-neighbors "$K_NEIGHBORS" \
    --feature-columns "$FEATURE_COLUMNS" \
    --geometry-columns "$GEOMETRY_COLUMNS" \
    --target-bins-csv "$PLAN_DIR/physical_feature_acquisition_targets.csv" \
    --local-target-fraction "$LOCAL_TARGET_FRACTION" \
    --rare-marginal-fraction "$RARE_MARGINAL_FRACTION" \
    --pairwise-target-fraction "$PAIRWISE_TARGET_FRACTION" \
    --pairwise-bins-csv "$PLAN_DIR/physical_feature_acquisition_bins.csv" \
    --pairwise-feature-pairs "$PAIRWISE_FEATURE_PAIRS" \
    --rare-marginal-bins "$RARE_MARGINAL_BINS" \
    --rare-marginal-feature-weights "$RARE_MARGINAL_FEATURE_WEIGHTS" \
    --local-seed-count "$LOCAL_SEED_COUNT" \
    --local-perturbation-scales "$LOCAL_PERTURBATION_SCALES" \
    --local-seed-anchor-strength "$LOCAL_SEED_ANCHOR_STRENGTH" \
    --local-seed-anchor-radius "$LOCAL_SEED_ANCHOR_RADIUS" \
    --no-fail-exit
  CANDIDATE_PREDICTIONS_CSV="$PRED_DIR/candidate_physical_feature_predictions.csv"
else
  CANDIDATE_PREDICTIONS_CSV="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$CANDIDATE_PREDICTIONS_CSV")"
fi
if [[ -n "$PREDICTION_CALIBRATION_JSON" ]]; then
  PREDICTION_CALIBRATION_JSON="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$PREDICTION_CALIBRATION_JSON")"
fi

# Candidate reachability consensus is advisory: it records recurring,
# uncertainty-robust proxy evidence but cannot remove target bins or change quota.
REACHABILITY_CMD=(
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_physical_feature_candidate_reachability_consensus.py"
  --bins-csv "$PLAN_DIR/physical_feature_acquisition_targets.csv"
  --candidate-csv "$CANDIDATE_PREDICTIONS_CSV"
  --out-dir "$REACHABILITY_DIR"
  --no-fail-exit
)
if [[ -n "$PREDICTION_CALIBRATION_JSON" ]]; then
  REACHABILITY_CMD+=(--prediction-calibration-json "$PREDICTION_CALIBRATION_JSON")
fi
run_step audit_candidate_reachability_consensus "${REACHABILITY_CMD[@]}"

RARE_SELECTION_COUNT="$($PYTHON_BIN - "$QUEUE_COUNT" "$RARE_MARGINAL_FRACTION" <<'PY'
import sys
print(int(round(int(sys.argv[1]) * float(sys.argv[2]))))
PY
)"
PAIRWISE_SELECTION_COUNT="$($PYTHON_BIN - "$QUEUE_COUNT" "$PAIRWISE_FALLBACK_FRACTION" <<'PY'
import sys
print(int(round(int(sys.argv[1]) * float(sys.argv[2]))))
PY
)"
RANDOM_SELECTION_COUNT=0
DIVERSITY_SELECTION_COUNT=0
COARSE_SELECTION_COUNT=$((QUEUE_COUNT - RARE_SELECTION_COUNT - PAIRWISE_SELECTION_COUNT))
if [[ -n "$ACQUISITION_MIX_JSON" ]]; then
  ACQUISITION_MIX_JSON="$($PYTHON_BIN -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$ACQUISITION_MIX_JSON")"
  read -r COARSE_SELECTION_COUNT RARE_SELECTION_COUNT PAIRWISE_SELECTION_COUNT RANDOM_SELECTION_COUNT DIVERSITY_SELECTION_COUNT < <(
    "$PYTHON_BIN" - "$ACQUISITION_MIX_JSON" "$QUEUE_COUNT" <<'PY'
import json,pathlib,sys
path=pathlib.Path(sys.argv[1]); expected=int(sys.argv[2])
if not path.is_file(): raise SystemExit("missing acquisition mix contract")
data=json.loads(path.read_text(encoding="utf-8"))
mix=data.get("production_acquisition_mix") or {}
counts=mix.get("counts") or {}
keys=("coarse_4d","rare_marginal","pairwise_gap","random_exploration","geometry_diversity")
checks=(
    data.get("overall_status")=="PASS",
    data.get("automatic_command_authorized") is True,
    int(mix.get("queue_count") or 0)==expected,
    set(counts)==set(keys),
    all(isinstance(counts.get(key),int) and counts[key]>=0 for key in keys),
    sum(int(counts.get(key) or 0) for key in keys)==expected,
    data.get("proxy_values_are_acquisition_only") is True,
)
if not all(checks): raise SystemExit("invalid or unauthorized acquisition mix contract")
print(*(counts[key] for key in keys))
PY
  ) || { echo "ERROR: acquisition mix contract rejected" >&2; exit 2; }
  SELECT_CMD=(
    "$PYTHON_BIN" "$SCRIPT_DIR/select_physical_feature_acquisition_mix.py"
    --plan-dir "$PLAN_DIR"
    --candidate-csv "$CANDIDATE_PREDICTIONS_CSV"
    --accepted-dataset-dir "$DATASET_DIR"
    --out-dir "$SELECTION_DIR"
    --feature-columns "$FEATURE_COLUMNS"
    --geometry-columns "$GEOMETRY_COLUMNS"
    --max-total "$QUEUE_COUNT"
    --coarse-4d-max-total "$COARSE_SELECTION_COUNT"
    --rare-marginal-max-total "$RARE_SELECTION_COUNT"
    --pairwise-gap-max-total "$PAIRWISE_SELECTION_COUNT"
    --random-exploration-max-total "$RANDOM_SELECTION_COUNT"
    --geometry-diversity-max-total "$DIVERSITY_SELECTION_COUNT"
    --pairwise-feature-pairs "$PAIRWISE_FEATURE_PAIRS"
    --pairwise-marginal-features "$PAIRWISE_MARGINAL_FEATURES"
    --seed "$SEED"
    --min-candidates-per-reachable-target "$MIN_CANDIDATES_PER_REACHABLE_TARGET"
    --no-fail-exit
  )
else
  SELECT_CMD=(
    "$PYTHON_BIN" "$SCRIPT_DIR/select_physical_feature_targeted_candidate_geometries.py"
    --plan-dir "$PLAN_DIR"
    --candidate-csv "$CANDIDATE_PREDICTIONS_CSV"
    --out-dir "$SELECTION_DIR"
    --feature-columns "$FEATURE_COLUMNS"
    --max-total "$QUEUE_COUNT"
    --no-fail-exit
  )
  if [[ "$RARE_SELECTION_COUNT" -gt 0 ]]; then
    SELECT_CMD+=(--rare-marginal-max-total "$RARE_SELECTION_COUNT")
  fi
  if [[ "$PAIRWISE_SELECTION_COUNT" -gt 0 ]]; then
    SELECT_CMD+=(
      --pairwise-fallback-max-total "$PAIRWISE_SELECTION_COUNT"
      --pairwise-feature-pairs "$PAIRWISE_FEATURE_PAIRS"
      --pairwise-marginal-features "$PAIRWISE_MARGINAL_FEATURES"
    )
  fi
  if [[ "$ALLOW_OUTSIDE_BIN" == "1" ]]; then
    SELECT_CMD+=(--allow-outside-bin)
  fi
fi
if [[ -n "$PREDICTION_CALIBRATION_JSON" ]]; then
  SELECT_CMD+=(--prediction-calibration-json "$PREDICTION_CALIBRATION_JSON")
fi
if [[ "$REACHABLE_TARGETS_ONLY" == "1" ]]; then
  SELECT_CMD+=(--reachable-targets-only)
else
  [[ -n "$ACQUISITION_MIX_JSON" ]] && SELECT_CMD+=(--no-reachable-targets-only)
fi
if [[ "$REDISTRIBUTE_REACHABLE_QUOTA" == "1" ]]; then
  SELECT_CMD+=(--redistribute-reachable-quota)
else
  [[ -n "$ACQUISITION_MIX_JSON" ]] && SELECT_CMD+=(--no-redistribute-reachable-quota)
fi
if [[ -z "$ACQUISITION_MIX_JSON" && -n "$MIN_CANDIDATES_PER_REACHABLE_TARGET" ]]; then
  SELECT_CMD+=(--min-candidates-per-reachable-target "$MIN_CANDIDATES_PER_REACHABLE_TARGET")
fi
run_step select_targeted_candidate_geometries "${SELECT_CMD[@]}"

MATERIALIZE_CMD=(
  "$PYTHON_BIN" "$SCRIPT_DIR/materialize_physical_feature_targeted_s4p_queue.py"
  --selection-csv "$SELECTION_DIR/physical_feature_targeted_candidate_selection.csv"
  --out-dir "$QUEUE_DIR"
  --max-count "$QUEUE_COUNT"
  --expected-count "$QUEUE_COUNT"
  --no-fail-exit
)
if [[ "$REQUIRE_INSIDE_TARGET_BIN" == "1" ]]; then
  MATERIALIZE_CMD+=(--require-inside-target-bin)
  if [[ "$PAIRWISE_SELECTION_COUNT" -gt 0 ]]; then
    MATERIALIZE_CMD+=(--allow-pairwise-fallback)
  fi
  if [[ "$RANDOM_SELECTION_COUNT" -gt 0 ]]; then
    MATERIALIZE_CMD+=(--allow-random-exploration)
  fi
  if [[ "$DIVERSITY_SELECTION_COUNT" -gt 0 ]]; then
    MATERIALIZE_CMD+=(--allow-geometry-diversity)
  fi
else
  MATERIALIZE_CMD+=(--no-require-inside-target-bin)
fi
run_step materialize_grounded_s4p_queue "${MATERIALIZE_CMD[@]}"

run_step audit_queue_provenance \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_mars56_s4p_candidate_queue_provenance.py" \
  --candidate-csv "$QUEUE_DIR/mars56_grounded_s4p_candidate_queue.csv" \
  --expected-count "$QUEUE_COUNT" \
  --out-dir "$PROVENANCE_DIR" \
  --no-fail-exit

"$PYTHON_BIN" - "$OUT_DIR" "$DATASET_DIR" "$FEATURE_COLUMNS" "$QUEUE_COUNT" "$CANDIDATE_PREDICTIONS_CSV" "$ACQUISITION_MIX_JSON" <<'PY'
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

out_dir = pathlib.Path(sys.argv[1])
dataset_dir = pathlib.Path(sys.argv[2])
feature_columns = sys.argv[3]
queue_count = int(sys.argv[4])
candidate_predictions_csv = sys.argv[5]
acquisition_mix_json = pathlib.Path(sys.argv[6]) if sys.argv[6] else None

def read_json(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

plan = read_json(out_dir / "plan" / "physical_feature_acquisition_plan_summary.json")
pred = read_json(out_dir / "predictions" / "candidate_physical_feature_prediction_summary.json")
reachability = read_json(out_dir / "reachability_consensus" / "candidate_reachability_consensus_summary.json")
selection = read_json(out_dir / "selection" / "physical_feature_targeted_candidate_selection_summary.json")
queue = read_json(out_dir / "queue" / "mars56_grounded_s4p_candidate_queue_summary.json")
provenance = read_json(out_dir / "provenance" / "mars56_s4p_candidate_queue_provenance_summary.json")

prediction_status = pred.get("overall_status", "EXTERNAL_CANDIDATE_PREDICTIONS")
statuses = {
    "plan": plan.get("overall_status"),
    "predictions": prediction_status,
    "reachability_consensus_advisory": reachability.get("overall_status"),
    "selection": selection.get("overall_status"),
    "queue": queue.get("overall_status"),
    "provenance": provenance.get("overall_status"),
}
selected_count = int(selection.get("selected_candidate_count") or 0)
inside_count = int(selection.get("selected_inside_target_bin_count") or 0)
pairwise_count = int(selection.get("selected_pairwise_gap_count") or 0)
effective_targeted_count = inside_count + pairwise_count
mix_contract = selection.get("acquisition_mix_contract")
policy_eligible_count = int(selection.get("selected_policy_eligible_count") or 0)
effective_policy_count = policy_eligible_count if isinstance(mix_contract, dict) else effective_targeted_count
queue_count_actual = int(queue.get("sample_count") or 0)
queue_identity = queue.get("identity_audit") or {}
reasons = []
if statuses["plan"] not in {"PASS"}:
    reasons.append(f"plan={statuses['plan']!r}")
if statuses["predictions"] not in {"PASS", "EXTERNAL_CANDIDATE_PREDICTIONS"}:
    reasons.append(f"predictions={statuses['predictions']!r}")
if statuses["selection"] != "PASS":
    reasons.append(f"selection={statuses['selection']!r}")
if selected_count < queue_count:
    reasons.append(f"selected_candidate_count={selected_count}")
if effective_policy_count < queue_count:
    reasons.append(
        f"selected_acquisition_policy_count={effective_policy_count} "
        f"(full4d_or_marginal={inside_count}, pairwise={pairwise_count}, five_arm={policy_eligible_count})"
    )
if statuses["queue"] != "PASS":
    reasons.append(f"queue={statuses['queue']!r}")
if queue_count_actual != queue_count:
    reasons.append(f"queue.sample_count={queue_count_actual}")
if queue.get("require_unique_geometry") is not True:
    reasons.append("queue.require_unique_geometry is not true")
if queue.get("require_unique_source_id") is not True:
    reasons.append("queue.require_unique_source_id is not true")
if int(queue_identity.get("duplicate_geometry_extra_row_count") or 0) != 0:
    reasons.append(
        f"queue.duplicate_geometry_extra_row_count={queue_identity.get('duplicate_geometry_extra_row_count')}"
    )
if int(queue_identity.get("duplicate_source_candidate_id_extra_row_count") or 0) != 0:
    reasons.append(
        "queue.duplicate_source_candidate_id_extra_row_count="
        f"{queue_identity.get('duplicate_source_candidate_id_extra_row_count')}"
    )
if statuses["provenance"] != "PASS":
    reasons.append(f"provenance={statuses['provenance']!r}")

status = "PASS" if not reasons else "FAIL"
summary = {
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "overall_status": status,
    "decision": "USE_QUEUE_FOR_NEXT_EMX_ACQUISITION" if status == "PASS" else "DO_NOT_RUN_EMX_FIX_TARGETING_FIRST",
    "dataset_dir": str(dataset_dir),
    "feature_columns": feature_columns.split(","),
    "k_axis_policy": "|K| is represented by k_abs_center; it may be derived from signed k_center before planning/prediction.",
    "requested_queue_count": queue_count,
    "candidate_predictions_csv": candidate_predictions_csv,
    "acquisition_mix_contract_source": (
        {
            "path": str(acquisition_mix_json),
            "sha256": hashlib.sha256(acquisition_mix_json.read_bytes()).hexdigest(),
        }
        if acquisition_mix_json is not None and acquisition_mix_json.is_file()
        else None
    ),
    "prediction_calibration": selection.get("prediction_calibration"),
    "candidate_reachability_consensus": {
        "status": reachability.get("overall_status"),
        "decision": reachability.get("decision"),
        "classification_counts": reachability.get("classification_counts") or {},
        "scientific_boundary": reachability.get("scientific_boundary"),
        "advisory_only": True,
    },
    "statuses": statuses,
    "selected_candidate_count": selected_count,
    "selected_inside_target_bin_count": inside_count,
    "selected_pairwise_gap_count": pairwise_count,
    "selected_inside_or_pairwise_target_count": effective_targeted_count,
    "selected_acquisition_policy_count": effective_policy_count,
    "acquisition_mix_contract": mix_contract,
    "queue_count": queue_count_actual,
    "queue_identity_evidence": {
        "canonical_geometry_fields": queue.get("canonical_geometry_fields") or [],
        "geometry_fingerprint_schema": queue.get("geometry_fingerprint_schema"),
        "geometry_fingerprint_quantization_um": queue.get("geometry_fingerprint_quantization_um"),
        "require_unique_geometry": queue.get("require_unique_geometry"),
        "require_unique_source_id": queue.get("require_unique_source_id"),
        "identity_audit": queue_identity,
    },
    "failure_reasons": reasons,
    "artifacts": {
        "plan_summary": str(out_dir / "plan" / "physical_feature_acquisition_plan_summary.json"),
        "candidate_predictions_summary": str(out_dir / "predictions" / "candidate_physical_feature_prediction_summary.json"),
        "candidate_reachability_consensus_summary": str(out_dir / "reachability_consensus" / "candidate_reachability_consensus_summary.json"),
        "candidate_reachability_consensus_bins": str(out_dir / "reachability_consensus" / "candidate_reachability_consensus_bins.csv"),
        "selection_summary": str(out_dir / "selection" / "physical_feature_targeted_candidate_selection_summary.json"),
        "queue_csv": str(out_dir / "queue" / "mars56_grounded_s4p_candidate_queue.csv"),
        "queue_summary": str(out_dir / "queue" / "mars56_grounded_s4p_candidate_queue_summary.json"),
        "provenance_summary": str(out_dir / "provenance" / "mars56_s4p_candidate_queue_provenance_summary.json"),
    },
    "limitations": [
        "This round creates a geometry queue only; simulator labels are produced later by EMX.",
        "Surrogate predictions are for acquisition priority only and are never used as final training labels.",
        "Pairwise fallback targets declared marginal/pair gaps and does not claim full 4-D target-bin membership; real EMX labels remain mandatory.",
        "Any prediction calibration is accepted only after an independent-geometry real-EMX holdout audit and is used for candidate ranking, never as a training label.",
        "Candidate reachability consensus is advisory only: no-evidence bins remain in the final real-EMX uniformity denominator and cannot be declared physically impossible.",
        "Every materialized EMX row must have a unique canonical 10-D geometry SHA-256 and a unique source candidate ID before launch.",
    ],
}
(out_dir / "adaptive_physical_acquisition_round_summary.json").write_text(
    json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
print(f"summary={out_dir / 'adaptive_physical_acquisition_round_summary.json'}")
print(f"overall_status={status}")
print(f"decision={summary['decision']}")
if reasons:
    print("failure_reasons=" + "; ".join(reasons))
PY

summary_status="$("$PYTHON_BIN" - "$OUT_DIR/adaptive_physical_acquisition_round_summary.json" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("overall_status", "FAIL"))
except Exception:
    print("FAIL")
PY
)"
if [[ "$summary_status" != "PASS" && "$NO_FAIL_EXIT" != "1" ]]; then
  exit 2
fi
