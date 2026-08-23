#!/usr/bin/env bash
# Run one MARS56 grounded-S4P physical-feature checkpoint.
#
# This pipeline is intentionally made of existing auditable steps:
# stable Touchstone index -> response features -> geometry enrichment ->
# Lp/Ls/Q/K uniformity audit -> inverse-training table -> ridge baseline test.

set -u

COUNT=100000
MIN_VALID=100000
TARGET_GHZ=15
BINS=10
PAIR_BINS=10
MIN_1D_OCCUPIED_FRAC=0.90
MIN_1D_ENTROPY_FRAC=0.90
MAX_1D_BIN_IMBALANCE=2.50
MIN_PAIR_OCCUPIED_FRAC=0.65
MIN_PAIR_ENTROPY_FRAC=0.80
LP_MIN=0.5
LP_MAX=3
LS_MIN=0.5
LS_MAX=3
Q_MIN=5
Q_MAX=25
K_MIN=0
K_MAX=0.8
REQUIRE_FOUR_D_GATE=0
FOUR_D_BINS=4
MIN_FOUR_D_OCCUPIED_FRAC=0.50
MIN_FOUR_D_ENTROPY_FRAC=0.80
MAX_FOUR_D_BIN_IMBALANCE=4.0
CONFIG=""
DATASET_DIR=""
OUT_DIR=""
PYTHON_BIN="${PYTHON_BIN:-python3}"

usage() {
  cat <<'USAGE'
Usage:
  run_mars56_s4p_physical_checkpoint_pipeline.sh \
    --dataset-dir DATASET_DIR \
    --out-dir OUT_DIR \
    --config CONFIG_YAML \
    [--count 100000] [--min-valid 100000] [--python /path/to/python]

Outputs under OUT_DIR:
  stable_index/
  response_features/
  enriched_geometry/
  physical_feature_uniformity/
  physical_feature_inverse_training_table/
  physical_feature_inverse_checkpoint_test/
  mars56_s4p_physical_checkpoint_pipeline_summary.json
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset-dir) DATASET_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --count) COUNT="$2"; shift 2 ;;
    --min-valid) MIN_VALID="$2"; shift 2 ;;
    --target-ghz) TARGET_GHZ="$2"; shift 2 ;;
    --bins) BINS="$2"; shift 2 ;;
    --pair-bins) PAIR_BINS="$2"; shift 2 ;;
    --min-1d-occupied-frac) MIN_1D_OCCUPIED_FRAC="$2"; shift 2 ;;
    --min-1d-entropy-frac) MIN_1D_ENTROPY_FRAC="$2"; shift 2 ;;
    --max-1d-bin-imbalance) MAX_1D_BIN_IMBALANCE="$2"; shift 2 ;;
    --min-pair-occupied-frac) MIN_PAIR_OCCUPIED_FRAC="$2"; shift 2 ;;
    --min-pair-entropy-frac) MIN_PAIR_ENTROPY_FRAC="$2"; shift 2 ;;
    --lp-min) LP_MIN="$2"; shift 2 ;;
    --lp-max) LP_MAX="$2"; shift 2 ;;
    --ls-min) LS_MIN="$2"; shift 2 ;;
    --ls-max) LS_MAX="$2"; shift 2 ;;
    --q-min) Q_MIN="$2"; shift 2 ;;
    --q-max) Q_MAX="$2"; shift 2 ;;
    --k-min) K_MIN="$2"; shift 2 ;;
    --k-max) K_MAX="$2"; shift 2 ;;
    --require-four-d-gate) REQUIRE_FOUR_D_GATE=1; shift ;;
    --four-d-bins) FOUR_D_BINS="$2"; shift 2 ;;
    --min-four-d-occupied-frac) MIN_FOUR_D_OCCUPIED_FRAC="$2"; shift 2 ;;
    --min-four-d-entropy-frac) MIN_FOUR_D_ENTROPY_FRAC="$2"; shift 2 ;;
    --max-four-d-bin-imbalance) MAX_FOUR_D_BIN_IMBALANCE="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$DATASET_DIR" || -z "$OUT_DIR" || -z "$CONFIG" ]]; then
  echo "ERROR: --dataset-dir, --out-dir, and --config are required." >&2
  usage >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$OUT_DIR")"
mkdir -p "$OUT_DIR"

IDX="$OUT_DIR/stable_index"
FEAT="$OUT_DIR/response_features"
ENRICH="$OUT_DIR/enriched_geometry"
UNIF="$OUT_DIR/physical_feature_uniformity"
TRAIN="$OUT_DIR/physical_feature_inverse_training_table"
MODEL="$OUT_DIR/physical_feature_inverse_checkpoint_test"
TRACE="$OUT_DIR/physical_checkpoint_traceability"
LOG="$OUT_DIR/mars56_s4p_physical_checkpoint_pipeline_commands.log"

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

run_step stable_index \
  "$PYTHON_BIN" "$SCRIPT_DIR/build_stable_touchstone_index.py" "$DATASET_DIR" \
  --out-dir "$IDX" --max-count "$COUNT" --min-count "$COUNT" --clean --no-fail-exit

run_step response_features \
  "$PYTHON_BIN" "$SCRIPT_DIR/extract_touchstone_response_features.py" "$IDX" \
  --out-dir "$FEAT" \
  --expected-ports 4 \
  --expected-frequency-start-ghz 5 \
  --expected-frequency-stop-ghz 60 \
  --expected-frequency-step-ghz 0.5 \
  --expected-frequency-points 111 \
  --target-frequency-ghz "$TARGET_GHZ" \
  --no-fail-exit

run_step enrich_geometry \
  "$PYTHON_BIN" "$SCRIPT_DIR/enrich_response_features_with_geometry.py" \
  --features-csv "$FEAT/response_features.csv" \
  --out-dir "$ENRICH" \
  --q-definition min \
  --no-fail-exit

run_step physical_feature_uniformity \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_physical_feature_uniformity.py" \
  --training-csv "$ENRICH/dataset_rows.csv" \
  --out-dir "$UNIF" \
  --min-valid-count "$MIN_VALID" \
  --bins "$BINS" \
  --pair-bins "$PAIR_BINS" \
  --min-1d-occupied-frac "$MIN_1D_OCCUPIED_FRAC" \
  --min-1d-entropy-frac "$MIN_1D_ENTROPY_FRAC" \
  --max-1d-bin-imbalance "$MAX_1D_BIN_IMBALANCE" \
  --min-pair-occupied-frac "$MIN_PAIR_OCCUPIED_FRAC" \
  --min-pair-entropy-frac "$MIN_PAIR_ENTROPY_FRAC" \
  --k-mode magnitude \
  --lp-min-nh "$LP_MIN" --lp-max-nh "$LP_MAX" \
  --ls-min-nh "$LS_MIN" --ls-max-nh "$LS_MAX" \
  --q-min "$Q_MIN" --q-max "$Q_MAX" \
  --k-min "$K_MIN" --k-max "$K_MAX" \
  --four-d-bins "$FOUR_D_BINS" \
  --min-four-d-occupied-frac "$MIN_FOUR_D_OCCUPIED_FRAC" \
  --min-four-d-entropy-frac "$MIN_FOUR_D_ENTROPY_FRAC" \
  --max-four-d-bin-imbalance "$MAX_FOUR_D_BIN_IMBALANCE" \
  $([[ "$REQUIRE_FOUR_D_GATE" == "1" ]] && printf '%s' "--require-four-d-gate") \
  --require-explicit-ranges \
  --require-plots \
  --no-fail-exit

run_step inverse_training_table \
  "$PYTHON_BIN" "$SCRIPT_DIR/build_physical_feature_inverse_training_table.py" "$ENRICH" \
  --out-dir "$TRAIN" \
  --config "$CONFIG" \
  --input-prefix input__ \
  --check-touchstone-exists \
  --no-fail-exit

run_step inverse_model_checkpoint \
  "$PYTHON_BIN" "$SCRIPT_DIR/run_physical_feature_inverse_checkpoint_test.py" \
  --training-csv "$TRAIN/physical_feature_inverse_training_table.csv" \
  --out-dir "$MODEL" \
  --input-prefix input__ \
  --min-training-rows "$MIN_VALID" \
  --max-train-rows 80000 \
  --max-test-rows 20000 \
  --no-fail-exit

run_step physical_checkpoint_traceability \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_physical_checkpoint_traceability.py" \
  --checkpoint-dir "$OUT_DIR" \
  --dataset-dir "$DATASET_DIR" \
  --expected-count "$COUNT" \
  --min-valid "$MIN_VALID" \
  --no-fail-exit

python3 - "$OUT_DIR" "$COUNT" "$MIN_VALID" "$BINS" "$PAIR_BINS" "$FOUR_D_BINS" "$MIN_FOUR_D_OCCUPIED_FRAC" "$LP_MIN" "$LP_MAX" "$LS_MIN" "$LS_MAX" "$Q_MIN" "$Q_MAX" "$K_MIN" "$K_MAX" "$REQUIRE_FOUR_D_GATE" "$MIN_1D_OCCUPIED_FRAC" "$MIN_1D_ENTROPY_FRAC" "$MAX_1D_BIN_IMBALANCE" "$MIN_PAIR_OCCUPIED_FRAC" "$MIN_PAIR_ENTROPY_FRAC" "$MIN_FOUR_D_ENTROPY_FRAC" "$MAX_FOUR_D_BIN_IMBALANCE" <<'PY'
import json
import math
import pathlib
import sys
from datetime import datetime, timezone

out_dir = pathlib.Path(sys.argv[1])
count = int(sys.argv[2])
min_valid = int(sys.argv[3])
bins = int(sys.argv[4])
pair_bins = int(sys.argv[5])
four_d_bins = int(sys.argv[6])
min_four_d_occupied_frac = float(sys.argv[7])
target_ranges = {
    "lp": (float(sys.argv[8]), float(sys.argv[9])),
    "ls": (float(sys.argv[10]), float(sys.argv[11])),
    "q": (float(sys.argv[12]), float(sys.argv[13])),
    "k": (float(sys.argv[14]), float(sys.argv[15])),
}
require_four_d_gate = sys.argv[16] == "1"
min_1d_occupied_frac = float(sys.argv[17])
min_1d_entropy_frac = float(sys.argv[18])
max_1d_bin_imbalance = float(sys.argv[19])
min_pair_occupied_frac = float(sys.argv[20])
min_pair_entropy_frac = float(sys.argv[21])
min_four_d_entropy_frac = float(sys.argv[22])
max_four_d_bin_imbalance = float(sys.argv[23])
physical_uniformity_gate = {
    "k_mode": "magnitude",
    "bins": bins,
    "pair_bins": pair_bins,
    "min_1d_occupied_fraction": min_1d_occupied_frac,
    "min_1d_entropy_fraction": min_1d_entropy_frac,
    "max_1d_bin_imbalance": max_1d_bin_imbalance,
    "min_pair_occupied_fraction": min_pair_occupied_frac,
    "min_pair_entropy_fraction": min_pair_entropy_frac,
    "four_d_bins": four_d_bins,
    "require_four_d_gate": require_four_d_gate,
    "min_four_d_occupied_fraction": min_four_d_occupied_frac,
    "min_four_d_normalized_entropy": min_four_d_entropy_frac,
    "max_four_d_nonzero_bin_imbalance": max_four_d_bin_imbalance,
    "target_ranges": {
        name: {"min": lo, "max": hi}
        for name, (lo, hi) in target_ranges.items()
    },
}
paths = {
    "stable_index": out_dir / "stable_index",
    "response_features": out_dir / "response_features" / "response_feature_extraction_summary.json",
    "enrichment": out_dir / "enriched_geometry" / "geometry_enrichment_manifest.json",
    "uniformity": out_dir / "physical_feature_uniformity" / "physical_feature_uniformity_summary.json",
    "uniformity_manifest": out_dir / "physical_feature_uniformity" / "physical_feature_uniformity_manifest.json",
    "training": out_dir / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json",
    "model": out_dir / "physical_feature_inverse_checkpoint_test" / "physical_feature_inverse_checkpoint_test_summary.json",
    "traceability": out_dir / "physical_checkpoint_traceability" / "physical_checkpoint_traceability_summary.json",
}
statuses = {}
details = {}
for name, path in paths.items():
    if path.is_dir():
        indexed = len(list(path.glob("evaluations/*/emx/*.s4p")))
        statuses[name] = "PASS" if indexed >= count else "FAIL"
        details[name] = {"path": str(path), "indexed_s4p": indexed}
    elif not path.exists():
        statuses[name] = "MISSING"
        details[name] = {"path": str(path)}
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            statuses[name] = f"PARSE_ERROR:{type(exc).__name__}"
            details[name] = {"path": str(path)}
        else:
            statuses[name] = str(data.get("overall_status") or "NO_STATUS")
            details[name] = {"path": str(path)}
            for key in ("valid_feature_count", "training_count", "usable_row_count", "test_row_count"):
                if key in data:
                    details[name][key] = data[key]
            if name == "uniformity":
                for key in (
                    "k_mode",
                    "k_sign_diagnostics",
                    "ranges",
                    "one_dimensional_uniformity",
                    "pairwise_uniformity",
                    "four_dimensional_uniformity",
                    "checks",
                ):
                    if key in data:
                        details[name][key] = data[key]
            if name == "traceability":
                for key in ("expected_count", "min_valid"):
                    if key in data:
                        details[name][key] = data[key]
                row_counts = data.get("row_counts") if isinstance(data.get("row_counts"), dict) else {}
                for key in (
                    "stable_manifest_rows",
                    "stable_unique_evaluations",
                    "response_feature_rows",
                    "response_unique_evaluations",
                    "response_dataset_rows",
                    "response_dataset_unique_evaluations",
                    "enriched_rows",
                    "enriched_unique_evaluations",
                    "training_rows",
                    "training_unique_evaluations",
                ):
                    if key in row_counts:
                        details[name][key] = row_counts[key]
            if name == "uniformity_manifest":
                details[name]["visual_artifact_count"] = data.get("visual_artifact_count")
                details[name]["require_plots"] = data.get("require_plots")
            if name == "model":
                for key in ("quality_status", "decision", "method", "metrics"):
                    if key in data:
                        details[name][key] = data[key]

proof_reasons = []
bad_statuses = {name: value for name, value in statuses.items() if value != "PASS"}
if bad_statuses:
    proof_reasons.append(
        "bad_statuses=" + ",".join(f"{name}:{value}" for name, value in sorted(bad_statuses.items()))
    )

for step, key, threshold in (
    ("uniformity", "valid_feature_count", min_valid),
    ("training", "training_count", min_valid),
    ("model", "usable_row_count", min_valid),
):
    step_details = details.get(step) if isinstance(details.get(step), dict) else {}
    if key not in step_details:
        proof_reasons.append(f"{step}.{key}=MISSING")
        continue
    try:
        value = int(step_details[key])
    except Exception:
        proof_reasons.append(f"{step}.{key}={step_details[key]!r}")
    else:
        if value < threshold:
            proof_reasons.append(f"{step}.{key}={value}")

model_details = details.get("model") if isinstance(details.get("model"), dict) else {}
try:
    model_test_rows = int(model_details.get("test_row_count"))
except Exception:
    proof_reasons.append(f"model.test_row_count={model_details.get('test_row_count')!r}")
    model_test_rows = None
else:
    if model_test_rows <= 0:
        proof_reasons.append(f"model.test_row_count={model_test_rows}")
model_metrics = model_details.get("metrics") if isinstance(model_details.get("metrics"), dict) else {}
if not model_metrics:
    proof_reasons.append("model.metrics=MISSING")
else:
    try:
        metric_test_count = int(model_metrics.get("test_count"))
    except Exception:
        proof_reasons.append(f"model.metrics.test_count={model_metrics.get('test_count')!r}")
    else:
        if metric_test_count <= 0:
            proof_reasons.append(f"model.metrics.test_count={metric_test_count}")
        if model_test_rows is not None and metric_test_count != model_test_rows:
            proof_reasons.append(f"model.metrics.test_count={metric_test_count},expected={model_test_rows}")
    try:
        geometry_count = int(model_metrics.get("geometry_count"))
    except Exception:
        proof_reasons.append(f"model.metrics.geometry_count={model_metrics.get('geometry_count')!r}")
    else:
        if geometry_count <= 0:
            proof_reasons.append(f"model.metrics.geometry_count={geometry_count}")
    for metric_key in (
        "max_normalized_mae",
        "max_normalized_rmse",
        "mean_normalized_mae",
        "mean_normalized_rmse",
    ):
        try:
            metric_value = float(model_metrics.get(metric_key))
        except Exception:
            proof_reasons.append(f"model.metrics.{metric_key}={model_metrics.get(metric_key)!r}")
        else:
            if not math.isfinite(metric_value):
                proof_reasons.append(f"model.metrics.{metric_key}={metric_value!r}")

trace_details = details.get("traceability") if isinstance(details.get("traceability"), dict) else {}
if not trace_details:
    proof_reasons.append("traceability.details_missing")
for key in ("stable_manifest_rows", "stable_unique_evaluations", "response_feature_rows", "response_unique_evaluations", "response_dataset_rows", "response_dataset_unique_evaluations", "enriched_rows", "enriched_unique_evaluations", "training_rows", "training_unique_evaluations"):
    if key not in trace_details:
        proof_reasons.append(f"traceability.{key}=MISSING")
        continue
    try:
        value = int(trace_details[key])
    except Exception:
        proof_reasons.append(f"traceability.{key}={trace_details[key]!r}")
    else:
        if value < count:
            proof_reasons.append(f"traceability.{key}={value}")

manifest_details = details.get("uniformity_manifest") if isinstance(details.get("uniformity_manifest"), dict) else {}
try:
    visual_artifact_count = int(manifest_details.get("visual_artifact_count"))
except Exception:
    proof_reasons.append(f"uniformity_manifest.visual_artifact_count={manifest_details.get('visual_artifact_count')!r}")
else:
    if visual_artifact_count < 3:
        proof_reasons.append(f"uniformity_manifest.visual_artifact_count={visual_artifact_count}")
if manifest_details.get("require_plots") is not True:
    proof_reasons.append(f"uniformity_manifest.require_plots={manifest_details.get('require_plots')!r}")

uniformity_details = details.get("uniformity") if isinstance(details.get("uniformity"), dict) else {}
k_diag = uniformity_details.get("k_sign_diagnostics") if isinstance(uniformity_details.get("k_sign_diagnostics"), dict) else None
if uniformity_details.get("k_mode") != "magnitude":
    proof_reasons.append(f"uniformity.k_mode={uniformity_details.get('k_mode')!r}")
range_details = uniformity_details.get("ranges") if isinstance(uniformity_details.get("ranges"), dict) else {}
if not range_details:
    proof_reasons.append("uniformity.ranges=MISSING")
for feature_name, (target_min, target_max) in target_ranges.items():
    item = range_details.get(feature_name) if isinstance(range_details.get(feature_name), dict) else {}
    if not item:
        proof_reasons.append(f"uniformity.ranges.{feature_name}=MISSING")
        continue
    if item.get("explicit") is not True or item.get("source") != "explicit":
        proof_reasons.append(f"uniformity.ranges.{feature_name}.explicit={item.get('explicit')!r},source={item.get('source')!r}")
    try:
        actual_min = float(item.get("min"))
        actual_max = float(item.get("max"))
    except Exception:
        proof_reasons.append(f"uniformity.ranges.{feature_name}.bounds={item!r}")
    else:
        if not (math.isclose(actual_min, target_min, rel_tol=0.0, abs_tol=1e-12) and math.isclose(actual_max, target_max, rel_tol=0.0, abs_tol=1e-12)):
            proof_reasons.append(
                f"uniformity.ranges.{feature_name}=({actual_min},{actual_max}),expected=({target_min},{target_max})"
            )
one_d = uniformity_details.get("one_dimensional_uniformity") if isinstance(uniformity_details.get("one_dimensional_uniformity"), dict) else {}
if not one_d:
    proof_reasons.append("uniformity.one_dimensional_uniformity=MISSING")
for feature_name in target_ranges:
    item = one_d.get(feature_name) if isinstance(one_d.get(feature_name), dict) else {}
    if not item:
        proof_reasons.append(f"uniformity.one_dimensional_uniformity.{feature_name}=MISSING")
        continue
    try:
        occupied_fraction = float(item.get("occupied_fraction"))
    except Exception:
        proof_reasons.append(f"uniformity.one_dimensional_uniformity.{feature_name}.occupied_fraction={item.get('occupied_fraction')!r}")
    else:
        if not math.isfinite(occupied_fraction) or occupied_fraction < min_1d_occupied_frac:
            proof_reasons.append(
                f"uniformity.one_dimensional_uniformity.{feature_name}.occupied_fraction={occupied_fraction:.6g},required={min_1d_occupied_frac}"
            )
    try:
        entropy_fraction = float(item.get("normalized_entropy"))
    except Exception:
        proof_reasons.append(f"uniformity.one_dimensional_uniformity.{feature_name}.normalized_entropy={item.get('normalized_entropy')!r}")
    else:
        if not math.isfinite(entropy_fraction) or entropy_fraction < min_1d_entropy_frac:
            proof_reasons.append(
                f"uniformity.one_dimensional_uniformity.{feature_name}.normalized_entropy={entropy_fraction:.6g},required={min_1d_entropy_frac}"
            )
    try:
        bin_imbalance = float(item.get("max_to_min_nonzero_ratio"))
    except Exception:
        proof_reasons.append(f"uniformity.one_dimensional_uniformity.{feature_name}.max_to_min_nonzero_ratio={item.get('max_to_min_nonzero_ratio')!r}")
    else:
        if not math.isfinite(bin_imbalance) or bin_imbalance > max_1d_bin_imbalance:
            proof_reasons.append(
                f"uniformity.one_dimensional_uniformity.{feature_name}.max_to_min_nonzero_ratio={bin_imbalance:.6g},limit={max_1d_bin_imbalance}"
            )
pairwise = uniformity_details.get("pairwise_uniformity") if isinstance(uniformity_details.get("pairwise_uniformity"), dict) else {}
if not pairwise:
    proof_reasons.append("uniformity.pairwise_uniformity=MISSING")
for pair_name, item in pairwise.items():
    if not isinstance(item, dict):
        proof_reasons.append(f"uniformity.pairwise_uniformity.{pair_name}={item!r}")
        continue
    try:
        occupied_fraction = float(item.get("occupied_fraction"))
    except Exception:
        proof_reasons.append(f"uniformity.pairwise_uniformity.{pair_name}.occupied_fraction={item.get('occupied_fraction')!r}")
    else:
        if not math.isfinite(occupied_fraction) or occupied_fraction < min_pair_occupied_frac:
            proof_reasons.append(
                f"uniformity.pairwise_uniformity.{pair_name}.occupied_fraction={occupied_fraction:.6g},required={min_pair_occupied_frac}"
            )
    try:
        entropy_fraction = float(item.get("normalized_entropy"))
    except Exception:
        proof_reasons.append(f"uniformity.pairwise_uniformity.{pair_name}.normalized_entropy={item.get('normalized_entropy')!r}")
    else:
        if not math.isfinite(entropy_fraction) or entropy_fraction < min_pair_entropy_frac:
            proof_reasons.append(
                f"uniformity.pairwise_uniformity.{pair_name}.normalized_entropy={entropy_fraction:.6g},required={min_pair_entropy_frac}"
            )
four_d = uniformity_details.get("four_dimensional_uniformity") if isinstance(uniformity_details.get("four_dimensional_uniformity"), dict) else {}
if require_four_d_gate:
    if not four_d:
        proof_reasons.append("uniformity.four_dimensional_uniformity=MISSING")
    else:
        try:
            occupied_fraction = float(four_d.get("occupied_fraction"))
        except Exception:
            proof_reasons.append(f"uniformity.four_dimensional_uniformity.occupied_fraction={four_d.get('occupied_fraction')!r}")
        else:
            if not math.isfinite(occupied_fraction) or occupied_fraction < min_four_d_occupied_frac:
                proof_reasons.append(
                    f"uniformity.four_dimensional_uniformity.occupied_fraction={occupied_fraction:.6g},required={min_four_d_occupied_frac}"
                )
        try:
            entropy_fraction = float(four_d.get("normalized_entropy"))
        except Exception:
            proof_reasons.append(
                f"uniformity.four_dimensional_uniformity.normalized_entropy={four_d.get('normalized_entropy')!r}"
            )
        else:
            if not math.isfinite(entropy_fraction) or entropy_fraction < min_four_d_entropy_frac:
                proof_reasons.append(
                    f"uniformity.four_dimensional_uniformity.normalized_entropy={entropy_fraction:.6g},required={min_four_d_entropy_frac}"
                )
        try:
            bin_imbalance = float(four_d.get("max_to_min_nonzero_ratio"))
        except Exception:
            proof_reasons.append(
                f"uniformity.four_dimensional_uniformity.max_to_min_nonzero_ratio={four_d.get('max_to_min_nonzero_ratio')!r}"
            )
        else:
            if not math.isfinite(bin_imbalance) or bin_imbalance > max_four_d_bin_imbalance:
                proof_reasons.append(
                    f"uniformity.four_dimensional_uniformity.max_to_min_nonzero_ratio={bin_imbalance:.6g},limit={max_four_d_bin_imbalance}"
                )
if not k_diag:
    proof_reasons.append("uniformity.k_sign_diagnostics=MISSING")
else:
    if k_diag.get("uniformity_k_axis") != "|K|":
        proof_reasons.append(f"uniformity.k_sign_diagnostics.uniformity_k_axis={k_diag.get('uniformity_k_axis')!r}")
    try:
        signed_k_count = int(k_diag.get("signed_k_count"))
    except Exception:
        proof_reasons.append(f"uniformity.k_sign_diagnostics.signed_k_count={k_diag.get('signed_k_count')!r}")
    else:
        if signed_k_count < min_valid:
            proof_reasons.append(f"uniformity.k_sign_diagnostics.signed_k_count={signed_k_count}")

overall_status = "PASS" if not proof_reasons else "FAIL"
summary = {
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "overall_status": overall_status,
    "decision": "ACCEPT_CHUNK_AND_CONTINUE" if overall_status == "PASS" else "STOP_OR_REPAIR_CHUNK",
    "expected_count": count,
    "min_valid": min_valid,
    "physical_uniformity_gate": physical_uniformity_gate,
    "out_dir": str(out_dir),
    "statuses": statuses,
    "details": details,
    "proof_reasons": proof_reasons,
}
target = out_dir / "mars56_s4p_physical_checkpoint_pipeline_summary.json"
target.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"summary={target}")
print(f"overall_status={summary['overall_status']}")
print(f"decision={summary['decision']}")
PY
