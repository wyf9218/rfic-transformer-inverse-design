#!/usr/bin/env python3
"""Audit loaded-Zin coverage across a wideband Touchstone sweep.

This script is intentionally post-processing only: it does not run EMX, HFSS,
ADS, Cadence, or MARS. It reads completed 4-port `.s4p` files, converts each
sample to the differential 2-port representation used by the ADS equations, and
then evaluates the loaded primary Zin over selected frequency slices.

The goal is to prove whether the generated dataset gives broad, non-collapsed
Zin coverage over the requested RF band, instead of relying on a single
center-frequency point.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception as exc:  # pragma: no cover - environment failure path
    print(f"ERROR: matplotlib is required to create figures: {exc}", file=sys.stderr)
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for item in (REPO_ROOT, SCRIPT_DIR):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from compare_emx_hfss_ads import four_port_z_to_differential_z, parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.network_analysis import s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else dataset_dir / "zin_sweep_coverage_audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    source_rows = _read_dataset_rows(dataset_dir / "dataset_rows.csv")
    candidates = _discover_touchstones(dataset_dir, source_rows)
    if args.max_files is not None:
        candidates = candidates[: int(args.max_files)]

    requested_freqs_ghz = _frequency_slices(args.frequency_slices_ghz)
    points, sample_records = _collect_points(candidates, requested_freqs_ghz, args)
    freq_summary = _frequency_summary(points, requested_freqs_ghz, args)
    checks = _build_checks(candidates, sample_records, freq_summary, args)
    overall_status = "FAIL" if any(check["status"] == "FAIL" for check in checks) else "PASS"

    points_csv = out_dir / "zin_sweep_points.csv"
    summary_csv = out_dir / "zin_sweep_frequency_summary.csv"
    _write_csv(points_csv, points)
    _write_csv(summary_csv, freq_summary)

    figures = _write_plots(out_dir, points, freq_summary, args) if points else []
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "overall_status": overall_status,
        "candidate_count": len(candidates),
        "ok_sample_count": sum(1 for record in sample_records if record["ok"]),
        "failed_sample_count": sum(1 for record in sample_records if not record["ok"]),
        "frequency_slices_ghz": requested_freqs_ghz,
        "points_csv": str(points_csv),
        "frequency_summary_csv": str(summary_csv),
        "checks": checks,
        "figures": figures,
        "sample_records": sample_records[: int(args.max_records_in_summary)],
        "arguments": vars(args),
        "limitations": [
            "This audit checks Zin coverage from existing Touchstone files only.",
            "It does not prove EMX, HFSS, ADS, Cadence, or MARS execution by itself.",
            "A PASS means the selected frequency slices have broad occupied-bin/entropy coverage under the configured thresholds.",
            "Physics can map uniform geometry sampling into non-uniform Zin; FAIL is useful evidence for target-aware resampling rather than a plotting error.",
        ],
    }
    summary_path = out_dir / "zin_sweep_coverage_summary.json"
    report_path = out_dir / "zin_sweep_coverage_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"points_csv={points_csv}")
    print(f"frequency_summary_csv={summary_csv}")
    for check in checks:
        print(f"{check['status']:4s} {check['name']}: {check['detail']}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--load-ohm", type=float, default=50.0)
    parser.add_argument("--frequency-slices-ghz", default="5,10,15,20,25,30,35,40,45,50")
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-frequency-start-ghz", type=float)
    parser.add_argument("--expected-frequency-stop-ghz", type=float)
    parser.add_argument("--expected-frequency-step-ghz", type=float)
    parser.add_argument("--expected-frequency-points", type=int)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--min-valid-count", type=int)
    parser.add_argument("--min-real-span-ohm", type=float)
    parser.add_argument("--min-imag-span-ohm", type=float)
    parser.add_argument("--min-occupied-2d-bins", type=int)
    parser.add_argument("--min-occupied-2d-frac", type=float)
    parser.add_argument("--min-entropy-frac", type=float, default=0.70)
    parser.add_argument("--max-records-in-summary", type=int, default=50)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_dataset_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "none", "no", "nan"}


def _is_touchstone(path: Path) -> bool:
    return path.suffix.lower() in {".s1p", ".s2p", ".s3p", ".s4p", ".s5p", ".s6p", ".s7p", ".s8p", ".s9p"}


def _discover_touchstones(dataset_dir: Path, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for idx, row in enumerate(rows):
        if not _truthy(row.get("ok", "true")):
            continue
        evaluation = row.get("evaluation") or row.get("sample_id") or row.get("id") or f"row_{idx:06d}"
        possible: list[Path] = []
        for key in ("touchstone_path", "sparam_path", "emx_s4p_path", "emx_touchstone_path"):
            if row.get(key):
                possible.append((dataset_dir / row[key]).resolve())
        possible.extend(sorted((dataset_dir / "evaluations" / evaluation / "emx").glob("*.s*p")))
        if not possible:
            possible.append((dataset_dir / "evaluations" / evaluation / "emx" / "emx.s4p").resolve())
        selected = next((path for path in possible if path.exists() and _is_touchstone(path)), None)
        candidate_path = selected if selected is not None else possible[0]
        if candidate_path not in seen:
            seen.add(candidate_path)
            candidates.append({"evaluation": evaluation, "row_index": idx, "path": candidate_path})

    if candidates:
        return candidates

    paths = sorted(dataset_dir.glob("evaluations/*/emx/*.s*p"))
    if not paths:
        paths = sorted(dataset_dir.glob("*.s*p"))
    for idx, path in enumerate(path for path in paths if _is_touchstone(path)):
        evaluation = path.parents[1].name if len(path.parents) >= 2 and path.parent.name == "emx" else path.stem
        candidates.append({"evaluation": evaluation, "row_index": idx, "path": path.resolve()})
    return candidates


def _frequency_slices(raw: str) -> list[float]:
    out: list[float] = []
    for item in str(raw).split(","):
        text = item.strip()
        if not text:
            continue
        out.append(float(text))
    if not out:
        raise SystemExit("No frequency slices were provided")
    return out


def _collect_points(
    candidates: list[dict[str, Any]],
    requested_freqs_ghz: list[float],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    points: list[dict[str, Any]] = []
    sample_records: list[dict[str, Any]] = []
    port_pairs = parse_port_pairs(args.port_pairs)
    requested_hz = np.asarray(requested_freqs_ghz, dtype=float) * 1.0e9
    for candidate in candidates:
        path = Path(candidate["path"]).resolve()
        record = {
            "evaluation": candidate["evaluation"],
            "path": str(path),
            "ok": False,
            "point_count": 0,
            "error": "",
        }
        try:
            if not path.exists():
                raise FileNotFoundError(path)
            touchstone = load_touchstone(path)
            s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
            freqs_hz = np.asarray(touchstone.freqs_hz, dtype=float)
            _validate_touchstone(s_matrix, freqs_hz, args)
            indices = _slice_indices(freqs_hz, requested_hz, float(args.frequency_tolerance_hz))
            z_single = s_to_z(s_matrix, z0=touchstone.reference_impedance_ohm)
            z_diff = z_single if z_single.shape[1:] == (2, 2) else four_port_z_to_differential_z(z_single, port_pairs)
            zin = _loaded_primary_zin(z_diff, float(args.load_ohm))
            for freq_ghz, idx in zip(requested_freqs_ghz, indices, strict=True):
                value = complex(zin[idx])
                if not (math.isfinite(value.real) and math.isfinite(value.imag)):
                    raise ValueError(f"non-finite Zin at {freq_ghz} GHz")
                points.append(
                    {
                        "evaluation": candidate["evaluation"],
                        "touchstone_path": str(path),
                        "freq_ghz": float(freq_ghz),
                        "real_ohm": float(value.real),
                        "imag_ohm": float(value.imag),
                        "abs_ohm": float(abs(value)),
                    }
                )
            record["ok"] = True
            record["point_count"] = len(requested_freqs_ghz)
        except Exception as exc:  # noqa: BLE001 - record exact data issue.
            record["error"] = f"{type(exc).__name__}: {exc}"
        sample_records.append(record)
    return points, sample_records


def _validate_touchstone(s_matrix: np.ndarray, freqs_hz: np.ndarray, args: argparse.Namespace) -> None:
    if s_matrix.ndim != 3 or s_matrix.shape[1] != s_matrix.shape[2]:
        raise ValueError(f"expected S matrix shape (N,P,P), got {s_matrix.shape}")
    if args.expected_ports is not None and int(s_matrix.shape[1]) != int(args.expected_ports):
        raise ValueError(f"expected {args.expected_ports} ports, got {s_matrix.shape[1]}")
    if len(freqs_hz) != s_matrix.shape[0]:
        raise ValueError(f"frequency rows {len(freqs_hz)} do not match S rows {s_matrix.shape[0]}")
    if len(freqs_hz) == 0:
        raise ValueError("no frequency points")
    if not np.isfinite(freqs_hz).all() or not np.isfinite(s_matrix.real).all() or not np.isfinite(s_matrix.imag).all():
        raise ValueError("non-finite Touchstone values")
    if len(freqs_hz) >= 2 and not bool(np.all(np.diff(freqs_hz) > 0.0)):
        raise ValueError("frequency grid is not strictly increasing")
    _validate_expected_frequency(freqs_hz, args)


def _validate_expected_frequency(freqs_hz: np.ndarray, args: argparse.Namespace) -> None:
    tol = float(args.frequency_tolerance_hz)
    checks = [
        ("start", args.expected_frequency_start_ghz, float(freqs_hz[0])),
        ("stop", args.expected_frequency_stop_ghz, float(freqs_hz[-1])),
    ]
    for name, ghz, actual in checks:
        if ghz is not None and abs(actual - float(ghz) * 1.0e9) > tol:
            raise ValueError(f"frequency {name} mismatch: expected {float(ghz) * 1.0e9}, actual {actual}")
    if args.expected_frequency_step_ghz is not None and len(freqs_hz) >= 2:
        step = float(np.median(np.diff(freqs_hz)))
        expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
        if abs(step - expected_step) > tol:
            raise ValueError(f"frequency step mismatch: expected {expected_step}, actual {step}")
    if args.expected_frequency_points is not None and len(freqs_hz) != int(args.expected_frequency_points):
        raise ValueError(f"frequency point mismatch: expected {args.expected_frequency_points}, actual {len(freqs_hz)}")


def _slice_indices(freqs_hz: np.ndarray, requested_hz: np.ndarray, tol_hz: float) -> list[int]:
    indices: list[int] = []
    for target in requested_hz:
        idx = int(np.argmin(np.abs(freqs_hz - target)))
        err = abs(float(freqs_hz[idx]) - float(target))
        if err > tol_hz:
            raise ValueError(f"missing requested frequency {target / 1e9:.6g} GHz; nearest error {err} Hz")
        indices.append(idx)
    return indices


def _loaded_primary_zin(z_diff: np.ndarray, load_ohm: float) -> np.ndarray:
    z11 = z_diff[:, 0, 0]
    z12 = z_diff[:, 0, 1]
    z21 = z_diff[:, 1, 0]
    z22 = z_diff[:, 1, 1]
    load = complex(float(load_ohm))
    return z11 - (z12 * z21) / (z22 + load)


def _frequency_summary(
    points: list[dict[str, Any]],
    requested_freqs_ghz: list[float],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_freq = {float(freq): [] for freq in requested_freqs_ghz}
    for point in points:
        by_freq.setdefault(float(point["freq_ghz"]), []).append(point)
    for freq in requested_freqs_ghz:
        items = by_freq.get(float(freq), [])
        real = np.asarray([item["real_ohm"] for item in items], dtype=float)
        imag = np.asarray([item["imag_ohm"] for item in items], dtype=float)
        absv = np.asarray([item["abs_ohm"] for item in items], dtype=float)
        occupancy = _occupancy(real, imag, int(args.bins))
        rows.append(
            {
                "freq_ghz": float(freq),
                "valid_count": int(real.size),
                "real_min_ohm": _finite_stat(real, np.min),
                "real_p05_ohm": _finite_percentile(real, 5),
                "real_median_ohm": _finite_percentile(real, 50),
                "real_p95_ohm": _finite_percentile(real, 95),
                "real_max_ohm": _finite_stat(real, np.max),
                "real_span_ohm": _span(real),
                "imag_min_ohm": _finite_stat(imag, np.min),
                "imag_p05_ohm": _finite_percentile(imag, 5),
                "imag_median_ohm": _finite_percentile(imag, 50),
                "imag_p95_ohm": _finite_percentile(imag, 95),
                "imag_max_ohm": _finite_stat(imag, np.max),
                "imag_span_ohm": _span(imag),
                "abs_min_ohm": _finite_stat(absv, np.min),
                "abs_median_ohm": _finite_percentile(absv, 50),
                "abs_max_ohm": _finite_stat(absv, np.max),
                "occupied_2d_bins": occupancy["occupied_bins"],
                "occupied_2d_frac": occupancy["occupied_frac"],
                "entropy_frac": occupancy["entropy_frac"],
            }
        )
    return rows


def _occupancy(real: np.ndarray, imag: np.ndarray, bins: int) -> dict[str, float]:
    if real.size == 0 or imag.size == 0:
        return {"occupied_bins": 0, "occupied_frac": 0.0, "entropy_frac": 0.0}
    if _span(real) <= 0 or _span(imag) <= 0:
        return {"occupied_bins": 1 if real.size else 0, "occupied_frac": 1.0 / float(bins * bins), "entropy_frac": 0.0}
    hist, _, _ = np.histogram2d(real, imag, bins=bins)
    occupied = int(np.sum(hist > 0))
    total = int(bins * bins)
    probs = hist.ravel().astype(float)
    probs = probs[probs > 0] / np.sum(probs)
    entropy = float(-np.sum(probs * np.log(probs))) if probs.size else 0.0
    max_entropy = math.log(total) if total > 1 else 1.0
    return {
        "occupied_bins": occupied,
        "occupied_frac": float(occupied / total) if total else 0.0,
        "entropy_frac": float(entropy / max_entropy) if max_entropy > 0 else 0.0,
    }


def _finite_stat(arr: np.ndarray, func: Any) -> float | None:
    if arr.size == 0:
        return None
    return float(func(arr))


def _finite_percentile(arr: np.ndarray, percentile: float) -> float | None:
    if arr.size == 0:
        return None
    return float(np.percentile(arr, percentile))


def _span(arr: np.ndarray) -> float:
    if arr.size == 0:
        return 0.0
    return float(np.max(arr) - np.min(arr))


def _build_checks(
    candidates: list[dict[str, Any]],
    sample_records: list[dict[str, Any]],
    freq_summary: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    ok_count = sum(1 for record in sample_records if record["ok"])
    checks.append(_check("Touchstone candidates", "PASS" if candidates else "FAIL", f"count={len(candidates)}"))
    min_valid = int(args.min_valid_count) if args.min_valid_count is not None else len(candidates)
    checks.append(_check("valid samples per frequency", "PASS" if all(row["valid_count"] >= min_valid for row in freq_summary) else "FAIL", f"required>={min_valid}; ok_samples={ok_count}"))
    if args.min_real_span_ohm is not None:
        bad = [row["freq_ghz"] for row in freq_summary if float(row["real_span_ohm"] or 0.0) < float(args.min_real_span_ohm)]
        checks.append(_check("real Zin span by frequency", "PASS" if not bad else "FAIL", f"min={args.min_real_span_ohm}; bad_freqs={bad[:12]}"))
    if args.min_imag_span_ohm is not None:
        bad = [row["freq_ghz"] for row in freq_summary if float(row["imag_span_ohm"] or 0.0) < float(args.min_imag_span_ohm)]
        checks.append(_check("imag Zin span by frequency", "PASS" if not bad else "FAIL", f"min={args.min_imag_span_ohm}; bad_freqs={bad[:12]}"))
    if args.min_occupied_2d_bins is not None:
        bad = [row["freq_ghz"] for row in freq_summary if int(row["occupied_2d_bins"] or 0) < int(args.min_occupied_2d_bins)]
        checks.append(_check("occupied Zin bins by frequency", "PASS" if not bad else "FAIL", f"min={args.min_occupied_2d_bins}; bad_freqs={bad[:12]}"))
    if args.min_occupied_2d_frac is not None:
        bad = [row["freq_ghz"] for row in freq_summary if float(row["occupied_2d_frac"] or 0.0) < float(args.min_occupied_2d_frac)]
        checks.append(_check("occupied Zin bin fraction by frequency", "PASS" if not bad else "FAIL", f"min={args.min_occupied_2d_frac}; bad_freqs={bad[:12]}"))
    if args.min_entropy_frac is not None:
        bad = [row["freq_ghz"] for row in freq_summary if float(row["entropy_frac"] or 0.0) < float(args.min_entropy_frac)]
        checks.append(_check("Zin entropy by frequency", "PASS" if not bad else "FAIL", f"min={args.min_entropy_frac}; bad_freqs={bad[:12]}"))
    failed_samples = [record for record in sample_records if not record["ok"]]
    checks.append(_check("sample extraction errors", "PASS" if not failed_samples else "FAIL", f"failed={len(failed_samples)}"))
    return checks


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _write_plots(out_dir: Path, points: list[dict[str, Any]], freq_summary: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, str]]:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#333333",
            "axes.labelcolor": "#222222",
            "xtick.color": "#222222",
            "ytick.color": "#222222",
            "font.size": 9,
        }
    )
    figures: list[dict[str, str]] = []
    _add_figure(figures, "Zin Re/Im scatter by frequency", _plot_scatter_grid(points, out_dir / "01_zin_sweep_scatter_grid.png"))
    _add_figure(figures, "Zin occupancy heatmap by frequency", _plot_heatmap_grid(points, out_dir / "02_zin_sweep_occupancy_heatmaps.png", int(args.bins)))
    _add_figure(figures, "Zin range vs frequency", _plot_range_vs_frequency(freq_summary, out_dir / "03_zin_sweep_range_vs_frequency.png"))
    _add_figure(figures, "Zin uniformity vs frequency", _plot_uniformity_vs_frequency(freq_summary, out_dir / "04_zin_sweep_uniformity_vs_frequency.png"))
    return figures


def _add_figure(figures: list[dict[str, str]], title: str, path: Path | None) -> None:
    if path is not None and path.exists():
        figures.append({"title": title, "path": str(path)})


def _group_points(points: list[dict[str, Any]]) -> dict[float, list[dict[str, Any]]]:
    grouped: dict[float, list[dict[str, Any]]] = {}
    for point in points:
        grouped.setdefault(float(point["freq_ghz"]), []).append(point)
    return dict(sorted(grouped.items()))


def _global_limits(points: list[dict[str, Any]]) -> tuple[tuple[float, float], tuple[float, float]]:
    real = np.asarray([p["real_ohm"] for p in points], dtype=float)
    imag = np.asarray([p["imag_ohm"] for p in points], dtype=float)
    return _padded_limits(real), _padded_limits(imag)


def _padded_limits(arr: np.ndarray) -> tuple[float, float]:
    lo = float(np.min(arr))
    hi = float(np.max(arr))
    pad = 0.05 * max(hi - lo, 1.0)
    return lo - pad, hi + pad


def _plot_scatter_grid(points: list[dict[str, Any]], path: Path) -> Path | None:
    if not points:
        return None
    grouped = _group_points(points)
    xlim, ylim = _global_limits(points)
    n = len(grouped)
    ncols = min(5, max(1, n))
    nrows = int(math.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.1 * ncols, 2.8 * nrows), squeeze=False)
    for ax, (freq, items) in zip(axes.flat, grouped.items(), strict=False):
        real = [item["real_ohm"] for item in items]
        imag = [item["imag_ohm"] for item in items]
        ax.scatter(real, imag, s=10, alpha=0.60, color="#2563EB", edgecolors="none")
        ax.set_title(f"{freq:g} GHz, n={len(items)}")
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_xlabel("Re(Zin) ohm")
        ax.set_ylabel("Im(Zin) ohm")
        ax.grid(True, linewidth=0.3, alpha=0.35)
    for ax in axes.flat[n:]:
        ax.axis("off")
    fig.suptitle("Loaded Primary Zin Distribution Across Frequency Slices", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def _plot_heatmap_grid(points: list[dict[str, Any]], path: Path, bins: int) -> Path | None:
    if not points:
        return None
    grouped = _group_points(points)
    xlim, ylim = _global_limits(points)
    n = len(grouped)
    ncols = min(5, max(1, n))
    nrows = int(math.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.1 * ncols, 2.8 * nrows), squeeze=False)
    for ax, (freq, items) in zip(axes.flat, grouped.items(), strict=False):
        real = [item["real_ohm"] for item in items]
        imag = [item["imag_ohm"] for item in items]
        hist, xedges, yedges = np.histogram2d(real, imag, bins=bins, range=[xlim, ylim])
        ax.imshow(hist.T, origin="lower", aspect="auto", extent=[xedges[0], xedges[-1], yedges[0], yedges[-1]], cmap="viridis")
        ax.set_title(f"{freq:g} GHz")
        ax.set_xlabel("Re(Zin) ohm")
        ax.set_ylabel("Im(Zin) ohm")
    for ax in axes.flat[n:]:
        ax.axis("off")
    fig.suptitle("Loaded Primary Zin 2D Occupancy Heatmaps", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def _plot_range_vs_frequency(rows: list[dict[str, Any]], path: Path) -> Path | None:
    if not rows:
        return None
    freq = np.asarray([row["freq_ghz"] for row in rows], dtype=float)
    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    for ax, prefix, label, color in (
        (axes[0], "real", "Re(Zin) ohm", "#2563EB"),
        (axes[1], "imag", "Im(Zin) ohm", "#DC2626"),
        (axes[2], "abs", "|Zin| ohm", "#059669"),
    ):
        if prefix == "abs":
            p05 = np.asarray([row["abs_min_ohm"] for row in rows], dtype=float)
            med = np.asarray([row["abs_median_ohm"] for row in rows], dtype=float)
            p95 = np.asarray([row["abs_max_ohm"] for row in rows], dtype=float)
        else:
            p05 = np.asarray([row[f"{prefix}_p05_ohm"] for row in rows], dtype=float)
            med = np.asarray([row[f"{prefix}_median_ohm"] for row in rows], dtype=float)
            p95 = np.asarray([row[f"{prefix}_p95_ohm"] for row in rows], dtype=float)
        ax.fill_between(freq, p05, p95, color=color, alpha=0.18, label="5-95% window")
        ax.plot(freq, med, color=color, linewidth=1.8, label="median")
        ax.set_ylabel(label)
        ax.grid(True, linewidth=0.3, alpha=0.35)
        ax.legend(loc="best", frameon=False)
    axes[-1].set_xlabel("Frequency (GHz)")
    fig.suptitle("Loaded Primary Zin Range Across Frequency", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def _plot_uniformity_vs_frequency(rows: list[dict[str, Any]], path: Path) -> Path | None:
    if not rows:
        return None
    freq = np.asarray([row["freq_ghz"] for row in rows], dtype=float)
    occupied = np.asarray([row["occupied_2d_frac"] for row in rows], dtype=float)
    entropy = np.asarray([row["entropy_frac"] for row in rows], dtype=float)
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.plot(freq, occupied, marker="o", color="#7C3AED", linewidth=1.6, label="occupied 2D-bin fraction")
    ax.plot(freq, entropy, marker="s", color="#EA580C", linewidth=1.6, label="2D entropy fraction")
    ax.set_ylim(-0.02, 1.02)
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("fraction")
    ax.set_title("Loaded Primary Zin Coverage Uniformity vs Frequency")
    ax.grid(True, linewidth=0.3, alpha=0.35)
    ax.legend(loc="best", frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


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
        "# Zin Sweep Coverage Audit",
        "",
        f"- Generated UTC: `{summary['generated_utc']}`",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Candidate Touchstone files: `{summary['candidate_count']}`",
        f"- OK samples: `{summary['ok_sample_count']}`",
        f"- Frequency slices GHz: `{summary['frequency_slices_ghz']}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## Figures", ""])
    if summary["figures"]:
        for figure in summary["figures"]:
            lines.append(f"- {figure['title']}: `{figure['path']}`")
    else:
        lines.append("- No figures were generated.")
    lines.extend(["", "## Limitations", ""])
    for item in summary["limitations"]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
