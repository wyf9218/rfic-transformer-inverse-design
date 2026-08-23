#!/usr/bin/env python3
"""Visualize final-500 signal-to-shield clearance audit records."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main() -> int:
    args = _parse_args()
    audit_path = Path(args.audit_json).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    records = list(audit.get("records") or [])
    if not records:
        raise SystemExit(f"No records found in {audit_path}")

    rows = [_row_from_record(record, audit.get("selected", {}).get("cache_key")) for record in records]
    selected = next((row for row in rows if row["selected"]), None)
    status_counts = Counter(row["status"] for row in rows)
    reject_rows = [row for row in rows if not row["pass"]]
    reject_rows.sort(key=lambda row: row["violation_area_um2"], reverse=True)

    figures = []
    figures.append(_plot_pass_fail(rows, out_dir / "01_clearance_pass_fail_counts.png"))
    figures.append(_plot_violation_distribution(rows, out_dir / "02_clearance_violation_area_hist.png"))
    figures.append(_plot_bbox_centers(rows, selected, out_dir / "03_clearance_bbox_center_scatter.png"))
    figures.append(_plot_bbox_sizes(rows, selected, out_dir / "04_clearance_bbox_size_scatter.png"))

    top_rejects_path = out_dir / "top_rejects.csv"
    _write_top_rejects(top_rejects_path, reject_rows)

    summary = {
        "audit_json": str(audit_path),
        "out_dir": str(out_dir),
        "record_count": len(rows),
        "pass_count": sum(1 for row in rows if row["pass"]),
        "reject_count": sum(1 for row in rows if not row["pass"]),
        "status_counts": dict(status_counts),
        "selected_cache_key": selected["cache_key"] if selected else None,
        "selected_status": selected["status"] if selected else None,
        "max_direct_overlap_area_um2": max(row["direct_overlap_area_um2"] for row in rows),
        "max_violation_area_um2": max(row["violation_area_um2"] for row in rows),
        "mean_violation_area_um2": float(np.mean([row["violation_area_um2"] for row in rows])),
        "top_rejects_csv": str(top_rejects_path),
        "figures": [str(path) for path in figures],
    }
    (out_dir / "clearance_audit_visual_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "clearance_audit_report.md").write_text(_render_report(summary), encoding="utf-8")

    print(f"visualizations={out_dir}")
    print(f"records={summary['record_count']}")
    print(f"pass={summary['pass_count']}")
    print(f"reject={summary['reject_count']}")
    print(f"report={out_dir / 'clearance_audit_report.md'}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit_json", help="final500_ground_clearance_audit.json")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    return parser.parse_args()


def _row_from_record(record: dict[str, Any], selected_cache_key: str | None) -> dict[str, Any]:
    primary = _bbox(record, "primary")
    secondary = _bbox(record, "secondary")
    status = str(record.get("status") or "unknown")
    return {
        "cache_key": str(record.get("cache_key") or ""),
        "status": status,
        "pass": status == "pass_signal_to_shield_clearance",
        "selected": str(record.get("cache_key") or "") == str(selected_cache_key or ""),
        "direct_overlap_area_um2": _as_float(record.get("direct_signal_shield_overlap_area_um2")),
        "violation_area_um2": _as_float(record.get("signal_shield_clearance_violation_area_um2")),
        "primary_cx_um": primary["cx"],
        "primary_cy_um": primary["cy"],
        "primary_w_um": primary["w"],
        "primary_h_um": primary["h"],
        "secondary_cx_um": secondary["cx"],
        "secondary_cy_um": secondary["cy"],
        "secondary_w_um": secondary["w"],
        "secondary_h_um": secondary["h"],
    }


def _bbox(record: dict[str, Any], name: str) -> dict[str, float]:
    values = (((record.get("bbox_um") or {}).get(name)) or [0.0, 0.0, 0.0, 0.0])
    x0, y0, x1, y1 = [float(value) for value in values]
    return {"cx": 0.5 * (x0 + x1), "cy": 0.5 * (y0 + y1), "w": abs(x1 - x0), "h": abs(y1 - y0)}


def _plot_pass_fail(rows: list[dict[str, Any]], path: Path) -> Path:
    passed = sum(1 for row in rows if row["pass"])
    rejected = len(rows) - passed
    fig, ax = plt.subplots(figsize=(7, 4.5))
    bars = ax.bar(["Pass", "Reject"], [passed, rejected], color=["#2563EB", "#DC2626"], alpha=0.82)
    ax.set_ylabel("count")
    ax.set_title("Final-500 Signal-to-Shield Clearance Gate")
    ax.bar_label(bars, padding=4)
    ax.set_ylim(0, max(passed, rejected) * 1.15)
    ax.text(
        0.0,
        -0.17,
        "Source: final500_ground_clearance_audit.json. Geometry clearance only, not EM/Zin coverage.",
        transform=ax.transAxes,
        fontsize=8,
        color="#555555",
    )
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def _plot_violation_distribution(rows: list[dict[str, Any]], path: Path) -> Path:
    rejects = np.array([row["violation_area_um2"] for row in rows if not row["pass"]], dtype=float)
    fig, ax = plt.subplots(figsize=(8, 4.8))
    if rejects.size:
        ax.hist(rejects, bins=min(16, max(6, int(np.sqrt(rejects.size)))), color="#F97316", edgecolor="white")
        ax.axvline(float(np.max(rejects)), color="#991B1B", linestyle="--", linewidth=1.4, label=f"max={np.max(rejects):.1f} um^2")
        ax.legend(frameon=False)
    ax.set_title("Rejected Samples: Clearance Violation Area")
    ax.set_xlabel("signal-to-shield clearance violation area (um^2)")
    ax.set_ylabel("count")
    ax.text(
        0.0,
        -0.18,
        "Only rejected records are shown. Passed records have zero violation area by the audit rule.",
        transform=ax.transAxes,
        fontsize=8,
        color="#555555",
    )
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def _plot_bbox_centers(rows: list[dict[str, Any]], selected: dict[str, Any] | None, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(7.2, 6))
    for passed, color, label in [(True, "#2563EB", "pass"), (False, "#DC2626", "reject")]:
        subset = [row for row in rows if row["pass"] is passed]
        ax.scatter(
            [row["primary_cx_um"] for row in subset],
            [row["secondary_cx_um"] for row in subset],
            s=22,
            alpha=0.72,
            color=color,
            label=label,
            linewidths=0,
        )
    if selected:
        ax.scatter(
            [selected["primary_cx_um"]],
            [selected["secondary_cx_um"]],
            s=140,
            facecolors="none",
            edgecolors="#111827",
            linewidths=2.0,
            label="selected sample",
        )
    ax.set_xlabel("primary bbox center x (um)")
    ax.set_ylabel("secondary bbox center x (um)")
    ax.set_title("Final-500 Geometry Position Coverage")
    ax.legend(frameon=False)
    ax.grid(True, alpha=0.22)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def _plot_bbox_sizes(rows: list[dict[str, Any]], selected: dict[str, Any] | None, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(7.2, 6))
    for passed, color, label in [(True, "#2563EB", "pass"), (False, "#DC2626", "reject")]:
        subset = [row for row in rows if row["pass"] is passed]
        ax.scatter(
            [row["primary_w_um"] for row in subset],
            [row["secondary_w_um"] for row in subset],
            s=22,
            alpha=0.72,
            color=color,
            label=label,
            linewidths=0,
        )
    if selected:
        ax.scatter(
            [selected["primary_w_um"]],
            [selected["secondary_w_um"]],
            s=140,
            facecolors="none",
            edgecolors="#111827",
            linewidths=2.0,
            label="selected sample",
        )
    ax.set_xlabel("primary bbox width (um)")
    ax.set_ylabel("secondary bbox width (um)")
    ax.set_title("Final-500 Size Coverage And Clearance Rejects")
    ax.legend(frameon=False)
    ax.grid(True, alpha=0.22)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def _write_top_rejects(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "cache_key",
        "status",
        "violation_area_um2",
        "direct_overlap_area_um2",
        "primary_cx_um",
        "primary_w_um",
        "secondary_cx_um",
        "secondary_w_um",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows[:25]:
            writer.writerow({field: row[field] for field in fields})


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Final-500 Clearance Audit Visual Report",
        "",
        f"- Source: `{summary['audit_json']}`",
        f"- Records: `{summary['record_count']}`",
        f"- Pass: `{summary['pass_count']}`",
        f"- Reject: `{summary['reject_count']}`",
        f"- Selected sample: `{summary['selected_cache_key']}`",
        f"- Selected status: `{summary['selected_status']}`",
        f"- Max direct overlap area: `{summary['max_direct_overlap_area_um2']:.6g} um^2`",
        f"- Max clearance violation area: `{summary['max_violation_area_um2']:.6g} um^2`",
        "",
        "These figures prove only the geometry clearance gate. They do not prove S-parameter or Zin coverage.",
        "",
        "## Figures",
        "",
    ]
    for figure in summary["figures"]:
        lines.append(f"- `{figure}`")
    lines.extend(["", f"Top rejects CSV: `{summary['top_rejects_csv']}`", ""])
    return "\n".join(lines)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
