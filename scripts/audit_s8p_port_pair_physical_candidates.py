#!/usr/bin/env python3
"""Audit candidate S8P differential port pairs for physical-feature extraction.

The next-generation topology has eight single-ended ports. This script helps
avoid silently using the wrong pair map by extracting Lp/Ls/Qp/Qs/K for several
candidate pair definitions from the same `.s8p` file and writing auditable
tables/plots.

It does not run EMX, HFSS, or ADS. It is a post-processing diagnostic for real
Touchstone files that already exist.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for item in (REPO_ROOT, SCRIPT_DIR):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from compare_emx_hfss_ads import load_touchstone_curves, parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception as exc:  # pragma: no cover - environment-specific fallback
    plt = None
    MATPLOTLIB_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    MATPLOTLIB_IMPORT_ERROR = ""


DEFAULT_CANDIDATE_PORT_PAIRS = "1,4:5,6;7,8:1,2;1,2:7,8;3,4:5,6;1,2:3,4;5,6:7,8"
METRICS = ("lp_nh", "ls_nh", "qp", "qs", "k")


@dataclass(frozen=True)
class Sample:
    label: str
    touchstone_path: Path


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    samples = _load_samples(args)
    pair_specs = _split_pair_specs(args.candidate_port_pairs)
    records: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    artifact_records: list[dict[str, str]] = []

    checks.append(_check("samples_present", bool(samples), f"samples={len(samples)}"))
    checks.append(_check("candidate_port_pairs_present", bool(pair_specs), f"pairs={pair_specs}"))
    for sample in samples:
        sample_out = out_dir / _slug(sample.label)
        sample_out.mkdir(parents=True, exist_ok=True)
        sample_records, sample_checks, sample_artifacts = _audit_sample(sample, pair_specs, sample_out, args)
        records.extend(sample_records)
        checks.extend(sample_checks)
        artifact_records.extend(sample_artifacts)

    expected_records = [record for record in records if record["port_pairs"] == str(args.expected_port_pairs)]
    expected_pass = bool(expected_records) and all(record["status"] == "PASS" for record in expected_records)
    status = "FAIL" if any(check["status"] == "FAIL" for check in checks) else "PASS"
    if status == "PASS" and not expected_pass:
        status = "REVIEW"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": _decision(status, expected_pass),
        "out_dir": str(out_dir),
        "sample_count": len(samples),
        "candidate_port_pairs": pair_specs,
        "expected_port_pairs": str(args.expected_port_pairs),
        "expected_port_pairs_all_pass": expected_pass,
        "records": records,
        "artifacts": artifact_records,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This diagnostic compares candidate port-pair physical-feature curves from existing S8P files only.",
            "It does not decide the final scientific port convention by itself; advisor-approved topology intent still wins.",
            "A PASS supports using the expected port pair for downstream validation, but final acceptance still requires EMX/HFSS <=5% curve comparison.",
        ],
    }
    summary_path = out_dir / "s8p_port_pair_physical_candidate_audit_summary.json"
    report_path = out_dir / "s8p_port_pair_physical_candidate_audit_report.md"
    records_csv = out_dir / "s8p_port_pair_physical_candidate_audit_records.csv"
    checks_csv = out_dir / "s8p_port_pair_physical_candidate_audit_checks.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_csv(records_csv, records)
    _write_csv(checks_csv, checks)

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"records_csv={records_csv}")
    print(f"checks_csv={checks_csv}")
    return 2 if status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--touchstone", action="append", default=[], help="S8P path to audit; may repeat")
    parser.add_argument("--samples-csv", help="CSV with touchstone_path/raw_touchstone_path and optional evaluation columns")
    parser.add_argument("--dataset-dir", help="Base directory for relative paths in --samples-csv")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--candidate-port-pairs", default=DEFAULT_CANDIDATE_PORT_PAIRS, help="Semicolon-separated pair specs")
    parser.add_argument("--expected-port-pairs", default="1,4:5,6")
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--physical-window-start-ghz", type=float, default=5.0)
    parser.add_argument("--physical-window-stop-ghz", type=float, default=60.0)
    parser.add_argument("--min-target-inductance-nh", type=float, default=0.02)
    parser.add_argument("--min-target-q", type=float, default=0.5)
    parser.add_argument("--min-target-abs-k", type=float, default=0.03)
    parser.add_argument("--max-target-abs-k", type=float, default=1.05)
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument(
        "--ground-unused-ports",
        action="store_true",
        help="Short all Touchstone ports outside the candidate differential pairs to ground before metric extraction.",
    )
    parser.add_argument("--skip-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _load_samples(args: argparse.Namespace) -> list[Sample]:
    samples = [
        Sample(label=Path(path).stem, touchstone_path=Path(path).expanduser().resolve())
        for path in args.touchstone
    ]
    if args.samples_csv:
        dataset_dir = Path(args.dataset_dir).expanduser().resolve() if args.dataset_dir else Path(args.samples_csv).expanduser().resolve().parent
        with Path(args.samples_csv).expanduser().open(newline="", encoding="utf-8-sig") as handle:
            for idx, row in enumerate(csv.DictReader(handle)):
                raw = (row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
                if not raw:
                    continue
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    path = dataset_dir / path
                label = row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or f"sample_{idx + 1:03d}"
                samples.append(Sample(label=str(label), touchstone_path=path.resolve()))
    seen: set[Path] = set()
    unique: list[Sample] = []
    for sample in samples:
        if sample.touchstone_path in seen:
            continue
        seen.add(sample.touchstone_path)
        unique.append(sample)
    return unique


def _split_pair_specs(text: str) -> list[str]:
    return [item.strip() for item in str(text).replace("\n", ";").split(";") if item.strip()]


def _audit_sample(sample: Sample, pair_specs: list[str], out_dir: Path, args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    checks: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    artifacts: list[dict[str, str]] = []
    path = sample.touchstone_path
    checks.append(_check(f"{sample.label}: touchstone exists", path.is_file(), str(path)))
    if not path.is_file():
        return records, checks, artifacts
    try:
        loaded = load_touchstone(path)
    except Exception as exc:  # noqa: BLE001 - exact parser failure is evidence.
        checks.append(_check(f"{sample.label}: touchstone loads", False, f"{type(exc).__name__}: {exc}"))
        return records, checks, artifacts
    checks.append(_check(f"{sample.label}: touchstone loads", True, str(path)))
    freqs = np.asarray(loaded.freqs_hz, dtype=float)
    n_ports = int(loaded.num_ports)
    checks.extend(_touchstone_grid_checks(sample.label, n_ports, freqs, args))

    source = _file_source(path)
    for pair_spec in pair_specs:
        pair_out = out_dir / _slug(pair_spec)
        pair_out.mkdir(parents=True, exist_ok=True)
        record = {
            "sample": sample.label,
            "touchstone_path": str(path),
            "touchstone_sha256": source.get("sha256", ""),
            "port_pairs": pair_spec,
            "status": "FAIL",
            "reason": "",
            "plot_path": "",
            "curve_csv": "",
            "ground_unused_ports": bool(args.ground_unused_ports),
        }
        try:
            curves = load_touchstone_curves(
                path,
                port_pairs=parse_port_pairs(pair_spec),
                ground_unused_ports=bool(args.ground_unused_ports),
            )
            metric_record = _metric_record(curves, args)
            record.update(metric_record)
            curve_csv = pair_out / "metrics_by_frequency.csv"
            _write_curve_csv(curve_csv, curves)
            record["curve_csv"] = str(curve_csv)
            artifacts.append({"sample": sample.label, "port_pairs": pair_spec, "kind": "curve_csv", "path": str(curve_csv)})
            if not args.skip_plots and plt is not None:
                plot_path = pair_out / "physical_feature_curves.png"
                _write_plot(plot_path, curves, pair_spec, args)
                record["plot_path"] = str(plot_path)
                artifacts.append({"sample": sample.label, "port_pairs": pair_spec, "kind": "plot", "path": str(plot_path)})
            elif not args.skip_plots and plt is None:
                record["plot_error"] = MATPLOTLIB_IMPORT_ERROR
            record["status"] = "PASS" if _record_passes(record, args) else "REVIEW"
        except Exception as exc:  # noqa: BLE001
            record["reason"] = f"{type(exc).__name__}: {exc}"
        records.append(record)
    return records, checks, artifacts


def _touchstone_grid_checks(label: str, n_ports: int, freqs: np.ndarray, args: argparse.Namespace) -> list[dict[str, Any]]:
    checks = [_check(f"{label}: expected port count", n_ports == int(args.expected_ports), f"ports={n_ports}")]
    tol = float(args.frequency_tolerance_hz)
    checks.append(_check(f"{label}: expected frequency points", int(freqs.size) == int(args.expected_frequency_points), f"points={freqs.size}"))
    if freqs.size:
        checks.append(_check(f"{label}: expected frequency start", abs(float(freqs[0]) - float(args.expected_frequency_start_ghz) * 1.0e9) <= tol, f"start_hz={float(freqs[0])}"))
        checks.append(_check(f"{label}: expected frequency stop", abs(float(freqs[-1]) - float(args.expected_frequency_stop_ghz) * 1.0e9) <= tol, f"stop_hz={float(freqs[-1])}"))
    if freqs.size > 1:
        diffs = np.diff(freqs)
        expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
        checks.append(_check(f"{label}: expected frequency step", abs(float(np.median(diffs)) - expected_step) <= tol and float(np.max(diffs) - np.min(diffs)) <= tol, f"step_hz={float(np.median(diffs))}, span_hz={float(np.max(diffs) - np.min(diffs))}"))
    return checks


def _metric_record(curves: Any, args: argparse.Namespace) -> dict[str, Any]:
    freq_ghz = np.asarray(curves.freq_hz, dtype=float) / 1.0e9
    target_idx = int(np.argmin(np.abs(freq_ghz - float(args.target_ghz))))
    window = (freq_ghz >= float(args.physical_window_start_ghz) - 1.0e-12) & (
        freq_ghz <= float(args.physical_window_stop_ghz) + 1.0e-12
    )
    record: dict[str, Any] = {
        "n_ports": int(load_touchstone(Path(curves.source)).num_ports),
        "target_freq_ghz": float(freq_ghz[target_idx]),
        "window_point_count": int(np.count_nonzero(window)),
    }
    for metric in METRICS:
        values = np.asarray(curves.metrics[metric], dtype=float)
        target_value = float(values[target_idx])
        record[f"{metric}_target"] = target_value
        record[f"{metric}_finite"] = bool(np.isfinite(values).all())
        if np.any(window):
            record[f"{metric}_window_min"] = float(np.nanmin(values[window]))
            record[f"{metric}_window_max"] = float(np.nanmax(values[window]))
    return record


def _record_passes(record: dict[str, Any], args: argparse.Namespace) -> bool:
    finite = all(bool(record.get(f"{metric}_finite")) for metric in METRICS)
    lp = float(record.get("lp_nh_target") or 0.0)
    ls = float(record.get("ls_nh_target") or 0.0)
    qp = float(record.get("qp_target") or 0.0)
    qs = float(record.get("qs_target") or 0.0)
    k = abs(float(record.get("k_target") or 0.0))
    return (
        finite
        and lp >= float(args.min_target_inductance_nh)
        and ls >= float(args.min_target_inductance_nh)
        and qp >= float(args.min_target_q)
        and qs >= float(args.min_target_q)
        and k >= float(args.min_target_abs_k)
        and k <= float(args.max_target_abs_k)
    )


def _write_curve_csv(path: Path, curves: Any) -> None:
    rows = []
    freq_hz = np.asarray(curves.freq_hz, dtype=float)
    for idx, freq in enumerate(freq_hz):
        row = {"freq_hz": float(freq), "freq_ghz": float(freq) / 1.0e9}
        for metric in METRICS:
            row[metric] = float(np.asarray(curves.metrics[metric], dtype=float)[idx])
        rows.append(row)
    _write_csv(path, rows)


def _write_plot(path: Path, curves: Any, port_pairs: str, args: argparse.Namespace) -> None:
    freq_ghz = np.asarray(curves.freq_hz, dtype=float) / 1.0e9
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), constrained_layout=True)
    axes[0, 0].plot(freq_ghz, curves.metrics["lp_nh"], label="Lp")
    axes[0, 0].plot(freq_ghz, curves.metrics["ls_nh"], label="Ls")
    axes[0, 0].set_ylabel("nH")
    axes[0, 0].legend()
    axes[0, 1].plot(freq_ghz, curves.metrics["qp"], label="Qp")
    axes[0, 1].plot(freq_ghz, curves.metrics["qs"], label="Qs")
    axes[0, 1].set_ylabel("Q")
    axes[0, 1].legend()
    axes[1, 0].plot(freq_ghz, curves.metrics["k"], label="K", color="#6f4aa8")
    axes[1, 0].set_ylabel("K")
    axes[1, 0].legend()
    axes[1, 1].axis("off")
    axes[1, 1].text(
        0.0,
        0.9,
        f"Source: {Path(curves.source).name}\nPort pairs: {port_pairs}\nTarget: {float(args.target_ghz):g} GHz",
        va="top",
        fontsize=10,
    )
    for ax in axes.ravel()[:3]:
        ax.axvline(float(args.target_ghz), color="#111827", linestyle="--", linewidth=1.0)
        ax.grid(True, alpha=0.25)
        ax.set_xlabel("Frequency (GHz)")
    fig.suptitle(f"S8P candidate port-pair physical features: {port_pairs}")
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _decision(status: str, expected_pass: bool) -> str:
    if status == "PASS" and expected_pass:
        return "EXPECTED_S8P_PORT_PAIR_DIAGNOSTIC_PASSES"
    if status == "REVIEW":
        return "REVIEW_S8P_PORT_PAIR_DIAGNOSTIC_BEFORE_HFSS_HANDOFF"
    return "DO_NOT_USE_S8P_PORT_PAIR_DIAGNOSTIC"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    if not fieldnames:
        fieldnames = ["empty"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _file_source(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    out.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    return out


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"status": "PASS" if bool(passed) else "FAIL", "name": name, "detail": str(detail)}


def _slug(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
    return safe.strip("_")[:80] or "item"


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# S8P Port-Pair Physical Candidate Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Expected port pairs: `{summary['expected_port_pairs']}`",
        f"- Candidate port pairs: `{'; '.join(summary['candidate_port_pairs'])}`",
        "",
        "## Records",
        "",
        "| Sample | Port pairs | Status | Lp nH | Ls nH | Qp | Qs | K |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for record in summary["records"]:
        lines.append(
            f"| {record.get('sample', '')} | {record.get('port_pairs', '')} | {record.get('status', '')} | "
            f"{_fmt(record.get('lp_nh_target'))} | {_fmt(record.get('ls_nh_target'))} | "
            f"{_fmt(record.get('qp_target'))} | {_fmt(record.get('qs_target'))} | {_fmt(record.get('k_target'))} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
