#!/usr/bin/env python3
"""Build auditable evidence for the S4P that matches the user's ADS photo."""

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
from scan_s4p_ads_photo_reference_candidates import _source_kind  # noqa: E402


DEFAULT_S4P = Path("/home/researcher/Downloads/test of answer 2.s4p")
METRIC_ORDER = (
    ("lp_nh", "Lp", "nH", "#2563eb"),
    ("ls_nh", "Ls", "nH", "#dc2626"),
    ("k", "K", "", "#c2410c"),
    ("qp", "Qp", "", "#3348a3"),
    ("qs", "Qs", "", "#be123c"),
    ("cm_single_primary_ff", "Cm", "fF", "#047857"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--s4p", default=str(DEFAULT_S4P))
    parser.add_argument("--out-dir", default=str(DEFAULT_PACKAGE_DIR / "photo_matched_hfss_reference_20260613"))
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=None)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    s4p_path = Path(args.s4p).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    metadata = parse_touchstone_metadata(s4p_path)
    curves = _extract_metric_curves(s4p_path.stem, s4p_path, args.port_pairs)
    target = _target_record(curves, args)
    source_kind = _source_kind(s4p_path)
    metric_fail_count = sum(1 for check in target["checks"] if check["status"] == "FAIL")
    frequency = _frequency_summary(curves.freq_hz)
    source_is_emx = source_kind == "EMX"
    source_declares_hfss = _declares_hfss(metadata)
    metrics_pass = metric_fail_count == 0
    overall_status = "PASS" if metrics_pass and source_is_emx else ("REVIEW_REQUIRED" if metrics_pass else "FAIL")

    csv_path = out_dir / "photo_matched_reference_metrics.csv"
    plot_path = out_dir / "photo_matched_reference_ads_style_metrics.png"
    summary_path = out_dir / "photo_matched_reference_summary.json"
    report_path = out_dir / "photo_matched_reference_report.md"
    _write_metrics_csv(csv_path, curves)
    _write_reference_plot(plot_path, curves, target, args.target_ghz, args.dpi)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "touchstone": str(s4p_path),
        "source_kind_from_path": source_kind,
        "source_declares_hfss": bool(source_declares_hfss),
        "port_pairs": args.port_pairs,
        "target_ghz": float(args.target_ghz),
        "frequency_ghz": frequency,
        "metadata": metadata,
        "target_record": target,
        "artifacts": {
            "plot": str(plot_path),
            "csv": str(csv_path),
            "report": str(report_path),
        },
        "notes": [
            "This file matches the user's ADS correct-curve photo at 15 GHz, but its header declares an HFSS export.",
            "Because it is not an EMX-labeled source, it is evidence for provenance recovery only and cannot satisfy the EMX reference-source gate.",
            "The sweep covers the source file frequency range only; it is not a 5-50 GHz, 0.1 GHz-step production dataset.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"plot={plot_path}")
    print(f"csv={csv_path}")
    print(f"metric_fail_count={metric_fail_count} source_kind={source_kind} source_declares_hfss={source_declares_hfss}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def parse_touchstone_metadata(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {"header_fields": {}, "variables": {}, "ports": {}, "option_line": None}
    in_variables = False
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                metadata["option_line"] = line
                continue
            if not line.startswith("!"):
                if metadata.get("option_line") is not None:
                    break
                continue
            text = line[1:].strip()
            if text == "Variables:":
                in_variables = True
                continue
            port_match = re.match(r"Port\[(\d+)\]\s*=\s*(.+)", text)
            if port_match:
                metadata["ports"][port_match.group(1)] = port_match.group(2).strip()
                continue
            if in_variables and "=" in text:
                name, value = text.split("=", 1)
                metadata["variables"][name.strip()] = value.strip()
                continue
            if ":" in text:
                key, value = text.split(":", 1)
                metadata["header_fields"][key.strip()] = value.strip()
    return metadata


def _declares_hfss(metadata: dict[str, Any]) -> bool:
    haystack = json.dumps(metadata, ensure_ascii=False).lower()
    return "hfss" in haystack


def _target_record(curves: Any, args: argparse.Namespace) -> dict[str, Any]:
    freq_ghz = curves.freq_hz / 1.0e9
    idx = int(np.argmin(np.abs(freq_ghz - float(args.target_ghz))))
    actuals = {
        "lp_nh": float(curves.lp_nh[idx]),
        "ls_nh": float(curves.ls_nh[idx]),
        "k": float(curves.k[idx]),
        "qp": float(curves.qp[idx]),
        "qs": float(curves.qs[idx]),
        "cm_single_primary_ff": float(curves.cm_single_primary_ff[idx]),
    }
    return {
        "nearest_frequency_ghz": float(freq_ghz[idx]),
        "actuals": actuals,
        "checks": [_metric_check(spec, actuals[spec.key], args.max_percent_error) for spec in REFERENCE_METRICS],
    }


def _frequency_summary(freq_hz: np.ndarray) -> dict[str, Any]:
    diffs = np.diff(freq_hz)
    return {
        "start": float(freq_hz[0] / 1.0e9),
        "stop": float(freq_hz[-1] / 1.0e9),
        "points": int(len(freq_hz)),
        "step": float(diffs[0] / 1.0e9) if len(diffs) else None,
    }


def _write_metrics_csv(path: Path, curves: Any) -> None:
    fields = ["freq_hz", "freq_ghz", *(key for key, *_ in METRIC_ORDER)]
    arrays = {
        "lp_nh": curves.lp_nh,
        "ls_nh": curves.ls_nh,
        "k": curves.k,
        "qp": curves.qp,
        "qs": curves.qs,
        "cm_single_primary_ff": curves.cm_single_primary_ff,
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for idx, freq_hz in enumerate(curves.freq_hz):
            writer.writerow(
                {
                    "freq_hz": float(freq_hz),
                    "freq_ghz": float(freq_hz / 1.0e9),
                    **{key: float(values[idx]) for key, values in arrays.items()},
                }
            )


def _write_reference_plot(path: Path, curves: Any, target: dict[str, Any], target_ghz: float, dpi: int) -> None:
    import matplotlib.pyplot as plt

    freq_ghz = curves.freq_hz / 1.0e9
    arrays = {
        "lp_nh": curves.lp_nh,
        "ls_nh": curves.ls_nh,
        "k": curves.k,
        "qp": curves.qp,
        "qs": curves.qs,
        "cm_single_primary_ff": curves.cm_single_primary_ff,
    }
    reference = {spec.key: spec.expected for spec in REFERENCE_METRICS}
    checks = {check["metric"]: check for check in target["checks"]}
    fig, axes = plt.subplots(2, 3, figsize=(14, 8.2), constrained_layout=True)
    fig.suptitle("Photo-matched S4P clue: ADS-style physical metrics (HFSS export, not EMX)", fontsize=15, fontweight="bold")
    for ax, (key, label, unit, color) in zip(axes.ravel(), METRIC_ORDER):
        values = arrays[key]
        ax.plot(freq_ghz, values, color=color, linewidth=1.9, label=label)
        ax.axvline(float(target_ghz), color="#111827", linewidth=1.0, linestyle=":")
        ax.scatter([target["nearest_frequency_ghz"]], [target["actuals"][key]], color="#111827", s=22, zorder=5, label="15 GHz actual")
        ax.scatter([target["nearest_frequency_ghz"]], [reference[key]], color="#f59e0b", s=30, marker="x", zorder=6, label="photo marker")
        ax.set_title(label if not unit else f"{label} ({unit})")
        ax.set_xlabel("freq (GHz)")
        ax.grid(True, alpha=0.28)
        check = checks[key]
        unit_text = f" {unit}" if unit else ""
        ax.text(
            0.02,
            0.94,
            f"{target['nearest_frequency_ghz']:.2f} GHz\nactual={target['actuals'][key]:.5g}{unit_text}\nphoto={reference[key]:.5g}{unit_text}\nerr={check['percent_error']:.3g}%",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.88, "pad": 3},
        )
        ax.legend(loc="best", fontsize=7)
    fig.savefig(path, dpi=dpi)
    plt.close(fig)


def _render_report(summary: dict[str, Any]) -> str:
    fields = summary.get("metadata", {}).get("header_fields", {})
    variables = summary.get("metadata", {}).get("variables", {})
    ports = summary.get("metadata", {}).get("ports", {})
    target = summary["target_record"]
    lines = [
        "# Photo-Matched HFSS Reference Evidence",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Touchstone: `{summary['touchstone']}`",
        f"- Source kind from path: `{summary['source_kind_from_path']}`",
        f"- Header declares HFSS: `{summary['source_declares_hfss']}`",
        f"- Port pairs used for extraction: `{summary['port_pairs']}`",
        f"- Frequency range: `{summary['frequency_ghz']}`",
        "",
        "## Header Provenance",
        "",
    ]
    for key in ("File", "Generated", "Design", "Project", "Setup", "Solution"):
        if key in fields:
            lines.append(f"- {key}: `{fields[key]}`")
    lines.extend(["", "## Ports", ""])
    if ports:
        lines.extend(f"- Port {port}: `{name}`" for port, name in sorted(ports.items(), key=lambda item: int(item[0])))
    else:
        lines.append("- No port comments found.")
    lines.extend(["", "## Key Variables", ""])
    for key in ("$D1", "$D2", "$m10_w_inner", "$m10_w_outer", "$m9_w_inner", "$m9_w_outer", "$s", "$theta_bridge", "$sub_h"):
        if key in variables:
            lines.append(f"- {key}: `{variables[key]}`")
    lines.extend(
        [
            "",
            "## 15 GHz Photo Alignment",
            "",
            f"- Nearest frequency: `{target['nearest_frequency_ghz']} GHz`",
            "",
            "| Status | Metric | Expected | Actual | Error | Limit |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for check in target["checks"]:
        unit = f" {check['unit']}" if check["unit"] else ""
        lines.append(
            f"| {check['status']} | {check['label']} | {check['expected']:.6g}{unit} | "
            f"{check['actual']:.6g}{unit} | {check['percent_error']:.4f}% | {check['max_percent_error']:.2f}% |"
        )
    lines.extend(["", "## Boundary", ""])
    lines.extend(f"- {note}" for note in summary["notes"])
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
