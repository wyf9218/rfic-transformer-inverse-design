#!/usr/bin/env python3
"""Watch for a MARS S8P 20-pilot return package and import it.

This helper is intentionally local-side only.  It does not run EMX, HFSS, ADS,
Guacamole, or SSH.  It waits for the real MARS return tarball to appear, then
delegates to import_latest_s8p_20_pilot_return.py so the 20-sample contract is
used instead of the production/default 500-sample contract.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "watch_s8p_20_pilot_return_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, float(args.timeout_seconds))
    attempts: list[dict[str, Any]] = []
    selected_tarball: Path | None = None

    while True:
        selected_tarball = _find_return_tarball(args)
        attempts.append(
            {
                "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "found": selected_tarball is not None,
                "tarball": "" if selected_tarball is None else str(selected_tarball),
            }
        )
        if selected_tarball is not None:
            break
        if float(args.timeout_seconds) <= 0.0 or time.monotonic() >= deadline:
            break
        time.sleep(max(1.0, float(args.poll_seconds)))

    import_result: dict[str, Any] | None = None
    if selected_tarball is not None:
        import_result = _run_import(selected_tarball, out_dir, args)

    overall_status, decision = _decision(selected_tarball, import_result)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "selected_tarball": "" if selected_tarball is None else str(selected_tarball),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "import_result": import_result,
        "arguments": {
            "timeout_seconds": float(args.timeout_seconds),
            "poll_seconds": float(args.poll_seconds),
            "run_after_import": bool(args.run_after_import),
            "hfss_results_dir": "" if not args.hfss_results_dir else str(Path(args.hfss_results_dir).expanduser()),
            "search_roots": [str(path) for path in _search_roots(args)],
        },
        "method_notes": [
            "This watcher only waits for a real MARS return package and imports it.",
            "PASS means the imported EMX-HFSS postrun comparison passed, not merely that a tarball was found.",
            "WAITING means no real return package was found in the configured roots before timeout.",
        ],
    }
    summary_path = out_dir / "watch_s8p_20_pilot_return_summary.json"
    report_path = out_dir / "WATCH_S8P_20_PILOT_RETURN_REPORT_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status in {"WAITING_FOR_RETURN", "READY_FOR_HFSS", "WAITING_FOR_HFSS_EXPORT", "PASS"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--return-tarball", help="Explicit return tarball path; skips directory search")
    parser.add_argument("--search-root", action="append", help="Directory to search; may be repeated")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--timeout-seconds", type=float, default=0.0, help="0 means one check only")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--run-after-import", action="store_true")
    parser.add_argument("--hfss-results-dir", help="Directory containing exported HFSS .s8p files")
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _search_roots(args: argparse.Namespace) -> list[Path]:
    if args.search_root:
        return [Path(item).expanduser().resolve() for item in args.search_root]
    return [
        PROJECT_ROOT,
        PROJECT_ROOT / "outputs",
        Path.home() / "Downloads",
        Path.home() / "Desktop",
    ]


def _find_return_tarball(args: argparse.Namespace) -> Path | None:
    if args.return_tarball:
        path = Path(args.return_tarball).expanduser().resolve()
        return path if path.is_file() else None
    candidates: list[Path] = []
    for root in _search_roots(args):
        if not root.exists():
            continue
        for pattern in ("next_gen_s8p_mars_return_latest.tar.gz", "next_gen_s8p_mars_return_20_after_unlock_*.tar.gz"):
            candidates.extend(path for path in root.rglob(pattern) if path.is_file())
    if not candidates:
        return None
    candidates.sort(key=lambda path: (path.stat().st_mtime, str(path)), reverse=True)
    return candidates[0].resolve()


def _run_import(tarball: Path, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    import_out = out_dir / "latest_return_import"
    command = [
        str(Path(args.python).expanduser()),
        str(SCRIPT_DIR / "import_latest_s8p_20_pilot_return.py"),
        "--return-tarball",
        str(tarball),
        "--out-dir",
        str(import_out),
        "--max-percent-error",
        f"{float(args.max_percent_error):g}",
        "--no-fail-exit",
    ]
    if args.run_after_import:
        command.append("--run-after-import")
    if args.hfss_results_dir:
        command.extend(["--hfss-results-dir", str(Path(args.hfss_results_dir).expanduser().resolve())])
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    summary_path = import_out / "latest_s8p_20_pilot_return_import_summary.json"
    summary = _read_json(summary_path)
    return {
        "returncode": int(completed.returncode),
        "command": command,
        "stdout_tail": (completed.stdout or "")[-4000:],
        "stderr_tail": (completed.stderr or "")[-4000:],
        "summary_path": str(summary_path),
        "summary": summary,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _decision(tarball: Path | None, import_result: dict[str, Any] | None) -> tuple[str, str]:
    if tarball is None:
        return "WAITING_FOR_RETURN", "WAIT_FOR_MARS_20_PILOT_RETURN"
    if import_result is None:
        return "FAIL", "RETURN_FOUND_BUT_IMPORT_NOT_RUN"
    if int(import_result.get("returncode") or 0) != 0:
        return "FAIL", "FIX_20_PILOT_IMPORT_FAILURE"
    import_summary = import_result.get("summary") if isinstance(import_result.get("summary"), dict) else {}
    status = str(import_summary.get("overall_status") or "")
    if status == "PASS":
        return "PASS", "ACCEPT_EMX_HFSS_S8P_20_PILOT_VALIDATION"
    if status in {"READY_FOR_HFSS", "WAITING_FOR_HFSS_EXPORT"}:
        return status, str(import_summary.get("decision") or "CONTINUE_HFSS_EXPORT_AND_POSTRUN_VALIDATION")
    if status == "WAITING_FOR_RETURN":
        return "WAITING_FOR_RETURN", "RETURN_DISAPPEARED_OR_IMPORT_COULD_NOT_OPEN_TARBALL"
    return "FAIL", f"UNEXPECTED_IMPORT_STATUS_{status or 'MISSING'}"


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Watch S8P 20-Pilot Return",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Selected tarball: `{summary.get('selected_tarball')}`",
        f"- Attempts: `{summary.get('attempt_count')}`",
        "",
    ]
    if summary["overall_status"] == "WAITING_FOR_RETURN":
        lines.append("尚未发现 MARS 20-pilot return 包；等待 `next_gen_s8p_mars_return_latest.tar.gz`。")
    elif summary["overall_status"] == "READY_FOR_HFSS":
        lines.append("已导入 return 包；下一步运行 after-import gates，并准备 HFSS `.s8p` 导出。")
    elif summary["overall_status"] == "WAITING_FOR_HFSS_EXPORT":
        lines.append("已进入 HFSS 后处理等待/修复阶段；需要同规格 HFSS `.s8p`。")
    elif summary["overall_status"] == "PASS":
        lines.append("EMX-HFSS S8P 20-pilot validation 已通过。")
    else:
        lines.append("导入或验证失败；查看 JSON summary 中的 import_result。")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
