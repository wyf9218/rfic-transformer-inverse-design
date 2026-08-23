#!/usr/bin/env python3
"""Compare low- and current-frequency transformer feature stability from real S4P labels.

The audit reads the same real Touchstone files at two target frequencies and
compares the forward one-step change of Lp, Ls, Qp, Qs, Qmin, and |K|.  It is
an ablation-readiness diagnostic only: it never changes the production feature
contract or replaces a shared-split inverse-model and real-EMX comparison.
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

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_emx_hfss_ads import four_port_z_to_differential_z, parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.analysis import multiport_s_to_grounded_differential_z  # noqa: E402
from rfic_transformer_inverse_design.network_analysis import s_to_z  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


FEATURES = ("lp_nh", "ls_nh", "qp", "qs", "q_min", "k_abs")
RELATIVE_FLOORS = {
    "lp_nh": 0.05,
    "ls_nh": 0.05,
    "qp": 1.0,
    "qs": 1.0,
    "q_min": 1.0,
    "k_abs": 0.05,
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    source_csv = dataset_dir / "dataset_rows.csv"
    source_rows = _read_csv(source_csv)
    candidates = _candidate_paths(dataset_dir, source_rows)
    sampled = _distributed_sample(candidates, int(args.max_files))
    target_frequencies = (float(args.low_frequency_ghz), float(args.current_frequency_ghz))
    records = [_audit_one(item, target_frequencies, args) for item in sampled]
    target_summaries = {
        _frequency_key(frequency): _summarize_frequency(records, frequency)
        for frequency in target_frequencies
    }
    checks = _checks(source_csv, candidates, records, target_summaries, args)
    status = "PASS" if all(checks.values()) else "FAIL"
    recommendation = _recommendation(target_summaries, target_frequencies, args) if status == "PASS" else {
        "status": "UNAVAILABLE",
        "decision": "FIX_FREQUENCY_STABILITY_AUDIT_INPUTS",
        "reason": "One or more evidence checks failed.",
    }
    summary_path = out_dir / "physical_feature_frequency_stability_summary.json"
    rows_path = out_dir / "physical_feature_frequency_stability_rows.csv"
    report_path = out_dir / "physical_feature_frequency_stability_report.md"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "FREQUENCY_STABILITY_ABLATION_READY" if status == "PASS" else "DO_NOT_USE_FREQUENCY_STABILITY_RESULT",
        "dataset_dir": str(dataset_dir),
        "source_dataset_csv": str(source_csv),
        "candidate_touchstone_count": len(candidates),
        "sampled_touchstone_count": len(sampled),
        "successful_touchstone_count": sum(item.get("ok") is True for item in records),
        "target_frequencies_ghz": list(target_frequencies),
        "forward_step_ghz": float(args.forward_step_ghz),
        "target_summaries": target_summaries,
        "recommendation": recommendation,
        "checks": checks,
        "arguments": vars(args),
        "artifacts": {"rows_csv": str(rows_path), "report_md": str(report_path)},
        "literature_basis": (
            "TMTT-2026-02-0420_Proof_hi.pdf, PDF page 6: low-frequency L/Q/K descriptors are used because "
            "high-frequency apparent parameters become unstable near resonance. This project compares 5 and 15 GHz "
            "because its measured EMX band starts at 5 GHz; it does not extrapolate an unobserved 2 GHz label."
        ),
        "scientific_boundary": (
            "PASS means the same real S4P files were compared on the declared grid. A recommendation is diagnostic "
            "and cannot change the production input contract without a shared-row model ablation and real-EMX closure."
        ),
    }
    _write_csv(rows_path, records)
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={payload['decision']}")
    print(f"recommendation={recommendation.get('decision')}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--low-frequency-ghz", type=float, default=5.0)
    parser.add_argument("--current-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--forward-step-ghz", type=float, default=0.5)
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--ground-unused-ports", action="store_true")
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-files", type=int, default=512)
    parser.add_argument("--min-files", type=int, default=128)
    parser.add_argument("--min-success-fraction", type=float, default=0.98)
    parser.add_argument("--min-plausible-fraction", type=float, default=0.95)
    parser.add_argument("--max-abs-k", type=float, default=1.0)
    parser.add_argument("--material-improvement-fraction", type=float, default=0.10)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.max_files < 1 or args.min_files < 1 or args.min_files > args.max_files:
        parser.error("file counts must satisfy 1 <= min-files <= max-files")
    if args.forward_step_ghz <= 0.0:
        parser.error("--forward-step-ghz must be positive")
    if not 0.0 <= args.min_success_fraction <= 1.0 or not 0.0 <= args.min_plausible_fraction <= 1.0:
        parser.error("success and plausibility fractions must be in [0, 1]")
    return args


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _candidate_paths(dataset_dir: Path, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    candidates = []
    seen: set[Path] = set()
    for index, row in enumerate(rows):
        if not _truthy(row.get("ok", "true")):
            continue
        raw = str(row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (dataset_dir / path).resolve()
        else:
            path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        candidates.append(
            {
                "source_row_index": index,
                "evaluation": row.get("evaluation") or row.get("sample_id") or f"row_{index}",
                "path": path,
            }
        )
    return candidates


def _distributed_sample(items: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    if len(items) <= maximum:
        return list(items)
    indices = np.linspace(0, len(items) - 1, maximum, dtype=int)
    return [items[int(index)] for index in indices]


def _audit_one(item: dict[str, Any], targets_ghz: tuple[float, float], args: argparse.Namespace) -> dict[str, Any]:
    path = Path(item["path"])
    record: dict[str, Any] = {
        "source_row_index": item["source_row_index"],
        "evaluation": item["evaluation"],
        "touchstone_path": str(path),
        "touchstone_sha256": _sha256(path) if path.is_file() else "",
        "ok": False,
        "error": "",
    }
    try:
        touchstone = load_touchstone(path)
        frequencies = np.asarray(touchstone.freqs_hz, dtype=float)
        s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
        _validate_grid(frequencies, s_matrix, args)
        z_single = s_to_z(s_matrix, z0=touchstone.reference_impedance_ohm)
        if bool(args.ground_unused_ports):
            z_diff = multiport_s_to_grounded_differential_z(
                s_matrix,
                touchstone.reference_impedance_ohm,
                parse_port_pairs(args.port_pairs),
            )
        elif z_single.shape[1:] == (4, 4):
            z_diff = four_port_z_to_differential_z(z_single, parse_port_pairs(args.port_pairs))
        elif z_single.shape[1:] == (2, 2):
            z_diff = z_single
        else:
            raise ValueError("non-grounded extraction supports only differential S2P or single-ended S4P")
        curves = _metric_curves(z_diff, frequencies)
        for target in targets_ghz:
            _add_frequency_record(record, curves, frequencies, target, args)
        record["ok"] = True
    except Exception as exc:  # noqa: BLE001 - exact simulator evidence failure is required.
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def _validate_grid(frequencies: np.ndarray, s_matrix: np.ndarray, args: argparse.Namespace) -> None:
    expected_ports = int(args.expected_ports)
    if s_matrix.shape != (len(frequencies), expected_ports, expected_ports):
        raise ValueError(f"expected shape (N,{expected_ports},{expected_ports}), got {s_matrix.shape}")
    if len(frequencies) != int(args.expected_points):
        raise ValueError(f"expected {args.expected_points} frequency points, got {len(frequencies)}")
    if not np.isfinite(frequencies).all() or not np.isfinite(s_matrix.real).all() or not np.isfinite(s_matrix.imag).all():
        raise ValueError("non-finite Touchstone data")
    tolerance = float(args.frequency_tolerance_hz)
    expected = np.linspace(
        float(args.expected_start_ghz) * 1.0e9,
        float(args.expected_stop_ghz) * 1.0e9,
        int(args.expected_points),
    )
    if not np.allclose(frequencies, expected, rtol=0.0, atol=tolerance):
        raise ValueError("Touchstone frequency grid does not match the declared start/stop/step/point contract")
    if len(expected) > 1 and not math.isclose(
        float(expected[1] - expected[0]),
        float(args.expected_step_ghz) * 1.0e9,
        abs_tol=tolerance,
    ):
        raise ValueError("declared frequency step is inconsistent with start/stop/point count")


def _metric_curves(z_diff: np.ndarray, frequencies: np.ndarray) -> dict[str, np.ndarray]:
    omega = 2.0 * math.pi * frequencies
    z11 = z_diff[:, 0, 0]
    z22 = z_diff[:, 1, 1]
    z21 = z_diff[:, 1, 0]
    lp = np.imag(z11) / omega * 1.0e9
    ls = np.imag(z22) / omega * 1.0e9
    mutual = np.imag(z21) / omega * 1.0e9
    qp = _safe_div(np.imag(z11), np.real(z11))
    qs = _safe_div(np.imag(z22), np.real(z22))
    k_abs = np.abs(mutual / np.sqrt(np.maximum(np.abs(lp * ls), 1.0e-30)))
    return {"lp_nh": lp, "ls_nh": ls, "qp": qp, "qs": qs, "q_min": np.minimum(qp, qs), "k_abs": k_abs}


def _add_frequency_record(
    record: dict[str, Any],
    curves: dict[str, np.ndarray],
    frequencies: np.ndarray,
    target_ghz: float,
    args: argparse.Namespace,
) -> None:
    target_hz = target_ghz * 1.0e9
    next_hz = (target_ghz + float(args.forward_step_ghz)) * 1.0e9
    target_index = _exact_index(frequencies, target_hz, float(args.frequency_tolerance_hz))
    next_index = _exact_index(frequencies, next_hz, float(args.frequency_tolerance_hz))
    prefix = _frequency_key(target_ghz)
    plausible = True
    for feature in FEATURES:
        target_value = float(curves[feature][target_index])
        next_value = float(curves[feature][next_index])
        relative_step = abs(next_value - target_value) / max(abs(target_value), RELATIVE_FLOORS[feature])
        record[f"{prefix}__{feature}"] = target_value
        record[f"{prefix}__{feature}__next"] = next_value
        record[f"{prefix}__{feature}__forward_relative_step"] = relative_step
        plausible = plausible and math.isfinite(target_value) and math.isfinite(next_value) and math.isfinite(relative_step)
    plausible = plausible and all(float(record[f"{prefix}__{feature}"]) > 0.0 for feature in ("lp_nh", "ls_nh", "qp", "qs", "q_min"))
    plausible = plausible and float(record[f"{prefix}__k_abs"]) <= float(args.max_abs_k)
    record[f"{prefix}__plausible"] = bool(plausible)
    record[f"{prefix}__actual_frequency_ghz"] = float(frequencies[target_index] / 1.0e9)
    record[f"{prefix}__actual_next_frequency_ghz"] = float(frequencies[next_index] / 1.0e9)


def _exact_index(frequencies: np.ndarray, target_hz: float, tolerance: float) -> int:
    index = int(np.argmin(np.abs(frequencies - target_hz)))
    if abs(float(frequencies[index]) - target_hz) > tolerance:
        raise ValueError(f"required frequency {target_hz} Hz is absent")
    return index


def _summarize_frequency(records: list[dict[str, Any]], frequency_ghz: float) -> dict[str, Any]:
    prefix = _frequency_key(frequency_ghz)
    valid = [item for item in records if item.get("ok") is True and f"{prefix}__plausible" in item]
    plausible_count = sum(item.get(f"{prefix}__plausible") is True for item in valid)
    per_feature = {}
    p95_values = []
    for feature in FEATURES:
        values = np.asarray(
            [float(item[f"{prefix}__{feature}__forward_relative_step"]) for item in valid],
            dtype=float,
        )
        finite = values[np.isfinite(values)]
        item = {
            "count": int(finite.size),
            "median_forward_relative_step": float(np.median(finite)) if finite.size else None,
            "p95_forward_relative_step": float(np.quantile(finite, 0.95)) if finite.size else None,
            "max_forward_relative_step": float(np.max(finite)) if finite.size else None,
        }
        per_feature[feature] = item
        if item["p95_forward_relative_step"] is not None:
            p95_values.append(float(item["p95_forward_relative_step"]))
    return {
        "frequency_ghz": frequency_ghz,
        "valid_count": len(valid),
        "plausible_count": plausible_count,
        "plausible_fraction": plausible_count / len(valid) if valid else 0.0,
        "per_feature": per_feature,
        "mean_feature_p95_forward_relative_step": float(np.mean(p95_values)) if p95_values else None,
        "worst_feature_p95_forward_relative_step": float(np.max(p95_values)) if p95_values else None,
    }


def _checks(
    source_csv: Path,
    candidates: list[dict[str, Any]],
    records: list[dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, bool]:
    successes = sum(item.get("ok") is True for item in records)
    success_fraction = successes / len(records) if records else 0.0
    return {
        "dataset_rows_csv_exists": source_csv.is_file(),
        "candidate_count_meets_minimum": len(candidates) >= int(args.min_files),
        "sample_count_meets_minimum": len(records) >= int(args.min_files),
        "touchstone_success_fraction": success_fraction >= float(args.min_success_fraction),
        "both_target_summaries_present": len(summaries) == 2 and all(item.get("valid_count", 0) > 0 for item in summaries.values()),
        "both_target_plausibility_pass": all(
            float(item.get("plausible_fraction") or 0.0) >= float(args.min_plausible_fraction)
            for item in summaries.values()
        ),
        "all_feature_summaries_finite": all(
            all((feature_item or {}).get("p95_forward_relative_step") is not None for feature_item in (item.get("per_feature") or {}).values())
            for item in summaries.values()
        ),
    }


def _recommendation(
    summaries: dict[str, dict[str, Any]],
    targets: tuple[float, float],
    args: argparse.Namespace,
) -> dict[str, Any]:
    low = summaries[_frequency_key(targets[0])]
    current = summaries[_frequency_key(targets[1])]
    low_score = float(low["worst_feature_p95_forward_relative_step"])
    current_score = float(current["worst_feature_p95_forward_relative_step"])
    material = float(args.material_improvement_fraction)
    if low_score <= current_score * (1.0 - material):
        decision = "LOW_FREQUENCY_MORE_STABLE_RUN_SHARED_ROW_MODEL_ABLATION"
        reason = f"low-frequency worst-feature p95={low_score:.6g}, current={current_score:.6g}"
    elif current_score <= low_score * (1.0 - material):
        decision = "CURRENT_FREQUENCY_MORE_STABLE_RETAIN_PENDING_MODEL_ABLATION"
        reason = f"current-frequency worst-feature p95={current_score:.6g}, low={low_score:.6g}"
    else:
        decision = "NO_MATERIAL_STABILITY_DIFFERENCE_RUN_SHARED_ROW_MODEL_ABLATION"
        reason = f"worst-feature p95 low={low_score:.6g}, current={current_score:.6g}"
    return {
        "status": "AUDIT_ONLY_NO_AUTOMATIC_CONTRACT_CHANGE",
        "decision": decision,
        "reason": reason,
        "material_improvement_fraction": material,
    }


def _frequency_key(frequency_ghz: float) -> str:
    return f"f{frequency_ghz:g}ghz".replace(".", "p")


def _safe_div(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    return np.divide(
        numerator,
        denominator,
        out=np.full_like(np.asarray(numerator, dtype=float), np.nan),
        where=np.abs(denominator) > 1.0e-18,
    )


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "pass", "ok"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _render_report(data: dict[str, Any]) -> str:
    lines = [
        "# Physical-feature extraction-frequency stability audit",
        "",
        f"- Overall status: **{data['overall_status']}**",
        f"- Decision: **{data['decision']}**",
        f"- Sampled real S4P files: `{data['sampled_touchstone_count']}`",
        f"- Successful files: `{data['successful_touchstone_count']}`",
        f"- Diagnostic recommendation: **{data['recommendation'].get('decision')}**",
        "",
    ]
    for key, item in data["target_summaries"].items():
        lines.extend(
            [
                f"## {item['frequency_ghz']:g} GHz",
                "",
                f"- Plausible fraction: `{item['plausible_fraction']:.6g}`",
                f"- Mean feature p95 forward relative step: `{item['mean_feature_p95_forward_relative_step']}`",
                f"- Worst feature p95 forward relative step: `{item['worst_feature_p95_forward_relative_step']}`",
                "",
            ]
        )
    lines.extend([data["scientific_boundary"], ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
