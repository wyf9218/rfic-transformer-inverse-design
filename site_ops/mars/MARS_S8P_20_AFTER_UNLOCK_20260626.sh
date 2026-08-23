#!/usr/bin/env bash
set -euo pipefail

# Run this on mars-0002 after the existing GUI session is unlocked.
# It patches the two known blockers, then reruns a 20-sample .s8p EMX pilot
# with the required 8-port / 5-60 GHz / 0.5 GHz / 111-point contract.

locate_repo() {
  for candidate in \
    "$PWD" \
    "$PWD/rfic-transformer-inverse-design" \
    "$HOME/researcher/transformer_inverse/rfic-transformer-inverse-design" \
    "$HOME/transformer_inverse/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/transformer_inverse/rfic-transformer-inverse-design"; do
    if [[ -f "$candidate/pyproject.toml" && -d "$candidate/rfic_transformer_inverse_design" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

REPO_ROOT="${REPO_ROOT:-$(locate_repo)}"
cd "$REPO_ROOT"

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
  else
    PY="$(command -v python)"
  fi
fi
export PYTHON="$PY"

echo "REPO_ROOT=$REPO_ROOT"
echo "PYTHON=$PYTHON"

STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
WORK_DIR="${WORK_DIR:-/shared/research/${USER:-researcher}/codex_next_gen_s8p_ssh_20260620}"
RETURNS_DIR="${RETURNS_DIR:-$WORK_DIR/returns}"
mkdir -p "$RETURNS_DIR"

link_latest() {
  local source_path="$1"
  local target_path="$2"
  rm -f "$target_path"
  ln -s "$source_path" "$target_path" 2>/dev/null || cp -f "$source_path" "$target_path"
}

run_stage1_calibration_if_available() {
  if [[ "${RUN_STAGE1_CALIBRATION:-1}" != "1" ]]; then
    echo "STAGE1_CALIBRATION_SKIPPED disabled_by_RUN_STAGE1_CALIBRATION"
    return 0
  fi

  local cal_tar=""
  for candidate in \
    "$REPO_ROOT/next_gen_s8p_evidence_20260619/reports/s8p_shared_line_width_mars_evidence_20260622/calibration_execution_packet_stage1_wideband_20260626.tar.gz" \
    "$REPO_ROOT/reports/s8p_shared_line_width_mars_evidence_20260622/calibration_execution_packet_stage1_wideband_20260626.tar.gz" \
    "$REPO_ROOT/calibration_execution_packet_stage1_wideband_20260626.tar.gz"; do
    if [[ -f "$candidate" ]]; then
      cal_tar="$candidate"
      break
    fi
  done

  if [[ -z "$cal_tar" ]]; then
    echo "STAGE1_CALIBRATION_SKIPPED missing_calibration_execution_packet_stage1_wideband_20260626.tar.gz"
    return 0
  fi

  local cal_work="$REPO_ROOT/runs/stage1_calibration_wideband_${STAMP}"
  rm -rf "$cal_work"
  mkdir -p "$cal_work"
  tar -xzf "$cal_tar" -C "$cal_work"

  local cal_script
  cal_script="$(find "$cal_work" -maxdepth 3 -type f -name mars_run_emx_calibration.sh -print -quit)"
  if [[ -z "$cal_script" ]]; then
    echo "STAGE1_CALIBRATION_SKIPPED missing_mars_run_emx_calibration.sh"
    return 0
  fi

  "$PYTHON" - "$cal_script" "$REPO_ROOT" <<'PY'
from pathlib import Path
import sys

script = Path(sys.argv[1])
repo = Path(sys.argv[2]).resolve()
text = script.read_text(encoding="utf-8")
text = text.replace(
    "/home/researcher/researcher/transformer_inverse/rfic-transformer-inverse-design",
    str(repo),
)
script.write_text(text, encoding="utf-8")
PY
  chmod +x "$cal_script"

  echo "STAGE1_CALIBRATION_START $(date) packet=$cal_tar"
  set +e
  (cd "$(dirname "$cal_script")" && bash "$cal_script") > "$cal_work/stage1_mars_run.log" 2>&1
  local stage1_rc=$?
  set -e

  local stage1_run_root="$REPO_ROOT/runs/emx_hfss_calibration_20260626"
  local stage1_return_tar="$RETURNS_DIR/stage1_emx_calibration_wideband_${STAMP}.tar.gz"
  local stage1_manifest="${stage1_return_tar}.manifest.json"
  "$PYTHON" - "$cal_work" "$stage1_run_root" "$stage1_rc" "$stage1_return_tar" "$stage1_manifest" <<'PY'
from pathlib import Path
import hashlib
import json
import sys
import tarfile

cal_work = Path(sys.argv[1]).resolve()
stage1_run_root = Path(sys.argv[2]).resolve()
return_code = int(sys.argv[3])
tar_path = Path(sys.argv[4]).resolve()
manifest_path = Path(sys.argv[5]).resolve()
tar_path.parent.mkdir(parents=True, exist_ok=True)
roots = [cal_work]
if stage1_run_root.exists():
    roots.append(stage1_run_root)
files = []
with tarfile.open(tar_path, "w:gz") as tar:
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                arcname = Path(root.name) / path.relative_to(root)
                tar.add(path, arcname=str(arcname))
                files.append(path)
sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()
manifest = {
    "schema": "rfic_transformer_stage1_emx_calibration_return.v1",
    "status": "PASS" if return_code == 0 else "FAIL",
    "return_code": return_code,
    "tarball": str(tar_path),
    "tarball_sha256": sha,
    "calibration_work_dir": str(cal_work),
    "stage1_run_root": str(stage1_run_root),
    "file_count": len(files),
    "s2p_count": sum(1 for path in files if path.suffix.lower() == ".s2p"),
    "log_exists": (cal_work / "stage1_mars_run.log").is_file(),
    "files": [str(path) for path in files],
}
manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
tar_path.with_suffix(tar_path.suffix + ".sha256").write_text(f"{sha}  {tar_path.name}\n", encoding="utf-8")
print(f"STAGE1_RETURN_PACKAGE={tar_path}")
print(f"STAGE1_RETURN_SHA256={sha}")
print(f"STAGE1_RETURN_MANIFEST={manifest_path}")
print(f"STAGE1_RETURN_STATUS={manifest['status']}")
print(f"STAGE1_S2P_COUNT={manifest['s2p_count']}")
PY
  link_latest "$stage1_return_tar" "$RETURNS_DIR/stage1_emx_calibration_wideband_latest.tar.gz"
  link_latest "${stage1_return_tar}.sha256" "$RETURNS_DIR/stage1_emx_calibration_wideband_latest.tar.gz.sha256"
  link_latest "$stage1_manifest" "$RETURNS_DIR/stage1_emx_calibration_wideband_latest_manifest.json"

  echo "STAGE1_CALIBRATION_DONE $(date) return_code=$stage1_rc latest=$RETURNS_DIR/stage1_emx_calibration_wideband_latest.tar.gz"
  if [[ "$stage1_rc" -ne 0 && "${STAGE1_FAILS_MAIN:-0}" == "1" ]]; then
    exit "$stage1_rc"
  fi
}

"$PYTHON" - <<'PY'
from pathlib import Path

repo = Path.cwd()

parallel = repo / "scripts" / "run_candidate_queue_dataset_parallel.py"
text = parallel.read_text(encoding="utf-8")
text = text.replace("import math\n", "")
old_split = '''def _split_rows(rows: list[dict[str, str]], jobs: int) -> list[list[dict[str, str]]]:
    if not rows:
        return []
    jobs = max(1, int(jobs))
    chunk_size = int(math.ceil(len(rows) / float(jobs)))
    return [rows[start : start + chunk_size] for start in range(0, len(rows), chunk_size)]
'''
new_split = '''def _split_rows(rows: list[dict[str, str]], jobs: int) -> list[list[dict[str, str]]]:
    if not rows:
        return []
    shard_count = min(max(1, int(jobs)), len(rows))
    base_size, extra_rows = divmod(len(rows), shard_count)
    shards: list[list[dict[str, str]]] = []
    start = 0
    for index in range(shard_count):
        stop = start + base_size + (1 if index < extra_rows else 0)
        shards.append(rows[start:stop])
        start = stop
    return shards
'''
if old_split in text:
    text = text.replace(old_split, new_split)
elif "shard_count = min(max(1, int(jobs)), len(rows))" not in text:
    raise SystemExit("Cannot patch _split_rows: expected old or new block not found")
parallel.write_text(text, encoding="utf-8")

builder = repo / "scripts" / "build_physical_feature_s8p_launch_packet.py"
text = builder.read_text(encoding="utf-8")
if 'from rfic_transformer_inverse_design.paths import bundled_proc_dir, resolve_local_path' not in text:
    text = text.replace(
        'from rfic_transformer_inverse_design.core import load_run_config  # noqa: E402\n',
        'from rfic_transformer_inverse_design.core import load_run_config  # noqa: E402\n'
        'from rfic_transformer_inverse_design.paths import bundled_proc_dir, resolve_local_path  # noqa: E402\n',
    )
if 'REPO_ROOT = Path(__file__).resolve().parents[1]' not in text:
    text = text.replace(
        'DEFAULT_FEATURE_COLUMNS = "lp_nh_center,ls_nh_center,q_center,k_center"\n',
        'DEFAULT_FEATURE_COLUMNS = "lp_nh_center,ls_nh_center,q_center,k_center"\n'
        'REPO_ROOT = Path(__file__).resolve().parents[1]\n',
    )
text = text.replace(
    'repo_root = Path(__file__).resolve().parents[1]',
    'repo_root = REPO_ROOT',
)
if 'proc_path = resolve_local_path(cfg.emx.emx_process_file' not in text:
    anchor = '''    try:
        cfg = load_run_config(path)
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"{type(exc).__name__}: {exc}"
'''
    insert = anchor + '''    proc_path = resolve_local_path(cfg.emx.emx_process_file, extra_roots=(REPO_ROOT, bundled_proc_dir(), path.parent))
    if not proc_path.is_file():
        return "FAIL", f"emx_process_file does not resolve to an existing .proc file: {cfg.emx.emx_process_file} -> {proc_path}"
'''
    text = text.replace(anchor, insert)
else:
    text = text.replace(
        'resolve_local_path(cfg.emx.emx_process_file, extra_roots=(bundled_proc_dir(), path.parent))',
        'resolve_local_path(cfg.emx.emx_process_file, extra_roots=(REPO_ROOT, bundled_proc_dir(), path.parent))',
    )
builder.write_text(text, encoding="utf-8")

proc_dir = repo / "rfic_transformer_inverse_design" / "process" / "assets" / "proc"
proc_path = proc_dir / "default_typical.proc"
if not proc_path.exists():
    raise SystemExit(f"Missing bundled EMX proc; do not generate data until this exists: {proc_path}")

configs = list(repo.glob("outputs/s8p_port_order_from_the_best_20260619/final_s8p_physical_feature_500_the_best_candidate.yaml"))
configs += list(repo.glob("next_gen_s8p_evidence_20260619/outputs/s8p_port_order_from_the_best_20260619/final_s8p_physical_feature_500_the_best_candidate.yaml"))
if not configs:
    configs += list(repo.glob("**/final_s8p_physical_feature_500_the_best_candidate.yaml"))
seen = set()
for cfg in configs:
    if cfg in seen:
        continue
    seen.add(cfg)
    raw = cfg.read_text(encoding="utf-8")
    replacements = {
        "frequency_stop_hz: 50000000000.0": "frequency_stop_hz: 60000000000.0",
        "frequency_step_hz: 100000000.0": "frequency_step_hz: 500000000.0",
        "band_points: 451": "band_points: 111",
    }
    for old, new in replacements.items():
        raw = raw.replace(old, new)
    import re
    raw = re.sub(
        r"emx_process_file: .+",
        "emx_process_file: rfic_transformer_inverse_design/process/assets/proc/default_typical.proc",
        raw,
    )
    cfg.write_text(raw, encoding="utf-8")
    print(f"patched_config={cfg}")

print("PATCHED_REMOTE_S8P_PIPELINE=1")
PY

"$PYTHON" -m py_compile \
  scripts/run_candidate_queue_dataset_parallel.py \
  scripts/build_physical_feature_s8p_launch_packet.py

run_stage1_calibration_if_available

CONFIG="outputs/s8p_port_order_from_the_best_20260619/final_s8p_physical_feature_500_the_best_candidate.yaml"
if [[ ! -f "$CONFIG" ]]; then
  CONFIG="$(find . -path '*final_s8p_physical_feature_500_the_best_candidate.yaml' | head -n 1)"
fi
PORT_APPROVAL="outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/s8p_port_map_approval_summary.json"
GEOM_APPROVAL="outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_user_approved_20260619/s8p_geometry_contract_approval_summary.json"
if [[ ! -f "$PORT_APPROVAL" ]]; then
  PORT_APPROVAL="$(find . -path '*s8p_port_map_approval_summary.json' | head -n 1)"
fi
if [[ ! -f "$GEOM_APPROVAL" ]]; then
  GEOM_APPROVAL="$(find . -path '*s8p_geometry_contract_approval_summary.json' | head -n 1)"
fi

OUT_DIR="next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/physical_feature_s8p_launch_packet_20_after_unlock_20260626"
RUN_DIR="next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/new_s8p_physical_feature_emx_20_after_unlock_20260626"
rm -rf "$OUT_DIR" "$RUN_DIR"

"$PYTHON" scripts/build_physical_feature_s8p_launch_packet.py \
  --bootstrap-geometry-candidate-queue \
  --config "$CONFIG" \
  --out-dir "$OUT_DIR" \
  --run-dir "$RUN_DIR" \
  --inverse-candidate-count 20 \
  --emx-max-count 20 \
  --expected-emx-count 20 \
  --batch-size 1 \
  --jobs 8 \
  --expected-jobs 8 \
  --port-map-approval-summary "$PORT_APPROVAL" \
  --geometry-contract-approval-summary "$GEOM_APPROVAL" \
  --no-package

bash "$OUT_DIR/physical_feature_s8p_launch.commands.sh" 2>&1 | tee "$RUN_DIR/launch_20_after_unlock.log"

"$PYTHON" - <<'PY'
import csv, json
from pathlib import Path

out = Path("next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/new_s8p_physical_feature_emx_20_after_unlock_20260626")
summary = out / "parallel_candidate_queue_dataset_summary.json"
rows_csv = out / "dataset_rows.csv"
print("POSTRUN_DIR", out)
if summary.exists():
    s = json.loads(summary.read_text(encoding="utf-8"))
    keys = ["overall_status", "decision", "input_row_count", "expected_count", "jobs_requested", "expected_jobs", "shard_count", "merged_row_count", "pass_shard_count", "fail_shard_count"]
    print("SUMMARY", {k: s.get(k) for k in keys})
    for check in s.get("checks", []):
        if not check.get("pass"):
            print("FAILED_CHECK", check.get("name"), check.get("detail"))
if rows_csv.exists():
    rows = list(csv.DictReader(rows_csv.open(newline="", encoding="utf-8-sig")))
    print("ROWS", len(rows))
print("S8P_COUNT", len(list(out.rglob("*.s8p"))))
PY

"$PYTHON" scripts/discover_final_valid_emx_s8p_candidates.py \
  --search-root "$RUN_DIR" \
  --out-dir "$RUN_DIR/final_valid_emx_s8p_discovery" \
  --no-fail-exit

mkdir -p "$RETURNS_DIR"

PACKAGE_TAR="$RETURNS_DIR/next_gen_s8p_mars_return_20_after_unlock_${STAMP}.tar.gz"
PACKAGE_INVENTORY="${PACKAGE_TAR}.inventory.json"
PACKAGE_REPORT="${PACKAGE_TAR}.inventory.md"
PACKAGE_VERIFY_DIR="$RETURNS_DIR/next_gen_s8p_mars_return_20_after_unlock_verify_${STAMP}"
PACKAGE_VERIFY_LOG="$RETURNS_DIR/next_gen_s8p_mars_return_20_after_unlock_verify_${STAMP}.log"
LATEST_TRANSFER_TARBALL="$RETURNS_DIR/next_gen_s8p_mars_return_latest.tar.gz"
LATEST_TRANSFER_SHA="$RETURNS_DIR/next_gen_s8p_mars_return_latest.tar.gz.sha256"
LATEST_TRANSFER_INVENTORY="$RETURNS_DIR/next_gen_s8p_mars_return_latest.tar.gz.inventory.json"
LATEST_TRANSFER_REPORT="$RETURNS_DIR/next_gen_s8p_mars_return_latest.tar.gz.inventory.md"
LATEST_TRANSFER_VERIFY_LOG="$RETURNS_DIR/next_gen_s8p_mars_return_latest_verify.log"
LATEST_TRANSFER_VERIFY_SUMMARY="$RETURNS_DIR/next_gen_s8p_mars_return_latest_verify_summary.json"

echo "RETURN_PACKAGE_START $(date) tar=$PACKAGE_TAR"
(
  cd "$REPO_ROOT"
  "$PYTHON" scripts/package_mars_dataset_run.py "$RUN_DIR" \
    --out "$PACKAGE_TAR" \
    --inventory "$PACKAGE_INVENTORY" \
    --report "$PACKAGE_REPORT" \
    --include-layout-previews \
    --include-quality-figures \
    --include-hfss-validation-assets
  "$PYTHON" scripts/verify_mars_dataset_package.py "$PACKAGE_TAR" \
    --inventory "$PACKAGE_INVENTORY" \
    --inventory-report "$PACKAGE_REPORT" \
    --sha256-file "${PACKAGE_TAR}.sha256" \
    --out-dir "$PACKAGE_VERIFY_DIR" \
    --run-progress-audit \
    --expected-count 20 \
    --expected-touchstone-ports 8 \
    --required-touchstone-extension .s8p \
    --expected-frequency-start-ghz 5 \
    --expected-frequency-stop-ghz 60 \
    --expected-frequency-step-ghz 0.5 \
    --expected-frequency-points 111 \
    --require-emx-command \
    --expected-port-mode single_ended_shield_grounded \
    --expected-pin-purpose 51 \
    --require-s8p-quality-gates \
    --require-next-gen-s8p-status \
    --require-run-config \
    --no-fail-exit
) > "$PACKAGE_VERIFY_LOG" 2>&1

link_latest "$PACKAGE_TAR" "$LATEST_TRANSFER_TARBALL"
link_latest "${PACKAGE_TAR}.sha256" "$LATEST_TRANSFER_SHA"
link_latest "$PACKAGE_INVENTORY" "$LATEST_TRANSFER_INVENTORY"
link_latest "$PACKAGE_REPORT" "$LATEST_TRANSFER_REPORT"
link_latest "$PACKAGE_VERIFY_LOG" "$LATEST_TRANSFER_VERIFY_LOG"
if [[ -f "$PACKAGE_VERIFY_DIR/mars_dataset_package_verify_summary.json" ]]; then
  link_latest "$PACKAGE_VERIFY_DIR/mars_dataset_package_verify_summary.json" "$LATEST_TRANSFER_VERIFY_SUMMARY"
fi

echo "RETURN_PACKAGE_DONE $(date) tar=$PACKAGE_TAR verify_log=$PACKAGE_VERIFY_LOG"
echo "RETURN_PACKAGE_LATEST_TARBALL=$LATEST_TRANSFER_TARBALL"
echo "RETURN_PACKAGE_LATEST_VERIFY_SUMMARY=$LATEST_TRANSFER_VERIFY_SUMMARY"
