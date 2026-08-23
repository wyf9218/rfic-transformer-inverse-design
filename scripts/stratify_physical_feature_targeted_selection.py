#!/usr/bin/env python3
"""Downsample a targeted candidate selection without changing bin proportions.

The input contains surrogate-ranked geometry candidates grouped by physical-
feature target bin. Predictions remain acquisition metadata only; this script
does not create simulator labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    source = Path(args.selection_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "physical_feature_targeted_candidate_selection.csv"
    summary_path = out_dir / "physical_feature_targeted_selection_stratification_summary.json"

    fieldnames, rows = _read_csv(source)
    eligible = [row for row in rows if not args.require_inside_target_bin or _truthy(row.get("inside_target_bin"))]
    groups: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    for idx, row in enumerate(eligible):
        groups[str(row.get(args.group_column) or "")].append((idx, row))

    errors: list[str] = []
    if not source.is_file():
        errors.append("source selection CSV is missing")
    if not rows:
        errors.append("source selection CSV has no rows")
    if args.count <= 0:
        errors.append("count must be positive")
    if args.count > len(eligible):
        errors.append(f"requested count {args.count} exceeds eligible rows {len(eligible)}")
    if "" in groups:
        errors.append(f"missing group column {args.group_column}")

    quotas = _proportional_quotas(groups, args.count) if not errors else {}
    selected_by_group: dict[str, list[tuple[str, int, dict[str, str]]]] = {}
    for key in sorted(groups):
        ranked = sorted(groups[key], key=lambda item: _within_group_key(item[1], item[0], args))
        selected_by_group[key] = [(key, idx, dict(row)) for idx, row in ranked[: quotas.get(key, 0)]]
    selected = _ordered_rows(selected_by_group, args.output_order)
    selected_rows = []
    for rank, (_key, _idx, row) in enumerate(selected, start=1):
        row["selection_rank"] = str(rank)
        selected_rows.append(row)
    if "selection_rank" not in fieldnames:
        fieldnames = ["selection_rank", *fieldnames]

    ids = [str(row.get(args.candidate_id_column) or "") for row in selected_rows]
    if len(selected_rows) != args.count:
        errors.append(f"selected rows {len(selected_rows)} != requested {args.count}")
    if any(not value for value in ids):
        errors.append(f"missing {args.candidate_id_column} values")
    if len(set(ids)) != len(ids):
        errors.append(f"duplicate {args.candidate_id_column} values")
    if args.require_inside_target_bin and any(not _truthy(row.get("inside_target_bin")) for row in selected_rows):
        errors.append("one or more selected rows are outside their target bin")

    _write_csv(output, fieldnames, selected_rows)
    group_summary = []
    max_share_error = 0.0
    for key in sorted(groups):
        source_count = len(groups[key])
        selected_count = quotas.get(key, 0)
        source_share = source_count / len(eligible) if eligible else 0.0
        selected_share = selected_count / len(selected_rows) if selected_rows else 0.0
        max_share_error = max(max_share_error, abs(selected_share - source_share))
        group_summary.append(
            {
                "target_bin_key": key,
                "source_count": source_count,
                "selected_count": selected_count,
                "source_share": source_share,
                "selected_share": selected_share,
            }
        )
    status = "PASS" if not errors else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_STRATIFIED_SELECTION_FOR_ACQUISITION_QUEUE" if status == "PASS" else "DO_NOT_USE_STRATIFIED_SELECTION",
        "source": _file_source(source),
        "output": _file_source(output),
        "input_row_count": len(rows),
        "eligible_row_count": len(eligible),
        "requested_count": args.count,
        "selected_count": len(selected_rows),
        "group_column": args.group_column,
        "group_count": len(groups),
        "score_column": args.score_column,
        "output_order": args.output_order,
        "within_group_order": args.within_group_order,
        "require_inside_target_bin": args.require_inside_target_bin,
        "max_absolute_group_share_error": max_share_error,
        "groups": group_summary,
        "errors": errors,
        "limitations": [
            "Surrogate values are used only to preserve acquisition targeting and rank candidates.",
            "Final Lp/Ls/Q/K labels must be extracted from EMX-generated Touchstone files.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"overall_status={status}")
    print(f"selected_csv={output}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--count", required=True, type=int)
    parser.add_argument("--group-column", default="target_bin_key")
    parser.add_argument("--score-column", default="selection_score")
    parser.add_argument("--candidate-id-column", default="candidate_id")
    parser.add_argument("--output-order", choices=("source", "interleaved"), default="interleaved")
    parser.add_argument("--within-group-order", choices=("score", "estimated_em_cost"), default="score")
    parser.add_argument("--require-inside-target-bin", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def _proportional_quotas(groups: dict[str, list[tuple[int, dict[str, str]]]], count: int) -> dict[str, int]:
    total = sum(len(rows) for rows in groups.values())
    if total <= 0 or count <= 0:
        return {key: 0 for key in groups}
    exact = {key: len(rows) * count / total for key, rows in groups.items()}
    quotas = {key: min(len(groups[key]), int(math.floor(value))) for key, value in exact.items()}
    remaining = count - sum(quotas.values())
    order = sorted(groups, key=lambda key: (-(exact[key] - math.floor(exact[key])), key))
    while remaining > 0:
        progressed = False
        for key in order:
            if quotas[key] >= len(groups[key]):
                continue
            quotas[key] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            break
    return quotas


def _ordered_rows(
    selected_by_group: dict[str, list[tuple[str, int, dict[str, str]]]],
    output_order: str,
) -> list[tuple[str, int, dict[str, str]]]:
    if output_order == "source":
        rows = [row for group_rows in selected_by_group.values() for row in group_rows]
        return sorted(rows, key=lambda item: (item[1], item[0]))
    keys = sorted(selected_by_group)
    rows: list[tuple[str, int, dict[str, str]]] = []
    max_count = max((len(selected_by_group[key]) for key in keys), default=0)
    for offset in range(max_count):
        for key in keys:
            group_rows = selected_by_group[key]
            if offset < len(group_rows):
                rows.append(group_rows[offset])
    return rows


def _score(value: str | None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.inf
    return out if math.isfinite(out) else math.inf


def _within_group_key(row: dict[str, str], source_index: int, args: argparse.Namespace) -> tuple[float, ...]:
    score = _score(row.get(args.score_column))
    if args.within_group_order == "score":
        return (score, float(source_index))
    return (_estimated_em_cost_proxy(row), score, float(source_index))


def _estimated_em_cost_proxy(row: dict[str, str]) -> float:
    values = []
    for field in (
        "primary_outer_width_um",
        "primary_outer_height_um",
        "secondary_outer_width_um",
        "secondary_outer_height_um",
    ):
        value = math.nan
        for key in (f"candidate__geom__{field}", f"geom__{field}", f"candidate__{field}", field):
            value = _score(row.get(key))
            if math.isfinite(value):
                break
        values.append(value)
    pw, ph, sw, sh = values
    if not all(math.isfinite(value) and value > 0.0 for value in values):
        return math.inf
    return pw * ph + sw * sh


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "pass", "ok"}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or ["empty"])
        writer.writeheader()
        writer.writerows(rows)


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


if __name__ == "__main__":
    raise SystemExit(main())
