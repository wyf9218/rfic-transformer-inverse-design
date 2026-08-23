#!/usr/bin/env python3
"""Import a MARS Stage-1 EMX calibration return and compare local HFSS cases.

This script is intentionally local-only. It never launches EMX, HFSS, ADS, or
Cadence; it only unpacks a returned Stage-1 tarball, locates EMX .s2p files,
and runs the existing straight-line R/L/C comparison against local HFSS .s2p
results.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parent


@dataclass(frozen=True)
class HfssCase:
    label: str
    packet_dir: Path
    hfss_results_root: Path


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return_tar = Path(args.return_tar).expanduser().resolve()
    if not return_tar.is_file():
        raise SystemExit(f"Stage-1 return tarball not found: {return_tar}")

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else _default_out_dir(return_tar)
    unpack_dir = out_dir / "unpacked"
    if out_dir.exists() and args.force:
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    unpack_dir.mkdir(parents=True, exist_ok=True)

    _safe_extract(return_tar, unpack_dir)
    return_manifest = _load_first_json(unpack_dir, "*manifest*.json")
    packet_dir = _resolve_packet_dir(args.packet_dir, unpack_dir)
    emx_results_root = _resolve_emx_results_root(args.emx_results_root, unpack_dir)
    cases = _resolve_hfss_cases(args.hfss_case)

    rows: list[dict[str, Any]] = []
    for case in cases:
        case_out = out_dir / case.label
        cmd = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "summarize_stage1_calibration_results.py"),
            "--packet-dir",
            str(case.packet_dir),
            "--emx-results-root",
            str(emx_results_root),
            "--hfss-results-root",
            str(case.hfss_results_root),
            "--out-dir",
            str(case_out),
            "--target-ghz",
            str(args.target_ghz),
            "--max-percent-error",
            str(args.max_percent_error),
            "--target-frequency-tolerance-ghz",
            str(args.target_frequency_tolerance_ghz),
            "--no-fail-exit",
        ]
        if args.require_matching_frequency_grid:
            cmd.append("--require-matching-frequency-grid")
        run = subprocess.run(cmd, check=False, capture_output=True, text=True)
        summary_path = case_out / "stage1_calibration_summary.json"
        summary: dict[str, Any] | None = None
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        rows.append(
            {
                "label": case.label,
                "return_code": run.returncode,
                "packet_dir": str(case.packet_dir),
                "hfss_results_root": str(case.hfss_results_root),
                "summary_path": str(summary_path) if summary_path.is_file() else "",
                "overall_status": "" if summary is None else summary.get("overall_status", ""),
                "status_counts": {} if summary is None else summary.get("status_counts", {}),
                "stdout_tail": _tail(run.stdout),
                "stderr_tail": _tail(run.stderr),
            }
        )

    payload = {
        "schema": "rfic_transformer_stage1_mars_return_import.v1",
        "overall_status": _overall_status(rows),
        "return_tar": str(return_tar),
        "out_dir": str(out_dir),
        "unpacked_dir": str(unpack_dir),
        "return_manifest": return_manifest,
        "packet_dir_from_return": str(packet_dir),
        "emx_results_root": str(emx_results_root),
        "target_ghz": args.target_ghz,
        "max_percent_error": args.max_percent_error,
        "require_matching_frequency_grid": bool(args.require_matching_frequency_grid),
        "hfss_case_count": len(cases),
        "rows": rows,
    }
    summary_out = out_dir / "stage1_mars_return_import_summary.json"
    report_out = out_dir / "STAGE1_MARS_RETURN_IMPORT_REPORT_CN.md"
    summary_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report_out.write_text(_render_report(payload), encoding="utf-8")

    print(f"overall_status={payload['overall_status']}")
    print(f"summary={summary_out}")
    print(f"report={report_out}")
    return 2 if payload["overall_status"] == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("return_tar", help="MARS Stage-1 return tarball, e.g. stage1_emx_calibration_wideband_latest.tar.gz")
    parser.add_argument("--out-dir")
    parser.add_argument("--packet-dir", help="Override calibration execution packet directory")
    parser.add_argument("--emx-results-root", help="Override unpacked EMX results root")
    parser.add_argument(
        "--hfss-case",
        action="append",
        default=[],
        help=(
            "HFSS comparison case. Use either /path/to/case_dir containing "
            "calibration_execution_summary.json and windows_results, or "
            "label=/packet_dir:/hfss_results_root"
        ),
    )
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--target-frequency-tolerance-ghz", type=float, default=0.05)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--require-matching-frequency-grid", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _default_out_dir(return_tar: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "reports" / "s8p_shared_line_width_mars_evidence_20260622" / f"stage1_mars_return_import_{stamp}"


def _safe_extract(tar_path: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if dest_resolved not in target.parents and target != dest_resolved:
                raise SystemExit(f"Refusing unsafe tar member outside destination: {member.name}")
        tar.extractall(dest, filter="data")


def _load_first_json(root: Path, pattern: str) -> dict[str, Any] | None:
    for path in sorted(root.rglob(pattern)):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _resolve_packet_dir(override: str | None, unpack_dir: Path) -> Path:
    if override:
        packet = Path(override).expanduser().resolve()
        if not (packet / "calibration_execution_summary.json").is_file():
            raise SystemExit(f"Missing calibration_execution_summary.json in packet dir: {packet}")
        return packet
    candidates = sorted(path.parent for path in unpack_dir.rglob("calibration_execution_summary.json"))
    for candidate in candidates:
        if candidate.name.startswith("calibration_execution_packet_stage1"):
            return candidate
    if candidates:
        return candidates[0]
    fallback = PROJECT_ROOT / "reports" / "s8p_shared_line_width_mars_evidence_20260622" / "calibration_execution_packet_stage1_wideband_20260626"
    if (fallback / "calibration_execution_summary.json").is_file():
        return fallback
    raise SystemExit("Could not locate calibration_execution_summary.json in return tar or local fallback")


def _resolve_emx_results_root(override: str | None, unpack_dir: Path) -> Path:
    if override:
        root = Path(override).expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"EMX results root not found: {root}")
        return root
    # The summarizer can glob recursively, so the unpack root is the safest
    # default for both MARS-packaged and hand-collected return tars.
    if list(unpack_dir.rglob("*.s2p")):
        return unpack_dir
    raise SystemExit(f"No .s2p files found under unpacked Stage-1 return: {unpack_dir}")


def _resolve_hfss_cases(values: list[str]) -> list[HfssCase]:
    if not values:
        values = [
            "/home/researcher/Documents/hfss_calibration_stage1_20260626",
            "/home/researcher/Documents/hfss_calibration_stage1_m5_united_20260626",
        ]
    cases: list[HfssCase] = []
    for value in values:
        case = _parse_hfss_case(value)
        if case is not None:
            cases.append(case)
    return cases


def _parse_hfss_case(value: str) -> HfssCase | None:
    if "=" in value and ":" in value.split("=", 1)[1]:
        label, rest = value.split("=", 1)
        packet_text, hfss_text = rest.split(":", 1)
        packet = Path(packet_text).expanduser().resolve()
        hfss = Path(hfss_text).expanduser().resolve()
        if not (packet / "calibration_execution_summary.json").is_file():
            raise SystemExit(f"HFSS case {label}: missing packet summary in {packet}")
        if not hfss.is_dir():
            raise SystemExit(f"HFSS case {label}: missing results root {hfss}")
        return HfssCase(label=_slug(label), packet_dir=packet, hfss_results_root=hfss)

    root = Path(value).expanduser().resolve()
    packet = root
    hfss = root / "windows_results"
    if not (packet / "calibration_execution_summary.json").is_file() or not hfss.is_dir():
        return None
    return HfssCase(label=_slug(root.name), packet_dir=packet, hfss_results_root=hfss)


def _overall_status(rows: list[dict[str, Any]]) -> str:
    statuses = {str(row.get("overall_status", "")) for row in rows}
    if "PASS" in statuses:
        return "PASS"
    if "FAIL" in statuses:
        return "FAIL"
    return "INCOMPLETE"


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Stage1 MARS Return Import Report",
        "",
        f"- Overall status: **{payload['overall_status']}**",
        f"- Return tar: `{payload['return_tar']}`",
        f"- EMX root: `{payload['emx_results_root']}`",
        f"- Gate: <= {payload['max_percent_error']}% at {payload['target_ghz']} GHz",
        f"- Require matching frequency grid: `{payload['require_matching_frequency_grid']}`",
        "",
        "| HFSS case | Status | Counts | Summary |",
        "|---|---|---|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| `{row['label']}` | {row['overall_status'] or 'NO_SUMMARY'} | `{row['status_counts']}` | `{row['summary_path']}` |"
        )
    lines.extend(
        [
            "",
            "判读：",
            "",
            "- `PASS` 只能说明 Stage1 直线结构的 EMX/HFSS R/L/C 在 gate 内，不能替代最终 8-port transformer `Lp/Ls/Q/Kw` 验证。",
            "- 如果所有 case 都是 `MISSING` 或 frequency-grid failure，先补齐同频率规格的 HFSS Stage1 `.s2p`，再进入完整变压器对比。",
            "- 只有 Stage1 通过后，才把完整 EMX `.s8p` 和 HFSS `.s8p` 放进最终 10% gate。",
            "",
        ]
    )
    return "\n".join(lines)


def _tail(text: str, limit: int = 2000) -> str:
    text = text.strip()
    return text[-limit:] if len(text) > limit else text


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("_") or "hfss_case"


if __name__ == "__main__":
    raise SystemExit(main())
