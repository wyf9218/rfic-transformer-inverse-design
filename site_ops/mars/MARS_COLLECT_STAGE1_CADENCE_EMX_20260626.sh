#!/usr/bin/env bash
set -euo pipefail

# Run this on mars-0002 after the Cadence OA roundtrip Stage-1 EMX calibration.
# It collects the known-good Cadence-pin EMX .s2p files into a local return tar
# that can be compared on the Mac with:
#   outputs/mars_stage1_emx_calibration_min_20260626/COMPARE_RETURNED_STAGE1_EMX_CALIBRATION.sh <tar>

RUN_NAME="emx_hfss_calibration_20260626_cadence"
OUT_DIR="${OUT_DIR:-$PWD/stage1_emx_cadence_return_20260626}"

find_repo_root() {
  for candidate in \
    "$PWD" \
    "$PWD/rfic-transformer-inverse-design" \
    "$HOME/researcher/transformer_inverse/rfic-transformer-inverse-design" \
    "$HOME/transformer_inverse/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/transformer_inverse/rfic-transformer-inverse-design"; do
    if [[ -d "$candidate/runs" || -f "$candidate/pyproject.toml" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

find_stage1_file() {
  local name="$1"
  local repo_root="${2:-}"
  local candidate
  for candidate in \
    "$PWD/runs/$RUN_NAME/$name/emx/${name}_cadence.s2p" \
    "$PWD/$RUN_NAME/$name/emx/${name}_cadence.s2p" \
    "${repo_root:+$repo_root/runs/$RUN_NAME/$name/emx/${name}_cadence.s2p}" \
    "${repo_root:+$repo_root/$RUN_NAME/$name/emx/${name}_cadence.s2p}" \
    "/shared/research/${USER:-researcher}/$RUN_NAME/$name/emx/${name}_cadence.s2p"; do
    if [[ -n "$candidate" && -s "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  candidate="$(find "$PWD" "/shared/research/${USER:-researcher}" "$HOME" \
    -type f -path "*/$RUN_NAME/$name/emx/${name}_cadence.s2p" 2>/dev/null | sort | head -n 1 || true)"
  if [[ -n "$candidate" && -s "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  return 1
}

REPO_ROOT="$(find_repo_root || true)"
mkdir -p "$OUT_DIR/emx/m9_straight_line" "$OUT_DIR/emx/m10_straight_line" "$OUT_DIR/logs"

M9="$(find_stage1_file m9_straight_line "$REPO_ROOT")"
M10="$(find_stage1_file m10_straight_line "$REPO_ROOT")"

cp "$M9" "$OUT_DIR/emx/m9_straight_line/m9_straight_line_cadence.s2p"
cp "$M10" "$OUT_DIR/emx/m10_straight_line/m10_straight_line_cadence.s2p"

cat > "$OUT_DIR/stage1_emx_calibration_manifest.json" <<JSON
{
  "schema": "stage1_emx_calibration_results.v2",
  "source": "cadence_oa_roundtrip",
  "repo_root": "$REPO_ROOT",
  "source_files": {
    "m9_straight_line": "$M9",
    "m10_straight_line": "$M10"
  },
  "outputs": {
    "m9_straight_line": "$OUT_DIR/emx/m9_straight_line/m9_straight_line_cadence.s2p",
    "m10_straight_line": "$OUT_DIR/emx/m10_straight_line/m10_straight_line_cadence.s2p"
  }
}
JSON

tar -czf "$OUT_DIR/stage1_emx_cadence_return_20260626.tar.gz" \
  -C "$OUT_DIR" \
  stage1_emx_calibration_manifest.json emx logs

sha256sum "$OUT_DIR/stage1_emx_cadence_return_20260626.tar.gz" \
  > "$OUT_DIR/stage1_emx_cadence_return_20260626.tar.gz.sha256"

echo "STAGE1_CADENCE_RETURN_READY=1"
echo "OUT_DIR=$OUT_DIR"
echo "RESULT_TAR=$OUT_DIR/stage1_emx_cadence_return_20260626.tar.gz"
echo "RESULT_SHA=$OUT_DIR/stage1_emx_cadence_return_20260626.tar.gz.sha256"
