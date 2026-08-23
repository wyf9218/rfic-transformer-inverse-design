#!/usr/bin/env bash
# Accepted-data controller for the 1,000,000-sample MARS56 EMX campaign.
#
# Progress is measured in real, in-range, independent-geometry EMX rows.  A
# cumulative model test is recorded at 100k, 200k, ..., 1M.  Intermediate
# uniformity failures remain visible and steer the next adaptive queue.  The
# final 1M completion marker requires the strict uniformity gate to PASS.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PROJECT="${PROJECT:-$REPO_ROOT}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG="${CONFIG:-$PROJECT/configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml}"
TARGET_ENVELOPE="${TARGET_ENVELOPE:-$PROJECT/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json}"
ACQUISITION_MIX_JSON="${ACQUISITION_MIX_JSON:-}"
CAMPAIGN_ROOT="${CAMPAIGN_ROOT:-$BASE/status/accepted_1m_campaign_controller_20260710}"
FIRST_STATUS="${FIRST_STATUS:-$BASE/status/first100k_urgent_targeted_20260709}"
FIRST_PRODUCTION="${FIRST_PRODUCTION:-$BASE/production_chunks/chunk_001_100k_urgent_targeted_20260709}"
FIRST_WATCHER="${FIRST_WATCHER:-$FIRST_STATUS/accepted_checkpoint_watcher}"
FIRST_POOL="${FIRST_POOL:-$FIRST_WATCHER/milestone_100000/accepted_pool}"
CHUNK2_PREP="${CHUNK2_PREP:-$FIRST_STATUS/chunk2_adaptive_queue_preparation_20260710}"

TOTAL_ACCEPTED="${TOTAL_ACCEPTED:-1000000}"
CHECKPOINT_SIZE="${CHECKPOINT_SIZE:-100000}"
QUEUE_COUNT="${QUEUE_COUNT:-120000}"
CANDIDATE_COUNT="${CANDIDATE_COUNT:-1200000}"
JOBS="${JOBS:-48}"
POLL_SECONDS="${POLL_SECONDS:-300}"
MODEL_MAX_CANDIDATES="${MODEL_MAX_CANDIDATES:-8}"
MODEL_MAX_EPOCHS="${MODEL_MAX_EPOCHS:-120}"
MODEL_PATIENCE="${MODEL_PATIENCE:-15}"
MODEL_THREADS="${MODEL_THREADS:-8}"
MODEL_INIT_SEED="${MODEL_INIT_SEED:-20260711}"
MODEL_SPLIT_SEED="${MODEL_SPLIT_SEED:-20260711}"
SAMPLE_EFFICIENCY_EPOCHS="${SAMPLE_EFFICIENCY_EPOCHS:-60}"
SAMPLE_EFFICIENCY_PATIENCE="${SAMPLE_EFFICIENCY_PATIENCE:-10}"
LOCAL_TARGET_FRACTION="${LOCAL_TARGET_FRACTION:-0.50}"
RARE_MARGINAL_FRACTION="${RARE_MARGINAL_FRACTION:-0.20}"
PAIRWISE_FALLBACK_FRACTION="${PAIRWISE_FALLBACK_FRACTION:-0.25}"
PAIRWISE_FEATURE_PAIRS="${PAIRWISE_FEATURE_PAIRS:-lp_nh_center:q_center,ls_nh_center:q_center}"
PAIRWISE_MARGINAL_FEATURES="${PAIRWISE_MARGINAL_FEATURES:-q_center,k_abs_center}"
PHYSICAL_FEATURE_BINS="${PHYSICAL_FEATURE_BINS:-4}"
PHYSICAL_FEATURE_DIMENSIONS="${PHYSICAL_FEATURE_DIMENSIONS:-4}"
PREPARED_CHUNK2_POLICY_VERSION="${PREPARED_CHUNK2_POLICY_VERSION:-3}"
SEED_BASE="${SEED_BASE:-2026071000}"
PUBLICATION_MIN_HFSS_SAMPLES="${PUBLICATION_MIN_HFSS_SAMPLES:-5}"
HFSS_VALIDATION_RECORDS="${HFSS_VALIDATION_RECORDS:-}"
PREFLIGHT_ONLY="${PREFLIGHT_ONLY:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preflight-only) PREFLIGHT_ONLY=1; shift ;;
    -h|--help)
      echo "Usage: run_accepted_1m_campaign_controller.sh [--preflight-only]"
      exit 0
      ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

for numeric in TOTAL_ACCEPTED CHECKPOINT_SIZE QUEUE_COUNT CANDIDATE_COUNT JOBS POLL_SECONDS PUBLICATION_MIN_HFSS_SAMPLES; do
  if ! [[ "${!numeric}" =~ ^[0-9]+$ ]] || [[ "${!numeric}" -lt 1 ]]; then
    echo "ERROR: $numeric must be a positive integer." >&2
    exit 2
  fi
done
if [[ $((TOTAL_ACCEPTED % CHECKPOINT_SIZE)) -ne 0 ]]; then
  echo "ERROR: TOTAL_ACCEPTED must be divisible by CHECKPOINT_SIZE." >&2
  exit 2
fi
CHECKPOINTS=$((TOTAL_ACCEPTED / CHECKPOINT_SIZE))
if [[ "$CHECKPOINTS" -ne 10 ]]; then
  echo "ERROR: this controller requires ten cumulative checkpoints." >&2
  exit 2
fi

case "$PREFLIGHT_ONLY" in 0|1) ;;
  *) echo "ERROR: PREFLIGHT_ONLY must be 0 or 1." >&2; exit 2 ;;
esac

mkdir -p "$CAMPAIGN_ROOT" "$CAMPAIGN_ROOT/checkpoints" "$CAMPAIGN_ROOT/rounds"
LOG="$CAMPAIGN_ROOT/accepted_1m_campaign_controller.log"
STATE="$CAMPAIGN_ROOT/accepted_1m_campaign_state.json"
CURRENT_POOL_FILE="$CAMPAIGN_ROOT/current_pool_path.txt"
LOCK_DIR="$CAMPAIGN_ROOT/active.lock"
COMPLETE_MARKER="$CAMPAIGN_ROOT/accepted_1m_campaign.complete"
FINAL_AUDIT_DIR="$CAMPAIGN_ROOT/final_completion_audit"
FINAL_AUDIT_SUMMARY="$FINAL_AUDIT_DIR/accepted_1m_campaign_completion_audit_summary.json"
FINAL_AUDIT_MARKER="$FINAL_AUDIT_DIR/accepted_1m_campaign_completion.pass"
FINAL_EVIDENCE_DIR="$CAMPAIGN_ROOT/final_model_evidence_matrix"
FINAL_EVIDENCE_SUMMARY="$FINAL_EVIDENCE_DIR/final_model_publication_readiness_summary.json"
FINAL_EVIDENCE_MARKER="$FINAL_EVIDENCE_DIR/final_model_evidence_matrix.pass"
FINAL_RESIDUAL_DIR="$CAMPAIGN_ROOT/final_cross_solver_residual_benchmark"
FINAL_RESIDUAL_SUMMARY="$FINAL_RESIDUAL_DIR/emx_hfss_cross_solver_residual_summary.json"
CALIBRATION_ROOT="$CAMPAIGN_ROOT/proxy_to_real_calibration"
ACTIVE_CALIBRATION="$CALIBRATION_ROOT/active_proxy_to_real_calibration.json"
mkdir -p "$CALIBRATION_ROOT"

if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
  "$PYTHON_BIN" - "$CAMPAIGN_ROOT/controller_preflight_summary.json" "$CONFIG" "$TARGET_ENVELOPE" "$SCRIPT_DIR" "$JOBS" "$QUEUE_COUNT" "$CANDIDATE_COUNT" "$MODEL_INIT_SEED" "$MODEL_SPLIT_SEED" "$CHECKPOINT_SIZE" "$PHYSICAL_FEATURE_BINS" "$PHYSICAL_FEATURE_DIMENSIONS" "$ACQUISITION_MIX_JSON" <<'PY'
import hashlib,json,pathlib,sys
from datetime import datetime,timezone
target,config_raw,envelope_raw,scripts_raw,jobs,queue_count,candidate_count,model_seed,split_seed,checkpoint_size,bins,dimensions,mix_raw=sys.argv[1:]
config=pathlib.Path(config_raw); envelope=pathlib.Path(envelope_raw); scripts=pathlib.Path(scripts_raw)
required=[
 'run_mars56_s4p_adaptive_physical_acquisition_round.sh',
 'select_physical_feature_targeted_candidate_geometries.py',
 'select_physical_feature_acquisition_mix.py',
 'materialize_physical_feature_targeted_s4p_queue.py',
 'physical_feature_prediction_calibration.py',
 'audit_proxy_to_real_physical_feature_calibration.py',
 'audit_proxy_uncertainty_real_emx_reliability.py',
 'audit_physical_feature_candidate_reachability_consensus.py',
 'refresh_campaign_proxy_to_real_calibration.py',
 'run_real_emx_accepted_increment_round.sh',
 'run_accepted_physical_feature_model_checkpoint.sh',
	 'audit_tandem_predicted_geometry_feasibility.py',
	 'audit_accepted_1m_campaign_completion.py',
	 'audit_final_model_publication_readiness.py',
	 'benchmark_emx_hfss_cross_solver_residual.py',
 'audit_physical_feature_model_learning_curve.py',
 'benchmark_physical_feature_sample_efficiency.py',
 'audit_physical_feature_extraction_frequency_stability.py',
 'audit_broadband_sparameter_surrogate_readiness.py',
 'compare_physical_feature_q_input_ablation.py',
 'train_physical_feature_tandem_inverse.py',
 'train_broadband_sparameter_pca_surrogate.py',
 'select_balanced_physical_feature_checkpoint.py',
 'merge_physical_feature_accepted_pool.py',
]
checks={
 'config_exists':config.is_file(),'target_envelope_exists':envelope.is_file(),
 'required_scripts_exist':all((scripts/name).is_file() for name in required),
 'jobs_is_48':int(jobs)==48,'queue_count_has_acceptance_margin':int(queue_count)>100000,
 'candidate_pool_larger_than_queue':int(candidate_count)>int(queue_count),
 'model_seed_fixed':int(model_seed)>0,'split_seed_fixed':int(split_seed)>0,
}
parallel2=False
if config.is_file():
    try:
        import yaml
        data=yaml.safe_load(config.read_text(encoding='utf-8')) or {}
        parallel2='--parallel=2' in [str(item) for item in ((data.get('emx') or {}).get('extra_args') or [])]
    except Exception:
        parallel2=False
checks['emx_parallel2_configured']=parallel2
mix_evidence={"configured":bool(mix_raw)}
if mix_raw:
    mix_path=pathlib.Path(mix_raw).expanduser().resolve()
    try:
        mix_data=json.loads(mix_path.read_text(encoding='utf-8'))
    except Exception as exc:
        mix_data={}; mix_evidence["error"]=f"{type(exc).__name__}: {exc}"
    production=mix_data.get('production_acquisition_mix') or {}
    counts=production.get('counts') or {}
    keys={"coarse_4d","rare_marginal","pairwise_gap","random_exploration","geometry_diversity"}
    mix_ok=bool(
        mix_data.get('overall_status')=='PASS'
        and mix_data.get('automatic_command_authorized') is True
        and mix_data.get('proxy_values_are_acquisition_only') is True
        and int(production.get('queue_count') or 0)==int(queue_count)
        and set(counts)==keys
        and all(isinstance(counts.get(key),int) and counts[key]>=0 for key in keys)
        and sum(int(value) for value in counts.values())==int(queue_count)
    )
    checks['acquisition_mix_contract_authorized_exact']=mix_ok
    mix_evidence.update({
        "path":str(mix_path),"exists":mix_path.is_file(),
        "sha256":hashlib.sha256(mix_path.read_bytes()).hexdigest() if mix_path.is_file() else None,
        "production_acquisition_mix":production,
    })
else:
    checks['acquisition_mix_contract_authorized_exact']=True
    mix_evidence["mode"]="LEGACY_DEFAULT_NO_FIVE_ARM_CONTRACT"
checkpoint_targets=[int(checkpoint_size)*i for i in range(1,11)]
total_bins=int(bins)**int(dimensions)
cumulative_per_bin_targets=[(value+total_bins-1)//total_bins for value in checkpoint_targets]
checks['cumulative_uniformity_targets_scale']=bool(
    int(bins)==4 and int(dimensions)==4
    and cumulative_per_bin_targets[0]==391
    and cumulative_per_bin_targets[-1]==3907
    and all(right>left for left,right in zip(cumulative_per_bin_targets,cumulative_per_bin_targets[1:]))
)
payload={
 'generated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
 'overall_status':'PASS' if all(checks.values()) else 'FAIL',
 'checks':checks,'jobs':int(jobs),'queue_count':int(queue_count),'candidate_count':int(candidate_count),
 'model_initialization_seed':int(model_seed),'cross_checkpoint_split_seed':int(split_seed),
 'checkpoint_targets':checkpoint_targets,
 'physical_feature_bins_per_axis':int(bins),
 'physical_feature_dimensions':int(dimensions),
 'acquisition_mix_contract':mix_evidence,
 'cumulative_target_count_per_4d_bin':cumulative_per_bin_targets,
 'completion_contract':'1M real in-range geometry-unique EMX rows + ten model tests + final strict uniformity PASS',
}
pathlib.Path(target).parent.mkdir(parents=True,exist_ok=True)
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print(json.dumps(payload,indent=2,ensure_ascii=False))
if payload['overall_status']!='PASS': raise SystemExit(2)
PY
  exit $?
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  old_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "controller already active pid=$old_pid" >> "$LOG"
    exit 0
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR" || exit 2
fi
echo "$$" > "$LOCK_DIR/pid"
cleanup() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" | tee -a "$LOG"; }

json_value() {
  "$PYTHON_BIN" - "$1" "$2" <<'PY'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1])
if not p.is_file(): print(""); raise SystemExit(0)
v=json.loads(p.read_text(encoding="utf-8"))
for part in sys.argv[2].split("."):
    v=v[part]
print(v)
PY
}

if [[ -f "$COMPLETE_MARKER" ]]; then
  if [[ -f "$FINAL_AUDIT_MARKER" ]] \
    && [[ "$(json_value "$FINAL_AUDIT_SUMMARY" overall_status)" == "PASS" ]] \
    && [[ -f "$FINAL_EVIDENCE_MARKER" ]] \
    && [[ "$(json_value "$FINAL_EVIDENCE_SUMMARY" overall_status)" == "PASS" ]]; then
    exit 0
  fi
  echo "ERROR: legacy completion marker exists without strict completion and final evidence-matrix PASS." >&2
  exit 2
fi

pool_count() {
  json_value "$1/accepted_pool_merge_summary.json" row_count
}

physical_target_count_per_bin() {
  local desired_total="$1"
  local total_bins=$((PHYSICAL_FEATURE_BINS ** PHYSICAL_FEATURE_DIMENSIONS))
  echo $(((desired_total + total_bins - 1) / total_bins))
}

write_state() {
  local phase="$1" checkpoint="$2" target="$3" pool="$4" detail="$5"
  local count="0"
  if [[ -f "$pool/accepted_pool_merge_summary.json" ]]; then count="$(pool_count "$pool")"; fi
  "$PYTHON_BIN" - "$STATE.tmp" "$phase" "$checkpoint" "$target" "$pool" "$count" "$detail" "$ACQUISITION_MIX_JSON" <<'PY'
import hashlib,json,pathlib,sys
from datetime import datetime,timezone
target,phase,checkpoint,accepted_target,pool,count,detail,mix_raw=sys.argv[1:]
mix_path=pathlib.Path(mix_raw).expanduser().resolve() if mix_raw else None
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "campaign_status":phase,"active_checkpoint_index":int(checkpoint),
 "active_checkpoint_target":int(accepted_target),"current_pool":pool,
 "current_accepted_count":int(count),"detail":detail,
 "acquisition_mix_contract":(
   {"path":str(mix_path),"sha256":hashlib.sha256(mix_path.read_bytes()).hexdigest()}
   if mix_path is not None and mix_path.is_file() else None
 ),
 "scientific_boundary":"Counts are real EMX rows inside explicit ranges and unique by the 10 independent geometry variables; surrogate values only prioritize future simulations.",
}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
  mv "$STATE.tmp" "$STATE"
}

first_ready() {
  [[ -f "$FIRST_WATCHER/first100k_model_checkpoint.complete" ]] \
    && [[ -f "$FIRST_WATCHER/milestone_100000/milestone.complete" ]] \
    && [[ -f "$FIRST_POOL/accepted_pool_merge_summary.json" ]] \
    && [[ "$(pool_count "$FIRST_POOL")" -ge "$CHECKPOINT_SIZE" ]]
}

log "accepted 1M campaign controller start"
while ! first_ready; do
  raw="$(find "$FIRST_PRODUCTION/dataset" -type f -name '*.s4p' -size +0c 2>/dev/null | wc -l | tr -d ' ')"
  write_state WAIT_FIRST100K 1 "$CHECKPOINT_SIZE" "$FIRST_POOL" "raw_s4p=$raw"
  log "waiting first complete raw=$raw model=$([[ -f "$FIRST_WATCHER/first100k_model_checkpoint.complete" ]] && echo 1 || echo 0)"
  sleep "$POLL_SECONDS"
done

if [[ -f "$CURRENT_POOL_FILE" ]]; then
  CURRENT_POOL="$(cat "$CURRENT_POOL_FILE")"
  if [[ ! -f "$CURRENT_POOL/accepted_pool_merge_summary.json" ]]; then CURRENT_POOL="$FIRST_POOL"; fi
else
  CURRENT_POOL="$FIRST_POOL"
fi
# Recover the newest completed round even if the controller stopped between
# writing round.complete and updating current_pool_path.txt.
for completed_round in "$CAMPAIGN_ROOT"/rounds/round_*; do
  [[ -f "$completed_round/real_emx/round.complete" ]] || continue
  candidate_pool="$completed_round/real_emx/accepted_pool"
  [[ -f "$candidate_pool/accepted_pool_merge_summary.json" ]] || continue
  if [[ "$(pool_count "$candidate_pool")" -gt "$(pool_count "$CURRENT_POOL")" ]]; then
    CURRENT_POOL="$candidate_pool"
  fi
done
printf '%s\n' "$CURRENT_POOL" > "$CURRENT_POOL_FILE"
log "first accepted pool ready count=$(pool_count "$CURRENT_POOL")"

next_round_number() {
  local max=0 base name number
  for base in "$CAMPAIGN_ROOT"/rounds/round_*; do
    [[ -d "$base" ]] || continue
    name="$(basename "$base")"; number="${name#round_}"
    number="${number%%_*}"
    if [[ "$number" =~ ^[0-9]+$ ]] && [[ ! -f "$base/real_emx/round.complete" ]]; then
      echo $((10#$number))
      return
    fi
    if [[ "$number" =~ ^[0-9]+$ ]] && [[ $((10#$number)) -gt "$max" ]]; then max=$((10#$number)); fi
  done
  echo $((max + 1))
}

record_checkpoint() {
  local checkpoint="$1" target="$2" pool="$3" model_dir="$4" record_dir="$5"
  mkdir -p "$record_dir"
  rm -f "$record_dir/model_test.complete" "$record_dir/formal_checkpoint.pass"
  "$PYTHON_BIN" - "$record_dir/checkpoint_record.json.tmp" "$checkpoint" "$target" "$pool" "$model_dir" <<'PY'
import csv,hashlib,json,pathlib,sys
from datetime import datetime,timezone
target,index,count,pool_raw,model_raw=sys.argv[1:]
pool=pathlib.Path(pool_raw); model=pathlib.Path(model_raw)
manifest=model/'accepted_physical_feature_model_checkpoint_manifest.json'
data=json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else {}
dataset=pool/'dataset_rows.csv'
pool_count=0
if dataset.is_file():
    with dataset.open(newline='',encoding='utf-8-sig') as handle:
        pool_count=sum(1 for _ in csv.DictReader(handle))
item={
 'generated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
 'checkpoint_index':int(index),'target_accepted_count':int(count),
 'accepted_pool':str(pool),'model_checkpoint':str(model),
 'accepted_pool_row_count_at_record':int(pool_count),
 'model_manifest':str(manifest),'model_test_status':data.get('model_test_status','MISSING'),
 'uniformity_status':data.get('uniformity_status','MISSING'),
 'overall_status':data.get('overall_status','MISSING'),
 'formal_checkpoint_pass':data.get('overall_status')=='PASS',
}
if manifest.is_file(): item['model_manifest_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest()
pathlib.Path(target).write_text(json.dumps(item,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
if not manifest.is_file(): raise SystemExit('missing model manifest')
if item['model_test_status']!='PASS': raise SystemExit('model test status is not PASS')
if int(data.get('checkpoint_index') or -1)!=int(index): raise SystemExit('model manifest checkpoint index mismatch')
if int(data.get('accepted_checkpoint_count') or -1)!=int(count): raise SystemExit('model manifest accepted count mismatch')
if pool_count<int(count): raise SystemExit('accepted pool is smaller than checkpoint target')
PY
  mv "$record_dir/checkpoint_record.json.tmp" "$record_dir/checkpoint_record.json"
  touch "$record_dir/model_test.complete"
  if [[ "$(json_value "$record_dir/checkpoint_record.json" formal_checkpoint_pass)" == "True" ]]; then
    touch "$record_dir/formal_checkpoint.pass"
  fi
}

checkpoint_record_valid() {
  local checkpoint="$1" target="$2" record_dir="$3"
  "$PYTHON_BIN" - "$record_dir/checkpoint_record.json" "$checkpoint" "$target" <<'PY'
import hashlib,json,pathlib,sys
record_path=pathlib.Path(sys.argv[1]); checkpoint=int(sys.argv[2]); target=int(sys.argv[3])
if not record_path.is_file(): raise SystemExit(2)
try:
    record=json.loads(record_path.read_text(encoding='utf-8'))
except Exception:
    raise SystemExit(2)
manifest=pathlib.Path(str(record.get('model_manifest') or ''))
if not manifest.is_file(): raise SystemExit(2)
try:
    data=json.loads(manifest.read_text(encoding='utf-8'))
except Exception:
    raise SystemExit(2)
actual_sha=hashlib.sha256(manifest.read_bytes()).hexdigest()
checks=(
    int(record.get('checkpoint_index') or -1)==checkpoint,
    int(record.get('target_accepted_count') or -1)==target,
    int(record.get('accepted_pool_row_count_at_record') or -1)>=target,
    str(record.get('model_manifest_sha256') or '')==actual_sha,
    record.get('model_test_status')=='PASS',
    int(data.get('checkpoint_index') or -1)==checkpoint,
    int(data.get('accepted_checkpoint_count') or -1)==target,
    data.get('model_test_status')=='PASS',
)
raise SystemExit(0 if all(checks) else 2)
PY
}

refresh_learning_curve() {
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_physical_feature_model_learning_curve.py" \
    --checkpoint-root "$CAMPAIGN_ROOT/checkpoints" \
    --out-dir "$CAMPAIGN_ROOT/model_learning_curve" \
    --minimum-checkpoints 3 --plateau-window 3 \
    --max-marginal-relative-improvement 0.02 \
    --regression-relative-tolerance 0.05 \
    --expected-checkpoint-size "$CHECKPOINT_SIZE" \
    --expected-total-checkpoints "$CHECKPOINTS" \
    --no-fail-exit >> "$LOG" 2>&1
}

run_first100k_sample_efficiency() {
  local checkpoint="$1" model_dir="$2" record_dir="$3"
  [[ "$checkpoint" -eq 1 ]] || return 0
  local out_dir="$record_dir/sample_efficiency_100k"
  local marker="$out_dir/sample_efficiency.complete"
  local summary="$out_dir/physical_feature_sample_efficiency_summary.json"
  local training_csv="$model_dir/training_table/physical_feature_inverse_training_table.csv"
  if [[ -f "$marker" ]] && [[ "$(json_value "$summary" overall_status)" == "PASS" ]]; then
    return 0
  fi
  if [[ ! -f "$training_csv" ]]; then
    log "sample-efficiency advisory waiting: missing training table $training_csv"
    return 0
  fi
  local training_sha
  training_sha="$("$PYTHON_BIN" - "$training_csv" <<'PY'
import hashlib, sys
path=sys.argv[1]
digest=hashlib.sha256()
with open(path,'rb') as handle:
    for chunk in iter(lambda: handle.read(1024*1024), b''):
        digest.update(chunk)
print(digest.hexdigest())
PY
)"
  if [[ ! "$training_sha" =~ ^[0-9a-f]{64}$ ]]; then
    log "sample-efficiency advisory waiting: could not freeze training-table SHA"
    return 0
  fi
  mkdir -p "$out_dir"
  log "sample-efficiency advisory start checkpoint=1 training_csv=$training_csv"
  env OPENBLAS_NUM_THREADS="$MODEL_THREADS" OMP_NUM_THREADS="$MODEL_THREADS" MKL_NUM_THREADS="$MODEL_THREADS" \
    nice -n 15 "$PYTHON_BIN" "$SCRIPT_DIR/benchmark_physical_feature_sample_efficiency.py" \
    --training-csv "$training_csv" --out-dir "$out_dir" \
    --expected-source-rows "$CHECKPOINT_SIZE" --expected-geometry-columns 10 \
    --require-training-csv-sha256 "$training_sha" \
    --training-counts 2400,3216,4000,8000,16000,32000,64000 \
    --model-seeds "$MODEL_INIT_SEED,$((MODEL_INIT_SEED + 1)),$((MODEL_INIT_SEED + 2))" \
    --selection-seed "$SEED_BASE" --split-seed "$MODEL_SPLIT_SEED" \
    --forward-depth 2 --forward-width 128 --inverse-depth 2 --inverse-width 128 \
    --batch-size 4096 --forward-epochs "$SAMPLE_EFFICIENCY_EPOCHS" \
    --inverse-epochs "$SAMPLE_EFFICIENCY_EPOCHS" --patience "$SAMPLE_EFFICIENCY_PATIENCE" \
    --sufficiency-relative-tolerance 0.05 --no-fail-exit >> "$LOG" 2>&1
  local rc=$?
  if [[ "$rc" -eq 0 ]] && [[ "$(json_value "$summary" overall_status)" == "PASS" ]]; then
    touch "$marker"
    log "sample-efficiency advisory PASS checkpoint=1"
  else
    log "sample-efficiency advisory incomplete rc=$rc; campaign continues because this audit is not a production stop gate"
  fi
  return 0
}

refresh_proxy_to_real_calibration() {
  local trigger_round="$1"
  local audit_dir="$CALIBRATION_ROOT/round_$(printf '%03d' "$trigger_round")"
  log "proxy-to-real campaign refresh start trigger_round=$trigger_round"
  "$PYTHON_BIN" "$SCRIPT_DIR/refresh_campaign_proxy_to_real_calibration.py" \
    --rounds-root "$CAMPAIGN_ROOT/rounds" --out-dir "$audit_dir" \
    --active-json "$ACTIVE_CALIBRATION" --trigger-round "$trigger_round" \
    --min-independent-geometries 80 --min-holdout-geometries 20 \
    --no-fail-exit >> "$LOG" 2>&1
  local rc=$?
  log "proxy-to-real campaign refresh returncode=$rc active=$([[ -f "$ACTIVE_CALIBRATION" ]] && echo 1 || echo 0)"
  return 0
}

run_direct_checkpoint() {
  local checkpoint="$1" target="$2" pool="$3" record_dir="$4"
  local attempt model_dir
  attempt=$(( $(find "$record_dir" -maxdepth 1 -type d -name 'model_attempt_*' 2>/dev/null | wc -l | tr -d ' ') + 1 ))
  model_dir="$record_dir/model_attempt_$(printf '%03d' "$attempt")"
  write_state RUN_MODEL "$checkpoint" "$target" "$pool" "$model_dir"
  log "model checkpoint start index=$checkpoint target=$target pool_count=$(pool_count "$pool")"
  bash "$SCRIPT_DIR/run_accepted_physical_feature_model_checkpoint.sh" \
    --accepted-pool-dir "$pool" --out-dir "$model_dir" \
    --geometry-config "$CONFIG" \
    --checkpoint-count "$target" --checkpoint-index "$checkpoint" \
    --seed "$((SEED_BASE + checkpoint))" --max-candidates "$MODEL_MAX_CANDIDATES" \
    --max-epochs "$MODEL_MAX_EPOCHS" --patience "$MODEL_PATIENCE" \
    --model-seed "$MODEL_INIT_SEED" --split-seed "$MODEL_SPLIT_SEED" \
    --model-threads "$MODEL_THREADS" --allow-provisional-uniformity \
    --no-fail-exit --python "$PYTHON_BIN" >> "$LOG" 2>&1
  local rc=$?
  if [[ "$rc" -ne 0 ]]; then log "model checkpoint failed rc=$rc"; return 2; fi
  record_checkpoint "$checkpoint" "$target" "$pool" "$model_dir" "$record_dir" || return 2
  refresh_learning_curve || log "learning-curve audit warning after checkpoint=$checkpoint"
  run_first100k_sample_efficiency "$checkpoint" "$model_dir" "$record_dir"
  log "model checkpoint recorded index=$checkpoint status=$(json_value "$record_dir/checkpoint_record.json" overall_status)"
}

build_adaptive_queue() {
  local pool="$1" queue_build="$2" checkpoint="$3" round_number="$4"
  if [[ "$(json_value "$queue_build/adaptive_physical_acquisition_round_summary.json" overall_status)" == "PASS" ]]; then return 0; fi
  local desired_total=$((checkpoint * CHECKPOINT_SIZE))
  local target_per_bin
  target_per_bin="$(physical_target_count_per_bin "$desired_total")"
  write_state BUILD_QUEUE "$checkpoint" "$((checkpoint * CHECKPOINT_SIZE))" "$pool" "$queue_build"
  log "adaptive queue build start checkpoint=$checkpoint round=$round_number pool_count=$(pool_count "$pool")"
  local -a calibration_args=()
  if [[ -f "$ACTIVE_CALIBRATION" ]]; then
    calibration_args+=(--prediction-calibration-json "$ACTIVE_CALIBRATION")
  fi
  local -a acquisition_mix_args=()
  if [[ -n "$ACQUISITION_MIX_JSON" ]]; then
    acquisition_mix_args+=(--acquisition-mix-json "$ACQUISITION_MIX_JSON")
  fi
  env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 15 \
    bash "$SCRIPT_DIR/run_mars56_s4p_adaptive_physical_acquisition_round.sh" \
    --dataset-dir "$pool" --out-dir "$queue_build" \
    --queue-count "$QUEUE_COUNT" --candidate-count "$CANDIDATE_COUNT" \
    --bins "$PHYSICAL_FEATURE_BINS" --desired-total-count "$desired_total" \
    --target-count-per-bin "$target_per_bin" \
    --prediction-batch-size 8192 --seed "$((SEED_BASE + checkpoint * 100 + round_number))" \
    --k-neighbors 8 --target-envelope-config "$TARGET_ENVELOPE" \
    --local-target-fraction "$LOCAL_TARGET_FRACTION" --rare-marginal-fraction "$RARE_MARGINAL_FRACTION" \
    --rare-marginal-bins 10 --rare-marginal-feature-weights 0.5,0.5,2.0,1.5 \
    --pairwise-fallback-fraction "$PAIRWISE_FALLBACK_FRACTION" \
    --pairwise-feature-pairs "$PAIRWISE_FEATURE_PAIRS" \
    --pairwise-marginal-features "$PAIRWISE_MARGINAL_FEATURES" \
    --local-seed-count 8 \
    --local-perturbation-scales 0.01,0.03,0.08 \
    --reachable-targets-only --redistribute-reachable-quota \
    --require-inside-target-bin --python "$PYTHON_BIN" \
    "${calibration_args[@]}" "${acquisition_mix_args[@]}" >> "$LOG" 2>&1
  local rc=$?
  if [[ "$rc" -ne 0 ]] || [[ "$(json_value "$queue_build/adaptive_physical_acquisition_round_summary.json" overall_status)" != "PASS" ]]; then
    log "adaptive queue build failed rc=$rc"; return 2
  fi
  if [[ "$(json_value "$queue_build/plan/physical_feature_acquisition_plan_summary.json" planning_envelope.desired_total_count)" != "$desired_total" ]] \
    || [[ "$(json_value "$queue_build/plan/physical_feature_acquisition_plan_summary.json" planning_envelope.target_count_per_bin)" != "$target_per_bin" ]]; then
    log "adaptive queue cumulative target contract failed desired_total=$desired_total target_per_bin=$target_per_bin"
    return 2
  fi
  if [[ -n "$ACQUISITION_MIX_JSON" ]]; then
    "$PYTHON_BIN" - "$ACQUISITION_MIX_JSON" "$queue_build/adaptive_physical_acquisition_round_summary.json" <<'PY'
import hashlib,json,pathlib,sys
contract=pathlib.Path(sys.argv[1]).expanduser().resolve()
summary=pathlib.Path(sys.argv[2])
if not contract.is_file() or not summary.is_file(): raise SystemExit(1)
data=json.loads(summary.read_text(encoding='utf-8'))
source=data.get('acquisition_mix_contract_source') or {}
mix=data.get('acquisition_mix_contract') or {}
checks=(
    data.get('overall_status')=='PASS',
    pathlib.Path(source.get('path') or '').expanduser().resolve()==contract,
    source.get('sha256')==hashlib.sha256(contract.read_bytes()).hexdigest(),
    mix.get('arms_are_disjoint') is True,
    mix.get('proxy_values_are_acquisition_only') is True,
    mix.get('requested_counts')==mix.get('selected_counts'),
)
if not all(checks): raise SystemExit(1)
PY
    if [[ "$?" -ne 0 ]]; then
      log "adaptive queue acquisition-mix source contract failed"
      return 2
    fi
  fi
  log "adaptive queue build PASS checkpoint=$checkpoint round=$round_number"
}

use_prepared_chunk2_queue() {
  local checkpoint="$1" round_number="$2"
  [[ "$checkpoint" -eq 2 && "$round_number" -eq 1 ]] || return 1
  if [[ -n "$ACQUISITION_MIX_JSON" ]]; then
    log "prepared chunk2 queue rejected: active five-arm acquisition mix requires a fresh queue"
    return 1
  fi
  local policy="$CHUNK2_PREP/chunk2_queue_preparation_policy.json"
  local adaptive="$CHUNK2_PREP/adaptive_round"
  local selection="$adaptive/selection/physical_feature_targeted_candidate_selection_summary.json"
  local desired_total=$((checkpoint * CHECKPOINT_SIZE))
  local target_per_bin
  target_per_bin="$(physical_target_count_per_bin "$desired_total")"
  if ! "$PYTHON_BIN" - "$policy" "$PREPARED_CHUNK2_POLICY_VERSION" "$QUEUE_COUNT" \
      "$LOCAL_TARGET_FRACTION" "$RARE_MARGINAL_FRACTION" "$PAIRWISE_FALLBACK_FRACTION" \
      "$PAIRWISE_FEATURE_PAIRS" "$PAIRWISE_MARGINAL_FEATURES" "$PHYSICAL_FEATURE_BINS" \
      "$desired_total" "$target_per_bin" <<'PY'
import json,pathlib,sys
(raw,version,queue_count,local_fraction,rare_fraction,pairwise_fraction,pairs,marginals,bins,desired_total,target_per_bin)=sys.argv[1:]
path=pathlib.Path(raw)
if not path.is_file(): raise SystemExit(1)
data=json.loads(path.read_text(encoding="utf-8"))
checks=(
    int(data.get("policy_version") or 0)==int(version),
    int(data.get("queue_count") or 0)==int(queue_count),
    abs(float(data.get("local_target_fraction") or -1)-float(local_fraction))<=1e-12,
    abs(float(data.get("rare_marginal_fraction") or -1)-float(rare_fraction))<=1e-12,
    abs(float(data.get("pairwise_fallback_fraction") or -1)-float(pairwise_fraction))<=1e-12,
    str(data.get("pairwise_feature_pairs") or "")==pairs,
    str(data.get("pairwise_marginal_features") or "")==marginals,
    int(data.get("bins_per_feature") or 0)==int(bins),
    int(data.get("desired_total_count") or 0)==int(desired_total),
    int(data.get("target_count_per_bin") or 0)==int(target_per_bin),
    data.get("proxy_values_are_acquisition_only") is True,
)
if not all(checks): raise SystemExit(1)
PY
  then
    log "prepared chunk2 queue rejected before wait: missing or stale policy; rebuilding with current adaptive contract"
    return 1
  fi
  while [[ ! -f "$CHUNK2_PREP/chunk2_queue_preparation.complete" ]]; do
    if ! pgrep -f 'PREPARE_CHUNK2_QUEUE_AFTER_FIRST100K_20260710.sh' >/dev/null 2>&1; then return 1; fi
    write_state WAIT_PREPARED_CHUNK2 "$checkpoint" "$((checkpoint * CHECKPOINT_SIZE))" "$CURRENT_POOL" "$CHUNK2_PREP"
    sleep "$POLL_SECONDS"
  done
  [[ "$(json_value "$CHUNK2_PREP/chunk2_queue_preparation_summary.json" overall_status)" == "PASS" ]] || return 1
  [[ "$(json_value "$adaptive/adaptive_physical_acquisition_round_summary.json" overall_status)" == "PASS" ]] || return 1
  [[ "$(json_value "$adaptive/plan/physical_feature_acquisition_plan_summary.json" planning_envelope.desired_total_count)" == "$desired_total" ]] || return 1
  [[ "$(json_value "$adaptive/plan/physical_feature_acquisition_plan_summary.json" planning_envelope.target_count_per_bin)" == "$target_per_bin" ]] || return 1
  "$PYTHON_BIN" - "$selection" "$QUEUE_COUNT" "$RARE_MARGINAL_FRACTION" \
      "$PAIRWISE_FALLBACK_FRACTION" "$PAIRWISE_FEATURE_PAIRS" "$PAIRWISE_MARGINAL_FEATURES" <<'PY'
import json,pathlib,sys
raw,queue_count,rare_fraction,pairwise_fraction,pairs,marginals=sys.argv[1:]
path=pathlib.Path(raw)
if not path.is_file(): raise SystemExit(1)
data=json.loads(path.read_text(encoding="utf-8")); args=data.get("arguments") or {}
queue_count=int(queue_count)
checks=(
    data.get("overall_status")=="PASS",
    int(data.get("selected_candidate_count") or 0)==queue_count,
    int(args.get("rare_marginal_max_total") or 0)==int(round(queue_count*float(rare_fraction))),
    int(args.get("pairwise_fallback_max_total") or 0)==int(round(queue_count*float(pairwise_fraction))),
    str(args.get("pairwise_feature_pairs") or "")==pairs,
    str(args.get("pairwise_marginal_features") or "")==marginals,
)
if not all(checks): raise SystemExit(1)
PY
}

# Rebuild the active calibration pointer after a controller restart. Old rounds
# without prediction provenance are ignored rather than treated as evidence.
latest_completed_round=0
for completed_round in "$CAMPAIGN_ROOT"/rounds/round_*; do
  [[ -f "$completed_round/real_emx/round.complete" ]] || continue
  completed_name="$(basename "$completed_round")"
  completed_number="${completed_name#round_}"; completed_number="${completed_number%%_*}"
  if [[ "$completed_number" =~ ^[0-9]+$ ]] && [[ $((10#$completed_number)) -gt "$latest_completed_round" ]]; then
    latest_completed_round=$((10#$completed_number))
  fi
done
if [[ "$latest_completed_round" -gt 0 ]]; then
  refresh_proxy_to_real_calibration "$latest_completed_round"
fi

for checkpoint in $(seq 1 "$CHECKPOINTS"); do
  target=$((checkpoint * CHECKPOINT_SIZE))
  checkpoint_dir="$CAMPAIGN_ROOT/checkpoints/checkpoint_$(printf '%02d' "$checkpoint")_n${target}"
  mkdir -p "$checkpoint_dir"

  while :; do
    accepted_count="$(pool_count "$CURRENT_POOL")"
    if [[ -f "$checkpoint_dir/model_test.complete" ]] \
      && ! checkpoint_record_valid "$checkpoint" "$target" "$checkpoint_dir"; then
      log "stale checkpoint marker rejected index=$checkpoint target=$target; rebuilding evidence"
      rm -f "$checkpoint_dir/model_test.complete" "$checkpoint_dir/formal_checkpoint.pass"
    fi
    if [[ "$accepted_count" -ge "$target" && ! -f "$checkpoint_dir/model_test.complete" ]]; then
      run_direct_checkpoint "$checkpoint" "$target" "$CURRENT_POOL" "$checkpoint_dir" || exit 2
    fi

    if [[ -f "$checkpoint_dir/model_test.complete" ]]; then
      if [[ "$checkpoint" -eq 1 ]]; then
        recorded_manifest="$(json_value "$checkpoint_dir/checkpoint_record.json" model_manifest)"
        if [[ -n "$recorded_manifest" ]]; then
          run_first100k_sample_efficiency "$checkpoint" "$(dirname "$recorded_manifest")" "$checkpoint_dir"
        fi
      fi
      if [[ "$checkpoint" -lt "$CHECKPOINTS" || -f "$checkpoint_dir/formal_checkpoint.pass" ]]; then
        write_state CHECKPOINT_RECORDED "$checkpoint" "$target" "$CURRENT_POOL" "$checkpoint_dir"
        break
      fi
      log "final 1M model exists but uniformity is not PASS; continuing targeted remediation"
    fi

    round_number="$(next_round_number)"
    round_dir="$CAMPAIGN_ROOT/rounds/round_$(printf '%03d' "$round_number")_checkpoint_$(printf '%02d' "$checkpoint")"
    mkdir -p "$round_dir"
    queue_build="$round_dir/adaptive_queue"

    if use_prepared_chunk2_queue "$checkpoint" "$round_number"; then
      queue_build="$CHUNK2_PREP/adaptive_round"
      log "reusing prepared chunk2 adaptive queue"
    else
      build_adaptive_queue "$CURRENT_POOL" "$queue_build" "$checkpoint" "$round_number" || exit 2
    fi
    queue_csv="$queue_build/queue/mars56_grounded_s4p_candidate_queue.csv"

    write_state RUN_REAL_EMX "$checkpoint" "$target" "$CURRENT_POOL" "$round_dir"
    log "real EMX round start round=$round_number checkpoint=$checkpoint target=$target"
    bash "$SCRIPT_DIR/run_real_emx_accepted_increment_round.sh" \
      --queue-csv "$queue_csv" --queue-dir "$queue_build/queue" --config "$CONFIG" \
      --source-pool-dir "$CURRENT_POOL" --out-dir "$round_dir/real_emx" \
      --target-accepted "$target" --checkpoint-index "$checkpoint" \
      --raw-count "$QUEUE_COUNT" --jobs "$JOBS" --chunk-size 64 \
      --model-max-candidates "$MODEL_MAX_CANDIDATES" \
      --model-max-epochs "$MODEL_MAX_EPOCHS" --model-patience "$MODEL_PATIENCE" \
      --model-threads "$MODEL_THREADS" --model-seed "$MODEL_INIT_SEED" \
      --split-seed "$MODEL_SPLIT_SEED" --seed "$((SEED_BASE + checkpoint * 100 + round_number))" \
      --no-fail-exit --python "$PYTHON_BIN" >> "$LOG" 2>&1
    round_rc=$?
    if [[ "$round_rc" -ne 0 || ! -f "$round_dir/real_emx/round.complete" ]]; then
      log "real EMX round failed rc=$round_rc"; exit 2
    fi
    refresh_proxy_to_real_calibration "$round_number"
    CURRENT_POOL="$round_dir/real_emx/accepted_pool"
    printf '%s\n' "$CURRENT_POOL" > "$CURRENT_POOL_FILE"
    accepted_count="$(pool_count "$CURRENT_POOL")"
    log "real EMX round complete round=$round_number accepted=$accepted_count"

    round_model="$round_dir/real_emx/model_checkpoint_${checkpoint}_n${target}"
    if [[ "$accepted_count" -ge "$target" && -f "$round_model/model_test.complete" ]]; then
      record_checkpoint "$checkpoint" "$target" "$CURRENT_POOL" "$round_model" "$checkpoint_dir" || exit 2
      refresh_learning_curve || log "learning-curve audit warning after checkpoint=$checkpoint"
      run_first100k_sample_efficiency "$checkpoint" "$round_model" "$checkpoint_dir"
      if [[ "$checkpoint" -eq "$CHECKPOINTS" && ! -f "$checkpoint_dir/formal_checkpoint.pass" ]]; then
        rm -f "$checkpoint_dir/model_test.complete"
        log "final model remains provisional; another targeted round will be acquired"
      fi
    fi
  done
done

refresh_learning_curve || log "final learning-curve audit warning"

final_checkpoint="$CAMPAIGN_ROOT/checkpoints/checkpoint_10_n1000000"
if [[ ! -f "$final_checkpoint/model_test.complete" || ! -f "$final_checkpoint/formal_checkpoint.pass" ]]; then
  log "ERROR: final checkpoint contract missing"
  exit 2
fi

mkdir -p "$FINAL_AUDIT_DIR"
"$PYTHON_BIN" "$SCRIPT_DIR/audit_accepted_1m_campaign_completion.py" \
  --campaign-root "$CAMPAIGN_ROOT" \
  --final-pool-dir "$CURRENT_POOL" \
  --out-dir "$FINAL_AUDIT_DIR" \
  --expected-total "$TOTAL_ACCEPTED" \
  --checkpoint-count "$CHECKPOINTS" \
  --checkpoint-size "$CHECKPOINT_SIZE" \
  --check-touchstone-exists >> "$LOG" 2>&1
if [[ $? -ne 0 ]] || [[ ! -f "$FINAL_AUDIT_MARKER" ]] || [[ "$(json_value "$FINAL_AUDIT_SUMMARY" overall_status)" != "PASS" ]]; then
  log "final strict campaign audit failed"
  exit 2
fi
cp "$FINAL_AUDIT_SUMMARY" "$CAMPAIGN_ROOT/accepted_1m_campaign_final_summary.json"

final_manifest_path="$(json_value "$FINAL_AUDIT_SUMMARY" artifacts.final_model_manifest)"
learning_curve_path="$(json_value "$FINAL_AUDIT_SUMMARY" artifacts.learning_curve)"
mkdir -p "$FINAL_EVIDENCE_DIR"
evidence_args=(
  --campaign-completion-summary "$FINAL_AUDIT_SUMMARY"
  --learning-curve-summary "$learning_curve_path"
  --final-model-manifest "$final_manifest_path"
  --out-dir "$FINAL_EVIDENCE_DIR"
  --expected-total "$TOTAL_ACCEPTED"
  --expected-checkpoints "$CHECKPOINTS"
  --min-hfss-samples "$PUBLICATION_MIN_HFSS_SAMPLES"
  --max-percent-error 10
)
validation_records=()
if [[ -n "$HFSS_VALIDATION_RECORDS" ]]; then
  IFS=':' read -r -a validation_records <<< "$HFSS_VALIDATION_RECORDS"
  for record in "${validation_records[@]}"; do
    [[ -n "$record" ]] && evidence_args+=(--hfss-validation-record "$record")
  done
fi
"$PYTHON_BIN" "$SCRIPT_DIR/audit_final_model_publication_readiness.py" "${evidence_args[@]}" >> "$LOG" 2>&1
if [[ $? -ne 0 ]] || [[ ! -f "$FINAL_EVIDENCE_MARKER" ]] || [[ "$(json_value "$FINAL_EVIDENCE_SUMMARY" overall_status)" != "PASS" ]]; then
  log "final model evidence matrix failed"
  exit 2
fi
cp "$FINAL_EVIDENCE_SUMMARY" "$CAMPAIGN_ROOT/accepted_1m_model_evidence_final_summary.json"
publication_status="$(json_value "$FINAL_EVIDENCE_SUMMARY" publication_readiness_status)"
log "final model evidence matrix PASS publication_readiness=$publication_status"
if [[ "$publication_status" == "PASS" ]]; then
  mkdir -p "$FINAL_RESIDUAL_DIR"
  residual_args=(
    --out-dir "$FINAL_RESIDUAL_DIR"
    --min-samples "$PUBLICATION_MIN_HFSS_SAMPLES"
  )
  for record in "${validation_records[@]}"; do
    [[ -n "$record" ]] && residual_args+=(--hfss-validation-record "$record")
  done
  "$PYTHON_BIN" "$SCRIPT_DIR/benchmark_emx_hfss_cross_solver_residual.py" "${residual_args[@]}" >> "$LOG" 2>&1
  if [[ $? -ne 0 ]] || [[ "$(json_value "$FINAL_RESIDUAL_SUMMARY" overall_status)" != "COMPLETE_REVIEW_REQUIRED" ]]; then
    log "final cross-solver residual benchmark failed"
    exit 2
  fi
  cp "$FINAL_RESIDUAL_SUMMARY" "$CAMPAIGN_ROOT/accepted_1m_cross_solver_residual_final_summary.json"
  residual_decision="$(json_value "$FINAL_RESIDUAL_SUMMARY" decision)"
  log "final cross-solver residual benchmark complete decision=$residual_decision"
else
  log "final cross-solver residual benchmark skipped because raw publication status is $publication_status"
fi
touch "$COMPLETE_MARKER"
write_state COMPLETE 10 "$TOTAL_ACCEPTED" "$CURRENT_POOL" "$CAMPAIGN_ROOT/accepted_1m_model_evidence_final_summary.json"
log "accepted 1M campaign COMPLETE"
