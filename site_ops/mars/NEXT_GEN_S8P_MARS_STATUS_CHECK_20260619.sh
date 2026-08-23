#!/usr/bin/env bash
set -euo pipefail

# Read-only status check for the next-gen S8P MARS recovery/EMX run.
# Run on MARS. It never starts EMX and never modifies generated data.

WORK_DIR="${WORK_DIR:-/shared/research/researcher/codex_next_gen_s8p_ssh_20260620}"
PROJECT_DIR="${PROJECT_DIR:-}"
RUN_SUBDIR="${RUN_SUBDIR:-next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/new_s8p_physical_feature_emx_500}"
EXPECTED_SYNC_SHA="${EXPECTED_SYNC_SHA:-}"
SYNC_PACKET_NAME="${SYNC_PACKET_NAME:-next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix.tar.gz}"
LEGACY_SYNC_PACKET_NAME="${LEGACY_SYNC_PACKET_NAME:-next_gen_s8p_mars_sync_packet_20260619.tar.gz}"
CADENCE_INSTALL_ROOT="${CADENCE_INSTALL_ROOT:-/cae/apps/data/cadence-2025/installs/IC231}"

echo "== Codex next-gen S8P MARS status check =="
date
echo "WORK_DIR=${WORK_DIR}"

if [[ -d "${WORK_DIR}" ]]; then
  echo
  echo "== Work directory =="
  ls -lh "${WORK_DIR}" || true
  echo
  echo "== Logs =="
  ls -lh "${WORK_DIR}/logs" 2>/dev/null || true
  echo
  echo "== latest_status.txt =="
  cat "${WORK_DIR}/logs/latest_status.txt" 2>/dev/null || true
  echo
	  echo "== latest_status.json =="
	  cat "${WORK_DIR}/logs/latest_status.json" 2>/dev/null || true
	  echo
	  echo "== TSMC65 runner status =="
	  cat "${WORK_DIR}/logs/tsmc65_latest_status.txt" 2>/dev/null || true
	  echo
	  cat "${WORK_DIR}/logs/tsmc65_latest_status.json" 2>/dev/null || true
	  echo
	  echo "== TSMC65 latest pid/log =="
	  if [[ -s "${WORK_DIR}/logs/tsmc65_real_emx_500_latest.pid" ]]; then
	    LATEST_PID="$(cat "${WORK_DIR}/logs/tsmc65_real_emx_500_latest.pid" 2>/dev/null || true)"
	    echo "LATEST_PID=${LATEST_PID}"
	    if [[ -n "${LATEST_PID}" ]] && kill -0 "${LATEST_PID}" >/dev/null 2>&1; then
	      echo "LATEST_PID_STATUS=RUNNING"
	    else
	      echo "LATEST_PID_STATUS=NOT_RUNNING"
	    fi
	  fi
	  if [[ -e "${WORK_DIR}/logs/tsmc65_real_emx_500_latest.log" ]]; then
	    echo "LATEST_REAL_LOG=${WORK_DIR}/logs/tsmc65_real_emx_500_latest.log"
	    tail -n 80 "${WORK_DIR}/logs/tsmc65_real_emx_500_latest.log" 2>/dev/null || true
	  fi
	  if [[ -e "${WORK_DIR}/logs/tsmc65_real_emx_500_latest_monitor.log" ]]; then
	    echo
	    echo "LATEST_MONITOR_LOG=${WORK_DIR}/logs/tsmc65_real_emx_500_latest_monitor.log"
	    tail -n 40 "${WORK_DIR}/logs/tsmc65_real_emx_500_latest_monitor.log" 2>/dev/null || true
	  fi
	  if [[ -e "${WORK_DIR}/logs/tsmc65_real_emx_500_latest_progress.csv" ]]; then
	    echo
	    echo "LATEST_PROGRESS_CSV=${WORK_DIR}/logs/tsmc65_real_emx_500_latest_progress.csv"
	    tail -n 20 "${WORK_DIR}/logs/tsmc65_real_emx_500_latest_progress.csv" 2>/dev/null || true
	  fi
	  if [[ -e "${WORK_DIR}/logs/tsmc65_real_emx_500_latest_status_check.log" ]]; then
	    echo
	    echo "LATEST_FINAL_STATUS_CHECK_LOG=${WORK_DIR}/logs/tsmc65_real_emx_500_latest_status_check.log"
	    tail -n 160 "${WORK_DIR}/logs/tsmc65_real_emx_500_latest_status_check.log" 2>/dev/null || true
	  fi
	  if [[ -d "${WORK_DIR}/returns" ]]; then
	    echo
	    echo "== Latest MARS return package =="
	    ls -lh "${WORK_DIR}/returns" 2>/dev/null || true
	    for artifact in \
	      "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest.tar.gz" \
	      "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest.tar.gz.sha256" \
	      "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest.tar.gz.inventory.json" \
	      "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest.tar.gz.inventory.md" \
	      "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest_verify.log" \
	      "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest_verify_summary.json"; do
	      if [[ -e "${artifact}" ]]; then
	        echo "RETURN_ARTIFACT=${artifact}"
	        ls -lh "${artifact}" 2>/dev/null || true
	      fi
	    done
	    if [[ -s "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest.tar.gz.sha256" ]]; then
	      (cd "${WORK_DIR}/returns" && sha256sum -c next_gen_s8p_mars_return_latest.tar.gz.sha256) || true
	    fi
	    if [[ -s "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest_verify_summary.json" ]]; then
	      python3 - "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest_verify_summary.json" <<'PY' 2>/dev/null || cat "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest_verify_summary.json"
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(f"return_package_verify_status={data.get('overall_status')}")
print(f"return_package_inventory_counts={data.get('inventory_category_counts')}")
for item in data.get("checks", []):
    if item.get("name") in {
        "packaged S8P physical-feature quality gates",
        "packaged next-gen S8P status evidence",
        "extracted run progress audit",
    }:
        print(f"return_package_check[{item.get('name')}]={item.get('status')} {item.get('detail')}")
PY
	    fi
	    if [[ -e "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest_verify.log" ]]; then
	      echo
	      echo "LATEST_RETURN_PACKAGE_VERIFY_LOG=${WORK_DIR}/returns/next_gen_s8p_mars_return_latest_verify.log"
	      tail -n 80 "${WORK_DIR}/returns/next_gen_s8p_mars_return_latest_verify.log" 2>/dev/null || true
	    fi
	  fi
	  echo
	  echo "== sync packet SHA =="
  if [[ -f "${WORK_DIR}/${SYNC_PACKET_NAME}" ]]; then
    sha256sum "${WORK_DIR}/${SYNC_PACKET_NAME}" || true
    wc -c "${WORK_DIR}/${SYNC_PACKET_NAME}" || true
    if [[ -n "${EXPECTED_SYNC_SHA}" ]]; then
      printf '%s  %s\n' "${EXPECTED_SYNC_SHA}" "${WORK_DIR}/${SYNC_PACKET_NAME}" | sha256sum -c - || true
    elif [[ -s "${WORK_DIR}/${SYNC_PACKET_NAME}.sha256" ]]; then
      (cd "${WORK_DIR}" && sha256sum -c "${SYNC_PACKET_NAME}.sha256") || true
    else
      echo "WARNING: no expected SHA sidecar found; transfer ${SYNC_PACKET_NAME}.sha256"
    fi
  elif [[ -f "${WORK_DIR}/${LEGACY_SYNC_PACKET_NAME}" ]]; then
    echo "WARNING: legacy sync packet found; current preferred packet is ${SYNC_PACKET_NAME}"
    sha256sum "${WORK_DIR}/${LEGACY_SYNC_PACKET_NAME}" || true
    wc -c "${WORK_DIR}/${LEGACY_SYNC_PACKET_NAME}" || true
  elif [[ -f "${WORK_DIR}/next_gen_s8p_mars_ultra_sync_packet_20260619.tar.gz" ]]; then
    echo "WARNING: old ultra sync packet found; current preferred packet is ${SYNC_PACKET_NAME}"
    sha256sum "${WORK_DIR}/next_gen_s8p_mars_ultra_sync_packet_20260619.tar.gz" || true
    wc -c "${WORK_DIR}/next_gen_s8p_mars_ultra_sync_packet_20260619.tar.gz" || true
  fi
fi

if [[ -z "${PROJECT_DIR}" ]]; then
  PROJECT_DIR="$(find /shared/research/researcher -maxdepth 6 -type d -name rfic-transformer-inverse-design 2>/dev/null | head -n 1 || true)"
fi

echo
echo "PROJECT_DIR=${PROJECT_DIR}"
if [[ -n "${PROJECT_DIR}" && -d "${PROJECT_DIR}" ]]; then
  cd "${PROJECT_DIR}"
  echo
  echo "== Git/project hints =="
  pwd
  test -f pyproject.toml && echo "pyproject.toml: present" || true
  test -d rfic_transformer_inverse_design && echo "package dir: present" || true
  test -x NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh && echo "TSMC65 runner: present/executable" || echo "TSMC65 runner: missing"

  echo
  echo "== Cadence IC231 tool gate =="
  echo "CADENCE_INSTALL_ROOT=${CADENCE_INSTALL_ROOT}"
  for tool in dbAccess strmin strmout virtuoso; do
    if [[ -x "${CADENCE_INSTALL_ROOT}/bin/${tool}" ]]; then
      echo "PASS ${CADENCE_INSTALL_ROOT}/bin/${tool}"
    else
      echo "FAIL ${CADENCE_INSTALL_ROOT}/bin/${tool}"
    fi
  done

  RUN_DIR="${PROJECT_DIR}/${RUN_SUBDIR}"
  QUALITY_DIR="${RUN_DIR}/dataset_quality_gates_s8p_physical_feature"
  RUN_STATUS_SUMMARY="${RUN_DIR}/next_gen_s8p_mars_run_status/next_gen_s8p_mars_run_status_summary.json"
  echo
  echo "RUN_DIR=${RUN_DIR}"
  if [[ -d "${RUN_DIR}" ]]; then
    echo
    echo "== Dataset run files =="
    find "${RUN_DIR}" -maxdepth 2 -type f | sed -n '1,80p'
    echo
    echo "== Touchstone counts =="
    find "${RUN_DIR}" -type f \( -name '*.s8p' -o -name '*.S8P' \) | wc -l
    find "${RUN_DIR}" -type f \( -name '*.s8p' -o -name '*.S8P' \) | sed -n '1,10p'
    echo
    echo "== Dataset rows =="
    if [[ -f "${RUN_DIR}/dataset_rows.csv" ]]; then
      wc -l "${RUN_DIR}/dataset_rows.csv"
      tail -n 5 "${RUN_DIR}/dataset_rows.csv"
    fi

    echo
    echo "== Run-status manifest contract gate =="
    if [[ -f "${RUN_STATUS_SUMMARY}" ]]; then
      echo "SUMMARY=${RUN_STATUS_SUMMARY}"
      python3 - "${RUN_STATUS_SUMMARY}" <<'PY' 2>/dev/null || cat "${RUN_STATUS_SUMMARY}"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
for key in ("overall_status", "decision", "status_counts"):
    if key in data:
        print(f"{key}={data[key]}")
for item in data.get("evidence", []):
    if item.get("requirement") == "dataset manifest matches approved S8P topology contract":
        print(f"manifest_contract_status={item.get('status')}")
        print(f"manifest_contract_evidence={item.get('evidence')}")
        print(f"manifest_contract_next={item.get('next_action')}")
        break
else:
    print("manifest_contract_status=MISSING_IN_RUN_STATUS")
for item in data.get("evidence", []):
    if item.get("requirement") == "all successful rows are traceable to EMX-generated .s8p files":
        print(f"emx_source_gate_status={item.get('status')}")
        print(f"emx_source_gate_evidence={item.get('evidence')}")
        print(f"emx_source_gate_next={item.get('next_action')}")
        break
else:
    print("emx_source_gate_status=MISSING_IN_RUN_STATUS")
summary = data.get("dataset_manifest") or {}
if summary:
    print(f"manifest_summary={summary}")
PY
    else
      echo "MISSING=${RUN_STATUS_SUMMARY}"
      echo "This is expected before summarize_next_gen_s8p_mars_run.py has run; after EMX completion it must show the approved S8P manifest contract gate."
    fi
  fi

  if [[ -d "${QUALITY_DIR}" ]]; then
    echo
    echo "== Quality summaries =="
    find "${QUALITY_DIR}" -maxdepth 3 -type f \( -name '*summary.json' -o -name '*report.md' \) | sed -n '1,120p'

    HFSS_HANDOFF_SUMMARY="${QUALITY_DIR}/selected_s8p_hfss_handoff/selected_s8p_hfss_handoff_summary.json"
    AEDT_PACKET_SUMMARY="${QUALITY_DIR}/selected_s8p_hfss_aedt_scripts/hfss_s8p_aedt_script_packet_summary.json"
    HFSS_PAYLOAD_RENDER_SUMMARY="${QUALITY_DIR}/selected_s8p_hfss_payload_views/hfss_payload_geometry_render_batch_summary.json"
    POSTRUN_VALIDATION_SUMMARY="${QUALITY_DIR}/selected_s8p_hfss_postrun_validation/s8p_hfss_postrun_validation_summary.json"
    OBJECTIVE_ACCEPTANCE_SUMMARY="${QUALITY_DIR}/next_gen_s8p_objective_acceptance/next_gen_s8p_objective_acceptance_summary.json"

    echo
    echo "== HFSS validation chain summaries =="
    for summary in "${HFSS_HANDOFF_SUMMARY}" "${AEDT_PACKET_SUMMARY}" "${HFSS_PAYLOAD_RENDER_SUMMARY}" "${POSTRUN_VALIDATION_SUMMARY}"; do
      if [[ -f "${summary}" ]]; then
        echo "SUMMARY=${summary}"
        python3 - "${summary}" <<'PY' 2>/dev/null || cat "${summary}"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
fields = [
    "overall_status",
    "decision",
    "selected_count",
    "pass_count",
    "fail_count",
    "sample_count",
    "status_counts",
]
for key in fields:
    if key in data:
        print(f"{key}={data[key]}")
PY
      else
        echo "MISSING=${summary}"
      fi
    done

    echo
    echo "== HFSS AEDT handoff files =="
    find "${QUALITY_DIR}/selected_s8p_hfss_aedt_scripts" -type f \( \
      -name 'run_generated_hfss_s8p_scripts.commands.ps1' -o \
      -name 'hfss_s8p_build_payload.json' -o \
      -name 'build_hfss_s8p_from_payload.py' -o \
      -name 'solve_export_hfss_s8p.py' -o \
      -name 'source_geometry.gds' \
    \) 2>/dev/null | sed -n '1,120p'

    echo
    echo "== HFSS payload geometry views =="
    find "${QUALITY_DIR}/selected_s8p_hfss_payload_views" -type f \( \
      -name '*.png' -o \
      -name 'hfss_payload_geometry_render_summary.json' \
    \) 2>/dev/null | sed -n '1,120p'

    echo
    echo "== HFSS exported S8P / validation outputs =="
    find "${QUALITY_DIR}" -path '*selected_s8p_hfss*' -type f \( \
      -name '*.s8p' -o \
      -name '*.S8P' -o \
      -name 's8p_hfss_postrun_validation_results.csv' -o \
      -name 'emx_hfss_ads_comparison_summary.json' -o \
      -name 'ads_style_metric_plot_summary.json' -o \
      -name '*.png' \
    \) 2>/dev/null | sed -n '1,160p'

    echo
    echo "== Objective acceptance audit =="
    if [[ -f "${OBJECTIVE_ACCEPTANCE_SUMMARY}" ]]; then
      echo "SUMMARY=${OBJECTIVE_ACCEPTANCE_SUMMARY}"
      python3 - "${OBJECTIVE_ACCEPTANCE_SUMMARY}" <<'PY' 2>/dev/null || cat "${OBJECTIVE_ACCEPTANCE_SUMMARY}"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
for key in ("overall_status", "decision", "final_objective_ready", "objective_statuses", "status_counts"):
    if key in data:
        print(f"{key}={data[key]}")
waiting = [
    item
    for item in data.get("evidence", [])
    if str(item.get("status", "")).upper() in {"WAITING", "QUESTION", "FAIL"}
]
if waiting:
    print("open_requirements:")
    for item in waiting[:20]:
        objective = item.get("objective_id", "?")
        status = item.get("status", "?")
        requirement = item.get("requirement", "")
        next_action = item.get("next_action", "")
        print(f"- objective={objective} status={status} requirement={requirement} next={next_action}")
PY
    else
      echo "MISSING=${OBJECTIVE_ACCEPTANCE_SUMMARY}"
      echo "Run the launch packet through summarize_next_gen_s8p_mars_run.py and build_next_gen_s8p_objective_acceptance_audit.py."
    fi
    find "${QUALITY_DIR}/next_gen_s8p_objective_acceptance" -maxdepth 2 -type f \( \
      -name 'next_gen_s8p_objective_acceptance_summary.json' -o \
      -name 'NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md' -o \
      -name 'next_gen_s8p_objective_acceptance_evidence.csv' \
    \) 2>/dev/null | sed -n '1,40p'
  fi

  echo
  echo "== Running related processes =="
  ps -fu "$USER" | grep -E 'emx|cadence|virtuoso|physical_feature|s8p|next_gen' | grep -v grep || true
fi
