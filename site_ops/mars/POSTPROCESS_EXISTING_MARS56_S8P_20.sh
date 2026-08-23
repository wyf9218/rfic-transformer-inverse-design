#!/usr/bin/env bash
set -euo pipefail

locate_repo() {
  for candidate in \
    "${REPO:-}" \
    "$PWD" \
    "$PWD/rfic-transformer-inverse-design" \
    "$HOME/researcher/transformer_inverse/rfic-transformer-inverse-design" \
    "$HOME/transformer_inverse/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/transformer_inverse/rfic-transformer-inverse-design"; do
    if [[ -n "$candidate" && -f "$candidate/pyproject.toml" && -d "$candidate/rfic_transformer_inverse_design" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

REPO_ROOT="${REPO_ROOT:-$(locate_repo)}"
cd "$REPO_ROOT"

if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
  else
    PYTHON="$(command -v python)"
  fi
fi

EXEC_PACKET="${EXEC_PACKET:-next_gen_s8p_mars_execution_packet_20260630_5_60_1p0_grounded_tap_20pilot}"
RUN_DIR="${RUN_DIR:-$REPO_ROOT/$EXEC_PACKET/new_s8p_physical_feature_emx_20_5_60_1p0_grounded_tap}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
QUALITY_DIR="${QUALITY_DIR:-$RUN_DIR/dataset_quality_gates_s8p_physical_feature_postprocess_${STAMP}}"

echo "POSTPROCESS_MARS56_START $(date)"
echo "REPO_ROOT=$REPO_ROOT"
echo "PYTHON=$PYTHON"
echo "RUN_DIR=$RUN_DIR"
echo "QUALITY_DIR=$QUALITY_DIR"
echo "FREQUENCY_CONTRACT=5-60GHz inclusive, 1GHz step, 56 points"
echo "TOUCHSTONE_CONTRACT=.s8p, 8 ports"
echo "ACTIVE_RF_PAIRS=P1-P4:P5-P6"
echo "AC_GROUNDED_UNUSED_PORTS=P2,P3,P7,P8"

if [[ ! -d "$RUN_DIR" ]]; then
  echo "ERROR: run directory not found: $RUN_DIR" >&2
  exit 24
fi

"$PYTHON" "$REPO_ROOT/scripts/run_dataset_quality_gates.py" \
  "$RUN_DIR" \
  --out-dir "$QUALITY_DIR" \
  --skip-validation \
  --skip-visualization \
  --skip-geometry-audit \
  --skip-touchstone-audit \
  --extract-response-features \
  --audit-s8p-physical-feature-dataset \
  --s8p-expected-count 20 \
  --s8p-expected-ok-count 20 \
  --s8p-max-touchstone-checks 500 \
  --touchstone-all \
  --touchstone-expected-ports 8 \
  --touchstone-port-pairs 1,4:5,6 \
  --touchstone-target-frequency-ghz 15.0 \
  --expected-frequency-start-ghz 5.0 \
  --expected-frequency-stop-ghz 60.0 \
  --expected-frequency-step-ghz 1.0 \
  --expected-frequency-points 56 \
  --touchstone-ground-unused-ports \
  --derive-scalar-q-feature \
  --scalar-q-definition min \
  --scalar-q-output-column q_center \
  --select-physical-feature-validation-samples \
  --physical-feature-validation-sample-count 1 \
  --no-fail-exit

echo "POSTPROCESS_SUMMARY=$QUALITY_DIR/dataset_quality_gates_summary.json"
echo "POSTPROCESS_RESPONSE_FEATURES=$QUALITY_DIR/response_features/dataset_rows.csv"
echo "POSTPROCESS_SCALAR_Q_DATASET=$QUALITY_DIR/scalar_q_feature_dataset/dataset_rows.csv"
echo "POSTPROCESS_S8P_AUDIT=$QUALITY_DIR/s8p_physical_feature_dataset_audit/s8p_physical_feature_dataset_audit_summary.json"
echo "POSTPROCESS_VALIDATION_SAMPLE=$QUALITY_DIR/physical_feature_validation_samples/physical_feature_validation_samples.csv"

"$PYTHON" "$REPO_ROOT/scripts/audit_mars56_grounded_s8p_objective.py" \
  "$RUN_DIR" \
  --quality-dir "$QUALITY_DIR" \
  --out-dir "$QUALITY_DIR/mars56_grounded_s8p_objective_audit" \
  --expected-count 20 \
  --expected-ok-count 20 \
  --max-touchstone-checks 500 \
  --no-fail-exit

echo "POSTPROCESS_OBJECTIVE_AUDIT=$QUALITY_DIR/mars56_grounded_s8p_objective_audit/mars56_grounded_s8p_objective_audit_summary.json"

RETURNS_DIR="${RETURNS_DIR:-/shared/research/${USER:-researcher}/codex_s8p_56pt_grounded_tap_20260630/returns}"
mkdir -p "$RETURNS_DIR"
PACKAGE_TAR="$RETURNS_DIR/mars56_postprocess_${STAMP}.tar.gz"
tar -czf "$PACKAGE_TAR" \
  -C "$RUN_DIR" dataset_rows.csv dataset_manifest.json evaluations \
  -C "$QUALITY_DIR" .
sha256sum "$PACKAGE_TAR" > "$PACKAGE_TAR.sha256" 2>/dev/null || shasum -a 256 "$PACKAGE_TAR" > "$PACKAGE_TAR.sha256"
echo "POSTPROCESS_PACKAGE=$PACKAGE_TAR"
echo "POSTPROCESS_PACKAGE_SHA256=$(cat "$PACKAGE_TAR.sha256")"
echo "POSTPROCESS_MARS56_DONE $(date)"
