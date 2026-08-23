#!/usr/bin/env bash
# Preserve verified parallel=1 results, benchmark parallel=2 worker counts, and
# launch only the disjoint residual queue. This is an intentional one-time
# migration for the urgent first-100k checkpoint.

set -euo pipefail

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
PY="${PY:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python}"
PROD="${PROD:-$BASE/production_chunks/chunk_001_100k_urgent_targeted_20260709}"
QUEUE="${QUEUE:-$BASE/status/first100k_urgent_targeted_20260709/adaptive_round_100k/queue/mars56_grounded_s4p_candidate_queue.csv}"
CONFIG="${CONFIG:-$PROJECT/configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml}"
STATUS="${STATUS:-$BASE/status/first100k_urgent_targeted_20260709/parallel2_residual_migration}"
EXPECTED_COUNT=100000
BENCHMARK_COUNT=96

mkdir -p "$STATUS"
LOG="$STATUS/migration.log"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" | tee -a "$LOG"
}

json_value() {
  "$PY" - "$1" "$2" <<'PY'
import json, pathlib, sys
payload=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
value=payload
for part in sys.argv[2].split("."):
    value=value[part]
print(value)
PY
}

require_pass_summary() {
  local path="$1"
  local expected_rows="$2"
  "$PY" - "$path" "$expected_rows" <<'PY'
import json, pathlib, sys
path=pathlib.Path(sys.argv[1]); expected=int(sys.argv[2])
if not path.is_file(): raise SystemExit(f"missing summary: {path}")
d=json.loads(path.read_text(encoding="utf-8"))
if d.get("overall_status") != "PASS": raise SystemExit(f"status={d.get('overall_status')!r}")
rows=int(d.get("merged_row_count") or 0)
if rows != expected: raise SystemExit(f"rows={rows}, expected={expected}")
print(f"summary_pass rows={rows}")
PY
}

cd "$PROJECT"

log "preflight start"
"$PY" - "$QUEUE" "$CONFIG" "$EXPECTED_COUNT" <<'PY'
import csv, pathlib, sys, yaml
queue=pathlib.Path(sys.argv[1]); config=pathlib.Path(sys.argv[2]); expected=int(sys.argv[3])
with queue.open(newline="",encoding="utf-8-sig") as h: rows=list(csv.DictReader(h))
if len(rows) != expected: raise SystemExit(f"queue rows={len(rows)}, expected={expected}")
if len({row.get('candidate_id') for row in rows}) != expected: raise SystemExit("candidate IDs are not unique")
cfg=yaml.safe_load(config.read_text(encoding="utf-8"))
args=[str(item) for item in (cfg.get("emx") or {}).get("extra_args", [])]
for required in ("--parallel=2", "--simultaneous-frequencies=0"):
    if required not in args: raise SystemExit(f"missing config flag {required}")
print("preflight_contract=PASS")
PY

if [[ -f "$STATUS/migration.complete" ]]; then
  log "migration already complete"
  exit 0
fi

RUNNER_PID="$(pgrep -f "run_candidate_queue_dataset_parallel.py.*$PROD/dataset" | head -n 1 || true)"
if [[ -z "$RUNNER_PID" ]]; then
  log "ERROR: original production runner is not active"
  exit 2
fi
PGID="$(ps -o pgid= -p "$RUNNER_PID" | tr -d ' ')"
RAW_BEFORE="$(find "$PROD/dataset/parallel_shards" -type f -name '*.s4p' -size +0c 2>/dev/null | wc -l | tr -d ' ')"
log "stopping original parallel=1 process group pgid=$PGID raw_nonzero_before=$RAW_BEFORE"
kill -TERM -- "-$PGID"
for _ in $(seq 1 60); do
  if ! pgrep -f "run_candidate_queue_dataset.py.*$PROD/dataset/parallel_shards" >/dev/null \
    && ! pgrep -f "run_candidate_queue_dataset_parallel.py.*$PROD/dataset" >/dev/null \
    && ! pgrep -f "emx_cae_singularity.*$PROD/dataset/parallel_shards" >/dev/null; then
    break
  fi
  sleep 1
done
if pgrep -f "run_candidate_queue_dataset.py.*$PROD/dataset/parallel_shards" >/dev/null \
  || pgrep -f "run_candidate_queue_dataset_parallel.py.*$PROD/dataset" >/dev/null \
  || pgrep -f "emx_cae_singularity.*$PROD/dataset/parallel_shards" >/dev/null; then
  log "ERROR: original process group did not stop cleanly"
  exit 2
fi
log "original parallel=1 runner stopped"

PARTITION="$STATUS/partition_after_stop"
rm -rf "$PARTITION"
"$PY" scripts/build_emx_residual_queue_from_completed.py \
  --candidate-csv "$QUEUE" \
  --dataset-dir "$PROD/dataset/parallel_shards" \
  --out-dir "$PARTITION" \
  --expected-count "$EXPECTED_COUNT" | tee -a "$LOG"
PARTITION_SUMMARY="$PARTITION/residual_queue_partition_summary.json"
if [[ "$(json_value "$PARTITION_SUMMARY" overall_status)" != "PASS" ]]; then
  log "ERROR: completed/residual partition failed"
  exit 2
fi
P1_COMPLETED="$(json_value "$PARTITION_SUMMARY" completed_verified_count)"
RESIDUAL_COUNT="$(json_value "$PARTITION_SUMMARY" residual_count)"
if [[ $((P1_COMPLETED + RESIDUAL_COUNT)) -ne "$EXPECTED_COUNT" ]]; then
  log "ERROR: partition arithmetic mismatch"
  exit 2
fi
if [[ "$RESIDUAL_COUNT" -le "$BENCHMARK_COUNT" ]]; then
  log "ERROR: residual queue too small for benchmark"
  exit 2
fi
log "partition PASS kept_p1=$P1_COMPLETED residual=$RESIDUAL_COUNT"

SCALING="$STATUS/scaling_benchmark"
rm -rf "$SCALING"
mkdir -p "$SCALING"
"$PY" - "$PARTITION/residual_candidate_queue.csv" "$SCALING/candidates_n96.csv" "$SCALING/residual_after_n96.csv" "$BENCHMARK_COUNT" <<'PY'
import csv, pathlib, sys
source=pathlib.Path(sys.argv[1]); selected=pathlib.Path(sys.argv[2]); residual=pathlib.Path(sys.argv[3]); n=int(sys.argv[4])
with source.open(newline="",encoding="utf-8-sig") as h:
    reader=csv.DictReader(h); rows=list(reader); fields=list(reader.fieldnames or [])
if len(rows) <= n: raise SystemExit("not enough residual rows")
for path, subset in ((selected, rows[:n]), (residual, rows[n:])):
    with path.open("w",newline="",encoding="utf-8") as h:
        writer=csv.DictWriter(h,fieldnames=fields); writer.writeheader(); writer.writerows(subset)
print(f"benchmark_rows={n} final_residual_rows={len(rows)-n}")
PY

for JOBS in 32 48; do
  OUT="$SCALING/p2_j$JOBS"
  START="$(date +%s.%N)"
  log "parallel2 scaling benchmark jobs=$JOBS start"
  env RFIC_SKIP_LAYOUT_PREVIEWS=1 RFIC_SKIP_LUMPED_COMPARE=1 \
    "$PY" scripts/run_candidate_queue_dataset_parallel.py \
    --candidate-csv "$SCALING/candidates_n96.csv" \
    --config "$CONFIG" \
    --out-dir "$OUT" \
    --jobs "$JOBS" --expected-jobs "$JOBS" \
    --max-count "$BENCHMARK_COUNT" --expected-count "$BENCHMARK_COUNT" \
    --batch-size 1 \
    --force-wideband-5-60-0p5 \
    --expected-frequency-start-ghz 5 --expected-frequency-stop-ghz 60 \
    --expected-frequency-step-ghz 0.5 --expected-frequency-points 111 \
    --expected-touchstone-extension .s4p --expected-ports 4 \
    --max-touchstone-checks "$BENCHMARK_COUNT" \
    --expected-port-mode single_ended_shield_grounded \
    --expected-pin-purpose 51 --no-fail-exit > "$SCALING/p2_j$JOBS.log" 2>&1
  END="$(date +%s.%N)"
  WALL="$(awk -v s="$START" -v e="$END" 'BEGIN {printf "%.6f", e-s}')"
  printf '%s\n' "$WALL" > "$SCALING/p2_j$JOBS.wall_seconds"
  require_pass_summary "$OUT/parallel_candidate_queue_dataset_summary.json" "$BENCHMARK_COUNT"
  log "parallel2 scaling benchmark jobs=$JOBS PASS wall_seconds=$WALL"
done

J32="$(cat "$SCALING/p2_j32.wall_seconds")"
J48="$(cat "$SCALING/p2_j48.wall_seconds")"
CHOSEN_JOBS="$(awk -v a="$J32" -v b="$J48" 'BEGIN {print (b < a ? 48 : 32)}')"
CHOSEN_DIR="$SCALING/p2_j$CHOSEN_JOBS"
SELECTED_DATASET="$PROD/dataset/migration_p2_benchmark_selected"
if [[ -e "$SELECTED_DATASET" ]]; then
  log "ERROR: selected benchmark destination already exists"
  exit 2
fi
mv "$CHOSEN_DIR" "$SELECTED_DATASET"
FINAL_RESIDUAL="$SCALING/residual_after_n96.csv"
FINAL_RESIDUAL_COUNT=$((RESIDUAL_COUNT - BENCHMARK_COUNT))
ACTUAL_FINAL_COUNT="$($PY - "$FINAL_RESIDUAL" <<'PY'
import csv,pathlib,sys
with pathlib.Path(sys.argv[1]).open(newline="",encoding="utf-8-sig") as h: print(sum(1 for _ in csv.DictReader(h)))
PY
)"
if [[ "$ACTUAL_FINAL_COUNT" -ne "$FINAL_RESIDUAL_COUNT" ]]; then
  log "ERROR: final residual count mismatch actual=$ACTUAL_FINAL_COUNT expected=$FINAL_RESIDUAL_COUNT"
  exit 2
fi

PROVENANCE="$STATUS/final_residual_provenance"
rm -rf "$PROVENANCE"
ORIGINAL_QUEUE_SUMMARY="$(dirname "$QUEUE")/mars56_grounded_s4p_candidate_queue_summary.json"
DERIVED_QUEUE_SUMMARY="$(dirname "$FINAL_RESIDUAL")/mars56_grounded_s4p_candidate_queue_summary.json"
"$PY" - "$ORIGINAL_QUEUE_SUMMARY" "$FINAL_RESIDUAL" "$DERIVED_QUEUE_SUMMARY" "$PARTITION_SUMMARY" <<'PY'
import csv, json, pathlib, sys
source, residual, target, partition = map(pathlib.Path, sys.argv[1:])
payload = json.loads(source.read_text(encoding="utf-8"))
with residual.open(newline="", encoding="utf-8-sig") as handle:
    count = sum(1 for _ in csv.DictReader(handle))
payload.update(
    candidate_csv=str(residual.resolve()),
    sample_count=count,
    overall_status="PASS",
    decision="USE_PHYSICAL_FEATURE_TARGETED_RESIDUAL_QUEUE_FOR_MARS56_GROUNDED_S4P_EMX",
    residual_partition_summary=str(partition.resolve()),
    residual_queue_note="Physical-feature targeted residual after subtracting verified completed queue geometries; no new geometry was invented.",
)
target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
"$PY" scripts/audit_mars56_s4p_candidate_queue_provenance.py \
  --candidate-csv "$FINAL_RESIDUAL" \
  --expected-count "$FINAL_RESIDUAL_COUNT" \
  --out-dir "$PROVENANCE" | tee -a "$LOG"

RESIDUAL_OUT="$PROD/dataset/residual_parallel2"
if [[ -e "$RESIDUAL_OUT" ]]; then
  log "ERROR: residual production destination already exists"
  exit 2
fi
RUN_LOG="$STATUS/residual_parallel2_runner.log"
log "launching residual parallel2 jobs=$CHOSEN_JOBS count=$FINAL_RESIDUAL_COUNT"
nohup setsid -f -w env RFIC_SKIP_LAYOUT_PREVIEWS=1 RFIC_SKIP_LUMPED_COMPARE=1 \
  "$PY" scripts/run_candidate_queue_dataset_parallel.py \
  --candidate-csv "$FINAL_RESIDUAL" \
  --config "$CONFIG" \
  --out-dir "$RESIDUAL_OUT" \
  --jobs "$CHOSEN_JOBS" --expected-jobs "$CHOSEN_JOBS" \
  --chunk-size 64 \
  --max-count "$FINAL_RESIDUAL_COUNT" --expected-count "$FINAL_RESIDUAL_COUNT" \
  --batch-size 1 \
  --force-wideband-5-60-0p5 \
  --expected-frequency-start-ghz 5 --expected-frequency-stop-ghz 60 \
  --expected-frequency-step-ghz 0.5 --expected-frequency-points 111 \
  --expected-touchstone-extension .s4p --expected-ports 4 \
  --max-touchstone-checks "$FINAL_RESIDUAL_COUNT" \
  --expected-port-mode single_ended_shield_grounded \
  --expected-pin-purpose 51 --no-fail-exit --resume-completed \
  > "$RUN_LOG" 2>&1 < /dev/null &
LAUNCH_PID=$!
sleep 5
ACTIVE_RUNNER="$(pgrep -f "run_candidate_queue_dataset_parallel.py.*$RESIDUAL_OUT" | head -n 1 || true)"
ACTIVE_WORKERS="$(pgrep -af "run_candidate_queue_dataset.py.*$RESIDUAL_OUT/parallel_shards" | grep -v grep | wc -l | tr -d ' ')"
if [[ -z "$ACTIVE_RUNNER" || "$ACTIVE_WORKERS" -ne "$CHOSEN_JOBS" ]]; then
  log "ERROR: residual runner failed to reach requested workers runner=$ACTIVE_RUNNER workers=$ACTIVE_WORKERS"
  exit 2
fi

MONITOR_SCRIPT="$PROJECT/scripts/MONITOR_MARS56_FIRST100K_20260709.sh"
if [[ -x "$MONITOR_SCRIPT" ]] \
  && ! pgrep -af "MONITOR_MARS56_FIRST100K_20260709.sh" | grep -v grep >/dev/null; then
  MONITOR_STATUS="$BASE/status/first100k_urgent_targeted_20260709/live_monitor"
  nohup setsid bash "$MONITOR_SCRIPT" > "$MONITOR_STATUS/live_monitor_wrapper.log" 2>&1 < /dev/null &
  log "live monitor relaunched pid=$!"
fi

"$PY" - "$STATUS/migration_summary.json" "$PARTITION_SUMMARY" "$SCALING/candidates_n96.csv" "$FINAL_RESIDUAL" "$CONFIG" "$P1_COMPLETED" "$BENCHMARK_COUNT" "$FINAL_RESIDUAL_COUNT" "$CHOSEN_JOBS" "$J32" "$J48" "$SELECTED_DATASET" "$RESIDUAL_OUT" "$ACTIVE_RUNNER" <<'PY'
import hashlib,json,pathlib,sys
from datetime import datetime,timezone
(target,partition,benchmark_csv,residual_csv,config,p1,benchmark_n,residual_n,jobs,j32,j48,selected,residual_out,runner)=sys.argv[1:]
def rec(raw):
    p=pathlib.Path(raw); item={"path":str(p),"exists":p.exists()}
    if p.is_file(): item.update(size_bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    return item
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":"PASS",
 "decision":"RUN_PARALLEL2_RESIDUAL_QUEUE",
 "partition_summary":rec(partition),
 "config":rec(config),
 "preserved_parallel1_count":int(p1),
 "selected_parallel2_benchmark_count":int(benchmark_n),
 "launched_parallel2_residual_count":int(residual_n),
 "combined_unique_candidate_count":int(p1)+int(benchmark_n)+int(residual_n),
 "chosen_jobs":int(jobs),
 "benchmark_wall_seconds":{"jobs_32":float(j32),"jobs_48":float(j48)},
 "benchmark_candidate_csv":rec(benchmark_csv),
 "final_residual_candidate_csv":rec(residual_csv),
 "selected_benchmark_dataset":selected,
 "residual_output_dir":residual_out,
 "runner_pid":int(runner),
 "checks":{
   "combined_count_is_100000":int(p1)+int(benchmark_n)+int(residual_n)==100000,
   "residual_runner_alive":True,
 },
 "scientific_caveat":"Raw unique S4P completion still requires Lp/Ls/Q/|K| acceptance and uniformity audits before model training.",
}
if not all(payload["checks"].values()): raise SystemExit(payload)
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
print(json.dumps(payload,indent=2,ensure_ascii=False))
PY
touch "$STATUS/migration.complete"
log "migration PASS launch_pid=$LAUNCH_PID runner=$ACTIVE_RUNNER workers=$ACTIVE_WORKERS"
