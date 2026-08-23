#!/usr/bin/env python3
"""Select candidate geometries for a verified sparse-bin Zin acquisition plan.

This is the bridge between response-space planning and the next EMX/Cadence
batch. It does not invent labels. It expects a candidate prediction table from
an inverse model, surrogate, optimizer sweep, or other geometry proposal stage,
then assigns candidates to the sparse Re/Im(Zin) target bins produced by
``plan_zin_balanced_acquisition.py``.
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


DEFAULT_REAL_COLUMNS = ("pred_zin_center_real_ohm", "zin_center_real_ohm")
DEFAULT_IMAG_COLUMNS = ("pred_zin_center_imag_ohm", "zin_center_imag_ohm")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan_dir = Path(args.plan_dir).expanduser().resolve()
    candidate_csv = Path(args.candidate_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    targets_csv = Path(args.targets_csv).expanduser().resolve() if args.targets_csv else plan_dir / "zin_balanced_acquisition_targets.csv"
    targets = _read_csv(targets_csv)
    candidates = _read_csv(candidate_csv)
    real_column = _resolve_column(args.pred_real_column, DEFAULT_REAL_COLUMNS, candidates)
    imag_column = _resolve_column(args.pred_imag_column, DEFAULT_IMAG_COLUMNS, candidates)

    checks = [
        _check("plan_targets_csv_exists", targets_csv.is_file(), str(targets_csv)),
        _check("candidate_csv_exists", candidate_csv.is_file(), str(candidate_csv)),
        _check("target_rows_present", bool(targets), f"rows={len(targets)}"),
        _check("candidate_rows_present", bool(candidates), f"rows={len(candidates)}"),
        _check("prediction_real_column_present", real_column is not None, args.pred_real_column or DEFAULT_REAL_COLUMNS),
        _check("prediction_imag_column_present", imag_column is not None, args.pred_imag_column or DEFAULT_IMAG_COLUMNS),
    ]
    selected: list[dict[str, Any]] = []
    per_target: list[dict[str, Any]] = []
    selection_diagnostics: dict[str, Any] = {"status": "NOT_RUN"}
    if all(item["pass"] for item in checks):
        selected, per_target, selection_diagnostics = _select_candidates(targets, candidates, real_column or "", imag_column or "", args)
        requested = int(selection_diagnostics.get("effective_requested_candidate_count") or _requested_count(targets, args))
        checks.extend(
            [
                _check("requested_candidate_count_positive", requested > 0, requested),
                _check("selected_candidates_present", bool(selected), f"selected={len(selected)}"),
            ]
        )
        if args.reachable_targets_only:
            checks.append(
                _check(
                    "reachable_targets_present",
                    int(selection_diagnostics.get("reachable_target_count") or 0) > 0,
                    selection_diagnostics.get("reachable_target_count"),
                )
            )
    else:
        requested = 0

    selected_csv = out_dir / "zin_targeted_candidate_selection.csv"
    summary_path = out_dir / "zin_targeted_candidate_selection_summary.json"
    report_path = out_dir / "zin_targeted_candidate_selection_report.md"
    _write_csv(selected_csv, selected)

    selected_count = len(selected)
    status = _status(checks, selected_count, requested)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": _decision(status),
        "plan_dir": str(plan_dir),
        "targets_csv": str(targets_csv),
        "targets_source": _file_source(targets_csv),
        "candidate_csv": str(candidate_csv),
        "candidate_source": _file_source(candidate_csv),
        "out_dir": str(out_dir),
        "selected_csv": str(selected_csv),
        "prediction_columns": {"real": real_column, "imag": imag_column},
        "original_requested_candidate_count": selection_diagnostics.get("original_requested_candidate_count", requested),
        "requested_candidate_count": requested,
        "selected_candidate_count": selected_count,
        "selected_inside_target_bin_count": sum(1 for row in selected if row.get("inside_target_bin") is True),
        "target_count": len(targets),
        "candidate_count": len(candidates),
        "selection_diagnostics": selection_diagnostics,
        "per_target": per_target,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This selector ranks proposed geometries from predicted Zin only; it does not run Cadence, EMX, HFSS, or ADS.",
            "Use the selected candidates only as the next acquisition queue. Final labels must come from simulator-generated S-parameters.",
            "Prediction quality depends on the external inverse/surrogate model that produced the candidate CSV.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"selected_csv={selected_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", required=True, help="Directory produced by plan_zin_balanced_acquisition.py")
    parser.add_argument("--targets-csv", help="Override path to zin_balanced_acquisition_targets.csv")
    parser.add_argument("--candidate-csv", required=True, help="CSV containing proposed geometries and predicted Zin")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--pred-real-column")
    parser.add_argument("--pred-imag-column")
    parser.add_argument("--candidate-id-column", default="candidate_id")
    parser.add_argument("--max-total", type=int, help="Cap total selected candidates across all target bins")
    parser.add_argument("--max-per-target", type=int, help="Cap selected candidates per target bin")
    parser.add_argument("--allow-outside-bin", action="store_true", help="Allow nearest candidates even when outside a target bin")
    parser.add_argument("--reachable-targets-only", action="store_true", help="Drop target bins with too few inside-bin predicted candidates")
    parser.add_argument("--min-candidates-per-reachable-target", type=int, default=1)
    parser.add_argument(
        "--redistribute-reachable-quota",
        action="store_true",
        help="Redistribute the requested total across reachable target bins using inside-bin candidate capacity",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_column(explicit: str | None, defaults: tuple[str, ...], rows: list[dict[str, str]]) -> str | None:
    if explicit:
        return explicit if rows and explicit in rows[0] else None
    if not rows:
        return None
    fields = set(rows[0])
    for name in defaults:
        if name in fields:
            return name
    return None


def _select_candidates(
    targets: list[dict[str, str]],
    candidates: list[dict[str, str]],
    real_column: str,
    imag_column: str,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    per_target: list[dict[str, Any]] = []
    used: set[int] = set()
    sorted_targets = _sorted_targets(targets)
    original_requested_total = _requested_count(targets, args)
    inside_counts = _inside_candidate_counts(sorted_targets, candidates, real_column, imag_column)
    min_reachable = max(1, int(args.min_candidates_per_reachable_target))
    active_targets = [
        target for target in sorted_targets if not args.reachable_targets_only or inside_counts[_target_key(target)] >= min_reachable
    ]
    skipped_targets = [
        target for target in sorted_targets if args.reachable_targets_only and inside_counts[_target_key(target)] < min_reachable
    ]
    quota_by_key = _quota_by_target(active_targets, inside_counts, args, original_requested_total)
    effective_requested_total = sum(quota_by_key.values())
    remaining_total = effective_requested_total

    for target in skipped_targets:
        original_requested = _target_request(target, args)
        per_target.append(
            _target_record(
                target,
                original_requested,
                0,
                "UNREACHABLE_SKIPPED",
                inside_counts[_target_key(target)],
                "dropped by --reachable-targets-only",
            )
        )

    for target in active_targets:
        requested = min(quota_by_key.get(_target_key(target), 0), remaining_total)
        if requested <= 0:
            per_target.append(_target_record(target, requested, 0, "SKIPPED", inside_counts[_target_key(target)]))
            continue

        ranked: list[tuple[float, int, dict[str, Any]]] = []
        for idx, candidate in enumerate(candidates):
            if idx in used:
                continue
            pred_real = _as_float(candidate.get(real_column))
            pred_imag = _as_float(candidate.get(imag_column))
            if pred_real is None or pred_imag is None:
                continue
            score, inside = _target_distance(target, pred_real, pred_imag)
            if not inside and not args.allow_outside_bin:
                continue
            ranked.append((score, idx, {"pred_real_ohm": pred_real, "pred_imag_ohm": pred_imag, "inside_target_bin": inside}))
        ranked.sort(key=lambda item: (item[0], item[1]))

        chosen = ranked[:requested]
        for score, idx, prediction in chosen:
            used.add(idx)
            candidate = candidates[idx]
            selected.append(_selection_row(target, candidate, prediction, score, idx, len(selected) + 1, args))
        remaining_total -= len(chosen)
        per_target.append(
            _target_record(
                target,
                requested,
                len(chosen),
                "PASS" if len(chosen) == requested else "PARTIAL",
                inside_counts[_target_key(target)],
            )
        )
        if remaining_total <= 0:
            break
    diagnostics = {
        "status": "PASS",
        "original_requested_candidate_count": original_requested_total,
        "effective_requested_candidate_count": effective_requested_total,
        "reachable_targets_only": bool(args.reachable_targets_only),
        "redistribute_reachable_quota": bool(args.redistribute_reachable_quota),
        "min_candidates_per_reachable_target": min_reachable,
        "reachable_target_count": len(active_targets),
        "unreachable_target_count": len(skipped_targets),
        "reachable_inside_candidate_capacity": sum(inside_counts[_target_key(target)] for target in active_targets),
        "selected_inside_target_bin_count": sum(1 for row in selected if row.get("inside_target_bin") is True),
    }
    return selected, per_target, diagnostics


def _inside_candidate_counts(
    targets: list[dict[str, str]],
    candidates: list[dict[str, str]],
    real_column: str,
    imag_column: str,
) -> dict[tuple[str, str, str], int]:
    counts = {_target_key(target): 0 for target in targets}
    for target in targets:
        key = _target_key(target)
        for candidate in candidates:
            pred_real = _as_float(candidate.get(real_column))
            pred_imag = _as_float(candidate.get(imag_column))
            if pred_real is None or pred_imag is None:
                continue
            _score, inside = _target_distance(target, pred_real, pred_imag)
            if inside:
                counts[key] += 1
    return counts


def _quota_by_target(
    active_targets: list[dict[str, str]],
    inside_counts: dict[tuple[str, str, str], int],
    args: argparse.Namespace,
    original_requested_total: int,
) -> dict[tuple[str, str, str], int]:
    if not args.redistribute_reachable_quota:
        return {
            _target_key(target): min(_target_request(target, args), inside_counts.get(_target_key(target), 0))
            if args.reachable_targets_only and not args.allow_outside_bin
            else _target_request(target, args)
            for target in active_targets
        }
    desired_total = original_requested_total
    if args.max_total is not None:
        desired_total = min(desired_total, max(0, int(args.max_total)))
    capacities = {
        _target_key(target): inside_counts.get(_target_key(target), 0) if not args.allow_outside_bin else desired_total
        for target in active_targets
    }
    if args.max_per_target is not None:
        max_per_target = max(0, int(args.max_per_target))
        capacities = {key: min(value, max_per_target) for key, value in capacities.items()}
    quotas = {key: 0 for key in capacities}
    ordered_keys = [_target_key(target) for target in active_targets]
    remaining = min(desired_total, sum(capacities.values()))
    while remaining > 0 and ordered_keys:
        progressed = False
        for key in ordered_keys:
            if remaining <= 0:
                break
            if quotas[key] >= capacities[key]:
                continue
            quotas[key] += 1
            remaining -= 1
            progressed = True
        if not progressed:
            break
    return quotas


def _target_key(target: dict[str, str]) -> tuple[str, str, str]:
    return (str(target.get("rank")), str(target.get("real_bin")), str(target.get("imag_bin")))


def _sorted_targets(targets: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(targets, key=lambda row: (int(float(row.get("rank", "0") or 0)), -int(float(row.get("recommended_new_samples", "0") or 0))))


def _requested_count(targets: list[dict[str, str]], args: argparse.Namespace) -> int:
    total = sum(_target_request(row, args) for row in targets)
    if args.max_total is not None:
        total = min(total, max(0, int(args.max_total)))
    return total


def _target_request(target: dict[str, str], args: argparse.Namespace) -> int:
    requested = int(float(target.get("recommended_new_samples", "0") or 0))
    if args.max_per_target is not None:
        requested = min(requested, max(0, int(args.max_per_target)))
    return max(0, requested)


def _target_distance(target: dict[str, str], pred_real: float, pred_imag: float) -> tuple[float, bool]:
    real_min = _required_float(target, "real_min_ohm")
    real_max = _required_float(target, "real_max_ohm")
    imag_min = _required_float(target, "imag_min_ohm")
    imag_max = _required_float(target, "imag_max_ohm")
    target_real = _required_float(target, "target_real_ohm")
    target_imag = _required_float(target, "target_imag_ohm")
    real_half_width = max(abs(real_max - real_min) / 2.0, 1e-12)
    imag_half_width = max(abs(imag_max - imag_min) / 2.0, 1e-12)
    dx = (pred_real - target_real) / real_half_width
    dy = (pred_imag - target_imag) / imag_half_width
    inside = real_min <= pred_real <= real_max and imag_min <= pred_imag <= imag_max
    return float(math.hypot(dx, dy)), inside


def _selection_row(
    target: dict[str, str],
    candidate: dict[str, str],
    prediction: dict[str, Any],
    score: float,
    candidate_index: int,
    selection_rank: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    candidate_id = candidate.get(args.candidate_id_column) or candidate.get("sample_id") or candidate.get("evaluation") or str(candidate_index)
    row: dict[str, Any] = {
        "selection_rank": selection_rank,
        "candidate_index": candidate_index,
        "candidate_id": candidate_id,
        "target_rank": target.get("rank"),
        "target_real_bin": target.get("real_bin"),
        "target_imag_bin": target.get("imag_bin"),
        "target_real_ohm": target.get("target_real_ohm"),
        "target_imag_ohm": target.get("target_imag_ohm"),
        "target_recommended_new_samples": target.get("recommended_new_samples"),
        "pred_real_ohm": prediction["pred_real_ohm"],
        "pred_imag_ohm": prediction["pred_imag_ohm"],
        "inside_target_bin": prediction["inside_target_bin"],
        "selection_score": score,
    }
    for key, value in candidate.items():
        row[f"candidate__{key}"] = value
    return row


def _target_record(
    target: dict[str, str],
    requested: int,
    selected: int,
    status: str,
    inside_candidate_count: int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "target_rank": target.get("rank"),
        "real_bin": target.get("real_bin"),
        "imag_bin": target.get("imag_bin"),
        "requested": requested,
        "selected": selected,
        "missing": max(0, requested - selected),
        "inside_candidate_count": inside_candidate_count,
        "note": note,
    }


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


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _required_float(row: dict[str, str], key: str) -> float:
    value = _as_float(row.get(key))
    if value is None:
        raise ValueError(f"target row missing numeric {key}: {row}")
    return value


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


def _status(checks: list[dict[str, Any]], selected_count: int, requested_count: int) -> str:
    if any(not item["pass"] for item in checks):
        return "FAIL"
    if selected_count >= requested_count:
        return "PASS"
    return "PARTIAL"


def _decision(status: str) -> str:
    return {
        "PASS": "USE_SELECTED_CANDIDATES_FOR_NEXT_EMX_BATCH",
        "PARTIAL": "USE_SELECTED_CANDIDATES_WITH_MISSING_TARGET_CAVEAT",
        "FAIL": "DO_NOT_USE_CANDIDATE_SELECTION",
    }[status]


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Zin-targeted Candidate Geometry Selection",
        "",
        f"Status: **{summary['overall_status']}**",
        f"Decision: **{summary['decision']}**",
        f"Requested candidates: `{summary['requested_candidate_count']}`",
        f"Original requested candidates: `{summary.get('original_requested_candidate_count')}`",
        f"Selected candidates: `{summary['selected_candidate_count']}`",
        f"Selected inside target-bin candidates: `{summary.get('selected_inside_target_bin_count')}`",
        f"Selected CSV: `{summary['selected_csv']}`",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Per-target fill", "", "| Target rank | Bin | Requested | Selected | Missing | Status |", "| --- | --- | --- | --- | --- | --- |"])
    for row in summary["per_target"]:
        lines.append(
            f"| {row.get('target_rank')} | ({row.get('real_bin')},{row.get('imag_bin')}) | {row.get('requested')} | {row.get('selected')} | {row.get('missing')} | {row.get('status')} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
