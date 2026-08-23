#!/usr/bin/env bash
set -euo pipefail

# Regression check for physical-feature uniformity K handling.
#
# The production uniformity gate uses |K| for coupling-strength coverage, but
# the summary must retain signed-K diagnostics so a negative/signed extraction
# issue cannot be hidden by magnitude-only plots.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
SCRIPT="$ROOT_DIR/rfic-transformer-inverse-design/scripts/audit_physical_feature_uniformity.py"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

CSV="$TMP_DIR/k_sign_input.csv"
OUT="$TMP_DIR/out"

python3 - "$CSV" <<'PY'
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
rows = [
    {"input__lp_nh_center": "1.0", "input__ls_nh_center": "1.1", "input__q_center": "10", "input__k_center": "-0.20"},
    {"input__lp_nh_center": "1.2", "input__ls_nh_center": "1.3", "input__q_center": "12", "input__k_center": "-0.10"},
    {"input__lp_nh_center": "1.4", "input__ls_nh_center": "1.5", "input__q_center": "14", "input__k_center": "0.00"},
    {"input__lp_nh_center": "1.6", "input__ls_nh_center": "1.7", "input__q_center": "16", "input__k_center": "0.30"},
]
with path.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
PY

python3 "$SCRIPT" \
  --training-csv "$CSV" \
  --out-dir "$OUT" \
  --min-valid-count 4 \
  --lp-min-nh 0.5 --lp-max-nh 3 \
  --ls-min-nh 0.5 --ls-max-nh 3 \
  --q-min 5 --q-max 25 \
  --k-min 0 --k-max 0.8 \
  --k-mode magnitude \
  --require-explicit-ranges \
  --no-plots \
  --no-fail-exit >/tmp/k_sign_diagnostic_audit.log

python3 - "$OUT/physical_feature_uniformity_summary.json" "$OUT/physical_feature_uniformity_report.md" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text())
report = Path(sys.argv[2]).read_text()
diag = summary.get("k_sign_diagnostics")
assert isinstance(diag, dict), "missing k_sign_diagnostics"
assert diag.get("k_mode") == "magnitude", diag
assert diag.get("uniformity_k_axis") == "|K|", diag
assert diag.get("signed_k_count") == 4, diag
assert diag.get("negative_k_count") == 2, diag
assert diag.get("zero_k_count") == 1, diag
assert diag.get("positive_k_count") == 1, diag
assert abs(diag.get("min_signed_k") - (-0.2)) < 1e-12, diag
assert abs(diag.get("max_signed_k") - 0.3) < 1e-12, diag
assert abs(diag.get("max_abs_k") - 0.3) < 1e-12, diag
assert "Uniformity K axis" in report, "report missing K diagnostics"
assert "|K|" in report, "report missing magnitude axis"
print("K_SIGN_DIAGNOSTIC_BEHAVIOR_STATUS=PASS")
PY
