#!/usr/bin/env python3
"""Plan the next acquisition chunk from under-filled Zin bins.

This script is post-processing only. It reads completed dataset rows with real
Zin labels, bins the center-frequency Re/Im(Zin) plane, and writes a prioritized
list of response-space target bins for the next EMX/Cadence acquisition chunk.

It does not invent geometry-to-Zin labels. The intended workflow is:

1. Generate and simulate a pilot batch.
2. Run Zin coverage audits on the real labels.
3. Use this plan to bias the next candidate search toward under-filled Zin bins.
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_target_envelope_config(args)
    if args.next_count is None:
        args.next_count = 100
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else dataset_dir / "zin_balanced_acquisition_plan"
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset_csv = dataset_dir / "dataset_rows.csv"
    dataset_source = _dataset_source_summary(dataset_csv)
    rows = _read_rows(dataset_csv)
    ok_rows = [row for row in rows if _truthy(row.get("ok", "true"))]
    labels = _collect_labels(ok_rows, args.real_column, args.imag_column)
    envelope = _resolve_envelope(labels, args)
    bins = _build_bins(labels, envelope, args)
    targets = _select_targets(bins, args)
    checks = _build_checks(rows, ok_rows, labels, envelope, bins, targets, args)
    overall_status = "NOT_READY" if labels["valid_count"] == 0 else "PASS"

    bins_csv = out_dir / "zin_balanced_acquisition_bins.csv"
    targets_csv = out_dir / "zin_balanced_acquisition_targets.csv"
    summary_path = out_dir / "zin_balanced_acquisition_plan_summary.json"
    report_path = out_dir / "zin_balanced_acquisition_plan_report.md"
    figures = [] if args.no_plots or labels["valid_count"] == 0 else _write_plots(out_dir, labels, envelope, bins, targets)
    _write_csv(bins_csv, bins)
    _write_csv(targets_csv, targets)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "dataset_source": {**dataset_source, "csv_row_count": len(rows), "ok_row_count": len(ok_rows)},
        "out_dir": str(out_dir),
        "overall_status": overall_status,
        "plan_status": _plan_status(labels, bins, targets),
        "rows": {"row_count": len(rows), "ok_count": len(ok_rows)},
        "label_summary": _label_summary(labels),
        "target_envelope_config": getattr(args, "_target_envelope_config_summary", {"configured": False}),
        "planning_envelope": envelope,
        "bin_summary": _bin_summary(bins),
        "target_summary": _target_summary(targets),
        "bins_csv": str(bins_csv),
        "targets_csv": str(targets_csv),
        "figures": figures,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This script plans response-space targets from existing Zin labels only.",
            "It does not predict which geometry will hit each target bin by itself.",
            "Use it with a candidate generator, surrogate model, or active-learning loop that can propose manufacturable geometries for sparse bins.",
            "A PASS here means the plan is computable and traceable; final dataset acceptance still requires real EMX/HFSS/ADS validation.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"plan_status={summary['plan_status']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"bins_csv={bins_csv}")
    print(f"targets_csv={targets_csv}")
    for check in checks:
        print(f"{check['status']:9s} {check['name']}: {check['detail']}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--real-column", default="zin_center_real_ohm")
    parser.add_argument("--imag-column", default="zin_center_imag_ohm")
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--target-envelope-config", help="JSON file containing reusable Zin target-envelope bounds")
    parser.add_argument("--target-real-min-ohm", type=float)
    parser.add_argument("--target-real-max-ohm", type=float)
    parser.add_argument("--target-imag-min-ohm", type=float)
    parser.add_argument("--target-imag-max-ohm", type=float)
    parser.add_argument("--target-count-per-bin", type=int)
    parser.add_argument("--desired-total-count", type=int, help="Total desired samples in the target envelope; converted to count per bin")
    parser.add_argument("--next-count", type=int, help="Total recommended new samples allocated across sparse bins")
    parser.add_argument("--max-target-bins", type=int, help="Maximum number of sparse bins to include in the target list")
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _apply_target_envelope_config(args: argparse.Namespace) -> None:
    path_raw = getattr(args, "target_envelope_config", None)
    if not path_raw:
        args._target_envelope_config_summary = {"configured": False, "status": "NOT_CONFIGURED"}
        return
    path = Path(path_raw).expanduser().resolve()
    summary: dict[str, Any] = {"configured": True, "path": str(path)}
    if not path.is_file():
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"missing config file: {path}"}
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - keep exact parser issue.
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
        return
    if not isinstance(data, dict):
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"top-level JSON is {type(data).__name__}"}
        return
    if "TEMPLATE_ONLY" in str(data.get("status", "")).upper():
        args._target_envelope_config_summary = {
            **summary,
            "status": "FAIL",
            "error": "target envelope config is marked TEMPLATE_ONLY; fill a project-specific copy before using it",
        }
        return
    envelope = data.get("zin_target_envelope", data)
    if not isinstance(envelope, dict):
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": "zin_target_envelope is not an object"}
        return
    field_map = {
        "real_min_ohm": "target_real_min_ohm",
        "real_max_ohm": "target_real_max_ohm",
        "imag_min_ohm": "target_imag_min_ohm",
        "imag_max_ohm": "target_imag_max_ohm",
        "target_count_per_bin": "target_count_per_bin",
        "desired_total_count": "desired_total_count",
        "next_count": "next_count",
    }
    applied: dict[str, Any] = {}
    invalid: list[str] = []
    for source_key, arg_name in field_map.items():
        if source_key not in envelope or envelope[source_key] is None or getattr(args, arg_name) is not None:
            continue
        try:
            value: Any
            if arg_name in {"target_count_per_bin", "desired_total_count", "next_count"}:
                value = int(envelope[source_key])
            else:
                value = float(envelope[source_key])
        except (TypeError, ValueError):
            invalid.append(f"{source_key}={envelope[source_key]!r}")
            continue
        setattr(args, arg_name, value)
        applied[arg_name] = value
    if invalid:
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"invalid numeric fields: {invalid}"}
        return
    args._target_envelope_config_summary = {
        **summary,
        "status": "PASS",
        "schema": data.get("schema", "direct_or_zin_target_envelope"),
        "name": data.get("name") or envelope.get("name"),
        "applied_fields": applied,
        "notes": data.get("notes", []),
    }


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _dataset_source_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
    }
    if not path.exists():
        return summary
    digest = hashlib.sha256()
    line_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            line_count += chunk.count(b"\n")
    summary.update(
        {
            "size_bytes": path.stat().st_size,
            "sha256": digest.hexdigest(),
            "line_count": line_count,
            "data_line_count_estimate": max(0, line_count - 1),
        }
    )
    return summary


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


def _collect_labels(rows: list[dict[str, str]], real_column: str, imag_column: str) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        real = _as_float(row.get(real_column))
        imag = _as_float(row.get(imag_column))
        if real is None or imag is None:
            continue
        points.append(
            {
                "row_index": idx,
                "evaluation": row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or "",
                "real_ohm": real,
                "imag_ohm": imag,
            }
        )
    real_arr = np.asarray([point["real_ohm"] for point in points], dtype=float)
    imag_arr = np.asarray([point["imag_ohm"] for point in points], dtype=float)
    return {"points": points, "valid_count": len(points), "real": real_arr, "imag": imag_arr}


def _resolve_envelope(labels: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    explicit = {
        "real_min_ohm": args.target_real_min_ohm,
        "real_max_ohm": args.target_real_max_ohm,
        "imag_min_ohm": args.target_imag_min_ohm,
        "imag_max_ohm": args.target_imag_max_ohm,
    }
    if all(value is not None for value in explicit.values()):
        real_min = float(explicit["real_min_ohm"])
        real_max = float(explicit["real_max_ohm"])
        imag_min = float(explicit["imag_min_ohm"])
        imag_max = float(explicit["imag_max_ohm"])
        source = "explicit_target_envelope"
    elif labels["valid_count"]:
        real_min, real_max = _padded_min_max(labels["real"])
        imag_min, imag_max = _padded_min_max(labels["imag"])
        source = "observed_label_range_with_5pct_padding"
    else:
        real_min = real_max = imag_min = imag_max = None
        source = "unavailable_no_labels"
    status = "PASS"
    error = None
    if real_min is None or real_max is None or imag_min is None or imag_max is None:
        status = "NOT_READY"
        error = "no target envelope and no valid labels"
    elif real_max <= real_min or imag_max <= imag_min:
        status = "FAIL"
        error = "target envelope max bounds must be greater than min bounds"
    bin_count = int(args.bins)
    target_count = _target_count_per_bin(args, bin_count)
    return {
        "status": status,
        "source": source,
        "error": error,
        "real_min_ohm": real_min,
        "real_max_ohm": real_max,
        "imag_min_ohm": imag_min,
        "imag_max_ohm": imag_max,
        "bins": bin_count,
        "target_count_per_bin": target_count,
        "desired_total_count": None if args.desired_total_count is None else int(args.desired_total_count),
        "next_count": None if args.next_count is None else int(args.next_count),
    }


def _target_count_per_bin(args: argparse.Namespace, bins: int) -> int:
    if args.target_count_per_bin is not None:
        return max(0, int(args.target_count_per_bin))
    if args.desired_total_count is not None:
        return int(math.ceil(max(0, int(args.desired_total_count)) / float(max(1, bins * bins))))
    return 1


def _padded_min_max(arr: np.ndarray) -> tuple[float, float]:
    lo = float(np.min(arr))
    hi = float(np.max(arr))
    pad = 0.05 * max(hi - lo, 1.0)
    return lo - pad, hi + pad


def _build_bins(labels: dict[str, Any], envelope: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    if labels["valid_count"] == 0 or envelope["status"] != "PASS":
        return []
    bins = int(args.bins)
    hist, real_edges, imag_edges = np.histogram2d(
        labels["real"],
        labels["imag"],
        bins=bins,
        range=[
            (float(envelope["real_min_ohm"]), float(envelope["real_max_ohm"])),
            (float(envelope["imag_min_ohm"]), float(envelope["imag_max_ohm"])),
        ],
    )
    target_count = int(envelope["target_count_per_bin"])
    rows: list[dict[str, Any]] = []
    for i in range(bins):
        for j in range(bins):
            count = int(hist[i, j])
            deficit = max(0, target_count - count)
            rows.append(
                {
                    "real_bin": i,
                    "imag_bin": j,
                    "real_min_ohm": float(real_edges[i]),
                    "real_max_ohm": float(real_edges[i + 1]),
                    "imag_min_ohm": float(imag_edges[j]),
                    "imag_max_ohm": float(imag_edges[j + 1]),
                    "target_real_ohm": float((real_edges[i] + real_edges[i + 1]) / 2.0),
                    "target_imag_ohm": float((imag_edges[j] + imag_edges[j + 1]) / 2.0),
                    "current_count": count,
                    "target_count": target_count,
                    "deficit": deficit,
                    "status": "underfilled" if deficit > 0 else "covered",
                }
            )
    return rows


def _select_targets(bins: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    sparse = [row for row in bins if int(row["deficit"]) > 0]
    sparse.sort(key=lambda row: (-int(row["deficit"]), int(row["current_count"]), int(row["real_bin"]), int(row["imag_bin"])))
    if args.max_target_bins is not None:
        sparse = sparse[: max(0, int(args.max_target_bins))]
    allocations = _allocate_new_samples_across_sparse_bins(sparse, args.next_count)
    targets: list[dict[str, Any]] = []
    for rank, (row, recommended) in enumerate(zip(sparse, allocations), start=1):
        deficit = int(row["deficit"])
        if recommended <= 0:
            continue
        targets.append(
            {
                "rank": rank,
                "real_bin": row["real_bin"],
                "imag_bin": row["imag_bin"],
                "target_real_ohm": row["target_real_ohm"],
                "target_imag_ohm": row["target_imag_ohm"],
                "real_min_ohm": row["real_min_ohm"],
                "real_max_ohm": row["real_max_ohm"],
                "imag_min_ohm": row["imag_min_ohm"],
                "imag_max_ohm": row["imag_max_ohm"],
                "current_count": row["current_count"],
                "target_count": row["target_count"],
                "deficit": deficit,
                "recommended_new_samples": recommended,
                "priority_weight": float(deficit / max(1, int(row["target_count"]))),
            }
        )
    return targets


def _allocate_new_samples_across_sparse_bins(sparse: list[dict[str, Any]], next_count: int | None) -> list[int]:
    if not sparse:
        return []
    deficits = [max(0, int(row["deficit"])) for row in sparse]
    if next_count is None:
        return deficits
    remaining = max(0, int(next_count))
    allocations = [0 for _ in sparse]
    active = [idx for idx, deficit in enumerate(deficits) if deficit > 0]
    if remaining <= 0 or not active:
        return allocations

    # First spread the acquisition budget across sparse bins. This prevents a
    # small next chunk, for example 500 samples toward a 248k target envelope,
    # from being assigned entirely to one empty Zin bin.
    base = remaining // len(active)
    extra = remaining % len(active)
    for order, idx in enumerate(active):
        requested = base + (1 if order < extra else 0)
        assigned = min(deficits[idx], requested)
        allocations[idx] += assigned
        remaining -= assigned

    # If some bins had small deficits, redistribute the leftover budget one
    # sample at a time over bins that can still absorb samples.
    cursor = 0
    while remaining > 0:
        refillable = [idx for idx in active if allocations[idx] < deficits[idx]]
        if not refillable:
            break
        idx = refillable[cursor % len(refillable)]
        allocations[idx] += 1
        remaining -= 1
        cursor += 1
    return allocations


def _build_checks(
    rows: list[dict[str, str]],
    ok_rows: list[dict[str, str]],
    labels: dict[str, Any],
    envelope: dict[str, Any],
    bins: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    checks = [
        _check("PASS" if rows else "FAIL", "dataset rows", f"rows={len(rows)}, ok_rows={len(ok_rows)}"),
        _check("PASS" if labels["valid_count"] else "NOT_READY", "Zin labels", f"valid={labels['valid_count']}"),
        _check(str(envelope["status"]), "planning envelope", envelope.get("error") or envelope.get("source", "")),
    ]
    config = getattr(args, "_target_envelope_config_summary", {"configured": False})
    if config.get("configured"):
        checks.append(_check(str(config.get("status", "FAIL")), "target envelope config", config.get("error", f"applied={config.get('applied_fields', {})}")))
    if labels["valid_count"]:
        checks.append(_check("PASS" if bins else "FAIL", "Zin bins", f"bins={len(bins)}"))
        checks.append(_check("PASS" if targets else "WARN", "recommended sparse-bin targets", f"target_bins={len(targets)}"))
    return checks


def _check(status: str, name: str, detail: str) -> dict[str, str]:
    return {"status": status, "name": name, "detail": detail}


def _plan_status(labels: dict[str, Any], bins: list[dict[str, Any]], targets: list[dict[str, Any]]) -> str:
    if labels["valid_count"] == 0:
        return "NOT_READY"
    if not bins:
        return "NO_BINS"
    if not targets:
        return "TARGET_BINS_ALREADY_COVERED"
    return "SPARSE_BINS_PRIORITIZED"


def _label_summary(labels: dict[str, Any]) -> dict[str, Any]:
    return {
        "valid_count": int(labels["valid_count"]),
        "real_ohm": _range_summary(labels["real"]),
        "imag_ohm": _range_summary(labels["imag"]),
    }


def _range_summary(arr: np.ndarray) -> dict[str, float | None]:
    if arr.size == 0:
        return {"min": None, "max": None, "mean": None, "std": None}
    return {"min": float(np.min(arr)), "max": float(np.max(arr)), "mean": float(np.mean(arr)), "std": float(np.std(arr))}


def _bin_summary(bins: list[dict[str, Any]]) -> dict[str, Any]:
    if not bins:
        return {"bin_count": 0}
    counts = np.asarray([int(row["current_count"]) for row in bins], dtype=float)
    deficits = np.asarray([int(row["deficit"]) for row in bins], dtype=float)
    return {
        "bin_count": len(bins),
        "covered_bins": int(np.sum(counts >= np.asarray([int(row["target_count"]) for row in bins], dtype=float))),
        "underfilled_bins": int(np.sum(deficits > 0)),
        "empty_bins": int(np.sum(counts == 0)),
        "max_count": int(np.max(counts)),
        "max_deficit": int(np.max(deficits)),
        "total_deficit": int(np.sum(deficits)),
    }


def _target_summary(targets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "target_bin_count": len(targets),
        "recommended_new_sample_count": int(sum(int(row["recommended_new_samples"]) for row in targets)),
    }


def _write_plots(
    out_dir: Path,
    labels: dict[str, Any],
    envelope: dict[str, Any],
    bins: list[dict[str, Any]],
    targets: list[dict[str, Any]],
) -> list[dict[str, str]]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []
    figures: list[dict[str, str]] = []
    bins_n = int(envelope["bins"])
    deficits = np.zeros((bins_n, bins_n), dtype=float)
    counts = np.zeros((bins_n, bins_n), dtype=float)
    for row in bins:
        i = int(row["real_bin"])
        j = int(row["imag_bin"])
        deficits[i, j] = float(row["deficit"])
        counts[i, j] = float(row["current_count"])
    extent = [
        float(envelope["real_min_ohm"]),
        float(envelope["real_max_ohm"]),
        float(envelope["imag_min_ohm"]),
        float(envelope["imag_max_ohm"]),
    ]
    fig, ax = plt.subplots(figsize=(7, 5.5))
    image = ax.imshow(deficits.T, origin="lower", aspect="auto", extent=extent, cmap="magma")
    ax.set_title("Under-filled Zin bins for next acquisition")
    ax.set_xlabel("Re(Zin) ohm")
    ax.set_ylabel("Im(Zin) ohm")
    fig.colorbar(image, ax=ax, label="sample deficit")
    fig.tight_layout()
    path = out_dir / "01_zin_bin_deficit_heatmap.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    figures.append({"title": "Zin sparse-bin deficit heatmap", "path": str(path)})

    fig, ax = plt.subplots(figsize=(7, 5.5))
    ax.scatter(labels["real"], labels["imag"], s=12, alpha=0.45, color="#2563EB", label="existing labels")
    if targets:
        ax.scatter(
            [row["target_real_ohm"] for row in targets],
            [row["target_imag_ohm"] for row in targets],
            s=[max(30, 18 * int(row["recommended_new_samples"])) for row in targets],
            facecolors="none",
            edgecolors="#DC2626",
            linewidths=1.5,
            label="recommended sparse-bin centers",
        )
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_title("Existing Zin labels and recommended next targets")
    ax.set_xlabel("Re(Zin) ohm")
    ax.set_ylabel("Im(Zin) ohm")
    ax.grid(True, linewidth=0.3, alpha=0.35)
    ax.legend(loc="best", frameon=False)
    fig.tight_layout()
    path = out_dir / "02_next_zin_targets_overlay.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    figures.append({"title": "Zin next-target overlay", "path": str(path)})
    return figures


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Zin Balanced Acquisition Plan",
        "",
        f"- Generated UTC: `{summary['generated_utc']}`",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Dataset CSV SHA256: `{summary['dataset_source'].get('sha256', 'missing')}`",
        f"- Dataset CSV rows: `{summary['dataset_source'].get('csv_row_count', 0)}`",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Plan status: **{summary['plan_status']}**",
        f"- Valid Zin labels: `{summary['label_summary']['valid_count']}`",
        f"- Target bins: `{summary['target_summary']['target_bin_count']}`",
        f"- Recommended new samples: `{summary['target_summary']['recommended_new_sample_count']}`",
        f"- Targets CSV: `{summary['targets_csv']}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## Method", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.extend(["", "## Figures", ""])
    if summary["figures"]:
        for figure in summary["figures"]:
            lines.append(f"- {figure['title']}: `{figure['path']}`")
    else:
        lines.append("- No figures were generated.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
