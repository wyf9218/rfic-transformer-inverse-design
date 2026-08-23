#!/usr/bin/env python3
"""Extract transformer response features and Zin labels from Touchstone files.

This helper is intended for completed MARS/EMX runs after `.s4p` files exist.
It does not run EMX, HFSS, or ADS. Instead, it converts each 4-port single-ended
Touchstone matrix to the differential 2-port representation used by the ADS
formulas, then writes compact response labels for coverage audits and training
metadata.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
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

from compare_emx_hfss_ads import four_port_z_to_differential_z, multiport_z_to_differential_z, parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.analysis import multiport_s_to_grounded_differential_z  # noqa: E402
from rfic_transformer_inverse_design.network_analysis import s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


CM_DEFINITIONS = {
    "cm_single_primary_y11_plus_y12_ff": "single-ended primary: imag(Y11 + Y12) / omega",
    "cm_single_primary_y22_plus_y21_ff": "single-ended primary negative terminal: imag(Y22 + Y21) / omega",
    "cm_single_secondary_y33_plus_y34_ff": "single-ended secondary: imag(Y33 + Y34) / omega",
    "cm_single_secondary_y44_plus_y43_ff": "single-ended secondary negative terminal: imag(Y44 + Y43) / omega",
    "cm_diff_primary_y11_plus_y12_ff": "differential 2-port primary: imag(Yd11 + Yd12) / omega",
    "cm_diff_secondary_y22_plus_y21_ff": "differential 2-port secondary: imag(Yd22 + Yd21) / omega",
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    source_rows = _read_dataset_rows(dataset_dir / "dataset_rows.csv")
    candidates = _discover_touchstones(dataset_dir, source_rows)
    if args.max_files is not None:
        candidates = candidates[: int(args.max_files)]

    feature_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        feature_rows.append(_extract_one(candidate, args))

    status = _overall_status(feature_rows, candidates)
    summary = _build_summary(dataset_dir, out_dir, candidates, feature_rows, status, args)
    response_csv = out_dir / "response_features.csv"
    rows_csv = out_dir / "dataset_rows.csv"
    report_path = out_dir / "response_feature_extraction_report.md"
    summary_path = out_dir / "response_feature_extraction_summary.json"
    manifest_path = out_dir / "dataset_manifest.json"

    _write_csv(response_csv, feature_rows)
    _write_csv(rows_csv, _merge_source_rows(source_rows, feature_rows))
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    source_manifest = _read_json(dataset_dir / "dataset_manifest.json")
    derived_manifest = dict(source_manifest)
    derived_manifest.update(
        {
            "requested_count": len(candidates),
            "ok_count": sum(_truthy(row.get("ok")) for row in feature_rows),
            "fail_count": sum(not _truthy(row.get("ok")) for row in feature_rows),
            "response_feature_source": "Touchstone post-processing",
            "source_dataset_dir": str(dataset_dir),
            "generated_utc": summary["generated_utc"],
            "response_feature_extraction": {
                "port_pairs": args.port_pairs,
                "ground_unused_ports": bool(args.ground_unused_ports),
                "expected_ports": int(args.expected_ports),
                "target_frequency_ghz": float(args.target_frequency_ghz),
            },
        }
    )
    manifest_path.write_text(json.dumps(derived_manifest, indent=2), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"features_csv={response_csv}")
    print(f"dataset_rows_csv={rows_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"manifest={manifest_path}")
    return 2 if status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", help="Dataset/run directory containing dataset_rows.csv or evaluations/*/emx/*.s*p")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--port-pairs", default="1,2:3,4", help="Differential port pairs for .s4p files")
    parser.add_argument(
        "--ground-unused-ports",
        action="store_true",
        help="Short all ports outside selected differential pairs to ground before extracting features. Required for S8P power-line ports grounded in ADS.",
    )
    parser.add_argument("--target-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--load-ohm", type=float, default=50.0, help="Differential load used for loaded Zin columns")
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-frequency-start-ghz", type=float)
    parser.add_argument("--expected-frequency-stop-ghz", type=float)
    parser.add_argument("--expected-frequency-step-ghz", type=float)
    parser.add_argument("--expected-frequency-points", type=int)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


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
            candidates.append({"evaluation": evaluation, "row_index": idx, "path": candidate_path, "source_row": row})

    if candidates:
        return candidates

    paths = sorted(dataset_dir.glob("evaluations/*/emx/*.s*p"))
    if not paths:
        paths = sorted(dataset_dir.glob("parallel_shards/shard_*/evaluations/*/emx/*.s*p"))
    if not paths:
        paths = sorted(dataset_dir.glob("*.s*p"))
    for idx, path in enumerate(path for path in paths if _is_touchstone(path)):
        if len(path.parents) >= 2 and path.parent.name == "emx":
            evaluation = path.parents[1].name
            if "parallel_shards" in path.parts:
                shard = next((part for part in path.parts if part.startswith("shard_")), "")
                if shard:
                    evaluation = f"{shard}__{evaluation}"
        else:
            evaluation = path.stem
        candidates.append({"evaluation": evaluation, "row_index": idx, "path": path.resolve(), "source_row": {}})
    return candidates


def _extract_one(candidate: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    path = Path(candidate["path"]).resolve()
    base: dict[str, Any] = {
        "evaluation": candidate["evaluation"],
        "ok": "false",
        "touchstone_path": str(path),
        "touchstone_sha256": _sha256(path) if path.exists() else "",
        "error": "",
    }
    try:
        touchstone = load_touchstone(path)
        s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
        freqs_hz = np.asarray(touchstone.freqs_hz, dtype=float)
        _validate_basic_touchstone(s_matrix, freqs_hz, args)
        z_single = s_to_z(s_matrix, z0=touchstone.reference_impedance_ohm)
        if z_single.shape[1:] == (2, 2):
            z_diff = z_single
        elif bool(args.ground_unused_ports):
            z_diff = multiport_s_to_grounded_differential_z(
                s_matrix,
                touchstone.reference_impedance_ohm,
                parse_port_pairs(args.port_pairs),
            )
        elif z_single.shape[1:] == (4, 4):
            z_diff = four_port_z_to_differential_z(z_single, parse_port_pairs(args.port_pairs))
        else:
            z_diff = multiport_z_to_differential_z(z_single, parse_port_pairs(args.port_pairs))
        curves = _response_curves(z_diff, freqs_hz, load_ohm=float(args.load_ohm), z_single=z_single)
        target = _target_row(curves, freqs_hz, float(args.target_frequency_ghz))
        base.update(_frequency_summary(freqs_hz))
        base.update(_matrix_quality(s_matrix))
        base.update(target)
        base.update(_band_summary(curves))
        base["ok"] = "true"
    except Exception as exc:
        base["error"] = f"{type(exc).__name__}: {exc}"
    return base


def _validate_basic_touchstone(s_matrix: np.ndarray, freqs_hz: np.ndarray, args: argparse.Namespace) -> None:
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


def _response_curves(
    z_diff: np.ndarray,
    freqs_hz: np.ndarray,
    *,
    load_ohm: float,
    z_single: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    omega = 2.0 * math.pi * np.asarray(freqs_hz, dtype=float)
    z11 = z_diff[:, 0, 0]
    z22 = z_diff[:, 1, 1]
    z21 = z_diff[:, 1, 0]
    lp_nh = np.imag(z11) / omega * 1.0e9
    ls_nh = np.imag(z22) / omega * 1.0e9
    z12 = z_diff[:, 0, 1]
    m_nh = np.imag(z21) / omega * 1.0e9
    denom = np.sqrt(np.maximum(np.abs(lp_nh * ls_nh), 1.0e-30))
    load = complex(float(load_ohm))
    curves = {
        "lp_nh": lp_nh,
        "ls_nh": ls_nh,
        "m_nh": m_nh,
        "k": m_nh / denom,
        "qp": _safe_div(np.imag(z11), np.real(z11)),
        "qs": _safe_div(np.imag(z22), np.real(z22)),
        "zin_primary_open": z11,
        "zin_secondary_open": z22,
        "zin_primary_loaded": z11 - (z12 * z21) / (z22 + load),
        "zin_secondary_loaded": z22 - (z21 * z12) / (z11 + load),
    }
    curves.update(_cm_curves(z_diff, omega, z_single=z_single))
    return curves


def _cm_curves(z_diff: np.ndarray, omega: np.ndarray, *, z_single: np.ndarray | None) -> dict[str, np.ndarray]:
    y_diff = _invert_by_frequency(z_diff)
    values: dict[str, np.ndarray] = {
        "cm_diff_primary_y11_plus_y12_ff": _cm_ff(y_diff[:, 0, 0] + y_diff[:, 0, 1], omega),
        "cm_diff_secondary_y22_plus_y21_ff": _cm_ff(y_diff[:, 1, 1] + y_diff[:, 1, 0], omega),
    }
    if z_single is not None and z_single.shape[1:] == (4, 4):
        y_single = _invert_by_frequency(z_single)
        values.update(
            {
                "cm_single_primary_y11_plus_y12_ff": _cm_ff(y_single[:, 0, 0] + y_single[:, 0, 1], omega),
                "cm_single_primary_y22_plus_y21_ff": _cm_ff(y_single[:, 1, 1] + y_single[:, 1, 0], omega),
                "cm_single_secondary_y33_plus_y34_ff": _cm_ff(y_single[:, 2, 2] + y_single[:, 2, 3], omega),
                "cm_single_secondary_y44_plus_y43_ff": _cm_ff(y_single[:, 3, 3] + y_single[:, 3, 2], omega),
            }
        )
    else:
        nan = np.full_like(omega, np.nan, dtype=float)
        values.update(
            {
                "cm_single_primary_y11_plus_y12_ff": nan,
                "cm_single_primary_y22_plus_y21_ff": nan,
                "cm_single_secondary_y33_plus_y34_ff": nan,
                "cm_single_secondary_y44_plus_y43_ff": nan,
            }
        )
    return values


def _invert_by_frequency(matrix: np.ndarray) -> np.ndarray:
    out = np.empty_like(matrix, dtype=np.complex128)
    for idx, item in enumerate(matrix):
        out[idx] = np.linalg.inv(item)
    return out


def _cm_ff(y_expr: np.ndarray, omega: np.ndarray) -> np.ndarray:
    return np.imag(y_expr) / omega * 1.0e15


def _target_row(curves: dict[str, np.ndarray], freqs_hz: np.ndarray, target_ghz: float) -> dict[str, Any]:
    idx = int(np.argmin(np.abs(freqs_hz - target_ghz * 1.0e9)))
    row: dict[str, Any] = {
        "target_frequency_ghz": float(target_ghz),
        "target_frequency_used_ghz": float(freqs_hz[idx] / 1.0e9),
        "target_frequency_error_hz": float(abs(freqs_hz[idx] - target_ghz * 1.0e9)),
        "zin_center_mode": "primary_loaded_50ohm_differential",
    }
    for name in ("lp_nh", "ls_nh", "m_nh", "k", "qp", "qs", *CM_DEFINITIONS):
        row[f"{name}_center"] = float(curves[name][idx])
    for prefix in ("zin_primary_open", "zin_secondary_open", "zin_primary_loaded", "zin_secondary_loaded"):
        value = complex(curves[prefix][idx])
        row[f"{prefix}_real_ohm"] = float(value.real)
        row[f"{prefix}_imag_ohm"] = float(value.imag)
        row[f"{prefix}_abs_ohm"] = float(abs(value))
    row["zin_center_real_ohm"] = row["zin_primary_loaded_real_ohm"]
    row["zin_center_imag_ohm"] = row["zin_primary_loaded_imag_ohm"]
    row["zin_center_abs_ohm"] = row["zin_primary_loaded_abs_ohm"]
    return row


def _band_summary(curves: dict[str, np.ndarray]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for name, values in curves.items():
        if np.iscomplexobj(values):
            row[f"{name}_real_min_ohm"] = float(np.nanmin(values.real))
            row[f"{name}_real_max_ohm"] = float(np.nanmax(values.real))
            row[f"{name}_imag_min_ohm"] = float(np.nanmin(values.imag))
            row[f"{name}_imag_max_ohm"] = float(np.nanmax(values.imag))
            row[f"{name}_abs_min_ohm"] = float(np.nanmin(np.abs(values)))
            row[f"{name}_abs_max_ohm"] = float(np.nanmax(np.abs(values)))
        else:
            arr = np.asarray(values, dtype=float)
            row[f"{name}_min"] = float(np.nanmin(arr))
            row[f"{name}_max"] = float(np.nanmax(arr))
            row[f"{name}_p50"] = float(np.nanpercentile(arr, 50.0))
    return row


def _frequency_summary(freqs_hz: np.ndarray) -> dict[str, Any]:
    return {
        "frequency_start_hz": float(freqs_hz[0]),
        "frequency_stop_hz": float(freqs_hz[-1]),
        "frequency_step_hz": float(np.median(np.diff(freqs_hz))) if len(freqs_hz) >= 2 else "",
        "frequency_points": int(len(freqs_hz)),
    }


def _matrix_quality(s_matrix: np.ndarray) -> dict[str, Any]:
    rec = float(np.max(np.abs(s_matrix - np.swapaxes(s_matrix, 1, 2)))) if s_matrix.size else math.nan
    sigma = float(np.max(np.linalg.svd(s_matrix, compute_uv=False))) if s_matrix.size else math.nan
    return {"reciprocity_error_abs_max": rec, "passivity_sigma_max": sigma}


def _read_dataset_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_json_error": True}
    return data if isinstance(data, dict) else {"_json_type": type(data).__name__}


def _merge_source_rows(source_rows: list[dict[str, str]], feature_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not source_rows:
        return feature_rows
    by_eval = {str(row.get("evaluation")): row for row in feature_rows}
    merged: list[dict[str, Any]] = []
    for row in source_rows:
        evaluation = row.get("evaluation") or row.get("sample_id") or row.get("id")
        combined: dict[str, Any] = dict(row)
        if evaluation in by_eval:
            combined.update(by_eval[evaluation])
        merged.append(combined)
    return merged


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    if not fieldnames:
        fieldnames = ["evaluation", "ok", "error"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _build_summary(
    dataset_dir: Path,
    out_dir: Path,
    candidates: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    status: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    ok_rows = [row for row in rows if _truthy(row.get("ok"))]
    fail_rows = [row for row in rows if not _truthy(row.get("ok"))]
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "counts": {
            "touchstone_candidates": len(candidates),
            "feature_rows": len(rows),
            "ok_rows": len(ok_rows),
            "fail_rows": len(fail_rows),
        },
        "arguments": {
            "port_pairs": args.port_pairs,
            "ground_unused_ports": bool(args.ground_unused_ports),
            "target_frequency_ghz": args.target_frequency_ghz,
            "load_ohm": args.load_ohm,
            "expected_ports": args.expected_ports,
            "expected_frequency_start_ghz": args.expected_frequency_start_ghz,
            "expected_frequency_stop_ghz": args.expected_frequency_stop_ghz,
            "expected_frequency_step_ghz": args.expected_frequency_step_ghz,
            "expected_frequency_points": args.expected_frequency_points,
        },
        "zin_center_definition": "primary differential input impedance with the secondary differential port terminated by load_ohm",
        "cm_definitions": CM_DEFINITIONS,
        "failures": [{"evaluation": row.get("evaluation"), "error": row.get("error")} for row in fail_rows],
        "limitations": [
            "This script extracts labels from existing Touchstone files only; it does not prove EMX/HFSS agreement.",
            "For S8P power-line ports, use ground_unused_ports=true when ADS grounds all non-selected supply-line ports; otherwise Lp/Ls/Q/K are extracted with those ports open.",
            "The default zin_center_* columns use primary-loaded 50 ohm differential Zin. Change --load-ohm if the project target termination differs.",
            "Cm columns are formula-explicit labels; do not mix single-ended and differential Cm definitions in ADS/HFSS/EMX comparison gates.",
            "Run audit_zin_coverage.py on this output directory before claiming response-space coverage.",
        ],
    }


def _render_report(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    lines = [
        "# Touchstone Response Feature Extraction",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Output: `{summary['out_dir']}`",
        f"- Touchstone candidates: `{counts['touchstone_candidates']}`",
        f"- OK feature rows: `{counts['ok_rows']}`",
        f"- Failed rows: `{counts['fail_rows']}`",
        f"- Zin center definition: {summary['zin_center_definition']}",
        "",
        "## Cm Columns",
        "",
        "Cm is exported with explicit formula names so ADS single-ended and differential definitions are not mixed:",
        "",
    ]
    for name, definition in summary.get("cm_definitions", {}).items():
        lines.append(f"- `{name}_center`: {definition}")
    lines.extend(
        [
            "",
        "## Generated Files",
        "",
        "- `response_features.csv`: compact per-Touchstone response features.",
        "- `dataset_rows.csv`: compatibility file for `audit_zin_coverage.py`.",
        "- `dataset_manifest.json`: minimal derived manifest for coverage audits.",
        "",
        "## Next Command",
        "",
        "```bash",
        "scripts/audit_zin_coverage.py <this-output-dir> --out-dir <this-output-dir>/zin_coverage_audit --plot",
        "```",
        ]
    )
    if summary["failures"]:
        lines.extend(["", "## Failures", "", "| Evaluation | Error |", "| --- | --- |"])
        for item in summary["failures"]:
            lines.append(f"| {item['evaluation']} | {item['error']} |")
    lines.extend(["", "## Limits", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _overall_status(rows: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "INCOMPLETE"
    if not rows or any(not _truthy(row.get("ok")) for row in rows):
        return "FAIL"
    return "PASS"


def _safe_div(numer: np.ndarray, denom: np.ndarray) -> np.ndarray:
    numer = np.asarray(numer, dtype=float)
    denom = np.asarray(denom, dtype=float)
    out = np.full_like(numer, np.nan, dtype=float)
    mask = np.abs(denom) > 1.0e-30
    out[mask] = numer[mask] / denom[mask]
    return out


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass", "ok"}


def _is_touchstone(path: Path) -> bool:
    return path.is_file() and path.suffix.lower().endswith("p") and ".s" in path.name.lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
