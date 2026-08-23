#!/usr/bin/env python3
"""Build a concise handoff index for the current next-gen S8P state.

The index answers three practical questions without running EMX/HFSS:

1. Which local file should be uploaded to MARS now?
2. Which command should be run on MARS and after the MARS return download?
3. Which evidence still prevents claiming the full objective is complete?
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = Path(args.project_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "upload_bundle": _resolve(project_root, args.upload_bundle),
        "upload_bundle_sha": _resolve(project_root, args.upload_bundle_sha),
        "bundle_audit_summary": _resolve(project_root, args.bundle_audit_summary),
        "start_current_script": _resolve(project_root, args.start_current_script),
        "after_return_script": _resolve(project_root, args.after_return_script),
        "run_commands": _resolve(project_root, args.run_commands),
        "objective_audit_summary": _resolve(project_root, args.objective_audit_summary),
    }

    checks = _checks(paths)
    ready_to_upload = all(
        item["status"] == "PASS"
        for item in checks
        if item["name"]
        in {
            "upload bundle exists",
            "upload bundle SHA sidecar exists",
            "upload bundle SHA sidecar matches",
            "bundle audit passes",
            "start-current script exists/executable",
            "after-return script exists/executable",
            "run commands document start-current",
            "run commands document after-return",
        }
    )
    objective = _read_json(paths["objective_audit_summary"])
    objective_status = objective.get("overall_status") if isinstance(objective, dict) else None
    objective_decision = objective.get("decision") if isinstance(objective, dict) else None

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ready_to_upload_to_mars": ready_to_upload,
        "objective_complete": objective_status == "PASS",
        "objective_status": objective_status,
        "objective_decision": objective_decision,
        "paths": {key: str(value) for key, value in paths.items()},
        "commands": {
            "mars_upload_bundle": paths["upload_bundle"].name,
            "mars_unpack_and_start": [
                "WORK_DIR=/shared/research/researcher/codex_next_gen_s8p_ssh_20260620",
                'mkdir -p "$WORK_DIR"',
                'cd "$WORK_DIR"',
                f"tar -xzf {paths['upload_bundle'].name}",
                "shasum -a 256 -c SHA256SUMS.txt 2>/dev/null || sha256sum -c SHA256SUMS.txt",
                "bash NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh",
            ],
            "local_after_return": "bash NEXT_GEN_S8P_AFTER_MARS_RETURN_AUTOPROCESS_20260620.sh /path/to/next_gen_s8p_mars_return_latest.tar.gz",
        },
        "checks": checks,
        "remaining_external_work": [
            "Run the real 500-row / 8-worker EMX job on MARS.",
            "Download and import the MARS return package.",
            "Run the generated local after-return next-steps chain.",
            "Run/collect selected-sample HFSS .s8p export.",
            "Pass EMX/HFSS Lp/Ls/Q/K/Kw comparison before claiming completion.",
        ],
    }

    summary_path = out_dir / "next_gen_s8p_handoff_index.json"
    report_path = out_dir / "NEXT_GEN_S8P_HANDOFF_INDEX.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"ready_to_upload_to_mars={ready_to_upload}")
    print(f"objective_status={objective_status}")
    print(f"objective_decision={objective_decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if ready_to_upload or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--upload-bundle", default="next_gen_s8p_mars_start_current_upload_bundle_20260620.tar.gz")
    parser.add_argument("--upload-bundle-sha", default="next_gen_s8p_mars_start_current_upload_bundle_20260620.tar.gz.sha256")
    parser.add_argument("--bundle-audit-summary", default="outputs/mars_start_upload_bundle_audit_current/mars_start_upload_bundle_audit_summary.json")
    parser.add_argument("--start-current-script", default="NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh")
    parser.add_argument("--after-return-script", default="NEXT_GEN_S8P_AFTER_MARS_RETURN_AUTOPROCESS_20260620.sh")
    parser.add_argument("--run-commands", default="NEXT_GEN_S8P_MARS_20260620_RUN_COMMANDS_CN.md")
    parser.add_argument(
        "--objective-audit-summary",
        default="outputs/next_gen_s8p_objective_acceptance_aligned_current_20260620/next_gen_s8p_objective_acceptance_summary.json",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_PROJECT_ROOT / "outputs" / "next_gen_s8p_handoff_index_current"))
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _checks(paths: dict[str, Path]) -> list[dict[str, str]]:
    checks = []
    upload = paths["upload_bundle"]
    upload_sha = paths["upload_bundle_sha"]
    checks.append(_check("upload bundle exists", upload.is_file(), str(upload)))
    checks.append(_check("upload bundle SHA sidecar exists", upload_sha.is_file(), str(upload_sha)))
    checks.append(
        _check(
            "upload bundle SHA sidecar matches",
            upload.is_file() and upload_sha.is_file() and _sha256(upload) == _sidecar_digest(upload_sha),
            f"actual={_sha256(upload) if upload.is_file() else None}, sidecar={_sidecar_digest(upload_sha) if upload_sha.is_file() else None}",
        )
    )
    bundle_audit = _read_json(paths["bundle_audit_summary"])
    checks.append(
        _check(
            "bundle audit passes",
            isinstance(bundle_audit, dict)
            and bundle_audit.get("overall_status") == "PASS"
            and bundle_audit.get("decision") == "MARS_START_UPLOAD_BUNDLE_READY",
            f"status={bundle_audit.get('overall_status') if isinstance(bundle_audit, dict) else None}, decision={bundle_audit.get('decision') if isinstance(bundle_audit, dict) else None}",
        )
    )
    checks.append(_check("start-current script exists/executable", _is_executable(paths["start_current_script"]), str(paths["start_current_script"])))
    checks.append(_check("after-return script exists/executable", _is_executable(paths["after_return_script"]), str(paths["after_return_script"])))
    run_text = paths["run_commands"].read_text(encoding="utf-8") if paths["run_commands"].is_file() else ""
    checks.append(_check("run commands document start-current", "NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh" in run_text, str(paths["run_commands"])))
    checks.append(_check("run commands document after-return", "NEXT_GEN_S8P_AFTER_MARS_RETURN_AUTOPROCESS_20260620.sh" in run_text, str(paths["run_commands"])))
    objective = _read_json(paths["objective_audit_summary"])
    checks.append(
        _check(
            "objective audit remains non-complete",
            isinstance(objective, dict) and objective.get("overall_status") == "WAITING",
            f"status={objective.get('overall_status') if isinstance(objective, dict) else None}, decision={objective.get('decision') if isinstance(objective, dict) else None}",
        )
    )
    return checks


def _resolve(project_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sidecar_digest(path: Path) -> str | None:
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            parts = raw.split()
            if parts:
                return parts[0]
    except OSError:
        return None
    return None


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _check(name: str, ok: bool, detail: str) -> dict[str, str]:
    return {"status": "PASS" if ok else "FAIL", "name": name, "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Next-Gen S8P Handoff Index",
        "",
        f"- ready_to_upload_to_mars: `{summary['ready_to_upload_to_mars']}`",
        f"- objective_complete: `{summary['objective_complete']}`",
        f"- objective_status: `{summary['objective_status']}`",
        f"- objective_decision: `{summary['objective_decision']}`",
        "",
        "## MARS Command",
        "",
        "```bash",
        *summary["commands"]["mars_unpack_and_start"],
        "```",
        "",
        "## Local After Return",
        "",
        f"```bash\n{summary['commands']['local_after_return']}\n```",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "|---|---|---|",
    ]
    for item in summary["checks"]:
        lines.append(f"| {item['status']} | {item['name']} | {item['detail']} |")
    lines.extend(["", "## Remaining External Work", ""])
    lines.extend(f"- {item}" for item in summary["remaining_external_work"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
