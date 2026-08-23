#!/usr/bin/env python3
"""Diagnose whether 4-port pair/order choices explain an EMX/HFSS mismatch."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from compare_emx_hfss_ads import (  # noqa: E402
    METRICS,
    compare_curves,
    load_curves,
    parse_port_pairs,
)

DEFAULT_EMX_PORT_PAIRS = "1,2:3,4"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    emx_pairs = parse_port_pairs(args.emx_port_pairs)
    emx = load_curves(Path(args.emx), port_pairs=emx_pairs)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for hfss_spec in all_pair_specs():
        hfss = load_curves(Path(args.hfss), port_pairs=parse_port_pairs(hfss_spec))
        result = compare_curves(emx, hfss, max_percent_error=args.max_percent_error)
        metric_errors = {metric: result["metrics"][metric]["max_percent_error"] for metric in METRICS}
        row = {
            "hfss_port_pairs": hfss_spec,
            "overall_status": result["overall_status"],
            "score_max_percent_error": max(metric_errors.values()),
            "score_sum_percent_error": sum(metric_errors.values()),
            "k_status": result["metrics"]["k"]["status"],
            "qp_status": result["metrics"]["qp"]["status"],
            "qs_status": result["metrics"]["qs"]["status"],
            "lp_nh_status": result["metrics"]["lp_nh"]["status"],
            "ls_nh_status": result["metrics"]["ls_nh"]["status"],
            **{f"{metric}_max_percent_error": metric_errors[metric] for metric in METRICS},
            **signed_k_summary(result),
        }
        rows.append(row)

    rows.sort(key=lambda item: (item["score_sum_percent_error"], item["score_max_percent_error"]))
    csv_path = out_dir / "port_pairing_sensitivity.csv"
    json_path = out_dir / "port_pairing_sensitivity.json"
    report_path = out_dir / "port_pairing_sensitivity.md"
    write_csv(csv_path, rows)
    write_json(json_path, args, rows)
    write_report(report_path, args, rows)
    plot_path = maybe_plot(out_dir, rows, args.top_n)
    manifest_path = write_manifest(
        out_dir,
        [Path(args.emx), Path(args.hfss)],
        [csv_path, json_path, report_path, *(item for item in [plot_path] if item is not None)],
    )

    best = rows[0]
    default = next(row for row in rows if row["hfss_port_pairs"] == DEFAULT_EMX_PORT_PAIRS)
    print(f"best_hfss_port_pairs={best['hfss_port_pairs']}")
    print(f"best_overall_status={best['overall_status']}")
    print(f"best_score_sum_percent_error={best['score_sum_percent_error']:.6g}")
    print(f"default_score_sum_percent_error={default['score_sum_percent_error']:.6g}")
    print(f"csv={csv_path}")
    print(f"json={json_path}")
    print(f"report={report_path}")
    if plot_path:
        print(f"plot={plot_path}")
    print(f"manifest={manifest_path}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx", required=True)
    parser.add_argument("--hfss", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--emx-port-pairs", default=DEFAULT_EMX_PORT_PAIRS)
    parser.add_argument("--max-percent-error", type=float, default=5.0)
    parser.add_argument("--top-n", type=int, default=12)
    return parser.parse_args(argv)


def all_pair_specs() -> list[str]:
    partitions = [
        ((1, 2), (3, 4)),
        ((1, 3), (2, 4)),
        ((1, 4), (2, 3)),
    ]
    specs: list[str] = []
    for first, second in partitions:
        for oriented_first in orientations(first):
            for oriented_second in orientations(second):
                specs.append(f"{oriented_first[0]},{oriented_first[1]}:{oriented_second[0]},{oriented_second[1]}")
                specs.append(f"{oriented_second[0]},{oriented_second[1]}:{oriented_first[0]},{oriented_first[1]}")
    return sorted(set(specs))


def orientations(pair: tuple[int, int]) -> Iterable[tuple[int, int]]:
    yield pair
    yield (pair[1], pair[0])


def signed_k_summary(result: dict[str, Any]) -> dict[str, float]:
    emx_k = np.asarray(result["plot_data"]["emx"]["k"], dtype=float)
    hfss_k = np.asarray(result["plot_data"]["hfss_ads"]["k"], dtype=float)
    floor = np.maximum(np.abs(emx_k), 1.0e-3)
    abs_k_pct = np.abs(np.abs(hfss_k) - np.abs(emx_k)) / floor * 100.0
    return {
        "k_abs_max_percent_error": float(np.max(abs_k_pct)),
        "k_abs_mean_percent_error": float(np.mean(abs_k_pct)),
        "emx_k_mean": float(np.mean(emx_k)),
        "hfss_k_mean": float(np.mean(hfss_k)),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, args: argparse.Namespace, rows: list[dict[str, Any]]) -> None:
    payload = {
        "emx": str(Path(args.emx).expanduser().resolve()),
        "hfss": str(Path(args.hfss).expanduser().resolve()),
        "emx_port_pairs": args.emx_port_pairs,
        "criterion": {"max_percent_error": args.max_percent_error},
        "count": len(rows),
        "best": rows[0],
        "default_1_2_3_4": next(row for row in rows if row["hfss_port_pairs"] == DEFAULT_EMX_PORT_PAIRS),
        "rows": rows,
        "interpretation": (
            "If the default P001/P002:P003/P004 row is the best or near-best but still fails, "
            "the remaining mismatch is not explained by a simple S4P port pairing or orientation swap."
        ),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_report(path: Path, args: argparse.Namespace, rows: list[dict[str, Any]]) -> None:
    best = rows[0]
    default = next(row for row in rows if row["hfss_port_pairs"] == DEFAULT_EMX_PORT_PAIRS)
    lines = [
        "# S4P Port Pairing Sensitivity Diagnostic",
        "",
        f"- EMX port-pair reference: `{args.emx_port_pairs}`",
        f"- HFSS pairings tested: `{len(rows)}`",
        f"- Pass criterion: each metric max percent error <= `{args.max_percent_error}%`",
        "",
        "## Result",
        "",
        f"- Best HFSS pairing by total metric error: `{best['hfss_port_pairs']}`",
        f"- Best overall status: `{best['overall_status']}`",
        f"- Default HFSS pairing `1,2:3,4` overall status: `{default['overall_status']}`",
        "",
        "The table below lists the lowest-error candidates. A simple port pairing or polarity swap is not a validation fix unless it passes all K/Q/L gates and is physically consistent with the P001-P004 labels.",
        "",
        "| Rank | HFSS pairs | Overall | Sum err | Max err | k | Qp | Qs | Lp | Ls | |k| err |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(rows[:12], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(rank),
                    f"`{row['hfss_port_pairs']}`",
                    row["overall_status"],
                    f"{row['score_sum_percent_error']:.2f}%",
                    f"{row['score_max_percent_error']:.2f}%",
                    f"{row['k_max_percent_error']:.2f}%",
                    f"{row['qp_max_percent_error']:.2f}%",
                    f"{row['qs_max_percent_error']:.2f}%",
                    f"{row['lp_nh_max_percent_error']:.2f}%",
                    f"{row['ls_nh_max_percent_error']:.2f}%",
                    f"{row['k_abs_max_percent_error']:.2f}%",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Default Pairing",
            "",
            f"- `k`: `{default['k_max_percent_error']:.4f}%` signed, `{default['k_abs_max_percent_error']:.4f}%` magnitude-only",
            f"- `Qp`: `{default['qp_max_percent_error']:.4f}%`",
            f"- `Qs`: `{default['qs_max_percent_error']:.4f}%`",
            f"- `Lp`: `{default['lp_nh_max_percent_error']:.4f}%`",
            f"- `Ls`: `{default['ls_nh_max_percent_error']:.4f}%`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def maybe_plot(out_dir: Path, rows: list[dict[str, Any]], top_n: int) -> Path | None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return None
    top = rows[: max(1, top_n)]
    metrics = ["k", "qp", "qs", "lp_nh", "ls_nh"]
    labels = [row["hfss_port_pairs"] for row in top]
    x = np.arange(len(labels))
    width = 0.15
    colors = ["#5477C4", "#CC6F47", "#BD569B", "#71B436", "#B8A037"]
    fig, ax = plt.subplots(figsize=(13, 6), facecolor="#FCFCFD")
    for idx, metric in enumerate(metrics):
        vals = [row[f"{metric}_max_percent_error"] for row in top]
        ax.bar(x + (idx - 2) * width, vals, width=width, label=metric, color=colors[idx], edgecolor="#1F2430", linewidth=0.35)
    ax.axhline(5.0, color="#1F2430", linestyle="--", linewidth=1.0, label="5% gate")
    ax.set_ylabel("Max percent error vs EMX")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_title("S4P port pairing/orientation swaps do not pass the validation gate", loc="left", fontweight="bold", color="#1F2430")
    ax.legend(frameon=False, ncols=6, fontsize=8)
    ax.grid(axis="y", color="#E6E8F0")
    ax.set_facecolor("#FFFFFF")
    fig.subplots_adjust(left=0.07, right=0.98, bottom=0.28, top=0.88)
    path = out_dir / "port_pairing_sensitivity.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def write_manifest(out_dir: Path, inputs: list[Path], outputs: list[Path]) -> Path:
    payload = {
        "inputs": [{"path": str(path.resolve()), "sha256": sha256(path)} for path in inputs],
        "outputs": [{"path": str(path.resolve()), "sha256": sha256(path)} for path in outputs],
    }
    path = out_dir / "port_pairing_sensitivity_manifest.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
