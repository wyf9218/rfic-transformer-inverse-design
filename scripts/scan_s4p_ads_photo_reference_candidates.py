#!/usr/bin/env python3
"""Scan local S4P files for candidates matching the user-provided ADS photo."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for item in (REPO_ROOT, SCRIPT_DIR):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from audit_ads_photo_reference_alignment import REFERENCE_METRICS, _metric_check  # noqa: E402
from plot_emx_hfss_ads_style_metrics import DEFAULT_PACKAGE_DIR, _extract_metric_curves  # noqa: E402


DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_SEARCH_ROOTS = (
    DEFAULT_PACKAGE_DIR,
    DEFAULT_PROJECT_ROOT / "hfss_validation",
    Path("/home/researcher/Desktop"),
    Path("/home/researcher/Downloads"),
)
SKIP_DIR_NAMES = {".git", ".venv", "__pycache__", "__MACOSX", "node_modules"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-root", action="append", default=None, help="Root directory to scan; can be supplied multiple times")
    parser.add_argument("--out-dir", default=str(DEFAULT_PACKAGE_DIR / "ads_photo_reference_candidate_scan_20260613"))
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-frequency-distance-ghz", type=float, default=0.05)
    parser.add_argument("--max-percent-error", type=float, default=None, help="Override all per-metric percent-error tolerances")
    parser.add_argument("--top-n", type=int, default=25)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    roots = [Path(root).expanduser().resolve() for root in (args.search_root or DEFAULT_SEARCH_ROOTS)]
    candidates = discover_s4p_files(roots)
    records = [_candidate_record(path, args) for path in candidates]
    records_sorted = sorted(records, key=_ranking_key)
    top_records = records_sorted[: max(int(args.top_n), 1)]
    emx_matches = [row for row in records if row.get("status") == "PASS" and row.get("source_kind") == "EMX"]
    non_emx_matches = [row for row in records if row.get("status") == "PASS" and row.get("source_kind") != "EMX"]
    error_count = sum(1 for row in records if row.get("status") == "ERROR")
    overall_status = _overall_status(records, emx_matches, non_emx_matches)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "target_ghz": float(args.target_ghz),
        "port_pairs": args.port_pairs,
        "max_frequency_distance_ghz": float(args.max_frequency_distance_ghz),
        "search_roots": [str(root) for root in roots],
        "reference_source": "user-provided ADS correct-curve photo, values transcribed from visible 15 GHz markers",
        "counts": {
            "candidate_files": len(candidates),
            "evaluated": len(records),
            "pass_emx": len(emx_matches),
            "pass_non_emx": len(non_emx_matches),
            "errors": error_count,
        },
        "best": top_records[0] if top_records else None,
        "best_emx": next((row for row in records_sorted if row.get("source_kind") == "EMX"), None),
        "top_candidates": top_records,
        "notes": [
            "PASS is intentionally strict: an EMX-labeled S4P must pass every visible 15 GHz ADS-photo metric within the configured tolerance and have a frequency point close to the target.",
            "NO_MATCH means no scanned EMX S4P is safe to treat as the correct ADS reference source.",
            "This scan ranks local candidates; it does not replace ADS GUI verification of the selected file.",
        ],
    }
    summary_path = out_dir / "ads_photo_reference_candidate_scan_summary.json"
    report_path = out_dir / "ads_photo_reference_candidate_scan_report.md"
    csv_path = out_dir / "ads_photo_reference_candidate_scan.csv"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_csv(csv_path, records_sorted)

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"csv={csv_path}")
    print(f"candidate_files={len(candidates)} pass_emx={len(emx_matches)} pass_non_emx={len(non_emx_matches)} errors={error_count}")
    best = summary.get("best")
    if best:
        print(f"best={best.get('status')} {best.get('source_kind')} max_error={best.get('max_percent_error')} path={best.get('touchstone')}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def discover_s4p_files(roots: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file() and root.suffix.lower() == ".s4p":
            resolved = root.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(resolved)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() != ".s4p":
                continue
            rel_parts = path.relative_to(root).parts
            if any(part in SKIP_DIR_NAMES or part.startswith("._") for part in rel_parts):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(resolved)
    return sorted(files, key=lambda item: item.as_posix())


def _candidate_record(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    source_kind = _source_kind(path)
    try:
        curves = _extract_metric_curves(path.stem, path, args.port_pairs)
        freq_ghz = curves.freq_hz / 1.0e9
        idx = int(np.argmin(np.abs(freq_ghz - float(args.target_ghz))))
        nearest = float(freq_ghz[idx])
        freq_distance = abs(nearest - float(args.target_ghz))
        actuals = {
            "lp_nh": float(curves.lp_nh[idx]),
            "ls_nh": float(curves.ls_nh[idx]),
            "k": float(curves.k[idx]),
            "qp": float(curves.qp[idx]),
            "qs": float(curves.qs[idx]),
            "cm_single_primary_ff": float(curves.cm_single_primary_ff[idx]),
        }
        if not all(math.isfinite(value) for value in actuals.values()):
            raise ValueError("non-finite metric extracted at target frequency")
        checks = [_metric_check(spec, actuals[spec.key], args.max_percent_error) for spec in REFERENCE_METRICS]
        metric_fail_count = sum(1 for check in checks if check["status"] == "FAIL")
        max_error = max(float(check["percent_error"]) for check in checks)
        mean_error = sum(float(check["percent_error"]) for check in checks) / len(checks)
        frequency_status = "PASS" if freq_distance <= float(args.max_frequency_distance_ghz) else "FAIL"
        status = "PASS" if metric_fail_count == 0 and frequency_status == "PASS" else "FAIL"
        return {
            "status": status,
            "source_kind": source_kind,
            "touchstone": str(path),
            "nearest_frequency_ghz": nearest,
            "frequency_distance_ghz": float(freq_distance),
            "frequency_status": frequency_status,
            "metric_fail_count": metric_fail_count,
            "max_percent_error": float(max_error),
            "mean_percent_error": float(mean_error),
            "actuals": actuals,
            "checks": checks,
        }
    except Exception as exc:  # noqa: BLE001 - scanner should keep evaluating other files.
        return {
            "status": "ERROR",
            "source_kind": source_kind,
            "touchstone": str(path),
            "error": f"{type(exc).__name__}: {exc}",
            "metric_fail_count": len(REFERENCE_METRICS),
            "max_percent_error": float("inf"),
            "mean_percent_error": float("inf"),
        }


def _source_kind(path: Path) -> str:
    tokens = [token for part in path.parts for token in re.split(r"[^a-z0-9]+", part.lower()) if token]
    if any(token == "emx" or token.startswith("emx") for token in tokens):
        return "EMX"
    if any("hfss" in token or "aedtresults" in token for token in tokens):
        return "HFSS"
    if any(token == "ads" or token.startswith("ads") for token in tokens):
        return "ADS"
    header_kind = _source_kind_from_header(path)
    if header_kind:
        return header_kind
    return "UNKNOWN"


def _source_kind_from_header(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_bytes()[:8192].decode("utf-8", errors="ignore").lower()
    except OSError:
        return None
    if "exported from hfss" in text or "ansys" in text or ".aedt" in text or "hfssdesign" in text:
        return "HFSS"
    if "emx" in text:
        return "EMX"
    if "advanced design system" in text or "keysight" in text:
        return "ADS"
    return None


def _overall_status(records: list[dict[str, Any]], emx_matches: list[dict[str, Any]], non_emx_matches: list[dict[str, Any]]) -> str:
    if not records:
        return "NO_CANDIDATES"
    if emx_matches:
        return "PASS"
    if non_emx_matches:
        return "REVIEW_REQUIRED"
    return "NO_MATCH"


def _ranking_key(row: dict[str, Any]) -> tuple[int, int, float, float, str]:
    status_rank = {"PASS": 0, "FAIL": 1, "ERROR": 2}.get(str(row.get("status")), 3)
    kind_rank = {"EMX": 0, "HFSS": 1, "ADS": 2, "UNKNOWN": 3}.get(str(row.get("source_kind")), 4)
    max_error = float(row.get("max_percent_error", float("inf")))
    freq_distance = float(row.get("frequency_distance_ghz", float("inf")))
    return (status_rank, kind_rank, max_error, freq_distance, str(row.get("touchstone", "")))


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "status",
        "source_kind",
        "touchstone",
        "nearest_frequency_ghz",
        "frequency_distance_ghz",
        "frequency_status",
        "metric_fail_count",
        "max_percent_error",
        "mean_percent_error",
        "lp_nh",
        "ls_nh",
        "k",
        "qp",
        "qs",
        "cm_single_primary_ff",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in records:
            actuals = row.get("actuals", {})
            writer.writerow(
                {
                    **{field: row.get(field, "") for field in fields},
                    "lp_nh": actuals.get("lp_nh", ""),
                    "ls_nh": actuals.get("ls_nh", ""),
                    "k": actuals.get("k", ""),
                    "qp": actuals.get("qp", ""),
                    "qs": actuals.get("qs", ""),
                    "cm_single_primary_ff": actuals.get("cm_single_primary_ff", ""),
                }
            )


def _render_report(summary: dict[str, Any]) -> str:
    counts = summary.get("counts", {})
    lines = [
        "# ADS Photo Reference Candidate Scan",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Target frequency: `{summary['target_ghz']} GHz`",
        f"- Port pairs: `{summary['port_pairs']}`",
        f"- Candidate files: `{counts.get('candidate_files', 0)}`",
        f"- EMX PASS candidates: `{counts.get('pass_emx', 0)}`",
        f"- Non-EMX PASS candidates: `{counts.get('pass_non_emx', 0)}`",
        f"- Errors: `{counts.get('errors', 0)}`",
        "",
        "## Top Candidates",
        "",
        "| Rank | Status | Kind | Max Error | Mean Error | Nearest GHz | Path |",
        "| ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for rank, row in enumerate(summary.get("top_candidates", []), start=1):
        max_error = row.get("max_percent_error")
        mean_error = row.get("mean_percent_error")
        nearest = row.get("nearest_frequency_ghz", "")
        lines.append(
            f"| {rank} | {row.get('status')} | {row.get('source_kind')} | "
            f"{_format_float(max_error)}% | {_format_float(mean_error)}% | "
            f"{_format_float(nearest)} | `{row.get('touchstone')}` |"
        )
    lines.extend(["", "## Boundary", ""])
    lines.extend(f"- {note}" for note in summary.get("notes", []))
    lines.append("")
    return "\n".join(lines)


def _format_float(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(number):
        return "inf"
    return f"{number:.4g}"


if __name__ == "__main__":
    raise SystemExit(main())
