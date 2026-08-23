#!/usr/bin/env python3
"""Partition a candidate queue into verified completed and residual EMX rows.

Completed rows are accepted only when a readable Touchstone file, a successful
evaluation summary, and an exact geometry-key match to the source queue all
exist. The source queue order is preserved. No simulator label is fabricated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone


DEFAULT_GEOMETRY_COLUMNS = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "line_width_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
)

PORT_GROUND_OVERLAP_LABELS = tuple(f"P{index:03d}" for index in range(1, 9))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    candidate_csv = Path(args.candidate_csv).expanduser().resolve()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    queue_rows, fieldnames = _read_csv(candidate_csv)
    geometry_columns = tuple(item.strip() for item in args.geometry_columns.split(",") if item.strip())
    queue_keys, queue_key_errors = _queue_geometry_keys(queue_rows, geometry_columns, args.geometry_round_digits)
    key_to_index = {key: idx for idx, key in enumerate(queue_keys) if key is not None}

    completed_by_index: dict[int, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    duplicate_completed: list[dict[str, Any]] = []
    discovered_paths = sorted(
        path for path in dataset_dir.rglob("*.s*p")
        if path.is_file() and path.parent.name == "emx" and path.name.lower() == f"emx.s{args.expected_ports}p"
    )
    for touchstone_path in discovered_paths:
        record, reason = _completed_record(
            touchstone_path,
            geometry_columns=geometry_columns,
            round_digits=args.geometry_round_digits,
            expected_ports=args.expected_ports,
            expected_start_ghz=args.expected_frequency_start_ghz,
            expected_stop_ghz=args.expected_frequency_stop_ghz,
            expected_step_ghz=args.expected_frequency_step_ghz,
            expected_points=args.expected_frequency_points,
            expected_port_ground_overlap_um=args.expected_port_ground_overlap_um,
            port_ground_overlap_tolerance_um=args.port_ground_overlap_tolerance_um,
        )
        if record is None:
            rejected.append({"touchstone_path": str(touchstone_path), "reason": reason})
            continue
        queue_index = key_to_index.get(record["geometry_key"])
        if queue_index is None:
            rejected.append({"touchstone_path": str(touchstone_path), "reason": "geometry_not_in_candidate_queue"})
            continue
        completed = {
            **record,
            "queue_index": queue_index,
            "candidate_id": str(queue_rows[queue_index].get("candidate_id") or ""),
        }
        if queue_index in completed_by_index:
            duplicate_completed.append(completed)
            continue
        completed_by_index[queue_index] = completed

    completed_indices = set(completed_by_index)
    completed_rows = [row for idx, row in enumerate(queue_rows) if idx in completed_indices]
    residual_rows = [row for idx, row in enumerate(queue_rows) if idx not in completed_indices]
    completed_ids = {str(row.get("candidate_id") or "") for row in completed_rows}
    residual_ids = {str(row.get("candidate_id") or "") for row in residual_rows}
    queue_ids = [str(row.get("candidate_id") or "") for row in queue_rows]
    rejected_reason_counts = dict(
        sorted(Counter(str(item["reason"]) for item in rejected).items())
    )

    completed_csv = out_dir / "completed_candidate_rows.csv"
    residual_csv = out_dir / "residual_candidate_queue.csv"
    manifest_csv = out_dir / "completed_touchstone_manifest.csv"
    rejected_csv = out_dir / "rejected_or_incomplete_touchstones.csv"
    _write_csv(completed_csv, completed_rows, fieldnames)
    _write_csv(residual_csv, residual_rows, fieldnames)
    manifest_rows = [_manifest_row(completed_by_index[idx]) for idx in sorted(completed_by_index)]
    _write_csv(manifest_csv, manifest_rows, list(manifest_rows[0]) if manifest_rows else [])
    _write_csv(rejected_csv, rejected, ["touchstone_path", "reason"])

    checks = [
        _check("candidate_csv_exists", candidate_csv.is_file(), str(candidate_csv)),
        _check("expected_candidate_count", len(queue_rows) == args.expected_count, f"rows={len(queue_rows)} expected={args.expected_count}"),
        _check("candidate_ids_present", all(queue_ids), f"missing={sum(not value for value in queue_ids)}"),
        _check("candidate_ids_unique", len(set(queue_ids)) == len(queue_ids), f"unique={len(set(queue_ids))} rows={len(queue_ids)}"),
        _check("queue_geometry_keys_valid", not queue_key_errors, f"errors={len(queue_key_errors)}"),
        _check("queue_geometry_keys_unique", len(key_to_index) == len(queue_rows), f"unique={len(key_to_index)} rows={len(queue_rows)}"),
        _check("completed_rows_present", bool(completed_rows), f"completed={len(completed_rows)}"),
        _check("completed_ids_disjoint_from_residual", completed_ids.isdisjoint(residual_ids), f"intersection={len(completed_ids & residual_ids)}"),
        _check("completed_plus_residual_equals_queue", len(completed_rows) + len(residual_rows) == len(queue_rows), f"completed={len(completed_rows)} residual={len(residual_rows)} queue={len(queue_rows)}"),
        _check("completed_union_residual_ids_equals_queue", completed_ids | residual_ids == set(queue_ids), f"union={len(completed_ids | residual_ids)} queue={len(set(queue_ids))}"),
        _check("no_duplicate_completed_touchstones", not duplicate_completed, f"duplicates={len(duplicate_completed)}"),
        _check(
            "all_completed_touchstones_have_verified_port_ground_overlap",
            all(item.get("port_ground_overlap_verified") is True for item in completed_by_index.values()),
            f"verified={sum(item.get('port_ground_overlap_verified') is True for item in completed_by_index.values())} completed={len(completed_by_index)}",
        ),
        _check(
            "no_readable_successful_touchstone_unmatched_to_queue",
            not any(item["reason"] == "geometry_not_in_candidate_queue" for item in rejected),
            f"unmatched={sum(item['reason'] == 'geometry_not_in_candidate_queue' for item in rejected)}",
        ),
    ]
    overall_status = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "RUN_RESIDUAL_QUEUE" if overall_status == "PASS" else "DO_NOT_RUN_RESIDUAL_QUEUE",
        "candidate_csv": _file_record(candidate_csv),
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "expected_count": int(args.expected_count),
        "queue_row_count": len(queue_rows),
        "completed_verified_count": len(completed_rows),
        "residual_count": len(residual_rows),
        "discovered_touchstone_count": len(discovered_paths),
        "rejected_or_incomplete_touchstone_count": len(rejected),
        "rejected_or_incomplete_touchstone_reason_counts": rejected_reason_counts,
        "duplicate_completed_touchstone_count": len(duplicate_completed),
        "geometry_columns": list(geometry_columns),
        "geometry_round_digits": int(args.geometry_round_digits),
        "touchstone_contract": {
            "ports": int(args.expected_ports),
            "frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "frequency_points": int(args.expected_frequency_points),
        },
        "port_ground_overlap_contract": {
            "verification_required": True,
            "evidence_path": "geometry_check.power_line_8port_geometry_audit.port_ground_overlap_evidence",
            "required_port_labels": list(PORT_GROUND_OVERLAP_LABELS),
            "expected_overlap_um": float(args.expected_port_ground_overlap_um),
            "absolute_tolerance_um": float(args.port_ground_overlap_tolerance_um),
        },
        "outputs": {
            "completed_candidate_rows": _file_record(completed_csv),
            "residual_candidate_queue": _file_record(residual_csv),
            "completed_touchstone_manifest": _file_record(manifest_csv),
            "rejected_or_incomplete_touchstones": _file_record(rejected_csv),
        },
        "checks": checks,
        "limitations": [
            "Only readable completed S-parameter files with successful summaries, exact queue geometry matches, and verified P001-P008 ground-overlap evidence are removed from the residual queue.",
            "Files still being written, files without a completed summary, and failed evaluations remain in the residual queue.",
            "Files with missing or nonconforming port-ground overlap evidence remain in the residual queue even when their Touchstone files are readable.",
            "This partition proves queue completeness and disjointness; downstream physical-feature acceptance is still required.",
        ],
    }
    summary_path = out_dir / "residual_queue_partition_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"overall_status={overall_status}")
    print(f"completed_verified_count={len(completed_rows)}")
    print(f"residual_count={len(residual_rows)}")
    print(f"summary={summary_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-count", type=int, default=100_000)
    parser.add_argument("--geometry-columns", default=",".join(DEFAULT_GEOMETRY_COLUMNS))
    parser.add_argument("--geometry-round-digits", type=int, default=12)
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--expected-port-ground-overlap-um", type=float, default=10.0)
    parser.add_argument("--port-ground-overlap-tolerance-um", type=float, default=1.0e-6)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.expected_port_ground_overlap_um) or args.expected_port_ground_overlap_um < 0:
        parser.error("--expected-port-ground-overlap-um must be finite and non-negative")
    if not math.isfinite(args.port_ground_overlap_tolerance_um) or args.port_ground_overlap_tolerance_um < 0:
        parser.error("--port-ground-overlap-tolerance-um must be finite and non-negative")
    return args


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not fieldnames:
            return
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _queue_geometry_keys(
    rows: list[dict[str, str]], columns: tuple[str, ...], round_digits: int
) -> tuple[list[tuple[float, ...] | None], list[dict[str, Any]]]:
    keys: list[tuple[float, ...] | None] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        try:
            key = _geometry_key(row, columns, round_digits)
        except Exception as exc:  # noqa: BLE001
            keys.append(None)
            errors.append({"row_index": index, "error": str(exc)})
        else:
            keys.append(key)
    return keys, errors


def _geometry_key(values: dict[str, Any], columns: tuple[str, ...], round_digits: int) -> tuple[float, ...]:
    result = []
    for column in columns:
        value = float(values[column])
        if not math.isfinite(value):
            raise ValueError(f"non-finite {column}={value!r}")
        result.append(round(value, round_digits))
    return tuple(result)


def _completed_record(
    touchstone_path: Path,
    *,
    geometry_columns: tuple[str, ...],
    round_digits: int,
    expected_ports: int,
    expected_start_ghz: float,
    expected_stop_ghz: float,
    expected_step_ghz: float,
    expected_points: int,
    expected_port_ground_overlap_um: float,
    port_ground_overlap_tolerance_um: float,
) -> tuple[dict[str, Any] | None, str]:
    summary_path = touchstone_path.parent.parent / "summary.json"
    if not summary_path.is_file():
        return None, "missing_evaluation_summary"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable_evaluation_summary:{type(exc).__name__}"
    if summary.get("ok") is not True or summary.get("error") not in (None, ""):
        return None, "evaluation_summary_not_successful"
    overlap_record, overlap_reason = _port_ground_overlap_record(
        summary,
        expected_um=expected_port_ground_overlap_um,
        tolerance_um=port_ground_overlap_tolerance_um,
    )
    if overlap_record is None:
        return None, overlap_reason
    geometry = summary.get("geometry") if isinstance(summary.get("geometry"), dict) else {}
    try:
        geometry_key = _geometry_key(geometry, geometry_columns, round_digits)
    except Exception as exc:  # noqa: BLE001
        return None, f"invalid_geometry_summary:{exc}"
    try:
        result = load_touchstone(touchstone_path)
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable_touchstone:{type(exc).__name__}:{exc}"
    freqs = np.asarray(result.freqs_hz, dtype=float)
    if result.num_ports != expected_ports:
        return None, f"wrong_port_count:{result.num_ports}"
    if len(freqs) != expected_points:
        return None, f"wrong_frequency_point_count:{len(freqs)}"
    expected_start_hz = expected_start_ghz * 1e9
    expected_stop_hz = expected_stop_ghz * 1e9
    expected_step_hz = expected_step_ghz * 1e9
    if not np.isclose(freqs[0], expected_start_hz, rtol=0.0, atol=1.0):
        return None, f"wrong_frequency_start:{freqs[0]}"
    if not np.isclose(freqs[-1], expected_stop_hz, rtol=0.0, atol=1.0):
        return None, f"wrong_frequency_stop:{freqs[-1]}"
    if len(freqs) > 1 and not np.allclose(np.diff(freqs), expected_step_hz, rtol=0.0, atol=1.0):
        return None, "wrong_frequency_step"
    if not np.all(np.isfinite(result.s_matrix.real)) or not np.all(np.isfinite(result.s_matrix.imag)):
        return None, "non_finite_s_parameters"
    command = [str(item) for item in (summary.get("command") or [])]
    return {
        "geometry_key": geometry_key,
        "cache_key": str(summary.get("cache_key") or touchstone_path.parent.parent.name),
        "touchstone_path": str(touchstone_path.resolve()),
        "touchstone_sha256": _sha256(touchstone_path),
        "touchstone_size_bytes": touchstone_path.stat().st_size,
        "summary_path": str(summary_path.resolve()),
        "emx_parallel_arg": next((item for item in command if item.startswith("--parallel=")), "default"),
        **overlap_record,
    }, ""


def _port_ground_overlap_record(
    summary: dict[str, Any], *, expected_um: float, tolerance_um: float
) -> tuple[dict[str, Any] | None, str]:
    geometry_check = summary.get("geometry_check")
    if not isinstance(geometry_check, dict):
        return None, "missing_port_ground_overlap_evidence"
    power_line_audit = geometry_check.get("power_line_8port_geometry_audit")
    if not isinstance(power_line_audit, dict):
        return None, "missing_port_ground_overlap_evidence"
    evidence = power_line_audit.get("port_ground_overlap_evidence")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("ports"), dict):
        return None, "missing_port_ground_overlap_evidence"

    recorded_expected = _finite_float(evidence.get("expected_um"))
    if recorded_expected is None:
        return None, "missing_port_ground_overlap_evidence"
    if not math.isclose(recorded_expected, expected_um, rel_tol=0.0, abs_tol=tolerance_um):
        return None, "port_ground_overlap_expected_mismatch"

    measured: dict[str, float] = {}
    for label in PORT_GROUND_OVERLAP_LABELS:
        port_record = evidence["ports"].get(label)
        value = (
            _finite_float(port_record.get("measured_overlap_um"))
            if isinstance(port_record, dict)
            else None
        )
        if value is None:
            return None, "missing_port_ground_overlap_evidence"
        measured[label] = value

    mismatched_labels = tuple(
        label
        for label, value in measured.items()
        if not math.isclose(value, expected_um, rel_tol=0.0, abs_tol=tolerance_um)
    )
    if mismatched_labels:
        return None, f"port_ground_overlap_label_mismatch:{','.join(mismatched_labels)}"

    max_error = max(abs(value - expected_um) for value in measured.values())
    evidence_payload = {
        "expected_um": recorded_expected,
        "measured_overlap_um": measured,
    }
    return {
        "port_ground_overlap_verified": True,
        "port_ground_overlap_expected_um": expected_um,
        "port_ground_overlap_max_abs_error_um": max_error,
        "port_ground_overlap_evidence_sha256": hashlib.sha256(
            json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest(),
    }, ""


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _manifest_row(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "geometry_key"}


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": detail}


if __name__ == "__main__":
    raise SystemExit(main())
