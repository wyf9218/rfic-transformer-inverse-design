#!/usr/bin/env python3
"""Select EMX rows for HFSS/ADS validation in physical-feature space.

The new workflow uses Lp/Ls/Q/K rather than Zin as the main inverse-design
target. This selector therefore chooses validation samples from real
simulator-derived physical-feature labels. It does not run HFSS, ADS, or EMX.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_FEATURE_COLUMNS = "lp_nh_center,ls_nh_center,q_center,k_center"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_csv = dataset_dir / "dataset_rows.csv"
    rows = _read_rows(dataset_csv)
    feature_columns = _split_columns(args.feature_columns)
    candidates = _candidate_rows(rows, dataset_dir, feature_columns, args)
    selected = _select_samples(candidates, feature_columns, args)

    selected_csv = out_dir / "physical_feature_validation_samples.csv"
    summary_path = out_dir / "physical_feature_validation_sample_summary.json"
    report_path = out_dir / "physical_feature_validation_sample_report.md"
    _write_csv(selected_csv, [_sample_row(row, rank) for rank, row in enumerate(selected, start=1)])

    requested = max(0, int(args.sample_count))
    status = "PASS" if len(selected) >= requested and requested > 0 else ("PARTIAL" if selected else "FAIL")
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_SELECTED_ROWS_FOR_HFSS_ADS_VALIDATION" if status in {"PASS", "PARTIAL"} else "DO_NOT_USE_SAMPLE_SELECTION",
        "dataset_dir": str(dataset_dir),
        "dataset_source": _file_source(dataset_csv),
        "out_dir": str(out_dir),
        "selected_csv": str(selected_csv),
        "feature_columns": feature_columns,
        "row_count": len(rows),
        "candidate_count": len(candidates),
        "requested_sample_count": requested,
        "selected_sample_count": len(selected),
        "feature_summary": _feature_summary(candidates, feature_columns),
        "selected": [_sample_row(row, rank) for rank, row in enumerate(selected, start=1)],
        "checks": [
            _check("dataset_rows_csv_exists", dataset_csv.is_file(), str(dataset_csv)),
            _check("dataset_rows_present", bool(rows), f"rows={len(rows)}"),
            _check("candidate_rows_present", bool(candidates), f"candidates={len(candidates)}"),
            _check("selected_rows_present", bool(selected), f"selected={len(selected)}"),
        ],
        "arguments": vars(args),
        "limitations": [
            "Samples are selected from existing dataset_rows.csv labels only.",
            "This script does not prove EMX/HFSS agreement; selected rows still require HFSS model generation, .s8p export, and ADS/Python curve comparison.",
            "For sample_count=1 the selection is intentionally random but deterministic under --seed.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"selected_csv={selected_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status in {"PASS", "PARTIAL"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--feature-columns", default=DEFAULT_FEATURE_COLUMNS)
    parser.add_argument("--sample-count", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260615)
    parser.add_argument("--mode", choices=["random", "coverage_then_random"], default="random")
    parser.add_argument("--require-touchstone-path", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--check-touchstone-exists", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _split_columns(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _candidate_rows(
    rows: list[dict[str, str]],
    dataset_dir: Path,
    feature_columns: list[str],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    candidates = []
    for idx, row in enumerate(rows):
        if not _truthy(row.get("ok", "true")):
            continue
        features = {}
        for column in feature_columns:
            value = _as_float(row.get(column))
            if value is None:
                features = {}
                break
            features[column] = value
        if not features:
            continue
        touchstone_raw = (row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
        if args.require_touchstone_path and not touchstone_raw:
            continue
        touchstone_path = _resolve(dataset_dir, touchstone_raw) if touchstone_raw else None
        if args.check_touchstone_exists and (touchstone_path is None or not touchstone_path.is_file()):
            continue
        work_dir_raw = (row.get("work_dir") or "").strip()
        work_dir = _resolve(dataset_dir, work_dir_raw) if work_dir_raw else None
        candidates.append(
            {
                "row_index": idx,
                "evaluation": row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or f"row_{idx}",
                "work_dir": "" if work_dir is None else str(work_dir),
                "touchstone_path": "" if touchstone_path is None else str(touchstone_path),
                "features": features,
                "row": row,
            }
        )
    return candidates


def _select_samples(candidates: list[dict[str, Any]], feature_columns: list[str], args: argparse.Namespace) -> list[dict[str, Any]]:
    requested = max(0, int(args.sample_count))
    if requested <= 0 or not candidates:
        return []
    rng = np.random.default_rng(int(args.seed))
    if args.mode == "random" or requested == 1:
        indices = rng.choice(np.arange(len(candidates)), size=min(requested, len(candidates)), replace=False)
        selected = []
        for idx in np.asarray(indices, dtype=int):
            item = dict(candidates[int(idx)])
            item["selection_reason"] = "deterministic_random_seeded_sample"
            selected.append(item)
        return selected

    selected_by_index: dict[int, dict[str, Any]] = {}
    for column in feature_columns:
        values = np.asarray([float(item["features"][column]) for item in candidates], dtype=float)
        for reason, idx in (("min_" + column, int(np.argmin(values))), ("max_" + column, int(np.argmax(values)))):
            if len(selected_by_index) >= requested:
                break
            item = dict(candidates[idx])
            item["selection_reason"] = reason
            selected_by_index[int(item["row_index"])] = item
        if len(selected_by_index) >= requested:
            break
    remaining = [idx for idx, item in enumerate(candidates) if int(item["row_index"]) not in selected_by_index]
    if len(selected_by_index) < requested and remaining:
        fill = rng.choice(np.asarray(remaining, dtype=int), size=min(requested - len(selected_by_index), len(remaining)), replace=False)
        for idx in np.asarray(fill, dtype=int):
            item = dict(candidates[int(idx)])
            item["selection_reason"] = "deterministic_random_fill"
            selected_by_index[int(item["row_index"])] = item
    return list(selected_by_index.values())[:requested]


def _sample_row(item: dict[str, Any], rank: int) -> dict[str, Any]:
    row = {
        "selection_rank": int(rank),
        "row_index": int(item["row_index"]),
        "evaluation": item["evaluation"],
        "selection_reason": item.get("selection_reason", ""),
        "work_dir": item.get("work_dir", ""),
        "touchstone_path": item.get("touchstone_path", ""),
    }
    for key, value in item["features"].items():
        row[key] = value
    return row


def _feature_summary(candidates: list[dict[str, Any]], feature_columns: list[str]) -> dict[str, Any]:
    summary = {}
    for column in feature_columns:
        values = np.asarray([float(item["features"][column]) for item in candidates], dtype=float)
        if values.size == 0:
            summary[column] = {"min": None, "max": None, "mean": None}
        else:
            summary[column] = {"min": float(np.min(values)), "max": float(np.max(values)), "mean": float(np.mean(values))}
    return summary


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _resolve(dataset_dir: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else dataset_dir / path


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "none", "no", "nan"}


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _file_source(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    digest = hashlib.sha256()
    line_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            line_count += chunk.count(b"\n")
    out.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest(), "line_count": line_count})
    return out


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Physical-Feature Validation Sample Selection",
        "",
        f"Status: **{summary['overall_status']}**",
        f"Decision: **{summary['decision']}**",
        f"Candidate rows: `{summary['candidate_count']}`",
        f"Selected rows: `{summary['selected_sample_count']}`",
        f"Feature columns: `{', '.join(summary['feature_columns'])}`",
        f"Selected CSV: `{summary['selected_csv']}`",
        "",
        "## Selected Samples",
        "",
        "| Rank | Evaluation | Reason | Touchstone |",
        "| --- | --- | --- | --- |",
    ]
    for row in summary["selected"]:
        lines.append(f"| {row['selection_rank']} | {_cell(str(row['evaluation']))} | {_cell(str(row['selection_reason']))} | `{row['touchstone_path']}` |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
