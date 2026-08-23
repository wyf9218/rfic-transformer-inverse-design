#!/usr/bin/env python3
"""Audit the ADS-style transformer metric extraction formulas.

This script builds a synthetic four-port coupled transformer with known
frequency-dependent Lp/Ls/M/K/Qp/Qs, converts it through Z->S->Z, and verifies
that the project extraction formulas recover the known physical values. It is a
formula/implementation audit only; it does not validate any EMX or HFSS result.
"""

from __future__ import annotations

import argparse
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

from compare_emx_hfss_ads import four_port_z_to_differential_z, parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.network_analysis import s_to_z, z_to_s  # noqa: E402


DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_OUT_DIR = DEFAULT_PROJECT_ROOT / "hfss_validation" / "final500_ec6698dfc575950b" / "ads_metric_formula_consistency_20260614"

METRICS = ("lp_nh", "ls_nh", "m_nh", "k", "qp", "qs")
ADS_FORMULAS = {
    "Zdiff11": "Z11 - Z12 - Z21 + Z22",
    "Zdiff22": "Z33 - Z34 - Z43 + Z44",
    "Zdiff21": "Z31 - Z32 + Z42 - Z41",
    "Lp": "imag(Zdiff11) / (2*pi*freq)",
    "Ls": "imag(Zdiff22) / (2*pi*freq)",
    "M": "imag(Zdiff21) / (2*pi*freq)",
    "K": "M / sqrt(Lp*Ls)",
    "Qp": "imag(Zdiff11) / real(Zdiff11)",
    "Qs": "imag(Zdiff22) / real(Zdiff22)",
}
ADS_TEMPLATE_FILENAME = "ADS_DATA_DISPLAY_LP_LS_Q_K_TEMPLATE.md"

ADS_TEMPLATE_REQUIRED_FRAGMENTS = (
    "ADS Data Display equation template",
    "Touchstone reference impedance",
    "port pairs 1,2:3,4",
    "Zp = Z11 - Z12 + Z22 - Z21",
    "Zs = Z33 - Z34 + Z44 - Z43",
    "Zm = Z31 - Z32 + Z42 - Z41",
    "Lp = imag(Zp) / omega",
    "Ls = imag(Zs) / omega",
    "M  = imag(Zm) / omega",
    "K  = M / sqrt(Lp*Ls)",
    "Qp = imag(Zp) / real(Zp)",
    "Qs = imag(Zs) / real(Zs)",
    "target_marker_ghz = 15",
    "5-50 GHz / 0.1 GHz / 451 points",
    "no ADS extrapolation",
)


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    freqs_hz = _frequency_grid(args)
    known = _known_metric_curves(freqs_hz)
    z_diff = _build_known_differential_z(freqs_hz, known)
    z_single = _embed_differential_z_as_four_port(z_diff)
    s_matrix = z_to_s(z_single, z0=float(args.z0_ohm))
    z_roundtrip = s_to_z(s_matrix, z0=float(args.z0_ohm))
    z_direct = _direct_ads_differential_z(z_roundtrip)
    z_helper = four_port_z_to_differential_z(z_roundtrip, parse_port_pairs(args.port_pairs))
    direct = _extract_metrics_from_zdiff(freqs_hz, z_direct)
    helper = _extract_metrics_from_zdiff(freqs_hz, z_helper)

    metric_errors = _metric_errors(known, helper)
    helper_vs_direct_errors = _metric_errors(direct, helper)
    checks = [
        _roundtrip_check(z_single, z_roundtrip, args),
        _helper_vs_direct_check(helper_vs_direct_errors, args),
        _known_recovery_check(metric_errors, args),
        _passivity_check(s_matrix, args),
        _frequency_grid_check(freqs_hz, args),
    ]
    summary_path = out_dir / "ads_metric_formula_consistency_summary.json"
    report_path = out_dir / "ads_metric_formula_consistency_report.md"
    plot_path = out_dir / "ads_metric_formula_consistency_curves.png"
    template_path = out_dir / ADS_TEMPLATE_FILENAME
    _write_ads_data_display_template(template_path, args)
    if not args.no_plot:
        _write_plot(plot_path, freqs_hz, known, helper, metric_errors, args)
    checks.append(_ads_template_check(template_path))
    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    decision = "ADS_FORMULA_IMPLEMENTATION_ACCEPTED" if overall_status == "PASS" else "DO_NOT_TRUST_ADS_FORMULA_IMPLEMENTATION"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "port_pairs": args.port_pairs,
        "z0_ohm": float(args.z0_ohm),
        "frequency_ghz": {
            "start": float(freqs_hz[0] / 1.0e9),
            "stop": float(freqs_hz[-1] / 1.0e9),
            "step": float(np.median(np.diff(freqs_hz)) / 1.0e9),
            "points": int(freqs_hz.size),
        },
        "ads_formula_map": ADS_FORMULAS,
        "checks": [check.__dict__ for check in checks],
        "status_counts": _status_counts(checks),
        "metric_recovery_errors": metric_errors,
        "helper_vs_direct_formula_errors": helper_vs_direct_errors,
        "s_z_roundtrip_max_abs_ohm": float(np.max(np.abs(z_roundtrip - z_single))),
        "passivity_sigma_max": float(np.max(np.linalg.svd(s_matrix, compute_uv=False))),
        "artifacts": {
            "summary": str(summary_path),
            "report": str(report_path),
            "plot": str(plot_path) if plot_path.exists() else None,
            "ads_data_display_template": str(template_path),
        },
        "method_notes": [
            "This audit proves the ADS-style metric extraction implementation on a known synthetic transformer, not on a measured or EM-simulated device.",
            "The four-port differential transform uses primary ports 1/2 and secondary ports 3/4, with the first port in each pair treated as the positive terminal.",
            "The direct ADS single-ended expressions and the shared helper four_port_z_to_differential_z must agree before EMX or HFSS Lp/Ls/Q/K curves are trusted.",
            "EMX-first still requires real simulator S4P evidence, ADS-photo anchoring, port-pair sensitivity, passivity/reciprocity, and smooth physical curves.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if plot_path.exists():
        print(f"plot={plot_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--z0-ohm", type=float, default=50.0)
    parser.add_argument("--start-ghz", type=float, default=5.0)
    parser.add_argument("--stop-ghz", type=float, default=50.0)
    parser.add_argument("--step-ghz", type=float, default=0.1)
    parser.add_argument("--points", type=int, default=451)
    parser.add_argument("--max-recovery-percent-error", type=float, default=1.0e-6)
    parser.add_argument("--max-helper-direct-percent-error", type=float, default=1.0e-9)
    parser.add_argument("--max-roundtrip-abs-ohm", type=float, default=1.0e-9)
    parser.add_argument("--max-passivity-sigma", type=float, default=1.001)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _frequency_grid(args: argparse.Namespace) -> np.ndarray:
    if int(args.points) <= 1:
        raise ValueError("--points must be greater than 1")
    return np.linspace(float(args.start_ghz) * 1.0e9, float(args.stop_ghz) * 1.0e9, int(args.points))


def _write_ads_data_display_template(path: Path, args: argparse.Namespace) -> None:
    lines = [
        "# ADS Data Display Lp/Ls/Q/K Template",
        "",
        "Boundary: this is an ADS Data Display equation template for an accepted four-port transformer S4P.",
        "Do not use these equations as final EMX/HFSS evidence until the source S4P has passed the EMX-first or HFSS physical gates.",
        "",
        "## Required setup",
        "",
        f"- Touchstone reference impedance: use the impedance recorded by the S4P import; this audit used `{float(args.z0_ohm):g} ohm`.",
        f"- Differential port pairs: `{args.port_pairs}`.",
        "- For port pairs 1,2:3,4, port 1 is primary positive, port 2 is primary negative, port 3 is secondary positive, and port 4 is secondary negative.",
        "- Required final sweep: 5-50 GHz / 0.1 GHz / 451 points.",
        "- Requirement: no ADS extrapolation; the imported S4P frequency range must fully cover the plotted sweep and the 15 GHz marker.",
        "",
        "## ADS Data Display equation template",
        "",
        "Use the four-port Z-parameters derived from the imported S4P:",
        "",
        "```text",
        "omega = 2*pi*freq",
        "Zp = Z11 - Z12 + Z22 - Z21",
        "Zs = Z33 - Z34 + Z44 - Z43",
        "Zm = Z31 - Z32 + Z42 - Z41",
        "",
        "Lp = imag(Zp) / omega",
        "Ls = imag(Zs) / omega",
        "M  = imag(Zm) / omega",
        "K  = M / sqrt(Lp*Ls)",
        "Qp = imag(Zp) / real(Zp)",
        "Qs = imag(Zs) / real(Zs)",
        "",
        "target_marker_ghz = 15",
        "```",
        "",
        "Notes:",
        "",
        "- `Zp` and `Zs` are the primary and secondary differential self impedances.",
        "- `Zm` follows the same secondary-to-primary mutual sign convention used by the Python verifier.",
        "- If port polarity is flipped intentionally, record the new port pairing and rerun the port-pair sensitivity gate before using the curves.",
        "- The final report must compare EMX-only, HFSS-only, and EMX-vs-HFSS overlay curves from the same grid before claiming <=5% agreement.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _ads_template_check(path: Path) -> Check:
    if not path.is_file():
        return Check("FAIL", "ADS Data Display equation template", f"missing: {path}")
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in ADS_TEMPLATE_REQUIRED_FRAGMENTS if fragment not in text]
    if missing:
        return Check("FAIL", "ADS Data Display equation template", f"missing fragments={missing[:4]}")
    return Check("PASS", "ADS Data Display equation template", str(path))


def _known_metric_curves(freqs_hz: np.ndarray) -> dict[str, np.ndarray]:
    x = (freqs_hz - freqs_hz[0]) / (freqs_hz[-1] - freqs_hz[0])
    lp_h = (0.85 + 0.05 * np.sin(2.0 * math.pi * x)) * 1.0e-9
    ls_h = (0.82 + 0.04 * np.cos(2.0 * math.pi * x)) * 1.0e-9
    k = -0.52 + 0.03 * np.sin(math.pi * x)
    m_h = k * np.sqrt(lp_h * ls_h)
    qp = 14.0 + 2.0 * np.cos(2.0 * math.pi * x)
    qs = 12.0 + 1.5 * np.sin(2.0 * math.pi * x + 0.2)
    return {
        "lp_nh": lp_h * 1.0e9,
        "ls_nh": ls_h * 1.0e9,
        "m_nh": m_h * 1.0e9,
        "k": k,
        "qp": qp,
        "qs": qs,
    }


def _build_known_differential_z(freqs_hz: np.ndarray, known: dict[str, np.ndarray]) -> np.ndarray:
    omega = 2.0 * math.pi * freqs_hz
    lp_h = known["lp_nh"] * 1.0e-9
    ls_h = known["ls_nh"] * 1.0e-9
    m_h = known["m_nh"] * 1.0e-9
    rp = omega * lp_h / known["qp"]
    rs = omega * ls_h / known["qs"]
    z_diff = np.zeros((freqs_hz.size, 2, 2), dtype=np.complex128)
    z_diff[:, 0, 0] = rp + 1j * omega * lp_h
    z_diff[:, 1, 1] = rs + 1j * omega * ls_h
    z_diff[:, 0, 1] = 1j * omega * m_h
    z_diff[:, 1, 0] = 1j * omega * m_h
    return z_diff


def _embed_differential_z_as_four_port(z_diff: np.ndarray) -> np.ndarray:
    transform = np.array([[1.0, 0.0], [-1.0, 0.0], [0.0, 1.0], [0.0, -1.0]], dtype=np.complex128)
    return 0.25 * np.einsum("ai,fij,bj->fab", transform, z_diff, transform)


def _direct_ads_differential_z(z_single: np.ndarray) -> np.ndarray:
    z = np.asarray(z_single, dtype=np.complex128)
    result = np.zeros((z.shape[0], 2, 2), dtype=np.complex128)
    result[:, 0, 0] = z[:, 0, 0] - z[:, 0, 1] - z[:, 1, 0] + z[:, 1, 1]
    result[:, 1, 1] = z[:, 2, 2] - z[:, 2, 3] - z[:, 3, 2] + z[:, 3, 3]
    result[:, 1, 0] = z[:, 2, 0] - z[:, 2, 1] + z[:, 3, 1] - z[:, 3, 0]
    result[:, 0, 1] = z[:, 0, 2] - z[:, 0, 3] + z[:, 1, 3] - z[:, 1, 2]
    return result


def _extract_metrics_from_zdiff(freqs_hz: np.ndarray, z_diff: np.ndarray) -> dict[str, np.ndarray]:
    omega = 2.0 * math.pi * freqs_hz
    z11 = z_diff[:, 0, 0]
    z22 = z_diff[:, 1, 1]
    z21 = z_diff[:, 1, 0]
    lp_h = np.imag(z11) / omega
    ls_h = np.imag(z22) / omega
    m_h = np.imag(z21) / omega
    k = m_h / np.sqrt(np.maximum(np.abs(lp_h * ls_h), 1.0e-30))
    return {
        "lp_nh": lp_h * 1.0e9,
        "ls_nh": ls_h * 1.0e9,
        "m_nh": m_h * 1.0e9,
        "k": k,
        "qp": _safe_div(np.imag(z11), np.real(z11)),
        "qs": _safe_div(np.imag(z22), np.real(z22)),
    }


def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    out = np.full_like(num, np.nan, dtype=float)
    mask = np.abs(den) > 1.0e-30
    out[mask] = np.asarray(num, dtype=float)[mask] / np.asarray(den, dtype=float)[mask]
    return out


def _metric_errors(expected: dict[str, np.ndarray], actual: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
    errors: dict[str, dict[str, float]] = {}
    for metric in METRICS:
        exp = np.asarray(expected[metric], dtype=float)
        act = np.asarray(actual[metric], dtype=float)
        abs_error = np.abs(act - exp)
        denom = np.maximum(np.abs(exp), 1.0e-30)
        percent_error = abs_error / denom * 100.0
        errors[metric] = {
            "max_abs_error": float(np.max(abs_error)),
            "max_percent_error": float(np.max(percent_error)),
            "mean_percent_error": float(np.mean(percent_error)),
        }
    return errors


def _roundtrip_check(z_single: np.ndarray, z_roundtrip: np.ndarray, args: argparse.Namespace) -> Check:
    error = float(np.max(np.abs(z_roundtrip - z_single)))
    status = "PASS" if error <= float(args.max_roundtrip_abs_ohm) else "FAIL"
    return Check(status, "Z-S-Z roundtrip", f"max_abs_error_ohm={error:.6g}, limit={args.max_roundtrip_abs_ohm:g}")


def _helper_vs_direct_check(errors: dict[str, dict[str, float]], args: argparse.Namespace) -> Check:
    worst_metric, worst = _worst_metric(errors)
    status = "PASS" if worst <= float(args.max_helper_direct_percent_error) else "FAIL"
    return Check(
        status,
        "helper formula equals direct ADS expression",
        f"worst={worst_metric}, max_percent_error={worst:.6g}%, limit={args.max_helper_direct_percent_error:g}%",
    )


def _known_recovery_check(errors: dict[str, dict[str, float]], args: argparse.Namespace) -> Check:
    worst_metric, worst = _worst_metric(errors)
    status = "PASS" if worst <= float(args.max_recovery_percent_error) else "FAIL"
    return Check(
        status,
        "known transformer metric recovery",
        f"worst={worst_metric}, max_percent_error={worst:.6g}%, limit={args.max_recovery_percent_error:g}%",
    )


def _passivity_check(s_matrix: np.ndarray, args: argparse.Namespace) -> Check:
    sigma_max = float(np.max(np.linalg.svd(s_matrix, compute_uv=False)))
    status = "PASS" if sigma_max <= float(args.max_passivity_sigma) else "FAIL"
    return Check(status, "synthetic S passivity", f"sigma_max={sigma_max:.6g}, limit={args.max_passivity_sigma:g}")


def _frequency_grid_check(freqs_hz: np.ndarray, args: argparse.Namespace) -> Check:
    step_ghz = float(np.median(np.diff(freqs_hz)) / 1.0e9)
    expected = float(args.step_ghz)
    status = "PASS" if abs(step_ghz - expected) < 1.0e-12 and freqs_hz.size == int(args.points) else "FAIL"
    return Check(
        status,
        "formula audit frequency grid",
        f"{freqs_hz[0] / 1e9:.6g}-{freqs_hz[-1] / 1e9:.6g} GHz, step={step_ghz:.6g} GHz, points={freqs_hz.size}",
    )


def _worst_metric(errors: dict[str, dict[str, float]]) -> tuple[str, float]:
    metric, item = max(errors.items(), key=lambda row: float(row[1]["max_percent_error"]))
    return metric, float(item["max_percent_error"])


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return dict(sorted(counts.items()))


def _write_plot(
    path: Path,
    freqs_hz: np.ndarray,
    known: dict[str, np.ndarray],
    recovered: dict[str, np.ndarray],
    errors: dict[str, dict[str, float]],
    args: argparse.Namespace,
) -> None:
    import matplotlib.pyplot as plt

    freq_ghz = freqs_hz / 1.0e9
    fig, axes = plt.subplots(2, 3, figsize=(14, 8.0), constrained_layout=True)
    fig.suptitle("ADS metric formula consistency: known vs recovered", fontsize=15, fontweight="bold")
    labels = {
        "lp_nh": "Lp (nH)",
        "ls_nh": "Ls (nH)",
        "m_nh": "M (nH)",
        "k": "K",
        "qp": "Qp",
        "qs": "Qs",
    }
    for ax, metric in zip(axes.ravel(), METRICS):
        ax.plot(freq_ghz, known[metric], color="#2563eb", linewidth=1.8, label="known")
        ax.plot(freq_ghz, recovered[metric], color="#dc2626", linestyle="--", linewidth=1.4, label="recovered")
        ax.set_title(labels[metric])
        ax.set_xlabel("freq (GHz)")
        ax.grid(True, alpha=0.28)
        ax.text(
            0.02,
            0.94,
            f"max err={errors[metric]['max_percent_error']:.3g}%",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.88, "pad": 3},
        )
        ax.legend(loc="best", fontsize=7)
    fig.savefig(path, dpi=int(args.dpi))
    plt.close(fig)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# ADS Metric Formula Consistency Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Port pairs: `{summary['port_pairs']}`",
        f"- Frequency grid: `{summary['frequency_ghz']}`",
        "",
        "## Formula Map",
        "",
    ]
    for key, value in summary["ads_formula_map"].items():
        lines.append(f"- `{key}` = `{value}`")
    lines.extend(["", "## Checks", "", "| Status | Check | Detail |", "| --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## Metric Recovery Error", "", "| Metric | Max abs error | Max percent error | Mean percent error |", "| --- | ---: | ---: | ---: |"])
    for metric, item in summary["metric_recovery_errors"].items():
        lines.append(
            f"| {metric} | {item['max_abs_error']:.6g} | {item['max_percent_error']:.6g}% | {item['mean_percent_error']:.6g}% |"
        )
    lines.extend(["", "## Method Boundary", ""])
    lines.extend(f"- {note}" for note in summary["method_notes"])
    lines.extend(["", "## Artifacts", ""])
    for key, value in summary["artifacts"].items():
        if value:
            lines.append(f"- {key}: `{value}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
