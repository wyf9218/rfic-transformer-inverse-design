#!/usr/bin/env python3
"""Matrix-level EMX/HFSS diagnostics for a 4-port transformer S4P pair."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.network_analysis import s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402

METRICS = ("r11_ohm", "x11_ohm", "r22_ohm", "x22_ohm", "r12_ohm", "x12_ohm", "lp_nh", "ls_nh", "m_nh", "k", "qp", "qs")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    emx_s = load_touchstone(args.emx)
    hfss_s = load_touchstone(args.hfss)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    freq = common_freq_grid(emx_s.freqs_hz, hfss_s.freqs_hz)
    emx_s_i = interp_matrix(freq, emx_s.freqs_hz, emx_s.s_matrix)
    hfss_s_i = interp_matrix(freq, hfss_s.freqs_hz, hfss_s.s_matrix)
    emx_z_i = s_to_z(emx_s_i, z0=emx_s.reference_impedance_ohm)
    hfss_z_i = s_to_z(hfss_s_i, z0=hfss_s.reference_impedance_ohm)

    pairs = parse_port_pairs(args.port_pairs)
    emx_diff = differential_z(emx_z_i, pairs)
    hfss_diff = differential_z(hfss_z_i, pairs)
    emx_params = diff_params(freq, emx_diff)
    hfss_params = diff_params(freq, hfss_diff)

    target_idx = int(np.argmin(np.abs(freq - args.target_freq_hz)))
    target_freq = float(freq[target_idx])

    matrix_csv = out_dir / "matrix_error_at_target.csv"
    diff_csv = out_dir / "differential_params_by_frequency.csv"
    summary_json = out_dir / "matrix_error_summary.json"
    report_md = out_dir / "matrix_error_diagnostic.md"
    write_matrix_csv(matrix_csv, target_freq, emx_s_i[target_idx], hfss_s_i[target_idx], emx_z_i[target_idx], hfss_z_i[target_idx])
    write_diff_csv(diff_csv, freq, emx_params, hfss_params)
    summary = build_summary(args, target_freq, emx_s_i[target_idx], hfss_s_i[target_idx], emx_z_i[target_idx], hfss_z_i[target_idx], emx_params, hfss_params)
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_md.write_text(render_report(summary), encoding="utf-8")
    plot_paths = maybe_plot(out_dir, summary, target_freq, emx_s_i[target_idx], hfss_s_i[target_idx], emx_params, hfss_params)
    manifest_path = write_manifest(
        out_dir,
        [Path(args.emx), Path(args.hfss)],
        [matrix_csv, diff_csv, summary_json, report_md, *plot_paths],
    )

    print(f"target_freq_hz={target_freq:.6g}")
    print(f"summary={summary_json}")
    print(f"matrix_csv={matrix_csv}")
    print(f"diff_csv={diff_csv}")
    print(f"report={report_md}")
    for path in plot_paths:
        print(f"plot={path}")
    print(f"manifest={manifest_path}")
    print(f"largest_s_abs_error={summary['target_matrix']['largest_s_abs_error']['element']} {summary['target_matrix']['largest_s_abs_error']['abs_error']:.6g}")
    print(f"largest_diff_metric_error={summary['differential_metric_errors']['largest_metric']} {summary['differential_metric_errors']['largest_max_percent_error']:.6g}%")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx", required=True)
    parser.add_argument("--hfss", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--target-freq-hz", type=float, default=15.0e9)
    return parser.parse_args(argv)


def common_freq_grid(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    f_min = max(float(np.min(a)), float(np.min(b)))
    f_max = min(float(np.max(a)), float(np.max(b)))
    freq = np.asarray([f for f in a if f_min <= f <= f_max], dtype=float)
    if len(freq) < 2:
        freq = np.linspace(f_min, f_max, 101)
    return freq


def interp_matrix(freq: np.ndarray, source_freq: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    out = np.zeros((len(freq), matrix.shape[1], matrix.shape[2]), dtype=np.complex128)
    for i in range(matrix.shape[1]):
        for j in range(matrix.shape[2]):
            out[:, i, j] = np.interp(freq, source_freq, matrix[:, i, j].real) + 1j * np.interp(freq, source_freq, matrix[:, i, j].imag)
    return out


def parse_port_pairs(text: str) -> tuple[tuple[int, int], tuple[int, int]]:
    first, second = text.split(":", 1)
    a, b = (int(item.strip()) - 1 for item in first.split(",", 1))
    c, d = (int(item.strip()) - 1 for item in second.split(",", 1))
    return (a, b), (c, d)


def differential_z(z_single: np.ndarray, pairs: tuple[tuple[int, int], tuple[int, int]]) -> np.ndarray:
    transform = np.zeros((4, 2), dtype=np.complex128)
    transform[pairs[0][0], 0] = 1.0
    transform[pairs[0][1], 0] = -1.0
    transform[pairs[1][0], 1] = 1.0
    transform[pairs[1][1], 1] = -1.0
    return np.einsum("ai,fab,bj->fij", transform, z_single, transform)


def diff_params(freq: np.ndarray, z_diff: np.ndarray) -> dict[str, np.ndarray]:
    omega = 2.0 * math.pi * freq
    z11 = z_diff[:, 0, 0]
    z22 = z_diff[:, 1, 1]
    z12 = z_diff[:, 0, 1]
    z21 = z_diff[:, 1, 0]
    lp_h = np.imag(z11) / omega
    ls_h = np.imag(z22) / omega
    m_h = np.imag(z21) / omega
    k = m_h / np.sqrt(np.maximum(np.abs(lp_h * ls_h), 1.0e-30))
    return {
        "r11_ohm": np.real(z11),
        "x11_ohm": np.imag(z11),
        "r22_ohm": np.real(z22),
        "x22_ohm": np.imag(z22),
        "r12_ohm": np.real(z12),
        "x12_ohm": np.imag(z12),
        "lp_nh": lp_h * 1.0e9,
        "ls_nh": ls_h * 1.0e9,
        "m_nh": m_h * 1.0e9,
        "k": k,
        "qp": safe_div(np.imag(z11), np.real(z11)),
        "qs": safe_div(np.imag(z22), np.real(z22)),
    }


def safe_div(num: np.ndarray, denom: np.ndarray) -> np.ndarray:
    out = np.full_like(num, np.nan, dtype=float)
    mask = np.abs(denom) > 1.0e-30
    out[mask] = num[mask] / denom[mask]
    return out


def rel_pct(hfss: np.ndarray, emx: np.ndarray, floor: float = 1.0e-12) -> np.ndarray:
    return np.abs(hfss - emx) / np.maximum(np.abs(emx), floor) * 100.0


def write_matrix_csv(path: Path, target_freq: float, emx_s: np.ndarray, hfss_s: np.ndarray, emx_z: np.ndarray, hfss_z: np.ndarray) -> None:
    rows = []
    for i in range(4):
        for j in range(4):
            rows.append(
                {
                    "freq_hz": target_freq,
                    "element": f"{i + 1},{j + 1}",
                    "emx_s_real": emx_s[i, j].real,
                    "emx_s_imag": emx_s[i, j].imag,
                    "hfss_s_real": hfss_s[i, j].real,
                    "hfss_s_imag": hfss_s[i, j].imag,
                    "s_complex_abs_error": abs(hfss_s[i, j] - emx_s[i, j]),
                    "s_magnitude_percent_error": abs(abs(hfss_s[i, j]) - abs(emx_s[i, j])) / max(abs(emx_s[i, j]), 1.0e-12) * 100.0,
                    "emx_z_real": emx_z[i, j].real,
                    "emx_z_imag": emx_z[i, j].imag,
                    "hfss_z_real": hfss_z[i, j].real,
                    "hfss_z_imag": hfss_z[i, j].imag,
                    "z_complex_abs_error_ohm": abs(hfss_z[i, j] - emx_z[i, j]),
                    "z_magnitude_percent_error": abs(abs(hfss_z[i, j]) - abs(emx_z[i, j])) / max(abs(emx_z[i, j]), 1.0e-12) * 100.0,
                }
            )
    write_rows(path, rows)


def write_diff_csv(path: Path, freq: np.ndarray, emx: dict[str, np.ndarray], hfss: dict[str, np.ndarray]) -> None:
    rows = []
    for idx, f in enumerate(freq):
        row: dict[str, Any] = {"freq_hz": float(f), "freq_ghz": float(f / 1.0e9)}
        for metric in METRICS:
            row[f"emx_{metric}"] = float(emx[metric][idx])
            row[f"hfss_{metric}"] = float(hfss[metric][idx])
            row[f"{metric}_abs_error"] = float(abs(hfss[metric][idx] - emx[metric][idx]))
            row[f"{metric}_percent_error"] = float(rel_pct(np.asarray([hfss[metric][idx]]), np.asarray([emx[metric][idx]]), floor=relative_floor(metric))[0])
        rows.append(row)
    write_rows(path, rows)


def relative_floor(metric: str) -> float:
    return 1.0e-3 if metric in {"k"} else 1.0e-9


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_summary(
    args: argparse.Namespace,
    target_freq: float,
    emx_s: np.ndarray,
    hfss_s: np.ndarray,
    emx_z: np.ndarray,
    hfss_z: np.ndarray,
    emx_params: dict[str, np.ndarray],
    hfss_params: dict[str, np.ndarray],
) -> dict[str, Any]:
    s_abs = np.abs(hfss_s - emx_s)
    z_abs = np.abs(hfss_z - emx_z)
    s_idx = tuple(int(i) for i in np.unravel_index(np.argmax(s_abs), s_abs.shape))
    z_idx = tuple(int(i) for i in np.unravel_index(np.argmax(z_abs), z_abs.shape))
    metric_errors = {}
    for metric in METRICS:
        pct = rel_pct(hfss_params[metric], emx_params[metric], floor=relative_floor(metric))
        metric_errors[metric] = {
            "max_percent_error": float(np.max(pct)),
            "mean_percent_error": float(np.mean(pct)),
            "emx_min": float(np.min(emx_params[metric])),
            "emx_max": float(np.max(emx_params[metric])),
            "hfss_min": float(np.min(hfss_params[metric])),
            "hfss_max": float(np.max(hfss_params[metric])),
        }
    largest_metric = max(metric_errors, key=lambda item: metric_errors[item]["max_percent_error"])
    return {
        "emx": str(Path(args.emx).resolve()),
        "hfss": str(Path(args.hfss).resolve()),
        "port_pairs": args.port_pairs,
        "target_freq_hz": target_freq,
        "target_matrix": {
            "largest_s_abs_error": {
                "element": f"S{s_idx[0] + 1}{s_idx[1] + 1}",
                "abs_error": float(s_abs[s_idx]),
                "emx": complex_parts(emx_s[s_idx]),
                "hfss": complex_parts(hfss_s[s_idx]),
            },
            "largest_z_abs_error": {
                "element": f"Z{z_idx[0] + 1}{z_idx[1] + 1}",
                "abs_error_ohm": float(z_abs[z_idx]),
                "emx": complex_parts(emx_z[z_idx]),
                "hfss": complex_parts(hfss_z[z_idx]),
            },
            "s_abs_error_matrix": s_abs.tolist(),
            "z_abs_error_ohm_matrix": z_abs.tolist(),
        },
        "differential_metric_errors": {
            "metrics": metric_errors,
            "largest_metric": largest_metric,
            "largest_max_percent_error": metric_errors[largest_metric]["max_percent_error"],
        },
        "interpretation": (
            "This diagnostic localizes the validation failure at matrix/impedance level. "
            "Large R/Q errors point to loss/reference differences, while M/k errors point to mutual-coupling equivalence."
        ),
    }


def complex_parts(value: complex) -> dict[str, float]:
    return {"real": float(np.real(value)), "imag": float(np.imag(value)), "magnitude": float(abs(value))}


def render_report(summary: dict[str, Any]) -> str:
    metrics = summary["differential_metric_errors"]["metrics"]
    lines = [
        "# S4P Matrix Error Diagnostic",
        "",
        f"- Target frequency: `{summary['target_freq_hz'] / 1.0e9:.6g} GHz`",
        f"- Port pairs: `{summary['port_pairs']}`",
        "",
        "## Largest Target-Frequency Matrix Errors",
        "",
        f"- Largest S error: `{summary['target_matrix']['largest_s_abs_error']['element']}` = `{summary['target_matrix']['largest_s_abs_error']['abs_error']:.6g}`",
        f"- Largest Z error: `{summary['target_matrix']['largest_z_abs_error']['element']}` = `{summary['target_matrix']['largest_z_abs_error']['abs_error_ohm']:.6g} ohm`",
        "",
        "## Differential Metric Error Summary",
        "",
        "| Metric | Max percent error | Mean percent error | EMX range | HFSS range |",
        "|---|---:|---:|---:|---:|",
    ]
    for metric, item in metrics.items():
        lines.append(
            f"| `{metric}` | {item['max_percent_error']:.4f}% | {item['mean_percent_error']:.4f}% | "
            f"{item['emx_min']:.6g} to {item['emx_max']:.6g} | {item['hfss_min']:.6g} to {item['hfss_max']:.6g} |"
        )
    lines.extend(
        [
            "",
            f"Largest metric by max percent error: `{summary['differential_metric_errors']['largest_metric']}` "
            f"({summary['differential_metric_errors']['largest_max_percent_error']:.4f}%).",
            "",
        ]
    )
    return "\n".join(lines)


def maybe_plot(
    out_dir: Path,
    summary: dict[str, Any],
    target_freq: float,
    emx_s: np.ndarray,
    hfss_s: np.ndarray,
    emx_params: dict[str, np.ndarray],
    hfss_params: dict[str, np.ndarray],
) -> list[Path]:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return []
    paths: list[Path] = []
    s_abs = np.abs(hfss_s - emx_s)
    fig, ax = plt.subplots(figsize=(6.4, 5.5), facecolor="#FCFCFD")
    im = ax.imshow(s_abs, cmap="magma")
    ax.set_xticks(range(4), [f"P{i}" for i in range(1, 5)])
    ax.set_yticks(range(4), [f"P{i}" for i in range(1, 5)])
    ax.set_title(f"|HFSS - EMX| S-matrix at {target_freq / 1e9:.3g} GHz", loc="left", fontweight="bold")
    for i in range(4):
        for j in range(4):
            ax.text(j, i, f"{s_abs[i, j]:.3f}", ha="center", va="center", color="white" if s_abs[i, j] > np.max(s_abs) * 0.45 else "#1F2430", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    path = out_dir / "s_matrix_abs_error_heatmap.png"
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths.append(path)

    labels = ["R11", "R22", "R12", "Lp", "Ls", "M", "k", "Qp", "Qs"]
    metric_map = {
        "R11": "r11_ohm",
        "R22": "r22_ohm",
        "R12": "r12_ohm",
        "Lp": "lp_nh",
        "Ls": "ls_nh",
        "M": "m_nh",
        "k": "k",
        "Qp": "qp",
        "Qs": "qs",
    }
    idx = len(next(iter(emx_params.values()))) // 2
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(11.5, 5.2), facecolor="#FCFCFD")
    emx_vals = [emx_params[metric_map[label]][idx] for label in labels]
    hfss_vals = [hfss_params[metric_map[label]][idx] for label in labels]
    width = 0.38
    ax.bar(x - width / 2, emx_vals, width=width, label="EMX", color="#5477C4", edgecolor="#1F2430", linewidth=0.35)
    ax.bar(x + width / 2, hfss_vals, width=width, label="HFSS", color="#CC6F47", edgecolor="#1F2430", linewidth=0.35)
    ax.set_xticks(x, labels)
    ax.set_title("Differential impedance-derived quantities at middle frequency", loc="left", fontweight="bold")
    ax.grid(axis="y", color="#E6E8F0")
    ax.legend(frameon=False)
    path = out_dir / "differential_quantity_comparison.png"
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths.append(path)
    return paths


def write_manifest(out_dir: Path, inputs: list[Path], outputs: list[Path]) -> Path:
    payload = {
        "inputs": [{"path": str(path.resolve()), "sha256": sha256(path)} for path in inputs],
        "outputs": [{"path": str(path.resolve()), "sha256": sha256(path)} for path in outputs],
    }
    path = out_dir / "matrix_error_manifest.json"
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
