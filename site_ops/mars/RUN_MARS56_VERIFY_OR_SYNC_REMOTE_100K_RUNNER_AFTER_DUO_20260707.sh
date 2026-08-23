#!/usr/bin/env bash
set -euo pipefail

# Verify, and optionally sync, the queue-driven 100k production runner on MARS.
#
# Default is read-only:
#   bash RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh
#
# Sync local runner to MARS after backing up the remote file:
#   SYNC_REMOTE_RUNNER=1 bash RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh
#
# This is intentionally separate from data generation. It ensures the remote
# production watcher uses the strict checkpoint-proof runner before a 100k chunk
# is accepted and the next chunk is launched.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
LOCAL_RUNNER="${LOCAL_RUNNER:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/run_mars56_s4p_100k_chunk_from_queue.sh}"
LOCAL_QUEUE_PREFLIGHT="${LOCAL_QUEUE_PREFLIGHT:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/audit_mars56_s4p_candidate_queue_provenance.py}"

JUMP_HOST="${JUMP_HOST:-login.example.edu}"
MARS_HOST="${MARS_HOST:-mars.example.edu}"
USER_NAME="${USER_NAME:-researcher}"
REMOTE_PROJECT="${REMOTE_PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
REMOTE_RUNNER="${REMOTE_RUNNER:-$REMOTE_PROJECT/scripts/run_mars56_s4p_100k_chunk_from_queue.sh}"
REMOTE_QUEUE_PREFLIGHT="${REMOTE_QUEUE_PREFLIGHT:-$REMOTE_PROJECT/scripts/audit_mars56_s4p_candidate_queue_provenance.py}"

SYNC_REMOTE_RUNNER="${SYNC_REMOTE_RUNNER:-0}"
ALLOW_MISMATCH="${ALLOW_MISMATCH:-0}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-}"
SSH_PERSIST="${SSH_PERSIST:-20m}"

case "$SYNC_REMOTE_RUNNER" in 0|1) ;;
  *) echo "ERROR: SYNC_REMOTE_RUNNER must be 0 or 1." >&2; exit 2 ;;
esac
case "$ALLOW_MISMATCH" in 0|1) ;;
  *) echo "ERROR: ALLOW_MISMATCH must be 0 or 1." >&2; exit 2 ;;
esac
for value in "$SSH_CONTROL_PATH" "$REMOTE_PROJECT" "$REMOTE_RUNNER" "$LOCAL_RUNNER" "$LOCAL_QUEUE_PREFLIGHT" "$REMOTE_QUEUE_PREFLIGHT"; do
  if [[ "$value" == *"'"* || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: path/settings contain unsupported quote or newline characters." >&2
    exit 2
  fi
done
if [ ! -f "$LOCAL_RUNNER" ]; then
  echo "ERROR: LOCAL_RUNNER missing: $LOCAL_RUNNER" >&2
  exit 2
fi
if [ ! -f "$LOCAL_QUEUE_PREFLIGHT" ]; then
  echo "ERROR: LOCAL_QUEUE_PREFLIGHT missing: $LOCAL_QUEUE_PREFLIGHT" >&2
  exit 2
fi

LOCAL_SHA="$(shasum -a 256 "$LOCAL_RUNNER" | awk '{print $1}')"
QUEUE_PREFLIGHT_SHA="$(shasum -a 256 "$LOCAL_QUEUE_PREFLIGHT" | awk '{print $1}')"

SSH_ARGS=(-tt)
SCP_ARGS=()
PROXY_SSH_ARGS=(-o "ProxyJump=${USER_NAME}@${JUMP_HOST}")
PROXY_SCP_ARGS=(-o "ProxyJump=${USER_NAME}@${JUMP_HOST}")
if [ -n "$SSH_CONTROL_PATH" ]; then
  SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
  PROXY_SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
  PROXY_SCP_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
fi

read -r -d '' REMOTE_VERIFY <<'REMOTE' || true
set -euo pipefail

LOCAL_SHA="${LOCAL_SHA:?LOCAL_SHA is required}"
QUEUE_PREFLIGHT_SHA="${QUEUE_PREFLIGHT_SHA:?QUEUE_PREFLIGHT_SHA is required}"
REMOTE_RUNNER="${REMOTE_RUNNER:?REMOTE_RUNNER is required}"
REMOTE_QUEUE_PREFLIGHT="${REMOTE_QUEUE_PREFLIGHT:?REMOTE_QUEUE_PREFLIGHT is required}"
ALLOW_MISMATCH="${ALLOW_MISMATCH:-0}"

calc_sha() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
  else
    python3 - "$path" <<'PY'
import hashlib
import sys
with open(sys.argv[1], "rb") as fh:
    print(hashlib.sha256(fh.read()).hexdigest())
PY
  fi
}

printf 'remote_time='; date '+%Y-%m-%d %H:%M:%S %Z'
printf 'remote_runner=%s\n' "$REMOTE_RUNNER"
printf 'remote_queue_preflight=%s\n' "$REMOTE_QUEUE_PREFLIGHT"
printf 'local_runner_sha256=%s\n' "$LOCAL_SHA"
printf 'queue_preflight_sha256=%s\n' "$QUEUE_PREFLIGHT_SHA"

if [ ! -f "$REMOTE_RUNNER" ]; then
  echo "REMOTE_RUNNER_VERIFY_STATUS=FAIL_MISSING"
  exit 1
fi
if [ ! -f "$REMOTE_QUEUE_PREFLIGHT" ]; then
  echo "REMOTE_QUEUE_PREFLIGHT_VERIFY_STATUS=FAIL_MISSING"
  exit 1
fi

REMOTE_SHA="$(calc_sha "$REMOTE_RUNNER")"
REMOTE_QUEUE_PREFLIGHT_SHA="$(calc_sha "$REMOTE_QUEUE_PREFLIGHT")"
printf 'remote_runner_sha256=%s\n' "$REMOTE_SHA"
printf 'remote_queue_preflight_sha256=%s\n' "$REMOTE_QUEUE_PREFLIGHT_SHA"

if bash -n "$REMOTE_RUNNER"; then
  syntax_status=PASS
else
  syntax_status=FAIL
fi
printf 'remote_runner_syntax=%s\n' "$syntax_status"
if python3 -m py_compile "$REMOTE_QUEUE_PREFLIGHT"; then
  queue_preflight_syntax_status=PASS
else
  queue_preflight_syntax_status=FAIL
fi
printf 'remote_queue_preflight_syntax=%s\n' "$queue_preflight_syntax_status"

missing_tokens=0
for token in \
  "strict_acceptance" \
  "candidate_queue_provenance_preflight" \
  "candidate_queue_provenance_status" \
  "audit_mars56_s4p_candidate_queue_provenance.py" \
  "checkpoint_proof" \
  "checkpoint_proof_reasons" \
  "uniformity_manifest" \
  "traceability" \
  "traceability.details_missing" \
  "traceability.{key}=MISSING" \
  "stable_manifest_rows" \
  "stable_unique_evaluations" \
  "response_feature_rows" \
  "response_unique_evaluations" \
  "response_dataset_rows" \
  "response_dataset_unique_evaluations" \
  "enriched_rows" \
  "enriched_unique_evaluations" \
  "training_rows" \
  "training_unique_evaluations" \
  "uniformity_manifest.visual_artifact_count" \
  "uniformity_manifest.require_plots" \
  "physical_uniformity_gate" \
  "target_ranges" \
  "uniformity.ranges" \
  "uniformity.four_dimensional_uniformity" \
  "four_dimensional_uniformity.occupied_fraction" \
  "uniformity.k_sign_diagnostics" \
  "uniformity.k_sign_diagnostics.signed_k_count" \
  "uniformity.k_sign_diagnostics.uniformity_k_axis" \
  "ACCEPT_AND_PROCEED_TO_NEXT_100K_CHUNK" \
  "STOP_BEFORE_NEXT_100K_CHUNK"
do
  if grep -Fq "$token" "$REMOTE_RUNNER"; then
    printf 'REMOTE_RUNNER_TOKEN token=%s status=PASS\n' "$token"
  else
    printf 'REMOTE_RUNNER_TOKEN token=%s status=FAIL\n' "$token"
    missing_tokens=$((missing_tokens + 1))
  fi
done

for token in \
  "CANDIDATE_QUEUE_PROVENANCE_STATUS" \
  "source_selection_csv" \
  "geometry_space_filling_no_physical_labels" \
  "selection_has_predicted_physical_features" \
  "selection_has_target_physical_bins" \
  "candidate_rows_meet_expected_count" \
  "STOP_BEFORE_EMX_QUEUE_NOT_PROVEN_PHYSICAL_TARGETED"
do
  if grep -Fq "$token" "$REMOTE_QUEUE_PREFLIGHT"; then
    printf 'REMOTE_QUEUE_PREFLIGHT_TOKEN token=%s status=PASS\n' "$token"
  else
    printf 'REMOTE_QUEUE_PREFLIGHT_TOKEN token=%s status=FAIL\n' "$token"
    missing_tokens=$((missing_tokens + 1))
  fi
done

if [ "$REMOTE_SHA" = "$LOCAL_SHA" ]; then
  hash_status=PASS
else
  hash_status=FAIL
fi
printf 'remote_runner_hash_match=%s\n' "$hash_status"
if [ "$REMOTE_QUEUE_PREFLIGHT_SHA" = "$QUEUE_PREFLIGHT_SHA" ]; then
  queue_preflight_hash_status=PASS
else
  queue_preflight_hash_status=FAIL
fi
printf 'remote_queue_preflight_hash_match=%s\n' "$queue_preflight_hash_status"

if [ "$hash_status" = "PASS" ] && [ "$queue_preflight_hash_status" = "PASS" ] && [ "$syntax_status" = "PASS" ] && [ "$queue_preflight_syntax_status" = "PASS" ] && [ "$missing_tokens" -eq 0 ]; then
  echo "REMOTE_QUEUE_PREFLIGHT_VERIFY_STATUS=PASS"
  echo "REMOTE_RUNNER_VERIFY_STATUS=PASS"
  exit 0
fi

echo "REMOTE_QUEUE_PREFLIGHT_VERIFY_STATUS=FAIL"
echo "REMOTE_RUNNER_VERIFY_STATUS=FAIL"
if [ "$ALLOW_MISMATCH" = "1" ]; then
  echo "REMOTE_RUNNER_VERIFY_DECISION=ALLOW_MISMATCH_CONTINUE"
  exit 0
fi
exit 1
REMOTE

run_remote_verify() {
  local remote_verify_script
  read -r -d '' remote_verify_script <<SCRIPT || true
LOCAL_SHA='${LOCAL_SHA}'
QUEUE_PREFLIGHT_SHA='${QUEUE_PREFLIGHT_SHA}'
REMOTE_RUNNER='${REMOTE_RUNNER}'
REMOTE_QUEUE_PREFLIGHT='${REMOTE_QUEUE_PREFLIGHT}'
ALLOW_MISMATCH='${ALLOW_MISMATCH}'
export LOCAL_SHA QUEUE_PREFLIGHT_SHA REMOTE_RUNNER REMOTE_QUEUE_PREFLIGHT ALLOW_MISMATCH
${REMOTE_VERIFY}
SCRIPT
  ssh "${PROXY_SSH_ARGS[@]}" "${USER_NAME}@${MARS_HOST}" bash -s <<<"$remote_verify_script"
}

echo "MARS56 remote 100k runner verify/sync"
echo "local_runner=$LOCAL_RUNNER"
echo "local_runner_sha256=$LOCAL_SHA"
echo "local_queue_preflight=$LOCAL_QUEUE_PREFLIGHT"
echo "queue_preflight_sha256=$QUEUE_PREFLIGHT_SHA"
echo "remote_runner=$REMOTE_RUNNER"
echo "remote_queue_preflight=$REMOTE_QUEUE_PREFLIGHT"
echo "sync_remote_runner=$SYNC_REMOTE_RUNNER"
echo "allow_mismatch=$ALLOW_MISMATCH"

if [ "$SYNC_REMOTE_RUNNER" = "0" ]; then
  echo "Opening ${USER_NAME}@${JUMP_HOST}; approve Duo when prompted."
  run_remote_verify
  exit $?
fi

echo "SYNC_REMOTE_RUNNER=1: backing up and replacing the remote runner only after local syntax check."
bash -n "$LOCAL_RUNNER"

REMOTE_TMP="${REMOTE_RUNNER}.incoming.$(date '+%Y%m%d_%H%M%S').$$"
REMOTE_QUEUE_PREFLIGHT_TMP="${REMOTE_QUEUE_PREFLIGHT}.incoming.$(date '+%Y%m%d_%H%M%S').$$"
REMOTE_BACKUP="${REMOTE_RUNNER}.bak.$(date '+%Y%m%d_%H%M%S')"
REMOTE_QUEUE_PREFLIGHT_BACKUP="${REMOTE_QUEUE_PREFLIGHT}.bak.$(date '+%Y%m%d_%H%M%S')"
REMOTE_DIR="$(dirname "$REMOTE_RUNNER")"
REMOTE_QUEUE_PREFLIGHT_DIR="$(dirname "$REMOTE_QUEUE_PREFLIGHT")"
MARS_TARGET="${USER_NAME}@${MARS_HOST}"

echo "Creating remote backup through ProxyJump=${USER_NAME}@${JUMP_HOST}"
read -r -d '' REMOTE_BACKUP_SCRIPT <<SCRIPT || true
set -euo pipefail
mkdir -p '${REMOTE_DIR}' '${REMOTE_QUEUE_PREFLIGHT_DIR}'
if [ -f '${REMOTE_RUNNER}' ]; then
  cp '${REMOTE_RUNNER}' '${REMOTE_BACKUP}'
  echo remote_backup='${REMOTE_BACKUP}'
else
  echo remote_backup=none_existing_file_missing
fi
if [ -f '${REMOTE_QUEUE_PREFLIGHT}' ]; then
  cp '${REMOTE_QUEUE_PREFLIGHT}' '${REMOTE_QUEUE_PREFLIGHT_BACKUP}'
  echo remote_queue_preflight_backup='${REMOTE_QUEUE_PREFLIGHT_BACKUP}'
else
  echo remote_queue_preflight_backup=none_existing_file_missing
fi
SCRIPT
ssh "${PROXY_SSH_ARGS[@]}" "$MARS_TARGET" bash -s <<<"$REMOTE_BACKUP_SCRIPT"

echo "Copying local runner to temporary remote path: $REMOTE_TMP"
scp "${PROXY_SCP_ARGS[@]}" "$LOCAL_RUNNER" "${MARS_TARGET}:${REMOTE_TMP}"
echo "Copying local queue preflight to temporary remote path: $REMOTE_QUEUE_PREFLIGHT_TMP"
scp "${PROXY_SCP_ARGS[@]}" "$LOCAL_QUEUE_PREFLIGHT" "${MARS_TARGET}:${REMOTE_QUEUE_PREFLIGHT_TMP}"

echo "Validating and installing synced remote runner"
read -r -d '' REMOTE_INSTALL_SCRIPT <<SCRIPT || true
set -euo pipefail
bash -n '${REMOTE_TMP}'
python3 -m py_compile '${REMOTE_QUEUE_PREFLIGHT_TMP}'
chmod +x '${REMOTE_TMP}'
chmod +x '${REMOTE_QUEUE_PREFLIGHT_TMP}'
mv '${REMOTE_TMP}' '${REMOTE_RUNNER}'
mv '${REMOTE_QUEUE_PREFLIGHT_TMP}' '${REMOTE_QUEUE_PREFLIGHT}'
echo REMOTE_QUEUE_PREFLIGHT_SYNC_INSTALL=PASS
echo REMOTE_RUNNER_SYNC_INSTALL=PASS
SCRIPT
ssh "${PROXY_SSH_ARGS[@]}" "$MARS_TARGET" bash -s <<<"$REMOTE_INSTALL_SCRIPT"

run_remote_verify
