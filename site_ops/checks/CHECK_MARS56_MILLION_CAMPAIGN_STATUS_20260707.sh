#!/usr/bin/env bash
set -euo pipefail

# Usage after Duo is available:
#   bash CHECK_MARS56_MILLION_CAMPAIGN_STATUS_20260707.sh
#
# This script does not store a password. It opens the CAE jump host and runs a
# read-only MARS status audit for the current 1M-sample campaign.
# A production checkpoint is reported as PASS only when strict checkpoint_proof
# includes the Lp/Ls/Q/|K| visual artifact manifest.

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
DATA8=$BASE/datasets/chunk_08_accepted_pool_after_chunk07_qgap_nearest_8000_widthfix
RUN8_LOG=$BASE/logs/chunk08_accepted_pool_qgap_nearest_8000_j48_20260706.log
U8=$BASE/status/accepted_inrange_pool_after_chunk08_20260706/physical_feature_uniformity/physical_feature_uniformity_summary.json
FIRST100K=$BASE/datasets/chunk_001_100k_after_chunk08_pass
GEOMETRY_ONLY_100K_QUEUE=$BASE/candidate_queues/chunk_01_n100000/mars56_grounded_s4p_candidate_queue.csv
PY=/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python

checkpoint_proof() {
  local path="$1"
  local expected="$2"
  if [ ! -f "$path" ]; then
    echo "MISSING"
    return
  fi
  "$PY" - "$path" "$expected" <<'PY'
import json, sys
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
trace_details = details.get("traceability") if isinstance(details.get("traceability"), dict) else {}
if not trace_details:
    reasons.append("traceability.details_missing")
for key in ("stable_manifest_rows", "response_feature_rows", "enriched_rows", "training_rows"):
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

printf 'remote_time='; date '+%Y-%m-%d %H:%M:%S %Z'
printf 'base=%s\n' "$BASE"
if [ -f "$GEOMETRY_ONLY_100K_QUEUE" ]; then
  printf 'geometry_only_100k_queue=%s\n' "$GEOMETRY_ONLY_100K_QUEUE"
  echo 'geometry_only_100k_queue_note=exists but is not accepted physical-feature evidence; first100k production must come from the U8-gated chunk_001_100k_after_chunk08_pass dataset.'
fi
printf 'chunk08_dataset=%s\n' "$DATA8"
printf 'chunk08_nonempty='; find "$DATA8" -type f -name '*.s4p' -size +0c 2>/dev/null | wc -l
printf 'chunk08_empty_stale5='; find "$DATA8" -type f -name '*.s4p' -size 0c -mmin +5 2>/dev/null | wc -l
printf 'chunk08_empty_stale15='; find "$DATA8" -type f -name '*.s4p' -size 0c -mmin +15 2>/dev/null | wc -l
printf 'chunk08_runner='; ps -fu researcher | grep "$DATA8" | grep 'run_candidate_queue_dataset_parallel.py' | grep -v grep | wc -l
printf 'chunk08_workers='; ps -fu researcher | grep "$DATA8" | grep 'run_candidate_queue_dataset.py' | grep -v grep | wc -l
printf 'chunk08_emx='; ps -fu researcher | grep "$DATA8" | grep '/EMX20251/.*/emx' | grep -v grep | wc -l

printf '\n-- chunk08 recent errors --\n'
grep -Ei 'error|failed|exception|traceback' "$RUN8_LOG" 2>/dev/null | tail -n 20 || true

printf '\n-- watcher processes --\n'
ps -fu researcher | grep -E 'watch_chunk08_checkpoint|watch_chunk08_pass_prepare|watch_production_100k_chunks_02_to_10_after_chunk08' | grep -v grep || true

printf '\n-- watcher log tails --\n'
for f in \
  "$BASE/logs/watch_chunk08_checkpoint_merge_accept_20260706.log" \
  "$BASE/logs/watch_chunk08_pass_prepare_and_launch_first_100k_20260706.log" \
  "$BASE/logs/watch_production_100k_chunks_02_to_10_after_chunk08_20260706.log"
do
  printf '\n### %s\n' "$f"
  tail -n 30 "$f" 2>/dev/null || true
done

printf '\n-- U8 uniformity summary --\n'
if [ -f "$U8" ]; then
  ls -lh "$U8"
  "$PY" - "$U8" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("U8_overall_status=", d.get("overall_status"))
print("U8_valid_feature_count=", d.get("valid_feature_count"))
fd = d.get("four_dimensional_uniformity", {})
print("U8_4d_occupied_fraction=", fd.get("occupied_fraction"))
print("U8_4d_occupied_bins=", fd.get("occupied_bins"))
print("U8_pairwise=", {k: v.get("occupied_fraction") for k, v in d.get("pairwise_uniformity", {}).items()})
PY
else
  echo "U8_missing"
fi

printf '\n-- first100k state --\n'
if [ -d "$FIRST100K" ]; then
  printf 'first100k_dataset=%s\n' "$FIRST100K"
  printf 'first100k_nonempty='; find "$FIRST100K" -type f -name '*.s4p' -size +0c 2>/dev/null | wc -l
  printf 'first100k_empty_stale15='; find "$FIRST100K" -type f -name '*.s4p' -size 0c -mmin +15 2>/dev/null | wc -l
  printf 'first100k_runner='; ps -fu researcher | grep "$FIRST100K" | grep 'run_candidate_queue_dataset_parallel.py' | grep -v grep | wc -l
  printf 'first100k_workers='; ps -fu researcher | grep "$FIRST100K" | grep 'run_candidate_queue_dataset.py' | grep -v grep | wc -l
  printf 'first100k_emx='; ps -fu researcher | grep "$FIRST100K" | grep '/EMX20251/.*/emx' | grep -v grep | wc -l
  FIRST100K_SUMMARY=$FIRST100K/parallel_candidate_queue_dataset_summary.json
  if [ -f "$FIRST100K_SUMMARY" ]; then
    "$PY" - "$FIRST100K_SUMMARY" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("first100k_dataset_summary=", sys.argv[1])
print("first100k_dataset_summary_status=", d.get("overall_status"))
print("first100k_dataset_summary_count=", d.get("count") or d.get("completed_count") or d.get("expected_count"))
PY
  else
    echo "first100k_dataset_summary_missing"
  fi
else
  echo "first100k_dataset_missing_or_not_launched"
fi

printf '\n-- all formal 100k production chunks --\n'
shopt -s nullglob
prod_datasets=("$BASE"/datasets/*_100k_after_chunk08_pass)
if [ "${#prod_datasets[@]}" -eq 0 ]; then
  echo "formal_100k_production_dataset_count=0"
else
  seen_prod=$(mktemp)
  printf '%s\n' "${prod_datasets[@]}" | sort -u > "$seen_prod"
  while IFS= read -r PROD; do
    [ -n "$PROD" ] || continue
    tag=$(basename "$PROD")
    case "$tag" in
      chunk_01_n100000|chunk_01_n100000*)
        printf 'PROD100K_SKIP_GEOMETRY_ONLY tag=%s path=%s\n' "$tag" "$PROD"
        continue
        ;;
    esac

    nonempty=$(find "$PROD" -type f -name '*.s4p' -size +0c 2>/dev/null | wc -l | tr -d ' ')
    empty_stale15=$(find "$PROD" -type f -name '*.s4p' -size 0c -mmin +15 2>/dev/null | wc -l | tr -d ' ')
    ds_summary="$PROD/parallel_candidate_queue_dataset_summary.json"
    ds_status="MISSING"
    if [ -f "$ds_summary" ]; then
      ds_status=$("$PY" - "$ds_summary" <<'PY'
import json, sys
try:
    print(str(json.load(open(sys.argv[1])).get("overall_status", "")))
except Exception:
    print("PARSE_ERROR")
PY
)
    fi

    cp_summary=$(find "$BASE/model_tests/$tag" -path '*/mars56_s4p_physical_checkpoint_pipeline_summary.json' -type f 2>/dev/null | sort | tail -n 1 || true)
    cp_status="MISSING"
    cp_proof="MISSING"
    if [ -n "$cp_summary" ] && [ -f "$cp_summary" ]; then
      cp_status=$("$PY" - "$cp_summary" <<'PY'
import json, sys
try:
    print(str(json.load(open(sys.argv[1])).get("overall_status", "")))
except Exception:
    print("PARSE_ERROR")
PY
)
      cp_proof=$(checkpoint_proof "$cp_summary" 100000)
    fi

    if [ "$nonempty" -ge 100000 ] && [ "$ds_status" = "PASS" ] && [ "$cp_proof" = "PASS" ]; then
      prod_state="CHECKPOINT_PASS"
    elif [ "$nonempty" -ge 100000 ] && [ "$ds_status" = "PASS" ] && [ "$cp_proof" = "MISSING" ]; then
      prod_state="READY_NEEDS_CHECKPOINT"
    elif [ "$nonempty" -ge 100000 ] && [ "$ds_status" = "PASS" ]; then
      prod_state="CHECKPOINT_NOT_PASS"
    elif [ "$nonempty" -ge 100000 ]; then
      prod_state="WAIT_DATASET_SUMMARY_PASS"
    else
      prod_state="WAIT_DATASET_COMPLETE"
    fi

    printf 'PROD100K tag=%s state=%s nonempty=%s empty_stale15=%s dataset_status=%s checkpoint_status=%s checkpoint_proof=%s path=%s\n' \
      "$tag" "$prod_state" "$nonempty" "$empty_stale15" "$ds_status" "$cp_status" "$cp_proof" "$PROD"
    if [ -n "$cp_summary" ]; then
      printf 'PROD100K_CHECKPOINT_SUMMARY tag=%s summary=%s\n' "$tag" "$cp_summary"
    fi
  done < "$seen_prod"
  rm -f "$seen_prod"
fi
REMOTE

echo "Opening ${USER_NAME}@${JUMP_HOST}; approve Duo when prompted."
echo "Then this will query ${MARS_HOST} without changing remote files."
ssh "${SSH_ARGS[@]}" "${USER_NAME}@${JUMP_HOST}" "ssh -tt ${MARS_HOST} 'bash -s'" <<<"$REMOTE_AUDIT"
