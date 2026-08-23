#!/usr/bin/env python3
"""Diagnose Cm disagreement between two 4-port Touchstone files.

Cm is intentionally kept separate from the K/Q/L validation gate because the
definition depends on which Y-parameter convention is used in ADS. This script
computes several explicit definitions and reports which ones, if any, satisfy a
relative-error gate.
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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from compare_emx_hfss_ads import four_port_z_to_differential_z, parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.network_analysis import s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


DEFAULT_DEFINITION = "single_primary_y11_plus_y12_ff"
DEFINITION_LABELS = {
    "single_primary_y11_plus_y12_ff": "single-ended primary: imag(Y11 + Y12) / omega",
    "single_primary_y22_plus_y21_ff": "single-ended primary negative terminal: imag(Y22 + Y21) / omega",
    "single_secondary_y33_plus_y34_ff": "single-ended secondary: imag(Y33 + Y34) / omega",
    "single_secondary_y44_plus_y43_ff": "single-ended secondary negative terminal: imag(Y44 + Y43) / omega",
    "diff_primary_y11_plus_y12_ff": "differential 2-port primary: imag(Yd11 + Yd12) / omega",
    "diff_secondary_y22_plus_y21_ff": "differential 2-port secondary: imag(Yd22 + Yd21) / omega",
}


@dataclass(frozen=True)
class CmCurves:
    source: str
    freq_hz: np.ndarray
    definitions_ff: dict[str, np.ndarray]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    emx = _load_cm_curves(Path(args.emx), port_pairs=parse_port_pairs(args.emx_port_pairs))
    hfss = _load_cm_curves(Path(args.hfss), port_pairs=parse_port_pairs(args.hfss_port_pairs))
    result = _compare_cm(emx, hfss, args)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "cm_mismatch_summary.json"
    report_path = out_dir / "cm_mismatch_report.md"
    curves_path = out_dir / "cm_mismatch_curves.csv"
    manifest_path = out_dir / "cm_mismatch_manifest.json"
    plot_path = out_dir / "cm_mismatch_selected_definition.png"

    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(result), encoding="utf-8")
    _write_curves_csv(curves_path, result)
    if not args.no_plot:
        _maybe_plot(plot_path, result)
    manifest_path.write_text(
        json.dumps(
            {
                "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "inputs": [_file_record(Path(args.emx)), _file_record(Path(args.hfss))],
                "outputs": [
                    _file_record(path)
                    for path in (summary_path, report_path, curves_path, plot_path)
                    if path.exists()
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    selected = result["definitions"][args.definition]
    print(f"overall_status={result['overall_status']}")
    print(f"selected_definition={args.definition}")
    print(f"selected_status={selected['status']}")
    print(f"selected_max_percent_error={selected['max_percent_error']:.6g}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"curves_csv={curves_path}")
    if plot_path.exists():
        print(f"plot={plot_path}")
    print(f"manifest={manifest_path}")
    return 2 if result["overall_status"] == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx", required=True)
    parser.add_argument("--hfss", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--emx-port-pairs", default="1,2:3,4")
    parser.add_argument("--hfss-port-pairs", default="1,2:3,4")
    parser.add_argument("--definition", default=DEFAULT_DEFINITION, choices=sorted(DEFINITION_LABELS))
    parser.add_argument("--max-percent-error", type=float, default=5.0)
    parser.add_argument("--relative-floor-ff", type=float, default=1.0)
    parser.add_argument("--target-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _load_cm_curves(path: Path, *, port_pairs: tuple[tuple[int, int], tuple[int, int]]) -> CmCurves:
    sparams = load_touchstone(path)
    if sparams.s_matrix.shape[1:] != (4, 4):
        raise ValueError(f"Cm diagnostic expects a 4-port Touchstone file, got shape {sparams.s_matrix.shape}")
    freq = np.asarray(sparams.freqs_hz, dtype=float)
    z_single = s_to_z(sparams.s_matrix, z0=sparams.reference_impedance_ohm)
    y_single = _invert_by_frequency(z_single)
    z_diff = four_port_z_to_differential_z(z_single, port_pairs)
    y_diff = _invert_by_frequency(z_diff)
    omega = 2.0 * math.pi * freq
    definitions = {
        "single_primary_y11_plus_y12_ff": _cm_ff(y_single[:, 0, 0] + y_single[:, 0, 1], omega),
        "single_primary_y22_plus_y21_ff": _cm_ff(y_single[:, 1, 1] + y_single[:, 1, 0], omega),
        "single_secondary_y33_plus_y34_ff": _cm_ff(y_single[:, 2, 2] + y_single[:, 2, 3], omega),
        "single_secondary_y44_plus_y43_ff": _cm_ff(y_single[:, 3, 3] + y_single[:, 3, 2], omega),
        "diff_primary_y11_plus_y12_ff": _cm_ff(y_diff[:, 0, 0] + y_diff[:, 0, 1], omega),
        "diff_secondary_y22_plus_y21_ff": _cm_ff(y_diff[:, 1, 1] + y_diff[:, 1, 0], omega),
    }
    return CmCurves(source=str(path.expanduser().resolve()), freq_hz=freq, definitions_ff=definitions)


def _invert_by_frequency(matrix: np.ndarray) -> np.ndarray:
    out = np.empty_like(matrix, dtype=np.complex128)
    for idx, item in enumerate(matrix):
        out[idx] = np.linalg.inv(item)
    return out


def _cm_ff(y_expr: np.ndarray, omega: np.ndarray) -> np.ndarray:
    return np.imag(y_expr) / omega * 1.0e15


def _compare_cm(emx: CmCurves, hfss: CmCurves, args: argparse.Namespace) -> dict[str, Any]:
    freq = _common_freq(emx.freq_hz, hfss.freq_hz)
    definitions: dict[str, Any] = {}
    plot_data: dict[str, Any] = {"freq_hz": freq.tolist(), "definitions": {}}
    for name in sorted(DEFINITION_LABELS):
        emx_values = _interp(freq, emx.freq_hz, emx.definitions_ff[name])
        hfss_values = _interp(freq, hfss.freq_hz, hfss.definitions_ff[name])
        abs_error = np.abs(hfss_values - emx_values)
        pct_error = abs_error / np.maximum(np.abs(emx_values), float(args.relative_floor_ff)) * 100.0
        target_idx = int(np.argmin(np.abs(freq - float(args.target_frequency_ghz) * 1.0e9)))
        definitions[name] = {
            "label": DEFINITION_LABELS[name],
            "status": "PASS" if float(np.max(pct_error)) <= float(args.max_percent_error) else "FAIL",
            "max_abs_error_ff": float(np.max(abs_error)),
            "mean_abs_error_ff": float(np.mean(abs_error)),
            "max_percent_error": float(np.max(pct_error)),
            "mean_percent_error": float(np.mean(pct_error)),
            "target_frequency_hz": float(freq[target_idx]),
            "target_emx_ff": float(emx_values[target_idx]),
            "target_hfss_ff": float(hfss_values[target_idx]),
            "target_abs_error_ff": float(abs_error[target_idx]),
            "target_percent_error": float(pct_error[target_idx]),
        }
        plot_data["definitions"][name] = {
            "emx_ff": emx_values.tolist(),
            "hfss_ff": hfss_values.tolist(),
            "percent_error": pct_error.tolist(),
        }

    selected = definitions[args.definition]
    return {
        "overall_status": selected["status"],
        "criterion": {
            "selected_definition": args.definition,
            "max_percent_error": float(args.max_percent_error),
            "relative_floor_ff": float(args.relative_floor_ff),
        },
        "emx_source": emx.source,
        "hfss_source": hfss.source,
        "frequency_overlap_hz": {"min": float(freq[0]), "max": float(freq[-1]), "count": int(len(freq))},
        "definitions": definitions,
        "plot_data": plot_data,
        "interpretation": (
            "A Cm definition should only be used in reports if its ADS formula and port convention match the selected row. "
            "This diagnostic quantifies Cm but does not change the K/Q/L pass gate."
        ),
    }


def _common_freq(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    f_min = max(float(np.min(a)), float(np.min(b)))
    f_max = min(float(np.max(a)), float(np.max(b)))
    if not f_min < f_max:
        raise ValueError("EMX and HFSS frequency ranges do not overlap")
    freq = np.asarray([f for f in a if f_min <= f <= f_max], dtype=float)
    if len(freq) < 2:
        freq = np.linspace(f_min, f_max, 101)
    return freq


def _interp(freq: np.ndarray, source_freq: np.ndarray, values: np.ndarray) -> np.ndarray:
    return np.interp(freq, source_freq, values)


def _write_curves_csv(path: Path, result: dict[str, Any]) -> None:
    freq = result["plot_data"]["freq_hz"]
    definitions = result["plot_data"]["definitions"]
    fieldnames = ["freq_hz", "freq_ghz"]
    for name in sorted(definitions):
        fieldnames.extend([f"emx_{name}", f"hfss_{name}", f"{name}_percent_error"])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for idx, freq_hz in enumerate(freq):
            row: dict[str, Any] = {"freq_hz": freq_hz, "freq_ghz": float(freq_hz) / 1.0e9}
            for name in sorted(definitions):
                data = definitions[name]
                row[f"emx_{name}"] = data["emx_ff"][idx]
                row[f"hfss_{name}"] = data["hfss_ff"][idx]
                row[f"{name}_percent_error"] = data["percent_error"][idx]
            writer.writerow(row)


def _render_report(result: dict[str, Any]) -> str:
    selected = result["criterion"]["selected_definition"]
    lines = [
        "# Cm Mismatch Diagnostic",
        "",
        f"- Overall status for selected definition `{selected}`: **{result['overall_status']}**",
        f"- EMX source: `{result['emx_source']}`",
        f"- HFSS source: `{result['hfss_source']}`",
        f"- Frequency overlap: `{result['frequency_overlap_hz']}`",
        f"- Pass criterion: max relative error <= `{result['criterion']['max_percent_error']}%`",
        "",
        "Cm is not part of the K/Q/L validation pass gate. It is listed separately because ADS pages may use different Y-parameter conventions.",
        "",
        "| Definition | Status | Max err | Mean err | Target EMX fF | Target HFSS fF | Target err | Formula label |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for name, item in result["definitions"].items():
        lines.append(
            f"| `{name}` | {item['status']} | {item['max_percent_error']:.2f}% | "
            f"{item['mean_percent_error']:.2f}% | {item['target_emx_ff']:.3f} | "
            f"{item['target_hfss_ff']:.3f} | {item['target_percent_error']:.2f}% | {item['label']} |"
        )
    lines.extend(
        [
            "",
            "Interpretation:",
            "",
            result["interpretation"],
        ]
    )
    return "\n".join(lines) + "\n"


def _maybe_plot(path: Path, result: dict[str, Any]) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    selected = result["criterion"]["selected_definition"]
    data = result["plot_data"]["definitions"][selected]
    freq_ghz = np.asarray(result["plot_data"]["freq_hz"], dtype=float) / 1.0e9
    fig, axes = plt.subplots(2, 1, figsize=(9.5, 7.0), sharex=True, facecolor="#FCFCFD")
    axes[0].plot(freq_ghz, data["emx_ff"], label="EMX", color="#4267B2", linewidth=2)
    axes[0].plot(freq_ghz, data["hfss_ff"], label="HFSS", color="#C45A3A", linewidth=2)
    axes[0].set_ylabel("Cm (fF)")
    axes[0].set_title(f"Cm definition: {selected}", loc="left", fontweight="bold")
    axes[0].legend(frameon=False)
    axes[0].grid(color="#E5E7EB")
    axes[1].plot(freq_ghz, data["percent_error"], color="#7A3E9D", linewidth=2)
    axes[1].axhline(result["criterion"]["max_percent_error"], color="#202938", linestyle="--", linewidth=1)
    axes[1].set_ylabel("Percent error")
    axes[1].set_xlabel("Frequency (GHz)")
    axes[1].grid(color="#E5E7EB")
    for ax in axes:
        ax.set_facecolor("#FFFFFF")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _file_record(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    return {
        "path": str(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else None,
        "sha256": _sha256(path) if path.exists() else None,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
