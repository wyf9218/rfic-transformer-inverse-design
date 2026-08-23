#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   bash IMPORT_STAGE1_CADENCE_RETURN_AND_COMPARE_20260626.sh /path/to/stage1_emx_cadence_return_20260626.tar.gz
#
# Purpose:
#   Import Cadence-roundtrip EMX Stage-1 .s2p files and compare them against
#   all locally available HFSS straight-line calibration variants.

RETURN_TAR="${1:-}"
if [[ -z "$RETURN_TAR" || ! -f "$RETURN_TAR" ]]; then
  echo "Usage: $0 /path/to/stage1_emx_cadence_return_20260626.tar.gz" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$ROOT_DIR/rfic-transformer-inverse-design}"
PYTHON="${PYTHON:-/home/researcher/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="${PYTHON:-python3}"
fi

OUT_ROOT="${OUT_ROOT:-$ROOT_DIR/reports/s8p_shared_line_width_mars_evidence_20260622/stage1_cadence_return_compare_$(date +%Y%m%d_%H%M%S)}"
UNPACKED="$OUT_ROOT/unpacked"
mkdir -p "$UNPACKED"
tar -xzf "$RETURN_TAR" -C "$UNPACKED"

compare_one() {
  local label="$1"
  local hfss_root="$2"
  local packet_dir="$3"
  local out_dir="$OUT_ROOT/$label"
  if [[ ! -d "$hfss_root" ]]; then
    echo "SKIP $label: missing HFSS root $hfss_root"
    return 0
  fi
  if [[ ! -d "$packet_dir" || ! -f "$packet_dir/calibration_execution_summary.json" ]]; then
    echo "SKIP $label: missing packet summary $packet_dir/calibration_execution_summary.json"
    return 0
  fi
  "$PYTHON" "$REPO_ROOT/scripts/summarize_stage1_calibration_results.py" \
    --packet-dir "$packet_dir" \
    --emx-results-root "$UNPACKED" \
    --hfss-results-root "$hfss_root" \
    --out-dir "$out_dir" \
    --target-ghz 15 \
    --max-percent-error 10 \
    --require-matching-frequency-grid \
    --no-fail-exit
}

compare_one \
  "baseline_variants" \
  "/home/researcher/Documents/hfss_calibration_stage1_20260626/windows_results" \
  "/home/researcher/Documents/hfss_calibration_stage1_20260626"

compare_one \
  "m5_united_variant" \
  "/home/researcher/Documents/hfss_calibration_stage1_m5_united_20260626/windows_results" \
  "/home/researcher/Documents/hfss_calibration_stage1_m5_united_20260626"

cat > "$OUT_ROOT/README_CN.md" <<EOF
# Stage 1 Cadence EMX Return Compare

- Return tar: $RETURN_TAR
- Unpacked EMX root: $UNPACKED
- Baseline compare: $OUT_ROOT/baseline_variants
- M5-united compare: $OUT_ROOT/m5_united_variant

判读：

- 如果 M5-united straight-line 比 baseline 明显更接近 EMX，说明 HFSS 的 M5/local-ground reference 是完整 8-port 差距的关键根因。
- 如果所有 straight-line 都失败，则先修 HFSS 金属/介质/端口参考环境，不进入百万级 EMX 数据生成。
- 如果 straight-line 通过，再回到完整新结构 .s8p 样本做 HFSS 验证。
EOF

echo "OUT_ROOT=$OUT_ROOT"
find "$OUT_ROOT" -maxdepth 3 -type f \( -name 'stage1_calibration_summary.md' -o -name 'stage1_calibration_summary.json' -o -name 'README_CN.md' \) -print
