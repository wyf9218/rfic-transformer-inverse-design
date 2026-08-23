#!/usr/bin/env python3
"""Audit whether real EMX S4P labels are ready for a broadband surrogate."""

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

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _read_rows(training_csv)
    selected = _deterministic_spread_sample(rows, int(args.max_files))
    audits = [_audit_row(row, args) for row in selected]
    pass_count = sum(item["status"] == "PASS" for item in audits)
    geometry_columns = sorted(column for column in (rows[0] if rows else {}) if column.startswith("geom__"))
    physical_columns = sorted(column for column in (rows[0] if rows else {}) if column.startswith("input__"))
    checks = {
        "training_csv_exists": training_csv.is_file(),
        "training_rows_present": bool(rows),
        "sample_count_meets_minimum": len(audits) >= int(args.min_files),
        "all_sampled_touchstones_pass": bool(audits) and pass_count == len(audits),
        "ten_geometry_columns_present": len(geometry_columns) == 10,
        "physical_feature_columns_present": _physical_contract_present(physical_columns),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    frequency_points = int(args.expected_frequency_points)
    ports = int(args.expected_ports)
    storage_gib = len(rows) * frequency_points * ports * ports * 2 * 4 / float(1024**3)
    reciprocal_entries = ports * (ports + 1) // 2
    reciprocal_storage_gib = len(rows) * frequency_points * reciprocal_entries * 2 * 4 / float(1024**3)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "READY_FOR_BROADBAND_COMPLEX_SPARAMETER_BASELINE" if status == "PASS" else "FIX_BROADBAND_LABEL_CONTRACT",
        "training_csv": str(training_csv),
        "training_row_count": len(rows),
        "sampled_touchstone_count": len(audits),
        "sampled_touchstone_pass_count": pass_count,
        "checks": checks,
        "geometry_columns": geometry_columns,
        "physical_columns": physical_columns,
        "frequency_contract": {
            "start_ghz": float(args.expected_frequency_start_ghz),
            "stop_ghz": float(args.expected_frequency_stop_ghz),
            "step_ghz": float(args.expected_frequency_step_ghz),
            "points": frequency_points,
            "ports": ports,
        },
        "sample_metric_summary": _metric_summary(audits),
        "estimated_full_complex_s_float32_gib": storage_gib,
        "reciprocal_upper_triangle_contract": {
            "unique_complex_entries_per_frequency": reciprocal_entries,
            "real_channels_per_frequency": reciprocal_entries * 2,
            "estimated_float32_gib": reciprocal_storage_gib,
            "reconstruction": "Sji=Sij",
            "additional_layout_symmetry_compression": "FORBIDDEN_UNTIL_PORT_PAIR_SYMMETRY_IS_NUMERICALLY_AUDITED",
        },
        "recommended_baseline": {
            "input": "10-D geometry plus normalized frequency",
            "output": "full complex reciprocal S4P matrix as Re/Im values",
            "first_model": "frequency-conditioned MLP or low-rank spectral baseline",
            "losses": ["complex_S_MSE", "reciprocity_penalty", "passivity_audit"],
            "reason": "Full complex S is stable across resonance and preserves information that center-frequency L/Q/K cannot.",
        },
        "paper_aligned_adaptation": {
            "pulserf_unet_role": "CONTROLLED_FORWARD_SURROGATE_ABLATION_ONLY",
            "tmtt_two_stage_role": "SPEC_TO_SPECTRUM_TO_GEOMETRY_CANDIDATE",
            "causality_layer_status": "NOT_A_HARD_GATE_ON_TRUNCATED_5_TO_60_GHZ_DATA",
            "center_tap_reuse_status": "NOT_APPLICABLE_TO_CURRENT_FOUR_PORT_PRODUCTION_CONTRACT",
            "reason": (
                "PulseRF's 4000-row result used image, symmetry, center-tap reuse, and wideband causality priors. "
                "Those assumptions must be ablated rather than copied into this 10-D four-port inverse task."
            ),
        },
        "sample_audit_csv": str(out_dir / "broadband_sparameter_readiness_samples.csv"),
        "scientific_boundary": (
            "This is a real-label readiness audit, not a trained broadband model. Passivity requires singular-value checks; "
            "clipping Re/Im(S) to [-1,1] alone is insufficient. Reciprocity permits upper-triangle storage, but additional "
            "layout-symmetry compression and finite-band Kramers-Kronig enforcement require separate numerical audits."
        ),
        "arguments": vars(args),
    }
    _write_csv(out_dir / "broadband_sparameter_readiness_samples.csv", audits)
    summary_path = out_dir / "broadband_sparameter_surrogate_readiness_summary.json"
    report_path = out_dir / "broadband_sparameter_surrogate_readiness_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-files", type=int, default=64)
    parser.add_argument("--max-files", type=int, default=256)
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-passivity-excess", type=float, default=0.05)
    parser.add_argument("--max-reciprocity-error", type=float, default=0.02)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.min_files < 1 or args.max_files < args.min_files:
        parser.error("require 1 <= --min-files <= --max-files")
    if args.expected_ports < 1 or args.expected_frequency_points < 2:
        parser.error("expected ports and frequency points are invalid")
    return args


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _deterministic_spread_sample(rows: list[dict[str, str]], maximum: int) -> list[dict[str, str]]:
    if len(rows) <= maximum:
        return list(rows)
    indices = np.linspace(0, len(rows) - 1, num=maximum, dtype=int)
    return [rows[int(index)] for index in indices]


def _audit_row(row: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    path = Path(str(row.get("touchstone_path") or "")).expanduser()
    result: dict[str, Any] = {
        "evaluation": row.get("evaluation") or row.get("sample_id") or "",
        "touchstone_path": str(path),
        "status": "FAIL",
        "error": "",
        "max_passivity_excess": None,
        "max_reciprocity_error": None,
    }
    try:
        if not path.is_file():
            raise FileNotFoundError(path)
        touchstone = load_touchstone(path)
        s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
        frequencies = np.asarray(touchstone.freqs_hz, dtype=float)
        expected_shape = (int(args.expected_frequency_points), int(args.expected_ports), int(args.expected_ports))
        if s_matrix.shape != expected_shape:
            raise ValueError(f"S shape {s_matrix.shape}, expected {expected_shape}")
        if not np.all(np.isfinite(s_matrix.real)) or not np.all(np.isfinite(s_matrix.imag)):
            raise ValueError("S matrix contains non-finite values")
        _validate_grid(frequencies, args)
        singular_values = np.linalg.svd(s_matrix, compute_uv=False)
        passivity_excess = float(max(0.0, np.max(singular_values) - 1.0))
        reciprocity = float(np.max(np.abs(s_matrix - np.swapaxes(s_matrix, 1, 2))))
        result.update(
            {
                "frequency_start_ghz": float(frequencies[0] / 1.0e9),
                "frequency_stop_ghz": float(frequencies[-1] / 1.0e9),
                "frequency_points": int(len(frequencies)),
                "max_passivity_excess": passivity_excess,
                "max_reciprocity_error": reciprocity,
            }
        )
        if passivity_excess > float(args.max_passivity_excess):
            raise ValueError(f"passivity excess {passivity_excess} exceeds {args.max_passivity_excess}")
        if reciprocity > float(args.max_reciprocity_error):
            raise ValueError(f"reciprocity error {reciprocity} exceeds {args.max_reciprocity_error}")
        result["status"] = "PASS"
    except Exception as exc:  # noqa: BLE001 - exact label failure is evidence.
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _validate_grid(frequencies: np.ndarray, args: argparse.Namespace) -> None:
    if len(frequencies) != int(args.expected_frequency_points) or not np.all(np.diff(frequencies) > 0.0):
        raise ValueError("frequency point count or monotonicity mismatch")
    expected = np.linspace(
        float(args.expected_frequency_start_ghz) * 1.0e9,
        float(args.expected_frequency_stop_ghz) * 1.0e9,
        int(args.expected_frequency_points),
    )
    if np.max(np.abs(frequencies - expected)) > float(args.frequency_tolerance_hz):
        raise ValueError("frequency grid differs from the declared uniform grid")
    step = float(np.median(np.diff(frequencies)))
    if abs(step - float(args.expected_frequency_step_ghz) * 1.0e9) > float(args.frequency_tolerance_hz):
        raise ValueError("frequency step mismatch")


def _physical_contract_present(columns: list[str]) -> bool:
    names = " ".join(column.lower() for column in columns)
    return all(token in names for token in ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center"))


def _metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    passing = [row for row in rows if row.get("status") == "PASS"]
    if not passing:
        return {}
    return {
        "max_passivity_excess": float(max(float(row["max_passivity_excess"]) for row in passing)),
        "max_reciprocity_error": float(max(float(row["max_reciprocity_error"]) for row in passing)),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _render_report(data: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Broadband complex-S surrogate readiness",
            "",
            f"- Overall status: **{data['overall_status']}**",
            f"- Decision: **{data['decision']}**",
            f"- Real training rows: `{data['training_row_count']}`",
            f"- Sampled S4P pass: `{data['sampled_touchstone_pass_count']}/{data['sampled_touchstone_count']}`",
            f"- Estimated full float32 complex-S tensor: `{data['estimated_full_complex_s_float32_gib']:.3f} GiB`",
            f"- Estimated reciprocal upper-triangle tensor: `{data['reciprocal_upper_triangle_contract']['estimated_float32_gib']:.3f} GiB`",
            f"- Independent complex entries per frequency: `{data['reciprocal_upper_triangle_contract']['unique_complex_entries_per_frequency']}`",
            "",
            data["scientific_boundary"],
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
