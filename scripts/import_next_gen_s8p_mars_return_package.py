#!/usr/bin/env python3
"""Import and verify a downloaded next-generation S8P MARS return package.

This is the local-side counterpart to the automatic ``${WORK_DIR}/returns``
package created by ``NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh``. It is
deliberately conservative: it verifies package hashes and inventory, extracts
the tarball with path-traversal protection, and then dispatches the strict
next-gen S8P return discovery/status summary. It does not run EMX, HFSS, ADS,
or Cadence.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_OUT_DIR = DEFAULT_PROJECT_ROOT / "outputs" / "next_gen_s8p_mars_return_import_current"


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    tarball = Path(args.tarball).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    extract_dir = out_dir / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[Check] = []
    verifier_result = _run_package_verifier(tarball, out_dir, args)
    checks.append(_verifier_check(verifier_result))

    extracted_root: Path | None = None
    extract_error: str | None = None
    if verifier_result.get("accepted_for_import"):
        try:
            extracted_root = _safe_extract(tarball, extract_dir)
            checks.append(Check("PASS", "safe return package extraction", str(extracted_root)))
        except Exception as exc:  # noqa: BLE001 - preserve exact extraction issue.
            extract_error = f"{type(exc).__name__}: {exc}"
            checks.append(Check("FAIL", "safe return package extraction", extract_error))
    else:
        checks.append(Check("WARN", "safe return package extraction", "skipped because package verifier did not PASS"))

    discovery_result: dict[str, Any] | None = None
    if extracted_root and extracted_root.is_dir():
        discovery_result = _run_discovery(extracted_root, out_dir, args)
        checks.append(_discovery_check(discovery_result))
    else:
        checks.append(Check("WARN", "strict S8P return discovery", "not run because no extracted root is available"))

    overall_status, decision = _overall_decision(checks, verifier_result, discovery_result)
    next_steps_result = _write_after_import_next_steps(out_dir, discovery_result, overall_status, args)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "tarball": str(tarball),
        "out_dir": str(out_dir),
        "extracted_root": str(extracted_root) if extracted_root else None,
        "extract_error": extract_error,
        "verifier_result": verifier_result,
        "discovery_result": discovery_result,
        "next_steps_result": next_steps_result,
        "checks": [check.as_dict() for check in checks],
        "status_counts": _status_counts(checks),
        "requirements": {
            "expected_count": int(args.expected_count),
            "expected_jobs": int(args.expected_jobs),
            "expected_ports": int(args.expected_ports),
            "frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "frequency_points": int(args.expected_frequency_points),
        },
        "method_notes": [
            "This importer verifies and extracts a downloaded MARS return package only.",
            "It does not run EMX, HFSS, ADS, Cadence, or any GUI automation.",
            "A PASS here means the returned package is locally usable for the next S8P gates; final objective completion still requires HFSS export and EMX/HFSS Lp/Ls/Q/K/Kw comparison.",
        ],
    }
    summary_path = out_dir / "next_gen_s8p_mars_return_import_summary.json"
    report_path = out_dir / "NEXT_GEN_S8P_MARS_RETURN_IMPORT_REPORT.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if next_steps_result.get("script_path"):
        print(f"next_steps_script={next_steps_result['script_path']}")
    if next_steps_result.get("report_path"):
        print(f"next_steps_report={next_steps_result['report_path']}")
    for check in checks:
        print(f"{check.status:7s} {check.name}: {check.detail}")
    return 0 if overall_status in {"PASS", "READY_FOR_LOCAL_NEXT_GATES"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tarball", help="Downloaded next_gen_s8p_mars_return_*.tar.gz")
    parser.add_argument("--sha256-file")
    parser.add_argument("--inventory")
    parser.add_argument("--inventory-report")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expected-count", type=int, default=500)
    parser.add_argument("--expected-jobs", type=int, default=8)
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-touchstone-checks", type=int, default=500)
    parser.add_argument("--max-touchstone-frequency-checks", type=int, default=500)
    parser.add_argument("--require-hfss-validation-assets", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _run_package_verifier(tarball: Path, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    verify_dir = out_dir / "package_verify"
    command = [
        str(Path(args.python).expanduser()),
        str(SCRIPT_DIR / "verify_mars_dataset_package.py"),
        str(tarball),
        "--out-dir",
        str(verify_dir),
        "--run-progress-audit",
        "--expected-count",
        str(args.expected_count),
        "--expected-touchstone-ports",
        str(args.expected_ports),
        "--required-touchstone-extension",
        ".s8p",
        "--expected-frequency-start-ghz",
        str(args.expected_frequency_start_ghz),
        "--expected-frequency-stop-ghz",
        str(args.expected_frequency_stop_ghz),
        "--expected-frequency-step-ghz",
        str(args.expected_frequency_step_ghz),
        "--expected-frequency-points",
        str(args.expected_frequency_points),
        "--frequency-tolerance-hz",
        str(args.frequency_tolerance_hz),
        "--max-touchstone-frequency-checks",
        str(args.max_touchstone_frequency_checks),
        "--require-emx-command",
        "--expected-port-mode",
        "single_ended_shield_grounded",
        "--expected-pin-purpose",
        "51",
        "--require-s8p-quality-gates",
        "--require-next-gen-s8p-status",
        "--require-run-config",
        "--no-fail-exit",
    ]
    if args.sha256_file:
        command.extend(["--sha256-file", str(Path(args.sha256_file).expanduser().resolve())])
    if args.inventory:
        command.extend(["--inventory", str(Path(args.inventory).expanduser().resolve())])
    if args.inventory_report:
        command.extend(["--inventory-report", str(Path(args.inventory_report).expanduser().resolve())])
    if args.require_hfss_validation_assets:
        command.append("--require-hfss-validation-assets")
    completed = subprocess.run(command, cwd=SCRIPT_DIR.parents[0], text=True, capture_output=True, check=False)
    summary_path = verify_dir / "mars_dataset_package_verify_summary.json"
    summary = _read_json(summary_path)
    accepted = completed.returncode == 0 and summary.get("overall_status") == "PASS"
    return {
        "accepted_for_import": accepted,
        "returncode": completed.returncode,
        "command": command,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "summary_path": str(summary_path),
        "summary": summary,
    }


def _run_discovery(extracted_root: Path, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    discovery_dir = out_dir / "return_discovery"
    command = [
        str(Path(args.python).expanduser()),
        str(SCRIPT_DIR / "discover_next_gen_s8p_mars_return.py"),
        "--search-root",
        str(extracted_root),
        "--out-dir",
        str(discovery_dir),
        "--expected-count",
        str(args.expected_count),
        "--expected-jobs",
        str(args.expected_jobs),
        "--expected-ports",
        str(args.expected_ports),
        "--expected-frequency-start-ghz",
        str(args.expected_frequency_start_ghz),
        "--expected-frequency-stop-ghz",
        str(args.expected_frequency_stop_ghz),
        "--expected-frequency-step-ghz",
        str(args.expected_frequency_step_ghz),
        "--expected-frequency-points",
        str(args.expected_frequency_points),
        "--frequency-tolerance-hz",
        str(args.frequency_tolerance_hz),
        "--max-touchstone-checks",
        str(args.max_touchstone_checks),
        "--no-fail-exit",
    ]
    completed = subprocess.run(command, cwd=SCRIPT_DIR.parents[0], text=True, capture_output=True, check=False)
    summary_path = discovery_dir / "next_gen_s8p_mars_return_discovery_summary.json"
    summary = _read_json(summary_path)
    return {
        "returncode": completed.returncode,
        "command": command,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "summary_path": str(summary_path),
        "summary": summary,
    }


def _safe_extract(tarball: Path, extract_dir: Path) -> Path:
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            _validate_member(member, extract_dir)
        _safe_extractall_compat(archive, extract_dir, members)
    dirs = [path for path in extract_dir.iterdir() if path.is_dir()]
    return dirs[0] if len(dirs) == 1 else extract_dir


def _validate_member(member: tarfile.TarInfo, extract_dir: Path) -> None:
    member_path = Path(member.name)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError(f"unsafe tar member path: {member.name}")
    if member.issym() or member.islnk():
        raise ValueError(f"tar member link is not allowed: {member.name}")
    target = (extract_dir / member.name).resolve()
    try:
        target.relative_to(extract_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"unsafe tar extraction target: {member.name}") from exc


def _safe_extractall_compat(archive: tarfile.TarFile, extract_dir: Path, members: list[tarfile.TarInfo]) -> None:
    try:
        archive.extractall(extract_dir, members=members, filter="data")
    except TypeError:
        archive.extractall(extract_dir, members=members)


def _verifier_check(result: dict[str, Any]) -> Check:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    if result.get("accepted_for_import"):
        return Check("PASS", "return package verifier", f"PASS: {result.get('summary_path')}")
    return Check(
        "FAIL",
        "return package verifier",
        f"returncode={result.get('returncode')}, overall_status={summary.get('overall_status')}, summary={result.get('summary_path')}",
    )


def _discovery_check(result: dict[str, Any]) -> Check:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    status = summary.get("overall_status")
    if status in {"PASS", "READY_FOR_NEXT_GATES"}:
        return Check("PASS", "strict S8P return discovery", f"overall_status={status}, decision={summary.get('decision')}")
    return Check(
        "FAIL",
        "strict S8P return discovery",
        f"returncode={result.get('returncode')}, overall_status={status}, decision={summary.get('decision')}, summary={result.get('summary_path')}",
    )


def _overall_decision(
    checks: list[Check],
    verifier_result: dict[str, Any],
    discovery_result: dict[str, Any] | None,
) -> tuple[str, str]:
    if any(check.status == "FAIL" for check in checks):
        return "FAIL", "DO_NOT_IMPORT_NEXT_GEN_S8P_RETURN_PACKAGE"
    discovery_status = ""
    if discovery_result and isinstance(discovery_result.get("summary"), dict):
        discovery_status = str(discovery_result["summary"].get("overall_status") or "")
    if verifier_result.get("accepted_for_import") and discovery_status == "PASS":
        return "PASS", "ACCEPT_IMPORTED_NEXT_GEN_S8P_RETURN_PACKAGE"
    if verifier_result.get("accepted_for_import"):
        return "READY_FOR_LOCAL_NEXT_GATES", "CONTINUE_LOCAL_S8P_HFSS_AND_REPORT_GATES"
    return "FAIL", "DO_NOT_IMPORT_NEXT_GEN_S8P_RETURN_PACKAGE"


def _write_after_import_next_steps(
    out_dir: Path,
    discovery_result: dict[str, Any] | None,
    overall_status: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    run_dir = _selected_run_dir(discovery_result)
    result: dict[str, Any] = {
        "generated": False,
        "reason": "",
        "script_path": None,
        "report_path": None,
        "run_dir": str(run_dir) if run_dir else None,
    }
    if overall_status not in {"PASS", "READY_FOR_LOCAL_NEXT_GATES"}:
        result["reason"] = f"not generated because import overall_status={overall_status}"
        return result
    if run_dir is None or not run_dir.is_dir():
        result["reason"] = "not generated because no selected run_dir was discovered"
        return result

    script_path = out_dir / "next_gen_s8p_after_import_next_steps.commands.sh"
    report_path = out_dir / "NEXT_GEN_S8P_AFTER_IMPORT_NEXT_STEPS.md"
    script_path.write_text(_render_after_import_next_steps_script(run_dir, args), encoding="utf-8")
    script_path.chmod(0o755)
    report_path.write_text(_render_after_import_next_steps_report(run_dir, script_path, args), encoding="utf-8")
    result.update(
        {
            "generated": True,
            "reason": "generated local-only commands for inverse-model, HFSS handoff, postrun validation, and final evidence gates",
            "script_path": str(script_path),
            "report_path": str(report_path),
        }
    )
    return result


def _selected_run_dir(discovery_result: dict[str, Any] | None) -> Path | None:
    if not discovery_result or not isinstance(discovery_result.get("summary"), dict):
        return None
    summary = discovery_result["summary"]
    selected = summary.get("selected_candidate") if isinstance(summary.get("selected_candidate"), dict) else {}
    raw = selected.get("run_dir")
    if not raw:
        return None
    return Path(str(raw)).expanduser().resolve()


def _render_after_import_next_steps_script(run_dir: Path, args: argparse.Namespace) -> str:
    repo_root = SCRIPT_DIR.parents[0]
    q_repo = _sh(repo_root)
    q_run = _sh(run_dir)
    target_template_block = _render_target_template_block()
    expected_count = int(args.expected_count)
    expected_ports = int(args.expected_ports)
    start = float(args.expected_frequency_start_ghz)
    stop = float(args.expected_frequency_stop_ghz)
    step = float(args.expected_frequency_step_ghz)
    points = int(args.expected_frequency_points)
    return f"""#!/usr/bin/env bash
set -euo pipefail

# Generated by import_next_gen_s8p_mars_return_package.py.
# Local-only continuation after a verified MARS return package import.
# This script does not run EMX, ADS, Cadence, or a browser. It generates local
# evidence artifacts and HFSS rebuild/export scripts; an engineer still needs to
# run the generated AEDT/HFSS commands in the proper HFSS environment.

REPO_ROOT=${{REPO_ROOT:-{q_repo}}}
RUN_DIR=${{RUN_DIR:-{q_run}}}
QUALITY_DIR="${{QUALITY_DIR:-$RUN_DIR/dataset_quality_gates_s8p_physical_feature}}"
SCALAR_Q_DIR="${{SCALAR_Q_DIR:-$QUALITY_DIR/scalar_q_feature_dataset}}"
SAMPLES_CSV="${{SAMPLES_CSV:-$QUALITY_DIR/physical_feature_validation_sample_selection/physical_feature_validation_samples.csv}}"
PORT_PAIR_AUDIT_DIR="${{PORT_PAIR_AUDIT_DIR:-$QUALITY_DIR/selected_s8p_port_pair_physical_candidate_audit}}"
LAYOUT_AUDIT_DIR="${{LAYOUT_AUDIT_DIR:-$QUALITY_DIR/selected_power_line_8port_layout_audit}}"
HFSS_HANDOFF_DIR="${{HFSS_HANDOFF_DIR:-$QUALITY_DIR/selected_s8p_hfss_handoff}}"
AEDT_SCRIPT_DIR="${{AEDT_SCRIPT_DIR:-$QUALITY_DIR/selected_s8p_hfss_aedt_scripts}}"
HFSS_RENDER_DIR="${{HFSS_RENDER_DIR:-$QUALITY_DIR/selected_s8p_hfss_payload_views}}"
POSTRUN_DIR="${{POSTRUN_DIR:-$QUALITY_DIR/selected_s8p_hfss_postrun_validation}}"
FINAL_EVIDENCE_DIR="${{FINAL_EVIDENCE_DIR:-$QUALITY_DIR/s8p_final_report_evidence_packet}}"
FINAL_VALID_DISCOVERY_DIR="${{FINAL_VALID_DISCOVERY_DIR:-$QUALITY_DIR/final_valid_emx_s8p_discovery}}"
FINAL_VALID_SAMPLES_DIR="${{FINAL_VALID_SAMPLES_DIR:-$QUALITY_DIR/final_valid_emx_s8p_sample_selection}}"
TARGET_LAYOUT_SMOKE_DIR="${{TARGET_LAYOUT_SMOKE_DIR:-$QUALITY_DIR/physical_feature_saved_inverse_target_layout_smoke}}"
TARGET_TEMPLATE_JSON="${{TARGET_TEMPLATE_JSON:-$QUALITY_DIR/physical_feature_saved_inverse_model/target_lp_ls_q_k_template.json}}"
RUN_STATUS_DIR="${{RUN_STATUS_DIR:-$RUN_DIR/next_gen_s8p_mars_run_status}}"
OBJECTIVE_DIR="${{OBJECTIVE_DIR:-$QUALITY_DIR/next_gen_s8p_objective_acceptance}}"
CONFIG_PATH="${{CONFIG_PATH:-$RUN_DIR/final_s8p_physical_feature_500.yaml}}"
TARGET_JSON="${{TARGET_JSON:-}}"
ALLOW_TARGET_EXTRAPOLATION="${{ALLOW_TARGET_EXTRAPOLATION:-0}}"
LAUNCH_SUMMARY="${{LAUNCH_SUMMARY:-}}"
COMBINED_APPROVAL_SUMMARY="${{COMBINED_APPROVAL_SUMMARY:-}}"

if [[ -z "${{PYTHON:-}}" ]]; then
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
  else
    echo "No usable Python found." >&2
    exit 11
  fi
fi
export PYTHON

if [[ ! -f "$CONFIG_PATH" ]]; then
  for maybe_config in \\
    "$RUN_DIR/../02_final_s8p_config/final_s8p_physical_feature_500.yaml" \\
    "$RUN_DIR/../final_s8p_physical_feature_500.yaml"; do
    if [[ -f "$maybe_config" ]]; then
      CONFIG_PATH="$maybe_config"
      break
    fi
  done
fi

CONFIG_ARGS=()
if [[ -f "$CONFIG_PATH" ]]; then
  CONFIG_ARGS=(--config "$CONFIG_PATH")
else
  echo "CONFIG_PATH not found; continuing without config-bound geometry column order: $CONFIG_PATH"
fi

TARGET_ARGS=()
if [[ -n "$TARGET_JSON" ]]; then
  if [[ ! -f "$TARGET_JSON" ]]; then
    echo "TARGET_JSON was supplied but does not exist: $TARGET_JSON" >&2
    exit 13
  fi
  TARGET_ARGS=(--target-json "$TARGET_JSON")
  if [[ "$ALLOW_TARGET_EXTRAPOLATION" == "1" ]]; then
    TARGET_ARGS+=(--allow-target-extrapolation)
  fi
fi

echo "[1/16] Plan Lp/Ls/Q/K response-space coverage"
"$PYTHON" "$REPO_ROOT/scripts/plan_physical_feature_balanced_acquisition.py" "$SCALAR_Q_DIR" \\
  --out-dir "$QUALITY_DIR/physical_feature_balanced_acquisition_plan" \\
  --feature-columns lp_nh_center,ls_nh_center,q_center,k_center \\
  --bins 4 \\
  --desired-total-count {expected_count} \\
  --next-count 100 \\
  --no-fail-exit

echo "[2/16] Build post-EMX Lp/Ls/Q/K inverse training table"
"$PYTHON" "$REPO_ROOT/scripts/build_physical_feature_inverse_training_table.py" "$SCALAR_Q_DIR" \\
  --out-dir "$QUALITY_DIR/physical_feature_inverse_training_table" \\
  --feature-columns lp_nh_center,ls_nh_center,q_center,k_center \\
  "${{CONFIG_ARGS[@]}}" \\
  --no-fail-exit

echo "[3/16] Audit Lp/Ls/Q/K inverse-model quality"
"$PYTHON" "$REPO_ROOT/scripts/audit_physical_feature_inverse_model_quality.py" \\
  --training-csv "$QUALITY_DIR/physical_feature_inverse_training_table/physical_feature_inverse_training_table.csv" \\
  --out-dir "$QUALITY_DIR/physical_feature_inverse_model_quality" \\
  --k-neighbors 8 \\
  --no-fail-exit

echo "[4/16] Train saved baseline Lp/Ls/Q/K-to-geometry inverse model"
"$PYTHON" "$REPO_ROOT/scripts/train_physical_feature_inverse_model.py" \\
  --training-csv "$QUALITY_DIR/physical_feature_inverse_training_table/physical_feature_inverse_training_table.csv" \\
  --out-dir "$QUALITY_DIR/physical_feature_saved_inverse_model" \\
  "${{CONFIG_ARGS[@]}}" \\
  --degree 2 \\
  --ridge-alpha 1e-6 \\
  "${{TARGET_ARGS[@]}}" \\
  --no-fail-exit

{target_template_block}

echo "[6/16] Optional target Lp/Ls/Q/K-to-layout create-only smoke"
if [[ -n "$TARGET_JSON" ]]; then
  if [[ ! -f "$CONFIG_PATH" ]]; then
    echo "Skipping target-layout smoke because CONFIG_PATH is missing: $CONFIG_PATH"
  else
    "$PYTHON" "$REPO_ROOT/scripts/run_candidate_queue_dataset.py" \\
      --candidate-csv "$QUALITY_DIR/physical_feature_saved_inverse_model/physical_feature_inverse_model_target_predictions.csv" \\
      --out-dir "$TARGET_LAYOUT_SMOKE_DIR" \\
      --config "$CONFIG_PATH" \\
      --max-count 1 \\
      --batch-size 1 \\
      --create-only \\
      --force-wideband-5-60-1p0 \\
      --expected-port-mode single_ended_shield_grounded \\
      --expected-pin-purpose 51 \\
      --expected-frequency-start-ghz {start:g} \\
      --expected-frequency-stop-ghz {stop:g} \\
      --expected-frequency-step-ghz {step:g} \\
      --expected-frequency-points {points} \\
      --fail-on-error
  fi
else
  echo "TARGET_JSON is not set; skipping explicit target geometry prediction smoke."
fi

echo "[7/16] Discover final-valid real EMX S8P candidates before HFSS handoff"
"$PYTHON" "$REPO_ROOT/scripts/discover_final_valid_emx_s8p_candidates.py" \\
  --search-root "$RUN_DIR" \\
  --out-dir "$FINAL_VALID_DISCOVERY_DIR" \\
  --expected-ports {expected_ports} \\
  --expected-frequency-start-ghz {start:g} \\
  --expected-frequency-stop-ghz {stop:g} \\
  --expected-frequency-step-ghz {step:g} \\
  --expected-frequency-points {points} \\
  --no-fail-exit
"$PYTHON" - "$FINAL_VALID_DISCOVERY_DIR/final_valid_emx_s8p_candidate_discovery_summary.json" <<'PY'
import json
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
summary = json.loads(summary_path.read_text(encoding="utf-8"))
if summary.get("overall_status") != "PASS" or int(summary.get("final_valid_count") or 0) < 1:
    print("No final-valid real EMX .s8p candidate found; do not build HFSS handoff from this return.")
    print("summary=" + str(summary_path))
    raise SystemExit(21)
print("final_valid_emx_s8p_candidates=" + str(summary.get("final_valid_count")))
PY

"$PYTHON" "$REPO_ROOT/scripts/export_final_valid_emx_s8p_samples.py" \\
  --discovery-summary "$FINAL_VALID_DISCOVERY_DIR/final_valid_emx_s8p_candidate_discovery_summary.json" \\
  --out-dir "$FINAL_VALID_SAMPLES_DIR" \\
  --original-samples-csv "$SAMPLES_CSV" \\
  --max-samples 1
SAMPLES_CSV="$FINAL_VALID_SAMPLES_DIR/physical_feature_validation_samples.csv"
echo "SAMPLES_CSV=$SAMPLES_CSV"

echo "[8/16] Audit selected validation sample S8P port-pair physics"
"$PYTHON" "$REPO_ROOT/scripts/audit_s8p_port_pair_physical_candidates.py" \\
  --samples-csv "$SAMPLES_CSV" \\
  --dataset-dir "$RUN_DIR" \\
  --out-dir "$PORT_PAIR_AUDIT_DIR" \\
  --expected-port-pairs 1,4:5,6 \\
  --candidate-port-pairs '1,4:5,6;7,8:1,2;1,2:7,8;3,4:5,6;1,2:3,4;5,6:7,8' \\
  --expected-ports {expected_ports} \\
  --expected-frequency-start-ghz {start:g} \\
  --expected-frequency-stop-ghz {stop:g} \\
  --expected-frequency-step-ghz {step:g} \\
  --expected-frequency-points {points} \\
  --no-fail-exit

echo "[9/16] Audit selected validation sample 8-port power-line layout evidence"
"$PYTHON" "$REPO_ROOT/scripts/audit_selected_power_line_8port_layout_samples.py" \\
  --samples-csv "$SAMPLES_CSV" \\
  --dataset-dir "$RUN_DIR" \\
  --out-dir "$LAYOUT_AUDIT_DIR" \\
  --expected-port-names P001,P002,P003,P004,P005,P006,P007,P008 \\
  --expected-pin-purpose 51 \\
  --power-line-tolerance-um 1e-12 \\
  --internal-angle-deg 135.0 \\
  --terminal-angle-deg 90.0 \\
  --angle-tolerance-deg 0.001 \\
  --no-fail-exit

echo "[10/16] Build selected sample HFSS rebuild handoff packet"
"$PYTHON" "$REPO_ROOT/scripts/build_selected_s8p_hfss_handoff_packet.py" \\
  --samples-csv "$SAMPLES_CSV" \\
  --dataset-dir "$RUN_DIR" \\
  --out-dir "$HFSS_HANDOFF_DIR" \\
  --layout-audit-summary "$LAYOUT_AUDIT_DIR/selected_power_line_8port_layout_audit_summary.json" \\
  --port-pairs 1,4:5,6 \\
  --bridge-tolerance-um 1e-12 \\
  --no-fail-exit

echo "[11/16] Generate selected sample HFSS AEDT build/solve scripts"
"$PYTHON" "$REPO_ROOT/scripts/build_s8p_hfss_aedt_scripts_from_handoff.py" \\
  --handoff-summary "$HFSS_HANDOFF_DIR/selected_s8p_hfss_handoff_summary.json" \\
  --out-dir "$AEDT_SCRIPT_DIR" \\
  --frequency-start-ghz {start:g} \\
  --frequency-stop-ghz {stop:g} \\
  --frequency-step-ghz {step:g} \\
  --expected-frequency-points {points} \\
  --no-fail-exit

echo "[12/16] Render selected sample HFSS payload geometry views"
"$PYTHON" "$REPO_ROOT/scripts/render_hfss_model_views_from_payload.py" \\
  --aedt-packet-summary "$AEDT_SCRIPT_DIR/hfss_s8p_aedt_script_packet_summary.json" \\
  --out-dir "$HFSS_RENDER_DIR" \\
  --no-fail-exit

echo "[13/16] Prepare/read post-HFSS EMX/HFSS S8P physical validation gate"
"$PYTHON" "$REPO_ROOT/scripts/run_s8p_hfss_postrun_validation_from_aedt_packet.py" \\
  --aedt-packet-summary "$AEDT_SCRIPT_DIR/hfss_s8p_aedt_script_packet_summary.json" \\
  --out-dir "$POSTRUN_DIR" \\
  --max-percent-error 5.0 \\
  --no-fail-exit

echo "[14/16] Build final S8P report evidence packet"
"$PYTHON" "$REPO_ROOT/scripts/build_s8p_final_report_evidence_packet.py" \\
  --quality-dir "$QUALITY_DIR" \\
  --out-dir "$FINAL_EVIDENCE_DIR" \\
  --max-percent-error 5.0 \\
  --target-ghz 15.0 \\
  --no-fail-exit

echo "[15/16] Summarize current next-gen S8P run status"
"$PYTHON" "$REPO_ROOT/scripts/summarize_next_gen_s8p_mars_run.py" \\
  --run-dir "$RUN_DIR" \\
  --quality-dir "$QUALITY_DIR" \\
  --out-dir "$RUN_STATUS_DIR" \\
  --expected-count {expected_count} \\
  --expected-jobs {int(args.expected_jobs)} \\
  --expected-ports {expected_ports} \\
  --expected-frequency-start-ghz {start:g} \\
  --expected-frequency-stop-ghz {stop:g} \\
  --expected-frequency-step-ghz {step:g} \\
  --expected-frequency-points {points} \\
  --max-touchstone-checks {int(args.max_touchstone_checks)} \\
  --no-fail-exit

echo "[16/16] Optional objective-level audit if launch/approval summaries are provided"
if [[ -n "$LAUNCH_SUMMARY" && -f "$LAUNCH_SUMMARY" && -n "$COMBINED_APPROVAL_SUMMARY" && -f "$COMBINED_APPROVAL_SUMMARY" ]]; then
  "$PYTHON" "$REPO_ROOT/scripts/build_next_gen_s8p_objective_acceptance_audit.py" \\
    --launch-summary "$LAUNCH_SUMMARY" \\
    --combined-approval-summary "$COMBINED_APPROVAL_SUMMARY" \\
    --run-status-summary "$RUN_STATUS_DIR/next_gen_s8p_mars_run_status_summary.json" \\
    --out-dir "$OBJECTIVE_DIR" \\
    --no-fail-exit
  "$PYTHON" "$REPO_ROOT/scripts/build_s8p_final_report_evidence_packet.py" \\
    --quality-dir "$QUALITY_DIR" \\
    --objective-acceptance-summary "$OBJECTIVE_DIR/next_gen_s8p_objective_acceptance_summary.json" \\
    --out-dir "$FINAL_EVIDENCE_DIR" \\
    --max-percent-error 5.0 \\
    --target-ghz 15.0 \\
    --no-fail-exit
else
  echo "Skipping objective-level audit because LAUNCH_SUMMARY and/or COMBINED_APPROVAL_SUMMARY were not provided."
fi

echo "NEXT_STEPS_DONE"
echo "RUN_STATUS_SUMMARY=$RUN_STATUS_DIR/next_gen_s8p_mars_run_status_summary.json"
echo "FINAL_EVIDENCE_SUMMARY=$FINAL_EVIDENCE_DIR/s8p_final_report_evidence_packet_summary.json"
echo "HFSS_AEDT_PACKET_SUMMARY=$AEDT_SCRIPT_DIR/hfss_s8p_aedt_script_packet_summary.json"
echo "TARGET_TEMPLATE_JSON=$TARGET_TEMPLATE_JSON"
echo "TARGET_PREDICTIONS_CSV=$QUALITY_DIR/physical_feature_saved_inverse_model/physical_feature_inverse_model_target_predictions.csv"
echo "TARGET_LAYOUT_SMOKE_SUMMARY=$TARGET_LAYOUT_SMOKE_DIR/candidate_queue_dataset_summary.json"
"""


def _render_target_template_block() -> str:
    return """echo "[5/16] Write editable target Lp/Ls/Q/K JSON template from trained envelope"
"$PYTHON" - "$QUALITY_DIR/physical_feature_saved_inverse_model/physical_feature_inverse_model_training_summary.json" "$TARGET_TEMPLATE_JSON" <<'PY'
import json
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
if not summary_path.is_file():
    print("target template skipped: missing " + str(summary_path))
    raise SystemExit(0)

try:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    print("target template skipped: invalid training summary JSON: " + str(exc))
    raise SystemExit(0)

input_columns = [str(item) for item in (summary.get("input_columns") or [])]
feature_names = [column.removeprefix("input__") for column in input_columns]
zin_columns = [name for name in feature_names if "zin" in name.lower()]
required_features = {"lp_nh_center", "ls_nh_center", "q_center", "k_center"}
missing_features = sorted(required_features - set(feature_names))
if zin_columns:
    print("target template refused: saved inverse model contains Zin inputs: " + ",".join(zin_columns))
    raise SystemExit(2)
if missing_features:
    print("target template refused: saved inverse model is missing required Lp/Ls/Q/K inputs: " + ",".join(missing_features))
    raise SystemExit(2)
input_domain = summary.get("input_domain") if isinstance(summary.get("input_domain"), dict) else {}
per_feature = input_domain.get("per_feature") if isinstance(input_domain.get("per_feature"), dict) else {}
unit_by_name = {
    "lp_nh_center": "nH",
    "ls_nh_center": "nH",
    "q_center": "unitless",
    "k_center": "unitless",
    "kw_center": "unitless",
}

payload = {
    "_schema": "target_lp_ls_q_k.v1",
    "_description": "Edit the top-level physical-feature values, keep them inside _feature_envelope unless ALLOW_TARGET_EXTRAPOLATION=1 is intentionally used.",
    "_source_training_summary": str(summary_path),
    "_units": {},
    "_feature_envelope": {},
}

for column in input_columns:
    name = column.removeprefix("input__")
    stats = per_feature.get(column, {}) if isinstance(per_feature, dict) else {}
    envelope = {}
    for key in ("min", "max", "span", "mean", "std"):
        value = stats.get(key) if isinstance(stats, dict) else None
        if isinstance(value, (int, float)):
            envelope[key] = float(value)
    minimum = envelope.get("min")
    maximum = envelope.get("max")
    mean = envelope.get("mean")
    if minimum is not None and maximum is not None:
        target_value = 0.5 * (minimum + maximum)
    elif mean is not None:
        target_value = mean
    else:
        target_value = None
    payload["_units"][name] = unit_by_name.get(name, "see_training_summary")
    payload["_feature_envelope"][name] = envelope
    payload[name] = target_value

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
print("target_template_json=" + str(out_path))
PY
"""


def _render_after_import_next_steps_report(run_dir: Path, script_path: Path, args: argparse.Namespace) -> str:
    quality_dir = run_dir / "dataset_quality_gates_s8p_physical_feature"
    target_template = quality_dir / "physical_feature_saved_inverse_model" / "target_lp_ls_q_k_template.json"
    return "\n".join(
        [
            "# Next-Gen S8P After-Import Next Steps",
            "",
            "This is a local continuation plan generated after a verified MARS return package import.",
            "It does not claim that HFSS validation is complete.",
            "",
            f"- Run directory: `{run_dir}`",
            f"- Quality directory: `{quality_dir}`",
            f"- Command script: `{script_path}`",
            f"- Expected samples: `{int(args.expected_count)}`",
            f"- Expected ports: `{int(args.expected_ports)}`",
            f"- Frequency grid: `{float(args.expected_frequency_start_ghz):g}-{float(args.expected_frequency_stop_ghz):g} GHz`, step `{float(args.expected_frequency_step_ghz):g} GHz`, points `{int(args.expected_frequency_points)}`",
            "",
            "## Use",
            "",
            "```bash",
            f"bash {shlex.quote(str(script_path))}",
            "```",
            "",
            "The generated script first requires at least one final-valid real EMX `.s8p` candidate, then builds or refreshes the post-EMX physical-feature inverse-model evidence, selected-sample HFSS handoff packet, AEDT build/solve scripts, payload geometry views, post-HFSS comparison gate, run-status summary, and final evidence manifest.",
            "",
            "After the first run, it also writes an editable target template with feature units and the training envelope:",
            "",
            "```bash",
            f"cp {shlex.quote(str(target_template))} /path/to/target_lp_ls_q_k.json",
            "```",
            "",
            "To exercise the final inverse-design path, provide a target feature JSON before running it:",
            "",
            "```bash",
            f"TARGET_JSON=/path/to/target_lp_ls_q_k.json bash {shlex.quote(str(script_path))}",
            "```",
            "",
            "When `TARGET_JSON` is supplied, the script writes `physical_feature_inverse_model_target_predictions.csv` and runs a create-only layout smoke for the predicted geometry.",
            "",
            "HFSS simulation/export remains an external step: the generated AEDT scripts must be run in a valid HFSS environment before the EMX/HFSS Lp/Ls/Q/K/Kw comparison can PASS.",
            "",
        ]
    )


def _sh(path: Path | str) -> str:
    return shlex.quote(str(path))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"_missing": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_parse_error": f"top-level JSON is {type(payload).__name__}"}


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return dict(sorted(counts.items()))


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Next-Gen S8P MARS Return Import",
        "",
        f"- overall_status: `{summary['overall_status']}`",
        f"- decision: `{summary['decision']}`",
        f"- tarball: `{summary['tarball']}`",
        f"- extracted_root: `{summary.get('extracted_root')}`",
        "",
        "## Checks",
        "",
    ]
    for check in summary.get("checks", []):
        lines.append(f"- {check['status']}: {check['name']} - {check['detail']}")
    verifier = summary.get("verifier_result") or {}
    discovery = summary.get("discovery_result") or {}
    lines.extend(
        [
            "",
            "## Downstream Summaries",
            "",
            f"- package verifier: `{verifier.get('summary_path')}`",
            f"- return discovery: `{discovery.get('summary_path')}`",
            "",
            "## Method Notes",
            "",
        ]
    )
    for note in summary.get("method_notes", []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
