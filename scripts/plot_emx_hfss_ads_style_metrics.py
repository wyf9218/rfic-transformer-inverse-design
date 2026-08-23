#!/usr/bin/env python3
"""Plot ADS-style transformer metric panels from EMX/HFSS Touchstone files."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for item in (REPO_ROOT, SCRIPT_DIR):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from compare_emx_hfss_ads import multiport_z_to_differential_z, parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.analysis import multiport_s_to_grounded_differential_z  # noqa: E402
from rfic_transformer_inverse_design.network_analysis import s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


DEFAULT_PACKAGE_DIR = Path("/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")


@dataclass(frozen=True)
class MetricCurves:
    label: str
    source: Path
    n_ports: int
    port_pairs: str
    freq_hz: np.ndarray
    lp_nh: np.ndarray
    ls_nh: np.ndarray
    m_nh: np.ndarray
    k: np.ndarray
    q: np.ndarray
    qp: np.ndarray
    qs: np.ndarray
    cm_single_primary_ff: np.ndarray


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx-s4p", default=str(DEFAULT_PACKAGE_DIR / "ec6698dfc575950b_EMX_reference_NARROWBAND_13p5_16p5GHz.s4p"))
    parser.add_argument("--hfss-s4p", default=str(DEFAULT_PACKAGE_DIR / "ec6698dfc575950b_HFSS_WIDEBAND_0p1_50GHz_step0p1.s4p"))
    parser.add_argument("--emx-touchstone", help="EMX Touchstone file; use this for .s8p and newer flows")
    parser.add_argument("--hfss-touchstone", help="HFSS Touchstone file; use this for .s8p and newer flows")
    parser.add_argument("--out-dir", default=str(DEFAULT_PACKAGE_DIR / "ads_style_metric_curves_20260613"))
    parser.add_argument(
        "--emx-first-summary",
        default=str(DEFAULT_PACKAGE_DIR / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json"),
        help="Optional EMX-first summary used to mark these plots blocked when EMX is not accepted",
    )
    parser.add_argument(
        "--validation-chain-summary",
        help="Optional validation-chain summary; accepted final figures still require verify_accepted_emx_hfss_ads_figures.py",
    )
    parser.add_argument(
        "--port-pairs",
        help="Common differential terminal pairing, e.g. 1,4:5,6. If omitted, 4-port files default to 1,2:3,4; >4-port files require explicit pairs.",
    )
    parser.add_argument("--emx-port-pairs", help="EMX differential terminal pairing; overrides --port-pairs")
    parser.add_argument("--hfss-port-pairs", help="HFSS differential terminal pairing; overrides --port-pairs")
    parser.add_argument(
        "--ground-unused-ports",
        action="store_true",
        help="Short ports outside the selected differential pairs to ground before extracting curves. Use for S8P power-line ports grounded in ADS.",
    )
    parser.add_argument("--hfss-start-ghz", type=float, default=5.0)
    parser.add_argument("--hfss-stop-ghz", type=float, default=60.0)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument(
        "--core-only",
        action="store_true",
        help="Write report-facing plots with only Lp/Ls, scalar Q, and |K|. Qp/Qs/M remain in CSV/JSON as diagnostics.",
    )
    parser.add_argument(
        "--plot-signed-k",
        action="store_true",
        help="Plot signed K instead of the report-facing |K| magnitude.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    emx_path = Path(args.emx_touchstone or args.emx_s4p).expanduser().resolve()
    hfss_path = Path(args.hfss_touchstone or args.hfss_s4p).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    emx_first_summary_path = Path(args.emx_first_summary).expanduser().resolve() if args.emx_first_summary else None
    validation_chain_summary_path = (
        Path(args.validation_chain_summary).expanduser().resolve() if args.validation_chain_summary else None
    )
    emx_first_summary = _read_json_or_missing(emx_first_summary_path)
    validation_chain_summary = _read_json_or_missing(validation_chain_summary_path)

    emx = _extract_metric_curves(
        "EMX",
        emx_path,
        args.emx_port_pairs or args.port_pairs,
        ground_unused_ports=bool(args.ground_unused_ports),
    )
    hfss = _extract_metric_curves(
        "HFSS",
        hfss_path,
        args.hfss_port_pairs or args.port_pairs,
        ground_unused_ports=bool(args.ground_unused_ports),
    )
    hfss_window = _slice_window(hfss, args.hfss_start_ghz, args.hfss_stop_ghz)
    common_start = max(float(emx.freq_hz[0]), float(hfss.freq_hz[0]))
    common_stop = min(float(emx.freq_hz[-1]), float(hfss.freq_hz[-1]))
    emx_common = _slice_window(emx, common_start / 1.0e9, common_stop / 1.0e9)
    hfss_common = _slice_window(hfss, common_start / 1.0e9, common_stop / 1.0e9)
    hfss_interp = _interpolate_to(hfss_common, emx_common.freq_hz)
    common_window_tag = _window_tag_from_freq_hz(emx_common.freq_hz)

    legacy_artifacts = {
        "metric_csv": out_dir / "ads_style_metric_curves.csv",
        "emx_common_plot": out_dir / "emx_ads_style_metrics_13p5_16p5GHz.png",
        "hfss_common_plot": out_dir / "hfss_ads_style_metrics_13p5_16p5GHz.png",
        "hfss_wideband_plot": out_dir / "hfss_ads_style_metrics_5_60GHz.png",
        "overlay_common_plot": out_dir / "emx_vs_hfss_ads_style_overlay_13p5_16p5GHz.png",
    }
    window_artifacts = {
        "emx_common_plot": out_dir / f"emx_ads_style_metrics_common_{common_window_tag}.png",
        "hfss_common_plot": out_dir / f"hfss_ads_style_metrics_common_{common_window_tag}.png",
        "overlay_common_plot": out_dir / f"emx_vs_hfss_ads_style_overlay_common_{common_window_tag}.png",
    }
    _write_source_csv(legacy_artifacts["metric_csv"], [emx_common, hfss_interp])
    _write_single_panel(
        legacy_artifacts["emx_common_plot"],
        emx_common,
        args.target_ghz,
        args.dpi,
        core_only=bool(args.core_only),
        plot_signed_k=bool(args.plot_signed_k),
    )
    _write_single_panel(
        legacy_artifacts["hfss_common_plot"],
        hfss_common,
        args.target_ghz,
        args.dpi,
        core_only=bool(args.core_only),
        plot_signed_k=bool(args.plot_signed_k),
    )
    _write_single_panel(
        legacy_artifacts["hfss_wideband_plot"],
        hfss_window,
        args.target_ghz,
        args.dpi,
        core_only=bool(args.core_only),
        plot_signed_k=bool(args.plot_signed_k),
    )
    _write_overlay_panel(
        legacy_artifacts["overlay_common_plot"],
        emx_common,
        hfss_interp,
        args.target_ghz,
        args.dpi,
        core_only=bool(args.core_only),
        plot_signed_k=bool(args.plot_signed_k),
    )
    _copy_window_named_artifacts(legacy_artifacts, window_artifacts)

    summary = _summary(
        emx_common,
        hfss_interp,
        hfss_window,
        emx_path,
        hfss_path,
        args,
        emx_first_summary=emx_first_summary,
        validation_chain_summary=validation_chain_summary,
        emx_first_summary_path=emx_first_summary_path,
        validation_chain_summary_path=validation_chain_summary_path,
        artifact_paths=legacy_artifacts,
        window_named_artifact_paths=window_artifacts,
    )
    summary_path = out_dir / "ads_style_metric_plot_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path = out_dir / "ads_style_metric_plot_report.md"
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"out_dir={out_dir}")
    print(f"summary={summary_path}")
    print(f"emx_plot={legacy_artifacts['emx_common_plot']}")
    print(f"hfss_common_plot={legacy_artifacts['hfss_common_plot']}")
    print(f"hfss_plot={legacy_artifacts['hfss_wideband_plot']}")
    print(f"overlay_plot={legacy_artifacts['overlay_common_plot']}")
    print(f"window_named_emx_plot={window_artifacts['emx_common_plot']}")
    print(f"window_named_hfss_common_plot={window_artifacts['hfss_common_plot']}")
    print(f"window_named_overlay_plot={window_artifacts['overlay_common_plot']}")
    return 0


def _extract_metric_curves(
    label: str,
    touchstone_path: Path,
    port_pairs: str | None,
    *,
    ground_unused_ports: bool = False,
) -> MetricCurves:
    result = load_touchstone(touchstone_path)
    freqs_hz = np.asarray(result.freqs_hz, dtype=float)
    s_matrix = np.asarray(result.s_matrix, dtype=np.complex128)
    n_ports = int(s_matrix.shape[1])
    pair_text = _resolve_port_pairs_for_touchstone(n_ports, port_pairs, touchstone_path)
    parsed_pairs = parse_port_pairs(pair_text)
    z_single = s_to_z(s_matrix, z0=result.reference_impedance_ohm)
    if n_ports == 2:
        z_diff = z_single
    elif ground_unused_ports:
        z_diff = multiport_s_to_grounded_differential_z(
            s_matrix,
            result.reference_impedance_ohm,
            parsed_pairs,
        )
    else:
        z_diff = multiport_z_to_differential_z(z_single, parsed_pairs)
    y_single = np.linalg.inv(z_single)
    omega = 2.0 * math.pi * freqs_hz
    z11 = z_diff[:, 0, 0]
    z22 = z_diff[:, 1, 1]
    z21 = z_diff[:, 1, 0]
    lp_nh = np.imag(z11) / omega * 1.0e9
    ls_nh = np.imag(z22) / omega * 1.0e9
    # Use the same ADS worksheet convention as compare_emx_hfss_ads.py.
    m_nh = np.imag(z21) / omega * 1.0e9
    denom = np.sqrt(np.maximum(np.abs(lp_nh * ls_nh), 1.0e-30))
    k = m_nh / denom
    qp = _safe_div(np.imag(z11), np.real(z11))
    qs = _safe_div(np.imag(z22), np.real(z22))
    q = np.minimum(qp, qs)
    p0, p1 = parsed_pairs[0]
    cm_single_primary_ff = np.imag(y_single[:, p0, p0] + y_single[:, p0, p1]) / omega * 1.0e15
    return MetricCurves(
        label=label,
        source=touchstone_path,
        n_ports=n_ports,
        port_pairs=pair_text,
        freq_hz=freqs_hz,
        lp_nh=lp_nh,
        ls_nh=ls_nh,
        m_nh=m_nh,
        k=k,
        q=q,
        qp=qp,
        qs=qs,
        cm_single_primary_ff=cm_single_primary_ff,
    )


def _slice_window(curves: MetricCurves, start_ghz: float, stop_ghz: float) -> MetricCurves:
    freq_ghz = curves.freq_hz / 1.0e9
    mask = (freq_ghz >= float(start_ghz) - 1.0e-12) & (freq_ghz <= float(stop_ghz) + 1.0e-12)
    if not np.any(mask):
        raise ValueError(f"No frequency points for {curves.label} in {start_ghz}-{stop_ghz} GHz")
    return MetricCurves(
        label=curves.label,
        source=curves.source,
        n_ports=curves.n_ports,
        port_pairs=curves.port_pairs,
        freq_hz=curves.freq_hz[mask],
        lp_nh=curves.lp_nh[mask],
        ls_nh=curves.ls_nh[mask],
        m_nh=curves.m_nh[mask],
        k=curves.k[mask],
        q=curves.q[mask],
        qp=curves.qp[mask],
        qs=curves.qs[mask],
        cm_single_primary_ff=curves.cm_single_primary_ff[mask],
    )


def _interpolate_to(curves: MetricCurves, freqs_hz: np.ndarray) -> MetricCurves:
    x = np.asarray(curves.freq_hz, dtype=float)
    target = np.asarray(freqs_hz, dtype=float)
    return MetricCurves(
        label=curves.label,
        source=curves.source,
        n_ports=curves.n_ports,
        port_pairs=curves.port_pairs,
        freq_hz=target,
        lp_nh=np.interp(target, x, curves.lp_nh),
        ls_nh=np.interp(target, x, curves.ls_nh),
        m_nh=np.interp(target, x, curves.m_nh),
        k=np.interp(target, x, curves.k),
        q=np.interp(target, x, curves.q),
        qp=np.interp(target, x, curves.qp),
        qs=np.interp(target, x, curves.qs),
        cm_single_primary_ff=np.interp(target, x, curves.cm_single_primary_ff),
    )


def _write_source_csv(path: Path, curves_list: list[MetricCurves]) -> None:
    fields = [
        "source",
        "freq_hz",
        "freq_ghz",
        "lp_nh",
        "ls_nh",
        "m_nh",
        "k",
        "q",
        "qp",
        "qs",
        "cm_single_primary_y11_plus_y12_ff",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for curves in curves_list:
            for idx, freq_hz in enumerate(curves.freq_hz):
                writer.writerow(
                    {
                        "source": curves.label,
                        "freq_hz": float(freq_hz),
                        "freq_ghz": float(freq_hz / 1.0e9),
                        "lp_nh": float(curves.lp_nh[idx]),
                        "ls_nh": float(curves.ls_nh[idx]),
                        "m_nh": float(curves.m_nh[idx]),
                        "k": float(curves.k[idx]),
                        "q": float(curves.q[idx]),
                        "qp": float(curves.qp[idx]),
                        "qs": float(curves.qs[idx]),
                        "cm_single_primary_y11_plus_y12_ff": float(curves.cm_single_primary_ff[idx]),
                    }
                )


def _copy_window_named_artifacts(legacy_artifacts: dict[str, Path], window_artifacts: dict[str, Path]) -> None:
    for key, dst in window_artifacts.items():
        src = legacy_artifacts[key]
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)


def _write_single_panel(
    path: Path,
    curves: MetricCurves,
    target_ghz: float,
    dpi: int,
    *,
    core_only: bool = False,
    plot_signed_k: bool = False,
) -> None:
    import matplotlib.pyplot as plt

    if core_only:
        fig, axes = plt.subplots(1, 3, figsize=(14, 4.2), constrained_layout=True)
        axes = np.asarray(axes).reshape(1, 3)
    else:
        fig, axes = plt.subplots(2, 3, figsize=(14, 8.2), constrained_layout=True)
    fig.suptitle(f"{curves.label}: ADS-style transformer metrics from S{curves.n_ports}P", fontsize=15, fontweight="bold")
    freq_ghz = curves.freq_hz / 1.0e9
    _plot_lp_ls(axes[0, 0], freq_ghz, curves, None)
    _plot_metric(axes[0, 1], freq_ghz, curves.q, "Q" if core_only else "Q = min(Qp, Qs)", "#3348a3", None)
    k_values = curves.k if plot_signed_k else np.abs(curves.k)
    _plot_metric(axes[0, 2], freq_ghz, k_values, "K / Kw" if plot_signed_k else "|K| / |Kw|", "#c2410c", None)
    if not core_only:
        _plot_metric(axes[1, 0], freq_ghz, curves.qp, "Qp diagnostic", "#2563eb", None)
        _plot_metric(axes[1, 1], freq_ghz, curves.qs, "Qs diagnostic", "#be123c", None)
        _plot_metric(axes[1, 2], freq_ghz, curves.m_nh, "M diagnostic (nH)", "#8b5cf6", None)
    for ax in axes.ravel():
        _style_axis(ax, target_ghz)
        _annotate_target(ax, freq_ghz, curves, target_ghz)
    fig.savefig(path, dpi=dpi)
    plt.close(fig)


def _write_overlay_panel(
    path: Path,
    emx: MetricCurves,
    hfss: MetricCurves,
    target_ghz: float,
    dpi: int,
    *,
    core_only: bool = False,
    plot_signed_k: bool = False,
) -> None:
    import matplotlib.pyplot as plt

    if core_only:
        fig, axes = plt.subplots(1, 3, figsize=(14, 4.2), constrained_layout=True)
        axes = np.asarray(axes).reshape(1, 3)
    else:
        fig, axes = plt.subplots(2, 3, figsize=(14, 8.2), constrained_layout=True)
    fig.suptitle(
        f"EMX vs HFSS: ADS-style metric overlay on common {_window_label_from_freq_hz(emx.freq_hz)} window",
        fontsize=15,
        fontweight="bold",
    )
    freq_ghz = emx.freq_hz / 1.0e9
    _plot_lp_ls(axes[0, 0], freq_ghz, emx, hfss)
    _plot_overlay_metric(axes[0, 1], freq_ghz, emx.q, hfss.q, "Q" if core_only else "Q = min(Qp, Qs)")
    emx_k_values = emx.k if plot_signed_k else np.abs(emx.k)
    hfss_k_values = hfss.k if plot_signed_k else np.abs(hfss.k)
    _plot_overlay_metric(axes[0, 2], freq_ghz, emx_k_values, hfss_k_values, "K / Kw" if plot_signed_k else "|K| / |Kw|")
    if not core_only:
        _plot_overlay_metric(axes[1, 0], freq_ghz, emx.qp, hfss.qp, "Qp diagnostic")
        _plot_overlay_metric(axes[1, 1], freq_ghz, emx.qs, hfss.qs, "Qs diagnostic")
        _plot_overlay_metric(axes[1, 2], freq_ghz, emx.m_nh, hfss.m_nh, "M diagnostic (nH)")
    for ax in axes.ravel():
        _style_axis(ax, target_ghz)
    axes[0, 0].legend(loc="best", fontsize=8)
    error_panels = [
        (axes[0, 1], "Q", emx.q, hfss.q),
        (axes[0, 2], "K/Kw" if plot_signed_k else "|K|/|Kw|", emx_k_values, hfss_k_values),
    ]
    if not core_only:
        error_panels.extend(
            [
                (axes[1, 0], "Qp", emx.qp, hfss.qp),
                (axes[1, 1], "Qs", emx.qs, hfss.qs),
                (axes[1, 2], "M", emx.m_nh, hfss.m_nh),
            ]
        )
    for ax, metric_name, emx_values, hfss_values in error_panels:
        max_err = _max_percent_error(emx_values, hfss_values)
        ax.text(
            0.02,
            0.94,
            f"max err={max_err:.2f}%",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.85, "pad": 3},
        )
    fig.savefig(path, dpi=dpi)
    plt.close(fig)


def _plot_lp_ls(ax: Any, freq_ghz: np.ndarray, curves: MetricCurves, other: MetricCurves | None) -> None:
    if other is None:
        ax.plot(freq_ghz, curves.lp_nh, color="#2563eb", linewidth=1.9, label="Lp")
        ax.plot(freq_ghz, curves.ls_nh, color="#dc2626", linewidth=1.9, label="Ls")
    else:
        ax.plot(freq_ghz, curves.lp_nh, color="#2563eb", linewidth=1.9, label="EMX Lp")
        ax.plot(freq_ghz, other.lp_nh, color="#60a5fa", linewidth=1.7, linestyle="--", label="HFSS Lp")
        ax.plot(freq_ghz, curves.ls_nh, color="#dc2626", linewidth=1.9, label="EMX Ls")
        ax.plot(freq_ghz, other.ls_nh, color="#fca5a5", linewidth=1.7, linestyle="--", label="HFSS Ls")
        ax.text(
            0.02,
            0.94,
            f"Lp max err={_max_percent_error(curves.lp_nh, other.lp_nh):.2f}%\nLs max err={_max_percent_error(curves.ls_nh, other.ls_nh):.2f}%",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.85, "pad": 3},
        )
    ax.set_ylabel("nH")
    ax.set_title("Lp / Ls")
    ax.legend(loc="best", fontsize=8)


def _plot_metric(ax: Any, freq_ghz: np.ndarray, values: np.ndarray, title: str, color: str, label: str | None) -> None:
    ax.plot(freq_ghz, values, color=color, linewidth=1.9, label=label or title)
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)


def _plot_overlay_metric(ax: Any, freq_ghz: np.ndarray, emx_values: np.ndarray, hfss_values: np.ndarray, title: str) -> None:
    ax.plot(freq_ghz, emx_values, color="#111827", linewidth=1.9, label="EMX")
    ax.plot(freq_ghz, hfss_values, color="#ef4444", linewidth=1.7, linestyle="--", label="HFSS")
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)


def _style_axis(ax: Any, target_ghz: float) -> None:
    ax.axvline(float(target_ghz), color="#111827", linewidth=1.0, linestyle=":")
    ax.grid(True, alpha=0.28)
    ax.set_xlabel("freq (GHz)")


def _annotate_target(ax: Any, freq_ghz: np.ndarray, curves: MetricCurves, target_ghz: float) -> None:
    idx = int(np.argmin(np.abs(freq_ghz - float(target_ghz))))
    title = ax.get_title()
    if title.startswith("Lp"):
        text = f"{freq_ghz[idx]:.2f} GHz\nLp={curves.lp_nh[idx]:.3g} nH\nLs={curves.ls_nh[idx]:.3g} nH"
    elif title.startswith("Q"):
        text = f"{freq_ghz[idx]:.2f} GHz\nQ={curves.q[idx]:.3g}"
    elif title == "Qp diagnostic":
        text = f"{freq_ghz[idx]:.2f} GHz\nQp={curves.qp[idx]:.3g}"
    elif title.startswith("M"):
        text = f"{freq_ghz[idx]:.2f} GHz\nM={curves.m_nh[idx]:.3g} nH"
    elif title == "K / Kw":
        text = f"{freq_ghz[idx]:.2f} GHz\nK/Kw={curves.k[idx]:.3g}"
    elif title == "|K| / |Kw|":
        text = f"{freq_ghz[idx]:.2f} GHz\n|K|={abs(curves.k[idx]):.3g}"
    elif title == "Qs diagnostic":
        text = f"{freq_ghz[idx]:.2f} GHz\nQs={curves.qs[idx]:.3g}"
    else:
        text = f"{freq_ghz[idx]:.2f} GHz\nCm={curves.cm_single_primary_ff[idx]:.3g} fF"
    ax.text(
        0.02,
        0.94,
        text,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8,
        bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.86, "pad": 3},
    )


def _summary(
    emx: MetricCurves,
    hfss_common: MetricCurves,
    hfss_window: MetricCurves,
    emx_path: Path,
    hfss_path: Path,
    args: argparse.Namespace,
    *,
    emx_first_summary: dict[str, Any],
    validation_chain_summary: dict[str, Any],
    emx_first_summary_path: Path | None,
    validation_chain_summary_path: Path | None,
    artifact_paths: dict[str, Path],
    window_named_artifact_paths: dict[str, Path],
) -> dict[str, Any]:
    metrics = {
        "lp_nh": (emx.lp_nh, hfss_common.lp_nh),
        "ls_nh": (emx.ls_nh, hfss_common.ls_nh),
        "q": (emx.q, hfss_common.q),
        "k": (emx.k, hfss_common.k),
        "kw": (emx.k, hfss_common.k),
        "qp": (emx.qp, hfss_common.qp),
        "qs": (emx.qs, hfss_common.qs),
        "m_nh": (emx.m_nh, hfss_common.m_nh),
        "cm_single_primary_y11_plus_y12_ff": (emx.cm_single_primary_ff, hfss_common.cm_single_primary_ff),
    }
    evidence = _plot_evidence_boundary(emx_first_summary, validation_chain_summary)
    return {
        "overall_status": evidence["overall_status"],
        "decision": evidence["decision"],
        "evidence_use": evidence["evidence_use"],
        "emx_touchstone": str(emx_path),
        "hfss_touchstone": str(hfss_path),
        "emx_first_summary": str(emx_first_summary_path) if emx_first_summary_path else None,
        "validation_chain_summary": str(validation_chain_summary_path) if validation_chain_summary_path else None,
        "emx_port_pairs": emx.port_pairs,
        "hfss_port_pairs": hfss_common.port_pairs,
        "emx_n_ports": emx.n_ports,
        "hfss_n_ports": hfss_common.n_ports,
        "ground_unused_ports": bool(args.ground_unused_ports),
        "target_ghz": float(args.target_ghz),
        "emx_frequency_ghz": _freq_summary(emx.freq_hz),
        "hfss_plot_frequency_ghz": _freq_summary(hfss_window.freq_hz),
        "common_overlay_frequency_ghz": _freq_summary(emx.freq_hz),
        "artifact_paths": {key: str(value) for key, value in artifact_paths.items()},
        "window_named_artifact_paths": {key: str(value) for key, value in window_named_artifact_paths.items()},
        "metric_max_percent_errors_common_window": {
            name: _max_percent_error(emx_values, hfss_values) for name, (emx_values, hfss_values) in metrics.items()
        },
        "gate_inputs": {
            "emx_first_overall_status": emx_first_summary.get("overall_status"),
            "emx_first_decision": emx_first_summary.get("decision"),
            "validation_chain_overall_status": validation_chain_summary.get("overall_status"),
            "validation_chain_decision": validation_chain_summary.get("decision"),
        },
        "plot_options": {
            "core_only": bool(args.core_only),
            "k_display": "signed_k" if bool(args.plot_signed_k) else "abs_k",
        },
        "notes": [
            evidence["note"],
            _emx_scope_note(emx.freq_hz),
            _hfss_scope_note(hfss_window.freq_hz, args),
            "Scalar Q is min(Qp, Qs), matching the physical-feature inverse-model target Lp/Ls/Q/K.",
            "Report-facing plots use |K| by default; raw signed k remains in the CSV/JSON for polarity audits.",
            "When core_only=true, the report-facing panels include only Lp/Ls, Q, and |K|; Qp/Qs/M remain diagnostic data.",
            "Kw is recorded as an alias of K, the coupling coefficient from M/sqrt(abs(Lp*Ls)).",
            "For S8P power-line validation, ground_unused_ports=true means all non-selected power-line ports were shorted before differential Z extraction, matching ADS grounded-port review.",
            "Cm uses the ADS-style single-ended primary formula imag(Ypp+Ypn)/omega on the selected primary terminal pair.",
            "Generated plots are traceable post-processing artifacts, not ADS GUI screenshots.",
            "Final reportable Lp/Ls/Q/K figures require run_accepted_emx_hfss_ads_validation.py plus verify_accepted_emx_hfss_ads_figures.py, not this diagnostic plotting script alone.",
        ],
    }


def _plot_evidence_boundary(emx_first_summary: dict[str, Any], validation_chain_summary: dict[str, Any]) -> dict[str, str]:
    chain_accepted = (
        validation_chain_summary.get("overall_status") == "PASS"
        and validation_chain_summary.get("decision") == "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN"
    )
    emx_accepted = (
        emx_first_summary.get("overall_status") == "PASS"
        and emx_first_summary.get("decision") == "ACCEPT_AS_GOLDEN_EMX_REFERENCE"
    )
    if not emx_accepted:
        return {
            "overall_status": "BLOCKED_BY_EMX_REFERENCE",
            "decision": "DO_NOT_USE_AS_FINAL_LP_LS_Q_K_FIGURES",
            "evidence_use": "BLOCKED_AS_FINAL_EVIDENCE",
            "note": (
                "EMX-first has not accepted this EMX source as the golden reference; these curves are failure/diagnostic "
                "evidence only and must not be used as final Lp/Ls/Q/K/Kw validation figures."
            ),
        }
    if not chain_accepted:
        return {
            "overall_status": "DIAGNOSTIC_ONLY",
            "decision": "WAIT_FOR_ACCEPTED_EMX_HFSS_ADS_VALIDATION_CHAIN",
            "evidence_use": "DIAGNOSTIC_ONLY",
            "note": (
                "EMX-first accepted the EMX source, but the full EMX/HFSS/ADS validation chain is not accepted here; "
                "use these plots only as diagnostic context."
            ),
        }
    return {
        "overall_status": "DIAGNOSTIC_ONLY",
        "decision": "USE_ACCEPTED_FINAL_FIGURE_VERIFIER_FOR_REPORTABLE_LP_LS_Q_K",
        "evidence_use": "DIAGNOSTIC_ONLY",
        "note": (
            "The validation chain is accepted, but this plotting script is still diagnostic; final reportable figures must "
            "come from the accepted final-figure verifier."
        ),
    }


def _read_json_or_missing(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"_missing": str(path)}
    except json.JSONDecodeError as exc:
        return {"_parse_error": f"{type(exc).__name__}: {exc}", "_path": str(path)}


def _resolve_port_pairs_for_touchstone(n_ports: int, port_pairs: str | None, path: Path) -> str:
    if port_pairs:
        parsed = parse_port_pairs(port_pairs)
        flat = [port for pair in parsed for port in pair]
        if min(flat) < 0 or max(flat) >= int(n_ports):
            raise ValueError(f"Port pair spec {port_pairs!r} is outside S{n_ports}P port range for {path}")
        return port_pairs
    if int(n_ports) == 4:
        return "1,2:3,4"
    raise ValueError(
        f"S{n_ports}P ADS-style metric plotting requires explicit differential port pairs for {path}; "
        "pass --emx-port-pairs/--hfss-port-pairs or --port-pairs and record the physical port map."
    )


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# ADS-Style EMX/HFSS Metric Curves",
        "",
        f"- Overall status: **{summary.get('overall_status', 'UNKNOWN')}**",
        f"- Decision: **{summary.get('decision', 'UNKNOWN')}**",
        f"- Evidence use: **{summary.get('evidence_use', 'UNKNOWN')}**",
        f"- EMX source: `{summary['emx_touchstone']}`",
        f"- HFSS source: `{summary['hfss_touchstone']}`",
        f"- EMX port pairs: `{summary['emx_port_pairs']}`",
        f"- HFSS port pairs: `{summary['hfss_port_pairs']}`",
        f"- EMX/HFSS port count: `{summary['emx_n_ports']}` / `{summary['hfss_n_ports']}`",
        f"- Target marker: `{summary['target_ghz']} GHz`",
        f"- EMX-first summary: `{summary.get('emx_first_summary')}`",
        f"- Validation-chain summary: `{summary.get('validation_chain_summary')}`",
        "",
        "## Frequency Scope",
        "",
        f"- EMX plot/common overlay: `{summary['common_overlay_frequency_ghz']}`",
        f"- HFSS wideband plot: `{summary['hfss_plot_frequency_ghz']}`",
        "",
        "## Common-Window Max Percent Error",
        "",
        "| Metric | Max percent error |",
        "| --- | ---: |",
    ]
    for name, value in summary["metric_max_percent_errors_common_window"].items():
        lines.append(f"| {name} | {value:.4f}% |")
    lines.extend(["", "## Boundary", ""])
    lines.extend(f"- {note}" for note in summary["notes"])
    lines.append("")
    return "\n".join(lines)


def _freq_summary(freq_hz: np.ndarray) -> dict[str, Any]:
    diffs = np.diff(freq_hz)
    return {
        "start": float(freq_hz[0] / 1.0e9),
        "stop": float(freq_hz[-1] / 1.0e9),
        "points": int(len(freq_hz)),
        "step": float(diffs[0] / 1.0e9) if len(diffs) else None,
    }


def _window_tag_from_freq_hz(freq_hz: np.ndarray) -> str:
    freq = np.asarray(freq_hz, dtype=float) / 1.0e9
    return f"{_ghz_token(float(freq[0]))}_{_ghz_token(float(freq[-1]))}GHz"


def _window_label_from_freq_hz(freq_hz: np.ndarray) -> str:
    freq = np.asarray(freq_hz, dtype=float) / 1.0e9
    return f"{float(freq[0]):.6g}-{float(freq[-1]):.6g} GHz"


def _ghz_token(value: float) -> str:
    return f"{value:.6g}".replace("-", "m").replace(".", "p")


def _emx_scope_note(freq_hz: np.ndarray) -> str:
    summary = _freq_summary(freq_hz)
    if summary["start"] <= 5.0 + 1.0e-9 and summary["stop"] >= 60.0 - 1.0e-9:
        return (
            "EMX source covers the requested 5-60 GHz range for the common overlay; "
            "still verify point count and step before using as final evidence."
        )
    return (
        f"EMX source covers only {summary['start']:.6g}-{summary['stop']:.6g} GHz in this plot; "
        "do not claim 5-60 GHz EMX validation from these curves."
    )


def _hfss_scope_note(freq_hz: np.ndarray, args: argparse.Namespace) -> str:
    summary = _freq_summary(freq_hz)
    return (
        f"HFSS source is plotted over {summary['start']:.6g}-{summary['stop']:.6g} GHz "
        f"using the configured {float(args.hfss_start_ghz):.6g}-{float(args.hfss_stop_ghz):.6g} GHz window."
    )


def _max_percent_error(reference: np.ndarray, candidate: np.ndarray) -> float:
    ref = np.asarray(reference, dtype=float)
    cand = np.asarray(candidate, dtype=float)
    denom = np.maximum(np.abs(ref), 1.0e-30)
    return float(np.nanmax(np.abs(cand - ref) / denom * 100.0))


def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    den_arr = np.asarray(den, dtype=float)
    return np.divide(num, den_arr, out=np.full_like(np.asarray(num, dtype=float), np.nan), where=np.abs(den_arr) > 1.0e-30)


if __name__ == "__main__":
    raise SystemExit(main())
