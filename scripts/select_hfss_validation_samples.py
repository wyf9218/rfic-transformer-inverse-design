#!/usr/bin/env python3
"""Select representative samples for HFSS/ADS cross-validation.

The selector uses existing response labels only. It is meant to run after
`extract_touchstone_response_features.py` or on a completed dataset whose
`dataset_rows.csv` already contains real Zin/K/Q/L labels. It does not run EMX,
HFSS, or ADS, and it must not be used to claim validation by itself.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else dataset_dir / "hfss_validation_sample_selection"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(dataset_dir / "dataset_rows.csv")
    candidates = _collect_candidates(rows, dataset_dir, require_touchstone=bool(args.require_touchstone))
    selected = _select_candidates(candidates, args)
    status = _overall_status(candidates, selected, args)
    summary = _build_summary(dataset_dir, out_dir, rows, candidates, selected, status, args)
    plots, plot_errors = _write_selection_plots(out_dir, candidates, selected, args)
    summary["plots"] = plots
    summary["plot_errors"] = plot_errors

    selected_csv = out_dir / "hfss_validation_samples.csv"
    summary_path = out_dir / "hfss_validation_sample_selection_summary.json"
    report_path = out_dir / "hfss_validation_sample_selection_report.md"
    commands_path = out_dir / "hfss_validation_next_commands.md"
    _write_selected_csv(selected_csv, selected)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    commands_path.write_text(_render_commands(selected, args), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"selected_csv={selected_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"next_commands={commands_path}")
    print(f"selected_count={len(selected)}")
    return 2 if status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--sample-count", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--target-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--require-touchstone", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-candidates", type=int)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _collect_candidates(rows: list[dict[str, str]], dataset_dir: Path, *, require_touchstone: bool) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not _truthy(row.get("ok", "true")):
            continue
        real = _as_float(_first_value(row, ("zin_center_real_ohm", "zin_real_ohm", "center_zin_real_ohm")))
        imag = _as_float(_first_value(row, ("zin_center_imag_ohm", "zin_imag_ohm", "center_zin_imag_ohm")))
        mag = _as_float(_first_value(row, ("zin_center_abs_ohm", "zin_abs_ohm", "center_zin_abs_ohm")))
        if real is None or imag is None:
            continue
        if mag is None:
            mag = float(math.hypot(real, imag))
        touchstone = _resolve_touchstone(row, dataset_dir)
        if require_touchstone and (touchstone is None or not touchstone.exists()):
            continue
        candidates.append(
            {
                "source_index": index,
                "evaluation": row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or f"row_{index:06d}",
                "touchstone_path": str(touchstone) if touchstone is not None else "",
                "touchstone_exists": bool(touchstone and touchstone.exists()),
                "zin_center_real_ohm": real,
                "zin_center_imag_ohm": imag,
                "zin_center_abs_ohm": mag,
                "k_center": _as_float(row.get("k_center") or row.get("k")),
                "qp_center": _as_float(row.get("qp_center") or row.get("qp")),
                "qs_center": _as_float(row.get("qs_center") or row.get("qs")),
                "lp_nh_center": _as_float(row.get("lp_nh_center") or row.get("lp_nh")),
                "ls_nh_center": _as_float(row.get("ls_nh_center") or row.get("ls_nh")),
                "target_frequency_used_ghz": _as_float(row.get("target_frequency_used_ghz")),
                "selection_reasons": [],
            }
        )
    return candidates


def _resolve_touchstone(row: dict[str, str], dataset_dir: Path) -> Path | None:
    for key in ("touchstone_path", "sparam_path", "emx_s4p_path", "emx_touchstone_path"):
        value = row.get(key)
        if value:
            path = Path(value).expanduser()
            return path if path.is_absolute() else (dataset_dir / path).resolve()
    evaluation = row.get("evaluation") or row.get("sample_id") or row.get("id")
    if evaluation:
        return (dataset_dir / "evaluations" / evaluation / "emx" / "emx.s4p").resolve()
    return None


def _select_candidates(candidates: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    if not candidates or int(args.sample_count) <= 0:
        return []
    selected_by_key: dict[tuple[int, str], dict[str, Any]] = {}

    def add(candidate: dict[str, Any], reason: str) -> None:
        key = (int(candidate["source_index"]), str(candidate["evaluation"]))
        if key not in selected_by_key:
            copied = dict(candidate)
            copied["selection_reasons"] = []
            selected_by_key[key] = copied
        if reason not in selected_by_key[key]["selection_reasons"]:
            selected_by_key[key]["selection_reasons"].append(reason)

    for metric, label in (
        ("zin_center_abs_ohm", "abs_zin"),
        ("zin_center_real_ohm", "real_zin"),
        ("zin_center_imag_ohm", "imag_zin"),
    ):
        add(min(candidates, key=lambda item, m=metric: float(item[m])), f"min_{label}")
        add(max(candidates, key=lambda item, m=metric: float(item[m])), f"max_{label}")

    for metric, label in (("k_center", "k"), ("qp_center", "qp"), ("qs_center", "qs")):
        available = [item for item in candidates if item.get(metric) is not None]
        if available:
            add(min(available, key=lambda item, m=metric: float(item[m])), f"min_{label}")
            add(max(available, key=lambda item, m=metric: float(item[m])), f"max_{label}")

    for quantile in (0.25, 0.5, 0.75):
        add(_nearest_quantile(candidates, "zin_center_abs_ohm", quantile), f"abs_zin_q{int(quantile * 100)}")

    for candidate in _sparse_bin_candidates(candidates, int(args.bins)):
        add(candidate, "sparse_zin_2d_bin")

    rng = random.Random(int(args.seed))
    shuffled = candidates[:]
    rng.shuffle(shuffled)
    for candidate in shuffled:
        if len(selected_by_key) >= int(args.sample_count):
            break
        add(candidate, "seeded_random_fill")

    selected = list(selected_by_key.values())
    selected.sort(key=lambda item: _selection_sort_key(item))
    return selected[: int(args.sample_count)]


def _nearest_quantile(candidates: list[dict[str, Any]], metric: str, quantile: float) -> dict[str, Any]:
    values = np.asarray([float(item[metric]) for item in candidates], dtype=float)
    target = float(np.quantile(values, quantile))
    return min(candidates, key=lambda item: abs(float(item[metric]) - target))


def _sparse_bin_candidates(candidates: list[dict[str, Any]], bins: int) -> list[dict[str, Any]]:
    if len(candidates) < 2 or bins <= 0:
        return []
    real = np.asarray([float(item["zin_center_real_ohm"]) for item in candidates], dtype=float)
    imag = np.asarray([float(item["zin_center_imag_ohm"]) for item in candidates], dtype=float)
    real_edges = np.linspace(float(np.min(real)), float(np.max(real)), bins + 1)
    imag_edges = np.linspace(float(np.min(imag)), float(np.max(imag)), bins + 1)
    if np.allclose(real_edges[0], real_edges[-1]) or np.allclose(imag_edges[0], imag_edges[-1]):
        return []
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for candidate in candidates:
        real_bin = _bin_index(float(candidate["zin_center_real_ohm"]), real_edges)
        imag_bin = _bin_index(float(candidate["zin_center_imag_ohm"]), imag_edges)
        grouped.setdefault((real_bin, imag_bin), []).append(candidate)
    sparse_groups = sorted(grouped.values(), key=lambda group: (len(group), _group_abs_zin_center(group)))
    result = []
    for group in sparse_groups[: max(1, min(5, len(sparse_groups)))]:
        result.append(min(group, key=lambda item: float(item["zin_center_abs_ohm"])))
    return result


def _bin_index(value: float, edges: np.ndarray) -> int:
    idx = int(np.searchsorted(edges, value, side="right") - 1)
    return max(0, min(idx, len(edges) - 2))


def _group_abs_zin_center(group: list[dict[str, Any]]) -> float:
    return float(np.mean([float(item["zin_center_abs_ohm"]) for item in group]))


def _selection_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    reason_priority = {
        "min_abs_zin": 0,
        "max_abs_zin": 1,
        "abs_zin_q50": 2,
        "sparse_zin_2d_bin": 3,
        "seeded_random_fill": 4,
    }
    priority = min((reason_priority.get(reason, 9) for reason in item["selection_reasons"]), default=9)
    return priority, int(item["source_index"]), str(item["evaluation"])


def _overall_status(candidates: list[dict[str, Any]], selected: list[dict[str, Any]], args: argparse.Namespace) -> str:
    if not candidates:
        return "NOT_READY"
    min_candidates = int(args.min_candidates) if args.min_candidates is not None else min(int(args.sample_count), 1)
    if len(candidates) < min_candidates:
        return "FAIL"
    if len(selected) < min(int(args.sample_count), len(candidates)):
        return "FAIL"
    return "PASS"


def _build_summary(
    dataset_dir: Path,
    out_dir: Path,
    rows: list[dict[str, str]],
    candidates: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    status: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "row_count": len(rows),
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "arguments": {
            "sample_count": args.sample_count,
            "seed": args.seed,
            "bins": args.bins,
            "target_frequency_ghz": args.target_frequency_ghz,
            "require_touchstone": args.require_touchstone,
            "min_candidates": args.min_candidates,
        },
        "selected": [_selected_record(item, rank) for rank, item in enumerate(selected, start=1)],
        "candidate_feature_summary": _candidate_feature_summary(candidates),
        "selected_feature_summary": _candidate_feature_summary(selected),
        "selected_reason_counts": _reason_counts(selected),
        "zin_bin_coverage_summary": _zin_bin_coverage_summary(candidates, selected, int(args.bins)),
        "limitations": [
            "This selector uses existing labels only; it does not prove EMX/HFSS agreement.",
            "Selected samples should be rebuilt or imported in HFSS and compared against EMX/ADS formulas with the project 5% gate.",
            "If response labels are missing, the selector returns NOT_READY and no validation claim should be made.",
        ],
    }


def _candidate_feature_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {}
    result: dict[str, Any] = {}
    for key in ("zin_center_real_ohm", "zin_center_imag_ohm", "zin_center_abs_ohm", "k_center", "qp_center", "qs_center"):
        values = np.asarray([float(item[key]) for item in candidates if item.get(key) is not None], dtype=float)
        if values.size:
            result[key] = {
                "min": float(np.min(values)),
                "max": float(np.max(values)),
                "p50": float(np.percentile(values, 50.0)),
            }
    return result


def _reason_counts(selected: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in selected:
        for reason in item.get("selection_reasons", []):
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def _zin_bin_coverage_summary(
    candidates: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    bins: int,
) -> dict[str, Any]:
    if len(candidates) < 2 or bins <= 0:
        return {"status": "NOT_READY", "reason": "not enough candidates or invalid bins"}
    real = np.asarray([float(item["zin_center_real_ohm"]) for item in candidates], dtype=float)
    imag = np.asarray([float(item["zin_center_imag_ohm"]) for item in candidates], dtype=float)
    real_edges = np.linspace(float(np.min(real)), float(np.max(real)), bins + 1)
    imag_edges = np.linspace(float(np.min(imag)), float(np.max(imag)), bins + 1)
    if np.allclose(real_edges[0], real_edges[-1]) or np.allclose(imag_edges[0], imag_edges[-1]):
        return {"status": "NOT_READY", "reason": "degenerate Zin range"}

    def occupied(rows: list[dict[str, Any]]) -> set[tuple[int, int]]:
        result = set()
        for item in rows:
            result.add(
                (
                    _bin_index(float(item["zin_center_real_ohm"]), real_edges),
                    _bin_index(float(item["zin_center_imag_ohm"]), imag_edges),
                )
            )
        return result

    candidate_bins = occupied(candidates)
    selected_bins = occupied(selected)
    return {
        "status": "PASS" if selected_bins else "NOT_READY",
        "bins": int(bins),
        "candidate_occupied_2d_bins": len(candidate_bins),
        "selected_occupied_2d_bins": len(selected_bins),
        "selected_fraction_of_candidate_bins": (len(selected_bins) / len(candidate_bins)) if candidate_bins else None,
        "selected_bins": [[int(real_bin), int(imag_bin)] for real_bin, imag_bin in sorted(selected_bins)],
    }


def _selected_record(item: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "evaluation": item["evaluation"],
        "source_index": item["source_index"],
        "selection_reasons": item["selection_reasons"],
        "touchstone_path": item["touchstone_path"],
        "touchstone_exists": item["touchstone_exists"],
        "touchstone_sha256": _sha256(Path(item["touchstone_path"])) if item.get("touchstone_exists") else "",
        "zin_center_real_ohm": item["zin_center_real_ohm"],
        "zin_center_imag_ohm": item["zin_center_imag_ohm"],
        "zin_center_abs_ohm": item["zin_center_abs_ohm"],
        "k_center": item.get("k_center"),
        "qp_center": item.get("qp_center"),
        "qs_center": item.get("qs_center"),
        "lp_nh_center": item.get("lp_nh_center"),
        "ls_nh_center": item.get("ls_nh_center"),
        "target_frequency_used_ghz": item.get("target_frequency_used_ghz"),
    }


def _write_selected_csv(path: Path, selected: list[dict[str, Any]]) -> None:
    records = [_selected_record(item, rank) for rank, item in enumerate(selected, start=1)]
    fields = [
        "rank",
        "evaluation",
        "source_index",
        "selection_reasons",
        "touchstone_path",
        "touchstone_exists",
        "touchstone_sha256",
        "zin_center_real_ohm",
        "zin_center_imag_ohm",
        "zin_center_abs_ohm",
        "k_center",
        "qp_center",
        "qs_center",
        "lp_nh_center",
        "ls_nh_center",
        "target_frequency_used_ghz",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["selection_reasons"] = ";".join(record["selection_reasons"])
            writer.writerow(row)


def _write_selection_plots(
    out_dir: Path,
    candidates: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[dict[str, str], list[str]]:
    if not candidates:
        return {}, []
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001 - plot evidence is optional.
        return {}, [f"matplotlib unavailable: {type(exc).__name__}: {exc}"]

    try:
        png, svg = _plot_zin_selection_map(out_dir, candidates, selected, args, plt)
    except Exception as exc:  # noqa: BLE001
        return {}, [f"Zin selection map failed: {type(exc).__name__}: {exc}"]
    return {"zin_selection_map_png": str(png), "zin_selection_map_svg": str(svg)}, []


def _plot_zin_selection_map(
    out_dir: Path,
    candidates: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    args: argparse.Namespace,
    plt: Any,
) -> tuple[Path, Path]:
    fig, ax = plt.subplots(figsize=(8.8, 6.6), facecolor="#FCFCFD")
    ax.set_facecolor("#FFFFFF")
    cand_real = [float(item["zin_center_real_ohm"]) for item in candidates]
    cand_imag = [float(item["zin_center_imag_ohm"]) for item in candidates]
    ax.scatter(
        cand_real,
        cand_imag,
        s=24,
        color="#C5CAD3",
        edgecolor="#7A828F",
        linewidth=0.4,
        alpha=0.65,
        label="Candidate rows",
    )
    if selected:
        sel_real = [float(item["zin_center_real_ohm"]) for item in selected]
        sel_imag = [float(item["zin_center_imag_ohm"]) for item in selected]
        ax.scatter(
            sel_real,
            sel_imag,
            s=72,
            color="#F0986E",
            edgecolor="#804126",
            linewidth=0.9,
            label="Selected HFSS samples",
            zorder=3,
        )
        selected_by_key = {
            (int(item["source_index"]), str(item["evaluation"])): rank
            for rank, item in enumerate(selected, start=1)
        }
        for item in selected:
            rank = selected_by_key[(int(item["source_index"]), str(item["evaluation"]))]
            ax.annotate(
                str(rank),
                (float(item["zin_center_real_ohm"]), float(item["zin_center_imag_ohm"])),
                textcoords="offset points",
                xytext=(5, 5),
                fontsize=8,
                color="#1F2430",
            )
    ax.axhline(0.0, color="#D7DBE7", linewidth=0.9)
    ax.axvline(0.0, color="#D7DBE7", linewidth=0.9)
    ax.set_xlabel("Re(Zin) ohm")
    ax.set_ylabel("Im(Zin) ohm")
    ax.grid(color="#E6E8F0", linewidth=0.8)
    ax.legend(loc="best", frameon=False)
    _style_axes(ax)
    _add_chart_header(
        fig,
        "HFSS validation samples across Zin space",
        f"Selected rows are chosen from real response labels at {args.target_frequency_ghz:g} GHz; all points come from dataset_rows.csv.",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    png = out_dir / "hfss_validation_sample_zin_map.png"
    svg = out_dir / "hfss_validation_sample_zin_map.svg"
    fig.savefig(png, dpi=180)
    fig.savefig(svg)
    plt.close(fig)
    return png, svg


def _add_chart_header(fig: Any, title: str, subtitle: str) -> None:
    fig.text(0.08, 0.975, title, ha="left", va="top", fontsize=13, fontweight="bold", color="#1F2430")
    fig.text(0.08, 0.94, subtitle, ha="left", va="top", fontsize=9, color="#6F768A")


def _style_axes(ax: Any) -> None:
    ax.tick_params(axis="both", colors="#6F768A")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#D7DBE7")
    ax.spines["bottom"].set_color("#D7DBE7")


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS Validation Sample Selection",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Candidate rows with real Zin labels: `{summary['candidate_count']}`",
        f"- Selected rows: `{summary['selected_count']}`",
        "",
        "## Selected Samples",
        "",
        "| Rank | Evaluation | Reasons | Re(Zin) | Im(Zin) | |Zin| | Touchstone |",
        "| ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in summary["selected"]:
        lines.append(
            f"| {item['rank']} | {item['evaluation']} | {'; '.join(item['selection_reasons'])} | "
            f"{item['zin_center_real_ohm']:.6g} | {item['zin_center_imag_ohm']:.6g} | "
            f"{item['zin_center_abs_ohm']:.6g} | `{item['touchstone_path']}` |"
        )
    if summary.get("plots"):
        lines.extend(["", "## Plots", ""])
        for name, path in summary["plots"].items():
            lines.append(f"- {name}: `{path}`")
    if summary.get("plot_errors"):
        lines.extend(["", "## Plot Errors", ""])
        lines.extend(f"- {item}" for item in summary["plot_errors"])
    lines.extend(
        [
            "",
            "## Selection Coverage",
            "",
            "```json",
            json.dumps(
                {
                    "selected_reason_counts": summary["selected_reason_counts"],
                    "zin_bin_coverage_summary": summary["zin_bin_coverage_summary"],
                    "selected_feature_summary": summary["selected_feature_summary"],
                },
                indent=2,
            ),
            "```",
            "",
            "## Candidate Feature Summary",
            "",
            "```json",
            json.dumps(summary["candidate_feature_summary"], indent=2),
            "```",
            "",
            "## Limits",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _render_commands(selected: list[dict[str, Any]], args: argparse.Namespace) -> str:
    lines = [
        "# Next Commands For Selected HFSS/ADS Validation Samples",
        "",
        "Use these as traceable inputs for HFSS model rebuilds and EMX/HFSS/ADS comparisons.",
        "They are not a substitute for running HFSS or ADS.",
        "",
    ]
    for rank, item in enumerate(selected, start=1):
        lines.extend(
            [
                f"## {rank}. {item['evaluation']}",
                "",
                f"- Reasons: `{';'.join(item['selection_reasons'])}`",
                f"- Touchstone: `{item['touchstone_path']}`",
                f"- Target frequency: `{args.target_frequency_ghz}` GHz",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _first_value(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if row.get(name) not in (None, ""):
            return row.get(name)
    return None


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() not in {"", "0", "false", "no", "n", "fail", "failed"}


def _as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
