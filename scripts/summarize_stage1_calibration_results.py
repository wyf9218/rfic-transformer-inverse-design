#!/usr/bin/env python3
"""Summarize Stage-1 EMX/HFSS straight-line calibration results."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    packet_dir = Path(args.packet_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    compare = _load_compare_module(packet_dir)
    summary = json.loads((packet_dir / "calibration_execution_summary.json").read_text(encoding="utf-8"))
    hfss_root = Path(args.hfss_results_root).expanduser().resolve()
    emx_root = Path(args.emx_results_root).expanduser().resolve() if args.emx_results_root else None

    rows = []
    for structure in summary["structures"]:
        name = structure["name"]
        emx_path = _resolve_emx_path(name, structure, emx_root)
        for variant in summary.get("hfss_calibration_variants", []):
            variant_name = variant["name"]
            hfss_path = _find_hfss_s2p(hfss_root, name, variant_name)
            row = {
                "structure": name,
                "variant": variant_name,
                "emx_s2p": "" if emx_path is None else str(emx_path),
                "hfss_s2p": "" if hfss_path is None else str(hfss_path),
                "status": "MISSING",
                "missing_reason": "",
            }
            if emx_path is None or not emx_path.is_file():
                row["missing_reason"] = "missing_emx_s2p"
            elif hfss_path is None or not hfss_path.is_file():
                row["missing_reason"] = "missing_hfss_s2p"
            else:
                result = compare.compare_curves(
                    compare.load_calibration_curves(emx_path),
                    compare.load_calibration_curves(hfss_path),
                    target_hz=float(args.target_ghz) * 1.0e9,
                    max_percent_error=float(args.max_percent_error),
                    target_tolerance_hz=float(args.target_frequency_tolerance_ghz) * 1.0e9,
                    require_matching_frequency_grid=bool(args.require_matching_frequency_grid),
                )
                compare_dir = out_dir / f"{name}_{variant_name}"
                compare_dir.mkdir(parents=True, exist_ok=True)
                (compare_dir / "calibration_s2p_rlc_comparison_summary.json").write_text(
                    json.dumps(result, indent=2), encoding="utf-8"
                )
                (compare_dir / "calibration_s2p_rlc_comparison_report.md").write_text(
                    compare.render_report(result), encoding="utf-8"
                )
                row["status"] = result["overall_status"]
                row["missing_reason"] = ""
                for metric, item in result["metrics"].items():
                    row[f"{metric}_emx"] = item["emx"]
                    row[f"{metric}_hfss"] = item["hfss"]
                    row[f"{metric}_percent_error"] = item["percent_error"]
                    row[f"{metric}_status"] = item["status"]
            rows.append(row)

    csv_path = out_dir / "stage1_calibration_summary.csv"
    md_path = out_dir / "stage1_calibration_summary.md"
    json_path = out_dir / "stage1_calibration_summary.json"
    _write_csv(csv_path, rows)
    status_counts = _status_counts(rows)
    payload = {
        "overall_status": _overall_status(rows),
        "status_counts": status_counts,
        "packet_dir": str(packet_dir),
        "emx_results_root": "" if emx_root is None else str(emx_root),
        "hfss_results_root": str(hfss_root),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={payload['overall_status']}")
    print(f"summary={json_path}")
    print(f"csv={csv_path}")
    print(f"report={md_path}")
    return 2 if payload["overall_status"] == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet-dir", required=True)
    parser.add_argument("--hfss-results-root", required=True)
    parser.add_argument("--emx-results-root")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--target-frequency-tolerance-ghz", type=float, default=0.05)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--require-matching-frequency-grid", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _load_compare_module(packet_dir: Path):
    candidates = [packet_dir / "compare_calibration_s2p_rlc.py", SCRIPT_DIR / "compare_calibration_s2p_rlc.py"]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("compare_calibration_s2p_rlc_runtime", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError("compare_calibration_s2p_rlc.py was not found")


def _resolve_emx_path(name: str, structure: dict[str, Any], emx_root: Path | None) -> Path | None:
    if emx_root is not None:
        candidates = _emx_candidate_paths(emx_root, name)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]
    remote = structure.get("remote_emx_output")
    return Path(remote) if remote else None


def _emx_candidate_paths(emx_root: Path, name: str) -> list[Path]:
    exact_candidates = [
        emx_root / name / "emx" / f"{name}.s2p",
        emx_root / name / "emx" / f"{name}_cadence.s2p",
        emx_root / name / f"{name}.s2p",
        emx_root / name / f"{name}_cadence.s2p",
        emx_root / "emx" / name / f"{name}.s2p",
        emx_root / "emx" / name / f"{name}_cadence.s2p",
        emx_root / f"{name}.s2p",
        emx_root / f"{name}_cadence.s2p",
    ]
    glob_candidates: list[Path] = []
    for pattern in [
        f"**/{name}_cadence.s2p",
        f"**/{name}.s2p",
        f"**/{name}*.s2p",
    ]:
        glob_candidates.extend(sorted(emx_root.glob(pattern)))

    candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in [*exact_candidates, *glob_candidates]:
        resolved = candidate.resolve() if candidate.exists() else candidate
        if resolved not in seen:
            candidates.append(candidate)
            seen.add(resolved)
    return candidates


def _find_hfss_s2p(hfss_root: Path, name: str, variant_name: str) -> Path | None:
    run_name = f"calibration_{name}_{variant_name}"
    candidates = sorted((hfss_root / run_name).glob("**/*.s2p"))
    if candidates:
        return candidates[0]
    return hfss_root / run_name / "hfss_direct_results" / f"{name}_hfss_calibration_{variant_name}_Setup_15GHz_Sweep_15p0_15p5_direct.s2p"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


def _overall_status(rows: list[dict[str, Any]]) -> str:
    statuses = {str(row["status"]) for row in rows}
    if "FAIL" in statuses:
        return "FAIL"
    if statuses == {"PASS"}:
        return "PASS"
    return "INCOMPLETE"


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Stage 1 Calibration Summary",
        "",
        f"- Overall status: **{payload['overall_status']}**",
        f"- Status counts: `{payload['status_counts']}`",
        "",
        "| Structure | Variant | Status | Missing reason | R err | L err | Q err | C err |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        lines.append(
            "| {structure} | {variant} | {status} | {missing_reason} | {r} | {l} | {q} | {c} |".format(
                structure=row["structure"],
                variant=row["variant"],
                status=row["status"],
                missing_reason=row.get("missing_reason", ""),
                r=_fmt(row.get("series_r_ohm_percent_error")),
                l=_fmt(row.get("series_l_nh_percent_error")),
                q=_fmt(row.get("series_q_percent_error")),
                c=_fmt(row.get("shunt_c_ff_percent_error")),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"{float(value):.3f}%"


if __name__ == "__main__":
    raise SystemExit(main())
