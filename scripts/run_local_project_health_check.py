#!/usr/bin/env python3
"""Run the local RFIC transformer project health checks.

This is a local orchestration helper for reproducibility. It verifies the
desktop delivery package, MARS handoff install readiness, acceptance-matrix
boundary, and optionally runs the full local pytest suite. It does not run MARS,
HFSS, ADS, EMX, or claim external simulator completion.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StepResult:
    name: str
    status: str
    detail: str
    command: list[str]
    returncode: int | None = None
    summary_path: str | None = None
    report_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "command": self.command,
            "returncode": self.returncode,
            "summary_path": self.summary_path,
            "report_path": self.report_path,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
    package_dir = Path(args.package_dir).expanduser().resolve()
    zip_path = Path(args.zip_path).expanduser().resolve()
    zip_sha_record = Path(args.zip_sha_record).expanduser().resolve()
    handoff_root = Path(args.handoff_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    steps: list[StepResult] = []
    if args.rebuild_delivery_zip:
        if not args.skip_package_selfcheck_compare:
            steps.append(
                _run_package_selfcheck_compare(
                    repo_root=repo_root,
                    package_dir=package_dir,
                    python=args.python,
                )
            )
        steps.append(
            _run_mars_handoff_bundle_rebuild(
                repo_root=repo_root,
                project_root=project_root,
                handoff_root=handoff_root,
                python=args.python,
            )
        )
        steps.append(
            _run_validation_chain_decision(
                repo_root=repo_root,
                project_root=project_root,
                package_dir=package_dir,
                accepted_validation_summary=args.accepted_validation_summary,
                python=args.python,
            )
        )
        steps.append(
            _run_ads_metric_formula_consistency(
                repo_root=repo_root,
                project_root=project_root,
                python=args.python,
            )
        )
        steps.append(
            _run_hfss_model_geometry_asset_audit(
                repo_root=repo_root,
                package_dir=package_dir,
                python=args.python,
            )
        )
        steps.append(
            _run_project_acceptance_matrix_refresh(
                repo_root=repo_root,
                project_root=project_root,
                package_dir=package_dir,
                python=args.python,
                name="project acceptance matrix pre-sync",
            )
        )
        steps.append(
            _run_build_clean_zip(
                repo_root=repo_root,
                package_dir=package_dir,
                zip_path=zip_path,
                zip_sha_record=zip_sha_record,
                out_dir=out_dir,
                python=args.python,
            )
        )
    else:
        steps.append(
            _run_validation_chain_decision(
                repo_root=repo_root,
                project_root=project_root,
                package_dir=package_dir,
                accepted_validation_summary=args.accepted_validation_summary,
                python=args.python,
            )
        )
        steps.append(
            _run_ads_metric_formula_consistency(
                repo_root=repo_root,
                project_root=project_root,
                python=args.python,
            )
        )
        steps.append(
            _run_hfss_model_geometry_asset_audit(
                repo_root=repo_root,
                package_dir=package_dir,
                python=args.python,
            )
        )
    steps.append(
        _run_delivery_audit(
            repo_root=repo_root,
            project_root=project_root,
            package_dir=package_dir,
            zip_path=zip_path,
            zip_sha_record=zip_sha_record,
            python=args.python,
        )
    )
    steps.append(
        _run_handoff_verify(
            repo_root=repo_root,
            project_root=project_root,
            handoff_root=handoff_root,
            python=args.python,
        )
    )
    steps.append(
        _run_mars_next_action_packet(
            repo_root=repo_root,
            project_root=project_root,
            package_dir=package_dir,
            python=args.python,
        )
    )
    if not args.skip_mars_emx_return_watch:
        steps.append(
            _run_mars_emx_return_watch(
                repo_root=repo_root,
                project_root=project_root,
                python=args.python,
            )
        )
    if args.rebuild_delivery_zip:
        steps.append(
            _run_project_acceptance_matrix_refresh(
                repo_root=repo_root,
                project_root=project_root,
                package_dir=package_dir,
                python=args.python,
                name="project acceptance matrix post-audit",
            )
        )
        steps.append(
            _run_build_clean_zip(
                repo_root=repo_root,
                package_dir=package_dir,
                zip_path=zip_path,
                zip_sha_record=zip_sha_record,
                out_dir=out_dir,
                python=args.python,
                name="final clean delivery zip build",
            )
        )
        steps.append(
            _run_delivery_audit(
                repo_root=repo_root,
                project_root=project_root,
                package_dir=package_dir,
                zip_path=zip_path,
                zip_sha_record=zip_sha_record,
                python=args.python,
                name="final delivery package audit",
            )
        )
    steps.append(
        _run_acceptance_matrix(
            repo_root=repo_root,
            project_root=project_root,
            package_dir=package_dir,
            out_dir=out_dir,
            python=args.python,
        )
    )
    if args.run_tests:
        steps.append(_run_core_tests(repo_root=repo_root, python=args.python))

    overall_status = _overall_status(steps)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "repo_root": str(repo_root),
        "project_root": str(project_root),
        "package_dir": str(package_dir),
        "limitations": [
            "This health check verifies local artifacts and code gates only.",
            "PASS does not mean MARS final-500, MARS wideband 500, 248k production, HFSS, ADS, or EMX have completed.",
            "Acceptance matrix INCOMPLETE is expected until external simulator evidence exists.",
            "The validation-chain decision must block HFSS comparison whenever EMX-first is not accepted.",
            "The MARS next-action packet is a runbook/evidence boundary artifact, not simulator evidence.",
            "The MARS EMX return watcher records local pull/discovery state only; WAITING is expected until files are downloaded.",
            "ADS metric formula consistency proves extraction math on a synthetic known transformer only; it is not EMX/HFSS evidence.",
        ],
        "steps": [step.as_dict() for step in steps],
    }
    summary_path = out_dir / "local_project_health_summary.json"
    report_path = out_dir / "local_project_health_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for step in steps:
        print(f"{step.status:4s} {step.name}: {step.detail}")
    return 2 if overall_status == "FAIL" else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    default_repo = Path(__file__).resolve().parents[1]
    default_project = default_repo.parent
    default_package = Path("/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(default_repo))
    parser.add_argument("--project-root", default=str(default_project))
    parser.add_argument("--package-dir", default=str(default_package))
    parser.add_argument("--zip-path", default=str(default_package.with_suffix(".zip")))
    parser.add_argument(
        "--zip-sha-record",
        default=str(
            default_project
            / "hfss_validation"
            / "final500_ec6698dfc575950b"
            / "FINAL_DESKTOP_PACKAGE_HASH_20260613.txt"
        ),
    )
    parser.add_argument("--handoff-root", default=str(default_project / "mars_handoff_bundle_20260613"))
    parser.add_argument(
        "--out-dir",
        default=str(default_project / "hfss_validation" / "final500_ec6698dfc575950b" / "local_project_health_20260613"),
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--rebuild-delivery-zip", action="store_true")
    parser.add_argument(
        "--accepted-validation-summary",
        help="Optional final accepted EMX-vs-HFSS/ADS validation summary for full-chain acceptance",
    )
    parser.add_argument(
        "--skip-package-selfcheck-compare",
        action="store_true",
        help="With --rebuild-delivery-zip, skip refreshing the package selfcheck compare output before hashing",
    )
    parser.add_argument(
        "--skip-mars-emx-return-watch",
        action="store_true",
        help="Skip the one-iteration local watcher for returned MARS target EMX files",
    )
    parser.add_argument("--run-tests", action="store_true")
    return parser.parse_args(argv)


def _run_package_selfcheck_compare(*, repo_root: Path, package_dir: Path, python: str) -> StepResult:
    summary_path = package_dir / "package_selfcheck_compare_window_20260613" / "emx_hfss_ads_comparison_summary.json"
    report_path = package_dir / "package_selfcheck_compare_window_20260613" / "emx_hfss_ads_comparison_report.md"
    cmd = [
        python,
        str(repo_root / "scripts" / "run_package_selfcheck_compare.py"),
        "--package-dir",
        str(package_dir),
    ]
    completed = _run(cmd, cwd=repo_root)
    data = _read_json(summary_path)
    if completed.returncode == 0 and data.get("overall_status") == "PASS":
        window = data.get("frequency_window_hz", {})
        metrics = data.get("metrics", {})
        worst = _worst_metric_error(metrics)
        return StepResult(
            "package narrowband selfcheck compare",
            "PASS",
            f"narrowband-only window={window}, worst={worst}; not an EMX golden-reference gate",
            cmd,
            completed.returncode,
            str(summary_path),
            str(report_path),
        )
    return StepResult(
        "package narrowband selfcheck compare",
        "FAIL",
        _failure_detail(completed, data),
        cmd,
        completed.returncode,
        str(summary_path),
        str(report_path),
    )


def _run_mars_handoff_bundle_rebuild(
    *,
    repo_root: Path,
    project_root: Path,
    handoff_root: Path,
    python: str,
) -> StepResult:
    tar_path = project_root / "mars_handoff_bundle_20260613.tar.gz"
    sha_path = project_root / "mars_handoff_bundle_20260613.tar.gz.sha256"
    inventory_path = handoff_root / "MARS_HANDOFF_INVENTORY_20260613.json"
    cmd = [
        python,
        str(repo_root / "scripts" / "build_mars_handoff_bundle.py"),
        "--repo-root",
        str(repo_root),
        "--project-root",
        str(project_root),
        "--out",
        str(tar_path),
        "--staging-dir",
        str(handoff_root),
        "--force",
    ]
    completed = _run(cmd, cwd=repo_root)
    inventory = _read_json(inventory_path)
    file_count = int(inventory.get("file_count", 0) or 0)
    sha_text = sha_path.read_text(encoding="utf-8").split()[0] if sha_path.exists() else ""
    if completed.returncode == 0 and file_count > 0 and tar_path.exists() and sha_text:
        return StepResult(
            "MARS handoff bundle rebuild",
            "PASS",
            f"file_count={file_count}, tar_sha256={sha_text}",
            cmd,
            completed.returncode,
            str(inventory_path),
        )
    detail = _failure_detail(completed, inventory)
    if not tar_path.exists():
        detail = f"{detail}; missing tar={tar_path}"
    if not sha_text:
        detail = f"{detail}; missing sha record={sha_path}"
    return StepResult(
        "MARS handoff bundle rebuild",
        "FAIL",
        detail,
        cmd,
        completed.returncode,
        str(inventory_path),
    )


def _run_build_clean_zip(
    *,
    repo_root: Path,
    package_dir: Path,
    zip_path: Path,
    zip_sha_record: Path,
    out_dir: Path,
    python: str,
    name: str = "clean delivery zip build",
) -> StepResult:
    summary_path = out_dir / "build_clean_delivery_zip_summary.json"
    cmd = [
        python,
        str(repo_root / "scripts" / "build_clean_delivery_zip.py"),
        "--package-dir",
        str(package_dir),
        "--zip-path",
        str(zip_path),
        "--zip-sha-record",
        str(zip_sha_record),
        "--out-json",
        str(summary_path),
    ]
    completed = _run(cmd, cwd=repo_root)
    if completed.returncode != 0:
        return StepResult(name, "FAIL", _stderr_or_stdout(completed), cmd, completed.returncode, str(summary_path))
    data = _read_json(summary_path)
    metadata_count = int(data.get("metadata_entry_count", -1))
    file_count = int(data.get("package_file_count", 0) or 0)
    if metadata_count == 0 and file_count > 0:
        return StepResult(
            name,
            "PASS",
            f"package_file_count={file_count}, zip_entry_count={data.get('zip_entry_count')}, metadata_entry_count=0",
            cmd,
            completed.returncode,
            str(summary_path),
        )
    return StepResult(name, "FAIL", f"unexpected clean zip summary: {data}", cmd, completed.returncode, str(summary_path))


def _run_validation_chain_decision(
    *,
    repo_root: Path,
    project_root: Path,
    package_dir: Path,
    accepted_validation_summary: str | None,
    python: str,
) -> StepResult:
    out_dir = project_root / "validation_chain_decision_20260614"
    summary_path = out_dir / "validation_chain_decision_summary.json"
    report_path = out_dir / "validation_chain_decision_report.md"
    cmd = [
        python,
        str(repo_root / "scripts" / "build_validation_chain_decision_card.py"),
        "--emx-first-summary",
        str(package_dir / "emx_first_validation_gate_current_rerun_20260614" / "emx_first_validation_gate_summary.json"),
        "--hfss-geometry-summary",
        str(package_dir / "hfss_model_geometry_asset_audit_20260614" / "hfss_model_geometry_asset_audit_summary.json"),
        "--hfss-physical-summary",
        str(package_dir / "hfss_physical_gate_current_rerun_20260614" / "touchstone_transformer_audit_summary.json"),
        "--out-dir",
        str(out_dir),
        "--no-fail-exit",
    ]
    if accepted_validation_summary:
        cmd.extend(["--accepted-validation-summary", str(Path(accepted_validation_summary).expanduser().resolve())])
    completed = _run(cmd, cwd=repo_root)
    data = _read_json(summary_path)
    ok, detail = _validation_chain_detail(data)
    if completed.returncode == 0 and ok:
        return StepResult(
            "EMX/HFSS/ADS validation-chain decision",
            "PASS",
            detail,
            cmd,
            completed.returncode,
            str(summary_path),
            str(report_path),
        )
    return StepResult(
        "EMX/HFSS/ADS validation-chain decision",
        "FAIL",
        detail if completed.returncode == 0 else _failure_detail(completed, data),
        cmd,
        completed.returncode,
        str(summary_path),
        str(report_path),
    )


def _run_delivery_audit(
    *,
    repo_root: Path,
    project_root: Path,
    package_dir: Path,
    zip_path: Path,
    zip_sha_record: Path,
    python: str,
    name: str = "delivery package audit",
) -> StepResult:
    audit_dir = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "delivery_package_audit_20260613"
    summary_path = audit_dir / "delivery_package_audit_summary.json"
    report_path = audit_dir / "delivery_package_audit_report.md"
    cmd = [
        python,
        str(repo_root / "scripts" / "audit_delivery_package.py"),
        "--package-dir",
        str(package_dir),
        "--zip-path",
        str(zip_path),
        "--zip-sha-record",
        str(zip_sha_record),
        "--out-dir",
        str(audit_dir),
    ]
    completed = _run(cmd, cwd=repo_root)
    data = _read_json(summary_path)
    checks = data.get("checks", [])
    if completed.returncode == 0 and data.get("overall_status") == "PASS":
        return StepResult(
            name,
            "PASS",
            f"{len(checks)} checks PASS",
            cmd,
            completed.returncode,
            str(summary_path),
            str(report_path),
        )
    return StepResult(
        name,
        "FAIL",
        _failure_detail(completed, data),
        cmd,
        completed.returncode,
        str(summary_path),
        str(report_path),
    )


def _run_hfss_model_geometry_asset_audit(*, repo_root: Path, package_dir: Path, python: str) -> StepResult:
    audit_dir = package_dir / "hfss_model_geometry_asset_audit_20260614"
    summary_path = audit_dir / "hfss_model_geometry_asset_audit_summary.json"
    report_path = audit_dir / "hfss_model_geometry_asset_audit_report.md"
    cmd = [
        python,
        str(repo_root / "scripts" / "audit_hfss_model_geometry_assets.py"),
        "--package-dir",
        str(package_dir),
        "--out-dir",
        str(audit_dir),
    ]
    completed = _run(cmd, cwd=repo_root)
    data = _read_json(summary_path)
    if completed.returncode == 0 and data.get("overall_status") == "PASS":
        checks = data.get("checks", [])
        return StepResult(
            "HFSS model geometry asset audit",
            "PASS",
            f"{len(checks)} checks PASS; geometry assets are inspectable only, not EM-validated",
            cmd,
            completed.returncode,
            str(summary_path),
            str(report_path),
        )
    return StepResult(
        "HFSS model geometry asset audit",
        "FAIL",
        _failure_detail(completed, data),
        cmd,
        completed.returncode,
        str(summary_path),
        str(report_path),
    )


def _run_ads_metric_formula_consistency(*, repo_root: Path, project_root: Path, python: str) -> StepResult:
    audit_dir = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "ads_metric_formula_consistency_20260614"
    summary_path = audit_dir / "ads_metric_formula_consistency_summary.json"
    report_path = audit_dir / "ads_metric_formula_consistency_report.md"
    cmd = [
        python,
        str(repo_root / "scripts" / "audit_ads_metric_formula_consistency.py"),
        "--out-dir",
        str(audit_dir),
    ]
    completed = _run(cmd, cwd=repo_root)
    data = _read_json(summary_path)
    if completed.returncode == 0 and data.get("overall_status") == "PASS":
        recovery = data.get("metric_recovery_errors", {})
        worst_metric = _worst_formula_error(recovery)
        return StepResult(
            "ADS metric formula consistency",
            "PASS",
            f"{data.get('decision')}; worst_recovery={worst_metric}; synthetic formula audit only",
            cmd,
            completed.returncode,
            str(summary_path),
            str(report_path),
        )
    return StepResult(
        "ADS metric formula consistency",
        "FAIL",
        _failure_detail(completed, data),
        cmd,
        completed.returncode,
        str(summary_path),
        str(report_path),
    )


def _run_handoff_verify(*, repo_root: Path, project_root: Path, handoff_root: Path, python: str) -> StepResult:
    verify_dir = project_root / "mars_handoff_verify_20260613_latest"
    summary_path = verify_dir / "mars_handoff_verify_summary.json"
    report_path = verify_dir / "mars_handoff_verify_report.md"
    cmd = [
        python,
        str(repo_root / "scripts" / "verify_mars_handoff_install.py"),
        str(handoff_root),
        "--out-dir",
        str(verify_dir),
    ]
    completed = _run(cmd, cwd=repo_root)
    data = _read_json(summary_path)
    checks = data.get("checks", [])
    if completed.returncode == 0 and data.get("overall_status") == "PASS":
        return StepResult(
            "MARS handoff install verifier",
            "PASS",
            f"{len(checks)} checks PASS",
            cmd,
            completed.returncode,
            str(summary_path),
            str(report_path),
        )
    return StepResult(
        "MARS handoff install verifier",
        "FAIL",
        _failure_detail(completed, data),
        cmd,
        completed.returncode,
        str(summary_path),
        str(report_path),
    )


def _run_mars_next_action_packet(
    *,
    repo_root: Path,
    project_root: Path,
    package_dir: Path,
    python: str,
) -> StepResult:
    packet_dir = project_root / "mars_next_action_packet_20260614"
    summary_path = packet_dir / "mars_next_action_packet_summary.json"
    report_path = packet_dir / "MARS_NEXT_ACTION_PACKET_20260614_CN.md"
    cmd = [
        python,
        str(repo_root / "scripts" / "build_mars_next_action_packet.py"),
        "--project-root",
        str(project_root),
        "--package-dir",
        str(package_dir),
        "--out-dir",
        str(packet_dir),
        "--no-fail-exit",
    ]
    completed = _run(cmd, cwd=repo_root)
    data = _read_json(summary_path)
    status = str(data.get("overall_status"))
    decision = str(data.get("decision"))
    counts = data.get("status_counts", {})
    accepted_decisions = {
        "READY_FOR_MARS_TARGET_EMX_RERUN",
        "VALIDATION_CHAIN_ALREADY_ACCEPTED",
    }
    if completed.returncode == 0 and status == "PASS" and decision in accepted_decisions:
        return StepResult(
            "MARS next-action packet",
            "PASS",
            f"overall_status=PASS, decision={decision}, counts={counts}",
            cmd,
            completed.returncode,
            str(summary_path),
            str(report_path),
        )
    return StepResult(
        "MARS next-action packet",
        "FAIL",
        _failure_detail(completed, data),
        cmd,
        completed.returncode,
        str(summary_path),
        str(report_path),
    )


def _run_mars_emx_return_watch(*, repo_root: Path, project_root: Path, python: str) -> StepResult:
    watch_dir = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "mars_emx_return_watch_20260614"
    summary_path = watch_dir / "mars_emx_return_watch_summary.json"
    history_path = watch_dir / "mars_emx_return_watch_history.csv"
    cmd = [
        python,
        str(repo_root / "scripts" / "watch_mars_emx_return.py"),
        "--out-dir",
        str(watch_dir),
        "--interval-sec",
        "0",
        "--max-iterations",
        "1",
        "--no-fail-exit",
    ]
    completed = _run(cmd, cwd=repo_root)
    data = _read_json(summary_path)
    latest = data.get("latest_snapshot") if isinstance(data.get("latest_snapshot"), dict) else {}
    status = str(data.get("overall_status"))
    decision = str(data.get("decision"))
    if completed.returncode == 0 and status in {"WAITING_FOR_MARS_RETURN", "PASS", "READY_TO_VERIFY"}:
        s4p_count = data.get("s4p_candidate_count", latest.get("s4p_candidate_count"))
        tarball_count = data.get("tarball_candidate_count", latest.get("tarball_candidate_count"))
        evidence_use = data.get(
            "evidence_use",
            "NOT_ACCEPTED_EMX_REFERENCE" if status == "WAITING_FOR_MARS_RETURN" else "UNKNOWN",
        )
        accepted_emx_reference = data.get(
            "accepted_emx_reference",
            status == "PASS" and decision == "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS",
        )
        detail = (
            f"overall_status={status}, decision={decision}, iterations={data.get('iteration_count')}, "
            f"evidence_use={evidence_use}, accepted_emx_reference={accepted_emx_reference}, "
            f"s4p_candidates={s4p_count}, tarball_candidates={tarball_count}; "
            "watcher records local pull state only; WAITING_FOR_MARS_RETURN is not an accepted EMX reference"
        )
        return StepResult(
            "MARS target EMX return watcher",
            "PASS",
            detail,
            cmd,
            completed.returncode,
            str(summary_path),
            str(history_path),
        )
    return StepResult(
        "MARS target EMX return watcher",
        "FAIL",
        _failure_detail(completed, data),
        cmd,
        completed.returncode,
        str(summary_path),
        str(history_path),
    )


def _run_acceptance_matrix(
    *,
    repo_root: Path,
    project_root: Path,
    package_dir: Path,
    out_dir: Path,
    python: str,
) -> StepResult:
    summary_path = out_dir / "acceptance_matrix_health.json"
    report_path = out_dir / "ACCEPTANCE_MATRIX_HEALTH.md"
    return _run_acceptance_matrix_to_paths(
        repo_root=repo_root,
        project_root=project_root,
        package_dir=package_dir,
        summary_path=summary_path,
        report_path=report_path,
        python=python,
        name="acceptance matrix boundary",
    )


def _run_project_acceptance_matrix_refresh(
    *,
    repo_root: Path,
    project_root: Path,
    package_dir: Path,
    python: str,
    name: str,
) -> StepResult:
    return _run_acceptance_matrix_to_paths(
        repo_root=repo_root,
        project_root=project_root,
        package_dir=package_dir,
        summary_path=project_root / "acceptance_matrix_20260613.json",
        report_path=project_root / "ACCEPTANCE_MATRIX_20260613_CN.md",
        python=python,
        name=name,
    )


def _run_acceptance_matrix_to_paths(
    *,
    repo_root: Path,
    project_root: Path,
    package_dir: Path,
    summary_path: Path,
    report_path: Path,
    python: str,
    name: str,
) -> StepResult:
    cmd = [
        python,
        str(repo_root / "scripts" / "build_acceptance_matrix.py"),
        "--project-root",
        str(project_root),
        "--package-dir",
        str(package_dir),
        "--out-json",
        str(summary_path),
        "--out-md",
        str(report_path),
    ]
    completed = _run(cmd, cwd=repo_root)
    data = _read_json(summary_path)
    status = data.get("overall_status")
    counts = data.get("status_counts", {})
    if status == "INCOMPLETE" and completed.returncode in {0, 2}:
        return StepResult(
            name,
            "PASS",
            f"overall_status=INCOMPLETE, counts={counts}",
            cmd,
            completed.returncode,
            str(summary_path),
            str(report_path),
        )
    if status == "COMPLETE" and completed.returncode == 0:
        return StepResult(
            name,
            "PASS",
            "overall_status=COMPLETE",
            cmd,
            completed.returncode,
            str(summary_path),
            str(report_path),
        )
    return StepResult(
        name,
        "FAIL",
        _failure_detail(completed, data),
        cmd,
        completed.returncode,
        str(summary_path),
        str(report_path),
    )


def _run_core_tests(*, repo_root: Path, python: str) -> StepResult:
    cmd = [
        python,
        "-m",
        "pytest",
        "-q",
    ]
    completed = _run(cmd, cwd=repo_root)
    detail = _last_nonempty_line(completed.stdout) or _stderr_or_stdout(completed)
    status = "PASS" if completed.returncode == 0 else "FAIL"
    return StepResult(
        "full local pytest suite",
        status,
        f"{detail}; optional extras are represented as pytest skips when unavailable",
        cmd,
        completed.returncode,
    )


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=False, text=True, capture_output=True)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _overall_status(steps: list[StepResult]) -> str:
    return "FAIL" if any(step.status == "FAIL" for step in steps) else "PASS"


def _failure_detail(completed: subprocess.CompletedProcess[str], data: dict[str, Any]) -> str:
    failures = [
        f"{item.get('name')}: {item.get('detail')}"
        for item in data.get("checks", [])
        if item.get("status") != "PASS"
    ]
    if failures:
        return "; ".join(failures[:5])
    if data.get("_missing") or data.get("_parse_error"):
        return str(data.get("_missing") or data.get("_parse_error"))
    return _stderr_or_stdout(completed)


def _worst_metric_error(metrics: dict[str, Any]) -> str:
    errors: list[tuple[str, float]] = []
    for name, item in metrics.items():
        if isinstance(item, dict) and "max_percent_error" in item:
            try:
                errors.append((name, float(item["max_percent_error"])))
            except (TypeError, ValueError):
                continue
    if not errors:
        return "n/a"
    metric, value = max(errors, key=lambda item: item[1])
    return f"{metric}:{value:.4g}%"


def _worst_formula_error(metrics: dict[str, Any]) -> str:
    errors: list[tuple[str, float]] = []
    for name, item in metrics.items():
        if isinstance(item, dict) and "max_percent_error" in item:
            try:
                errors.append((name, float(item["max_percent_error"])))
            except (TypeError, ValueError):
                continue
    if not errors:
        return "n/a"
    metric, value = max(errors, key=lambda item: item[1])
    return f"{metric}:{value:.4g}%"


def _validation_chain_detail(data: dict[str, Any]) -> tuple[bool, str]:
    if data.get("_missing") or data.get("_parse_error"):
        return False, str(data.get("_missing") or data.get("_parse_error"))
    stages = {str(stage.get("name")): stage for stage in data.get("stages", []) if isinstance(stage, dict)}
    required = {
        "EMX-first golden reference",
        "HFSS geometry asset traceability",
        "HFSS physical S4P gate",
        "Accepted EMX-vs-HFSS/ADS comparison",
    }
    missing = sorted(required - set(stages))
    if missing:
        return False, f"missing validation stages={missing}"

    overall_status = str(data.get("overall_status"))
    decision = str(data.get("decision"))
    emx_status = str(stages["EMX-first golden reference"].get("status"))
    geometry_status = str(stages["HFSS geometry asset traceability"].get("status"))
    hfss_status = str(stages["HFSS physical S4P gate"].get("status"))
    comparison_status = str(stages["Accepted EMX-vs-HFSS/ADS comparison"].get("status"))
    comparison_decision = str(stages["Accepted EMX-vs-HFSS/ADS comparison"].get("decision"))
    detail = (
        f"overall_status={overall_status}, decision={decision}; "
        f"EMX-first={emx_status}, HFSS-geometry={geometry_status}, HFSS-physical={hfss_status}, "
        f"comparison={comparison_status}"
    )

    if overall_status == "PASS" and decision == "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN":
        if emx_status == geometry_status == hfss_status == comparison_status == "PASS":
            return True, f"{detail}; full chain accepted"
        return False, f"{detail}; full-chain decision without all stages PASS"

    if overall_status == "BLOCKED_BY_EMX_REFERENCE" and decision == "DO_NOT_USE_HFSS_COMPARISON":
        if emx_status in {"FAIL", "MISSING"} and comparison_status == "BLOCKED_BY_EMX_REFERENCE":
            return True, f"{detail}; HFSS comparison is correctly blocked until EMX-first passes"
        return False, f"{detail}; EMX block is inconsistent"

    if overall_status == "BLOCKED_BY_HFSS_GEOMETRY_GATE" and decision == "DO_NOT_USE_HFSS_COMPARISON":
        if emx_status == "PASS" and geometry_status != "PASS":
            return True, f"{detail}; final comparison is correctly blocked by HFSS geometry traceability gate"
        return False, f"{detail}; HFSS geometry block is inconsistent"

    if overall_status == "BLOCKED_BY_HFSS_PHYSICAL_GATE" and decision == "DO_NOT_USE_HFSS_COMPARISON":
        if emx_status == "PASS" and geometry_status == "PASS" and hfss_status != "PASS":
            return True, f"{detail}; final comparison is correctly blocked by HFSS physical gate"
        return False, f"{detail}; HFSS block is inconsistent"

    if overall_status == "INCOMPLETE" and decision == "WAIT_FOR_ACCEPTED_COMPARISON":
        if (
            emx_status == "PASS"
            and geometry_status == "PASS"
            and hfss_status == "PASS"
            and comparison_decision != "ACCEPT_HFSS_VALIDATION_SAMPLE"
        ):
            return True, f"{detail}; waiting for accepted <=5% EMX-vs-HFSS/ADS comparison"
        return False, f"{detail}; incomplete chain is inconsistent"

    return False, f"{detail}; unexpected validation-chain status"


def _stderr_or_stdout(completed: subprocess.CompletedProcess[str]) -> str:
    text = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
    return text[-800:] if len(text) > 800 else text


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Local Project Health Check",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Generated UTC: `{summary['generated_utc']}`",
        f"- Package: `{summary['package_dir']}`",
        "",
        "| Status | Step | Detail |",
        "| --- | --- | --- |",
    ]
    for step in summary["steps"]:
        lines.append(f"| {step['status']} | {step['name']} | {step['detail']} |")
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- This check verifies local artifacts and code gates only.",
            "- It does not claim MARS final-500, MARS wideband 500, 248k production, HFSS, ADS, or EMX completion.",
            "- `INCOMPLETE` in the acceptance matrix is expected until external simulator evidence exists.",
            "- HFSS-vs-EMX comparison figures are reportable only after EMX-first, HFSS physical, and final <=5% ADS-style comparison gates pass.",
            "- The MARS next-action packet records the next safe MARS action and keeps current EMX/HFSS figures out of final evidence until accepted.",
            "- The MARS target EMX return watcher records local file-return state; `WAITING_FOR_MARS_RETURN` is not an accepted EMX reference.",
            "- ADS metric formula consistency is a synthetic formula audit, not simulator evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
